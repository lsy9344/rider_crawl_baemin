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
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from .backend import (
    COMPLETE_ACCEPTED,
    COMPLETE_LEASE_LOST,
    COMPLETE_NOT_FOUND,
    ClaimedJobRecord,
    CompleteOutcome,
    QueueBackend,
    RetryDecider,
)
from .retry import default_retry_decider
from .states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RETRY,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    assert_transition,
    stale_recovery_reason,
)

STALE_LEASE_ERROR_CODE = "CRAWL_TIMEOUT"


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
    assigned_agent_id: str | None = None
    agent_id: str | None = None
    lease_expires_at: datetime | None = None
    claimed_at: datetime | None = None
    result_json: dict[str, Any] | None = None
    error_code: str | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    result_schema_version: str | None = None
    completion_id: str | None = None
    completion_payload_hash: str | None = None
    last_failed_at: datetime | None = None


class InMemoryQueueBackend(QueueBackend):
    """프로세스 메모리 기반 ``QueueBackend``(lock 으로 atomic claim 강제)."""

    def __init__(self, *, retry_decider: RetryDecider | None = default_retry_decider) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._retry_decider = retry_decider
        #: best-effort 이벤트 기록(테스트 가시성). secret 평문 없음(redact 통과값).
        self.events: list[dict[str, Any]] = []

    async def enqueue(
        self,
        *,
        job_type: str,
        target_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
        assigned_agent_id: str | None = None,
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
                assigned_agent_id=assigned_agent_id,
                status=JOB_STATUS_PENDING,
                run_after=run_after,
            )
        return job_id

    async def get_job_status(self, job_id: str) -> str | None:
        """잡 단건 상태(UPPER_SNAKE) 조회 — 없으면 None(채널 전송 테스트 폴링용)."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job.status if job is not None else None

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
                and (job.assigned_agent_id is None or job.assigned_agent_id == agent_id)
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
        duration_ms: int | None = None,
        result_schema_version: str | None = None,
        completion_id: str | None = None,
        completion_payload_hash: str | None = None,
        now: datetime,
    ) -> CompleteOutcome:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return CompleteOutcome(COMPLETE_NOT_FOUND, job_id)
            if completion_id and job.completion_id == completion_id:
                if (
                    job.agent_id == agent_id
                    and job.completion_payload_hash == completion_payload_hash
                    and job.status in (JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED)
                ):
                    return CompleteOutcome(COMPLETE_ACCEPTED, job_id, final_status=job.status)
                return CompleteOutcome(COMPLETE_LEASE_LOST, job_id)
            if (
                job.agent_id != agent_id
                or job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                or _lease_expired(job.lease_expires_at, now)
            ):
                # 만료/재할당/이미 종료 → 이 Agent 는 더는 소유하지 않는다(이중 success 차단).
                return CompleteOutcome(COMPLETE_LEASE_LOST, job_id)
            next_attempt = job.attempts + 1
            retry_decision = (
                self._retry_decider(error_code, next_attempt, now)
                if status == JOB_STATUS_FAILED and self._retry_decider is not None
                else None
            )
            if retry_decision is not None:
                assert_transition(job.status, JOB_STATUS_RETRY)
                assert_transition(JOB_STATUS_RETRY, JOB_STATUS_PENDING)
                job.status = JOB_STATUS_PENDING
                job.attempts = next_attempt
                job.run_after = retry_decision.run_after
                job.agent_id = None
                job.lease_expires_at = None
                job.claimed_at = None
                job.result_json = result_json
                job.error_code = error_code
                job.last_failed_at = now
                return CompleteOutcome(COMPLETE_ACCEPTED, job_id, final_status=JOB_STATUS_PENDING)
            assert_transition(job.status, status)
            job.status = status
            job.result_json = result_json
            job.error_code = error_code
            job.lease_expires_at = None
            job.completed_at = now
            if status == JOB_STATUS_FAILED:
                job.last_failed_at = now
            job.duration_ms = duration_ms
            job.result_schema_version = result_schema_version
            job.completion_id = completion_id
            job.completion_payload_hash = completion_payload_hash
            return CompleteOutcome(COMPLETE_ACCEPTED, job_id, final_status=status)

    async def count_in_flight(
        self,
        *,
        agent_id: str,
        job_types: Sequence[str] = (),
        now: datetime | None = None,
    ) -> int:
        type_filter = set(job_types)
        with self._lock:
            return sum(
                1
                for job in self._jobs.values()
                if job.agent_id == agent_id
                and job.status in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                and (not type_filter or job.type in type_filter)
                and (now is None or not _lease_expired(job.lease_expires_at, now))
            )

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
        extended = await self.extend_leases(
            job_ids=[job_id],
            agent_id=agent_id,
            lease_seconds=lease_seconds,
            now=now,
        )
        return job_id in extended

    async def extend_leases(
        self,
        *,
        job_ids: Sequence[str],
        agent_id: str,
        lease_seconds: float,
        now: datetime,
    ) -> set[str]:
        unique_job_ids = list(dict.fromkeys(job_ids))
        extended: set[str] = set()
        with self._lock:
            lease_until = now + timedelta(seconds=lease_seconds)
            for job_id in unique_job_ids:
                job = self._jobs.get(job_id)
                if (
                    job is None
                    or job.agent_id != agent_id
                    or job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                    or _lease_expired(job.lease_expires_at, now)
                ):
                    continue
                job.lease_expires_at = lease_until
                extended.add(job_id)
        return extended

    async def recover_stale(self, *, now: datetime, batch_size: int | None = None) -> int:
        recovered = 0
        limit = batch_size if batch_size is not None and batch_size > 0 else None
        with self._lock:
            # (1) 만료 lease 의 CLAIMED/RUNNING job. (2) payload TTL 이 지난 **PENDING** scheduled
            # crawl/auth job — 서버 downtime 뒤 누적된 stale backlog 가 한 번에 claim 되지 않게
            # 미리 terminal 종료한다(Task 6). PENDING 은 lease 가 없으니 payload expires_at 로만
            # stale 판정한다.
            stale_jobs = [
                job
                for job in self._jobs.values()
                if (
                    job.status in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                    and _lease_expired(job.lease_expires_at, now)
                )
                or (
                    job.status == JOB_STATUS_PENDING
                    and stale_recovery_reason(
                        job_type=job.type, payload_json=job.payload_json, now=now
                    )
                    is not None
                )
            ]
            stale_jobs.sort(
                key=lambda job: (
                    job.lease_expires_at or datetime.max.replace(tzinfo=timezone.utc),
                    job.job_id,
                )
            )
            for job in stale_jobs[:limit]:
                next_attempt = job.attempts + 1
                # 브라우저를 여는 interactive job / scheduled crawl 은 payload TTL 이 지났으면
                # 재시도하지 않고 terminal FAILED + safe reason 으로 닫는다(무제한 재실행 차단).
                # AUTH_COUPANG_2FA 는 CLAIMED/RUNNING lease 만료 자체로 terminal 종료(중복 OTP 방지).
                stale_reason = stale_recovery_reason(
                    job_type=job.type,
                    payload_json=job.payload_json,
                    now=now,
                    job_status=job.status,
                )
                # PENDING job 은 lease 만료 retry 대상이 아니다 — stale 이면 terminal 종료만 한다.
                retry_decision = (
                    self._retry_decider(STALE_LEASE_ERROR_CODE, next_attempt, now)
                    if (
                        self._retry_decider is not None
                        and stale_reason is None
                        and job.status in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                    )
                    else None
                )
                if retry_decision is not None:
                    assert_transition(job.status, JOB_STATUS_PENDING)
                    job.status = JOB_STATUS_PENDING
                    job.run_after = retry_decision.run_after
                else:
                    assert_transition(job.status, JOB_STATUS_FAILED)
                    job.status = JOB_STATUS_FAILED
                    job.run_after = None
                    job.completed_at = now
                    if stale_reason is not None:
                        existing = job.result_json if isinstance(job.result_json, dict) else {}
                        job.result_json = {**existing, "reason": stale_reason}
                job.attempts = next_attempt
                job.agent_id = None
                job.lease_expires_at = None
                job.claimed_at = None
                job.error_code = STALE_LEASE_ERROR_CODE
                job.last_failed_at = now
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

    async def emit_event_for_in_flight_job(
        self,
        *,
        job_id: str,
        event_type: str,
        severity: str,
        message_redacted: str,
        artifact_refs: Sequence[Any] = (),
        agent_id: str,
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
            event = {
                "job_id": job_id,
                "event_type": event_type,
                "severity": severity,
                "message_redacted": message_redacted,
                "artifact_refs": list(artifact_refs),
                "agent_id": agent_id,
                "created_at": now,
            }
            self.events.append(event)
            return True

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
