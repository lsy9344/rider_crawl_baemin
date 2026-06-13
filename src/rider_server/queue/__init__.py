"""rider_server queue 패키지 — Story 5.3.

job queue 의 backend 중립 추상화(``QueueBackend``)와 in-memory/PostgreSQL 구현, job type
어휘·상태머신을 제공한다. 단일 PostgreSQL 결정(Redis 미도입)이라 idempotency·exactly-one-claim
이 DB 트랜잭션 한 곳(``FOR UPDATE SKIP LOCKED``)에 응집하되, 인터페이스로 추후 Redis/SQS 교체
길을 연다(P4-05, ADD-4).
"""

from __future__ import annotations

from .backend import (
    COMPLETE_ACCEPTED,
    COMPLETE_LEASE_LOST,
    COMPLETE_NOT_FOUND,
    ClaimedJobRecord,
    CompleteOutcome,
    QueueBackend,
)
from .memory_queue import InMemoryQueueBackend
from .postgres_queue import PostgresQueueBackend
from .states import (
    ALLOWED_TRANSITIONS,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RETRY,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUSES,
    JOB_TYPES,
    InvalidJobTransition,
    UnknownAgentStatus,
    assert_transition,
    is_allowed_transition,
    map_agent_status,
)

__all__ = [
    # backend
    "QueueBackend",
    "ClaimedJobRecord",
    "CompleteOutcome",
    "COMPLETE_ACCEPTED",
    "COMPLETE_LEASE_LOST",
    "COMPLETE_NOT_FOUND",
    "InMemoryQueueBackend",
    "PostgresQueueBackend",
    # states
    "JOB_TYPES",
    "JOB_STATUSES",
    "JOB_STATUS_PENDING",
    "JOB_STATUS_CLAIMED",
    "JOB_STATUS_RUNNING",
    "JOB_STATUS_SUCCEEDED",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_RETRY",
    "ALLOWED_TRANSITIONS",
    "InvalidJobTransition",
    "UnknownAgentStatus",
    "assert_transition",
    "is_allowed_transition",
    "map_agent_status",
]
