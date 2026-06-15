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

from rider_agent.job_loop import JobResult, make_failure_result, make_success_result
from rider_agent.reuse import recover_coupang_session_with_email_2fa
from rider_agent.secure_store import DpapiSecretStore, default_secret_store_path

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

DEFAULT_MAX_RECOVERY_ATTEMPTS = 1
DEFAULT_RECOVERY_BACKOFF_SECONDS = 1.0

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
) -> str:
    if recovered is True:
        return STATE_RECOVERED
    if recovered is False:
        return STATE_USER_ACTION_REQUIRED
    if is_email_auth_required is True:
        return STATE_EMAIL_AUTH_REQUIRED
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
    is_email_auth_required: Callable[[BaseException], bool] | None = None,
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
                )
                if state == STATE_EMAIL_AUTH_REQUIRED:
                    return _failure_result(state, mailbox_ref, REASON_EMAIL_AUTH, attempts, log)
                if attempts >= max_attempts:
                    reason = REASON_REPEATED_FAILURE if attempts > 1 else REASON_MAIL_DELAY
                    return _failure_result(STATE_RECOVERY_FAILED, mailbox_ref, reason, attempts, log)
                sleep(backoff_seconds)
                continue

            state = classify_coupang_2fa_outcome(recovered=recovered)
            if state == STATE_RECOVERED:
                return _success_result(mailbox_ref, attempts, log)
            return _failure_result(state, mailbox_ref, REASON_CAPTCHA_OR_ABNORMAL, attempts, log)


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
