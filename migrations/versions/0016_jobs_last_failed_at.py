"""Add jobs last_failed_at.

Revision ID: 0016_jobs_last_failed_at
Revises: 0015_delivery_outbox
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_jobs_last_failed_at"
down_revision = "0015_delivery_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "last_failed_at")
