"""``python -m rider_agent`` 진입점 — 동기(sync) thin CLI (Story 4.1 + 4.2).

서브커맨드가 없으면 4.1 의 sync 시작 배너를 그대로 출력한다(무회귀). ``register`` 서브커맨드는
Story 4.2 가 추가한 **얇은** 등록 진입이다 — 핵심 로직은 ``registration.register_agent`` 에 있고,
여기서는 인자 파싱 → 실제 transport/store/identity_path 주입 → 호출 → **redaction 통과한** 한 줄
결과 출력만 한다. token/registration code 를 출력하지 않는다.

GUI(tkinter)/레거시 UI(``rider_crawl.ui``/``rider_crawl.app``)는 import 하지 않는다(4.1 가드).
network 부작용은 ``register`` 를 **명시적으로 호출할 때만** 발생한다(인자 없는 실행은 무부작용).
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


def main(argv: list[str] | None = None) -> int:
    # argv 명시 호출(테스트·CLI)만 신뢰한다. 인자 없는 호출은 배너(4.1 계약). runpy 가 pytest
    # 안에서 __main__ 을 돌리면 sys.argv 가 오염돼 있으므로, 'register' 가 아닌 토큰은 모두
    # 배너로 폴백한다(서브파서 invalid-choice 로 비정상 종료하지 않게 — 무회귀).
    if argv is None:
        argv = []
    if argv and argv[0] == "register":
        return _run_register(argv[1:])
    return _print_banner()


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
