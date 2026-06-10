import threading
from dataclasses import replace

from rider_crawl.app import run_once
from rider_crawl.config import AppConfig
from rider_crawl.lock import LockAlreadyHeldError
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)


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


def test_run_once_sends_again_when_telegram_target_changes(tmp_path):
    sent_targets: list[str] = []
    first = _config(
        tmp_path,
        send_enabled=True,
        send_only_on_change=True,
        telegram_bot_token="token",
        telegram_chat_id="-100111",
    )
    second = _config(
        tmp_path,
        send_enabled=True,
        send_only_on_change=True,
        telegram_bot_token="token",
        telegram_chat_id="-100222",
    )

    run_once(
        first,
        crawl_snapshot=lambda _config: _snapshot(),
        send_message=lambda config, _message: sent_targets.append(config.telegram_chat_id),
    )
    result = run_once(
        second,
        crawl_snapshot=lambda _config: _snapshot(),
        send_message=lambda config, _message: sent_targets.append(config.telegram_chat_id),
    )

    assert result.sent is True
    assert result.skipped is False
    assert sent_targets == ["-100111", "-100222"]


def test_run_once_skips_duplicate_when_telegram_thread_id_format_changes(tmp_path):
    sent_threads: list[str] = []
    first = _config(
        tmp_path,
        send_enabled=True,
        send_only_on_change=True,
        telegram_bot_token="token",
        telegram_chat_id="-100111",
        telegram_message_thread_id="77",
    )
    second = _config(
        tmp_path,
        send_enabled=True,
        send_only_on_change=True,
        telegram_bot_token="token",
        telegram_chat_id="-100111",
        telegram_message_thread_id="077",
    )

    run_once(
        first,
        crawl_snapshot=lambda _config: _snapshot(),
        send_message=lambda config, _message: sent_threads.append(config.telegram_message_thread_id),
    )
    result = run_once(
        second,
        crawl_snapshot=lambda _config: _snapshot(),
        send_message=lambda config, _message: sent_threads.append(config.telegram_message_thread_id),
    )

    assert result.sent is False
    assert result.skipped is True
    assert sent_threads == ["77"]


def test_run_once_allows_parallel_runs_for_different_browser_scopes(tmp_path):
    first_started = threading.Event()
    release_first = threading.Event()
    first_finished = threading.Event()
    first = _config(tmp_path)
    second = replace(
        first,
        cdp_url="http://127.0.0.1:9223",
        browser_user_data_dir=tmp_path / "browser-profile-2",
    )

    def slow_crawl(_config):
        first_started.set()
        release_first.wait(timeout=2)
        return _snapshot()

    def run_first():
        try:
            run_once(first, crawl_snapshot=slow_crawl, send_message=lambda _config, _message: None)
        finally:
            first_finished.set()

    worker = threading.Thread(target=run_first)
    worker.start()
    assert first_started.wait(timeout=1)

    try:
        result = run_once(second, crawl_snapshot=lambda _config: _snapshot(), send_message=lambda _config, _message: None)
    finally:
        release_first.set()
        worker.join(timeout=2)

    assert first_finished.is_set()
    assert result.skipped is False


def test_run_once_blocks_parallel_runs_for_same_browser_scope_even_with_different_state_subdirs(tmp_path):
    first_started = threading.Event()
    release_first = threading.Event()
    first_finished = threading.Event()
    first = _config(tmp_path)
    second = replace(
        first,
        coupang_eats_url="https://example.test/other",
        baemin_center_name="다른센터",
        baemin_center_id="DP999",
        state_subdir="crawling2",
    )

    def slow_crawl(_config):
        first_started.set()
        release_first.wait(timeout=2)
        return _snapshot()

    def run_first():
        try:
            run_once(first, crawl_snapshot=slow_crawl, send_message=lambda _config, _message: None)
        finally:
            first_finished.set()

    worker = threading.Thread(target=run_first)
    worker.start()
    assert first_started.wait(timeout=1)

    try:
        try:
            run_once(second, crawl_snapshot=lambda _config: _snapshot(), send_message=lambda _config, _message: None)
        except LockAlreadyHeldError:
            blocked = True
        else:
            blocked = False
    finally:
        release_first.set()
        worker.join(timeout=2)

    assert first_finished.is_set()
    assert blocked is True


def test_run_once_blocks_parallel_cdp_runs_even_when_profile_paths_differ(tmp_path):
    first_started = threading.Event()
    release_first = threading.Event()
    first_finished = threading.Event()
    first = _config(tmp_path)
    second = replace(
        first,
        browser_user_data_dir=tmp_path / "other-browser-profile",
        state_subdir="crawling2",
    )

    def slow_crawl(_config):
        first_started.set()
        release_first.wait(timeout=2)
        return _snapshot()

    def run_first():
        try:
            run_once(first, crawl_snapshot=slow_crawl, send_message=lambda _config, _message: None)
        finally:
            first_finished.set()

    worker = threading.Thread(target=run_first)
    worker.start()
    assert first_started.wait(timeout=1)

    try:
        try:
            run_once(second, crawl_snapshot=lambda _config: _snapshot(), send_message=lambda _config, _message: None)
        except LockAlreadyHeldError:
            blocked = True
        else:
            blocked = False
    finally:
        release_first.set()
        worker.join(timeout=2)

    assert first_finished.is_set()
    assert blocked is True


def _config(
    tmp_path,
    *,
    crawl_name: str = "",
    send_enabled: bool = False,
    send_only_on_change: bool = False,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    telegram_message_thread_id: str = "",
    platform_name: str = "baemin",
) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        platform_name=platform_name,
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser-profile",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=send_enabled,
        send_only_on_change=send_only_on_change,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        telegram_message_thread_id=telegram_message_thread_id,
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


def _performance_snapshot() -> PerformanceSnapshot:
    return PerformanceSnapshot(
        current_screen=_snapshot(),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=9, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )


def test_app_default_crawl_uses_configured_platform_name(tmp_path, monkeypatch):
    import rider_crawl.app as app

    config = _config(tmp_path, platform_name="coupang")
    snapshot = _performance_snapshot()
    calls: list[str] = []

    monkeypatch.setattr(
        "rider_crawl.platforms.crawl_snapshot",
        lambda received, *, platform_name=None: calls.append(platform_name) or snapshot,
    )

    assert app._crawl_snapshot(config) is snapshot
    assert calls == ["coupang"]


def test_message_scope_key_includes_platform_and_peak_dashboard_url(tmp_path):
    from dataclasses import replace
    import rider_crawl.app as app

    baemin = _config(tmp_path, platform_name="baemin")
    coupang = replace(
        baemin,
        platform_name="coupang",
        peak_dashboard_url="https://partner.coupangeats.com/page/peak-dashboard",
    )

    assert app._message_scope_key(baemin) != app._message_scope_key(coupang)
