"""Story 4.3 — heartbeat 리포터(payload·단발 send·주기 loop) 검증.

외부 호출 없음: transport 는 fake(canned/에러) 또는 실 ``HttpTransport``+fake ``urlopen``,
주기 루프는 **주입 fake sleep + stop event + 호출 카운터**로 실 대기/실 thread 없이 결정적
검증한다. 값은 명백한 가짜값만(``agtok-fake-…``/``agent-fake-…``). 실제 봇 토큰/chat_id/
전화/이메일/OTP 원문 없음(누출 가드).
"""

from __future__ import annotations

import json
import threading
from urllib.error import HTTPError

import pytest

from rider_agent import __version__
from rider_agent.heartbeat import (
    DEFAULT_CAPABILITIES,
    HEARTBEAT_OP_LABEL,
    HEARTBEAT_PATH,
    HeartbeatReporter,
    HeartbeatResult,
    build_heartbeat_payload,
    clamp_interval,
    send_heartbeat,
)
from rider_agent.registration import (
    DEFAULT_SERVER_BASE_URL,
    SERVER_URL_ENV,
    HttpTransport,
    TransportError,
)
from rider_agent.secure_store import (
    TOKEN_STATUS_REVOKED,
    TOKEN_STATUS_VALID,
    AgentIdentity,
)

# 명백한 가짜 token — 매 주기 헤더에 실리는 반복 노출 표면이라 누출 단언의 핵심 대상.
FAKE_TOKEN = "agtok-fake-heartbeat-secret"

_IDENTITY = AgentIdentity(
    agent_id="agent-fake-1",
    agent_token=FAKE_TOKEN,
    tenant_scope={"tenant": "t-fake"},
    config_version="cfg-fake-1",
)

CANNED_RESPONSE = {
    "server_time": "2026-06-13T00:00:00Z",
    "config_version": "cfg-fake-2",
    "commands": [{"type": "noop"}],
}

_SEVEN_KEYS = {
    "agent_id",
    "agent_version",
    "metrics",
    "capabilities",
    "active_jobs",
    "kakao_status",
    "browser_profiles",
}

_SIX_JOB_TYPES = {
    "CRAWL_BAEMIN",
    "CRAWL_COUPANG",
    "AUTH_CHECK",
    "OPEN_AUTH_BROWSER",
    "KAKAO_SEND",
    "CAPTURE_DIAGNOSTIC",
}


class FakeTransport:
    """주입 fake transport: canned 응답/에러. (url, body, headers) 캡처 + 호출 카운터.

    ``errors`` 가 주어지면 호출 순서대로 그 예외(또는 ``None``=성공)를 적용한다 — 단발 실패
    뒤 회복 같은 시퀀스를 결정적으로 재현한다.
    """

    def __init__(self, *, response=None, error=None, errors=None) -> None:
        self.response = response if response is not None else dict(CANNED_RESPONSE)
        self.error = error
        self.errors = list(errors) if errors is not None else None
        self.calls: list[tuple[str, dict, dict | None]] = []

    def post_json(self, url, body, *, headers=None) -> dict:
        self.calls.append((url, body, headers))
        if self.errors is not None:
            idx = len(self.calls) - 1
            exc = self.errors[idx] if idx < len(self.errors) else None
            if exc is not None:
                raise exc
            return dict(self.response)
        if self.error is not None:
            raise self.error
        return dict(self.response)


class StoppingSleep:
    """주입 fake sleep: N 회 호출 후 stop_event 를 set(결정적, 실 대기 0)."""

    def __init__(self, stop_event: threading.Event, *, stop_after: int) -> None:
        self._stop_event = stop_event
        self._stop_after = stop_after
        self.intervals: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.intervals.append(seconds)
        if len(self.intervals) >= self._stop_after:
            self._stop_event.set()


class _FakeHttpResponse:
    """urllib ``urlopen`` 이 돌려주는 context-manager 응답의 최소 fake(``read()`` 만)."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._raw


# ──────────────────────────────────────────────────────────────────────────
# AC1 — payload: 5필드 + agent_id + agent_version + provider 반영
# ──────────────────────────────────────────────────────────────────────────


def test_build_payload_has_seven_keys_and_version():
    payload = build_heartbeat_payload(_IDENTITY)

    assert set(payload) == _SEVEN_KEYS
    assert payload["agent_id"] == "agent-fake-1"
    assert payload["agent_version"] == __version__
    # 기본 provider → 안전 빈/idle 값(실제 소스는 후속 스토리가 주입).
    assert payload["active_jobs"] == []
    assert payload["browser_profiles"] == []
    assert payload["kakao_status"] == {"state": "disabled", "queue_depth": 0}
    assert isinstance(payload["metrics"], dict)
    # token 본문 미포함(인증은 헤더).
    assert "agent_token" not in payload
    assert FAKE_TOKEN not in json.dumps(payload)


def test_build_payload_reflects_injected_providers():
    payload = build_heartbeat_payload(
        _IDENTITY,
        capabilities=["CRAWL_BAEMIN"],
        metrics_provider={"cpu": 0.1},
        active_jobs_provider=lambda: ["job-1"],
        kakao_status_provider="idle",
        browser_profiles_provider=lambda: [{"profile_id": "p1", "cdp_port": 9222}],
    )

    assert payload["capabilities"] == ["CRAWL_BAEMIN"]
    assert payload["metrics"] == {"cpu": 0.1}
    assert payload["active_jobs"] == ["job-1"]
    assert payload["kakao_status"] == {"state": "idle"}
    assert payload["browser_profiles"] == [{"profile_id": "p1", "cdp_port": 9222}]


def test_send_heartbeat_posts_to_path_with_seven_field_body():
    transport = FakeTransport()

    send_heartbeat(_IDENTITY, transport=transport, base_url="https://srv.test")

    assert len(transport.calls) == 1
    url, body, _headers = transport.calls[0]
    assert url == "https://srv.test" + HEARTBEAT_PATH
    assert set(body) == _SEVEN_KEYS
    assert body["agent_id"] == "agent-fake-1"
    assert body["agent_version"] == __version__


# ──────────────────────────────────────────────────────────────────────────
# AC1 — interval [30,60] 검증/clamp (경계 포함)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [(5, 30), (29, 30), (30, 30), (45, 45), (60, 60), (61, 60), (600, 60)],
)
def test_clamp_interval_bounds(raw, expected):
    assert clamp_interval(raw) == expected


def test_reporter_clamps_interval_out_of_range():
    fast = HeartbeatReporter(_IDENTITY, transport=FakeTransport(), interval_seconds=5)
    slow = HeartbeatReporter(_IDENTITY, transport=FakeTransport(), interval_seconds=600)
    assert fast.interval_seconds == 30
    assert slow.interval_seconds == 60


# ──────────────────────────────────────────────────────────────────────────
# AC1 — 응답 파싱(server_time/config_version/commands) — commands 실행은 범위 밖
# ──────────────────────────────────────────────────────────────────────────


def test_send_heartbeat_parses_response():
    result = send_heartbeat(_IDENTITY, transport=FakeTransport(response=CANNED_RESPONSE))

    assert isinstance(result, HeartbeatResult)
    assert result.server_time == "2026-06-13T00:00:00Z"
    assert result.config_version == "cfg-fake-2"
    assert result.commands == [{"type": "noop"}]


def test_agent_surfaces_lease_extension_degraded() -> None:
    response = {
        **CANNED_RESPONSE,
        "lease_extension": {
            "status": "degraded",
            "extended_job_ids": ["job-ok"],
            "failed_job_ids": ["job-lost"],
        },
    }
    logs: list[str] = []
    reporter = HeartbeatReporter(
        _IDENTITY,
        transport=FakeTransport(response=response),
        log=logs.append,
    )

    result = reporter.report_once()

    assert result is not None
    assert result.lease_extension["failed_job_ids"] == ["job-lost"]
    assert reporter.last_error_event is not None
    assert reporter.last_error_event["code"] == "AGENT_HEARTBEAT_LEASE_EXTENSION_DEGRADED"
    assert FAKE_TOKEN not in " ".join(logs)


def test_send_heartbeat_missing_fields_parse_to_safe_defaults():
    result = send_heartbeat(_IDENTITY, transport=FakeTransport(response={}))

    assert result.server_time is None
    assert result.config_version is None
    assert result.commands == []


def test_heartbeat_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv(SERVER_URL_ENV, "https://env.test")
    transport = FakeTransport()

    send_heartbeat(_IDENTITY, transport=transport)

    url, _body, _headers = transport.calls[0]
    assert url == "https://env.test" + HEARTBEAT_PATH


def test_heartbeat_url_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv(SERVER_URL_ENV, raising=False)
    transport = FakeTransport()

    send_heartbeat(_IDENTITY, transport=transport)

    url, _body, _headers = transport.calls[0]
    assert url == DEFAULT_SERVER_BASE_URL + HEARTBEAT_PATH


# ──────────────────────────────────────────────────────────────────────────
# AC2 — 주기 보고 + best-effort 복원력(단발 실패가 thread 를 죽이지 않음)
# ──────────────────────────────────────────────────────────────────────────


def test_reporter_runs_n_times_then_stops():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=3)
    transport = FakeTransport()
    reporter = HeartbeatReporter(
        _IDENTITY,
        transport=transport,
        interval_seconds=30,
        sleep=sleep,
        stop_event=stop,
    )

    reporter.run()

    # 결정적: 정확히 3회 보고 후 정지(실 sleep/네트워크 0).
    assert len(transport.calls) == 3
    # interval=30(MIN)에서는 jitter 를 빼도 clamp 하한 30 으로 되돌아온다(상한/하한 불변식 유지).
    assert sleep.intervals == [30, 30, 30]
    assert reporter.last_result is not None


def test_heartbeat_interval_has_stable_jitter():
    # per-Agent stable jitter: 같은 Agent 는 매 주기 같은(결정적) jitter, [30,60] 안.
    from rider_agent.heartbeat import (
        DEFAULT_HEARTBEAT_JITTER_RATIO,
        MAX_HEARTBEAT_INTERVAL_SECONDS,
        MIN_HEARTBEAT_INTERVAL_SECONDS,
        stable_jitter_ratio,
    )

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=3)
    reporter = HeartbeatReporter(
        _IDENTITY, transport=FakeTransport(), interval_seconds=45, sleep=sleep, stop_event=stop
    )
    reporter.run()

    span = 45 * DEFAULT_HEARTBEAT_JITTER_RATIO
    expected = 45 - span * stable_jitter_ratio(_IDENTITY.agent_id)
    # 결정적·안정: 매 주기 같은 값이고 45 보다 짧다(위로 안 더해 60 상한 안전).
    assert sleep.intervals == [pytest.approx(expected)] * 3
    assert all(
        MIN_HEARTBEAT_INTERVAL_SECONDS <= w <= MAX_HEARTBEAT_INTERVAL_SECONDS
        for w in sleep.intervals
    )
    assert sleep.intervals[0] < 45


def test_heartbeat_jitter_differs_across_agents():
    # 서로 다른 Agent 는 서로 다른 jitter → 같은 초에 heartbeat 가 몰리지 않는다(thundering herd).
    other = AgentIdentity(
        agent_id="agent-fake-2",
        agent_token=FAKE_TOKEN,
        tenant_scope={"tenant": "t-fake"},
        config_version="cfg-fake-1",
    )
    stop_a, stop_b = threading.Event(), threading.Event()
    sleep_a = StoppingSleep(stop_a, stop_after=1)
    sleep_b = StoppingSleep(stop_b, stop_after=1)
    HeartbeatReporter(
        _IDENTITY, transport=FakeTransport(), interval_seconds=50, sleep=sleep_a, stop_event=stop_a
    ).run()
    HeartbeatReporter(
        other, transport=FakeTransport(), interval_seconds=50, sleep=sleep_b, stop_event=stop_b
    ).run()

    assert sleep_a.intervals[0] != sleep_b.intervals[0]


def test_reporter_survives_single_failure_and_continues():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    # 1차 호출 TransportError(5xx), 이후 성공.
    transport = FakeTransport(
        errors=[TransportError("agent heartbeat HTTP error", status_code=503), None, None]
    )
    logs: list[str] = []
    reporter = HeartbeatReporter(
        _IDENTITY, transport=transport, sleep=sleep, stop_event=stop, log=logs.append
    )

    reporter.run()

    # 첫 주기 실패에도 루프가 죽지 않고 다음 주기로 진행(>=2회 호출).
    assert len(transport.calls) == 2
    # 에러가 redact 되어 기록되고 평문 token 이 새지 않음.
    assert reporter.last_error_event is not None
    assert FAKE_TOKEN not in " ".join(logs)
    # 일시 실패는 status 를 revoke 로 보지 않고, 2차 성공으로 valid 회복.
    assert reporter.token_status == TOKEN_STATUS_VALID
    assert reporter.needs_registration is False


def test_reporter_surfaces_revoked_on_401_without_crash_or_spin():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    statuses: list[str] = []
    logs: list[str] = []
    transport = FakeTransport(
        error=TransportError("agent heartbeat HTTP error", status_code=401)
    )
    reporter = HeartbeatReporter(
        _IDENTITY,
        transport=transport,
        sleep=sleep,
        stop_event=stop,
        on_status=statuses.append,
        log=logs.append,
    )

    reporter.run()

    # 매 주기 401 이어도 crash 없이 루프 진행 → 주입 sleep 으로만 정지(무한 즉시 스핀 없음).
    assert len(transport.calls) == 2
    assert sleep.intervals  # 매 주기 끝에 sleep 했다(즉시 재호출 아님).
    # 재등록 필요 상태로 surfacing(4.2 TOKEN_STATUS_* 어휘 재사용).
    assert reporter.needs_registration is True
    assert reporter.token_status == TOKEN_STATUS_REVOKED
    # 상태 변화 시에만 1회 통지.
    assert statuses == [TOKEN_STATUS_REVOKED]
    assert FAKE_TOKEN not in " ".join(logs)


def test_reporter_stop_breaks_loop():
    stop = threading.Event()
    stop.set()  # 시작 전 정지 요청 → 한 번도 보고하지 않는다.
    transport = FakeTransport()
    reporter = HeartbeatReporter(_IDENTITY, transport=transport, stop_event=stop)

    reporter.run()

    assert transport.calls == []


# ──────────────────────────────────────────────────────────────────────────
# AC3 — capabilities = 처리 가능 job type(6종 superset, "정확히 N" lock 금지)
# ──────────────────────────────────────────────────────────────────────────


def test_default_capabilities_include_six_job_types_as_superset():
    payload = build_heartbeat_payload(_IDENTITY)
    # superset 허용 — 후속 워커가 job type 을 늘려도 무탈(memory: enum-member-count).
    assert _SIX_JOB_TYPES <= set(payload["capabilities"])
    assert _SIX_JOB_TYPES <= set(DEFAULT_CAPABILITIES)


def test_injected_capabilities_reflected_including_future_types():
    payload = build_heartbeat_payload(
        _IDENTITY, capabilities=("CRAWL_BAEMIN", "EXTRA_FUTURE")
    )
    assert payload["capabilities"] == ["CRAWL_BAEMIN", "EXTRA_FUTURE"]


# ──────────────────────────────────────────────────────────────────────────
# token-auth 헤더 + 평문 비노출(핵심 가드)
# ──────────────────────────────────────────────────────────────────────────


def test_send_heartbeat_carries_bearer_token_in_header():
    transport = FakeTransport()

    send_heartbeat(_IDENTITY, transport=transport)

    _url, body, headers = transport.calls[0]
    assert headers == {"Authorization": f"Bearer {FAKE_TOKEN}"}
    # 본문엔 token 평문 없음(헤더에만).
    assert FAKE_TOKEN not in json.dumps(body)


def test_no_plaintext_token_in_logs_event_or_body_on_error():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=1)
    logs: list[str] = []
    transport = FakeTransport(
        error=TransportError("agent heartbeat HTTP error", status_code=500)
    )
    reporter = HeartbeatReporter(
        _IDENTITY, transport=transport, sleep=sleep, stop_event=stop, log=logs.append
    )

    reporter.run()

    joined = " ".join(logs) + json.dumps(reporter.last_error_event or {})
    assert FAKE_TOKEN not in joined
    for _url, body, _headers in transport.calls:
        assert FAKE_TOKEN not in json.dumps(body)


# ──────────────────────────────────────────────────────────────────────────
# 실 HttpTransport 경로(fake urlopen) — 헤더 병합·op-label·E2E
# ──────────────────────────────────────────────────────────────────────────


def test_http_transport_merges_auth_header_and_preserves_content_type():
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["content_type"] = request.get_header("Content-type")
        captured["authorization"] = request.get_header("Authorization")
        return _FakeHttpResponse(json.dumps(CANNED_RESPONSE).encode("utf-8"))

    transport = HttpTransport(urlopen=fake_urlopen, op_label=HEARTBEAT_OP_LABEL)
    result = transport.post_json(
        "https://srv.test" + HEARTBEAT_PATH,
        {"agent_id": "agent-fake-1"},
        headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
    )

    # Content-Type 이 보존되고(드롭 금지) Authorization 이 병합됨.
    assert captured["content_type"] == "application/json"
    assert captured["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert isinstance(result, dict)


def test_http_transport_op_label_distinguishes_heartbeat_errors():
    def fake_urlopen(request, timeout=None):
        raise HTTPError("https://srv.test/x", 500, "boom", {}, None)

    transport = HttpTransport(urlopen=fake_urlopen, op_label=HEARTBEAT_OP_LABEL)
    with pytest.raises(TransportError) as excinfo:
        transport.post_json(
            "https://srv.test" + HEARTBEAT_PATH, {"agent_id": "agent-fake-1"}
        )

    assert excinfo.value.status_code == 500
    # heartbeat 5xx 가 "agent register …"로 오해되지 않도록 op-label 이 메시지에 반영됨.
    assert "heartbeat" in str(excinfo.value)
    assert "register" not in str(excinfo.value)


def test_send_heartbeat_end_to_end_through_http_transport():
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["authorization"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHttpResponse(json.dumps(CANNED_RESPONSE).encode("utf-8"))

    transport = HttpTransport(urlopen=fake_urlopen, op_label=HEARTBEAT_OP_LABEL)
    result = send_heartbeat(_IDENTITY, transport=transport, base_url="https://srv.test")

    assert captured["url"] == "https://srv.test" + HEARTBEAT_PATH
    assert captured["method"] == "POST"
    assert captured["authorization"] == f"Bearer {FAKE_TOKEN}"
    # E2E 경로에서도 token 평문이 본문에 없음.
    assert FAKE_TOKEN not in json.dumps(captured["body"])
    assert result.config_version == "cfg-fake-2"


# ──────────────────────────────────────────────────────────────────────────
# QA gap coverage (4.3 qa-generate-e2e-tests) — best-effort 일반 예외·revoked→valid
# 회복·malformed commands·URL 정규화·공개 stop() 메서드(기존 26 케이스의 미커버 분기)
# ──────────────────────────────────────────────────────────────────────────


def test_reporter_survives_non_transport_exception_and_records_event():
    """AC2.5 — ``TransportError`` 가 아닌 일반 예외(예: 주입 provider 폭발)도 루프를 죽이지
    않는다. ``log`` 미주입 시 ``_record_error`` 의 no-log 분기를 타며 ``last_error_event`` 는
    여전히 기록되고, 일반 예외를 revoke 로 오판하지 않는다."""

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    # 1차 호출이 transport 계층이 아닌 일반 RuntimeError → 다음 주기는 성공.
    transport = FakeTransport(errors=[RuntimeError("provider exploded"), None, None])
    # log 미주입(기본 None) → _record_error 의 `if self._log is not None` False 분기 커버.
    reporter = HeartbeatReporter(
        _IDENTITY, transport=transport, sleep=sleep, stop_event=stop
    )

    reporter.run()

    # best-effort: 일반 예외에도 thread 가 죽지 않고 다음 주기로 진행(>=2회 호출).
    assert len(transport.calls) == 2
    # log 콜백이 없어도 에러 이벤트는 기록된다.
    assert reporter.last_error_event is not None
    # 일반 예외는 token revoke 신호가 아니다 — 상태 불변(valid) · 재등록 불필요.
    assert reporter.token_status == TOKEN_STATUS_VALID
    assert reporter.needs_registration is False


def test_reporter_recovers_to_valid_after_revoked():
    """문서화된 회복 동작 — ``401`` 로 revoked surfacing 후 token 재발급으로 성공하면 상태가
    ``valid`` 로 복귀하고 ``on_status`` 가 ``[REVOKED, VALID]`` 두 전이를 통지한다."""

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    statuses: list[str] = []
    # 1차 401(revoke) → 2차 성공(재발급된 token 으로 valid 회복).
    transport = FakeTransport(
        errors=[TransportError("agent heartbeat HTTP error", status_code=401), None]
    )
    reporter = HeartbeatReporter(
        _IDENTITY,
        transport=transport,
        sleep=sleep,
        stop_event=stop,
        on_status=statuses.append,
    )

    reporter.run()

    assert len(transport.calls) == 2
    # revoked → valid 두 전이가 순서대로 한 번씩만 통지된다(상태 변화 시에만).
    assert statuses == [TOKEN_STATUS_REVOKED, TOKEN_STATUS_VALID]
    # 최종 상태는 valid 로 회복 · 재등록 불필요 · 성공 결과 보유.
    assert reporter.token_status == TOKEN_STATUS_VALID
    assert reporter.needs_registration is False
    assert reporter.last_result is not None


def test_send_heartbeat_non_list_commands_parse_to_empty():
    """AC1 응답 파싱 — 서버가 ``commands`` 를 list 가 아닌 형태로 주면 안전하게 ``[]`` 로
    파싱한다(malformed 응답 방어 — list/누락만 아니라 비-list 분기도 커버)."""

    response = {
        "server_time": "2026-06-13T01:00:00Z",
        "config_version": "cfg-fake-3",
        "commands": {"unexpected": "object-not-list"},
    }
    result = send_heartbeat(_IDENTITY, transport=FakeTransport(response=response))

    assert result.server_time == "2026-06-13T01:00:00Z"
    assert result.config_version == "cfg-fake-3"
    # 비-list commands → 안전 기본값 [].
    assert result.commands == []


def test_heartbeat_url_strips_trailing_slash():
    """URL 정규화 — base_url 끝의 ``/`` 가 제거되어 ``//v1/...`` 같은 이중 슬래시가 안 생긴다."""

    transport = FakeTransport()

    send_heartbeat(_IDENTITY, transport=transport, base_url="https://srv.test/")

    url, _body, _headers = transport.calls[0]
    assert url == "https://srv.test" + HEARTBEAT_PATH


def test_reporter_stop_method_halts_running_loop():
    """공개 ``stop()`` 메서드가 동작 중인 루프를 정지시킨다(테스트가 event 를 직접 set 하는
    기존 케이스와 달리 ``stop()`` 자체를 호출 — thread-safe 정지 경로 커버)."""

    stop = threading.Event()
    transport = FakeTransport()
    box: dict[str, HeartbeatReporter] = {}
    sleep_calls = {"n": 0}

    def sleep(_seconds: float) -> None:
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            box["reporter"].stop()  # 공개 stop() 으로 정지 요청(event 직접 set 아님).

    reporter = HeartbeatReporter(
        _IDENTITY, transport=transport, sleep=sleep, stop_event=stop
    )
    box["reporter"] = reporter

    reporter.run()

    # report → sleep(1) → report → sleep(2)=stop() → 다음 루프 top 가드에서 종료.
    assert len(transport.calls) == 2
    assert stop.is_set()
