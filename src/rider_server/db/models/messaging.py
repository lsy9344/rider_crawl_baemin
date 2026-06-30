"""messenger_channels·delivery_rules·snapshots·messages·delivery_logs ORM 모델 — Story 5.2.

dedup 정본(AC3): ``delivery_logs.dedup_key`` 에 유니크 제약 ``uq_delivery_logs_dedup_key``
(naming_convention 로 결정). dedup key 5차원은 ``dedup_key`` 문자열에 **합성**돼 있으므로
유니크는 단일 컬럼에 건다(축소 금지). dedup 합성·insert-then-send 정책은 services(3.5/3.6)
소유 — 여기는 DB 유니크 제약만 제공한다. JSON 컬럼(``snapshots.normalized_json``)은
Postgres 에선 JSONB. telegram_chat_id/thread_id 는 라우팅 식별자라 secret 아님(ref화 금지).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, json_variant
from ._columns import fk, ts, uuid_pk


class MessengerChannel(Base):
    __tablename__ = "messenger_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_messenger_channels_tenant_id_id"),
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
        Index(
            "uq_messenger_channels_active_kakao_room",
            "tenant_id",
            "kakao_room_name",
            unique=True,
            postgresql_where=text(
                "state = 'ACTIVE' AND messenger = 'KAKAO' AND kakao_room_name IS NOT NULL"
            ),
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
    # 카카오 인바운드 명령 트리거(Phase 3) — 0024 가 additive 로 추가. ``kakao_chat_id`` 는
    # 라우팅 식별자(secret 아님)로, 룸명만 설정된 채널은 첫 인바운드 매칭 시 서버가 바인딩한다.
    # ``command_trigger_enabled`` 가 false(기본)면 그 채널은 명령 트리거를 받지 않는다(opt-in).
    kakao_chat_id: Mapped[str | None] = mapped_column(String, nullable=True)
    command_trigger_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )


class DeliveryRule(Base):
    __tablename__ = "delivery_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "target_id"],
            ["monitoring_targets.tenant_id", "monitoring_targets.id"],
            name="fk_delivery_rules_tenant_target",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["messenger_channels.tenant_id", "messenger_channels.id"],
            name="fk_delivery_rules_tenant_channel",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_id: Mapped[uuid.UUID] = fk("monitoring_targets.id")
    channel_id: Mapped[uuid.UUID] = fk("messenger_channels.id")
    template_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # soft delete
    send_only_on_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        Index("ix_snapshots_target_collected_at_id", "target_id", "collected_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    target_id: Mapped[uuid.UUID] = fk("monitoring_targets.id")
    collected_at: Mapped[datetime] = ts()
    normalized_json: Mapped[dict] = mapped_column(json_variant(), nullable=False, default=dict)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    quality_state: Mapped[str] = mapped_column(String, nullable=False)  # SnapshotQualityState 값


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_snapshot_template_version", "snapshot_id", "template_version"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = fk("snapshots.id")
    template_version: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String, nullable=False)
    text_redacted_preview: Mapped[str] = mapped_column(String, nullable=False)


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"
    # AC3 — dedup_key 유니크 제약 → uq_delivery_logs_dedup_key (naming_convention)
    __table_args__ = (
        UniqueConstraint("dedup_key"),
        Index(
            "ix_delivery_logs_dispatch_claim",
            "status",
            "available_at",
            "locked_at",
        ),
        Index("ix_delivery_logs_channel_message", "channel_id", "message_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = fk("messages.id")
    channel_id: Mapped[uuid.UUID] = fk("messenger_channels.id")
    status: Mapped[str] = mapped_column(String, nullable=False)  # DeliveryStatus 값
    dedup_key: Mapped[str] = mapped_column(String, nullable=False)  # 5차원 합성 idempotency key
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)  # FailureCategory 값(3.6)
    sent_at: Mapped[datetime | None] = ts(nullable=True)
    send_attempted_at: Mapped[datetime | None] = ts(nullable=True)
    last_failed_at: Mapped[datetime | None] = ts(nullable=True)
    available_at: Mapped[datetime | None] = ts(nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = ts(nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
