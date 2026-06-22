"""Add jobs completion metadata.

Revision ID: 0014_jobs_completion_metadata
Revises: 0013_jobs_stale_lease_index
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_jobs_completion_metadata"
down_revision = "0013_jobs_stale_lease_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("result_schema_version", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "result_schema_version")
    op.drop_column("jobs", "duration_ms")
    op.drop_column("jobs", "completed_at")
