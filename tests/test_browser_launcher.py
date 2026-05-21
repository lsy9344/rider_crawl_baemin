import subprocess
from pathlib import Path

import pytest

from rider_crawl.browser_launcher import BrowserLaunchError, build_mac_chrome_command, prepare_mac_chrome
from rider_crawl.config import AppConfig


def test_build_mac_chrome_command_uses_cdp_port_and_dedicated_profile(tmp_path):
    config = _config(tmp_path)

    command = build_mac_chrome_command(config)
    user_data_arg = next(arg for arg in command if arg.startswith("--user-data-dir="))

    assert command[:4] == ["open", "-na", "Google Chrome", "--args"]
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=9222" in command
    assert user_data_arg == f"--user-data-dir={(tmp_path / 'runtime' / 'chrome-cdp-profile').resolve()}"
    assert Path(user_data_arg.removeprefix("--user-data-dir=")).is_absolute()
    assert command[-1] == "https://partner.coupangeats.com/page/rider-performance"


def test_build_mac_chrome_command_resolves_relative_default_profile_path():
    config = _config_with_relative_log_dir()

    command = build_mac_chrome_command(config)
    user_data_arg = next(arg for arg in command if arg.startswith("--user-data-dir="))

    assert user_data_arg == f"--user-data-dir={(Path('runtime') / 'chrome-cdp-profile').resolve()}"


def test_prepare_mac_chrome_runs_open_command_and_creates_profile_dir(tmp_path):
    calls = []
    config = _config(tmp_path)

    message = prepare_mac_chrome(
        config,
        platform_name="Darwin",
        run_command=lambda command, check: calls.append((command, check)),
    )

    assert calls == [(build_mac_chrome_command(config), True)]
    assert (tmp_path / "runtime" / "chrome-cdp-profile").is_dir()
    assert "Chrome 실행 요청 완료" in message


def test_prepare_mac_chrome_rejects_non_mac_platform(tmp_path):
    with pytest.raises(BrowserLaunchError, match="macOS"):
        prepare_mac_chrome(_config(tmp_path), platform_name="Windows")


def test_prepare_mac_chrome_wraps_command_failure(tmp_path):
    def fail(_command, check):
        raise subprocess.CalledProcessError(1, "open")

    with pytest.raises(BrowserLaunchError, match="Chrome 실행 실패"):
        prepare_mac_chrome(_config(tmp_path), platform_name="Darwin", run_command=fail)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
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


def _config_with_relative_log_dir() -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=Path("runtime/browser-profile"),
        headless=False,
        kakao_chat_name="",
        log_dir=Path("logs"),
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )
