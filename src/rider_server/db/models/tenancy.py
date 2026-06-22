"""tenants·subscriptions ORM 모델 — Story 5.2 (AC1·AC2).

domain ``Tenant``/``Subscription`` 필드를 영속 컬럼으로 미러한다(공개 경계 호환).
status 류는 String(대문자 enum 문자열) — native PG ENUM 금지(ADD-8/NFR-8). 값 검증은
service/Pydantic 경계 소유(여기는 컬럼만). ``subscriptions`` 는 계약 required fields 에
없는 ``id`` PK 를 추가한다(ADD-8 PK 정본).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, json_variant
from ._columns import fk, ts, uuid_pk


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # CustomerLifecycleState 값
    created_at: Mapped[datetime] = ts()
    # tenant 별 텔레그램 설정(0012) — 봇 토큰/webhook secret 은 평문 저장(0011 선례), redaction 으로
    # 로그/응답 마스킹. sending_enabled 는 fail-closed 기본 OFF(실발송 게이트의 tenant 스코프).
    telegram_bot_token: Mapped[str] = mapped_column(String, nullable=False, default="")
    telegram_webhook_secret: Mapped[str] = mapped_column(String, nullable=False, default="")
    sending_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()  # 계약 required 에 없으나 ADD-8 PK 정본
    tenant_id: Mapped[uuid.UUID] = fk("tenants.id")
    plan: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # SubscriptionStatus 값
    current_period_end: Mapped[datetime | None] = ts(nullable=True)
    quotas: Mapped[dict] = mapped_column(json_variant(), nullable=False, default=dict)
