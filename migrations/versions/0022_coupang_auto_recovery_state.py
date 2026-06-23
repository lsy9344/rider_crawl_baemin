"""Coupang auto recovery state on platform accounts.

Revision ID: 0022_coupang_auto_recovery_state
Revises: 0021_target_send_window
Create Date: 2026-06-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_coupang_auto_recovery_state"
down_revision = "0021_target_send_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_accounts",
        sa.Column("auto_recovery_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "platform_accounts",
        sa.Column("auto_recovery_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "platform_accounts",
        sa.Column("auto_recovery_cooldown_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_accounts", "auto_recovery_cooldown_until")
    op.drop_column("platform_accounts", "auto_recovery_failed_at")
    op.drop_column("platform_accounts", "auto_recovery_attempted_at")
