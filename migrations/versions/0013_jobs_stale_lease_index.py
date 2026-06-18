"""Add jobs stale lease recovery index.

Revision ID: 0013_jobs_stale_lease_index
Revises: 0012_tenant_telegram_config
Create Date: 2026-06-18

``POST /v1/jobs/claim`` recovers stale leases before selecting new work. Keep that
bulk UPDATE bounded as job volume grows by indexing the exact stale-recovery
predicate.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_jobs_stale_lease_index"
down_revision = "0012_tenant_telegram_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_jobs_status_lease_expires_at",
        "jobs",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text(
            "status IN ('CLAIMED', 'RUNNING') AND lease_expires_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jobs_status_lease_expires_at",
        table_name="jobs",
        postgresql_where=sa.text(
            "status IN ('CLAIMED', 'RUNNING') AND lease_expires_at IS NOT NULL"
        ),
    )
