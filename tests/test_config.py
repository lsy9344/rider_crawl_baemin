from pathlib import Path

import pytest

from rider_crawl.config import DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL, DEFAULT_BAEMIN_CENTER_NAME, AppConfig


def test_app_config_reads_environment_values(monkeypatch):
    monkeypatch.delenv("PERFORMANCE_PLATFORM", raising=False)
    monkeypatch.delenv("PERFORMANCE_URL", raising=False)
    monkeypatch.setenv("BAEMIN_DELIVERY_HISTORY_URL", "https://example.test/delivery/history")
    monkeypatch.setenv("BAEMIN_CENTER_NAME", "강남센터")
    monkeypatch.setenv("BAEMIN_CENTER_ID", "DP123")
    monkeypatch.setenv("BROWSER_MODE", "cdp")
    monkeypatch.setenv("CDP_URL", "http://127.0.0.1:9223")
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
    monkeypatch.setenv("CRAWL_NAME", "크롤링2")
    monkeypatch.setenv("STATE_SUBDIR", "crawling2")

    config = AppConfig.from_env()

    assert config.coupang_eats_url == "https://example.test/delivery/history"
    assert config.baemin_center_name == "강남센터"
    assert config.baemin_center_id == "DP123"
    assert config.browser_mode == "cdp"
    assert config.cdp_url == "http://127.0.0.1:9223"
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
    assert config.crawl_name == "크롤링2"
    assert config.state_subdir == "crawling2"


def test_app_config_defaults_to_safe_dry_run(monkeypatch):
    for key in (
        "PERFORMANCE_PLATFORM",
        "PERFORMANCE_URL",
        "COUPANG_EATS_URL",
        "BAEMIN_DELIVERY_HISTORY_URL",
        "BAEMIN_CENTER_NAME",
        "BAEMIN_CENTER_ID",
        "BROWSER_MODE",
        "CDP_URL",
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
        "CRAWL_NAME",
        "STATE_SUBDIR",
        "COUPANG_CREDENTIALS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)

    config = AppConfig.from_env()

    assert config.send_enabled is False
    assert config.coupang_eats_url == DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL
    assert config.browser_mode == "cdp"
    assert config.cdp_url == "http://127.0.0.1:9222"
    assert config.kakao_chat_name == ""
    assert config.log_dir == Path("logs")
    assert config.telegram_bot_token == ""
    assert config.telegram_chat_id == ""
    assert config.telegram_message_thread_id == ""
    assert config.messenger_name == "telegram"
    assert config.run_lock_timeout_seconds == 900
    assert config.crawl_name == ""
    assert config.state_subdir == ""
    assert config.coupang_credentials_path == Path("secrets/google/coupang.credentials.json")


def test_app_config_reads_coupang_credentials_path(monkeypatch):
    monkeypatch.setenv("COUPANG_CREDENTIALS_PATH", "C:/safe/coupang.credentials.json")

    config = AppConfig.from_env()

    assert config.coupang_credentials_path == Path("C:/safe/coupang.credentials.json")


def test_app_config_reads_coupang_environment_values(monkeypatch):
    monkeypatch.delenv("PERFORMANCE_URL", raising=False)
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.setenv("COUPANG_EATS_URL", "https://example.test/peak-dashboard")
    monkeypatch.setenv("PEAK_DASHBOARD_URL", "https://example.test/peak-dashboard")
    monkeypatch.setenv("BAEMIN_CENTER_NAME", "쿠팡강남센터")

    config = AppConfig.from_env()

    assert config.platform_name == "coupang"
    # 쿠팡은 로그인 직후 열리는 peak-dashboard 한 페이지만 주 URL로 읽는다.
    assert config.coupang_eats_url == "https://example.test/peak-dashboard"
    # 보조 URL(peak_dashboard_url)은 더 이상 쓰지 않으므로 PEAK_DASHBOARD_URL env가 있어도 빈 값이다.
    assert config.peak_dashboard_url == ""
    # 쿠팡 탭은 BAEMIN_CENTER_NAME을 기대 센터/상점명으로 재사용한다.
    assert config.baemin_center_name == "쿠팡강남센터"


def test_app_config_coupang_requires_center_name(monkeypatch):
    # 쿠팡에서 BAEMIN_CENTER_NAME 미설정이면 배민 기본값을 넣지 않고 설정 오류를 낸다.
    monkeypatch.delenv("PERFORMANCE_URL", raising=False)
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.setenv("COUPANG_EATS_URL", "https://example.test/rider-performance")
    monkeypatch.setenv("PEAK_DASHBOARD_URL", "https://example.test/peak-dashboard")
    monkeypatch.delenv("BAEMIN_CENTER_NAME", raising=False)

    with pytest.raises(ValueError, match="BAEMIN_CENTER_NAME"):
        AppConfig.from_env()


def test_app_config_coupang_rejects_default_baemin_center_name(monkeypatch):
    # 플랫폼만 쿠팡으로 바꾸고 배민 기본 센터명을 그대로 두면 크롤링이 항상 실패하므로
    # 실행 전에 설정 오류로 막는다.
    monkeypatch.delenv("PERFORMANCE_URL", raising=False)
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.setenv("COUPANG_EATS_URL", "https://example.test/rider-performance")
    monkeypatch.setenv("PEAK_DASHBOARD_URL", "https://example.test/peak-dashboard")
    monkeypatch.setenv("BAEMIN_CENTER_NAME", DEFAULT_BAEMIN_CENTER_NAME)

    with pytest.raises(ValueError, match="배민 기본값"):
        AppConfig.from_env()


def _config_with_log_dir(log_dir: str) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://example.test/history",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=Path("browser"),
        headless=False,
        kakao_chat_name="",
        log_dir=Path(log_dir),
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )


def test_runtime_dir_defaults_to_cwd_runtime_for_default_log_dir():
    # 기본값(LOG_DIR=logs)에서는 종전과 동일하게 cwd의 ``runtime``을 쓴다.
    assert _config_with_log_dir("logs").runtime_dir == Path("runtime")


def test_runtime_dir_sits_next_to_named_logs_dir():
    config = _config_with_log_dir("C:/rider_crawl/logs")
    assert config.runtime_dir == Path("C:/rider_crawl/runtime")


def test_custom_log_dirs_get_isolated_runtime_dirs():
    # 커스텀 로그 경로로 계정을 나누면 runtime(lock/last-hash)도 계정별로 분리돼야
    # 한다. 예전에는 log_dir.name != "logs"이면 둘 다 cwd ``runtime``으로 떨어져
    # lock/hash가 섞였다.
    first = _config_with_log_dir("C:/acct1/custom-log")
    second = _config_with_log_dir("C:/acct2/custom-log")

    assert first.runtime_dir == Path("C:/acct1/runtime")
    assert second.runtime_dir == Path("C:/acct2/runtime")
    assert first.runtime_dir != second.runtime_dir
    assert first.state_dir != second.state_dir


def test_app_config_defaults_to_baemin_platform(monkeypatch):
    for key in (
        "PERFORMANCE_PLATFORM",
        "PERFORMANCE_URL",
        "COUPANG_EATS_URL",
        "BAEMIN_DELIVERY_HISTORY_URL",
        "PEAK_DASHBOARD_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    config = AppConfig.from_env()

    assert config.platform_name == "baemin"
    assert "deliverycenter.baemin.com" in config.coupang_eats_url
    # 배민은 쿠팡 전용 보조 URL을 채우지 않아야 UI 배민 설정과 scope hash가 맞는다.
    assert config.peak_dashboard_url == ""


def test_app_config_baemin_ignores_peak_dashboard_url_env(monkeypatch):
    for key in ("PERFORMANCE_PLATFORM", "PERFORMANCE_URL", "COUPANG_EATS_URL", "BAEMIN_DELIVERY_HISTORY_URL"):
        monkeypatch.delenv(key, raising=False)
    # 배민이면 PEAK_DASHBOARD_URL env가 있어도 빈 값이어야 UI 배민 설정과 scope hash가 맞는다.
    monkeypatch.setenv("PEAK_DASHBOARD_URL", "https://example.test/peak-dashboard")

    config = AppConfig.from_env()

    assert config.platform_name == "baemin"
    assert config.peak_dashboard_url == ""


def test_app_config_coupang_platform_uses_coupang_defaults(monkeypatch):
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.delenv("PERFORMANCE_URL", raising=False)
    monkeypatch.delenv("COUPANG_EATS_URL", raising=False)
    monkeypatch.delenv("PEAK_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("BAEMIN_CENTER_NAME", "쿠팡강남센터")

    config = AppConfig.from_env()

    # 쿠팡 주 URL 기본값은 로그인 직후 열리는 peak-dashboard다(보조 URL은 사용하지 않음).
    assert config.coupang_eats_url == "https://partner.coupangeats.com/page/peak-dashboard"
    assert config.peak_dashboard_url == ""
    # 쿠팡에서는 배민 센터 ID 기본값을 넣지 않는다(쿠팡 탭에서 사용하지 않음).
    assert config.baemin_center_id == ""
