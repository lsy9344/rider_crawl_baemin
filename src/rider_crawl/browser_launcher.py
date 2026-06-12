from __future__ import annotations

import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import urlopen as default_urlopen

from .config import AppConfig


class BrowserLaunchError(RuntimeError):
    pass


class CdpUnavailableError(RuntimeError):
    """CDP 포트에 디버깅 가능한 Chrome이 없어 연결할 수 없는 상태.

    "Chrome 준비하기"가 안 됐거나 포트가 잘못된 환경/설정 오류이지, 페이지가 잠깐 안
    뜨는 일시적 오류가 아니다. 스케줄러가 5초마다 재시도해도 사람이 Chrome을 켜기
    전에는 절대 복구되지 않으므로, UI는 이 오류를 빠른 재시도 대상에서 제외해
    로그/연결 폭주를 막는다(ui._run_once_background 참고)."""

    pass


class BrowserActionRequiredError(RuntimeError):
    """Browser state requires manual operator action before crawling can continue."""

    pass


CommandRunner = Callable[[list[str], bool], object]
CdpProbe = Callable[[str], object]


def prepare_chrome(
    config: AppConfig,
    *,
    platform_name: str | None = None,
    run_command: CommandRunner | None = None,
    cdp_probe: CdpProbe | None = None,
    cdp_timeout_seconds: float = 10.0,
) -> str:
    current_platform = platform_name or platform.system()
    if current_platform == "Darwin":
        return prepare_mac_chrome(
            config,
            platform_name=current_platform,
            run_command=run_command,
            cdp_probe=cdp_probe,
            cdp_timeout_seconds=cdp_timeout_seconds,
        )
    if current_platform == "Windows":
        return prepare_windows_chrome(
            config,
            platform_name=current_platform,
            run_command=run_command,
            cdp_probe=cdp_probe,
            cdp_timeout_seconds=cdp_timeout_seconds,
        )
    raise BrowserLaunchError("Chrome 실행 준비는 Windows와 macOS에서만 지원합니다.")


def prepare_mac_chrome(
    config: AppConfig,
    *,
    platform_name: str | None = None,
    run_command: CommandRunner | None = None,
    cdp_probe: CdpProbe | None = None,
    cdp_timeout_seconds: float = 10.0,
) -> str:
    if (platform_name or platform.system()) != "Darwin":
        raise BrowserLaunchError("앱 실행 준비하기는 macOS에서만 지원합니다.")

    profile_dir = _chrome_profile_dir(config)
    profile_dir.mkdir(parents=True, exist_ok=True)

    ensure_local_cdp_address(config.cdp_url)
    probe = cdp_probe or _probe_cdp_endpoint
    _ensure_cdp_endpoint_unused(config.cdp_url, probe=probe)

    command = build_mac_chrome_command(config)
    runner = run_command or _run_command
    try:
        runner(command, True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BrowserLaunchError("Chrome 실행 실패: Google Chrome 설치와 CDP 주소를 확인하세요.") from exc
    _wait_for_cdp_ready(config.cdp_url, probe=probe, timeout_seconds=cdp_timeout_seconds)

    return _chrome_ready_message(config)


def build_mac_chrome_command(config: AppConfig) -> list[str]:
    port = _cdp_port(config.cdp_url)
    ensure_local_cdp_address(config.cdp_url)
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
    cdp_probe: CdpProbe | None = None,
    cdp_timeout_seconds: float = 10.0,
) -> str:
    if (platform_name or platform.system()) != "Windows":
        raise BrowserLaunchError("Windows Chrome 실행 준비는 Windows에서만 지원합니다.")

    profile_dir = _chrome_profile_dir(config)
    profile_dir.mkdir(parents=True, exist_ok=True)

    ensure_local_cdp_address(config.cdp_url)
    probe = cdp_probe or _probe_cdp_endpoint
    _ensure_cdp_endpoint_unused(config.cdp_url, probe=probe)
    _ensure_chrome_profile_free(profile_dir)

    command = build_windows_chrome_command(config)
    runner = run_command or _run_command
    try:
        runner(command, False)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BrowserLaunchError("Chrome 실행 실패: Google Chrome 설치와 CDP 주소를 확인하세요.") from exc
    _wait_for_cdp_ready(config.cdp_url, probe=probe, timeout_seconds=cdp_timeout_seconds)

    return _chrome_ready_message(config)


def build_windows_chrome_command(config: AppConfig, *, chrome_path: str | Path | None = None) -> list[str]:
    port = _cdp_port(config.cdp_url)
    ensure_local_cdp_address(config.cdp_url)
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


def _wait_for_cdp_ready(
    cdp_url: str,
    *,
    probe: CdpProbe | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    cdp_probe = probe or _probe_cdp_endpoint
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_error: Exception | None = None
    while True:
        try:
            cdp_probe(cdp_url)
            return
        except Exception as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise BrowserLaunchError(
                    "Chrome CDP 포트가 준비되지 않았습니다. 같은 포트를 쓰는 Chrome이 이미 떠 있거나 "
                    "프로필 경로가 잠겨 있는지 확인하세요."
                ) from last_error
            time.sleep(0.2)


def _chrome_ready_message(config: AppConfig) -> str:
    return (
        f"Chrome 실행 요청 완료. 열린 Chrome 창에서 {_platform_display_name(config)}에 로그인하고 "
        "실적 페이지가 보이는 상태로 두세요."
    )


def _platform_display_name(config: AppConfig) -> str:
    if getattr(config, "platform_name", "baemin") == "coupang":
        return "쿠팡이츠"
    return "배민"


def _ensure_chrome_profile_free(profile_dir: Path) -> None:
    # 이 지점은 ``_ensure_cdp_endpoint_unused``가 통과한 뒤다. 즉 CDP 포트에 디버깅
    # 가능한 Chrome이 "없다"는 뜻이다. 그런데도 이 프로필을 쓰는 Chrome이 이미 떠
    # 있으면, 같은 ``--user-data-dir``로 chrome.exe를 다시 띄워도 새 프로세스는 기존
    # 인스턴스에 URL만 넘기고 바로 종료한다. 그 결과 디버깅 포트는 안 열린 채 빈 창만
    # 하나 더 뜬다(준비하기를 누를 때마다 창이 계속 생기는 증상). 그래서 이미 프로필을
    # 점유한 Chrome이 있으면 실행하지 않고, 그 Chrome을 먼저 닫으라고 명확히 안내한다.
    if not _chrome_running_for_profile(profile_dir):
        return
    raise BrowserLaunchError(
        "이 브라우저 프로필을 쓰는 Chrome이 이미 실행 중인데 CDP 디버깅 포트는 열려 있지 "
        "않습니다. 같은 프로필로 Chrome을 다시 실행하면 디버깅 포트 없이 빈 창만 계속 "
        "생깁니다.\n"
        f"먼저 이 프로필의 Chrome 창을 모두 닫은 뒤 다시 '준비하기'를 누르세요: {profile_dir}"
    )


def _chrome_running_for_profile(profile_dir: Path) -> bool:
    # psutil은 crawl4ai를 통해 들어오는 선택적 의존성이라, 없으면 이 안전장치를 조용히
    # 건너뛰고 기존 동작(실행 시도 후 _wait_for_cdp_ready 타임아웃)으로 떨어진다.
    try:
        import psutil
    except Exception:
        return False

    target = _profile_dir_key(profile_dir)
    for process in psutil.process_iter(["name"]):
        try:
            name = (process.info.get("name") or "").casefold()
            if name not in {"chrome.exe", "chrome"}:
                continue
            cmdline = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
        if _cmdline_uses_profile(cmdline, target):
            return True
    return False


def _cmdline_uses_profile(cmdline: list[str], target_key: str) -> bool:
    for arg in cmdline:
        if not arg.startswith("--user-data-dir"):
            continue
        # ``--user-data-dir=PATH`` 또는 ``--user-data-dir PATH`` 두 형태 모두 처리한다.
        _, _, value = arg.partition("=")
        value = value.strip().strip('"')
        if value and _profile_dir_key(Path(value)) == target_key:
            return True
    # ``--user-data-dir`` 다음 인자가 경로인 분리형도 확인한다.
    for index, arg in enumerate(cmdline[:-1]):
        if arg == "--user-data-dir":
            candidate = cmdline[index + 1].strip().strip('"')
            if candidate and _profile_dir_key(Path(candidate)) == target_key:
                return True
    return False


def _profile_dir_key(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path
    return str(resolved).casefold()


def _ensure_cdp_endpoint_unused(cdp_url: str, *, probe: CdpProbe) -> None:
    try:
        probe(cdp_url)
    except Exception:
        return
    raise BrowserLaunchError(
        "CDP 주소가 이미 사용 중입니다. 여러 계정은 탭마다 다른 CDP 포트를 사용하고, "
        "해당 포트의 기존 Chrome을 닫은 뒤 다시 준비하세요."
    )


def _probe_cdp_endpoint(cdp_url: str) -> None:
    with default_urlopen(cdp_url.rstrip("/") + "/json/version", timeout=1) as response:
        response.read()


def _cdp_port(cdp_url: str) -> int:
    port = urlsplit(cdp_url).port
    if port is None:
        raise BrowserLaunchError("CDP 주소에는 포트가 필요합니다. 예: http://127.0.0.1:9222")
    return port


def ensure_local_cdp_address(cdp_url: str) -> None:
    host = (urlsplit(cdp_url).hostname or "").casefold()
    if host not in {"127.0.0.1", "localhost"}:
        raise BrowserLaunchError(
            "CDP 주소는 IPv4 로컬 주소만 허용합니다. 예: http://127.0.0.1:9222\n"
            "원격 CDP 주소는 다른 로그인 세션을 읽을 수 있어 차단합니다."
        )


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
