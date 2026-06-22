"""Add jobs pending claim index.

Revision ID: 0018_jobs_claim_index
Revises: 0017_delivery_attempt_timestamps
Create Date: 2026-06-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_jobs_claim_index"
down_revision = "0017_delivery_attempt_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_jobs_pending_claim",
        "jobs",
        ["status", "type", "run_after", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_pending_claim", table_name="jobs")
