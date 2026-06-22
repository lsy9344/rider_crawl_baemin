"""Story 5.9 / AC1·AC2 — 지표 조립 서비스·엔드포인트(always-run, in-memory, DB 없음).

``InMemoryMetricsRepository`` seed → ``MetricsSnapshot`` 조립 정확성과 **비식별 보장**(snapshot/
payload 에 식별 텍스트 부재), ``/metrics/operational`` 라우트 shape/키/알림 배열을 ``TestClient``
로 잠근다. 시각은 주입(``now``)해 결정적 — 라우트는 실 now 라 shape/알림 존재만 단언한다.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from rider_server.main import create_app
from rider_server.metrics import policy
from rider_server.metrics.service import InMemoryMetricsRepository, MetricsService
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_SNAKE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_EXPECTED_METRIC_KEYS = {
    "agents_total",
    "agents_offline",
    "oldest_heartbeat_age_seconds",
    "targets_total",
    "targets_warning",
    "targets_critical",
    "auth_required_count",
    "kakao_queue_lag_seconds",
    "crawl_error_rate_by_platform",
    "crawl_samples_by_platform",
    "telegram_error_count",
    "gmail_reauth_required_count",
}

_FAKE_SETTINGS = Settings(
    app_env="test", app_version="9.9.9", build_sha=None, build_time=None
)


def _seeded_repo() -> InMemoryMetricsRepository:
    repo = InMemoryMetricsRepository()
    # agents: None(offline) + 30초전(online) + 5분전(offline) → offline 2, oldest age 300.
    repo.seed_agent_heartbeat(None)
    repo.seed_agent_heartbeat(_NOW - timedelta(seconds=30))
    repo.seed_agent_heartbeat(_NOW - timedelta(minutes=5))
    # freshness(interval 10분): None→WARNING, 25분전→WARNING(>x2), 50분전→CRITICAL(>x4), 5분전→NORMAL.
    repo.seed_target_freshness(10, None)
    repo.seed_target_freshness(10, _NOW - timedelta(minutes=25))
    repo.seed_target_freshness(10, _NOW - timedelta(minutes=50))
    repo.seed_target_freshness(10, _NOW - timedelta(minutes=5))
    # crawl: BAEMIN 5/10=50%(>30%, 표본>=5), COUPANG 0표본.
    repo.seed_crawl_window("BAEMIN", total=10, failures=5)
    repo.seed_crawl_window("COUPANG", total=0, failures=0)
    repo.seed_kakao_queue_lag_seconds(150)
    repo.seed_telegram_error_count(2)
    repo.seed_auth_required_count(1)
    repo.seed_gmail_reauth_required_count(1)
    return repo


# ── 조립 정확성(sync 순수 + async snapshot 일치) ─────────────────────────────

def test_assemble_aggregates_seeded_facts_correctly() -> None:
    snap = asyncio.run(MetricsService().snapshot(_seeded_repo(), now=_NOW))
    assert snap.agents_total == 3
    assert snap.agents_offline == 2  # None + 5분전
    assert snap.oldest_heartbeat_age_seconds == 300
    assert snap.targets_total == 4
    assert snap.targets_warning == 2  # None + 25분전
    assert snap.targets_critical == 1  # 50분전
    assert snap.crawl_error_rate_by_platform["BAEMIN"] == 0.5
    assert snap.crawl_error_rate_by_platform["COUPANG"] == 0.0
    assert snap.crawl_samples_by_platform == {"BAEMIN": 10, "COUPANG": 0}
    assert snap.kakao_queue_lag_seconds == 150
    assert snap.telegram_error_count == 2
    assert snap.auth_required_count == 1
    assert snap.gmail_reauth_required_count == 1


def test_seeded_snapshot_fires_all_four_minimum_alerts() -> None:
    snap = asyncio.run(MetricsService().snapshot(_seeded_repo(), now=_NOW))
    codes = {a.code for a in policy.evaluate_alerts(snap)}
    assert codes == {"agent_offline", "queue_lag", "api_error_rate", "auth_required"}


def test_assemble_is_pure_sync_and_matches_async_snapshot() -> None:
    repo = _seeded_repo()
    async_snap = asyncio.run(MetricsService().snapshot(repo, now=_NOW))
    sync_snap = MetricsService.assemble(
        heartbeats=asyncio.run(repo.agent_heartbeats(now=_NOW)),
        freshness=asyncio.run(repo.target_freshness(now=_NOW)),
        crawl_windows=asyncio.run(repo.crawl_windows(since=_NOW, now=_NOW)),
        kakao_queue_lag_seconds=asyncio.run(repo.kakao_queue_lag_seconds(now=_NOW)),
        telegram_error_count=asyncio.run(repo.telegram_error_count(since=_NOW, now=_NOW)),
        auth_required_count=asyncio.run(repo.auth_required_count()),
        gmail_reauth_required_count=asyncio.run(repo.gmail_reauth_required_count()),
        now=_NOW,
    )
    assert async_snap == sync_snap


# ── 비식별 보장(식별 텍스트 부재) ────────────────────────────────────────────

def test_payload_holds_only_aggregate_numbers_no_identifiers() -> None:
    snap = asyncio.run(MetricsService().snapshot(_seeded_repo(), now=_NOW))
    payload = snap.to_payload()
    assert set(payload) == _EXPECTED_METRIC_KEYS
    # crawl dict 의 유일한 문자열 키는 플랫폼명(식별정보 아님)뿐.
    assert set(payload["crawl_error_rate_by_platform"]) <= {"BAEMIN", "COUPANG"}
    assert set(payload["crawl_samples_by_platform"]) <= {"BAEMIN", "COUPANG"}
    # 나머지 값은 모두 수치/None(이름·target_id 같은 식별 텍스트 0).
    scalar_keys = _EXPECTED_METRIC_KEYS - {
        "crawl_error_rate_by_platform",
        "crawl_samples_by_platform",
    }
    for key in scalar_keys:
        assert payload[key] is None or isinstance(payload[key], (int, float))


def test_snapshot_dataclass_has_no_identifying_fields() -> None:
    from dataclasses import fields

    names = {f.name for f in fields(policy.MetricsSnapshot)}
    forbidden = {"name", "center_name", "target_id", "tenant_id", "store_name", "room_name"}
    assert names.isdisjoint(forbidden)


# ── /metrics/operational 라우트 shape/키/알림 배열(TestClient) ────────────────

def _client(repo: InMemoryMetricsRepository) -> TestClient:
    app = create_app(_FAKE_SETTINGS, metrics_repository=repo)
    return TestClient(app, raise_server_exceptions=False)


def test_operational_endpoint_shape_and_keys() -> None:
    r = _client(_seeded_repo()).get("/metrics/operational")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"server_time", "metrics", "alerts"}
    assert _ISO_UTC.match(body["server_time"])
    metrics = body["metrics"]
    assert set(metrics) == _EXPECTED_METRIC_KEYS
    for key in metrics:
        assert _SNAKE_KEY.match(key), key
    assert isinstance(body["alerts"], list)
    for alert in body["alerts"]:
        assert set(alert) == {"code", "severity"}


def test_operational_endpoint_returns_firing_alerts() -> None:
    r = _client(_seeded_repo()).get("/metrics/operational")
    codes = {a["code"] for a in r.json()["alerts"]}
    assert codes == {"agent_offline", "queue_lag", "api_error_rate", "auth_required"}


def test_operational_endpoint_clean_state_no_alerts() -> None:
    # 무-seed(빈 fleet) → 알림 0, 키는 그대로 노출(확장 가능 shape).
    r = _client(InMemoryMetricsRepository()).get("/metrics/operational")
    body = r.json()
    assert body["alerts"] == []
    assert set(body["metrics"]) == _EXPECTED_METRIC_KEYS
    assert body["metrics"]["agents_total"] == 0


def test_operational_endpoint_is_root_level_not_v1() -> None:
    c = _client(InMemoryMetricsRepository())
    assert c.get("/metrics/operational").status_code == 200
    assert c.get("/v1/metrics/operational").status_code == 404


def test_existing_metrics_endpoint_unchanged() -> None:
    # 기존 /metrics(5.1) 무변경 — app_version/uptime_seconds/server_time 계약 보존.
    r = _client(InMemoryMetricsRepository()).get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"app_version", "uptime_seconds", "server_time"}


# ══════════════════════════════════════════════════════════════════════════
# qa-e2e 보강(Story 5.9): 조립 경계 커버리지 갭(always-run, in-memory)
# ══════════════════════════════════════════════════════════════════════════

def test_assemble_agent_offline_uses_strict_two_minute_boundary() -> None:
    # AC1 #1 "2분 초과"만 offline — 정확히 2분 경과는 online(severity 정본 재사용 잠금).
    repo = InMemoryMetricsRepository()
    repo.seed_agent_heartbeat(_NOW - timedelta(minutes=2))  # 정확히 2분 → online
    repo.seed_agent_heartbeat(_NOW - timedelta(minutes=2, seconds=1))  # 초과 → offline
    snap = asyncio.run(MetricsService().snapshot(repo, now=_NOW))
    assert snap.agents_total == 2
    assert snap.agents_offline == 1


def test_assemble_freshness_uses_strict_x2_x4_boundaries() -> None:
    # AC1 #2 경계: 정확히 ×2 → NORMAL(미경보), 정확히 ×4 → WARNING(critical 아님), ×4 초과 → CRITICAL.
    repo = InMemoryMetricsRepository()
    repo.seed_target_freshness(10, _NOW - timedelta(minutes=20))  # 정확히 ×2 → NORMAL
    repo.seed_target_freshness(10, _NOW - timedelta(minutes=40))  # 정확히 ×4 → WARNING
    repo.seed_target_freshness(10, _NOW - timedelta(minutes=41))  # ×4 초과 → CRITICAL
    snap = asyncio.run(MetricsService().snapshot(repo, now=_NOW))
    assert snap.targets_total == 3
    assert snap.targets_warning == 1  # 40분(정확히 ×4)
    assert snap.targets_critical == 1  # 41분(×4 초과) — 20분(정확히 ×2)은 NORMAL 미집계


def test_assemble_empty_fleet_is_all_zero_with_none_oldest_age() -> None:
    # 무-seed(빈 fleet): 카운트 0, oldest_heartbeat_age_seconds 는 None(ages 빈 분기), 알림 0.
    snap = asyncio.run(MetricsService().snapshot(InMemoryMetricsRepository(), now=_NOW))
    assert snap.agents_total == 0
    assert snap.agents_offline == 0
    assert snap.oldest_heartbeat_age_seconds is None
    assert snap.targets_total == 0
    assert snap.crawl_error_rate_by_platform == {}
    assert policy.evaluate_alerts(snap) == ()
