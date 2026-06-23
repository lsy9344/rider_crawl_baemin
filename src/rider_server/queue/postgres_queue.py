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
    JOB_TYPE_KAKAO_SEND,
    assert_transition,
    stale_recovery_reason,
)

_MAX_EVENT_TEXT_LENGTH = 200
_MAX_EVENT_MESSAGE_LENGTH = 500
_MAX_EVENT_ARTIFACTS = 5


def _iso_utc_z(dt: datetime) -> str:
    """datetime → ``YYYY-MM-DDTHH:MM:SSZ``(microsecond 0, UTC). payload ``expires_at`` 정규형과 동일.

    payload 의 ``expires_at`` 들은 모두 이 형식으로 생성되므로(scheduler/admin 의 ``_iso_utc``),
    같은 정규형의 now 문자열과 **사전식 비교**가 곧 시각 비교가 된다(SQL 텍스트 비교용).
    """

    return (
        dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
STALE_LEASE_ERROR_CODE = "CRAWL_TIMEOUT"

#: Coupang 자동 이메일 2FA 복구 모드 마커(scheduler payload / agent result 공용).
RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA = "coupang_auto_email_2fa"
#: 자동 복구 실패 뒤 같은 계정에 재시도를 막는 cooldown 길이. 한 계정에 여러 target 이 묶여
#: 있으면 계정 단위로 적용된다(문서에 명시 — queue-backlog-handling-policy.md).
COUPANG_AUTO_RECOVERY_COOLDOWN = timedelta(hours=6)


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
            # 실패 delivery_log 에 정본 실패 시각을 남긴다 — 이게 없으면 sent_at·last_failed_at 둘
            # 다 NULL 이라 대시보드 최신-실패 집계가 이 실패를 "시각 없음"으로 보고 무시해, 더
            # 오래된 실패 코드(예: 해소된 TARGET_VALIDATION_FAILURE)가 카드에 굳는다.
            "last_failed_at": now,
        }
    return None


# ── Coupang 2FA 세부 복구 상태(result_json.auth_recovery_state) → 계정 coarse gate ─────
# crawl-coupang-auth-separation Decision 3: 계정 auth_state 는 coarse gate, job result 는 세부
# 복구 상태. AUTH_COUPANG_2FA result 가 coarse auth_state 를 같이 실어 보내므로 보통 그 값을
# 그대로 쓰지만, 세부 상태만 온 경우를 대비해 결정적 fallback 매핑을 둔다(세부→gate).
_COUPANG_RECOVERY_STATE_TO_GATE: dict[str, str] = {
    "ACTIVE": BaeminAuthState.ACTIVE.value,
    "USER_ACTION_REQUIRED": BaeminAuthState.USER_ACTION_PENDING.value,
    "EMAIL_AUTH_REQUIRED": BaeminAuthState.AUTH_REQUIRED.value,
    "RECOVERY_FAILED": BaeminAuthState.AUTH_REQUIRED.value,
}


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
        BaeminAuthState.UNKNOWN.value,
        BaeminAuthState.ACTIVE.value,
        BaeminAuthState.AUTH_REQUIRED.value,
        BaeminAuthState.AUTH_VERIFIED.value,
        BaeminAuthState.USER_ACTION_PENDING.value,
        BaeminAuthState.CENTER_MISMATCH.value,
        BaeminAuthState.BLOCKED_OR_CAPTCHA.value,
    }:
        return str(platform_account_id), str(auth_state)

    # AUTH_COUPANG_2FA 가 coarse auth_state 없이 세부 상태만 실어 보낸 경우의 결정적 fallback.
    recovery_state = result_json.get("auth_recovery_state")
    gate = _COUPANG_RECOVERY_STATE_TO_GATE.get(str(recovery_state or ""))
    if gate is not None:
        return str(platform_account_id), gate

    if result_json.get("mismatch") == BaeminAuthState.CENTER_MISMATCH.value:
        return str(platform_account_id), BaeminAuthState.CENTER_MISMATCH.value
    if error_code == FailureCategory.AUTH_REQUIRED.value:
        return str(platform_account_id), BaeminAuthState.AUTH_REQUIRED.value
    if error_code == FailureCategory.TARGET_VALIDATION_FAILURE.value:
        return str(platform_account_id), BaeminAuthState.CENTER_MISMATCH.value
    return None


def _is_coupang_recovery_job(job: Job) -> bool:
    """이 완료가 Coupang 자동 이메일 2FA 복구 crawl 인가(payload 또는 result 의 recovery_mode)."""

    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    result = job.result_json if isinstance(job.result_json, dict) else {}
    return (
        payload.get("recovery_mode") == RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA
        or result.get("recovery_mode") == RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA
    )


def coupang_recovery_state_values(
    *,
    job: Job,
    status: str,
    now: datetime,
) -> tuple[str, dict[str, Any]] | None:
    """``(platform_account_id, account update values)`` 를 돌려준다(복구 job 아니면 None).

    "한 번만 자동 복구 + 실패 뒤 cooldown" 을 계정 단위로 강제한다:

    * 실패(``FAILED``) → ``auto_recovery_attempted_at``/``auto_recovery_failed_at`` 를 now 로,
      ``auto_recovery_cooldown_until`` 를 ``now + COUPANG_AUTO_RECOVERY_COOLDOWN`` 으로 설정해
      cooldown 동안 scheduler 가 새 복구 crawl 을 만들지 않게 한다.
    * 성공(``SUCCEEDED``) → ``auto_recovery_attempted_at`` 는 now 로 남기되
      ``auto_recovery_cooldown_until``/``auto_recovery_failed_at`` 를 클리어해 정상 스케줄로 복귀.

    secret 0(시각만). ``auth_state`` 갱신은 기존 :func:`_platform_account_auth_update` 가 담당한다.
    """

    if not _is_coupang_recovery_job(job):
        return None
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    platform_account_id = payload.get("platform_account_id")
    if not platform_account_id:
        return None
    if status == JOB_STATUS_FAILED:
        values = {
            "auto_recovery_attempted_at": now,
            "auto_recovery_failed_at": now,
            "auto_recovery_cooldown_until": now + COUPANG_AUTO_RECOVERY_COOLDOWN,
        }
    elif status == JOB_STATUS_SUCCEEDED:
        values = {
            "auto_recovery_attempted_at": now,
            "auto_recovery_failed_at": None,
            "auto_recovery_cooldown_until": None,
        }
    else:
        return None
    return str(platform_account_id), values


class PostgresQueueBackend(QueueBackend):
    """``jobs`` 테이블 기반 ``QueueBackend``(``FOR UPDATE SKIP LOCKED`` 정본)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retry_decider: RetryDecider | None = default_retry_decider,
    ) -> None:
        self._session_factory = session_factory
        self._retry_decider = retry_decider

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
        job_id = uuid.uuid4()
        async with self._session_factory() as session:
            session.add(
                Job(
                    id=job_id,
                    type=job_type,
                    target_id=_as_uuid(target_id),
                    assigned_agent_id=_as_uuid(assigned_agent_id),
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
                    (Job.assigned_agent_id.is_(None))
                    | (Job.assigned_agent_id == _as_uuid(agent_id)),
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
        duration_ms: int | None = None,
        result_schema_version: str | None = None,
        completion_id: str | None = None,
        completion_payload_hash: str | None = None,
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
            if completion_id and job.completion_id == completion_id:
                if (
                    job.agent_id == _as_uuid(agent_id)
                    and job.completion_payload_hash == completion_payload_hash
                    and job.status in (JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED)
                ):
                    final_status = job.status
                    await session.rollback()
                    return CompleteOutcome(COMPLETE_ACCEPTED, job_id, final_status=final_status)
                await session.rollback()
                return CompleteOutcome(COMPLETE_LEASE_LOST, job_id)
            owner_mismatch = job.agent_id != _as_uuid(agent_id)
            not_in_flight = job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
            expired = job.lease_expires_at is None or now >= job.lease_expires_at
            if owner_mismatch or not_in_flight or expired:
                await session.rollback()
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
                await session.commit()
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
            await self._mark_auth_required_account(
                session,
                job=job,
                error_code=error_code,
            )
            await self._persist_coupang_recovery_state(
                session,
                job=job,
                status=status,
                now=now,
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

    async def count_in_flight(
        self,
        *,
        agent_id: str,
        job_types: Sequence[str] = (),
        now: datetime | None = None,
    ) -> int:
        from sqlalchemy import func

        agent_uuid = _safe_uuid(agent_id)
        if agent_uuid is None:
            return 0
        stmt = (
            select(func.count())
            .select_from(Job)
            .where(
                Job.agent_id == agent_uuid,
                Job.status.in_((JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)),
            )
        )
        if job_types:
            stmt = stmt.where(Job.type.in_(list(job_types)))
        if now is not None:
            stmt = stmt.where(Job.lease_expires_at.is_not(None), Job.lease_expires_at > now)
        async with self._session_factory() as session:
            return int((await session.execute(stmt)).scalar_one() or 0)

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

    async def _persist_coupang_recovery_state(
        self,
        session: AsyncSession,
        *,
        job: Job,
        status: str,
        now: datetime,
    ) -> None:
        recovery_update = coupang_recovery_state_values(job=job, status=status, now=now)
        if recovery_update is None:
            return
        platform_account_id, values = recovery_update
        await session.execute(
            update(PlatformAccount)
            .where(PlatformAccount.id == _as_uuid(str(platform_account_id)))
            .values(**values)
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
        job_uuids = [job_id for job_id in (_safe_uuid(value) for value in unique_job_ids) if job_id]
        agent_uuid = _safe_uuid(agent_id)
        if not job_uuids or agent_uuid is None:
            return set()
        async with self._session_factory() as session:
            stmt = (
                update(Job)
                .where(
                    Job.id.in_(job_uuids),
                    Job.agent_id == agent_uuid,
                    Job.status.in_((JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)),
                    Job.lease_expires_at.is_not(None),
                    Job.lease_expires_at > now,
                )
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
                .returning(Job.id)
            )
            result = await session.execute(stmt)
            await session.commit()
            return {str(job_id) for job_id in result.scalars().all()}

    async def recover_stale(self, *, now: datetime, batch_size: int | None = None) -> int:
        async with self._session_factory() as session:
            # (1) 만료 lease 의 CLAIMED/RUNNING job. (2) payload TTL 이 지난 PENDING scheduled
            # crawl/auth job — 서버 downtime 뒤 누적 backlog 가 한 번에 claim 되지 않게 미리
            # terminal 종료(Task 6). PENDING 은 lease 가 없어 payload expires_at 로만 stale 판정한다.
            #
            # **batch 예산 오염 방지(검토 High):** LIMIT 을 SQL 에서 거는데, PENDING 후보를
            # **실제로 만료된 행**(``expires_at <= now``)으로만 좁혀야 한다. expires_at 존재 여부만
            # 보면, 아직 만료 전인 정상 scheduled crawl/auth PENDING(둘 다 expires_at 를 실음)이
            # batch 를 채워 뒤에 있는 진짜 만료 AUTH_COUPANG_2FA 가 영원히 cleanup 안 될 수 있다.
            # ``expires_at`` 는 모두 ``_iso_utc``(``YYYY-MM-DDTHH:MM:SSZ``, microsecond 0, UTC)로
            # 생성돼 **사전식 비교가 시각 비교와 일치**한다 — now 도 같은 정규형으로 만들어 텍스트
            # 비교한다(타임스탬프 cast 의 malformed-value 에러 위험 회피). 최종 stale 판정은
            # Python ``stale_recovery_reason`` 이 그대로 한다(``_parse_iso_utc`` 로 Z/+00:00 모두 허용).
            now_iso = _iso_utc_z(now)
            expires_text = Job.payload_json["expires_at"].as_string()
            pending_stale_candidate = (
                (Job.status == JOB_STATUS_PENDING)
                & (Job.payload_json["expires_at"].is_not(None))
                & (expires_text <= now_iso)
            )
            stmt = (
                select(Job)
                .where(
                    (
                        Job.status.in_((JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING))
                        & Job.lease_expires_at.is_not(None)
                        & (Job.lease_expires_at <= now)
                    )
                    | pending_stale_candidate
                )
                .order_by(Job.lease_expires_at.asc().nullslast(), Job.id.asc())
                .with_for_update(skip_locked=True)
            )
            if batch_size is not None and batch_size > 0:
                stmt = stmt.limit(batch_size)
            rows = (await session.execute(stmt)).scalars().all()
            recovered = 0
            for job in rows:
                stale_reason = stale_recovery_reason(
                    job_type=job.type,
                    payload_json=job.payload_json,
                    now=now,
                    job_status=job.status,
                )
                # PENDING 은 stale(payload TTL 만료)일 때만 종료 대상이다 — 그 외 PENDING 은 건드리지
                # 않는다(정상 대기 job 보존).
                if job.status == JOB_STATUS_PENDING and stale_reason is None:
                    continue
                next_attempt = int(job.attempts or 0) + 1
                # 브라우저를 여는 interactive job / scheduled crawl 은 payload TTL 이 지났으면
                # 재시도하지 않고 terminal FAILED + safe reason 으로 닫는다(무제한 재실행 차단).
                # PENDING 은 lease 만료 retry 대상이 아니다 — stale 이면 terminal 종료만 한다.
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
                    # stale Coupang 자동복구 job(AUTH_COUPANG_2FA/legacy recovery crawl)을 terminal
                    # FAILED 로 닫을 때 계정 cooldown 도 같이 건다 — 안 그러면 다음 scheduler tick 이
                    # 같은 AUTH_REQUIRED 계정에 곧바로 새 인증 job 을 만들어 복구가 무한 재시도된다
                    # (검토 High). recovery job 이 아니면 no-op.
                    await self._persist_coupang_recovery_state(
                        session, job=job, status=JOB_STATUS_FAILED, now=now
                    )
                job.attempts = next_attempt
                job.agent_id = None
                job.lease_expires_at = None
                job.claimed_at = None
                job.error_code = STALE_LEASE_ERROR_CODE
                job.last_failed_at = now
                recovered += 1
            await session.commit()
            return recovered

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
        job_uuid = _safe_uuid(job_id)
        agent_uuid = _safe_uuid(agent_id)
        if job_uuid is None or agent_uuid is None:
            return False

        async with self._session_factory() as session:
            stmt = (
                select(Job)
                .where(
                    Job.id == job_uuid,
                    Job.agent_id == agent_uuid,
                    Job.status.in_((JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)),
                    Job.lease_expires_at.is_not(None),
                    Job.lease_expires_at > now,
                )
                .with_for_update()
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return False
            diff = _agent_event_diff(
                job_id=job_id,
                agent_id=agent_id,
                severity=severity,
                message_redacted=message_redacted,
                artifact_refs=artifact_refs,
            )
            session.add(
                AuditLog(
                    actor_id=agent_uuid,
                    action=_bounded_event_text(event_type),
                    target_type="JOB",
                    target_id=job_uuid,
                    diff_redacted=diff,
                    created_at=now,
                    source="AGENT",
                    reason=diff["message_redacted"],
                    result=AuditResult.SUCCESS.value,
                )
            )
            await session.commit()
            return True
