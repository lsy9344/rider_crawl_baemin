from pathlib import Path

from rider_crawl.ui import (
    DEFAULT_WINDOW_GEOMETRY,
    MIN_WINDOW_HEIGHT,
    PREVIEW_TEXT_HEIGHT,
    active_crawling_settings,
    app_configs_from_settings,
    coerce_settings,
    disable_unsupported_send,
)


def test_ui_defaults_leave_more_vertical_room_for_preview_log():
    assert DEFAULT_WINDOW_GEOMETRY == "900x900"
    assert MIN_WINDOW_HEIGHT >= 780
    assert PREVIEW_TEXT_HEIGHT >= 22


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
    assert settings.interval_minutes == 12
    assert settings.send_enabled is False
    assert settings.send_only_on_change is True


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
