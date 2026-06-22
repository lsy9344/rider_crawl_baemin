"""Per-target send window policy.

Revision ID: 0021_target_send_window
Revises: 0020_fleet_claim_scale
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_target_send_window"
down_revision = "0020_fleet_claim_scale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "monitoring_targets",
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "monitoring_targets",
        sa.Column("start_time", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "monitoring_targets",
        sa.Column("stop_time", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("monitoring_targets", "schedule_enabled", server_default=None)
    op.alter_column("monitoring_targets", "start_time", server_default=None)
    op.alter_column("monitoring_targets", "stop_time", server_default=None)


def downgrade() -> None:
    op.drop_column("monitoring_targets", "stop_time")
    op.drop_column("monitoring_targets", "start_time")
    op.drop_column("monitoring_targets", "schedule_enabled")
