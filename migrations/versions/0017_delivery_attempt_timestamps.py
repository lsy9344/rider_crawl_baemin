"""Add delivery attempt timestamps.

Revision ID: 0017_delivery_attempt_timestamps
Revises: 0016_jobs_last_failed_at
Create Date: 2026-06-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_delivery_attempt_timestamps"
down_revision = "0016_jobs_last_failed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_logs",
        sa.Column("send_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "delivery_logs",
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("delivery_logs", "last_failed_at")
    op.drop_column("delivery_logs", "send_attempted_at")
