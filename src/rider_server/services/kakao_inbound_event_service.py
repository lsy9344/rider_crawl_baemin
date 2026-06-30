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
from typing import Mapping, Sequence

from rider_crawl.rider_lookup import UNSUPPORTED_PLATFORM_REPLY

from ..queue.states import JOB_TYPE_RIDER_LOOKUP

# The only inbound command type implemented in phase 1 (mirrors
# rider_crawl.rider_lookup.COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP by value).
COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP = "RIDER_CANCEL_RATE_LOOKUP"

MESSENGER_KAKAO = "KAKAO"
CHANNEL_STATE_ACTIVE = "ACTIVE"
TARGET_STATE_ACTIVE = "ACTIVE"
PLATFORM_BAEMIN = "baemin"

ORIGIN_KAKAO_INBOUND = "kakao_inbound"
DEFAULT_LOOKUP_TIMEOUT_SECONDS = 60

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
