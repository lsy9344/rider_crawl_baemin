"""Kakao inbound command event — transport/DB-neutral decision core (Phase 3).

The server owns validation, room→channel→target mapping, tenant/send/rate/
in-flight gates, dedupe, and the enqueue/reject decision for inbound Kakao
command events. This module holds that decision as a **pure, synchronous**
function over already-loaded views, so it is exhaustively unit-testable without
FastAPI, SQLAlchemy, or the queue. The async orchestration (load views → decide
→ enqueue) and the HTTP route are thin layers on top of this.

Privacy: decisions and rejection reasons are fixed machine-readable codes; raw
message text never enters this layer. The parsed name/phone live only inside the
job payload (job scope), never in logs.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Any, Awaitable, Callable, Mapping, Sequence

from rider_crawl.rider_lookup import UNSUPPORTED_PLATFORM_REPLY

from ..queue.states import JOB_TYPE_KAKAO_SEND, JOB_TYPE_RIDER_LOOKUP

# The only inbound command type implemented in phase 1 (mirrors
# rider_crawl.rider_lookup.COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP by value).
COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP = "RIDER_CANCEL_RATE_LOOKUP"

MESSENGER_KAKAO = "KAKAO"
CHANNEL_STATE_ACTIVE = "ACTIVE"
TARGET_STATE_ACTIVE = "ACTIVE"
PLATFORM_BAEMIN = "baemin"

ORIGIN_KAKAO_INBOUND = "kakao_inbound"
DEFAULT_LOOKUP_TIMEOUT_SECONDS = 60
# Extra slack beyond timeout_seconds for claim/browser-prep latency before the
# worker's stale-payload defense (expires_at) engages.
LOOKUP_TTL_GRACE_SECONDS = 60

# Decision actions (what the orchestrator should do).
ACTION_ENQUEUE_LOOKUP = "enqueue_lookup"  # accepted: enqueue one RIDER_LOOKUP
ACTION_DUPLICATE = "duplicate"            # accepted: already enqueued, do nothing
ACTION_REPLY = "reply"                    # rejected: enqueue one scoped KAKAO_SEND reply
ACTION_REJECT = "reject"                  # rejected: no job, no reply

# Fixed rejection reasons (lowercase, no PII) — the API surfaces these verbatim.
REASON_INVALID_EVENT = "invalid_event"
REASON_UNKNOWN_ROOM = "unknown_room"
REASON_CHANNEL_INACTIVE = "channel_inactive"
REASON_COMMAND_DISABLED = "command_disabled"
REASON_TARGET_UNMAPPED = "target_unmapped"
REASON_UNSUPPORTED_PLATFORM = "unsupported_platform"
REASON_TENANT_DISABLED = "tenant_disabled"
REASON_SENDING_DISABLED = "sending_disabled"
REASON_RATE_LIMITED = "rate_limited"
REASON_LOOKUP_IN_FLIGHT = "lookup_in_flight"

_PHONE_LAST4_RE = re.compile(r"^[0-9]{4}$")


@dataclass(frozen=True)
class InboundCommandInput:
    type: str
    name: str
    phone_last4: str


@dataclass(frozen=True)
class InboundEventInput:
    source: str
    kakao_user_hash_digest: str
    chat_id: str
    room_name: str
    last_log_id: str
    command: InboundCommandInput


@dataclass(frozen=True)
class ChannelView:
    """Minimal channel facts needed for mapping (neutral; no ORM)."""

    channel_id: str
    tenant_id: str
    messenger: str
    kakao_room_name: str | None
    kakao_chat_id: str | None
    state: str
    command_trigger_enabled: bool


@dataclass(frozen=True)
class TargetView:
    target_id: str
    tenant_id: str
    platform: str
    platform_account_id: str
    primary_url: str
    expected_display_name: str
    status: str
    external_id: str = ""


@dataclass(frozen=True)
class InboundContext:
    """Already-loaded inputs for a pure decision (orchestrator fills these in)."""

    channels: tuple[ChannelView, ...]
    targets_by_channel: Mapping[str, tuple[TargetView, ...]]
    sending_enabled: bool
    existing_event_keys: frozenset[str] = frozenset()
    in_flight_target_ids: frozenset[str] = frozenset()
    inactive_tenant_ids: frozenset[str] = frozenset()
    rate_limited_channel_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class InboundDecision:
    action: str
    reason: str = ""
    channel_id: str | None = None
    tenant_id: str | None = None
    target_id: str | None = None
    origin_event_key: str = ""
    job_payload: dict | None = None
    reply_text: str = ""
    reply_kakao_room_name: str = ""
    # Non-empty when the matched channel had no stored chat_id and should be
    # bound to this inbound chat_id on first accepted event.
    bind_chat_id: str = ""

    @property
    def accepted(self) -> bool:
        return self.action in (ACTION_ENQUEUE_LOOKUP, ACTION_DUPLICATE)

    @property
    def duplicate(self) -> bool:
        return self.action == ACTION_DUPLICATE


def normalize_room_name(value: str | None) -> str:
    return unicodedata.normalize("NFC", value or "").strip()


def origin_event_key(event: InboundEventInput) -> str:
    """Deterministic dedupe key per the design formula (sha256 over fixed fields)."""

    scope = event.chat_id.strip() if event.chat_id and event.chat_id.strip() else normalize_room_name(event.room_name)
    basis = "\n".join(
        [
            event.source,
            event.kakao_user_hash_digest,
            scope,
            event.last_log_id,
            event.command.type,
            event.command.name,
            event.command.phone_last4,
        ]
    )
    return "sha256:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _command_is_valid(command: InboundCommandInput) -> bool:
    if command.type != COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP:
        return False
    if not normalize_room_name(command.name):
        return False
    return bool(_PHONE_LAST4_RE.match(command.phone_last4 or ""))


def _match_channels(event: InboundEventInput, channels: Sequence[ChannelView]) -> list[ChannelView]:
    """Return KAKAO channels matching this event by chat_id (preferred) or room name.

    Fail-closed on chat_id conflict: a channel with a stored kakao_chat_id that
    differs from the inbound chat_id does NOT match even if the room name agrees.
    """

    inbound_chat_id = (event.chat_id or "").strip()
    inbound_room = normalize_room_name(event.room_name)
    matched: list[ChannelView] = []
    for channel in channels:
        if channel.messenger != MESSENGER_KAKAO:
            continue
        stored_chat_id = (channel.kakao_chat_id or "").strip()
        if stored_chat_id:
            # Bound channel: require an exact chat_id match (fail-closed on conflict).
            if inbound_chat_id and stored_chat_id == inbound_chat_id:
                matched.append(channel)
            continue
        # Unbound channel: match by exact (NFC) room name.
        if inbound_room and normalize_room_name(channel.kakao_room_name) == inbound_room:
            matched.append(channel)
    return matched


def decide_inbound_event(
    event: InboundEventInput,
    context: InboundContext,
) -> InboundDecision:
    """Pure decision for one inbound Kakao command event.

    Order (each step is fail-closed): command validity → room/channel mapping →
    target mapping → tenant gate → global send gate → unsupported platform →
    rate limit → in-flight lookup → dedupe → enqueue.
    """

    if not _command_is_valid(event.command):
        return InboundDecision(action=ACTION_REJECT, reason=REASON_INVALID_EVENT)

    matched = _match_channels(event, context.channels)
    active_matched = [c for c in matched if c.state == CHANNEL_STATE_ACTIVE]
    if not active_matched:
        # A non-active candidate means the room is known but the channel is off.
        reason = REASON_CHANNEL_INACTIVE if matched else REASON_UNKNOWN_ROOM
        return InboundDecision(action=ACTION_REJECT, reason=reason)
    if len(active_matched) > 1:
        # Ambiguous (e.g. same room name across tenants): do not guess.
        return InboundDecision(action=ACTION_REJECT, reason=REASON_UNKNOWN_ROOM)

    channel = active_matched[0]
    if not channel.command_trigger_enabled:
        return InboundDecision(
            action=ACTION_REJECT, reason=REASON_COMMAND_DISABLED, channel_id=channel.channel_id
        )

    targets = tuple(context.targets_by_channel.get(channel.channel_id, ()))
    active_targets = [t for t in targets if t.status == TARGET_STATE_ACTIVE]
    if len(active_targets) != 1:
        return InboundDecision(
            action=ACTION_REJECT,
            reason=REASON_TARGET_UNMAPPED,
            channel_id=channel.channel_id,
            tenant_id=channel.tenant_id,
        )
    target = active_targets[0]

    key = origin_event_key(event)
    bind_chat_id = (
        event.chat_id.strip()
        if (event.chat_id or "").strip() and not (channel.kakao_chat_id or "").strip()
        else ""
    )

    # Tenant + global send gates come before any reply (a reply also needs send).
    if channel.tenant_id in context.inactive_tenant_ids:
        return InboundDecision(
            action=ACTION_REJECT, reason=REASON_TENANT_DISABLED,
            channel_id=channel.channel_id, tenant_id=channel.tenant_id, target_id=target.target_id,
        )
    if not context.sending_enabled:
        return InboundDecision(
            action=ACTION_REJECT, reason=REASON_SENDING_DISABLED,
            channel_id=channel.channel_id, tenant_id=channel.tenant_id, target_id=target.target_id,
        )

    # Unsupported platform: send a scoped fixed reply (sending is enabled here).
    if target.platform.strip().lower() != PLATFORM_BAEMIN:
        return InboundDecision(
            action=ACTION_REPLY,
            reason=REASON_UNSUPPORTED_PLATFORM,
            channel_id=channel.channel_id,
            tenant_id=channel.tenant_id,
            target_id=target.target_id,
            origin_event_key=key,
            reply_text=UNSUPPORTED_PLATFORM_REPLY,
            reply_kakao_room_name=channel.kakao_room_name or "",
            bind_chat_id=bind_chat_id,
        )

    if channel.channel_id in context.rate_limited_channel_ids:
        return InboundDecision(
            action=ACTION_REJECT, reason=REASON_RATE_LIMITED,
            channel_id=channel.channel_id, tenant_id=channel.tenant_id, target_id=target.target_id,
        )
    if target.target_id in context.in_flight_target_ids:
        return InboundDecision(
            action=ACTION_REJECT, reason=REASON_LOOKUP_IN_FLIGHT,
            channel_id=channel.channel_id, tenant_id=channel.tenant_id, target_id=target.target_id,
        )

    if key in context.existing_event_keys:
        return InboundDecision(
            action=ACTION_DUPLICATE,
            channel_id=channel.channel_id,
            tenant_id=channel.tenant_id,
            target_id=target.target_id,
            origin_event_key=key,
        )

    payload = _lookup_payload(event, channel=channel, target=target, origin_event_key=key)
    return InboundDecision(
        action=ACTION_ENQUEUE_LOOKUP,
        channel_id=channel.channel_id,
        tenant_id=channel.tenant_id,
        target_id=target.target_id,
        origin_event_key=key,
        job_payload=payload,
        reply_kakao_room_name=channel.kakao_room_name or "",
        bind_chat_id=bind_chat_id,
    )


def _lookup_payload(
    event: InboundEventInput,
    *,
    channel: ChannelView,
    target: TargetView,
    origin_event_key: str,
) -> dict:
    payload: dict = {
        "tenant_id": target.tenant_id,
        "target_id": target.target_id,
        "platform": target.platform.strip().lower(),
        "platform_account_id": target.platform_account_id,
        "primary_url": target.primary_url,
        "expected_display_name": target.expected_display_name,
        "reply_channel_id": channel.channel_id,
        "reply_messenger": MESSENGER_KAKAO,
        "reply_kakao_room_name": channel.kakao_room_name or "",
        "origin": ORIGIN_KAKAO_INBOUND,
        "origin_event_key": origin_event_key,
        "command": {
            "type": event.command.type,
            "name": event.command.name,
            "phone_last4": event.command.phone_last4,
        },
        "timeout_seconds": DEFAULT_LOOKUP_TIMEOUT_SECONDS,
    }
    if target.external_id:
        payload["external_id"] = target.external_id
    return payload


# Convenience for the job type the orchestrator enqueues.
LOOKUP_JOB_TYPE = JOB_TYPE_RIDER_LOOKUP


# ── async orchestration (load views → pure decide → enqueue/reply/bind) ───────

ChannelLoader = Callable[[], Awaitable[Sequence[ChannelView]] | Sequence[ChannelView]]
TargetLoader = Callable[[str], Awaitable[Sequence[TargetView]] | Sequence[TargetView]]
Enqueue = Callable[..., Awaitable[str] | str]


async def _maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


def _iso_utc(moment: datetime) -> str:
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class KakaoInboundEventService:
    """Async orchestration over the pure decision core.

    Data access is injected as callables (seams), so this is unit-testable with
    fakes and wired to the real channel repository / queue backend / jobs reader
    in ``create_app``. Only matched channel's targets and the single event's
    dedupe/in-flight state are loaded (no full-table fan-out).
    """

    def __init__(
        self,
        *,
        load_channels: ChannelLoader,
        load_targets: TargetLoader,
        enqueue: Enqueue,
        sending_enabled: Callable[[], Awaitable[bool] | bool],
        bind_chat_id: Callable[[str, str], Awaitable[None] | None] | None = None,
        is_duplicate: Callable[[str], Awaitable[bool] | bool] | None = None,
        in_flight: Callable[[str], Awaitable[bool] | bool] | None = None,
        tenant_active: Callable[[str], Awaitable[bool] | bool] | None = None,
        rate_limited: Callable[[str], Awaitable[bool] | bool] | None = None,
        already_replied: Callable[[str], Awaitable[bool] | bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._load_channels = load_channels
        self._load_targets = load_targets
        self._enqueue = enqueue
        self._sending_enabled = sending_enabled
        self._bind_chat_id = bind_chat_id
        self._is_duplicate = is_duplicate
        self._in_flight = in_flight
        self._tenant_active = tenant_active
        self._rate_limited = rate_limited
        self._already_replied = already_replied
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def handle(self, event: InboundEventInput) -> dict:
        """Process one inbound event and return the API response dict."""

        context = await self._build_context(event)
        decision = decide_inbound_event(event, context)

        if decision.action == ACTION_ENQUEUE_LOOKUP:
            await self._maybe_bind(decision)
            payload = dict(decision.job_payload or {})
            # Stamp expires_at so the worker's stale-payload defense engages if the
            # job is claimed/run long after detection (kept out of the pure decision
            # because it is time-dependent).
            timeout = payload.get("timeout_seconds", DEFAULT_LOOKUP_TIMEOUT_SECONDS)
            expires = self._now() + timedelta(seconds=float(timeout) + LOOKUP_TTL_GRACE_SECONDS)
            payload["expires_at"] = _iso_utc(expires)
            job_id = await _maybe_await(
                self._enqueue(
                    job_type=LOOKUP_JOB_TYPE,
                    target_id=decision.target_id,
                    payload_json=payload,
                )
            )
            return {"accepted": True, "duplicate": False, "job_id": job_id}

        if decision.action == ACTION_DUPLICATE:
            return {"accepted": True, "duplicate": True}

        if decision.action == ACTION_REPLY:
            await self._maybe_bind(decision)
            key = decision.origin_event_key
            # Dedupe the rejection reply the same way the lookup path dedupes: a
            # resubmitted event (e.g. a lost submit response) must not enqueue a
            # second KAKAO_SEND for the same origin_event_key.
            if (
                key
                and self._already_replied is not None
                and await _maybe_await(self._already_replied(key))
            ):
                return {"accepted": False, "duplicate": True, "reason": decision.reason}
            reply_payload = {
                "kakao_room_name": decision.reply_kakao_room_name,
                "message": decision.reply_text,
                "origin": ORIGIN_KAKAO_INBOUND,
                "origin_event_key": decision.origin_event_key,
            }
            await _maybe_await(
                self._enqueue(
                    job_type=JOB_TYPE_KAKAO_SEND,
                    target_id=None,
                    payload_json=reply_payload,
                )
            )
            return {"accepted": False, "duplicate": False, "reason": decision.reason}

        return {"accepted": False, "duplicate": False, "reason": decision.reason}

    async def _build_context(self, event: InboundEventInput) -> InboundContext:
        sending_enabled = bool(await _maybe_await(self._sending_enabled()))
        if not _command_is_valid(event.command):
            # Skip data loads on an invalid command (the decision rejects anyway).
            return InboundContext(channels=(), targets_by_channel={}, sending_enabled=sending_enabled)

        channels = tuple(await _maybe_await(self._load_channels()))
        targets_by_channel: dict[str, tuple[TargetView, ...]] = {}
        existing_keys: set[str] = set()
        in_flight_ids: set[str] = set()
        inactive_tenants: set[str] = set()
        rate_limited_ids: set[str] = set()

        # Resolve the single matched active channel (if any) and load only its
        # targets + this event's dedupe/in-flight/tenant state.
        active_matched = [c for c in _match_channels(event, channels) if c.state == CHANNEL_STATE_ACTIVE]
        if len(active_matched) == 1:
            channel = active_matched[0]
            targets = tuple(await _maybe_await(self._load_targets(channel.channel_id)))
            targets_by_channel[channel.channel_id] = targets
            if self._rate_limited is not None and await _maybe_await(self._rate_limited(channel.channel_id)):
                rate_limited_ids.add(channel.channel_id)
            if self._tenant_active is not None and not await _maybe_await(self._tenant_active(channel.tenant_id)):
                inactive_tenants.add(channel.tenant_id)
            active_targets = [t for t in targets if t.status == TARGET_STATE_ACTIVE]
            if len(active_targets) == 1:
                target = active_targets[0]
                key = origin_event_key(event)
                if self._is_duplicate is not None and await _maybe_await(self._is_duplicate(key)):
                    existing_keys.add(key)
                if self._in_flight is not None and await _maybe_await(self._in_flight(target.target_id)):
                    in_flight_ids.add(target.target_id)

        return InboundContext(
            channels=channels,
            targets_by_channel=targets_by_channel,
            sending_enabled=sending_enabled,
            existing_event_keys=frozenset(existing_keys),
            in_flight_target_ids=frozenset(in_flight_ids),
            inactive_tenant_ids=frozenset(inactive_tenants),
            rate_limited_channel_ids=frozenset(rate_limited_ids),
        )

    async def _maybe_bind(self, decision: InboundDecision) -> None:
        if self._bind_chat_id is not None and decision.bind_chat_id and decision.channel_id:
            await _maybe_await(self._bind_chat_id(decision.channel_id, decision.bind_chat_id))
