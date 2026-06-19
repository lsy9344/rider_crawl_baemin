"""PostgreSQL ``QueueBackend`` 구현 — Story 5.3 (AC1·AC2·AC3).

``jobs`` 테이블 + ``SELECT … FOR UPDATE SKIP LOCKED`` 로 exactly-one-claim 을 보장한다(FR-13
서버 측 보장의 정본). 5.2 ``db/base.py`` 의 ``async_sessionmaker`` 를 그대로 받아 쓰고 새 엔진을
만들지 않는다. async 경계 준수: async 함수 본문에서 ``time.sleep``/``subprocess.*`` 를 직접
호출하지 않는다(lease 만료는 주입 ``now`` 로 결정, 대기는 async-native).
[Source: architecture.md:140,432-434, src/rider_server/db/base.py, src/rider_server/db/models/agent.py]

claim 알고리즘:
  SELECT id,type,target_id,status,run_after,attempts FROM jobs
   WHERE status='PENDING' AND (run_after IS NULL OR run_after<=:now) AND type = ANY(:caps)
   ORDER BY run_after NULLS FIRST LIMIT :max_jobs FOR UPDATE SKIP LOCKED
  → 잡은 행을 CLAIMED + agent_id + lease_expires_at + claimed_at 로 UPDATE, 같은 트랜잭션 commit.
recover_stale: UPDATE jobs SET status='PENDING', agent_id=NULL, lease_expires_at=NULL,
  claimed_at=NULL WHERE status IN ('CLAIMED','RUNNING') AND lease_expires_at <= :now.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_crawl.redaction import redact

from ..db.models.account import PlatformAccount
from ..db.models.agent import Job
from ..db.models.audit import AuditLog
from ..db.models.messaging import DeliveryLog
from ..domain import AuditResult, BaeminAuthState, DeliveryStatus, FailureCategory
from .backend import (
    COMPLETE_ACCEPTED,
    COMPLETE_LEASE_LOST,
    COMPLETE_NOT_FOUND,
    ClaimedJobRecord,
    CompleteOutcome,
    QueueBackend,
)
from .states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_KAKAO_SEND,
    assert_transition,
)

_MAX_EVENT_TEXT_LENGTH = 200
_MAX_EVENT_MESSAGE_LENGTH = 500
_MAX_EVENT_ARTIFACTS = 5


def _as_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    """문자열/UUID/None 을 UUID 로 강제(중립 입력 → ORM 타입)."""
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _safe_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    try:
        return _as_uuid(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _bounded_event_text(value: Any, *, limit: int = _MAX_EVENT_TEXT_LENGTH) -> str:
    text = redact(str(value)).strip()
    text = "".join(" " if ord(ch) < 32 or 127 <= ord(ch) <= 159 else ch for ch in text)
    return text[:limit]


def _agent_event_diff(
    *,
    job_id: str,
    agent_id: str | None,
    severity: str,
    message_redacted: str,
    artifact_refs: Sequence[Any],
) -> dict[str, Any]:
    return {
        "job_id": _bounded_event_text(job_id),
        "agent_id": _bounded_event_text(agent_id or ""),
        "severity": _bounded_event_text(severity),
        "message_redacted": _bounded_event_text(
            message_redacted, limit=_MAX_EVENT_MESSAGE_LENGTH
        ),
        "artifact_refs": [
            _bounded_event_text(ref) for ref in list(artifact_refs)[:_MAX_EVENT_ARTIFACTS]
        ],
    }


def kakao_delivery_log_values(
    *,
    job_status: str,
    error_code: str | None,
    now: datetime,
) -> dict[str, Any] | None:
    """Map a completed KAKAO_SEND job status to delivery_logs update values."""

    if job_status == JOB_STATUS_SUCCEEDED:
        return {
            "status": DeliveryStatus.SENT.value,
            "error_code": None,
            "sent_at": now,
        }
    if job_status == JOB_STATUS_FAILED:
        return {
            "status": DeliveryStatus.FAILED.value,
            "error_code": error_code or FailureCategory.KAKAO_FAILURE.value,
            "sent_at": None,
        }
    return None


def _platform_account_auth_update(
    job: Job,
    error_code: str | None,
) -> tuple[str, str] | None:
    """Return ``(platform_account_id, auth_state)`` to persist for crawl/auth results."""

    if not isinstance(job.payload_json, dict):
        return None
    platform_account_id = job.payload_json.get("platform_account_id")
    if not platform_account_id:
        return None

    result_json = job.result_json if isinstance(job.result_json, dict) else {}
    auth_state = result_json.get("auth_state")
    if auth_state in {
        BaeminAuthState.ACTIVE.value,
        BaeminAuthState.AUTH_REQUIRED.value,
        BaeminAuthState.AUTH_VERIFIED.value,
        BaeminAuthState.USER_ACTION_PENDING.value,
        BaeminAuthState.CENTER_MISMATCH.value,
        BaeminAuthState.BLOCKED_OR_CAPTCHA.value,
    }:
        return str(platform_account_id), str(auth_state)

    if result_json.get("mismatch") == BaeminAuthState.CENTER_MISMATCH.value:
        return str(platform_account_id), BaeminAuthState.CENTER_MISMATCH.value
    if error_code == FailureCategory.AUTH_REQUIRED.value:
        return str(platform_account_id), BaeminAuthState.AUTH_REQUIRED.value
    if error_code == FailureCategory.TARGET_VALIDATION_FAILURE.value:
        return str(platform_account_id), BaeminAuthState.CENTER_MISMATCH.value
    return None


class PostgresQueueBackend(QueueBackend):
    """``jobs`` 테이블 기반 ``QueueBackend``(``FOR UPDATE SKIP LOCKED`` 정본)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def enqueue(
        self,
        *,
        job_type: str,
        target_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
        run_after: datetime | None = None,
        now: datetime,
    ) -> str:
        job_id = uuid.uuid4()
        async with self._session_factory() as session:
            session.add(
                Job(
                    id=job_id,
                    type=job_type,
                    target_id=_as_uuid(target_id),
                    payload_json=payload_json,
                    agent_id=None,
                    status=JOB_STATUS_PENDING,
                    run_after=run_after,
                    attempts=0,
                )
            )
            await session.commit()
        return str(job_id)

    async def claim(
        self,
        *,
        agent_id: str,
        capabilities: Sequence[str],
        max_jobs: int,
        lease_seconds: float,
        now: datetime,
    ) -> list[ClaimedJobRecord]:
        caps = list(capabilities)
        if max_jobs <= 0 or not caps:
            return []
        lease_until = now + timedelta(seconds=lease_seconds)
        claimed: list[ClaimedJobRecord] = []
        async with self._session_factory() as session:
            stmt = (
                select(Job)
                .where(
                    Job.status == JOB_STATUS_PENDING,
                    (Job.run_after.is_(None)) | (Job.run_after <= now),
                    Job.type.in_(caps),
                )
                .order_by(Job.run_after.asc().nullsfirst())
                .limit(max_jobs)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(stmt)).scalars().all()
            for job in rows:
                assert_transition(job.status, JOB_STATUS_CLAIMED)
                job.status = JOB_STATUS_CLAIMED
                job.agent_id = _as_uuid(agent_id)
                job.lease_expires_at = lease_until
                job.claimed_at = now
                claimed.append(
                    ClaimedJobRecord(
                        job_id=str(job.id),
                        type=job.type,
                        target_id=None if job.target_id is None else str(job.target_id),
                        lease_expires_at=lease_until,
                        payload_json=job.payload_json,
                        attempts=job.attempts,
                        status=job.status,
                    )
                )
            await session.commit()
        return claimed

    async def complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None = None,
        error_code: str | None = None,
        now: datetime,
    ) -> CompleteOutcome:
        async with self._session_factory() as session:
            # 행 잠금으로 동시 complete 직렬화(재할당된 job 의 이중 success 차단).
            stmt = (
                select(Job).where(Job.id == _as_uuid(job_id)).with_for_update()
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None:
                return CompleteOutcome(COMPLETE_NOT_FOUND, job_id)
            owner_mismatch = job.agent_id != _as_uuid(agent_id)
            not_in_flight = job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
            expired = job.lease_expires_at is None or now >= job.lease_expires_at
            if owner_mismatch or not_in_flight or expired:
                await session.rollback()
                return CompleteOutcome(COMPLETE_LEASE_LOST, job_id)
            assert_transition(job.status, status)
            job.status = status
            job.result_json = result_json
            job.error_code = error_code
            job.lease_expires_at = None
            await self._mark_auth_required_account(
                session,
                job=job,
                error_code=error_code,
            )
            await self._update_kakao_delivery_log(
                session,
                job=job,
                status=status,
                error_code=error_code,
                now=now,
            )
            await session.commit()
            return CompleteOutcome(COMPLETE_ACCEPTED, job_id, final_status=status)

    async def _mark_auth_required_account(
        self,
        session: AsyncSession,
        *,
        job: Job,
        error_code: str | None,
    ) -> None:
        update_values = _platform_account_auth_update(job, error_code)
        if update_values is None:
            return
        platform_account_id, auth_state = update_values
        await session.execute(
            update(PlatformAccount)
            .where(PlatformAccount.id == _as_uuid(str(platform_account_id)))
            .values(auth_state=auth_state)
        )

    async def _update_kakao_delivery_log(
        self,
        session: AsyncSession,
        *,
        job: Job,
        status: str,
        error_code: str | None,
        now: datetime,
    ) -> None:
        if job.type != JOB_TYPE_KAKAO_SEND or not isinstance(job.payload_json, dict):
            return
        delivery_log_id = job.payload_json.get("delivery_log_id")
        if not delivery_log_id:
            return
        values = kakao_delivery_log_values(
            job_status=status,
            error_code=error_code,
            now=now,
        )
        if values is None:
            return
        await session.execute(
            update(DeliveryLog)
            .where(DeliveryLog.id == _as_uuid(str(delivery_log_id)))
            .values(**values)
        )

    async def in_flight_job(
        self,
        *,
        job_id: str,
        agent_id: str,
        now: datetime,
    ) -> ClaimedJobRecord | None:
        async with self._session_factory() as session:
            stmt = select(Job).where(
                Job.id == _as_uuid(job_id),
                Job.agent_id == _as_uuid(agent_id),
                Job.status.in_((JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)),
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at > now,
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None:
                return None
            return ClaimedJobRecord(
                job_id=str(job.id),
                type=job.type,
                target_id=None if job.target_id is None else str(job.target_id),
                lease_expires_at=job.lease_expires_at,
                payload_json=job.payload_json,
                attempts=job.attempts,
                status=job.status,
            )

    async def extend_lease(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_seconds: float,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            stmt = (
                select(Job).where(Job.id == _as_uuid(job_id)).with_for_update()
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if (
                job is None
                or job.agent_id != _as_uuid(agent_id)
                or job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                or job.lease_expires_at is None
                or now >= job.lease_expires_at
            ):
                await session.rollback()
                return False
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            await session.commit()
            return True

    async def recover_stale(self, *, now: datetime) -> int:
        async with self._session_factory() as session:
            stmt = (
                update(Job)
                .where(
                    Job.status.in_((JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)),
                    Job.lease_expires_at.is_not(None),
                    Job.lease_expires_at <= now,
                )
                .values(
                    status=JOB_STATUS_PENDING,
                    agent_id=None,
                    lease_expires_at=None,
                    claimed_at=None,
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    async def restore_claimed_after_snapshot_failure(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_seconds: float,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            stmt = (
                select(Job).where(Job.id == _as_uuid(job_id)).with_for_update()
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if (
                job is None
                or job.agent_id != _as_uuid(agent_id)
                or job.status != JOB_STATUS_SUCCEEDED
            ):
                await session.rollback()
                return False
            job.status = JOB_STATUS_CLAIMED
            job.result_json = None
            job.error_code = None
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            await session.commit()
            return True

    async def emit_event(
        self,
        *,
        job_id: str,
        event_type: str,
        severity: str,
        message_redacted: str,
        artifact_refs: Sequence[Any] = (),
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        created_at = now if now is not None else datetime.now(timezone.utc)
        diff = _agent_event_diff(
            job_id=job_id,
            agent_id=agent_id,
            severity=severity,
            message_redacted=message_redacted,
            artifact_refs=artifact_refs,
        )
        audit = AuditLog(
            actor_id=_safe_uuid(agent_id),
            action=_bounded_event_text(event_type),
            target_type="JOB",
            target_id=_safe_uuid(job_id),
            diff_redacted=diff,
            created_at=created_at,
            source="AGENT",
            reason=diff["message_redacted"],
            result=AuditResult.SUCCESS.value,
        )
        async with self._session_factory() as session:
            session.add(audit)
            await session.commit()
