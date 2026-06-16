"""Add per-tenant Telegram config (bot token, webhook secret, send gate).

Revision ID: 0012_tenant_telegram_config
Revises: 0011_rename_ref_to_plaintext
Create Date: 2026-06-17

tenant 별로 텔레그램 봇 토큰·webhook secret·실발송 게이트를 Admin UI 에서 설정할 수 있게
``tenants`` 에 컬럼을 추가한다. 자격증명은 평문 DB 저장 방향(0011 platform_accounts 선례)을
따른다 — secret 은 redaction(키 기반 마스킹)으로 로그/응답에서 가린다. ``sending_enabled`` 은
fail-closed 기본 OFF(false)로, 운영자가 명시 활성화해야 실발송한다(NFR-9·25 계승, env 전역
게이트의 tenant 스코프 대체).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_tenant_telegram_config"
down_revision = "0011_rename_ref_to_plaintext"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("telegram_bot_token", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "tenants",
        sa.Column("telegram_webhook_secret", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "tenants",
        sa.Column("sending_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # 기존 행에 기본값을 채운 뒤 server_default 제거 — 이후 INSERT 는 service 가 값을 명시한다.
    op.alter_column("tenants", "telegram_bot_token", server_default=None)
    op.alter_column("tenants", "telegram_webhook_secret", server_default=None)
    op.alter_column("tenants", "sending_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("tenants", "sending_enabled")
    op.drop_column("tenants", "telegram_webhook_secret")
    op.drop_column("tenants", "telegram_bot_token")
