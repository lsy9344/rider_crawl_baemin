"""audit_logs source·reason·result + agents token revoke/rotate 시각 — Story 5.8 / AC1·AC3.

5.2 ``0001_initial_schema`` 의 ``audit_logs`` 와 ``agents`` 테이블에 **additive** 컬럼만 더한다
(0001~0004 수정 금지 — done·커밋됨). 신규 테이블 0(테이블 수 **14 유지**), 컬럼만 추가:

  audit_logs(readiness gate 7필드 충족 — actor/source/diff/target/reason/timestamp/result):
    (1) ``source``(nullable String) — 변경 출처/역할/source IP(redaction 통과 자유 텍스트, secret 아님)
    (2) ``reason``(nullable String) — 운영자 자유 텍스트(redaction 통과)
    (3) ``result``(NOT NULL String) — :class:`AuditResult` 값(성공/실패/거부). 기존 행 보존 위해
        ``server_default='SUCCESS'`` 로 추가 후 default 제거(신규 INSERT 는 코드가 항상 값 제공).

  agents(server-side token revoke/rotate — AC3):
    (4) ``token_revoked_at``(nullable tz-aware) — revoke 시각. ``resolve_agent_id`` 가 이 값을 보고
        revoked agent 의 bearer 를 거부(→None→401).
    (5) ``token_rotated_at``(nullable tz-aware) — rotate 시각(기존 무효화 + 재발급 경로 마킹).

``upgrade``/``downgrade`` 는 정확히 round-trip 한다. ``token``/``secret``/``password`` 단독
컬럼명 금지(forbidden-column 정확매치 — ``token_*_at`` 는 안전).

Revision ID: 0005_audit_fields_and_agent_token_revoke
Revises: 0004_messenger_channel_registration
Create Date: 2026-06-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_audit_fields_and_agent_token_revoke"
down_revision: str | None = "0004_messenger_channel_registration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── audit_logs: source·reason·result(readiness gate 7필드) ────────────────────
    op.add_column("audit_logs", sa.Column("source", sa.String(), nullable=True))
    op.add_column("audit_logs", sa.Column("reason", sa.String(), nullable=True))
    op.add_column(
        "audit_logs",
        # 기존 행을 위해 server_default 로 NOT NULL 을 충족시킨 뒤 default 를 떼어낸다
        # (신규 INSERT 는 코드가 항상 AuditResult 값을 명시 — 컬럼 default 에 의존하지 않음).
        sa.Column(
            "result", sa.String(), nullable=False, server_default="SUCCESS"
        ),
    )
    op.alter_column("audit_logs", "result", server_default=None)

    # ── agents: server-side token revoke/rotate 시각 ──────────────────────────────
    op.add_column(
        "agents", sa.Column("token_revoked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agents", sa.Column("token_rotated_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agents", "token_rotated_at")
    op.drop_column("agents", "token_revoked_at")
    op.drop_column("audit_logs", "result")
    op.drop_column("audit_logs", "reason")
    op.drop_column("audit_logs", "source")
