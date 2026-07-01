"""Job completion workflow service.

The API route owns HTTP validation and status-code mapping. This service owns
queue completion, snapshot ingest preparation, atomic snapshot completion, and
compensation when post-complete snapshot persistence fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from inspect import isawaitable
from typing import Any, Protocol, runtime_checkable

from rider_server.queue.backend import (
    COMPLETE_ACCEPTED,
    COMPLETE_LEASE_LOST,
    COMPLETE_NOT_FOUND,
    ClaimedJobRecord,
    CompleteOutcome,
)
from rider_server.queue.states import (
    InvalidJobTransition,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
)

# The on_completed hook only fires on a real terminal transition. A failure that
# the queue re-queued for retry returns COMPLETE_ACCEPTED with final_status
# PENDING — not a completion — so firing then would send a premature/duplicate
# reply. Fire once, on the terminal attempt.
_TERMINAL_COMPLETION_STATUSES = frozenset({JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED})
from rider_server.services.job_result_ingest_service import (
    JobResultIngestError,
    SnapshotIngestRecord,
)


DEFAULT_COMPLETION_LEASE_SECONDS = 120.0


@dataclass(frozen=True)
class JobCompletionResult:
    job_id: str
    status: str


class JobCompletionConflict(Exception):
    """Completion reached a valid job, but the transition cannot be accepted."""


class JobCompletionNotFound(Exception):
    """Completion target job does not exist."""


class JobCompletionInvalid(ValueError):
    """Completion request is structurally invalid for this backend/workflow."""


class JobCompletionQueue(Protocol):
    async def in_flight_job(
        self,
        *,
        job_id: str,
        agent_id: str,
        now: datetime,
    ) -> ClaimedJobRecord | None: ...

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
    ) -> CompleteOutcome: ...


@runtime_checkable
class JobCompletionRestoreQueue(JobCompletionQueue, Protocol):
    def restore_claimed_after_snapshot_failure(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_seconds: float,
        now: datetime,
    ) -> Any: ...


class SnapshotIngestService(Protocol):
    def prepare_complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        completed_at: datetime,
        expected_target_id: str | None = None,
    ) -> SnapshotIngestRecord | None: ...

    def commit(self, record: SnapshotIngestRecord) -> Any: ...


@runtime_checkable
class AtomicSnapshotIngestService(SnapshotIngestService, Protocol):
    def complete_snapshot_job(
        self,
        record: SnapshotIngestRecord,
        *,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        error_code: str | None,
        duration_ms: int | None,
        result_schema_version: str | None,
        completion_id: str | None = ...,
        completion_payload_hash: str | None = ...,
        now: datetime,
    ) -> Any: ...


class JobCompletionService:
    def __init__(
        self,
        *,
        queue_backend: JobCompletionQueue,
        ingest_service: SnapshotIngestService | None = None,
        lease_seconds: float = DEFAULT_COMPLETION_LEASE_SECONDS,
        on_completed: Any = None,
    ) -> None:
        self._queue_backend = queue_backend
        self._ingest_service = ingest_service
        self._atomic_ingest_service = (
            ingest_service if isinstance(ingest_service, AtomicSnapshotIngestService) else None
        )
        self._lease_seconds = lease_seconds
        # Optional best-effort hook fired after a non-snapshot job completes
        # (RIDER_LOOKUP → scoped KAKAO_SEND reply). Snapshot jobs take the atomic
        # path and do not fire it. Signature:
        #   on_completed(*, job_id, status, result_json, now) -> awaitable | None
        self._on_completed = on_completed

    async def complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        ingest_result_json: dict[str, Any] | None,
        error_code: str | None,
        duration_ms: int | None,
        result_schema_version: str | None,
        completion_id: str | None = None,
        completion_payload_hash: str | None = None,
        now: datetime,
    ) -> JobCompletionResult:
        prepared_ingest = await self._prepare_ingest(
            job_id=job_id,
            agent_id=agent_id,
            status=status,
            result_json=ingest_result_json,
            completion_id=completion_id,
            now=now,
        )

        if prepared_ingest is not None and self._atomic_ingest_service is not None:
            outcome = await self._atomic_complete(
                prepared_ingest,
                agent_id=agent_id,
                status=status,
                result_json=result_json,
                error_code=error_code,
                duration_ms=duration_ms,
                result_schema_version=result_schema_version,
                completion_id=completion_id,
                completion_payload_hash=completion_payload_hash,
                now=now,
            )
            return self._result_from_outcome(outcome, status=status)

        outcome = await self._queue_complete(
            job_id=job_id,
            agent_id=agent_id,
            status=status,
            result_json=result_json,
            error_code=error_code,
            duration_ms=duration_ms,
            result_schema_version=result_schema_version,
            completion_id=completion_id,
            completion_payload_hash=completion_payload_hash,
            now=now,
        )
        result = self._result_from_outcome(outcome, status=status)
        if prepared_ingest is not None:
            await self._commit_ingest_after_queue_complete(
                prepared_ingest,
                job_id=job_id,
                agent_id=agent_id,
                now=now,
            )
        final_status = outcome.final_status or status
        if final_status in _TERMINAL_COMPLETION_STATUSES:
            await self._fire_on_completed(
                job_id=job_id, status=final_status, result_json=ingest_result_json, now=now
            )
        return result

    async def _fire_on_completed(
        self,
        *,
        job_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        # Best-effort: the job is already durably completed, so a reply-dispatch
        # failure must not fail completion (that would trigger an agent retry and
        # risk a double reply). Swallow hook errors.
        if self._on_completed is None:
            return
        try:
            outcome = self._on_completed(
                job_id=job_id, status=status, result_json=result_json, now=now
            )
            if isawaitable(outcome):
                await outcome
        except Exception:  # noqa: BLE001 - reply dispatch is best-effort, never fails completion.
            return

    async def _prepare_ingest(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        completion_id: str | None = None,
        now: datetime,
    ) -> SnapshotIngestRecord | None:
        if self._ingest_service is None or status != JOB_STATUS_SUCCEEDED:
            return None
        expected_target_id = None
        if result_json and result_json.get("result_type") == "snapshot":
            try:
                in_flight_job = await self._queue_backend.in_flight_job(
                    job_id=job_id,
                    agent_id=agent_id,
                    now=now,
                )
            except ValueError as exc:
                raise JobCompletionInvalid("invalid job id") from exc
            if in_flight_job is None:
                # job 이 더 이상 in-flight 가 아님. completion_id 가 있고 atomic ingest 가
                # 활성이면 멱등 재시도일 수 있으므로 여기서 conflict 로 막지 않고, atomic
                # complete_snapshot_job 의 멱등 검사(completion_id 일치 → ACCEPTED)에 위임한다.
                # expected_target_id 검증은 in-flight job 이 없어 건너뛴다(멱등 hit 이면 재삽입
                # 자체를 안 하고, mismatch 면 LEASE_LOST 로 안전하게 막힌다).
                if completion_id and self._atomic_ingest_service is not None:
                    return self._build_replay_record(
                        job_id=job_id, agent_id=agent_id, status=status,
                        result_json=result_json, now=now,
                    )
                outcome = await self._queue_complete(
                    job_id=job_id,
                    agent_id=agent_id,
                    status=status,
                    result_json=None,
                    error_code=None,
                    duration_ms=None,
                    result_schema_version=None,
                    completion_id=None,
                    completion_payload_hash=None,
                    now=now,
                )
                if outcome.result == COMPLETE_NOT_FOUND:
                    raise JobCompletionNotFound("job not found")
                raise JobCompletionConflict("job lease lost or reassigned")
            expected_target_id = in_flight_job.target_id
        try:
            return self._ingest_service.prepare_complete(
                job_id=job_id,
                agent_id=agent_id,
                status=status,
                result_json=result_json,
                completed_at=now,
                expected_target_id=expected_target_id,
            )
        except JobResultIngestError as exc:
            raise JobCompletionInvalid(str(exc)) from exc
        except ValueError as exc:
            raise JobCompletionInvalid("invalid job id") from exc

    def _build_replay_record(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        now: datetime,
    ) -> SnapshotIngestRecord | None:
        """in-flight 가 아닌(이미 terminal) job 의 멱등 replay 용 ingest record 를 만든다.

        in-flight job 이 없어 ``expected_target_id`` 검증은 건너뛴다 — atomic complete 의
        completion_id 멱등 검사가 일치/불일치를 ACCEPTED/LEASE_LOST 로 가른다.
        """

        try:
            return self._ingest_service.prepare_complete(
                job_id=job_id,
                agent_id=agent_id,
                status=status,
                result_json=result_json,
                completed_at=now,
                expected_target_id=None,
            )
        except JobResultIngestError as exc:
            raise JobCompletionInvalid(str(exc)) from exc
        except ValueError as exc:
            raise JobCompletionInvalid("invalid job id") from exc

    async def _atomic_complete(
        self,
        record: SnapshotIngestRecord,
        *,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        error_code: str | None,
        duration_ms: int | None,
        result_schema_version: str | None,
        completion_id: str | None = None,
        completion_payload_hash: str | None = None,
        now: datetime,
    ) -> CompleteOutcome:
        if self._atomic_ingest_service is None:
            raise RuntimeError("atomic ingest service missing")
        try:
            outcome = self._atomic_ingest_service.complete_snapshot_job(
                record,
                agent_id=agent_id,
                status=status,
                result_json=result_json,
                error_code=error_code,
                duration_ms=duration_ms,
                result_schema_version=result_schema_version,
                completion_id=completion_id,
                completion_payload_hash=completion_payload_hash,
                now=now,
            )
            if isawaitable(outcome):
                outcome = await outcome
            return outcome
        except ValueError as exc:
            raise JobCompletionInvalid("invalid job id") from exc
        except InvalidJobTransition as exc:
            raise JobCompletionConflict(str(exc)) from exc

    async def _queue_complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        error_code: str | None,
        duration_ms: int | None,
        result_schema_version: str | None,
        completion_id: str | None = None,
        completion_payload_hash: str | None = None,
        now: datetime,
    ) -> CompleteOutcome:
        try:
            return await self._queue_backend.complete(
                job_id=job_id,
                agent_id=agent_id,
                status=status,
                result_json=result_json,
                error_code=error_code,
                duration_ms=duration_ms,
                result_schema_version=result_schema_version,
                completion_id=completion_id,
                completion_payload_hash=completion_payload_hash,
                now=now,
            )
        except ValueError as exc:
            raise JobCompletionInvalid("invalid job id") from exc
        except InvalidJobTransition as exc:
            raise JobCompletionConflict(str(exc)) from exc

    async def _commit_ingest_after_queue_complete(
        self,
        record: SnapshotIngestRecord,
        *,
        job_id: str,
        agent_id: str,
        now: datetime,
    ) -> None:
        if self._ingest_service is None:
            return
        try:
            committed = self._ingest_service.commit(record)
            if isawaitable(committed):
                await committed
        except Exception:
            if isinstance(self._queue_backend, JobCompletionRestoreQueue):
                restored = self._queue_backend.restore_claimed_after_snapshot_failure(
                    job_id=job_id,
                    agent_id=agent_id,
                    lease_seconds=self._lease_seconds,
                    now=now,
                )
                if isawaitable(restored):
                    await restored
            raise

    @staticmethod
    def _result_from_outcome(
        outcome: CompleteOutcome,
        *,
        status: str,
    ) -> JobCompletionResult:
        if outcome.result == COMPLETE_NOT_FOUND:
            raise JobCompletionNotFound("job not found")
        if outcome.result == COMPLETE_LEASE_LOST:
            raise JobCompletionConflict("job lease lost or reassigned")
        if outcome.result != COMPLETE_ACCEPTED:
            raise JobCompletionConflict("job not accepted")
        return JobCompletionResult(
            job_id=outcome.job_id,
            status=outcome.final_status or status,
        )
