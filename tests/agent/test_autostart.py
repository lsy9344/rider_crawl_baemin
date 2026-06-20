"""Story 4.7 — Agent 실행 조건 게이트 + Windows autostart 등록 + 노드 역할 resolver 검증.

외부 호출 없음: 실 ``schtasks``·실 세션 API(``ctypes``)·실 ``%APPDATA%`` Startup 폴더 쓰기를
쓰지 않고 **주입 fake ``writer``/``runner``/``session_probe``/``remover`` + ``tmp_path``/fake
environ + 호출 인자 캡처**로 launch-command 합성·등록 멱등·해제·세션 게이트·노드 역할을 결정적
으로 검증한다(비-Windows CI 에서도 통과 — import-safety). 값은 명백한 가짜 경로/명령만
(``py-fake``/``https://srv-fake.example``) — 실 token/chat_id/휴대폰/이메일/OTP 원문 없음.

``rider_agent.__main__`` 은 **모듈 top 에서 import 하지 않는다**(필요 시 함수 내부 defer) —
runpy RuntimeWarning 회피(memory/agent-main-runpy-warning).
"""

from __future__ import annotations

import threading
import types
from pathlib import Path

import pytest

from rider_agent.autostart import (
    METHOD_STARTUP,
    METHOD_TASK_SCHEDULER,
    NODE_ROLE_CRAWLER_ONLY,
    NODE_ROLE_KAKAO_SENDER,
    SESSION_0_SERVICE,
    SESSION_INTERACTIVE,
    STARTUP_FILENAME,
    TASK_NAME,
    build_agent_launch_command,
    default_startup_dir,
    handleable_job_types,
    is_autostart_registered,
    is_interactive_session,
    kakao_session_allowed,
    register_autostart,
    requires_interactive_session,
    resolve_node_role,
    unregister_autostart,
)
from rider_agent.heartbeat import (
    CAPABILITY_KAKAO_SEND,
    DEFAULT_CAPABILITIES,
    DEFAULT_KAKAO_STATUS,
    build_heartbeat_payload,
)
from rider_agent.job_loop import run_agent
from rider_agent.secure_store import AgentIdentity, save_agent_identity

# 가짜 식별자만(누출 가드 — 실 token/방명/연락처 금지). job-loop 헤더에 반복 노출되는 token 은
# run_agent 게이트 테스트에서 store 로만 들어가고 어떤 산출물에도 평문으로 새지 않아야 한다.
FAKE_TOKEN = "agtok-fake-autostart-secret"
FAKE_SERVER_URL = "https://srv-fake.example"

_IDENTITY = AgentIdentity(
    agent_id="agent-fake-1",
    agent_token=FAKE_TOKEN,
    tenant_scope={"tenant": "t-fake"},
    config_version="cfg-fake-1",
)


# ── 주입 fake 들 ─────────────────────────────────────────────────────────────


class _FakeWriter:
    """fake Startup writer — 호출 횟수를 세고 실제 ``tmp_path`` 에 쓴다(멱등 검증용 읽기 가능)."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, path: Path, content: str) -> None:
        self.calls += 1
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")


class _FakeRunner:
    """fake schtasks runner — 받은 인자 리스트를 캡처하고 주어진 returncode 를 돌려준다."""

    def __init__(self, *, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._returncode = returncode

    def __call__(self, args):
        self.calls.append(list(args))
        return types.SimpleNamespace(returncode=self._returncode)


class _FakeStore:
    """최소 SecretStore — token 1개 보관/조회(비-Windows 에서도 동작, codec 불요)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def put(self, value, *, ref=""):
        self._data[ref] = value
        return ref

    def resolve(self, ref):
        return self._data.get(ref)


class _FakeTransport:
    """모든 POST 에 안전 빈 응답(claim → 빈 jobs)을 돌려주는 fake."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def post_json(self, url, body, *, headers=None):
        self.calls.append((url, body, headers))
        return {}


def _fake_build_config(*, room_name, **_ignored):
    return {"kakao_chat_name": room_name}


class _RecordingSend:
    """no-op fake send(주입 — 기본 send_kakao_text 의 실 pyautogui 경로를 끌지 않게)."""

    def __call__(self, *args, **kwargs):
        return None


def _crawler_only_caps():
    return tuple(c for c in DEFAULT_CAPABILITIES if c != CAPABILITY_KAKAO_SEND)


# ══════════════════════════════════════════════════════════════════════════
# AC2 — launch-command 합성(run 진입만, token/code 비노출)
# ══════════════════════════════════════════════════════════════════════════


def test_build_launch_command_dev_uses_module_run():
    cmd = build_agent_launch_command(executable="py-fake", frozen=False)
    assert cmd == ["py-fake", "-m", "rider_agent", "run"]


def test_build_launch_command_frozen_uses_exe_run():
    cmd = build_agent_launch_command(executable="agent-fake.exe", frozen=True)
    assert cmd == ["agent-fake.exe", "run"]


def test_build_launch_command_appends_server_url():
    cmd = build_agent_launch_command(
        executable="py-fake", frozen=False, server_url=FAKE_SERVER_URL
    )
    assert cmd[-2:] == ["--server-url", FAKE_SERVER_URL]


def test_launch_command_never_contains_token_or_code():
    for frozen in (True, False):
        cmd = build_agent_launch_command(
            executable="py-fake", frozen=frozen, server_url=FAKE_SERVER_URL
        )
        joined = " ".join(cmd)
        assert "--code" not in joined
        assert "token" not in joined.lower()
        assert FAKE_TOKEN not in joined
        assert "run" in cmd  # run 진입만(identity 는 run 시점 DPAPI 로드)


# ══════════════════════════════════════════════════════════════════════════
# AC2 — 등록 멱등·해제·조회(Startup 폴더, 주입 writer + tmp_path)
# ══════════════════════════════════════════════════════════════════════════


def test_startup_register_is_idempotent_and_unregister_clears(tmp_path):
    writer = _FakeWriter()
    cmd = build_agent_launch_command(executable="py-fake", frozen=False)

    result = register_autostart(command=cmd, startup_dir=tmp_path, writer=writer)
    assert result["method"] == METHOD_STARTUP
    assert writer.calls == 1
    assert is_autostart_registered(startup_dir=tmp_path) is True

    # 같은 커맨드 재등록 → 내용 동일 → 재쓰기 0(멱등 — DpapiSecretStore.put 선례).
    register_autostart(command=cmd, startup_dir=tmp_path, writer=writer)
    assert writer.calls == 1

    # 해제 → 파일 제거, 조회 False. 멱등 해제(미존재)는 무해(False).
    assert unregister_autostart(startup_dir=tmp_path) is True
    assert is_autostart_registered(startup_dir=tmp_path) is False
    assert unregister_autostart(startup_dir=tmp_path) is False


def test_startup_cmd_contains_run_command_no_secret(tmp_path):
    writer = _FakeWriter()
    cmd = build_agent_launch_command(
        executable="py-fake", frozen=False, server_url=FAKE_SERVER_URL
    )
    register_autostart(command=cmd, startup_dir=tmp_path, writer=writer)

    text = (tmp_path / STARTUP_FILENAME).read_text(encoding="utf-8")
    assert "run" in text  # run 진입
    assert "--code" not in text
    assert FAKE_TOKEN not in text


def test_startup_cmd_changes_to_registration_cwd(tmp_path, monkeypatch):
    writer = _FakeWriter()
    install_dir = tmp_path / "Rider Bot Install"
    install_dir.mkdir()
    monkeypatch.chdir(install_dir)
    cmd = build_agent_launch_command(executable="py-fake", frozen=False)

    register_autostart(command=cmd, startup_dir=tmp_path, writer=writer)

    text = (tmp_path / STARTUP_FILENAME).read_text(encoding="utf-8")
    assert f'cd /d "{install_dir}"' in text
    assert text.index("cd /d") < text.index("py-fake")


def test_default_startup_dir_uses_injected_environ(tmp_path):
    d = default_startup_dir(environ={"APPDATA": str(tmp_path)})
    assert d == (
        tmp_path
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


# ══════════════════════════════════════════════════════════════════════════
# AC2 — Task Scheduler 경로(주입 runner, schtasks 인자 캡처)
# ══════════════════════════════════════════════════════════════════════════


def test_task_scheduler_register_builds_onlogon_interactive_args():
    runner = _FakeRunner()
    cmd = build_agent_launch_command(executable="py-fake", frozen=False)

    result = register_autostart(
        command=cmd, method=METHOD_TASK_SCHEDULER, runner=runner
    )
    assert result["method"] == METHOD_TASK_SCHEDULER
    assert result["target"] == TASK_NAME

    args = runner.calls[-1]
    assert args[0] == "schtasks"
    for token in ("/create", "/sc", "ONLOGON", "/it", "/f", TASK_NAME):
        assert token in args
    # /tr 의 launch 커맨드에 token/code 0.
    joined = " ".join(args)
    assert "--code" not in joined
    assert FAKE_TOKEN not in joined
    assert "run" in joined


def test_task_scheduler_query_and_delete_use_runner_returncode():
    present = is_autostart_registered(
        method=METHOD_TASK_SCHEDULER, runner=_FakeRunner(returncode=0)
    )
    assert present is True
    absent = is_autostart_registered(
        method=METHOD_TASK_SCHEDULER, runner=_FakeRunner(returncode=1)
    )
    assert absent is False

    deleter = _FakeRunner(returncode=0)
    assert unregister_autostart(method=METHOD_TASK_SCHEDULER, runner=deleter) is True
    assert "/delete" in deleter.calls[-1]


# ══════════════════════════════════════════════════════════════════════════
# AC1·AC3 — 노드 역할 resolver + interactive-session 게이트
# ══════════════════════════════════════════════════════════════════════════


def test_resolve_node_role_kakao_sender_for_default_caps():
    assert resolve_node_role(DEFAULT_CAPABILITIES) == NODE_ROLE_KAKAO_SENDER
    assert resolve_node_role() == NODE_ROLE_KAKAO_SENDER  # 기본집합 = Kakao sender
    assert requires_interactive_session(DEFAULT_CAPABILITIES) is True


def test_resolve_node_role_crawler_only_without_kakao():
    caps = _crawler_only_caps()
    assert resolve_node_role(caps) == NODE_ROLE_CRAWLER_ONLY
    assert requires_interactive_session(caps) is False


def test_handleable_job_types_equals_capability_set():
    caps = (CAPABILITY_KAKAO_SEND, "CRAWL_BAEMIN")
    assert handleable_job_types(caps) == caps
    assert handleable_job_types(DEFAULT_CAPABILITIES) == tuple(DEFAULT_CAPABILITIES)


def test_is_interactive_session_uses_injected_probe():
    assert is_interactive_session(probe=lambda: True) is True
    assert is_interactive_session(probe=lambda: False) is False


def test_kakao_session_gate_fail_closed_on_session0():
    allowed, reason = kakao_session_allowed(
        DEFAULT_CAPABILITIES, session_probe=lambda: False
    )
    assert allowed is False
    assert reason == SESSION_0_SERVICE


def test_kakao_session_gate_allows_interactive():
    allowed, reason = kakao_session_allowed(
        DEFAULT_CAPABILITIES, session_probe=lambda: True
    )
    assert allowed is True
    assert reason == SESSION_INTERACTIVE


def test_kakao_session_gate_irrelevant_for_crawler_only():
    allowed, reason = kakao_session_allowed(
        _crawler_only_caps(), session_probe=lambda: False
    )
    assert allowed is True  # crawler-only 노드는 게이트 대상 아님
    assert reason is None


def test_kakao_session_gate_no_probe_is_no_regression():
    allowed, reason = kakao_session_allowed(DEFAULT_CAPABILITIES, session_probe=None)
    assert allowed is True  # 미주입 → 게이트 없음 = 4.6 동작 그대로
    assert reason is None


def test_session_reason_constants_are_plain_strings():
    assert isinstance(SESSION_INTERACTIVE, str)
    assert isinstance(SESSION_0_SERVICE, str)
    assert SESSION_0_SERVICE == "session0_service"
    assert NODE_ROLE_KAKAO_SENDER == "kakao_sender"
    assert NODE_ROLE_CRAWLER_ONLY == "crawler_only"


# ══════════════════════════════════════════════════════════════════════════
# AC1 — run_agent 게이트 배선(비대화형 차단) + 무회귀(미주입 통과)
# ══════════════════════════════════════════════════════════════════════════


def test_run_agent_session_gate_blocks_kakao_on_session0(tmp_path):
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    stop.set()  # 루프 진입 즉시 정지(결정적).
    statuses: list[str] = []

    summary = run_agent(
        transport=_FakeTransport(),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_kakao_sender=True,
        session_probe=lambda: False,  # Session 0(비대화형) 재현
        on_status=statuses.append,
    )

    assert summary.kakao_worker is None  # fail-closed: 워커 미기동
    # kakao_status 는 4.3 기본 "disabled" 유지.
    payload = build_heartbeat_payload(
        _IDENTITY, kakao_status_provider=summary.reporter._kakao_status_provider
    )
    assert payload["kakao_status"] == DEFAULT_KAKAO_STATUS
    assert SESSION_0_SERVICE in statuses  # 명확한 사유 surfacing


def test_run_agent_session_probe_none_is_no_regression(tmp_path):
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    stop.set()

    summary = run_agent(
        transport=_FakeTransport(),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_kakao_sender=True,
        session_probe=None,  # 미주입 → 게이트 없음(무회귀)
        kakao_send=_RecordingSend(),
        kakao_build_config=_fake_build_config,
    )

    assert summary.kakao_worker is not None  # 워커 기동(4.6 동작 보존)
    assert summary.kakao_worker.thread is not None
    assert not summary.kakao_worker.thread.is_alive()  # 종료 시 정리됨


def test_run_agent_session_gate_no_token_leak_in_status(tmp_path):
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    stop.set()
    logs: list[str] = []
    statuses: list[str] = []

    run_agent(
        transport=_FakeTransport(),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_kakao_sender=True,
        session_probe=lambda: False,
        on_status=statuses.append,
        log=logs.append,
    )

    joined = " ".join(logs) + " ".join(statuses)
    assert FAKE_TOKEN not in joined  # token 평문 0


# ══════════════════════════════════════════════════════════════════════════
# AC2 — __main__ 얇은 autostart 서브커맨드(주입 의존성, redact 출력, 무회귀)
# ══════════════════════════════════════════════════════════════════════════


def test_main_routes_autostart_subcommand(monkeypatch):
    from rider_agent import __main__ as agent_main

    seen: dict = {}

    def fake_autostart(argv, **_kwargs):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(agent_main, "_run_autostart", fake_autostart)
    rc = agent_main.main(["autostart", "--status"])
    assert rc == 0
    assert seen["argv"] == ["--status"]


def test_run_autostart_register_calls_primitive_and_redacts(capsys):
    from rider_agent import __main__ as agent_main

    seen: dict = {}

    def fake_register(*, command, method, **_ignored):
        seen["command"] = command
        seen["method"] = method
        return {"method": method, "target": "fake-target", "command": command}

    rc = agent_main._run_autostart(
        ["--register", "--server-url", FAKE_SERVER_URL], register=fake_register
    )
    assert rc == 0
    # 등록 primitive 가 build_agent_launch_command 산출 커맨드(run, token/code 0)를 받았다.
    assert "run" in seen["command"]
    assert "--code" not in " ".join(seen["command"])
    out = capsys.readouterr().out
    assert "autostart registered" in out
    assert FAKE_SERVER_URL not in out  # 고정 메시지만 — server-url/경로 미노출
    assert FAKE_TOKEN not in out


def test_run_autostart_status_returns_0_with_injected_dependency(capsys):
    from rider_agent import __main__ as agent_main

    rc = agent_main._run_autostart(["--status"], is_registered=lambda *, method: True)
    assert rc == 0
    assert "registered" in capsys.readouterr().out


def test_run_autostart_unregister_returns_0_with_injected_dependency(capsys):
    from rider_agent import __main__ as agent_main

    rc = agent_main._run_autostart(
        ["--unregister"], unregister=lambda *, method: True
    )
    assert rc == 0
    assert "unregistered" in capsys.readouterr().out


def test_main_without_subcommand_still_prints_banner(capsys):
    from rider_agent import __main__ as agent_main

    rc = agent_main.main([])
    assert rc == 0
    assert "sync runtime" in capsys.readouterr().out  # 무회귀(4.1 배너)


# ══════════════════════════════════════════════════════════════════════════
# 누출 가드 — 등록 산출물/로그에 secret·raw 경로 0
# ══════════════════════════════════════════════════════════════════════════


def test_register_log_is_fixed_message_no_path(tmp_path):
    writer = _FakeWriter()
    logs: list[str] = []
    cmd = build_agent_launch_command(
        executable=str(tmp_path / "py-fake.exe"), frozen=False
    )
    register_autostart(
        command=cmd, startup_dir=tmp_path, writer=writer, log=logs.append
    )

    assert len(logs) == 1
    assert "autostart registered" in logs[0]
    assert "method=startup" in logs[0]
    assert str(tmp_path) not in logs[0]  # 경로/사용자명 미노출(고정 사유만)


def test_no_secret_in_startup_and_schtasks_artifacts(tmp_path):
    writer = _FakeWriter()
    runner = _FakeRunner()
    cmd = build_agent_launch_command(
        executable="py-fake", frozen=False, server_url=FAKE_SERVER_URL
    )
    register_autostart(command=cmd, startup_dir=tmp_path, writer=writer)
    register_autostart(command=cmd, method=METHOD_TASK_SCHEDULER, runner=runner)

    cmd_text = (tmp_path / STARTUP_FILENAME).read_text(encoding="utf-8")
    schtasks_args = " ".join(runner.calls[-1])
    for blob in (cmd_text, schtasks_args):
        assert "--code" not in blob
        assert FAKE_TOKEN not in blob
        assert "run" in blob  # run 진입만


# ══════════════════════════════════════════════════════════════════════════
# QA gap — 등록 멱등의 반대 분기·기본 writer·경로 인용(CRLF round-trip 회귀 가드) (AC2)
# ══════════════════════════════════════════════════════════════════════════


def test_startup_register_rewrites_on_changed_command(tmp_path):
    """멱등의 반대 분기: 커맨드가 바뀌면(내용 변경) Startup .cmd 를 다시 쓴다."""

    writer = _FakeWriter()
    cmd1 = build_agent_launch_command(executable="py-fake", frozen=False)
    register_autostart(command=cmd1, startup_dir=tmp_path, writer=writer)
    assert writer.calls == 1

    cmd2 = build_agent_launch_command(
        executable="py-fake", frozen=False, server_url=FAKE_SERVER_URL
    )
    register_autostart(command=cmd2, startup_dir=tmp_path, writer=writer)
    assert writer.calls == 2  # 내용 변경 → 재쓰기(같은 내용이면 0 인 멱등의 반대)


def test_startup_register_default_writer_and_remover_roundtrip(tmp_path):
    """주입 writer/remover 미지정 → 기본 ``_default_startup_writer``/``_default_startup_remover``
    경로가 ``tmp_path`` 에서 실제로 동작한다(실 ``%APPDATA%`` 미사용)."""

    cmd = build_agent_launch_command(executable="py-fake", frozen=False)
    register_autostart(command=cmd, startup_dir=tmp_path)  # writer=None → 기본 writer
    assert (tmp_path / STARTUP_FILENAME).exists()
    assert is_autostart_registered(startup_dir=tmp_path) is True

    assert unregister_autostart(startup_dir=tmp_path) is True  # remover=None → 기본 remover
    assert is_autostart_registered(startup_dir=tmp_path) is False


def test_startup_cmd_quotes_paths_with_spaces_and_stays_idempotent(tmp_path):
    """공백 경로는 ``list2cmdline`` 으로 인용되고(재부팅 자동시작 안정성, Dev Notes 열린질문 #3),
    text-mode CRLF round-trip 이 일관돼 재등록 시 재쓰기 0(Debug Log 멱등 회귀 가드)."""

    writer = _FakeWriter()
    cmd = build_agent_launch_command(
        executable=r"C:\Program Files\py.exe", frozen=True
    )
    register_autostart(command=cmd, startup_dir=tmp_path, writer=writer)
    assert writer.calls == 1

    text = (tmp_path / STARTUP_FILENAME).read_text(encoding="utf-8")
    assert r'"C:\Program Files\py.exe"' in text  # 공백 경로 인용
    assert text.startswith("@echo off")

    # 같은 커맨드 재등록 → round-trip 일관 → 재쓰기 0(CRLF 멱등 회귀 가드).
    register_autostart(command=cmd, startup_dir=tmp_path, writer=writer)
    assert writer.calls == 1


# ══════════════════════════════════════════════════════════════════════════
# QA gap — 미지원 method 는 ValueError(register/unregister/is_registered 전부) (AC2)
# ══════════════════════════════════════════════════════════════════════════


def test_register_autostart_unknown_method_raises():
    with pytest.raises(ValueError):
        register_autostart(command=["py-fake", "run"], method="bogus")


def test_unregister_autostart_unknown_method_raises():
    with pytest.raises(ValueError):
        unregister_autostart(method="bogus")


def test_is_autostart_registered_unknown_method_raises():
    with pytest.raises(ValueError):
        is_autostart_registered(method="bogus")


# ══════════════════════════════════════════════════════════════════════════
# QA gap — 기본 세션 probe·기본 runner 의 Windows-gating(import-safety, 비-Windows 분기) (AC1)
# ══════════════════════════════════════════════════════════════════════════


def test_default_session_probe_non_windows_returns_true(monkeypatch):
    """``is_interactive_session()`` 무주입 → 기본 probe. 비-Windows 에선 실 ``ctypes`` 호출 없이
    ``True``(interactive)로 본다(명시 정책 — 운영 win32 에서만 실제 Session 0 차단)."""

    from rider_agent import autostart

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert autostart._default_session_probe() is True
    assert is_interactive_session() is True  # probe 미주입 → 기본 probe 경유


def test_default_runner_non_windows_raises(monkeypatch):
    """기본 ``schtasks`` runner 는 win32 에서만 실 호출 — 비-Windows 에선 raise(import-safety)."""

    from rider_agent import autostart

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        autostart._default_runner(["schtasks", "/query", "/tn", TASK_NAME])


# ══════════════════════════════════════════════════════════════════════════
# QA gap — __main__ autostart 서브커맨드 분기(not-registered·method 전달·action 필수) (AC2)
# ══════════════════════════════════════════════════════════════════════════


def test_run_autostart_status_reports_not_registered(capsys):
    from rider_agent import __main__ as agent_main

    rc = agent_main._run_autostart(["--status"], is_registered=lambda *, method: False)
    assert rc == 0
    assert "not-registered" in capsys.readouterr().out


def test_run_autostart_unregister_reports_not_registered_when_absent(capsys):
    from rider_agent import __main__ as agent_main

    rc = agent_main._run_autostart(
        ["--unregister"], unregister=lambda *, method: False
    )
    assert rc == 0
    assert "not-registered" in capsys.readouterr().out


def test_run_autostart_register_forwards_task_scheduler_method(capsys):
    from rider_agent import __main__ as agent_main

    seen: dict = {}

    def fake_register(*, command, method, **_ignored):
        seen["method"] = method
        seen["command"] = command
        return {"method": method, "target": TASK_NAME, "command": command}

    rc = agent_main._run_autostart(
        ["--register", "--method", "task_scheduler"], register=fake_register
    )
    assert rc == 0
    assert seen["method"] == "task_scheduler"  # --method 가 primitive 로 전달됨
    assert "run" in seen["command"]
    out = capsys.readouterr().out
    assert "method=task_scheduler" in out
    assert FAKE_TOKEN not in out  # secret 비노출


def test_autostart_requires_an_action_flag():
    """``--register``/``--unregister``/``--status`` 중 하나는 필수(상호배타·required)."""

    from rider_agent import __main__ as agent_main

    with pytest.raises(SystemExit):
        agent_main._run_autostart([])


# ══════════════════════════════════════════════════════════════════════════
# QA gap — run_agent 세션 게이트는 crawler-only 노드에 무관(AC3 — run_agent 통합)
# ══════════════════════════════════════════════════════════════════════════


def test_run_agent_session_gate_irrelevant_for_crawler_only(tmp_path):
    """crawler-only 노드(KAKAO_SEND 없음)는 Session 0 여도 게이트 무관 — 워커는 역할 기준으로
    미기동(게이트가 아니라 capability)이고 fail-closed 사유를 surfacing 하지 않는다."""

    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    stop.set()
    statuses: list[str] = []

    summary = run_agent(
        transport=_FakeTransport(),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_kakao_sender=True,
        capabilities=_crawler_only_caps(),
        session_probe=lambda: False,  # Session 0 이어도 crawler-only 는 게이트 대상 아님
        on_status=statuses.append,
    )

    assert summary.kakao_worker is None  # KAKAO_SEND 없음 → 워커 미기동(역할 기준)
    assert SESSION_0_SERVICE not in statuses  # crawler-only 는 fail-closed 사유 surfacing 안 함
