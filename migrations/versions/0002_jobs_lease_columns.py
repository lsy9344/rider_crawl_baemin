"""jobs lease 컬럼 additive — Story 5.3 / AC2 (lease 의미론).

5.2 ``0001_initial_schema`` 의 ``jobs`` 테이블에 lease/claim 컬럼을 **additive** 로 추가한다
(0001 은 수정 금지 — done·커밋됨). ``lease_expires_at``/``claimed_at`` 은 tz-aware,
``result_json`` 은 Postgres JSONB(complete 결과 저장). claim 성능을 위해 복합 인덱스
``ix_jobs_status (status, run_after)`` 를 추가해 ``FOR UPDATE SKIP LOCKED`` 대상 행 스캔을
최소화한다. downgrade 는 round-trip(드롭) 한다. 계약 Required 8필드는 불변 — 새 컬럼은
superset 이라 5.2 schema 가드 무회귀.

Revision ID: 0002_jobs_lease_columns
Revises: 0001_initial_schema
Create Date: 2026-06-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_jobs_lease_columns"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    """JSON 컬럼 — Postgres 에선 JSONB(모델 ``json_variant`` 미러)."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("result_json", _json(), nullable=True),
    )
    op.create_index("ix_jobs_status", "jobs", ["status", "run_after"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_column("jobs", "result_json")
    op.drop_column("jobs", "claimed_at")
    op.drop_column("jobs", "lease_expires_at")
