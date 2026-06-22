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

FAKE_MBX_1 = "mailbox-fake-1"
FAKE_MBX_2 = "mailbox-fake-2"
FAKE_APP_PASSWORD = "fake app password value"
FAKE_OTP = "otp-fake-654321"
FAKE_EMAIL = "operator@example.com"

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
    assert classify_coupang_2fa_outcome(recovered=False) == STATE_USER_ACTION_REQUIRED
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


def test_recover_false_stops_user_action_required_no_retry():
    calls = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(False, calls=calls),
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
    assert STATE_USER_ACTION_REQUIRED in joined


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
