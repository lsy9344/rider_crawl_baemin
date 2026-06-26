"""Story 5.4 / AC2·AC4 QA gap-fill — PostgresSchedulerRepository 순수 헬퍼(항상 실행, DB-less).

PG-gated 통합 테스트(``tests/negative/test_scheduler_idempotency.py``)는 Postgres 부재 시 전부
skip 되므로, 그 안에 묻힌 **순수 매핑 헬퍼**(DB 문자열 → 도메인 enum, 미매핑 fail-closed)와
스코프 상수(scheduler 가 보는 활성 job type/status)는 CI 에서 한 번도 실행되지 않는다. 이 파일은
그 결정적 의미를 always-run 으로 잠근다(DB 연결 불필요 — 함수는 순수·결정적).

- ``_to_subscription_status``/``_to_lifecycle_status``: 미매핑/``None`` → ``None`` → 게이트
  합성에서 fail-closed 차단으로 이어진다(AC2 — 미매핑 고객은 신규 CrawlJob 예약 안 됨).
- ``_CRAWL_JOB_TYPES``/``_ACTIVE_JOB_STATUSES``: scheduler 가 멱등성/breaker 집계 시 보는 스코프가
  정본 CrawlJob 2종 · 활성 상태 3종(PENDING/CLAIMED/RUNNING)으로 고정됨(AC4).

fake 값만 — 실제 토큰/전화/이메일/chat_id 형태 없음.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import asyncio
import inspect
from dataclasses import replace

import pytest

from rider_server.domain import CustomerLifecycleState, FailureCategory, SubscriptionStatus
from rider_server.queue import InMemoryQueueBackend
from rider_server.queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
)
from rider_server.scheduler import policy
from rider_server.scheduler.postgres_repository import (
    _ACTIVE_JOB_STATUSES,
    _CRAWL_JOB_TYPES,
    _PLATFORM_BREAKER_IGNORED_FAILURE_CODES,
    _capacity_from_agent_rows,
    _is_platform_breaker_failure_code,
    _safe_uuid,
    PostgresSchedulerRepository,
    _to_lifecycle_status,
    _to_subscription_status,
)
from rider_server.scheduler.service import (
    REASON_ACTIVE_JOB_EXISTS,
    DueTarget,
    SchedulerRepository,
    SchedulerService,
    TenantGate,
)


# ── AC2: DB 문자열 → 구독 상태(미매핑 fail-closed) ────────────────────────────

def test_to_subscription_status_maps_valid_value() -> None:
    assert _to_subscription_status("PAYMENT_ACTIVE") is SubscriptionStatus.PAYMENT_ACTIVE
    assert _to_subscription_status("SUSPENDED") is SubscriptionStatus.SUSPENDED


@pytest.mark.parametrize("value", [None, "", "BOGUS", "active", "PAYMENT_FAILED"])
def test_to_subscription_status_none_or_invalid_is_fail_closed(value) -> None:
    # 미매핑/None → None → decide_schedule 가 NO_SUBSCRIPTION 으로 차단(fail-closed).
    assert _to_subscription_status(value) is None


# ── AC2: DB 문자열 → lifecycle 상태(미매핑 fail-closed) ───────────────────────

def test_to_lifecycle_status_maps_valid_value() -> None:
    assert _to_lifecycle_status("ACTIVE") is CustomerLifecycleState.ACTIVE
    assert _to_lifecycle_status("SETUP_PENDING") is CustomerLifecycleState.SETUP_PENDING


@pytest.mark.parametrize("value", [None, "", "BOGUS", "active"])
def test_to_lifecycle_status_none_or_invalid_is_fail_closed(value) -> None:
    assert _to_lifecycle_status(value) is None


# ── AC2: 미매핑 DB 문자열이 게이트 합성에서 차단으로 이어진다(end-to-end) ──────

def test_invalid_db_strings_compose_to_blocked_decision() -> None:
    sub = _to_subscription_status("???")
    life = _to_lifecycle_status("???")
    decision = policy.decide_schedule(sub, life)
    assert decision.allow_new_crawl_job is False
    assert decision.reason == "NO_SUBSCRIPTION"


def test_valid_active_db_strings_compose_to_allowed_decision() -> None:
    sub = _to_subscription_status("PAYMENT_ACTIVE")
    life = _to_lifecycle_status("ACTIVE")
    assert policy.decide_schedule(sub, life).allow_new_crawl_job is True


# ── AC4: scheduler 스코프 상수 고정(활성 job type 2종 · 활성 status 3종) ───────

def test_repository_crawl_job_type_scope_is_canonical_two() -> None:
    # scheduler 가 만들고 집계하는 CrawlJob 은 정본 6종 중 BAEMIN/COUPANG 2종뿐.
    assert set(_CRAWL_JOB_TYPES) == {JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG}


def test_repository_active_status_scope_is_pending_claimed_running() -> None:
    # AC4 멱등성의 "활성 CrawlJob" 정의 = PENDING/CLAIMED/RUNNING(터미널/실패 제외).
    assert set(_ACTIVE_JOB_STATUSES) == {
        JOB_STATUS_PENDING,
        JOB_STATUS_CLAIMED,
        JOB_STATUS_RUNNING,
    }


def test_platform_breaker_ignores_target_specific_human_action_failures() -> None:
    """대상별 조치 실패는 쿠팡 전체 장애율을 열지 않는다."""

    assert set(_PLATFORM_BREAKER_IGNORED_FAILURE_CODES) == {
        FailureCategory.AUTH_REQUIRED.value,
        FailureCategory.TARGET_VALIDATION_FAILURE.value,
    }
    assert _is_platform_breaker_failure_code(FailureCategory.CRAWL_FAILURE.value) is True
    assert _is_platform_breaker_failure_code(FailureCategory.RENDER_FAILURE.value) is True
    assert _is_platform_breaker_failure_code(None) is True
    assert _is_platform_breaker_failure_code(FailureCategory.AUTH_REQUIRED.value) is False
    assert (
        _is_platform_breaker_failure_code(
            FailureCategory.TARGET_VALIDATION_FAILURE.value
        )
        is False
    )


def test_platform_failure_window_applies_platform_breaker_failure_filter() -> None:
    source = inspect.getsource(PostgresSchedulerRepository.platform_failure_window)
    assert "_platform_breaker_failure_filter()" in source


def test_postgres_repository_has_release_due_target_for_enqueue_failures() -> None:
    assert hasattr(PostgresSchedulerRepository, "release_due_target")


def test_safe_uuid_parses_valid_and_fails_closed_on_garbage() -> None:
    import uuid

    valid = uuid.uuid4()
    assert _safe_uuid(str(valid)) == valid
    assert _safe_uuid(valid) is valid  # 이미 UUID 면 그대로
    assert _safe_uuid("") is None
    assert _safe_uuid(None) is None
    assert _safe_uuid("not-a-uuid") is None


def test_advisory_lock_key_is_deterministic_and_int4_safe() -> None:
    """advisory lock 키는 같은 UUID 면 항상 같고 signed int4 양수 범위 안(검토 High)."""
    import uuid as _uuid
    from rider_server.db.base import advisory_lock_key_for_uuid

    u = _uuid.uuid4()
    k = advisory_lock_key_for_uuid(u)
    assert advisory_lock_key_for_uuid(u) == k  # 결정적
    assert 0 <= k <= 0x7FFFFFFF  # int4 양수 범위(부호 문제 0)
    # 서로 다른 UUID 는 (거의) 다른 키 — 결정성만 보장하면 충돌해도 정확성은 유지된다.
    other = advisory_lock_key_for_uuid(_uuid.uuid4())
    assert isinstance(other, int)


def test_auth_enqueue_lock_namespace_is_stable_int4() -> None:
    """scheduler/admin 두 경로가 공유하는 advisory lock 네임스페이스가 안정·int4 범위."""
    from rider_server.db.base import AUTH_ENQUEUE_LOCK_NAMESPACE

    assert isinstance(AUTH_ENQUEUE_LOCK_NAMESPACE, int)
    assert -0x80000000 <= AUTH_ENQUEUE_LOCK_NAMESPACE <= 0x7FFFFFFF


def test_account_target_ids_subquery_is_account_scoped_not_target_scoped() -> None:
    """auth job 중복 검사가 platform_account_id 단위로 묶이는지(검토 High) — 같은 계정 공유 target 들."""
    import uuid

    account_id = str(uuid.uuid4())
    subq = PostgresSchedulerRepository._account_target_ids_subquery(account_id)
    assert subq is not None
    sql = str(subq).lower()
    # monitoring_targets 를 platform_account_id 로 거른다(target_id 단일이 아님).
    assert "monitoring_targets" in sql
    assert "platform_account_id" in sql

    # account id 가 없거나 UUID 가 아니면 None → 호출자가 보수적으로 target 단위로만 검사.
    assert PostgresSchedulerRepository._account_target_ids_subquery(None) is None
    assert PostgresSchedulerRepository._account_target_ids_subquery("garbage") is None


def test_capacity_snapshot_counts_only_online_agent_capacity() -> None:
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

    capacity = _capacity_from_agent_rows(
        [
            SimpleNamespace(
                capacity_json={"max_in_flight": 2, "capabilities": [JOB_TYPE_CRAWL_BAEMIN]},
                last_heartbeat_at=now - timedelta(seconds=30),
            ),
            SimpleNamespace(
                capacity_json={"max_in_flight": 9, "capabilities": [JOB_TYPE_CRAWL_COUPANG]},
                last_heartbeat_at=now - timedelta(minutes=5),
            ),
            SimpleNamespace(
                capacity_json={"max_in_flight": 4, "capabilities": ["KAKAO_SEND"]},
                last_heartbeat_at=None,
            ),
        ],
        aggregate_in_flight=1,
        now=now,
    )

    assert capacity.aggregate_capacity == 2
    assert capacity.aggregate_in_flight == 1
    assert capacity.capabilities == frozenset({JOB_TYPE_CRAWL_BAEMIN})
    assert capacity.capacity_by_job_type == {JOB_TYPE_CRAWL_BAEMIN: 2}


def test_capacity_snapshot_keeps_exact_two_minute_heartbeat_online() -> None:
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

    capacity = _capacity_from_agent_rows(
        [
            SimpleNamespace(
                capacity_json={"max_in_flight": 1, "capabilities": [JOB_TYPE_CRAWL_COUPANG]},
                last_heartbeat_at=now - timedelta(minutes=2),
            )
        ],
        aggregate_in_flight=0,
        in_flight_by_job_type={JOB_TYPE_CRAWL_COUPANG: 1},
        now=now,
    )

    assert capacity.aggregate_capacity == 1
    assert capacity.capabilities == frozenset({JOB_TYPE_CRAWL_COUPANG})
    assert capacity.in_flight_by_job_type == {JOB_TYPE_CRAWL_COUPANG: 1}


# ── Task 6: pending crawl coalescing(target/platform 당 활성 crawl 1건) ───────────


class _CoalesceRepo(SchedulerRepository):
    """최소 ``SchedulerRepository`` — 활성 crawl 이 있는 target 은 새 enqueue 를 막고 전진 안 함."""

    def __init__(self, target: DueTarget, *, active: set[str]) -> None:
        self._targets = {target.target_id: target}
        self._active = set(active)
        self.claim_calls = 0

    async def due_targets(self, *, now, limit):
        return [t for t in self._targets.values() if policy.is_due(t.next_run_at, now)][:limit]

    async def tenant_gate(self, tenant_id):
        return TenantGate(SubscriptionStatus.PAYMENT_ACTIVE, CustomerLifecycleState.ACTIVE)

    async def tenant_gates(self, tenant_ids):
        return {tid: TenantGate(SubscriptionStatus.PAYMENT_ACTIVE, CustomerLifecycleState.ACTIVE) for tid in tenant_ids}

    async def platform_failure_window(self, platform, *, since, now):
        return (0, 0)

    async def has_active_crawl_job(self, target_id):
        return target_id in self._active

    async def active_crawl_job_target_ids(self, target_ids):
        return {tid for tid in target_ids if tid in self._active}

    async def capacity_snapshot(self, *, now):
        return policy.CapacityPolicy(
            aggregate_capacity=10,
            aggregate_in_flight=0,
            capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG}),
        )

    async def claim_due_target(self, target_id, *, now, next_run_at):
        self.claim_calls += 1
        target = self._targets.get(target_id)
        if target is None or not policy.is_due(target.next_run_at, now):
            return False
        self._targets[target_id] = replace(target, next_run_at=next_run_at)
        return True

    async def release_due_target(self, target_id, *, claimed_next_run_at, restore_next_run_at):
        return False

    def next_run_at_of(self, target_id):
        return self._targets[target_id].next_run_at


def test_scheduler_does_not_create_second_pending_crawl_for_same_target_and_platform() -> None:
    """Backlog is coalesced to one useful crawl per target/platform."""

    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    target = DueTarget(
        target_id="t-c",
        tenant_id="tn-1",
        platform="COUPANG",
        interval_minutes=10,
        next_run_at=None,
        auth_state="ACTIVE",
    )
    repo = _CoalesceRepo(target, active={"t-c"})
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=now))

    # 활성 CRAWL_COUPANG 이 이미 있으면 두 번째를 만들지 않는다.
    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_ACTIVE_JOB_EXISTS
    # 같은 stale target 으로 매 tick 스핀하지 않는다(전진 안 함 → claim_due_target 호출 0).
    assert repo.claim_calls == 0
    assert repo.next_run_at_of("t-c") is None
