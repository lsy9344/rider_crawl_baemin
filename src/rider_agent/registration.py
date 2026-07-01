"""등록 클라이언트(register) — 일회용 코드로 Agent 를 서버에 등록 (Story 4.2 / P3-02).

:func:`register_agent` 은 일회용 ``registration_code`` 로 ``POST /v1/agents/register`` 를
호출해 발급 4값(``agent_id``/``agent_token``/``tenant_scope``/``config_version``)을 받아
secret(``agent_token``)은 DPAPI store 로, 나머지는 ``agent_config.json`` 으로 분리 저장한다.

설계 결정(반드시 지킬 것):

* **HTTPS = stdlib ``urllib``.** 텔레그램이 ``urllib`` 로 Bot API 를 호출하는 선례와 동일 —
  ``requests``/``httpx`` 같은 새 HTTP 의존을 도입하지 않는다(4.1 import-root 가드 green 유지).
  transport 는 **주입 가능한 seam**(:class:`Transport`)이라 단위 테스트는 네트워크 없이 fake 로
  검증한다(``run_once`` 가 crawler/sender 를 주입하는 규율과 동형).
* **멱등(로컬 identity 기준).** 이미 유효한 local identity 가 있으면 재등록 POST 를 보내지 않고
  기존 identity 를 반환한다 — 일회용 코드를 불필요하게 소모/덮어쓰지 않는다.
* **token/code 평문 비노출.** 예외(:class:`RegistrationError`) 메시지·로그에 token 이나
  registration code 평문을 넣지 않는다(상태 코드 등 비밀 아님 값만).

자기(own) 코드는 **순수 동기**이고 ``rider_crawl`` 만 import 한다 — 4.1 AST 가드가 자동 검사.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen as default_urlopen

from rider_crawl.secret_store import SecretStore

from rider_agent import __version__
from rider_agent.secure_store import (
    AgentIdentity,
    load_local_agent_identity,
    save_agent_identity,
)
from rider_agent.locking import agent_state_lock

REGISTER_PATH = "/v1/agents/register"

# 서버 base URL: env override > 기본(placeholder). 단위 테스트는 fake transport 로 대체하므로
# URL 은 무관하다(실제 outbound 는 운영/4.4 가 배선). secret 아님.
DEFAULT_SERVER_BASE_URL = "https://localhost"
SERVER_URL_ENV = "RIDER_AGENT_SERVER_URL"


class RegistrationError(RuntimeError):
    """등록 실패. 메시지에 token/registration code 평문을 절대 담지 않는다(ADD-15)."""


class TransportError(RuntimeError):
    """transport 계층(HTTP) 실패. ``status_code`` 는 비밀 아님(있으면 진단용)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class Transport(Protocol):
    """주입 가능한 outbound JSON POST seam.

    실제 구현은 :class:`HttpTransport`(stdlib ``urllib``). 단위 테스트는 canned 응답을 주는
    fake 로 대체한다. 비-2xx 는 :class:`TransportError` 로 올린다(2xx 만 dict 반환).

    ``headers`` 는 **후방호환 선택 인자**(Story 4.3 추가)다 — register 는 본문의 일회용
    코드로 인증하므로 헤더를 전달하지 않지만(기본 ``None``), heartbeat(4.3)는
    ``Authorization: Bearer <token>`` 헤더를 실어야 한다. 새 outbound seam 을 만들지 않고
    이 단일 seam 을 재사용한다.
    """

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


class HttpTransport:
    """stdlib ``urllib`` 기반 JSON POST(새 HTTP 의존 0). ``urlopen`` 주입 가능.

    ``op_label`` 은 :class:`TransportError` 메시지의 operation 접두(기본 ``"agent register"``)
    다 — heartbeat(4.3) 같은 다른 호출자가 ``op_label="agent heartbeat"`` 로 구성하면
    운영 로그에서 register/heartbeat 실패를 구분할 수 있다(기본값 유지 시 register 동작 불변).
    """

    def __init__(
        self,
        *,
        urlopen: Callable[..., Any] = default_urlopen,
        timeout_seconds: int = 10,
        op_label: str = "agent register",
    ) -> None:
        self._urlopen = urlopen
        self._timeout = timeout_seconds
        self._op = op_label

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # 기존 Content-Type 을 보존하고 주입 headers 를 병합한다 — Content-Type 을
        # 덮어쓰지(drop) 않는다(주입 headers 가 우선이되 Content-Type 은 기본 유지).
        merged_headers = {"Content-Type": "application/json", **(headers or {})}
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=merged_headers,
            method="POST",
        )
        try:
            response = self._urlopen(request, timeout=self._timeout)
        except HTTPError as exc:
            # 4xx/5xx — 본문(에러 메시지)에 secret 이 섞일 수 있으니 읽지 않고 상태코드만 surfacing.
            raise TransportError(
                f"{self._op} HTTP error", status_code=exc.code
            ) from exc
        except (URLError, OSError) as exc:
            raise TransportError(f"{self._op} request failed") from exc

        try:
            with response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise TransportError(f"{self._op} response unreadable") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TransportError(f"{self._op} response was not JSON") from exc
        if not isinstance(data, dict):
            raise TransportError(f"{self._op} response was not an object")
        return data

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request = Request(url, headers=dict(headers or {}), method="GET")
        try:
            response = self._urlopen(request, timeout=self._timeout)
        except HTTPError as exc:
            # 4xx/5xx — 본문에 secret 이 섞일 수 있으니 읽지 않고 상태코드만 surfacing.
            raise TransportError(
                f"{self._op} HTTP error", status_code=exc.code
            ) from exc
        except (URLError, OSError) as exc:
            raise TransportError(f"{self._op} request failed") from exc

        try:
            with response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise TransportError(f"{self._op} response unreadable") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TransportError(f"{self._op} response was not JSON") from exc
        if not isinstance(data, dict):
            raise TransportError(f"{self._op} response was not an object")
        return data


@dataclass(frozen=True)
class MachineInfo:
    """등록 요청의 비밀 아님 머신 식별값(주입 가능 — 테스트가 결정적 값을 넣는다)."""

    machine_fingerprint: str
    hostname: str
    os: str
    agent_version: str


def _default_machine_fingerprint(hostname: str) -> str:
    # 안정적 per-machine 해시(비밀 아님). MAC/식별자 원문을 그대로 남기지 않으려 sha256 으로
    # 잘라 둔다. 결정적이라 같은 머신이면 같은 값.
    basis = f"{hostname}|{platform.machine()}|{platform.system()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def collect_machine_info(
    *,
    hostname: str | None = None,
    os_name: str | None = None,
    agent_version: str | None = None,
    machine_fingerprint: str | None = None,
) -> MachineInfo:
    """머신 정보를 모은다. 각 값은 주입 가능(테스트는 결정적 값을 넣어 네트워크/환경 비의존)."""

    host = hostname if hostname is not None else socket.gethostname()
    return MachineInfo(
        machine_fingerprint=(
            machine_fingerprint
            if machine_fingerprint is not None
            else _default_machine_fingerprint(host)
        ),
        hostname=host,
        os=os_name if os_name is not None else platform.platform(),
        agent_version=agent_version if agent_version is not None else __version__,
    )


def _register_url(base_url: str | None) -> str:
    base = base_url or os.getenv(SERVER_URL_ENV) or DEFAULT_SERVER_BASE_URL
    return base.rstrip("/") + REGISTER_PATH


def _failure_message(exc: TransportError) -> str:
    # token/code 평문 없이 상태코드만 — 호출부가 재등록 안내를 띄울 수 있게.
    if exc.status_code is not None:
        return f"agent registration rejected by server (status={exc.status_code})"
    return "agent registration request failed"


def _identity_from_response(response: dict[str, Any]) -> AgentIdentity:
    required = ("agent_id", "agent_token", "tenant_scope", "config_version")
    missing = [key for key in required if key not in response]
    if missing:
        # 키 이름만 — 값(특히 token)은 메시지에 넣지 않는다.
        raise RegistrationError(
            f"registration response missing fields: {sorted(missing)}"
        )
    agent_id = response["agent_id"]
    token = response["agent_token"]
    if not (isinstance(agent_id, str) and agent_id):
        raise RegistrationError("registration response had empty agent_id")
    if not (isinstance(token, str) and token):
        raise RegistrationError("registration response had empty agent_token")
    tenant_scope = response["tenant_scope"]
    return AgentIdentity(
        agent_id=agent_id,
        agent_token=token,
        tenant_scope=tenant_scope if isinstance(tenant_scope, dict) else {},
        config_version=str(response["config_version"]),
    )


def register_agent(
    registration_code: str,
    *,
    transport: Transport,
    store: SecretStore,
    identity_path: Path,
    base_url: str | None = None,
    machine_info: MachineInfo | None = None,
) -> AgentIdentity:
    """일회용 코드로 등록하고 발급 4값을 분리 저장한 :class:`AgentIdentity` 를 반환한다.

    멱등(AC1.2): 유효 local identity 가 이미 있으면 POST 없이 그대로 반환한다. 서버가 코드
    무효/이미 사용/거부로 응답하면 :class:`RegistrationError`(token/code 평문 미포함)로 올리고
    기존 identity 를 덮어쓰지 않는다.
    """

    identity_path = Path(identity_path)
    with agent_state_lock(identity_path.parent, "agent-registration.lock"):
        existing = load_local_agent_identity(store=store, identity_path=identity_path)
        if existing is not None:
            # 멱등: 일회용 코드를 다시 소모/덮어쓰지 않는다.
            return existing

        if not (registration_code or "").strip():
            raise RegistrationError("registration code is required")

        info = machine_info if machine_info is not None else collect_machine_info()
        body = {
            "registration_code": registration_code,
            "machine_fingerprint": info.machine_fingerprint,
            "hostname": info.hostname,
            "os": info.os,
            "agent_version": info.agent_version,
        }

        try:
            response = transport.post_json(_register_url(base_url), body)
        except TransportError as exc:
            # 기존 identity 가 없으므로 덮어쓸 것도 없다(위에서 early-return). 평문 미포함 메시지.
            raise RegistrationError(_failure_message(exc)) from exc

        identity = _identity_from_response(response)
        save_agent_identity(identity, store=store, identity_path=identity_path)
        return identity
