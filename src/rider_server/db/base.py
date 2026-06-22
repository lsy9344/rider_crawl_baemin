"""rider_server DB 기반 — Story 5.2 (Epic 5 영속 레이어 진입점).

SQLAlchemy 2.x async ORM 의 선언적 ``Base`` 와 결정적 제약 이름(naming_convention),
이식 가능한 JSON 타입, async 엔진/세션 팩토리를 제공한다. DB URL 은 settings/env
(``DATABASE_URL``)에서만 읽고 평문 비밀·하드코딩 URL 을 두지 않는다(NFR-8).

레이어 분리: ``domain/`` 은 순수 frozen dataclass(SQLAlchemy import 0), ``db/`` 가 영속
ORM 이다. domain dataclass 를 ORM 으로 바꾸지 않고 별도 ORM 클래스가 필드를 미러한다.

async 경계: 본 모듈의 async 함수는 ``time.sleep``/``subprocess.*`` 같은 blocking sync 를
직접 호출하지 않는다(tests/server/test_server_async_boundary.py rglob 가드 준수).
"""

from __future__ import annotations

from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import Pool

# ADD-8 DB 네이밍 정본 — 인덱스/유니크/FK/PK 제약 이름을 결정적으로 생성한다.
# (Alembic autogenerate 가 ``uq_delivery_logs_dedup_key`` 같은 이름을 안정적으로 만들도록.)
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """모든 ORM 모델의 선언적 베이스. naming_convention 적용 metadata 를 공유한다."""

    metadata = metadata


def json_variant() -> JSON:
    """JSON 컬럼 타입 — PostgreSQL 에선 JSONB, 그 외 dialect 에선 표준 JSON.

    이식 가능 타입으로 두어 native PG 전용 타입을 모델 레벨에서 강제하지 않는다.
    컬럼마다 새 인스턴스를 돌려준다(타입 객체 공유로 인한 상태 오염 회피).
    """
    return JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def create_engine(
    database_url: str,
    *,
    echo: bool = False,
    poolclass: type[Pool] | None = None,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> AsyncEngine:
    """async 엔진을 만든다. URL 은 호출부(settings/env)가 주입한다(하드코딩 금지)."""
    kwargs: dict[str, object] = {}
    if poolclass is not None:
        kwargs["poolclass"] = poolclass
    else:
        if pool_size is not None:
            kwargs["pool_size"] = pool_size
        if max_overflow is not None:
            kwargs["max_overflow"] = max_overflow
    return create_async_engine(database_url, echo=echo, future=True, **kwargs)


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """async 세션 팩토리. 커밋 후 만료 비활성(detached 객체 접근 안전)."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
