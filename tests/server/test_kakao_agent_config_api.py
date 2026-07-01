"""Integration tests for GET /v1/agents/kakao-inbound-config (Hybrid watchlist).

Covers Agent bearer auth, non-secret-only watchlist payload, enabled flag, and a
deterministic config_version. The repository SQL filter
(ACTIVE && command_trigger_enabled && KAKAO) mirrors active_channels and is
exercised at the domain level via InMemoryChannelRepository in
test_channel_registration.
"""

from fastapi.testclient import TestClient

from rider_server.domain import Messenger, MessengerChannel, MessengerChannelState
from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.settings import Settings

_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_BEARER = {"Authorization": "Bearer agent-1"}
_PATH = "/v1/agents/kakao-inbound-config"


class _FakeChannelRepo:
    def __init__(self, channels):
        self._channels = channels

    async def active_kakao_command_channels(self):
        return list(self._channels)


def _channel(cid="ch1", room="운영방", chat_id="111"):
    return MessengerChannel(
        id=cid,
        tenant_id="t1",
        messenger=Messenger.KAKAO,
        kakao_room_name=room,
        kakao_chat_id=chat_id,
        state=MessengerChannelState.ACTIVE,
        command_trigger_enabled=True,
    )


def _app(channels, *, resolved_agent_id="agent-1"):
    app = create_app(_SETTINGS, queue_backend=InMemoryQueueBackend())
    app.state.resolve_agent_id = (lambda _token: resolved_agent_id)
    app.state.channel_repository = _FakeChannelRepo(channels)
    return app


def test_requires_bearer_token():
    app = _app([_channel()])
    with TestClient(app) as client:
        assert client.get(_PATH).status_code == 401


def test_rejects_invalid_token():
    app = _app([_channel()], resolved_agent_id=None)
    with TestClient(app) as client:
        assert client.get(_PATH, headers=_BEARER).status_code == 401


def test_returns_non_secret_watchlist():
    app = _app([_channel(room="운영방", chat_id="111"), _channel(cid="ch2", room="상담방", chat_id="")])
    with TestClient(app) as client:
        resp = client.get(_PATH, headers=_BEARER)

    assert resp.status_code == 200
    body = resp.json()["kakao_inbound"]
    assert body["enabled"] is True
    assert body["rooms"] == [
        {"room_name": "운영방", "chat_id": "111"},
        {"room_name": "상담방", "chat_id": ""},
    ]
    # non-secret only: no key/hash/path fields anywhere in the payload.
    blob = resp.text.lower()
    for secret in ("db_key", "user_hash", "cipher", ".edb", "password", "tenant_id"):
        assert secret not in blob


def test_enabled_false_when_no_rooms():
    app = _app([])
    with TestClient(app) as client:
        body = client.get(_PATH, headers=_BEARER).json()["kakao_inbound"]
    assert body["enabled"] is False
    assert body["rooms"] == []


def test_config_version_is_deterministic_and_content_sensitive():
    with TestClient(_app([_channel(chat_id="111")])) as c1, \
            TestClient(_app([_channel(chat_id="111")])) as c2, \
            TestClient(_app([_channel(chat_id="222")])) as c3:
        v1 = c1.get(_PATH, headers=_BEARER).json()["kakao_inbound"]["config_version"]
        v2 = c2.get(_PATH, headers=_BEARER).json()["kakao_inbound"]["config_version"]
        v3 = c3.get(_PATH, headers=_BEARER).json()["kakao_inbound"]["config_version"]

    assert v1 == v2  # same watchlist -> same version
    assert v1 != v3  # different chat_id -> different version
