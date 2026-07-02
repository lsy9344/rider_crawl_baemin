"""Story 5.6 / AC1 — PG repository 의 순수 헬퍼·정책 상수(항상 실행, DB 불필요).

``dashboard_repository_postgres`` 는 PG-gated 테스트(``tests/negative/test_dashboard_repository_pg.py``)
가 ``TEST_DATABASE_URL`` 없으면 skip 되는 탓에, 그 안의 **순수 헬퍼/상수**(SQL 과 무관한 결정
로직)가 CI 에서 한 번도 실행되지 않는 사각이 생긴다(memory/pg-gated-files-hide-pure-helpers).
이 파일은 그 순수 의미를 always-run 으로 끌어내 잠근다:

  (1) :func:`_pick_latest_code` — jobs/delivery_logs 후보 중 "더 최신 ts 의 error_code" 선택
      규칙 전수(둘 다 None·한쪽만·ts 비교·ts None 취급·동률 시 job 우선).
  (2) 정책 상수 — auth_session 인증대기 상태 집합·활성 job status·Telegram 오류 윈도가
      정본 어휘와 일치(드리프트 차단; 예: USER_ACTION_PENDING 누락 회귀).

fake 값만 — 실제 토큰/전화/이메일/chat_id 형태 없음. 평면 ``tests/server/`` 컨벤션.
``_pick_latest_code`` 는 ``.error_code``/``.ts`` 속성만 읽으므로 ``SimpleNamespace`` row 로 충분
하다(ORM/DB 불필요).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rider_server.admin import dashboard_repository_postgres as pg_repo
from rider_server.admin.dashboard_repository_postgres import (
    _ACTIVE_JOB_STATUSES,
    _AUTH_SESSION_PENDING_STATES,
    _TELEGRAM_ERROR_WINDOW,
    _agent_reports_kakao_runtime_unavailable,
    _pick_latest_code,
    _set_latest,
)
from rider_server.domain import BaeminAuthState
from rider_server.queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_RUNNING,
    JOB_TYPE_KAKAO_SEND,
    RESULT_REASON_STALE_CRAWL_SKIPPED,
)

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _row(code: str | None, ts: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(error_code=code, ts=ts)


# ── _pick_latest_code: "더 최신 ts 의 error_code" 선택 규칙 전수 ────────────────

def test_pick_latest_code_both_none_returns_none() -> None:
    assert _pick_latest_code(None, None) is None


def test_pick_latest_code_only_job_returns_job_code() -> None:
    assert _pick_latest_code(_row("CRAWL_FAILURE", _NOW), None) == "CRAWL_FAILURE"


def test_pick_latest_code_only_delivery_returns_delivery_code() -> None:
    assert _pick_latest_code(None, _row("TELEGRAM_FAILURE", _NOW)) == "TELEGRAM_FAILURE"


def test_pick_latest_code_picks_more_recent_delivery() -> None:
    job = _row("CRAWL_FAILURE", _NOW - timedelta(minutes=6))
    delivery = _row("TELEGRAM_FAILURE", _NOW - timedelta(minutes=1))
    assert _pick_latest_code(job, delivery) == "TELEGRAM_FAILURE"


def test_pick_latest_code_picks_more_recent_job() -> None:
    job = _row("CRAWL_FAILURE", _NOW - timedelta(minutes=1))
    delivery = _row("TELEGRAM_FAILURE", _NOW - timedelta(minutes=6))
    assert _pick_latest_code(job, delivery) == "CRAWL_FAILURE"


def test_pick_latest_code_delivery_ts_none_prefers_job() -> None:
    # delivery ts(None)는 "가장 오래된"으로 취급 → job 이 더 최신.
    job = _row("CRAWL_FAILURE", _NOW - timedelta(minutes=9))
    delivery = _row("TELEGRAM_FAILURE", None)
    assert _pick_latest_code(job, delivery) == "CRAWL_FAILURE"


def test_pick_latest_code_tie_prefers_job() -> None:
    # 동률 ts 면 결정적으로 job 우선.
    job = _row("CRAWL_FAILURE", _NOW)
    delivery = _row("TELEGRAM_FAILURE", _NOW)
    assert _pick_latest_code(job, delivery) == "CRAWL_FAILURE"


def test_pick_latest_code_both_ts_none_prefers_job() -> None:
    job = _row("CRAWL_FAILURE", None)
    delivery = _row("TELEGRAM_FAILURE", None)
    assert _pick_latest_code(job, delivery) == "CRAWL_FAILURE"


def test_set_latest_preserves_coupang_recovery_detail_from_latest_job() -> None:
    latest: dict[str, tuple[str, datetime | None, str | None, str | None]] = {}

    _set_latest(
        latest,
        "target-1",
        "AUTH_REQUIRED",
        _NOW,
        auth_recovery_state="RECOVERY_FAILED",
        auth_recovery_reason="verification_mail_delayed",
    )

    assert latest["target-1"] == (
        "AUTH_REQUIRED",
        _NOW,
        "RECOVERY_FAILED",
        "verification_mail_delayed",
    )


def test_set_latest_keeps_detail_from_more_recent_failure() -> None:
    latest: dict[str, tuple[str, datetime | None, str | None, str | None]] = {}

    _set_latest(
        latest,
        "target-1",
        "AUTH_REQUIRED",
        _NOW - timedelta(minutes=10),
        auth_recovery_state="RECOVERY_FAILED",
        auth_recovery_reason="verification_mail_delayed",
    )
    _set_latest(
        latest,
        "target-1",
        "AUTH_REQUIRED",
        _NOW,
        auth_recovery_state="USER_ACTION_REQUIRED",
        auth_recovery_reason="captcha_or_abnormal_login",
    )

    assert latest["target-1"] == (
        "AUTH_REQUIRED",
        _NOW,
        "USER_ACTION_REQUIRED",
        "captcha_or_abnormal_login",
    )


def test_display_failure_code_maps_stale_crawl_timeout_to_queue_skip() -> None:
    assert (
        pg_repo._display_failure_code(
            "CRAWL_TIMEOUT",
            {"reason": RESULT_REASON_STALE_CRAWL_SKIPPED},
        )
        == "STALE_CRAWL_SKIPPED"
    )


def test_display_failure_code_keeps_real_crawl_timeout() -> None:
    assert (
        pg_repo._display_failure_code("CRAWL_TIMEOUT", {"reason": "browser_timed_out"})
        == "CRAWL_TIMEOUT"
    )


# ── 정책 상수: 정본 어휘 드리프트 차단 ────────────────────────────────────────

def test_target_health_failure_job_types_exclude_lookup_jobs() -> None:
    assert set(pg_repo._TARGET_HEALTH_FAILURE_JOB_TYPES) == {
        "CRAWL_BAEMIN",
        "CRAWL_COUPANG",
        "AUTH_CHECK",
        "OPEN_AUTH_BROWSER",
        "AUTH_COUPANG_2FA",
    }
    assert "RIDER_LOOKUP" not in pg_repo._TARGET_HEALTH_FAILURE_JOB_TYPES


def test_target_failure_job_stmt_filters_target_health_job_types() -> None:
    stmt = pg_repo._target_failure_job_stmt(
        [uuid.UUID("00000000-0000-0000-0000-000000000001")]
    )
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "jobs.type IN" in compiled
    for job_type in pg_repo._TARGET_HEALTH_FAILURE_JOB_TYPES:
        assert repr(job_type) in compiled
    assert "RIDER_LOOKUP" not in compiled


def test_auth_session_pending_states_cover_both_pending_vocab() -> None:
    # 인증대기 = AUTH_REQUIRED + USER_ACTION_PENDING(둘 다 누락 시 인증 필요 누출).
    assert _AUTH_SESSION_PENDING_STATES == (
        BaeminAuthState.AUTH_REQUIRED.value,
        BaeminAuthState.USER_ACTION_PENDING.value,
    )


def test_account_auth_required_states_cover_manual_action_vocab() -> None:
    # 계정 자체가 사람 조치 상태면 auth_sessions row 없이도 인증 필요 목록에 떠야 한다.
    assert getattr(pg_repo, "_ACCOUNT_AUTH_REQUIRED_STATES", ()) == (
        BaeminAuthState.AUTH_REQUIRED.value,
        BaeminAuthState.USER_ACTION_PENDING.value,
        BaeminAuthState.BLOCKED_OR_CAPTCHA.value,
    )


def test_active_job_statuses_are_claimed_and_running() -> None:
    # Agent "현재 job" 판정 스코프 = CLAIMED/RUNNING(완료/대기 제외).
    assert _ACTIVE_JOB_STATUSES == (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)


def test_telegram_error_window_is_ten_minutes() -> None:
    # ops-contract 정합(최근 10분 윈도; 정밀화는 5.9).
    assert _TELEGRAM_ERROR_WINDOW == timedelta(minutes=10)


def test_agent_reports_kakao_runtime_unavailable_for_sessionless_worker() -> None:
    assert _agent_reports_kakao_runtime_unavailable(
        capacity_json={
            "capabilities": [JOB_TYPE_KAKAO_SEND],
            "kakao_status": {"enabled": True, "interactive_session_available": False},
        },
        last_heartbeat_at=_NOW,
        now=_NOW,
    )

    assert not _agent_reports_kakao_runtime_unavailable(
        capacity_json={
            "capabilities": [JOB_TYPE_KAKAO_SEND],
            "kakao_status": {"enabled": True, "interactive_session_available": False},
        },
        last_heartbeat_at=_NOW - timedelta(minutes=5),
        now=_NOW,
    )
    assert not _agent_reports_kakao_runtime_unavailable(
        capacity_json={
            "capabilities": [JOB_TYPE_KAKAO_SEND],
            "kakao_status": {"enabled": True, "interactive_session_available": True},
        },
        last_heartbeat_at=_NOW,
        now=_NOW,
    )
    assert not _agent_reports_kakao_runtime_unavailable(
        capacity_json={
            "capabilities": ["CRAWL_COUPANG"],
            "kakao_status": {"enabled": True, "interactive_session_available": False},
        },
        last_heartbeat_at=_NOW,
        now=_NOW,
    )
