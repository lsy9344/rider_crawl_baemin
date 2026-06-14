"""Story 5.8 / AC1 — audit_logs 7필드 완전성 + redaction(always-run, 무 DB).

readiness gate 7필드(actor/source/diff/target/reason/timestamp/result)를 위험 액션 audit 가
모두 채우는지, 거부 시도가 ``result=DENIED`` 로 남는지, source/reason 이 redaction 통과인지를
service + in-memory fake 로 잠근다(PG-gated 영속은 tests/negative/test_security_pg.py).

fake 값만(실제 토큰/전화/이메일/chat_id 형태 0). 평면 ``tests/server/`` 컨벤션.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from rider_crawl.redaction import REDACTED
from rider_server.db.base import Base
from rider_server.db import models  # noqa: F401  (Base.metadata 등록)
from rider_server.domain import AuditResult, MonitoringTarget, MonitoringTargetStatus
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.services.admin_action_repository_postgres import _audit_values
from rider_server.services.admin_action_service import (
    UNAUTHENTICATED_ACTOR,
    AdminActionService,
    AuditEntry,
    InMemoryAdminActionRepository,
)

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "tn-1"
_ACTOR = "11111111-1111-1111-1111-111111111111"


def _run(coro):
    return asyncio.run(coro)


def _repo_with_target() -> InMemoryAdminActionRepository:
    repo = InMemoryAdminActionRepository()
    repo.seed_target(
        MonitoringTarget(
            id="mt-1", tenant_id=_TENANT, platform_account_id="pa-1",
            name="가게", center_name="센터", status=MonitoringTargetStatus.ACTIVE,
        )
    )
    return repo


def _svc(repo) -> AdminActionService:
    return AdminActionService(repo, InMemoryQueueBackend())


# ══════════════════════════════════════════════════════════════════════════
# (1) 스키마 — audit_logs 가 readiness gate 7필드 컬럼을 모두 가진다
# ══════════════════════════════════════════════════════════════════════════

def test_audit_logs_table_has_readiness_gate_7_fields() -> None:
    cols = set(Base.metadata.tables["audit_logs"].columns.keys())
    # actor_id/source/diff_redacted/target_*/reason/created_at(timestamp)/result.
    assert {"actor_id", "source", "diff_redacted", "target_type", "target_id",
            "reason", "created_at", "result"} <= cols
    # result 는 NOT NULL(거부/성공 항상 기록), source/reason 은 nullable.
    assert Base.metadata.tables["audit_logs"].columns["result"].nullable is False


# ══════════════════════════════════════════════════════════════════════════
# (2) 위험 액션 audit 가 7필드를 채운다(actor·source·action·target·reason·timestamp·result)
# ══════════════════════════════════════════════════════════════════════════

def test_pause_audit_fills_all_seven_fields() -> None:
    repo = _repo_with_target()
    _run(
        _svc(repo).set_target_status(
            "mt-1", active=False, tenant_id=_TENANT, actor_id=_ACTOR,
            reason="점검", at=_NOW, source="ADMIN_UI/operator",
        )
    )
    e = repo.audits[-1]
    assert e.actor_id == _ACTOR              # actor
    assert e.source == "ADMIN_UI/operator"   # source
    assert e.action == "TARGET_PAUSE"        # action
    assert e.target_type == "monitoring_target" and e.target_id == "mt-1"  # target
    assert e.reason == "점검"                 # reason(first-class)
    assert e.created_at == _NOW              # timestamp
    assert e.result == "SUCCESS"             # result


def test_result_defaults_to_success() -> None:
    # 5.7 6필드 positional 생성 호환 — result 기본 SUCCESS, source/reason 기본 None.
    e = AuditEntry(actor_id=_ACTOR, action="X", target_type="t", target_id="i",
                   diff_redacted={}, created_at=_NOW)
    assert e.result == AuditResult.SUCCESS.value
    assert e.source is None and e.reason is None


# ══════════════════════════════════════════════════════════════════════════
# (3) 거부 시도도 result=DENIED 로 남는다(보안 audit 핵심) + redaction
# ══════════════════════════════════════════════════════════════════════════

def test_record_denied_writes_denied_result() -> None:
    repo = InMemoryAdminActionRepository()
    _run(
        _svc(repo).record_denied(
            actor_id=_ACTOR, action="ACCESS_DENIED", source="ADMIN_UI/viewer",
            reason="ROLE_INSUFFICIENT: POST /admin/targets/mt-1/pause", at=_NOW,
        )
    )
    e = repo.audits[-1]
    assert e.result == AuditResult.DENIED.value
    assert e.actor_id == _ACTOR
    assert e.created_at == _NOW


def test_record_break_glass_is_audited_success() -> None:
    repo = InMemoryAdminActionRepository()
    _run(
        _svc(repo).record_break_glass(
            actor_id=_ACTOR, source="ADMIN_UI/break-glass", reason="긴급 override", at=_NOW
        )
    )
    e = repo.audits[-1]
    assert e.action == "BREAK_GLASS_OVERRIDE"
    assert e.result == AuditResult.SUCCESS.value
    assert e.source == "ADMIN_UI/break-glass"


def test_source_and_reason_pass_redaction() -> None:
    # source/reason 에 secret 이 섞여도 평문이 남지 않는다(게이트레일 #5).
    repo = _repo_with_target()
    leaky_reason = "사유 bot_token 111:AAFAKEsecrettoken code 123456"
    _run(
        _svc(repo).set_target_status(
            "mt-1", active=False, tenant_id=_TENANT, actor_id=_ACTOR,
            reason=leaky_reason, at=_NOW, source="authorization=Bearer 222:AAFAKE",
        )
    )
    e = repo.audits[-1]
    assert REDACTED in e.reason and "111:AAFAKEsecrettoken" not in e.reason and "123456" not in e.reason
    assert REDACTED in e.source and "222:AAFAKE" not in e.source


# ══════════════════════════════════════════════════════════════════════════
# (4-QA) _audit_values 순수 매핑 — actor UUID 파싱·sentinel 보존·7필드 passthrough
# ══════════════════════════════════════════════════════════════════════════
# (qa-generate-e2e 보강: PG repo 의 _audit_values 는 actor UUID 파싱/미인증 sentinel 보존 +
#  source/reason/result passthrough 를 담는 순수 함수인데 PG-gated 파일만 간접 사용해 CI PG
#  skip 시 의미가 가려졌다 — memory pg-gated-files-hide-pure-helpers. 무-DB always-run 으로 잠금.)

_TARGET_UUID = "d1111111-1111-1111-1111-111111111111"


def test_audit_values_maps_seven_fields_with_uuid_actor() -> None:
    entry = AuditEntry(
        actor_id=_ACTOR, action="TARGET_PAUSE", target_type="monitoring_target",
        target_id=_TARGET_UUID, diff_redacted={"k": "v"}, created_at=_NOW,
        source="ADMIN_UI/operator", reason="점검", result=AuditResult.SUCCESS.value,
    )
    values = _audit_values(entry)
    assert values["actor_id"] == uuid.UUID(_ACTOR)        # UUID actor → 컬럼
    assert values["target_id"] == uuid.UUID(_TARGET_UUID)
    assert values["action"] == "TARGET_PAUSE"
    assert values["source"] == "ADMIN_UI/operator"        # 5.8 신규 3필드 passthrough
    assert values["reason"] == "점검"
    assert values["result"] == "SUCCESS"


def test_audit_values_preserves_unauthenticated_actor_sentinel() -> None:
    # 미인증 sentinel(UUID 아님) → actor_id 컬럼 NULL + diff_redacted.actor 보존(추적 유지).
    entry = AuditEntry(
        actor_id=UNAUTHENTICATED_ACTOR, action="ACCESS_DENIED", target_type="admin_access",
        target_id=None, diff_redacted={}, created_at=_NOW, result=AuditResult.DENIED.value,
    )
    values = _audit_values(entry)
    assert values["actor_id"] is None
    assert values["target_id"] is None
    assert values["diff_redacted"]["actor"] == UNAUTHENTICATED_ACTOR
    assert values["result"] == "DENIED"
