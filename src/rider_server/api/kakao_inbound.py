"""Kakao inbound command event API — ``POST /v1/kakao/inbound-events`` (Phase 3).

The Agent PC posts detected ``!!<name><4digits>`` command events here. The route
is a thin layer: Agent bearer auth (reusing :func:`rider_server.api.jobs.resolve_agent`),
Pydantic validation, then delegate to ``app.state.kakao_inbound_event_service`` which
owns mapping/gates/dedupe/enqueue. The Agent is tenant-unaware; the tenant is
derived server-side from the matched channel. No raw message text crosses here —
only the parsed command fields and a pre-hashed ``kakao_user_hash_digest``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..services.kakao_inbound_event_service import (
    InboundCommandInput,
    InboundEventInput,
)
from .jobs import resolve_agent

router = APIRouter(prefix="/v1/kakao", tags=["kakao"])

MAX_SOURCE_LENGTH = 64
MAX_DIGEST_LENGTH = 128
MAX_CHAT_ID_LENGTH = 128
MAX_ROOM_NAME_LENGTH = 256
MAX_LOG_ID_LENGTH = 128
MAX_COMMAND_TYPE_LENGTH = 64
MAX_NAME_LENGTH = 40


class InboundCommandBody(BaseModel):
    type: str = Field(min_length=1, max_length=MAX_COMMAND_TYPE_LENGTH)
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    phone_last4: str = Field(min_length=4, max_length=4)


class InboundEventBody(BaseModel):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)
    kakao_user_hash_digest: str = Field(min_length=1, max_length=MAX_DIGEST_LENGTH)
    chat_id: str = Field(default="", max_length=MAX_CHAT_ID_LENGTH)
    room_name: str = Field(default="", max_length=MAX_ROOM_NAME_LENGTH)
    last_log_id: str = Field(min_length=1, max_length=MAX_LOG_ID_LENGTH)
    command: InboundCommandBody


@router.post("/inbound-events")
async def receive_inbound_event(
    request: Request,
    body: InboundEventBody,
    agent_id: str = Depends(resolve_agent),
) -> dict:
    """``POST /v1/kakao/inbound-events`` — accept one detected command event.

    Returns ``{"accepted": bool, "duplicate": bool, ...}``: accepted+job_id when a
    RIDER_LOOKUP is enqueued, accepted+duplicate on idempotent replay, or
    accepted=false + a fixed ``reason`` code when rejected. Agent identity is only
    an auth gate here (the Agent is tenant-unaware)."""

    service = request.app.state.kakao_inbound_event_service
    event = InboundEventInput(
        source=body.source,
        kakao_user_hash_digest=body.kakao_user_hash_digest,
        chat_id=body.chat_id,
        room_name=body.room_name,
        last_log_id=body.last_log_id,
        command=InboundCommandInput(
            type=body.command.type,
            name=body.command.name,
            phone_last4=body.command.phone_last4,
        ),
    )
    return await service.handle(event, agent_id=agent_id)
