"""Story 5.9 / AC1·AC2 — 순수 지표·알림 정책(always-run, DB 없음).

``evaluate_alerts`` 4개 최소 알림 경계(2분/120초/30%+min_samples/≥1)와 **임계 상수 재사용
identity/동등**(scheduler/severity 정본과 동일값)을 잠근다. PG-skip CI 에서도 의미(임계·알림
경계)가 결정적으로 검증된다 — 시각/임계는 호출부 주입.
"""

from __future__ import annotations

from datetime import timedelta

from rider_server.admin import severity
from rider_server.admin.dashboard_repository_postgres import _TELEGRAM_ERROR_WINDOW
from rider_server.metrics import policy
from rider_server.scheduler import policy as scheduler_policy
from rider_server.scheduler import service as scheduler_service


def _snap(**kw) -> policy.MetricsSnapshot:
    return policy.MetricsSnapshot(**kw)


def _codes(snapshot: policy.MetricsSnapshot) -> set[str]:
    return {a.code for a in policy.evaluate_alerts(snapshot)}


# ── 임계 상수 재사용(drift 0) — identity/동등 잠금 ────────────────────────────

def test_agent_offline_threshold_reuses_severity_canonical() -> None:
    # 같은 객체(import 재사용) — agent offline 2분 임계를 두 곳에서 다른 값으로 두지 않는다.
    assert policy.AGENT_OFFLINE_AFTER is severity.AGENT_OFFLINE_AFTER
    assert policy.AGENT_OFFLINE_AFTER == timedelta(minutes=2)


def test_crawl_thresholds_reuse_scheduler_canonical() -> None:
    assert policy.DEFAULT_BREAKER_THRESHOLD == scheduler_policy.DEFAULT_BREAKER_THRESHOLD
    assert policy.DEFAULT_BREAKER_THRESHOLD == 0.30
    assert (
        policy.DEFAULT_BREAKER_MIN_SAMPLES
        == scheduler_policy.DEFAULT_BREAKER_MIN_SAMPLES
    )


def test_windows_match_canonical_values() -> None:
    # 15분(scheduler 정본)·10분(dashboard private) 동일값 재선언을 동등으로 잠근다.
    assert policy.DEFAULT_BREAKER_WINDOW == scheduler_service.DEFAULT_BREAKER_WINDOW
    assert policy.DEFAULT_BREAKER_WINDOW == timedelta(minutes=15)
    assert policy.TELEGRAM_ERROR_WINDOW == _TELEGRAM_ERROR_WINDOW
    assert policy.TELEGRAM_ERROR_WINDOW == timedelta(minutes=10)


def test_queue_lag_threshold_is_120_seconds_nfr14() -> None:
    assert policy.QUEUE_LAG_ALERT_SECONDS == 120


def test_crawl_alert_open_is_equivalent_to_evaluate_breaker() -> None:
    # rate 위 판정이 (total, failures) 위 evaluate_breaker 정본과 결과가 같음을 전수 잠금.
    for total in range(0, 9):
        for failures in range(0, total + 1):
            rate = (failures / total) if total > 0 else 0.0
            assert policy.crawl_alert_open(rate, total) == scheduler_policy.evaluate_breaker(
                total, failures
            ), (total, failures)


# ── 알림 코드/심각도 어휘(plain-string, enum 아님) ───────────────────────────

def test_minimum_alert_codes_are_ac2_set_lowercase() -> None:
    assert set(policy.MINIMUM_ALERT_CODES) == {
        "agent_offline",
        "queue_lag",
        "api_error_rate",
        "auth_required",
    }


def test_alert_severity_uses_severity_plain_string_constants() -> None:
    snap = _snap(
        agents_offline=1,
        kakao_queue_lag_seconds=200,
        auth_required_count=1,
    )
    by_code = {a.code: a.severity for a in policy.evaluate_alerts(snap)}
    assert by_code["agent_offline"] == severity.SEVERITY_CRITICAL
    assert by_code["queue_lag"] == severity.SEVERITY_WARNING
    assert by_code["auth_required"] == severity.SEVERITY_WARNING
    # 모든 발화 심각도는 severity.py 정본 어휘에 속한다(새 어휘 신설 0).
    assert all(a.severity in severity.SEVERITIES for a in policy.evaluate_alerts(snap))


# ── agent_offline 경계 ────────────────────────────────────────────────────────

def test_agent_offline_fires_when_one_or_more_offline() -> None:
    assert "agent_offline" in _codes(_snap(agents_offline=1))
    assert "agent_offline" not in _codes(_snap(agents_total=3, agents_offline=0))


# ── queue_lag 경계(120초 정확/초과) ──────────────────────────────────────────

def test_queue_lag_fires_only_above_120_seconds() -> None:
    assert "queue_lag" not in _codes(_snap(kakao_queue_lag_seconds=120))  # 정확히=미발화
    assert "queue_lag" in _codes(_snap(kakao_queue_lag_seconds=121))


# ── api_error_rate 경계(30%+min_samples / telegram) ─────────────────────────

def test_api_error_rate_blocks_small_sample_false_positive() -> None:
    # 1/1=100% 지만 표본<min_samples → false-positive 차단(미발화).
    snap = _snap(
        crawl_error_rate_by_platform={"BAEMIN": 1.0},
        crawl_samples_by_platform={"BAEMIN": 1},
    )
    assert "api_error_rate" not in _codes(snap)


def test_api_error_rate_fires_above_30pct_with_enough_samples() -> None:
    snap = _snap(
        crawl_error_rate_by_platform={"COUPANG": 0.5},
        crawl_samples_by_platform={"COUPANG": 6},
    )
    assert "api_error_rate" in _codes(snap)


def test_api_error_rate_not_fire_at_exactly_30pct() -> None:
    snap = _snap(
        crawl_error_rate_by_platform={"BAEMIN": 0.30},
        crawl_samples_by_platform={"BAEMIN": 10},
    )
    assert "api_error_rate" not in _codes(snap)  # 정확히 30%는 초과(>) 아님 → 미발화


def test_api_error_rate_fires_on_telegram_surge() -> None:
    assert "api_error_rate" in _codes(_snap(telegram_error_count=1))
    assert "api_error_rate" not in _codes(_snap(telegram_error_count=0))


def test_api_error_rate_fires_if_any_platform_exceeds() -> None:
    snap = _snap(
        crawl_error_rate_by_platform={"BAEMIN": 0.0, "COUPANG": 0.9},
        crawl_samples_by_platform={"BAEMIN": 10, "COUPANG": 10},
    )
    assert "api_error_rate" in _codes(snap)


# ── auth_required 경계(auth≥1 / gmail≥1) ─────────────────────────────────────

def test_auth_required_fires_on_auth_count() -> None:
    assert "auth_required" in _codes(_snap(auth_required_count=1))
    assert "auth_required" not in _codes(_snap(auth_required_count=0))


def test_auth_required_fires_on_gmail_reauth() -> None:
    assert "auth_required" in _codes(_snap(gmail_reauth_required_count=1))


# ── 종합: 정상 스냅샷은 알림 0, 시그니처 now 주입 가능 ───────────────────────

def test_clean_snapshot_yields_no_alerts() -> None:
    clean = _snap(
        agents_total=2,
        agents_offline=0,
        targets_total=5,
        kakao_queue_lag_seconds=10,
        crawl_error_rate_by_platform={"BAEMIN": 0.0, "COUPANG": 0.0},
        crawl_samples_by_platform={"BAEMIN": 10, "COUPANG": 10},
        telegram_error_count=0,
        auth_required_count=0,
        gmail_reauth_required_count=0,
    )
    assert policy.evaluate_alerts(clean) == ()


def test_evaluate_alerts_accepts_now_kwarg() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
    # now 주입은 시그니처 대칭(현재 분기 미사용) — 호출이 깨지지 않음을 확인.
    assert policy.evaluate_alerts(_snap(), now=now) == ()


# ══════════════════════════════════════════════════════════════════════════
# qa-e2e 보강(Story 5.9): 알림 정책 커버리지 갭
# ══════════════════════════════════════════════════════════════════════════

def test_api_error_rate_severity_is_critical() -> None:
    # 4종 중 api_error_rate 심각도가 기존 테스트에서 단정되지 않았다(crawl breaker 경로).
    snap = _snap(
        crawl_error_rate_by_platform={"COUPANG": 0.9},
        crawl_samples_by_platform={"COUPANG": 10},
    )
    by_code = {a.code: a.severity for a in policy.evaluate_alerts(snap)}
    assert by_code["api_error_rate"] == severity.SEVERITY_CRITICAL
    # telegram 급증 경로도 동일 심각도(CRITICAL).
    tg = policy.evaluate_alerts(_snap(telegram_error_count=3))
    assert {a.severity for a in tg if a.code == "api_error_rate"} == {
        severity.SEVERITY_CRITICAL
    }


def test_auth_required_fires_exactly_once_when_both_signals_set() -> None:
    # auth_required ≥1 AND gmail_reauth ≥1 이어도 auth_required 알림은 단 한 번(OR 중복 금지).
    snap = _snap(auth_required_count=2, gmail_reauth_required_count=3)
    codes = [a.code for a in policy.evaluate_alerts(snap)]
    assert codes.count("auth_required") == 1


def test_all_four_alerts_fire_in_canonical_order_without_duplicates() -> None:
    # AC2 결정성: 전 조건 발화 시 코드 순서가 MINIMUM_ALERT_CODES 정본 순서와 일치하고 중복 0.
    snap = _snap(
        agents_offline=1,
        kakao_queue_lag_seconds=200,
        crawl_error_rate_by_platform={"BAEMIN": 0.9},
        crawl_samples_by_platform={"BAEMIN": 10},
        auth_required_count=1,
    )
    codes = tuple(a.code for a in policy.evaluate_alerts(snap))
    assert codes == policy.MINIMUM_ALERT_CODES
    assert len(codes) == len(set(codes))  # 중복 코드 0


def test_alert_threshold_minimums_lock_ac1_ge_one() -> None:
    # AC1 "≥1" 임계: auth/gmail/telegram 최소 발화 카운트는 1(정본 임계 잠금).
    assert policy.AUTH_REQUIRED_ALERT_MIN == 1
    assert policy.EMAIL_AUTH_ALERT_MIN == 1
    assert policy.TELEGRAM_ERROR_ALERT_MIN == 1


def test_snapshot_and_alert_are_frozen_immutable() -> None:
    # AC1 facts/알림 불변식: frozen dataclass 라 우발적 변이가 차단된다.
    import dataclasses

    import pytest

    alert = policy.Alert("agent_offline", severity.SEVERITY_CRITICAL)
    with pytest.raises(dataclasses.FrozenInstanceError):
        alert.code = "x"  # type: ignore[misc]
    snap = _snap()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.agents_offline = 9  # type: ignore[misc]
