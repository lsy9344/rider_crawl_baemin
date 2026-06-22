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
    assert failclosed_signals_from(
        account_auth_state="ACTIVE", lifecycle_state="AUTH_REQUIRED", latest_failure_code=None
    ).auth_required
    assert failclosed_signals_from(
        account_auth_state="ACTIVE", lifecycle_state="ACTIVE", latest_failure_code="AUTH_REQUIRED"
    ).auth_required
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


def test_signals_clean_has_no_signal() -> None:
    s = failclosed_signals_from(
        account_auth_state="ACTIVE", lifecycle_state="ACTIVE", latest_failure_code="CRAWL_FAILURE"
    )
    assert s.any_signal is False


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
