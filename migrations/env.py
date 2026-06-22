"""Alembic 마이그레이션 환경 — Story 5.2 (async 템플릿).

이 파일은 ``migrations/`` 아래라 rider_server async 경계 가드(전 ``rider_server/**`` rglob)
스코프 **밖**이므로 ``asyncio.run``/``connection.run_sync`` 를 자유롭게 쓴다. DB URL 은
ini 평문이 아니라 env(``DATABASE_URL``) 또는 프로그램 주입(config ``sqlalchemy.url``)에서
읽는다(NFR-8). ``target_metadata`` 는 14개 모델 import 후 ``Base.metadata`` 다(누락 감지).

offline(``--sql``)·online(async) 양쪽을 지원한다. offline 은 인프라 없이 전체 스키마
재현 SQL 을 생성하고(Task 5b 가드), online 은 실제 빈 Postgres 에 적용한다(Task 5c gated).
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# repo 루트의 src 를 path 에 올려 CLI(`alembic`)·프로그램 주입 양쪽에서 rider_server import.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rider_server.db import models  # noqa: E402,F401  (import 만으로 Base.metadata 등록)
from rider_server.db.base import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # 로깅 설정 부재는 마이그레이션을 막지 않는다.
        pass

target_metadata = Base.metadata


def _database_url() -> str:
    """DB URL 을 config(프로그램 주입) → env(DATABASE_URL) 순으로 해결한다."""
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    raise RuntimeError(
        "DATABASE_URL 또는 config sqlalchemy.url 이 필요합니다 — 평문 URL 하드코딩 금지(NFR-8)."
    )


def run_migrations_offline() -> None:
    """오프라인(``--sql``) — 연결 없이 DDL 을 렌더한다(dialect 는 URL 에서 결정)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """온라인 — async 엔진으로 연결해 ``run_sync`` 로 동기 마이그레이션을 실행한다."""
    connectable = create_async_engine(_database_url())
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
