"""``python -m rider_agent`` 진입점 — 동기(sync) thin CLI (Story 4.1 + 4.2 + 4.4).

서브커맨드가 없으면 4.1 의 sync 시작 배너를 그대로 출력한다(무회귀). ``register`` 서브커맨드는
Story 4.2 가 추가한 **얇은** 등록 진입이다 — 핵심 로직은 ``registration.register_agent`` 에 있고,
여기서는 인자 파싱 → 실제 transport/store/identity_path 주입 → 호출 → **redaction 통과한** 한 줄
결과 출력만 한다. ``run`` 서브커맨드는 Story 4.4 가 추가한 **얇은** job 루프 진입이다 — 핵심
로직은 ``job_loop.run_agent`` 에 있고, 여기서는 실제 HttpTransport(op_label="agent jobs")/
store/identity_path 주입 → 호출만 한다(둘 다 token/registration code 를 출력하지 않는다).

GUI(tkinter)/레거시 UI(``rider_crawl.ui``/``rider_crawl.app``)는 import 하지 않는다(4.1 가드).
무거운 import(job_loop/registration/secure_store)는 **함수 내부로 defer** 해 인자 없는 배너 경로의
무부작용과 runpy RuntimeWarning 회피를 지킨다. network 부작용은 ``register``/``run`` 을
**명시적으로 호출할 때만** 발생한다(인자 없는 실행은 무부작용).
"""

from __future__ import annotations

import argparse

from rider_agent import __version__, reuse
from rider_crawl.redaction import redact


def _print_banner() -> int:
    # reuse seam 은 상단 import 로 로드·검증된다. 배너에 seam 개수를 실어 그 import 를 실제로
    # 사용한다(4.1 계약 유지). 함수는 실행하지 않는다.
    print(
        f"rider_agent {__version__} (sync runtime; reuses rider_crawl "
        f"[{len(reuse.__all__)} seams], no new framework)"
    )
    return 0


def _parse_register_args(register_argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rider_agent register",
        description="일회용 registration code로 이 Agent를 서버에 등록한다.",
    )
    parser.add_argument(
        "--code",
        required=True,
        help="운영자가 발급한 일회용 registration code(평문은 출력/로그에 남지 않는다)",
    )
    return parser.parse_args(register_argv)


def _run_register(
    register_argv: list[str],
    *,
    transport: object | None = None,
    store: object | None = None,
    identity_path: object | None = None,
    registrar: object | None = None,
) -> int:
    """register 서브커맨드 실행. 의존성은 주입 가능(테스트는 fake transport/store 주입)."""

    args = _parse_register_args(register_argv)

    from rider_agent.registration import HttpTransport, RegistrationError, register_agent
    from rider_agent.secure_store import (
        DpapiSecretStore,
        default_identity_path,
        default_secret_store_path,
        load_local_agent_identity,
    )

    if registrar is None:
        registrar = register_agent
    if store is None:
        store = DpapiSecretStore(default_secret_store_path())
    if identity_path is None:
        identity_path = default_identity_path()
    if transport is None:
        transport = HttpTransport()

    pre_existing = (
        load_local_agent_identity(store=store, identity_path=identity_path) is not None
    )

    try:
        identity = registrar(
            args.code, transport=transport, store=store, identity_path=identity_path
        )
    except RegistrationError as exc:
        # 예외 메시지에 평문이 없도록 registration 이 보장하지만, 출력도 redact 를 한 번 더 통과시킨다.
        print(redact(f"agent registration failed: {exc}"))
        return 1

    state = "already-registered" if pre_existing else "registered"
    print(
        redact(
            f"agent {state}: agent_id={identity.agent_id} "
            f"config_version={identity.config_version}"
        )
    )
    return 0


def _parse_run_args(run_argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rider_agent run",
        description="outbound HTTPS job 폴링/claim/complete 루프를 시작한다(inbound 포트 미개방).",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        help="서버 base URL(미지정 시 RIDER_AGENT_SERVER_URL env 또는 기본값)",
    )
    return parser.parse_args(run_argv)


def _run_agent_loop(
    run_argv: list[str],
    *,
    transport: object | None = None,
    store: object | None = None,
    identity_path: object | None = None,
    runner: object | None = None,
) -> int:
    """run 서브커맨드 실행. 의존성은 주입 가능(테스트는 fake transport/store/runner 주입).

    실 CLI 의 ``run_agent`` 는 무한 루프(정상)다 — 테스트는 fake ``runner`` 또는 즉시 정지하는
    ``stop_event``/주입 sleep 으로 hang 을 막는다. 출력은 token/code 미포함(redact 통과).
    """

    args = _parse_run_args(run_argv)

    from rider_agent.job_loop import run_agent
    from rider_agent.registration import HttpTransport
    from rider_agent.secure_store import (
        DpapiSecretStore,
        default_identity_path,
        default_secret_store_path,
    )

    if runner is None:
        runner = run_agent
    if store is None:
        store = DpapiSecretStore(default_secret_store_path())
    if identity_path is None:
        identity_path = default_identity_path()
    if transport is None:
        transport = HttpTransport(op_label="agent jobs")

    summary = runner(
        transport=transport,
        store=store,
        identity_path=identity_path,
        base_url=args.server_url,
        start_auth_worker=True,
        start_crawl_worker=True,
        start_kakao_sender=True,
    )

    if not getattr(summary, "started", False):
        print(
            redact(
                "agent loop not started: valid identity/token required "
                "(run `rider_agent register --code ...` first)"
            )
        )
        return 1
    print(redact("agent loop stopped"))
    return 0


def _parse_autostart_args(autostart_argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rider_agent autostart",
        description="재부팅 후 사용자 로그인 시 Agent 자동 시작을 등록/해제/조회한다.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--register", action="store_true", help="autostart launch 항목을 등록한다(멱등)"
    )
    action.add_argument(
        "--unregister", action="store_true", help="autostart launch 항목을 해제한다(멱등)"
    )
    action.add_argument(
        "--status", action="store_true", help="autostart 등록 여부를 조회한다"
    )
    parser.add_argument(
        "--method",
        default="startup",
        choices=("startup", "task_scheduler"),
        help="등록 메커니즘(기본 startup — 관리자 권한 불요·로그인=interactive)",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        help="launch 커맨드에 실을 서버 base URL(secret 아님, 미지정 시 미포함)",
    )
    return parser.parse_args(autostart_argv)


def _run_autostart(
    autostart_argv: list[str],
    *,
    register: object | None = None,
    unregister: object | None = None,
    is_registered: object | None = None,
    build_command: object | None = None,
) -> int:
    """autostart 서브커맨드 실행(얇은 wiring). 의존성은 주입 가능(테스트는 fake autostart 주입).

    실제 등록 primitive 를 호출하고(빈 stub 금지) **redact 통과한 고정 메시지 한 줄**만 출력한다 —
    launch 커맨드/경로/사용자명·token/code 를 출력하지 않는다(secret/운영 식별자 비노출).
    """

    args = _parse_autostart_args(autostart_argv)

    # autostart import 는 함수 내부 defer — import-safety/runpy 경고 회피(4.1·4.2 패턴).
    from rider_agent import autostart

    if register is None:
        register = autostart.register_autostart
    if unregister is None:
        unregister = autostart.unregister_autostart
    if is_registered is None:
        is_registered = autostart.is_autostart_registered
    if build_command is None:
        build_command = autostart.build_agent_launch_command

    if args.unregister:
        removed = unregister(method=args.method)
        state = "unregistered" if removed else "not-registered"
        print(redact(f"autostart {state} (method={args.method})"))
        return 0
    if args.status:
        present = is_registered(method=args.method)
        state = "registered" if present else "not-registered"
        print(redact(f"autostart status: {state} (method={args.method})"))
        return 0

    # 기본/--register: launch 커맨드(=run, token/code 0)를 합성해 등록한다.
    command = build_command(server_url=args.server_url)
    register(command=command, method=args.method)
    print(redact(f"autostart registered (method={args.method})"))
    return 0


def main(argv: list[str] | None = None) -> int:
    # argv 명시 호출(테스트·CLI)만 신뢰한다. 인자 없는 호출은 배너(4.1 계약). runpy 가 pytest
    # 안에서 __main__ 을 돌리면 sys.argv 가 오염돼 있으므로, 'register'/'run'/'autostart' 가 아닌
    # 토큰은 모두 배너로 폴백한다(서브파서 invalid-choice 로 비정상 종료하지 않게 — 무회귀).
    if argv is None:
        argv = []
    if argv and argv[0] == "register":
        return _run_register(argv[1:])
    if argv and argv[0] == "run":
        return _run_agent_loop(argv[1:])
    if argv and argv[0] == "autostart":
        return _run_autostart(argv[1:])
    return _print_banner()


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
