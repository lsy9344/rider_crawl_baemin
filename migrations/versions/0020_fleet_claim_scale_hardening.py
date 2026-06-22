"""Fleet claim affinity, complete idempotency, and scale indexes.

Revision ID: 0020_fleet_claim_scale
Revises: 0019_profile_channel_uniqueness
Create Date: 2026-06-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_fleet_claim_scale"
down_revision = "0019_profile_channel_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("assigned_agent_id", sa.Uuid(), nullable=True))
    op.add_column("jobs", sa.Column("completion_id", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("completion_payload_hash", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_jobs_assigned_agent_id_agents",
        "jobs",
        "agents",
        ["assigned_agent_id"],
        ["id"],
    )
    op.drop_index("ix_jobs_pending_claim", table_name="jobs", postgresql_where="status = 'PENDING'")
    op.create_index(
        "ix_jobs_pending_claim",
        "jobs",
        ["status", "type", "assigned_agent_id", "run_after", "id"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_jobs_active_crawl_target_type",
        "jobs",
        ["target_id", "type", "status"],
        postgresql_where=sa.text(
            "target_id IS NOT NULL AND type IN ('CRAWL_BAEMIN', 'CRAWL_COUPANG') "
            "AND status IN ('PENDING', 'CLAIMED', 'RUNNING', 'RETRY')"
        ),
    )
    op.create_index(
        "ix_snapshots_target_collected_at_id",
        "snapshots",
        ["target_id", "collected_at", "id"],
    )
    op.create_index(
        "ix_messages_snapshot_template_version",
        "messages",
        ["snapshot_id", "template_version"],
    )
    op.create_index(
        "ix_delivery_logs_channel_message",
        "delivery_logs",
        ["channel_id", "message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_logs_channel_message", table_name="delivery_logs")
    op.drop_index("ix_messages_snapshot_template_version", table_name="messages")
    op.drop_index("ix_snapshots_target_collected_at_id", table_name="snapshots")
    op.drop_index("ix_jobs_active_crawl_target_type", table_name="jobs")
    op.drop_index("ix_jobs_pending_claim", table_name="jobs", postgresql_where="status = 'PENDING'")
    op.create_index(
        "ix_jobs_pending_claim",
        "jobs",
        ["status", "type", "run_after", "id"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_constraint("fk_jobs_assigned_agent_id_agents", "jobs", type_="foreignkey")
    op.drop_column("jobs", "completion_payload_hash")
    op.drop_column("jobs", "completion_id")
    op.drop_column("jobs", "assigned_agent_id")
