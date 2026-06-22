"""Story 5.9 / AC1 — PostgreSQL MetricsRepository fleet 집계·비식별(PG-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) crawl 실패율 윈도(scheduler 정본 재사용) — 플랫폼별 (total, failures) fleet 집계.
  (2) kakao lag fleet 최댓값 — **cross-tenant 합산이 식별정보 없이 fleet 수치로만** 나온다.
  (3) telegram 10분 윈도 fleet 카운트, (4) auth_required fleet 카운트, (5) gmail reauth 근사.

always-run 단위(``test_metrics_policy.py``·``test_metrics_service.py``)가 임계·조립·비식별
의미를 잠그고, 이 파일은 SQL 파생 집계·fleet 합산만 PG 로 확인한다. 시각은 주입(repo 메서드
``now``)해 결정적이다. fake 값만 — 실제 토큰/전화/이메일/chat_id 형태 없음.
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

_TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_ACC_A_BAEMIN = "a1111111-1111-1111-1111-111111111111"  # ACTIVE
_ACC_A_COUPANG = "a2222222-2222-2222-2222-222222222222"  # AUTH_REQUIRED
_ACC_B_BAEMIN = "b1111111-1111-1111-1111-111111111111"  # AUTH_REQUIRED
_ACC_B_COUPANG = "b2222222-2222-2222-2222-222222222222"  # ACTIVE
_T_A1 = "11111111-1111-1111-1111-111111111111"  # tenant A active
_T_B1 = "22222222-2222-2222-2222-222222222222"  # tenant B active
_T_A_INACTIVE = "33333333-3333-3333-3333-333333333333"  # 제외 대상
_AGENT_1 = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_AGENT_2 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_CHAN_A = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_CHAN_B = "ffffffff-ffff-ffff-ffff-ffffffffffff"


async def _seed(session_factory) -> None:
    from rider_server.db.models.account import (
        AuthSession,
        MonitoringTarget,
        PlatformAccount,
    )
    from rider_server.db.models.agent import Agent, Job
    from rider_server.db.models.messaging import (
        DeliveryLog,
        Message,
        MessengerChannel,
        Snapshot,
    )
    from rider_server.db.models.tenancy import Tenant
    from rider_server.queue.states import JOB_TYPE_KAKAO_SEND

    def _acc(acc_id, tenant, platform, auth_state):
        return PlatformAccount(
            id=uuid.UUID(acc_id), tenant_id=uuid.UUID(tenant), platform=platform,
            label="l", username="vault://u", password="vault://p",
            auth_state=auth_state,
        )

    def _target(tid, tenant, account, status):
        return MonitoringTarget(
            id=uuid.UUID(tid), tenant_id=uuid.UUID(tenant),
            platform_account_id=uuid.UUID(account), name="n", center_name="c",
            external_id="", url="", interval_minutes=10, status=status, next_run_at=None,
        )

    def _snap(snap_id, target_id, collected_at, quality):
        return Snapshot(
            id=uuid.UUID(snap_id), target_id=uuid.UUID(target_id),
            collected_at=collected_at, normalized_json={}, parser_version="v1",
            quality_state=quality,
        )

    async with session_factory() as session:
        for tid in (_TENANT_A, _TENANT_B):
            session.add(Tenant(id=uuid.UUID(tid), name="t", status="ACTIVE", created_at=_T0))
        await session.flush()
        session.add(_acc(_ACC_A_BAEMIN, _TENANT_A, "BAEMIN", "ACTIVE"))
        session.add(_acc(_ACC_A_COUPANG, _TENANT_A, "COUPANG", "AUTH_REQUIRED"))
        session.add(_acc(_ACC_B_BAEMIN, _TENANT_B, "BAEMIN", "AUTH_REQUIRED"))
        session.add(_acc(_ACC_B_COUPANG, _TENANT_B, "COUPANG", "ACTIVE"))
        session.add(Agent(id=uuid.UUID(_AGENT_1), name="a1", machine_id="m", version="1.0.0", os="windows", status="active", last_heartbeat_at=_T0 - timedelta(seconds=30), capacity_json={}))
        session.add(Agent(id=uuid.UUID(_AGENT_2), name="a2", machine_id="m", version="1.0.0", os="windows", status="active", last_heartbeat_at=_T0 - timedelta(minutes=10), capacity_json={}))
        await session.flush()
        session.add(_target(_T_A1, _TENANT_A, _ACC_A_BAEMIN, "ACTIVE"))
        session.add(_target(_T_B1, _TENANT_B, _ACC_B_BAEMIN, "ACTIVE"))
        session.add(_target(_T_A_INACTIVE, _TENANT_A, _ACC_A_BAEMIN, "INACTIVE"))
        session.add(MessengerChannel(id=uuid.UUID(_CHAN_A), tenant_id=uuid.UUID(_TENANT_A), messenger="TELEGRAM", telegram_chat_id="-100a", thread_id=None, kakao_room_name=None, state="ACTIVE"))
        session.add(MessengerChannel(id=uuid.UUID(_CHAN_B), tenant_id=uuid.UUID(_TENANT_B), messenger="TELEGRAM", telegram_chat_id="-100b", thread_id=None, kakao_room_name=None, state="ACTIVE"))
        await session.flush()
        # freshness: T_A1 OK -3min(+MISSING -1min 제외), T_B1 OK -50min(CRITICAL 후보).
        session.add(_snap("51111111-1111-1111-1111-111111111111", _T_A1, _T0 - timedelta(minutes=3), "OK"))
        session.add(_snap("52222222-2222-2222-2222-222222222222", _T_A1, _T0 - timedelta(minutes=1), "MISSING_REQUIRED"))
        session.add(_snap("53333333-3333-3333-3333-333333333333", _T_B1, _T0 - timedelta(minutes=50), "OK"))
        await session.flush()
        # messages on existing snapshots
        session.add(Message(id=uuid.UUID("61111111-1111-1111-1111-111111111111"), snapshot_id=uuid.UUID("51111111-1111-1111-1111-111111111111"), template_version="v1", text="p", text_hash="h", text_redacted_preview="p"))
        session.add(Message(id=uuid.UUID("62222222-2222-2222-2222-222222222222"), snapshot_id=uuid.UUID("53333333-3333-3333-3333-333333333333"), template_version="v1", text="p", text_hash="h", text_redacted_preview="p"))
        await session.flush()

        def _dlog(did, mid, chan, status, last_failed_at, error_code, dedup):
            return DeliveryLog(
                id=uuid.UUID(did),
                message_id=uuid.UUID(mid),
                channel_id=uuid.UUID(chan),
                status=status,
                dedup_key=dedup,
                error_code=error_code,
                sent_at=None,
                last_failed_at=last_failed_at,
            )

        # telegram 오류: A -2min(윈도내), B -1min(윈도내), A -20min(윈도밖 제외).
        session.add(_dlog("71111111-1111-1111-1111-111111111111", "61111111-1111-1111-1111-111111111111", _CHAN_A, "FAILED", _T0 - timedelta(minutes=2), "TELEGRAM_FAILURE", "k1"))
        session.add(_dlog("72222222-2222-2222-2222-222222222222", "62222222-2222-2222-2222-222222222222", _CHAN_B, "FAILED", _T0 - timedelta(minutes=1), "TELEGRAM_FAILURE", "k2"))
        session.add(_dlog("73333333-3333-3333-3333-333333333333", "61111111-1111-1111-1111-111111111111", _CHAN_A, "FAILED", _T0 - timedelta(minutes=20), "TELEGRAM_FAILURE", "k3"))
        # crawl jobs(claimed_at -5min, 윈도 15분 내): BAEMIN total=3/fail=2(A FAILED+SUCCEEDED, B FAILED), COUPANG total=1/fail=0.
        def _job(jtype, target, status, claimed_at=None, run_after=None, error_code=None, agent=None):
            return Job(id=uuid.uuid4(), type=jtype, target_id=uuid.UUID(target), status=status, error_code=error_code, claimed_at=claimed_at, run_after=run_after, attempts=1, agent_id=uuid.UUID(agent) if agent else None)
        session.add(_job("CRAWL_BAEMIN", _T_A1, "FAILED", claimed_at=_T0 - timedelta(minutes=5), error_code="CRAWL_FAILURE"))
        session.add(_job("CRAWL_BAEMIN", _T_A1, "SUCCEEDED", claimed_at=_T0 - timedelta(minutes=5)))
        session.add(_job("CRAWL_BAEMIN", _T_B1, "FAILED", claimed_at=_T0 - timedelta(minutes=4), error_code="CRAWL_FAILURE"))
        session.add(_job("CRAWL_COUPANG", _T_B1, "SUCCEEDED", claimed_at=_T0 - timedelta(minutes=5)))
        # kakao queue lag fleet: A run_after -90s, B run_after -200s → fleet 최댓값 200s.
        session.add(_job(JOB_TYPE_KAKAO_SEND, _T_A1, "PENDING", run_after=_T0 - timedelta(seconds=90)))
        session.add(_job(JOB_TYPE_KAKAO_SEND, _T_B1, "PENDING", run_after=_T0 - timedelta(seconds=200)))
        # auth_sessions: A_COUPANG 미해결(gmail 근사 카운트), B_BAEMIN 미해결(BAEMIN→제외), B_COUPANG 해결(제외).
        session.add(AuthSession(id=uuid.uuid4(), account_id=uuid.UUID(_ACC_A_COUPANG), state="AUTH_REQUIRED", reason=None, requested_at=_T0 - timedelta(minutes=2), resolved_at=None))
        session.add(AuthSession(id=uuid.uuid4(), account_id=uuid.UUID(_ACC_B_BAEMIN), state="AUTH_REQUIRED", reason=None, requested_at=_T0 - timedelta(minutes=2), resolved_at=None))
        session.add(AuthSession(id=uuid.uuid4(), account_id=uuid.UUID(_ACC_B_COUPANG), state="AUTH_VERIFIED", reason=None, requested_at=_T0 - timedelta(minutes=5), resolved_at=_T0 - timedelta(minutes=4)))
        await session.commit()


def _fresh_pg():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.metrics.repository_postgres import PostgresMetricsRepository

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
    repo = PostgresMetricsRepository(factory)

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


# ── (1) crawl 실패율 윈도(scheduler 정본 재사용) — 플랫폼별 fleet 집계 ────────

@_pg_gate
def test_crawl_windows_aggregate_per_platform_fleet(pg_repo) -> None:
    since = _T0 - timedelta(minutes=15)
    facts = asyncio.run(pg_repo.crawl_windows(since=since, now=_T0))
    by_platform = {f.platform: f for f in facts}
    # BAEMIN: A FAILED+SUCCEEDED + B FAILED(cross-tenant 합) = total 3, failures 2.
    assert by_platform["BAEMIN"].total == 3
    assert by_platform["BAEMIN"].failures == 2
    # COUPANG: SUCCEEDED 1 = total 1, failures 0.
    assert by_platform["COUPANG"].total == 1
    assert by_platform["COUPANG"].failures == 0


# ── (2) kakao lag fleet 최댓값(cross-tenant — 식별정보 없이 수치만) ────────────

@_pg_gate
def test_kakao_queue_lag_is_fleet_max_across_tenants(pg_repo) -> None:
    lag = asyncio.run(pg_repo.kakao_queue_lag_seconds(now=_T0))
    # 가장 오래된 대기 KAKAO_SEND run_after 는 tenant B 의 -200s → fleet lag 200(tenant scope 아님).
    assert lag == 200


# ── (3) telegram 10분 윈도 fleet 카운트 ────────────────────────────────────────

@_pg_gate
def test_telegram_error_count_is_fleet_windowed(pg_repo) -> None:
    since = _T0 - timedelta(minutes=10)
    count = asyncio.run(pg_repo.telegram_error_count(since=since, now=_T0))
    # A(-2min) + B(-1min) = 2. -20min 은 윈도 밖 제외(cross-tenant 합).
    assert count == 2


# ── (4) auth_required fleet 카운트 ─────────────────────────────────────────────

@_pg_gate
def test_auth_required_count_is_fleet_wide(pg_repo) -> None:
    count = asyncio.run(pg_repo.auth_required_count())
    # A_COUPANG(AUTH_REQUIRED) + B_BAEMIN(AUTH_REQUIRED) = 2(cross-tenant fleet 합).
    assert count == 2


# ── (5) gmail reauth 근사(쿠팡 미해결 auth_session) ────────────────────────────

@_pg_gate
def test_gmail_reauth_count_approximates_coupang_unresolved(pg_repo) -> None:
    count = asyncio.run(pg_repo.gmail_reauth_required_count())
    # A_COUPANG 미해결만 카운트. B_BAEMIN(BAEMIN) 제외, B_COUPANG(해결) 제외 → 1.
    assert count == 1


# ── 파생 집계: heartbeat / freshness(이름·target_id 미노출, 비식별) ────────────

@_pg_gate
def test_agent_heartbeats_are_fleet_wide(pg_repo) -> None:
    facts = asyncio.run(pg_repo.agent_heartbeats(now=_T0))
    assert len(facts) == 2  # fleet 전체 agent
    # facts 는 heartbeat 시각만 — 이름/id 필드 없음(비식별).
    from dataclasses import fields

    assert {f.name for f in fields(type(facts[0]))} == {"last_heartbeat_at"}


@_pg_gate
def test_target_freshness_excludes_inactive_and_uses_ok_only(pg_repo) -> None:
    facts = asyncio.run(pg_repo.target_freshness(now=_T0))
    # 활성 2개만(INACTIVE 제외). 이름/target_id 노출 없이 interval+last_success 만.
    assert len(facts) == 2
    last_successes = sorted(
        (f.last_success_at for f in facts), key=lambda d: d or _T0
    )
    # T_B1 OK -50min, T_A1 OK -3min(MISSING -1min 은 제외).
    assert last_successes == [_T0 - timedelta(minutes=50), _T0 - timedelta(minutes=3)]
    from dataclasses import fields

    assert {f.name for f in fields(type(facts[0]))} == {"interval_minutes", "last_success_at"}
