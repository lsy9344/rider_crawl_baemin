"""Agent registration and heartbeat service.

The server compares registration codes and bearer tokens by hash. Plaintext
registration codes and issued tokens are never stored.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from rider_crawl.redaction import redact

AGENT_STATUS_REGISTERED = "REGISTERED"
AGENT_STATUS_ONLINE = "ONLINE"
DEFAULT_CONFIG_VERSION = 1


class AgentRegistryError(RuntimeError):
    """Base error for safe HTTP mapping. Messages must not include code/token values."""


class RegistrationCodeNotFound(AgentRegistryError):
    """Registration code does not map to a pending Agent."""


class RegistrationCodeAlreadyUsed(AgentRegistryError):
    """Registration code was already consumed."""


class DuplicateMachineRegistration(AgentRegistryError):
    """A different Agent already owns this machine fingerprint."""


class InvalidAgentToken(AgentRegistryError):
    """Bearer token is missing, unknown, or revoked."""


class AgentTokenMismatch(AgentRegistryError):
    """Bearer token resolves to a different agent than the heartbeat body."""


@dataclass(frozen=True)
class AgentRecord:
    id: str
    name: str
    machine_id: str
    version: str
    os: str
    status: str
    last_heartbeat_at: datetime | None = None
    capacity_json: dict[str, Any] = field(default_factory=dict)
    token_hash: str | None = None
    token_issued_at: datetime | None = None
    token_revoked_at: datetime | None = None
    token_rotated_at: datetime | None = None
    registration_code_hash: str | None = None
    registration_code_used_at: datetime | None = None


@dataclass(frozen=True)
class RegisterAgentInput:
    registration_code: str
    machine_fingerprint: str
    hostname: str
    os: str
    agent_version: str


@dataclass(frozen=True)
class RegisterAgentResult:
    agent_id: str
    agent_token: str
    tenant_scope: dict[str, Any]
    config_version: int


@dataclass(frozen=True)
class HeartbeatInput:
    agent_id: str
    metrics: dict[str, Any]
    capabilities: list[str]
    active_jobs: list[Any]
    kakao_status: dict[str, Any]
    browser_profiles: list[Any]
    browser_slots: dict[str, Any] = field(default_factory=dict)
    agent_version: str = ""


@dataclass(frozen=True)
class HeartbeatResult:
    server_time: datetime
    config_version: int = DEFAULT_CONFIG_VERSION
    commands: list[Any] = field(default_factory=list)


class AgentRegistry(Protocol):
    async def register(self, request: RegisterAgentInput, *, now: datetime) -> RegisterAgentResult: ...

    async def heartbeat(
        self,
        request: HeartbeatInput,
        *,
        bearer_token: str,
        now: datetime,
    ) -> HeartbeatResult: ...

    async def resolve_agent_id(self, bearer_token: str) -> str | None: ...

    async def capacity_for_agent(self, agent_id: str) -> dict[str, Any] | None: ...


def hash_registration_code(code: str) -> str:
    """Hash a one-time registration code before storage/lookup."""

    return _sha256("agent-registration-code", code)


def hash_agent_token(token: str) -> str:
    """Hash a bearer token before storage/lookup."""

    return _sha256("agent-token", token)


def generate_agent_token() -> str:
    """Generate a high-entropy bearer token returned only at registration time."""

    return "agtok_" + secrets.token_urlsafe(32)


_ACTIVE_JOB_KEYS = frozenset({"job_id", "lease_expires_at"})
_BROWSER_PROFILE_KEYS = frozenset(
    {
        "id",
        "target_id",
        "state",
        "cdp_port",
        "profile_path_ref",
        # Task 3A 최소 진단 필드(optional). secret/URL/HTML/page_kind 는 허용하지 않는다.
        "auth_state",
        "last_probe_at",
        "last_error_code",
    }
)
#: browser_profiles[] 의 문자열 진단 필드(타입/길이/control char 검증 대상).
_BROWSER_PROFILE_STRING_KEYS = frozenset(
    {"id", "target_id", "state", "profile_path_ref", "auth_state", "last_probe_at", "last_error_code"}
)
#: heartbeat 진단 문자열 최대 길이(과도하게 긴 값은 저장하지 않는다).
_BROWSER_PROFILE_STRING_MAX_LENGTH = 120
#: 유효한 CDP 디버깅 포트 범위(0/음수/65535 초과/non-int 는 저장하지 않는다).
_CDP_PORT_MIN = 1
_CDP_PORT_MAX = 65535
_BROWSER_SLOT_COUNT_KEYS = frozenset(
    {
        "max",
        "used",
        "available",
        "manual_auth_used",
        "orphan_count",
        "registry_profiles",
    }
)
_BROWSER_SLOT_PERCENT_KEYS = frozenset({"ram_used_percent"})
_KAKAO_STATUS_KEYS = frozenset(
    {
        "queue_depth",
        "queue_lag_seconds",
        "enabled",
        "current_state",
        "state",
        "last_success_at",
        "last_error_code",
        "sent",
        "failed",
        "worker_enabled",
        "interactive_session_available",
        "inbound",
    }
)
_SENSITIVE_KEY_PARTS = frozenset(
    {"token", "secret", "password", "credential", "clipboard", "message", "room", "screenshot"}
)


def heartbeat_capacity(request: HeartbeatInput) -> dict[str, Any]:
    metrics = _sanitize_mapping(request.metrics)
    capabilities = [redact(str(capability)) for capability in request.capabilities]
    return {
        "metrics": metrics,
        "capabilities": capabilities,
        "max_in_flight": _max_in_flight(metrics, capabilities),
        "active_jobs": _sanitize_list(request.active_jobs, allowed_keys=_ACTIVE_JOB_KEYS),
        "kakao_status": _sanitize_mapping(request.kakao_status, allowed_keys=_KAKAO_STATUS_KEYS),
        # browser_profiles 는 item 단위로 타입/길이/cdp_port 범위까지 검증한다(generic
        # _sanitize_list 가 아니라 전용 sanitizer — agent_registry 가 저장 경계의 정본).
        "browser_profiles": _sanitize_browser_profiles(request.browser_profiles),
        "browser_slots": _sanitize_browser_slots(request.browser_slots),
    }


def _max_in_flight(metrics: dict[str, Any], capabilities: list[str]) -> int:
    value = metrics.get("max_in_flight")
    if isinstance(value, int) and value > 0:
        return value
    return 1 if capabilities else 0


def _sha256(scope: str, value: str) -> str:
    return hashlib.sha256(f"{scope}:{value}".encode("utf-8")).hexdigest()


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if "path" in lowered and lowered != "profile_path_ref":
        return True
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_mapping(
    value: dict[str, Any],
    *,
    allowed_keys: frozenset[str] | None = None,
) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if allowed_keys is not None and key not in allowed_keys:
            continue
        if _is_sensitive_key(key):
            continue
        cleaned[key] = _sanitize_value(raw_value)
    return cleaned


def _sanitize_list(
    values: list[Any],
    *,
    allowed_keys: frozenset[str] | None = None,
) -> list[Any]:
    cleaned: list[Any] = []
    for value in values:
        if isinstance(value, dict):
            cleaned.append(_sanitize_mapping(value, allowed_keys=allowed_keys))
        else:
            cleaned.append(_sanitize_value(value))
    return cleaned


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def _has_control_char(text: str) -> bool:
    return any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in text)


def _sanitize_browser_profile_string(value: Any) -> str | None:
    """browser_profiles[] 문자열 필드를 정제한다 — redact 후 빈/control/과길이는 버린다."""

    if not isinstance(value, str):
        return None
    text = redact(value).strip()
    if not text or len(text) > _BROWSER_PROFILE_STRING_MAX_LENGTH:
        return None
    if _has_control_char(text):
        return None
    return text


def _sanitize_cdp_port(value: Any) -> int | None:
    """cdp_port 를 정제한다 — 1..65535 정수만 저장(bool/float/문자열/범위 밖은 버린다).

    bool 은 int 서브타입이라 먼저 걸러낸다. 이 검증은 browser_profiles[] item 경로에만
    적용하고 전역 _sanitize_value 의 다른 int 는 건드리지 않는다.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int) and _CDP_PORT_MIN <= value <= _CDP_PORT_MAX:
        return value
    return None


def _sanitize_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _sanitize_percent(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and 0 <= value <= 100:
        return value
    return None


def _sanitize_browser_slots(value: Any) -> dict[str, Any]:
    """Sanitize top-level heartbeat browser slot aggregates.

    This is a positive allowlist for numeric aggregate fields only. Raw profile
    paths, URLs, titles, command lines, email identifiers, and secret-like keys
    are intentionally not representable here.
    """

    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key in _BROWSER_SLOT_COUNT_KEYS:
            number = _sanitize_nonnegative_int(raw_value)
            if number is not None:
                cleaned[key] = number
        elif key in _BROWSER_SLOT_PERCENT_KEYS:
            percent = _sanitize_percent(raw_value)
            if percent is not None:
                cleaned[key] = percent
    return cleaned


def _sanitize_browser_profiles(values: list[Any]) -> list[dict[str, Any]]:
    """heartbeat browser_profiles 를 item 단위로 정제한다(allowlist + 타입/길이/범위).

    - dict 아닌 item 은 건너뛴다.
    - allowlist 밖 키(``profile_path``/``current_url``/``page_kind``/secret 류)는 버린다.
    - 문자열 진단 필드는 redact + 빈/control/과길이 검증.
    - ``cdp_port`` 는 1..65535 정수만 저장(bool/float/문자열/범위 밖은 버린다).
    """

    cleaned: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        item: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key not in _BROWSER_PROFILE_KEYS:
                continue
            if _is_sensitive_key(key):
                continue
            if key == "cdp_port":
                port = _sanitize_cdp_port(raw_value)
                if port is not None:
                    item[key] = port
            elif key in _BROWSER_PROFILE_STRING_KEYS:
                text = _sanitize_browser_profile_string(raw_value)
                if text is not None:
                    item[key] = text
            else:
                item[key] = _sanitize_value(raw_value)
        cleaned.append(item)
    return cleaned


class InMemoryAgentRegistry:
    """In-memory Agent registry for dev and always-run tests."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentRecord] = {}

    def seed_registration_code(
        self,
        code: str,
        *,
        agent_id: str,
        name: str = "pending-agent",
    ) -> None:
        self._agents[agent_id] = AgentRecord(
            id=agent_id,
            name=name,
            machine_id="pending",
            version="pending",
            os="pending",
            status="PENDING_REGISTRATION",
            capacity_json={},
            registration_code_hash=hash_registration_code(code),
        )

    def agent(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    async def register(
        self,
        request: RegisterAgentInput,
        *,
        now: datetime,
    ) -> RegisterAgentResult:
        code_hash = hash_registration_code(request.registration_code.strip())
        agent = next(
            (candidate for candidate in self._agents.values() if candidate.registration_code_hash == code_hash),
            None,
        )
        if agent is None:
            raise RegistrationCodeNotFound("registration code not found")
        if agent.registration_code_used_at is not None:
            raise RegistrationCodeAlreadyUsed("registration code already used")
        duplicate = next(
            (
                candidate
                for candidate in self._agents.values()
                if candidate.id != agent.id
                and candidate.machine_id == request.machine_fingerprint
                and candidate.registration_code_used_at is not None
            ),
            None,
        )
        if duplicate is not None:
            raise DuplicateMachineRegistration("machine already registered")

        token = generate_agent_token()
        updated = replace(
            agent,
            name=redact(request.hostname.strip()) or "agent",
            machine_id=request.machine_fingerprint.strip(),
            version=request.agent_version.strip(),
            os=request.os.strip(),
            status=AGENT_STATUS_REGISTERED,
            registration_code_used_at=now,
            token_hash=hash_agent_token(token),
            token_issued_at=now,
        )
        self._agents[agent.id] = updated
        return RegisterAgentResult(
            agent_id=updated.id,
            agent_token=token,
            tenant_scope={},
            config_version=DEFAULT_CONFIG_VERSION,
        )

    async def heartbeat(
        self,
        request: HeartbeatInput,
        *,
        bearer_token: str,
        now: datetime,
    ) -> HeartbeatResult:
        agent_id = await self.resolve_agent_id(bearer_token)
        if agent_id is None:
            raise InvalidAgentToken("invalid agent token")
        if agent_id != request.agent_id:
            raise AgentTokenMismatch("agent token does not match body agent_id")
        agent = self._agents.get(agent_id)
        if agent is None:
            raise InvalidAgentToken("invalid agent token")

        updated_fields: dict[str, Any] = {
            "status": AGENT_STATUS_ONLINE,
            "last_heartbeat_at": now,
            "capacity_json": heartbeat_capacity(request),
        }
        agent_version = request.agent_version.strip()
        if agent_version:
            updated_fields["version"] = agent_version

        self._agents[agent_id] = replace(agent, **updated_fields)
        return HeartbeatResult(server_time=now)

    async def resolve_agent_id(self, bearer_token: str) -> str | None:
        token_hash = hash_agent_token(bearer_token)
        for agent in self._agents.values():
            if (
                agent.token_hash == token_hash
                and agent.token_revoked_at is None
                and agent.token_rotated_at is None
            ):
                return agent.id
        return None

    async def capacity_for_agent(self, agent_id: str) -> dict[str, Any] | None:
        agent = self._agents.get(agent_id)
        if agent is None:
            return None
        return dict(agent.capacity_json or {})
