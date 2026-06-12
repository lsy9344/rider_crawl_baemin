import math
from pathlib import Path

import pytest

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


# ── Story 2.1: customer/target ID 발급 + legacy_alias 보존 ──


def test_ui_settings_round_trip_preserves_id_and_alias_fields(tmp_path):
    # AC1: 신규 5개 필드가 save/load 라운드트립에서 손실 없이 보존된다(모두 채워 두면
    # 재발급되지 않으므로 입력값이 그대로 유지된다).
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettings.defaults()
    settings.customer_id = "cust-1"
    settings.customer_name = "의정부남부점"
    settings.platform_account_id = "pa-1"
    settings.monitoring_target_id = "mt-1"
    settings.legacy_alias = "크롤링1"

    store.save(settings)
    loaded = store.load()

    assert loaded.customer_id == "cust-1"
    assert loaded.customer_name == "의정부남부점"
    assert loaded.platform_account_id == "pa-1"
    assert loaded.monitoring_target_id == "mt-1"
    assert loaded.legacy_alias == "크롤링1"


def test_ui_settings_save_all_load_all_preserves_id_and_alias_fields(tmp_path):
    # AC1: save_all/load_all 라운드트립 보존 + 저장 JSON은 ensure_ascii=False·"crawlings" 구조 유지.
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettingsStore(tmp_path / "missing.json").load_all()
    settings[0].customer_id = "cust-1"
    settings[0].customer_name = "의정부남부점"
    settings[0].platform_account_id = "pa-1"
    settings[0].monitoring_target_id = "mt-1"
    settings[0].legacy_alias = "크롤링1"

    store.save_all(settings)
    loaded = store.load_all()

    assert loaded[0].customer_id == "cust-1"
    assert loaded[0].customer_name == "의정부남부점"
    assert loaded[0].platform_account_id == "pa-1"
    assert loaded[0].monitoring_target_id == "mt-1"
    assert loaded[0].legacy_alias == "크롤링1"

    text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert '"crawlings"' in text
    assert "의정부남부점" in text  # ensure_ascii=False: 한글이 escape 되지 않는다
    assert "\\u" not in text


def test_load_all_issues_stable_monitoring_target_id_across_reloads(tmp_path):
    # AC3 #6: ID 없던 활성 탭을 처음 로드하면 ID가 발급·영속화되고, 재로드 시 동일 ID가 유지된다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "https://example.test/delivery/history"}]}',
        encoding="utf-8",
    )
    store = UiSettingsStore(path)

    first = store.load_all()
    issued_id = first[0].monitoring_target_id
    assert issued_id != ""
    assert len(issued_id) == 32  # uuid4().hex (불투명 ID)

    second = store.load_all()
    assert second[0].monitoring_target_id == issued_id
    assert second[0].customer_id == first[0].customer_id
    assert second[0].platform_account_id == first[0].platform_account_id


def test_load_all_preserves_existing_ids_without_reissue(tmp_path):
    # AC3 #7: 이미 ID가 있는 탭은 idempotent하게 그대로 보존하고 절대 재발급하지 않는다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "https://example.test/x",'
        ' "monitoring_target_id": "mt-fixed", "customer_id": "cust-fixed",'
        ' "platform_account_id": "pa-fixed", "legacy_alias": "내가정한별칭"}]}',
        encoding="utf-8",
    )
    store = UiSettingsStore(path)

    loaded = store.load_all()

    assert loaded[0].monitoring_target_id == "mt-fixed"
    assert loaded[0].customer_id == "cust-fixed"
    assert loaded[0].platform_account_id == "pa-fixed"
    assert loaded[0].legacy_alias == "내가정한별칭"


def test_load_all_issues_ids_only_for_active_tabs(tmp_path):
    # AC3 #8: 활성 탭(performance_url 있음)에만 발급하고, 빈 filler 탭은 ID를 만들지 않는다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "https://example.test/delivery/history"}]}',
        encoding="utf-8",
    )

    settings = UiSettingsStore(path).load_all()

    assert settings[0].monitoring_target_id != ""
    for filler in settings[1:]:
        assert filler.performance_url == ""
        assert filler.monitoring_target_id == ""
        assert filler.customer_id == ""
        assert filler.platform_account_id == ""
        assert filler.legacy_alias == ""


def test_load_all_does_not_create_file_when_missing(tmp_path):
    # AC3 가드: 파일이 없으면 발급/영속화하지 않는다(새 파일을 만들지 않는다).
    path = tmp_path / "does-not-exist.json"

    settings = UiSettingsStore(path).load_all()

    assert path.exists() is False
    assert len(settings) == 9
    assert settings[0].monitoring_target_id == ""


def test_load_all_seeds_legacy_alias_from_tab_index_and_preserves_existing(tmp_path):
    # AC2 #4: alias 없는 탭은 크롤링{index}로 seed, 이미 있는 alias는 보존(표시/보조 식별 전용).
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": ['
        '{"performance_url": "https://example.test/a"},'
        '{"performance_url": "https://example.test/b", "legacy_alias": "이미있는별칭"}'
        "]}",
        encoding="utf-8",
    )

    settings = UiSettingsStore(path).load_all()

    assert settings[0].legacy_alias == "크롤링1"
    assert settings[1].legacy_alias == "이미있는별칭"


def test_load_single_issues_stable_id_for_single_object_file(tmp_path):
    # AC3: 단일 객체 파일도 load()에서 활성 탭이면 ID를 발급·영속화해 재로드 시 동일 ID를 읽는다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"performance_url": "https://example.test/delivery/history",'
        ' "telegram_bot_token": "token", "telegram_chat_id": "-100123"}',
        encoding="utf-8",
    )
    store = UiSettingsStore(path)

    first = store.load()
    assert first.monitoring_target_id != ""
    assert first.legacy_alias == "크롤링1"

    second = store.load()
    assert second.monitoring_target_id == first.monitoring_target_id


def test_load_all_issues_three_distinct_independent_ids(tmp_path):
    # AC3 / Dev Notes: customer_id·platform_account_id·monitoring_target_id는 각각 독립 발급
    # 하며 같은 값을 재사용하지 않는다 — 세 ID는 서로 달라야 한다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "https://example.test/delivery/history"}]}',
        encoding="utf-8",
    )

    tab = UiSettingsStore(path).load_all()[0]

    ids = {tab.customer_id, tab.platform_account_id, tab.monitoring_target_id}
    assert "" not in ids
    assert len(ids) == 3  # 셋 다 서로 다른 불투명 ID


def test_load_all_does_not_auto_issue_customer_name(tmp_path):
    # Dev Notes: customer_name은 사람 표시명이라 자동 발급 대상이 아니다 — 활성 탭이어도 비워 둔다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "https://example.test/delivery/history"}]}',
        encoding="utf-8",
    )

    tab = UiSettingsStore(path).load_all()[0]

    assert tab.monitoring_target_id != ""  # 다른 ID는 발급됐는데도
    assert tab.customer_name == ""  # customer_name만은 비어 있다


def test_load_all_fills_only_missing_ids_and_preserves_existing(tmp_path):
    # AC3 #7: idempotency는 레코드가 아니라 필드 단위다. 일부 ID만 있는 탭을 로드하면 기존
    # 값은 보존하고 비어 있는 ID만 새로 발급한다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "https://example.test/x",'
        ' "monitoring_target_id": "mt-keep"}]}',
        encoding="utf-8",
    )

    tab = UiSettingsStore(path).load_all()[0]

    assert tab.monitoring_target_id == "mt-keep"  # 기존 값 보존
    assert tab.customer_id != "" and len(tab.customer_id) == 32  # 누락분만 발급
    assert tab.platform_account_id != "" and len(tab.platform_account_id) == 32
    assert tab.legacy_alias == "크롤링1"  # alias도 seed


def test_load_all_treats_whitespace_only_url_as_inactive(tmp_path):
    # AC3 #8: 활성 판정은 performance_url.strip()이다 — 공백뿐인 URL은 비활성으로 보고
    # ID를 발급하지 않으며, 발급이 없으니 파일도 다시 쓰지 않는다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "   "}]}',
        encoding="utf-8",
    )
    before = path.read_bytes()

    tab = UiSettingsStore(path).load_all()[0]

    assert tab.monitoring_target_id == ""
    assert tab.customer_id == ""
    assert tab.platform_account_id == ""
    assert path.read_bytes() == before  # 발급이 없으면 원본 파일 무변경


def test_load_all_does_not_rewrite_file_when_all_ids_present(tmp_path):
    # AC3 #7: 모든 ID가 이미 있는 파일을 로드하면 재발급도 영속화도 일어나지 않는다
    # (persist-on-FIRST-issue) — 원본 파일 바이트가 그대로여야 한다.
    path = tmp_path / "settings.json"
    path.write_text(
        '{"crawlings": [{"performance_url": "https://example.test/x",'
        ' "monitoring_target_id": "mt-fixed", "customer_id": "cust-fixed",'
        ' "platform_account_id": "pa-fixed", "legacy_alias": "이미있는별칭"}]}',
        encoding="utf-8",
    )
    before = path.read_bytes()

    UiSettingsStore(path).load_all()

    assert path.read_bytes() == before  # 멱등 로드는 파일을 다시 쓰지 않는다


def test_to_app_config_does_not_expose_id_fields(tmp_path):
    # AC1 #3: 신규 ID/alias 필드는 to_app_config()/AppConfig에 연결하지 않는다(런타임 실행
    # 스냅샷은 본 스토리 범위 밖). AppConfig가 이 필드들을 갖지 않음을 가드한다.
    settings = UiSettings.defaults()
    settings.performance_url = "https://example.test/rider"
    settings.customer_id = "cust-1"
    settings.platform_account_id = "pa-1"
    settings.monitoring_target_id = "mt-1"
    settings.legacy_alias = "크롤링1"

    config = settings.to_app_config()

    for leaked in (
        "customer_id",
        "customer_name",
        "platform_account_id",
        "monitoring_target_id",
        "legacy_alias",
    ):
        assert not hasattr(config, leaked)


def test_save_all_atomic_preserves_original_on_replace_failure(tmp_path, monkeypatch):
    # AC2 #4: 저장 도중(=os.replace 직전) 강제 종료에도 기존 ui_settings.json은 이전 유효
    # 상태가 그대로 보존되고 반쪽짜리로 손상되지 않으며, .tmp 잔여물이 남지 않는다.
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettingsStore(tmp_path / "missing.json").load_all()
    settings[0].telegram_bot_token = "token"
    settings[0].telegram_chat_id = "-100123"
    store.save_all(settings)

    # 활성 탭 ID 발급·영속화까지 끝낸 "직전 유효 상태"를 기준으로 잡는다.
    settled = store.load_all()
    before = store.path.read_bytes()
    settled[0].telegram_chat_id = "-100999"  # 저장 도중 중단될 새 값

    def boom(*_args, **_kwargs):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr("rider_crawl.ui_settings.os.replace", boom)

    with pytest.raises(OSError):
        store.save_all(settled)

    # 원본 파일은 손대지 않은 이전 유효 상태 그대로 load 가능하고 바이트가 동일하다.
    assert store.path.read_bytes() == before
    assert store.load_all()[0].telegram_chat_id == "-100123"
    # 같은 디렉터리에 임시 파일(.tmp) 잔여물이 남지 않는다.
    leftovers = [p.name for p in store.path.parent.iterdir() if p.name != store.path.name]
    assert leftovers == []


def test_save_all_atomic_preserves_serialization_format(tmp_path):
    # AC2 #5: atomic 전환은 직렬화 형식을 바꾸지 않는다 — ensure_ascii=False(한글 비escape)와
    # {"crawlings":[...]} 구조가 그대로 유지된다.
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettingsStore(tmp_path / "missing.json").load_all()
    settings[0].legacy_alias = "실적봇_A"

    store.save_all(settings)

    text = store.path.read_text(encoding="utf-8")
    assert '"crawlings"' in text
    assert "실적봇_A" in text  # ensure_ascii=False라 한글이 그대로 보인다
    assert "\\uc2e4" not in text  # escape됐다면 '실'이 실로 나왔을 것


def test_save_single_object_atomic_preserves_original_on_replace_failure(tmp_path, monkeypatch):
    # AC2 #4: AC는 save_all뿐 아니라 단일 객체 save()도 atomic이라고 명시한다. load()의
    # persist-on-first-issue가 쓰는 이 경로도 os.replace 직전 강제 종료에 기존 파일을 이전
    # 유효 상태로 보존하고 .tmp 잔여물을 남기지 않아야 한다(평면 객체 직렬화 형식도 보존).
    store = UiSettingsStore(tmp_path / "settings.json")
    original = UiSettings.defaults()
    original.telegram_bot_token = "token"
    original.telegram_chat_id = "-100123"
    store.save(original)
    before = store.path.read_bytes()
    assert '"crawlings"' not in before.decode("utf-8")  # save()는 래핑 없는 평면 객체

    crashed = UiSettings.defaults()
    crashed.telegram_chat_id = "-100999"  # 저장 도중 중단될 새 값

    def boom(*_args, **_kwargs):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr("rider_crawl.ui_settings.os.replace", boom)

    with pytest.raises(OSError):
        store.save(crashed)

    # 원본은 손대지 않은 이전 유효 상태 그대로(바이트 동일)이고 .tmp 잔여물이 없다.
    assert store.path.read_bytes() == before
    leftovers = [p.name for p in store.path.parent.iterdir() if p.name != store.path.name]
    assert leftovers == []


# ── Story 2.3: 플랫폼 중립 Target 필드(read-only alias) + 비차단 위험 분류기 ──


def test_ui_settings_neutral_accessors_alias_legacy_fields_for_baemin():
    # AC1: 배민 탭에서 플랫폼 중립 접근자가 기존 legacy 필드 값을 그대로 반환한다.
    settings = UiSettings.defaults()
    settings.platform_name = "baemin"
    settings.performance_url = "https://example.test/delivery/history"
    settings.baemin_center_name = "강남센터"
    settings.baemin_center_id = "DP000"
    settings.legacy_alias = "크롤링1"

    assert settings.primary_url == settings.performance_url
    assert settings.center_name == settings.baemin_center_name
    assert settings.target_external_id == settings.baemin_center_id
    assert settings.display_name == settings.legacy_alias


def test_ui_settings_neutral_accessors_alias_legacy_fields_for_coupang():
    # AC1: 쿠팡 탭도 같은 중립 필드 이름으로 같은 매핑을 읽는다(동일 Target 필드 집합).
    # 쿠팡은 baemin_center_name을 기대 센터/상점명으로 재사용하므로 center_name으로 노출된다.
    settings = UiSettings.defaults()
    settings.platform_name = "coupang"
    settings.performance_url = "https://partner.coupangeats.com/page/peak-dashboard"
    settings.baemin_center_name = "강남센터"
    settings.baemin_center_id = "DP000"
    settings.legacy_alias = "크롤링2"

    assert settings.primary_url == settings.performance_url
    assert settings.center_name == settings.baemin_center_name
    assert settings.target_external_id == settings.baemin_center_id
    assert settings.display_name == settings.legacy_alias


def test_ui_settings_neutral_accessors_return_raw_value_without_stripping():
    # Task 1: 중립 접근자는 순수 읽기다 — strip/가공 없이 원본 값을 그대로 돌려준다
    # (소비자가 기존처럼 .strip()을 호출하므로 여기서 가공하면 의미가 갈라진다).
    settings = UiSettings.defaults()
    settings.baemin_center_name = "  강남센터  "

    assert settings.center_name == "  강남센터  "


def test_ui_settings_neutral_accessors_are_not_serialized(tmp_path):
    # AC2/AC4: @property는 dataclass 필드가 아니므로 asdict/저장 JSON에 새 키가 생기지
    # 않는다. save/load·save_all/load_all 라운드트립 텍스트에 중립 이름 키가 없어야 한다.
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettings.defaults()
    settings.legacy_alias = "크롤링1"
    store.save(settings)

    flat_text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    for neutral_key in ('"primary_url"', '"center_name"', '"target_external_id"', '"display_name"'):
        assert neutral_key not in flat_text
    # 라운드트립으로 legacy 값은 그대로 보존된다.
    assert store.load().legacy_alias == "크롤링1"

    # save_all/load_all 경로도 동일하게 중립 키를 직렬화하지 않고 "crawlings" 구조를 유지한다.
    multi = UiSettingsStore(tmp_path / "missing.json").load_all()
    multi[0].performance_url = "https://example.test/x"
    store.save_all(multi)
    all_text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert '"crawlings"' in all_text
    for neutral_key in ('"primary_url"', '"center_name"', '"target_external_id"', '"display_name"'):
        assert neutral_key not in all_text


def test_ui_settings_coupang_center_name_risk_delegates_to_classifier():
    # AC3(편의 접근자): 중립 center_name/platform_name으로 위험 분류기를 바로 호출할 수
    # 있다(분류만 — 예외/저장/상태 전이 없음).
    risky = UiSettings.defaults()
    risky.platform_name = "coupang"
    risky.baemin_center_name = ""  # 빈 기대 센터/상점명 → 위험
    is_risky, reason = risky.coupang_center_name_risk()
    assert is_risky is True
    assert reason

    safe = UiSettings.defaults()
    safe.platform_name = "coupang"
    safe.baemin_center_name = "강남센터"
    assert safe.coupang_center_name_risk() == (False, "")

    baemin = UiSettings.defaults()  # platform_name == "baemin"
    baemin.baemin_center_name = ""
    assert baemin.coupang_center_name_risk() == (False, "")


def test_to_app_config_preserves_neutral_target_fields(tmp_path):
    # AC1: UiSettings→AppConfig 변환 경계를 거쳐도 같은 Target 필드 집합을 읽는다. 두 모델은
    # primary_url/center_name/target_external_id를 서로 다른 legacy 필드(performance_url↔
    # coupang_eats_url 등)에 매핑하지만, to_app_config가 값을 옮기므로 중립 이름으로는 동일
    # 값이 유지된다 — AC1의 양쪽(UiSettings·AppConfig 중립 접근자)을 변환 경로로 잇는다.
    settings = UiSettings.defaults()
    settings.platform_name = "coupang"
    settings.performance_url = "https://partner.coupangeats.com/page/peak-dashboard"
    settings.baemin_center_name = "강남센터"
    settings.baemin_center_id = "DP000"

    config = settings.to_app_config()

    assert config.primary_url == settings.primary_url
    assert config.center_name == settings.center_name
    assert config.target_external_id == settings.target_external_id


def test_save_all_atomic_cleans_temp_and_preserves_original_on_fsync_failure(tmp_path, monkeypatch):
    # AC2 #4: 실패 지점이 os.replace 이전(os.fsync)이어도 — temp는 쓰였지만 아직 교체 전 —
    # 기존 ui_settings.json은 이전 유효 상태로 보존되고 temp(.tmp)는 정리된다(unlink 후 재발생).
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettingsStore(tmp_path / "missing.json").load_all()
    settings[0].telegram_bot_token = "token"
    settings[0].telegram_chat_id = "-100123"
    store.save_all(settings)

    settled = store.load_all()  # 활성 탭 ID 발급·영속화까지 끝낸 직전 유효 상태
    before = store.path.read_bytes()
    settled[0].telegram_chat_id = "-100999"  # fsync 단계에서 중단될 새 값

    def boom(*_args, **_kwargs):
        raise OSError("simulated fsync failure before rename")

    monkeypatch.setattr("rider_crawl.ui_settings.os.fsync", boom)

    with pytest.raises(OSError):
        store.save_all(settled)

    # 교체 전 실패라 원본은 불변이고, temp 잔여물이 남지 않는다.
    assert store.path.read_bytes() == before
    assert store.load_all()[0].telegram_chat_id == "-100123"
    leftovers = [p.name for p in store.path.parent.iterdir() if p.name != store.path.name]
    assert leftovers == []
