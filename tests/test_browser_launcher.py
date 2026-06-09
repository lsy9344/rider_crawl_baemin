import subprocess
from pathlib import Path

import pytest

from rider_crawl.browser_launcher import (
    BrowserLaunchError,
    build_mac_chrome_command,
    build_windows_chrome_command,
    prepare_chrome,
    prepare_mac_chrome,
)
from rider_crawl.config import AppConfig


def test_build_mac_chrome_command_uses_cdp_port_and_dedicated_profile(tmp_path):
    config = _config(tmp_path)

    command = build_mac_chrome_command(config)
    user_data_arg = next(arg for arg in command if arg.startswith("--user-data-dir="))

    assert command[:4] == ["open", "-na", "Google Chrome", "--args"]
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=9222" in command
    assert user_data_arg == f"--user-data-dir={(tmp_path / 'browser').resolve()}"
    assert Path(user_data_arg.removeprefix("--user-data-dir=")).is_absolute()
    assert (
        command[-1]
        == "https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
    )


def test_build_mac_chrome_command_resolves_relative_default_profile_path():
    config = _config_with_relative_log_dir()

    command = build_mac_chrome_command(config)
    user_data_arg = next(arg for arg in command if arg.startswith("--user-data-dir="))

    assert user_data_arg == f"--user-data-dir={(Path('runtime') / 'browser-profile').resolve()}"


def test_build_windows_chrome_command_uses_cdp_port_profile_and_baemin_url(tmp_path):
    config = _config(tmp_path)

    command = build_windows_chrome_command(config, chrome_path="chrome.exe")
    user_data_arg = next(arg for arg in command if arg.startswith("--user-data-dir="))

    assert command[0] == "chrome.exe"
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=9222" in command
    assert user_data_arg == f"--user-data-dir={(tmp_path / 'browser').resolve()}"
    assert command[-1] == config.coupang_eats_url


def test_prepare_mac_chrome_runs_open_command_and_creates_profile_dir(tmp_path):
    calls = []
    probes = []
    config = _config(tmp_path)

    def probe(cdp_url):
        probes.append(cdp_url)
        if not calls:
            raise OSError("not ready")

    message = prepare_mac_chrome(
        config,
        platform_name="Darwin",
        run_command=lambda command, check: calls.append((command, check)),
        cdp_probe=probe,
    )

    assert calls == [(build_mac_chrome_command(config), True)]
    assert probes == ["http://127.0.0.1:9222", "http://127.0.0.1:9222"]
    assert (tmp_path / "browser").is_dir()
    assert "Chrome 실행 요청 완료" in message


def test_prepare_chrome_runs_windows_command_and_creates_profile_dir(tmp_path):
    calls = []
    probes = []
    config = _config(tmp_path)

    def probe(cdp_url):
        probes.append(cdp_url)
        if not calls:
            raise OSError("not ready")

    message = prepare_chrome(
        config,
        platform_name="Windows",
        run_command=lambda command, check: calls.append((command, check)),
        cdp_probe=probe,
    )

    assert calls == [(build_windows_chrome_command(config), False)]
    assert probes == ["http://127.0.0.1:9222", "http://127.0.0.1:9222"]
    assert (tmp_path / "browser").is_dir()
    assert "Chrome 실행 요청 완료" in message


def test_prepare_chrome_rejects_existing_cdp_endpoint_before_launch(tmp_path):
    calls = []
    config = _config(tmp_path)

    with pytest.raises(BrowserLaunchError, match="이미 사용 중"):
        prepare_chrome(
            config,
            platform_name="Windows",
            run_command=lambda command, check: calls.append((command, check)),
            cdp_probe=lambda _cdp_url: None,
        )

    assert calls == []


def test_prepare_chrome_rejects_when_cdp_endpoint_does_not_become_ready(tmp_path):
    config = _config(tmp_path)

    with pytest.raises(BrowserLaunchError, match="CDP 포트"):
        prepare_chrome(
            config,
            platform_name="Windows",
            run_command=lambda *_args: None,
            cdp_probe=lambda _cdp_url: (_ for _ in ()).throw(OSError("refused")),
            cdp_timeout_seconds=0,
        )


def test_prepare_mac_chrome_rejects_non_mac_platform(tmp_path):
    with pytest.raises(BrowserLaunchError, match="macOS"):
        prepare_mac_chrome(_config(tmp_path), platform_name="Windows")


def test_prepare_chrome_rejects_remote_cdp_address(tmp_path):
    config = _config(tmp_path)
    config = AppConfig(
        **{
            **config.__dict__,
            "cdp_url": "http://192.168.0.10:9222",
        }
    )

    with pytest.raises(BrowserLaunchError, match="로컬"):
        prepare_chrome(config, platform_name="Windows", run_command=lambda *_args: None)


def test_prepare_chrome_rejects_ipv6_localhost_because_launcher_binds_ipv4(tmp_path):
    config = _config(tmp_path)
    config = AppConfig(
        **{
            **config.__dict__,
            "cdp_url": "http://[::1]:9222",
        }
    )

    with pytest.raises(BrowserLaunchError, match="127.0.0.1"):
        prepare_chrome(config, platform_name="Windows", run_command=lambda *_args: None)


def test_prepare_mac_chrome_wraps_command_failure(tmp_path):
    def fail(_command, check):
        raise subprocess.CalledProcessError(1, "open")

    with pytest.raises(BrowserLaunchError, match="Chrome 실행 실패"):
        prepare_mac_chrome(_config(tmp_path), platform_name="Darwin", run_command=fail)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus=",
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


def _config_with_relative_log_dir() -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus=",
        baemin_center_name="",
        baemin_center_id="",
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
