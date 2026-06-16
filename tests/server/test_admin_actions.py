"""Story 5.7 / AC1·AC2·AC3 — Admin 수동 운영 액션 + 구독/Dispatch 상태 전이(always-run + 라우트).

(1) always-run 순수/service(무 DB, in-memory fake repo + 주입 시각/actor): 구독 suspend/resume,
    dispose_held(HELD,DISCARD/RESUME)·비-HELD 거부, job retry(FAILED/RETRY→PENDING)·SUCCEEDED 거부,
    test send 단일 채널만(fan-out 0)·dedup 우회 0(DUPLICATE_BLOCKED), 대상 활성/비활성·INACTIVE 거부,
    test crawl 1건 enqueue, tenant scope 차단.
(2) 라우트(``TestClient``): POST 액션 200/HTMX fragment·service 호출·상태 반영, 잘못된 retry/dispose→4xx,
    tenant 불일치→404, ``require_admin_session`` 거부 seam→4xx, 읽기 전용 대시보드 GET 무회귀.

fake 값만(실제 토큰/전화/이메일/chat_id 형태 금지). 평면 ``tests/server/`` 컨벤션.
``pytest-asyncio`` 미도입 → ``asyncio.run`` 으로 async service 구동(5.4~5.6 선례).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http import HTTPStatus

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rider_server.domain import (
    Messenger,
    MonitoringTarget,
    MonitoringTargetStatus,
    Subscription,
    SubscriptionStatus,
)
from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.queue.states import (
    InvalidJobTransition,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_SUCCEEDED,
)
from rider_server.services.admin_action_service import (
    AdminActionNotFound,
    AdminActionService,
    HeldDispatchRef,
    InMemoryAdminActionRepository,
    JobRef,
    TenantScopeViolation,
)
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.dispatch_fanout_service import DispatchJob
from rider_server.services.subscription_gate import DispatchJobStatus, HeldDisposition
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_SENT_AT = datetime(2026, 6, 14, 12, 0, 5, tzinfo=timezone.utc)
_TENANT = "tn-1"
_OTHER = "tn-2"
_ACTOR = "11111111-1111-1111-1111-111111111111"
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
# Story 5.8: 액션 라우트는 OPERATOR↑ 게이트(fail-closed). 5.7 라우트 테스트는 MFA 검증된
# OPERATOR principal 을 주입해 통과시킨다(의도된 보안 강화 — story 4.5).
_OPERATOR = AdminPrincipal(actor_id=_ACTOR, role=AdminRole.OPERATOR, mfa_verified=True,
                           source="ADMIN_UI/operator")


def _run(coro):
    return asyncio.run(coro)


def _sub(status=SubscriptionStatus.PAYMENT_ACTIVE, *, tenant=_TENANT) -> Subscription:
    return Subscription(id="sub-1", tenant_id=tenant, plan="basic", status=status)


def _target(status=MonitoringTargetStatus.ACTIVE, *, tenant=_TENANT) -> MonitoringTarget:
    return MonitoringTarget(
        id="mt-1",
        tenant_id=tenant,
        platform_account_id="pa-1",
        name="가게",
        center_name="센터",
        url="https://example.invalid/mt-1",
        interval_minutes=10,
        status=status,
    )


def _job(status=JOB_STATUS_FAILED, *, tenant=_TENANT) -> JobRef:
    return JobRef(job_id="job-1", type="CRAWL_BAEMIN", target_id="mt-1", status=status, tenant_id=tenant)


def _held(status="HELD", *, tenant=_TENANT) -> HeldDispatchRef:
    return HeldDispatchRef(dispatch_id="dsp-1", tenant_id=tenant, subscription_id="sub-1", status=status)


def _service(repo: InMemoryAdminActionRepository, queue=None) -> AdminActionService:
    return AdminActionService(repo, queue or InMemoryQueueBackend())


def _dispatch_job(channel_id="ch-test") -> DispatchJob:
    return DispatchJob(
        id="dj-1",
        target_id="mt-1",
        channel_id=channel_id,
        message_id="msg-1",
        messenger=Messenger.TELEGRAM,
        template_version="v1",
        message_hash="abc123",
    )


# ══════════════════════════════════════════════════════════════════════════
# (1) AC2 — 구독 중지/복구(게이트 호출 + persist)
# ══════════════════════════════════════════════════════════════════════════

def test_suspend_persists_suspended_and_records_change() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_subscription(_sub())
    svc = _service(repo)

    result = _run(
        svc.suspend_subscription("sub-1", reason="미납", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW)
    )

    assert result.status is SubscriptionStatus.SUSPENDED
    assert _run(repo.get_subscription("sub-1")).status is SubscriptionStatus.SUSPENDED
    assert repo.audits[-1].action == "SUBSCRIPTION_SUSPEND"
    assert repo.audits[-1].diff_redacted["to_status"] == "SUSPENDED"


def test_resume_restores_payment_active() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_subscription(_sub(SubscriptionStatus.SUSPENDED))
    svc = _service(repo)

    result = _run(
        svc.resume_subscription("sub-1", reason="복구", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW)
    )

    assert result.status is SubscriptionStatus.PAYMENT_ACTIVE
    assert repo.audits[-1].action == "SUBSCRIPTION_RESUME"


# ══════════════════════════════════════════════════════════════════════════
# (1) AC2 — HELD Dispatch 폐기/재개(게이트 dispose_held — 불변식 ①②)
# ══════════════════════════════════════════════════════════════════════════

def test_dispose_held_discard_to_discarded() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_held_dispatch(_held("HELD"))
    svc = _service(repo)

    new_status = _run(
        svc.dispose_held_dispatch(
            "dsp-1", HeldDisposition.DISCARD, tenant_id=_TENANT, actor_id=_ACTOR, reason="폐기", at=_NOW
        )
    )

    assert new_status == DispatchJobStatus.DISCARDED.value
    assert _run(repo.get_held_dispatch("dsp-1")).status == "DISCARDED"


def test_dispose_held_resume_to_pending() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_held_dispatch(_held("HELD"))
    svc = _service(repo)

    new_status = _run(
        svc.dispose_held_dispatch(
            "dsp-1", HeldDisposition.RESUME, tenant_id=_TENANT, actor_id=_ACTOR, reason="재개", at=_NOW
        )
    )

    assert new_status == DispatchJobStatus.PENDING.value


def test_dispose_non_held_succeeded_is_rejected() -> None:
    """불변식 ① — SUCCEEDED 분은 dispose 로 발송 가능으로 되돌릴 수 없다(게이트 ValueError)."""
    repo = InMemoryAdminActionRepository()
    repo.seed_held_dispatch(_held("SUCCEEDED"))
    svc = _service(repo)

    with pytest.raises(ValueError):
        _run(
            svc.dispose_held_dispatch(
                "dsp-1", HeldDisposition.RESUME, tenant_id=_TENANT, actor_id=_ACTOR, reason="x", at=_NOW
            )
        )
    # 상태 불변(거부) — DISCARDED/PENDING 으로 새지 않음.
    assert _run(repo.get_held_dispatch("dsp-1")).status == "SUCCEEDED"


def test_resume_does_not_auto_dispatch_held() -> None:
    """불변식 ② — 구독 복구는 HELD Dispatch 를 자동으로 건드리지 않는다(별도 dispose 필요)."""
    repo = InMemoryAdminActionRepository()
    repo.seed_subscription(_sub(SubscriptionStatus.SUSPENDED))
    repo.seed_held_dispatch(_held("HELD"))
    svc = _service(repo)

    _run(svc.resume_subscription("sub-1", reason="복구", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW))

    # 복구 후에도 HELD 는 그대로(자동 PENDING/발송 0).
    assert _run(repo.get_held_dispatch("dsp-1")).status == "HELD"


# ══════════════════════════════════════════════════════════════════════════
# (1) AC1 — job retry(FAILED/RETRY→PENDING 만, SUCCEEDED 거부)
# ══════════════════════════════════════════════════════════════════════════

def test_retry_failed_job_to_pending() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_job(_job(JOB_STATUS_FAILED))
    svc = _service(repo)

    status = _run(svc.retry_job("job-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="재시도", at=_NOW))

    assert status == JOB_STATUS_PENDING
    assert _run(repo.get_job("job-1")).status == JOB_STATUS_PENDING


def test_retry_succeeded_job_is_rejected() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_job(_job(JOB_STATUS_SUCCEEDED))
    svc = _service(repo)

    with pytest.raises(InvalidJobTransition):
        _run(svc.retry_job("job-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="x", at=_NOW))
    assert _run(repo.get_job("job-1")).status == JOB_STATUS_SUCCEEDED  # 불변(SUCCEEDED 터미널)


# ══════════════════════════════════════════════════════════════════════════
# (1) AC1 — test send 단일 채널만(fan-out 0) + dedup 우회 0
# ══════════════════════════════════════════════════════════════════════════

def test_test_send_single_channel_and_dedup_not_bypassed() -> None:
    repo = InMemoryAdminActionRepository()
    svc = _service(repo)
    reserved: set[str] = set()
    sends: list[str] = []

    def reserve(key: str) -> bool:
        if key in reserved:
            return False
        reserved.add(key)
        return True

    def send(job: DispatchJob) -> None:
        sends.append(job.channel_id)

    job = _dispatch_job("ch-test")
    first = _run(
        svc.test_send(
            job, collected_at=_NOW, reserve=reserve, send=send, log_id_for=lambda j: "log-1",
            sent_at=_SENT_AT, tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW,
        )
    )
    # 같은 dedup key 재시도 → 우회 없이 DUPLICATE_BLOCKED(send 미호출).
    second = _run(
        svc.test_send(
            job, collected_at=_NOW, reserve=reserve, send=send, log_id_for=lambda j: "log-2",
            sent_at=_SENT_AT, tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW,
        )
    )

    assert first.status.value == "SENT"
    assert second.status.value == "DUPLICATE_BLOCKED"
    # 단일 채널로만 1회 전송(fan-out 0, 재시도가 dedup 우회 0).
    assert sends == ["ch-test"]


# ══════════════════════════════════════════════════════════════════════════
# (1) AC1 — 대상 활성/비활성(ACTIVE↔PAUSED), INACTIVE 거부 + test crawl
# ══════════════════════════════════════════════════════════════════════════

def test_pause_and_activate_toggle() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target(MonitoringTargetStatus.ACTIVE))
    svc = _service(repo)

    paused = _run(
        svc.set_target_status("mt-1", active=False, tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW)
    )
    assert paused.status is MonitoringTargetStatus.PAUSED
    activated = _run(
        svc.set_target_status("mt-1", active=True, tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW)
    )
    assert activated.status is MonitoringTargetStatus.ACTIVE


def test_inactive_target_toggle_rejected() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target(MonitoringTargetStatus.INACTIVE))
    svc = _service(repo)

    with pytest.raises(ValueError):
        _run(
            svc.set_target_status("mt-1", active=True, tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW)
        )


def test_test_crawl_enqueues_single_job() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    queue = InMemoryQueueBackend()
    svc = _service(repo, queue)

    job_id = _run(
        svc.test_crawl(target_id="mt-1", job_type="CRAWL_BAEMIN", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW)
    )

    assert queue.job_status(job_id) == JOB_STATUS_PENDING
    job = queue.job_snapshot(job_id)
    assert job is not None
    assert job.payload_json == {
        "target_id": "mt-1",
        "tenant_id": _TENANT,
        "platform": "baemin",
        "platform_account_id": "pa-1",
        "primary_url": "https://example.invalid/mt-1",
        "expected_display_name": "센터",
        "browser_profile_ref": "profile:mt-1",
        "timeout_seconds": 60,
        "parser_version": "baemin-v1",
        "job_type": "CRAWL_BAEMIN",
    }


def test_test_crawl_unknown_job_type_rejected() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    svc = _service(repo)

    with pytest.raises(ValueError):
        _run(
            svc.test_crawl(target_id="mt-1", job_type="DESTROY", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW)
        )


def test_dry_run_render_returns_text_without_send() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    svc = _service(repo)

    text = _run(
        svc.dry_run_render(
            lambda: "렌더된 메시지", target_id="mt-1", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW
        )
    )

    assert text == "렌더된 메시지"
    assert repo.audits[-1].action == "DRY_RUN_RENDER"
    assert repo.audits[-1].diff_redacted["sent"] is False


# ══════════════════════════════════════════════════════════════════════════
# (1) tenant scope — cross-tenant 차단(not-found 동급, 누출 0)
# ══════════════════════════════════════════════════════════════════════════

def test_cross_tenant_subscription_blocked() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_subscription(_sub(tenant=_OTHER))
    svc = _service(repo)

    with pytest.raises(TenantScopeViolation):
        _run(
            svc.suspend_subscription("sub-1", reason="x", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW)
        )
    # 다른 tenant 구독 상태 불변(누출/변경 0).
    assert _run(repo.get_subscription("sub-1")).status is SubscriptionStatus.PAYMENT_ACTIVE


def test_missing_target_is_not_found() -> None:
    repo = InMemoryAdminActionRepository()
    svc = _service(repo)

    with pytest.raises(AdminActionNotFound):
        _run(
            svc.set_target_status("nope", active=True, tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW)
        )


# ══════════════════════════════════════════════════════════════════════════
# (2) 라우트(TestClient) — POST 액션·4xx·tenant·auth seam·무회귀
# ══════════════════════════════════════════════════════════════════════════

def _app_with(repo: InMemoryAdminActionRepository, queue=None):
    svc = AdminActionService(repo, queue or InMemoryQueueBackend())
    app = create_app(_FAKE_SETTINGS, admin_action_service=svc)
    app.state.resolve_admin_principal = lambda request: _OPERATOR  # 통과 principal 주입(5.8)
    return app


def test_route_pause_returns_fragment_and_persists() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target(MonitoringTargetStatus.ACTIVE))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/pause?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "text/html" in resp.headers["content-type"]
    assert "비활성" in resp.text
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.PAUSED


def test_route_suspend_resume_roundtrip() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_subscription(_sub())
    client = TestClient(_app_with(repo))

    assert client.post("/admin/subscriptions/sub-1/suspend?tenant=tn-1").status_code == HTTPStatus.OK
    assert _run(repo.get_subscription("sub-1")).status is SubscriptionStatus.SUSPENDED
    assert client.post("/admin/subscriptions/sub-1/resume?tenant=tn-1").status_code == HTTPStatus.OK
    assert _run(repo.get_subscription("sub-1")).status is SubscriptionStatus.PAYMENT_ACTIVE


def test_route_retry_invalid_transition_is_400() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_job(_job(JOB_STATUS_SUCCEEDED))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/jobs/job-1/retry?tenant=tn-1")

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert resp.json()["error"]["code"]


def test_route_dispose_invalid_disposition_is_400() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_held_dispatch(_held("HELD"))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/dispatch/dsp-1/dispose?tenant=tn-1", data={"disposition": "NUKE"})

    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_route_cross_tenant_is_404() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target(tenant=_OTHER))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/pause?tenant=tn-1")

    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_route_requires_admin_session() -> None:
    """Story 5.8: principal 미해결(seam None) → 401(fail-closed). 액션 라우트는 OPERATOR↑ 게이트."""
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    app = create_app(_FAKE_SETTINGS, admin_action_service=AdminActionService(repo, InMemoryQueueBackend()))
    app.state.resolve_admin_principal = lambda request: None  # 미인증 → fail-closed
    client = TestClient(app)

    resp = client.post("/admin/targets/mt-1/pause?tenant=tn-1")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_route_viewer_role_cannot_act() -> None:
    """Story 5.8: VIEWER principal 이 운영 액션(OPERATOR↑) 시도 → 403 + DENIED audit."""
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    app = create_app(_FAKE_SETTINGS, admin_action_service=AdminActionService(repo, InMemoryQueueBackend()))
    viewer = AdminPrincipal(actor_id=_ACTOR, role=AdminRole.VIEWER, mfa_verified=True, source="ADMIN_UI/viewer")
    app.state.resolve_admin_principal = lambda request: viewer
    client = TestClient(app)

    resp = client.post("/admin/targets/mt-1/pause?tenant=tn-1")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    # 거부 시도가 result=DENIED 로 audit(보안 audit — 시도 자체를 남긴다).
    assert repo.audits[-1].result == "DENIED"
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.ACTIVE  # 전이 0


def test_route_mfa_unverified_privileged_denied() -> None:
    """Story 5.8: MFA 미검증 principal 의 privileged 액션 → 403(게이트레일 #4)."""
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    app = create_app(_FAKE_SETTINGS, admin_action_service=AdminActionService(repo, InMemoryQueueBackend()))
    no_mfa = AdminPrincipal(actor_id=_ACTOR, role=AdminRole.OPERATOR, mfa_verified=False, source="x")
    app.state.resolve_admin_principal = lambda request: no_mfa
    client = TestClient(app)

    resp = client.post("/admin/targets/mt-1/pause?tenant=tn-1")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert repo.audits[-1].result == "DENIED"


def test_readonly_dashboard_get_still_ok() -> None:
    repo = InMemoryAdminActionRepository()
    client = TestClient(_app_with(repo))

    resp = client.get("/admin")
    assert resp.status_code == HTTPStatus.OK
    assert "운영 대시보드" in resp.text


def test_route_test_send_fail_closed_without_seam() -> None:
    """test send seam 미설정 → fail-closed 400(모호하면 미발송)."""
    repo = InMemoryAdminActionRepository()
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/test-send?tenant=tn-1", data={"channel_id": "ch-test"})
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_route_test_send_single_channel_when_seam_wired() -> None:
    repo = InMemoryAdminActionRepository()
    app = _app_with(repo)
    sends: list[str] = []
    reserved: set[str] = set()

    async def _seam(service, *, target_id, channel_id, tenant_id, actor_id, at, source=None):
        def reserve(key: str) -> bool:
            if key in reserved:
                return False
            reserved.add(key)
            return True

        return await service.test_send(
            _dispatch_job(channel_id),
            collected_at=_NOW,
            reserve=reserve,
            send=lambda j: sends.append(j.channel_id),
            log_id_for=lambda j: "log-1",
            sent_at=_SENT_AT,
            tenant_id=tenant_id,
            actor_id=actor_id,
            at=at,
            source=source,
        )

    app.state.admin_test_send = _seam
    app.state.sending_enabled = True  # 5.10 kill switch: 실전송하려면 전역 발송이 켜져 있어야 함.
    client = TestClient(app)

    resp = client.post("/admin/targets/mt-1/test-send?tenant=tn-1", data={"channel_id": "ch-test"})
    assert resp.status_code == HTTPStatus.OK
    assert "SENT" in resp.text
    assert sends == ["ch-test"]  # 단일 채널만(fan-out 0)


# ══════════════════════════════════════════════════════════════════════════
# (1-QA) AC1 service 빈틈 — Agent 배정·auth-check(PG-gated 만 있던 always-run 보강)
# ══════════════════════════════════════════════════════════════════════════
# (qa-generate-e2e 보강: assign_agent/auth_check 는 always-run 단위 부재였고, PG-gated 파일이
#  의미를 가리고 있었다 — memory pg-gated-files-hide-pure-helpers.)

def test_assign_agent_persists_affinity_and_audit() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    svc = _service(repo)

    _run(
        svc.assign_agent(
            target_id="mt-1", agent_id="ag-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="배정", at=_NOW
        )
    )

    assert repo.agent_for("mt-1") == "ag-1"
    assert repo.audits[-1].action == "AGENT_ASSIGN"
    assert repo.audits[-1].diff_redacted["agent_id"] == "ag-1"  # 불투명 id 보존(secret 아님)


def test_assign_agent_cross_tenant_blocked() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target(tenant=_OTHER))
    svc = _service(repo)

    with pytest.raises(TenantScopeViolation):
        _run(
            svc.assign_agent(
                target_id="mt-1", agent_id="ag-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW
            )
        )
    assert repo.agent_for("mt-1") is None  # 누출/변경 0


def test_auth_check_enqueues_auth_check_job_and_audit() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    queue = InMemoryQueueBackend()
    svc = _service(repo, queue)

    job_id = _run(svc.auth_check(target_id="mt-1", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW))

    assert queue.job_status(job_id) == JOB_STATUS_PENDING  # AUTH_CHECK job 1건 PENDING 진입
    assert repo.audits[-1].action == "AUTH_CHECK"


# ══════════════════════════════════════════════════════════════════════════
# (1-QA) tenant scope — retry/dispose 도 cross-tenant 차단(누출/변경 0)
# ══════════════════════════════════════════════════════════════════════════

def test_retry_cross_tenant_blocked() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_job(_job(JOB_STATUS_FAILED, tenant=_OTHER))
    svc = _service(repo)

    with pytest.raises(TenantScopeViolation):
        _run(svc.retry_job("job-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW))
    assert _run(repo.get_job("job-1")).status == JOB_STATUS_FAILED  # 전이 0


def test_dispose_cross_tenant_blocked() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_held_dispatch(_held("HELD", tenant=_OTHER))
    svc = _service(repo)

    with pytest.raises(TenantScopeViolation):
        _run(
            svc.dispose_held_dispatch(
                "dsp-1", HeldDisposition.DISCARD, tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW
            )
        )
    assert _run(repo.get_held_dispatch("dsp-1")).status == "HELD"  # 전이/누출 0


# ══════════════════════════════════════════════════════════════════════════
# (2-QA) 라우트 빈틈 — activate·test-crawl(±platform)·auth-check·dry-run·assign·retry·dispose
# ══════════════════════════════════════════════════════════════════════════

def test_route_activate_returns_fragment_and_persists() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target(MonitoringTargetStatus.PAUSED))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/activate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert "활성" in resp.text
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.ACTIVE


def test_route_test_crawl_enqueues_baemin() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/test-crawl?tenant=tn-1", data={"platform": "BAEMIN"})

    assert resp.status_code == HTTPStatus.OK
    assert "enqueue" in resp.text


def test_route_test_crawl_coupang_platform_branch() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/test-crawl?tenant=tn-1", data={"platform": "COUPANG"})

    assert resp.status_code == HTTPStatus.OK


def test_route_auth_check_triggers() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/auth-check?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert "AUTH_CHECK" in resp.text


def test_route_dry_run_returns_preview_without_send() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/targets/mt-1/dry-run?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "미발송" in resp.text  # FR-3: 렌더만, 실발송 0


def test_route_assign_agent_happy_path() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/agents/assign?tenant=tn-1", data={"target_id": "mt-1", "agent_id": "ag-1"})

    assert resp.status_code == HTTPStatus.OK
    assert repo.agent_for("mt-1") == "ag-1"


def test_route_assign_agent_missing_fields_is_400() -> None:
    repo = InMemoryAdminActionRepository()
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/agents/assign?tenant=tn-1", data={"target_id": "mt-1"})

    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_route_retry_failed_job_to_pending() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_job(_job(JOB_STATUS_FAILED))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/jobs/job-1/retry?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_job("job-1")).status == JOB_STATUS_PENDING


def test_route_dispose_discard_happy_path() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_held_dispatch(_held("HELD"))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/dispatch/dsp-1/dispose?tenant=tn-1", data={"disposition": "DISCARD"})

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_held_dispatch("dsp-1")).status == "DISCARDED"


def test_route_resume_invalid_to_status_is_400() -> None:
    repo = InMemoryAdminActionRepository()
    repo.seed_subscription(_sub(SubscriptionStatus.SUSPENDED))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/subscriptions/sub-1/resume?tenant=tn-1", data={"to_status": "BOGUS"})

    assert resp.status_code == HTTPStatus.BAD_REQUEST
