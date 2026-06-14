"""Story 5.5 / AC3 — messenger_channels 활성 (chat_id, thread_id) 부분 유니크 (PostgreSQL-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) 활성(``state='ACTIVE'``) 채널 간 같은 ``(telegram_chat_id, thread_id)`` 2회 INSERT 가
      ``uq_messenger_channels_active_telegram_topic`` 부분 유니크로 ``IntegrityError`` 차단.
  (2) **부분** 유니크라 PENDING 중복 ``(chat_id, thread_id)`` 는 허용(재등록/soft-delete 충돌 회피).
  (3) ``upgrade``/``downgrade`` round-trip(0004 컬럼+인덱스가 정확히 추가·제거).

**SQLite 로 Postgres 부분 유니크 의미를 대체하지 않는다**(NULL/부분 술어 의미 차이 오탐) — 현
WSL/venv 에 Postgres 부재 시 전부 skip 하고, always-run ``tests/server/test_channel_lifecycle.py``
가 활성 충돌 정책(``assert_unique_telegram_topics`` 재사용)을 결정적으로 잠근다.

PG fixture 는 **유효 UUID + 부모 행 시드**(5.3 HIGH-1 교훈 — 비-UUID/미시드 FK 는 실행 즉시
에러): tenants 1행. fake 값만(실제 토큰/전화/이메일/chat_id 형태 없음).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
_pg_gate = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재(현 WSL/venv). always-run 정책 테스트로 잠금.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OFFLINE_PG_URL = "postgresql://alembic:offline@localhost/offline"


# ── always-run(DB 불필요): 0004 offline 렌더 — 부분 유니크 술어 의미 잠금 ────────────
# PG-gated 온라인 테스트는 CI 에서 skip 돼 ``WHERE state='ACTIVE'`` 부분 술어(설계의 핵심)가
# always-run 가드 없이 남는다(memory/pg-gated-files-hide-pure-helpers). Postgres dialect 로
# 0004 SQL 을 **연결 없이** 렌더해 부분 유니크 인덱스 + 컬럼/round-trip 을 CI 에서도 잠근다.

_INDEX_NAME = "uq_messenger_channels_active_telegram_topic"


def _alembic_cfg():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _OFFLINE_PG_URL)
    return cfg


def _render_offline(revision_range: str) -> str:
    from alembic import command

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if ":" in revision_range:
            command.downgrade(_alembic_cfg(), revision_range, sql=True)
        else:
            command.upgrade(_alembic_cfg(), revision_range, sql=True)
    return buf.getvalue()


def test_0004_offline_upgrade_renders_partial_unique_on_active_only():
    sql = _render_offline("head")
    # 등록 코드 컬럼이 additive 로 추가된다.
    assert "registration_code" in sql
    # 활성 채널만 (chat_id, thread_id) 유일 — 부분 유니크 인덱스 + WHERE state='ACTIVE' 술어.
    assert f"CREATE UNIQUE INDEX {_INDEX_NAME}" in sql
    assert "messenger_channels" in sql
    assert "telegram_chat_id" in sql and "thread_id" in sql
    assert "WHERE state = 'ACTIVE'" in sql  # 전역 유니크 아님(PENDING/INACTIVE 중복 허용)


def test_0004_offline_downgrade_round_trip_drops_index_and_column():
    sql = _render_offline(
        "0004_messenger_channel_registration:0003_monitoring_targets_scheduling"
    )
    assert f"DROP INDEX {_INDEX_NAME}" in sql
    assert "DROP COLUMN registration_code" in sql


# ── 이하 PG-gated 온라인(실 Postgres 필요, @_pg_gate) ──────────────────────────────
_T0 = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_FAKE_CHAT = "-100fake"


def _fresh_pg():
    """빈 PG 에 0001~0004 적용 후 (engine, session_factory, teardown)."""
    from alembic import command
    from alembic.config import Config

    from rider_server.db.base import create_engine, create_session_factory

    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DB_URL)
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(_TEST_DB_URL)
    factory = create_session_factory(engine)

    async def _seed_tenant() -> None:
        from rider_server.db.models.tenancy import Tenant

        async with factory() as session:
            session.add(
                Tenant(id=uuid.UUID(_TENANT), name="t", status="ACTIVE", created_at=_T0)
            )
            await session.commit()

    asyncio.run(_seed_tenant())

    def _teardown() -> None:
        try:
            command.downgrade(cfg, "base")
        finally:
            asyncio.run(engine.dispose())
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev

    return cfg, factory, _teardown


@pytest.fixture
def pg_env():
    cfg, factory, teardown = _fresh_pg()
    try:
        yield cfg, factory
    finally:
        teardown()


def _channel_row(*, chat_id: str, thread_id: str | None, state: str):
    from rider_server.db.models.messaging import MessengerChannel

    return MessengerChannel(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(_TENANT),
        messenger="TELEGRAM",
        telegram_chat_id=chat_id,
        thread_id=thread_id,
        state=state,
    )


@_pg_gate
def test_active_duplicate_chat_thread_blocked_by_partial_unique(pg_env):
    from sqlalchemy.exc import IntegrityError

    _cfg, factory = pg_env

    async def _run():
        async with factory() as s:
            s.add(_channel_row(chat_id=_FAKE_CHAT, thread_id="7", state="ACTIVE"))
            await s.commit()
        # 같은 (chat_id, thread_id) 활성 채널 2번째 → 부분 유니크 위반.
        with pytest.raises(IntegrityError):
            async with factory() as s:
                s.add(_channel_row(chat_id=_FAKE_CHAT, thread_id="7", state="ACTIVE"))
                await s.commit()

    asyncio.run(_run())


@_pg_gate
def test_pending_duplicate_chat_thread_allowed(pg_env):
    _cfg, factory = pg_env

    async def _run():
        # 부분 유니크는 WHERE state='ACTIVE' 라 PENDING 중복은 허용(재등록 충돌 회피).
        async with factory() as s:
            s.add(_channel_row(chat_id=_FAKE_CHAT, thread_id="7", state="PENDING"))
            s.add(_channel_row(chat_id=_FAKE_CHAT, thread_id="7", state="PENDING"))
            await s.commit()

    asyncio.run(_run())


@_pg_gate
def test_upgrade_downgrade_round_trip(pg_env):
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    cfg, _factory = pg_env
    from alembic import command

    async def _has_column_and_index() -> tuple[bool, bool]:
        engine = create_async_engine(_TEST_DB_URL)
        try:
            async with engine.connect() as conn:
                cols = await conn.run_sync(
                    lambda sc: {c["name"] for c in sa.inspect(sc).get_columns("messenger_channels")}
                )
                idx = await conn.run_sync(
                    lambda sc: {i["name"] for i in sa.inspect(sc).get_indexes("messenger_channels")}
                )
            return ("registration_code" in cols, "uq_messenger_channels_active_telegram_topic" in idx)
        finally:
            await engine.dispose()

    has_col, has_idx = asyncio.run(_has_column_and_index())
    assert has_col and has_idx  # 0004 upgrade 적용됨

    # 0003 으로 downgrade → 0004 가 추가한 컬럼·인덱스가 정확히 제거(round-trip).
    command.downgrade(cfg, "0003_monitoring_targets_scheduling")
    has_col2, has_idx2 = asyncio.run(_has_column_and_index())
    assert not has_col2 and not has_idx2
