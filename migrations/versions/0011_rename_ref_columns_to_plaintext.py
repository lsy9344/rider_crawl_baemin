"""Rename *_ref columns to plaintext credential columns.

Revision ID: 0011_rename_ref_to_plaintext
Revises: 0010_tenant_scoped_relational_guards
Create Date: 2026-06-16
"""

from __future__ import annotations

from alembic import op

revision = "0011_rename_ref_to_plaintext"
down_revision = "0010_tenant_scope_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("platform_accounts", "username_ref", new_column_name="username")
    op.alter_column("platform_accounts", "password_ref", new_column_name="password")
    op.alter_column(
        "platform_accounts",
        "verification_email_address_ref",
        new_column_name="verification_email_address",
    )
    op.alter_column(
        "platform_accounts",
        "verification_email_app_password_ref",
        new_column_name="verification_email_app_password",
    )


def downgrade() -> None:
    op.alter_column("platform_accounts", "username", new_column_name="username_ref")
    op.alter_column("platform_accounts", "password", new_column_name="password_ref")
    op.alter_column(
        "platform_accounts",
        "verification_email_address",
        new_column_name="verification_email_address_ref",
    )
    op.alter_column(
        "platform_accounts",
        "verification_email_app_password",
        new_column_name="verification_email_app_password_ref",
    )
