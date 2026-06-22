"""Story 4.8 — 배민 auth 상태 분류기 + AUTH_CHECK/OPEN_AUTH_BROWSER 실행자 + bounded
재인증 대기 + build_auth_execute_job 라우터 검증.

외부 호출 없음: 실제 Chrome/실 배민 로그인/실 휴대폰 인증/실 시계/실 네트워크/실 thread 장기
대기 미사용. ``login_probe``/``open_auth_browser``/``detect_completion``/``now``/``sleep``/
transport 를 모두 주입 fake + 호출 카운터/타임스탬프로 대체해 분류·열기·사람-완료 감지·bounded
timeout·라우팅·무회귀를 결정적으로 검증한다(비-Windows CI 에서도 통과 — import-safety). 값은
명백한 가짜값만(``target-fake-…``/``otp-fake-…``) — 실 OTP/휴대폰/이메일/token 원문 없음(누출
가드). "OTP 취득·우회 0" 가드는 raw grep 이 아니라 **AST import-edge** 로 검사한다(scope
docstring 이 금지 심볼명을 문자열로 언급하므로 — memory/negative-guard-tests-use-ast).
``rider_agent.__main__`` 은 top-import 하지 않는다(runpy 경고 회피 — memory/agent-main-runpy-warning).
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
from pathlib import Path

import pytest

from rider_agent.auth.baemin_auth import (
    AUTH_STATE_ACTIVE,
    AUTH_STATE_AUTH_REQUIRED,
    AUTH_STATE_AUTH_VERIFIED,
    AUTH_STATE_BLOCKED_OR_CAPTCHA,
    AUTH_STATE_UNKNOWN,
    DEFAULT_MAX_ATTEMPTS,
    ERROR_AUTH_REQUIRED,
    REASON_AUTH_TIMEOUT,
    build_auth_execute_job,
    classify_baemin_auth_state,
    default_detect_completion,
    default_login_probe,
    default_open_auth_browser,
    execute_auth_check_job,
    execute_open_auth_browser_job,
)
from rider_agent.heartbeat import (
    CAPABILITY_AUTH_CHECK,
    CAPABILITY_OPEN_AUTH_BROWSER,
)
from rider_agent.job_loop import (
    CLAIM_PATH,
    ERROR_UNSUPPORTED_JOB_TYPE,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCESS,
    ClaimedJob,
    default_execute_job,
    make_success_result,
    run_agent,
)
from rider_agent.reuse import BrowserActionRequiredError, CdpUnavailableError
from rider_agent.secure_store import AgentIdentity, save_agent_identity

# 가짜 식별자만(누출 가드 — 실 토큰/OTP/휴대폰/이메일 금지).
FAKE_TOKEN = "agtok-fake-baemin-auth-secret"
FAKE_TARGET = "target-fake-1"
FAKE_OTP = "otp-fake-123456"
FAKE_PHONE = "010fakephone"

_IDENTITY = AgentIdentity(
    agent_id="agent-fake-1",
    agent_token=FAKE_TOKEN,
    tenant_scope={"tenant": "t-fake"},
    config_version="cfg-fake-1",
)

# 먼 미래 lease(서버 부여값) — 성공 결과의 lease self-check 가 만료로 보지 않게(year ~2128).
FUTURE_LEASE = 5_000_000_000.0

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "rider_agent"
    / "auth"
    / "baemin_auth.py"
)


def _auth_job(job_id="job-fake-1", *, type=CAPABILITY_AUTH_CHECK, target_id=FAKE_TARGET, payload=None):
    return ClaimedJob(
        job_id=job_id,
        type=type,
        target_id=target_id,
        lease_expires_at=FUTURE_LEASE,
        payload=payload if payload is not None else {},
    )


# ══════════════════════════════════════════════════════════════════════════
# AC1 — 분류기: BrowserActionRequiredError→AUTH_REQUIRED, 비-auth 예외 오분류 금지
# ══════════════════════════════════════════════════════════════════════════


def test_classify_browser_action_required_maps_to_auth_required():
    state = classify_baemin_auth_state(error=BrowserActionRequiredError("login needed (fake)"))
    assert state == AUTH_STATE_AUTH_REQUIRED


def test_classify_snapshot_ok_maps_to_active():
    assert classify_baemin_auth_state(snapshot_ok=True) == AUTH_STATE_ACTIVE


def test_classify_no_signal_is_unknown():
    assert classify_baemin_auth_state() == AUTH_STATE_UNKNOWN
    assert classify_baemin_auth_state(snapshot_ok=False) == AUTH_STATE_UNKNOWN
    assert classify_baemin_auth_state(snapshot_ok=None) == AUTH_STATE_UNKNOWN


def test_classify_non_auth_exceptions_are_not_auth_required():
    # 배민은 BrowserActionRequiredError 를 raise 하지 않는다(쿠팡만) — 파서/연결 문제를 인증
    # 문제로 오인하지 않는다(memory/baemin-no-action-required-signal). 모두 UNKNOWN.
    from rider_crawl.parser import MissingPerformanceDataError

    for exc in (
        CdpUnavailableError("cdp down (fake)"),
        MissingPerformanceDataError("missing perf fields (fake)"),
        RuntimeError("generic (fake)"),
    ):
        assert classify_baemin_auth_state(error=exc) == AUTH_STATE_UNKNOWN, exc
        # snapshot_ok=True 라도 BrowserActionRequiredError 가 아니므로 ACTIVE 우선 안 됨? —
        # 분류기는 error 우선 검사이나 비-auth 예외는 통과하므로 snapshot_ok 가 판정.
        assert (
            classify_baemin_auth_state(snapshot_ok=True, error=exc) == AUTH_STATE_ACTIVE
        )


# ══════════════════════════════════════════════════════════════════════════
# AC1 — AUTH_CHECK 실행자: 로그인 상태만 점검·보고(수집/렌더/전송 0)
# ══════════════════════════════════════════════════════════════════════════


def test_auth_check_active_reports_success_with_target_and_state():
    probe_calls = []

    def probe(job):
        probe_calls.append(job)
        return AUTH_STATE_ACTIVE

    result = execute_auth_check_job(_auth_job(), login_probe=probe)

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json == {"target_id": FAKE_TARGET, "auth_state": AUTH_STATE_ACTIVE}
    assert len(probe_calls) == 1  # 상태만 점검


def test_auth_check_auth_required_surfaces_without_message_generation():
    result = execute_auth_check_job(_auth_job(), login_probe=lambda j: AUTH_STATE_AUTH_REQUIRED)

    # 메시지 생성 없이 auth-required 표면화(상태 점검은 "필요 신호" — success 결과로 일관).
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert result.result_json["target_id"] == FAKE_TARGET
    # 렌더/전송 흔적 없음(error_code 도 없음 — 상태 보고).
    assert result.error_code is None


def test_auth_check_unknown_state_is_preserved_as_unknown():
    # UNKNOWN/모호는 인증 필요가 아니라 판정 불가다. 서버가 최신 profile 오류 등을
    # 그대로 보여줄 수 있게 AUTH_REQUIRED 로 덮어쓰지 않는다.
    result = execute_auth_check_job(_auth_job(), login_probe=lambda j: AUTH_STATE_UNKNOWN)
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == AUTH_STATE_UNKNOWN


def test_auth_check_does_not_call_crawl_or_send(monkeypatch):
    # 트립와이어: AUTH_CHECK 는 수집/전송을 호출하지 않는다(fail-closed, NFR-2). reuse 의
    # crawl_snapshot/send_kakao_text 를 호출하면 즉시 단언 실패하게 만든다.
    import rider_agent.reuse as reuse

    tripped: list[str] = []
    monkeypatch.setattr(reuse, "crawl_snapshot", lambda *a, **k: tripped.append("crawl"))
    monkeypatch.setattr(reuse, "send_kakao_text", lambda *a, **k: tripped.append("send"))

    execute_auth_check_job(_auth_job(), login_probe=lambda j: AUTH_STATE_ACTIVE)
    execute_auth_check_job(_auth_job(), login_probe=lambda j: AUTH_STATE_AUTH_REQUIRED)

    assert tripped == []  # 수집/전송 호출 0


# ══════════════════════════════════════════════════════════════════════════
# AC2 — OPEN_AUTH_BROWSER: 프로필 열기 + 사람-완료 감지(AUTH_VERIFIED), OTP 0
# ══════════════════════════════════════════════════════════════════════════


def _detect_after(n):
    """N번째 호출에서 True 를 돌려주는 fake detect_completion(+ 호출 카운트 상태)."""

    state = {"calls": 0}

    def _detect(job):
        state["calls"] += 1
        return state["calls"] >= n

    return _detect, state


def test_open_auth_browser_detects_human_completion_and_resumes():
    open_calls = []
    sleeps = []
    detect, st = _detect_after(2)

    result = execute_open_auth_browser_job(
        _auth_job(type=CAPABILITY_OPEN_AUTH_BROWSER),
        open_auth_browser=lambda j: open_calls.append(j),
        detect_completion=detect,
        now=lambda: 0.0,
        sleep=lambda s: sleeps.append(s),
        max_attempts=5,
        max_wait_seconds=1e9,
        poll_interval_seconds=5.0,
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_VERIFIED
    assert result.result_json["target_id"] == FAKE_TARGET
    assert len(open_calls) == 1  # 프로필을 정확히 1회 연다(열기만)
    assert st["calls"] == 2  # 2번째 polling 에서 사람-완료 감지
    assert len(sleeps) == 1  # 1·2번째 사이 1회 대기


def test_open_auth_browser_signature_has_no_otp_or_code_input_param():
    # 인증번호(OTP) 취득·입력·우회 0(ADD-15): 실행자는 open/detect 만 받는다 — OTP/코드 입력
    # 주입점이 시그니처에 존재하지 않음을 정적으로 단언(자동입력 경로 부재).
    params = set(inspect.signature(execute_open_auth_browser_job).parameters)
    assert params == {
        "job",
        "open_auth_browser",
        "detect_completion",
        "now",
        "sleep",
        "max_wait_seconds",
        "poll_interval_seconds",
        "max_attempts",
        "log",
    }
    forbidden = {"otp", "code", "verification_code", "fetch_code", "submit_code", "fill"}
    assert forbidden.isdisjoint(params)


# ══════════════════════════════════════════════════════════════════════════
# AC3 — bounded 유지: AUTH_REQUIRED/auth_timeout, 전송 0, 무한 재시도 금지
# ══════════════════════════════════════════════════════════════════════════


def test_open_auth_browser_times_out_to_auth_required_bounded_by_attempts():
    open_calls = []
    sleeps = []
    now_calls = []
    detect_calls = []

    def detect(job):
        detect_calls.append(1)
        return False  # 사람-미완료 — 절대 완료되지 않음

    def now():
        now_calls.append(0.0)
        return 0.0  # wall-clock 은 진행 안 함 → attempts 상한이 멈춘다

    result = execute_open_auth_browser_job(
        _auth_job(type=CAPABILITY_OPEN_AUTH_BROWSER),
        open_auth_browser=lambda j: open_calls.append(j),
        detect_completion=detect,
        now=now,
        sleep=lambda s: sleeps.append(s),
        max_attempts=3,
        max_wait_seconds=1e9,
        poll_interval_seconds=5.0,
    )

    # AUTH_VERIFIED 로 가지 않고 AUTH_REQUIRED(auth_timeout)로 멈춘다(전송/메시지 0).
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert result.result_json["reason"] == REASON_AUTH_TIMEOUT
    assert result.metrics["auth_reason"] == REASON_AUTH_TIMEOUT
    # bounded: detect/sleep/now 호출이 상한(max_attempts) 이하 — 무한 polling 0.
    assert len(detect_calls) == 3
    assert len(detect_calls) <= 3
    assert len(sleeps) == 2  # max_attempts - 1
    assert len(now_calls) <= 3  # 무한 재시도 금지(상한 준수)
    assert len(open_calls) == 1  # 프로필은 1회만 열고 재시도 0


def test_open_auth_browser_times_out_bounded_by_wall_clock():
    # attempts 상한이 커도 wall-clock(max_wait_seconds) 이 멈춘다(주입 now 로 결정적).
    clock = {"t": 0.0}
    detect_calls = []
    sleeps = []

    def now():
        v = clock["t"]
        clock["t"] += 6.0
        return v

    def detect(job):
        detect_calls.append(1)
        return False

    result = execute_open_auth_browser_job(
        _auth_job(type=CAPABILITY_OPEN_AUTH_BROWSER),
        open_auth_browser=lambda j: None,
        detect_completion=detect,
        now=now,
        sleep=lambda s: sleeps.append(s),
        max_attempts=100,  # 크게 — wall-clock 이 먼저 멈춰야 함
        max_wait_seconds=10.0,
        poll_interval_seconds=5.0,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.metrics["auth_reason"] == REASON_AUTH_TIMEOUT
    # wall-clock 10s 안에서 멈춤 — attempts 100 까지 가지 않는다(무한 재시도 금지).
    assert len(detect_calls) < 100
    assert len(detect_calls) == 2  # start=0; t=6(<10) sleep; t=12(>=10) break


def test_open_auth_browser_default_bounds_are_finite():
    # 운영 기본 상한이 유한(무한 재시도 금지 모듈 상수)임을 잠근다.
    assert DEFAULT_MAX_ATTEMPTS >= 1
    assert DEFAULT_MAX_ATTEMPTS < 1000


# ══════════════════════════════════════════════════════════════════════════
# AC2 — OTP/우회 0: AST import-edge 부정 가드(raw grep 아님)
# ══════════════════════════════════════════════════════════════════════════


def _import_modules_and_names(tree: ast.Module):
    """모듈의 import edge — (dotted 모듈 경로 집합, 임포트된 심볼명 집합)."""

    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
            for alias in node.names:
                names.add(alias.name)
    return modules, names


def test_baemin_auth_does_not_import_otp_or_gui_automation_symbols():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    modules, names = _import_modules_and_names(tree)

    forbidden_modules = {
        "pyautogui",
        "pywinauto",
        "pyperclip",
        "rider_crawl.auth.imap_2fa",
        "rider_crawl.auth.coupang_email_2fa",
    }
    forbidden_names = {
        "fetch_latest_verification_code",
        "recover_coupang_session_with_email_2fa",
        "pyautogui",
        "pywinauto",
        "pyperclip",
    }
    assert forbidden_modules.isdisjoint(modules), modules & forbidden_modules
    assert forbidden_names.isdisjoint(names), names & forbidden_names
    # rider_server import 0(단방향) + 새 third-party root 0(rider_crawl/자기 패키지만).
    assert "rider_server" not in {m.split(".")[0] for m in modules}


def test_negative_guard_is_ast_not_grep():
    # scope docstring 은 금지 심볼명을 문자열로 언급한다(설명용) — raw grep 이면 오탐이지만 AST
    # import-edge 검사는 통과해야 한다(memory/negative-guard-tests-use-ast).
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "fetch_latest_verification_code" in source  # docstring 언급(존재)
    assert "pyautogui" in source  # docstring 언급(존재)
    # 그러나 import edge 에는 없다(위 테스트가 단언) — 두 사실이 공존함을 명시.


# ══════════════════════════════════════════════════════════════════════════
# AC4 — build_auth_execute_job 라우터 + 실 루프 무회귀
# ══════════════════════════════════════════════════════════════════════════


def test_router_routes_auth_check_open_and_fallback():
    probe_calls = []
    open_calls = []
    fallback_jobs = []

    execute = build_auth_execute_job(
        login_probe=lambda j: (probe_calls.append(j) or AUTH_STATE_ACTIVE),
        open_auth_browser=lambda j: open_calls.append(j),
        detect_completion=lambda j: True,
        fallback=lambda j: (fallback_jobs.append(j) or make_success_result()),
        now=lambda: 0.0,
        sleep=lambda s: None,
    )

    r_check = execute(_auth_job("j1", type=CAPABILITY_AUTH_CHECK))
    r_open = execute(_auth_job("j2", type=CAPABILITY_OPEN_AUTH_BROWSER))
    other = _auth_job("j3", type="CRAWL_BAEMIN")
    r_other = execute(other)

    assert r_check.result_json["auth_state"] == AUTH_STATE_ACTIVE
    assert len(probe_calls) == 1
    assert r_open.result_json["auth_state"] == AUTH_STATE_AUTH_VERIFIED
    assert len(open_calls) == 1
    assert fallback_jobs == [other]  # 그 외 type 은 기존 executor 로
    assert r_other.status == JOB_STATUS_SUCCESS


def test_router_default_fallback_rejects_unknown_type():
    # fallback 미지정 = default_execute_job: 비-auth type 은 UNSUPPORTED_JOB_TYPE 로 종결되고
    # auth 실행자로 새지 않는다(4.7 동작 보존).
    open_calls = []
    execute = build_auth_execute_job(
        login_probe=lambda j: AUTH_STATE_ACTIVE,
        open_auth_browser=lambda j: open_calls.append(j),
        detect_completion=lambda j: True,
    )

    result = execute(ClaimedJob(job_id="j", type="CRAWL_BAEMIN"))

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_UNSUPPORTED_JOB_TYPE
    assert open_calls == []  # auth 실행자 미호출


def test_unwired_default_execute_job_preserves_unsupported_for_auth_types():
    # build_auth_execute_job 미합성이면 auth type 도 기존 default_execute_job 그대로(무회귀).
    for jtype in (CAPABILITY_AUTH_CHECK, CAPABILITY_OPEN_AUTH_BROWSER):
        result = default_execute_job(ClaimedJob(job_id="j", type=jtype))
        assert result.status == JOB_STATUS_FAILED
        assert result.error_code == ERROR_UNSUPPORTED_JOB_TYPE


# ── 실 claim→execute→complete 루프 라우팅(run_agent 합성, hang 0) ──────────────


class _FakeTransport:
    """주입 fake transport — claim 응답 스크립트 + (url, body, headers) 캡처(실 네트워크 0)."""

    def __init__(self, *, claim_script=None):
        self.claim_script = list(claim_script) if claim_script is not None else []
        self._idx = 0
        self.calls: list[tuple] = []

    def post_json(self, url, body, *, headers=None) -> dict:
        self.calls.append((url, body, headers))
        if url.endswith(CLAIM_PATH):
            if self._idx < len(self.claim_script):
                item = self.claim_script[self._idx]
                self._idx += 1
                return item
            return {"jobs": []}
        return {}

    def calls_for(self, suffix):
        return [c for c in self.calls if c[0].endswith(suffix)]


class _FakeStore:
    def __init__(self):
        self._data: dict[str, str] = {}

    def put(self, value, *, ref=""):
        self._data[ref] = value
        return ref

    def resolve(self, ref):
        return self._data.get(ref)


class _StoppingSleep:
    """주입 fake sleep — N회 호출 후 stop_event set(결정적, 실 대기 0)."""

    def __init__(self, stop_event, *, stop_after):
        self._stop = stop_event
        self._after = stop_after
        self.calls = 0

    def __call__(self, _seconds):
        self.calls += 1
        if self.calls >= self._after:
            self._stop.set()


def test_run_agent_routes_auth_job_through_real_loop(tmp_path):
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    job_dict = {
        "job_id": "job-fake-auth-1",
        "type": CAPABILITY_AUTH_CHECK,
        "target_id": FAKE_TARGET,
        "lease_expires_at": FUTURE_LEASE,
        "payload": {},
    }
    transport = _FakeTransport(claim_script=[{"jobs": [job_dict]}])
    stop = threading.Event()
    sleep = _StoppingSleep(stop, stop_after=1)
    probe_calls = []

    execute_job = build_auth_execute_job(
        login_probe=lambda j: (probe_calls.append(j) or AUTH_STATE_ACTIVE),
        open_auth_browser=lambda j: None,
        detect_completion=lambda j: True,
    )

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        execute_job=execute_job,
    )

    assert summary.started is True
    assert len(probe_calls) == 1  # auth job 이 AUTH_CHECK 실행자로 라우팅됨
    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    body = completes[0][1]
    assert body["status"] == JOB_STATUS_SUCCESS
    assert body["result_json"] == {"target_id": FAKE_TARGET, "auth_state": AUTH_STATE_ACTIVE}


def test_run_agent_composes_auth_worker_for_auth_job_by_default_option(tmp_path):
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    job_dict = {
        "job_id": "job-fake-auth-default-1",
        "type": CAPABILITY_AUTH_CHECK,
        "target_id": FAKE_TARGET,
        "lease_expires_at": FUTURE_LEASE,
        "payload": {},
    }
    transport = _FakeTransport(claim_script=[{"jobs": [job_dict]}])
    stop = threading.Event()
    sleep = _StoppingSleep(stop, stop_after=1)
    probe_calls = []

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_auth_worker=True,
        auth_login_probe=lambda j: (probe_calls.append(j) or AUTH_STATE_ACTIVE),
    )

    assert summary.started is True
    assert len(probe_calls) == 1
    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    body = completes[0][1]
    assert body["status"] == JOB_STATUS_SUCCESS
    assert body["result_json"] == {"target_id": FAKE_TARGET, "auth_state": AUTH_STATE_ACTIVE}


def test_run_agent_open_auth_browser_timeout_completes_without_hang(tmp_path):
    # OPEN_AUTH_BROWSER timeout 도 실 루프에서 complete 로 보고된다(무한 polling/hang 0).
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    job_dict = {
        "job_id": "job-fake-auth-2",
        "type": CAPABILITY_OPEN_AUTH_BROWSER,
        "target_id": FAKE_TARGET,
        "lease_expires_at": FUTURE_LEASE,
        "payload": {},
    }
    transport = _FakeTransport(claim_script=[{"jobs": [job_dict]}])
    stop = threading.Event()
    sleep = _StoppingSleep(stop, stop_after=1)

    execute_job = build_auth_execute_job(
        login_probe=lambda j: AUTH_STATE_ACTIVE,
        open_auth_browser=lambda j: None,
        detect_completion=lambda j: False,  # 사람-미완료
        now=lambda: 0.0,
        sleep=lambda s: None,  # 실행자 내부 polling 은 즉시(상한이 멈춘다)
        max_attempts=2,
        max_wait_seconds=1e9,
    )

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        execute_job=execute_job,
    )

    assert summary.started is True
    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    body = completes[0][1]
    assert body["status"] == JOB_STATUS_FAILED
    assert body["error_code"] == ERROR_AUTH_REQUIRED
    assert body["result_json"]["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert body["metrics"]["auth_reason"] == REASON_AUTH_TIMEOUT


# ══════════════════════════════════════════════════════════════════════════
# 기본 probe — Windows-gated lazy(비-Windows/주입 미시 호출 차단, import-safety)
# ══════════════════════════════════════════════════════════════════════════


def test_default_login_probe_reuses_crawl_snapshot_for_active(monkeypatch):
    calls = []

    def fake_crawl_snapshot(config, *, platform_name=None):
        calls.append((config, platform_name))
        return object()

    monkeypatch.setattr("rider_agent.reuse.crawl_snapshot", fake_crawl_snapshot)

    state = default_login_probe(
        _auth_job(
            payload={
                "tenant_id": "tenant-fake-1",
                "target_id": FAKE_TARGET,
                "platform": "baemin",
                "primary_url": "https://self.baemin.example/stats",
                "expected_display_name": "배민센터A",
                "timeout_seconds": 30,
            }
        )
    )

    assert state == AUTH_STATE_ACTIVE
    assert len(calls) == 1
    config, platform_name = calls[0]
    assert platform_name == "baemin"
    assert config.coupang_eats_url == "https://self.baemin.example/stats"
    assert config.baemin_center_name == "배민센터A"
    assert config.send_enabled is False


def test_default_login_probe_maps_browser_action_required(monkeypatch):
    def fake_crawl_snapshot(config, *, platform_name=None):
        raise BrowserActionRequiredError("login needed")

    monkeypatch.setattr("rider_agent.reuse.crawl_snapshot", fake_crawl_snapshot)

    assert default_login_probe(_auth_job(payload={"platform": "baemin"})) == AUTH_STATE_AUTH_REQUIRED


def test_default_login_probe_maps_ambiguous_errors_to_unknown(monkeypatch):
    def fake_crawl_snapshot(config, *, platform_name=None):
        raise RuntimeError("parser or connection problem")

    monkeypatch.setattr("rider_agent.reuse.crawl_snapshot", fake_crawl_snapshot)

    assert default_login_probe(_auth_job(payload={"platform": "baemin"})) == AUTH_STATE_UNKNOWN


def test_default_open_auth_browser_and_detect_completion_reuse_existing_seams(monkeypatch):
    prepare_calls = []

    def fake_prepare_chrome(config, *, platform_name=None):
        prepare_calls.append((config, platform_name))
        return "ready"

    monkeypatch.setattr("rider_agent.reuse.prepare_chrome", fake_prepare_chrome)

    job = _auth_job(
        type=CAPABILITY_OPEN_AUTH_BROWSER,
        payload={
            "tenant_id": "tenant-fake-1",
            "target_id": FAKE_TARGET,
            "platform": "baemin",
            "primary_url": "https://self.baemin.example/stats",
            "expected_display_name": "배민센터A",
        },
    )
    default_open_auth_browser(job)

    assert len(prepare_calls) == 1
    config, platform_name = prepare_calls[0]
    assert platform_name == "Windows"
    assert config.coupang_eats_url == "https://self.baemin.example/stats"

    monkeypatch.setattr("rider_agent.auth.baemin_auth.default_login_probe", lambda j: AUTH_STATE_ACTIVE)
    assert default_detect_completion(job) is True
    monkeypatch.setattr(
        "rider_agent.auth.baemin_auth.default_login_probe", lambda j: AUTH_STATE_AUTH_REQUIRED
    )
    assert default_detect_completion(job) is False


def test_run_agent_default_auth_worker_completes_auth_check_with_real_probe(tmp_path, monkeypatch):
    monkeypatch.setattr("rider_agent.reuse.crawl_snapshot", lambda config, *, platform_name=None: object())
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    job_dict = {
        "job_id": "job-fake-auth-default-real-1",
        "type": CAPABILITY_AUTH_CHECK,
        "target_id": FAKE_TARGET,
        "lease_expires_at": FUTURE_LEASE,
        "payload": {
            "tenant_id": "tenant-fake-1",
            "target_id": FAKE_TARGET,
            "platform": "baemin",
            "primary_url": "https://self.baemin.example/stats",
            "expected_display_name": "배민센터A",
            "timeout_seconds": 30,
        },
    }
    transport = _FakeTransport(claim_script=[{"jobs": [job_dict]}])
    stop = threading.Event()
    sleep = _StoppingSleep(stop, stop_after=1)

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_auth_worker=True,
    )

    assert summary.started is True
    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    body = completes[0][1]
    assert body["status"] == JOB_STATUS_SUCCESS
    assert body["result_json"] == {"target_id": FAKE_TARGET, "auth_state": AUTH_STATE_ACTIVE}


# ══════════════════════════════════════════════════════════════════════════
# 누출 가드 + 값 정합 — secret/OTP/휴대폰 0, 평문 상수 == rider_server 도메인 값
# ══════════════════════════════════════════════════════════════════════════


def test_no_otp_or_phone_leaks_into_result_or_log_even_if_in_payload():
    # job payload 에 OTP/휴대폰 류가 들어 있어도 결과/로그에 새지 않는다(실행자는 target_id 만
    # 읽고 OTP/휴대폰을 만지지 않는다). result_json·metrics·log 캡처에 secret 0건.
    logs: list[str] = []
    payload = {"otp": FAKE_OTP, "phone": FAKE_PHONE, "verification_code": FAKE_OTP}

    r_check = execute_auth_check_job(
        _auth_job(payload=payload), login_probe=lambda j: AUTH_STATE_AUTH_REQUIRED, log=logs.append
    )
    r_open = execute_open_auth_browser_job(
        _auth_job(type=CAPABILITY_OPEN_AUTH_BROWSER, payload=payload),
        open_auth_browser=lambda j: None,
        detect_completion=lambda j: False,
        now=lambda: 0.0,
        sleep=lambda s: None,
        max_attempts=2,
        max_wait_seconds=1e9,
        log=logs.append,
    )

    blob = json.dumps(
        {
            "check": {"result_json": r_check.result_json, "metrics": r_check.metrics},
            "open": {
                "result_json": r_open.result_json,
                "metrics": r_open.metrics,
                "error_message_redacted": r_open.error_message_redacted,
            },
            "logs": logs,
        },
        ensure_ascii=False,
    )
    assert FAKE_OTP not in blob
    assert FAKE_PHONE not in blob
    assert FAKE_TOKEN not in blob


def test_plain_constants_align_with_rider_server_domain_values():
    # rider_agent 코드는 rider_server 를 import 하지 않는다 — 테스트에서만 값 정합 확인(import 0
    # 단방향 유지, 평문 상수가 BaeminAuthState/FailureCategory 값과 일치).
    from rider_server.domain.states import BaeminAuthState, FailureCategory

    assert AUTH_STATE_UNKNOWN == BaeminAuthState.UNKNOWN.value
    assert AUTH_STATE_ACTIVE == BaeminAuthState.ACTIVE.value
    assert AUTH_STATE_AUTH_REQUIRED == BaeminAuthState.AUTH_REQUIRED.value
    assert AUTH_STATE_AUTH_VERIFIED == BaeminAuthState.AUTH_VERIFIED.value
    assert AUTH_STATE_BLOCKED_OR_CAPTCHA == BaeminAuthState.BLOCKED_OR_CAPTCHA.value
    assert ERROR_AUTH_REQUIRED == FailureCategory.AUTH_REQUIRED.value == "AUTH_REQUIRED"


# ══════════════════════════════════════════════════════════════════════════
# qa-e2e 추가 — 경계/우선순위/실-루프 성공 경로 커버리지 갭(2026-06-14)
# ══════════════════════════════════════════════════════════════════════════


def test_classify_auth_required_error_wins_over_ok_snapshot():
    # fail-closed 우선순위(AC1): auth-required 신호(BrowserActionRequiredError)는 정상처럼
    # 보이는 snapshot 보다 우선한다 — snapshot_ok=True 가 와도 인증 필요면 ACTIVE 가 아니라
    # AUTH_REQUIRED 로 막는다(인증으로 막힌 대상에 잘못된 메시지 0, NFR-2). 분류기 error 우선
    # 검사 분기를 명시적으로 잠근다.
    state = classify_baemin_auth_state(
        snapshot_ok=True, error=BrowserActionRequiredError("login needed (fake)")
    )
    assert state == AUTH_STATE_AUTH_REQUIRED


def test_auth_check_blocked_or_captcha_is_fail_closed_to_auth_required():
    # ACTIVE 외 정의된 비-active 상태(BLOCKED_OR_CAPTCHA)도 fail-closed 로 AUTH_REQUIRED 어휘로
    # surfacing — 메시지 생성 0, error_code 없음(상태 보고). UNKNOWN 외 다른 비-active 입력도
    # 같은 정책임을 잠근다(AC1·3).
    result = execute_auth_check_job(
        _auth_job(), login_probe=lambda j: AUTH_STATE_BLOCKED_OR_CAPTCHA
    )
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert result.error_code is None


def test_auth_check_logs_redacted_message_on_both_branches():
    # ACTIVE/AUTH_REQUIRED 두 분기 모두 log 콜백을 호출(관측 가능)하고, 로그에 secret/OTP/휴대폰
    # 가 새지 않는다(redact 통과·고정 메시지 + target_id 만). ACTIVE 분기 로그 라인은 기존
    # 누출 테스트가 검증하지 않던 분기다(AC1·3, NFR-5/8).
    active_logs: list[str] = []
    required_logs: list[str] = []
    payload = {"otp": FAKE_OTP, "phone": FAKE_PHONE}

    execute_auth_check_job(
        _auth_job(payload=payload), login_probe=lambda j: AUTH_STATE_ACTIVE, log=active_logs.append
    )
    execute_auth_check_job(
        _auth_job(payload=payload),
        login_probe=lambda j: AUTH_STATE_AUTH_REQUIRED,
        log=required_logs.append,
    )

    assert len(active_logs) == 1  # ACTIVE 분기도 관측 로그를 남긴다
    assert len(required_logs) == 1  # AUTH_REQUIRED 분기도 관측 로그를 남긴다
    for line in active_logs + required_logs:
        assert FAKE_OTP not in line
        assert FAKE_PHONE not in line
        assert FAKE_TOKEN not in line
    assert FAKE_TARGET in active_logs[0]  # 보고 식별자(target_id)는 남는다(redact 비대상)


def test_open_auth_browser_immediate_completion_no_sleep():
    # 경계(AC2): 사람-완료가 1번째 polling 에서 감지되면 sleep 없이 즉시 AUTH_VERIFIED 로 재개
    # 신호를 낸다(불필요한 대기 0). 프로필은 정확히 1회 연다.
    open_calls = []
    sleeps = []
    detect, st = _detect_after(1)

    result = execute_open_auth_browser_job(
        _auth_job(type=CAPABILITY_OPEN_AUTH_BROWSER),
        open_auth_browser=lambda j: open_calls.append(j),
        detect_completion=detect,
        now=lambda: 0.0,
        sleep=lambda s: sleeps.append(s),
        max_attempts=5,
        max_wait_seconds=1e9,
        poll_interval_seconds=5.0,
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_VERIFIED
    assert len(open_calls) == 1  # 열기 1회
    assert st["calls"] == 1  # 1번째 polling 에서 감지
    assert sleeps == []  # 즉시 완료 → 대기 0


def test_open_auth_browser_single_attempt_times_out_without_sleep():
    # 최소 상한 경계(AC3): max_attempts=1 이면 detect 1회 후 미완료 시 sleep 없이 즉시
    # AUTH_REQUIRED/auth_timeout 으로 멈춘다(무한 재시도 금지의 최소 경계 — off-by-one 방지).
    open_calls = []
    sleeps = []
    detect_calls = []

    result = execute_open_auth_browser_job(
        _auth_job(type=CAPABILITY_OPEN_AUTH_BROWSER),
        open_auth_browser=lambda j: open_calls.append(j),
        detect_completion=lambda j: detect_calls.append(1) or False,
        now=lambda: 0.0,
        sleep=lambda s: sleeps.append(s),
        max_attempts=1,
        max_wait_seconds=1e9,
        poll_interval_seconds=5.0,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert result.result_json["reason"] == REASON_AUTH_TIMEOUT
    assert len(detect_calls) == 1  # 단일 시도
    assert sleeps == []  # 시도 1회 → 대기 0
    assert len(open_calls) == 1  # 프로필 열기 1회·재시도 0


def test_run_agent_open_auth_browser_success_completes_with_verified(tmp_path):
    # AC4 — OPEN_AUTH_BROWSER 사람-완료(AUTH_VERIFIED) 성공 경로도 실 claim→execute→complete
    # 루프로 보고된다(기존엔 timeout 실패 경로만 루프 검증). hang 0.
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    job_dict = {
        "job_id": "job-fake-auth-3",
        "type": CAPABILITY_OPEN_AUTH_BROWSER,
        "target_id": FAKE_TARGET,
        "lease_expires_at": FUTURE_LEASE,
        "payload": {},
    }
    transport = _FakeTransport(claim_script=[{"jobs": [job_dict]}])
    stop = threading.Event()
    sleep = _StoppingSleep(stop, stop_after=1)
    open_calls = []

    execute_job = build_auth_execute_job(
        login_probe=lambda j: AUTH_STATE_ACTIVE,
        open_auth_browser=lambda j: open_calls.append(j),
        detect_completion=lambda j: True,  # 사람-완료(즉시)
        now=lambda: 0.0,
        sleep=lambda s: None,
    )

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        execute_job=execute_job,
    )

    assert summary.started is True
    assert len(open_calls) == 1  # 프로필 열기 1회
    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    body = completes[0][1]
    assert body["status"] == JOB_STATUS_SUCCESS
    assert body["result_json"] == {
        "target_id": FAKE_TARGET,
        "auth_state": AUTH_STATE_AUTH_VERIFIED,
    }
