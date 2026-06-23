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

import uuid

from sqlalchemy import JSON, MetaData, text
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


# ── transaction-scoped advisory lock (count→insert 류 race 직렬화) ─────────────────
# 잠글 행이 아직 없는 "논리적 중복 생성"(예: 같은 계정에 인증 job 두 개)을 막으려면 row lock 으론
# 부족하다 — 두 트랜잭션이 모두 count 0 을 보고 각각 insert 할 수 있다. Postgres advisory lock
# 은 임의 키를 잠가 그 구간을 직렬화한다(트랜잭션 종료 시 자동 해제). 비-Postgres 백엔드(테스트
# in-memory/SQLite)는 단일 프로세스·직렬 실행이라 락이 불필요해 no-op 으로 둔다.

#: auth job enqueue 직렬화용 advisory lock 네임스페이스(첫 인자). scheduler 자동 복구와 admin 수동
#: 인증이 같은 계정에 대해 **서로도** 직렬화되도록 두 경로가 이 한 값을 공유한다(중복 auth job 0).
#: "RIDE"(1380598853) — signed int4 양수 범위 안의 임의 고정값.
AUTH_ENQUEUE_LOCK_NAMESPACE = 0x52494445


def advisory_lock_key_for_uuid(value: uuid.UUID) -> int:
    """UUID → 결정적 signed 32bit 키(``pg_advisory_xact_lock`` 둘째 인자용).

    int4 둘째 인자는 signed 32bit 라 UUID 128bit 를 그대로 못 넣는다. 하위 31bit 로 좁혀
    양수로 만든다 — 충돌해도 정확성은 유지된다(서로 다른 키가 같은 슬롯을 공유하면 그 둘이 잠깐
    직렬화될 뿐). namespace(첫 인자)로 다른 용도의 advisory lock 과 분리한다.
    """

    return value.int & 0x7FFFFFFF


async def acquire_xact_advisory_lock(
    session: AsyncSession, *, namespace: int, key: int
) -> bool:
    """이 트랜잭션 동안 ``(namespace, key)`` advisory lock 을 잡는다(Postgres 한정).

    트랜잭션 종료(commit/rollback) 시 자동 해제된다(``pg_advisory_xact_lock``). 반환값은 락을
    실제로 잡았는지 — Postgres 면 ``True``, 그 외 dialect 면 no-op ``False``. 락은 정확성을 위한
    직렬화일 뿐이라 비-Postgres 단일 프로세스 테스트에선 생략해도 의미가 보존된다.
    """

    bind = session.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", "") != "postgresql":
        return False
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)").bindparams(
            ns=namespace, key=key
        )
    )
    return True
