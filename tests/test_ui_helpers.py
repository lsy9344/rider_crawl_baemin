import queue
import threading
import time
from pathlib import Path

import pytest

from rider_crawl.app import RunResult
from rider_crawl.ui import (
    DEFAULT_WINDOW_GEOMETRY,
    MESSENGER_OPTIONS,
    MIN_WINDOW_HEIGHT,
    PREVIEW_TEXT_HEIGHT,
    RiderBotUi,
    active_crawling_settings,
    app_configs_from_settings,
    coerce_settings,
    disable_unsupported_send,
    telegram_configs_by_token,
    validate_active_tab_isolation,
)


def test_ui_defaults_leave_more_vertical_room_for_preview_log():
    assert DEFAULT_WINDOW_GEOMETRY == "900x900"
    assert MIN_WINDOW_HEIGHT >= 780
    assert PREVIEW_TEXT_HEIGHT >= 22


def test_messenger_options_expose_telegram_and_kakao_for_ui():
    assert MESSENGER_OPTIONS == (("telegram", "텔레그램"), ("kakao", "카카오톡"))


def test_coerce_settings_builds_ui_settings_from_form_values(tmp_path):
    settings = coerce_settings(
        {
            "performance_url": " https://example.test/rider ",
            "peak_dashboard_url": " https://example.test/dashboard ",
            "browser_mode": "cdp",
            "cdp_url": " http://127.0.0.1:9222 ",
            "browser_user_data_dir": str(tmp_path / "browser"),
            "log_dir": str(tmp_path / "logs"),
            "kakao_chat_name": " 실적봇_의정부남부 ",
            "telegram_bot_token": " token ",
            "telegram_chat_id": " -100123 ",
            "telegram_message_thread_id": " 77 ",
            "messenger_name": "kakao",
            "interval_minutes": "12",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": True,
            "send_enabled": False,
            "send_only_on_change": True,
        }
    )

    assert settings.performance_url == "https://example.test/rider"
    assert settings.peak_dashboard_url == "https://example.test/dashboard"
    assert settings.browser_mode == "cdp"
    assert settings.cdp_url == "http://127.0.0.1:9222"
    assert settings.browser_user_data_dir == Path(tmp_path / "browser")
    assert settings.log_dir == Path(tmp_path / "logs")
    assert settings.kakao_chat_name == "실적봇_의정부남부"
    assert settings.telegram_bot_token == "token"
    assert settings.telegram_chat_id == "-100123"
    assert settings.telegram_message_thread_id == "77"
    assert settings.messenger_name == "kakao"
    assert settings.interval_minutes == 12
    assert settings.send_enabled is False
    assert settings.send_only_on_change is True


def test_coerce_settings_rejects_unknown_messenger():
    try:
        coerce_settings(
            {
                "performance_url": "https://example.test/rider",
                "peak_dashboard_url": "https://example.test/dashboard",
                "browser_mode": "cdp",
                "cdp_url": "http://127.0.0.1:9222",
                "browser_user_data_dir": "runtime/browser",
                "log_dir": "logs",
                "kakao_chat_name": "실적봇",
                "messenger_name": "discord",
                "interval_minutes": "35",
                "page_timeout_seconds": "60000",
                "run_lock_timeout_seconds": "900",
                "headless": False,
                "send_enabled": False,
                "send_only_on_change": False,
            }
        )
    except ValueError as exc:
        assert "전송 방식" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_active_tab_isolation_rejects_duplicate_cdp_ports(tmp_path):
    first = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
    )
    second = _settings(
        tmp_path,
        performance_url="https://example.test/second",
        cdp_url="http://localhost:9222",
        browser_user_data_dir=tmp_path / "browser2",
    )

    with pytest.raises(ValueError, match="CDP"):
        validate_active_tab_isolation([first, second])


def test_validate_active_tab_isolation_rejects_duplicate_browser_profiles(tmp_path):
    first = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "shared-profile",
    )
    second = _settings(
        tmp_path,
        performance_url="https://example.test/second",
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "shared-profile",
    )

    with pytest.raises(ValueError, match="프로필"):
        validate_active_tab_isolation([first, second])


def test_validate_active_tab_isolation_ignores_inactive_tabs(tmp_path):
    active = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
    )
    inactive = _settings(
        tmp_path,
        performance_url="",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
    )

    validate_active_tab_isolation([active, inactive])


def test_validate_active_tab_isolation_ignores_duplicate_cdp_ports_for_persistent_tabs(tmp_path):
    first = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        browser_mode="persistent",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
        telegram_chat_id="-100111",
    )
    second = _settings(
        tmp_path,
        performance_url="https://example.test/second",
        browser_mode="persistent",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser2",
        telegram_chat_id="-100222",
    )

    validate_active_tab_isolation([first, second])


def test_validate_active_tab_isolation_allows_duplicate_telegram_tokens_with_unique_chat_ids(tmp_path):
    first = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
        telegram_bot_token="same-token",
        telegram_chat_id="-100111",
    )
    second = _settings(
        tmp_path,
        performance_url="https://example.test/second",
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "browser2",
        telegram_bot_token="same-token",
        telegram_chat_id="-100222",
    )

    validate_active_tab_isolation([first, second])


def test_validate_active_tab_isolation_allows_same_chat_id_with_unique_thread_ids(tmp_path):
    first = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
        telegram_bot_token="same-token",
        telegram_chat_id="-100123",
        telegram_message_thread_id="77",
    )
    second = _settings(
        tmp_path,
        performance_url="https://example.test/second",
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "browser2",
        telegram_bot_token="same-token",
        telegram_chat_id="-100123",
        telegram_message_thread_id="88",
    )

    validate_active_tab_isolation([first, second])


def test_validate_active_tab_isolation_rejects_duplicate_active_telegram_chat_ids(tmp_path):
    first = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
        telegram_bot_token="token-one",
        telegram_chat_id="-100123",
    )
    second = _settings(
        tmp_path,
        performance_url="https://example.test/second",
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "browser2",
        telegram_bot_token="token-two",
        telegram_chat_id="-100123",
    )

    with pytest.raises(ValueError, match="텔레그램 채팅방 ID"):
        validate_active_tab_isolation([first, second])


def test_validate_active_tab_isolation_ignores_duplicate_chat_ids_for_non_telegram_or_disabled_tabs(tmp_path):
    telegram = _settings(
        tmp_path,
        performance_url="https://example.test/first",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
        telegram_chat_id="-100123",
    )
    kakao = _settings(
        tmp_path,
        performance_url="https://example.test/second",
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "browser2",
        telegram_chat_id="-100123",
        messenger_name="kakao",
    )
    disabled = _settings(
        tmp_path,
        performance_url="https://example.test/third",
        cdp_url="http://127.0.0.1:9224",
        browser_user_data_dir=tmp_path / "browser3",
        telegram_chat_id="-100123",
        send_enabled=False,
    )

    validate_active_tab_isolation([telegram, kakao, disabled])


def test_telegram_configs_by_token_groups_duplicate_tokens_once(tmp_path):
    first = _settings(
        tmp_path,
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
        telegram_bot_token="same-token",
        telegram_chat_id="-100111",
    )
    second = _settings(
        tmp_path,
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "browser2",
        telegram_bot_token="same-token",
        telegram_chat_id="-100222",
    )
    blank_token = _settings(
        tmp_path,
        cdp_url="http://127.0.0.1:9224",
        browser_user_data_dir=tmp_path / "browser3",
        telegram_bot_token="",
        telegram_chat_id="-100333",
    )
    configs = app_configs_from_settings([(0, first), (1, second), (2, blank_token)])

    grouped = telegram_configs_by_token(configs)

    assert list(grouped) == ["same-token"]
    assert [config.telegram_chat_id for config in grouped["same-token"]] == ["-100111", "-100222"]


def test_telegram_configs_by_token_ignores_non_telegram_or_disabled_tabs(tmp_path):
    telegram = _settings(
        tmp_path,
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser1",
        telegram_bot_token="same-token",
        telegram_chat_id="-100111",
    )
    kakao = _settings(
        tmp_path,
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "browser2",
        telegram_bot_token="same-token",
        telegram_chat_id="-100222",
        messenger_name="kakao",
    )
    disabled = _settings(
        tmp_path,
        cdp_url="http://127.0.0.1:9224",
        browser_user_data_dir=tmp_path / "browser3",
        telegram_bot_token="same-token",
        telegram_chat_id="-100333",
        send_enabled=False,
    )
    configs = app_configs_from_settings([(0, telegram), (1, kakao), (2, disabled)])

    grouped = telegram_configs_by_token(configs)

    assert [config.telegram_chat_id for config in grouped["same-token"]] == ["-100111"]


def test_scheduled_run_skips_when_stop_requested(tmp_path, monkeypatch):
    ui = RiderBotUi.__new__(RiderBotUi)
    ui.messages = queue.Queue()
    stop_event = threading.Event()
    stop_event.set()
    calls = []
    monkeypatch.setattr("rider_crawl.ui.run_once", lambda config: calls.append(config))

    ui._run_once_background(0, _settings(tmp_path), stop_event)

    assert calls == []
    assert ui.messages.get_nowait() == ("status", "크롤링1 중지됨")


def test_different_tabs_can_enter_run_once_concurrently(tmp_path, monkeypatch):
    ui = RiderBotUi.__new__(RiderBotUi)
    ui.messages = queue.Queue()
    ui.crawl_locks_by_tab = {}
    ui.kakao_send_lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=1)
    entered: list[str] = []

    def fake_run_once(config, **_kwargs):
        entered.append(config.crawl_name)
        barrier.wait()
        return RunResult(message=config.crawl_name, sent=False, skipped=False, message_hash=config.crawl_name)

    monkeypatch.setattr("rider_crawl.ui.run_once", fake_run_once)

    first = threading.Thread(target=ui._run_once_background, args=(0, _settings(tmp_path, cdp_url="http://127.0.0.1:9222")))
    second = threading.Thread(target=ui._run_once_background, args=(1, _settings(tmp_path, cdp_url="http://127.0.0.1:9223")))
    first.start()
    second.start()
    first.join(2)
    second.join(2)

    assert sorted(entered) == ["크롤링1", "크롤링2"]


def test_same_tab_run_is_skipped_when_already_running(tmp_path, monkeypatch):
    ui = RiderBotUi.__new__(RiderBotUi)
    ui.messages = queue.Queue()
    ui.crawl_locks_by_tab = {0: threading.Lock()}
    ui.kakao_send_lock = threading.Lock()
    ui.crawl_locks_by_tab[0].acquire()
    calls = []
    monkeypatch.setattr("rider_crawl.ui.run_once", lambda config, **_kwargs: calls.append(config))

    try:
        ui._run_once_background(0, _settings(tmp_path))
    finally:
        ui.crawl_locks_by_tab[0].release()

    assert calls == []
    assert ("status", "크롤링1 이미 실행 중, 건너뜀") in list(ui.messages.queue)


def test_kakao_send_uses_common_lock(tmp_path, monkeypatch):
    ui = RiderBotUi.__new__(RiderBotUi)
    ui.messages = queue.Queue()
    ui.kakao_send_lock = threading.Lock()
    sent: list[str] = []
    finished = threading.Event()
    config = _settings(tmp_path, messenger_name="kakao").to_app_config(crawl_name="크롤링1", state_subdir="crawling1")
    monkeypatch.setattr("rider_crawl.ui.dispatch_text_message", lambda received, message: sent.append(received.crawl_name))

    ui.kakao_send_lock.acquire()
    worker = threading.Thread(
        target=lambda: (ui._send_message_with_kakao_lock(config, "hello"), finished.set()),
        daemon=True,
    )
    worker.start()
    time.sleep(0.05)
    assert finished.is_set() is False

    ui.kakao_send_lock.release()
    worker.join(1)

    assert finished.is_set() is True
    assert sent == ["크롤링1"]
    messages = list(ui.messages.queue)
    assert ("log", "크롤링1 카카오톡 전송 대기") in messages
    assert ("log", "크롤링1 카카오톡 전송 완료") in messages


def test_coerce_settings_uses_default_message_interval_when_field_is_missing(tmp_path):
    settings = coerce_settings(
        {
            "performance_url": "https://example.test/rider",
            "peak_dashboard_url": "",
            "browser_mode": "cdp",
            "cdp_url": "http://127.0.0.1:9222",
            "browser_user_data_dir": str(tmp_path / "browser"),
            "log_dir": str(tmp_path / "logs"),
            "kakao_chat_name": "실적봇",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": False,
            "send_only_on_change": False,
        }
    )

    assert settings.interval_minutes == 35


def test_coerce_settings_rejects_bad_interval():
    try:
        coerce_settings(
            {
                "performance_url": "https://example.test/rider",
                "peak_dashboard_url": "https://example.test/dashboard",
                "browser_mode": "cdp",
                "cdp_url": "http://127.0.0.1:9222",
                "browser_user_data_dir": "runtime/browser",
                "log_dir": "logs",
                "kakao_chat_name": "실적봇",
                "interval_minutes": "0",
                "page_timeout_seconds": "60000",
                "run_lock_timeout_seconds": "900",
                "headless": False,
                "send_enabled": False,
                "send_only_on_change": False,
            }
    )
    except ValueError as exc:
        assert "메세지 전송 간격" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_disable_unsupported_send_turns_off_send_on_mac():
    settings = coerce_settings(
        {
            "performance_url": "https://example.test/rider",
            "peak_dashboard_url": "https://example.test/dashboard",
            "browser_mode": "cdp",
            "cdp_url": "http://127.0.0.1:9222",
            "browser_user_data_dir": "runtime/browser",
            "log_dir": "logs",
            "kakao_chat_name": "실적봇",
            "messenger_name": "kakao",
            "interval_minutes": "35",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": True,
            "send_only_on_change": False,
        }
    )

    changed = disable_unsupported_send(settings, platform_name="Darwin")

    assert changed is True
    assert settings.send_enabled is False


def test_disable_unsupported_send_keeps_send_on_windows():
    settings = coerce_settings(
        {
            "performance_url": "https://example.test/rider",
            "peak_dashboard_url": "https://example.test/dashboard",
            "browser_mode": "cdp",
            "cdp_url": "http://127.0.0.1:9222",
            "browser_user_data_dir": "runtime/browser",
            "log_dir": "logs",
            "kakao_chat_name": "실적봇",
            "messenger_name": "kakao",
            "interval_minutes": "35",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": True,
            "send_only_on_change": False,
        }
    )

    changed = disable_unsupported_send(settings, platform_name="Windows")

    assert changed is False
    assert settings.send_enabled is True


def test_disable_unsupported_send_keeps_telegram_send_on_mac():
    settings = coerce_settings(
        {
            "performance_url": "https://example.test/rider",
            "peak_dashboard_url": "https://example.test/dashboard",
            "browser_mode": "cdp",
            "cdp_url": "http://127.0.0.1:9222",
            "browser_user_data_dir": "runtime/browser",
            "log_dir": "logs",
            "kakao_chat_name": "",
            "telegram_bot_token": "token",
            "telegram_chat_id": "-100123",
            "interval_minutes": "35",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": True,
            "send_only_on_change": False,
        }
    )

    changed = disable_unsupported_send(settings, platform_name="Darwin")

    assert changed is False
    assert settings.send_enabled is True


def test_app_configs_from_settings_names_tabs_and_skips_blank_urls(tmp_path):
    first = coerce_settings(
        {
            "performance_url": "https://example.test/first",
            "peak_dashboard_url": "",
            "browser_mode": "cdp",
            "cdp_url": "http://127.0.0.1:9222",
            "browser_user_data_dir": str(tmp_path / "browser1"),
            "log_dir": str(tmp_path / "logs"),
            "kakao_chat_name": "",
            "telegram_bot_token": "token",
            "telegram_chat_id": "-100123",
            "interval_minutes": "35",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": True,
            "send_only_on_change": False,
        }
    )
    second = coerce_settings(
        {
            "performance_url": "",
            "peak_dashboard_url": "",
            "browser_mode": "cdp",
            "cdp_url": "http://127.0.0.1:9223",
            "browser_user_data_dir": str(tmp_path / "browser2"),
            "log_dir": str(tmp_path / "logs"),
            "kakao_chat_name": "",
            "interval_minutes": "35",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": True,
            "send_only_on_change": False,
        }
    )

    active = active_crawling_settings([first, second])
    configs = app_configs_from_settings(active)

    assert active == [(0, first)]
    assert len(configs) == 1
    assert configs[0].crawl_name == "크롤링1"
    assert configs[0].state_subdir == "crawling1"
    assert configs[0].telegram_bot_token == "token"
    assert configs[0].telegram_chat_id == "-100123"


def _settings(
    tmp_path: Path,
    *,
    performance_url: str = "https://example.test/rider",
    browser_mode: str = "cdp",
    cdp_url: str = "http://127.0.0.1:9222",
    browser_user_data_dir: Path | None = None,
    telegram_bot_token: str = "token",
    telegram_chat_id: str = "-100123",
    telegram_message_thread_id: str = "",
    messenger_name: str = "telegram",
    send_enabled: bool = True,
):
    return coerce_settings(
        {
            "performance_url": performance_url,
            "peak_dashboard_url": "",
            "browser_mode": browser_mode,
            "cdp_url": cdp_url,
            "browser_user_data_dir": str(browser_user_data_dir or tmp_path / "browser"),
            "log_dir": str(tmp_path / "logs"),
            "kakao_chat_name": "",
            "telegram_bot_token": telegram_bot_token,
            "telegram_chat_id": telegram_chat_id,
            "telegram_message_thread_id": telegram_message_thread_id,
            "messenger_name": messenger_name,
            "interval_minutes": "35",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": send_enabled,
            "send_only_on_change": False,
        }
    )
