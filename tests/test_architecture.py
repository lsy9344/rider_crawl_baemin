from rider_crawl.config import AppConfig
from rider_crawl.models import CurrentScreenSnapshot


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


def test_default_messenger_registry_resolves_kakao_sender():
    from rider_crawl.messengers import DEFAULT_MESSENGER_NAME, get_messenger
    from rider_crawl.messengers.kakao import KakaoMessenger

    messenger = get_messenger(DEFAULT_MESSENGER_NAME)

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

    monkeypatch.setattr("rider_crawl.platforms.crawl_snapshot", lambda received: snapshot)
    monkeypatch.setattr("rider_crawl.messengers.dispatch_text_message", lambda received, message: sent.append((received, message)))

    assert app._crawl_snapshot(config) is snapshot
    app._send_message(config, "hello")

    assert sent == [(config, "hello")]


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history",
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
