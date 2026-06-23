"""outbound HTTPS job 폴링/claim/complete 루프 + lease 인지 + startup 배선 (Story 4.4 / P3-04).

이 모듈이 책임지는 것(범위 — client 루프 primitive + 배선만):

* **claim/complete/events HTTP client.** :func:`claim_jobs`/:func:`complete_job`/
  :func:`emit_job_event` 가 4.2/4.3 의 :class:`~rider_agent.registration.Transport`/
  ``HttpTransport`` outbound seam 을 ``Authorization: Bearer`` 헤더로 호출한다. 새 HTTP
  의존(``requests``/``httpx``)을 도입하지 않는다(4.1 import-root 가드 green 유지).
* **루프 primitive.** :class:`JobRunner` 가 architecture-contract ``main_loop``(claim→
  execute→complete)을 ``threading.Event``(stop) + **주입 sleep/now** 로 짠 **순수 동기**
  루프로 구현한다(``asyncio`` 금지). 단발 실패가 루프 thread 를 죽이지 않고(best-effort),
  ``401``/revoke 는 재등록 필요 상태로 surfacing 한다.
* **lease 인지(client 3가지만).** (a) claim 응답의 ``lease_expires_at`` 를 기록, (b) in-flight
  job 을 heartbeat ``active_jobs`` provider 로 노출(서버 연장 입력), (c) complete 때 서버
  거부(409/410) 흡수. **단일-claim 강제·lease 부여/연장/stale 회수/재할당/최종 complete
  소유 검증은 서버(Epic 5) 소유.**
* **startup 배선.** :func:`start_heartbeat_thread` 로 4.3 :class:`HeartbeatReporter` 를 띄우고,
  :func:`run_agent` 가 architecture-contract startup(load identity → validate token →
  start heartbeat thread → main loop)을 구현한다. ``active_jobs_provider=runner.active_jobs``
  배선으로 "heartbeat 로 lease 연장"의 client 측을 완성한다.

소유 분리(스코프 경계):

* **실제 job 실행(``execute_job`` 워커)은 후속 소유** — ``CRAWL_*``=4.5, ``KAKAO_SEND``=4.6,
  ``AUTH_*``=4.8/4.9. 본 모듈은 ``execute_job`` 를 **주입 seam** 으로 받고, 기본
  :func:`default_execute_job` 는 ``UNSUPPORTED_JOB_TYPE`` 실패 결과를 돌려 루프가 complete 로
  깔끔히 보고하게 한다(빈 stub 워커 파일을 만들지 않는다).
* **서버 측 queue/단일-claim/lease 강제·연장·stale sweep/재할당/job 생성/Admin 은 Epic 5** —
  본 모듈은 client + 주입 transport stub 검증.

자기(own) 코드는 **순수 동기**이고 ``rider_crawl``/자기 패키지만 import 한다(역방향/
``rider_server`` import 0, ``asyncio`` 0) — 4.1 의 AST 가드가 자동 검사한다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from rider_crawl.redaction import redact, redact_mapping, redacted_error_event

from rider_agent.heartbeat import (
    DEFAULT_CAPABILITIES,
    MIN_HEARTBEAT_INTERVAL_SECONDS,
    HeartbeatReporter,
    default_metrics,
    jittered_interval,
)
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
    TokenValidation,
    load_local_agent_identity,
    validate_agent_token,
)
from rider_agent.worker_composition import compose_execute_job

# ── 경로 상수(4.2 _register_url·4.3 _heartbeat_url 패턴과 정합) ────────────────

CLAIM_PATH = "/v1/jobs/claim"

# jobs 호출 transport 의 operation 라벨(HttpTransport(op_label=...) 로 운영 로그 구분 —
# registration.py 무변경, 4.3 op_label seam 재사용).
JOBS_OP_LABEL = "agent jobs"

# job 없을 때 재폴링 전 대기(초). architecture-contract main_loop 의 short_poll_interval.
DEFAULT_SHORT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_COMPLETE_RETRY_ATTEMPTS = 3
DEFAULT_COMPLETE_RETRY_DELAY_SECONDS = 1.0

# claim 실패(network/5xx) 연속 시 지수 backoff 상한(초)·배수. 서버 복구 직후 모든 Agent 가
# 같은 초에 몰리는 thundering herd 를 막는다. 401/revoke 는 backoff 대상이 아니라 재등록 surfacing.
DEFAULT_CLAIM_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_CLAIM_BACKOFF_MAX_SECONDS = 60.0
CLAIM_BACKOFF_MULTIPLIER = 2.0
# per-Agent stable jitter 비율(0~이 값 사이의 결정적 오프셋을 대기에 더한다). random 미사용 —
# agent_id 해시 기반이라 같은 Agent 는 항상 같은 jitter, Agent 간엔 서로 다르게 분산된다.
DEFAULT_POLL_JITTER_RATIO = 0.2
COMPLETE_REPORT_SENT = "sent"
COMPLETE_REPORT_LEASE_LOST = "lease_lost"
COMPLETE_REPORT_FAILED = "failed"
COMPLETE_REPORT_REVOKED = "revoked"

# ── job status / 이벤트 — **평문 상수**, enum/"정확히 N개" lock 금지 ───────────
# secure_store ``TOKEN_STATUS_*``·heartbeat ``DEFAULT_CAPABILITIES`` 선례: 후속 워커가
# status/event 를 늘려도 다른 테스트를 깨지 않는다(memory: enum-member-count).
JOB_STATUS_SUCCESS = "success"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_LEASE_LOST = "lease_lost"  # client-side surfacing(서버에 success 보고 안 함)

# 기본 executor 가 미지원 type 에 돌려주는 에러 코드(UPPER_SNAKE, secret 아님).
ERROR_UNSUPPORTED_JOB_TYPE = "UNSUPPORTED_JOB_TYPE"

# best-effort 루프가 기록하는 에러 코드(UPPER_SNAKE, secret 아님).
ERROR_JOB_CLAIM = "AGENT_JOB_CLAIM_ERROR"
ERROR_JOB_EXECUTION = "AGENT_JOB_EXECUTION_ERROR"
ERROR_JOB_COMPLETE = "AGENT_JOB_COMPLETE_ERROR"
ERROR_JOB_REVOKED = "AGENT_JOB_REVOKED"
ERROR_JOB_LEASE_LOST = "AGENT_JOB_LEASE_LOST"
ERROR_JOB_EVENT = "AGENT_JOB_EVENT_ERROR"
# preflight denied / 호출 불가 시 기록하는 에러 코드(UPPER_SNAKE, secret 아님).
ERROR_JOB_PREFLIGHT_DENIED = "AGENT_JOB_PREFLIGHT_DENIED"

# preflight denied 결과 result_json 에 남기는 reason(server reason 그대로 보존, 없으면 기본값).
# server preflight 호출 자체가 실패하면 fail-closed 로 browser open 을 막고 이 reason 을 남긴다.
PREFLIGHT_REASON_DEFAULT = "payload_expired"
PREFLIGHT_REASON_UNAVAILABLE = "preflight_unavailable"

# server 가 브라우저를 여는 job 만 preflight 대상으로 본다(crawl/auth). 나머지는 preflight 생략
# (불필요한 왕복 0) — 이 type 집합은 heartbeat capability 문자열과 1:1 미러(import 금지).
_PREFLIGHT_JOB_TYPES = frozenset(
    {"CRAWL_BAEMIN", "CRAWL_COUPANG", "OPEN_AUTH_BROWSER"}
)

# 최소 진행 이벤트(claim 직후). 풍부한 진단 이벤트는 워커(4.5+) 소유.
EVENT_TYPE_JOB_STARTED = "JOB_STARTED"
SEVERITY_INFO = "info"


# ── 도메인 모델(frozen) — token 필드 없음(인증은 헤더) ────────────────────────


@dataclass(frozen=True)
class ClaimedJob:
    """서버가 claim 으로 돌려준 job 한 건. ``lease_expires_at`` 는 서버 부여값을 그대로 보존한다.

    ``payload`` 는 응답 raw dict(워커가 type 별로 해석) — 본 스토리는 type/target_id/
    lease_expires_at 만 직접 쓰고 나머지는 후속 워커(4.5+)가 소비한다. token 필드는 없다.
    """

    job_id: str
    type: str = ""
    target_id: Any | None = None
    lease_expires_at: Any | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "ClaimedJob | None":
        """단일 job dict 를 파싱한다. 비-dict/``job_id`` 누락은 fail-closed(``None``)."""

        if not isinstance(data, dict):
            return None
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return None
        return cls(
            job_id=job_id,
            type=str(data.get("type") or ""),
            target_id=data.get("target_id"),
            lease_expires_at=data.get("lease_expires_at"),
            payload=dict(data),
        )

    @classmethod
    def list_from_response(cls, response: Any) -> list["ClaimedJob"]:
        """claim 응답에서 ``jobs`` 리스트를 파싱한다. 누락/비-list 는 빈 리스트(fail-closed)."""

        if not isinstance(response, dict):
            return []
        jobs = response.get("jobs")
        if not isinstance(jobs, list):
            return []
        parsed = [cls.from_dict(item) for item in jobs]
        return [job for job in parsed if job is not None]


@dataclass(frozen=True)
class JobResult:
    """job 실행 결과. ``error_message_redacted`` 는 반드시 redact 통과값만 담는다(평문 금지).

    ``agent_id``/``started_at``/``finished_at`` 는 :class:`JobRunner` 가 주입 ``now`` 로
    측정해 채운다(executor 는 비워도 됨 — :func:`make_success_result`/:func:`make_failure_result`).
    """

    status: str
    result_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message_redacted: str | None = None
    metrics: dict[str, Any] | None = None
    agent_id: str = ""
    started_at: float | None = None
    finished_at: float | None = None


@dataclass(frozen=True)
class JobEvent:
    """job 진행 이벤트. ``message_redacted`` 는 redact 통과값, artifact 는 sanitized ref 만."""

    event_type: str
    severity: str
    message_redacted: str
    artifact_refs: tuple[Any, ...] = ()


def make_success_result(
    *,
    result_json: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> JobResult:
    """성공 결과를 만든다(error 필드 없음)."""

    return JobResult(
        status=JOB_STATUS_SUCCESS, result_json=result_json, metrics=metrics
    )


def make_failure_result(
    error_code: str,
    message: str,
    *,
    error: BaseException | None = None,
    result_json: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    status: str = JOB_STATUS_FAILED,
) -> JobResult:
    """실패 결과를 만든다. ``error_message_redacted`` 는 :func:`redacted_error_event` 로 생성해
    raw error/OTP/secret/HTML 평문이 남지 않게 한다(중복 마스킹 로직 금지)."""

    event = redacted_error_event(error_code, message, error)
    redacted_message = event.get("error_message_redacted") or event["message_redacted"]
    return JobResult(
        status=status,
        error_code=error_code,
        error_message_redacted=redacted_message,
        result_json=result_json,
        metrics=metrics,
    )


def make_job_event(
    event_type: str,
    severity: str,
    message: str,
    *,
    artifact_refs: Sequence[Any] = (),
) -> JobEvent:
    """진행 이벤트를 만든다. ``message`` 는 :func:`redact` 를 통과시켜 secret/OTP 가 새지 않게 한다.

    ``artifact_refs`` 는 **sanitized ref 만** 받는 계약이다(raw HTML/마스킹 안 된 스크린샷 금지) —
    호출자가 ref 화한 식별자만 넘긴다.
    """

    return JobEvent(
        event_type=event_type,
        severity=severity,
        message_redacted=redact(message),
        artifact_refs=tuple(artifact_refs),
    )


# ── 기본 executor(워커 미생성 — 주입 seam) ────────────────────────────────────


def default_execute_job(job: ClaimedJob) -> JobResult:
    """미지원 job type 에 대한 기본 실행 결과(``UNSUPPORTED_JOB_TYPE`` 실패).

    후속 워커(4.5/4.6/4.8/4.9)가 type 별 executor 를 ``execute_job`` 로 주입한다. 여기서는
    **빈 stub 워커 파일을 만들지 않고**, 루프가 complete 로 깔끔히 보고할 실패 결과만 돌려준다.
    ``job.type`` 은 capability 문자열(secret 아님)이지만 메시지도 redact 를 통과한다.
    """

    return make_failure_result(
        ERROR_UNSUPPORTED_JOB_TYPE,
        f"unsupported job type: {job.type}",
    )


# ── URL/헤더 헬퍼 ─────────────────────────────────────────────────────────────


def _server_base(base_url: str | None) -> str:
    # 4.2 _register_url 패턴: 주입 base_url > env > 기본 placeholder. secret 아님.
    return (base_url or os.getenv(SERVER_URL_ENV) or DEFAULT_SERVER_BASE_URL).rstrip("/")


def _claim_url(base_url: str | None) -> str:
    return _server_base(base_url) + CLAIM_PATH


def _complete_url(base_url: str | None, job_id: str) -> str:
    return f"{_server_base(base_url)}/v1/jobs/{job_id}/complete"


def _preflight_url(base_url: str | None, job_id: str) -> str:
    return f"{_server_base(base_url)}/v1/jobs/{job_id}/preflight"


def _events_url(base_url: str | None, job_id: str) -> str:
    return f"{_server_base(base_url)}/v1/jobs/{job_id}/events"


def _auth_headers(token: str) -> dict[str, str]:
    # Agent API = token-auth. token 은 헤더에만 — 로그/payload/예외에 통째로 출력하지 않는다.
    # 4.3 heartbeat 와 동일 Bearer 패턴.
    return {"Authorization": f"Bearer {token}"}


# ── client 함수(claim/complete/events) ────────────────────────────────────────


def claim_jobs(
    identity: AgentIdentity,
    *,
    transport: Transport,
    capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
    max_jobs: int = 1,
    base_url: str | None = None,
) -> list[ClaimedJob]:
    """``POST /v1/jobs/claim`` 으로 job 을 claim 하고 :class:`ClaimedJob` 리스트로 파싱한다.

    본문은 ``agent_id``/``capabilities``/``max_jobs`` 이고 ``agent_token`` 은 헤더로만 싣는다.
    비-2xx 는 주입 transport 가 :class:`TransportError`(status_code 만)로 올린다(본문 미읽음).
    """

    body = {
        "agent_id": identity.agent_id,
        "capabilities": list(capabilities),
        "max_jobs": max_jobs,
    }
    response = transport.post_json(
        _claim_url(base_url), body, headers=_auth_headers(identity.agent_token)
    )
    return ClaimedJob.list_from_response(response)


def complete_job(
    identity: AgentIdentity,
    job_id: str,
    result: JobResult,
    *,
    transport: Transport,
    base_url: str | None = None,
    completion_id: str | None = None,
) -> dict[str, Any]:
    """``POST /v1/jobs/{job_id}/complete`` 로 결과를 보고한다.

    본문에 ``status``/``result_json``/``error_code``/``error_message_redacted``/``metrics``
    (+ ``agent_id``/``started_at``/``finished_at``)을 싣는다. ``error_message_redacted`` 는
    이미 redact 통과값이라 raw error/secret 평문이 없다. token 은 헤더로만.
    """

    body = _complete_body(result, completion_id=completion_id)
    return transport.post_json(
        _complete_url(base_url, job_id), body, headers=_auth_headers(identity.agent_token)
    )


def preflight_job(
    identity: AgentIdentity,
    job_id: str,
    *,
    transport: Transport,
    base_url: str | None = None,
) -> dict[str, Any]:
    """``POST /v1/jobs/{job_id}/preflight`` — 브라우저/profile 열기 전 server 유효성 확인.

    server 가 ``{"allowed": bool, "reason": str|None, "server_time": str}`` 를 돌려준다. token 은
    헤더로만. 비-2xx 는 주입 transport 가 :class:`TransportError` 로 올린다(호출부가 fail-closed
    처리). 본문 모양은 :func:`claim_jobs`/:func:`complete_job` 과 동형(snake_case).
    """

    return transport.post_json(
        _preflight_url(base_url, job_id),
        {"agent_id": identity.agent_id},
        headers=_auth_headers(identity.agent_token),
    )


def _complete_body(
    result: JobResult,
    *,
    completion_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": result.status,
        "result_json": result.result_json,
        "error_code": result.error_code,
        "error_message_redacted": result.error_message_redacted,
        "metrics": result.metrics,
        "agent_id": result.agent_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    }
    if completion_id:
        body["completion_id"] = completion_id
    return body


def emit_job_event(
    identity: AgentIdentity,
    job_id: str,
    event: JobEvent,
    *,
    transport: Transport,
    base_url: str | None = None,
) -> dict[str, Any]:
    """``POST /v1/jobs/{job_id}/events`` 로 진행 이벤트를 보고한다(본문에 secret/OTP/raw 없음).

    본문은 ``event_type``/``severity``/``message_redacted``/``artifact_refs`` 이고
    ``message_redacted`` 는 :func:`make_job_event` 가 이미 redact 통과시킨 값이다. token 은 헤더.
    """

    body = {
        "event_type": event.event_type,
        "severity": event.severity,
        "message_redacted": event.message_redacted,
        "artifact_refs": list(event.artifact_refs),
    }
    return transport.post_json(
        _events_url(base_url, job_id), body, headers=_auth_headers(identity.agent_token)
    )


class CompleteOutbox:
    """Small durable store for completed job reports that failed to reach the server."""

    def __init__(self, path: Any | None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._path is not None

    def pending_records(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._read_payload()
            return [dict(record) for record in payload["pending"] if isinstance(record, dict)]

    def put(self, *, job_id: str, body: dict[str, Any], completion_id: str) -> None:
        if self._path is None:
            return
        record = {
            "job_id": job_id,
            "completion_id": completion_id,
            "body": redact_mapping(body),
        }
        with self._lock:
            payload = self._read_payload()
            payload["pending"] = [
                item
                for item in payload["pending"]
                if item.get("completion_id") != completion_id
            ]
            payload["pending"].append(record)
            self._write_payload(payload)

    def mark_sent(self, completion_id: str) -> None:
        if self._path is None:
            return
        with self._lock:
            payload = self._read_payload()
            payload["pending"] = [
                item
                for item in payload["pending"]
                if item.get("completion_id") != completion_id
            ]
            self._write_payload(payload)

    def mark_discarded(self, completion_id: str, *, reason: str) -> None:
        if self._path is None:
            return
        with self._lock:
            payload = self._read_payload()
            remaining = []
            discarded = payload["discarded"]
            for item in payload["pending"]:
                if item.get("completion_id") == completion_id:
                    discarded.append(
                        {
                            "job_id": item.get("job_id"),
                            "completion_id": completion_id,
                            "reason": redact(reason),
                        }
                    )
                else:
                    remaining.append(item)
            payload["pending"] = remaining
            payload["discarded"] = discarded
            self._write_payload(payload)

    def _read_payload(self) -> dict[str, list[dict[str, Any]]]:
        if self._path is None or not self._path.exists():
            return {"pending": [], "discarded": []}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"pending": [], "discarded": []}
        if not isinstance(payload, dict):
            return {"pending": [], "discarded": []}
        pending = payload.get("pending")
        discarded = payload.get("discarded")
        return {
            "pending": [
                item for item in pending if isinstance(item, dict)
            ] if isinstance(pending, list) else [],
            "discarded": [
                item for item in discarded if isinstance(item, dict)
            ] if isinstance(discarded, list) else [],
        }

    def _write_payload(self, payload: dict[str, Any]) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        tmp_path = self._path.with_name(f"{self._path.name}.tmp")
        tmp_path.write_text(encoded, encoding="utf-8")
        tmp_path.replace(self._path)


# ── 루프 primitive + lease 인지 + 결과 보고 ───────────────────────────────────


class JobRunner:
    """claim→execute→complete 메인 루프 primitive(순수 동기·best-effort·lease 인지).

    :meth:`run` 은 ``stop_event`` 가 set 될 때까지 매 주기 1회 :meth:`run_once` 를 돌리고 매
    주기 끝에 ``sleep(short_poll_interval)`` 한다(어떤 분기에서도 즉시 재호출=무한 스핀 없음 —
    4.3 :class:`HeartbeatReporter` 와 동일 규율). ``sleep``/``now``/``execute_job``/transport/
    ``token_check`` 는 모두 주입 가능해 테스트가 실 네트워크·실 thread·실 시계 없이 결정적으로
    검증한다.

    lease 인지(client 3가지): (a) claim 한 job 의 ``lease_expires_at`` 를 in-flight 에 기록,
    (b) :meth:`active_jobs` 로 heartbeat 에 노출(서버 연장 입력), (c) 서버 complete 거부
    (409/410)를 crash 없이 흡수. **단일-claim 강제·실제 연장/회수/재할당/complete 소유
    검증은 서버(Epic 5).**
    """

    def __init__(
        self,
        identity: AgentIdentity,
        *,
        transport: Transport,
        execute_job: Callable[[ClaimedJob], JobResult] = default_execute_job,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
        max_jobs: int = 1,
        short_poll_interval_seconds: float = DEFAULT_SHORT_POLL_INTERVAL_SECONDS,
        base_url: str | None = None,
        token_check: Callable[[AgentIdentity], bool] | None = None,
        stop_event: threading.Event | None = None,
        on_status: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
        complete_outbox_path: Any | None = None,
        backoff_base_seconds: float = DEFAULT_CLAIM_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = DEFAULT_CLAIM_BACKOFF_MAX_SECONDS,
        poll_jitter_ratio: float = DEFAULT_POLL_JITTER_RATIO,
        preflight_enabled: bool = True,
    ) -> None:
        self.identity = identity
        self._transport = transport
        self._execute_job = execute_job
        self._preflight_enabled = preflight_enabled
        self._sleep = sleep
        self._now = now
        self._capabilities = capabilities
        self._max_jobs = max(1, int(max_jobs))
        self._short_poll_interval = short_poll_interval_seconds
        self._base_url = base_url
        self._token_check = token_check
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._on_status = on_status
        self._log = log
        self._complete_outbox = CompleteOutbox(complete_outbox_path)
        # claim 실패 backoff/jitter 상태. seed 는 agent_id(없으면 빈 문자열) — per-Agent 안정.
        self._backoff_base = max(0.0, float(backoff_base_seconds))
        self._backoff_max = max(self._backoff_base, float(backoff_max_seconds))
        self._poll_jitter_ratio = max(0.0, float(poll_jitter_ratio))
        self._jitter_seed = str(getattr(identity, "agent_id", "") or "")
        #: 연속 claim 실패 횟수(성공/job 수신 시 0 으로 리셋). 다음 대기 backoff 산정에 쓴다.
        self._consecutive_claim_failures = 0
        #: in-flight job(claim~complete 사이). heartbeat thread 가 동시 읽으므로 lock 보호.
        self._lock = threading.Lock()
        self._in_flight: dict[str, ClaimedJob] = {}
        #: surfacing 상태(4.2 ``TOKEN_STATUS_*`` 어휘 재사용 — 새 ad-hoc 플래그 금지).
        self.token_status: str = TOKEN_STATUS_VALID
        self.last_error_event: dict[str, Any] | None = None

    # ── 공개 상태/배선 ────────────────────────────────────────────────────────

    @property
    def needs_registration(self) -> bool:
        """``401``/revoke 로 재등록이 필요한 상태인가(서버 소유 반응은 운영)."""

        return self.token_status == TOKEN_STATUS_REVOKED

    def active_jobs(self) -> list[dict[str, Any]]:
        """in-flight job 의 식별 목록(heartbeat ``active_jobs`` provider 로 배선).

        각 항목은 ``{"job_id", "lease_expires_at"}`` — 서버가 heartbeat 수신 시 lease 를 연장할
        입력을 제공한다(연장 자체는 서버 소유). thread-safe 스냅샷을 돌려준다.
        """

        with self._lock:
            return [
                {"job_id": job.job_id, "lease_expires_at": job.lease_expires_at}
                for job in self._in_flight.values()
            ]

    def stop(self) -> None:
        """루프 정지를 요청한다(thread-safe)."""

        self._stop_event.set()

    # ── 메인 루프 ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """stop 이 set 될 때까지 매 주기 claim→execute→complete 를 돌린다(thread/CLI target)."""

        while not self._stop_event.is_set():
            self.run_once()
            if self._stop_event.is_set():
                break
            # 매 주기 끝에 대기 — 어떤 분기에서도 즉시 재호출(무한 스핀)하지 않는다.
            # 빈 큐는 short_poll + per-Agent jitter, 연속 claim 실패는 지수 backoff(+jitter).
            self._sleep_interruptible(self._next_poll_delay())

    def _next_poll_delay(self) -> float:
        """다음 폴링 전 대기(초): 빈 큐는 short_poll, 연속 실패는 지수 backoff. 둘 다 jitter 가산.

        per-Agent stable jitter 로 서버 복구 직후 여러 Agent 가 같은 초에 몰리는 thundering
        herd 를 막는다(결정적 — random 미사용, agent_id seed 기반).
        """

        if self._consecutive_claim_failures > 0:
            backoff = self._backoff_base * (
                CLAIM_BACKOFF_MULTIPLIER ** (self._consecutive_claim_failures - 1)
            )
            base = min(self._backoff_max, backoff)
        else:
            base = self._short_poll_interval
        return jittered_interval(
            base, seed=self._jitter_seed, ratio=self._poll_jitter_ratio
        )

    def run_once(self) -> None:
        """단발 주기: token 게이트 → claim → 각 job execute+complete. 어떤 예외도 흡수(best-effort)."""

        validation = self._gate_token()
        if not validation.can_receive_jobs:
            # identity 없음/token revoke → claim 미전송(=job 미수신, FR-16)·재등록 필요 surfacing.
            self._set_status(validation.status)
            self._record_error(
                ERROR_JOB_REVOKED,
                "job claim skipped: token invalid — re-registration required",
                None,
            )
            return

        if not self._replay_outbox():
            return

        try:
            jobs = claim_jobs(
                self.identity,
                transport=self._transport,
                capabilities=self._capabilities,
                max_jobs=self._max_jobs,
                base_url=self._base_url,
            )
        except TransportError as exc:
            self._handle_transport_error(ERROR_JOB_CLAIM, "job claim failed", exc)
            # 401/revoke 는 backoff 대상이 아니다(재등록 필요 — 폴링 backoff 로 숨기지 않는다).
            # network/5xx 등 일시 실패만 연속 카운터를 올려 다음 대기에 지수 backoff 를 적용한다.
            if exc.status_code == 401:
                self._consecutive_claim_failures = 0
            else:
                self._consecutive_claim_failures += 1
            return
        except Exception as exc:  # noqa: BLE001 — best-effort: 어떤 예외도 thread 를 죽이지 않는다.
            self._record_error(ERROR_JOB_CLAIM, "job claim failed", exc)
            self._consecutive_claim_failures += 1
            return

        # claim 성공 → 정상 상태로 회복(이전 revoked 이후 재발급되면 valid 로 복귀) + backoff 리셋.
        self._set_status(TOKEN_STATUS_VALID)
        self._consecutive_claim_failures = 0

        # claim 한 전체 묶음을 heartbeat 에 노출해 serial 처리 대기 중인 lease 도 연장 입력에 포함한다.
        self._track_many(jobs)

        # claim 한 job 만 실행한다(임의 job 생성·실행 0).
        self._process_jobs(jobs)

    # ── job 처리(execute → complete) ──────────────────────────────────────────

    def _process_jobs(self, jobs: Sequence[ClaimedJob]) -> None:
        if not jobs:
            return
        if self._max_jobs <= 1 or len(jobs) <= 1:
            self._process_jobs_serial(jobs)
            return
        self._process_jobs_parallel(jobs)

    def _process_jobs_serial(self, jobs: Sequence[ClaimedJob]) -> None:
        pending = {job.job_id for job in jobs}
        try:
            for job in jobs:
                if self._stop_event.is_set():
                    break
                pending.discard(job.job_id)
                self._process_job(job)
        finally:
            for job_id in pending:
                self._untrack(job_id)

    def _process_jobs_parallel(self, jobs: Sequence[ClaimedJob]) -> None:
        pending = {job.job_id for job in jobs}
        with ThreadPoolExecutor(
            max_workers=min(self._max_jobs, len(jobs)),
            thread_name_prefix="rider-agent-job",
        ) as executor:
            futures = {}
            for job in jobs:
                if self._stop_event.is_set():
                    break
                pending.discard(job.job_id)
                futures[executor.submit(self._process_job, job)] = job
            for future in as_completed(futures):
                job = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - defensive guard for worker thread.
                    self._record_error(
                        ERROR_JOB_EXECUTION,
                        f"job worker failed: {job.type}",
                        exc,
                    )
        for job_id in pending:
            self._untrack(job_id)

    def _process_job(self, job: ClaimedJob) -> None:
        self._track(job)
        started_at = self._now()
        self._emit_started(job)

        # 브라우저/profile 을 열기 전 server preflight — payload TTL 만료/서버 거부면 워커를
        # 호출하지 않고(브라우저 미오픈) 실패로 안전히 닫는다(서버/Agent 재시작 뒤 stale 작업
        # 무제한 재실행 차단). 비대상 type 은 skip(불필요 왕복 0).
        denied = self._preflight_denial(job)
        if denied is not None:
            finished_at = self._now()
            result = replace(
                denied,
                agent_id=self.identity.agent_id,
                started_at=started_at,
                finished_at=finished_at,
            )
            self._complete(job, result)
            return

        try:
            result = self._execute_job(job)
        except Exception as exc:  # noqa: BLE001 — executor 예외도 루프를 죽이지 않는다(complete 로 보고).
            result = make_failure_result(
                ERROR_JOB_EXECUTION, "job execution failed", error=exc
            )

        finished_at = self._now()
        result = replace(
            result,
            agent_id=self.identity.agent_id,
            started_at=started_at,
            finished_at=finished_at,
        )

        self._complete(job, result)

    def _preflight_denial(self, job: ClaimedJob) -> JobResult | None:
        """server preflight 가 거부하면 실패 :class:`JobResult` 를(허용/비대상이면 ``None``).

        브라우저를 여는 job type(crawl/auth)만 preflight 한다. preflight 호출 자체가 실패하면
        **fail-closed**(browser open 차단) 로 ``preflight_unavailable`` reason 을 남긴다 — 서버
        장애가 stale 작업의 무제한 브라우저 오픈으로 번지지 않게 한다(리스크 대응 #3).
        """

        if not self._preflight_enabled or job.type not in _PREFLIGHT_JOB_TYPES:
            return None
        try:
            response = preflight_job(
                self.identity,
                job.job_id,
                transport=self._transport,
                base_url=self._base_url,
            )
        except Exception as exc:  # noqa: BLE001 — preflight 실패는 fail-closed(브라우저 미오픈).
            self._record_error(
                ERROR_JOB_PREFLIGHT_DENIED, "job preflight unavailable", exc
            )
            return make_failure_result(
                ERROR_JOB_PREFLIGHT_DENIED,
                "job preflight unavailable",
                result_json={"reason": PREFLIGHT_REASON_UNAVAILABLE},
            )
        if isinstance(response, dict) and response.get("allowed") is False:
            reason = response.get("reason")
            safe_reason = reason if isinstance(reason, str) and reason else PREFLIGHT_REASON_DEFAULT
            self._record_error(
                ERROR_JOB_PREFLIGHT_DENIED,
                f"job preflight denied: {job.type}",
                None,
            )
            return make_failure_result(
                ERROR_JOB_PREFLIGHT_DENIED,
                "job preflight denied",
                result_json={"reason": safe_reason},
            )
        return None

    def _complete(self, job: ClaimedJob, result: JobResult) -> None:
        completion_id = self._completion_id(job, result)
        self._complete_outbox.put(
            job_id=job.job_id,
            body=_complete_body(result, completion_id=completion_id),
            completion_id=completion_id,
        )
        try:
            outcome = self._complete_with_retry(job, result, completion_id=completion_id)
            if outcome == COMPLETE_REPORT_SENT:
                self._complete_outbox.mark_sent(completion_id)
            elif outcome == COMPLETE_REPORT_LEASE_LOST:
                self._complete_outbox.mark_discarded(completion_id, reason=outcome)
        finally:
            # client 관점에서 이 job 처리는 끝났다 — in-flight 에서 제거(heartbeat 연장 중단).
            # 재완료/재시도는 서버 lease 만료→재할당(Epic 5)에 맡긴다(best-effort).
            self._untrack(job.job_id)

    def _complete_with_retry(
        self,
        job: ClaimedJob,
        result: JobResult,
        *,
        completion_id: str | None = None,
    ) -> str:
        attempts = max(1, DEFAULT_COMPLETE_RETRY_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            should_retry = attempt < attempts
            try:
                complete_job(
                    self.identity,
                    job.job_id,
                    result,
                    transport=self._transport,
                    base_url=self._base_url,
                    completion_id=completion_id,
                )
                return COMPLETE_REPORT_SENT
            except TransportError as exc:
                if exc.status_code in (409, 410):
                    # lease lost / 이미 재할당 — 다른 Agent 소유로 본다. crash 없이 흡수·기록.
                    self._record_error(
                        ERROR_JOB_LEASE_LOST,
                        "job complete rejected: lease lost or already reassigned",
                        exc,
                    )
                    return COMPLETE_REPORT_LEASE_LOST
                if exc.status_code == 401:
                    self._handle_transport_error(
                        ERROR_JOB_REVOKED, "job complete rejected: token revoked", exc
                    )
                    return COMPLETE_REPORT_REVOKED
                if should_retry and self._sleep_before_complete_retry():
                    continue
                self._record_error(ERROR_JOB_COMPLETE, "job complete failed", exc)
                return COMPLETE_REPORT_FAILED
            except Exception as exc:  # noqa: BLE001 — best-effort.
                if should_retry and self._sleep_before_complete_retry():
                    continue
                self._record_error(ERROR_JOB_COMPLETE, "job complete failed", exc)
                return COMPLETE_REPORT_FAILED
        return COMPLETE_REPORT_FAILED

    def _replay_outbox(self) -> bool:
        for record in self._complete_outbox.pending_records():
            job_id = record.get("job_id")
            completion_id = record.get("completion_id")
            body = record.get("body")
            if not isinstance(job_id, str) or not isinstance(completion_id, str) or not isinstance(body, dict):
                if isinstance(completion_id, str):
                    self._complete_outbox.mark_discarded(completion_id, reason="invalid outbox record")
                continue
            result = self._result_from_complete_body(body)
            if result is None:
                self._complete_outbox.mark_discarded(completion_id, reason="invalid outbox body")
                continue
            outcome = self._complete_with_retry(
                ClaimedJob(job_id=job_id),
                result,
                completion_id=completion_id,
            )
            if outcome == COMPLETE_REPORT_SENT:
                self._complete_outbox.mark_sent(completion_id)
                continue
            if outcome == COMPLETE_REPORT_LEASE_LOST:
                self._complete_outbox.mark_discarded(completion_id, reason=outcome)
                continue
            return False
        return True

    def _completion_id(self, job: ClaimedJob, result: JobResult) -> str:
        payload = {
            "job_id": job.job_id,
            "body": redact_mapping(_complete_body(result)),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _result_from_complete_body(body: dict[str, Any]) -> JobResult | None:
        status = body.get("status")
        if not isinstance(status, str) or not status:
            return None
        result_json = body.get("result_json")
        metrics = body.get("metrics")
        error_code = body.get("error_code")
        error_message = body.get("error_message_redacted")
        started_at = body.get("started_at")
        finished_at = body.get("finished_at")
        safe_started_at = (
            started_at if isinstance(started_at, (int, float)) and not isinstance(started_at, bool) else None
        )
        safe_finished_at = (
            finished_at if isinstance(finished_at, (int, float)) and not isinstance(finished_at, bool) else None
        )
        return JobResult(
            status=status,
            result_json=result_json if isinstance(result_json, dict) else None,
            error_code=error_code if isinstance(error_code, str) else None,
            error_message_redacted=error_message if isinstance(error_message, str) else None,
            metrics=metrics if isinstance(metrics, dict) else None,
            agent_id=str(body.get("agent_id") or ""),
            started_at=safe_started_at,
            finished_at=safe_finished_at,
        )

    def _sleep_before_complete_retry(self) -> bool:
        if self._stop_event.is_set():
            return False
        self._sleep_interruptible(DEFAULT_COMPLETE_RETRY_DELAY_SECONDS)
        return not self._stop_event.is_set()

    def _sleep_interruptible(self, seconds: float) -> None:
        if self._sleep is time.sleep:
            self._stop_event.wait(max(0.0, float(seconds)))
            return
        self._sleep(seconds)

    def _emit_started(self, job: ClaimedJob) -> None:
        # 최소 진행 이벤트(claim 직후). best-effort — 실패해도 루프를 죽이지 않는다.
        try:
            event = make_job_event(
                EVENT_TYPE_JOB_STARTED, SEVERITY_INFO, f"job started: {job.type}"
            )
            emit_job_event(
                self.identity,
                job.job_id,
                event,
                transport=self._transport,
                base_url=self._base_url,
            )
        except Exception as exc:  # noqa: BLE001 — 이벤트 보고 실패는 무시(진행에 영향 없음).
            self._record_error(ERROR_JOB_EVENT, "job started event failed", exc)

    # ── in-flight 추적(thread-safe) ───────────────────────────────────────────

    def _track(self, job: ClaimedJob) -> None:
        with self._lock:
            self._in_flight[job.job_id] = job

    def _track_many(self, jobs: Sequence[ClaimedJob]) -> None:
        with self._lock:
            for job in jobs:
                self._in_flight[job.job_id] = job

    def _untrack(self, job_id: str) -> None:
        with self._lock:
            self._in_flight.pop(job_id, None)

    # ── token 게이트 / 상태 surfacing / 에러 기록(4.3 패턴 계승) ───────────────

    def _gate_token(self) -> TokenValidation:
        return validate_agent_token(self.identity, server_check=self._token_check)

    def _handle_transport_error(
        self, code: str, message: str, exc: TransportError
    ) -> None:
        if exc.status_code == 401:
            # 재등록 필요 상태로 surfacing(서버가 token revoke). 루프는 다음 주기로 진행.
            self._set_status(TOKEN_STATUS_REVOKED)
            self._record_error(
                ERROR_JOB_REVOKED,
                "rejected: token revoked — re-registration required",
                exc,
            )
        else:
            # 네트워크/5xx 등 일시 실패 — 상태는 그대로 두고 다음 주기에 재시도.
            self._record_error(code, message, exc)

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


# ── startup 배선(4.3 이 4.4 로 위임한 부분) ───────────────────────────────────


def start_heartbeat_thread(reporter: HeartbeatReporter) -> threading.Thread:
    """4.3 :class:`HeartbeatReporter` 를 daemon thread 로 띄운다(heartbeat.py 무변경).

    daemon=True 라 메인 루프가 끝나면 프로세스 종료를 막지 않는다. 정지는 ``reporter.stop()``
    + thread join 으로 한다(:func:`run_agent` 가 배선).
    """

    thread = threading.Thread(
        target=reporter.run, name="rider-agent-heartbeat", daemon=True
    )
    thread.start()
    return thread


def build_agent_components(
    identity: AgentIdentity,
    *,
    transport: Transport,
    base_url: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    execute_job: Callable[[ClaimedJob], JobResult] = default_execute_job,
    capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
    max_jobs: int = 1,
    short_poll_interval_seconds: float = DEFAULT_SHORT_POLL_INTERVAL_SECONDS,
    heartbeat_interval_seconds: float = MIN_HEARTBEAT_INTERVAL_SECONDS,
    metrics_provider: Any = None,
    token_check: Callable[[AgentIdentity], bool] | None = None,
    stop_event: threading.Event | None = None,
    on_status: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
    browser_profiles_provider: Any = None,
    kakao_status_provider: Any = None,
    complete_outbox_path: Any | None = None,
) -> tuple[JobRunner, HeartbeatReporter]:
    """:class:`JobRunner` 와 :class:`HeartbeatReporter` 를 구성한다(핵심 배선: active_jobs).

    ``HeartbeatReporter(active_jobs_provider=runner.active_jobs)`` 로 배선해 heartbeat 가
    in-flight job 을 실어 서버 lease 연장을 트리거하게 한다(4.3 이 비워둔 ``active_jobs`` 소스).
    ``browser_profiles_provider`` 는 4.5 ``BrowserProfileManager.browser_profiles`` 를 주입받아
    heartbeat ``browser_profiles`` 소스를 채운다(``active_jobs`` 배선과 동형; 미주입이면 4.3
    기본 빈 리스트 → 무회귀). ``kakao_status_provider`` 는 4.6
    ``KakaoSenderWorker.kakao_status`` 를 주입받아 heartbeat ``kakao_status`` 소스를 채운다
    (동형; 미주입이면 4.3 기본 ``"disabled"`` → 무회귀). runner/reporter 는 **같은
    ``stop_event``** 를 공유해 한쪽 정지가 다른 쪽도 정지시킨다.
    """

    shared_stop = stop_event if stop_event is not None else threading.Event()
    effective_max_jobs = max(1, int(max_jobs))

    def _metrics_with_capacity() -> dict[str, Any]:
        source = metrics_provider() if callable(metrics_provider) else metrics_provider
        if isinstance(source, dict):
            metrics = dict(source)
        elif source is None:
            metrics = default_metrics()
        else:
            metrics = default_metrics()
        metrics["max_in_flight"] = effective_max_jobs
        return metrics

    runner = JobRunner(
        identity,
        transport=transport,
        execute_job=execute_job,
        sleep=sleep,
        now=now,
        capabilities=capabilities,
        max_jobs=effective_max_jobs,
        short_poll_interval_seconds=short_poll_interval_seconds,
        base_url=base_url,
        token_check=token_check,
        stop_event=shared_stop,
        on_status=on_status,
        log=log,
        complete_outbox_path=complete_outbox_path,
    )
    reporter = HeartbeatReporter(
        identity,
        transport=transport,
        interval_seconds=heartbeat_interval_seconds,
        base_url=base_url,
        sleep=sleep,
        stop_event=shared_stop,
        capabilities=capabilities,
        metrics_provider=_metrics_with_capacity,
        active_jobs_provider=runner.active_jobs,
        browser_profiles_provider=browser_profiles_provider,
        kakao_status_provider=kakao_status_provider,
        on_status=on_status,
        log=log,
    )
    return runner, reporter


@dataclass(frozen=True)
class AgentRunSummary:
    """:func:`run_agent` 결과 요약. ``started`` 가 False 면 재등록 필요로 루프 미진입."""

    started: bool
    token_status: str
    runner: JobRunner | None = None
    reporter: HeartbeatReporter | None = None
    heartbeat_thread: threading.Thread | None = None
    #: 활성 노드에서 기동된 4.6 KakaoSenderWorker(미배선/비활성이면 ``None``).
    kakao_worker: Any = None
    #: 활성 노드에서 구성된 CRAWL_BAEMIN/CRAWL_COUPANG worker(미배선이면 ``None``).
    crawl_worker: Any = None


def run_agent(
    *,
    transport: Transport,
    store: Any,
    identity_path: Any,
    base_url: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    execute_job: Callable[[ClaimedJob], JobResult] = default_execute_job,
    capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
    max_jobs: int = 1,
    short_poll_interval_seconds: float = DEFAULT_SHORT_POLL_INTERVAL_SECONDS,
    heartbeat_interval_seconds: float = MIN_HEARTBEAT_INTERVAL_SECONDS,
    metrics_provider: Any = None,
    token_check: Callable[[AgentIdentity], bool] | None = None,
    stop_event: threading.Event | None = None,
    on_status: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
    browser_profiles_provider: Any = None,
    kakao_status_provider: Any = None,
    start_auth_worker: bool = False,
    auth_login_probe: Callable[[ClaimedJob], str] | None = None,
    auth_open_auth_browser: Callable[[ClaimedJob], Any] | None = None,
    auth_detect_completion: Callable[[ClaimedJob], bool] | None = None,
    auth_max_wait_seconds: float | None = None,
    auth_poll_interval_seconds: float | None = None,
    auth_max_attempts: int | None = None,
    start_crawl_worker: bool = False,
    crawl_profile_manager: Any = None,
    crawl_snapshot: Callable[..., Any] | None = None,
    crawl_auth_probe: Callable[[ClaimedJob, Any], str] | None = None,
    profile_idle_ttl_seconds: float | None = 3600.0,
    max_profiles: int | None = 20,
    start_kakao_sender: bool = False,
    kakao_send: Callable[..., Any] | None = None,
    kakao_build_config: Callable[..., Any] | None = None,
    session_probe: Callable[[], bool] | None = None,
    start_heartbeat: bool = True,
    heartbeat_join_timeout: float = 5.0,
    complete_outbox_path: Any | None = None,
) -> AgentRunSummary:
    """architecture-contract startup 을 구현한다: identity 로드 → token 검증 → (활성 시) Kakao
    sender 워커 기동 → heartbeat thread 기동 → 메인 run 루프. 모든 주입점(transport/store/
    sleep/now/execute_job/stop_event)을 노출해 테스트가 결정적으로 검증한다.

    identity 없음/token revoke 면 명확히 surfacing 하고 **루프에 진입하지 않는다**(재등록 필요).
    ``start_crawl_worker`` 가 True 이고 ``capabilities`` 에 ``CRAWL_BAEMIN`` 또는
    ``CRAWL_COUPANG`` 이 있으면 crawl worker 를 구성해 해당 job type 을 실제 crawler seam 으로
    라우팅한다. ``start_kakao_sender`` 가 True 이고 ``capabilities`` 에 ``KAKAO_SEND`` 가 있으면
    :func:`~rider_agent.workers.kakao_sender.start_kakao_sender_worker_if_enabled` 로 FIFO 단일-
    세션 직렬 워커를 띄우고, ``KAKAO_SEND`` job 을 그 워커로 라우팅하며(그 외 type 은 기존
    ``execute_job`` 유지) ``kakao_status`` 소스를 배선한다(미배선/비활성이면 4.3 기본
    ``"disabled"`` → 무회귀). 종료 시 ``reporter.stop()``/``runner.stop()``/``kakao_worker.stop()``
    + thread join 으로 정리한다.

    ``session_probe`` (4.7)가 주입되고 노드가 ``KAKAO_SEND`` 를 보유하면
    :func:`~rider_agent.autostart.kakao_session_allowed` 로 interactive-session 게이트를 적용한다 —
    비대화형(Session 0)이면 Kakao 워커를 **띄우지 않고**(``kakao_worker=None`` → ``kakao_status``
    기본 ``"disabled"``) ``on_status``/``log`` 로 surfacing 한다. **``session_probe=None``(미주입)이면
    게이트 없음 = 4.6 동작 그대로(무회귀).**
    """

    identity = load_local_agent_identity(store=store, identity_path=identity_path)
    validation = validate_agent_token(identity, server_check=token_check)
    if identity is None or not validation.can_receive_jobs:
        # 재등록 필요 — 루프 미진입(claim/heartbeat 안 띄움).
        if log is not None:
            log(
                redact(
                    "agent not started: valid identity/token required — "
                    "run registration first (re-registration required)"
                )
            )
        if on_status is not None:
            on_status(validation.status)
        return AgentRunSummary(started=False, token_status=validation.status)

    effective_max_jobs = max(1, int(max_jobs))
    effective_max_profiles = max_profiles
    if (
        start_crawl_worker
        and effective_max_profiles is not None
        and effective_max_profiles < effective_max_jobs
    ):
        raise ValueError("max_profiles must be greater than or equal to max_jobs")

    composition = compose_execute_job(
        identity=identity,
        capabilities=capabilities,
        fallback=execute_job,
        log=log,
        now=now,
        sleep=sleep,
        browser_profiles_provider=browser_profiles_provider,
        kakao_status_provider=kakao_status_provider,
        on_status=on_status,
        start_auth_worker=start_auth_worker,
        auth_login_probe=auth_login_probe,
        auth_open_auth_browser=auth_open_auth_browser,
        auth_detect_completion=auth_detect_completion,
        auth_max_wait_seconds=auth_max_wait_seconds,
        auth_poll_interval_seconds=auth_poll_interval_seconds,
        auth_max_attempts=auth_max_attempts,
        start_crawl_worker=start_crawl_worker,
        crawl_profile_manager=crawl_profile_manager,
        crawl_snapshot=crawl_snapshot,
        crawl_auth_probe=crawl_auth_probe,
        secret_resolver=getattr(store, "resolve", None),
        profile_idle_ttl_seconds=profile_idle_ttl_seconds,
        max_profiles=effective_max_profiles,
        start_kakao_sender=start_kakao_sender,
        kakao_send=kakao_send,
        kakao_build_config=kakao_build_config,
        session_probe=session_probe,
    )

    runner, reporter = build_agent_components(
        identity,
        transport=transport,
        base_url=base_url,
        sleep=sleep,
        now=now,
        execute_job=composition.execute_job,
        capabilities=capabilities,
        max_jobs=effective_max_jobs,
        short_poll_interval_seconds=short_poll_interval_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        metrics_provider=metrics_provider,
        token_check=token_check,
        stop_event=stop_event,
        on_status=on_status,
        log=log,
        browser_profiles_provider=composition.browser_profiles_provider,
        kakao_status_provider=composition.kakao_status_provider,
        complete_outbox_path=complete_outbox_path,
    )

    hb_thread = start_heartbeat_thread(reporter) if start_heartbeat else None
    try:
        runner.run()
    finally:
        reporter.stop()
        runner.stop()
        for close in composition.close_callbacks:
            close()
        if hb_thread is not None:
            hb_thread.join(timeout=heartbeat_join_timeout)

    return AgentRunSummary(
        started=True,
        token_status=runner.token_status,
        runner=runner,
        reporter=reporter,
        heartbeat_thread=hb_thread,
        kakao_worker=composition.kakao_worker,
        crawl_worker=composition.crawl_worker,
    )
