"""Story 5.5 / AC1 (P4-06, FR-29, ADD-11) — secret header 검증 webhook + ``/register <code>``.

(1) always-run 순수 정책(DB 불필요): secret 헤더 상수시간 검증(누락/불일치 거부·일치 수락),
    ``/register <code>`` 파싱(chat_id/thread_id 추출·``@Bot`` 접미·비명령·코드 누락 무시).
(2) always-run 라우트(TestClient + in-memory fake repo): 미검증 요청 거부(401 envelope, secret 미노출),
    검증 통과 시 등록(PENDING + 라우팅 저장)·동일 chat 재등록 idempotent·비명령 200 무시.
(3) reuse/boundary 가드(AST): webhook 모듈이 ``getUpdates``/poller 를 import 하지 않음(send-only 미러).

외부 호출 없음 — fake secret/chat_id 만(실제 토큰/전화/이메일/chat_id 형태 금지). 평면
``tests/server/`` 컨벤션(``__init__.py`` 미추가·자급자족).
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from rider_server.api.telegram_webhook import (
    WEBHOOK_SECRET_HEADER,
    RegisterCommand,
    TelegramUpdate,
    parse_register_command,
    verify_webhook_secret,
)
from rider_server.domain import Messenger, MessengerChannel, MessengerChannelState
from rider_server.main import create_app
from rider_server.services.channel_registration import InMemoryChannelRepository
from rider_server.settings import Settings

# ── fixture: 가짜 값만 ────────────────────────────────────────────────────────────
_FAKE_SECRET = "FAKE-WEBHOOK-SECRET"
_FAKE_CHAT_ID = "-100fake"
_REG_CODE = "REG-CODE-1"
_CHANNEL_ID = "ch-tg-1"
_TENANT_ID = "tn-1"
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)


def _pending_channel(*, chat_id: str | None = None, thread_id: str | None = None) -> MessengerChannel:
    return MessengerChannel(
        id=_CHANNEL_ID,
        tenant_id=_TENANT_ID,
        messenger=Messenger.TELEGRAM,
        telegram_chat_id=chat_id,
        thread_id=thread_id,
        state=MessengerChannelState.PENDING,
    )


def _client(repo: InMemoryChannelRepository, *, secret: str | None = _FAKE_SECRET) -> TestClient:
    app = create_app(_FAKE_SETTINGS, channel_repository=repo)
    # webhook secret 해석 seam 을 fake 로 주입(평문 secret 은 테스트 안에서만).
    app.state.resolve_telegram_secret = lambda: secret
    return TestClient(app, raise_server_exceptions=False)


def _update(text: str, *, chat_id: int = -100123, thread_id: int | None = None, key: str = "message") -> dict:
    inbound: dict = {"text": text, "chat": {"id": chat_id}}
    if thread_id is not None:
        inbound["message_thread_id"] = thread_id
    return {key: inbound}


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 — secret 상수시간 검증
# ══════════════════════════════════════════════════════════════════════════


def test_verify_secret_rejects_missing_either_side():
    # 설정 secret 없음/헤더 없음/빈 문자열 → fail-closed(미설정 환경 임의 수락 금지).
    assert verify_webhook_secret(None, _FAKE_SECRET) is False
    assert verify_webhook_secret(_FAKE_SECRET, None) is False
    assert verify_webhook_secret("", _FAKE_SECRET) is False
    assert verify_webhook_secret(_FAKE_SECRET, "") is False


def test_verify_secret_match_and_mismatch():
    assert verify_webhook_secret(_FAKE_SECRET, _FAKE_SECRET) is True
    assert verify_webhook_secret("other-secret", _FAKE_SECRET) is False
    # 길이가 달라도 안전하게 False(compare_digest).
    assert verify_webhook_secret("short", _FAKE_SECRET) is False


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 — /register <code> 파싱
# ══════════════════════════════════════════════════════════════════════════


def test_parse_register_extracts_chat_and_thread():
    update = TelegramUpdate.model_validate(_update(f"/register {_REG_CODE}", chat_id=-100123, thread_id=7))
    cmd = parse_register_command(update)
    assert cmd == RegisterCommand(code=_REG_CODE, chat_id="-100123", thread_id="7")


def test_parse_register_without_thread_id_is_none():
    update = TelegramUpdate.model_validate(_update(f"/register {_REG_CODE}", chat_id=-100123))
    cmd = parse_register_command(update)
    assert cmd is not None
    assert cmd.thread_id is None


def test_parse_register_accepts_bot_suffix():
    update = TelegramUpdate.model_validate(_update(f"/register@MyRiderBot {_REG_CODE}"))
    cmd = parse_register_command(update)
    assert cmd is not None and cmd.code == _REG_CODE


def test_parse_register_uses_channel_post_when_no_message():
    update = TelegramUpdate.model_validate(_update(f"/register {_REG_CODE}", key="channel_post"))
    cmd = parse_register_command(update)
    assert cmd is not None and cmd.code == _REG_CODE


def test_parse_register_prefers_message_over_channel_post():
    # 둘 다 있으면 message 가 우선(``update.message or update.channel_post``) — 라우팅이 message
    # 의 chat 으로 묶이는지 잠근다(channel_post 로 새지 않음).
    update = TelegramUpdate.model_validate(
        {
            "message": {"text": f"/register {_REG_CODE}", "chat": {"id": -100111}},
            "channel_post": {"text": f"/register OTHER", "chat": {"id": -100999}},
        }
    )
    cmd = parse_register_command(update)
    assert cmd is not None
    assert cmd.code == _REG_CODE and cmd.chat_id == "-100111"


def test_parse_non_command_or_missing_code_is_none():
    for text in ("hello world", "/register", "/register   ", "/registerfoo CODE", "/start CODE"):
        update = TelegramUpdate.model_validate(_update(text))
        assert parse_register_command(update) is None, text


def test_parse_update_without_chat_or_text_is_none():
    assert parse_register_command(TelegramUpdate.model_validate({})) is None
    assert parse_register_command(
        TelegramUpdate.model_validate({"message": {"text": f"/register {_REG_CODE}"}})
    ) is None  # chat 없음
    assert parse_register_command(
        TelegramUpdate.model_validate({"message": {"chat": {"id": -100123}}})
    ) is None  # text 없음


# ══════════════════════════════════════════════════════════════════════════
# (2) 라우트 — secret 거부 / 등록 / idempotent / 무시
# ══════════════════════════════════════════════════════════════════════════


def test_webhook_rejects_missing_secret_header_401_envelope():
    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    # 헤더 없이 호출 → 401 + 전역 에러 envelope(secret 미노출).
    r = client.post("/v1/telegram/webhook", json=_update(f"/register {_REG_CODE}"))
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert _FAKE_SECRET not in r.text


def test_webhook_rejects_wrong_secret_and_does_not_register():
    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    r = client.post(
        "/v1/telegram/webhook",
        json=_update(f"/register {_REG_CODE}"),
        headers={WEBHOOK_SECRET_HEADER: "wrong-secret"},
    )
    assert r.status_code == 401
    # 미검증 요청은 페이로드를 처리하지 않는다 — 채널은 그대로 PENDING·라우팅 없음.
    import asyncio

    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id is None
    assert channel.state is MessengerChannelState.PENDING


def test_webhook_registers_chat_and_thread_on_valid_secret():
    import asyncio

    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    r = client.post(
        "/v1/telegram/webhook",
        json=_update(f"/register {_REG_CODE}", chat_id=-100777, thread_id=42),
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id == "-100777"
    assert channel.thread_id == "42"
    assert channel.state is MessengerChannelState.PENDING  # 등록=PENDING (검증 전)


def test_webhook_accepts_awaitable_secret_resolver():
    import asyncio

    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    app = create_app(_FAKE_SETTINGS, channel_repository=repo)

    async def _resolve_secret():
        return [_FAKE_SECRET]

    app.state.resolve_telegram_secret = _resolve_secret
    client = TestClient(app, raise_server_exceptions=False)

    r = client.post(
        "/v1/telegram/webhook",
        json=_update(f"/register {_REG_CODE}", chat_id=-100777),
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )

    assert r.status_code == 200
    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id == "-100777"


def test_webhook_duplicate_register_is_idempotent():
    import asyncio

    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    payload = _update(f"/register {_REG_CODE}", chat_id=-100777, thread_id=42)
    headers = {WEBHOOK_SECRET_HEADER: _FAKE_SECRET}
    r1 = client.post("/v1/telegram/webhook", json=payload, headers=headers)
    r2 = client.post("/v1/telegram/webhook", json=payload, headers=headers)
    assert r1.status_code == r2.status_code == 200
    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id == "-100777"
    assert channel.thread_id == "42"
    assert channel.state is MessengerChannelState.PENDING


def test_webhook_non_command_is_ignored_with_ok_true():
    import asyncio

    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    r = client.post(
        "/v1/telegram/webhook",
        json=_update("just chatting"),
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id is None  # 비명령 → 등록 안 됨


def test_webhook_unknown_code_is_ignored_with_ok_true():
    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    r = client.post(
        "/v1/telegram/webhook",
        json=_update("/register NOPE-UNKNOWN"),
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_webhook_malformed_body_is_ignored_with_ok_true():
    # 검증 통과 후 본문이 비-JSON 이면 ValueError(JSONDecodeError) → 200 {"ok": true} 무시
    # (Telegram 재전송 폭주 방지). 채널은 등록되지 않는다.
    import asyncio

    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    r = client.post(
        "/v1/telegram/webhook",
        content=b"this-is-not-json",
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id is None  # 깨진 본문 → 등록 안 됨


def test_webhook_schema_mismatch_body_is_ignored_with_ok_true():
    # 유효 JSON 이지만 스키마 불일치(chat.id 가 int 아님)면 pydantic ValidationError(ValueError
    # 하위) → 200 {"ok": true} 무시. secret 검증은 통과한 상태라 거부(401)가 아니다.
    import asyncio

    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    r = client.post(
        "/v1/telegram/webhook",
        json={"message": {"text": f"/register {_REG_CODE}", "chat": {"id": "not-an-int"}}},
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id is None


def test_webhook_registers_without_thread_id_keeps_thread_none():
    # 라우트 경로로 thread 없이 등록 → thread_id None 정규화(서비스 단위 외 HTTP 경계도 잠금).
    import asyncio

    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    client = _client(repo)
    r = client.post(
        "/v1/telegram/webhook",
        json=_update(f"/register {_REG_CODE}", chat_id=-100777),
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )
    assert r.status_code == 200
    channel = asyncio.run(repo.get(_CHANNEL_ID))
    assert channel.telegram_chat_id == "-100777"
    assert channel.thread_id is None


def test_webhook_default_secret_seam_is_fail_closed():
    # 기본 resolve_telegram_secret 은 None 반환(평문 store 미배선) → 어떤 헤더도 거부.
    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    app = create_app(_FAKE_SETTINGS, channel_repository=repo)  # seam 미주입(기본값)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/v1/telegram/webhook",
        json=_update(f"/register {_REG_CODE}"),
        headers={WEBHOOK_SECRET_HEADER: "anything"},
    )
    assert r.status_code == 401


def test_webhook_default_secret_resolver_reads_env_ref(monkeypatch):
    monkeypatch.setenv("RIDER_TELEGRAM_WEBHOOK_SECRET", _FAKE_SECRET)
    repo = InMemoryChannelRepository()
    repo.seed(_pending_channel(), registration_code=_REG_CODE)
    settings = Settings(
        app_env="test",
        app_version="9.9.9",
        build_sha=None,
        build_time=None,
        telegram_webhook_secret_ref="env:RIDER_TELEGRAM_WEBHOOK_SECRET",
    )
    app = create_app(settings, channel_repository=repo)
    client = TestClient(app, raise_server_exceptions=False)

    r = client.post(
        "/v1/telegram/webhook",
        json=_update(f"/register {_REG_CODE}"),
        headers={WEBHOOK_SECRET_HEADER: _FAKE_SECRET},
    )

    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# (3) reuse/boundary 가드 — webhook 은 send-only(getUpdates/poller import 0)
# ══════════════════════════════════════════════════════════════════════════


def test_webhook_module_is_send_only_no_getupdates_or_poller():
    # AC1.2: webhook/등록 경로는 getUpdates/TelegramUpdatePoller 를 import 하지 않는다(AST edge —
    # docstring 언급은 무시). 3.7 send-only 가드 미러.
    source = Path("src/rider_server/api/telegram_webhook.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            if node.module:
                imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "get_telegram_updates" not in imported
    assert "TelegramUpdatePoller" not in imported
    assert "telegram_commands" not in imported
