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
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models.agent import Job
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
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    assert_transition,
)


def _as_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    """문자열/UUID/None 을 UUID 로 강제(중립 입력 → ORM 타입)."""
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class PostgresQueueBackend(QueueBackend):
    """``jobs`` 테이블 기반 ``QueueBackend``(``FOR UPDATE SKIP LOCKED`` 정본)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def enqueue(
        self,
        *,
        job_type: str,
        target_id: str | None = None,
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
            await session.commit()
            return CompleteOutcome(COMPLETE_ACCEPTED, job_id, final_status=status)

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

    async def emit_event(
        self,
        *,
        job_id: str,
        event_type: str,
        severity: str,
        message_redacted: str,
        artifact_refs: Sequence[Any] = (),
    ) -> None:
        # 14테이블 계약에 events 테이블이 없다 — best-effort no-op(라우트가 2xx 반환).
        # 영속 events 저장/감사는 후속 스토리(audit_logs 연계) 소유.
        return None
