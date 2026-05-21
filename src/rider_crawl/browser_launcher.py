from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .config import AppConfig


class BrowserLaunchError(RuntimeError):
    pass


CommandRunner = Callable[[list[str], bool], object]


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
        "Chrome 실행 요청 완료. 열린 Chrome 창에서 쿠팡이츠에 로그인하고 "
        "실적 페이지가 보이는 상태로 두세요."
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


def _run_command(command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check)


def _cdp_port(cdp_url: str) -> int:
    port = urlsplit(cdp_url).port
    if port is None:
        raise BrowserLaunchError("CDP 주소에는 포트가 필요합니다. 예: http://127.0.0.1:9222")
    return port


def _chrome_profile_dir(config: AppConfig) -> Path:
    return (config.runtime_dir / "chrome-cdp-profile").resolve()
