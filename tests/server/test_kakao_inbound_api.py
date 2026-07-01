"""Integration tests for POST /v1/kakao/inbound-events (Phase 3, route wiring).

Covers Agent bearer auth, Pydantic validation, delegation to
``app.state.kakao_inbound_event_service``, and that the default (no-injection,
DB-less) wiring is fail-closed. Mapping/gate/dedupe logic is unit-tested in
test_kakao_inbound_event.py; here we only exercise the HTTP boundary.
"""

from fastapi.testclient import TestClient

from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.settings import Settings

_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_BEARER = {"Authorization": "Bearer agent-1"}
_BODY = {
    "source": "pc_kakao_db",
    "kakao_user_hash_digest": "sha256:abc",
    "chat_id": "111",
    "room_name": "운영방",
    "last_log_id": "1002",
    "command": {"type": "RIDER_CANCEL_RATE_LOOKUP", "name": "강민기", "phone_last4": "1234"},
}


class _FakeService:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def handle(self, event, *, agent_id=None):
        self.calls.append((event, agent_id))
        return self.response


def _app(service=None, *, resolved_agent_id="agent-1"):
    app = create_app(
        _SETTINGS,
        queue_backend=InMemoryQueueBackend(),
        kakao_inbound_event_service=service,
    )
    if resolved_agent_id is not None:
        app.state.resolve_agent_id = lambda _token: resolved_agent_id
    else:
        app.state.resolve_agent_id = lambda _token: None
    return app


def test_requires_bearer_token():
    app = _app(_FakeService({"accepted": True, "duplicate": False, "job_id": "j1"}))
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=_BODY)
    assert resp.status_code == 401


def test_rejects_revoked_token():
    app = _app(
        _FakeService({"accepted": True, "duplicate": False, "job_id": "j1"}),
        resolved_agent_id=None,
    )
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=_BODY, headers=_BEARER)
    assert resp.status_code == 401


def test_delegates_to_service_and_returns_response():
    service = _FakeService({"accepted": True, "duplicate": False, "job_id": "job-9"})
    app = _app(service)
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=_BODY, headers=_BEARER)

    assert resp.status_code == 200
    assert resp.json() == {"accepted": True, "duplicate": False, "job_id": "job-9"}
    assert len(service.calls) == 1
    event, agent_id = service.calls[0]
    assert agent_id == "agent-1"
    assert event.command.name == "강민기"
    assert event.command.phone_last4 == "1234"
    assert event.chat_id == "111"
    assert event.room_name == "운영방"


def test_reject_response_passthrough():
    service = _FakeService({"accepted": False, "duplicate": False, "reason": "unknown_room"})
    app = _app(service)
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=_BODY, headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": False, "duplicate": False, "reason": "unknown_room"}


def test_route_passes_agent_id_to_inbound_service():
    service = _FakeService({"accepted": True, "duplicate": False, "job_id": "job-9"})
    app = _app(service, resolved_agent_id="agent-pc-7")
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=_BODY, headers=_BEARER)

    assert resp.status_code == 200
    assert service.calls[0][1] == "agent-pc-7"


def test_validation_rejects_bad_phone_last4():
    service = _FakeService({"accepted": True, "duplicate": False, "job_id": "j1"})
    app = _app(service)
    bad = {**_BODY, "command": {**_BODY["command"], "phone_last4": "12"}}
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=bad, headers=_BEARER)
    assert resp.status_code == 422
    assert service.calls == []


def test_validation_rejects_missing_command():
    service = _FakeService({"accepted": True, "duplicate": False, "job_id": "j1"})
    app = _app(service)
    bad = {k: v for k, v in _BODY.items() if k != "command"}
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=bad, headers=_BEARER)
    assert resp.status_code == 422


def test_default_wiring_is_fail_closed_without_db():
    # No service injected + no Postgres => empty channel loader => unknown_room.
    app = _app(service=None)
    with TestClient(app) as client:
        resp = client.post("/v1/kakao/inbound-events", json=_BODY, headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": False, "duplicate": False, "reason": "unknown_room"}
