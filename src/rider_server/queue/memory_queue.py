"""in-memory ``QueueBackend`` 구현 — Story 5.3 (AC1·AC2·AC3).

DB 없이 단일-claim·lease 의미론·상태 전이를 강제하는 **실제로 동작하는 fake**(빈 stub 아님)다.
``threading.Lock`` 으로 claim/complete/recover 를 atomic 하게 만들어, 같은 ``PENDING`` job 에
두 claim 이 와도 정확히 하나만 성공하게 한다(in-memory 동형 보장 — PG 의 ``FOR UPDATE SKIP
LOCKED`` 와 의미가 같다). 주입 ``now`` 로 lease 만료를 결정적으로 검증한다.

이게 AC1 계약 테스트의 always-run 대상이자 AC4 end-to-end 의 DB-less 경로다. 메서드는
``async`` 지만 본문은 동기(no ``await``)이므로 단일 이벤트 루프에서 claim 임계구역이 쪼개지지
않아 단일-claim 이 결정적으로 보장된다(lock 은 멀티스레드 호출에도 안전하게 하는 추가 가드).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

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
    JOB_STATUS_SUCCEEDED,
    assert_transition,
)


@dataclass
class _Job:
    """in-memory job 행(가변). PG ``jobs`` 컬럼을 미러한다."""

    job_id: str
    type: str
    target_id: str | None
    status: str
    attempts: int = 0
    run_after: datetime | None = None
    payload_json: dict[str, Any] | None = None
    agent_id: str | None = None
    lease_expires_at: datetime | None = None
    claimed_at: datetime | None = None
    result_json: dict[str, Any] | None = None
    error_code: str | None = None


class InMemoryQueueBackend(QueueBackend):
    """프로세스 메모리 기반 ``QueueBackend``(lock 으로 atomic claim 강제)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        #: best-effort 이벤트 기록(테스트 가시성). secret 평문 없음(redact 통과값).
        self.events: list[dict[str, Any]] = []

    async def enqueue(
        self,
        *,
        job_type: str,
        target_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
        run_after: datetime | None = None,
        now: datetime,
    ) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = _Job(
                job_id=job_id,
                type=job_type,
                target_id=target_id,
                payload_json=dict(payload_json) if payload_json is not None else None,
                status=JOB_STATUS_PENDING,
                run_after=run_after,
            )
        return job_id

    async def claim(
        self,
        *,
        agent_id: str,
        capabilities: Sequence[str],
        max_jobs: int,
        lease_seconds: float,
        now: datetime,
    ) -> list[ClaimedJobRecord]:
        caps = set(capabilities)
        lease_until = now + timedelta(seconds=lease_seconds)
        claimed: list[ClaimedJobRecord] = []
        with self._lock:
            candidates = [
                job
                for job in self._jobs.values()
                if job.status == JOB_STATUS_PENDING
                and (job.run_after is None or job.run_after <= now)
                and job.type in caps
            ]
            # run_after NULLS FIRST, 그다음 run_after asc(오래 대기한 job 우선).
            candidates.sort(
                key=lambda j: (j.run_after is not None, j.run_after or now)
            )
            for job in candidates[: max(0, max_jobs)]:
                assert_transition(job.status, JOB_STATUS_CLAIMED)
                job.status = JOB_STATUS_CLAIMED
                job.agent_id = agent_id
                job.lease_expires_at = lease_until
                job.claimed_at = now
                claimed.append(
                    ClaimedJobRecord(
                        job_id=job.job_id,
                        type=job.type,
                        target_id=job.target_id,
                        lease_expires_at=lease_until,
                        payload_json=dict(job.payload_json) if job.payload_json is not None else None,
                        attempts=job.attempts,
                        status=job.status,
                    )
                )
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
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return CompleteOutcome(COMPLETE_NOT_FOUND, job_id)
            if (
                job.agent_id != agent_id
                or job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                or _lease_expired(job.lease_expires_at, now)
            ):
                # 만료/재할당/이미 종료 → 이 Agent 는 더는 소유하지 않는다(이중 success 차단).
                return CompleteOutcome(COMPLETE_LEASE_LOST, job_id)
            assert_transition(job.status, status)
            job.status = status
            job.result_json = result_json
            job.error_code = error_code
            job.lease_expires_at = None
            return CompleteOutcome(COMPLETE_ACCEPTED, job_id, final_status=status)

    async def in_flight_job(
        self,
        *,
        job_id: str,
        agent_id: str,
        now: datetime,
    ) -> ClaimedJobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if (
                job is None
                or job.agent_id != agent_id
                or job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                or _lease_expired(job.lease_expires_at, now)
            ):
                return None
            return ClaimedJobRecord(
                job_id=job.job_id,
                type=job.type,
                target_id=job.target_id,
                lease_expires_at=job.lease_expires_at,
                payload_json=dict(job.payload_json) if job.payload_json is not None else None,
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
        with self._lock:
            job = self._jobs.get(job_id)
            if (
                job is None
                or job.agent_id != agent_id
                or job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                or _lease_expired(job.lease_expires_at, now)
            ):
                return False
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return True

    async def recover_stale(self, *, now: datetime) -> int:
        recovered = 0
        with self._lock:
            for job in self._jobs.values():
                if job.status in (
                    JOB_STATUS_CLAIMED,
                    JOB_STATUS_RUNNING,
                ) and _lease_expired(job.lease_expires_at, now):
                    assert_transition(job.status, JOB_STATUS_PENDING)
                    job.status = JOB_STATUS_PENDING
                    job.agent_id = None
                    job.lease_expires_at = None
                    job.claimed_at = None
                    recovered += 1
        return recovered

    async def restore_claimed_after_snapshot_failure(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_seconds: float,
        now: datetime,
    ) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if (
                job is None
                or job.agent_id != agent_id
                or job.status != JOB_STATUS_SUCCEEDED
            ):
                return False
            job.status = JOB_STATUS_CLAIMED
            job.result_json = None
            job.error_code = None
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
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
        # 이미 redact 통과값 — 그대로 기록(테스트 가시성). secret 평문 없음.
        event = {
            "job_id": job_id,
            "event_type": event_type,
            "severity": severity,
            "message_redacted": message_redacted,
            "artifact_refs": list(artifact_refs),
        }
        if agent_id is not None:
            event["agent_id"] = agent_id
        if now is not None:
            event["created_at"] = now
        self.events.append(event)

    # ── 테스트 가시성 헬퍼(인터페이스 아님 — in-memory 전용) ──────────────────────
    def job_status(self, job_id: str) -> str | None:
        """job 의 현재 status(없으면 None) — 테스트 단언용."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job.status if job is not None else None

    def job_snapshot(self, job_id: str) -> _Job | None:
        """job 행 스냅샷(없으면 None) — 테스트 단언용."""
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else _Job(**vars(job))


def _lease_expired(lease_expires_at: datetime | None, now: datetime) -> bool:
    """lease 가 만료되었는가. 없거나 ``now >= lease_expires_at`` 이면 만료(Agent self-check 동형)."""

    return lease_expires_at is None or now >= lease_expires_at
