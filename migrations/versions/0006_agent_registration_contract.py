"""Agent registration/heartbeat contract columns.

Adds hash-only columns required for ``/v1/agents/register`` and
``/v1/agents/heartbeat``. Plaintext registration codes and bearer tokens are
never stored.

Revision ID: 0006_agent_registration_contract
Revises: 0005_audit_agent_tokens
Create Date: 2026-06-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_agent_registration_contract"
down_revision: str | None = "0005_audit_agent_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("registration_code_hash", sa.String(), nullable=True))
    op.add_column(
        "agents",
        sa.Column("registration_code_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("agents", sa.Column("token_hash", sa.String(), nullable=True))
    op.add_column("agents", sa.Column("token_issued_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "token_issued_at")
    op.drop_column("agents", "token_hash")
    op.drop_column("agents", "registration_code_used_at")
    op.drop_column("agents", "registration_code_hash")
