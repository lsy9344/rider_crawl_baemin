"""messenger_channels 등록 코드 + 활성 (chat_id, thread_id) 부분 유니크 — Story 5.5 / AC3.

5.2 ``0001_initial_schema`` 의 ``messenger_channels`` 테이블에 **additive** 로:
  (1) ``registration_code``(nullable String) — ``/register <code>`` 매핑용 라우팅/운영 코드
      (secret 아님). 운영자가 PENDING 채널 사전 생성 시 1회용 코드를 부여한다.
  (2) ``uq_messenger_channels_active_telegram_topic`` — ``(telegram_chat_id, thread_id)`` **부분
      유니크 인덱스**(``WHERE state = 'ACTIVE'``). 순수 정책(``find_telegram_topic_collisions`` 이
      ACTIVE 만 봄)과 정합 — PENDING/INACTIVE 중복 등록은 허용하고 **활성 채널 간**에만 충돌을
      DB 로 강제한다(전역 유니크는 재등록/soft-delete 충돌 위험이 커 부분 유니크로 둔다).

0001/0002/0003 은 수정 금지. ``upgrade``/``downgrade`` 는 정확히 round-trip 한다. 테이블 수는
**14 유지**(신규 테이블 없음 — 컬럼/인덱스만 additive).

Revision ID: 0004_messenger_channel_registration
Revises: 0003_monitoring_targets_scheduling
Create Date: 2026-06-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_messenger_channel_registration"
down_revision: str | None = "0003_monitoring_targets_scheduling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_messenger_channels_active_telegram_topic"


def upgrade() -> None:
    op.add_column(
        "messenger_channels",
        sa.Column("registration_code", sa.String(), nullable=True),
    )
    # 활성 채널만 (chat_id, thread_id) 유일 — Postgres 부분 유니크 인덱스.
    op.create_index(
        _INDEX_NAME,
        "messenger_channels",
        ["telegram_chat_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="messenger_channels")
    op.drop_column("messenger_channels", "registration_code")
