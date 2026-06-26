"""Tenant send-test gate timestamp.

채널 전송 테스트가 마지막으로 성공한 시각을 ``tenants.send_test_passed_at`` 에 기록한다.
NULL 이면 전송 테스트 미통과 → ``sending_enabled`` OFF→ON 전이를 막는다(fail-closed 게이트).

Revision ID: 0023_tenant_send_test_gate
Revises: 0022_coupang_auto_recovery_state
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_tenant_send_test_gate"
down_revision = "0022_coupang_auto_recovery_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # nullable timestamp — 기존 행은 NULL(미통과)로 채워져 fail-closed 게이트가 유지된다.
    op.add_column(
        "tenants",
        sa.Column("send_test_passed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "send_test_passed_at")
