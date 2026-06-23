"""Story 5.6 / AC2·AC3 — 순수 심각도 분류 정책(항상 실행, DB 불필요).

시간 경과 심각도(``classify_freshness`` ×2/×4 경계·None·interval<=0), fail-closed 우선
(``overall_severity`` 가 인증 필요 시 freshness 무시), 어휘 매핑(``failclosed_signals_from``),
agent online/offline 2분 경계를 결정적으로 잠근다(시각 주입 — PG 없이 의미 확정). 심각도 어휘가
plain-string 상수임을 확인해 기존 enum count-lock 무회귀를 보장한다.

fake 값만 — 실제 토큰/전화/이메일/chat_id 형태 없음. 평면 ``tests/server/`` 컨벤션.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rider_server.admin import severity
from rider_server.admin.severity import (
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_STOPPED,
    SEVERITY_WARNING,
    FailClosedSignals,
    classify_failclosed,
    classify_freshness,
    coupang_recovery_detail_label,
    failclosed_signals_from,
    is_agent_online,
    overall_severity,
    severity_rank,
)

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_INTERVAL = 10  # 분


def _ago(minutes: float) -> datetime:
    return _NOW - timedelta(minutes=minutes)


# ── AC2: 시간 경과 심각도 ×2/×4 경계 ──────────────────────────────────────────

def test_freshness_normal_within_two_intervals() -> None:
    # 19분 경과 < interval×2(20분) → 정상.
    assert classify_freshness(_ago(19), _INTERVAL, _NOW) == SEVERITY_NORMAL


def test_freshness_warning_over_two_intervals() -> None:
    # 21분 경과 > interval×2(20분), <= ×4(40분) → 주의.
    assert classify_freshness(_ago(21), _INTERVAL, _NOW) == SEVERITY_WARNING


def test_freshness_critical_over_four_intervals() -> None:
    # 41분 경과 > interval×4(40분) → 위험.
    assert classify_freshness(_ago(41), _INTERVAL, _NOW) == SEVERITY_CRITICAL


def test_freshness_exact_double_boundary_is_lower_grade() -> None:
    # 정확히 ×2(20분)는 "초과(>)" 아니므로 하위 등급(정상).
    assert classify_freshness(_ago(20), _INTERVAL, _NOW) == SEVERITY_NORMAL


def test_freshness_exact_quadruple_boundary_is_lower_grade() -> None:
    # 정확히 ×4(40분)는 "초과(>)" 아니므로 하위 등급(주의).
    assert classify_freshness(_ago(40), _INTERVAL, _NOW) == SEVERITY_WARNING


def test_freshness_none_last_success_is_at_least_warning() -> None:
    # 한 번도 성공 못함 → 최소 주의(interval 무관).
    assert classify_freshness(None, _INTERVAL, _NOW) == SEVERITY_WARNING


def test_freshness_none_last_success_warning_even_with_zero_interval() -> None:
    # None 우선 — interval 미설정이어도 정상으로 떨어지지 않는다.
    assert classify_freshness(None, 0, _NOW) == SEVERITY_WARNING


@pytest.mark.parametrize("interval", [0, -5])
def test_freshness_nonpositive_interval_skips_to_normal(interval) -> None:
    # interval<=0(미설정) → 시간 경과 평가 skip → 정상(성공 이력은 있는 경우).
    assert classify_freshness(_ago(9999), interval, _NOW) == SEVERITY_NORMAL


# ── AC3: fail-closed 어휘 매핑 ────────────────────────────────────────────────

def test_signals_auth_required_from_account_auth_state() -> None:
    s = failclosed_signals_from(
        account_auth_state="AUTH_REQUIRED",
        lifecycle_state="ACTIVE",
        latest_failure_code=None,
    )
    assert s.auth_required is True
    assert s.any_signal is True


def test_signals_auth_required_from_lifecycle_and_failure_and_session() -> None:
    # lifecycle 의 AUTH_REQUIRED 는 계정 인증과 독립 → ACTIVE 계정이어도 인증필요.
    assert failclosed_signals_from(
        account_auth_state="ACTIVE", lifecycle_state="AUTH_REQUIRED", latest_failure_code=None
    ).auth_required
    # 실패 코드 유래 인증필요는 계정 인증이 "정상"이 아닐 때(UNKNOWN 등)만 유효(ACTIVE/
    # AUTH_VERIFIED 면 권위 있는 정상 신호가 stale 코드를 덮는다 — 별도 테스트로 잠금).
    assert failclosed_signals_from(
        account_auth_state="UNKNOWN", lifecycle_state="ACTIVE", latest_failure_code="AUTH_REQUIRED"
    ).auth_required
    # 진행 중 auth_session 도 계정 인증과 독립 신호 → ACTIVE 계정이어도 인증필요.
    assert failclosed_signals_from(
        account_auth_state="ACTIVE",
        lifecycle_state="ACTIVE",
        latest_failure_code=None,
        auth_session_pending=True,
    ).auth_required


def test_signals_target_validation_failure_from_center_mismatch_or_code() -> None:
    assert failclosed_signals_from(
        account_auth_state="CENTER_MISMATCH", lifecycle_state="ACTIVE", latest_failure_code=None
    ).target_validation_failed
    assert failclosed_signals_from(
        account_auth_state="ACTIVE",
        lifecycle_state="ACTIVE",
        latest_failure_code="TARGET_VALIDATION_FAILURE",
    ).target_validation_failed


def test_signals_kakao_misdelivery_risk_from_kakao_failure() -> None:
    s = failclosed_signals_from(
        account_auth_state="ACTIVE", lifecycle_state="ACTIVE", latest_failure_code="KAKAO_FAILURE"
    )
    assert s.kakao_misdelivery_risk is True


def test_resolved_kakao_failure_after_center_fixed_labels_kakao_not_validation() -> None:
    # 회귀: 센터 검증을 통과(계정 auth_state ACTIVE)시킨 뒤 카카오 전송이 실패하면, 카드는
    # 더 오래된 '대상 검증 실패'가 아니라 현재 실패인 '카카오 오발송 위험'으로 라벨링돼야 한다.
    # latest_failure_code 가 KAKAO_FAILURE 로 올바르게 뽑히면(쿼리 fix) target_validation 은
    # 꺼지고 kakao 만 켜진다 → _display_severity 가 kakao 라벨을 돌려준다.
    from rider_server.admin.routes import _display_severity
    from rider_server.admin.severity import SEVERITY_KAKAO_MISDELIVERY_RISK
    from rider_server.admin.dashboard_service import TargetHealthFacts

    s = failclosed_signals_from(
        account_auth_state="ACTIVE",
        lifecycle_state="ACTIVE",
        latest_failure_code="KAKAO_FAILURE",
    )
    assert s.kakao_misdelivery_risk is True
    assert s.target_validation_failed is False

    # 카카오 실패가 마지막 성공보다 뒤(재검증 crawl 성공 → 그 후 카카오 전송 실패)라 active.
    facts = TargetHealthFacts(
        target_id="t1",
        tenant_id="tn1",
        name="HJ",
        center_name="의정부남부",
        platform="COUPANG",
        interval_minutes=10,
        last_success_at=_ago(5),
        last_delivery_at=None,
        last_failure_code="KAKAO_FAILURE",
        account_auth_state="ACTIVE",
        lifecycle_state="ACTIVE",
        last_failure_at=_NOW,
    )
    assert _display_severity(SEVERITY_STOPPED, facts) == SEVERITY_KAKAO_MISDELIVERY_RISK


def test_signals_clean_has_no_signal() -> None:
    s = failclosed_signals_from(
        account_auth_state="ACTIVE", lifecycle_state="ACTIVE", latest_failure_code="CRAWL_FAILURE"
    )
    assert s.any_signal is False


# ── 인증 후 묵은 실패 코드/AUTH_VERIFIED 억제(2026-06 회귀 수정) ─────────────────

def test_auth_verified_suppresses_stale_auth_required_failure() -> None:
    # 인증 완료(AUTH_VERIFIED) + 마지막 성공보다 과거의 AUTH_REQUIRED 실패 → 인증필요 끔.
    s = failclosed_signals_from(
        account_auth_state="AUTH_VERIFIED",
        lifecycle_state="ACTIVE",
        latest_failure_code="AUTH_REQUIRED",
        last_success_at=_NOW,
        latest_failure_at=_ago(30),
    )
    assert s.auth_required is False
    assert s.any_signal is False


def test_auth_verified_suppresses_even_without_timestamps() -> None:
    # 시각이 없어도 AUTH_VERIFIED 자체가 권위 있는 정상 신호라 인증필요를 끈다.
    s = failclosed_signals_from(
        account_auth_state="AUTH_VERIFIED",
        lifecycle_state="ACTIVE",
        latest_failure_code="AUTH_REQUIRED",
    )
    assert s.auth_required is False


def test_active_auth_state_suppresses_stale_auth_required_failure() -> None:
    # ACTIVE 도 정상 인증 집합 — 마지막 성공보다 과거 실패면 인증필요 끔.
    s = failclosed_signals_from(
        account_auth_state="ACTIVE",
        lifecycle_state="ACTIVE",
        latest_failure_code="AUTH_REQUIRED",
        last_success_at=_NOW,
        latest_failure_at=_ago(30),
    )
    assert s.auth_required is False


def test_current_auth_required_still_flags_even_with_recent_success() -> None:
    # 현재 계정상태가 AUTH_REQUIRED 면(권위 신호) 최근 성공이 있어도 인증필요 유지(거짓 음성 금지).
    s = failclosed_signals_from(
        account_auth_state="AUTH_REQUIRED",
        lifecycle_state="ACTIVE",
        latest_failure_code="AUTH_REQUIRED",
        last_success_at=_NOW,
        latest_failure_at=_NOW,
    )
    assert s.auth_required is True


def test_failure_newer_than_success_is_not_stale() -> None:
    # 마지막 성공 이후(>)에 난 AUTH_REQUIRED 실패는 stale 아님 → 인증필요 유지.
    s = failclosed_signals_from(
        account_auth_state="UNKNOWN",
        lifecycle_state="ACTIVE",
        latest_failure_code="AUTH_REQUIRED",
        last_success_at=_ago(30),
        latest_failure_at=_NOW,
    )
    assert s.auth_required is True


def test_stale_failure_suppressed_for_neutral_auth_state() -> None:
    # 정상 인증 집합이 아니어도(UNKNOWN), 실패가 마지막 성공보다 과거면 stale → 인증필요 끔.
    s = failclosed_signals_from(
        account_auth_state="UNKNOWN",
        lifecycle_state="ACTIVE",
        latest_failure_code="AUTH_REQUIRED",
        last_success_at=_NOW,
        latest_failure_at=_ago(30),
    )
    assert s.auth_required is False


def test_healthy_auth_states_set_is_locked() -> None:
    # 드리프트 락 — 정상 인증 집합은 ACTIVE/AUTH_VERIFIED 둘뿐(UNKNOWN 은 중립이라 제외).
    assert severity.HEALTHY_AUTH_STATES == frozenset({"ACTIVE", "AUTH_VERIFIED"})


def test_classify_failclosed_returns_stopped_or_none() -> None:
    assert classify_failclosed(FailClosedSignals(auth_required=True)) == SEVERITY_STOPPED
    assert classify_failclosed(FailClosedSignals(kakao_misdelivery_risk=True)) == SEVERITY_STOPPED
    assert classify_failclosed(FailClosedSignals()) is None


# ── AC3: 병합 우선순위 — fail-closed > 시간 경과 ──────────────────────────────

def test_overall_failclosed_overrides_recent_freshness() -> None:
    # 마지막 성공이 방금(정상)이어도 인증 필요면 중지 우선(순서 뒤집힘 방지).
    freshness = classify_freshness(_ago(1), _INTERVAL, _NOW)
    assert freshness == SEVERITY_NORMAL
    signals = failclosed_signals_from(
        account_auth_state="AUTH_REQUIRED", lifecycle_state="ACTIVE", latest_failure_code=None
    )
    assert overall_severity(freshness, classify_failclosed(signals)) == SEVERITY_STOPPED


def test_overall_uses_freshness_when_no_failclosed() -> None:
    freshness = classify_freshness(_ago(41), _INTERVAL, _NOW)
    assert overall_severity(freshness, None) == SEVERITY_CRITICAL


def test_stopped_outranks_critical() -> None:
    # 중지(fail-closed)가 위험(시간 경과)보다 높은 순위임을 잠근다.
    assert severity_rank(SEVERITY_STOPPED) > severity_rank(SEVERITY_CRITICAL)
    assert severity_rank(SEVERITY_CRITICAL) > severity_rank(SEVERITY_WARNING)
    assert severity_rank(SEVERITY_WARNING) > severity_rank(SEVERITY_NORMAL)


# ── AC1: agent online/offline 2분 경계 ────────────────────────────────────────

def test_agent_online_within_two_minutes() -> None:
    assert is_agent_online(_NOW - timedelta(seconds=119), _NOW) is True


def test_agent_online_exact_two_minutes_is_online() -> None:
    # "more than 2 minutes" → 정확히 2분은 online(초과만 offline).
    assert is_agent_online(_NOW - timedelta(minutes=2), _NOW) is True


def test_agent_offline_over_two_minutes() -> None:
    assert is_agent_online(_NOW - timedelta(seconds=121), _NOW) is False


def test_agent_offline_when_never_heartbeat() -> None:
    assert is_agent_online(None, _NOW) is False


# ── 무회귀: 심각도 어휘는 plain-string 상수(enum 아님) ────────────────────────

def test_severity_constants_are_plain_strings_not_enum() -> None:
    for code in severity.SEVERITIES:
        assert isinstance(code, str)
        assert type(code) is str  # Enum 멤버가 아님(count-lock 무관)
    assert severity.SEVERITIES == (
        SEVERITY_NORMAL,
        SEVERITY_WARNING,
        SEVERITY_CRITICAL,
        SEVERITY_STOPPED,
    )


# ── QA 보강: fail-closed 신호 3종 모두 STOPPED, 병합 passthrough/override 전수 ──────

def test_classify_failclosed_stopped_for_target_validation_signal() -> None:
    # target_validation_failed 단독도 중지(STOPPED) — any_signal 경로(기존 테스트 누락분).
    assert classify_failclosed(FailClosedSignals(target_validation_failed=True)) == SEVERITY_STOPPED


def test_overall_passes_through_freshness_when_no_failclosed() -> None:
    # fail-closed 없으면 freshness 값을 그대로(주의/정상 passthrough — 기존엔 CRITICAL 만 검증).
    assert overall_severity(SEVERITY_WARNING, None) == SEVERITY_WARNING
    assert overall_severity(SEVERITY_NORMAL, None) == SEVERITY_NORMAL


def test_overall_failclosed_overrides_even_critical_freshness() -> None:
    # freshness 가 이미 위험(CRITICAL)이어도 fail-closed 값(STOPPED)이 그대로 우선(AC3).
    crit = classify_freshness(_ago(41), _INTERVAL, _NOW)
    assert crit == SEVERITY_CRITICAL
    assert overall_severity(crit, SEVERITY_STOPPED) == SEVERITY_STOPPED


# ── QA 보강: 방어적 분기(미지 severity, 주입 가능한 offline 임계) ─────────────────

def test_severity_rank_unknown_value_is_zero() -> None:
    # 정본 4종이 아닌 값은 0(정상 취급) — 표시 정렬이 미지값에 깨지지 않음.
    assert severity_rank("NOPE") == 0


def test_is_agent_online_respects_injected_offline_threshold() -> None:
    # offline_after 주입(임계 조정 가능) — 30s 임계에서 31s 전 heartbeat 는 offline.
    short = timedelta(seconds=30)
    assert is_agent_online(_NOW - timedelta(seconds=29), _NOW, offline_after=short) is True
    assert is_agent_online(_NOW - timedelta(seconds=31), _NOW, offline_after=short) is False


# ── crawl-coupang-auth-separation Task 9: 쿠팡 자동 인증 복구 세부 사유 표시 ─────────


def test_dashboard_surfaces_coupang_email_auth_required_detail() -> None:
    """Email mailbox auth issue is visible as auth-required detail."""
    # 계정 gate 는 AUTH_REQUIRED(STOPPED) 그대로, 세부는 "메일 인증 필요"로 구분된다.
    assert (
        coupang_recovery_detail_label(auth_recovery_state="EMAIL_AUTH_REQUIRED")
        == "메일 인증 필요"
    )
    assert (
        coupang_recovery_detail_label(
            auth_recovery_state="EMAIL_AUTH_REQUIRED", reason="email_auth_required"
        )
        == "메일 인증 필요"
    )


def test_dashboard_surfaces_coupang_recovery_failed_detail() -> None:
    """Repeated/mail-delay recovery failures are not shown as generic crawl failure."""
    # 메일 지연은 "인증 메일 지연"으로, 반복 실패는 "자동 인증 실패"로 구분된다.
    assert (
        coupang_recovery_detail_label(
            auth_recovery_state="RECOVERY_FAILED", reason="verification_mail_delayed"
        )
        == "인증 메일 지연"
    )
    assert (
        coupang_recovery_detail_label(
            auth_recovery_state="RECOVERY_FAILED", reason="repeated_recovery_failure"
        )
        == "자동 인증 실패"
    )
    # reason 없이 상태만 와도 일반 "자동 인증 실패"로 표면화.
    assert coupang_recovery_detail_label(auth_recovery_state="RECOVERY_FAILED") == "자동 인증 실패"


def test_dashboard_surfaces_coupang_user_action_required_detail() -> None:
    """CAPTCHA/abnormal login is surfaced as captcha detail, not crawl failure."""
    assert (
        coupang_recovery_detail_label(auth_recovery_state="USER_ACTION_REQUIRED")
        == "캡차/이상 로그인"
    )


def test_coupang_recovery_detail_is_none_for_active_or_unknown() -> None:
    # 복구 성공(ACTIVE)·미매핑 상태는 detail 을 만들지 않는다(gate 심각도만 표시).
    assert coupang_recovery_detail_label(auth_recovery_state="ACTIVE") is None
    assert coupang_recovery_detail_label(auth_recovery_state=None) is None
    assert coupang_recovery_detail_label(auth_recovery_state="", reason="") is None
