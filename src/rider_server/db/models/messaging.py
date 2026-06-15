"""messenger_channels·delivery_rules·snapshots·messages·delivery_logs ORM 모델 — Story 5.2.

dedup 정본(AC3): ``delivery_logs.dedup_key`` 에 유니크 제약 ``uq_delivery_logs_dedup_key``
(naming_convention 로 결정). dedup key 5차원은 ``dedup_key`` 문자열에 **합성**돼 있으므로
유니크는 단일 컬럼에 건다(축소 금지). dedup 합성·insert-then-send 정책은 services(3.5/3.6)
소유 — 여기는 DB 유니크 제약만 제공한다. JSON 컬럼(``snapshots.normalized_json``)은
Postgres 에선 JSONB. telegram_chat_id/thread_id 는 라우팅 식별자라 secret 아님(ref화 금지).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, json_variant
from ._columns import fk, ts, uuid_pk


class MessengerChannel(Base):
    __tablename__ = "messenger_channels"
    __table_args__ = (
        Index(
            "uq_messenger_channels_active_telegram_topic",
            "telegram_chat_id",
            "thread_id",
            unique=True,
            postgresql_where=text("state = 'ACTIVE' AND thread_id IS NOT NULL"),
        ),
        Index(
            "uq_messenger_channels_active_telegram_general",
            "telegram_chat_id",
            unique=True,
            postgresql_where=text("state = 'ACTIVE' AND thread_id IS NULL"),
        ),
        Index(
            "uq_messenger_channels_registration_code",
            "registration_code",
            unique=True,
            postgresql_where=text("registration_code IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = fk("tenants.id")
    messenger: Mapped[str] = mapped_column(String, nullable=False)  # Messenger 값
    telegram_chat_id: Mapped[str | None] = mapped_column(String, nullable=True)  # 라우팅 식별자(secret 아님)
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True)  # 라우팅 식별자(secret 아님)
    kakao_room_name: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False)  # MessengerChannelState 값
    # Story 5.5: 운영자가 PENDING 채널 사전 생성 시 부여하는 1회용 등록 코드(라우팅/운영용 —
    # secret 아님). 고객 ``/register <code>`` → webhook 이 telegram_chat_id/thread_id 를 채운다.
    # 0004 가 additive 로 추가(nullable). 활성 (chat_id, thread_id) 부분 유니크도 0004 가 건다.
    registration_code: Mapped[str | None] = mapped_column(String, nullable=True)


class DeliveryRule(Base):
    __tablename__ = "delivery_rules"

    id: Mapped[uuid.UUID] = uuid_pk()
    target_id: Mapped[uuid.UUID] = fk("monitoring_targets.id")
    channel_id: Mapped[uuid.UUID] = fk("messenger_channels.id")
    template_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # soft delete
    send_only_on_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    target_id: Mapped[uuid.UUID] = fk("monitoring_targets.id")
    collected_at: Mapped[datetime] = ts()
    normalized_json: Mapped[dict] = mapped_column(json_variant(), nullable=False, default=dict)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    quality_state: Mapped[str] = mapped_column(String, nullable=False)  # SnapshotQualityState 값


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = fk("snapshots.id")
    template_version: Mapped[str] = mapped_column(String, nullable=False)
    text_hash: Mapped[str] = mapped_column(String, nullable=False)
    text_redacted_preview: Mapped[str] = mapped_column(String, nullable=False)


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"
    # AC3 — dedup_key 유니크 제약 → uq_delivery_logs_dedup_key (naming_convention)
    __table_args__ = (UniqueConstraint("dedup_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = fk("messages.id")
    channel_id: Mapped[uuid.UUID] = fk("messenger_channels.id")
    status: Mapped[str] = mapped_column(String, nullable=False)  # DeliveryStatus 값
    dedup_key: Mapped[str] = mapped_column(String, nullable=False)  # 5차원 합성 idempotency key
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)  # FailureCategory 값(3.6)
    sent_at: Mapped[datetime | None] = ts(nullable=True)
