"""rider_server 영속(DB) 레이어 — Story 5.2 (Epic 5 첫 DB 코드).

``base`` 의 선언적 ``Base``/엔진 팩토리와 ``models`` 의 14개 ORM 모델을 묶는다.
``domain/`` 은 순수 frozen dataclass(SQLAlchemy import 0)로 유지하고, 영속 표현은
여기 ``db/`` 의 ORM 클래스가 별도로 든다(레이어 분리). repository/CRUD·INSERT 흐름은
Story 5.3+ 범위이며, 본 패키지는 스키마 정의와 엔진/세션 배선까지만 제공한다.
"""

from __future__ import annotations

from .base import (
    Base,
    create_engine,
    create_session_factory,
    json_variant,
    metadata,
)

__all__ = [
    "Base",
    "metadata",
    "json_variant",
    "create_engine",
    "create_session_factory",
]
