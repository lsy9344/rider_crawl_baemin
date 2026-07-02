"""KakaoTalk inbound command watcher + event client (Agent side, Phase 2).

The watcher runs only on interactive Windows Agent nodes with inbound Kakao
detection enabled. It scans *only* configured rooms in the local KakaoTalk DB
(via the ``rider_crawl`` reader seam), applies the shared command parser, and
POSTs **sanitized** events to the server. It never sends Kakao messages and
never starts crawling — the server owns validation, mapping, and job creation.

Operational safety / privacy:

- Disabled by default; a degraded or invalid Kakao DB state disables only inbound
  detection, never the existing crawl/send paths.
- Raw Kakao message text, parsed name, and phone suffix are sensitive: they are
  held in memory only long enough to build the event, and are never written to
  logs, heartbeat, or status. Logs use fixed event codes and redaction.
- Only ``kakao_user_hash_digest`` (a hash) is sent to the server, never the raw
  user hash or DB key.

This module is pure-sync and imports only stdlib, ``rider_agent``, and
``rider_crawl`` (the 4.1 AST guard enforces this).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rider_crawl.redaction import redact, redacted_error_event

from rider_agent.registration import (
    DEFAULT_SERVER_BASE_URL,
    SERVER_URL_ENV,
    Transport,
    TransportError,
)
from rider_agent.reuse import (
    DEFAULT_ACCEPTED_CHAT_TYPES,
    ChatLogsReader,
    ChatRoomListReader,
    KakaoDbDependencyMissing,
    KakaoDbError,
    chat_type_accepted,
    parse_rider_lookup_command,
)
from rider_agent.secure_store import AgentIdentity

INBOUND_EVENTS_PATH = "/v1/kakao/inbound-events"
KAKAO_INBOUND_CONFIG_PATH = "/v1/agents/kakao-inbound-config"
SOURCE_PC_KAKAO_DB = "pc_kakao_db"

# Inbound watcher health states + fixed reasons (no PII; safe for status/logs).
HEALTH_DISABLED = "disabled"
HEALTH_DEGRADED = "degraded"
HEALTH_WARNING = "warning"
HEALTH_ACTIVE = "active"

REASON_FEATURE_DISABLED = "feature_disabled"
REASON_DB_KEY_MISSING = "db_key_missing"
REASON_SQLCIPHER_MISSING = "sqlcipher_missing"
REASON_DB_UNAVAILABLE = "db_unavailable"
REASON_LATEST_WINDOW_1 = "latest_window_size_1"
REASON_ROOM_NOT_FOUND = "configured_room_not_found"
REASON_OK = "ok"
REASON_NON_INTERACTIVE = "non_interactive_session"
REASON_PREREQUISITES_MISSING = "prerequisites_missing"
REASON_EMPTY_WATCHLIST = "empty_watchlist"

INBOUND_OP_LABEL = "kakao inbound event"
INBOUND_CONFIG_OP_LABEL = "kakao inbound config"
STATE_VERSION = 1


class KakaoInboundSubmitError(RuntimeError):
    """Inbound event submit failed at the transport/auth layer (no server verdict)."""


def user_hash_digest(raw_user_hash: str) -> str:
    """Return the ``sha256:<hex>`` digest of a local Kakao user hash.

    Only this digest is sent to the server; the raw user hash stays Agent-local.
    """

    digest = hashlib.sha256((raw_user_hash or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class KakaoRoomConfig:
    """A configured/allowlisted room to scan. ``chat_id`` is optional."""

    room_name: str
    chat_id: str = ""


@dataclass(frozen=True)
class KakaoInboundConfig:
    enabled: bool = False
    rooms: tuple[KakaoRoomConfig, ...] = ()
    accepted_chat_types: tuple[str, ...] = DEFAULT_ACCEPTED_CHAT_TYPES
    # Digest sent to the server (use :func:`user_hash_digest` to derive it).
    user_hash_digest: str = ""
    latest_messages_limit: int = 20


@dataclass(frozen=True)
class InboundEventResult:
    accepted: bool
    duplicate: bool = False
    reason: str = ""
    job_id: str | None = None


@dataclass(frozen=True)
class ScanReport:
    """Non-PII summary of one scan pass (safe to log/aggregate)."""

    health: str
    reason: str = REASON_OK
    rooms_scanned: int = 0
    missing_rooms: int = 0
    primed: int = 0
    submitted: int = 0
    duplicates: int = 0
    rejected: int = 0
    parser_misses: int = 0
    submit_errors: int = 0
    gap_possible: int = 0


_SAFE_HEALTH_KEYS = frozenset(
    {
        "state",
        "reason",
        "latest_window_size",
        "configured_missing_count",
        "scanned_count",
        "submitted_count",
        "duplicate_count",
        "rejected_count",
        "parser_miss_count",
        "submit_error_count",
        "gap_possible_count",
    }
)


@dataclass(frozen=True)
class StaticKakaoInboundHealth:
    """Heartbeat-only Kakao inbound health for setup states before a watcher exists."""

    state: str
    reason: str
    metrics: dict[str, Any]

    def health(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": self.state, "reason": self.reason}
        for key, value in self.metrics.items():
            if key not in _SAFE_HEALTH_KEYS:
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                payload[key] = value
        return payload


def static_kakao_inbound_health(
    state: str, reason: str, **metrics: Any
) -> StaticKakaoInboundHealth:
    """Return a safe heartbeat source for Kakao inbound setup/gate failures."""

    return StaticKakaoInboundHealth(
        state=str(state), reason=str(reason), metrics=dict(metrics)
    )


def _result_from_response(response: dict[str, Any]) -> InboundEventResult:
    return InboundEventResult(
        accepted=bool(response.get("accepted")),
        duplicate=bool(response.get("duplicate")),
        reason=str(response.get("reason") or ""),
        job_id=response.get("job_id"),
    )


class KakaoInboundClient:
    """Posts sanitized inbound events to the server with the Agent token.

    Business rejections are expected as ``200`` responses with
    ``accepted=false`` and a fixed ``reason``. Non-2xx (transport/auth) raises
    :class:`~rider_agent.registration.TransportError`; transient failures are
    retried with bounded backoff, auth failures are not.
    """

    def __init__(
        self,
        identity: AgentIdentity,
        *,
        transport: Transport,
        base_url: str | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._identity = identity
        self._transport = transport
        self._base_url = base_url
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_seconds = max(0.0, float(backoff_seconds))
        if sleep is not None:
            self._sleep = sleep
        else:
            import time

            self._sleep = time.sleep
        self._log = log

    def _url(self) -> str:
        base = self._base_url or os.getenv(SERVER_URL_ENV) or DEFAULT_SERVER_BASE_URL
        return base.rstrip("/") + INBOUND_EVENTS_PATH

    def submit(self, event: dict[str, Any]) -> InboundEventResult:
        headers = {"Authorization": f"Bearer {self._identity.agent_token}"}
        url = self._url()
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._transport.post_json(url, event, headers=headers)
            except TransportError as exc:
                if exc.status_code in (401, 403):
                    # Auth/identity rejection is not a transient condition.
                    raise KakaoInboundSubmitError(
                        f"{INBOUND_OP_LABEL} auth rejected"
                    ) from exc
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_seconds * attempt)
                    continue
                raise KakaoInboundSubmitError(f"{INBOUND_OP_LABEL} submit failed") from exc
            if not isinstance(response, dict):
                raise KakaoInboundSubmitError(f"{INBOUND_OP_LABEL} response was not an object")
            return _result_from_response(response)
        # Unreachable: the loop either returns or raises.
        raise KakaoInboundSubmitError(f"{INBOUND_OP_LABEL} submit failed")


ReaderFactory = Callable[[], Any]


class KakaoInboundWatcher:
    """Scans configured rooms, parses commands, and submits sanitized events.

    A fresh reader is created per scan (via ``reader_factory``) and closed
    afterward so a copied/locked DB is not held across polls. High-water marks
    per dedupe scope (``chat_id`` or normalized room name) prevent reprocessing
    and seed startup so messages sent before activation are not replayed.
    """

    def __init__(
        self,
        *,
        config: KakaoInboundConfig,
        reader_factory: ReaderFactory,
        client: KakaoInboundClient,
        state_path: Path | str,
        parser: Callable[[str], Any] = parse_rider_lookup_command,
        now: Callable[[], datetime] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._reader_factory = reader_factory
        self._client = client
        self._state_path = Path(state_path)
        self._parser = parser
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._log = log
        self._high_water: dict[str, int] = self._load_state()
        self._health = (
            (HEALTH_DISABLED, REASON_FEATURE_DISABLED)
            if not config.enabled
            else (HEALTH_ACTIVE, REASON_OK)
        )
        self.last_error_event: dict[str, Any] | None = None
        self.last_verdict_event: dict[str, Any] | None = None

    # -- public ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        state, reason = self._health
        return {"state": state, "reason": reason, "latest_window_size": self._window_size}

    def scan_once(self) -> ScanReport:
        if not self._config.enabled:
            self._health = (HEALTH_DISABLED, REASON_FEATURE_DISABLED)
            return ScanReport(health=HEALTH_DISABLED, reason=REASON_FEATURE_DISABLED)

        try:
            reader = self._reader_factory()
        except KakaoDbDependencyMissing as exc:
            return self._disabled_scan(REASON_SQLCIPHER_MISSING, exc)
        except KakaoDbError as exc:
            return self._disabled_scan(REASON_DB_KEY_MISSING, exc)
        except Exception as exc:  # noqa: BLE001 — never crash the host process
            return self._disabled_scan(REASON_DB_UNAVAILABLE, exc)

        try:
            return self._scan_with_reader(reader)
        finally:
            self._close_reader(reader)

    # -- scan internals ----------------------------------------------------

    def _scan_with_reader(self, reader: Any) -> ScanReport:
        self._window_size = int(getattr(reader, "latest_window_size", self._config.latest_messages_limit))
        try:
            rooms = reader.list_rooms()
        except KakaoDbDependencyMissing as exc:
            return self._disabled_scan(REASON_SQLCIPHER_MISSING, exc)
        except KakaoDbError as exc:
            return self._disabled_scan(REASON_DB_KEY_MISSING, exc)
        except Exception as exc:  # noqa: BLE001
            return self._disabled_scan(REASON_DB_UNAVAILABLE, exc)

        rooms_by_id = {room.chat_id: room for room in rooms if room.chat_id}
        rooms_by_name = {_normalize_room_name(room.room_name): room for room in rooms}

        counters = {
            "rooms_scanned": 0,
            "missing_rooms": 0,
            "primed": 0,
            "submitted": 0,
            "duplicates": 0,
            "rejected": 0,
            "parser_misses": 0,
            "submit_errors": 0,
            "gap_possible": 0,
        }
        changed = False
        for room_config in self._config.rooms:
            room = self._match_room(room_config, rooms_by_id, rooms_by_name)
            if room is None:
                counters["missing_rooms"] += 1
                continue
            if not chat_type_accepted(room.chat_type, self._config.accepted_chat_types):
                continue
            counters["rooms_scanned"] += 1
            try:
                messages = reader.latest_messages(room, self._config.latest_messages_limit)
            except Exception as exc:  # noqa: BLE001 — one bad room must not kill the scan
                self._record_error("AGENT_KAKAO_INBOUND_READ_ERROR", "room read failed", exc)
                continue
            if self._handle_room_messages(messages, room, counters):
                changed = True

        if changed:
            self._save_state()

        self._window_size = int(getattr(reader, "latest_window_size", self._window_size))
        health, reason = self._resolve_health(counters)
        self._health = (health, reason)
        return ScanReport(health=health, reason=reason, **counters)

    def _handle_room_messages(
        self, messages: list[Any], room: Any, counters: dict[str, int]
    ) -> bool:
        valid_messages = [
            message
            for message in messages
            if _as_int(getattr(message, "log_id", None)) is not None
        ]
        if not valid_messages:
            return False

        scope = _message_scope(valid_messages[-1], room)
        log_ids = [
            parsed
            for parsed in (_as_int(message.log_id) for message in valid_messages)
            if parsed is not None
        ]
        if not log_ids:
            return False

        newest = max(log_ids)
        high_water = self._high_water.get(scope)
        if high_water is None:
            self._high_water[scope] = newest
            counters["primed"] += 1
            return True

        if self._gap_possible(high_water, log_ids):
            self._high_water[scope] = newest
            counters["gap_possible"] += 1
            counters["primed"] += 1
            return True

        changed = False
        for message in sorted(valid_messages, key=lambda item: _as_int(item.log_id) or 0):
            before_submit_errors = counters["submit_errors"]
            if self._handle_message(message, room, counters):
                changed = True
            if counters["submit_errors"] > before_submit_errors:
                break
        return changed

    def _gap_possible(self, high_water: int, log_ids: list[int]) -> bool:
        if getattr(self, "_window_size", self._config.latest_messages_limit) <= 1:
            return False
        effective_limit = min(
            int(self._config.latest_messages_limit),
            int(getattr(self, "_window_size", self._config.latest_messages_limit)),
        )
        if len(log_ids) < effective_limit:
            return False
        return high_water < min(log_ids)

    def _handle_message(self, message: Any, room: Any, counters: dict[str, int]) -> bool:
        log_id = _as_int(message.log_id)
        if log_id is None:
            return False
        scope = _message_scope(message, room)
        high_water = self._high_water.get(scope)
        if high_water is None:
            # First sighting of this scope: prime the baseline; do not process a
            # message that predates watcher activation.
            self._high_water[scope] = log_id
            counters["primed"] += 1
            return True
        if log_id <= high_water:
            return False  # already processed (dedupe by scope + log_id)

        command = self._parser(message.text)
        if command is None:
            # The cheap "!!" prefilter matched but there is no valid token.
            # Advance to avoid re-evaluating the same row each poll.
            self._high_water[scope] = log_id
            counters["parser_misses"] += 1
            return True

        event = self._build_event(message, room, command)
        try:
            result = self._client.submit(event)
        except KakaoInboundSubmitError as exc:
            # No server verdict (unreachable/auth): do not advance — retry next
            # poll while the message remains visible. Never log raw text.
            self._record_error("AGENT_KAKAO_INBOUND_SUBMIT_ERROR", "inbound submit failed", exc)
            counters["submit_errors"] += 1
            return False

        # The server returned a verdict (accept or reject); both are terminal, so
        # advance the high-water mark to keep one message → at most one job.
        self._record_verdict(message, result)
        self._high_water[scope] = log_id
        if result.accepted:
            counters["submitted"] += 1
            if result.duplicate:
                counters["duplicates"] += 1
        else:
            counters["rejected"] += 1
        return True

    def _build_event(self, message: Any, room: Any, command: Any) -> dict[str, Any]:
        return {
            "source": SOURCE_PC_KAKAO_DB,
            "kakao_user_hash_digest": self._config.user_hash_digest,
            "chat_id": message.chat_id or getattr(room, "chat_id", ""),
            "room_name": room.room_name,
            "last_log_id": message.log_id,
            "message_timestamp": message.timestamp,
            "detected_at": self._now().isoformat(),
            "command": {
                "type": command.type,
                "name": command.name,
                "phone_last4": command.phone_last4,
            },
        }

    def _match_room(self, room_config: KakaoRoomConfig, rooms_by_id: dict, rooms_by_name: dict):
        if room_config.chat_id:
            return rooms_by_id.get(room_config.chat_id)
        return rooms_by_name.get(_normalize_room_name(room_config.room_name))

    def _resolve_health(self, counters: dict[str, int]) -> tuple[str, str]:
        if getattr(self, "_window_size", self._config.latest_messages_limit) <= 1:
            return (HEALTH_DEGRADED, REASON_LATEST_WINDOW_1)
        if counters["missing_rooms"]:
            return (HEALTH_WARNING, REASON_ROOM_NOT_FOUND)
        return (HEALTH_ACTIVE, REASON_OK)

    def _disabled_scan(self, reason: str, exc: BaseException | None) -> ScanReport:
        self._health = (HEALTH_DISABLED, reason)
        if exc is not None:
            self._record_error("AGENT_KAKAO_INBOUND_DISABLED", reason, exc)
        return ScanReport(health=HEALTH_DISABLED, reason=reason)

    def _close_reader(self, reader: Any) -> None:
        close = getattr(reader, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    # -- state -------------------------------------------------------------

    def _load_state(self) -> dict[str, int]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        high_water = raw.get("high_water") if isinstance(raw, dict) else None
        if not isinstance(high_water, dict):
            return {}
        result: dict[str, int] = {}
        for scope, value in high_water.items():
            parsed = _as_int(value)
            if parsed is not None:
                result[str(scope)] = parsed
        return result

    def _save_state(self) -> None:
        payload = {"version": STATE_VERSION, "high_water": dict(self._high_water)}
        from rider_crawl.ui_settings import _atomic_write_text

        _atomic_write_text(self._state_path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _record_error(self, code: str, message: str, error: BaseException | None) -> None:
        event = redacted_error_event(code, message, error)
        self.last_error_event = event
        if self._log is not None:
            self._log(redact(str(event)))

    def _record_verdict(self, message: Any, result: InboundEventResult) -> None:
        event: dict[str, Any] = {
            "code": "AGENT_KAKAO_INBOUND_VERDICT",
            "accepted": bool(result.accepted),
            "duplicate": bool(result.duplicate),
            "last_log_id": str(getattr(message, "log_id", "")),
        }
        if result.reason:
            event["reason"] = result.reason
        if result.job_id:
            event["job_id"] = result.job_id
        self.last_verdict_event = event
        if self._log is not None:
            self._log(redact(str(event)))

    _window_size: int = 1


def _normalize_room_name(value: str) -> str:
    return unicodedata.normalize("NFC", value or "").strip()


def _message_scope(message: Any, room: Any) -> str:
    return (
        str(getattr(message, "chat_id", "") or "")
        or str(getattr(room, "chat_id", "") or "")
        or _normalize_room_name(getattr(room, "room_name", ""))
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_kakao_inbound_watcher(
    *,
    identity: AgentIdentity,
    transport: Transport,
    base_url: str | None = None,
    config: KakaoInboundConfig,
    reader_factory: ReaderFactory,
    state_path: Path | str,
    now: Callable[[], datetime] | None = None,
    log: Callable[[str], None] | None = None,
) -> KakaoInboundWatcher:
    """Assemble a :class:`KakaoInboundWatcher` over its transport client.

    The reader is injected as ``reader_factory`` (built by the caller from the
    ``reuse`` seam), so this module never imports rider_crawl reader classes
    directly. Secrets (DB key / user hash / DB paths) live only in the caller-
    built reader and ``config``; they are never handled or logged here, and only
    ``config.user_hash_digest`` (a hash) ever leaves the Agent.
    """

    client = KakaoInboundClient(
        identity, transport=transport, base_url=base_url, log=log
    )
    return KakaoInboundWatcher(
        config=config,
        reader_factory=reader_factory,
        client=client,
        state_path=state_path,
        now=now,
        log=log,
    )


@dataclass(frozen=True)
class KakaoWatchlist:
    """Server-provided non-secret Kakao inbound watchlist (Hybrid source).

    Only room identifiers arrive from the server; no secret is ever included.
    """

    enabled: bool
    config_version: str
    rooms: tuple[KakaoRoomConfig, ...]


def _parse_watchlist(response: Any) -> KakaoWatchlist:
    inbound = response.get("kakao_inbound") if isinstance(response, dict) else None
    if not isinstance(inbound, dict):
        return KakaoWatchlist(enabled=False, config_version="", rooms=())
    rooms_raw = inbound.get("rooms")
    rooms = tuple(
        KakaoRoomConfig(
            room_name=str(item.get("room_name") or ""),
            chat_id=str(item.get("chat_id") or ""),
        )
        for item in (rooms_raw if isinstance(rooms_raw, list) else [])
        if isinstance(item, dict) and item.get("room_name")
    )
    return KakaoWatchlist(
        enabled=bool(inbound.get("enabled")),
        config_version=str(inbound.get("config_version") or ""),
        rooms=rooms,
    )


class KakaoWatchlistClient:
    """Fetches the server's non-secret Kakao inbound watchlist.

    ``GET /v1/agents/kakao-inbound-config`` with the Agent token. Transient
    failures retry with bounded backoff; auth failures and exhausted retries
    return ``None`` so the caller can fall back to local bootstrap rooms. The
    server returns only non-secret room identifiers — DB key / user_hash / path
    are never received here.
    """

    def __init__(
        self,
        identity: AgentIdentity,
        *,
        transport: Transport,
        base_url: str | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._identity = identity
        self._transport = transport
        self._base_url = base_url
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_seconds = max(0.0, float(backoff_seconds))
        if sleep is not None:
            self._sleep = sleep
        else:
            import time

            self._sleep = time.sleep
        self._log = log

    def _url(self) -> str:
        base = self._base_url or os.getenv(SERVER_URL_ENV) or DEFAULT_SERVER_BASE_URL
        return base.rstrip("/") + KAKAO_INBOUND_CONFIG_PATH

    def fetch(self) -> KakaoWatchlist | None:
        headers = {"Authorization": f"Bearer {self._identity.agent_token}"}
        url = self._url()
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._transport.get_json(url, headers=headers)
            except TransportError as exc:
                if exc.status_code in (401, 403):
                    if self._log is not None:
                        self._log(redact(f"{INBOUND_CONFIG_OP_LABEL} auth rejected"))
                    return None
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_seconds * attempt)
                    continue
                if self._log is not None:
                    self._log(redact(f"{INBOUND_CONFIG_OP_LABEL} fetch failed"))
                return None
            return _parse_watchlist(response)
        return None


def resolve_kakao_inbound_enabled(
    *,
    local_enabled: bool,
    prerequisites_ok: bool,
    session_interactive: bool,
    watchlist_enabled: bool,
    watchlist_has_rooms: bool,
) -> tuple[bool, str]:
    """Hybrid effective-enabled gate → ``(enabled, reason)``.

    effective_enabled = local kill switch && local prerequisites OK && session
    interactive && server watchlist enabled with rooms. Any failure disables the
    watcher with a fixed non-PII reason (order matters: cheapest/most-explanatory
    first).
    """

    if not local_enabled:
        return False, REASON_FEATURE_DISABLED
    if not session_interactive:
        return False, REASON_NON_INTERACTIVE
    if not prerequisites_ok:
        return False, REASON_PREREQUISITES_MISSING
    if not (watchlist_enabled and watchlist_has_rooms):
        return False, REASON_EMPTY_WATCHLIST
    return True, REASON_OK


def resolve_kakao_inbound_rooms(
    *,
    watchlist: KakaoWatchlist | None,
    fallback_rooms: tuple[KakaoRoomConfig, ...] = (),
) -> tuple[KakaoRoomConfig, ...]:
    """Effective scan rooms.

    The server watchlist is the source of truth; local ``fallback_rooms`` are
    only bootstrap/fallback/canary, used solely when the server list is
    unavailable. A server-provided empty list is an explicit empty scope.
    """

    if watchlist is not None:
        return tuple(watchlist.rooms)
    return tuple(fallback_rooms)


@dataclass(frozen=True)
class LocalKakaoInboundSettings:
    """Agent-local, non-server-controlled inbound settings.

    Secrets (db_key/user_hash) are NOT stored here — they come from the secure
    store. This holds only local paths, the kill switch, scan defaults, and
    optional bootstrap/fallback rooms.
    """

    enabled: bool = False
    chat_list_db_path: str = ""
    chat_logs_dir: str = ""
    use_chat_logs: bool = True
    latest_messages_limit: int = 20
    accepted_chat_types: tuple[str, ...] = DEFAULT_ACCEPTED_CHAT_TYPES
    fallback_rooms: tuple[KakaoRoomConfig, ...] = ()


def load_local_kakao_inbound_settings(path: Path | str) -> LocalKakaoInboundSettings:
    """Load Agent-local inbound settings from a JSON file (missing/bad → disabled).

    Never raises: an unreadable/oddly-shaped file yields disabled defaults so the
    Agent's existing paths are unaffected.
    """

    p = Path(path)
    if not p.exists():
        return LocalKakaoInboundSettings()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — bad local config must not crash startup
        return LocalKakaoInboundSettings()
    if not isinstance(data, dict):
        return LocalKakaoInboundSettings()
    rooms_raw = data.get("rooms")
    fallback_rooms = tuple(
        KakaoRoomConfig(
            room_name=str(item.get("room_name") or ""),
            chat_id=str(item.get("chat_id") or ""),
        )
        for item in (rooms_raw if isinstance(rooms_raw, list) else [])
        if isinstance(item, dict) and item.get("room_name")
    )
    try:
        limit = int(data.get("latest_messages_limit", 20) or 20)
    except (TypeError, ValueError):
        limit = 20
    accepted_raw = data.get("accepted_chat_types")
    accepted_chat_types = tuple(
        str(item).strip()
        for item in (accepted_raw if isinstance(accepted_raw, list) else [])
        if str(item).strip()
    ) or DEFAULT_ACCEPTED_CHAT_TYPES
    return LocalKakaoInboundSettings(
        enabled=bool(data.get("enabled")),
        chat_list_db_path=str(data.get("chat_list_db_path") or ""),
        chat_logs_dir=str(data.get("chat_logs_dir") or ""),
        use_chat_logs=bool(data.get("use_chat_logs", True)),
        latest_messages_limit=limit,
        accepted_chat_types=accepted_chat_types,
        fallback_rooms=fallback_rooms,
    )


def make_kakao_reader_factory(
    *,
    chat_list_db_path: str,
    chat_list_db_key: str,
    chat_logs_dir: str | None = None,
    chat_logs_key_resolver: Callable[[Any], str | None] | None = None,
    use_chat_logs: bool = True,
) -> ReaderFactory:
    """Build a reader factory over the reuse seam (no direct rider_crawl import).

    With ``use_chat_logs`` set, returns a latest-N ``ChatLogsReader`` (which itself
    degrades to the latest-one ``ChatRoomListReader`` on open/schema/key failure);
    otherwise the latest-one reader directly. Secrets are passed in, never logged.
    """

    def factory() -> Any:
        if use_chat_logs:
            return ChatLogsReader(
                chat_list_db_path=chat_list_db_path,
                chat_list_db_key=chat_list_db_key,
                chat_logs_dir=chat_logs_dir,
                chat_logs_db_key=chat_list_db_key,
                chat_logs_key_resolver=chat_logs_key_resolver,
            )
        return ChatRoomListReader(db_path=chat_list_db_path, db_key=chat_list_db_key)

    return factory


# Secure-store refs for Agent-local Kakao secrets (never sent to/from the server).
KAKAO_DB_KEY_REF = "kakao_inbound:db_key"
KAKAO_USER_HASH_REF = "kakao_inbound:user_hash"


def build_kakao_inbound_watcher_from_sources(
    *,
    identity: AgentIdentity,
    transport: Transport,
    base_url: str | None,
    settings: LocalKakaoInboundSettings,
    secret_resolver: Callable[[str], str | None] | None,
    session_interactive: bool,
    state_path: Path | str,
    watchlist: KakaoWatchlist | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[Any | None, str]:
    """Resolve the Hybrid gate and, if enabled, build the watcher → ``(watcher, reason)``.

    Secrets (db_key/user_hash) are read from the injected ``secret_resolver`` and
    kept as locals — only the digest reaches ``config`` and only the reason (never
    a secret) is logged. Returns ``(None, reason)`` when the gate is closed so the
    caller can surface the reason and leave the watcher unstarted.
    """

    db_key = secret_resolver(KAKAO_DB_KEY_REF) if secret_resolver is not None else None
    user_hash = secret_resolver(KAKAO_USER_HASH_REF) if secret_resolver is not None else None
    prerequisites_ok = bool(db_key and user_hash and settings.chat_list_db_path)

    effective_rooms = resolve_kakao_inbound_rooms(
        watchlist=watchlist, fallback_rooms=settings.fallback_rooms
    )
    # Server watchlist is SoT. Local fallback rooms are only a canary/bootstrap
    # source when the server watchlist is unavailable, not when it is explicitly
    # empty or disabled.
    watchlist_enabled = (
        bool(settings.fallback_rooms) if watchlist is None else bool(watchlist.enabled)
    )

    enabled, reason = resolve_kakao_inbound_enabled(
        local_enabled=settings.enabled,
        prerequisites_ok=prerequisites_ok,
        session_interactive=session_interactive,
        watchlist_enabled=watchlist_enabled,
        watchlist_has_rooms=bool(effective_rooms),
    )
    if not enabled:
        if log is not None:
            log(redact(f"kakao inbound disabled: {reason}"))
        return None, reason

    config = KakaoInboundConfig(
        enabled=True,
        rooms=effective_rooms,
        accepted_chat_types=settings.accepted_chat_types,
        user_hash_digest=user_hash_digest(user_hash or ""),
        latest_messages_limit=settings.latest_messages_limit,
    )
    reader_factory = make_kakao_reader_factory(
        chat_list_db_path=settings.chat_list_db_path,
        chat_list_db_key=db_key or "",
        chat_logs_dir=settings.chat_logs_dir or None,
        use_chat_logs=settings.use_chat_logs,
    )
    watcher = build_kakao_inbound_watcher(
        identity=identity,
        transport=transport,
        base_url=base_url,
        config=config,
        reader_factory=reader_factory,
        state_path=state_path,
        log=log,
    )
    return watcher, reason


class RefreshingKakaoInboundWatcher:
    """Runtime wrapper that reapplies the server watchlist when it changes.

    The job loop only needs a ``scan_once`` object. This wrapper keeps that
    contract while polling the non-secret server watchlist on scan boundaries and
    rebuilding the inner watcher when the watchlist fingerprint changes.
    """

    def __init__(
        self,
        *,
        identity: AgentIdentity,
        transport: Transport,
        base_url: str | None,
        settings: LocalKakaoInboundSettings,
        secret_resolver: Callable[[str], str | None] | None,
        session_probe: Callable[[], bool] | None,
        state_path: Path | str,
        watchlist_client: KakaoWatchlistClient,
        refresh_interval_seconds: float = 30.0,
        builder: Callable[..., tuple[Any | None, str]] | None = None,
        log: Callable[[str], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._identity = identity
        self._transport = transport
        self._base_url = base_url
        self._settings = settings
        self._secret_resolver = secret_resolver
        self._session_probe = session_probe
        self._state_path = state_path
        self._watchlist_client = watchlist_client
        self._refresh_interval_seconds = max(0.0, float(refresh_interval_seconds))
        self._builder = builder or build_kakao_inbound_watcher_from_sources
        self._log = log
        self._now = now or time.monotonic
        self._last_refresh_at: float | None = None
        self._watchlist_fingerprint: tuple[Any, ...] | None = None
        self._has_applied_watchlist = False
        self._watcher: Any | None = None
        self._reason = REASON_EMPTY_WATCHLIST

    def health(self) -> dict[str, Any]:
        if self._watcher is not None and hasattr(self._watcher, "health"):
            return self._watcher.health()
        return {"state": HEALTH_DISABLED, "reason": self._reason}

    def scan_once(self) -> ScanReport:
        self._refresh_if_due()
        if self._watcher is None:
            return ScanReport(health=HEALTH_DISABLED, reason=self._reason)
        return self._watcher.scan_once()

    def _refresh_if_due(self) -> None:
        now = self._now()
        if (
            self._last_refresh_at is not None
            and now - self._last_refresh_at < self._refresh_interval_seconds
        ):
            return
        self._last_refresh_at = now

        try:
            watchlist = self._watchlist_client.fetch()
        except Exception:  # noqa: BLE001 - config refresh must not kill scanning.
            if self._log is not None:
                self._log(redact(f"{INBOUND_CONFIG_OP_LABEL} refresh failed"))
            watchlist = None

        fingerprint = self._fingerprint(watchlist)
        if self._has_applied_watchlist and fingerprint == self._watchlist_fingerprint:
            return

        session_interactive = (
            bool(self._session_probe()) if callable(self._session_probe) else True
        )
        watcher, reason = self._builder(
            identity=self._identity,
            transport=self._transport,
            base_url=self._base_url,
            settings=self._settings,
            secret_resolver=self._secret_resolver,
            session_interactive=session_interactive,
            state_path=self._state_path,
            watchlist=watchlist,
            log=self._log,
        )
        self._watcher = watcher
        self._reason = reason
        self._watchlist_fingerprint = fingerprint
        self._has_applied_watchlist = True

    @staticmethod
    def _fingerprint(watchlist: KakaoWatchlist | None) -> tuple[Any, ...] | None:
        if watchlist is None:
            return None
        return (watchlist.enabled, watchlist.config_version, tuple(watchlist.rooms))
