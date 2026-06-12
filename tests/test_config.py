import dataclasses
from pathlib import Path

import pytest

from rider_crawl.config import (
    DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL,
    DEFAULT_BAEMIN_CENTER_NAME,
    AppConfig,
    _require_coupang_center,
    coupang_center_name_risk,
)


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


# ── Story 2.3: 플랫폼 중립 Target 필드(read-only alias) + 비차단 위험 분류기 ──


def _coupang_app_config(*, baemin_center_name: str = "강남센터", crawl_name: str = "크롤링2") -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/peak-dashboard",
        baemin_center_name=baemin_center_name,
        baemin_center_id="DP000",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=Path("browser"),
        headless=False,
        kakao_chat_name="",
        log_dir=Path("logs"),
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        platform_name="coupang",
        crawl_name=crawl_name,
    )


def test_app_config_neutral_accessors_alias_legacy_fields():
    # AC1: AppConfig가 같은 중립 이름으로 기존 legacy 필드를 그대로 읽는다(동일 Target 필드 집합).
    config = _coupang_app_config(baemin_center_name="강남센터", crawl_name="크롤링2")

    assert config.primary_url == config.coupang_eats_url
    assert config.center_name == config.baemin_center_name
    assert config.target_external_id == config.baemin_center_id
    assert config.display_name == config.crawl_name


def test_app_config_neutral_accessors_return_raw_value_without_stripping():
    # Task 2: 중립 접근자는 순수 읽기다 — strip 없이 원본 값을 그대로 돌려준다.
    config = _coupang_app_config(baemin_center_name="  강남센터  ")

    assert config.center_name == "  강남센터  "


def test_coupang_center_name_risk_flags_empty_for_coupang():
    # AC3 (a): 쿠팡 + 빈 기대 센터/상점명 → 위험으로 분류(비차단).
    is_risky, reason = coupang_center_name_risk("coupang", "")

    assert is_risky is True
    assert reason


def test_coupang_center_name_risk_flags_baemin_default_for_coupang():
    # AC3 (b): 쿠팡 + 배민 기본값 → 위험(화면 센터명과 절대 일치하지 않음).
    is_risky, reason = coupang_center_name_risk("coupang", DEFAULT_BAEMIN_CENTER_NAME)

    assert is_risky is True
    assert reason


def test_coupang_center_name_risk_allows_real_center_for_coupang():
    # AC3 (c): 쿠팡 + 실제 가공 센터명 → 위험 아님.
    assert coupang_center_name_risk("coupang", "강남센터") == (False, "")


def test_coupang_center_name_risk_ignores_non_coupang_platform():
    # AC3 (d)/AC6: 배민 탭은 이 분류기에서 위험으로 보지 않는다(어떤 center_name이든).
    assert coupang_center_name_risk("baemin", "") == (False, "")
    assert coupang_center_name_risk("baemin", DEFAULT_BAEMIN_CENTER_NAME) == (False, "")


def test_coupang_center_name_risk_is_non_blocking_and_never_raises():
    # AC3 (e)/AC6: 분류만 하고 차단하지 않는다 — 어떤 입력에도 예외를 던지지 않고
    # (is_risky: bool, reason: str) 튜플만 반환한다(상태 enum 미사용 — Story 2.5 소유).
    for platform, name in (
        ("coupang", ""),
        ("coupang", DEFAULT_BAEMIN_CENTER_NAME),
        ("coupang", "강남센터"),
        ("baemin", ""),
    ):
        result = coupang_center_name_risk(platform, name)
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], bool) and isinstance(result[1], str)


def test_coupang_center_name_risk_normalizes_platform_and_strips_center_name():
    # 쿠팡 판정은 공백/대소문자 무시, center_name은 strip 후 empty/배민-기본값 판정.
    assert coupang_center_name_risk("  Coupang  ", "   ")[0] is True
    assert coupang_center_name_risk("coupang", f"  {DEFAULT_BAEMIN_CENTER_NAME}  ")[0] is True


def test_coupang_center_name_risk_distinguishes_empty_from_baemin_default_reason():
    # AC3 #5: 두 위험 조건(empty / 배민-기본값)은 운영자가 어느 쪽인지 알 수 있도록 서로
    # 다른 사유 문자열을 돌려줘야 한다 — 한 사유로 뭉뚱그리지 않는다.
    _, empty_reason = coupang_center_name_risk("coupang", "")
    _, default_reason = coupang_center_name_risk("coupang", DEFAULT_BAEMIN_CENTER_NAME)

    assert empty_reason and default_reason
    assert empty_reason != default_reason


@pytest.mark.parametrize(
    "center_name",
    [
        "",
        "   ",
        DEFAULT_BAEMIN_CENTER_NAME,
        f"  {DEFAULT_BAEMIN_CENTER_NAME}  ",
        "강남센터",
        "제이앤에이치플러스 의정부남부",
    ],
)
def test_risk_classifier_agrees_with_require_coupang_center_single_source(center_name):
    # AC3 / Task 3 (드리프트 방지): 비차단 분류기와 env/CLI raise 경로는 같은 조건 단일
    # 소스(_coupang_center_name_issue)를 공유한다. 한쪽이 막는 입력은 다른 쪽도 위험으로
    # 분류해야 한다 — 같은 두 조건이 두 경로에서 어긋나면(드리프트) 실패한다.
    raised = False
    try:
        _require_coupang_center(center_name)
    except ValueError:
        raised = True

    is_risky, _ = coupang_center_name_risk("coupang", center_name)
    assert is_risky is raised


def test_app_config_neutral_accessors_are_not_dataclass_fields():
    # AC2/AC4: 중립 접근자는 @property라 dataclass 필드가 아니다 — dataclasses.fields와
    # asdict 어디에도 잡히지 않아 직렬화/마이그레이션에 새 키를 만들지 않는다.
    config = _coupang_app_config()

    field_names = {f.name for f in dataclasses.fields(config)}
    serialized = dataclasses.asdict(config)
    for neutral in ("primary_url", "center_name", "target_external_id", "display_name"):
        assert neutral not in field_names
        assert neutral not in serialized


def test_app_config_neutral_accessors_are_read_only_on_frozen_config():
    # AC4: AppConfig(frozen=True)에서도 중립 접근자는 읽을 수 있고(@property는 메서드라
    # frozen과 무관), 읽기 전용 별칭이라 값을 덮어쓸 수 없다(저장 정본은 legacy 필드).
    config = _coupang_app_config(baemin_center_name="강남센터")

    assert config.center_name == "강남센터"
    with pytest.raises(AttributeError):
        config.center_name = "다른센터"
