"""Add delivery log dispatch outbox columns.

Revision ID: 0015_delivery_outbox
Revises: 0014_jobs_completion_metadata
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_delivery_outbox"
down_revision = "0014_jobs_completion_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("text", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE delivery_logs
        SET status = 'HELD',
            error_code = COALESCE(error_code, 'TARGET_VALIDATION_FAILURE')
        WHERE status IN ('RETRYING', 'FAILED')
        """
    )
    op.execute("UPDATE messages SET text = text_redacted_preview WHERE text IS NULL")
    op.alter_column("messages", "text", nullable=False)
    op.add_column(
        "delivery_logs",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "delivery_logs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "delivery_logs",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("delivery_logs", sa.Column("locked_by", sa.String(), nullable=True))
    op.create_index(
        "ix_delivery_logs_dispatch_claim",
        "delivery_logs",
        ["status", "available_at", "locked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_logs_dispatch_claim", table_name="delivery_logs")
    op.drop_column("delivery_logs", "locked_by")
    op.drop_column("delivery_logs", "locked_at")
    op.drop_column("delivery_logs", "attempt_count")
    op.drop_column("delivery_logs", "available_at")
    op.drop_column("messages", "text")
