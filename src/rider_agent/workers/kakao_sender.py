"""KakaoSenderWorker — FIFO 단일-세션 직렬 전송 + 정확한 채팅방 검증 매핑 (Story 4.6 / P3-06, FR-15·25).

이 모듈이 책임지는 것(범위 — 직렬 큐잉 + 실패 매핑 + 상태 노출 + 라우팅/배선 primitive 만):

* :class:`KakaoSenderWorker` — stdlib :class:`queue.Queue`(FIFO) + **단일 소비자 thread** 로
  Kakao 전송을 **같은 Windows 세션에서 한 번에 하나씩** 직렬 처리한다(ADD-15 금지행위 = "같은
  세션에서 두 전송을 병렬로"). 모든 외부 부작용(전송 ``send``·시간 ``sleep``/``now``·정지
  ``stop_event``·``log``)을 주입 가능하게 해 실 KakaoTalk·실 thread 장기 대기·실 시계 없이
  결정적으로 검증한다.
* **정확한 방 검증 = 재사용(재구현 금지).** 항목마다 :func:`~rider_crawl.sender.send_kakao_text`
  (고유 제목 exact-match 선택·입력 동등성/전송 후 확인·ambiguous 마킹)를 **그대로 호출**하고,
  워커는 그 예외를 흡수해 운영 어휘로 **매핑**만 한다 — ``KakaoUnsafeSelectionError``(방명 중복/
  모호·창 스캔 불가) → ``KAKAO_FAILURE`` + 하위 사유 ``kakao_ambiguous_room``; 그 외
  ``KakaoSendError`` → ``KAKAO_FAILURE``(``ambiguous`` 면 unconfirmed, **재시도/재-enqueue 안
  함** — 같은 메시지 이중 전송 방지). 검증 없는 별도 전송 경로를 신설하지 않는다(fail-closed).
* **제한/best-effort 운영 상태.** :meth:`KakaoSenderWorker.kakao_status` 가 heartbeat
  ``kakao_status`` 소스로 **집계 수치만**(``enabled``/``queue_depth``/``queue_lag_seconds``/
  ``sent``/``failed``/``last_error_code``) 노출한다 — **방명·메시지 본문·raw 진단은 넣지 않는다**
  (NFR-9/ADD-15). 실패를 다른 방/채널로 자동 재전송하지 않는다(기본 경로는 ``send_kakao_text``
  직접 호출 — ``dispatch_text_message`` messenger 라우팅 미사용).
* **배선 헬퍼.** :func:`build_execute_job` 는 ``KAKAO_SEND`` job 을 워커로, 그 외 type 은 기존
  executor 로 보내는 얇은 라우터다. :func:`start_kakao_sender_worker_if_enabled` 는
  architecture-contract startup(94) 진입으로, capability 가 활성일 때만 소비자 thread 를 띄운다.

소유 분리(스코프 경계):

* **실제 KakaoTalk PC 앱 UI 자동화·창 스캔·키 입력은 한 줄도 새로 짜지 않는다** —
  :func:`~rider_crawl.sender.send_kakao_text` 재사용. ``rider_crawl`` 은 0 줄 변경.
* **서버 측 ``KAKAO_SEND`` job 생성/queue·``kakao_status`` 수신/저장·``messenger_channels``
  등록 검증·Admin kakao lag runbook 표시는 Epic 5 소유.** 본 모듈은 client 워커 + 주입 fake.
* ``error_code`` 는 ``rider_server.domain.states.FailureCategory.KAKAO_FAILURE`` 값과 정합하는
  **평문 상수**다 — ``rider_server`` 를 직접 import 하면 단방향 가드 위반이라 값만 베끼고, **새
  카테고리를 추가하거나 "정확히 N개" lock 을 두지 않는다**(``kakao_ambiguous_room`` 은 카테고리가
  아니라 하위 사유).

자기(own) 코드는 **순수 동기**이고 ``rider_crawl``/자기 패키지만 import 한다(역방향/
``rider_server`` import 0, ``asyncio`` 0) — 4.1 의 AST 가드가 ``workers/`` 하위까지 자동 검사한다.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from rider_crawl.config import AppConfig
from rider_crawl.redaction import redact

from rider_agent.heartbeat import CAPABILITY_KAKAO_SEND, DEFAULT_CAPABILITIES
from rider_agent.job_loop import (
    ClaimedJob,
    JobResult,
    default_execute_job,
    make_failure_result,
    make_success_result,
)
from rider_agent.reuse import (
    KakaoSendError,
    KakaoUnsafeSelectionError,
    kakao_login_available,
    send_kakao_text,
)

# ── 운영 어휘 — **평문 상수**(rider_server 직접 import 금지), enum/"정확히 N개" lock 금지.
# job-level error_code 는 FailureCategory.KAKAO_FAILURE 값과 정합한다. kakao_ambiguous_room/
# unconfirmed 는 **카테고리가 아니라 하위 사유**(metrics.kakao_outcome)로 구별한다 — 새 카테고리를
# 추가하면 rider_server 의 "정확히 7 멤버" lock 이 깨진다(memory: enum-member-count-locks).
# [Source: rider_server/domain/states.py(183·165-184)]
ERROR_KAKAO_FAILURE = "KAKAO_FAILURE"

# kakao 전송 결과 하위 사유(metrics.kakao_outcome) — 평문 상수, "정확히 N개" lock 금지.
KAKAO_OUTCOME_SENT = "kakao_sent"
KAKAO_OUTCOME_FAILURE = "kakao_failure"
KAKAO_OUTCOME_AMBIGUOUS_ROOM = "kakao_ambiguous_room"  # 방명 중복/모호·창 스캔 불가
KAKAO_OUTCOME_UNCONFIRMED = "kakao_unconfirmed"  # Enter 눌렀으나 결과 미확인(이중 전송 방지)

# 서버(Epic 5) 미확정 payload 에서 room/message 를 방어적으로 추출할 때 수용하는 키.
_ROOM_KEYS = ("kakao_room_name", "room_name")
_MESSAGE_KEYS = ("message", "text")

# 기본 진단 로그 경로(send_kakao_text 가 config.log_dir 에 kakao_diagnostics.log 를 남긴다).
DEFAULT_KAKAO_LOG_DIR = Path("logs")

# KakaoTalk 로그인/세션 probe 캐시 TTL(초). heartbeat 마다 창을 스캔하면 비용·포커스 경합이
# 있으므로 결과를 잠시 캐시한다(매 heartbeat 가 아니라 이 간격으로만 실제 probe).
DEFAULT_SESSION_PROBE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class KakaoSendRequest:
    """단일 Kakao 전송 작업 항목 — **내부 전용**(server 로 내보내지 않는다).

    ``room_name``/``message`` 는 민감 표면이라 heartbeat ``kakao_status``·로그·예외·job 결과
    어디에도 **평문으로 노출하지 않는다**(NFR-9/ADD-15). ``job_id`` 는 작업 식별자(secret 아님).
    """

    job_id: str
    room_name: str
    message: str


class _SendTicket:
    """``enqueue`` 가 돌려주는 완료 티켓 — 소비자 thread 가 결과를 채우면 깨운다.

    ``execute_job`` 어댑터가 :meth:`wait` 로 결과(JobResult)를 받아 ``complete_job`` 보고
    경로에 태운다. ``threading.Event`` 라 실 대기 없이 결정적으로 깨운다(테스트는 소비자 thread
    가 즉시 처리).
    """

    __slots__ = ("_done", "result")

    def __init__(self) -> None:
        self._done = threading.Event()
        self.result: JobResult | None = None

    def set_result(self, result: JobResult) -> None:
        self.result = result
        self._done.set()

    def wait(self, timeout: float | None = None) -> JobResult | None:
        self._done.wait(timeout)
        return self.result


def default_build_kakao_config(
    *, room_name: str, log_dir: Path = DEFAULT_KAKAO_LOG_DIR, **_ignored: Any
) -> AppConfig:
    """``send_kakao_text`` 가 받을 ``AppConfig`` 호환 객체를 room_name 으로 구성한다(주입 seam).

    ``send_kakao_text`` 는 ``kakao_chat_name``(전송 대상 방명)과 ``log_dir``(진단 로그 위치)만
    실질적으로 쓰므로 그 둘을 채우고 나머지 필수 필드는 안전 기본값으로 둔다. 서버(Epic 5)가
    payload 형식을 확정하면 호출자가 더 풍부한 ``build_config`` 를 주입할 수 있다(4.5
    ``BrowserProfileManager.build_config`` 선례). [Source: src/rider_crawl/sender.py(312-336)]
    """

    return AppConfig(
        coupang_eats_url="",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="",
        browser_user_data_dir=Path("."),
        headless=False,
        kakao_chat_name=room_name,
        log_dir=Path(log_dir),
        send_enabled=True,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )


def request_from_job(job: ClaimedJob) -> KakaoSendRequest | None:
    """``KAKAO_SEND`` job payload 에서 room/message 를 **방어적으로** 추출한다(fail-closed).

    서버(Epic 5)가 어떤 키로 줄지 미확정이라 흔한 키(``kakao_room_name``/``room_name``,
    ``message``/``text``)를 수용하되, 둘 중 하나라도 비-문자열/빈 값이면 ``None`` 을 돌려
    호출자가 임의 전송 없이 ``KAKAO_FAILURE`` 로 종결하게 한다. [Source: data-api-contract.md(30·69)]
    """

    payload = _raw_payload(job)
    room = _first_str(payload, _ROOM_KEYS)
    message = _first_str(payload, _MESSAGE_KEYS)
    if room is None or message is None:
        return None
    return KakaoSendRequest(job_id=job.job_id, room_name=room, message=message)


def _raw_payload(job: ClaimedJob) -> dict[str, Any]:
    raw = dict(job.payload or {})
    nested = raw.get("payload")
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update(raw)
        return merged
    return raw


def _first_str(payload: dict[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class KakaoSenderWorker:
    """FIFO 단일-세션 직렬 Kakao 전송 워커(순수 동기·best-effort·방 검증 reuse).

    단일 소비자 thread 가 stdlib :class:`queue.Queue` 에서 enqueue 순서대로 한 번에 하나씩
    꺼내 ``send`` 를 호출한다 — 이것이 곧 **세션 직렬화 장치**라 같은 세션에서 두 전송이 겹치지
    않는다(ADD-15). 카운터/큐 메타는 heartbeat thread 가 :meth:`kakao_status` 로 동시 읽으므로
    ``threading.Lock`` 으로 보호한다.

    실패는 다른 방/채널로 자동 재전송하지 않고(best-effort) 실패로 종결·집계만 한다.
    ``ambiguous`` (Enter 눌렀으나 미확인) 전송은 재-enqueue/재시도하지 않는다(이중 전송 방지).
    """

    def __init__(
        self,
        *,
        send: Callable[..., Any] = send_kakao_text,
        build_config: Callable[..., Any] = default_build_kakao_config,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        stop_event: threading.Event | None = None,
        log: Callable[[str], None] | None = None,
        capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
        submit_timeout: float | None = None,
        session_probe: Callable[[], bool | None] | None = None,
        session_probe_ttl_seconds: float = DEFAULT_SESSION_PROBE_TTL_SECONDS,
    ) -> None:
        self._send = send
        self._build_config = build_config
        self._sleep = sleep
        self._now = now
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        #: KakaoTalk 로그인/세션 probe(주입 가능, best-effort). None 이면 신호 미수집.
        self._session_probe = session_probe
        self._session_probe_ttl = session_probe_ttl_seconds
        #: probe 결과 캐시(값, 측정 시각) — heartbeat 마다 스캔하지 않게 TTL 캐시.
        self._session_cache: bool | None = None
        self._session_cache_at: float | None = None
        self._log = log
        self._capabilities = tuple(capabilities)
        self._submit_timeout = submit_timeout
        #: 활성 여부 = capability 에 KAKAO_SEND 포함(crawler-only 노드면 비활성).
        self._enabled = CAPABILITY_KAKAO_SEND in self._capabilities
        #: FIFO 큐 — 소비자 thread 가 한 번에 하나씩 꺼낸다.
        self._queue: "queue.Queue[Any]" = queue.Queue()
        #: 대기 항목의 enqueue 시각(FIFO) — queue lag/depth 계산용. lock 보호.
        self._lock = threading.Lock()
        self._pending_ts: list[float] = []
        self._sent = 0
        self._failed = 0
        self._last_error_code: str | None = None
        self._thread: threading.Thread | None = None

    # ── 공개 상태 ─────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def thread(self) -> threading.Thread | None:
        """소비자 thread(미기동이면 ``None``) — 정리(stop/join) 검증용 read-only 핸들."""

        return self._thread

    def kakao_status(self) -> dict[str, Any]:
        """heartbeat ``kakao_status`` 소스 — **집계 수치만**(방명/메시지/raw 진단 미포함).

        ``queue_lag_seconds`` 는 주입 ``now`` − 가장 오래된 **대기** 항목 enqueue 시각이며 빈
        큐면 0 이다(처리 중 항목은 대기에서 빠진다). thread-safe 스냅샷을 돌려준다.
        ``interactive_session_available`` 는 KakaoTalk 로그인/세션 probe 결과로, 명확히
        판정될 때만 포함한다(미상이면 생략 — 거짓 신호 금지). 활성(``KAKAO_SEND``) 워커에서만
        probe 한다.
        [Source: operations-security-test-contract.md(28·16), architecture.md(215)]
        """

        # probe 는 창 스캔이라 느릴 수 있어 lock 밖에서(캐시 경유) 먼저 구한다.
        session_available = self._session_available_cached()
        with self._lock:
            depth = len(self._pending_ts)
            lag = max(0.0, self._now() - self._pending_ts[0]) if self._pending_ts else 0.0
            status: dict[str, Any] = {
                "enabled": self._enabled,
                "queue_depth": depth,
                "queue_lag_seconds": lag,
                "sent": self._sent,
                "failed": self._failed,
                "last_error_code": self._last_error_code,
            }
        if session_available is not None:
            status["interactive_session_available"] = session_available
        return status

    def _session_available_cached(self) -> bool | None:
        """KakaoTalk 로그인/세션 probe 결과(TTL 캐시·best-effort). 미상이면 ``None``.

        - 비활성(``KAKAO_SEND`` 없음) 워커거나 probe 미주입이면 ``None``(신호 미수집).
        - probe 가 예외를 던지면 흡수하고 ``None``(heartbeat 를 절대 막지 않는다).
        - 같은 결과를 TTL 동안 캐시해 heartbeat 마다 창 스캔하지 않는다.
        """

        if not self._enabled or self._session_probe is None:
            return None
        now = self._now()
        cached_at = self._session_cache_at
        if cached_at is not None and (now - cached_at) < self._session_probe_ttl:
            return self._session_cache
        try:
            value = self._session_probe()
        except Exception:  # noqa: BLE001 - probe 실패가 heartbeat/전송을 막으면 안 된다.
            value = None
        if not isinstance(value, bool):
            value = None
        self._session_cache = value
        self._session_cache_at = now
        return value

    # ── enqueue / 소비자 루프 ─────────────────────────────────────────────────

    def enqueue(self, request: KakaoSendRequest) -> _SendTicket:
        """전송 작업을 큐에 넣고 완료 티켓을 돌려준다(enqueue 시각 기록)."""

        ticket = _SendTicket()
        ts = self._now()
        with self._lock:
            self._pending_ts.append(ts)
        self._queue.put((request, ts, ticket))
        return ticket

    def start(self) -> threading.Thread:
        """소비자 thread 를 daemon 으로 띄운다(이미 살아 있으면 재사용 — 이중 기동 금지)."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            thread = threading.Thread(
                target=self.run, name="rider-agent-kakao-sender", daemon=True
            )
            self._thread = thread
        thread.start()
        return thread

    def run(self) -> None:
        """stop 까지 FIFO 로 한 번에 하나씩 처리한다(thread target — 세션 직렬화 장치)."""

        while not self._stop_event.is_set():
            item = self._queue.get()
            try:
                if item is _STOP:
                    break
                request, _enqueue_ts, ticket = item
                # 처리 시작 → 대기 목록에서 가장 오래된 항목(=FIFO 선두) 제거(lag/depth 반영).
                with self._lock:
                    if self._pending_ts:
                        self._pending_ts.pop(0)
                result = self._process(request)
                ticket.set_result(result)
            finally:
                self._queue.task_done()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        """정지를 요청하고(이벤트 set + sentinel 로 즉시 깨움) 소비자 thread 를 join 한다."""

        self._stop_event.set()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:  # pragma: no cover — 무한 큐라 발생하지 않음(방어).
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)

    # ── execute_job 어댑터(enqueue → 결과 대기 → JobResult) ───────────────────

    def execute(self, job: ClaimedJob, *, timeout: float | None = None) -> JobResult:
        """``KAKAO_SEND`` job 을 워커에 enqueue 하고 결과(JobResult)를 기다려 돌려준다.

        payload 누락은 임의 전송 없이 fail-closed(``KAKAO_FAILURE``). 결과 미수신(워커 미기동/
        타임아웃)도 fail-closed 로 종결한다(이중 성공/hang 방지).
        """

        request = request_from_job(job)
        if request is None:
            return self._fail_closed(
                KAKAO_OUTCOME_FAILURE, "kakao job missing room/message payload"
            )
        ticket = self.enqueue(request)
        result = ticket.wait(timeout if timeout is not None else self._submit_timeout)
        if result is None:
            return self._fail_closed(
                KAKAO_OUTCOME_FAILURE, "kakao send produced no result"
            )
        return result

    # ── 단일 항목 처리(방 검증 reuse + 실패 매핑) ──────────────────────────────

    def _process(self, request: KakaoSendRequest) -> JobResult:
        try:
            config = self._build_config(room_name=request.room_name)
            # 정확한 방 검증/UI 자동화는 send_kakao_text 가 수행(재구현 0). 기본 경로는 Kakao
            # 직접 전송이라 실패가 다른 방/채널(Telegram 등)로 새지 않는다(AC3.2).
            self._send(config, request.message)
        except KakaoUnsafeSelectionError as exc:
            # 방명 중복/모호·창 스캔 불가 → 보내지 않고 ambiguous-room 하위 사유로 surfacing.
            return self._failure(KAKAO_OUTCOME_AMBIGUOUS_ROOM, request, exc)
        except KakaoSendError as exc:
            # Enter 눌렀으나 미확인이면 unconfirmed(빠른 재시도 금지) — 재-enqueue 하지 않는다.
            outcome = (
                KAKAO_OUTCOME_UNCONFIRMED
                if getattr(exc, "ambiguous", False)
                else KAKAO_OUTCOME_FAILURE
            )
            return self._failure(outcome, request, exc)
        except Exception as exc:  # noqa: BLE001 — best-effort: 단발 실패가 소비자 thread 를 죽이지 않는다.
            return self._failure(KAKAO_OUTCOME_FAILURE, request, exc)
        return self._success()

    def _success(self) -> JobResult:
        with self._lock:
            self._sent += 1
        return make_success_result(metrics={"kakao_outcome": KAKAO_OUTCOME_SENT})

    def _failure(
        self, outcome: str, request: KakaoSendRequest, exc: BaseException
    ) -> JobResult:
        with self._lock:
            self._failed += 1
            self._last_error_code = ERROR_KAKAO_FAILURE
        if self._log is not None:
            # outcome 은 고정 하위 사유, job_id 는 식별자 — raw 방명/메시지/예외 본문은 싣지
            # 않는다(redact 는 운영 식별자인 방명을 자유 텍스트에서 가리지 못하므로 처음부터
            # 넣지 않는다). [Source: src/rider_crawl/redaction.py(130-153·181-191)]
            self._log(redact(f"kakao send {outcome} (job {request.job_id})"))
        # 실패 메시지는 고정 사유만 담고 raw 예외 본문(send_kakao_text 진단=방명 포함)을 싣지
        # 않는다 — make_failure_result 가 redact 통과시키지만 free-text redact 는 방명을 가리지
        # 못한다(가드 #3). 하위 사유는 metrics.kakao_outcome 으로 구별한다.
        return make_failure_result(
            ERROR_KAKAO_FAILURE,
            f"kakao send failed ({outcome})",
            metrics={"kakao_outcome": outcome},
        )

    def _fail_closed(self, outcome: str, message: str) -> JobResult:
        with self._lock:
            self._failed += 1
            self._last_error_code = ERROR_KAKAO_FAILURE
        return make_failure_result(
            ERROR_KAKAO_FAILURE, message, metrics={"kakao_outcome": outcome}
        )


#: 소비자 루프를 즉시 깨워 정지시키는 sentinel(고유 객체 — 어떤 항목과도 구별).
_STOP = object()


# ── execute_job 라우터 + startup 배선 ─────────────────────────────────────────


def build_execute_job(
    *,
    kakao_worker: KakaoSenderWorker,
    fallback: Callable[[ClaimedJob], JobResult] = default_execute_job,
    timeout: float | None = None,
) -> Callable[[ClaimedJob], JobResult]:
    """``KAKAO_SEND`` job 은 워커로, 그 외 type 은 ``fallback`` 으로 보내는 얇은 라우터.

    ``fallback`` 은 기존 ``execute_job``(기본 :func:`~rider_agent.job_loop.default_execute_job`
    또는 주입 워커)이라 다른 type 용 빈 stub 워커를 만들지 않는다(KAKAO_SEND 만 가로챈다).
    [Source: src/rider_agent/job_loop.py(234-245), architecture-contract.md(128)]
    """

    def _execute(job: ClaimedJob) -> JobResult:
        if job.type == CAPABILITY_KAKAO_SEND:
            return kakao_worker.execute(job, timeout=timeout)
        return fallback(job)

    return _execute


def start_kakao_sender_worker_if_enabled(
    *,
    capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
    enabled: bool | None = None,
    send: Callable[..., Any] | None = None,
    build_config: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    stop_event: threading.Event | None = None,
    log: Callable[[str], None] | None = None,
    submit_timeout: float | None = None,
    session_probe: Callable[[], bool | None] | None = kakao_login_available,
) -> KakaoSenderWorker | None:
    """활성 조건일 때만 :class:`KakaoSenderWorker` 를 만들어 소비자 thread 를 띄운다(startup 진입).

    활성 조건은 ``capabilities`` 에 ``KAKAO_SEND`` 포함(또는 명시 ``enabled``)이다 — 비활성
    (crawler-only 4.7 노드)이면 ``None`` 을 돌려 ``kakao_status`` 가 4.3 기본 ``"disabled"`` 로
    남게 한다(무회귀). 미주입 ``send``/``build_config`` 는 안전 기본값(``send_kakao_text``/
    :func:`default_build_kakao_config`)을 쓴다. 정리(``stop()``+join)는 호출자(``run_agent``)가
    한다. **빈 호출/빈 stub 금지 — 실제 thread 를 배선한다.**
    [Source: architecture-contract.md(70·94), src/rider_agent/job_loop.py(775-776)]
    """

    is_enabled = (
        enabled if enabled is not None else (CAPABILITY_KAKAO_SEND in capabilities)
    )
    if not is_enabled:
        return None

    worker = KakaoSenderWorker(
        send=send if send is not None else send_kakao_text,
        build_config=build_config
        if build_config is not None
        else default_build_kakao_config,
        sleep=sleep,
        now=now,
        stop_event=stop_event,
        log=log,
        capabilities=capabilities,
        submit_timeout=submit_timeout,
        session_probe=session_probe,
    )
    worker.start()
    return worker
