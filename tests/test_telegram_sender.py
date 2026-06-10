import json
from urllib.error import HTTPError
from urllib.parse import parse_qs

import pytest

from rider_crawl.config import AppConfig
from rider_crawl.sender import TelegramSendError, get_telegram_updates, send_telegram_text


def test_send_telegram_text_posts_send_message_request(tmp_path):
    calls: list[tuple[str, bytes]] = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        return _FakeResponse({"ok": True, "result": {"message_id": 10}})

    config = _config(tmp_path)

    send_telegram_text(config, "hello", urlopen=fake_urlopen)

    assert calls[0][0] == "https://api.telegram.org/botsecret-token/sendMessage"
    payload = parse_qs(calls[0][1].decode("utf-8"))
    assert payload["chat_id"] == ["-100123"]
    assert payload["text"] == ["hello"]
    assert payload["disable_web_page_preview"] == ["true"]


def test_send_telegram_text_includes_configured_message_thread_id(tmp_path):
    calls: list[tuple[str, bytes]] = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        return _FakeResponse({"ok": True, "result": {"message_id": 10}})

    config = _config(tmp_path, telegram_message_thread_id="77")

    send_telegram_text(config, "hello", urlopen=fake_urlopen)

    payload = parse_qs(calls[0][1].decode("utf-8"))
    assert payload["message_thread_id"] == ["77"]


def test_send_telegram_text_can_override_message_thread_id_for_replies(tmp_path):
    calls: list[tuple[str, bytes]] = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        return _FakeResponse({"ok": True, "result": {"message_id": 10}})

    config = _config(tmp_path, telegram_message_thread_id="77")

    send_telegram_text(config, "hello", message_thread_id=88, urlopen=fake_urlopen)

    payload = parse_qs(calls[0][1].decode("utf-8"))
    assert payload["message_thread_id"] == ["88"]


def test_send_telegram_text_retries_after_telegram_rate_limit(tmp_path):
    calls: list[tuple[str, bytes]] = []
    sleeps: list[float] = []
    responses = [
        _FakeResponse(
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 2},
            }
        ),
        _FakeResponse({"ok": True, "result": {"message_id": 10}}),
    ]

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        return responses.pop(0)

    send_telegram_text(_config(tmp_path), "hello", urlopen=fake_urlopen, sleep=sleeps.append)

    assert len(calls) == 2
    assert sleeps == [2]


def test_send_telegram_text_does_not_retry_ambiguous_transport_failure(tmp_path):
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise OSError("response lost after request")

    with pytest.raises(TelegramSendError, match="request failed") as exc_info:
        send_telegram_text(_config(tmp_path), "hello", urlopen=fake_urlopen, sleep=sleeps.append)

    assert calls == 1
    assert sleeps == []
    # The request may have reached Telegram, so the outcome is ambiguous and must
    # not be fast-retried by the caller (would double-send).
    assert exc_info.value.ambiguous is True


def test_send_telegram_text_does_not_retry_after_response_read_failure(tmp_path):
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return _UnreadableResponse()

    with pytest.raises(TelegramSendError, match="response could not be read") as exc_info:
        send_telegram_text(_config(tmp_path), "hello", urlopen=fake_urlopen, sleep=lambda _seconds: None)

    assert calls == 1
    # Telegram accepted the request; the message was almost certainly delivered.
    # The caller must not fast-retry this ambiguous failure.
    assert exc_info.value.ambiguous is True


def test_get_telegram_updates_transport_failure_is_not_ambiguous(tmp_path):
    # getUpdates는 멱등 조회라 다시 호출해도 중복 부작용이 없다. sendMessage만
    # ambiguous로 표시하고, 조회 실패는 ambiguous가 아니어야 한다.
    def fake_urlopen(request, timeout):
        raise OSError("connection reset")

    with pytest.raises(TelegramSendError) as exc_info:
        get_telegram_updates(_config(tmp_path), offset=1, urlopen=fake_urlopen)

    assert exc_info.value.ambiguous is False


def test_send_telegram_text_reads_retry_after_from_http_error(tmp_path):
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                hdrs=None,
                fp=_FakeBytesResponse(
                    {
                        "ok": False,
                        "error_code": 429,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 3},
                    }
                ),
            )
        return _FakeResponse({"ok": True, "result": {"message_id": 10}})

    send_telegram_text(_config(tmp_path), "hello", urlopen=fake_urlopen, sleep=sleeps.append)

    assert calls == 2
    assert sleeps == [3]


def test_send_telegram_text_requires_token_and_chat_id(tmp_path):
    config = _config(tmp_path, token="", chat_id="")

    with pytest.raises(TelegramSendError, match="TELEGRAM_BOT_TOKEN"):
        send_telegram_text(config, "hello", urlopen=lambda *_args, **_kwargs: None)


def test_send_telegram_text_raises_send_error_for_non_dict_json_body(tmp_path):
    # 텔레그램이 dict가 아닌 JSON(예: ``[]``)을 주면 data.get(...)에서 AttributeError가
    # 나면 안 된다. 응답을 명확한 TelegramSendError로 변환해야 한다.
    def fake_urlopen(request, timeout):
        return _FakeResponse([])

    with pytest.raises(TelegramSendError, match="Telegram Bot API error"):
        send_telegram_text(_config(tmp_path), "hello", urlopen=fake_urlopen, sleep=lambda _seconds: None)


def test_get_telegram_updates_uses_long_polling_parameters(tmp_path):
    calls: list[tuple[str, bytes]] = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        return _FakeResponse({"ok": True, "result": [{"update_id": 42, "message": {"text": "!홍길동1234"}}]})

    updates = get_telegram_updates(_config(tmp_path), offset=41, timeout_seconds=25, urlopen=fake_urlopen)

    assert updates[0]["update_id"] == 42
    assert calls[0][0] == "https://api.telegram.org/botsecret-token/getUpdates"
    payload = parse_qs(calls[0][1].decode("utf-8"))
    assert payload["offset"] == ["41"]
    assert payload["timeout"] == ["25"]
    assert payload["allowed_updates"] == ['["message"]']


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FakeBytesResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _UnreadableResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        raise OSError("socket closed after response")


def _config(
    tmp_path,
    *,
    token: str = "secret-token",
    chat_id: str = "-100123",
    telegram_message_thread_id: str = "",
) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=True,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        telegram_message_thread_id=telegram_message_thread_id,
    )
