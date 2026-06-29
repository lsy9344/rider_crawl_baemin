"""Story 5.4 / AC1·AC2·AC3·AC4 — tick 오케스트레이션(in-memory, 항상 실행).

5.3 ``InMemoryQueueBackend`` + fake 대상/구독/agent 데이터로 ``SchedulerService.run_tick`` 한
바퀴를 돌려 due 만 enqueue·중지/비활성 제외·breaker open 플랫폼 제외·capacity throttle·**멱등성**
(같은 due 에 tick 2회 → CrawlJob 정확히 1건)을 결정적으로 잠근다. 추가로 100 fake 대상 storm
미발생(jitter 분산 + capacity throttle)을 1차 잠근다(부하/타이밍 차원 확장은 Story 5.10).

``pytest-asyncio`` 미도입 → ``asyncio.run`` 으로 async tick 을 구동(5.1 선례). 시각·데이터는
주입(결정적). fake fixture 만 — 실제 토큰/전화/이메일/chat_id 형태 없음.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from rider_server.queue import InMemoryQueueBackend
from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG
from rider_server.scheduler import policy
from rider_server.scheduler.service import (
    REASON_ACTIVE_JOB_EXISTS,
    REASON_AUTH_JOB_ALREADY_ACTIVE,
    REASON_AUTH_REQUIRED_NO_AUTO_RECOVERY,
    REASON_AUTH_STATE_BLOCKED_OR_CAPTCHA,
    REASON_AUTH_STATE_UNKNOWN,
    REASON_AUTH_STATE_USER_ACTION_PENDING,
    REASON_BREAKER_OPEN,
    REASON_COUPANG_AUTO_RECOVERY_COOLDOWN,
    REASON_ENQUEUED,
    REASON_ENQUEUED_AUTH_COUPANG_2FA,
    REASON_ENQUEUED_CRAWL,
    REASON_RACE_LOST,
    REASON_THROTTLED_CAPACITY,
    REASON_UNKNOWN_PLATFORM,
    DueTarget,
    SchedulerRepository,
    SchedulerService,
    TenantGate,
)
from rider_server.domain import CustomerLifecycleState, SubscriptionStatus

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_INTERVAL_MIN = 10

_ACTIVE_GATE = TenantGate(
    subscription_status=SubscriptionStatus.PAYMENT_ACTIVE,
    lifecycle_status=CustomerLifecycleState.ACTIVE,
)


class FakeSchedulerRepo(SchedulerRepository):
    """DB-less ``SchedulerRepository`` — conditional advance 의미를 결정적으로 모사.

    ``claim_due_target`` 은 ``next_run_at <= now`` 일 때만 전진시켜 True 를 돌려준다 — 같은 due 에
    두 번째 호출(또는 두 번째 tick)은 False(중복 due 차단, AC4). due_targets 는 advance 된
    next_run_at 을 반영해 두 번째 tick 에서 제외된다.
    """

    def __init__(
        self,
        *,
        targets,
        gates,
        failure_windows=None,
        active_jobs=(),
        active_auth_jobs=(),
        capacity,
    ) -> None:
        self._targets = {t.target_id: t for t in targets}
        self._gates = dict(gates)
        self._failure_windows = dict(failure_windows or {})
        self._active_jobs = set(active_jobs)
        self._active_auth_jobs = set(active_auth_jobs)
        self._capacity = capacity
        self.claim_wins: list[str] = []
        self.release_calls: list[tuple[str, datetime, datetime | None]] = []
        self.tenant_gate_calls = 0
        self.bulk_tenant_gate_calls = 0
        self.has_active_crawl_job_calls = 0
        self.bulk_active_job_calls = 0
        self.bulk_active_auth_job_calls = 0
        self.auth_enqueues: list[tuple[str, dict]] = []

    async def due_targets(self, *, now, limit):
        return [
            t for t in self._targets.values() if policy.is_due(t.next_run_at, now)
        ][:limit]

    async def tenant_gate(self, tenant_id):
        self.tenant_gate_calls += 1
        return self._gates.get(tenant_id, TenantGate(None, None))

    async def tenant_gates(self, tenant_ids):
        self.bulk_tenant_gate_calls += 1
        return {
            tenant_id: self._gates.get(tenant_id, TenantGate(None, None))
            for tenant_id in tenant_ids
        }

    async def platform_failure_window(self, platform, *, since, now):
        return self._failure_windows.get(platform, (0, 0))

    async def has_active_crawl_job(self, target_id):
        self.has_active_crawl_job_calls += 1
        return target_id in self._active_jobs

    async def active_crawl_job_target_ids(self, target_ids):
        self.bulk_active_job_calls += 1
        return {target_id for target_id in target_ids if target_id in self._active_jobs}

    async def active_auth_job_target_ids(self, target_ids):
        self.bulk_active_auth_job_calls += 1
        return {target_id for target_id in target_ids if target_id in self._active_auth_jobs}

    async def enqueue_auth_coupang_2fa_job(self, queue_backend, target, *, payload_json, now):
        # crawl next_run_at 은 전진시키지 않는다(인증 성공 후 다음 tick 이 crawl 재개).
        self.auth_enqueues.append((target.target_id, dict(payload_json)))
        return await queue_backend.enqueue(
            job_type="AUTH_COUPANG_2FA",
            target_id=target.target_id,
            payload_json=payload_json,
            assigned_agent_id=target.assigned_agent_id or None,
            run_after=now,
            now=now,
        )

    async def capacity_snapshot(self, *, now):
        return self._capacity

    async def claim_due_target(self, target_id, *, now, next_run_at):
        target = self._targets.get(target_id)
        if target is None or not policy.is_due(target.next_run_at, now):
            return False
        self._targets[target_id] = replace(target, next_run_at=next_run_at)
        self.claim_wins.append(target_id)
        return True

    async def release_due_target(self, target_id, *, claimed_next_run_at, restore_next_run_at):
        target = self._targets.get(target_id)
        if target is None or target.next_run_at != claimed_next_run_at:
            return False
        self._targets[target_id] = replace(target, next_run_at=restore_next_run_at)
        self.release_calls.append((target_id, claimed_next_run_at, restore_next_run_at))
        return True

    # 테스트 가시성 헬퍼.
    def next_run_at_of(self, target_id):
        return self._targets[target_id].next_run_at


def _capacity(n=100, in_flight=0, caps=(JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG)):
    return policy.CapacityPolicy(
        aggregate_capacity=n, aggregate_in_flight=in_flight, capabilities=frozenset(caps)
    )


def _target(
    tid,
    *,
    platform="BAEMIN",
    tenant=None,
    interval=_INTERVAL_MIN,
    next_run=None,
    username="",
    password="",
    verification_email_address="",
    verification_email_app_password="",
    verification_email_subject_keyword="인증번호",
    verification_email_sender_keyword="coupang",
    assigned_agent_id="",
    auth_state="ACTIVE",
    auto_recovery_attempted_at=None,
    auto_recovery_failed_at=None,
    auto_recovery_cooldown_until=None,
    active_auth_job_count=0,
    external_id="",
):
    return DueTarget(
        target_id=tid,
        tenant_id=tenant or f"tenant-{tid}",
        platform=platform,
        interval_minutes=interval,
        next_run_at=next_run,
        platform_account_id=f"acct-{tid}",
        primary_url=f"https://example.invalid/{tid}",
        expected_display_name=f"센터-{tid}",
        external_id=external_id,
        username=username,
        password=password,
        verification_email_address=verification_email_address,
        verification_email_app_password=verification_email_app_password,
        verification_email_subject_keyword=verification_email_subject_keyword,
        verification_email_sender_keyword=verification_email_sender_keyword,
        assigned_agent_id=assigned_agent_id,
        auth_state=auth_state,
        auto_recovery_attempted_at=auto_recovery_attempted_at,
        auto_recovery_failed_at=auto_recovery_failed_at,
        auto_recovery_cooldown_until=auto_recovery_cooldown_until,
        active_auth_job_count=active_auth_job_count,
    )


# ── AC1: due 대상만 enqueue ───────────────────────────────────────────────────

def test_only_due_targets_are_enqueued() -> None:
    due_a = _target("t-a")
    due_b = _target("t-b")
    not_due = _target("t-c", next_run=_NOW + timedelta(minutes=5))
    repo = FakeSchedulerRepo(
        targets=[due_a, due_b, not_due],
        gates={t.tenant_id: _ACTIVE_GATE for t in (due_a, due_b, not_due)},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 2
    assert set(result.enqueued_target_ids) == {"t-a", "t-b"}


def test_scheduler_does_not_catch_up_after_reactivation_reset() -> None:
    # reactivation no-catchup: reset 된 next_run_at(now+interval+jitter) 이후로는 같은 now tick 에서
    # enqueue 되지 않고(catch-up 금지), reset 시각 이후 tick 에서만 정상 enqueue 된다.
    reset_at = policy.reactivation_next_run_at("t-react", _INTERVAL_MIN, _NOW)
    assert reset_at > _NOW  # reset 은 미래여야 한다(즉시 due 금지)
    target = _target("t-react", next_run=reset_at)
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()

    # ① reactivation 직후 같은 now → 즉시 enqueue 0(밀린 주기 replay 안 함).
    result_now = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result_now.enqueued_count == 0

    # ② reset 시각 이후 tick → 정상 enqueue 1건.
    later = reset_at + timedelta(seconds=1)
    result_later = asyncio.run(SchedulerService().run_tick(repo, backend, now=later))
    assert result_later.enqueued_count == 1
    assert set(result_later.enqueued_target_ids) == {"t-react"}


def test_reactivation_reset_is_strict_future_even_for_zero_interval() -> None:
    # Finding 2: interval_minutes<=0 도 reactivation reset 은 strict-future 라 즉시 due 가 아니다.
    for interval in (0, -5):
        reset_at = policy.reactivation_next_run_at("t-zero", interval, _NOW)
        assert reset_at > _NOW
        assert policy.is_due(reset_at, _NOW) is False
    # tick 으로도 즉시 enqueue 되지 않음을 확인(interval 0 reset).
    reset_at = policy.reactivation_next_run_at("t-zero", 0, _NOW)
    target = _target("t-zero", interval=0, next_run=reset_at)
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 0


def test_enqueued_jobs_use_platform_specific_canonical_job_type() -> None:
    baemin = _target("t-b", platform="BAEMIN")
    coupang = _target("t-c", platform="COUPANG")
    repo = FakeSchedulerRepo(
        targets=[baemin, coupang],
        gates={baemin.tenant_id: _ACTIVE_GATE, coupang.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    by_target = {o.target_id: o for o in result.outcomes}
    assert by_target["t-b"].job_type == JOB_TYPE_CRAWL_BAEMIN
    assert by_target["t-c"].job_type == JOB_TYPE_CRAWL_COUPANG
    # 실제 backend 에 PENDING job 으로 들어갔는지 확인.
    for o in result.outcomes:
        assert backend.job_status(o.job_id) == "PENDING"


def test_scheduler_preserves_target_agent_affinity_on_enqueued_job() -> None:
    target = _target("t-affinity", assigned_agent_id="agent-1")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    assert job.assigned_agent_id == "agent-1"


def test_scheduler_enqueues_crawl_payload_needed_by_agent_worker() -> None:
    target = _target("t-payload", platform="BAEMIN", external_id="DP100")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    assert job.payload_json == {
        "target_id": "t-payload",
        "tenant_id": "tenant-t-payload",
        "platform": "baemin",
        "platform_account_id": "acct-t-payload",
        "primary_url": "https://example.invalid/t-payload",
        "expected_display_name": "센터-t-payload",
        "external_id": "DP100",
        "browser_profile_ref": "profile:t-payload",
        "timeout_seconds": 60,
        "parser_version": "baemin-v1",
        "job_type": JOB_TYPE_CRAWL_BAEMIN,
        "job_origin": "scheduler",
        "scheduled_at": "2026-06-14T12:00:00Z",
        "expires_at": "2026-06-14T12:10:00Z",
    }


def test_scheduler_enqueues_coupang_secret_refs_without_plaintext_values() -> None:
    target = _target(
        "t-coupang",
        platform="COUPANG",
        username="vault://coupang/login-id",
        password="vault://coupang/login-password",
        verification_email_address="vault://mail/address",
        verification_email_app_password="vault://mail/app-password",
        verification_email_subject_keyword="보안코드",
        verification_email_sender_keyword="wing",
    )
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    assert job.payload_json["job_type"] == JOB_TYPE_CRAWL_COUPANG
    assert job.payload_json["coupang_login_id_ref"] == "vault://coupang/login-id"
    assert job.payload_json["coupang_login_password_ref"] == "vault://coupang/login-password"
    assert job.payload_json["verification_email_address_ref"] == "vault://mail/address"
    assert job.payload_json["verification_email_app_password_ref"] == "vault://mail/app-password"
    assert job.payload_json["verification_email_subject_keyword"] == "보안코드"
    assert job.payload_json["verification_email_sender_keyword"] == "wing"
    assert job.payload_json["coupang_auto_email_2fa_enabled"] is True
    assert job.payload_json["timeout_seconds"] == 180
    assert "username" not in job.payload_json
    assert "password" not in job.payload_json
    assert "coupang_login_id" not in job.payload_json
    assert "coupang_login_password" not in job.payload_json
    assert "verification_email_address" not in job.payload_json
    assert "verification_email_app_password" not in job.payload_json


def test_scheduler_crawl_payload_contains_scheduled_at_expires_at_and_origin() -> None:
    """Scheduled crawl payloads are bounded and identify their source."""

    coupang = _target(
        "t-c",
        platform="COUPANG",
        username="vault://coupang/login-id",
        password="vault://coupang/login-password",
        verification_email_address="vault://mail/address",
        verification_email_app_password="vault://mail/app-password",
    )
    repo = FakeSchedulerRepo(
        targets=[coupang],
        gates={coupang.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    payload = job.payload_json
    assert payload["job_origin"] == "scheduler"
    # scheduled_at == tick now.
    assert payload["scheduled_at"] == "2026-06-14T12:00:00Z"
    assert payload["timeout_seconds"] == 180
    scheduled_at = datetime.fromisoformat(payload["scheduled_at"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    # 긴 interval 은 inline email 2FA timeout 보다 길면 그대로 존중한다.
    assert expires_at == scheduled_at + timedelta(minutes=_INTERVAL_MIN)
    # coupang 자동 2FA 플래그는 유지하되 인증번호 값은 저장하지 않는다.
    assert payload["coupang_auto_email_2fa_enabled"] is True
    assert "verification_code" not in payload
    assert not any("code" in str(k).casefold() and "keyword" not in str(k).casefold() for k in payload)


def test_scheduler_coupang_incomplete_2fa_config_uses_default_crawl_timeout() -> None:
    target = _target(
        "t-c-incomplete",
        platform="COUPANG",
        username="vault://coupang/login-id",
        password="vault://coupang/login-password",
        verification_email_address="vault://mail/address",
    )
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    payload = job.payload_json
    assert payload["job_type"] == JOB_TYPE_CRAWL_COUPANG
    assert payload["timeout_seconds"] == 60
    assert payload["coupang_auto_email_2fa_enabled"] is False


def test_scheduler_coupang_inline_2fa_expiry_uses_timeout_when_interval_is_short() -> None:
    target = _target(
        "t-c-short",
        platform="COUPANG",
        interval=1,
        username="vault://coupang/login-id",
        password="vault://coupang/login-password",
        verification_email_address="vault://mail/address",
        verification_email_app_password="vault://mail/app-password",
    )
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    payload = job.payload_json
    assert payload["timeout_seconds"] == 180
    assert payload["expires_at"] == "2026-06-14T12:03:00Z"


# ── AC2: 중지/비활성 고객 제외 ────────────────────────────────────────────────

def test_suspended_and_inactive_lifecycle_targets_excluded() -> None:
    active = _target("t-ok")
    suspended = _target("t-susp")
    inactive_life = _target("t-life")
    repo = FakeSchedulerRepo(
        targets=[active, suspended, inactive_life],
        gates={
            active.tenant_id: _ACTIVE_GATE,
            suspended.tenant_id: TenantGate(
                SubscriptionStatus.SUSPENDED, CustomerLifecycleState.ACTIVE
            ),
            inactive_life.tenant_id: TenantGate(
                SubscriptionStatus.PAYMENT_ACTIVE, CustomerLifecycleState.SETUP_PENDING
            ),
        },
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 1
    assert result.enqueued_target_ids == ("t-ok",)
    reasons = {o.target_id: o.reason for o in result.outcomes}
    assert reasons["t-susp"] == "SUSPENDED"
    assert reasons["t-life"] == "LIFECYCLE_INACTIVE"


# ── AC3: breaker open 플랫폼 제외 ─────────────────────────────────────────────

def test_breaker_open_platform_targets_skipped() -> None:
    baemin = _target("t-b", platform="BAEMIN")
    coupang = _target("t-c", platform="COUPANG")
    repo = FakeSchedulerRepo(
        targets=[baemin, coupang],
        gates={baemin.tenant_id: _ACTIVE_GATE, coupang.tenant_id: _ACTIVE_GATE},
        # BAEMIN 최근 윈도 10 표본 중 5 실패(50% > 30%) → breaker open. COUPANG 무실패.
        failure_windows={"BAEMIN": (10, 5), "COUPANG": (10, 0)},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_target_ids == ("t-c",)
    reasons = {o.target_id: o.reason for o in result.outcomes}
    assert reasons["t-b"] == REASON_BREAKER_OPEN


def test_breaker_small_sample_does_not_open() -> None:
    # 1/1=100% 지만 표본 부족 → breaker open 안 됨(오탐 방지) → enqueue 됨.
    baemin = _target("t-b", platform="BAEMIN")
    repo = FakeSchedulerRepo(
        targets=[baemin],
        gates={baemin.tenant_id: _ACTIVE_GATE},
        failure_windows={"BAEMIN": (1, 1)},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 1


# ── AC1: capacity throttle ────────────────────────────────────────────────────

def test_capacity_throttle_limits_enqueue_within_tick() -> None:
    targets = [_target(f"t-{i}") for i in range(5)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=2),  # aggregate capacity 2 → 5 due 중 2만 enqueue.
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 2
    throttled = [o for o in result.outcomes if o.reason == REASON_THROTTLED_CAPACITY]
    assert len(throttled) == 3


def test_no_capable_agent_throttles_all() -> None:
    targets = [_target(f"t-{i}") for i in range(3)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=10, caps=()),  # capability 없음 → 전부 throttle.
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 0


# ── AC4: 멱등성 — 반복/동시 tick 이 중복 due 작업을 만들지 않음 ───────────────

def test_active_crawl_job_blocks_reenqueue() -> None:
    target = _target("t-a")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        active_jobs=["t-a"],  # 이미 활성 CrawlJob 존재.
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_ACTIVE_JOB_EXISTS
    # 활성 job 이 있으면 next_run_at 도 전진하지 않는다(재진입 차단, job 종료 후 재시도).
    assert repo.next_run_at_of("t-a") is None


def test_repeated_tick_same_due_window_creates_exactly_one_job() -> None:
    target = _target("t-a")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    svc = SchedulerService()

    r1 = asyncio.run(svc.run_tick(repo, backend, now=_NOW))
    r2 = asyncio.run(svc.run_tick(repo, backend, now=_NOW))

    assert r1.enqueued_count == 1
    assert r2.enqueued_count == 0  # 두 번째 tick 은 next_run_at 전진으로 due 아님.
    # next_run_at 이 now + interval + jitter 로 전진했다.
    expected = policy.next_run_at(
        _NOW, _INTERVAL_MIN * 60, policy.compute_jitter("t-a", _INTERVAL_MIN * 60)
    )
    assert repo.next_run_at_of("t-a") == expected


def test_concurrent_ticks_create_exactly_one_job() -> None:
    target = _target("t-a")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    svc = SchedulerService()

    async def _two_ticks():
        return await asyncio.gather(
            svc.run_tick(repo, backend, now=_NOW),
            svc.run_tick(repo, backend, now=_NOW),
        )

    r1, r2 = asyncio.run(_two_ticks())
    assert r1.enqueued_count + r2.enqueued_count == 1  # conditional advance 가 경합 차단.
    assert len(repo.claim_wins) == 1


# ── AC1 (c): 100 fake 대상 storm 미발생 결정적 검증 (5.10 1차 잠금) ───────────

def test_hundred_targets_no_storm_jitter_spread_and_capacity_bound() -> None:
    targets = [_target(f"target-{i}", interval=_INTERVAL_MIN) for i in range(100)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=100),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 100
    # 전진된 next_run_at 이 같은 초에 몰리지 않는다(jitter 분산 — storm 미발생).
    next_runs = [repo.next_run_at_of(f"target-{i}") for i in range(100)]
    distinct_seconds = {dt.replace(microsecond=0) for dt in next_runs}
    assert len(distinct_seconds) >= 85, f"next_run_at 분산 부족: {len(distinct_seconds)}"


def test_hundred_targets_capacity_bound_prevents_storm() -> None:
    targets = [_target(f"target-{i}") for i in range(100)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=10),  # 한 tick 에 최대 10건 — storm 없이 제한.
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 10


def test_scheduler_tick_respects_due_batch_limit() -> None:
    targets = [_target(f"target-{i}") for i in range(25)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=25),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(
        SchedulerService(due_batch_size=10).run_tick(repo, backend, now=_NOW)
    )

    assert len(result.outcomes) == 10
    assert result.enqueued_count == 10
    assert result.enqueued_target_ids == tuple(f"target-{i}" for i in range(10))


def test_scheduler_service_accepts_batch_size_alias() -> None:
    targets = [_target(f"target-{i}") for i in range(25)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=25),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(
        SchedulerService(batch_size=10).run_tick(repo, backend, now=_NOW)
    )

    assert len(result.outcomes) == 10
    assert result.enqueued_count == 10
    assert result.enqueued_target_ids == tuple(f"target-{i}" for i in range(10))


def test_scheduler_tick_uses_bulk_gate_and_active_job_lookups() -> None:
    targets = [_target(f"target-{i}") for i in range(3)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        active_jobs={"target-1"},
        capacity=_capacity(n=3),
    )
    backend = InMemoryQueueBackend()

    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert {o.target_id: o.reason for o in result.outcomes}["target-1"] == (
        REASON_ACTIVE_JOB_EXISTS
    )
    assert repo.tenant_gate_calls == 0
    assert repo.bulk_tenant_gate_calls == 1
    assert repo.has_active_crawl_job_calls == 0
    assert repo.bulk_active_job_calls == 1


# ══════════════════════════════════════════════════════════════════════════
# Story 5.10 / AC1 — 100 fake target scheduling smoke로 확장성 입증(NFR-26, P4 smoke)
# (재구현 금지: 위 5.4 smoke 두 건은 무변경 유지. 본 smoke 는 AC1 문구를 명시적으로 단정한다 —
#  단일 tick·exception/race/throttle 0·전부 PENDING·jitter 분산으로 storm 미발생.)
# ══════════════════════════════════════════════════════════════════════════

def test_5_10_hundred_targets_single_tick_all_enqueued_pending_and_jitter_spread() -> None:
    """AC1: 100 대상 전부 due·capacity=100 → 단일 tick 에서 (1) enqueued_count==100,
    (2) 모든 outcome reason==REASON_ENQUEUED(예외/RACE_LOST/THROTTLED_CAPACITY 0),
    (3) 각 job 이 queue 에 PENDING 으로 기록(상태 전환 정상), (4) next_run_at ≥85 distinct
    seconds 분산(같은 초 몰림=job storm 차단).
    """

    targets = [_target(f"target-{i}", interval=_INTERVAL_MIN) for i in range(100)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=100),  # 슬롯 100 — 부족으로 인한 throttle 0(순수 확장성 입증).
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    # (1) 100개 전부 enqueue(예외/누락 0).
    assert result.enqueued_count == 100
    assert len(result.outcomes) == 100

    # (2) 모든 결정이 ENQUEUED_CRAWL — race loss / capacity throttle / 미지 플랫폼 0.
    reasons = {o.reason for o in result.outcomes}
    assert reasons == {REASON_ENQUEUED_CRAWL}
    assert all(o.enqueued for o in result.outcomes)
    assert all(o.reason != REASON_RACE_LOST for o in result.outcomes)
    assert all(o.reason != REASON_THROTTLED_CAPACITY for o in result.outcomes)

    # (3) 각 job 이 queue 에 PENDING 으로 기록(상태 전환 정상 — 단순 enqueue 카운트만 보지 않음).
    for o in result.outcomes:
        snap = backend.job_snapshot(o.job_id)
        assert snap is not None
        assert snap.status == "PENDING"

    # (4) jitter 로 next_run_at 이 여러 초로 분산 → 같은 초 몰림(storm) 미발생(결정적 ≥85).
    next_runs = [repo.next_run_at_of(f"target-{i}") for i in range(100)]
    distinct_seconds = {dt.replace(microsecond=0) for dt in next_runs}
    assert len(distinct_seconds) >= 85, f"next_run_at 분산 부족(storm 위험): {len(distinct_seconds)}"


def test_5_10_hundred_targets_second_cycle_also_spread_no_storm() -> None:
    """AC1(2.3): 첫 tick 후 같은 due 윈도가 닫히고(전진), T+interval 재-tick 에서도 결정적 jitter
    가 분산을 유지해 두 번째 주기에도 storm 이 없다(결정적 jitter 특성). 첫 tick 직후 재-tick 은
    next_run_at 전진으로 due 아님(중복 0)도 함께 잠근다.
    """

    targets = [_target(f"target-{i}", interval=_INTERVAL_MIN) for i in range(100)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=100),
    )
    backend = InMemoryQueueBackend()
    svc = SchedulerService()

    first = asyncio.run(svc.run_tick(repo, backend, now=_NOW))
    assert first.enqueued_count == 100

    # 같은 시각 재-tick → 전부 전진했으므로 due 아님(중복 enqueue 0 — storm 재발 차단).
    immediate = asyncio.run(svc.run_tick(repo, backend, now=_NOW))
    assert immediate.enqueued_count == 0

    # 두 번째 주기: 전진된 next_run_at 의 최댓값(now + interval + jitter, jitter≤interval)을 지나
    # 전부 다시 due 가 되는 시점(now + 2·interval + 여유)에서 재-tick. 결정적 jitter 가 같은
    # 분산을 유지해 두 번째 주기에도 storm 이 없다.
    next_cycle = _NOW + timedelta(minutes=2 * _INTERVAL_MIN, seconds=1)
    second = asyncio.run(svc.run_tick(repo, backend, now=next_cycle))
    assert second.enqueued_count == 100
    next_runs = [repo.next_run_at_of(f"target-{i}") for i in range(100)]
    distinct_seconds = {dt.replace(microsecond=0) for dt in next_runs}
    assert len(distinct_seconds) >= 85, f"두 번째 주기 분산 부족: {len(distinct_seconds)}"


# ══════════════════════════════════════════════════════════════════════════
# QA gap-fill (qa-generate-e2e-tests, Story 5.4) — tick 분기/전파/precedence
# ══════════════════════════════════════════════════════════════════════════

# ── AC1: 미지 플랫폼 fail-closed(REASON_UNKNOWN_PLATFORM) ─────────────────────

def test_unknown_platform_target_marked_unknown_not_enqueued() -> None:
    bad = _target("t-bad", platform="YOGIYO")  # 정본 6종 매핑 불가 → fail-closed.
    repo = FakeSchedulerRepo(
        targets=[bad], gates={bad.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_UNKNOWN_PLATFORM


# ── AC4: conditional advance 패배 시 REASON_RACE_LOST(enqueue 0) ──────────────

def test_race_lost_reason_when_conditional_advance_fails() -> None:
    target = _target("t-a")
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )

    async def _always_lose(target_id, *, now, next_run_at):  # 다른 worker 가 선점한 상황.
        return False

    repo.claim_due_target = _always_lose  # type: ignore[assignment]
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_RACE_LOST


# ── AC2: warn_admin 을 결정 결과(ScheduleOutcome)에 전파(차단/허용 모두) ───────

def test_warn_admin_propagated_on_enqueue_for_grace_period() -> None:
    grace = _target("t-grace")
    repo = FakeSchedulerRepo(
        targets=[grace],
        gates={
            grace.tenant_id: TenantGate(
                SubscriptionStatus.PAYMENT_FAILED_GRACE, CustomerLifecycleState.ACTIVE
            )
        },
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 1
    assert result.outcomes[0].enqueued is True
    assert result.outcomes[0].warn_admin is True  # 유예 고객 enqueue 하되 경고 보존(AC2).


def test_warn_admin_propagated_on_block_for_suspended() -> None:
    susp = _target("t-susp")
    repo = FakeSchedulerRepo(
        targets=[susp],
        gates={
            susp.tenant_id: TenantGate(
                SubscriptionStatus.SUSPENDED, CustomerLifecycleState.ACTIVE
            )
        },
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.outcomes[0].enqueued is False
    assert result.outcomes[0].reason == "SUSPENDED"
    assert result.outcomes[0].warn_admin is True


# ── AC1: capacity 스냅샷의 기존 in-flight 가 가용 슬롯을 줄인다 ────────────────

def test_preexisting_in_flight_reduces_available_slots() -> None:
    targets = [_target(f"t-{i}") for i in range(5)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(n=5, in_flight=3),  # capacity 5, 이미 3 in-flight → 2 슬롯.
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 2


# ── AC3: 같은 플랫폼 다수 대상이어도 breaker 윈도 집계는 tick당 1회 ────────────

def test_breaker_window_aggregated_once_per_platform_per_tick() -> None:
    targets = [_target(f"t-{i}", platform="BAEMIN") for i in range(4)]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(),
    )
    calls: list[str] = []
    orig = repo.platform_failure_window

    async def _counting(platform, *, since, now):
        calls.append(platform)
        return await orig(platform, since=since, now=now)

    repo.platform_failure_window = _counting  # type: ignore[assignment]
    backend = InMemoryQueueBackend()
    asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    # 4 대상이 같은 플랫폼이어도 윈도 집계는 1회(대상별 중복 집계 회피, AC3).
    assert calls == ["BAEMIN"]


# ── AC1: enqueue 는 run_after=now 로 즉시 claim 가능하게 만든다 ───────────────

def test_enqueued_job_run_after_is_now_for_immediate_claim() -> None:
    target = _target("t-a")
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    snap = backend.job_snapshot(result.outcomes[0].job_id)
    assert snap is not None
    assert snap.run_after == _NOW


def test_enqueue_failure_restores_due_claim_before_reraising() -> None:
    class FailingQueue(InMemoryQueueBackend):
        async def enqueue(self, **_kwargs):
            raise RuntimeError("queue down")

    original_next = _NOW - timedelta(minutes=1)
    target = _target("t-a", next_run=original_next)
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity(n=1)
    )

    try:
        asyncio.run(SchedulerService().run_tick(repo, FailingQueue(), now=_NOW))
    except RuntimeError as exc:
        assert str(exc) == "queue down"
    else:
        raise AssertionError("queue failure should propagate")

    assert repo.next_run_at_of("t-a") == original_next
    assert len(repo.release_calls) == 1


def test_scheduler_uses_repository_atomic_claim_enqueue_hook() -> None:
    class QueueThatMustNotBeCalled(InMemoryQueueBackend):
        async def enqueue(self, **_kwargs):
            raise AssertionError("scheduler should delegate claim+enqueue to repository")

    class AtomicRepo(FakeSchedulerRepo):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.atomic_calls = []

        async def claim_due_target_and_enqueue(
            self,
            queue_backend,
            target,
            *,
            job_type,
            payload_json,
            now,
            next_run_at,
        ):
            self.atomic_calls.append(
                (queue_backend, target.target_id, job_type, payload_json, next_run_at)
            )
            won = await self.claim_due_target(
                target.target_id, now=now, next_run_at=next_run_at
            )
            return "job-atomic" if won else None

    target = _target("t-a")
    repo = AtomicRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )

    result = asyncio.run(
        SchedulerService().run_tick(repo, QueueThatMustNotBeCalled(), now=_NOW)
    )

    assert result.enqueued_count == 1
    assert result.outcomes[0].job_id == "job-atomic"
    assert len(repo.atomic_calls) == 1
    _queue, target_id, job_type, payload_json, _next_run_at = repo.atomic_calls[0]
    assert target_id == "t-a"
    assert job_type == JOB_TYPE_CRAWL_BAEMIN
    assert payload_json["target_id"] == "t-a"


# ── 일반: due 대상이 없으면 tick 은 no-op ─────────────────────────────────────

def test_tick_with_no_due_targets_is_noop() -> None:
    future = _target("t-f", next_run=_NOW + timedelta(minutes=5))
    repo = FakeSchedulerRepo(
        targets=[future], gates={future.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.enqueued_count == 0
    assert result.outcomes == ()


# ── precedence: 게이트 차단 > breaker, 활성-job 차단 > capacity ────────────────

def test_gate_block_takes_precedence_over_breaker() -> None:
    susp = _target("t-susp", platform="BAEMIN")
    repo = FakeSchedulerRepo(
        targets=[susp],
        gates={
            susp.tenant_id: TenantGate(
                SubscriptionStatus.SUSPENDED, CustomerLifecycleState.ACTIVE
            )
        },
        failure_windows={"BAEMIN": (10, 9)},  # breaker open 상태여도 게이트가 먼저.
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.outcomes[0].reason == "SUSPENDED"


def test_active_job_block_takes_precedence_over_capacity() -> None:
    target = _target("t-a")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        active_jobs=["t-a"],
        capacity=_capacity(n=0),  # capacity 0 이지만 활성-job 차단이 먼저 평가됨.
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))
    assert result.outcomes[0].reason == REASON_ACTIVE_JOB_EXISTS


# ── AC3: SchedulerService 의 custom breaker_threshold 가 open 판정에 반영 ──────

def test_service_custom_breaker_threshold_changes_open_decision() -> None:
    target = _target("t-b", platform="BAEMIN")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        failure_windows={"BAEMIN": (10, 4)},  # 40% 실패.
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    # 기본 30% 면 40% 는 open(skip). threshold 를 50% 로 올리면 40% 는 closed → enqueue.
    result = asyncio.run(
        SchedulerService(breaker_threshold=0.5).run_tick(repo, backend, now=_NOW)
    )
    assert result.enqueued_count == 1


# ══════════════════════════════════════════════════════════════════════════
# Task 3 — 인증 상태 게이트 + Coupang 자동 복구(한 번만 + cooldown)
# ══════════════════════════════════════════════════════════════════════════


def _coupang_target(tid="t-cp", *, auth_state="AUTH_REQUIRED", auto_2fa=True, cooldown=None):
    refs = dict(
        username="vault://coupang/login-id",
        password="vault://coupang/login-password",
        verification_email_address="vault://mail/address",
        verification_email_app_password="vault://mail/app-password",
    ) if auto_2fa else {}
    return _target(
        tid,
        platform="COUPANG",
        auth_state=auth_state,
        auto_recovery_cooldown_until=cooldown,
        **refs,
    )


def test_scheduler_blocks_crawl_when_coupang_auth_required_without_auto_2fa() -> None:
    """AUTH_REQUIRED Coupang without complete email 2FA does not enqueue crawl."""

    target = _coupang_target(auth_state="AUTH_REQUIRED", auto_2fa=False)
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_AUTH_REQUIRED_NO_AUTO_RECOVERY


def test_scheduler_blocks_crawl_for_user_action_pending_blocked_and_unknown() -> None:
    """Unsafe auth states do not open scheduled browser crawl attempts."""

    uap = _target("t-uap", auth_state="USER_ACTION_PENDING")
    blocked = _target("t-blk", auth_state="BLOCKED_OR_CAPTCHA")
    unknown = _target("t-unk", auth_state="UNKNOWN")
    targets = [uap, blocked, unknown]
    repo = FakeSchedulerRepo(
        targets=targets,
        gates={t.tenant_id: _ACTIVE_GATE for t in targets},
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 0
    reasons = {o.target_id: o.reason for o in result.outcomes}
    assert reasons["t-uap"] == REASON_AUTH_STATE_USER_ACTION_PENDING
    assert reasons["t-blk"] == REASON_AUTH_STATE_BLOCKED_OR_CAPTCHA
    assert reasons["t-unk"] == REASON_AUTH_STATE_UNKNOWN


def test_scheduler_enqueues_auth_coupang_2fa_instead_of_crawl_when_auth_required() -> None:
    """AUTH_REQUIRED Coupang account gets auth job, not crawl job.

    crawl-coupang-auth-separation Task 7: 자동 2FA 설정이 완전하고 cooldown 이 없으면 scheduler 는
    recovery crawl 이 아니라 전용 인증 job(AUTH_COUPANG_2FA)을 만든다.
    """

    target = _coupang_target(auth_state="AUTH_REQUIRED", auto_2fa=True, cooldown=None)
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 1
    assert result.outcomes[0].enqueued is True
    assert result.outcomes[0].reason == REASON_ENQUEUED_AUTH_COUPANG_2FA
    assert result.outcomes[0].job_type == "AUTH_COUPANG_2FA"
    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    payload = job.payload_json
    assert payload["job_type"] == "AUTH_COUPANG_2FA"
    assert payload["job_origin"] == "scheduler"
    assert payload["recovery_mode"] == "coupang_auto_email_2fa"
    assert payload["coupang_login_id_ref"] == "vault://coupang/login-id"
    assert payload["verification_email_address_ref"] == "vault://mail/address"
    assert payload["timeout_seconds"] == 60
    # payload TTL — 오래된 인증 job 이 downtime 뒤 실행돼 중복 OTP 를 요청하는 것을 막는다(검토 High).
    scheduled_at = datetime.fromisoformat(payload["scheduled_at"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    assert scheduled_at < expires_at <= scheduled_at + timedelta(minutes=5)
    # crawl 전용 플래그·인증번호 값은 싣지 않는다(auth job 은 recovery_mode 로 식별).
    assert "coupang_auto_email_2fa_enabled" not in payload
    assert "verification_code" not in payload
    # crawl next_run_at 은 전진하지 않는다(인증 성공 후 다음 tick 이 crawl 재개).
    assert repo.next_run_at_of(target.target_id) == target.next_run_at


def test_scheduler_blocks_coupang_crawl_while_auth_job_active() -> None:
    """Duplicate auth jobs and crawl jobs are both suppressed.

    이미 진행 중인 인증 job 이 있으면 새 AUTH_COUPANG_2FA 를 만들지 않는다(중복 OTP 요청 방지).
    """

    target = _coupang_target(auth_state="AUTH_REQUIRED", auto_2fa=True)
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        active_auth_jobs=[target.target_id],
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_AUTH_JOB_ALREADY_ACTIVE
    assert repo.auth_enqueues == []


def test_scheduler_blocks_auth_job_when_active_auth_job_count_positive() -> None:
    """DueTarget.active_auth_job_count facts also suppress a duplicate auth job."""

    target = _coupang_target(auth_state="AUTH_REQUIRED", auto_2fa=True)
    target = replace(target, active_auth_job_count=1)
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_AUTH_JOB_ALREADY_ACTIVE


def test_scheduler_resumes_crawl_after_coupang_auth_active() -> None:
    """ACTIVE auth state allows normal crawl scheduling again."""

    target = _coupang_target(auth_state="ACTIVE", auto_2fa=True)
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 1
    assert result.outcomes[0].reason == REASON_ENQUEUED_CRAWL
    assert result.outcomes[0].job_type == JOB_TYPE_CRAWL_COUPANG
    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    assert job.payload_json["job_type"] == JOB_TYPE_CRAWL_COUPANG
    # crawl 은 next_run_at 을 전진시킨다(정상 스케줄).
    assert repo.next_run_at_of(target.target_id) != target.next_run_at


def test_scheduler_requires_manual_action_for_user_action_pending() -> None:
    """CAPTCHA/manual states do not create auto 2FA attempts."""

    target = _coupang_target(auth_state="USER_ACTION_PENDING", auto_2fa=True)
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_AUTH_STATE_USER_ACTION_PENDING
    assert repo.auth_enqueues == []


def test_scheduler_blocks_coupang_recovery_during_cooldown() -> None:
    """Recent failed recovery suppresses new auth job attempts."""

    cooldown_until = _NOW + timedelta(minutes=30)
    target = _coupang_target(
        auth_state="AUTH_REQUIRED", auto_2fa=True, cooldown=cooldown_until
    )
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_COUPANG_AUTO_RECOVERY_COOLDOWN


def test_scheduler_auth_job_keeps_target_affinity() -> None:
    """Auth job uses the same target affinity as normal scheduled crawl."""

    target = _coupang_target(auth_state="AUTH_REQUIRED", auto_2fa=True)
    target = replace(target, assigned_agent_id="agent-7")
    repo = FakeSchedulerRepo(
        targets=[target], gates={target.tenant_id: _ACTIVE_GATE}, capacity=_capacity()
    )
    backend = InMemoryQueueBackend()
    result = asyncio.run(SchedulerService().run_tick(repo, backend, now=_NOW))

    job = backend.job_snapshot(result.outcomes[0].job_id)
    assert job is not None
    assert job.assigned_agent_id == "agent-7"


# ══════════════════════════════════════════════════════════════════════════
# Task 6 — pending crawl coalescing(target/platform 당 활성 crawl 1건)
# ══════════════════════════════════════════════════════════════════════════


def test_scheduler_does_not_create_second_pending_crawl_for_same_target_and_platform() -> None:
    """Backlog is coalesced to one useful crawl per target/platform."""

    target = _target("t-c", platform="COUPANG")
    repo = FakeSchedulerRepo(
        targets=[target],
        gates={target.tenant_id: _ACTIVE_GATE},
        active_jobs=["t-c"],  # 이미 활성 CRAWL_COUPANG 존재.
        capacity=_capacity(),
    )
    backend = InMemoryQueueBackend()
    svc = SchedulerService()

    result = asyncio.run(svc.run_tick(repo, backend, now=_NOW))

    # 두 번째 crawl 을 만들지 않는다(coalescing → target/platform 당 활성 1건).
    assert result.enqueued_count == 0
    assert result.outcomes[0].reason == REASON_ACTIVE_JOB_EXISTS
    # next_run_at 도 전진하지 않아 매 tick 같은 stale target 으로 스핀하지 않는다(전진=재진입 차단).
    assert repo.next_run_at_of("t-c") is None

    # 다음 tick(같은 시각)에도 활성 job 이 남아 있으면 여전히 새 job 0(중복 backlog 누적 없음).
    again = asyncio.run(svc.run_tick(repo, backend, now=_NOW))
    assert again.enqueued_count == 0
