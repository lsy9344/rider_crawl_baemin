"""Story 5.6 / AC1·AC4 — PostgreSQL DashboardRepository 파생 집계·tenant 격리(PG-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) 파생 집계 — 수집 성공 ``MAX(snapshots.collected_at WHERE quality_state='OK')``(비-OK 제외),
      전송 성공 ``MAX(delivery_logs.sent_at WHERE status='SENT')``, 최신 실패 ``error_code``.
  (2) **tenant 격리** — tenant A 질의에 tenant B 데이터가 새지 않음(cross-tenant negative).
  (3) 채널 구분 — Kakao queue lag(대기 KAKAO_SEND ``now - MIN(run_after)``)·Telegram 오류 카운트.
  (4) AC4 인증 필요 목록(계정 AUTH_REQUIRED → 대상/프로필 조인, tenant scope).

always-run 단위 테스트(``tests/server/test_dashboard_severity.py``·``test_admin_dashboard.py``)가
심각도/조립/라우트 의미를 잠그고, 이 파일은 SQL 파생 집계·격리만 PG 로 확인한다. 시각은 주입
(repo 메서드 ``now``)해 결정적이다. fake 값만 — 실제 토큰/전화/이메일/chat_id 형태 없음.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
_pg_gate = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재(현 WSL/venv). always-run 단위로 잠금.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)

# 유효 UUID — PG FK 충족.
_TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_ACC_A_OK = "a1111111-1111-1111-1111-111111111111"
_ACC_A_AUTH = "a2222222-2222-2222-2222-222222222222"
_ACC_B = "b1111111-1111-1111-1111-111111111111"
_T1 = "11111111-1111-1111-1111-111111111111"  # tenant A, 집계 대상
_T2 = "22222222-2222-2222-2222-222222222222"  # tenant A, 인증 필요
_T3 = "33333333-3333-3333-3333-333333333333"  # tenant B
_AGENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_PROFILE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_CHAN_A = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


async def _seed(session_factory) -> None:
    from rider_server.db.models.account import (
        AuthSession,
        MonitoringTarget,
        PlatformAccount,
    )
    from rider_server.db.models.agent import Agent, BrowserProfile, Job
    from rider_server.db.models.messaging import (
        DeliveryLog,
        Message,
        MessengerChannel,
        Snapshot,
    )
    from rider_server.db.models.tenancy import Subscription, Tenant
    from rider_server.queue.states import JOB_TYPE_KAKAO_SEND

    def _snap(snap_id, target_id, collected_at, quality):
        return Snapshot(
            id=uuid.UUID(snap_id),
            target_id=uuid.UUID(target_id),
            collected_at=collected_at,
            normalized_json={},
            parser_version="v1",
            quality_state=quality,
        )

    async with session_factory() as session:
        # ── tenants / subscriptions ──
        for tid in (_TENANT_A, _TENANT_B):
            session.add(Tenant(id=uuid.UUID(tid), name="t", status="ACTIVE", created_at=_T0))
        await session.flush()
        for tid in (_TENANT_A, _TENANT_B):
            session.add(
                Subscription(
                    id=uuid.uuid4(), tenant_id=uuid.UUID(tid),
                    plan="basic", status="PAYMENT_ACTIVE", quotas={},
                )
            )
        await session.flush()
        # ── platform_accounts ──
        session.add(PlatformAccount(id=uuid.UUID(_ACC_A_OK), tenant_id=uuid.UUID(_TENANT_A), platform="BAEMIN", label="l", username="vault://u", password="vault://p", auth_state="ACTIVE"))
        session.add(PlatformAccount(id=uuid.UUID(_ACC_A_AUTH), tenant_id=uuid.UUID(_TENANT_A), platform="COUPANG", label="l", username="vault://u", password="vault://p", auth_state="AUTH_REQUIRED"))
        session.add(PlatformAccount(id=uuid.UUID(_ACC_B), tenant_id=uuid.UUID(_TENANT_B), platform="BAEMIN", label="l", username="vault://u", password="vault://p", auth_state="ACTIVE"))
        session.add(Agent(id=uuid.UUID(_AGENT), name="agent-1", machine_id="m", version="1.0.0", os="windows", status="active", last_heartbeat_at=_T0 - timedelta(seconds=30), capacity_json={"max_in_flight": 5, "capabilities": ["CRAWL_BAEMIN", "KAKAO_SEND"]}))
        await session.flush()
        # ── monitoring_targets ──
        def _target(tid, tenant, account, name):
            return MonitoringTarget(id=uuid.UUID(tid), tenant_id=uuid.UUID(tenant), platform_account_id=uuid.UUID(account), name=name, center_name="c", external_id="", url="", interval_minutes=10, status="ACTIVE", next_run_at=None)
        session.add(_target(_T1, _TENANT_A, _ACC_A_OK, "A-target"))
        session.add(_target(_T2, _TENANT_A, _ACC_A_AUTH, "A-auth-target"))
        session.add(_target(_T3, _TENANT_B, _ACC_B, "B-target"))
        session.add(MessengerChannel(id=uuid.UUID(_CHAN_A), tenant_id=uuid.UUID(_TENANT_A), messenger="TELEGRAM", telegram_chat_id="-100fake", thread_id=None, kakao_room_name=None, state="ACTIVE"))
        await session.flush()
        # ── snapshots: T1 OK at -5/-3min, MISSING at -1min(제외 대상) ──
        session.add(_snap("51111111-1111-1111-1111-111111111111", _T1, _T0 - timedelta(minutes=5), "OK"))
        session.add(_snap("52222222-2222-2222-2222-222222222222", _T1, _T0 - timedelta(minutes=3), "OK"))
        session.add(_snap("53333333-3333-3333-3333-333333333333", _T1, _T0 - timedelta(minutes=1), "MISSING_REQUIRED"))
        # T3 snapshot(B) — 격리 확인용
        session.add(_snap("54444444-4444-4444-4444-444444444444", _T3, _T0 - timedelta(minutes=2), "OK"))
        await session.flush()
        # ── messages + delivery_logs(T1): SENT -4/-2min, FAILED TELEGRAM -1min ──
        def _msg(mid, snap_id):
            return Message(id=uuid.UUID(mid), snapshot_id=uuid.UUID(snap_id), template_version="v1", text_hash="h", text_redacted_preview="p")
        session.add(_msg("11111111-aaaa-1111-1111-111111111111", "51111111-1111-1111-1111-111111111111"))
        session.add(_msg("22222222-aaaa-2222-2222-222222222222", "52222222-2222-2222-2222-222222222222"))
        await session.flush()
        def _dlog(did, mid, status, sent_at, error_code=None, dedup=""):
            return DeliveryLog(id=uuid.UUID(did), message_id=uuid.UUID(mid), channel_id=uuid.UUID(_CHAN_A), status=status, dedup_key=dedup, error_code=error_code, sent_at=sent_at)
        session.add(_dlog("d1111111-1111-1111-1111-111111111111", "11111111-aaaa-1111-1111-111111111111", "SENT", _T0 - timedelta(minutes=4), None, "k1"))
        session.add(_dlog("d2222222-2222-2222-2222-222222222222", "22222222-aaaa-2222-2222-222222222222", "SENT", _T0 - timedelta(minutes=2), None, "k2"))
        session.add(_dlog("d3333333-3333-3333-3333-333333333333", "22222222-aaaa-2222-2222-222222222222", "FAILED", _T0 - timedelta(minutes=1), "TELEGRAM_FAILURE", "k3"))
        # ── jobs: crawl FAILED(-6min) + 2 KAKAO_SEND PENDING(run_after -120s/-60s) ──
        session.add(Job(id=uuid.uuid4(), type="CRAWL_BAEMIN", target_id=uuid.UUID(_T1), status="FAILED", error_code="CRAWL_FAILURE", claimed_at=_T0 - timedelta(minutes=6), attempts=1))
        session.add(Job(id=uuid.uuid4(), type=JOB_TYPE_KAKAO_SEND, target_id=uuid.UUID(_T1), status="PENDING", run_after=_T0 - timedelta(seconds=120), attempts=0))
        session.add(Job(id=uuid.uuid4(), type=JOB_TYPE_KAKAO_SEND, target_id=uuid.UUID(_T1), status="PENDING", run_after=_T0 - timedelta(seconds=60), attempts=0))
        # agent 가 claim 한 활성 job(현재 job 표시)
        session.add(Job(id=uuid.uuid4(), type="CRAWL_BAEMIN", target_id=uuid.UUID(_T1), agent_id=uuid.UUID(_AGENT), status="RUNNING", claimed_at=_T0 - timedelta(seconds=10), attempts=1))
        # ── agent(fleet) + browser_profile(T2) + auth_session(A2 pending) ──
        session.add(BrowserProfile(id=uuid.UUID(_PROFILE), agent_id=uuid.UUID(_AGENT), target_id=uuid.UUID(_T2), profile_path_ref="vault://profile", cdp_port=None, state="READY"))
        session.add(AuthSession(id=uuid.uuid4(), account_id=uuid.UUID(_ACC_A_AUTH), state="AUTH_REQUIRED", reason=None, requested_at=_T0 - timedelta(minutes=2), resolved_at=None))
        await session.commit()


def _fresh_pg():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.pool import NullPool

    from rider_server.admin.dashboard_repository_postgres import PostgresDashboardRepository
    from rider_server.db.base import create_engine, create_session_factory

    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DB_URL)
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(_TEST_DB_URL, poolclass=NullPool)
    factory = create_session_factory(engine)
    asyncio.run(_seed(factory))
    repo = PostgresDashboardRepository(factory)

    def _teardown() -> None:
        try:
            command.downgrade(cfg, "base")
        finally:
            asyncio.run(engine.dispose())
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev

    return repo, _teardown


@pytest.fixture
def pg_repo():
    repo, teardown = _fresh_pg()
    try:
        yield repo
    finally:
        teardown()


# ── (1) 파생 집계 ──────────────────────────────────────────────────────────────

@_pg_gate
def test_target_health_derives_last_success_delivery_and_failure(pg_repo) -> None:
    facts = asyncio.run(pg_repo.target_health(tenant_id=_TENANT_A, now=_T0))
    by_id = {f.target_id: f for f in facts}
    t1 = by_id[_T1]
    # 수집 성공 = OK 최신(-3min). MISSING_REQUIRED(-1min)은 제외.
    assert t1.last_success_at == _T0 - timedelta(minutes=3)
    # 전송 성공 = SENT 최신(-2min). FAILED 는 제외.
    assert t1.last_delivery_at == _T0 - timedelta(minutes=2)
    # 최신 실패 = delivery TELEGRAM_FAILURE(-1min) > job CRAWL_FAILURE(-6min).
    assert t1.last_failure_code == "TELEGRAM_FAILURE"
    # 인증 필요 대상의 계정 auth_state 노출.
    assert by_id[_T2].account_auth_state == "AUTH_REQUIRED"


# ── (2) tenant 격리(cross-tenant negative) ─────────────────────────────────────

@_pg_gate
def test_target_health_is_tenant_isolated(pg_repo) -> None:
    facts_a = asyncio.run(pg_repo.target_health(tenant_id=_TENANT_A, now=_T0))
    ids_a = {f.target_id for f in facts_a}
    assert _T1 in ids_a and _T2 in ids_a
    assert _T3 not in ids_a  # tenant B 누출 0
    assert all(f.tenant_id == _TENANT_A for f in facts_a)


# ── (3) 채널 구분 — kakao lag / telegram error ─────────────────────────────────

@_pg_gate
def test_channel_health_separates_kakao_lag_and_telegram_error(pg_repo) -> None:
    health = asyncio.run(pg_repo.channel_health(tenant_id=_TENANT_A, now=_T0))
    # 가장 오래된 대기 KAKAO_SEND run_after(-120s) 기준 lag.
    assert health.kakao_queue_lag_seconds == 120
    # 최근 윈도 TELEGRAM_FAILURE 1건.
    assert health.telegram_error_count == 1
    # tenant B 는 채널/대기 job 없음 → 0.
    health_b = asyncio.run(pg_repo.channel_health(tenant_id=_TENANT_B, now=_T0))
    assert health_b.kakao_queue_lag_seconds == 0
    assert health_b.telegram_error_count == 0


# ── (4) AC4 인증 필요 목록(tenant scope) ───────────────────────────────────────

@_pg_gate
def test_auth_required_lists_tenant_accounts_with_profile(pg_repo) -> None:
    rows = asyncio.run(pg_repo.auth_required(tenant_id=_TENANT_A))
    assert rows, "인증 필요 행이 있어야 한다"
    assert all(r.tenant_id == _TENANT_A for r in rows)
    account_rows = [r for r in rows if r.reason == "ACCOUNT_AUTH_REQUIRED"]
    assert any(r.target_id == _T2 and r.profile_id == _PROFILE for r in account_rows)
    # auth_session 인증대기 행도 도출.
    assert any(r.reason == "AUTH_SESSION_PENDING" for r in rows)


@_pg_gate
def test_auth_required_is_tenant_isolated(pg_repo) -> None:
    rows_b = asyncio.run(pg_repo.auth_required(tenant_id=_TENANT_B))
    # tenant B 계정은 ACTIVE 라 인증 필요 0 — A 데이터 누출 없음.
    assert rows_b == []


# ── agent fleet(tenant 무관 전역) ──────────────────────────────────────────────

@_pg_gate
def test_agent_health_is_fleet_wide(pg_repo) -> None:
    facts = asyncio.run(pg_repo.agent_health(now=_T0))
    by_id = {f.agent_id: f for f in facts}
    assert _AGENT in by_id
    agent = by_id[_AGENT]
    assert agent.current_job_type == "CRAWL_BAEMIN"  # RUNNING job
    assert "KAKAO_SEND" in agent.capabilities
