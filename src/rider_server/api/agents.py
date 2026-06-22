"""Agent registration and heartbeat routes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from rider_server.api.jobs import (
    DEFAULT_LEASE_SECONDS,
    MAX_CAPABILITIES,
    CapabilityName,
)
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
_LOG = logging.getLogger(__name__)
MAX_ACTIVE_JOBS = 128
MAX_BROWSER_PROFILES = 128
MAX_HEARTBEAT_MAPPING_KEYS = 128
MAX_HEARTBEAT_SEQUENCE_ITEMS = 256
MAX_HEARTBEAT_STRING_LENGTH = 4_096


def _validate_bounded_json(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > MAX_HEARTBEAT_STRING_LENGTH:
            raise ValueError("string value is too large")
        return value
    if isinstance(value, dict):
        if len(value) > MAX_HEARTBEAT_MAPPING_KEYS:
            raise ValueError("mapping has too many items")
        for key, item in value.items():
            _validate_bounded_json(key)
            _validate_bounded_json(item)
        return value
    if isinstance(value, list | tuple):
        if len(value) > MAX_HEARTBEAT_SEQUENCE_ITEMS:
            raise ValueError("sequence has too many items")
        for item in value:
            _validate_bounded_json(item)
    return value


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
    capabilities: list[CapabilityName] = Field(default_factory=list, max_length=MAX_CAPABILITIES)
    active_jobs: list[Any] = Field(default_factory=list, max_length=MAX_ACTIVE_JOBS)
    kakao_status: dict[str, Any] = Field(default_factory=dict)
    browser_profiles: list[Any] = Field(default_factory=list, max_length=MAX_BROWSER_PROFILES)

    @field_validator("metrics", "active_jobs", "kakao_status", "browser_profiles")
    @classmethod
    def _bounded_payload(cls, value: Any) -> Any:
        return _validate_bounded_json(value)


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


def _lease_seconds(request: Request) -> float:
    settings = getattr(request.app.state, "settings", None)
    value = getattr(settings, "job_lease_seconds", DEFAULT_LEASE_SECONDS)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LEASE_SECONDS
    return parsed if parsed > 0 else DEFAULT_LEASE_SECONDS


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
    active_job_ids: list[str] = []
    seen_job_ids: set[str] = set()
    for active_job in body.active_jobs:
        job_id = active_job.get("job_id") if isinstance(active_job, dict) else None
        if not isinstance(job_id, str) or not job_id.strip():
            continue
        if job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)
        active_job_ids.append(job_id)
    if active_job_ids:
        try:
            extended = await backend.extend_leases(
                job_ids=active_job_ids,
                agent_id=body.agent_id,
                lease_seconds=_lease_seconds(request),
                now=now,
            )
        except Exception:  # noqa: BLE001 - active job lease extension is best-effort.
            _LOG.warning("agent heartbeat lease extension failed", exc_info=True)
            extended = set()
    else:
        extended = set()

    extended_job_ids = [job_id for job_id in active_job_ids if job_id in extended]
    failed_job_ids = [job_id for job_id in active_job_ids if job_id not in extended]
    lease_extension = {
        "status": "ok" if not failed_job_ids else "degraded",
        "extended_job_ids": extended_job_ids,
        "failed_job_ids": failed_job_ids,
    }

    return {
        "server_time": _iso_utc(result.server_time),
        "config_version": result.config_version,
        "commands": result.commands,
        "lease_extension": lease_extension,
    }
