"""Coupang email 2FA Agent helpers.

This module keeps the old import path for compatibility, but the runtime model is
IMAP app-password based. It stores only per-mailbox app-password refs, serializes
requests for the same mailbox, and exposes bounded recovery results without OTPs
or password values.
"""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Callable, Iterator

from rider_crawl.redaction import redact
from rider_crawl.secret_store import SecretStore

from rider_agent.job_loop import (
    JOB_STATUS_SUCCESS,
    ClaimedJob,
    JobResult,
    make_failure_result,
    make_success_result,
)
from rider_agent.reuse import (
    BrowserLaunchError,
    CdpUnavailableError,
    recover_coupang_session_with_email_2fa,
)
from rider_agent.secure_store import DpapiSecretStore, default_secret_store_path

# ── Coupang 2FA 세부 복구 상태(auth job ``result_json.auth_recovery_state``) ─────────
STATE_RECOVERED = "ACTIVE"
STATE_USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
STATE_EMAIL_AUTH_REQUIRED = "EMAIL_AUTH_REQUIRED"
STATE_RECOVERY_FAILED = "RECOVERY_FAILED"

ERROR_USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
ERROR_EMAIL_AUTH_REQUIRED = "EMAIL_AUTH_REQUIRED"
ERROR_RECOVERY_FAILED = "RECOVERY_FAILED"

REASON_CAPTCHA_OR_ABNORMAL = "captcha_or_abnormal_login"
REASON_EMAIL_AUTH = "email_auth_required"
REASON_MAIL_DELAY = "verification_mail_delayed"
REASON_REPEATED_FAILURE = "repeated_recovery_failure"
REASON_BROWSER_UNAVAILABLE = "browser_unavailable"

DEFAULT_MAX_RECOVERY_ATTEMPTS = 1
DEFAULT_RECOVERY_BACKOFF_SECONDS = 1.0

# ── AUTH_COUPANG_2FA job 어휘(평문 상수 — rider_server 도메인 값 미러, import 강결합 금지) ─
# job type/capability 는 ``rider_agent.heartbeat.CAPABILITY_AUTH_COUPANG_2FA`` 와 같은 문자열.
CAPABILITY_AUTH_COUPANG_2FA = "AUTH_COUPANG_2FA"
# 계정 coarse gate 상태(``result_json.auth_state``) — BaeminAuthState 값과 정합.
AUTH_STATE_ACTIVE = "ACTIVE"
AUTH_STATE_AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_STATE_USER_ACTION_PENDING = "USER_ACTION_PENDING"
# job-level error_code(서버 retry 정책이 사람 개입으로 보류하는 카테고리).
ERROR_AUTH_REQUIRED = "AUTH_REQUIRED"
# 복구 mode 식별자(crawl payload 가 아니라 auth job result/payload 에만 둔다).
RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA = "coupang_auto_email_2fa"
# 필수 secret ref 미해소 시 fail-closed reason.
REASON_SECRET_REF_UNRESOLVED = "secret_ref_unresolved"

_SAFE_EMAIL_AUTH_REASONS = frozenset(
    {
        REASON_EMAIL_AUTH,
        "mail_app_password_invalid",
        "imap_access_disabled",
        "unsupported_email_domain",
        "mailbox_auth_blocked",
        "mailbox_login_failed",
    }
)

# 세부 복구 상태 → 계정 coarse gate 상태(Decision 3: account=coarse gate, job=detailed state).
_RECOVERY_STATE_TO_GATE: dict[str, str] = {
    STATE_RECOVERED: AUTH_STATE_ACTIVE,
    STATE_USER_ACTION_REQUIRED: AUTH_STATE_USER_ACTION_PENDING,
    STATE_EMAIL_AUTH_REQUIRED: AUTH_STATE_AUTH_REQUIRED,
    STATE_RECOVERY_FAILED: AUTH_STATE_AUTH_REQUIRED,
}

_STATE_TO_ERROR: dict[str, str] = {
    STATE_USER_ACTION_REQUIRED: ERROR_USER_ACTION_REQUIRED,
    STATE_EMAIL_AUTH_REQUIRED: ERROR_EMAIL_AUTH_REQUIRED,
    STATE_RECOVERY_FAILED: ERROR_RECOVERY_FAILED,
}


def _mailbox_handle(mailbox_id: str) -> str:
    return hashlib.sha256(mailbox_id.encode("utf-8")).hexdigest()[:16]


def mailbox_credential_ref(mailbox_id: str) -> str:
    """Return an opaque deterministic ref for one mailbox credential."""

    return f"email:{_mailbox_handle(mailbox_id)}"


def _default_store() -> SecretStore:
    return DpapiSecretStore(default_secret_store_path())


def store_mailbox_app_password(
    store: SecretStore | None, mailbox_id: str, app_password: str
) -> str:
    target = store if store is not None else _default_store()
    return target.put(app_password, ref=mailbox_credential_ref(mailbox_id))


def resolve_mailbox_app_password(
    store: SecretStore | None, mailbox_id: str
) -> str | None:
    target = store if store is not None else _default_store()
    return target.resolve(mailbox_credential_ref(mailbox_id))


class MailboxLockRegistry:
    """Per-mailbox lock registry.

    The same mailbox is serialized so duplicate verification-code requests do
    not overlap. Different mailboxes can still run in parallel.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def lock_for(self, mailbox_id: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(mailbox_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[mailbox_id] = lock
            return lock

    @contextmanager
    def acquire(self, mailbox_id: str) -> Iterator[threading.Lock]:
        lock = self.lock_for(mailbox_id)
        lock.acquire()
        try:
            yield lock
        finally:
            lock.release()


_DEFAULT_LOCKS = MailboxLockRegistry()


def classify_coupang_2fa_outcome(
    *,
    recovered: bool | None = None,
    error: BaseException | None = None,
    is_email_auth_required: bool | None = None,
    is_user_action_required: bool | None = None,
) -> str:
    if recovered is True:
        return STATE_RECOVERED
    # 실제 캡차/이상 로그인 신호가 있을 때만 USER_ACTION_REQUIRED(사람 조치, 자동 재시도 0).
    if is_user_action_required is True:
        return STATE_USER_ACTION_REQUIRED
    if is_email_auth_required is True:
        return STATE_EMAIL_AUTH_REQUIRED
    # recover 가 False(2FA 플로우 미완)거나 그 외 실패는 재시도 가능한 RECOVERY_FAILED 로 둔다.
    # False 를 무조건 캡차로 보지 않는다 — 정상 세션을 데드 상태로 잘못 고착시키지 않기 위함.
    return STATE_RECOVERY_FAILED


def _email_auth_flag(
    predicate: Callable[[BaseException], bool] | None, exc: BaseException
) -> bool | None:
    if predicate is None:
        return None
    try:
        return bool(predicate(exc))
    except Exception:
        return None


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _safe_email_auth_reason(value: object) -> str | None:
    reason = str(value or "").strip()
    if reason in _SAFE_EMAIL_AUTH_REASONS:
        return reason
    return None


def default_email_auth_reason(exc: BaseException) -> str:
    """Return only a fixed safe reason code for mailbox auth failures."""

    for current in _exception_chain(exc):
        reason = _safe_email_auth_reason(getattr(current, "email_auth_reason", None))
        if reason is not None:
            return reason
        reason = _safe_email_auth_reason(getattr(current, "reason", None))
        if reason is not None:
            return reason
        if getattr(current, "email_auth_required", False):
            return REASON_EMAIL_AUTH
    return REASON_EMAIL_AUTH


def default_is_email_auth_required(exc: BaseException) -> bool:
    """기본 운영 predicate — IMAP 로그인/이메일 설정 실패면 ``EMAIL_AUTH_REQUIRED`` 로 본다.

    ``recover_coupang_session_with_email_2fa`` 가 IMAP 로그인 실패를 ``Coupang2faError
    (email_auth_required=True)`` 로(또는 ``ImapAuthError`` 를 cause 로) 올린다. 예외 체인
    (``__cause__``)을 따라가 그 신호를 감지한다 — "메일 지연(코드 미수신)" 같은 일시적 실패는
    ``False`` 로 두어 ``RECOVERY_FAILED``(재요청 가능)로 남긴다. import-safe(lazy import).
    """

    try:
        from rider_crawl.auth.coupang_email_2fa import Coupang2faError
        from rider_crawl.auth.imap_2fa import ImapAuthError
    except Exception:  # pragma: no cover - 의존성 부재 환경에선 보수적으로 미지정.
        return False

    for current in _exception_chain(exc):
        if isinstance(current, ImapAuthError):
            return True
        if isinstance(current, Coupang2faError) and getattr(current, "email_auth_required", False):
            return True
    return False


def default_is_user_action_required(exc: BaseException) -> bool:
    """캡차/이상 로그인(사람 조치 필요)인지 — 실제 캡차 신호일 때만 True.

    ``recover_coupang_session_with_email_2fa`` 가 캡차 화면을 감지하면
    ``CoupangCaptchaError`` 로 올린다. 예외 체인(``__cause__``/``__context__``)을 따라가 그
    신호만 잡는다. 단순 복구 미완(``recover`` 가 ``False``)은 여기서 True 가 아니다 — 정상 세션을
    데드 상태(``USER_ACTION_PENDING``)로 잘못 고착시키지 않기 위함. import-safe(lazy import).
    """

    try:
        from rider_crawl.auth.coupang_email_2fa import CoupangCaptchaError
    except Exception:  # pragma: no cover - 의존성 부재 환경에선 보수적으로 미지정.
        return False

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CoupangCaptchaError):
            return True
        current = current.__cause__ or current.__context__
    return False


def default_is_browser_unavailable(exc: BaseException) -> bool:
    """Chrome/CDP 준비·연결 실패를 메일 지연과 분리한다."""

    markers = (
        "connect_over_cdp",
        "chrome cdp",
        "cdp endpoint",
        "econnrefused",
        "err_connection_refused",
    )
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (BrowserLaunchError, CdpUnavailableError)):
            return True
        text = f"{type(current).__name__}: {current}".casefold()
        if any(marker in text for marker in markers):
            return True
        current = current.__cause__ or current.__context__
    return False


def _success_result(mailbox_ref: str, attempts: int, log: Callable[[str], None] | None) -> JobResult:
    if log is not None:
        log(redact(f"coupang email 2fa recovered (mailbox {mailbox_ref})"))
    return make_success_result(
        result_json={"mailbox_credential_ref": mailbox_ref, "state": STATE_RECOVERED},
        metrics={"attempts": attempts},
    )


def _failure_result(
    state: str,
    mailbox_ref: str,
    reason: str,
    attempts: int,
    log: Callable[[str], None] | None,
) -> JobResult:
    error_code = _STATE_TO_ERROR.get(state, ERROR_RECOVERY_FAILED)
    if log is not None:
        log(redact(f"coupang email 2fa recovery stopped: {state} (mailbox {mailbox_ref})"))
    return make_failure_result(
        error_code,
        "coupang email 2fa recovery stopped at bounded limit",
        result_json={"mailbox_credential_ref": mailbox_ref, "state": state, "reason": reason},
        metrics={"reason": reason, "attempts": attempts},
    )


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
    is_email_auth_required: Callable[[BaseException], bool] | None = default_is_email_auth_required,
    is_user_action_required: Callable[[BaseException], bool] | None = default_is_user_action_required,
    is_browser_unavailable: Callable[[BaseException], bool] | None = default_is_browser_unavailable,
    log: Callable[[str], None] | None = None,
) -> JobResult:
    del store, now
    registry = locks if locks is not None else _DEFAULT_LOCKS
    mailbox_ref = mailbox_credential_ref(mailbox_id)
    attempts = 0
    with registry.acquire(mailbox_id):
        while True:
            attempts += 1
            try:
                recovered = recover()
            except Exception as exc:  # noqa: BLE001 - deliberate classification boundary
                state = classify_coupang_2fa_outcome(
                    error=exc,
                    is_email_auth_required=_email_auth_flag(is_email_auth_required, exc),
                    is_user_action_required=_email_auth_flag(is_user_action_required, exc),
                )
                if state == STATE_USER_ACTION_REQUIRED:
                    # 실제 캡차/이상 로그인 — 사람 조치 필요, 재시도하지 않는다.
                    return _failure_result(
                        state, mailbox_ref, REASON_CAPTCHA_OR_ABNORMAL, attempts, log
                    )
                if state == STATE_EMAIL_AUTH_REQUIRED:
                    return _failure_result(
                        state,
                        mailbox_ref,
                        default_email_auth_reason(exc),
                        attempts,
                        log,
                    )
                if _email_auth_flag(is_browser_unavailable, exc) is True:
                    return _failure_result(
                        STATE_RECOVERY_FAILED,
                        mailbox_ref,
                        REASON_BROWSER_UNAVAILABLE,
                        attempts,
                        log,
                    )
                if attempts >= max_attempts:
                    reason = REASON_REPEATED_FAILURE if attempts > 1 else REASON_MAIL_DELAY
                    return _failure_result(STATE_RECOVERY_FAILED, mailbox_ref, reason, attempts, log)
                sleep(backoff_seconds)
                continue

            state = classify_coupang_2fa_outcome(recovered=recovered)
            if state == STATE_RECOVERED:
                return _success_result(mailbox_ref, attempts, log)
            # recover 가 False(2FA 플로우 미완 — 캡차는 위 예외 경로에서 처리)면 재시도 가능한
            # 실패로 다룬다. 캡차로 단정하지 않으므로 RECOVERY_FAILED 로 닫혀 AUTH_REQUIRED 로
            # 매핑되고(데드 USER_ACTION_PENDING 아님) cooldown 뒤 다시 자동 복구 대상이 된다.
            if attempts >= max_attempts:
                reason = REASON_REPEATED_FAILURE if attempts > 1 else REASON_MAIL_DELAY
                return _failure_result(STATE_RECOVERY_FAILED, mailbox_ref, reason, attempts, log)
            sleep(backoff_seconds)
            continue


def build_coupang_recover(
    *,
    page: Any,
    config: Any,
    mailbox_id: str,
    email_address: str = "",
    app_password: str = "",
    store: SecretStore | None = None,
    recover_session: Callable[..., bool] = recover_coupang_session_with_email_2fa,
    fetch_code: Callable[..., str] | None = None,
) -> Callable[[], bool]:
    resolved_address = email_address or str(getattr(config, "verification_email_address", "") or "")
    resolved_password = app_password or resolve_mailbox_app_password(store, mailbox_id) or ""
    mailbox_config = replace(
        config,
        verification_email_address=resolved_address,
        verification_email_app_password=resolved_password,
    )

    def _recover() -> bool:
        return recover_session(page, mailbox_config, fetch_code=fetch_code)

    return _recover


# ── AUTH_COUPANG_2FA job 실행자 ──────────────────────────────────────────────────
# 크롤 job 안에서 조용히 하던 email 2FA 자동복구를 전용 인증 job 으로 승격한다. 기존
# ``recover_coupang_mailbox``(lock·bounded attempt·reason·실패 분류) primitive 를 그대로 재사용
# 하고, 결과를 서버 account state 갱신이 읽는 **coarse gate + 세부 상태** 형태로 표면화한다.
# OTP/비밀번호/app password/평문 이메일은 result/metrics/log 어디에도 남기지 않는다.


def _config_from_coupang_auth_job(
    job: ClaimedJob,
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> Any:
    """job payload → AppConfig(크롤 worker 의 변환·reuse seam 재사용, lazy import 로 import-safe)."""

    from rider_agent.auth.baemin_auth import _config_from_auth_job

    return _config_from_auth_job(job, secret_resolver=secret_resolver)


@contextmanager
def _coupang_auth_page(config: Any) -> Iterator[Any | None]:
    """자동복구가 운전할 Coupang 인증/로그인 page 를 **열린 Playwright 컨텍스트 안에서** 내준다.

    과거 ``baemin_auth._drive_coupang_email_2fa_flow`` 의 **안전한 페이지 선택 부분만** 옮겨온다:
    로그인 화면이면 그대로 두고(로그인 전 절대 안 뜰 대시보드 텍스트를 기다리지 않음 — 과거
    page_timeout 헛대기 회피), 로그인 화면이 아니고 대상 URL 이 있으면 한 번만 안내 navigate 한다.
    실제 OTP 취득·입력·제출은 호출자(``recover_coupang_session_with_email_2fa``)가 한다.

    **반드시 page 사용이 끝날 때까지 컨텍스트를 열어 둔다** — ``_sync_playwright()`` 를 빠져나가면
    CDP 연결이 끊겨 page 가 죽으므로, 호출자는 ``with`` 블록 안에서 복구를 완료해야 한다(닫힌
    컨텍스트의 page 를 운전하던 use-after-close 버그 회피).
    """

    from rider_agent.auth.baemin_auth import (
        _AUTH_NETWORKIDLE_TIMEOUT_MS,
        _INTERACTION_TIMEOUT_MS,
        _first_browser_page,
        _playwright_timeout_errors,
        _sync_playwright,
        _wait_for_auth_screen_ready,
    )
    from rider_crawl.platforms.coupang import crawler as coupang_crawler

    with _sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(config.cdp_url)
        target_url = str(getattr(config, "coupang_eats_url", "") or "").strip()
        timeout_errors = _playwright_timeout_errors()
        pages = coupang_crawler._browser_pages(browser)
        page = coupang_crawler._login_required_page(pages)
        if page is None and target_url:
            page = coupang_crawler._select_page_by_url(pages, target_url)
        if page is None:
            page = _first_browser_page(browser)
        if page is None:
            yield None
            return
        is_login_screen = coupang_crawler._page_looks_like_coupang_login_required(page)
        if target_url and not is_login_screen:
            try:
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=getattr(config, "page_timeout_seconds", _INTERACTION_TIMEOUT_MS),
                )
            except timeout_errors:
                pass
            except Exception:
                yield page
                return
            # networkidle 대신 입력칸 등장까지만 대기(공통 헬퍼) — idle 헛대기 제거.
            _wait_for_auth_screen_ready(page, _AUTH_NETWORKIDLE_TIMEOUT_MS)
        yield page


def _default_coupang_recover(
    job: ClaimedJob,
    *,
    secret_resolver: Callable[[str], str | None] | None,
) -> Callable[[], bool]:
    """기본 recover() 클로저 — page 획득 + ``build_coupang_recover`` 합성(실 브라우저는 lazy).

    page 획득부터 OTP 취득·입력·제출까지 **한 ``with _coupang_auth_page`` 블록 안**에서 끝낸다 —
    Playwright 컨텍스트가 닫힌 뒤 page 를 운전하면 CDP 연결이 끊겨 모든 동작이 실패하기 때문이다.
    """

    config = _config_from_coupang_auth_job(job, secret_resolver=secret_resolver)
    email_address = str(getattr(config, "verification_email_address", "") or "")
    app_password = str(getattr(config, "verification_email_app_password", "") or "")

    def _recover() -> bool:
        with _coupang_auth_page(config) as page:
            if page is None:
                return False
            inner = build_coupang_recover(
                page=page,
                config=config,
                mailbox_id=email_address,
                email_address=email_address,
                app_password=app_password,
            )
            return bool(inner())

    return _recover


def _coupang_auth_account_fields(job: ClaimedJob) -> dict[str, Any]:
    """result_json 공통 식별 필드(secret 0 — target/platform/account id + recovery mode)."""

    payload = _raw_job_payload(job)
    return {
        "target_id": str(payload.get("target_id") or job.target_id or "") or None,
        "platform": "coupang",
        "platform_account_id": str(payload.get("platform_account_id") or "") or None,
        "recovery_mode": RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA,
    }


def _raw_job_payload(job: ClaimedJob) -> dict[str, Any]:
    raw = dict(job.payload or {})
    nested = raw.get("payload")
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update(raw)
        raw = merged
    return raw


# 기본(브라우저/IMAP) 복구 경로가 실제로 쓰는 필수 secret 들 — 하나라도 비면 fail-closed.
# (config attr 이름, 사람이 읽을 라벨) 쌍. 라벨은 reason/log 에 secret 값 없이 무엇이 빠졌는지만 남긴다.
_REQUIRED_COUPANG_AUTH_SECRETS: tuple[tuple[str, str], ...] = (
    ("verification_email_address", "mailbox_address"),
    ("verification_email_app_password", "mailbox_app_password"),
    ("coupang_login_id", "coupang_login_id"),
    ("coupang_login_password", "coupang_login_password"),
)


def _resolve_coupang_auth_config(
    job: ClaimedJob, *, secret_resolver: Callable[[str], str | None] | None
) -> Any | None:
    """auth job → 해소된 AppConfig(필수 ref 가 핸들 모양인데 미해소면 ``None``).

    ``_build_config`` 가 ``SecretRefUnresolved`` 를 올리면(핸들 모양 ref 가 secret store 에서 안
    풀림) ``None`` 을 돌려 호출자가 fail-closed(AUTH_REQUIRED + secret_ref_unresolved)로 종결하게
    한다 — 브라우저/IMAP 미접근. 그 외 예외도 보수적으로 ``None``.
    """

    try:
        return _config_from_coupang_auth_job(job, secret_resolver=secret_resolver)
    except Exception:
        return None


def _coupang_mailbox_id(job: ClaimedJob, *, secret_resolver: Callable[[str], str | None] | None) -> str:
    """lock/ref 용 mailbox 식별자(해소된 이메일 주소 — ref 로만 노출, 평문은 결과에 안 남김)."""

    config = _resolve_coupang_auth_config(job, secret_resolver=secret_resolver)
    if config is None:
        return ""
    return str(getattr(config, "verification_email_address", "") or "")


# is_email_auth_required 기본값 sentinel — 미지정이면 운영 predicate 를 쓰고, 명시적으로 None 을
# 넘기면(테스트가 분류를 끄려는 경우 등) 비활성화한다. None 자체가 "미지정"이 아님을 구분하기 위함.
_USE_DEFAULT_EMAIL_AUTH_PREDICATE = object()


def _missing_required_coupang_secret(config: Any | None) -> str | None:
    """기본 복구가 쓸 필수 secret 중 비어 있는 첫 항목의 라벨(없으면 ``None``).

    이메일 주소만 있고 app password/login id/password 가 비어도 브라우저를 열고 OTP 를 요청하는
    것을 막기 위한 fail-closed 검증(검토 Medium). secret 값은 노출하지 않고 라벨만 돌려준다.
    """

    if config is None:
        return _REQUIRED_COUPANG_AUTH_SECRETS[0][1]
    for attr, label in _REQUIRED_COUPANG_AUTH_SECRETS:
        if not str(getattr(config, attr, "") or "").strip():
            return label
    return None


def execute_auth_coupang_2fa_job(
    job: ClaimedJob,
    *,
    recover: Callable[[], bool] | None = None,
    secret_resolver: Callable[[str], str | None] | None = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    locks: MailboxLockRegistry | None = None,
    is_email_auth_required: Callable[[BaseException], bool] | None | object = _USE_DEFAULT_EMAIL_AUTH_PREDICATE,
    log: Callable[[str], None] | None = None,
) -> JobResult:
    """``AUTH_COUPANG_2FA`` job — 쿠팡 email 2FA 자동복구 1회(mailbox lock·bounded·재시도 폭주 0).

    기존 :func:`recover_coupang_mailbox` 로 mailbox 직렬화·bounded attempt·reason·실패 분류를
    재사용하고, 그 세부 상태(``ACTIVE``/``USER_ACTION_REQUIRED``/``EMAIL_AUTH_REQUIRED``/
    ``RECOVERY_FAILED``)를 ``result_json.auth_recovery_state`` 로 보존한다. 계정 coarse gate
    (``result_json.auth_state``)는 스케줄러/대시보드가 읽는 값으로 정규화한다(Decision 3).

    실패는 자동 retry 하지 않는다 — ``error_code == AUTH_REQUIRED``(서버 retry 정책이 사람 개입으로
    보류하는 카테고리)로 종결하고 세부 원인은 ``auth_recovery_state``/``reason`` 으로 남긴다
    (Decision 5). OTP/비밀번호/app password/평문 이메일은 result/metrics/log 어디에도 없다.
    """

    fields = _coupang_auth_account_fields(job)

    def _fail_closed(reason: str) -> JobResult:
        if log is not None:
            log(redact("coupang 2fa job fail-closed: {} (target {})".format(reason, job.target_id)))
        return make_failure_result(
            ERROR_AUTH_REQUIRED,
            "coupang 2fa job could not resolve required secret refs",
            result_json={
                **fields,
                "auth_state": AUTH_STATE_AUTH_REQUIRED,
                "auth_recovery_state": STATE_RECOVERY_FAILED,
                "reason": REASON_SECRET_REF_UNRESOLVED,
            },
            metrics={"reason": REASON_SECRET_REF_UNRESOLVED},
        )

    # 필수 ref 미해소 시 fail-closed(AUTH_REQUIRED + 고정 reason) — 브라우저/IMAP 미접근.
    config = _resolve_coupang_auth_config(job, secret_resolver=secret_resolver)
    mailbox_id = str(getattr(config, "verification_email_address", "") or "") if config else ""
    if not mailbox_id:
        return _fail_closed(REASON_SECRET_REF_UNRESOLVED)

    # 기본(브라우저/IMAP) 복구 경로일 때만 필수 secret 전체를 검증한다 — 주입 recover(테스트/대체
    # 구현)는 브라우저/IMAP 를 안 쓰므로 secret 강제 대상이 아니다. 이메일 주소만 있고 app
    # password/login id/password 가 비면 브라우저를 열기 전에 fail-closed 로 멈춘다(검토 Medium).
    if recover is None:
        missing = _missing_required_coupang_secret(config)
        if missing is not None:
            return _fail_closed("missing_{}".format(missing))

    # sentinel → 운영 predicate. 명시적 None(분류 비활성화)/주입 predicate 는 그대로 존중한다.
    email_auth_predicate = (
        default_is_email_auth_required
        if is_email_auth_required is _USE_DEFAULT_EMAIL_AUTH_PREDICATE
        else is_email_auth_required
    )

    recover_fn = recover or _default_coupang_recover(job, secret_resolver=secret_resolver)
    inner = recover_coupang_mailbox(
        mailbox_id=mailbox_id,
        recover=recover_fn,
        locks=locks,
        now=now,
        sleep=sleep,
        max_attempts=max_attempts,
        is_email_auth_required=email_auth_predicate,  # type: ignore[arg-type]
        log=log,
    )

    recovery_state = str((inner.result_json or {}).get("state") or STATE_RECOVERY_FAILED)
    gate_state = _RECOVERY_STATE_TO_GATE.get(recovery_state, AUTH_STATE_AUTH_REQUIRED)
    result_json: dict[str, Any] = {
        **fields,
        "auth_state": gate_state,
        "auth_recovery_state": recovery_state,
    }
    reason = (inner.result_json or {}).get("reason")
    if reason:
        result_json["reason"] = reason

    if inner.status == JOB_STATUS_SUCCESS:
        return make_success_result(result_json=result_json, metrics=inner.metrics)

    # 실패: queue retry category 는 항상 AUTH_REQUIRED(detail 은 auth_recovery_state/reason).
    return make_failure_result(
        ERROR_AUTH_REQUIRED,
        "coupang email 2fa auto recovery stopped (see auth_recovery_state)",
        result_json=result_json,
        metrics=inner.metrics,
    )


def build_coupang_auth_execute_job(
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
    fallback: Callable[[ClaimedJob], JobResult],
    recover: Callable[[], bool] | None = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    log: Callable[[str], None] | None = None,
) -> Callable[[ClaimedJob], JobResult]:
    """``AUTH_COUPANG_2FA`` 를 :func:`execute_auth_coupang_2fa_job` 로, 그 외 type 은 ``fallback`` 으로.

    기존 ``baemin_auth.build_auth_execute_job`` 라우터 위에 합성된다(``worker_composition`` 에서
    auth worker 보다 바깥에 둬서, AUTH_CHECK/OPEN_AUTH_BROWSER 는 그대로 흐르고
    AUTH_COUPANG_2FA 만 가로챈다). 미합성이면 fallback 그대로(무회귀).
    """

    def _execute(job: ClaimedJob) -> JobResult:
        if job.type == CAPABILITY_AUTH_COUPANG_2FA:
            return execute_auth_coupang_2fa_job(
                job,
                recover=recover,
                secret_resolver=secret_resolver,
                now=now,
                sleep=sleep,
                max_attempts=max_attempts,
                log=log,
            )
        return fallback(job)

    return _execute
