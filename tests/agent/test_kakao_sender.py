"""Story 4.6 — KakaoSenderWorker(FIFO 단일-세션 직렬 + 정확한 방 검증 매핑 + kakao_status) 검증.

외부 호출 없음: 실제 KakaoTalk PC 앱/`pyautogui`/`pyperclip`/실 네트워크/실 시계/실 thread
장기 대기 미사용. ``send``/``build_config``/``now``/``sleep``/``stop_event`` 를 모두 주입 fake +
호출 카운터/타임스탬프로 대체해 FIFO 직렬·병렬 입력 금지·방 검증 실패 매핑·queue lag·자동
다른-방 복구 금지·heartbeat 배선·startup 을 결정적으로 검증한다. 값은 명백한 가짜값만
(``room-fake-…``/``msg-fake-…``/``job-fake-…``) — 실 ``chat_id``/한국 휴대폰/이메일/OTP/token
원문 없음(누출 가드).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from rider_agent.heartbeat import (
    CAPABILITY_KAKAO_SEND,
    DEFAULT_CAPABILITIES,
    DEFAULT_KAKAO_STATUS,
    build_heartbeat_payload,
)
from rider_agent.job_loop import (
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCESS,
    ClaimedJob,
    build_agent_components,
    make_success_result,
    run_agent,
)
from rider_agent.reuse import (
    KakaoSendError,
    KakaoUnsafeSelectionError,
    dispatch_text_message,
    send_kakao_text,
)
from rider_agent.secure_store import AgentIdentity, save_agent_identity
from rider_agent.workers.kakao_sender import (
    ERROR_KAKAO_FAILURE,
    KAKAO_OUTCOME_AMBIGUOUS_ROOM,
    KAKAO_OUTCOME_FAILURE,
    KAKAO_OUTCOME_SENT,
    KAKAO_OUTCOME_UNCONFIRMED,
    KakaoSendRequest,
    KakaoSenderWorker,
    build_execute_job,
    default_build_kakao_config,
    request_from_job,
    start_kakao_sender_worker_if_enabled,
)

# 가짜 식별자/방명/메시지만(누출 가드 — 실제 토큰/방명/연락처 금지).
FAKE_TOKEN = "agtok-fake-kakao-sender-secret"
FAKE_ROOM = "room-fake-1"
FAKE_MESSAGE = "msg-fake-실적-1"

_IDENTITY = AgentIdentity(
    agent_id="agent-fake-1",
    agent_token=FAKE_TOKEN,
    tenant_scope={"tenant": "t-fake"},
    config_version="cfg-fake-1",
)


# ── 주입 fake 들 ─────────────────────────────────────────────────────────────


def _fake_build_config(*, room_name, **_ignored):
    """실 AppConfig 없이 room_name 만 담은 가벼운 config(주입 send 가 받는다)."""

    return {"kakao_chat_name": room_name}


class RecordingSend:
    """fake send — 호출 인자/순서/동시성(중첩)을 기록하고 스크립트대로 예외를 던진다.

    ``exc_factory(message)`` 가 예외를 돌려주면 그 전송은 실패한다(None 이면 성공). 단일 소비자
    thread 가 직렬화 장치이므로 ``max_active`` 는 1 을 넘지 않아야 한다(ADD-15 병렬 입력 금지).
    """

    def __init__(self, *, exc_factory=None):
        self.calls = []  # (config, message)
        self.events = []  # "enter:<msg>" / "exit:<msg>"
        self.active = 0
        self.max_active = 0
        self._exc_factory = exc_factory

    def __call__(self, config, message):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.events.append(f"enter:{message}")
        try:
            self.calls.append((config, message))
            if self._exc_factory is not None:
                exc = self._exc_factory(message)
                if exc is not None:
                    raise exc
        finally:
            self.active -= 1
            self.events.append(f"exit:{message}")


class _FakeTransport:
    """최소 fake transport — post_json 은 빈 응답(실 네트워크 0)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def post_json(self, url, body, *, headers=None) -> dict:
        self.calls.append((url, body, headers))
        return {}


class _FakeStore:
    def __init__(self):
        self._data: dict[str, str] = {}

    def put(self, value, *, ref=""):
        self._data[ref] = value
        return ref

    def resolve(self, ref):
        return self._data.get(ref)


def _request(job_id="job-fake-1", *, room=FAKE_ROOM, message=FAKE_MESSAGE):
    return KakaoSendRequest(job_id=job_id, room_name=room, message=message)


def _kakao_job(job_id="job-fake-1", *, type=CAPABILITY_KAKAO_SEND, payload=None):
    if payload is None:
        payload = {"kakao_room_name": FAKE_ROOM, "message": FAKE_MESSAGE}
    return ClaimedJob(job_id=job_id, type=type, payload=payload)


def _worker(send=None, **kwargs):
    kwargs.setdefault("build_config", _fake_build_config)
    kwargs.setdefault("submit_timeout", 2.0)
    return KakaoSenderWorker(send=send if send is not None else RecordingSend(), **kwargs)


def _wait_until(predicate, *, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_request_from_server_claim_payload_shape() -> None:
    job = ClaimedJob.from_dict(
        {
            "job_id": "job-server-1",
            "type": CAPABILITY_KAKAO_SEND,
            "target_id": "target-1",
            "lease_expires_at": "2026-06-18T00:00:00Z",
            "payload": {"kakao_room_name": FAKE_ROOM, "message": FAKE_MESSAGE},
        }
    )

    request = request_from_job(job)

    assert request == KakaoSendRequest(
        job_id="job-server-1",
        room_name=FAKE_ROOM,
        message=FAKE_MESSAGE,
    )


# ══════════════════════════════════════════════════════════════════════════
# AC1 — FIFO 단일-세션 직렬 + 병렬 입력 금지 + 주입 primitive
# ══════════════════════════════════════════════════════════════════════════


def test_fifo_processing_preserves_enqueue_order():
    send = RecordingSend()
    worker = _worker(send=send)
    worker.start()

    tickets = [worker.enqueue(_request(f"j{i}", message=f"m{i}")) for i in range(5)]
    results = [t.wait(2.0) for t in tickets]
    worker.stop()

    # 처리 순서 == enqueue 순서(FIFO 보존).
    assert [m for (_c, m) in send.calls] == [f"m{i}" for i in range(5)]
    assert all(r is not None and r.status == JOB_STATUS_SUCCESS for r in results)


def test_single_consumer_never_overlaps_sends_under_concurrent_enqueue():
    send = RecordingSend()
    worker = _worker(send=send)
    worker.start()

    tickets: list = []
    lock = threading.Lock()

    def producer(i):
        ticket = worker.enqueue(_request(f"j{i}", message=f"m{i}"))
        with lock:
            tickets.append(ticket)

    threads = [threading.Thread(target=producer, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    for ticket in tickets:
        assert ticket.wait(2.0) is not None
    worker.stop()

    # 같은 세션에서 두 전송이 겹치지 않는다(단일 소비자 = 직렬화 장치).
    assert len(send.calls) == 8
    assert send.max_active == 1
    # enter/exit 가 항상 같은 메시지로 짝지어 닫힌다(중첩 0).
    for k in range(0, len(send.events), 2):
        assert send.events[k].startswith("enter:")
        assert send.events[k + 1].startswith("exit:")
        assert send.events[k].split(":", 1)[1] == send.events[k + 1].split(":", 1)[1]


def test_stop_wakes_consumer_immediately_without_real_wait():
    worker = _worker(send=RecordingSend())
    thread = worker.start()

    worker.stop(join_timeout=2.0)  # sentinel 로 즉시 깨움(실 대기 0)

    assert not thread.is_alive()


def test_start_is_idempotent_no_double_thread():
    worker = _worker(send=RecordingSend())
    t1 = worker.start()
    t2 = worker.start()
    try:
        assert t1 is t2  # 이미 살아 있으면 재기동하지 않는다
    finally:
        worker.stop()


# ══════════════════════════════════════════════════════════════════════════
# AC2 — 정확한 방 검증 재사용 + 실패 매핑(통과 못하면 임의 전송 없이 실패 기록)
# ══════════════════════════════════════════════════════════════════════════


def test_unsafe_selection_maps_to_ambiguous_room_and_does_not_send_elsewhere():
    send = RecordingSend(exc_factory=lambda m: KakaoUnsafeSelectionError("ambiguous (fake)"))
    worker = _worker(send=send)
    worker.start()

    result = worker.enqueue(_request()).wait(2.0)
    worker.stop()

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_KAKAO_FAILURE
    assert result.metrics["kakao_outcome"] == KAKAO_OUTCOME_AMBIGUOUS_ROOM
    # 모호하면 보내지 않고 실패 기록 — 다른 방으로 자동 재전송 0.
    assert len(send.calls) == 1


def test_ambiguous_send_error_is_not_retried_or_requeued():
    send = RecordingSend(
        exc_factory=lambda m: KakaoSendError("enter unconfirmed (fake)", ambiguous=True)
    )
    worker = _worker(send=send)
    worker.start()

    result = worker.enqueue(_request()).wait(2.0)
    worker.stop()

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_KAKAO_FAILURE
    assert result.metrics["kakao_outcome"] == KAKAO_OUTCOME_UNCONFIRMED
    # Enter 눌렀으나 미확인 → 같은 메시지 이중 전송 방지(재-enqueue/재시도 0).
    assert len(send.calls) == 1


def test_pre_send_kakao_error_maps_to_plain_failure():
    send = RecordingSend(
        exc_factory=lambda m: KakaoSendError("focus/clear failed (fake)")
    )
    worker = _worker(send=send)
    worker.start()

    result = worker.enqueue(_request()).wait(2.0)
    worker.stop()

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_KAKAO_FAILURE
    assert result.metrics["kakao_outcome"] == KAKAO_OUTCOME_FAILURE
    assert len(send.calls) == 1


def test_success_returns_success_result():
    send = RecordingSend()
    worker = _worker(send=send)
    worker.start()

    result = worker.enqueue(_request()).wait(2.0)
    worker.stop()

    assert result.status == JOB_STATUS_SUCCESS
    assert result.metrics["kakao_outcome"] == KAKAO_OUTCOME_SENT
    assert worker.kakao_status()["sent"] == 1


def test_unexpected_exception_does_not_kill_consumer_thread():
    # 예상 외 예외도 흡수(best-effort) — 다음 항목을 계속 처리한다.
    send = RecordingSend(
        exc_factory=lambda m: RuntimeError("boom (fake)") if m == "m0" else None
    )
    worker = _worker(send=send)
    worker.start()

    r0 = worker.enqueue(_request("j0", message="m0")).wait(2.0)
    r1 = worker.enqueue(_request("j1", message="m1")).wait(2.0)
    worker.stop()

    assert r0.status == JOB_STATUS_FAILED
    assert r0.error_code == ERROR_KAKAO_FAILURE
    assert r1.status == JOB_STATUS_SUCCESS  # thread 가 죽지 않고 다음 항목 처리


def test_error_code_matches_server_failure_category_value():
    # rider_agent 코드는 rider_server 를 import 하지 않는다 — 테스트에서만 값 정합 확인.
    from rider_server.domain.states import FailureCategory

    assert ERROR_KAKAO_FAILURE == FailureCategory.KAKAO_FAILURE.value == "KAKAO_FAILURE"


# ══════════════════════════════════════════════════════════════════════════
# AC3 — kakao_status(전송량 + queue lag) + 자동 다른-방 복구 금지(best-effort)
# ══════════════════════════════════════════════════════════════════════════


def test_queue_lag_and_depth_are_deterministic_with_injected_now():
    clock = {"t": 100.0}
    worker = _worker(send=RecordingSend(), now=lambda: clock["t"])
    # 소비자 미기동 → 항목이 큐에 머문다(결정적 lag 계산).

    empty = worker.kakao_status()
    assert empty["queue_depth"] == 0
    assert empty["queue_lag_seconds"] == 0.0  # 빈 큐 = 0

    worker.enqueue(_request("j1"))  # enqueue_ts = 100
    worker.enqueue(_request("j2"))  # enqueue_ts = 100
    clock["t"] = 130.0

    status = worker.kakao_status()
    assert status["queue_depth"] == 2
    assert status["queue_lag_seconds"] == 30.0  # now - 가장 오래된 대기 항목


def test_status_aggregates_sent_failed_and_last_error_code():
    send = RecordingSend(
        exc_factory=lambda m: KakaoSendError("fail (fake)") if m == "bad" else None
    )
    worker = _worker(send=send)
    worker.start()

    worker.enqueue(_request("j1", message="ok")).wait(2.0)
    worker.enqueue(_request("j2", message="bad")).wait(2.0)
    status = worker.kakao_status()
    worker.stop()

    assert status["sent"] == 1
    assert status["failed"] == 1
    assert status["last_error_code"] == ERROR_KAKAO_FAILURE
    assert status["enabled"] is True


def test_status_includes_interactive_session_available_when_probe_true():
    worker = _worker(session_probe=lambda: True)
    worker.kakao_status()
    assert _wait_until(
        lambda: worker.kakao_status().get("interactive_session_available") is True
    )
    assert worker.kakao_status()["interactive_session_available"] is True


def test_status_includes_interactive_session_available_when_probe_false():
    # 카톡 미로그인(probe False) → 신호가 명시적으로 False 로 노출된다(대시보드 경고 근거).
    worker = _worker(session_probe=lambda: False)
    worker.kakao_status()
    assert _wait_until(
        lambda: worker.kakao_status().get("interactive_session_available") is False
    )
    assert worker.kakao_status()["interactive_session_available"] is False


def test_status_omits_session_signal_when_probe_unknown():
    # probe 가 None(미상: pywinauto 미설치/앱 미실행 등)이면 키를 넣지 않는다(거짓 신호 금지).
    worker = _worker(session_probe=lambda: None)
    assert "interactive_session_available" not in worker.kakao_status()


def test_status_omits_session_signal_when_no_probe():
    worker = _worker(session_probe=None)
    assert "interactive_session_available" not in worker.kakao_status()


def test_status_omits_session_signal_when_kakao_disabled():
    # 비활성(KAKAO_SEND 없음) 워커는 probe 하지 않는다 — 신호 미수집.
    calls = []

    def probe():
        calls.append(1)
        return True

    worker = _worker(capabilities=("CRAWL_BAEMIN",), session_probe=probe)
    status = worker.kakao_status()
    assert "interactive_session_available" not in status
    assert calls == []  # probe 호출 자체가 없다


def test_status_session_probe_is_cached_within_ttl():
    # heartbeat 마다 창을 스캔하지 않게 TTL 동안 결과를 캐시한다.
    calls = []
    clock = {"t": 1000.0}

    def probe():
        calls.append(1)
        return True

    worker = _worker(session_probe=probe, session_probe_ttl_seconds=30.0, now=lambda: clock["t"])
    worker.kakao_status()
    assert _wait_until(lambda: calls == [1])
    worker.kakao_status()
    assert calls == [1]  # TTL 안에서는 1회만 실제 probe

    clock["t"] = 1031.0  # TTL 경과
    worker.kakao_status()
    assert _wait_until(lambda: calls == [1, 1])
    assert calls == [1, 1]  # 다시 1회 probe


def test_status_session_probe_unknown_result_is_not_cached():
    values = [None, False]
    calls = []

    def probe():
        calls.append(1)
        return values.pop(0)

    worker = _worker(session_probe=probe)
    worker.kakao_status()
    assert _wait_until(lambda: calls == [1])
    assert "interactive_session_available" not in worker.kakao_status()
    assert _wait_until(lambda: calls == [1, 1])
    assert _wait_until(
        lambda: worker.kakao_status().get("interactive_session_available") is False
    )


def test_status_session_probe_cache_expires_when_clock_moves_backward():
    values = [True, False]
    clock = {"t": 1000.0}
    calls = []

    def probe():
        calls.append(1)
        return values.pop(0)

    worker = _worker(session_probe=probe, now=lambda: clock["t"])
    worker.kakao_status()
    assert _wait_until(
        lambda: worker.kakao_status().get("interactive_session_available") is True
    )

    clock["t"] = 990.0
    assert "interactive_session_available" not in worker.kakao_status()
    assert _wait_until(lambda: calls == [1, 1])
    assert _wait_until(
        lambda: worker.kakao_status().get("interactive_session_available") is False
    )


def test_status_does_not_block_on_slow_session_probe():
    started = threading.Event()
    release = threading.Event()
    done = threading.Event()
    result: dict[str, object] = {}

    def slow_probe():
        started.set()
        release.wait(1.0)
        return True

    worker = _worker(session_probe=slow_probe)

    def collect_status() -> None:
        result["status"] = worker.kakao_status()
        done.set()

    thread = threading.Thread(target=collect_status)
    thread.start()
    assert started.wait(0.5)
    assert done.wait(0.1)
    release.set()
    thread.join(1.0)

    assert "interactive_session_available" not in result["status"]


def test_status_session_probe_failure_is_swallowed():
    # probe 가 예외를 던져도 heartbeat 가 죽지 않고 신호만 생략된다.
    def boom():
        raise RuntimeError("probe blew up")

    worker = _worker(session_probe=boom)
    status = worker.kakao_status()  # 예외 없이 반환
    assert "interactive_session_available" not in status


def test_start_kakao_sender_worker_wires_real_login_probe():
    # 프로덕션 startup 은 실제 로그인 probe 를 배선한다(테스트 기본은 None 이라 무회귀).
    from rider_agent.reuse import kakao_login_available

    worker = start_kakao_sender_worker_if_enabled(
        capabilities=(CAPABILITY_KAKAO_SEND,),
        build_config=_fake_build_config,
    )
    try:
        assert worker is not None
        assert worker._session_probe is kakao_login_available
    finally:
        if worker is not None:
            worker.stop()


def test_default_send_path_is_send_kakao_text_not_messenger_routing():
    # 기본 전송 경로는 Kakao 직접 전송(send_kakao_text) — messenger 라우팅(dispatch_text_message,
    # Telegram 가능)을 쓰지 않는다(AC3.2 자동 다른-채널 복구 금지).
    worker = KakaoSenderWorker(build_config=_fake_build_config)
    assert worker._send is send_kakao_text
    assert worker._send is not dispatch_text_message


def test_failure_does_not_auto_resend_to_another_room():
    send = RecordingSend(exc_factory=lambda m: KakaoUnsafeSelectionError("dup (fake)"))
    worker = _worker(send=send)
    worker.start()

    worker.enqueue(_request("j1", room=FAKE_ROOM)).wait(2.0)
    worker.stop()

    # 실패해도 다른 방으로 추가 전송하지 않는다(정확히 1회 시도, 그 방만).
    assert len(send.calls) == 1
    assert send.calls[0][0] == {"kakao_chat_name": FAKE_ROOM}


# ══════════════════════════════════════════════════════════════════════════
# AC4 — execute_job 라우팅 + heartbeat kakao_status 배선 + startup + 비노출
# ══════════════════════════════════════════════════════════════════════════


def test_build_execute_job_routes_kakao_to_worker_and_others_to_fallback():
    send = RecordingSend()
    worker = _worker(send=send)
    worker.start()
    fallback_jobs: list = []

    def fallback(job):
        fallback_jobs.append(job)
        return make_success_result()

    execute = build_execute_job(kakao_worker=worker, fallback=fallback, timeout=2.0)

    kakao_result = execute(_kakao_job())
    other_job = ClaimedJob(job_id="j2", type="CRAWL_BAEMIN")
    other_result = execute(other_job)
    worker.stop()

    assert kakao_result.status == JOB_STATUS_SUCCESS
    assert len(send.calls) == 1  # KAKAO_SEND 만 워커로
    assert fallback_jobs == [other_job]  # 그 외 type 은 기존 executor
    assert other_result.status == JOB_STATUS_SUCCESS


def test_build_execute_job_fail_closed_on_missing_payload():
    send = RecordingSend()
    worker = _worker(send=send)  # 소비자 미기동(전송 시도조차 없어야 함)
    execute = build_execute_job(kakao_worker=worker)

    result = execute(_kakao_job(payload={}))  # room/message 누락

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_KAKAO_FAILURE
    assert len(send.calls) == 0  # 임의 전송 0(fail-closed)


def test_request_from_job_accepts_alt_keys_and_rejects_missing():
    assert request_from_job(
        ClaimedJob(job_id="j", payload={"kakao_room_name": "r", "message": "m"})
    ) == KakaoSendRequest(job_id="j", room_name="r", message="m")
    # 대체 키(room_name/text)도 수용.
    assert request_from_job(
        ClaimedJob(job_id="j", payload={"room_name": "r2", "text": "m2"})
    ) == KakaoSendRequest(job_id="j", room_name="r2", message="m2")
    # 누락/빈 값/비-dict 는 fail-closed(None).
    assert request_from_job(ClaimedJob(job_id="j", payload={"message": "m"})) is None
    assert request_from_job(ClaimedJob(job_id="j", payload={"room_name": "r"})) is None
    assert (
        request_from_job(ClaimedJob(job_id="j", payload={"room_name": "", "message": "m"}))
        is None
    )


def test_start_kakao_sender_worker_starts_thread_when_capable():
    worker = start_kakao_sender_worker_if_enabled(
        capabilities=DEFAULT_CAPABILITIES,
        send=RecordingSend(),
        build_config=_fake_build_config,
    )
    assert worker is not None
    assert worker.thread is not None and worker.thread.is_alive()

    worker.stop()
    assert not worker.thread.is_alive()  # run_agent 종료 패턴(stop + join)


def test_start_kakao_sender_worker_disabled_when_not_capable():
    caps = tuple(c for c in DEFAULT_CAPABILITIES if c != CAPABILITY_KAKAO_SEND)
    worker = start_kakao_sender_worker_if_enabled(capabilities=caps)
    assert worker is None  # crawler-only 노드면 미기동 → kakao_status 는 "disabled"


def test_build_components_wires_kakao_status_into_reporter():
    worker = _worker(send=RecordingSend())

    runner, reporter = build_agent_components(
        _IDENTITY, transport=_FakeTransport(), kakao_status_provider=worker.kakao_status
    )

    # reporter provider 가 워커 상태를 반영(4.5 browser_profiles 배선과 동형).
    assert reporter._kakao_status_provider() == worker.kakao_status()
    payload = build_heartbeat_payload(
        _IDENTITY, kakao_status_provider=reporter._kakao_status_provider
    )
    assert payload["kakao_status"] == worker.kakao_status()


def test_unwired_kakao_status_stays_disabled_no_regression():
    runner, reporter = build_agent_components(_IDENTITY, transport=_FakeTransport())
    payload = build_heartbeat_payload(
        _IDENTITY, kakao_status_provider=reporter._kakao_status_provider
    )
    assert payload["kakao_status"] == DEFAULT_KAKAO_STATUS  # 미배선 = "disabled"


def test_run_agent_starts_stops_kakao_worker_and_wires_status(tmp_path):
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    stop.set()  # 루프 진입 즉시 정지(결정적).

    summary = run_agent(
        transport=_FakeTransport(),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_kakao_sender=True,
        kakao_send=RecordingSend(),
        kakao_build_config=_fake_build_config,
    )

    assert summary.started is True
    assert summary.kakao_worker is not None
    # kakao_status 가 reporter 에 배선됨.
    assert (
        summary.reporter._kakao_status_provider()
        == summary.kakao_worker.kakao_status()
    )
    # 종료 시 worker.stop()+join 으로 정리됨.
    assert summary.kakao_worker.thread is not None
    assert not summary.kakao_worker.thread.is_alive()


def test_run_agent_without_kakao_sender_keeps_disabled(tmp_path):
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
    )

    assert summary.kakao_worker is None  # 무회귀(기본 미기동)
    payload = build_heartbeat_payload(
        _IDENTITY, kakao_status_provider=summary.reporter._kakao_status_provider
    )
    assert payload["kakao_status"] == DEFAULT_KAKAO_STATUS


# ══════════════════════════════════════════════════════════════════════════
# 누출 가드 — raw 방명/메시지/secret 0(payload·result·status·로그)
# ══════════════════════════════════════════════════════════════════════════


def test_no_raw_room_or_message_in_result_or_status_even_if_exc_carries_them():
    # 예외 본문에 방명이 들어 있어도(실 send_kakao_text 진단처럼) 결과/상태에 새지 않는다.
    send = RecordingSend(
        exc_factory=lambda m: KakaoUnsafeSelectionError(
            f"chat_name={FAKE_ROOM} ambiguous, body={FAKE_MESSAGE}"
        )
    )
    worker = _worker(send=send)
    worker.start()
    result = worker.enqueue(_request()).wait(2.0)
    status = worker.kakao_status()
    worker.stop()

    result_blob = json.dumps(
        {
            "error_code": result.error_code,
            "error_message_redacted": result.error_message_redacted,
            "metrics": result.metrics,
            "result_json": result.result_json,
        },
        ensure_ascii=False,
    )
    assert FAKE_ROOM not in result_blob
    assert FAKE_MESSAGE not in result_blob
    status_blob = json.dumps(status, ensure_ascii=False)
    assert FAKE_ROOM not in status_blob
    assert FAKE_MESSAGE not in status_blob


def test_no_raw_room_or_message_in_heartbeat_payload_and_no_token():
    send = RecordingSend()
    worker = _worker(send=send)
    worker.start()
    worker.enqueue(_request()).wait(2.0)

    payload = build_heartbeat_payload(
        _IDENTITY, kakao_status_provider=worker.kakao_status
    )
    worker.stop()

    blob = json.dumps(payload, ensure_ascii=False)
    assert FAKE_ROOM not in blob
    assert FAKE_MESSAGE not in blob
    assert FAKE_TOKEN not in blob  # token 은 payload 본문에 실리지 않는다(헤더만)


def test_log_does_not_leak_raw_room_or_message():
    logs: list[str] = []
    send = RecordingSend(exc_factory=lambda m: KakaoSendError("fail (fake)"))
    worker = _worker(send=send, log=logs.append)
    worker.start()
    worker.enqueue(_request()).wait(2.0)
    worker.stop()

    blob = "\n".join(logs)
    assert FAKE_ROOM not in blob
    assert FAKE_MESSAGE not in blob


# ══════════════════════════════════════════════════════════════════════════
# 도메인 모델 / 기본 build_config sanity
# ══════════════════════════════════════════════════════════════════════════


def test_kakao_send_request_is_frozen():
    request = _request()
    with pytest.raises(Exception):
        request.room_name = "other"  # type: ignore[misc]


def test_default_build_kakao_config_sets_chat_name_and_log_dir():
    config = default_build_kakao_config(room_name="room-fake-x", log_dir=Path("logs-x"))
    assert config.kakao_chat_name == "room-fake-x"
    assert config.log_dir == Path("logs-x")
    assert config.send_enabled is True


# ══════════════════════════════════════════════════════════════════════════
# qa-e2e 보강 — 커버리지 갭(fail-closed 결과 미수신·기본 fallback·enabled override·
#               run_agent 비활성 노드·정리 idempotency·드레인 후 lag)
# ══════════════════════════════════════════════════════════════════════════


def test_execute_fail_closed_when_no_result_within_timeout():
    # AC4 fail-closed: 소비자 미기동/타임아웃으로 결과(JobResult)를 못 받으면 hang/이중 성공
    # 없이 KAKAO_FAILURE 로 종결한다(임의 전송 0). execute() 의 "no result" 분기.
    send = RecordingSend()
    worker = _worker(send=send)  # start() 미호출 → ticket 결과가 채워지지 않는다

    # timeout=0 → Event.wait(0) 가 즉시 None 반환(실 대기 0·결정적): 소비자가 없으니 항상 미수신.
    result = worker.execute(_kakao_job(), timeout=0)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_KAKAO_FAILURE
    assert result.metrics["kakao_outcome"] == KAKAO_OUTCOME_FAILURE
    assert len(send.calls) == 0  # 결과 대기 실패여도 임의 전송 0
    status = worker.kakao_status()
    assert status["failed"] == 1
    assert status["last_error_code"] == ERROR_KAKAO_FAILURE


def test_build_execute_job_default_fallback_rejects_unknown_type():
    # 기본 fallback = default_execute_job: KAKAO_SEND 외 미지원 type 은 UNSUPPORTED_JOB_TYPE
    # 으로 종결되고 워커로 새지 않는다(라우터가 비-kakao 를 삼키지 않음 — fallback 미지정 분기).
    from rider_agent.job_loop import ERROR_UNSUPPORTED_JOB_TYPE

    send = RecordingSend()
    worker = _worker(send=send)  # 미기동 — 비-kakao job 은 워커에 닿지 않아야 함
    execute = build_execute_job(kakao_worker=worker)  # fallback 미지정 = 기본값

    result = execute(ClaimedJob(job_id="j-other", type="CRAWL_BAEMIN"))

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_UNSUPPORTED_JOB_TYPE
    assert len(send.calls) == 0


def test_start_kakao_sender_respects_explicit_enabled_override():
    # enabled 명시 override 가 capability 추론보다 우선한다(양방향 — `enabled if not None` 분기).
    caps_without = tuple(c for c in DEFAULT_CAPABILITIES if c != CAPABILITY_KAKAO_SEND)

    # (a) caps 에 KAKAO_SEND 없어도 enabled=True 면 기동.
    forced_on = start_kakao_sender_worker_if_enabled(
        capabilities=caps_without,
        enabled=True,
        send=RecordingSend(),
        build_config=_fake_build_config,
    )
    assert forced_on is not None
    assert forced_on.thread is not None and forced_on.thread.is_alive()
    forced_on.stop()

    # (b) caps 에 KAKAO_SEND 있어도 enabled=False 면 미기동.
    forced_off = start_kakao_sender_worker_if_enabled(
        capabilities=DEFAULT_CAPABILITIES, enabled=False
    )
    assert forced_off is None


def test_run_agent_start_kakao_sender_but_not_capable_stays_disabled(tmp_path):
    # start_kakao_sender=True 라도 capability 에 KAKAO_SEND 없으면(crawler-only 노드) 워커를
    # 띄우지 않고 kakao_status 는 "disabled" 유지(AC4 무회귀).
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    stop.set()  # 루프 진입 즉시 정지(결정적).
    caps = tuple(c for c in DEFAULT_CAPABILITIES if c != CAPABILITY_KAKAO_SEND)

    summary = run_agent(
        transport=_FakeTransport(),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_kakao_sender=True,
        capabilities=caps,
        kakao_send=RecordingSend(),
        kakao_build_config=_fake_build_config,
    )

    assert summary.kakao_worker is None  # 비활성 노드면 미기동
    payload = build_heartbeat_payload(
        _IDENTITY, kakao_status_provider=summary.reporter._kakao_status_provider
    )
    assert payload["kakao_status"] == DEFAULT_KAKAO_STATUS


def test_stop_is_safe_on_never_started_worker():
    # 미기동 워커에 stop() 을 호출해도 예외 없이 안전하게 종료한다(정리 idempotency — thread None).
    worker = _worker(send=RecordingSend())
    worker.stop()  # _thread 가 None 이어도 안전(sentinel put + join 생략)
    assert worker.thread is None


def test_queue_depth_and_lag_return_to_zero_after_drain():
    # AC3: 처리된 항목은 대기 집합에서 빠진다 — 드레인 후 시계가 진행해도 depth/lag 는 0(결정적).
    clock = {"t": 100.0}
    send = RecordingSend()
    worker = _worker(send=send, now=lambda: clock["t"])
    worker.start()

    assert worker.enqueue(_request("j1")).wait(2.0) is not None
    assert worker.enqueue(_request("j2")).wait(2.0) is not None
    clock["t"] = 200.0  # 대기 항목이 없으면 시계가 진행해도 lag 0
    status = worker.kakao_status()
    worker.stop()

    assert status["queue_depth"] == 0
    assert status["queue_lag_seconds"] == 0.0
    assert status["sent"] == 2
