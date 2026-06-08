from rider_crawl.app import run_once
from rider_crawl.config import AppConfig
from rider_crawl.models import CurrentScreenSnapshot


def test_run_once_dry_run_builds_message_without_sending(tmp_path):
    sent_messages: list[str] = []
    snapshot = CurrentScreenSnapshot(
        center_name="제이앤에이치플러스 의정부남부",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        afternoon_non_peak_count=41.8,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=41.8,
        active_riders=5,
    )
    config = AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser-profile",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )

    result = run_once(
        config,
        crawl_snapshot=lambda _config: snapshot,
        send_message=lambda _config, message: sent_messages.append(message),
    )

    assert result.sent is False
    assert sent_messages == []
    assert "⏰ 14:02 기준" in result.message
    assert "오전오후피크 : 60.6건" in result.message
    assert "오후논피크 : 41.8건" in result.message


def test_run_once_includes_crawl_name_when_present(tmp_path):
    config = _config(tmp_path, crawl_name="크롤링2")

    result = run_once(
        config,
        crawl_snapshot=lambda _config: _snapshot(),
        send_message=lambda _config, message: None,
    )

    assert "[크롤링2]" in result.message


def _config(tmp_path, *, crawl_name: str = "") -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser-profile",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        crawl_name=crawl_name,
    )


def _snapshot() -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name="제이앤에이치플러스 의정부남부",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        afternoon_non_peak_count=41.8,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=41.8,
        active_riders=5,
    )
