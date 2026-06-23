"""Agent API 라우트 — claim/complete/events (Story 5.3 / AC4).

``rider_agent.job_loop`` 의 claim/complete/events 클라이언트 계약에 **서버가 맞춘다**(서버가
Agent 를 바꾸지 않는다 — 단방향 의존). 본문은 Pydantic v2 로 검증하고 JSON 은 snake_case
(camelCase 변환 0), 시각은 ISO 8601 UTC(``…Z``)로 통일한다. 5.1 ``create_app``·전역 에러
envelope·``/v1/`` 접두 규약을 그대로 계승한다.

상태 전이(success→SUCCEEDED 등)·lease 소유 검증은 ``QueueBackend`` 경계에서만 일어난다 —
라우트는 Agent 소문자 status 를 상태머신값으로 매핑(:func:`~rider_server.queue.states.map_agent_status`)
해 backend 에 넘기고, 결과 코드를 HTTP 상태로 옮긴다(LEASE_LOST→409, NOT_FOUND→404). Agent 가
409/410 을 ``lease_lost`` 로 흡수하므로 **서버가 409 를 정확히 내야** 재할당된 job 의 이중 success
가 막힌다(AC2). token 은 ``Authorization: Bearer`` 헤더에서만 읽고 로그/payload/예외에 평문
출력하지 않는다. full token lifecycle(발급/revoke/MFA/4역할)은 5.8 소유 — 여기는 bearer→agent_id
해석 + 401 경로만.
[Source: src/rider_agent/job_loop.py:80-144,277-354,540-585, architecture.md:290-298,438-440]
"""

from __future__ import annotations

import math
import hashlib
import json
from datetime import datetime, timezone
from http import HTTPStatus
from inspect import isawaitable
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from rider_crawl.redaction import redact, redact_mapping

from ..queue.backend import QueueBackend
from ..queue.states import (
    UnknownAgentStatus,
    map_agent_status,
    preflight_decision,
)
from ..services.job_completion_service import (
    JobCompletionConflict,
    JobCompletionInvalid,
    JobCompletionNotFound,
)

# claim 시 부여하는 lease 기간(초). Agent heartbeat(30~60초)보다 길어 연장 여유를 둔다.
DEFAULT_LEASE_SECONDS = 120.0
MAX_CLAIM_JOBS = 50
MAX_CAPABILITIES = 128
MAX_CAPABILITY_LENGTH = 128
MAX_AGENT_ID_LENGTH = 128
MAX_STATUS_LENGTH = 64
MAX_ERROR_CODE_LENGTH = 128
MAX_ERROR_MESSAGE_LENGTH = 4_096
MAX_RESULT_JSON_ITEMS = 1_024
MAX_COMPLETE_METRICS_ITEMS = 128
MAX_EVENT_TYPE_LENGTH = 128
MAX_EVENT_SEVERITY_LENGTH = 32
MAX_EVENT_MESSAGE_LENGTH = 4_096
MAX_EVENT_ARTIFACT_REFS = 128
MAX_EVENT_ARTIFACT_STRING_LENGTH = 4_096
MAX_JSON_SEQUENCE_ITEMS = 512
MAX_JSON_STRING_LENGTH = 4_096
CapabilityName = Annotated[str, Field(min_length=1, max_length=MAX_CAPABILITY_LENGTH)]
_COMPLETE_DIAGNOSTIC_METRIC_KEYS = frozenset(
    {"duration_ms", "kakao_outcome", "auth_reason", "reason", "attempts"}
)
_MAX_DIAGNOSTIC_STRING_LENGTH = 120
_MAX_DIAGNOSTIC_NUMBER = 1_000_000_000
_MISSING = object()

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


# ── 인증 seam(bearer → agent_id 해석 + 401) ──────────────────────────────────────


def default_resolve_agent_id(token: str) -> str | None:
    """기본 agent identity 해석 seam(5.3 최소 — full lifecycle 은 5.8).

    비어있지 않은 bearer 는 통과시키되 **token 평문을 반환/로그하지 않는다**(non-secret
    sentinel 반환). 실제 queue 연산은 요청 본문의 ``agent_id`` 를 쓴다. 테스트는 알려진
    token→agent_id 매핑/revoke(None→401)를 주입해 이 seam 을 교체한다.
    """

    return "agent" if token else None


async def resolve_agent(request: Request) -> str:
    """``Authorization: Bearer <token>`` → agent_id. 누락/무효 token 은 401."""

    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="missing bearer token")
    resolver: Callable[[str], str | None] = request.app.state.resolve_agent_id
    agent_id = resolver(token)
    if isawaitable(agent_id):
        agent_id = await agent_id
    if not agent_id:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="invalid agent token")
    return str(agent_id)


# ── 요청/응답 스키마(Pydantic v2, snake_case) ─────────────────────────────────────


class ClaimRequest(BaseModel):
    agent_id: str
    capabilities: list[CapabilityName] = Field(default_factory=list, max_length=MAX_CAPABILITIES)
    max_jobs: int = Field(1, ge=1, le=MAX_CLAIM_JOBS)


class CompleteRequest(BaseModel):
    # Agent 는 소문자 status("success"/"failed")를 보낸다 — 서버가 상태머신값으로 매핑.
    status: str = Field(max_length=MAX_STATUS_LENGTH)
    agent_id: str = Field(max_length=MAX_AGENT_ID_LENGTH)
    result_json: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=MAX_ERROR_CODE_LENGTH)
    error_message_redacted: str | None = Field(default=None, max_length=MAX_ERROR_MESSAGE_LENGTH)
    metrics: dict[str, Any] | None = None
    started_at: float | None = None
    finished_at: float | None = None
    completion_id: str | None = Field(default=None, max_length=128)

    @field_validator("result_json")
    @classmethod
    def _bounded_result_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            _validate_bounded_json(
                value,
                max_mapping_items=MAX_RESULT_JSON_ITEMS,
                max_sequence_items=MAX_JSON_SEQUENCE_ITEMS,
                max_string_length=MAX_JSON_STRING_LENGTH,
            )
        return value

    @field_validator("metrics")
    @classmethod
    def _bounded_metrics_payload(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            _validate_bounded_json(
                value,
                max_mapping_items=MAX_COMPLETE_METRICS_ITEMS,
                max_sequence_items=MAX_JSON_SEQUENCE_ITEMS,
                max_string_length=MAX_JSON_STRING_LENGTH,
            )
        return value


class EventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=MAX_EVENT_TYPE_LENGTH)
    severity: str = Field(min_length=1, max_length=MAX_EVENT_SEVERITY_LENGTH)
    message_redacted: str = Field(max_length=MAX_EVENT_MESSAGE_LENGTH)
    artifact_refs: list[Any] = Field(default_factory=list, max_length=MAX_EVENT_ARTIFACT_REFS)

    @field_validator("artifact_refs")
    @classmethod
    def _bounded_artifact_refs(cls, value: list[Any]) -> list[Any]:
        _validate_bounded_json(
            value,
            max_mapping_items=MAX_RESULT_JSON_ITEMS,
            max_sequence_items=MAX_EVENT_ARTIFACT_REFS,
            max_string_length=MAX_EVENT_ARTIFACT_STRING_LENGTH,
        )
        return value


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────────


def _bounded_metric_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if 0 <= value <= _MAX_DIAGNOSTIC_NUMBER else _MISSING
    if isinstance(value, float):
        if not math.isfinite(value) or not 0 <= value <= _MAX_DIAGNOSTIC_NUMBER:
            return _MISSING
        return value
    if isinstance(value, str):
        text = redact(value).strip()
        if not text or any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in text):
            return _MISSING
        return text[:_MAX_DIAGNOSTIC_STRING_LENGTH]
    return _MISSING


def _validate_bounded_json(
    value: Any,
    *,
    max_mapping_items: int,
    max_sequence_items: int,
    max_string_length: int,
) -> Any:
    if isinstance(value, str):
        if len(value) > max_string_length:
            raise ValueError("string value is too large")
        return value
    if isinstance(value, dict):
        if len(value) > max_mapping_items:
            raise ValueError("mapping has too many items")
        for key, item in value.items():
            _validate_bounded_json(
                key,
                max_mapping_items=max_mapping_items,
                max_sequence_items=max_sequence_items,
                max_string_length=max_string_length,
            )
            _validate_bounded_json(
                item,
                max_mapping_items=max_mapping_items,
                max_sequence_items=max_sequence_items,
                max_string_length=max_string_length,
            )
        return value
    if isinstance(value, list | tuple):
        if len(value) > max_sequence_items:
            raise ValueError("sequence has too many items")
        for item in value:
            _validate_bounded_json(
                item,
                max_mapping_items=max_mapping_items,
                max_sequence_items=max_sequence_items,
                max_string_length=max_string_length,
            )
    return value


def _bounded_metrics(metrics: dict[str, Any]) -> dict[str, Any] | None:
    bounded: dict[str, Any] = {}
    for key, value in metrics.items():
        if key not in _COMPLETE_DIAGNOSTIC_METRIC_KEYS:
            continue
        stored = _bounded_metric_value(value)
        if stored is not _MISSING:
            bounded[key] = stored
    return bounded or None


def _optional_finite_float(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _complete_diagnostics(body: CompleteRequest) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    if body.error_message_redacted is not None:
        diagnostics["error_message_redacted"] = redact(body.error_message_redacted)
    if body.metrics is not None:
        metrics = _bounded_metrics(body.metrics)
        if metrics:
            diagnostics["metrics"] = metrics
    started_at = _optional_finite_float(body.started_at)
    if started_at is not None:
        diagnostics["started_at"] = started_at
    finished_at = _optional_finite_float(body.finished_at)
    if finished_at is not None:
        diagnostics["finished_at"] = finished_at
    return diagnostics


def _complete_diagnostics_key(existing: dict[str, Any]) -> str:
    for key in ("complete", "server_complete"):
        if key not in existing:
            return key
    suffix = 2
    while f"server_complete_{suffix}" in existing:
        suffix += 1
    return f"server_complete_{suffix}"


def _stored_result_json(body: CompleteRequest) -> dict[str, Any] | None:
    result = redact_mapping(body.result_json) if body.result_json is not None else None
    diagnostics = _complete_diagnostics(body)
    if not diagnostics:
        return result
    stored = dict(result or {})
    existing = stored.get("diagnostics")
    merged: dict[str, Any]
    if isinstance(existing, dict):
        merged = dict(existing)
    elif existing is None:
        merged = {}
    else:
        merged = {"agent": existing}
    merged[_complete_diagnostics_key(merged)] = diagnostics
    stored["diagnostics"] = merged
    return stored


def _complete_duration_ms(body: CompleteRequest) -> int | None:
    if body.metrics is not None:
        value = body.metrics.get("duration_ms")
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and 0 <= value <= _MAX_DIAGNOSTIC_NUMBER:
            return value
        if isinstance(value, float) and math.isfinite(value) and 0 <= value <= _MAX_DIAGNOSTIC_NUMBER:
            return int(value)
    started_at = _optional_finite_float(body.started_at)
    finished_at = _optional_finite_float(body.finished_at)
    if started_at is None or finished_at is None or finished_at < started_at:
        return None
    duration_ms = int(round((finished_at - started_at) * 1000))
    return duration_ms if 0 <= duration_ms <= _MAX_DIAGNOSTIC_NUMBER else None


def _result_schema_version(body: CompleteRequest) -> str | None:
    if not isinstance(body.result_json, dict):
        return None
    value = body.result_json.get("schema_version")
    if not isinstance(value, str | int):
        return None
    text = str(value).strip()
    if not text or any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in text):
        return None
    return text[:80]


def _iso_utc(dt: datetime) -> str:
    """timezone-aware datetime 을 ISO 8601 UTC(``…Z``)로 — epoch 혼용 금지(ADD-13)."""
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _backend(request: Request) -> QueueBackend:
    return request.app.state.queue_backend


def _lease_seconds(request: Request) -> float:
    settings = getattr(request.app.state, "settings", None)
    value = getattr(settings, "job_lease_seconds", DEFAULT_LEASE_SECONDS)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LEASE_SECONDS
    return parsed if parsed > 0 else DEFAULT_LEASE_SECONDS


async def _maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


async def _claim_max_jobs(
    request: Request,
    *,
    agent_id: str,
    requested_max_jobs: int,
    capabilities: list[str],
    now: datetime,
) -> int:
    registry = getattr(request.app.state, "agent_registry", None)
    capacity_method = getattr(registry, "capacity_for_agent", None)
    if capacity_method is None:
        return requested_max_jobs
    capacity = await _maybe_await(capacity_method(agent_id))
    if not isinstance(capacity, dict):
        return requested_max_jobs
    max_in_flight = _positive_int(capacity.get("max_in_flight"))
    if max_in_flight is None:
        return requested_max_jobs
    count_method = getattr(_backend(request), "count_in_flight", None)
    if count_method is None:
        return min(requested_max_jobs, max_in_flight)
    in_flight = await _maybe_await(
        count_method(agent_id=agent_id, job_types=capabilities)
    )
    try:
        remaining = max(0, max_in_flight - int(in_flight or 0))
    except (TypeError, ValueError):
        remaining = max_in_flight
    return min(requested_max_jobs, remaining)


def _invalid_job_id_http_error() -> HTTPException:
    return HTTPException(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        detail="invalid job id",
    )


def _completion_payload_hash(
    *,
    body: CompleteRequest,
    target_status: str,
    stored_result_json: dict[str, Any] | None,
) -> str | None:
    if not body.completion_id:
        return None
    payload = {
        "status": target_status,
        "result_json": stored_result_json,
        "error_code": body.error_code,
        "duration_ms": _complete_duration_ms(body),
        "result_schema_version": _result_schema_version(body),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ── 라우트 ───────────────────────────────────────────────────────────────────────


@router.post("/claim")
async def claim_jobs(
    request: Request,
    body: ClaimRequest,
    agent_id: str = Depends(resolve_agent),
) -> dict:
    """``POST /v1/jobs/claim`` — capability 매칭 PENDING job 을 claim(빈 큐면 ``{"jobs":[]}``)."""

    if agent_id != body.agent_id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="agent token mismatch")

    now = datetime.now(timezone.utc)
    backend = _backend(request)
    max_jobs = await _claim_max_jobs(
        request,
        agent_id=body.agent_id,
        requested_max_jobs=body.max_jobs,
        capabilities=body.capabilities,
        now=now,
    )
    if max_jobs <= 0:
        return {"jobs": []}
    records = await backend.claim(
        agent_id=body.agent_id,
        capabilities=body.capabilities,
        max_jobs=max_jobs,
        lease_seconds=_lease_seconds(request),
        now=now,
    )
    return {
        "jobs": [
            {
                "job_id": r.job_id,
                "type": r.type,
                "target_id": r.target_id,
                "lease_expires_at": _iso_utc(r.lease_expires_at),
                "payload": r.payload_json or {},
            }
            for r in records
        ]
    }


@router.post("/{job_id}/complete")
async def complete_job(
    request: Request,
    job_id: str,
    body: CompleteRequest,
    agent_id: str = Depends(resolve_agent),
) -> dict:
    """``POST /v1/jobs/{id}/complete`` — lease 소유 검증 후 SUCCEEDED/FAILED 기록.

    재할당/만료면 409(Agent 가 lease_lost 흡수), 미존재면 404, 알 수 없는 status 는 422.
    """

    if agent_id != body.agent_id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="agent token mismatch")

    try:
        target_status = map_agent_status(body.status)
    except UnknownAgentStatus as exc:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        stored_result_json = _stored_result_json(body)
        result = await request.app.state.job_completion_service.complete(
            job_id=job_id,
            agent_id=body.agent_id,
            status=target_status,
            result_json=stored_result_json,
            ingest_result_json=body.result_json,
            error_code=body.error_code,
            duration_ms=_complete_duration_ms(body),
            result_schema_version=_result_schema_version(body),
            completion_id=body.completion_id,
            completion_payload_hash=_completion_payload_hash(
                body=body,
                target_status=target_status,
                stored_result_json=stored_result_json,
            ),
            now=datetime.now(timezone.utc),
        )
    except JobCompletionInvalid as exc:
        if str(exc) == "invalid job id":
            raise _invalid_job_id_http_error() from exc
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except JobCompletionNotFound as exc:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="job not found") from exc
    except JobCompletionConflict as exc:
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise _invalid_job_id_http_error() from exc
    return {"job_id": result.job_id, "status": result.status}


@router.post("/{job_id}/preflight")
async def preflight_job(
    request: Request,
    job_id: str,
    agent_id: str = Depends(resolve_agent),
) -> dict:
    """``POST /v1/jobs/{id}/preflight`` — 브라우저/profile 을 열기 전 server 측 유효성 확인.

    Agent 는 claim 한 job 을 실행하기 직전 이 엔드포인트로 server 상태(payload TTL·서버 시각)를
    재확인한다. payload ``expires_at`` 이 지났거나 더는 유효하지 않으면 ``allowed=false`` +
    안전한 ``reason`` 을 돌려준다 — Agent 는 그러면 브라우저를 열지 않고 실패로 닫는다.

    이 job 이 ``agent_id`` 소유 in-flight 가 아니면(미존재/재할당/만료/종료) **fail-closed**
    (``allowed=false``, reason=``payload_expired``) 로 응답해 오래된 lease 가 부수효과를 내지 못하게
    한다(서버가 lease 소유 판단의 단일 소유처).
    """

    now = datetime.now(timezone.utc)
    server_time = _iso_utc(now)
    backend = _backend(request)
    try:
        record = await backend.in_flight_job(job_id=job_id, agent_id=agent_id, now=now)
    except ValueError as exc:
        raise _invalid_job_id_http_error() from exc
    if record is None:
        # 소유 in-flight 아님 → fail-closed(브라우저 열기 금지).
        return {"allowed": False, "reason": "payload_expired", "server_time": server_time}
    allowed, reason = preflight_decision(
        job_type=record.type,
        payload_json=record.payload_json,
        now=now,
    )
    return {"allowed": allowed, "reason": reason, "server_time": server_time}


@router.post("/{job_id}/events", status_code=HTTPStatus.ACCEPTED)
async def emit_event(
    request: Request,
    job_id: str,
    body: EventRequest,
    agent_id: str = Depends(resolve_agent),
) -> dict:
    """``POST /v1/jobs/{id}/events`` — 진행 이벤트 best-effort 수신(본문은 이미 redact 통과값).

    저장/로깅 시 secret 평문이 남지 않게 ``message_redacted`` 를 한 번 더 redact 통과시킨다.
    """

    now = datetime.now(timezone.utc)
    accepted = await _backend(request).emit_event_for_in_flight_job(
        job_id=job_id,
        agent_id=agent_id,
        event_type=body.event_type,
        severity=body.severity,
        message_redacted=redact(body.message_redacted),
        artifact_refs=body.artifact_refs,
        now=now,
    )
    if not accepted:
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail="job lease lost")
    return {"status": "accepted"}
