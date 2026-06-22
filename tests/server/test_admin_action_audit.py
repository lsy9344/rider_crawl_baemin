"""Story 5.7 / AC3 — 위험한 수동 액션 audit 기록(actor+시각+redaction, 같은 트랜잭션).

각 위험 액션이 ``audit_logs``(in-memory fake) 에 actor·action·target·timestamp 를 기록하고,
``diff_redacted`` 에 token/OTP/password/chat_id 원문 평문이 남지 않음을 redact 어서션으로 잠근다.
미인증 actor 는 명시적 sentinel 로 기록된다. always-run(무 DB, 주입 시각/actor).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from rider_crawl.redaction import REDACTED
from rider_server.domain import (
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    Subscription,
    SubscriptionStatus,
)
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.services.admin_action_service import (
    UNAUTHENTICATED_ACTOR,
    AdminActionService,
    HeldDispatchRef,
    InMemoryAdminActionRepository,
    JobRef,
)
from rider_server.services.subscription_gate import HeldDisposition

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "tn-1"
_ACTOR = "11111111-1111-1111-1111-111111111111"


def _run(coro):
    return asyncio.run(coro)


def _svc(repo):
    return AdminActionService(repo, InMemoryQueueBackend())


def _seeded_repo() -> InMemoryAdminActionRepository:
    repo = InMemoryAdminActionRepository()
    repo.seed_subscription(
        Subscription(id="sub-1", tenant_id=_TENANT, plan="basic", status=SubscriptionStatus.PAYMENT_ACTIVE)
    )
    repo.seed_target(
        MonitoringTarget(
            id="mt-1", tenant_id=_TENANT, platform_account_id="pa-1",
            name="가게", center_name="센터", status=MonitoringTargetStatus.ACTIVE,
        )
    )
    repo.seed_platform_account(
        PlatformAccount(
            id="pa-1",
            tenant_id=_TENANT,
            platform=Platform.BAEMIN,
            label="계정",
            username="login-id-ref",
            password="login-password-ref",
        )
    )
    repo.seed_job(JobRef(job_id="job-1", type="CRAWL_BAEMIN", target_id="mt-1", status="FAILED", tenant_id=_TENANT))
    return repo


def test_suspend_records_actor_action_target_timestamp() -> None:
    repo = _seeded_repo()
    _run(_svc(repo).suspend_subscription("sub-1", reason="미납", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW))

    entry = repo.audits[-1]
    assert entry.actor_id == _ACTOR
    assert entry.action == "SUBSCRIPTION_SUSPEND"
    assert entry.target_type == "subscription"
    assert entry.target_id == "sub-1"
    assert entry.created_at == _NOW


def test_pause_retry_each_record_an_audit_row() -> None:
    repo = _seeded_repo()
    svc = _svc(repo)
    before = len(repo.audits)

    _run(svc.set_target_status("mt-1", active=False, tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW))
    _run(svc.retry_job("job-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="", at=_NOW))

    actions = [e.action for e in repo.audits[before:]]
    assert actions == ["TARGET_PAUSE", "JOB_RETRY"]


def test_diff_redacted_masks_secret_in_reason() -> None:
    """자유 텍스트 reason 에 token/OTP 가 섞여도 diff_redacted 에 원문 평문이 남지 않는다."""
    repo = _seeded_repo()
    leaky = "사유 bot_token 111:AAFAKEsecrettoken 그리고 code 123456 확인"

    _run(_svc(repo).suspend_subscription("sub-1", reason=leaky, tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW))

    reason_field = repo.audits[-1].diff_redacted["reason"]
    assert REDACTED in reason_field
    assert "111:AAFAKEsecrettoken" not in reason_field
    assert "123456" not in reason_field


def test_unauthenticated_actor_recorded_as_sentinel() -> None:
    repo = _seeded_repo()

    _run(
        _svc(repo).set_target_status(
            "mt-1", active=False, tenant_id=_TENANT, actor_id=UNAUTHENTICATED_ACTOR, reason="", at=_NOW
        )
    )

    assert repo.audits[-1].actor_id == UNAUTHENTICATED_ACTOR


def test_no_secret_keys_leak_in_diff_for_dispatch_actions() -> None:
    """chat_id 류 secret 키가 diff 에 들어와도 redact_mapping 이 통째 마스킹한다(방어적)."""
    from rider_server.services.admin_action_service import build_diff_redacted

    diff = build_diff_redacted({"channel_id": "ch-1", "chat_id": "999888777", "reason": "ok"})
    assert diff["chat_id"] == REDACTED
    assert diff["channel_id"] == "ch-1"  # 불투명 FK 는 secret 아님(보존)


# ── (QA) 나머지 위험 액션도 audit row 를 남긴다(AGENT_ASSIGN·TEST_CRAWL·AUTH_CHECK·HELD dispose) ──

def test_assign_agent_records_audit_with_agent_id() -> None:
    repo = _seeded_repo()

    _run(
        _svc(repo).assign_agent(
            target_id="mt-1", agent_id="ag-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="배정", at=_NOW
        )
    )

    entry = repo.audits[-1]
    assert entry.action == "AGENT_ASSIGN"
    assert entry.target_type == "monitoring_target"
    assert entry.target_id == "mt-1"
    assert entry.created_at == _NOW
    assert entry.diff_redacted["agent_id"] == "ag-1"


def test_test_crawl_and_auth_check_each_record_an_audit_row() -> None:
    repo = _seeded_repo()
    svc = _svc(repo)
    before = len(repo.audits)

    _run(svc.test_crawl(target_id="mt-1", job_type="CRAWL_BAEMIN", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW))
    _run(svc.auth_check(target_id="mt-1", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW))

    actions = [e.action for e in repo.audits[before:]]
    assert actions == ["TEST_CRAWL", "AUTH_CHECK"]


def test_auth_start_records_audit_row() -> None:
    repo = _seeded_repo()
    svc = _svc(repo)

    _run(svc.start_auth(target_id="mt-1", tenant_id=_TENANT, actor_id=_ACTOR, at=_NOW))

    entry = repo.audits[-1]
    assert entry.action == "AUTH_START"
    assert entry.target_type == "monitoring_target"
    assert entry.target_id == "mt-1"
    assert entry.diff_redacted["job_type"] == "OPEN_AUTH_BROWSER"


def test_dispose_held_records_audit_with_disposition() -> None:
    repo = _seeded_repo()
    repo.seed_held_dispatch(
        HeldDispatchRef(dispatch_id="dsp-1", tenant_id=_TENANT, subscription_id="sub-1", status="HELD")
    )

    _run(
        _svc(repo).dispose_held_dispatch(
            "dsp-1", HeldDisposition.DISCARD, tenant_id=_TENANT, actor_id=_ACTOR, reason="폐기", at=_NOW
        )
    )

    entry = repo.audits[-1]
    assert entry.action == "HELD_DISPATCH_DISCARD"
    assert entry.target_type == "dispatch"
    assert entry.diff_redacted["disposition"] == "DISCARD"
