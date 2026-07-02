"""heartbeat 리포터 primitive — Agent 상태 주기 보고 (Story 4.3 / P3-03, FR-12).

이 모듈이 책임지는 것(범위 — primitive 만):

* :func:`build_heartbeat_payload` — ``POST /v1/agents/heartbeat`` 요청 본문을 합성한다.
  6필드(``metrics``/``capabilities``/``active_jobs``/``kakao_status``/``browser_profiles``/
  ``browser_slots``)
  + ``agent_id`` + 버전 drift 입력용 ``agent_version``(``rider_agent.__version__``). **token 은
  본문에 넣지 않는다**(인증은 헤더 — :func:`send_heartbeat`).
* :func:`send_heartbeat` — Task 1 payload 로 단발 POST 를 보내고 응답
  (``server_time``/``config_version``/``commands``)을 :class:`HeartbeatResult` 로 파싱한다.
  4.2 의 :class:`~rider_agent.registration.Transport`/``HttpTransport`` outbound seam 을
  재사용하고 ``agent_token`` 은 ``Authorization: Bearer`` 헤더로만 싣는다(평문 비노출).
* :class:`HeartbeatReporter` — 30~60초 주기 보고 loop primitive. ``threading.Event`` 와
  **주입 가능한 ``sleep``** 으로 짠 **순수 동기** 루프다(``asyncio`` 금지). 단발 실패가 루프를
  죽이지 않고(best-effort), ``401``/revoke 는 재등록 필요 상태로 surfacing 한다.

소유 분리(스코프 경계):

* **판정은 서버(Epic 5).** "2분 무신호 → offline" 임계와 "버전 ≠ 기대 → 식별"의 **판정 로직은
  서버** 다. Agent(4.3)는 (a) interval 을 ``[30, 60]`` 초로 보장해 2분 무신호가 ≥2회 누락이
  되게 하고, (b) payload 에 ``agent_version`` 을 실어 판정 입력을 제공할 뿐이다. Agent 가
  스스로 offline 판정/Admin 표시를 하지 않는다.
* **실제 소스 배선은 후속.** ``active_jobs``(4.4)·``browser_profiles``(4.5)·
  ``kakao_status``(4.6)·``metrics`` 는 **주입 가능한 provider** 로 두고 기본은 안전한
  빈/idle 값이다. ``start_heartbeat_thread()`` 로 startup 에 배선하는 것은 4.4 소유다 —
  본 모듈은 thread 로 띄울 수 있는 primitive 만 제공한다.

자기(own) 코드는 **순수 동기**이고 ``rider_crawl``/자기 패키지만 import 한다(역방향/
``rider_server`` import 0, ``asyncio`` 0) — 4.1 의 AST 가드가 자동 검사한다.
"""

from __future__ import annotations

import hashlib
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from rider_crawl.redaction import redact, redacted_error_event

from rider_agent import __version__
from rider_agent.registration import (
    DEFAULT_SERVER_BASE_URL,
    SERVER_URL_ENV,
    Transport,
    TransportError,
)
from rider_agent.secure_store import (
    TOKEN_STATUS_REVOKED,
    TOKEN_STATUS_VALID,
    AgentIdentity,
)

HEARTBEAT_PATH = "/v1/agents/heartbeat"

# Agent Job Types(capabilities 기본값) — **평문 상수**, enum/"정확히 N개" lock 금지.
# secure_store 의 ``TOKEN_STATUS_*`` 선례와 동일: 후속 워커(4.5/4.6/4.8/4.9)가 job type 을
# 늘려도 "정확히 N" lock 이 없어 다른 테스트를 깨지 않는다(memory: enum-member-count).
# [Source: architecture-contract.md Agent Job Types(120-129)]
CAPABILITY_CRAWL_BAEMIN = "CRAWL_BAEMIN"
CAPABILITY_CRAWL_COUPANG = "CRAWL_COUPANG"
CAPABILITY_AUTH_CHECK = "AUTH_CHECK"
CAPABILITY_OPEN_AUTH_BROWSER = "OPEN_AUTH_BROWSER"
# 쿠팡 email 2FA 자동복구 전용 job(OPEN_AUTH_BROWSER 의 수동 브라우저 열기와 분리). 이 Agent 가
# auth worker 를 시작하지 않으면 fallback 이 UNSUPPORTED 를 돌려줄 수 있으므로, 실제 실행 배선은
# ``worker_composition`` 의 auth worker 합성에서 한다.
CAPABILITY_AUTH_COUPANG_2FA = "AUTH_COUPANG_2FA"
CAPABILITY_KAKAO_SEND = "KAKAO_SEND"
CAPABILITY_CAPTURE_DIAGNOSTIC = "CAPTURE_DIAGNOSTIC"
# 카카오 인바운드 명령 트리거 라이더 조회(Phase 4). 실제 실행 배선(worker)이 없으면 fallback 이
# 처리하므로(미라우팅 → 안전 실패), 실행 합성은 ``worker_composition`` 에서 한다. 서버 job type
# ``JOB_TYPE_RIDER_LOOKUP`` 과 **문자열로 일치**하되 import 하지 않는다(단방향 — 값만 미러).
CAPABILITY_RIDER_LOOKUP = "RIDER_LOOKUP"

#: capabilities 기본값. tuple 로 두어 모듈 상수의 우발적 변이를 막는다(superset 허용 — "정확히
#: N" lock 금지라 후속 워커가 job type 을 늘려도 무탈).
DEFAULT_CAPABILITIES: tuple[str, ...] = (
    CAPABILITY_CRAWL_BAEMIN,
    CAPABILITY_CRAWL_COUPANG,
    CAPABILITY_AUTH_CHECK,
    CAPABILITY_OPEN_AUTH_BROWSER,
    CAPABILITY_AUTH_COUPANG_2FA,
    CAPABILITY_KAKAO_SEND,
    CAPABILITY_CAPTURE_DIAGNOSTIC,
    CAPABILITY_RIDER_LOOKUP,
)

# interval clamp 범위. 상한(≤60)이 offline 판정(서버 2분 임계)에 load-bearing 하다 —
# 60 초과를 막아야 2분 무신호가 정상 지연이 아닌 ≥2회 누락이 된다. 하한 30 은 rate-limit 보호.
# [Source: implementation-contract.md P3-03(60), operations-security-test-contract.md(25)]
MIN_HEARTBEAT_INTERVAL_SECONDS = 30
MAX_HEARTBEAT_INTERVAL_SECONDS = 60

# heartbeat 간격에 더하는 per-Agent stable jitter 비율(0~이 값 사이 결정적 오프셋). 여러 Agent
# 가 같은 초에 heartbeat 를 몰아 보내지 않게 분산한다(thundering herd 완화, random 미사용).
DEFAULT_HEARTBEAT_JITTER_RATIO = 0.1


def stable_jitter_ratio(seed: str) -> float:
    """``seed``(agent_id 등) 기반 결정적 jitter 비율 [0,1) 을 만든다(random 미사용).

    같은 seed 는 항상 같은 값, 다른 seed 는 고르게 다른 값 → 여러 Agent 의 polling/heartbeat
    가 같은 초에 겹치지 않고 분산된다(thundering herd 완화). sha256 앞 8바이트를 [0,1) 로 정규화.
    """

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big")
    return bucket / float(1 << 64)


def jittered_interval(base_seconds: float, *, seed: str, ratio: float) -> float:
    """``base_seconds`` 에 seed 기반 안정 jitter(최대 ``base*ratio``)를 더한다(결정적)."""

    base = max(0.0, float(base_seconds))
    span = base * max(0.0, float(ratio))
    return base + span * stable_jitter_ratio(seed)

# provider 미주입 시 안전 기본값(후속 스토리 소유 소스의 placeholder).
DEFAULT_KAKAO_STATUS: dict[str, Any] = {"state": "disabled", "queue_depth": 0}

# heartbeat 전송 transport 의 operation 라벨(HttpTransport(op_label=...) 로 운영 로그 구분).
HEARTBEAT_OP_LABEL = "agent heartbeat"

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

def _resolve(provider: Any) -> Any:
    """provider 가 callable 이면 호출해 값을 얻고, 아니면 그대로 값으로 쓴다.

    후속 스토리는 실제 소스를 callable 로 주입하고, 테스트/기본은 정적 값을 쓴다
    (``run_once`` 가 crawler/sender 를 주입하는 규율과 동형).
    """

    return provider() if callable(provider) else provider


def _normalize_kakao_status(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return {"state": value}
    return {"state": str(value)}


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


def _normalize_browser_slots(value: Any) -> dict[str, Any]:
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


def default_metrics() -> dict[str, Any]:
    """기본 metrics — **stdlib 만**(``psutil`` 등 새 의존 금지). 최소 식별/런타임 정보.

    풍부한 시스템 metrics 는 후속/운영이 provider 로 주입한다. 여기서는 4.1 deps-pin 가드를
    깨지 않는 stdlib(``platform``)만으로 안전한 최소 dict 를 만든다.
    """

    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }


def build_heartbeat_payload(
    identity: AgentIdentity,
    *,
    capabilities: Sequence[str] | Callable[[], Sequence[str]] | None = None,
    metrics_provider: Any = None,
    active_jobs_provider: Any = None,
    kakao_status_provider: Any = None,
    browser_profiles_provider: Any = None,
    browser_slots_provider: Any = None,
) -> dict[str, Any]:
    """``POST /v1/agents/heartbeat`` 요청 본문을 합성한다(token 은 본문에 넣지 않는다).

    6필드(``metrics``/``capabilities``/``active_jobs``/``kakao_status``/``browser_profiles``/
    ``browser_slots``)
    + ``agent_id`` + ``agent_version``(버전 drift 입력). 각 provider 는 값 또는 callable 이며,
    미주입 시 안전 기본값(빈 리스트/disabled 상태 dict/stdlib metrics/6종 capabilities)을 쓴다.
    [Source: data-api-contract.md(67-69), architecture-contract.md(120-129)]
    """

    caps = _resolve(capabilities) if capabilities is not None else DEFAULT_CAPABILITIES
    metrics = (
        _resolve(metrics_provider) if metrics_provider is not None else default_metrics()
    )
    active_jobs = (
        _resolve(active_jobs_provider) if active_jobs_provider is not None else []
    )
    kakao_status = (
        _resolve(kakao_status_provider)
        if kakao_status_provider is not None
        else DEFAULT_KAKAO_STATUS
    )
    browser_profiles = (
        _resolve(browser_profiles_provider)
        if browser_profiles_provider is not None
        else []
    )
    browser_slots = (
        _resolve(browser_slots_provider) if browser_slots_provider is not None else {}
    )

    return {
        "agent_id": identity.agent_id,
        "agent_version": __version__,
        "metrics": metrics,
        "capabilities": list(caps),
        "active_jobs": list(active_jobs),
        "kakao_status": _normalize_kakao_status(kakao_status),
        "browser_profiles": list(browser_profiles),
        "browser_slots": _normalize_browser_slots(browser_slots),
    }


@dataclass(frozen=True)
class HeartbeatResult:
    """heartbeat 응답 파싱 결과. ``commands`` 실행/적용은 본 스토리 범위 아님(파싱까지만)."""

    server_time: Any | None = None
    config_version: Any | None = None
    commands: list[Any] = field(default_factory=list)
    lease_extension: dict[str, Any] = field(default_factory=dict)


def _heartbeat_url(base_url: str | None) -> str:
    # 4.2 _register_url 패턴과 정합: 주입 base_url > env > 기본 placeholder. secret 아님.
    base = base_url or os.getenv(SERVER_URL_ENV) or DEFAULT_SERVER_BASE_URL
    return base.rstrip("/") + HEARTBEAT_PATH


def _auth_headers(token: str) -> dict[str, str]:
    # Agent API = token-auth. token 은 헤더에만 — 로그/payload/예외에 통째로 출력하지 않는다.
    # 헤더명/scheme 은 서버 계약(Epic 5)이라 합리적 기본(Bearer)을 쓰고 통합 시 재조정 가능.
    return {"Authorization": f"Bearer {token}"}


def _result_from_response(response: dict[str, Any]) -> HeartbeatResult:
    commands = response.get("commands")
    lease_extension = response.get("lease_extension")
    return HeartbeatResult(
        server_time=response.get("server_time"),
        config_version=response.get("config_version"),
        commands=list(commands) if isinstance(commands, list) else [],
        lease_extension=dict(lease_extension) if isinstance(lease_extension, dict) else {},
    )


def send_heartbeat(
    identity: AgentIdentity,
    *,
    transport: Transport,
    base_url: str | None = None,
    capabilities: Sequence[str] | Callable[[], Sequence[str]] | None = None,
    metrics_provider: Any = None,
    active_jobs_provider: Any = None,
    kakao_status_provider: Any = None,
    browser_profiles_provider: Any = None,
    browser_slots_provider: Any = None,
) -> HeartbeatResult:
    """단발 heartbeat 를 보내고 응답을 :class:`HeartbeatResult` 로 파싱한다.

    payload 는 :func:`build_heartbeat_payload` 로 만들고, ``agent_token`` 은
    ``Authorization: Bearer`` 헤더로만 싣는다(본문에 넣지 않는다). 비-2xx 는 주입 transport 가
    :class:`~rider_agent.registration.TransportError`(status_code 만, 본문 미읽음)로 올린다 —
    응답 본문에 섞인 secret 노출을 막는 4.2 정책을 그대로 계승한다.
    """

    payload = build_heartbeat_payload(
        identity,
        capabilities=capabilities,
        metrics_provider=metrics_provider,
        active_jobs_provider=active_jobs_provider,
        kakao_status_provider=kakao_status_provider,
        browser_profiles_provider=browser_profiles_provider,
        browser_slots_provider=browser_slots_provider,
    )
    response = transport.post_json(
        _heartbeat_url(base_url),
        payload,
        headers=_auth_headers(identity.agent_token),
    )
    return _result_from_response(response)


def clamp_interval(seconds: float) -> float:
    """interval 을 ``[30, 60]`` 초로 검증/clamp 한다(경계 포함).

    상한 clamp(≤60)가 offline 판정에 load-bearing — 60 초과를 허용하면 2분 무신호가 정상
    주기 지연으로 보일 수 있다. 하한 30 은 서버 rate-limit 보호다.
    """

    if seconds < MIN_HEARTBEAT_INTERVAL_SECONDS:
        return float(MIN_HEARTBEAT_INTERVAL_SECONDS)
    if seconds > MAX_HEARTBEAT_INTERVAL_SECONDS:
        return float(MAX_HEARTBEAT_INTERVAL_SECONDS)
    return float(seconds)


class HeartbeatReporter:
    """30~60초 주기 heartbeat loop primitive(순수 동기·best-effort·복원력).

    :meth:`run` 은 ``stop_event`` 가 set 될 때까지 매 interval 마다 1회 보고한다.
    ``threading.Thread(target=reporter.run)`` 형태로 4.4 가 startup 에서 띄운다 — 본 클래스는
    thread 를 직접 띄우지 않는다(``start_heartbeat_thread()`` 배선은 4.4 소유). ``sleep`` 은
    주입 가능해 테스트가 실 대기 없이 결정적으로 검증한다.

    복원력(AC2): 단발 ``send_heartbeat`` 가 예외를 던져도 루프는 죽지 않고 다음 주기로 진행한다
    (best-effort). 에러는 :func:`redact`/:func:`redacted_error_event` 로 마스킹해 기록한다.
    ``401``/revoke 응답은 :data:`~rider_agent.secure_store.TOKEN_STATUS_REVOKED` 로 surfacing
    하되(``on_status`` 콜백·:attr:`needs_registration`) crash·무한 즉시 스핀 없이 처리한다 —
    실제 중단/재등록 반응 배선은 4.4/운영 소유.
    """

    def __init__(
        self,
        identity: AgentIdentity,
        *,
        transport: Transport,
        interval_seconds: float = MIN_HEARTBEAT_INTERVAL_SECONDS,
        base_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        stop_event: threading.Event | None = None,
        capabilities: Sequence[str] | Callable[[], Sequence[str]] | None = None,
        metrics_provider: Any = None,
        active_jobs_provider: Any = None,
        kakao_status_provider: Any = None,
        browser_profiles_provider: Any = None,
        browser_slots_provider: Any = None,
        on_status: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
        jitter_ratio: float = DEFAULT_HEARTBEAT_JITTER_RATIO,
    ) -> None:
        self.identity = identity
        self._transport = transport
        self.interval_seconds = clamp_interval(interval_seconds)
        # per-Agent stable jitter(agent_id seed) — heartbeat 가 같은 초에 몰리지 않게 분산.
        self._jitter_ratio = max(0.0, float(jitter_ratio))
        self._jitter_seed = str(getattr(identity, "agent_id", "") or "")
        self._base_url = base_url
        self._sleep = sleep
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._capabilities = capabilities
        self._metrics_provider = metrics_provider
        self._active_jobs_provider = active_jobs_provider
        self._kakao_status_provider = kakao_status_provider
        self._browser_profiles_provider = browser_profiles_provider
        self._browser_slots_provider = browser_slots_provider
        self._on_status = on_status
        self._log = log
        #: 현재 surfacing 상태(4.2 의 ``TOKEN_STATUS_*`` 어휘 재사용 — 새 ad-hoc 플래그 금지).
        self.token_status: str = TOKEN_STATUS_VALID
        self.last_result: HeartbeatResult | None = None
        self.last_error_event: dict[str, Any] | None = None

    @property
    def needs_registration(self) -> bool:
        """``401``/revoke 로 재등록이 필요한 상태인가(서버 소유 반응은 4.4/운영)."""

        return self.token_status == TOKEN_STATUS_REVOKED

    def stop(self) -> None:
        """루프 정지를 요청한다(thread-safe)."""

        self._stop_event.set()

    def run(self) -> None:
        """stop 이 set 될 때까지 매 interval 마다 1회 보고하는 주기 루프(thread target)."""

        while not self._stop_event.is_set():
            self.report_once()
            if self._stop_event.is_set():
                break
            # 매 주기 끝에 대기 — 어떤 분기에서도 즉시 재호출(무한 스핀)하지 않는다.
            self._sleep(self._next_interval())

    def _next_interval(self) -> float:
        """다음 heartbeat 전 대기(초): interval 에서 per-Agent stable jitter 만큼 **빼서** 분산.

        jitter 를 위로 더하면 60 초 상한(offline 판정 load-bearing — 60 초과 금지)을 넘을 수
        있으므로, 대신 interval 보다 짧게(최대 ``interval*ratio`` 만큼) 만들어 여러 Agent 의
        heartbeat 가 같은 초에 겹치지 않게 한다. 결과는 ``clamp_interval`` 로 [30,60] 안에 든다.
        """

        span = max(0.0, self.interval_seconds * self._jitter_ratio)
        reduced = self.interval_seconds - span * stable_jitter_ratio(self._jitter_seed)
        return clamp_interval(reduced)

    def report_once(self) -> HeartbeatResult | None:
        """단발 보고. 어떤 예외도 루프를 죽이지 않게 흡수한다(best-effort)."""

        try:
            result = send_heartbeat(
                self.identity,
                transport=self._transport,
                base_url=self._base_url,
                capabilities=self._capabilities,
                metrics_provider=self._metrics_provider,
                active_jobs_provider=self._active_jobs_provider,
                kakao_status_provider=self._kakao_status_provider,
                browser_profiles_provider=self._browser_profiles_provider,
                browser_slots_provider=self._browser_slots_provider,
            )
        except TransportError as exc:
            self._handle_transport_error(exc)
            return None
        except Exception as exc:  # noqa: BLE001 — best-effort: 어떤 예외도 thread 를 죽이지 않는다.
            self._record_error("AGENT_HEARTBEAT_ERROR", "heartbeat failed", exc)
            return None

        # 성공 → 정상 상태로 회복(이전 revoked 이후 재발급되면 valid 로 복귀 가능).
        self._set_status(TOKEN_STATUS_VALID)
        self.last_result = result
        lease_extension = result.lease_extension
        failed_job_ids = lease_extension.get("failed_job_ids")
        if (
            lease_extension.get("status") == "degraded"
            or (isinstance(failed_job_ids, list) and failed_job_ids)
        ):
            self._record_error(
                "AGENT_HEARTBEAT_LEASE_EXTENSION_DEGRADED",
                "heartbeat lease extension degraded",
                None,
            )
        return result

    def _handle_transport_error(self, exc: TransportError) -> None:
        if exc.status_code == 401:
            # 재등록 필요 상태로 surfacing(서버가 token 을 revoke). 루프는 다음 주기로 진행한다.
            self._set_status(TOKEN_STATUS_REVOKED)
            self._record_error(
                "AGENT_HEARTBEAT_REVOKED",
                "heartbeat rejected: token revoked — re-registration required",
                exc,
            )
        elif exc.status_code == 403:
            # 403 = token 자체는 유효하나 다른 agent identity 로 해석됨(mismatch). 401 과 같은
            # 재등록 필요 상태로 두되, 운영자가 "revoked" 와 "identity mismatch" 를 구분하도록
            # 이벤트 코드를 분리한다(generic error/backoff 로 묻지 않는다).
            self._set_status(TOKEN_STATUS_REVOKED)
            self._record_error(
                "AGENT_HEARTBEAT_IDENTITY_REJECTED",
                "heartbeat rejected: agent identity mismatch — re-registration required",
                exc,
            )
        else:
            # 네트워크/5xx 등 일시 실패 — 상태는 그대로 두고 다음 주기에 재시도.
            self._record_error("AGENT_HEARTBEAT_ERROR", "heartbeat send failed", exc)

    def _record_error(
        self, code: str, message: str, error: BaseException | None
    ) -> None:
        # redacted_error_event 가 message/error 본문을 redact 한다 — token 평문이 남지 않는다.
        event = redacted_error_event(code, message, error)
        self.last_error_event = event
        if self._log is not None:
            # 헤더 dict 를 통째로 로깅하지 않는다. 이벤트 문자열도 한 번 더 redact 통과.
            self._log(redact(str(event)))

    def _set_status(self, status: str) -> None:
        if status == self.token_status:
            return
        self.token_status = status
        if self._on_status is not None:
            self._on_status(status)
