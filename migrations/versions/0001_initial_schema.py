"""initial schema — 14 tables (Story 5.2 / AC1·AC2·AC3, P4-02)

빈 PostgreSQL DB 에서 ``data-api-contract`` Required Tables 14개를 재현한다.
FK 의존성 순서(부모 먼저)로 생성하고 downgrade 는 역순으로 전부 drop 한다(round-trip).
제약 이름은 ``Base.metadata`` naming_convention(ADD-8)과 일치(``op.f`` 로 고정) —
``uq_delivery_logs_dedup_key`` 가 dedup 재시도를 IntegrityError 로 차단한다(AC3).
status/type 류는 String(native PG ENUM 금지), secret 은 ``*_ref`` 컬럼만(평문 0, NFR-8).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    """JSON 컬럼 — Postgres 에선 JSONB, 그 외 표준 JSON(모델 ``json_variant`` 미러)."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # ── FK 무의존(루트) ────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("machine_id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("os", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capacity_json", _json(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),  # FK 없음(users 부재)
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),  # 다형 참조(FK 없음)
        sa.Column("diff_redacted", _json(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )

    # ── tenants 자식 ──────────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quotas", _json(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_subscriptions_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
    )
    op.create_table(
        "platform_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("username_ref", sa.String(), nullable=False),  # SecretRef 핸들(평문 아님)
        sa.Column("password_ref", sa.String(), nullable=False),  # SecretRef 핸들(평문 아님)
        sa.Column("auth_state", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_platform_accounts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_accounts")),
    )
    op.create_table(
        "messenger_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("messenger", sa.String(), nullable=False),
        sa.Column("telegram_chat_id", sa.String(), nullable=True),  # 라우팅 식별자(secret 아님)
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("kakao_room_name", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_messenger_channels_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messenger_channels")),
    )

    # ── platform_accounts 자식 ────────────────────────────────────────
    op.create_table(
        "monitoring_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("center_name", sa.String(), nullable=False),  # FR-20 기대 센터/상점명
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_monitoring_targets_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id"],
            ["platform_accounts.id"],
            name=op.f("fk_monitoring_targets_platform_account_id_platform_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitoring_targets")),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),  # 계약 필드명 그대로
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform_accounts.id"],
            name=op.f("fk_auth_sessions_account_id_platform_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )

    # ── agents + monitoring_targets 자식 ──────────────────────────────
    op.create_table(
        "browser_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("profile_path_ref", sa.String(), nullable=False),  # ref(raw 경로 아님)
        sa.Column("cdp_port", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_browser_profiles_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["monitoring_targets.id"],
            name=op.f("fk_browser_profiles_target_id_monitoring_targets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_browser_profiles")),
    )
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized_json", _json(), nullable=False),
        sa.Column("parser_version", sa.String(), nullable=False),
        sa.Column("quality_state", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["monitoring_targets.id"],
            name=op.f("fk_snapshots_target_id_monitoring_targets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snapshots")),
    )
    op.create_table(
        "delivery_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("send_only_on_change", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["monitoring_targets.id"],
            name=op.f("fk_delivery_rules_target_id_monitoring_targets"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["messenger_channels.id"],
            name=op.f("fk_delivery_rules_channel_id_messenger_channels"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_rules")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),  # claim 전엔 미할당
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["monitoring_targets.id"],
            name=op.f("fk_jobs_target_id_monitoring_targets"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_jobs_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )

    # ── snapshots 자식 ────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("template_version", sa.String(), nullable=False),
        sa.Column("text_hash", sa.String(), nullable=False),
        sa.Column("text_redacted_preview", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshots.id"], name=op.f("fk_messages_snapshot_id_snapshots")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )

    # ── messages + messenger_channels 자식 (dedup UNIQUE) ─────────────
    op.create_table(
        "delivery_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("dedup_key", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name=op.f("fk_delivery_logs_message_id_messages")
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["messenger_channels.id"],
            name=op.f("fk_delivery_logs_channel_id_messenger_channels"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_logs")),
        sa.UniqueConstraint("dedup_key", name=op.f("uq_delivery_logs_dedup_key")),
    )


def downgrade() -> None:
    # 의존성 역순 drop — 빈 DB 로 깨끗이 round-trip(AC3).
    op.drop_table("delivery_logs")
    op.drop_table("messages")
    op.drop_table("jobs")
    op.drop_table("delivery_rules")
    op.drop_table("snapshots")
    op.drop_table("browser_profiles")
    op.drop_table("auth_sessions")
    op.drop_table("monitoring_targets")
    op.drop_table("messenger_channels")
    op.drop_table("platform_accounts")
    op.drop_table("subscriptions")
    op.drop_table("audit_logs")
    op.drop_table("agents")
    op.drop_table("tenants")
