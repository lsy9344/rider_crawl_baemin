"""Story 4.9 — 쿠팡 Gmail 2FA mailbox 분리 + mailbox lock + 실패 분류 + bounded 복구 검증.

외부 호출 없음: 실제 Gmail API/실 쿠팡 인증 화면/실 DPAPI/실 token 파일/실 시계 미사용.
``recover``/``store``(fake codec)/``now``/``sleep``/``fetch_code``/``recover_session`` 을 모두
주입 fake + 호출 카운터/타임스탬프/active-count 로 대체해 token 분리·lock 직렬화·분류·bounded-
stop·누출가드를 결정적으로 검증한다(비-Windows CI 에서도 통과 — import-safety). 값은 명백한
가짜값만(``mailbox-fake-…``/``otp-fake-…``/``…-fake-token``) — 실 OTP/token/이메일/비밀번호 원문
없음. ``rider_agent.__main__`` 은 top-import 하지 않는다(runpy 경고 회피 —
memory/agent-main-runpy-warning). 부정 가드(sync·단방향)는 raw grep 이 아니라 AST import-edge
로 검사한다(memory/negative-guard-tests-use-ast).
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from rider_agent.auth.coupang_gmail_2fa import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    ERROR_GMAIL_REAUTH_REQUIRED,
    ERROR_RECOVERY_FAILED,
    ERROR_USER_ACTION_REQUIRED,
    REASON_CAPTCHA_OR_ABNORMAL,
    REASON_GMAIL_REAUTH,
    REASON_MAIL_DELAY,
    REASON_REPEATED_FAILURE,
    STATE_GMAIL_REAUTH_REQUIRED,
    STATE_RECOVERED,
    STATE_RECOVERY_FAILED,
    STATE_USER_ACTION_REQUIRED,
    MailboxLockRegistry,
    build_coupang_recover,
    classify_coupang_2fa_outcome,
    mailbox_token_path,
    mailbox_token_ref,
    recover_coupang_mailbox,
    resolve_mailbox_token,
    store_mailbox_token,
)
from rider_agent.job_loop import JOB_STATUS_FAILED, JOB_STATUS_SUCCESS
from rider_agent.reuse import recover_coupang_session_with_email_2fa
from rider_agent.secure_store import DpapiSecretStore
from rider_crawl.secret_store import (
    SECRET_STORAGE_AGENT_LOCAL,
    SECRET_STORAGE_NOT_STORED,
    classify_secret_storage,
)

# 가짜 식별자만(누출 가드 — 실 token/OTP/이메일/비밀번호 금지).
FAKE_MBX_1 = "mailbox-fake-1"
FAKE_MBX_2 = "mailbox-fake-2"
FAKE_TOKEN = "gmail-oauth-fake-token-value"
FAKE_OTP = "otp-fake-654321"
FAKE_EMAIL = "operator@example.com"

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
MODULE_PATH = SRC_DIR / "rider_agent" / "auth" / "coupang_gmail_2fa.py"


# ── 주입 fake codec: 비-Windows 에서도 결정적 round-trip. XOR 라 store 파일엔 평문이 안 남는다.
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
    """``build_coupang_recover`` 가 ``dataclasses.replace`` 로 token 경로만 바꾸는 데 필요한
    최소 frozen 설정(실 ``AppConfig`` 대신 — recover_session 도 주입 fake 라 다른 필드 불필요)."""

    gmail_token_path: Path = Path("secrets/google/token.gmail.json")


def _recover(value=None, *, raises=None, calls=None):
    """주입 fake recover — 고정 bool 반환 또는 예외 raise + 호출 카운트(calls 리스트)."""

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


# ══════════════════════════════════════════════════════════════════════════
# AC1 — mailbox 별 token 분리 저장(DpapiSecretStore 재사용)·서버 ref 만·고객 간 비공유
# ══════════════════════════════════════════════════════════════════════════


def test_store_resolve_round_trip_returns_ref_only(tmp_path):
    store = _store(tmp_path)
    ref = store_mailbox_token(store, FAKE_MBX_1, FAKE_TOKEN)
    assert ref == mailbox_token_ref(FAKE_MBX_1)
    assert resolve_mailbox_token(store, FAKE_MBX_1) == FAKE_TOKEN
    # 평문 token 은 store 파일(DPAPI blob)에만 — 반환 ref/파일 텍스트에 평문 0.
    assert FAKE_TOKEN not in ref
    assert FAKE_TOKEN not in store.path.read_text(encoding="utf-8")


def test_two_mailboxes_have_distinct_refs_and_no_cross_resolve(tmp_path):
    store = _store(tmp_path)
    store_mailbox_token(store, FAKE_MBX_1, "token-fake-A")
    store_mailbox_token(store, FAKE_MBX_2, "token-fake-B")
    # 두 다른 mailbox → 다른 ref(고객 간 token 비공유, ADD-15·ops:92).
    assert mailbox_token_ref(FAKE_MBX_1) != mailbox_token_ref(FAKE_MBX_2)
    # 한 mailbox 의 resolve 가 다른 mailbox token 을 돌려주지 않는다(교차 0).
    assert resolve_mailbox_token(store, FAKE_MBX_1) == "token-fake-A"
    assert resolve_mailbox_token(store, FAKE_MBX_2) == "token-fake-B"


def test_resolve_missing_mailbox_is_fail_closed_none(tmp_path):
    store = _store(tmp_path)
    assert resolve_mailbox_token(store, "mailbox-fake-absent") is None


def test_mailbox_token_ref_is_opaque_no_plaintext_email():
    ref = mailbox_token_ref(FAKE_EMAIL)
    assert ref.startswith("gmail:")
    assert FAKE_EMAIL not in ref  # 평문 이메일 비노출(redact 한계 회피)
    assert "@" not in ref and "operator" not in ref  # opaque/hashed 핸들
    # 결정적: 같은 mailbox → 같은 ref.
    assert mailbox_token_ref(FAKE_EMAIL) == ref


# ── AC1 회귀 트랩: per-mailbox token 경로 분기(token 공유 회귀를 fake 통과 뒤에 숨기지 않음) ──


def test_mailbox_token_path_branches_per_mailbox_no_shared_file():
    base = Path("secrets/google/token.gmail.json")
    p1 = mailbox_token_path(base, FAKE_MBX_1)
    p2 = mailbox_token_path(base, FAKE_MBX_2)
    assert p1 != p2  # 두 mailbox → 서로 다른 token 파일
    assert p1 != base and p2 != base  # 공유 token.gmail.json 으로 안 떨어짐
    # 파일 keyspace 로 평문 이메일이 새지 않는다(해시 핸들).
    assert FAKE_EMAIL not in str(mailbox_token_path(base, FAKE_EMAIL))


def test_build_coupang_recover_wires_distinct_token_path_per_mailbox():
    # 🚨 회귀 트랩 명시 단언: 기본 recover 배선이 두 다른 mailbox 에 다른 token 경로를 쓰는지.
    # spy recover_session 이 받은 config.gmail_token_path 가 mailbox 간 분기해야 한다.
    seen: dict[str, Path] = {}

    def spy_recover_session(page, config, *, fetch_code=None, **kw):
        seen[page] = config.gmail_token_path
        return True

    base = _FakeConfig()
    r1 = build_coupang_recover(
        page="page-1", config=base, mailbox_id=FAKE_MBX_1, recover_session=spy_recover_session
    )
    r2 = build_coupang_recover(
        page="page-2", config=base, mailbox_id=FAKE_MBX_2, recover_session=spy_recover_session
    )
    assert r1() is True and r2() is True
    assert seen["page-1"] != seen["page-2"]  # 분기됨(token 공유 회귀 아님)
    assert seen["page-1"] != base.gmail_token_path  # 공유 파일로 안 떨어짐
    assert seen["page-2"] != base.gmail_token_path


def test_build_coupang_recover_passes_injected_fetch_code_to_reuse():
    captured: dict[str, object] = {}

    def spy_recover_session(page, config, *, fetch_code=None, **kw):
        captured["fetch_code"] = fetch_code
        return True

    def spy_fetch_code(**kwargs):
        return "code-fake"

    recover = build_coupang_recover(
        page="p",
        config=_FakeConfig(),
        mailbox_id=FAKE_MBX_1,
        recover_session=spy_recover_session,
        fetch_code=spy_fetch_code,
    )
    recover()
    # OTP 조회는 reuse 가 수행 — 4.9 는 fetch_code 를 그대로 넘기기만(재구현 0).
    assert captured["fetch_code"] is spy_fetch_code


# ══════════════════════════════════════════════════════════════════════════
# AC2 — mailbox lock: 같은 mailbox 직렬화(겹쳐 실행 0)·다른 mailbox 병렬·결정적 해제
# ══════════════════════════════════════════════════════════════════════════


def test_lock_for_same_mailbox_returns_same_object_distinct_per_mailbox():
    reg = MailboxLockRegistry()
    assert reg.lock_for(FAKE_MBX_1) is reg.lock_for(FAKE_MBX_1)  # 같은 id → 같은 객체
    assert reg.lock_for(FAKE_MBX_1) is not reg.lock_for(FAKE_MBX_2)  # 다른 id → 독립 lock


def test_acquire_releases_lock_even_on_exception():
    reg = MailboxLockRegistry()
    lock = reg.lock_for(FAKE_MBX_1)
    with pytest.raises(ValueError):
        with reg.acquire(FAKE_MBX_1):
            raise ValueError("boom (fake)")
    # finally 로 해제됐으면 재획득 가능(hang 0).
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_same_mailbox_recoveries_are_serialized_max_active_one():
    reg = MailboxLockRegistry()
    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    def recover():
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.02)  # 겹칠 기회를 준다 — lock 이 막아 max active 는 1 이어야 한다
        with guard:
            state["active"] -= 1
        return True

    results = []

    def worker():
        results.append(
            recover_coupang_mailbox(
                mailbox_id=FAKE_MBX_1, recover=recover, locks=reg, sleep=lambda s: None
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["max"] == 1  # 같은 mailbox 두 복구가 겹치지 않음(직렬화)
    assert all(r.status == JOB_STATUS_SUCCESS for r in results)


def test_different_mailbox_recoveries_run_in_parallel():
    reg = MailboxLockRegistry()
    barrier = threading.Barrier(2, timeout=3.0)
    broke: list[bool] = []

    def recover():
        try:
            barrier.wait()  # 둘 다 도달해야 통과 — 같은 lock 으로 직렬화되면 deadlock→Broken
        except threading.BrokenBarrierError:
            broke.append(True)
            return False
        return True

    results = []

    def worker(mbx):
        results.append(
            recover_coupang_mailbox(
                mailbox_id=mbx, recover=recover, locks=reg, sleep=lambda s: None
            )
        )

    threads = [
        threading.Thread(target=worker, args=(m,)) for m in (FAKE_MBX_1, FAKE_MBX_2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert broke == []  # 둘 다 barrier 통과 → 다른 mailbox 는 병렬(직렬화 안 됨)
    assert all(r.status == JOB_STATUS_SUCCESS for r in results)


# ══════════════════════════════════════════════════════════════════════════
# AC3 — 실패 분류기: recovered/CAPTCHA/reauth/transient → 평문 상태(secret 미접근)
# ══════════════════════════════════════════════════════════════════════════


def test_classify_recovered_true_is_active():
    assert classify_coupang_2fa_outcome(recovered=True) == STATE_RECOVERED == "ACTIVE"


def test_classify_recovered_false_is_user_action_required():
    assert (
        classify_coupang_2fa_outcome(recovered=False)
        == STATE_USER_ACTION_REQUIRED
        == "USER_ACTION_REQUIRED"
    )


def test_classify_reauth_signal_is_gmail_reauth_required():
    assert (
        classify_coupang_2fa_outcome(is_reauth=True)
        == STATE_GMAIL_REAUTH_REQUIRED
        == "GMAIL_REAUTH_REQUIRED"
    )
    # reauth 분류된 error 도 동일(error 가 있어도 reauth 우선).
    assert (
        classify_coupang_2fa_outcome(error=RuntimeError("x (fake)"), is_reauth=True)
        == STATE_GMAIL_REAUTH_REQUIRED
    )


def test_classify_transient_error_is_recovery_failed():
    assert (
        classify_coupang_2fa_outcome(error=RuntimeError("mail delayed (fake)"))
        == STATE_RECOVERY_FAILED
    )


def test_classify_ambiguous_input_is_fail_closed_recovery_failed():
    assert classify_coupang_2fa_outcome() == STATE_RECOVERY_FAILED


def test_classify_does_not_surface_secret_in_error_message():
    # 분류기는 상태 문자열만 돌려준다 — 예외 메시지(코드/token)를 읽거나 surfacing 하지 않는다.
    state = classify_coupang_2fa_outcome(error=RuntimeError(f"code={FAKE_OTP}"))
    assert state == STATE_RECOVERY_FAILED
    assert FAKE_OTP not in state


def test_user_action_required_is_not_baemin_user_action_pending():
    # 쿠팡 USER_ACTION_REQUIRED 를 배민 USER_ACTION_PENDING 과 혼동하지 않는다(다른 상태머신).
    assert STATE_USER_ACTION_REQUIRED == "USER_ACTION_REQUIRED"
    assert STATE_USER_ACTION_REQUIRED != "USER_ACTION_PENDING"


def test_plain_states_absent_from_rider_server_enums():
    # rider_agent 코드는 rider_server 를 import 하지 않는다 — 테스트에서만 값 정합 확인.
    # 쿠팡 상태는 spec data-api-contract 정본 평문이고 rider_server enum 에 없다(평문 상수 유지).
    from rider_server.domain.states import BaeminAuthState, FailureCategory

    baemin = {s.value for s in BaeminAuthState}
    failures = {f.value for f in FailureCategory}
    assert STATE_USER_ACTION_REQUIRED not in baemin and STATE_USER_ACTION_REQUIRED not in failures
    assert STATE_GMAIL_REAUTH_REQUIRED not in baemin and STATE_GMAIL_REAUTH_REQUIRED not in failures
    assert "USER_ACTION_PENDING" in baemin  # 배민 전용(혼동 회피 대상)


# ══════════════════════════════════════════════════════════════════════════
# AC2·3·4 — bounded orchestrator: 분류 표면화 + 상한 + 반복 인증 요청 0
# ══════════════════════════════════════════════════════════════════════════


def test_recover_success_surfaces_active_result_json():
    calls = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1, recover=_recover(True, calls=calls), sleep=lambda s: None
    )
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json == {
        "mailbox_ref": mailbox_token_ref(FAKE_MBX_1),
        "state": STATE_RECOVERED,
    }
    assert len(calls) == 1


def test_recover_false_stops_user_action_required_no_retry():
    calls = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(False, calls=calls),
        max_attempts=3,
        sleep=lambda s: None,
    )
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_USER_ACTION_REQUIRED
    assert result.result_json["state"] == STATE_USER_ACTION_REQUIRED
    assert result.result_json["reason"] == REASON_CAPTCHA_OR_ABNORMAL
    assert result.metrics["reason"] == REASON_CAPTCHA_OR_ABNORMAL
    # CAPTCHA/이상 로그인 → 재시도 0(재요청 0) — max_attempts 가 3이어도 1회만.
    assert len(calls) == 1


def test_recover_reauth_signal_stops_gmail_reauth_required_no_retry():
    calls = []

    class _ReauthError(RuntimeError):
        pass

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=_ReauthError("reauth (fake)"), calls=calls),
        is_reauth=lambda exc: isinstance(exc, _ReauthError),
        max_attempts=3,
        sleep=lambda s: None,
    )
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_GMAIL_REAUTH_REQUIRED
    assert result.result_json["state"] == STATE_GMAIL_REAUTH_REQUIRED
    assert result.metrics["reason"] == REASON_GMAIL_REAUTH
    assert len(calls) == 1  # 재승인 필요 → 재시도 무의미·즉시 멈춤


def test_recover_transient_error_bounded_by_max_attempts():
    calls = []
    sleeps = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=RuntimeError("mail delayed (fake)"), calls=calls),
        max_attempts=3,
        backoff_seconds=2.0,
        sleep=lambda s: sleeps.append(s),
    )
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_RECOVERY_FAILED
    assert result.result_json["state"] == STATE_RECOVERY_FAILED
    assert len(calls) == 3  # 상한까지만 재시도
    assert len(calls) <= 3  # 무한 재시도 0
    assert sleeps == [2.0, 2.0]  # backoff 는 attempts-1 회, 주입 sleep 으로 결정적
    assert result.metrics["attempts"] == 3
    assert result.metrics["reason"] == REASON_REPEATED_FAILURE


def test_recover_succeeds_on_nth_attempt_then_no_more_calls():
    counter = {"n": 0}
    calls = []

    def recover():
        calls.append(1)
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError("mail delayed (transient fake)")
        return True

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1, recover=recover, max_attempts=5, sleep=lambda s: None
    )
    assert result.status == JOB_STATUS_SUCCESS
    assert len(calls) == 3  # 3회째 성공, 이후 호출 0


def test_recover_single_transient_default_reason_is_mail_delay():
    # 기본 max_attempts=1 → 단일 transient 는 mail_delay 사유(반복 실패가 아님).
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=RuntimeError("delay (fake)")),
        sleep=lambda s: None,
    )
    assert result.error_code == ERROR_RECOVERY_FAILED
    assert result.metrics["reason"] == REASON_MAIL_DELAY


def test_default_max_recovery_attempts_is_finite_small():
    assert DEFAULT_MAX_RECOVERY_ATTEMPTS >= 1
    assert DEFAULT_MAX_RECOVERY_ATTEMPTS < 10  # 기존 1회 정책 본떠 작게(무한 재시도 0)


# ══════════════════════════════════════════════════════════════════════════
# AC3·AC4 — 누출 가드: OTP/token/refresh/비밀번호/평문 mailbox 0(ref·상태·고정 사유만)
# ══════════════════════════════════════════════════════════════════════════


def test_no_secret_leaks_even_if_recover_raises_with_secrets():
    secret_blob = f"boom code={FAKE_OTP} token=oauth-fake-zzz refresh=rt-fake-yyy"

    def recover():
        raise RuntimeError(secret_blob)  # reuse 예외에 secret 이 실려도

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_EMAIL,  # 평문 이메일이 mailbox_id 라도 ref 는 해시
        recover=recover,
        max_attempts=1,
        sleep=lambda s: None,
    )
    blob = json.dumps(
        {
            "result_json": result.result_json,
            "metrics": result.metrics,
            "error_message_redacted": result.error_message_redacted,
        },
        ensure_ascii=False,
    )
    assert FAKE_OTP not in blob  # OTP 0
    assert "oauth-fake-zzz" not in blob and "rt-fake-yyy" not in blob  # token/refresh 0
    assert FAKE_EMAIL not in blob  # 평문 mailbox 0(ref 만)
    assert result.result_json["mailbox_ref"] == mailbox_token_ref(FAKE_EMAIL)


def test_log_capture_has_state_but_no_plaintext_mailbox():
    logs: list[str] = []
    recover_coupang_mailbox(
        mailbox_id=FAKE_EMAIL,
        recover=_recover(False),
        log=logs.append,
        sleep=lambda s: None,
    )
    joined = "\n".join(logs)
    assert FAKE_EMAIL not in joined  # log 에도 평문 mailbox 0
    assert STATE_USER_ACTION_REQUIRED in joined  # 상태는 운영 표면에 남는다


# ══════════════════════════════════════════════════════════════════════════
# import-safety + 단방향 + sync(4.1 가드가 rglob 로 자동 적용 — 명시 케이스 1개)
# ══════════════════════════════════════════════════════════════════════════


def test_import_is_safe_no_heavy_deps_on_non_windows():
    code = (
        "import sys\n"
        "import rider_agent.auth.coupang_gmail_2fa\n"
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
    assert has_async is False  # async 0
    assert "asyncio" not in roots  # 직접 이벤트 루프 0
    assert "rider_server" not in roots  # 단방향(rider_server import 0)
    third_party = roots - set(sys.stdlib_module_names) - {"rider_agent", "__future__"}
    assert third_party <= {"rider_crawl"}, third_party  # 유일 third-party root


# ══════════════════════════════════════════════════════════════════════════
# QA gap pass (qa-generate-e2e-tests) — AC 빈틈 보강
#   기존 30 케이스가 못 잠근 동작만 결정적으로 추가한다(외부 호출 0, 주입 fake 만):
#   저장 분류 정합·reauth predicate fail-closed·실패 경로 lock 해제·reuse 위임 계약·
#   성공 경로 누출 가드·기본 등록부 직렬화·성공 attempts·분류 우선순위.
# ══════════════════════════════════════════════════════════════════════════


def test_secret_storage_policy_gmail_token_agent_local_otp_not_stored():
    # (AC1.2) mailbox token helper 가 의존하는 저장 분류 정합을 잠근다:
    # gmail_oauth_token=agent_local(영속·DPAPI), otp=not_stored(읽어 입력 후 폐기 — store 0).
    assert classify_secret_storage("gmail_oauth_token") == SECRET_STORAGE_AGENT_LOCAL
    assert classify_secret_storage("otp") == SECRET_STORAGE_NOT_STORED


def test_reauth_predicate_that_raises_fails_closed_to_transient():
    # (AC3·AC4) 운영이 주입한 is_reauth predicate 자체가 던져도 reauth 로 오분류하지 않는다 —
    # fail-closed 로 transient(bounded) 취급해 GMAIL_REAUTH 로 가지 않는다(predicate 가
    # 분류를 오도하지 못함). 결과는 RECOVERY_FAILED.
    def recover():
        raise RuntimeError("boom (fake)")

    def is_reauth(exc):
        raise ValueError("predicate boom (fake)")

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=recover,
        is_reauth=is_reauth,
        max_attempts=1,
        sleep=lambda s: None,
    )
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_RECOVERY_FAILED  # GMAIL_REAUTH 아님
    assert result.result_json["state"] == STATE_RECOVERY_FAILED


def test_orchestrator_releases_lock_on_failure_path():
    # (AC2.6) 실패/예외 경로에서도 mailbox lock 이 항상 해제된다(finally) — 다음 복구가 hang 0.
    # 기존 직렬화 테스트는 성공 경로 해제만 증명한다.
    reg = MailboxLockRegistry()
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1,
        recover=_recover(raises=RuntimeError("transient (fake)")),
        locks=reg,
        max_attempts=1,
        sleep=lambda s: None,
    )
    assert result.status == JOB_STATUS_FAILED
    lock = reg.lock_for(FAKE_MBX_1)
    assert lock.acquire(blocking=False) is True  # 해제됐으면 재획득 가능(hang 0)
    lock.release()


def test_build_coupang_recover_default_recover_session_consumes_reuse():
    # (AC2.5/설계) 기본 recover_session 은 reuse seam 의 검증된 세션 복구 심볼이다 —
    # OTP 조회·요청시각 컷오프·query/customer 필터·코드 파싱을 4.9 가 재구현하지 않고 위임함을 잠근다.
    default = inspect.signature(build_coupang_recover).parameters["recover_session"].default
    assert default is recover_coupang_session_with_email_2fa


def test_success_result_surfaces_ref_only_no_plaintext_mailbox():
    # (AC3/NFR-5) 성공 경로도 평문 mailbox(이메일) 0 — result_json/metrics/log 에 해시 ref 만.
    # 기존 누출 가드는 실패 경로만 덮는다.
    logs: list[str] = []
    result = recover_coupang_mailbox(
        mailbox_id=FAKE_EMAIL,  # 평문 이메일이 mailbox_id 라도
        recover=_recover(True),
        log=logs.append,
        sleep=lambda s: None,
    )
    blob = json.dumps(
        {"result_json": result.result_json, "metrics": result.metrics},
        ensure_ascii=False,
    ) + "\n".join(logs)
    assert FAKE_EMAIL not in blob  # 평문 mailbox 0
    assert result.result_json["mailbox_ref"] == mailbox_token_ref(FAKE_EMAIL)
    assert result.result_json["state"] == STATE_RECOVERED


def test_default_registry_serializes_same_mailbox_across_calls():
    # (AC2) locks 미주입 → 프로세스 전역 _DEFAULT_LOCKS 공유 → 같은 mailbox 가 호출 간에도 직렬화.
    # (매 호출 새 registry 면 직렬화가 깨진다 — 모듈 주석이 경고하는 동작을 잠근다.)
    mbx = "mailbox-fake-default-shared"
    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    def recover():
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.02)  # 겹칠 기회 — 직렬화면 max active 는 1
        with guard:
            state["active"] -= 1
        return True

    def worker():
        recover_coupang_mailbox(mailbox_id=mbx, recover=recover, sleep=lambda s: None)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["max"] == 1  # 기본 등록부도 같은 mailbox 를 직렬화


def test_success_metrics_surface_attempt_count_on_nth_attempt():
    # (AC4·AC10) 재시도 후 성공도 attempts 를 운영 표면에 남긴다(bounded 카운터 관측 가능).
    counter = {"n": 0}

    def recover():
        counter["n"] += 1
        if counter["n"] < 2:
            raise RuntimeError("mail delayed (transient fake)")
        return True

    result = recover_coupang_mailbox(
        mailbox_id=FAKE_MBX_1, recover=recover, max_attempts=3, sleep=lambda s: None
    )
    assert result.status == JOB_STATUS_SUCCESS
    assert result.metrics["attempts"] == 2  # 2회째 성공


def test_classify_recovered_false_takes_precedence_over_reauth():
    # (AC3) 순수 함수 분류 우선순위: 명시적 recovered=False(CAPTCHA/이상 = 사람 조치)가
    # is_reauth 보다 우선해 USER_ACTION_REQUIRED 로 간다(더 보수적·조치 가능한 상태).
    assert (
        classify_coupang_2fa_outcome(recovered=False, is_reauth=True)
        == STATE_USER_ACTION_REQUIRED
    )
