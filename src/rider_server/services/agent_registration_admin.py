"""Server-side provisioning for Agent registration codes."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.agent import Agent as AgentRow
from rider_server.services.agent_registry import hash_registration_code

AGENT_STATUS_PENDING_REGISTRATION = "PENDING_REGISTRATION"


class DuplicateAgentRegistrationError(ValueError):
    """운영자가 다시 시도할 수 있는 등록 코드/hash 중복."""


def generate_registration_code() -> str:
    """Generate a one-time registration code shown once to the operator."""

    return "agreg_" + secrets.token_urlsafe(18)


def pending_agent_values(
    *,
    agent_id: uuid.UUID,
    name: str,
    registration_code: str,
    now: datetime,
) -> dict[str, Any]:
    """Build DB values for an Agent waiting to register.

    Only the registration code hash is persisted. The plaintext code is returned
    by the CLI once and must not be stored in the database.
    """

    return {
        "id": agent_id,
        "name": name.strip() or "pending-agent",
        "machine_id": "pending",
        "version": "pending",
        "os": "pending",
        "status": AGENT_STATUS_PENDING_REGISTRATION,
        "last_heartbeat_at": None,
        "capacity_json": {},
        "token_revoked_at": None,
        "token_rotated_at": None,
        "registration_code_hash": hash_registration_code(registration_code.strip()),
        "registration_code_used_at": None,
        "token_hash": None,
        "token_issued_at": None,
    }


async def seed_pending_agent_registration(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent_id: str | uuid.UUID,
    name: str,
    now: datetime,
    registration_code: str | None = None,
) -> str:
    """Insert or refresh a pending Agent registration row."""

    parsed_agent_id = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
    code = registration_code or generate_registration_code()
    values = pending_agent_values(
        agent_id=parsed_agent_id,
        name=name,
        registration_code=code,
        now=now,
    )
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(AgentRow.registration_code_used_at).where(AgentRow.id == parsed_agent_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise RuntimeError("agent is already registered")
        row_exists = (
            await session.execute(select(AgentRow.id).where(AgentRow.id == parsed_agent_id))
        ).scalar_one_or_none()
        try:
            if row_exists is None:
                await session.execute(insert(AgentRow).values(**values))
            else:
                update_values = dict(values)
                update_values.pop("id", None)
                await session.execute(
                    update(AgentRow)
                    .where(AgentRow.id == parsed_agent_id)
                    .values(**update_values)
                )
            await session.commit()
        except IntegrityError as exc:
            raise DuplicateAgentRegistrationError(
                "중복된 Agent 등록 코드입니다. 새 등록 코드를 발급하세요."
            ) from exc
    return code
