"""PostgreSQL ``AgentTokenRepository`` 구현 — Story 5.8 / AC3.

:class:`rider_server.services.agent_token_service.AgentTokenRepository` 포트의 실 DB 구현.
5.2 ``db/base.py`` 의 ``async_sessionmaker`` 를 주입받아 쓰고 새 엔진을 만들지 않는다
(``PostgresAdminActionRepository`` 선례). async 본문은 DB I/O 만 한다.

**같은 트랜잭션(AC3):** ``agents.token_revoked_at``/``token_rotated_at`` UPDATE 와 ``audit_logs``
INSERT 를 **한 세션·한 commit** 으로 묶는다(token 무효화 성공·audit 누락 불가). 신규 테이블 0
(0005 가 additive 컬럼만 추가). ``is_revoked`` 는 revoke/rotate 시각 중 하나라도 있으면 거부한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.agent import Agent as AgentRow
from rider_server.db.models.audit import AuditLog as AuditLogRow

from .admin_action_repository_postgres import _audit_values
from .agent_token_service import AuditEntry


class PostgresAgentTokenRepository:
    """async SQLAlchemy 기반 ``AgentTokenRepository`` — revoke/rotate 시각 UPDATE + audit INSERT 동일 tx."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def set_revoked(self, agent_id: str, *, at: datetime, audit: AuditEntry) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(AgentRow).where(AgentRow.id == agent_id).values(token_revoked_at=at)
            )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def set_rotated(self, agent_id: str, *, at: datetime, audit: AuditEntry) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(AgentRow).where(AgentRow.id == agent_id).values(token_rotated_at=at)
            )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def is_revoked(self, agent_id: str) -> bool:
        try:
            key = uuid.UUID(agent_id)
        except (ValueError, AttributeError, TypeError):
            return True  # 식별 불가 → fail-closed(거부)
        stmt = select(AgentRow.token_revoked_at, AgentRow.token_rotated_at).where(AgentRow.id == key)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).one_or_none()
        return row is None or row.token_revoked_at is not None or row.token_rotated_at is not None

    async def record_audit(self, audit: AuditEntry) -> None:
        async with self._session_factory() as session:
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()
