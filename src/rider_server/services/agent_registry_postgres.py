"""PostgreSQL Agent registry implementation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.agent import Agent as AgentRow

from .agent_registry import (
    AGENT_STATUS_ONLINE,
    AGENT_STATUS_REGISTERED,
    DEFAULT_CONFIG_VERSION,
    AgentTokenMismatch,
    DuplicateMachineRegistration,
    HeartbeatInput,
    HeartbeatResult,
    InvalidAgentToken,
    RegisterAgentInput,
    RegisterAgentResult,
    RegistrationCodeAlreadyUsed,
    RegistrationCodeNotFound,
    generate_agent_token,
    hash_agent_token,
    hash_registration_code,
    heartbeat_capacity,
)


class PostgresAgentRegistry:
    """Async SQLAlchemy implementation using the existing ``agents`` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def register(
        self,
        request: RegisterAgentInput,
        *,
        now: datetime,
    ) -> RegisterAgentResult:
        code_hash = hash_registration_code(request.registration_code.strip())
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentRow).where(AgentRow.registration_code_hash == code_hash)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise RegistrationCodeNotFound("registration code not found")
            if row.registration_code_used_at is not None:
                raise RegistrationCodeAlreadyUsed("registration code already used")

            duplicate = (
                await session.execute(
                    select(AgentRow.id).where(
                        AgentRow.machine_id == request.machine_fingerprint.strip(),
                        AgentRow.registration_code_used_at.is_not(None),
                        AgentRow.id != row.id,
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise DuplicateMachineRegistration("machine already registered")

            token = generate_agent_token()
            result = await session.execute(
                update(AgentRow)
                .where(
                    AgentRow.id == row.id,
                    AgentRow.registration_code_used_at.is_(None),
                )
                .values(
                    name=request.hostname.strip() or "agent",
                    machine_id=request.machine_fingerprint.strip(),
                    version=request.agent_version.strip(),
                    os=request.os.strip(),
                    status=AGENT_STATUS_REGISTERED,
                    registration_code_used_at=now,
                    token_hash=hash_agent_token(token),
                    token_issued_at=now,
                )
            )
            if (result.rowcount or 0) != 1:
                await session.rollback()
                raise RegistrationCodeAlreadyUsed("registration code already used")
            await session.commit()
            return RegisterAgentResult(
                agent_id=str(row.id),
                agent_token=token,
                tenant_scope={},
                config_version=DEFAULT_CONFIG_VERSION,
            )

    async def heartbeat(
        self,
        request: HeartbeatInput,
        *,
        bearer_token: str,
        now: datetime,
    ) -> HeartbeatResult:
        token_hash = hash_agent_token(bearer_token)
        async with self._session_factory() as session:
            row = (
                await session.execute(select(AgentRow).where(AgentRow.token_hash == token_hash))
            ).scalar_one_or_none()
            if row is None or row.token_revoked_at is not None:
                raise InvalidAgentToken("invalid agent token")
            if str(row.id) != request.agent_id:
                raise AgentTokenMismatch("agent token does not match body agent_id")

            values = {
                "status": AGENT_STATUS_ONLINE,
                "last_heartbeat_at": now,
                "capacity_json": heartbeat_capacity(request),
            }
            agent_version = request.agent_version.strip()
            if agent_version:
                values["version"] = agent_version

            await session.execute(
                update(AgentRow)
                .where(AgentRow.id == row.id)
                .values(**values)
            )
            await session.commit()
        return HeartbeatResult(server_time=now)

    async def resolve_agent_id(self, bearer_token: str) -> str | None:
        token_hash = hash_agent_token(bearer_token)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentRow.id, AgentRow.token_revoked_at).where(
                        AgentRow.token_hash == token_hash
                    )
                )
            ).one_or_none()
        if row is None or row.token_revoked_at is not None:
            return None
        return str(row.id)
