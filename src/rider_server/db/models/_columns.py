"""ORM 컬럼 헬퍼 — Story 5.2. 14개 모델이 공유하는 PK/FK/시각 컬럼 패턴.

ADD-8 정본: PK 는 UUID ``id``(client-side ``uuid.uuid4`` default — ``gen_random_uuid()``/
pgcrypto 의존 회피), FK 는 ``<entity>_id``(부모 ``id`` 참조), 시각은 timezone-aware
``DateTime(timezone=True)``. 이식 가능 ``Uuid`` 타입(dialect 별 UUID/CHAR(32) 자동).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column


def uuid_pk() -> Mapped[uuid.UUID]:
    """UUID PK ``id`` — client-side ``uuid.uuid4`` default."""
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def fk(target: str, *, nullable: bool = False) -> Mapped[uuid.UUID]:
    """``<entity>_id`` FK 컬럼 — 부모 ``id`` 참조."""
    return mapped_column(Uuid, ForeignKey(target), nullable=nullable)


def ts(*, nullable: bool = False) -> Mapped[datetime]:
    """timezone-aware 시각 컬럼(``_at`` 접미사 — ADD-8)."""
    return mapped_column(DateTime(timezone=True), nullable=nullable)
