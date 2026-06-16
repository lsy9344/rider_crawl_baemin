"""배민 auth 상태 감지 + 사람 개입형 재인증 실행자 primitive (Story 4.8 / P3, FR-17·18).

이 모듈이 책임지는 것(범위 — 분류기 + 두 실행자 + bounded 대기 + 얇은 라우터 primitive 만):

* :func:`classify_baemin_auth_state` — reuse seam 의 :class:`BrowserActionRequiredError`
  (로그인/휴대폰 인증 필요 신호)와 정상 snapshot 여부를 받아 **평문 auth 상태**
  (``AUTH_REQUIRED``/``ACTIVE``/``UNKNOWN``)로 분류한다. **비-auth 예외**
  (``CdpUnavailableError``/``MissingPerformanceDataError``/``RuntimeError``)는 auth 로
  오분류하지 않는다(파서/연결 문제를 인증 문제로 오인 금지 — fail-closed 는 ``UNKNOWN``).
* :func:`execute_auth_check_job` — ``AUTH_CHECK`` job 실행자. 주입 ``login_probe`` 로 **로그인
  상태만 점검**해 ``ACTIVE``/``AUTH_REQUIRED`` 를 보고한다. **수집/렌더/전송을 호출하지 않는다**
  (fail-closed = 인증으로 막힌 대상에 잘못된 메시지 0, NFR-2).
* :func:`execute_open_auth_browser_job` — ``OPEN_AUTH_BROWSER`` job 실행자. 주입
  ``open_auth_browser`` 로 프로필 브라우저를 **열고**, 주입 ``detect_completion`` 으로 사람이
  완료한 로그인 상태를 **read-only 로 감지**(``AUTH_VERIFIED``)한다. **휴대폰 인증 코드(OTP)를
  취득·자동입력·우회·자동 통과하려 시도하지 않는다(ADD-15 — 이 스토리의 핵심 금지).** 재인증
  대기는 주입 ``now``/``sleep`` + 상한(``max_wait_seconds``/``max_attempts``)으로 **bounded**
  하며, 상한 내 미완료면 ``AUTH_REQUIRED``(사유 ``auth_timeout``)로 **멈춘다**(무한 재시도 금지,
  NFR-4).
* :func:`build_auth_execute_job` — ``AUTH_CHECK``/``OPEN_AUTH_BROWSER`` 를 각 실행자로, 그 외
  type 은 ``fallback`` 으로 보내는 얇은 라우터(4.6 :func:`build_execute_job` 패턴 동형). 기존
  ``execute_job`` 주입점 위에 합성된다 — ``run_agent``/``job_loop`` 0줄(auth 실행자는 stateless
  요청-응답이라 thread 수명 배선이 불필요. 4.6 kakao 와 대조).

소유 분리(스코프 경계):

* **재인증은 "사람이 한다" — 자동화·우회 절대 금지(ADD-15·ops-security-contract 76·90).** 이
  모듈은 ``fetch_latest_verification_code``/``recover_coupang_session_with_email_2fa``(쿠팡 Gmail
  2FA·OTP 자동입력 경로)와 ``pyautogui``/``pywinauto``/``pyperclip`` 류를 **import 도 호출도
  하지 않는다** — 4.1 AST 부정 가드로 영구 고정한다(배민은 OTP 자동화 금지, 쿠팡 2FA 와 정반대
  정책). 프로필을 **열고** 사람-완료를 **감지**만 한다.
* **``rider_crawl`` 0줄·서버 측 ``auth_sessions``/job 생성/queue 는 본 스토리 범위가 아니다.**
  배민 login-page 감지 로직을 ``rider_crawl`` 에 새로 넣지 않고, 기본 probe 도 기존
  ``crawl_snapshot``/``BrowserActionRequiredError`` seam 을 재사용한다.

상태/오류 어휘는 **평문 문자열 상수**다(``rider_server.domain.states`` 의 ``BaeminAuthState``·
``FailureCategory.AUTH_REQUIRED`` 값과 정합) — ``rider_server`` 를 직접 import 하면 단방향 가드
위반이라 **값만 베끼고**(4.5 ``STATE_AUTH_REQUIRED``·4.6 ``ERROR_KAKAO_FAILURE`` 선례) 어떤
enum/"정확히 N개" lock 도 두지 않는다(memory: enum-member-count-locks).

자기(own) 코드는 **순수 동기**이고 stdlib(+``rider_crawl.redaction``·``rider_agent`` 자기
패키지)만 import 한다(역방향/``rider_server`` import 0, ``asyncio`` 0). 실 브라우저/실 시계는
함수 내부 lazy + Windows-gated + 주입 가능이라 ``import rider_agent.auth.baemin_auth`` 가
비-Windows(WSL/CI)에서도 import-safe 하다 — 4.1 의 AST 가드가 ``auth/`` 하위까지 자동 검사한다.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from rider_crawl.redaction import redact

from rider_agent.heartbeat import (
    CAPABILITY_AUTH_CHECK,
    CAPABILITY_OPEN_AUTH_BROWSER,
)
from rider_agent.job_loop import (
    ClaimedJob,
    JobResult,
    default_execute_job,
    make_failure_result,
    make_success_result,
)
from rider_agent.reuse import BrowserActionRequiredError

# ── auth 상태 어휘 — **평문 상수**(BaeminAuthState 값 정합), enum/"정확히 N개" lock 금지.
# rider_server 를 직접 import 하면 단방향 가드 위반이라 값만 베낀다(browser_profile
# ``STATE_AUTH_REQUIRED``·secure_store ``TOKEN_STATUS_*`` 선례). 후속(4.9)이 상태를 늘려도
# 다른 lock 을 깨지 않는다. [Source: rider_server/domain/states.py(53-59)]
AUTH_STATE_UNKNOWN = "UNKNOWN"
AUTH_STATE_ACTIVE = "ACTIVE"
AUTH_STATE_AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_STATE_AUTH_VERIFIED = "AUTH_VERIFIED"
AUTH_STATE_BLOCKED_OR_CAPTCHA = "BLOCKED_OR_CAPTCHA"

# job-level error_code — FailureCategory.AUTH_REQUIRED 값과 정합(평문 상수).
# [Source: rider_server/domain/states.py(180)]
ERROR_AUTH_REQUIRED = "AUTH_REQUIRED"

# 재인증 미완료 사유(평문 상수) — timeout 결과 metrics/result_json 에 실어 운영에 남긴다.
REASON_AUTH_TIMEOUT = "auth_timeout"

# auth-required/verified 진행 이벤트 type(평문 상수). 실제 emit 은 호출자(loop)가 한다.
EVENT_TYPE_AUTH_REQUIRED = "AUTH_REQUIRED"
EVENT_TYPE_AUTH_VERIFIED = "AUTH_VERIFIED"
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"

# ── 재인증 대기 상한(무한 재시도 금지, NFR-4) — 4.5 recover_profile(attempts 3·backoff 1.0)
# 규모를 본뜬 모듈 상수. 운영 실값은 Epic 5 스케줄/lease 와 정합하게 조정 가능(본 스토리는
# bounded 보장만). 테스트는 항상 작은 값 + 주입 now/sleep 으로 결정적 검증한다.
# [Source: src/rider_agent/browser_profile.py(71-73·345-392)]
DEFAULT_MAX_WAIT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_ATTEMPTS = 3


# ── 분류기(auth-required 신호 → 평문 상태) ─────────────────────────────────────


def classify_baemin_auth_state(
    *, snapshot_ok: bool | None = None, error: BaseException | None = None
) -> str:
    """배민 auth 상태를 평문 상수로 분류한다(reuse 신호 소비 — 재구현 0, fail-closed).

    * ``error`` 가 :class:`BrowserActionRequiredError`(reuse seam·로그인/휴대폰 인증 필요
      신호) → :data:`AUTH_STATE_AUTH_REQUIRED`.
    * 그 외 ``snapshot_ok is True``(정상 달성현황 snapshot) → :data:`AUTH_STATE_ACTIVE`.
    * 그 외/모호 → :data:`AUTH_STATE_UNKNOWN`.

    **비-auth 예외는 auth 로 오분류하지 않는다.** 배민 크롤러는 ``BrowserActionRequiredError`` 를
    어디서도 raise 하지 않고(쿠팡만 raise) 로그인 안 됨이 ``MissingPerformanceDataError``/
    ``RuntimeError`` 로 표면화하므로, ``MissingPerformanceDataError``→``AUTH_REQUIRED`` 같은
    광범위 매핑은 금지한다(파서/연결 문제를 인증 문제로 오인 → 잘못된 운영 신호). 그런 예외는
    ``UNKNOWN`` 으로 두고 auth-required 판정은 주입 probe 가 결정한다.
    [Source: src/rider_agent/reuse.py(69), memory/baemin-no-action-required-signal]
    """

    if error is not None and isinstance(error, BrowserActionRequiredError):
        return AUTH_STATE_AUTH_REQUIRED
    if snapshot_ok is True:
        return AUTH_STATE_ACTIVE
    return AUTH_STATE_UNKNOWN


# ── 기본 probe/open/detect (reuse seam 기반 실제 배선) ─────────────────────────
# 실 브라우저/실 crawl import 는 함수 내부 lazy 로 유지한다(import-safety). 새 크롤러/인증
# 우회는 만들지 않고, crawl worker 가 이미 쓰는 job payload → AppConfig 변환과 reuse seam 을
# 그대로 재사용한다.


def _config_from_auth_job(job: ClaimedJob) -> Any:
    from rider_agent.workers.crawl_worker import _build_config, payload_from_job

    payload = payload_from_job(job)
    cdp_url = str((job.payload or {}).get("cdp_url") or "http://127.0.0.1:9222")
    return _build_config(
        payload,
        cdp_url=cdp_url,
        user_data_dir=Path("runtime") / "agent-browser-profiles" / payload.target_id,
    )


def default_login_probe(job: ClaimedJob) -> str:
    """기본 배민 로그인 상태 probe(reuse ``crawl_snapshot`` 기반 read-only 판정).

    정상 snapshot 을 얻으면 ``ACTIVE``, ``BrowserActionRequiredError`` 는 ``AUTH_REQUIRED``,
    그 외 파서/연결류 예외는 인증 문제로 단정하지 않고 ``UNKNOWN`` 으로 둔다.
    """

    from rider_agent import reuse

    config = _config_from_auth_job(job)
    platform = str((job.payload or {}).get("platform") or "baemin").strip().casefold()
    try:
        reuse.crawl_snapshot(config, platform_name=platform or "baemin")
    except BrowserActionRequiredError as exc:
        return classify_baemin_auth_state(error=exc)
    except Exception:
        return AUTH_STATE_UNKNOWN
    return classify_baemin_auth_state(snapshot_ok=True)


def default_open_auth_browser(job: ClaimedJob) -> None:
    """기본 프로필 브라우저 열기.

    ``prepare_chrome`` 를 재사용해 프로필을 **열기만** 한다. OTP 취득·자동입력·우회는 하지 않는다.
    """

    from rider_agent import reuse

    reuse.prepare_chrome(_config_from_auth_job(job), platform_name="Windows")


def default_detect_completion(job: ClaimedJob) -> bool:
    """기본 사람-완료 감지(read-only).

    로그인 완료 여부만 다시 probe 한다. 인증번호를 읽거나 입력·제출하지 않는다.
    """

    return default_login_probe(job) == AUTH_STATE_ACTIVE


# ── AUTH_CHECK 실행자(로그인 상태만 점검·보고 — 수집/전송 0) ────────────────────


def execute_auth_check_job(
    job: ClaimedJob,
    *,
    login_probe: Callable[[ClaimedJob], str] = default_login_probe,
    now: Callable[[], float] = time.time,
    log: Callable[[str], None] | None = None,
) -> JobResult:
    """``AUTH_CHECK`` job — 로그인 상태만 점검해 ``ACTIVE``/``AUTH_REQUIRED`` 를 보고한다.

    주입 ``login_probe(job) -> str``(auth_state 평문 상수)로 상태를 얻어:

    * ``ACTIVE`` → :func:`make_success_result` ``result_json={target_id, auth_state: ACTIVE}``.
    * 그 외(``AUTH_REQUIRED``/``UNKNOWN``/…) → **메시지 생성 없이** auth-required 를 표면화한다
      — :func:`make_success_result` ``result_json={target_id, auth_state}`` (상태 점검은
      "실패"가 아니라 "필요 신호"라 success 결과로 일관, AUTH_CHECK 표면 정본). ``ACTIVE`` 가
      아니면 fail-closed 로 ``AUTH_REQUIRED`` 어휘로 surfacing 한다.

    **수집/렌더/전송을 호출하지 않는다**(``crawl_snapshot``/``render_*``/``send_*`` 미호출) — 이
    실행자는 ``login_probe`` 만 부른다(fail-closed = 인증으로 막힌 대상에 잘못된 메시지 0,
    NFR-2). 보고 표면에는 ``target_id`` + 평문 auth_state 만 — raw 프로필 경로/secret/OTP/휴대폰
    0(NFR-5/8). 자유 텍스트 로그는 고정 메시지 + :func:`redact` 통과.
    [Source: src/rider_agent/job_loop.py(175-207), architecture-contract.md(126)]
    """

    state = login_probe(job)
    target_id = job.target_id
    if state == AUTH_STATE_ACTIVE:
        if log is not None:
            log(redact(f"auth check: ACTIVE (target {target_id})"))
        return make_success_result(
            result_json={"target_id": target_id, "auth_state": AUTH_STATE_ACTIVE}
        )

    # ACTIVE 가 아니면 fail-closed: auth-required 신호로 surfacing(메시지 생성 0).
    if log is not None:
        log(redact(f"auth check: AUTH_REQUIRED (target {target_id})"))
    return make_success_result(
        result_json={"target_id": target_id, "auth_state": AUTH_STATE_AUTH_REQUIRED}
    )


# ── OPEN_AUTH_BROWSER 실행자(열기 + 사람-완료 감지 + bounded timeout) ───────────


def execute_open_auth_browser_job(
    job: ClaimedJob,
    *,
    open_auth_browser: Callable[[ClaimedJob], Any] = default_open_auth_browser,
    detect_completion: Callable[[ClaimedJob], bool] = default_detect_completion,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    log: Callable[[str], None] | None = None,
) -> JobResult:
    """``OPEN_AUTH_BROWSER`` job — 프로필 열기 + 사람-완료 감지 + bounded 재인증 대기.

    흐름: (a) ``open_auth_browser(job)`` 로 프로필 브라우저를 **연다**(정확히 1회), (b)
    ``detect_completion(job) -> bool`` 를 **bounded** polling(주입 ``now``/``sleep``, 상한
    ``max_attempts`` 와 ``max_wait_seconds``)으로 호출, (c) 사람-완료 감지 →
    :func:`make_success_result` ``result_json={target_id, auth_state: AUTH_VERIFIED}`` (작업
    재개 신호), 상한 소진 → ``AUTH_REQUIRED`` + 사유 ``auth_timeout`` 실패 결과로 **멈춘다**
    (전송/메시지 생성 0, 무한 재시도·무한 polling 0 — 4.5 ``recover_profile`` bounded·NFR-4 동형).

    **휴대폰 인증 코드(OTP)를 읽거나 입력·제출하지 않는다(ADD-15).** 프로필을 열고 사람-완료를
    감지만 한다. 사유는 평문 상수, 로그는 고정 메시지 + :func:`redact` 통과(raw 경로/OTP/휴대폰
    비노출). [Source: src/rider_agent/browser_profile.py(337-392), architecture-contract.md(118·127)]
    """

    target_id = job.target_id

    # (a) 프로필 브라우저를 사람용으로 연다(열기만 — OTP 취득·입력·우회 0). 정확히 1회.
    open_auth_browser(job)

    # (b) bounded polling — 주입 now/sleep + 상한(attempts·wall-clock). 무한 대기 0.
    start = now()
    attempts = 0
    while True:
        attempts += 1
        if detect_completion(job):
            # (c) 사람-완료 감지 → AUTH_VERIFIED 로 작업 재개 신호를 표면화한다.
            if log is not None:
                log(redact(f"auth verified by human (target {target_id})"))
            return make_success_result(
                result_json={
                    "target_id": target_id,
                    "auth_state": AUTH_STATE_AUTH_VERIFIED,
                }
            )
        # 상한 점검(다음 polling 전) — attempts 우선(cheap), 그다음 wall-clock.
        if attempts >= max_attempts:
            break
        if (now() - start) >= max_wait_seconds:
            break
        sleep(poll_interval_seconds)

    # 상한 소진: AUTH_VERIFIED 로 가지 않고 AUTH_REQUIRED(auth_timeout)로 멈춘다(전송 0).
    if log is not None:
        log(redact(f"auth re-verification timed out (target {target_id})"))
    return make_failure_result(
        ERROR_AUTH_REQUIRED,
        "auth re-verification not completed within bounded wait",
        result_json={
            "target_id": target_id,
            "auth_state": AUTH_STATE_AUTH_REQUIRED,
            "reason": REASON_AUTH_TIMEOUT,
        },
        metrics={"auth_reason": REASON_AUTH_TIMEOUT},
    )


# ── execute_job 라우터(AUTH_CHECK/OPEN_AUTH_BROWSER → 실행자, 그 외 → fallback) ──


def build_auth_execute_job(
    *,
    login_probe: Callable[[ClaimedJob], str] = default_login_probe,
    open_auth_browser: Callable[[ClaimedJob], Any] = default_open_auth_browser,
    detect_completion: Callable[[ClaimedJob], bool] = default_detect_completion,
    fallback: Callable[[ClaimedJob], JobResult] = default_execute_job,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    log: Callable[[str], None] | None = None,
) -> Callable[[ClaimedJob], JobResult]:
    """``AUTH_CHECK``/``OPEN_AUTH_BROWSER`` 를 각 실행자로, 그 외 type 은 ``fallback`` 으로 보낸다.

    4.6 :func:`~rider_agent.workers.kakao_sender.build_execute_job` 패턴 동형(다른 type 용 빈
    stub 0). ``fallback`` 은 기존 ``execute_job``(기본 :func:`~rider_agent.job_loop.
    default_execute_job` = ``UNSUPPORTED_JOB_TYPE``, 또는 주입 워커)이라 auth 두 type 만
    가로챈다. job-type 매칭은 ``heartbeat.CAPABILITY_AUTH_CHECK``/``CAPABILITY_OPEN_AUTH_BROWSER``
    평문 상수를 재사용한다.

    이 산출물은 기존 ``execute_job`` 주입점 위에 합성된다 —
    ``run_agent(execute_job=build_auth_execute_job(...))`` 또는 ``JobRunner(execute_job=...)`` 로
    실 claim→execute→complete 루프가 auth job 을 라우팅한다. ``run_agent``/``job_loop.py``/
    ``__main__.py`` 는 0줄 변경(auth 실행자는 stateless 라 thread 수명 배선이 불필요 — 4.6 kakao
    와 대조). 미합성이면 기존 ``default_execute_job`` 그대로(4.7 동작 보존, 무회귀).
    [Source: src/rider_agent/workers/kakao_sender.py(393-411), src/rider_agent/job_loop.py(763·855-857)]
    """

    def _execute(job: ClaimedJob) -> JobResult:
        if job.type == CAPABILITY_AUTH_CHECK:
            return execute_auth_check_job(job, login_probe=login_probe, now=now, log=log)
        if job.type == CAPABILITY_OPEN_AUTH_BROWSER:
            return execute_open_auth_browser_job(
                job,
                open_auth_browser=open_auth_browser,
                detect_completion=detect_completion,
                now=now,
                sleep=sleep,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_attempts=max_attempts,
                log=log,
            )
        return fallback(job)

    return _execute
