"""Add DB-level unique guards for registration and Telegram routing.

Revision ID: 0009_dbx_unique_guards
Revises: 0008_acct_email_2fa_refs
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_dbx_unique_guards"
down_revision: str | None = "0008_acct_email_2fa_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOPIC_INDEX = "uq_messenger_channels_active_telegram_topic"
_GENERAL_INDEX = "uq_messenger_channels_active_telegram_general"
_CHANNEL_CODE_INDEX = "uq_messenger_channels_registration_code"
_AGENT_REG_CODE_INDEX = "uq_agents_registration_code_hash"
_AGENT_TOKEN_INDEX = "uq_agents_token_hash"


def upgrade() -> None:
    op.drop_index(_TOPIC_INDEX, table_name="messenger_channels")
    op.create_index(
        _TOPIC_INDEX,
        "messenger_channels",
        ["telegram_chat_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE' AND thread_id IS NOT NULL"),
    )
    op.create_index(
        _GENERAL_INDEX,
        "messenger_channels",
        ["telegram_chat_id"],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE' AND thread_id IS NULL"),
    )
    op.create_index(
        _CHANNEL_CODE_INDEX,
        "messenger_channels",
        ["registration_code"],
        unique=True,
        postgresql_where=sa.text("registration_code IS NOT NULL"),
    )
    op.create_index(
        _AGENT_REG_CODE_INDEX,
        "agents",
        ["registration_code_hash"],
        unique=True,
        postgresql_where=sa.text("registration_code_hash IS NOT NULL"),
    )
    op.create_index(
        _AGENT_TOKEN_INDEX,
        "agents",
        ["token_hash"],
        unique=True,
        postgresql_where=sa.text("token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_AGENT_TOKEN_INDEX, table_name="agents")
    op.drop_index(_AGENT_REG_CODE_INDEX, table_name="agents")
    op.drop_index(_CHANNEL_CODE_INDEX, table_name="messenger_channels")
    op.drop_index(_GENERAL_INDEX, table_name="messenger_channels")
    op.drop_index(_TOPIC_INDEX, table_name="messenger_channels")
    op.create_index(
        _TOPIC_INDEX,
        "messenger_channels",
        ["telegram_chat_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE'"),
    )
