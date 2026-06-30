"""messenger_channels 카카오 인바운드 명령 트리거 컬럼(additive) — Phase 3.

0001 ``messenger_channels`` 테이블에 **additive** 로:
  (1) ``kakao_chat_id``(nullable String) — 카카오 chat_id 라우팅 식별자(secret 아님). 룸명만
      설정된 채널은 서버가 첫 인바운드 매칭 시 이 값을 바인딩한다.
  (2) ``command_trigger_enabled``(Boolean NOT NULL, server_default false) — 채널이 인바운드
      명령 트리거를 받을지의 opt-in 플래그. 기존 행은 false(미허용)로 채워져 fail-closed.

신규 테이블 없음(컬럼만 additive) — 테이블 수 14 유지. ``upgrade``/``downgrade`` 는 정확히
round-trip 한다. ``command_trigger_enabled`` 의 server_default(false)는 영구 default 동작이라
backfill 후 제거하지 않는다(새 행도 기본 미허용).

Revision ID: 0024_channel_kakao_command
Revises: 0023_tenant_send_test_gate
Create Date: 2026-07-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_channel_kakao_command"
down_revision: str | None = "0023_tenant_send_test_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messenger_channels",
        sa.Column("kakao_chat_id", sa.String(), nullable=True),
    )
    op.add_column(
        "messenger_channels",
        sa.Column(
            "command_trigger_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("messenger_channels", "command_trigger_enabled")
    op.drop_column("messenger_channels", "kakao_chat_id")
