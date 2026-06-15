"""Add platform account verification email refs.

Revision ID: 0008_acct_email_2fa_refs
Revises: 0007_jobs_payload_json
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_acct_email_2fa_refs"
down_revision = "0007_jobs_payload_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_accounts",
        sa.Column("verification_email_address_ref", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "platform_accounts",
        sa.Column("verification_email_app_password_ref", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "platform_accounts",
        sa.Column(
            "verification_email_subject_keyword",
            sa.String(),
            nullable=False,
            server_default="인증번호",
        ),
    )
    op.add_column(
        "platform_accounts",
        sa.Column(
            "verification_email_sender_keyword",
            sa.String(),
            nullable=False,
            server_default="coupang",
        ),
    )


def downgrade() -> None:
    op.drop_column("platform_accounts", "verification_email_sender_keyword")
    op.drop_column("platform_accounts", "verification_email_subject_keyword")
    op.drop_column("platform_accounts", "verification_email_app_password_ref")
    op.drop_column("platform_accounts", "verification_email_address_ref")
