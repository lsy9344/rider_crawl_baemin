"""Agent 실행 조건 게이트 + Windows autostart 등록 + 노드 역할 resolver (Story 4.7 / P3-07).

이 모듈이 책임지는 것(범위 — primitive 만, Epic 4 런타임-환경 시리즈의 마지막 조각):

* **launch-command 합성.** :func:`build_agent_launch_command` 가 재부팅 후 사용자 로그인 시
  Agent 를 띄울 커맨드(``python -m rider_agent run`` 또는 packaged exe ``<exe> run``)를
  만든다. **token/registration code 를 절대 포함하지 않는다** — identity 는 run 시점에 DPAPI
  store 에서 로드된다(secure_store.load_local_agent_identity).
* **노드 역할 resolver.** :func:`resolve_node_role`/:func:`requires_interactive_session`/
  :func:`handleable_job_types` 가 4.6 의 ``CAPABILITY_KAKAO_SEND`` capability 신호를 **명시적
  API 로 노출**할 뿐이다 — 새 역할 enum·별도 실행 경로·새 job-type 목록을 만들지 않는다
  (재구현 금지, 재사용만).
* **interactive-session 게이트.** :func:`is_interactive_session`/:func:`kakao_session_allowed`
  가 Kakao sender 노드가 비대화형(Session 0 service-only)에서 워커를 띄우지 않도록 **fail-closed**
  판정을 제공한다. 판정은 여기(autostart)에 응집하고 소비는 ``job_loop.run_agent`` 가 한다
  (``kakao_sender.py``/``heartbeat.py`` 는 0줄).
* **autostart 등록/해제/조회 primitive.** :func:`register_autostart`/:func:`unregister_autostart`/
  :func:`is_autostart_registered` 가 Startup 폴더 ``.cmd`` 쓰기 **또는** Task Scheduler
  (``schtasks /create /sc ONLOGON /it /f``) 호출로 launch 항목을 멱등 관리한다.

핵심 불변식(ADD-15·NFR-5/8):

* **secret 비노출.** launch 커맨드·Startup ``.cmd``·``schtasks`` 인자·로그 어디에도
  agent_token·registration code 가 들어가지 않는다(``run`` 진입만 등록). 등록/해제 로그는
  **고정 메시지**(``autostart registered (method=...)``)만 남기고 :func:`redact` 를 통과시킨다.
* **import-safety.** 실 ``ctypes``(세션 probe)·``subprocess``(schtasks)·Startup 파일 쓰기는
  **함수 내부 lazy + Windows-gated** 라, ``import rider_agent.autostart`` 가 비-Windows(WSL/CI)
  에서도 import-safe 하다(``secure_store._dpapi_crypt`` 선례). 테스트는 주입 fake
  ``writer``/``runner``/``session_probe`` + ``tmp_path``/fake environ 로 실 OS 없이 검증한다.

자기(own) 코드는 **순수 동기**이고 stdlib(+``rider_crawl.redaction``·``rider_agent.heartbeat``)만
import 한다(역방향/``rider_server`` import 0, ``asyncio`` 0) — 4.1 의 AST 가드가 자동 검사한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from rider_crawl.redaction import redact

from rider_agent.heartbeat import CAPABILITY_KAKAO_SEND, DEFAULT_CAPABILITIES

# ── 노드 역할 상수(**평문**, enum/"정확히 N개" lock 금지) ───────────────────────
# secure_store ``TOKEN_STATUS_*``·heartbeat ``DEFAULT_CAPABILITIES``·kakao_sender
# ``KAKAO_OUTCOME_*`` 선례: 후속(4.8/4.9)이 역할/사유를 늘려도 다른 lock 을 깨지 않는다
# (memory: enum-member-count-locks). rider_server 도메인 enum 은 import 하지 않는다(단방향).
NODE_ROLE_KAKAO_SENDER = "kakao_sender"
NODE_ROLE_CRAWLER_ONLY = "crawler_only"

# ── 세션 사유 상수(**평문**) ───────────────────────────────────────────────────
SESSION_INTERACTIVE = "interactive"  # interactive user session(Kakao 허용)
SESSION_0_SERVICE = "session0_service"  # Session 0 service-only(비대화형 → Kakao fail-closed)

# ── autostart 등록 메서드/식별자 상수(**평문**) ────────────────────────────────
METHOD_STARTUP = "startup"  # Windows Startup 폴더 .cmd(권장 기본 — 관리자 권한 불요·로그인=interactive)
METHOD_TASK_SCHEDULER = "task_scheduler"  # Task Scheduler ONLOGON(대안 — schtasks)

#: Task Scheduler 태스크명(고정). 서버 측 표시/배정은 Epic 5 소유.
TASK_NAME = "RiderBotAgent"
#: Startup 폴더에 쓰는 launch 스크립트 파일명(고정).
STARTUP_FILENAME = "rider_agent.cmd"


# ── launch-command 합성(token/code 절대 미포함) ───────────────────────────────


def build_agent_launch_command(
    *,
    executable: str = sys.executable,
    frozen: bool = getattr(sys, "frozen", False),
    server_url: str | None = None,
    module: str = "rider_agent",
) -> list[str]:
    """재부팅 후 로그인 시 Agent 를 띄울 launch 커맨드를 합성한다(``run`` 진입만).

    개발(``frozen=False``)은 ``[executable, "-m", module, "run"]``, packaged exe(``frozen=True``)는
    ``[executable, "run"]`` 를 돌려준다. ``server_url`` 을 주면 ``--server-url <url>`` 을 덧붙인다.

    **token/registration code 를 절대 포함하지 않는다** — ``run`` 진입은 identity 를 run 시점에
    DPAPI store 에서 로드한다(secure_store). 실행 경로/``--server-url`` 은 secret 이 아니다.
    """

    if frozen:
        command = [executable, "run"]
    else:
        command = [executable, "-m", module, "run"]
    if server_url:
        command += ["--server-url", server_url]
    return command


# ── 노드 역할 resolver(4.6 capability 신호를 "노출"만 — 재구현 금지) ────────────


def resolve_node_role(capabilities: Sequence[str] = DEFAULT_CAPABILITIES) -> str:
    """capability 로 노드 역할을 도출한다(``KAKAO_SEND`` 보유면 Kakao sender, 아니면 crawler-only).

    새 역할 enum·별도 실행 경로를 만들지 않고 4.6 ``start_kakao_sender_worker_if_enabled`` 와
    **같은 신호**(``CAPABILITY_KAKAO_SEND in capabilities``)를 명시적 API 로 노출만 한다.
    """

    return (
        NODE_ROLE_KAKAO_SENDER
        if CAPABILITY_KAKAO_SEND in capabilities
        else NODE_ROLE_CRAWLER_ONLY
    )


def requires_interactive_session(
    capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
) -> bool:
    """이 노드가 interactive 세션을 요구하는가(Kakao sender 노드만 True)."""

    return CAPABILITY_KAKAO_SEND in capabilities


def handleable_job_types(
    capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
) -> tuple[str, ...]:
    """이 노드가 처리 가능한 job type 집합(= capability 집합, ``heartbeat.CAPABILITY_*`` 재사용).

    crawler-only 는 ``KAKAO_SEND`` 를 뺀 **부분집합**일 뿐 별도 목록이 아니다(ADD-15 정합).
    """

    return tuple(capabilities)


# ── interactive-session probe + Kakao 세션 게이트 ─────────────────────────────


def _default_session_probe() -> bool:
    """현재 프로세스가 interactive(Session ≠ 0) 세션에서 도는지 판정(Windows-gated lazy).

    실 ``ctypes`` 호출은 함수 내부 lazy 라 ``import rider_agent.autostart`` 가 비-Windows 에서도
    import-safe 하다(``secure_store._dpapi_crypt`` 선례). 비-Windows(WSL/CI 개발)에선 실 Session 0
    판정이 의미 없으므로 ``True``(interactive)로 본다 — 운영(win32)에서만 실제 Session 0 차단이
    의미를 갖는다. 판정 실패는 fail-closed(비대화형으로 간주).
    """

    if sys.platform != "win32":
        return True

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    session_id = wintypes.DWORD()
    pid = kernel32.GetCurrentProcessId()
    ok = kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id))
    if not ok:
        return False
    return session_id.value != 0


def is_interactive_session(*, probe: Callable[[], bool] | None = None) -> bool:
    """현재 세션이 interactive 인가. ``probe`` 주입이면 그 결과, 미주입이면 기본 Windows-gated probe.

    테스트는 ``probe=lambda: False`` 로 Session 0 을 결정적으로 재현한다(실 OS 세션 API 미호출).
    """

    if probe is not None:
        return bool(probe())
    return _default_session_probe()


def kakao_session_allowed(
    capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
    *,
    session_probe: Callable[[], bool] | None = None,
) -> tuple[bool, str | None]:
    """Kakao sender 워커 기동을 허용할지 ``(allowed, reason)`` 으로 판정한다.

    * crawler-only 노드(``KAKAO_SEND`` 없음) → ``(True, None)`` — 게이트 무관(AC3 정합).
    * ``session_probe`` 미주입 → ``(True, None)`` — **게이트 없음 = 4.6 동작 그대로(무회귀 절대 불변)**.
    * Kakao sender 노드 + probe 주입 → interactive 면 ``(True, SESSION_INTERACTIVE)``,
      비대화형(Session 0)이면 **fail-closed** ``(False, SESSION_0_SERVICE)``.

    판정 로직은 여기(autostart)에 응집하고 ``run_agent`` 가 소비한다 — ``kakao_sender.py``/
    ``heartbeat.py`` 는 0줄. 사유는 평문 상수만(raw OS 식별자 비노출).
    """

    if CAPABILITY_KAKAO_SEND not in capabilities:
        return (True, None)
    if session_probe is None:
        return (True, None)
    if is_interactive_session(probe=session_probe):
        return (True, SESSION_INTERACTIVE)
    return (False, SESSION_0_SERVICE)


# ── autostart 등록/해제/조회 primitive(Startup 폴더 + Task Scheduler) ──────────


def default_startup_dir(*, environ: Any = os.environ) -> Path:
    """Windows 사용자 Startup 폴더 경로(``%APPDATA%/.../Startup``). 주입 가능(테스트는 fake environ).

    실 ``%APPDATA%`` 미사용으로 테스트가 ``tmp_path``/fake environ 로 격리된다
    (``secure_store.default_agent_state_dir`` 의 주입-가능 동형 설계).
    """

    appdata = environ.get("APPDATA") or ""
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def _startup_cmd_text(command: Sequence[str]) -> str:
    """Startup ``.cmd`` 내용 — 로그인 시 실행될 launch 커맨드(경로 공백 안전 인용).

    ``subprocess.list2cmdline`` 으로 인용해 재부팅 자동 시작 안정성을 지킨다. 커맨드 자체에
    secret 이 없다(``run`` 진입 — identity 는 DPAPI). 줄바꿈은 ``\\n`` 으로 두어 text-mode
    writer 가 Windows 에서 CRLF 로 변환하게 한다(round-trip 일관 → 멱등 비교가 안정적).
    """

    cwd_line = subprocess.list2cmdline(["cd", "/d", str(Path.cwd())])
    line = subprocess.list2cmdline(list(command))
    return f"@echo off\n{cwd_line}\n{line}\n"


def _schtasks_create_args(command: Sequence[str]) -> list[str]:
    """``schtasks /create`` 인자 리스트. ``/sc ONLOGON``=로그인 시, ``/it``=interactive,
    ``/f``=멱등 덮어쓰기. ``/tr`` 은 인용된 launch 커맨드(secret 없음)."""

    return [
        "schtasks",
        "/create",
        "/tn",
        TASK_NAME,
        "/tr",
        subprocess.list2cmdline(list(command)),
        "/sc",
        "ONLOGON",
        "/it",
        "/f",
    ]


def _default_startup_writer(path: Path, content: str) -> None:
    """Startup ``.cmd`` 실 쓰기(기본 writer). 파일 I/O 부작용은 주입으로 대체 가능."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _default_startup_remover(path: Path) -> bool:
    """Startup ``.cmd`` 실 삭제(기본 remover). 미존재 해제는 무해(멱등)."""

    p = Path(path)
    if not p.exists():
        return False
    p.unlink()
    return True


def _default_runner(args: Sequence[str]) -> Any:
    """``schtasks`` 실 호출(기본 runner·Windows-gated lazy).

    실 ``subprocess.run`` 은 win32 에서만 의미가 있으므로 비-Windows 에선 raise 한다 — 테스트는
    fake ``runner`` 를 주입해 인자만 캡처한다(실 ``schtasks`` 미호출).
    """

    if sys.platform != "win32":
        raise RuntimeError(
            "schtasks autostart는 Windows에서만 동작한다(비-Windows는 runner 주입으로 검증)"
        )
    return subprocess.run(list(args), capture_output=True, text=True)


def _runner_ok(completed: Any) -> bool:
    """runner 결과가 성공(returncode == 0)인가. fake/실 ``CompletedProcess`` 양쪽 호환."""

    return getattr(completed, "returncode", 1) == 0


def _read_text_or_none(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _startup_target(startup_dir: Path | None) -> Path:
    return (startup_dir if startup_dir is not None else default_startup_dir()) / STARTUP_FILENAME


def register_autostart(
    *,
    command: Sequence[str],
    method: str = METHOD_STARTUP,
    startup_dir: Path | None = None,
    writer: Callable[[Path, str], None] | None = None,
    runner: Callable[[Sequence[str]], Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """autostart 항목을 등록한다(멱등). 모든 OS 부작용(파일 쓰기·subprocess)은 주입 가능.

    * ``METHOD_STARTUP`` — ``command`` 를 감싼 ``.cmd`` 텍스트를 Startup 폴더에 **멱등 쓰기**
      (같은 내용이면 재쓰기 0 — churn/blob 회전 방지, ``DpapiSecretStore.put`` 선례).
    * ``METHOD_TASK_SCHEDULER`` — ``schtasks /create ... /f`` 인자를 주입 ``runner`` 로 호출
      (``/f`` 가 덮어쓰기 멱등).

    결과는 ``{method, target, command}`` dict 다. 등록 로그는 **고정 메시지**(method 만)·redact
    통과 — target 경로/사용자명·command 를 로그에 싣지 않는다(secret/운영 식별자 비노출).
    """

    if method == METHOD_STARTUP:
        target = _startup_target(startup_dir)
        content = _startup_cmd_text(command)
        if _read_text_or_none(target) != content:
            write = writer if writer is not None else _default_startup_writer
            write(target, content)
        result: dict[str, Any] = {
            "method": method,
            "target": str(target),
            "command": list(command),
        }
    elif method == METHOD_TASK_SCHEDULER:
        run = runner if runner is not None else _default_runner
        run(_schtasks_create_args(command))
        result = {"method": method, "target": TASK_NAME, "command": list(command)}
    else:
        raise ValueError(f"unknown autostart method: {method}")

    if log is not None:
        log(redact(f"autostart registered (method={method})"))
    return result


def unregister_autostart(
    *,
    method: str = METHOD_STARTUP,
    startup_dir: Path | None = None,
    remover: Callable[[Path], bool] | None = None,
    runner: Callable[[Sequence[str]], Any] | None = None,
) -> bool:
    """autostart 항목을 해제한다(멱등 — 미존재 해제는 무해). 부작용은 주입으로 대체 가능.

    Startup 은 ``.cmd`` 삭제, Task Scheduler 는 ``schtasks /delete /tn ... /f`` 다. 제거 성공
    여부(``True``)를 돌려준다.
    """

    if method == METHOD_STARTUP:
        remove = remover if remover is not None else _default_startup_remover
        return remove(_startup_target(startup_dir))
    if method == METHOD_TASK_SCHEDULER:
        run = runner if runner is not None else _default_runner
        return _runner_ok(run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"]))
    raise ValueError(f"unknown autostart method: {method}")


def is_autostart_registered(
    *,
    method: str = METHOD_STARTUP,
    startup_dir: Path | None = None,
    runner: Callable[[Sequence[str]], Any] | None = None,
) -> bool:
    """autostart 항목이 등록돼 있는가. Startup 은 ``.cmd`` 존재, Task Scheduler 는
    ``schtasks /query /tn ...`` 성공 여부."""

    if method == METHOD_STARTUP:
        return _startup_target(startup_dir).exists()
    if method == METHOD_TASK_SCHEDULER:
        run = runner if runner is not None else _default_runner
        return _runner_ok(run(["schtasks", "/query", "/tn", TASK_NAME]))
    raise ValueError(f"unknown autostart method: {method}")
