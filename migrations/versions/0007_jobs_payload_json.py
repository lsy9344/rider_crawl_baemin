"""Add jobs.payload_json for Agent execution payloads.

Revision ID: 0007_jobs_payload_json
Revises: 0006_agent_registration_contract
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_jobs_payload_json"
down_revision = "0006_agent_registration_contract"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("jobs", sa.Column("payload_json", _json(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "payload_json")
