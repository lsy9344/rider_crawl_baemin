"""monitoring_targets 스케줄링 컬럼 additive — Story 5.4 / AC1·AC4 (scheduler due 질의).

5.2 ``0001_initial_schema`` 의 ``monitoring_targets`` 테이블에 scheduler 스케줄링 컬럼을
**additive** 로 추가한다(0001/0002 는 수정 금지 — done·커밋됨). ``next_run_at`` 은 due 질의
(contract: "Query due targets by ``monitoring_targets.next_run_at``")·멱등 전진의 정본 시각,
``last_enqueued_at`` 은 멱등/가시성용. 둘 다 tz-aware nullable(null=즉시 due 또는 미초기화).
due 스캔(``next_run_at <= now``) 성능을 위해 ``ix_monitoring_targets_next_run_at`` 인덱스를
추가한다. downgrade 는 round-trip(드롭). 계약 Required 8필드는 불변 — 새 컬럼은 superset 이라
5.2 schema 가드 무회귀.

Revision ID: 0003_monitoring_targets_scheduling
Revises: 0002_jobs_lease_columns
Create Date: 2026-06-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_monitoring_targets_scheduling"
down_revision: str | None = "0002_jobs_lease_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "monitoring_targets",
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "monitoring_targets",
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_monitoring_targets_next_run_at", "monitoring_targets", ["next_run_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_monitoring_targets_next_run_at", table_name="monitoring_targets"
    )
    op.drop_column("monitoring_targets", "last_enqueued_at")
    op.drop_column("monitoring_targets", "next_run_at")
