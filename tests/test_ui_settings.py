import math
from pathlib import Path

from rider_crawl.config import DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL
from rider_crawl.ui_settings import UiSettings, UiSettingsStore


def test_ui_settings_defaults_to_baemin_platform():
    settings = UiSettings.defaults()

    assert settings.platform_name == "baemin"
    assert settings.peak_dashboard_url == ""


def test_ui_settings_save_and_load_round_trip_keeps_platform(tmp_path):
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettings.defaults()
    settings.platform_name = "coupang"
    settings.performance_url = "https://partner.coupangeats.com/page/rider-performance"
    settings.peak_dashboard_url = "https://partner.coupangeats.com/page/peak-dashboard"

    store.save(settings)
    loaded = store.load()

    assert loaded.platform_name == "coupang"
    assert loaded.performance_url == "https://partner.coupangeats.com/page/rider-performance"
    assert loaded.peak_dashboard_url == "https://partner.coupangeats.com/page/peak-dashboard"


def test_ui_settings_load_infers_coupang_from_legacy_coupang_url(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://partner.coupangeats.com/page/rider-performance",
          "peak_dashboard_url": "https://partner.coupangeats.com/page/peak-dashboard"
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.platform_name == "coupang"


def test_ui_settings_defaults_are_safe_for_first_run():
    settings = UiSettings.defaults()

    assert settings.performance_url == DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL
    assert settings.peak_dashboard_url == ""
    assert settings.browser_mode == "cdp"
    assert settings.cdp_url == "http://127.0.0.1:9222"
    assert settings.kakao_chat_name == ""
    assert settings.interval_minutes == 35
    assert settings.send_enabled is False
    assert settings.send_only_on_change is False
    assert settings.telegram_bot_token == ""
    assert settings.telegram_chat_id == ""
    assert settings.telegram_message_thread_id == ""
    assert settings.run_lock_timeout_seconds == 900


def test_additional_tab_defaults_do_not_inherit_first_center():
    settings = UiSettings.default_for_tab(2)

    assert settings.performance_url == ""
    assert settings.baemin_center_name == ""
    assert settings.baemin_center_id == ""
    assert settings.cdp_url == "http://127.0.0.1:9223"
    assert settings.browser_user_data_dir == Path("runtime/browser-profile-2")


def test_ui_settings_save_and_load_round_trip(tmp_path):
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettings.defaults()
    settings.kakao_chat_name = "실적봇_의정부남부"
    settings.interval_minutes = 20
    settings.send_enabled = True
    settings.browser_mode = "persistent"
    settings.cdp_url = "http://127.0.0.1:9333"
    settings.browser_user_data_dir = Path("C:/rider_crawl/browser-profile")
    settings.telegram_bot_token = "token"
    settings.telegram_chat_id = "-100123"
    settings.telegram_message_thread_id = "77"

    store.save(settings)

    loaded = store.load()
    assert loaded.kakao_chat_name == "실적봇_의정부남부"
    assert loaded.interval_minutes == 20
    assert loaded.send_enabled is True
    assert loaded.browser_mode == "persistent"
    assert loaded.cdp_url == "http://127.0.0.1:9333"
    assert loaded.browser_user_data_dir == Path("C:/rider_crawl/browser-profile")
    assert loaded.telegram_bot_token == "token"
    assert loaded.telegram_chat_id == "-100123"
    assert loaded.telegram_message_thread_id == "77"


def test_ui_settings_load_all_migrates_single_settings_to_nine_tabs(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://example.test/delivery/history",
          "telegram_bot_token": "token",
          "telegram_chat_id": "-100123"
        }
        """,
        encoding="utf-8",
    )

    settings = UiSettingsStore(path).load_all()

    assert len(settings) == 9
    assert settings[0].performance_url == "https://example.test/delivery/history"
    assert settings[0].telegram_bot_token == "token"
    assert settings[0].telegram_chat_id == "-100123"
    assert settings[0].telegram_message_thread_id == ""
    assert settings[0].cdp_url == "http://127.0.0.1:9222"
    assert settings[1].performance_url == ""
    assert settings[1].cdp_url == "http://127.0.0.1:9223"
    assert settings[8].cdp_url == "http://127.0.0.1:9230"


def test_ui_settings_save_all_and_load_all_round_trip(tmp_path):
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettingsStore(tmp_path / "missing.json").load_all()
    settings[0].telegram_bot_token = "token"
    settings[0].telegram_chat_id = "-100123"
    settings[0].telegram_message_thread_id = "77"
    settings[1].performance_url = "https://example.test/second"
    settings[1].browser_user_data_dir = Path("runtime/browser-profile-2")

    store.save_all(settings)

    loaded = store.load_all()
    assert len(loaded) == 9
    assert loaded[0].telegram_bot_token == "token"
    assert loaded[0].telegram_chat_id == "-100123"
    assert loaded[0].telegram_message_thread_id == "77"
    assert loaded[1].performance_url == "https://example.test/second"
    assert loaded[1].browser_user_data_dir == Path("runtime/browser-profile-2")


def test_ui_settings_load_keeps_legacy_minute_interval(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://partner.coupangeats.com/page/rider-performance",
          "interval_minutes": 35
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.interval_minutes == 35


def test_ui_settings_load_migrates_legacy_refresh_seconds_to_message_minutes(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://partner.coupangeats.com/page/rider-performance",
          "refresh_interval_seconds": 125
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.interval_minutes == math.ceil(125 / 60)


def test_ui_settings_load_migrates_legacy_kakao_without_messenger_name_when_send_enabled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://example.test/delivery/history",
          "kakao_chat_name": "실적봇_의정부남부",
          "send_enabled": true
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.messenger_name == "kakao"
    assert loaded.kakao_chat_name == "실적봇_의정부남부"
    assert loaded.send_enabled is True


def test_ui_settings_load_migrates_legacy_kakao_without_messenger_name_when_send_disabled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://example.test/delivery/history",
          "kakao_chat_name": "실적봇_의정부남부",
          "send_enabled": false
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.messenger_name == "kakao"
    assert loaded.kakao_chat_name == "실적봇_의정부남부"
    assert loaded.send_enabled is False


def test_ui_settings_load_keeps_telegram_default_for_ambiguous_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://example.test/delivery/history",
          "telegram_bot_token": "token",
          "telegram_chat_id": "-100123"
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.messenger_name == "telegram"


def test_ui_settings_load_keeps_explicit_messenger_name_over_legacy_kakao_heuristic(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://example.test/delivery/history",
          "kakao_chat_name": "실적봇_의정부남부",
          "messenger_name": "telegram"
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.messenger_name == "telegram"


def test_ui_settings_convert_to_app_config(tmp_path):
    settings = UiSettings.defaults()
    settings.performance_url = "https://example.test/rider"
    settings.browser_mode = "cdp"
    settings.cdp_url = "http://127.0.0.1:9223"
    settings.browser_user_data_dir = tmp_path / "browser"
    settings.kakao_chat_name = "실적봇_의정부남부"
    settings.log_dir = tmp_path / "logs"
    settings.send_enabled = True
    settings.send_only_on_change = True
    settings.telegram_bot_token = "token"
    settings.telegram_chat_id = "-100123"
    settings.telegram_message_thread_id = "77"

    config = settings.to_app_config()

    assert config.coupang_eats_url == "https://example.test/rider"
    assert config.browser_mode == "cdp"
    assert config.cdp_url == "http://127.0.0.1:9223"
    assert config.browser_user_data_dir == tmp_path / "browser"
    assert config.kakao_chat_name == "실적봇_의정부남부"
    assert config.log_dir == tmp_path / "logs"
    assert config.send_enabled is True
    assert config.send_only_on_change is True
    assert config.telegram_bot_token == "token"
    assert config.telegram_chat_id == "-100123"
    assert config.telegram_message_thread_id == "77"


def test_ui_settings_to_app_config_uses_ui_2fa_fields():
    # 쿠팡 자동 2FA 복구 설정은 (.env가 아니라) UI에서 입력받아 탭별로 저장한 값을 쓴다.
    settings = UiSettings.defaults()
    settings.coupang_auto_email_2fa_enabled = True
    settings.coupang_login_id = "worker-id"
    settings.coupang_login_password = "worker-password"
    settings.gmail_2fa_query = "from:(no-reply@coupang.com) subject:(인증)"
    settings.gmail_credentials_path = "C:/safe/credentials.gmail.json"
    settings.gmail_token_path = "C:/safe/token.gmail.json"

    config = settings.to_app_config()

    assert config.coupang_auto_email_2fa_enabled is True
    assert config.coupang_login_id == "worker-id"
    assert config.coupang_login_password == "worker-password"
    assert config.gmail_2fa_query == "from:(no-reply@coupang.com) subject:(인증)"
    assert config.gmail_credentials_path == Path("C:/safe/credentials.gmail.json")
    assert config.gmail_token_path == Path("C:/safe/token.gmail.json")


def test_ui_settings_to_app_config_2fa_does_not_read_env(monkeypatch):
    # 정책 변경: to_app_config는 더 이상 환경변수/.env를 읽지 않는다. env가 켜져 있어도
    # UI 설정이 비활성이면 비활성이어야 한다.
    monkeypatch.setenv("COUPANG_AUTO_EMAIL_2FA_ENABLED", "true")
    monkeypatch.setenv("GMAIL_2FA_QUERY", "from:(env@coupang.com)")

    config = UiSettings.defaults().to_app_config()

    assert config.coupang_auto_email_2fa_enabled is False
    assert config.gmail_2fa_query != "from:(env@coupang.com)"


def test_ui_settings_to_app_config_defaults_2fa_disabled():
    config = UiSettings.defaults().to_app_config()

    assert config.coupang_auto_email_2fa_enabled is False
    assert config.coupang_login_id == ""
    assert config.coupang_login_password == ""
