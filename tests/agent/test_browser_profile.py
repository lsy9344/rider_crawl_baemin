"""Story 4.5 — BrowserProfileManager(per-target 프로필/CDP 격리 + 대상 검증) 검증.

외부 호출 없음: 실제 Chrome/CDP/네트워크/socket 대기 미사용. ``prepare``/``cdp_probe``/
``run_command``/포트 할당/``sleep`` 을 모두 주입 fake + 호출 카운터로 대체해 격리·중복 거부·
건강/복구·센터 검증 매핑·heartbeat provider 를 결정적으로 검증한다. 값은 명백한 가짜값만
(``t1``/``alpha``/``9301`` …) — 실 프로필 경로/실 봇 토큰/agent token/chat_id/한국 휴대폰/
이메일·OTP 원문 없음(누출 가드).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import rider_agent.browser_profile as browser_profile
from rider_agent.browser_profile import (
    DEFAULT_MAX_RESTART_ATTEMPTS,
    ERROR_TARGET_VALIDATION_FAILURE,
    MISMATCH_CENTER_MISMATCH,
    STATE_AUTH_REQUIRED,
    STATE_CENTER_MISMATCH,
    STATE_INACTIVE,
    STATE_IN_USE,
    STATE_READY,
    STATE_UNKNOWN,
    BrowserProfileManager,
    ProfileAssignment,
    TargetValidationError,
    classify_target_risk,
    map_target_validation_failure,
)
from rider_agent.reuse import (
    BrowserActionRequiredError,
    BrowserLaunchError,
    CdpUnavailableError,
    prepare_chrome,
)
from rider_agent.secure_store import AgentIdentity
from rider_crawl.config import AppConfig, DEFAULT_BAEMIN_CENTER_NAME

# 가짜 식별자만(누출 가드 — 실제 토큰/경로/연락처 금지).
FAKE_TOKEN = "agtok-fake-browser-profile-secret"
_IDENTITY = AgentIdentity(
    agent_id="agent-fake-1",
    agent_token=FAKE_TOKEN,
    tenant_scope={"tenant": "t-fake"},
    config_version="cfg-fake-1",
)


# ── 주입 fake 들 ─────────────────────────────────────────────────────────────


def _fixed_ports(seq):
    """주입 포트 할당기: 리스트를 순서대로 돌려준다(중복 포트 강제용)."""

    values = list(seq)
    index = {"i": 0}

    def _allocate() -> int:
        port = values[index["i"]]
        index["i"] += 1
        return port

    return _allocate


class CountingPrepare:
    """성공만 하는 fake prepare — 호출 인자(config/run_command/cdp_probe)를 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, config, *, run_command=None, cdp_probe=None):
        self.calls.append((config, run_command, cdp_probe))
        return "chrome ready (fake)"


class ScriptedPrepare:
    """스크립트대로 None(성공)/예외를 돌려주는 fake prepare(건강/복구 결정 검증).

    스크립트가 소진된 뒤 또 호출되면 ``RuntimeError`` 를 던져 **무한 재시도 버그를 hang 없이
    즉시 실패**로 드러낸다.
    """

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self, config, *, run_command=None, cdp_probe=None):
        self.calls += 1
        if not self.script:
            raise RuntimeError("prepare over-called — 무한 재시도 의심")
        action = self.script.pop(0)
        if isinstance(action, BaseException):
            raise action
        return "chrome ready (fake)"


class FakeCdpProbe:
    """prepare_chrome 계약을 만족하는 fake CDP probe.

    첫 호출(``_ensure_cdp_endpoint_unused``)은 raise → 포트 미사용(good), 이후 호출
    (``_wait_for_cdp_ready``)은 성공 → 준비됨. 실제 CDP/네트워크 미접속.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, cdp_url):
        self.calls += 1
        if self.calls == 1:
            raise CdpUnavailableError("port unused (fake)")
        return None


class FakeRunCommand:
    """fake run_command — 실제 subprocess 없이 호출 인자만 기록(실 Chrome 0)."""

    def __init__(self) -> None:
        self.calls: list[tuple[list, bool]] = []

    def __call__(self, command, check):
        self.calls.append((list(command), check))
        return None


class FakeProcess:
    """Minimal process-like object for release cleanup tests."""

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.waits: list[float | None] = []

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return 0


def _debug_endpoint(cdp_url: str, cdp_port: int, process=None):
    return type(
        "Endpoint",
        (),
        {
            "cdp_url": cdp_url,
            "cdp_port": cdp_port,
            "process": process,
        },
    )()


def _windows_prepare(config, *, run_command=None, cdp_probe=None):
    """실 ``prepare_chrome`` 를 Windows 경로로 강제 호출(테스트 OS 무관, 실 Chrome 0)."""

    return prepare_chrome(
        config, platform_name="Windows", run_command=run_command, cdp_probe=cdp_probe
    )


def _build_config(
    *,
    cdp_url,
    user_data_dir,
    platform_name="baemin",
    center_name="표준배민센터X",
    coupang_eats_url="https://example.test/perf",
):
    return AppConfig(
        coupang_eats_url=coupang_eats_url,
        baemin_center_name=center_name,
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url=cdp_url,
        browser_user_data_dir=Path(user_data_dir),
        headless=False,
        kakao_chat_name="",
        log_dir=Path("logs"),
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        platform_name=platform_name,
    )


def make_build_config(
    *, platform_name="baemin", center_name="표준배민센터X", cdp_url_override=None
):
    """``ensure_profile`` 에 주입할 build_config 팩토리(대상 설정 baked-in)."""

    def _bc(*, tenant_id, target_id, cdp_url, user_data_dir):
        return _build_config(
            cdp_url=cdp_url_override or cdp_url,
            user_data_dir=user_data_dir,
            platform_name=platform_name,
            center_name=center_name,
        )

    return _bc


def _manager(tmp_path, **overrides):
    kwargs = dict(
        profiles_root=tmp_path / "profiles",
        agent_id="agent-fake-1",
        prepare=CountingPrepare(),
        allocate_port=_fixed_ports([9301]),
    )
    kwargs.update(overrides)
    return BrowserProfileManager(**kwargs)


# ══════════════════════════════════════════════════════════════════════════
# AC1 — per-target 프로필/CDP 격리 + prepare_chrome 재사용
# ══════════════════════════════════════════════════════════════════════════


def test_ensure_profile_isolates_and_reuses_prepare_chrome(tmp_path):
    probe = FakeCdpProbe()
    runner = FakeRunCommand()
    manager = BrowserProfileManager(
        profiles_root=tmp_path / "profiles",
        agent_id="agent-fake-1",
        prepare=_windows_prepare,  # 실 prepare_chrome(격리 가드 reuse)
        run_command=runner,
        cdp_probe=probe,
        allocate_port=_fixed_ports([9301]),
    )

    assignment = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    # 대상별 독립 User Data Dir + 사용 가능한 127.0.0.1:<port> + 기본 프로필 미재사용.
    assert assignment.profile_dir == tmp_path / "profiles" / "t1" / "alpha"
    assert assignment.cdp_url == "http://127.0.0.1:9301"
    assert assignment.cdp_port == 9301
    assert assignment.state == STATE_READY
    # prepare_chrome 가 주입 run_command/cdp_probe 로 호출됨(실 Chrome 0, 실 CDP 0).
    assert runner.calls, "주입 run_command 가 prepare_chrome 안에서 호출돼야 함"
    assert probe.calls >= 2, "격리 가드(unused) + 준비 대기에서 주입 cdp_probe 재사용"
    # 실행 명령에 대상별 user-data-dir 가 들어간다(기본 프로필 재사용 금지).
    command = " ".join(runner.calls[0][0])
    assert "--user-data-dir" in command
    assert "alpha" in command


def test_distinct_targets_get_distinct_profiles_and_ports(tmp_path):
    manager = _manager(tmp_path, allocate_port=_fixed_ports([9301, 9302]))

    a = manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    b = manager.ensure_profile("t1", "beta", build_config=make_build_config())

    # 서로 다른 대상은 프로필/포트를 공유하지 않는다(계정 격리).
    assert a.profile_dir != b.profile_dir
    assert a.cdp_port == 9301 and b.cdp_port == 9302
    assert {p["cdp_port"] for p in manager.browser_profiles()} == {9301, 9302}


def test_same_target_reuses_assignment_without_reallocation(tmp_path, monkeypatch):
    prepare = CountingPrepare()
    # 둘째 포트(9999)는 재할당이 일어나면 쓰일 값 — 재사용이면 절대 쓰이지 않는다.
    manager = _manager(tmp_path, prepare=prepare, allocate_port=_fixed_ports([9301, 9999]))
    endpoints = iter([None, _debug_endpoint("http://127.0.0.1:9301", 9301)])

    monkeypatch.setattr(
        browser_profile,
        "find_existing_chrome_debug_endpoint",
        lambda _profile_dir, *, cdp_probe=None: next(endpoints),
    )

    a = manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    a2 = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert a is a2
    assert a.cdp_port == 9301  # 9999 미사용 — 재배정 없음(idempotent)
    assert len(prepare.calls) == 1  # 두번째 호출은 prepare 재실행 안 함


def test_same_target_relaunches_when_registered_cdp_is_not_live(tmp_path, monkeypatch):
    """READY registry alone is not enough; stale CDP must be relaunched before auth/crawl."""

    prepare_calls: list[str] = []
    first_process = FakeProcess()
    second_process = FakeProcess()
    processes = [first_process, second_process]
    lookup_calls: list[Path] = []

    def no_live_endpoint(profile_dir, *, cdp_probe=None):
        del cdp_probe
        lookup_calls.append(profile_dir)
        return None

    def prepare(config, *, run_command=None, cdp_probe=None):
        del cdp_probe
        prepare_calls.append(str(config.cdp_url))
        assert run_command is not None
        run_command(["chrome-fake"], False)

    monkeypatch.setattr(
        browser_profile,
        "find_existing_chrome_debug_endpoint",
        no_live_endpoint,
    )
    manager = _manager(
        tmp_path,
        prepare=prepare,
        run_command=lambda command, check: processes.pop(0),
        allocate_port=_fixed_ports([9301, 9302]),
    )

    first = manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    second = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert first is not second
    assert first.cdp_port == 9301
    assert second.cdp_port == 9302
    assert prepare_calls == ["http://127.0.0.1:9301", "http://127.0.0.1:9302"]
    assert first_process.terminated is True
    assert lookup_calls == [tmp_path / "profiles" / "t1" / "alpha"] * 3


def test_same_target_relaunches_stale_cdp_without_tracked_process(
    tmp_path, monkeypatch
):
    """A READY registry entry without a process handle still must prove live CDP."""

    prepare_calls: list[str] = []
    lookup_calls: list[Path] = []

    def no_live_endpoint(profile_dir, *, cdp_probe=None):
        del cdp_probe
        lookup_calls.append(profile_dir)
        return None

    def prepare(config, *, run_command=None, cdp_probe=None):
        del run_command, cdp_probe
        prepare_calls.append(str(config.cdp_url))

    monkeypatch.setattr(
        browser_profile,
        "find_existing_chrome_debug_endpoint",
        no_live_endpoint,
    )
    manager = _manager(
        tmp_path,
        prepare=prepare,
        allocate_port=_fixed_ports([9301, 9302]),
    )

    first = manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    second = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert first is not second
    assert first.cdp_port == 9301
    assert second.cdp_port == 9302
    assert prepare_calls == ["http://127.0.0.1:9301", "http://127.0.0.1:9302"]
    assert lookup_calls == [tmp_path / "profiles" / "t1" / "alpha"] * 3


def test_same_target_relaunches_when_tracked_browser_process_exited(tmp_path):
    prepare_calls: list[str] = []
    first = FakeProcess()
    second = FakeProcess()
    processes = [first, second]

    def prepare(config, *, run_command=None, cdp_probe=None):
        prepare_calls.append(str(config.cdp_url))
        assert run_command is not None
        run_command(["chrome-fake"], False)

    manager = _manager(
        tmp_path,
        prepare=prepare,
        run_command=lambda command, check: processes.pop(0),
        allocate_port=_fixed_ports([9301, 9302]),
    )

    first_assignment = manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    first.poll = lambda: 0
    second_assignment = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert first_assignment is not second_assignment
    assert first_assignment.cdp_port == 9301
    assert second_assignment.cdp_port == 9302
    assert prepare_calls == ["http://127.0.0.1:9301", "http://127.0.0.1:9302"]
    assert manager.browser_profiles() == [
        {
            "id": "t1:alpha",
            "target_id": "alpha",
            "agent_id": "agent-fake-1",
            "cdp_port": 9302,
            "state": STATE_READY,
        }
    ]


def test_agent_restart_adopts_existing_profile_chrome_with_live_cdp(
    tmp_path, monkeypatch
):
    prepare = CountingPrepare()
    adopted_process = FakeProcess()

    def fail_if_allocated():
        raise AssertionError("existing Chrome CDP should be adopted before port allocation")

    def find_existing(profile_dir, *, cdp_probe=None):
        assert profile_dir == tmp_path / "profiles" / "t1" / "alpha"
        if cdp_probe is not None:
            cdp_probe("http://127.0.0.1:9555")
        return type(
            "Endpoint",
            (),
            {
                "cdp_url": "http://127.0.0.1:9555",
                "cdp_port": 9555,
                "process": adopted_process,
            },
        )()

    probe_calls = []
    monkeypatch.setattr(
        browser_profile,
        "find_existing_chrome_debug_endpoint",
        find_existing,
        raising=False,
    )
    manager = _manager(
        tmp_path,
        prepare=prepare,
        allocate_port=fail_if_allocated,
        cdp_probe=lambda cdp_url: probe_calls.append(cdp_url),
    )

    assignment = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert assignment.cdp_url == "http://127.0.0.1:9555"
    assert assignment.cdp_port == 9555
    assert assignment.state == STATE_READY
    assert prepare.calls == []
    assert probe_calls == ["http://127.0.0.1:9555"]
    assert manager.browser_profiles() == [
        {
            "id": "t1:alpha",
            "target_id": "alpha",
            "agent_id": "agent-fake-1",
            "cdp_port": 9555,
            "state": STATE_READY,
        }
    ]

    manager.release("t1", "alpha")
    assert adopted_process.terminated is True


def test_agent_restart_closes_stale_profile_chrome_without_live_cdp_before_launch(
    tmp_path, monkeypatch
):
    calls = []

    monkeypatch.setattr(
        browser_profile,
        "find_existing_chrome_debug_endpoint",
        lambda _profile_dir, *, cdp_probe=None: None,
        raising=False,
    )

    def close_stale(profile_dir, *, cdp_probe=None):
        calls.append(("close_stale", profile_dir))
        return 1

    monkeypatch.setattr(
        browser_profile,
        "close_stale_chrome_processes_for_profile",
        close_stale,
        raising=False,
    )

    def allocate_port():
        calls.append(("allocate_port", None))
        return 9301

    def prepare(config, *, run_command=None, cdp_probe=None):
        calls.append(("prepare", config.cdp_url))

    manager = _manager(
        tmp_path,
        allocate_port=allocate_port,
        prepare=prepare,
    )

    assignment = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert assignment.cdp_url == "http://127.0.0.1:9301"
    assert calls == [
        ("close_stale", tmp_path / "profiles" / "t1" / "alpha"),
        ("allocate_port", None),
        ("prepare", "http://127.0.0.1:9301"),
    ]


def test_reuse_updates_last_used_and_idle_cleanup_releases_indexes(tmp_path, monkeypatch):
    times = iter([100.0, 200.0, 500.0, 501.0])
    manager = _manager(
        tmp_path,
        allocate_port=_fixed_ports([9301, 9301]),
        now=lambda: next(times),
    )
    endpoints = iter([None, _debug_endpoint("http://127.0.0.1:9301", 9301), None])

    monkeypatch.setattr(
        browser_profile,
        "find_existing_chrome_debug_endpoint",
        lambda _profile_dir, *, cdp_probe=None: next(endpoints),
    )

    a = manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    a2 = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert a is a2
    assert a2.last_used_at == 200.0

    released = manager.cleanup_idle_profiles(max_idle_seconds=299)

    assert released == ["t1:alpha"]
    assert manager.browser_profiles() == []
    b = manager.ensure_profile("t1", "beta", build_config=make_build_config())
    assert b.cdp_port == 9301
    assert {p["id"] for p in manager.browser_profiles()} == {"t1:beta"}


def test_max_profiles_releases_least_recent_assignment(tmp_path):
    times = iter([100.0, 200.0, 201.0])
    manager = _manager(
        tmp_path,
        allocate_port=_fixed_ports([9301, 9302]),
        now=lambda: next(times),
        max_profiles=1,
    )

    manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    beta = manager.ensure_profile("t1", "beta", build_config=make_build_config())

    assert beta.cdp_port == 9302
    assert {p["id"] for p in manager.browser_profiles()} == {"t1:beta"}


def test_concurrent_same_target_launch_uses_single_reservation(tmp_path, monkeypatch):
    entered_prepare = threading.Event()
    allow_prepare = threading.Event()
    second_prepare_started = threading.Event()
    lock = threading.Lock()
    prepare_calls = 0
    results: list[ProfileAssignment] = []
    errors: list[BaseException] = []

    def prepare(config, *, run_command=None, cdp_probe=None):
        nonlocal prepare_calls
        with lock:
            prepare_calls += 1
            if prepare_calls > 1:
                second_prepare_started.set()
        entered_prepare.set()
        allow_prepare.wait(timeout=1)

    manager = _manager(
        tmp_path,
        prepare=prepare,
        allocate_port=_fixed_ports([9301, 9302]),
    )
    endpoints = iter([None, _debug_endpoint("http://127.0.0.1:9301", 9301)])

    monkeypatch.setattr(
        browser_profile,
        "find_existing_chrome_debug_endpoint",
        lambda _profile_dir, *, cdp_probe=None: next(endpoints),
    )

    def ensure():
        try:
            results.append(
                manager.ensure_profile("t1", "alpha", build_config=make_build_config())
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below for thread assertions.
            errors.append(exc)

    first = threading.Thread(target=ensure)
    second = threading.Thread(target=ensure)
    first.start()
    assert entered_prepare.wait(timeout=1)
    second.start()

    assert not second_prepare_started.wait(timeout=0.2)
    allow_prepare.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert errors == []
    assert prepare_calls == 1
    assert len(results) == 2
    assert results[0] is results[1]
    assert {p["id"] for p in manager.browser_profiles()} == {"t1:alpha"}


def test_concurrent_launch_counts_reservations_against_max_profiles(tmp_path):
    entered_prepare = threading.Event()
    allow_prepare = threading.Event()
    prepare_calls = 0
    errors: list[BaseException] = []

    def prepare(config, *, run_command=None, cdp_probe=None):
        nonlocal prepare_calls
        prepare_calls += 1
        entered_prepare.set()
        allow_prepare.wait(timeout=1)

    manager = _manager(
        tmp_path,
        prepare=prepare,
        allocate_port=_fixed_ports([9301, 9302]),
        max_profiles=1,
    )

    def ensure_first():
        try:
            manager.ensure_profile("t1", "alpha", build_config=make_build_config())
        except BaseException as exc:  # noqa: BLE001 - surfaced below for thread assertions.
            errors.append(exc)

    first = threading.Thread(target=ensure_first)
    first.start()
    assert entered_prepare.wait(timeout=1)

    with pytest.raises(BrowserLaunchError):
        manager.ensure_profile("t1", "beta", build_config=make_build_config())

    allow_prepare.set()
    first.join(timeout=1)

    assert prepare_calls == 1
    assert errors == []
    assert {p["id"] for p in manager.browser_profiles()} == {"t1:alpha"}


# ══════════════════════════════════════════════════════════════════════════
# AC1.2 / AC1.3 / AC3 — 중복 거부(fail-closed) + 약화 금지
# ══════════════════════════════════════════════════════════════════════════


def test_duplicate_port_to_other_target_is_rejected(tmp_path):
    # 두 대상에 같은 포트를 강제 → 둘째는 시작하지 않는다.
    manager = _manager(tmp_path, allocate_port=_fixed_ports([9301, 9301]))
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    with pytest.raises(BrowserLaunchError):
        manager.ensure_profile("t1", "beta", build_config=make_build_config())

    # beta 는 등록되지 않는다(작업 미시작).
    assert {p["id"] for p in manager.browser_profiles()} == {"t1:alpha"}


def test_prepare_chrome_launch_error_is_surfaced_and_not_started(tmp_path):
    # prepare_chrome 가 던지는 BrowserLaunchError(CDP 사용중/프로필 점유)를 흡수해 시작 안 함.
    def _raise(config, *, run_command=None, cdp_probe=None):
        raise BrowserLaunchError("CDP 주소가 이미 사용 중입니다.")

    manager = _manager(tmp_path, prepare=_raise)

    with pytest.raises(BrowserLaunchError):
        manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    assert manager.browser_profiles() == []  # 미등록


def test_remote_cdp_url_is_rejected(tmp_path):
    # build_config 가 원격 cdp_url 을 돌려주면 ensure_local_cdp_address 위반으로 거부.
    manager = _manager(tmp_path)
    remote_bc = make_build_config(cdp_url_override="http://10.0.0.5:9301")

    with pytest.raises(BrowserLaunchError):
        manager.ensure_profile("t1", "alpha", build_config=remote_bc)
    assert manager.browser_profiles() == []


def test_duplicate_guard_holds_across_many_targets(tmp_path):
    # 대상 N개 추가 후에도 중복 가드가 유지(약화 금지) — 마지막 충돌 포트는 거부된다.
    ports = _fixed_ports([9301, 9302, 9303, 9304, 9305, 9301])
    manager = _manager(tmp_path, allocate_port=ports)

    for i in range(5):
        manager.ensure_profile("t1", f"tg{i}", build_config=make_build_config())
    assert len({p["cdp_port"] for p in manager.browser_profiles()}) == 5

    # 여섯 번째 대상이 기존 포트(9301)를 재발급받으면 거부된다(중복 가드 유지).
    with pytest.raises(BrowserLaunchError):
        manager.ensure_profile("t1", "tg5", build_config=make_build_config())


def test_release_reclaims_port_and_profile(tmp_path):
    manager = _manager(tmp_path, allocate_port=_fixed_ports([9301, 9301]))
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    manager.release("t1", "alpha")

    # 회수 뒤에는 같은 포트를 다른 대상에 재할당할 수 있다(누수 없음).
    b = manager.ensure_profile("t1", "beta", build_config=make_build_config())
    assert b.cdp_port == 9301
    assert {p["id"] for p in manager.browser_profiles()} == {"t1:beta"}


def test_release_terminates_tracked_browser_process(tmp_path):
    process = FakeProcess()

    def prepare(config, *, run_command=None, cdp_probe=None):
        assert run_command is not None
        run_command(["chrome-fake"], False)

    manager = _manager(
        tmp_path,
        prepare=prepare,
        run_command=lambda command, check: process,
    )

    manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    manager.release("t1", "alpha")

    assert process.terminated is True
    assert process.killed is False
    assert process.waits == [5.0]


def test_close_all_releases_assignments_and_terminates_tracked_processes(tmp_path):
    all_processes = [FakeProcess(), FakeProcess(), FakeProcess()]
    pending_processes = list(all_processes)

    def prepare(config, *, run_command=None, cdp_probe=None):
        assert run_command is not None
        run_command(["chrome-fake"], False)

    def run_command(command, check):
        return pending_processes.pop(0)

    manager = _manager(
        tmp_path,
        prepare=prepare,
        run_command=run_command,
        allocate_port=_fixed_ports([9301, 9302, 9301]),
    )
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    manager.ensure_profile("t1", "beta", build_config=make_build_config())

    manager.close_all()

    assert manager.browser_profiles() == []
    assert [process.terminated for process in all_processes[:2]] == [True, True]
    gamma = manager.ensure_profile("t1", "gamma", build_config=make_build_config())
    assert gamma.cdp_port == 9301


# ══════════════════════════════════════════════════════════════════════════
# AC2 — 기대 센터/상점명 검증 + CENTER_MISMATCH 미생성 + target_validation_failure
# ══════════════════════════════════════════════════════════════════════════


def test_classify_target_risk_reuses_config_classifier():
    from rider_crawl.config import coupang_center_name_risk

    # config 의 분류기를 그대로 재사용(재구현 아님).
    assert classify_target_risk("coupang", "") == coupang_center_name_risk("coupang", "")
    assert classify_target_risk("coupang", "")[0] is True
    assert classify_target_risk("coupang", DEFAULT_BAEMIN_CENTER_NAME)[0] is True
    assert classify_target_risk("baemin", "")[0] is False


def test_empty_coupang_center_blocks_target_and_does_not_start(tmp_path):
    prepare = CountingPrepare()
    manager = _manager(tmp_path, prepare=prepare)
    risky_bc = make_build_config(platform_name="coupang", center_name="")

    with pytest.raises(TargetValidationError) as excinfo:
        manager.ensure_profile("t1", "alpha", build_config=risky_bc)

    assert excinfo.value.error_code == ERROR_TARGET_VALIDATION_FAILURE
    assert manager.browser_profiles() == []  # 진행 안 함
    assert prepare.calls == []  # 검증이 먼저 — Chrome 준비도 안 함


def test_baemin_default_coupang_center_is_risky(tmp_path):
    manager = _manager(tmp_path)
    risky_bc = make_build_config(
        platform_name="coupang", center_name=DEFAULT_BAEMIN_CENTER_NAME
    )
    with pytest.raises(TargetValidationError):
        manager.ensure_profile("t1", "alpha", build_config=risky_bc)


def test_map_center_mismatch_to_target_validation_failure():
    # 쿠팡 센터 exact-match 검증이 던지는 RuntimeError(이미 fail-closed)를 흡수·매핑.
    exc = RuntimeError(
        "쿠팡 센터 검증 실패: 설정한 센터와 화면에서 확인된 센터가 다릅니다.\n"
        "설정 센터명: 기대센터A\n"
        "화면 센터명: 다른계정센터XYZ"
    )

    mapped = map_target_validation_failure(exc)

    assert mapped["error_code"] == ERROR_TARGET_VALIDATION_FAILURE
    assert mapped["mismatch"] == MISMATCH_CENTER_MISMATCH == "CENTER_MISMATCH"
    assert mapped["state"] == STATE_CENTER_MISMATCH
    # raw 화면/설정 센터명(운영 식별자)은 사유에 노출되지 않는다(헤드라인만 surfacing).
    assert "다른계정센터XYZ" not in mapped["reason"]
    assert "기대센터A" not in mapped["reason"]


def test_validation_reason_redacts_secrets_and_contacts():
    exc = RuntimeError(
        "대상 검증 실패: token=supersecretvalue 010-1234-5678 user@example.com"
    )
    mapped = map_target_validation_failure(exc)

    assert "supersecretvalue" not in mapped["reason"]
    assert "010-1234-5678" not in mapped["reason"]
    assert "user@example.com" not in mapped["reason"]


def test_target_validation_error_reason_is_redacted():
    err = TargetValidationError("센터 위험: token=abc123secretvalue")
    assert "abc123secretvalue" not in err.reason
    assert err.error_code == ERROR_TARGET_VALIDATION_FAILURE


# ══════════════════════════════════════════════════════════════════════════
# AC3 — 건강 점검 / 복구(재시작 bounded + AUTH_REQUIRED 전이, 무한 재시도 금지)
# ══════════════════════════════════════════════════════════════════════════


def test_recover_restarts_on_cdp_unavailable_then_ready(tmp_path):
    sleeps: list[float] = []
    # ensure(성공) → recover: CdpUnavailable(1회) → 성공.
    prepare = ScriptedPrepare([None, CdpUnavailableError("no cdp"), None])
    manager = _manager(tmp_path, prepare=prepare, sleep=sleeps.append)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    recovered = manager.recover_profile(
        "t1", "alpha", build_config=make_build_config()
    )

    assert recovered.state == STATE_READY
    assert prepare.calls == 3  # ensure 1 + 재시작 2(실패→성공)
    assert sleeps == [1.0]  # 첫 실패 후 backoff 1회(주입 sleep)


def test_recover_profile_tracks_restarted_browser_process(tmp_path):
    first = FakeProcess()
    recovered = FakeProcess()
    pending = [first, recovered]

    def prepare(config, *, run_command=None, cdp_probe=None):
        assert run_command is not None
        run_command(["chrome-fake"], False)

    manager = _manager(
        tmp_path,
        prepare=prepare,
        run_command=lambda command, check: pending.pop(0),
    )
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    manager.recover_profile("t1", "alpha", build_config=make_build_config())
    manager.close_all()

    assert first.terminated is True
    assert recovered.terminated is True


def test_recover_transitions_to_auth_required_without_infinite_retry(tmp_path):
    sleeps: list[float] = []
    # ensure(성공) → recover: 로그인 필요(BrowserActionRequiredError).
    prepare = ScriptedPrepare([None, BrowserActionRequiredError("login needed")])
    manager = _manager(tmp_path, prepare=prepare, sleep=sleeps.append)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    recovered = manager.recover_profile(
        "t1", "alpha", build_config=make_build_config()
    )

    assert recovered.state == STATE_AUTH_REQUIRED
    assert prepare.calls == 2  # ensure 1 + recover 1(재시도 안 함)
    assert sleeps == []  # 로그인 필요는 backoff/재시도하지 않는다


def test_recover_is_bounded_and_does_not_retry_forever(tmp_path):
    sleeps: list[float] = []
    # ensure(성공) → recover: CdpUnavailable 가 한도(3)만큼 반복.
    script = [None] + [CdpUnavailableError("no cdp")] * DEFAULT_MAX_RESTART_ATTEMPTS
    prepare = ScriptedPrepare(script)
    manager = _manager(tmp_path, prepare=prepare, sleep=sleeps.append)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    with pytest.raises(CdpUnavailableError):
        manager.recover_profile("t1", "alpha", build_config=make_build_config())

    # ensure 1 + 정확히 max_attempts 회 재시작(무한 아님), backoff 는 그 사이 2회.
    assert prepare.calls == 1 + DEFAULT_MAX_RESTART_ATTEMPTS
    assert sleeps == [1.0, 2.0]


def test_check_health_reports_ready_or_unknown(tmp_path):
    state = {"down": False}

    def probe(cdp_url):
        if state["down"]:
            raise CdpUnavailableError("down")
        return None

    manager = _manager(tmp_path, cdp_probe=probe)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert manager.check_health("t1", "alpha") == STATE_READY
    state["down"] = True
    assert manager.check_health("t1", "alpha") == STATE_UNKNOWN
    # 미등록 대상은 UNKNOWN.
    assert manager.check_health("t1", "ghost") == STATE_UNKNOWN


# ══════════════════════════════════════════════════════════════════════════
# AC4 — heartbeat browser_profiles provider + raw 경로/secret 비노출 + 배선
# ══════════════════════════════════════════════════════════════════════════


def test_browser_profiles_projection_excludes_raw_path(tmp_path):
    manager = _manager(tmp_path)
    assignment = manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    profiles = manager.browser_profiles()

    assert profiles == [
        {
            "id": "t1:alpha",
            "target_id": "alpha",
            "agent_id": "agent-fake-1",
            "cdp_port": 9301,
            "state": STATE_READY,
        }
    ]
    # raw profile_dir/secret 미포함(server stores id/ref, not raw path).
    blob = json.dumps(profiles, ensure_ascii=False)
    assert "profile_dir" not in blob
    assert str(assignment.profile_dir) not in blob


def test_browser_slots_inventory_counts_roots_without_raw_process_details(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, max_profiles=2)
    profiles_root = tmp_path / "profiles"
    live_profile = profiles_root / "t1" / "alpha"
    wrong_profile = tmp_path / "other" / "profile"

    class FakeProcess:
        info = {"name": "chrome.exe"}

        def __init__(self, cmdline, *, parent=None):
            self._cmdline = cmdline
            self._parent = parent

        def cmdline(self):
            return list(self._cmdline)

        def parent(self):
            return self._parent

    root = FakeProcess(
        [
            "chrome.exe",
            "--remote-debugging-port=9555",
            f"--user-data-dir={live_profile}",
        ],
        parent=None,
    )
    child_with_port = FakeProcess(
        [
            "chrome.exe",
            "--type=renderer",
            "--remote-debugging-port=9555",
            f"--user-data-dir={live_profile}",
        ],
        parent=root,
    )
    wrong_root = FakeProcess(
        [
            "chrome.exe",
            "--remote-debugging-port=9666",
            f"--user-data-dir={wrong_profile}",
        ],
        parent=None,
    )

    class FakePsutil:
        NoSuchProcess = RuntimeError
        AccessDenied = PermissionError
        ZombieProcess = RuntimeError

        @staticmethod
        def process_iter(_attrs):
            return [child_with_port, root, wrong_root]

        @staticmethod
        def virtual_memory():
            return type("Memory", (), {"percent": 72.5})()

    monkeypatch.setitem(__import__("sys").modules, "psutil", FakePsutil)

    slots = manager.browser_slots()

    assert slots == {
        "max": 2,
        "used": 1,
        "available": 1,
        "manual_auth_used": 0,
        "orphan_count": 1,
        "registry_profiles": 0,
        "ram_used_percent": 72.5,
    }
    blob = json.dumps(slots, ensure_ascii=False)
    assert str(live_profile) not in blob
    assert "remote-debugging-port" not in blob
    assert "renderer" not in blob


def test_browser_slots_reports_zero_max_when_profile_limit_is_unbounded(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, max_profiles=None)
    live_profile = tmp_path / "profiles" / "t1" / "alpha"

    class FakeProcess:
        info = {"name": "chrome.exe"}

        def cmdline(self):
            return [
                "chrome.exe",
                "--remote-debugging-port=9555",
                f"--user-data-dir={live_profile}",
            ]

        def parent(self):
            return None

    class FakePsutil:
        NoSuchProcess = RuntimeError
        AccessDenied = PermissionError
        ZombieProcess = RuntimeError

        @staticmethod
        def process_iter(_attrs):
            return [FakeProcess()]

        @staticmethod
        def virtual_memory():
            return type("Memory", (), {"percent": 61.0})()

    monkeypatch.setitem(__import__("sys").modules, "psutil", FakePsutil)

    slots = manager.browser_slots()

    assert slots == {
        "max": 0,
        "used": 1,
        "available": 0,
        "manual_auth_used": 0,
        "orphan_count": 1,
        "registry_profiles": 0,
        "ram_used_percent": 61.0,
    }


def test_record_profile_diagnostic_noop_when_profile_absent(tmp_path):
    # registry 에 row 가 없으면 placeholder 를 만들지 않고 조용히 반환한다.
    manager = _manager(tmp_path)

    manager.record_profile_diagnostic(
        "t1", "missing", auth_state="UNKNOWN", last_error_code="CDP_UNREACHABLE"
    )

    assert manager.browser_profiles() == []


def test_record_profile_diagnostic_updates_projection(tmp_path):
    # frozen dataclass 라 replace 로 새 assignment 를 저장하고, projection 에 진단 필드가 뜬다.
    manager = _manager(tmp_path)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    manager.record_profile_diagnostic(
        "t1",
        "alpha",
        auth_state="AUTH_REQUIRED",
        last_error_code="AUTH_REQUIRED",
        last_probe_at="2026-06-28T10:20:30Z",
    )

    profiles = manager.browser_profiles()
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["auth_state"] == "AUTH_REQUIRED"
    assert profile["last_error_code"] == "AUTH_REQUIRED"
    assert profile["last_probe_at"] == "2026-06-28T10:20:30Z"
    # 기존 필드는 유지된다.
    assert profile["id"] == "t1:alpha"
    assert profile["cdp_port"] == 9301
    # raw profile_dir/secret/URL/HTML/screenshot/clipboard 는 projection 에 없다.
    blob = json.dumps(profiles, ensure_ascii=False)
    for forbidden in ("profile_dir", "current_url", "html", "screenshot", "clipboard"):
        assert forbidden not in blob


def test_record_profile_diagnostic_partial_update_preserves_existing(tmp_path):
    # None 인자는 기존 값을 덮어쓰지 않는다(부분 갱신).
    manager = _manager(tmp_path)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    manager.record_profile_diagnostic("t1", "alpha", auth_state="ACTIVE")

    manager.record_profile_diagnostic("t1", "alpha", last_error_code="CRAWL_TIMEOUT")

    profile = manager.browser_profiles()[0]
    assert profile["auth_state"] == "ACTIVE"  # 유지
    assert profile["last_error_code"] == "CRAWL_TIMEOUT"  # 추가


def test_record_profile_diagnostic_can_clear_last_error_code(tmp_path):
    manager = _manager(tmp_path)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())
    manager.record_profile_diagnostic("t1", "alpha", last_error_code="AUTH_REQUIRED")

    manager.record_profile_diagnostic("t1", "alpha", clear_last_error_code=True)

    profile = manager.browser_profiles()[0]
    assert "last_error_code" not in profile


class _FakeTransport:
    """최소 fake transport — post_json 은 빈 응답(실 네트워크 0)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def post_json(self, url, body, *, headers=None) -> dict:
        self.calls.append((url, body, headers))
        return {}


def test_build_components_wires_browser_profiles_into_reporter(tmp_path):
    from rider_agent.heartbeat import build_heartbeat_payload
    from rider_agent.job_loop import build_agent_components

    manager = _manager(tmp_path)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    runner, reporter = build_agent_components(
        _IDENTITY,
        transport=_FakeTransport(),
        browser_profiles_provider=manager.browser_profiles,
    )

    # reporter 의 provider 가 manager 현재 프로필을 반영(=4.4 active_jobs 배선과 동형).
    assert reporter._browser_profiles_provider() == manager.browser_profiles()
    # heartbeat payload 의 browser_profiles 가 실제로 채워진다.
    payload = build_heartbeat_payload(
        _IDENTITY, browser_profiles_provider=reporter._browser_profiles_provider
    )
    assert payload["browser_profiles"] == manager.browser_profiles()
    # raw 경로가 payload 에도 새지 않는다.
    assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)


def test_run_agent_threads_browser_profiles_provider(tmp_path):
    import threading

    from rider_agent.job_loop import run_agent
    from rider_agent.secure_store import save_agent_identity

    class _FakeStore:
        def __init__(self):
            self._data: dict[str, str] = {}

        def put(self, value, *, ref=""):
            self._data[ref] = value
            return ref

        def resolve(self, ref):
            return self._data.get(ref)

    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    manager = _manager(tmp_path)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    stop = threading.Event()
    stop.set()  # 루프에 진입하자마자 즉시 정지(결정적).

    summary = run_agent(
        transport=_FakeTransport(),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        browser_profiles_provider=manager.browser_profiles,
    )

    assert summary.started is True
    assert summary.reporter is not None
    assert summary.reporter._browser_profiles_provider() == manager.browser_profiles()


# ══════════════════════════════════════════════════════════════════════════
# 상태/오류 어휘 — rider_server enum 값과 정합(직접 import 는 rider_agent 코드에선 금지,
# 테스트에선 값 정합 확인용으로만 import — agent 가드는 src/rider_agent/* 만 검사).
# ══════════════════════════════════════════════════════════════════════════


def test_state_and_error_constants_match_server_domain_values():
    from rider_server.domain.states import (
        BaeminAuthState,
        BrowserProfileState,
        FailureCategory,
    )

    assert STATE_UNKNOWN == BrowserProfileState.UNKNOWN.value
    assert STATE_READY == BrowserProfileState.READY.value
    assert STATE_IN_USE == BrowserProfileState.IN_USE.value
    assert STATE_INACTIVE == BrowserProfileState.INACTIVE.value
    assert STATE_AUTH_REQUIRED == BaeminAuthState.AUTH_REQUIRED.value
    assert STATE_CENTER_MISMATCH == BaeminAuthState.CENTER_MISMATCH.value
    assert ERROR_TARGET_VALIDATION_FAILURE == FailureCategory.TARGET_VALIDATION_FAILURE.value


def test_profile_assignment_is_frozen():
    assignment = ProfileAssignment(
        id="t1:alpha",
        tenant_id="t1",
        target_id="alpha",
        agent_id="agent-fake-1",
        profile_dir=Path("/tmp/fake/profiles/t1/alpha"),
        cdp_port=9301,
        cdp_url="http://127.0.0.1:9301",
    )
    assert assignment.state == STATE_UNKNOWN
    with pytest.raises(Exception):
        assignment.state = STATE_READY  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# QA 보강 — 커버리지 갭(미검증 fail-closed 분기 · 기본값 · 투영 상태 반영)
# ══════════════════════════════════════════════════════════════════════════


class _SharedDirManager(BrowserProfileManager):
    """모든 대상을 같은 프로필 경로로 강제해 **프로필-키 중복 거부 분기**를 검증한다.

    공개 경로 정책(`profiles/<tenant>/<target>/`)에서는 서로 다른 대상이 절대 같은
    프로필 경로를 갖지 않으므로, 그 fail-closed 분기를 직접 단위 검증하려면 경로를 고정한다.
    """

    def _profile_dir_for(self, tenant_id, target_id):
        return self._profiles_root / "shared"


def test_duplicate_profile_key_to_other_target_is_rejected(tmp_path):
    # 포트는 서로 다르지만(포트검사 통과) 프로필-키가 같으면 둘째 대상은 시작하지 않는다.
    manager = _SharedDirManager(
        profiles_root=tmp_path / "profiles",
        agent_id="agent-fake-1",
        prepare=CountingPrepare(),
        allocate_port=_fixed_ports([9301, 9302]),
    )
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    with pytest.raises(BrowserLaunchError):
        manager.ensure_profile("t1", "beta", build_config=make_build_config())

    # beta 는 등록되지 않는다(프로필 중복 → 작업 미시작, fail-closed).
    assert {p["id"] for p in manager.browser_profiles()} == {"t1:alpha"}


def test_recover_unregistered_target_raises(tmp_path):
    # 등록부에 없는 대상 복구 요청은 BrowserLaunchError(조용히 새 프로필 만들지 않음).
    manager = _manager(tmp_path)
    with pytest.raises(BrowserLaunchError):
        manager.recover_profile("t1", "ghost", build_config=make_build_config())


def test_release_unknown_target_is_noop(tmp_path):
    # 미등록 대상 release 는 예외 없이 무시(idempotent) — 등록부를 흔들지 않는다.
    manager = _manager(tmp_path)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    manager.release("t1", "ghost")  # no-op

    assert {p["id"] for p in manager.browser_profiles()} == {"t1:alpha"}


def test_check_health_without_probe_returns_current_state(tmp_path):
    # cdp_probe 미주입이면 현재 상태를 그대로 반환(주입 없는 결정적 경로).
    manager = _manager(tmp_path)  # cdp_probe 미주입
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    assert manager.check_health("t1", "alpha") == STATE_READY
    assert manager.check_health("t1", "ghost") == STATE_UNKNOWN  # 미등록은 UNKNOWN


def test_map_empty_exception_uses_default_headline():
    from rider_crawl.redaction import redact

    # 빈 예외 본문이면 기본 헤드라인을 surfacing 한다(raw 본문 없음).
    mapped = map_target_validation_failure(RuntimeError(""))

    assert mapped["reason"] == redact("대상 검증 실패")
    assert mapped["error_code"] == ERROR_TARGET_VALIDATION_FAILURE
    assert mapped["mismatch"] == MISMATCH_CENTER_MISMATCH
    assert mapped["state"] == STATE_CENTER_MISMATCH


def test_map_center_unconfirmed_runtimeerror_is_mapped(tmp_path):
    # 화면에서 센터명을 확인하지 못한 경우(센터 미확인)도 CENTER_MISMATCH 로 매핑된다(AC2.6).
    exc = RuntimeError(
        "쿠팡 센터 검증 실패: 화면에서 센터명을 확인하지 못했습니다.\n설정 센터명: 기대센터A"
    )

    mapped = map_target_validation_failure(exc)

    assert mapped["error_code"] == ERROR_TARGET_VALIDATION_FAILURE
    assert mapped["mismatch"] == MISMATCH_CENTER_MISMATCH
    assert mapped["state"] == STATE_CENTER_MISMATCH
    # 설정 센터명(운영 식별자)은 사유에 노출되지 않는다(헤드라인만).
    assert "기대센터A" not in mapped["reason"]


def test_recover_exhaustion_sets_state_unknown_in_projection(tmp_path):
    # 재시작 한도 소진 후 등록부 상태가 UNKNOWN 으로 투영돼 heartbeat 에 반영된다.
    script = [None] + [CdpUnavailableError("no cdp")] * DEFAULT_MAX_RESTART_ATTEMPTS
    manager = _manager(tmp_path, prepare=ScriptedPrepare(script), sleep=lambda _s: None)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    with pytest.raises(CdpUnavailableError):
        manager.recover_profile("t1", "alpha", build_config=make_build_config())

    states = {p["id"]: p["state"] for p in manager.browser_profiles()}
    assert states["t1:alpha"] == STATE_UNKNOWN


def test_recover_auth_required_is_reflected_in_browser_profiles(tmp_path):
    # AUTH_REQUIRED 전이가 heartbeat 투영에 반영돼 운영(Epic 5)이 조치 필요를 본다.
    prepare = ScriptedPrepare([None, BrowserActionRequiredError("login needed")])
    manager = _manager(tmp_path, prepare=prepare, sleep=lambda _s: None)
    manager.ensure_profile("t1", "alpha", build_config=make_build_config())

    manager.recover_profile("t1", "alpha", build_config=make_build_config())

    states = {p["id"]: p["state"] for p in manager.browser_profiles()}
    assert states["t1:alpha"] == STATE_AUTH_REQUIRED


def test_allocate_local_port_returns_valid_local_port():
    # 기본 포트 할당기(stdlib socket)는 유효한 로컬 포트 번호를 돌려준다(새 의존 0).
    from rider_agent.browser_profile import _allocate_local_port

    port = _allocate_local_port()

    assert isinstance(port, int)
    assert 1 <= port <= 65535
