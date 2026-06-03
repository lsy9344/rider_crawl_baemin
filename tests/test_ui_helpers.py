from pathlib import Path

from rider_crawl.ui import coerce_settings, disable_unsupported_send


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
