"""rider_server scheduler 패키지 — Story 5.4.

"누가·언제·무엇을 enqueue 하는가"를 결정하는 scheduler. 순수 정책(:mod:`policy` — jitter·due·
게이트 합성·job type 매핑·플랫폼 breaker·error_code backoff 재사용)과 async tick 오케스트레이션
(:mod:`service` — due 질의→게이트→breaker→capacity throttle→멱등 enqueue→next_run_at 전진),
PostgreSQL 포트 구현(:mod:`postgres_repository`)을 제공한다.

scheduler 는 **별도 process**(architecture-contract.md:54)라 ``create_app`` 라우트로 노출하지
않는다 — tick 함수/클래스만 제공하고 주기 loop·배포 배선은 후속 스토리 소유.
"""

from __future__ import annotations

from .policy import (
    ACTIVE_LIFECYCLE_STATES,
    BREAKER_CLOSED,
    BREAKER_OPEN,
    DEFAULT_BREAKER_MIN_SAMPLES,
    DEFAULT_BREAKER_THRESHOLD,
    CapacityPolicy,
    RetrySchedule,
    SchedulerDecision,
    breaker_state,
    can_admit,
    compute_jitter,
    crawl_job_type_for,
    decide_schedule,
    evaluate_breaker,
    is_due,
    next_run_at,
    retry_run_after,
)
from .postgres_repository import PostgresSchedulerRepository
from .service import (
    DEFAULT_BREAKER_WINDOW,
    REASON_ACTIVE_JOB_EXISTS,
    REASON_BREAKER_OPEN,
    REASON_ENQUEUED,
    REASON_RACE_LOST,
    REASON_THROTTLED_CAPACITY,
    REASON_UNKNOWN_PLATFORM,
    DueTarget,
    ScheduleOutcome,
    SchedulerRepository,
    SchedulerService,
    TenantGate,
    TickResult,
)

__all__ = [
    # policy (순수)
    "ACTIVE_LIFECYCLE_STATES",
    "BREAKER_OPEN",
    "BREAKER_CLOSED",
    "DEFAULT_BREAKER_THRESHOLD",
    "DEFAULT_BREAKER_MIN_SAMPLES",
    "CapacityPolicy",
    "RetrySchedule",
    "SchedulerDecision",
    "breaker_state",
    "can_admit",
    "compute_jitter",
    "crawl_job_type_for",
    "decide_schedule",
    "evaluate_breaker",
    "is_due",
    "next_run_at",
    "retry_run_after",
    # service (async tick)
    "DEFAULT_BREAKER_WINDOW",
    "REASON_ENQUEUED",
    "REASON_BREAKER_OPEN",
    "REASON_ACTIVE_JOB_EXISTS",
    "REASON_THROTTLED_CAPACITY",
    "REASON_UNKNOWN_PLATFORM",
    "REASON_RACE_LOST",
    "DueTarget",
    "ScheduleOutcome",
    "SchedulerRepository",
    "SchedulerService",
    "TenantGate",
    "TickResult",
    # postgres 포트 구현
    "PostgresSchedulerRepository",
]
