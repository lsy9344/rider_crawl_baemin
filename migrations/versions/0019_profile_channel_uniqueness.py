"""Add profile and Kakao active-room uniqueness.

Revision ID: 0019_profile_channel_uniqueness
Revises: 0018_jobs_claim_index
Create Date: 2026-06-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_profile_channel_uniqueness"
down_revision = "0018_jobs_claim_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_browser_profiles_agent_target",
        "browser_profiles",
        ["agent_id", "target_id"],
        unique=True,
    )
    op.create_index(
        "uq_browser_profiles_agent_cdp_port",
        "browser_profiles",
        ["agent_id", "cdp_port"],
        unique=True,
        postgresql_where=sa.text("cdp_port IS NOT NULL"),
    )
    op.create_index(
        "uq_messenger_channels_active_kakao_room",
        "messenger_channels",
        ["tenant_id", "kakao_room_name"],
        unique=True,
        postgresql_where=sa.text(
            "state = 'ACTIVE' AND messenger = 'KAKAO' AND kakao_room_name IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_messenger_channels_active_kakao_room", table_name="messenger_channels")
    op.drop_index("uq_browser_profiles_agent_cdp_port", table_name="browser_profiles")
    op.drop_index("uq_browser_profiles_agent_target", table_name="browser_profiles")
