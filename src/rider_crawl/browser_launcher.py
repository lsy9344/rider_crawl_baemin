from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .config import AppConfig


class BrowserLaunchError(RuntimeError):
    pass


CommandRunner = Callable[[list[str], bool], object]


def prepare_chrome(
    config: AppConfig,
    *,
    platform_name: str | None = None,
    run_command: CommandRunner | None = None,
) -> str:
    current_platform = platform_name or platform.system()
    if current_platform == "Darwin":
        return prepare_mac_chrome(config, platform_name=current_platform, run_command=run_command)
    if current_platform == "Windows":
        return prepare_windows_chrome(config, platform_name=current_platform, run_command=run_command)
    raise BrowserLaunchError("Chrome 실행 준비는 Windows와 macOS에서만 지원합니다.")


def prepare_mac_chrome(
    config: AppConfig,
    *,
    platform_name: str | None = None,
    run_command: CommandRunner | None = None,
) -> str:
    if (platform_name or platform.system()) != "Darwin":
        raise BrowserLaunchError("앱 실행 준비하기는 macOS에서만 지원합니다.")

    profile_dir = _chrome_profile_dir(config)
    profile_dir.mkdir(parents=True, exist_ok=True)

    command = build_mac_chrome_command(config)
    runner = run_command or _run_command
    try:
        runner(command, True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BrowserLaunchError("Chrome 실행 실패: Google Chrome 설치와 CDP 주소를 확인하세요.") from exc

    return (
        "Chrome 실행 요청 완료. 열린 Chrome 창에서 배민에 로그인하고 "
        "배달현황 페이지가 보이는 상태로 두세요."
    )


def build_mac_chrome_command(config: AppConfig) -> list[str]:
    port = _cdp_port(config.cdp_url)
    return [
        "open",
        "-na",
        "Google Chrome",
        "--args",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={_chrome_profile_dir(config)}",
        config.coupang_eats_url,
    ]


def prepare_windows_chrome(
    config: AppConfig,
    *,
    platform_name: str | None = None,
    run_command: CommandRunner | None = None,
) -> str:
    if (platform_name or platform.system()) != "Windows":
        raise BrowserLaunchError("Windows Chrome 실행 준비는 Windows에서만 지원합니다.")

    profile_dir = _chrome_profile_dir(config)
    profile_dir.mkdir(parents=True, exist_ok=True)

    command = build_windows_chrome_command(config)
    runner = run_command or _run_command
    try:
        runner(command, False)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BrowserLaunchError("Chrome 실행 실패: Google Chrome 설치와 CDP 주소를 확인하세요.") from exc

    return (
        "Chrome 실행 요청 완료. 열린 Chrome 창에서 배민에 로그인하고 "
        "배달현황 페이지가 보이는 상태로 두세요."
    )


def build_windows_chrome_command(config: AppConfig, *, chrome_path: str | Path | None = None) -> list[str]:
    port = _cdp_port(config.cdp_url)
    return [
        str(chrome_path or _find_windows_chrome_executable()),
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={_chrome_profile_dir(config)}",
        config.coupang_eats_url,
    ]


def _run_command(command: list[str], check: bool) -> object:
    if check:
        return subprocess.run(command, check=True)
    return subprocess.Popen(command)


def _cdp_port(cdp_url: str) -> int:
    port = urlsplit(cdp_url).port
    if port is None:
        raise BrowserLaunchError("CDP 주소에는 포트가 필요합니다. 예: http://127.0.0.1:9222")
    return port


def _chrome_profile_dir(config: AppConfig) -> Path:
    return config.browser_user_data_dir.resolve()


def _find_windows_chrome_executable() -> Path | str:
    found = shutil.which("chrome.exe") or shutil.which("chrome")
    if found:
        return found

    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return "chrome.exe"
