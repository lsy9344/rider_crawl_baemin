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

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from rider_crawl.redaction import redact

from ..queue.backend import (
    COMPLETE_LEASE_LOST,
    COMPLETE_NOT_FOUND,
    QueueBackend,
)
from ..queue.states import (
    InvalidJobTransition,
    UnknownAgentStatus,
    map_agent_status,
)

# claim 시 부여하는 lease 기간(초). Agent heartbeat(30~60초)보다 길어 연장 여유를 둔다.
# heartbeat 라우트 배선·설정화는 후속 — 5.3 은 단일 서버 상수로 둔다.
DEFAULT_LEASE_SECONDS = 120.0

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


# ── 인증 seam(bearer → agent_id 해석 + 401) ──────────────────────────────────────


def default_resolve_agent_id(token: str) -> str | None:
    """기본 agent identity 해석 seam(5.3 최소 — full lifecycle 은 5.8).

    비어있지 않은 bearer 는 통과시키되 **token 평문을 반환/로그하지 않는다**(non-secret
    sentinel 반환). 실제 queue 연산은 요청 본문의 ``agent_id`` 를 쓴다. 테스트는 알려진
    token→agent_id 매핑/revoke(None→401)를 주입해 이 seam 을 교체한다.
    """

    return "agent" if token else None


def resolve_agent(request: Request) -> str:
    """``Authorization: Bearer <token>`` → agent_id. 누락/무효 token 은 401."""

    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="missing bearer token")
    resolver: Callable[[str], str | None] = request.app.state.resolve_agent_id
    agent_id = resolver(token)
    if not agent_id:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="invalid agent token")
    return agent_id


# ── 요청/응답 스키마(Pydantic v2, snake_case) ─────────────────────────────────────


class ClaimRequest(BaseModel):
    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    max_jobs: int = 1


class CompleteRequest(BaseModel):
    # Agent 는 소문자 status("success"/"failed")를 보낸다 — 서버가 상태머신값으로 매핑.
    status: str
    agent_id: str
    result_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message_redacted: str | None = None
    metrics: dict[str, Any] | None = None
    started_at: float | None = None
    finished_at: float | None = None


class EventRequest(BaseModel):
    event_type: str
    severity: str
    message_redacted: str
    artifact_refs: list[Any] = Field(default_factory=list)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────────


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


# ── 라우트 ───────────────────────────────────────────────────────────────────────


@router.post("/claim")
async def claim_jobs(
    request: Request,
    body: ClaimRequest,
    agent_id: str = Depends(resolve_agent),
) -> dict:
    """``POST /v1/jobs/claim`` — capability 매칭 PENDING job 을 claim(빈 큐면 ``{"jobs":[]}``)."""

    now = datetime.now(timezone.utc)
    records = await _backend(request).claim(
        agent_id=body.agent_id,
        capabilities=body.capabilities,
        max_jobs=body.max_jobs,
        lease_seconds=DEFAULT_LEASE_SECONDS,
        now=now,
    )
    return {
        "jobs": [
            {
                "job_id": r.job_id,
                "type": r.type,
                "target_id": r.target_id,
                "lease_expires_at": _iso_utc(r.lease_expires_at),
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

    try:
        target_status = map_agent_status(body.status)
    except UnknownAgentStatus as exc:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    now = datetime.now(timezone.utc)
    try:
        outcome = await _backend(request).complete(
            job_id=job_id,
            agent_id=body.agent_id,
            status=target_status,
            result_json=body.result_json,
            error_code=body.error_code,
            now=now,
        )
    except InvalidJobTransition as exc:
        # 진행 중이 아닌 job 으로의 전이(미정의) — 충돌로 본다(이미 종료/재할당).
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=str(exc)) from exc

    if outcome.result == COMPLETE_NOT_FOUND:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="job not found")
    if outcome.result == COMPLETE_LEASE_LOST:
        # 409 — lease lost / 재할당. Agent 가 흡수해 이중 success 를 막는다.
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT, detail="job lease lost or reassigned"
        )
    return {"job_id": outcome.job_id, "status": outcome.final_status}


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

    await _backend(request).emit_event(
        job_id=job_id,
        event_type=body.event_type,
        severity=body.severity,
        message_redacted=redact(body.message_redacted),
        artifact_refs=body.artifact_refs,
    )
    return {"status": "accepted"}
