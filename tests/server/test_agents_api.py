"""Agent register/heartbeat API contract required by refactoring Phase 1.

The Agent client already posts to ``/v1/agents/register`` and
``/v1/agents/heartbeat``. These tests lock the server side of that contract:
one-time registration codes, bearer-only heartbeat auth, no token/code echo, and
observable heartbeat state.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http import HTTPStatus

from fastapi.testclient import TestClient

from rider_agent.heartbeat import build_heartbeat_payload
from rider_agent.secure_store import AgentIdentity
from rider_server.main import create_app
from rider_server.queue import InMemoryQueueBackend
from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN
from rider_server.services.agent_registry import InMemoryAgentRegistry
from rider_server.settings import Settings

_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_CODE = "JOIN-CODE-AGENT-1"
_AGENT_ID = "11111111-1111-1111-1111-111111111111"
_AGENT_ID_2 = "22222222-2222-2222-2222-222222222222"


def _client(
    registry: InMemoryAgentRegistry, *, queue_backend: InMemoryQueueBackend | None = None
) -> TestClient:
    return TestClient(
        create_app(_SETTINGS, agent_registry=registry, queue_backend=queue_backend),
        raise_server_exceptions=False,
    )


def _register_body(*, code: str = _CODE, fingerprint: str = "fp-work-pc-1") -> dict:
    return {
        "registration_code": code,
        "machine_fingerprint": fingerprint,
        "hostname": "WORK-PC-01",
        "os": "Windows 11",
        "agent_version": "0.1.0",
    }


def _register(client: TestClient, *, code: str = _CODE, fingerprint: str = "fp-work-pc-1") -> dict:
    response = client.post(
        "/v1/agents/register",
        json=_register_body(code=code, fingerprint=fingerprint),
    )
    assert response.status_code == HTTPStatus.OK
    return response.json()


def test_register_consumes_one_time_code_and_returns_token_once() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    client = _client(registry)

    body = _register(client)

    assert body["agent_id"] == _AGENT_ID
    assert body["agent_token"]
    assert body["tenant_scope"] == {}
    assert body["config_version"] == 1
    assert _CODE not in body["agent_token"]
    assert _CODE not in str(body)

    saved = registry.agent(_AGENT_ID)
    assert saved is not None
    assert saved.machine_id == "fp-work-pc-1"
    assert saved.name == "WORK-PC-01"
    assert saved.version == "0.1.0"
    assert saved.os == "Windows 11"
    assert saved.status == "REGISTERED"

    reused = client.post("/v1/agents/register", json=_register_body())
    assert reused.status_code == HTTPStatus.CONFLICT
    assert _CODE not in reused.text
    assert body["agent_token"] not in reused.text


def test_register_rejects_duplicate_machine_with_different_code() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    registry.seed_registration_code("JOIN-CODE-AGENT-2", agent_id=_AGENT_ID_2)
    client = _client(registry)

    _register(client)
    duplicate = client.post(
        "/v1/agents/register",
        json=_register_body(code="JOIN-CODE-AGENT-2"),
    )

    assert duplicate.status_code == HTTPStatus.CONFLICT
    assert "JOIN-CODE-AGENT-2" not in duplicate.text


def test_heartbeat_requires_bearer_token() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    client = _client(registry)

    response = client.post(
        "/v1/agents/heartbeat",
        json={"agent_id": _AGENT_ID, "metrics": {}, "capabilities": []},
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_heartbeat_rejects_agent_id_mismatch() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    client = _client(registry)
    token = _register(client)["agent_token"]

    response = client.post(
        "/v1/agents/heartbeat",
        json={"agent_id": _AGENT_ID_2, "metrics": {}, "capabilities": []},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert token not in response.text


def test_heartbeat_updates_agent_capacity_and_status_without_echoing_token() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    client = _client(registry)
    token = _register(client)["agent_token"]

    response = client.post(
        "/v1/agents/heartbeat",
        json={
            "agent_id": _AGENT_ID,
            "metrics": {"cpu_percent": 12.5},
            "capabilities": ["CRAWL_BAEMIN", "KAKAO_SEND"],
            "active_jobs": [{"job_id": "job-1", "lease_expires_at": "2026-06-15T00:00:00Z"}],
            "kakao_status": {"state": "idle", "queue_depth": 0},
            "browser_profiles": [
                {"id": "profile-1", "target_id": "33333333-3333-3333-3333-333333333333", "state": "READY", "cdp_port": 9222}
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["server_time"].endswith("Z")
    assert body["config_version"] == 1
    assert body["commands"] == []
    assert token not in response.text

    saved = registry.agent(_AGENT_ID)
    assert saved is not None
    assert saved.status == "ONLINE"
    assert saved.last_heartbeat_at is not None
    assert saved.capacity_json["metrics"] == {"cpu_percent": 12.5}
    assert saved.capacity_json["capabilities"] == ["CRAWL_BAEMIN", "KAKAO_SEND"]
    assert saved.capacity_json["max_in_flight"] == 1
    assert saved.capacity_json["kakao_status"] == {"state": "idle", "queue_depth": 0}
    assert saved.capacity_json["active_jobs"][0]["job_id"] == "job-1"
    assert saved.capacity_json["browser_profiles"][0]["id"] == "profile-1"


def test_heartbeat_updates_agent_version() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    client = _client(registry)
    token = _register(client)["agent_token"]

    response = client.post(
        "/v1/agents/heartbeat",
        json={
            "agent_id": _AGENT_ID,
            "agent_version": "0.2.0",
            "metrics": {},
            "capabilities": ["CRAWL_BAEMIN"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == HTTPStatus.OK
    saved = registry.agent(_AGENT_ID)
    assert saved is not None
    assert saved.version == "0.2.0"


def test_heartbeat_extends_owned_active_job_lease() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(
        backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, target_id="target-1", now=now)
    )
    [claimed] = asyncio.run(
        backend.claim(
            agent_id=_AGENT_ID,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=now,
        )
    )
    original_lease = claimed.lease_expires_at
    client = _client(registry, queue_backend=backend)
    token = _register(client)["agent_token"]

    response = client.post(
        "/v1/agents/heartbeat",
        json={
            "agent_id": _AGENT_ID,
            "metrics": {},
            "capabilities": [JOB_TYPE_CRAWL_BAEMIN],
            "active_jobs": [{"job_id": job_id, "lease_expires_at": "ignored-by-server"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == HTTPStatus.OK
    snapshot = backend.job_snapshot(job_id)
    assert snapshot is not None
    assert snapshot.lease_expires_at is not None
    assert snapshot.lease_expires_at > original_lease


def test_heartbeat_ignores_unowned_active_job_without_failing() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(
        backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, target_id="target-1", now=now)
    )
    [claimed] = asyncio.run(
        backend.claim(
            agent_id=_AGENT_ID_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )
    original_lease = claimed.lease_expires_at
    client = _client(registry, queue_backend=backend)
    token = _register(client)["agent_token"]

    response = client.post(
        "/v1/agents/heartbeat",
        json={
            "agent_id": _AGENT_ID,
            "metrics": {},
            "capabilities": [JOB_TYPE_CRAWL_BAEMIN],
            "active_jobs": [{"job_id": job_id, "lease_expires_at": "ignored-by-server"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == HTTPStatus.OK
    snapshot = backend.job_snapshot(job_id)
    assert snapshot is not None
    assert snapshot.lease_expires_at == original_lease


def test_heartbeat_strips_sensitive_payload_fields_before_storage() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    client = _client(registry)
    token = _register(client)["agent_token"]

    response = client.post(
        "/v1/agents/heartbeat",
        json={
            "agent_id": _AGENT_ID,
            "metrics": {"cpu_percent": 12.5, "api_token": "metric-token-raw"},
            "capabilities": ["CRAWL_BAEMIN"],
            "active_jobs": [
                {
                    "job_id": "job-1",
                    "lease_expires_at": "2026-06-15T00:00:00Z",
                    "agent_token": "job-token-raw",
                }
            ],
            "kakao_status": {
                "state": "idle",
                "queue_depth": 0,
                "last_error_code": "ROOM_NOT_FOUND",
                "room_name": "customer-room-raw",
                "message_text": "message-body-raw",
                "clipboard_content": "clipboard-raw",
            },
            "browser_profiles": [
                {
                    "id": "profile-1",
                    "target_id": "33333333-3333-3333-3333-333333333333",
                    "state": "READY",
                    "cdp_port": 9222,
                    "profile_path_ref": "opaque-profile-ref",
                    "profile_path": "C:\\Users\\KimYS\\ChromeProfile",
                    "password": "profile-password-raw",
                }
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == HTTPStatus.OK
    saved = registry.agent(_AGENT_ID)
    assert saved is not None
    capacity = saved.capacity_json
    assert capacity["metrics"] == {"cpu_percent": 12.5}
    assert capacity["active_jobs"] == [{"job_id": "job-1", "lease_expires_at": "2026-06-15T00:00:00Z"}]
    assert capacity["kakao_status"] == {
        "state": "idle",
        "queue_depth": 0,
        "last_error_code": "ROOM_NOT_FOUND",
    }
    assert capacity["browser_profiles"] == [
        {
            "id": "profile-1",
            "target_id": "33333333-3333-3333-3333-333333333333",
            "state": "READY",
            "cdp_port": 9222,
            "profile_path_ref": "opaque-profile-ref",
        }
    ]
    saved_text = str(capacity)
    assert "metric-token-raw" not in saved_text
    assert "job-token-raw" not in saved_text
    assert "customer-room-raw" not in saved_text
    assert "message-body-raw" not in saved_text
    assert "clipboard-raw" not in saved_text
    assert "ChromeProfile" not in saved_text
    assert "profile-password-raw" not in saved_text


def test_server_accepts_default_agent_heartbeat_payload_shape() -> None:
    registry = InMemoryAgentRegistry()
    registry.seed_registration_code(_CODE, agent_id=_AGENT_ID)
    client = _client(registry)
    token = _register(client)["agent_token"]
    identity = AgentIdentity(
        agent_id=_AGENT_ID,
        agent_token=token,
        tenant_scope={},
        config_version="1",
    )

    response = client.post(
        "/v1/agents/heartbeat",
        json=build_heartbeat_payload(identity),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == HTTPStatus.OK
    saved = registry.agent(_AGENT_ID)
    assert saved is not None
    assert saved.capacity_json["kakao_status"] == {"state": "disabled", "queue_depth": 0}
    assert saved.capacity_json["max_in_flight"] == 1
