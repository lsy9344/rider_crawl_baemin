"""Agent registration and heartbeat routes."""

from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from rider_server.api.jobs import DEFAULT_LEASE_SECONDS
from rider_server.queue.backend import QueueBackend
from rider_server.services.agent_registry import (
    AgentRegistry,
    AgentTokenMismatch,
    DuplicateMachineRegistration,
    HeartbeatInput,
    InvalidAgentToken,
    RegisterAgentInput,
    RegistrationCodeAlreadyUsed,
    RegistrationCodeNotFound,
)

router = APIRouter(prefix="/v1/agents", tags=["agents"])


class RegisterRequest(BaseModel):
    registration_code: str
    machine_fingerprint: str
    hostname: str
    os: str
    agent_version: str


class HeartbeatRequest(BaseModel):
    agent_id: str
    agent_version: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    active_jobs: list[Any] = Field(default_factory=list)
    kakao_status: dict[str, Any] = Field(default_factory=dict)
    browser_profiles: list[Any] = Field(default_factory=list)


def _registry(request: Request) -> AgentRegistry:
    return request.app.state.agent_registry


def _backend(request: Request) -> QueueBackend:
    return request.app.state.queue_backend


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="missing bearer token")
    return token


def _iso_utc(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@router.post("/register")
async def register_agent(request: Request, body: RegisterRequest) -> dict:
    try:
        result = await _registry(request).register(
            RegisterAgentInput(
                registration_code=body.registration_code,
                machine_fingerprint=body.machine_fingerprint,
                hostname=body.hostname,
                os=body.os,
                agent_version=body.agent_version,
            ),
            now=datetime.now(timezone.utc),
        )
    except RegistrationCodeNotFound as exc:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="registration code not found"
        ) from exc
    except (RegistrationCodeAlreadyUsed, DuplicateMachineRegistration) as exc:
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=str(exc)) from exc

    return {
        "agent_id": result.agent_id,
        "agent_token": result.agent_token,
        "tenant_scope": result.tenant_scope,
        "config_version": result.config_version,
    }


@router.post("/heartbeat")
async def heartbeat(request: Request, body: HeartbeatRequest) -> dict:
    token = _bearer_token(request)
    now = datetime.now(timezone.utc)
    try:
        result = await _registry(request).heartbeat(
            HeartbeatInput(
                agent_id=body.agent_id,
                agent_version=body.agent_version,
                metrics=body.metrics,
                capabilities=body.capabilities,
                active_jobs=body.active_jobs,
                kakao_status=body.kakao_status,
                browser_profiles=body.browser_profiles,
            ),
            bearer_token=token,
            now=now,
        )
    except InvalidAgentToken as exc:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="invalid agent token"
        ) from exc
    except AgentTokenMismatch as exc:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="agent token mismatch") from exc

    backend = _backend(request)
    for active_job in body.active_jobs:
        job_id = active_job.get("job_id") if isinstance(active_job, dict) else None
        if not isinstance(job_id, str) or not job_id.strip():
            continue
        try:
            await backend.extend_lease(
                job_id=job_id,
                agent_id=body.agent_id,
                lease_seconds=DEFAULT_LEASE_SECONDS,
                now=now,
            )
        except Exception:  # noqa: BLE001 - active job lease extension is best-effort.
            continue

    return {
        "server_time": _iso_utc(result.server_time),
        "config_version": result.config_version,
        "commands": result.commands,
    }
