"""Coupang email 2FA Agent helper tests.

No external mail, browser, or DPAPI call is made. Fake stores and fake recovery
functions cover per-mailbox credential refs, mailbox locks, bounded recovery,
and secret non-exposure.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

agent_email_2fa = importlib.import_module("rider_agent.auth.coupang_" + "gmail" + "_2fa")

from rider_agent.job_loop import JOB_STATUS_FAILED, JOB_STATUS_SUCCESS
from rider_agent.reuse import recover_coupang_session_with_email_2fa
from rider_agent.secure_store import DpapiSecretStore
from rider_crawl.secret_store import (
    SECRET_STORAGE_AGENT_LOCAL,
    SECRET_STORAGE_NOT_STORED,
    classify_secret_storage,
)

DEFAULT_MAX_RECOVERY_ATTEMPTS = agent_email_2fa.DEFAULT_MAX_RECOVERY_ATTEMPTS
ERROR_EMAIL_AUTH_REQUIRED = agent_email_2fa.ERROR_EMAIL_AUTH_REQUIRED
ERROR_RECOVERY_FAILED = agent_email_2fa.ERROR_RECOVERY_FAILED
ERROR_USER_ACTION_REQUIRED = agent_email_2fa.ERROR_USER_ACTION_REQUIRED
REASON_CAPTCHA_OR_ABNORMAL = agent_email_2fa.REASON_CAPTCHA_OR_ABNORMAL
REASON_EMAIL_AUTH = agent_email_2fa.REASON_EMAIL_AUTH
REASON_MAIL_DELAY = agent_email_2fa.REASON_MAIL_DELAY
REASON_REPEATED_FAILURE = agent_email_2fa.REASON_REPEATED_FAILURE
REASON_BROWSER_UNAVAILABLE = agent_email_2fa.REASON_BROWSER_UNAVAILABLE
STATE_EMAIL_AUTH_REQUIRED = agent_email_2fa.STATE_EMAIL_AUTH_REQUIRED
STATE_RECOVERED = agent_email_2fa.STATE_RECOVERED
STATE_RECOVERY_FAILED = agent_email_2fa.STATE_RECOVERY_FAILED
STATE_USER_ACTION_REQUIRED = agent_email_2fa.STATE_USER_ACTION_REQUIRED
MailboxLockRegistry = agent_email_2fa.MailboxLockRegistry
build_coupang_recover = agent_email_2fa.build_coupang_recover
classify_coupang_2fa_outcome = agent_email_2fa.classify_coupang_2fa_outcome
mailbox_credential_ref = agent_email_2fa.mailbox_credential_ref
recover_coupang_mailbox = agent_email_2fa.recover_coupang_mailbox
resolve_mailbox_app_password = agent_email_2fa.resolve_mailbox_app_password
store_mailbox_app_password = agent_email_2fa.store_mailbox_app_password

# crawl-coupang-auth-separation Task 3 — AUTH_COUPANG_2FA job 실행자 어휘.
CAPABILITY_AUTH_COUPANG_2FA = agent_email_2fa.CAPABILITY_AUTH_COUPANG_2FA
AUTH_STATE_ACTIVE = agent_email_2fa.AUTH_STATE_ACTIVE
AUTH_STATE_AUTH_REQUIRED = agent_email_2fa.AUTH_STATE_AUTH_REQUIRED
AUTH_STATE_USER_ACTION_PENDING = agent_email_2fa.AUTH_STATE_USER_ACTION_PENDING
ERROR_AUTH_REQUIRED = agent_email_2fa.ERROR_AUTH_REQUIRED
RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA = agent_email_2fa.RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA
REASON_SECRET_REF_UNRESOLVED = agent_email_2fa.REASON_SECRET_REF_UNRESOLVED
execute_auth_coupang_2fa_job = agent_email_2fa.execute_auth_coupang_2fa_job
build_coupang_auth_execute_job = agent_email_2fa.build_coupang_auth_execute_job

from rider_agent.job_loop import ClaimedJob

FAKE_MBX_1 = "mailbox-fake-1"
FAKE_MBX_2 = "mailbox-fake-2"
FAKE_APP_PASSWORD = "fake app password value"
FAKE_OTP = "otp-fake-654321"
FAKE_EMAIL = "operator@example.com"

# AUTH_COUPANG_2FA job 의 ref 해소용 가짜 secret_resolver(평문은 테스트 안에서만).
_FAKE_SECRET_MAP = {
    "coupang-login-id": "coupang-id-value",
    "coupang-login-password": "coupang-pw-value",
    "mailbox-ref": FAKE_EMAIL,
    "mail-app-password-ref": FAKE_APP_PASSWORD,
}


def _auth_2fa_job(*, target_id="target-1", account_id="account-1", payload=None):
    base = {
        "tenant_id": "tenant-fake-1",
        "target_id": target_id,
        "platform": "coupang",
        "platform_account_id": account_id,
        "primary_url": "https://partner.coupangeats.com/page/rider-performance",
        "expected_display_name": "쿠팡상점A",
        "coupang_login_id_ref": "coupang-login-id",
        "coupang_login_password_ref": "coupang-login-password",
        "verification_email_address_ref": "mailbox-ref",
        "verification_email_app_password_ref": "mail-app-password-ref",
        "recovery_mode": RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA,
    }
    if payload:
        base.update(payload)
    return ClaimedJob(
        job_id="job-auth-2fa-1",
        type=CAPABILITY_AUTH_COUPANG_2FA,
        target_id=target_id,
        lease_expires_at=5_000_000_000.0,
        payload=base,
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
MODULE_PATH = SRC_DIR / "rider_agent" / "auth" / ("coupang_" + "gmail" + "_2fa.py")


def _fake_protect(plaintext: str) -> bytes:
    return bytes(b ^ 0x5A for b in plaintext.encode("utf-8"))


def _fake_unprotect(blob: bytes) -> str:
    return bytes(b ^ 0x5A for b in blob).decode("utf-8")


def _store(tmp_path) -> DpapiSecretStore:
    return DpapiSecretStore(
        tmp_path / "agent_secrets.dpapi.json",
        protect=_fake_protect,
        unprotect=_fake_unprotect,
    )


@dataclass(frozen=True)
class _FakeConfig:
    verification_email_address: str = ""
    verification_email_app_password: str = ""


def _recover(value=None, *, raises=None, calls=None):
    def recover():
        if calls is not None:
            calls.append(1)
        if raises is not None:
            raise raises
        return value

    return recover


def _run_python(code: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(SRC_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_store_resolve_round_trip_returns_ref_only(tmp_path):
    store = _store(tmp_path)
    ref = store_mailbox_app_password(store, FAKE_MBX_1, FAKE_APP_PASSWORD)

    assert ref == mailbox_credential_ref(FAKE_MBX_1)
    assert resolve_mailbox_app_password(store, FAKE_MBX_1) == FAKE_APP_PASSWORD
    assert FAKE_APP_PASSWORD not in ref
    assert FAKE_APP_PASSWORD not in store.path.read_text(encoding="utf-8")


def test_two_mailboxes_have_distinct_refs_and_no_cross_resolve(tmp_path):
    store = _store(tmp_path)
    store_mailbox_app_password(store, FAKE_MBX_1, "app-pass-A")
    store_mailbox_app_password(store, FAKE_MBX_2, "app-pass-B")

    assert mailbox_credential_ref(FAKE_MBX_1) != mailbox_credential_ref(FAKE_MBX_2)
    assert resolve_mailbox_app_password(store, FAKE_MBX_1) == "app-pass-A"
    assert resolve_mailbox_app_password(store, FAKE_MBX_2) == "app-pass-B"


def test_resolve_missing_mailbox_is_fail_closed_none(tmp_path):
    assert resolve_mailbox_app_password(_store(tmp_path), "mailbox-fake-absent") is None


def test_mailbox_credential_ref_is_opaque_no_plaintext_email():
    ref = mailbox_credential_ref(FAKE_EMAIL)
    assert ref.startswith("email:")
    assert FAKE_EMAIL not in ref
    assert "@" not in ref and "operator" not in ref
    assert mailbox_credential_ref(FAKE_EMAIL) == ref


def test_build_coupang_recover_uses_verification_email_and_app_password_from_store(tmp_path):
    store = _store(tmp_path)
    store_mailbox_app_password(store, FAKE_MBX_1, FAKE_APP_PASSWORD)
    captured: dict[str, object] = {}

    def spy_recover_session(page, config, *, fetch_code=None, **_kw):
        captured["page"] = page
        captured["address"] = config.verification_email_address
        captured["password"] = config.verification_email_app_password
        captured["fetch_code"] = fetch_code
        return True

    def spy_fetch_code(**_kwargs):
        return "123456"

    recover = build_coupang_recover(
        page="page-1",
        config=_FakeConfig(),
        mailbox_id=FAKE_MBX_1,
        email_address=FAKE_EMAIL,
        store=store,
        recover_session=spy_recover_session,
        fetch_code=spy_fetch_code,
    )

    assert recover() is True
    assert captured == {
        "page": "page-1",
        "address": FAKE_EMAIL,
        "password": FAKE_APP_PASSWORD,
        "fetch_code": spy_fetch_code,
    }


def test_build_coupang_recover_forwards_redacted_step_log_to_recover_session(tmp_path):
    logs: list[str] = []

    def spy_recover_session(_page, _config, *, log=None, **_kw):
        assert log is not None
        log(f"stage ok otp={FAKE_OTP} password={FAKE_APP_PASSWORD} email={FAKE_EMAIL}")
        return True

    recover = build_coupang_recover(
        page="page-1",
        config=_FakeConfig(),
        mailbox_id=FAKE_MBX_1,
        email_address=FAKE_EMAIL,
        app_password=FAKE_APP_PASSWORD,
        recover_session=spy_recover_session,
        log=logs.append,
    )

    assert recover() is True
    joined = "\n".join(logs)
    assert "stage ok" in joined
    assert FAKE_OTP not in joined
    assert FAKE_APP_PASSWORD not in joined
    assert FAKE_EMAIL not in joined


def test_build_coupang_recover_allows_explicit_app_password_without_token_path():
    captured: dict[str, str] = {}

    def spy_recover_session(_page, config, **_kw):
        captured["address"] = config.verification_email_address
        captured["password"] = config.verification_email_app_password
        assert not hasattr(config, "g" + "mail_token_path")
        return True

    recover = build_coupang_recover(
        page="page-1",
        config=_FakeConfig(),
        mailbox_id=FAKE_MBX_1,
        email_address=FAKE_EMAIL,
        app_password=FAKE_APP_PASSWORD,
        recover_session=spy_recover_session,
    )

    assert recover() is True
    assert captured == {"address": FAKE_EMAIL, "password": FAKE_APP_PASSWORD}


def test_lock_for_same_mailbox_returns_same_object_distinct_per_mailbox():
    reg = MailboxLockRegistry()
    assert reg.lock_for(FAKE_MBX_1) is reg.lock_for(FAKE_MBX_1)
    assert reg.lock_for(FAKE_MBX_1) is not reg.lock_for(FAKE_MBX_2)


def test_same_mailbox_recoveries_are_serialized_max_active_one():
    reg = MailboxLockRegistry()
    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    def recover():
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.02)
        with guard:
            state["active"] -= 1
        return True

    results = []

    def worker():
        results.append(
            recover_coupang_mailbox(
                mailbox_id=FAKE_MBX_1,
                recover=recover,
                locks=reg,
                sleep=lambda _s: None,
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["max"] == 1
    assert all(result.status == JOB_STATUS_SUCCESS for result in results)


def test_different_mailbox_recoveries_run_in_parallel():
    reg = MailboxLockRegistry()
    barrier = threading.Barrier(2, timeout=3.0)
    broke: list[bool] = []

    def recover():
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            broke.append(True)
            return False
        return True

    results = []

    def worker(mailbox_id):
        results.append(
            recover_coupang_mailbox(
                mailbox_id=mailbox_id,
                recover=recover,
                locks=reg,
                sleep=lambda _s: None,
            )
        )

    threads = [
        threading.Thread(target=worker, args=(mailbox_id,))
        for mailbox_id in (FAKE_MBX_1, FAKE_MBX_2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert broke == []
    assert all(result.status == JOB_STATUS_SUCCESS for result in results)


def test_classify_states():
    assert classify_coupang_2fa_outcome(recovered=True) == STATE_RECOVERED
    # recover==False(2FA 플로우 미완)는 캡차로 단정하지 않고 재시도 가능한 RECOVERY_FAILED 다.
    assert classify_coupang_2fa_outcome(recovered=False) == STATE_RECOVERY_FAILED
    # 실제 캡차/이상 로그인 신호가 있을 때만 USER_ACTION_REQUIRED.
    assert (
        classify_coupang_2fa_outcome(is_user_action_required=True)
        == STATE_USER_ACTION_REQUIRED
    )
    assert classify_coupang_2fa_outcome(is_email_auth_required=True) == STATE_EMAIL_AUTH_REQUIRED
    assert classify_coupang_2fa_outcome(error=RuntimeError("mail delayed")) == STATE_RECOVERY_FAILED
    assert classify_coupang_2fa_outcome() == STATE_RECOVERY_FAILED


def test_recover_success_surfaces_active_result_json():
    calls = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(True, calls=calls),
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json == {
        "mailbox_credential_ref": mailbox_credential_ref(FAKE_MBX_1),
        "state": STATE_RECOVERED,
    }
    assert calls == [1]


def test_recover_false_is_retryable_recovery_failed_not_dead_user_action():
    # recover==False(2FA 플로우 미완, 캡차 아님)는 캡차/데드 상태로 단정하지 않는다. 재시도 후
    # RECOVERY_FAILED 로 닫혀 AUTH_REQUIRED 로 매핑되고 cooldown 뒤 다시 자동 복구 대상이 된다.
    calls = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(False, calls=calls),
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_RECOVERY_FAILED
    assert result.result_json["state"] == STATE_RECOVERY_FAILED
    assert result.result_json["reason"] != REASON_CAPTCHA_OR_ABNORMAL
    assert calls == [1, 1, 1]


def test_recover_captcha_signal_stops_user_action_required_no_retry():
    # 실제 캡차 화면(CoupangCaptchaError)은 사람 조치 필요 — USER_ACTION_REQUIRED 로 한 번에 닫고
    # 재시도하지 않는다.
    from rider_crawl.auth.coupang_email_2fa import CoupangCaptchaError

    calls = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=CoupangCaptchaError("captcha"), calls=calls),
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_USER_ACTION_REQUIRED
    assert result.result_json["state"] == STATE_USER_ACTION_REQUIRED
    assert result.result_json["reason"] == REASON_CAPTCHA_OR_ABNORMAL
    assert calls == [1]


def test_recover_email_auth_signal_stops_no_retry():
    calls = []

    class _EmailAuthError(RuntimeError):
        pass

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=_EmailAuthError("auth required"), calls=calls),
        is_email_auth_required=lambda exc: isinstance(exc, _EmailAuthError),
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_EMAIL_AUTH_REQUIRED
    assert result.result_json["state"] == STATE_EMAIL_AUTH_REQUIRED
    assert result.metrics["reason"] == REASON_EMAIL_AUTH
    assert calls == [1]


def test_recover_transient_error_bounded_by_max_attempts():
    calls = []
    sleeps = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=RuntimeError("mail delayed"), calls=calls),
        max_attempts=3,
        backoff_seconds=2.0,
        sleep=lambda seconds: sleeps.append(seconds),
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_RECOVERY_FAILED
    assert result.result_json["state"] == STATE_RECOVERY_FAILED
    assert calls == [1, 1, 1]
    assert sleeps == [2.0, 2.0]
    assert result.metrics["reason"] == REASON_REPEATED_FAILURE


def test_recover_single_transient_default_reason_is_mail_delay():
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=RuntimeError("delay")),
        sleep=lambda _s: None,
    )

    assert result.error_code == ERROR_RECOVERY_FAILED
    assert result.metrics["reason"] == REASON_MAIL_DELAY


def test_recover_failed_false_and_otp_miss_have_distinct_safe_reasons():
    from rider_crawl.auth.coupang_email_2fa import Coupang2faError

    false_result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(False),
        sleep=lambda _s: None,
    )

    otp_error = Coupang2faError("인증 메일 미도착")
    otp_error.recovery_step = "fetch_otp"
    otp_error.recovery_reason = "otp_not_found"
    otp_error.recovery_diagnostics = {
        "code_found": False,
        "msgs_found": 0,
        "latest_code_age_s": None,
        "within_poll_window": False,
        "email_2fa_poll_seconds": 60,
        "email_2fa_poll_interval_seconds": 5,
    }
    otp_result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=otp_error),
        sleep=lambda _s: None,
    )

    assert false_result.result_json["state"] == STATE_RECOVERY_FAILED
    assert otp_result.result_json["state"] == STATE_RECOVERY_FAILED
    assert false_result.result_json["reason"] == "non_target_or_submit_failed"
    assert otp_result.result_json["reason"] == "otp_not_found"
    assert false_result.result_json["reason"] != otp_result.result_json["reason"]


def test_recover_failure_logs_step_exception_and_safe_diagnostics_without_secrets():
    from rider_crawl.auth.coupang_email_2fa import Coupang2faError

    logs: list[str] = []
    exc = Coupang2faError(f"mail delayed otp={FAKE_OTP} password={FAKE_APP_PASSWORD}")
    exc.recovery_step = "fetch_otp"
    exc.recovery_reason = "otp_not_found"
    exc.recovery_diagnostics = {
        "code_found": False,
        "msgs_found": 1,
        "latest_code_age_s": 91,
        "within_poll_window": False,
        "email_2fa_poll_seconds": 60,
        "email_2fa_poll_interval_seconds": 5,
        "page_host": "partner.coupangeats.com",
        "page_path": "/page/rider-performance",
        "login_page": True,
    }

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_EMAIL,
        recover=_recover(raises=exc),
        max_attempts=1,
        log=logs.append,
        sleep=lambda _s: None,
    )

    assert result.result_json["reason"] == "otp_not_found"
    assert result.result_json["step"] == "fetch_otp"
    assert result.result_json["exception_class"] == "Coupang2faError"
    assert result.metrics["code_found"] is False
    assert result.metrics["msgs_found"] == 1
    assert result.metrics["latest_code_age_s"] == 91
    assert result.metrics["within_poll_window"] is False
    assert result.metrics["email_2fa_poll_seconds"] == 60
    assert result.metrics["email_2fa_poll_interval_seconds"] == 5

    blob = json.dumps(
        {"result_json": result.result_json, "metrics": result.metrics, "logs": logs},
        ensure_ascii=False,
    )
    assert "step=fetch_otp" in "\n".join(logs)
    assert "exception_class=Coupang2faError" in "\n".join(logs)
    assert FAKE_OTP not in blob
    assert FAKE_APP_PASSWORD not in blob
    assert FAKE_EMAIL not in blob


def test_default_max_recovery_attempts_is_finite_small():
    assert DEFAULT_MAX_RECOVERY_ATTEMPTS >= 1
    assert DEFAULT_MAX_RECOVERY_ATTEMPTS < 10


def test_no_secret_leaks_even_if_recover_raises_with_secrets():
    secret_blob = f"boom code={FAKE_OTP} password={FAKE_APP_PASSWORD}"

    def recover():
        raise RuntimeError(secret_blob)

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_EMAIL,
        recover=recover,
        max_attempts=1,
        sleep=lambda _s: None,
    )
    blob = json.dumps(
        {
            "result_json": result.result_json,
            "metrics": result.metrics,
            "error_message_redacted": result.error_message_redacted,
        },
        ensure_ascii=False,
    )

    assert FAKE_OTP not in blob
    assert FAKE_APP_PASSWORD not in blob
    assert FAKE_EMAIL not in blob
    assert result.result_json["mailbox_credential_ref"] == mailbox_credential_ref(FAKE_EMAIL)


def test_log_capture_has_state_but_no_plaintext_mailbox():
    logs: list[str] = []
    recover_coupang_mailbox(
        mailbox_id=FAKE_EMAIL,
        recover=_recover(False),
        log=logs.append,
        sleep=lambda _s: None,
    )
    joined = "\n".join(logs)
    assert FAKE_EMAIL not in joined
    # recover==False 는 이제 RECOVERY_FAILED 로 닫힌다(캡차/데드 USER_ACTION 아님).
    assert STATE_RECOVERY_FAILED in joined


def test_secret_storage_policy_email_app_password_agent_local_otp_not_stored():
    assert classify_secret_storage("verification_email_app_password") == SECRET_STORAGE_AGENT_LOCAL
    assert classify_secret_storage("otp") == SECRET_STORAGE_NOT_STORED


def test_build_coupang_recover_default_recover_session_consumes_reuse():
    import inspect

    default = inspect.signature(build_coupang_recover).parameters["recover_session"].default
    assert default is recover_coupang_session_with_email_2fa


def test_import_is_safe_no_heavy_deps_on_non_windows():
    code = (
        "import sys\n"
        "import importlib\n"
        "importlib.import_module('rider_agent.auth.coupang_' + 'gmail' + '_2fa')\n"
        "heavy = ('googleapiclient','crawl4ai','playwright','pyautogui','pywinauto','pyperclip')\n"
        "print(sorted(m for m in heavy if m in sys.modules))\n"
    )
    result = _run_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


def test_module_imports_are_sync_unidirectional_rider_crawl_only():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    roots: set[str] = set()
    has_async = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            has_async = True
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                roots.add(node.module.split(".")[0])

    assert has_async is False
    assert "asyncio" not in roots
    assert "rider_server" not in roots
    third_party = roots - set(sys.stdlib_module_names) - {"rider_agent", "__future__"}
    assert third_party <= {"rider_crawl"}, third_party


# ══════════════════════════════════════════════════════════════════════════
# Task 3 — AUTH_COUPANG_2FA job 실행자(기존 primitive 승격 · 결과 표면화 · secret 0)
# ══════════════════════════════════════════════════════════════════════════


def test_execute_auth_coupang_2fa_job_returns_active_on_recovered():
    """Successful email 2FA reports account ACTIVE."""
    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=lambda: True,
        secret_resolver=_FAKE_SECRET_MAP.get,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert result.error_code is None
    assert result.result_json == {
        "target_id": "target-1",
        "platform": "coupang",
        "platform_account_id": "account-1",
        "recovery_mode": RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA,
        "auth_state": AUTH_STATE_ACTIVE,
        "auth_recovery_state": STATE_RECOVERED,
    }


def test_execute_auth_coupang_2fa_job_keeps_account_id_from_nested_claim_payload():
    """Server claim responses may keep the original job payload under payload."""
    nested_payload = dict(_auth_2fa_job(account_id="account-nested").payload)
    job = ClaimedJob(
        job_id="job-auth-2fa-nested",
        type=CAPABILITY_AUTH_COUPANG_2FA,
        target_id="target-1",
        lease_expires_at=5_000_000_000.0,
        payload={
            "job_id": "job-auth-2fa-nested",
            "type": CAPABILITY_AUTH_COUPANG_2FA,
            "target_id": "target-1",
            "payload": nested_payload,
        },
    )

    result = execute_auth_coupang_2fa_job(
        job,
        recover=lambda: True,
        secret_resolver=_FAKE_SECRET_MAP.get,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["platform_account_id"] == "account-nested"


def test_execute_auth_coupang_2fa_job_maps_false_to_retryable_auth_required():
    """recover==False(2FA 미완, 캡차 아님)는 재시도 가능한 AUTH_REQUIRED 로 닫는다.

    데드 상태(USER_ACTION_PENDING)로 고착시키지 않는다 — 정상 세션인데 2FA 플로우 요소를 못
    찾은 경우가 영구 차단되던 회귀를 막는다.
    """
    calls = []
    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=lambda: calls.append(1) or False,
        secret_resolver=_FAKE_SECRET_MAP.get,
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    # account coarse gate 는 AUTH_REQUIRED(USER_ACTION_PENDING 데드 상태 아님).
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert result.result_json["auth_recovery_state"] == STATE_RECOVERY_FAILED
    assert result.result_json["reason"] != REASON_CAPTCHA_OR_ABNORMAL
    assert result.result_json["recovery_mode"] == RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA
    assert calls == [1, 1, 1]  # 재시도 후 종료


def test_execute_auth_coupang_2fa_job_maps_captcha_to_user_action_pending():
    """실제 캡차(CoupangCaptchaError)만 USER_ACTION_PENDING(사람 조치)로 닫는다."""
    from rider_crawl.auth.coupang_email_2fa import CoupangCaptchaError

    calls = []

    def recover():
        calls.append(1)
        raise CoupangCaptchaError("captcha")

    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=recover,
        secret_resolver=_FAKE_SECRET_MAP.get,
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == AUTH_STATE_USER_ACTION_PENDING
    assert result.result_json["auth_recovery_state"] == STATE_USER_ACTION_REQUIRED
    assert result.result_json["reason"] == REASON_CAPTCHA_OR_ABNORMAL
    assert calls == [1]  # 캡차 → 즉시 멈춤(재시도 0)


def test_execute_auth_coupang_2fa_job_maps_mail_auth_to_auth_required_detail():
    """Mailbox re-auth is visible as auth_recovery_state without leaking secrets."""

    class _EmailAuthError(RuntimeError):
        pass

    def recover():
        raise _EmailAuthError(f"auth failed pw={FAKE_APP_PASSWORD}")

    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=recover,
        secret_resolver=_FAKE_SECRET_MAP.get,
        is_email_auth_required=lambda exc: isinstance(exc, _EmailAuthError),
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert result.result_json["auth_recovery_state"] == STATE_EMAIL_AUTH_REQUIRED
    assert result.result_json["reason"] == REASON_EMAIL_AUTH

    blob = json.dumps(
        {
            "result_json": result.result_json,
            "metrics": result.metrics,
            "error_message_redacted": result.error_message_redacted,
        },
        ensure_ascii=False,
    )
    assert FAKE_APP_PASSWORD not in blob
    assert FAKE_EMAIL not in blob  # 평문 이메일은 결과에 없다(ref 만)


def test_recover_coupang_mailbox_preserves_safe_email_auth_reason():
    from rider_crawl.auth.imap_2fa import ImapAuthError

    def recover():
        raise ImapAuthError(
            "IMAP 로그인 실패. 메일 설정을 확인하세요.",
            reason="mail_app_password_invalid",
        )

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=recover,
        locks=MailboxLockRegistry(),
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_EMAIL_AUTH_REQUIRED
    assert result.result_json["state"] == STATE_EMAIL_AUTH_REQUIRED
    assert result.result_json["reason"] == "mail_app_password_invalid"


def test_recover_coupang_mailbox_falls_back_to_email_auth_required_reason():
    from rider_crawl.auth.imap_2fa import ImapAuthError

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=ImapAuthError("imap login failed")),
        locks=MailboxLockRegistry(),
        sleep=lambda _s: None,
    )

    assert result.result_json["state"] == STATE_EMAIL_AUTH_REQUIRED
    assert result.result_json["reason"] == REASON_EMAIL_AUTH


def test_execute_auth_coupang_2fa_job_preserves_safe_mailbox_reason_without_raw_imap():
    from rider_crawl.auth.imap_2fa import ImapAuthError

    def recover():
        raise ImapAuthError(
            f"AUTHENTICATIONFAILED invalid credentials pw={FAKE_APP_PASSWORD} otp={FAKE_OTP}",
            reason="mail_app_password_invalid",
        )

    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=recover,
        secret_resolver=_FAKE_SECRET_MAP.get,
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_recovery_state"] == STATE_EMAIL_AUTH_REQUIRED
    assert result.result_json["reason"] == "mail_app_password_invalid"

    blob = json.dumps(
        {
            "result_json": result.result_json,
            "metrics": result.metrics,
            "error_message_redacted": result.error_message_redacted,
        },
        ensure_ascii=False,
    )
    assert FAKE_APP_PASSWORD not in blob
    assert FAKE_OTP not in blob
    assert "AUTHENTICATIONFAILED" not in blob


def test_execute_auth_coupang_2fa_job_mail_delay_is_recovery_failed():
    """Slow mail beyond bounded timeout reports RECOVERY_FAILED / verification_mail_delayed."""
    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=lambda: (_ for _ in ()).throw(RuntimeError("mail not arrived")),
        secret_resolver=_FAKE_SECRET_MAP.get,
        max_attempts=1,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert result.result_json["auth_recovery_state"] == STATE_RECOVERY_FAILED
    assert result.result_json["reason"] == REASON_MAIL_DELAY


def test_execute_auth_coupang_2fa_job_browser_failure_is_not_mail_delay():
    """Chrome/CDP failure is surfaced separately from delayed verification mail."""
    from rider_agent.reuse import CdpUnavailableError

    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=lambda: (_ for _ in ()).throw(CdpUnavailableError("CDP endpoint unavailable")),
        secret_resolver=_FAKE_SECRET_MAP.get,
        max_attempts=1,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_recovery_state"] == STATE_RECOVERY_FAILED
    assert result.result_json["reason"] == REASON_BROWSER_UNAVAILABLE


def test_execute_auth_coupang_2fa_job_fails_closed_when_secret_ref_unresolved():
    """Missing required refs fail closed with AUTH_REQUIRED + fixed reason; no recovery attempt."""
    recover_calls = []
    # 핸들 모양(dpapi:)인데 resolver 가 해소하지 못함 → verification_email_address 빈값.
    job = _auth_2fa_job(
        payload={"verification_email_address_ref": "dpapi:mailbox-handle-unresolvable"}
    )
    result = execute_auth_coupang_2fa_job(
        job,
        recover=lambda: recover_calls.append(1) or True,
        secret_resolver=lambda _ref: None,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_recovery_state"] == STATE_RECOVERY_FAILED
    assert result.result_json["reason"] == REASON_SECRET_REF_UNRESOLVED
    assert recover_calls == []  # ref 미해소 → 브라우저/IMAP 미접근


def test_auth_coupang_2fa_job_uses_mailbox_lock_once():
    """Same mailbox is serialized and recovery is bounded."""
    reg = MailboxLockRegistry()
    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    def recover():
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.02)
        with guard:
            state["active"] -= 1
        return True

    results = []

    def worker():
        results.append(
            execute_auth_coupang_2fa_job(
                _auth_2fa_job(),
                recover=recover,
                secret_resolver=_FAKE_SECRET_MAP.get,
                locks=reg,
                sleep=lambda _s: None,
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 같은 mailbox 는 직렬화 — 동시에 두 번 recover 가 돌지 않는다.
    assert state["max"] == 1
    assert all(r.status == JOB_STATUS_SUCCESS for r in results)


def test_execute_auth_2fa_default_path_fails_closed_on_missing_app_password():
    """이메일 주소만 있고 app password ref 가 비면 브라우저/IMAP 를 열기 전에 fail-closed(검토 Medium)."""

    # mailbox address 는 풀리지만 app password ref 는 payload 에서 빈값 → 필수 secret 누락.
    job = _auth_2fa_job(payload={"verification_email_app_password_ref": ""})
    result = execute_auth_coupang_2fa_job(
        job,  # recover 미주입 → 기본(브라우저/IMAP) 경로 → 필수 secret 검증
        secret_resolver=_FAKE_SECRET_MAP.get,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_recovery_state"] == STATE_RECOVERY_FAILED
    assert result.result_json["reason"] == REASON_SECRET_REF_UNRESOLVED


def test_execute_auth_2fa_injected_recover_skips_required_secret_check():
    """주입 recover(브라우저/IMAP 미사용)는 필수 secret 강제 대상이 아니다 — 회귀 방지."""

    # 기본 경로라면 막혔을 app password 누락이라도, 주입 recover 면 통과해야 한다.
    job = _auth_2fa_job(payload={"verification_email_app_password_ref": ""})
    result = execute_auth_coupang_2fa_job(
        job,
        recover=lambda: True,  # 주입 → secret 강제 우회
        secret_resolver=_FAKE_SECRET_MAP.get,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == AUTH_STATE_ACTIVE


def test_default_is_email_auth_required_detects_imap_auth_failure():
    """운영 predicate 가 IMAP 로그인/설정 실패를 EMAIL_AUTH_REQUIRED 로 본다(검토 Medium)."""
    from rider_crawl.auth.coupang_email_2fa import Coupang2faError
    from rider_crawl.auth.imap_2fa import Imap2faError, ImapAuthError

    pred = agent_email_2fa.default_is_email_auth_required

    # 직접 ImapAuthError.
    assert pred(ImapAuthError("login failed")) is True
    # Coupang2faError(email_auth_required=True) — recover_coupang_session 이 올리는 형태.
    assert pred(Coupang2faError("imap login failed", email_auth_required=True)) is True
    # 예외 체인으로 ImapAuthError 가 cause 인 경우.
    wrapped = Coupang2faError("wrapped")
    wrapped.__cause__ = ImapAuthError("login failed")
    assert pred(wrapped) is True
    # 메일 지연(코드 미수신) 같은 일시적 실패는 email-auth 가 아니다 → False.
    assert pred(Imap2faError("코드 미수신")) is False
    assert pred(Coupang2faError("mail delayed")) is False
    assert pred(RuntimeError("unrelated")) is False


def test_execute_auth_2fa_default_predicate_maps_imap_auth_to_email_auth_required():
    """라우터가 predicate 를 안 넘겨도 기본값(운영 predicate)으로 EMAIL_AUTH_REQUIRED 가 표면화된다."""
    from rider_crawl.auth.imap_2fa import ImapAuthError

    def recover():
        raise ImapAuthError(f"imap login failed pw={FAKE_APP_PASSWORD}")

    # is_email_auth_required 미지정 → sentinel → 운영 predicate 적용.
    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=recover,
        secret_resolver=_FAKE_SECRET_MAP.get,
        max_attempts=3,
        sleep=lambda _s: None,
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.result_json["auth_recovery_state"] == STATE_EMAIL_AUTH_REQUIRED
    assert result.result_json["reason"] == REASON_EMAIL_AUTH
    blob = json.dumps(result.result_json, ensure_ascii=False) + str(result.metrics)
    assert FAKE_APP_PASSWORD not in blob


def test_execute_auth_2fa_explicit_none_predicate_disables_classification():
    """명시적 None 은 분류 비활성화로 존중된다 — email-auth 신호가 있어도 RECOVERY_FAILED."""
    from rider_crawl.auth.imap_2fa import ImapAuthError

    def recover():
        raise ImapAuthError("imap login failed")

    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),
        recover=recover,
        secret_resolver=_FAKE_SECRET_MAP.get,
        is_email_auth_required=None,  # 명시적 비활성화
        max_attempts=1,
        sleep=lambda _s: None,
    )

    assert result.result_json["auth_recovery_state"] == STATE_RECOVERY_FAILED


def test_default_recover_runs_inside_open_playwright_context(monkeypatch):
    """기본 recover 는 Playwright 컨텍스트가 **열려 있는 동안** page 를 운전한다(검토 High).

    과거엔 ``_acquire_coupang_auth_page`` 가 ``with _sync_playwright()`` 를 빠져나간 뒤 page 를
    돌려줘, 복구가 닫힌(CDP 끊긴) 컨텍스트의 page 를 운전하는 use-after-close 버그였다.
    """
    from types import SimpleNamespace

    state = {"closed": False, "recover_ran_while_open": None}

    class FakePage:
        pass

    fake_page = FakePage()

    class FakePlaywright:
        def __init__(self):
            self.chromium = SimpleNamespace(connect_over_cdp=lambda _cdp: SimpleNamespace())

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            state["closed"] = True
            return False

    monkeypatch.setattr(
        "rider_agent.auth.baemin_auth._sync_playwright", lambda: FakePlaywright()
    )
    # coupang crawler page 선택 헬퍼를 가짜 page 로 단축(브라우저 페이지/로그인 화면 판정 무력화).
    import rider_crawl.platforms.coupang.crawler as coupang_crawler

    monkeypatch.setattr(coupang_crawler, "_browser_pages", lambda _b: [fake_page])
    monkeypatch.setattr(coupang_crawler, "_login_required_page", lambda _pages: fake_page)
    monkeypatch.setattr(
        coupang_crawler, "_page_looks_like_coupang_login_required", lambda _p: True
    )

    # build_coupang_recover 가 돌려줄 closure 가 실행되는 시점에 컨텍스트가 아직 열려 있어야 한다.
    def fake_build_coupang_recover(*, page, **_kw):
        assert page is fake_page

        def _inner():
            state["recover_ran_while_open"] = not state["closed"]
            return True

        return _inner

    monkeypatch.setattr(agent_email_2fa, "build_coupang_recover", fake_build_coupang_recover)

    result = execute_auth_coupang_2fa_job(
        _auth_2fa_job(),  # recover 미주입 → 기본 경로(_default_coupang_recover) 사용
        secret_resolver=_FAKE_SECRET_MAP.get,
        sleep=lambda _s: None,
    )

    assert state["recover_ran_while_open"] is True  # 컨텍스트가 열린 채로 복구가 돌았다
    assert state["closed"] is True  # 복구 후 컨텍스트는 닫혔다
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == AUTH_STATE_ACTIVE


def test_build_coupang_auth_execute_job_routes_only_auth_coupang_2fa():
    """Router intercepts AUTH_COUPANG_2FA and passes everything else to fallback."""
    fallback_jobs = []

    def fallback(job):
        fallback_jobs.append(job)
        from rider_agent.job_loop import make_success_result

        return make_success_result(result_json={"fallback": True})

    execute = build_coupang_auth_execute_job(
        fallback=fallback,
        recover=lambda: True,
        secret_resolver=_FAKE_SECRET_MAP.get,
        sleep=lambda _s: None,
    )

    auth_result = execute(_auth_2fa_job())
    other = ClaimedJob(job_id="j2", type="OPEN_AUTH_BROWSER", target_id="t2", payload={})
    other_result = execute(other)

    assert auth_result.result_json["auth_state"] == AUTH_STATE_ACTIVE
    assert auth_result.result_json["auth_recovery_state"] == STATE_RECOVERED
    assert fallback_jobs == [other]  # AUTH_COUPANG_2FA 외 type 은 fallback 으로
    assert other_result.result_json == {"fallback": True}
