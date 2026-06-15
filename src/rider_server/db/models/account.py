"""platform_accounts·monitoring_targets·auth_sessions ORM 모델 — Story 5.2 (AC1·AC2).

secret 평문 금지(NFR-8): ``username_ref``/``password_ref`` 와 쿠팡 인증 메일 ref만 둔다 — 평문
``password``/``username``/``token`` 컬럼 0. ``monitoring_targets.center_name`` 은 domain
공개 경계 필드라 보존한다(FR-20 기대 센터/상점명 검증 정본). ``auth_sessions`` 는 domain
dataclass 가 없어 data-api-contract Required fields 에서 직접 정의하며, **계약 필드명
``account_id`` 를 그대로** 쓴다(``platform_account_id`` 로 바꾸지 않는다).
"""

import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._columns import fk, ts, uuid_pk


class PlatformAccount(Base):
    __tablename__ = "platform_accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = fk("tenants.id")
    platform: Mapped[str] = mapped_column(String, nullable=False)  # Platform 값
    label: Mapped[str] = mapped_column(String, nullable=False)
    username_ref: Mapped[str] = mapped_column(String, nullable=False)  # SecretRef 핸들(평문 아님)
    password_ref: Mapped[str] = mapped_column(String, nullable=False)  # SecretRef 핸들(평문 아님)
    verification_email_address_ref: Mapped[str] = mapped_column(String, nullable=False, default="")
    verification_email_app_password_ref: Mapped[str] = mapped_column(String, nullable=False, default="")
    verification_email_subject_keyword: Mapped[str] = mapped_column(String, nullable=False, default="인증번호")
    verification_email_sender_keyword: Mapped[str] = mapped_column(String, nullable=False, default="coupang")
    auth_state: Mapped[str] = mapped_column(String, nullable=False)  # BaeminAuthState 값


class MonitoringTarget(Base):
    __tablename__ = "monitoring_targets"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = fk("tenants.id")
    platform_account_id: Mapped[uuid.UUID] = fk("platform_accounts.id")
    name: Mapped[str] = mapped_column(String, nullable=False)  # 표시명(2.3 display_name)
    center_name: Mapped[str] = mapped_column(String, nullable=False)  # 기대 센터/상점명(FR-20)
    external_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    url: Mapped[str] = mapped_column(String, nullable=False, default="")
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False)  # MonitoringTargetStatus 값
    # ── 5.4 스케줄링 컬럼(additive, 0003 마이그레이션) ──────────────────────────
    # due 질의/멱등 전진용. null=즉시 due 또는 미초기화(5.4 scheduler 가 conditional UPDATE 로 전진).
    next_run_at: Mapped[datetime | None] = ts(nullable=True)
    last_enqueued_at: Mapped[datetime | None] = ts(nullable=True)  # 멱등/가시성

    # due 스캔(next_run_at <= now) 최소화 — scheduler tick 성능.
    __table_args__ = (Index("ix_monitoring_targets_next_run_at", "next_run_at"),)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = fk("platform_accounts.id")  # 계약 필드명 그대로
    state: Mapped[str] = mapped_column(String, nullable=False)  # BaeminAuthState 값
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_at: Mapped[datetime] = ts()
    resolved_at: Mapped[datetime | None] = ts(nullable=True)
