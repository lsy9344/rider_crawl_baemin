import pytest

from rider_crawl.config import AppConfig
from rider_crawl.sender import KakaoSendError, send_kakao_text


def test_send_kakao_text_requires_chat_name(tmp_path):
    config = _config(tmp_path, chat_name="")

    with pytest.raises(KakaoSendError, match="KAKAO_CHAT_NAME"):
        send_kakao_text(config, "hello")


def test_send_kakao_text_refuses_non_windows_runtime(tmp_path):
    config = _config(tmp_path, chat_name="실적봇_의정부남부")

    with pytest.raises(KakaoSendError, match="Windows"):
        send_kakao_text(config, "hello", platform_name="Darwin")


def _config(tmp_path, *, chat_name: str) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser",
        headless=False,
        kakao_chat_name=chat_name,
        log_dir=tmp_path / "logs",
        send_enabled=True,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )
