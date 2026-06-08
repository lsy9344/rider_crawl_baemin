from pathlib import Path

from rider_crawl.config import AppConfig


def test_app_config_reads_environment_values(monkeypatch):
    monkeypatch.setenv("BAEMIN_DELIVERY_HISTORY_URL", "https://example.test/delivery/history")
    monkeypatch.setenv("BROWSER_USER_DATA_DIR", "C:\\rider_crawl\\browser-profile")
    monkeypatch.setenv("HEADLESS", "true")
    monkeypatch.setenv("KAKAO_CHAT_NAME", "실적봇_의정부남부")
    monkeypatch.setenv("LOG_DIR", "C:\\rider_crawl\\logs")
    monkeypatch.setenv("SEND_ENABLED", "false")
    monkeypatch.setenv("SEND_ONLY_ON_CHANGE", "true")
    monkeypatch.setenv("TIMEZONE", "Asia/Seoul")
    monkeypatch.setenv("RUN_LOCK_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("PAGE_TIMEOUT_SECONDS", "30000")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("TELEGRAM_MESSAGE_THREAD_ID", "77")
    monkeypatch.setenv("MESSENGER_NAME", "telegram")

    config = AppConfig.from_env()

    assert config.coupang_eats_url == "https://example.test/delivery/history"
    assert config.browser_mode == "cdp"
    assert config.cdp_url == "http://127.0.0.1:9222"
    assert config.browser_user_data_dir == Path("C:\\rider_crawl\\browser-profile")
    assert config.headless is True
    assert config.kakao_chat_name == "실적봇_의정부남부"
    assert config.log_dir == Path("C:\\rider_crawl\\logs")
    assert config.send_enabled is False
    assert config.send_only_on_change is True
    assert config.timezone == "Asia/Seoul"
    assert config.run_lock_timeout_seconds == 120
    assert config.page_timeout_seconds == 30000
    assert config.telegram_bot_token == "token"
    assert config.telegram_chat_id == "-100123"
    assert config.telegram_message_thread_id == "77"
    assert config.messenger_name == "telegram"


def test_app_config_defaults_to_safe_dry_run(monkeypatch):
    for key in (
        "COUPANG_EATS_URL",
        "BAEMIN_DELIVERY_HISTORY_URL",
        "BROWSER_USER_DATA_DIR",
        "HEADLESS",
        "KAKAO_CHAT_NAME",
        "LOG_DIR",
        "SEND_ENABLED",
        "SEND_ONLY_ON_CHANGE",
        "TIMEZONE",
        "RUN_LOCK_TIMEOUT_SECONDS",
        "PAGE_TIMEOUT_SECONDS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_MESSAGE_THREAD_ID",
        "MESSENGER_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    config = AppConfig.from_env()

    assert config.send_enabled is False
    assert (
        config.coupang_eats_url
        == "https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
    )
    assert config.browser_mode == "cdp"
    assert config.cdp_url == "http://127.0.0.1:9222"
    assert config.kakao_chat_name == ""
    assert config.log_dir == Path("logs")
    assert config.telegram_bot_token == ""
    assert config.telegram_chat_id == ""
    assert config.telegram_message_thread_id == ""
    assert config.messenger_name == "telegram"
