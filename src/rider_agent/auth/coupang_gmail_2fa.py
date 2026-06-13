"""쿠팡 Gmail 2FA — mailbox 별 token 분리 + mailbox lock + 실패 분류 + bounded 복구 primitive
(Story 4.9 / P3, FR-19, NFR-4·5·8·16, ADD-15).

이 모듈이 책임지는 것(범위 — primitive 4종만, 실제 워커/서버 알림은 미래/Epic 5 소유):

* **mailbox 별 token 분리 helper** (:func:`mailbox_token_ref`/:func:`store_mailbox_token`/
  :func:`resolve_mailbox_token`) — Gmail OAuth token 을 ``mailbox_id`` 단위로 분리 저장한다.
  4.2 :class:`~rider_agent.secure_store.DpapiSecretStore`(Agent-local DPAPI) 를 그대로 끼우고
  ref 만 ``mailbox_id`` 로 keying 한다(새 crypto/store 재발명 0). **서버는 ref(불투명 핸들)만**
  보유하고 token bytes 는 store 에만 둔다 — 두 고객은 **다른 ref** 라 token 을 공유하지 않는다
  (ADD-15·NFR-8·ops:92 "Share Gmail token between customers" = 금지).
* :class:`MailboxLockRegistry` — ``mailbox_id`` 별 :class:`threading.Lock` 발급(같은 id→같은
  객체, 다른 id→독립 lock). 같은 메일함 두 인증 요청을 **직렬화**(겹쳐 실행 0)하고 다른
  메일함은 병렬 허용한다(FR-19·NFR-16). 4.5 ``browser_profile``·4.6 ``kakao_sender`` 등록부
  lock 과 동형(stdlib ``threading`` — 재발명 0).
* :func:`classify_coupang_2fa_outcome` — 복구 결과를 **조치 가능 평문 상태**로 분류한다(순수
  함수, secret 미접근 — bool/예외 타입만 본다): ``recovered is True`` → ``ACTIVE``;
  ``recovered is False``(CAPTCHA/이상 로그인 — reuse 가 자동 복구 불가) → ``USER_ACTION_REQUIRED``;
  reauth 신호(Gmail refresh 실패/grant 취소) → ``GMAIL_REAUTH_REQUIRED``; 그 외 transient →
  ``RECOVERY_FAILED``.
* :func:`recover_coupang_mailbox` — **bounded 복구 orchestrator**. mailbox lock 아래에서 주입
  ``recover`` 를 ``max_attempts`` 상한으로 호출하고, 결과를 분류해
  :func:`~rider_agent.job_loop.make_success_result`/:func:`~rider_agent.job_loop.make_failure_result`
  로 표면화한다. **자동복구가 반복 실패하면 인증 요청을 계속 보내지 않고 상한에서 멈춘다**
  (반복 인증 요청·무한 polling 0, NFR-4 — 기존 "로그인 만료 시 탭 중지" 정책 계승).
  :func:`build_coupang_recover` 는 기본 ``recover`` 를 reuse
  :func:`~rider_crawl.auth.coupang_email_2fa.recover_coupang_session_with_email_2fa` 로 배선하되
  **mailbox 별 token 파일 경로**(:func:`mailbox_token_path`)를 파생해 고객 간 token 공유를 막는다.

소유 분리(스코프 경계):

* **배민(4.8)과 정반대 정책 — 쿠팡은 OTP 자동 복구를 "소비"한다.** 4.8 ``baemin_auth`` 는 OTP
  취득·우회를 AST 부정 가드로 **금지**했지만, 4.9 는 reuse 의 OTP 조회·세션 복구 seam
  (``recover_coupang_session_with_email_2fa``/``fetch_latest_verification_code``)을 **적극
  소비**한다. OTP 조회·입력·요청시각 컷오프(``requested_after``)·from/subject/query/customer
  필터·코드 파싱은 **reuse 가 이미 검증된 형태로 수행**한다 — 4.9 는 재구현하지 않고 그 위에
  mailbox 분리·lock·분류·bounded-stop 만 얹는다(AST 부정 가드 없음 — 쿠팡은 OTP 조회가 정상).
* **``rider_crawl``/``rider_server`` 소스·실제 ``CRAWL_COUPANG`` 수집 워커·서버 측 OAuth
  onboarding·``gmail_reauth_required_count`` 알림은 본 스토리 범위가 아니다.** 4.9 는 미래
  워커가 소비할 primitive 만 제공한다. 정밀 reauth 판별(어떤 예외가 Gmail 재승인인지)과 실 OAuth
  token 파일 생성/갱신 위치는 운영/Epic 5 가 ``is_reauth`` predicate·``recover``/``fetch_code``
  주입으로 배선한다.

**민감값 0 노출(NFR-5·ops:15·93).** 인증번호(OTP)·OAuth/refresh token·쿠팡 비밀번호·full
email 이 로그·예외·result_json·metrics 어디에도 남지 않는다 — reuse 예외 본문을 통째
forwarding 하지 않고 **고정 사유 상수 + mailbox ref(불투명 핸들)** 만 싣는다. ``redact()`` 는
운영 식별자(mailbox/이메일 명)를 못 가리므로(memory: redact-skips-operational-ids) ref 는
평문 이메일이 아닌 **해시 핸들**이다.

상태/오류 어휘는 **평문 문자열 상수**다(spec data-api-contract 의 ``USER_ACTION_REQUIRED``/
``GMAIL_REAUTH_REQUIRED`` 값과 정합) — ``rider_server`` 를 import 하면 단방향 가드 위반이라
**값만 베끼고**(4.5 ``STATE_AUTH_REQUIRED``·4.8 ``AUTH_STATE_*`` 선례) 어떤 enum/"정확히 N개"
lock 도 두지 않는다(memory: enum-member-count-locks). 쿠팡 ``USER_ACTION_REQUIRED`` 를 배민
``USER_ACTION_PENDING`` 과 혼동하지 않는다.

자기(own) 코드는 **순수 동기**이고 stdlib(+``rider_crawl.redaction``·``rider_crawl.secret_store``·
``rider_agent`` 자기 패키지)만 import 한다(역방향/``rider_server`` import 0, ``asyncio`` 0). 실
Gmail/실 DPAPI/실 시계는 함수 내부 lazy + 주입 가능이라
``import rider_agent.auth.coupang_gmail_2fa`` 가 비-Windows(WSL/CI)에서도 import-safe 하다
(reuse seam eager import 도 ``googleapiclient`` 미로드 — gmail.py 가 google 을 함수 내부 import).
4.1 의 AST 가드가 ``auth/`` 하위까지 자동 검사한다.
"""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator

from rider_crawl.redaction import redact
from rider_crawl.secret_store import SecretStore

from rider_agent.job_loop import JobResult, make_failure_result, make_success_result
from rider_agent.reuse import recover_coupang_session_with_email_2fa
from rider_agent.secure_store import DpapiSecretStore, default_secret_store_path

# ── 상태 어휘 — **평문 상수**(data-api-contract 값 정합), enum/"정확히 N개" lock 금지.
# rider_server 를 직접 import 하면 단방향 가드 위반이라 값만 베낀다(baemin_auth ``AUTH_STATE_*``
# 선례). 후속(Epic 5)이 상태를 늘려도 다른 lock 을 깨지 않는다.
# [Source: data-api-contract.md(143-144), src/rider_server/domain/states.py(45-59)]
STATE_RECOVERED = "ACTIVE"
STATE_USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
STATE_GMAIL_REAUTH_REQUIRED = "GMAIL_REAUTH_REQUIRED"
STATE_RECOVERY_FAILED = "RECOVERY_FAILED"

# job-level error_code — 분류 상태명과 동일 UPPER_SNAKE 평문 상수.
ERROR_USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
ERROR_GMAIL_REAUTH_REQUIRED = "GMAIL_REAUTH_REQUIRED"
ERROR_RECOVERY_FAILED = "RECOVERY_FAILED"

# 사유(평문 상수) — result_json/metrics 에 실어 운영(Epic 5 알림)에 남긴다. secret 아님.
REASON_CAPTCHA_OR_ABNORMAL = "captcha_or_abnormal_login"
REASON_GMAIL_REAUTH = "gmail_reauth_required"
REASON_MAIL_DELAY = "verification_mail_delayed"
REASON_REPEATED_FAILURE = "repeated_recovery_failure"

# ── bounded 복구 상한(반복 인증 요청 금지, NFR-4) — 기존 crawler 정책("crawl 1회당 복구 1회,
# 실패면 탭 중지" crawler.py:518-540·config.py:20)을 Agent-job 레이어에서 재현한 모듈 상수.
# 기본 1회(기존 정책 그대로). 운영이 작은 N(>1)으로 올리면 transient(인증메일 지연) 한정으로
# backoff 후 재시도하되 상한에서 멈춘다. 테스트는 작은 값 + 주입 now/sleep 으로 결정적 검증.
# [Source: src/rider_crawl/platforms/coupang/crawler.py(518-540), src/rider_crawl/config.py(20)]
DEFAULT_MAX_RECOVERY_ATTEMPTS = 1
DEFAULT_RECOVERY_BACKOFF_SECONDS = 1.0

# 분류 상태 → job error_code 매핑(평문 상수, secret 아님).
_STATE_TO_ERROR: dict[str, str] = {
    STATE_USER_ACTION_REQUIRED: ERROR_USER_ACTION_REQUIRED,
    STATE_GMAIL_REAUTH_REQUIRED: ERROR_GMAIL_REAUTH_REQUIRED,
    STATE_RECOVERY_FAILED: ERROR_RECOVERY_FAILED,
}


# ── mailbox 별 token 분리 helper(DpapiSecretStore 재사용) ──────────────────────


def _mailbox_handle(mailbox_id: str) -> str:
    """``mailbox_id`` 를 평문 노출 없는 결정적 해시 핸들로 만든다(같은 id→같은 핸들).

    ``mailbox_id`` 가 평문 이메일일 수 있는데, ``redact()`` 는 운영 식별자(이메일/mailbox 명)를
    못 가리므로(memory: redact-skips-operational-ids) ref/파일 경로/result_json keyspace 로
    평문 mailbox 가 새지 않게 sha256 앞 16hex 로 opaque 화한다. 해시라 secret 이 아니다.
    """

    return hashlib.sha256(mailbox_id.encode("utf-8")).hexdigest()[:16]


def mailbox_token_ref(mailbox_id: str) -> str:
    """``mailbox_id`` 별 결정적 고유 ref(불투명 핸들). 두 다른 mailbox→다른 ref(고객 간 비공유).

    ``f"gmail:{handle}"`` — secret 이 아닌 참조라 redaction 이 ``*:ref`` 형태로 보존하고, handle
    이 해시라 평문 이메일을 담지 않는다. 서버는 이 ref 만 보유하고 token bytes 는 store 에만 둔다.
    """

    return f"gmail:{_mailbox_handle(mailbox_id)}"


def _default_store() -> SecretStore:
    """기본 mailbox token store(Agent-local DPAPI). 실 DPAPI 는 put/resolve 시점에만 Windows
    를 요구하고(codec lazy), 테스트는 항상 fake codec store 를 주입한다(이 기본은 미호출)."""

    return DpapiSecretStore(default_secret_store_path())


def store_mailbox_token(
    store: SecretStore | None, mailbox_id: str, token: str
) -> str:
    """Gmail OAuth token 을 ``mailbox_id`` 단위로 분리 저장하고 ref 만 돌려준다.

    ``store.put(token, ref=mailbox_token_ref(mailbox_id))`` 그대로 — 4.2 DPAPI 백엔드(암호화·
    atomic·결정적 ref·fail-closed)를 재사용한다(새 store/crypto 재발명 0). 반환값은 ref 뿐이라
    평문 token 이 노출되지 않는다(token bytes 는 store 의 암호화 blob 에만). ``store`` 미주입 시
    기본 :func:`_default_store`(주입 가능 — 테스트는 fake codec/``tmp_path``).
    """

    target = store if store is not None else _default_store()
    return target.put(token, ref=mailbox_token_ref(mailbox_id))


def resolve_mailbox_token(
    store: SecretStore | None, mailbox_id: str
) -> str | None:
    """``mailbox_id`` 의 Gmail token 을 store 에서 해소한다(없으면 ``None`` fail-closed).

    한 mailbox 의 resolve 는 그 mailbox 의 ref 만 보므로 다른 고객 token 을 돌려주지 않는다
    (교차 mailbox 0). ``otp`` 는 store 에 넣지 않는다(``classify_secret_storage`` 상
    ``not_stored`` — 읽어 입력 후 폐기, reuse 가 담당). ``gmail_oauth_token`` 만 ``agent_local``.
    """

    target = store if store is not None else _default_store()
    return target.resolve(mailbox_token_ref(mailbox_id))


def mailbox_token_path(base_token_path: Path, mailbox_id: str) -> Path:
    """base Gmail token 파일 경로를 ``mailbox_id`` 별 경로로 파생한다(고객 간 token 파일 비공유).

    🚨 회귀 트랩 방지: reuse 기본 ``fetch_latest_verification_code`` 는 **단일 공유 파일**
    ``config.gmail_token_path``(기본 ``secrets/google/token.gmail.json``)를 읽는다. 미배선이면
    모든 고객이 같은 token 파일을 공유해 AC1/ADD-15(고객 간 token 비공유)를 **운영에서 위반**한다
    (fake 주입 테스트는 통과해 회귀가 숨는다). 두 다른 mailbox→다른 파일이 되게 base 파일명 앞에
    해시 핸들을 끼운다(``token.gmail.json`` → ``<handle>.token.gmail.json``). handle 은 해시라
    파일 keyspace 로 평문 mailbox 가 새지 않는다.
    [Source: src/rider_crawl/config.py(22·67), data-api-contract.md(139-141), ops:92]
    """

    base = Path(base_token_path)
    return base.with_name(f"{_mailbox_handle(mailbox_id)}.{base.name}")


# ── mailbox lock 등록부(같은 mailbox 직렬·다른 mailbox 병렬) ───────────────────


class MailboxLockRegistry:
    """``mailbox_id`` 별 :class:`threading.Lock` 등록부(4.5/4.6 등록부 패턴 동형).

    같은 ``mailbox_id`` 는 **같은 lock 객체**를 받아 직렬화되고(겹쳐 실행 0), 다른
    ``mailbox_id`` 는 독립 lock 이라 병렬 허용된다. 내부 dict 는 등록부 ``threading.Lock`` 으로
    보호한다(동시 ``lock_for`` 경합 시에도 mailbox 당 lock 객체가 유일). stdlib ``threading`` 만
    쓴다 — 재발명 0.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def lock_for(self, mailbox_id: str) -> threading.Lock:
        """``mailbox_id`` 의 lock 을 돌려준다(같은 id→같은 객체, 없으면 신규 발급)."""

        with self._guard:
            lock = self._locks.get(mailbox_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[mailbox_id] = lock
            return lock

    @contextmanager
    def acquire(self, mailbox_id: str) -> Iterator[threading.Lock]:
        """``mailbox_id`` lock 을 획득하고 ``finally`` 로 항상 해제한다(예외 안전·결정적)."""

        lock = self.lock_for(mailbox_id)
        lock.acquire()
        try:
            yield lock
        finally:
            lock.release()


# 프로세스 전역 기본 등록부 — orchestrator 가 ``locks`` 미주입 시 이걸 공유해 같은 mailbox 가
# 호출 간에도 직렬화되게 한다(매 호출 새 registry 면 직렬화가 깨진다). 주입하면 테스트가 격리.
_DEFAULT_LOCKS = MailboxLockRegistry()


# ── 실패 분류기(복구 결과 → 평문 조치 가능 상태) ──────────────────────────────


def classify_coupang_2fa_outcome(
    *,
    recovered: bool | None = None,
    error: BaseException | None = None,
    is_reauth: bool | None = None,
) -> str:
    """쿠팡 2FA 복구 결과를 평문 조치 가능 상태로 분류한다(순수 함수, secret 미접근).

    * ``recovered is True`` → :data:`STATE_RECOVERED`(``ACTIVE``).
    * ``recovered is False``(CAPTCHA/이상 로그인 — reuse ``recover`` 가 자동 복구 불가로 False)
      → :data:`STATE_USER_ACTION_REQUIRED`.
    * ``is_reauth is True``(Gmail refresh 실패/grant 취소 = 재승인 필요 신호) →
      :data:`STATE_GMAIL_REAUTH_REQUIRED`.
    * 그 외 ``error``(인증메일 지연/코드 미추출 transient) 또는 모호 입력 →
      :data:`STATE_RECOVERY_FAILED`(fail-closed).

    **메시지 텍스트를 파싱하지 않는다**(fragile·운영 식별자 한계) — bool/예외 객체 유무와 주입
    ``is_reauth`` 판정만 본다. reauth 판별의 실 binding(어떤 예외가 재승인인지)은 운영/Epic 5 가
    ``is_reauth`` predicate 로 주입한다. 어떤 secret(코드/token)도 읽지 않는다.
    [Source: data-api-contract.md(143-144), memory/redact-skips-operational-ids]
    """

    if recovered is True:
        return STATE_RECOVERED
    if recovered is False:
        return STATE_USER_ACTION_REQUIRED
    if is_reauth is True:
        return STATE_GMAIL_REAUTH_REQUIRED
    # transient error(인증메일 지연 등) 또는 모호 입력 → fail-closed.
    return STATE_RECOVERY_FAILED


def _reauth_flag(
    is_reauth: Callable[[BaseException], bool] | None, exc: BaseException
) -> bool | None:
    """주입 reauth predicate 를 예외에 적용해 bool 신호로 만든다(미주입/오류는 ``None``).

    predicate 자체가 던지면 reauth 로 판정하지 않는다(fail-closed → transient 로 둔다). predicate
    는 예외 **타입**만 보게 운영이 주입한다 — 메시지/secret 파싱 금지.
    """

    if is_reauth is None:
        return None
    try:
        return bool(is_reauth(exc))
    except Exception:
        return None


# ── 결과 표면화(민감값 0 — mailbox ref·평문 상태·고정 사유만) ─────────────────


def _success_result(mailbox_ref: str, attempts: int, log: Callable[[str], None] | None) -> JobResult:
    if log is not None:
        log(redact(f"coupang gmail 2fa recovered (mailbox {mailbox_ref})"))
    return make_success_result(
        result_json={"mailbox_ref": mailbox_ref, "state": STATE_RECOVERED},
        metrics={"attempts": attempts},
    )


def _failure_result(
    state: str,
    mailbox_ref: str,
    reason: str,
    attempts: int,
    log: Callable[[str], None] | None,
) -> JobResult:
    """분류 상태로 멈춘 결과를 만든다. **고정 사유 상수 + mailbox ref 만** — raw 예외 본문/OTP/
    token 0(reuse 예외는 forwarding 하지 않는다). :func:`make_failure_result` 가
    ``redacted_error_event`` 로 메시지를 마스킹한다(중복 마스킹 로직 신설 0)."""

    error_code = _STATE_TO_ERROR.get(state, ERROR_RECOVERY_FAILED)
    if log is not None:
        log(redact(f"coupang gmail 2fa recovery stopped: {state} (mailbox {mailbox_ref})"))
    return make_failure_result(
        error_code,
        "coupang gmail 2fa recovery stopped at bounded limit",
        result_json={"mailbox_ref": mailbox_ref, "state": state, "reason": reason},
        metrics={"reason": reason, "attempts": attempts},
    )


# ── bounded 복구 orchestrator(lock 직렬화 + 상한 + 분류 표면화) ────────────────


def recover_coupang_mailbox(
    *,
    mailbox_id: str,
    recover: Callable[[], bool],
    locks: MailboxLockRegistry | None = None,
    store: SecretStore | None = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    backoff_seconds: float = DEFAULT_RECOVERY_BACKOFF_SECONDS,
    is_reauth: Callable[[BaseException], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> JobResult:
    """쿠팡 mailbox 의 Gmail 2FA 복구를 lock 직렬화 + bounded 로 수행하고 결과를 표면화한다.

    흐름: (a) ``locks.acquire(mailbox_id)`` 아래에서(같은 mailbox 직렬·다른 mailbox 병렬), (b)
    주입 ``recover() -> bool`` 를 호출한다. (c) 결과 분류:

    * ``recover`` 가 ``True`` → :func:`make_success_result`(``result_json={mailbox_ref, state:
      ACTIVE}``), 이후 호출 0.
    * ``recover`` 가 ``False``(CAPTCHA/이상 로그인) → ``USER_ACTION_REQUIRED`` 로 **즉시 멈춘다**
      (재시도 0 — 재시도해도 CAPTCHA 는 사람 조치 필요, 재요청 0).
    * ``recover`` 가 reauth 신호 예외(``is_reauth`` predicate 가 True) → ``GMAIL_REAUTH_REQUIRED``
      로 **즉시 멈춘다**(재승인 필요라 재시도 무의미).
    * ``recover`` 가 transient 예외(인증메일 지연 등) → ``backoff_seconds`` 대기 후 ``max_attempts``
      상한까지 재시도, 상한 소진 시 ``RECOVERY_FAILED`` 로 **멈춘다**.

    **상한 소진 시 인증 요청을 계속 보내지 않는다**(반복 인증 요청·무한 polling 0, NFR-4 — 기존
    "로그인 만료 시 탭 중지" 정책 계승). result_json·metrics·log 에는 **mailbox ref·평문 상태·고정
    사유만** — OTP/token/refresh/비밀번호/full email 0(reuse 예외 본문 forwarding 0, NFR-5).

    ``store``/``now`` 는 운영/Epic 5 배선(서버는 ref 만 보유)과의 인터페이스 대칭을 위해 받되
    본 primitive 흐름에서 secret 을 읽지 않는다(4.8 ``execute_auth_check_job(now=…)`` 선례).
    한 job 안의 bounded 시도만 보장하며, job 재스케줄 상한은 lease/scheduler(4.4·Epic 5) 소유다.
    [Source: epics.md AC(900-902), src/rider_agent/job_loop.py(175-228),
    src/rider_crawl/platforms/coupang/crawler.py(518-540)]
    """

    registry = locks if locks is not None else _DEFAULT_LOCKS
    mailbox_ref = mailbox_token_ref(mailbox_id)
    attempts = 0
    with registry.acquire(mailbox_id):
        while True:
            attempts += 1
            try:
                recovered = recover()
            except Exception as exc:  # noqa: BLE001 — 분류는 타입/predicate 만, secret 미접근
                state = classify_coupang_2fa_outcome(
                    error=exc, is_reauth=_reauth_flag(is_reauth, exc)
                )
                if state == STATE_GMAIL_REAUTH_REQUIRED:
                    # 재승인 필요 → 재시도 무의미·즉시 멈춤(반복 인증 요청 0).
                    return _failure_result(
                        state, mailbox_ref, REASON_GMAIL_REAUTH, attempts, log
                    )
                # transient(인증메일 지연/코드 미추출) → bounded 재시도 후 멈춤.
                if attempts >= max_attempts:
                    reason = (
                        REASON_REPEATED_FAILURE if attempts > 1 else REASON_MAIL_DELAY
                    )
                    return _failure_result(
                        STATE_RECOVERY_FAILED, mailbox_ref, reason, attempts, log
                    )
                sleep(backoff_seconds)
                continue

            state = classify_coupang_2fa_outcome(recovered=recovered)
            if state == STATE_RECOVERED:
                return _success_result(mailbox_ref, attempts, log)
            # recovered is False → CAPTCHA/이상 로그인 → USER_ACTION_REQUIRED 로 멈춤(재요청 0).
            return _failure_result(
                state, mailbox_ref, REASON_CAPTCHA_OR_ABNORMAL, attempts, log
            )


def build_coupang_recover(
    *,
    page: Any,
    config: Any,
    mailbox_id: str,
    recover_session: Callable[..., bool] = recover_coupang_session_with_email_2fa,
    fetch_code: Callable[..., str] | None = None,
) -> Callable[[], bool]:
    """reuse 세션 복구를 ``mailbox_id`` 별 token 파일 경로와 함께 부르는 thin 0-arg wrapper.

    🚨 회귀 트랩 방지: ``AppConfig`` 는 ``@dataclass(frozen=True)`` 라 ``dataclasses.replace`` 로
    ``gmail_token_path`` 만 **mailbox 별 경로**(:func:`mailbox_token_path`)로 바꿔 두 고객이 같은
    공유 ``token.gmail.json`` 을 읽지 않게 한다(0줄-소스-변경 배선, ``rider_crawl`` 무변경). OTP
    조회·입력·요청시각 컷오프·query/customer 필터·코드 파싱은 **reuse 가 수행**한다(4.9 재구현 0).
    테스트는 항상 fake ``recover_session``/``fetch_code`` 를 주입한다(실 Gmail/실 화면 0).
    [Source: src/rider_crawl/auth/coupang_email_2fa.py(76-124·131-158), src/rider_agent/reuse.py(47)]
    """

    mailbox_config = replace(
        config, gmail_token_path=mailbox_token_path(config.gmail_token_path, mailbox_id)
    )

    def _recover() -> bool:
        return recover_session(page, mailbox_config, fetch_code=fetch_code)

    return _recover
