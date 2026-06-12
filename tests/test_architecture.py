from rider_crawl.config import AppConfig
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)


def test_coupang_snapshot_models_are_available():
    from rider_crawl.models import (
        CurrentScreenSnapshot,
        PeakDashboardSnapshot,
        PeakPeriodSnapshot,
        PerformanceSnapshot,
    )

    current = CurrentScreenSnapshot(
        center_name="센터",
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
        dinner_peak_count=0,
        non_peak_count=41.8,
        active_riders=7,
    )
    dashboard = PeakDashboardSnapshot(
        updated_at="20:38",
        assigned_count=103,
        processed_count=67,
        reject_rate=6.5,
        morning=PeakPeriodSnapshot(done=9, total=9),
        lunch_peak=PeakPeriodSnapshot(done=45, total=45),
        lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
        dinner_peak=PeakPeriodSnapshot(done=17, total=39),
        dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
    )

    snapshot = PerformanceSnapshot(current_screen=current, peak_dashboard=dashboard)

    assert snapshot.current_screen.active_riders == 7
    assert snapshot.peak_dashboard.dinner_non_peak.done == 2


def test_default_platform_registry_resolves_baemin_crawler(tmp_path):
    from rider_crawl.platforms import DEFAULT_PLATFORM_NAME, get_platform
    from rider_crawl.platforms.baemin import BaeminDeliveryPlatform

    platform = get_platform(DEFAULT_PLATFORM_NAME)

    assert isinstance(platform, BaeminDeliveryPlatform)


def test_baemin_platform_crawls_snapshot_with_injected_crawler(tmp_path):
    from rider_crawl.platforms.baemin import BaeminDeliveryPlatform

    config = _config(tmp_path)
    snapshot = _snapshot()
    platform = BaeminDeliveryPlatform(crawl=lambda received: snapshot)

    assert platform.crawl_snapshot(config) is snapshot


def test_coupang_platform_registry_resolves_coupang_crawler():
    from rider_crawl.platforms import get_platform
    from rider_crawl.platforms.coupang import CoupangEatsPlatform

    platform = get_platform("coupang")

    assert isinstance(platform, CoupangEatsPlatform)


def test_crawl_snapshot_uses_configured_platform_name(tmp_path, monkeypatch):
    from rider_crawl.platforms import crawl_snapshot
    from rider_crawl.platforms.coupang import CoupangEatsPlatform

    config = _config(tmp_path, platform_name="coupang")
    snapshot = _performance_snapshot()
    requested_names: list[str] = []

    def fake_get_platform(name):
        requested_names.append(name)
        return CoupangEatsPlatform(crawl=lambda received: snapshot)

    monkeypatch.setattr("rider_crawl.platforms.get_platform", fake_get_platform)

    assert crawl_snapshot(config) is snapshot
    # 라우팅이 설정된 platform_name을 get_platform에 그대로 전달하는지 검증한다.
    assert requested_names == ["coupang"]


def test_crawl_snapshot_explicit_platform_name_overrides_config(tmp_path, monkeypatch):
    from rider_crawl.platforms import crawl_snapshot
    from rider_crawl.platforms.baemin import BaeminDeliveryPlatform

    config = _config(tmp_path, platform_name="coupang")
    snapshot = _snapshot()
    requested_names: list[str] = []

    def fake_get_platform(name):
        requested_names.append(name)
        return BaeminDeliveryPlatform(crawl=lambda received: snapshot)

    monkeypatch.setattr("rider_crawl.platforms.get_platform", fake_get_platform)

    # 명시 platform_name 인자가 config.platform_name보다 우선한다.
    assert crawl_snapshot(config, platform_name="baemin") is snapshot
    assert requested_names == ["baemin"]


def test_default_messenger_registry_resolves_telegram_sender():
    from rider_crawl.messengers import DEFAULT_MESSENGER_NAME, get_messenger
    from rider_crawl.messengers.telegram import TelegramMessenger

    messenger = get_messenger(DEFAULT_MESSENGER_NAME)

    assert isinstance(messenger, TelegramMessenger)


def test_kakao_messenger_can_still_be_resolved_explicitly():
    from rider_crawl.messengers import get_messenger
    from rider_crawl.messengers.kakao import KakaoMessenger

    messenger = get_messenger("kakao")

    assert isinstance(messenger, KakaoMessenger)


def test_kakao_messenger_sends_text_with_injected_sender(tmp_path):
    from rider_crawl.messengers.kakao import KakaoMessenger

    calls: list[tuple[AppConfig, str]] = []
    config = _config(tmp_path)
    messenger = KakaoMessenger(send=lambda received, message: calls.append((received, message)))

    messenger.send_text(config, "hello")

    assert calls == [(config, "hello")]


def test_app_default_crawl_and_send_use_extension_registries(tmp_path, monkeypatch):
    import rider_crawl.app as app

    config = _config(tmp_path)
    snapshot = _snapshot()
    sent: list[tuple[AppConfig, str]] = []

    monkeypatch.setattr(
        "rider_crawl.platforms.crawl_snapshot",
        lambda received, *, platform_name=None: snapshot,
    )
    monkeypatch.setattr("rider_crawl.messengers.dispatch_text_message", lambda received, message: sent.append((received, message)))

    assert app._crawl_snapshot(config) is snapshot
    app._send_message(config, "hello")

    assert sent == [(config, "hello")]


def _config(tmp_path, *, platform_name: str = "baemin") -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history",
        peak_dashboard_url="",
        platform_name=platform_name,
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
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


def _snapshot() -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name="center",
        date_label="",
        shift_label="",
        shift_time_range="",
        shift_status="",
        updated_at="12:00",
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=0,
        rejected_ignored_count=0,
        cancelled_count=0,
        completed_count=0,
        sequence_violation_count=0,
        lunch_peak_count=0,
        dinner_peak_count=0,
        non_peak_count=0,
        active_riders=0,
    )
