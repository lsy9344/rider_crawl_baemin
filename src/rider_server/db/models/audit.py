"""audit_logs ORM 모델 — Story 5.2 (AC1·AC2).

domain dataclass 가 없어 data-api-contract Required fields 에서 직접 정의한다.
``actor_id``/``target_id`` 는 admin users 테이블 부재로 **FK 없이** UUID 컬럼으로 둔다
(후속 보안 스토리가 users 도입 시 FK 추가). ``target_id`` 는 다형 참조라 FK 를 걸지 않는다.
``id`` PK 추가(ADD-8), ``diff_redacted`` 는 JSON(Postgres JSONB) — redaction 통과 diff 만.
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, json_variant
from ._columns import ts, uuid_pk


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()  # 계약 required 에 없으나 ADD-8 PK 정본
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # FK 없음(users 부재)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # 다형 참조(FK 없음)
    diff_redacted: Mapped[dict | None] = mapped_column(json_variant(), nullable=True)
    created_at: Mapped[datetime] = ts()
