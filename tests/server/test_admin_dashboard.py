"""Story 5.6 / AC1·AC2·AC3·AC4 — read-model 조립 + HTMX 라우트(항상 실행, DB 불필요).

(1) in-memory fake repo + 주입 ``now`` 로 ``DashboardService`` 조립이 올바른 severity·online·
    tenant scope·채널 구분을 만드는지 결정적으로 잠근다.
(2) ``TestClient`` 로 ``/admin`` 풀 페이지(200·HTML·``hx-`` 속성)·부분 fragment(200·HTML) 반환과
    ``require_admin_session`` seam(거부 시 401 envelope)을 확인한다.
(3) 무회귀 lock: ``jinja2`` 는 server extra(additive)·``[project].dependencies`` 9개 유지.
(4) secret 위생: read-model DTO 에 token/secret 류 필드 0(HTML 평문 누출 차단).

fake 값만 — 실제 토큰/전화/이메일/chat_id 형태 없음. 평면 ``tests/server/`` 컨벤션.
``pytest-asyncio`` 미도입 → ``asyncio.run`` 으로 async 서비스 구동(5.4 선례).
"""

from __future__ import annotations

import asyncio
import tomllib
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from rider_server.admin import routes as admin_routes
from rider_server.admin.dashboard_repository_postgres import PostgresDashboardRepository
from rider_server.admin.dashboard_service import (
    AgentHealthFacts,
    AgentRow,
    AuthRequiredRow,
    ChannelHealthRow,
    DashboardRepository,
    DashboardService,
    InMemoryDashboardRepository,
    JobQueueRow,
    TargetHealthFacts,
    TargetRow,
)
from rider_server.admin.severity import (
    SEVERITY_AUTH_REQUIRED,
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_OPERATOR_STOPPED,
    SEVERITY_STOPPED,
    SEVERITY_TARGET_VALIDATION_FAILURE,
    SEVERITY_WARNING,
)
from rider_server.domain import (
    CustomerLifecycleState,
    DeliveryRule,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    Tenant,
)
from rider_server.main import create_app
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.admin_entity_service import (
    AdminEntityService,
    InMemoryAdminEntityRepository,
)
from rider_server.settings import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_TENANT = "tn-1"
_OTHER_TENANT = "tn-2"

# Story 5.8: 기본 seam 이 fail-closed deny 라 읽기 테스트는 VIEWER principal 을 주입해 통과시킨다
# (의도된 보안 강화 — story 4.5). 거부 테스트는 require_admin_session seam 을 직접 교체한다.
_VIEWER = AdminPrincipal(actor_id="00000000-0000-0000-0000-0000000000aa", role=AdminRole.VIEWER,
                         mfa_verified=True, source="ADMIN_UI/viewer")


def _allow_viewer(app):
    app.state.resolve_admin_principal = lambda request: _VIEWER
    return app


def _target(
    *,
    target_id: str,
    tenant_id: str = _TENANT,
    name: str = "가게",
    interval_minutes: int = 10,
    last_success_at: datetime | None = None,
    last_failure_code: str | None = None,
    last_failure_at: datetime | None = None,
    account_auth_state: str | None = "ACTIVE",
    lifecycle_state: str | None = "ACTIVE",
    auth_session_pending: bool = False,
) -> TargetHealthFacts:
    return TargetHealthFacts(
        target_id=target_id,
        tenant_id=tenant_id,
        name=name,
        center_name="센터",
        platform="BAEMIN",
        interval_minutes=interval_minutes,
        last_success_at=last_success_at,
        last_delivery_at=None,
        last_failure_code=last_failure_code,
        last_failure_at=last_failure_at,
        account_auth_state=account_auth_state,
        lifecycle_state=lifecycle_state,
        auth_session_pending=auth_session_pending,
    )


def _seeded_repo() -> InMemoryDashboardRepository:
    repo = InMemoryDashboardRepository()
    # 정상(방금 성공)·위험(오래됨)·중지(인증 필요).
    repo.seed_target(_target(target_id="t-normal", last_success_at=_NOW - timedelta(minutes=5)))
    repo.seed_target(_target(target_id="t-critical", last_success_at=_NOW - timedelta(minutes=41)))
    repo.seed_target(
        _target(
            target_id="t-stopped",
            last_success_at=_NOW - timedelta(minutes=1),
            account_auth_state="AUTH_REQUIRED",
            lifecycle_state="AUTH_REQUIRED",
        )
    )
    # 다른 tenant 데이터(누출 0 검증용).
    repo.seed_target(_target(target_id="t-other", tenant_id=_OTHER_TENANT, name="다른고객"))
    repo.seed_agent(
        AgentHealthFacts(
            agent_id="a-online",
            name="agent-online",
            version="1.0.0",
            last_heartbeat_at=_NOW - timedelta(seconds=30),
            current_job_type="CRAWL_BAEMIN",
            capabilities=("CRAWL_BAEMIN", "KAKAO_SEND"),
            kakao_status={
                "enabled": True,
                "state": "idle",
                "queue_depth": 2,
                "queue_lag_seconds": 30,
                "sent": 7,
                "failed": 1,
                "last_success_at": "2026-06-14T11:59:00Z",
                "last_error_code": "KAKAO_FAILURE",
                "interactive_session_available": True,
            },
        )
    )
    repo.seed_agent(
        AgentHealthFacts(
            agent_id="a-offline",
            name="agent-offline",
            version="0.9.0",
            last_heartbeat_at=_NOW - timedelta(minutes=5),
            current_job_type=None,
            capabilities=(),
        )
    )
    repo.seed_channel_health(_TENANT, ChannelHealthRow(kakao_queue_lag_seconds=42, telegram_error_count=3))
    repo.seed_auth_required(
        AuthRequiredRow(tenant_id=_TENANT, target_id="t-stopped", profile_id="p1", reason="ACCOUNT_AUTH_REQUIRED")
    )
    repo.seed_auth_required(
        AuthRequiredRow(tenant_id=_OTHER_TENANT, target_id="t-other", profile_id=None, reason="ACCOUNT_AUTH_REQUIRED")
    )
    return repo


# ══════════════════════════════════════════════════════════════════════════
# (1) 서비스 조립 — severity·online·tenant scope·채널 구분
# ══════════════════════════════════════════════════════════════════════════

def test_target_rows_compose_correct_severity_and_sort_desc() -> None:
    rows = asyncio.run(DashboardService().target_rows(_seeded_repo(), tenant_id=_TENANT, now=_NOW))
    by_id = {r.target_id: r for r in rows}
    assert by_id["t-normal"].severity == SEVERITY_NORMAL
    assert by_id["t-critical"].severity == SEVERITY_CRITICAL
    # 인증 필요 → 마지막 성공이 최근(1분 전)이어도 중지 우선(AC3).
    assert by_id["t-stopped"].severity == SEVERITY_STOPPED
    # 위험도 높은 순 정렬(중지 먼저).
    assert [r.target_id for r in rows] == ["t-stopped", "t-critical", "t-normal"]


def test_target_rows_are_tenant_scoped() -> None:
    rows = asyncio.run(DashboardService().target_rows(_seeded_repo(), tenant_id=_TENANT, now=_NOW))
    tenants = {r.tenant_id for r in rows}
    assert tenants == {_TENANT}
    assert "t-other" not in {r.target_id for r in rows}


def test_agent_rows_online_offline() -> None:
    rows = asyncio.run(DashboardService().agent_rows(_seeded_repo(), now=_NOW))
    by_id = {r.agent_id: r for r in rows}
    assert by_id["a-online"].online is True
    assert by_id["a-online"].current_job_type == "CRAWL_BAEMIN"
    assert by_id["a-online"].kakao_enabled is True
    assert by_id["a-online"].kakao_state == "idle"
    assert by_id["a-online"].kakao_queue_depth == 2
    assert by_id["a-online"].kakao_queue_lag_seconds == 30
    assert by_id["a-online"].kakao_sent == 7
    assert by_id["a-online"].kakao_failed == 1
    assert by_id["a-online"].kakao_last_success_at == "2026-06-14T11:59:00Z"
    assert by_id["a-online"].kakao_last_error_code == "KAKAO_FAILURE"
    assert by_id["a-online"].kakao_interactive_session_available is True
    assert by_id["a-offline"].online is False


def test_agents_fragment_renders_kakao_worker_status() -> None:
    html = _client(_seeded_repo()).get("/admin/agents").text

    assert "Kakao 상태" in html
    assert "enabled" in html
    assert "idle" in html
    assert "대기 2건" in html
    assert "지연 30초" in html
    assert "성공 7건" in html
    assert "실패 1건" in html
    assert "마지막 성공 2026-06-14T11:59:00Z" in html
    assert "세션 OK" in html
    assert "KAKAO_FAILURE" in html


def test_agent_row_drops_unsafe_kakao_status_values() -> None:
    row = DashboardService.agent_row(
        AgentHealthFacts(
            agent_id="a-unsafe",
            name="agent-unsafe",
            version="1.0.0",
            last_heartbeat_at=_NOW,
            current_job_type=None,
            capabilities=("KAKAO_SEND",),
            kakao_status={
                "state": "idle\x7f",
                "queue_depth": -1,
                "queue_lag_seconds": 10**20,
                "sent": float("inf"),
                "failed": True,
                "last_success_at": "2026-06-14T11:59:00Z\u0085extra",
                "last_error_code": "KAKAO_FAILURE\x7fraw details",
            },
        ),
        _NOW,
    )

    assert row.kakao_state is None
    assert row.kakao_queue_depth is None
    assert row.kakao_queue_lag_seconds is None
    assert row.kakao_sent is None
    assert row.kakao_failed is None
    assert row.kakao_last_success_at is None
    assert row.kakao_last_error_code is None


def test_channel_health_separates_kakao_lag_and_telegram_error() -> None:
    health = asyncio.run(DashboardService().channel_health(_seeded_repo(), tenant_id=_TENANT, now=_NOW))
    # 두 값이 별도 필드(혼합 금지).
    assert health.kakao_queue_lag_seconds == 42
    assert health.telegram_error_count == 3


def test_auth_required_rows_are_tenant_scoped() -> None:
    rows = asyncio.run(DashboardService().auth_required_rows(_seeded_repo(), tenant_id=_TENANT))
    assert [r.target_id for r in rows] == ["t-stopped"]
    assert all(r.tenant_id == _TENANT for r in rows)


def test_auth_required_rows_collapse_duplicate_target_and_prefer_pending_session() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_target(_target(target_id="t-pending", name="가게", auth_session_pending=True))
    repo.seed_auth_required(
        AuthRequiredRow(
            tenant_id=_TENANT,
            target_id="t-pending",
            profile_id="p-old",
            reason="ACCOUNT_AUTH_REQUIRED",
            target_name="가게",
        )
    )
    repo.seed_auth_required(
        AuthRequiredRow(
            tenant_id=_TENANT,
            target_id="t-pending",
            profile_id="p-ready",
            reason="AUTH_SESSION_PENDING",
        )
    )
    repo.seed_auth_required(
        AuthRequiredRow(
            tenant_id=_TENANT,
            target_id="t-pending",
            profile_id="p-duplicate",
            reason="AUTH_SESSION_PENDING",
        )
    )

    rows = asyncio.run(DashboardService().auth_required_rows(repo, tenant_id=_TENANT, now=_NOW))

    assert len(rows) == 1
    assert rows[0] == AuthRequiredRow(
        tenant_id=_TENANT,
        target_id="t-pending",
        profile_id="p-ready",
        reason="AUTH_SESSION_PENDING",
        target_name="가게",
    )


# ── 실시간 큐 뷰(active job) ─────────────────────────────────────────────────────


def _job(
    *,
    job_id: str,
    status: str = "PENDING",
    job_type: str = "CRAWL_COUPANG",
    tenant_id: str = _TENANT,
    stuck: bool = False,
    run_after: datetime | None = None,
    claimed_at: datetime | None = None,
    agent_name: str | None = None,
    agent_online: bool | None = None,
    error_code: str | None = None,
    recently_failed: bool = False,
) -> JobQueueRow:
    return JobQueueRow(
        job_id=job_id,
        job_type=job_type,
        status=status,
        target_id=f"tgt-{job_id}",
        target_name=f"가게-{job_id}",
        center_name="센터",
        tenant_id=tenant_id,
        attempts=0,
        agent_name=agent_name,
        agent_online=agent_online,
        run_after=run_after,
        claimed_at=claimed_at,
        stuck=stuck,
        error_code=error_code,
        recently_failed=recently_failed,
    )


def test_job_queue_rows_put_stuck_first_then_run_after() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_active_job(_job(job_id="wait-late", run_after=_NOW + timedelta(minutes=10)))
    repo.seed_active_job(_job(job_id="wait-soon", run_after=_NOW + timedelta(minutes=1)))
    repo.seed_active_job(_job(job_id="stuck", status="CLAIMED", stuck=True))

    rows = asyncio.run(
        DashboardService().job_queue_rows(repo, tenant_id=_TENANT, now=_NOW)
    )

    # stuck 이 가장 위, 그다음 run_after 가 가까운 순.
    assert [r.job_id for r in rows] == ["stuck", "wait-soon", "wait-late"]


def test_job_queue_rows_are_tenant_scoped() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_active_job(_job(job_id="mine", tenant_id=_TENANT))
    repo.seed_active_job(_job(job_id="theirs", tenant_id=_OTHER_TENANT))

    mine = asyncio.run(DashboardService().job_queue_rows(repo, tenant_id=_TENANT, now=_NOW))
    assert [r.job_id for r in mine] == ["mine"]

    all_rows = asyncio.run(DashboardService().job_queue_rows(repo, tenant_id="all", now=_NOW))
    assert {r.job_id for r in all_rows} == {"mine", "theirs"}


def test_job_queue_rows_surface_recent_failure_at_top_with_reason() -> None:
    # 재검증 crawl 이 실패하면 active 가 아니라 사라지므로, 최근 실패를 사유와 함께 위로 올려
    # 운영자가 "왜 사라졌는지"를 본다.
    repo = InMemoryDashboardRepository()
    repo.seed_active_job(_job(job_id="wait", run_after=_NOW + timedelta(minutes=1)))
    repo.seed_active_job(
        _job(
            job_id="failed",
            status="FAILED",
            recently_failed=True,
            error_code="CDP_UNREACHABLE",
            claimed_at=_NOW - timedelta(seconds=30),
        )
    )

    rows = asyncio.run(DashboardService().job_queue_rows(repo, tenant_id=_TENANT, now=_NOW))

    # 최근 실패가 맨 위(주의 필요), 사유 코드 보존.
    assert rows[0].job_id == "failed"
    assert rows[0].recently_failed is True
    assert rows[0].error_code == "CDP_UNREACHABLE"


def test_jobs_fragment_route_shows_failed_reason() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_active_job(
        _job(
            job_id="f1",
            status="FAILED",
            job_type="CRAWL_COUPANG",
            recently_failed=True,
            error_code="CDP_UNREACHABLE",
        )
    )

    body = _client(repo).get(f"/admin/jobs?tenant={_TENANT}").text
    # 실패 상태 한글 라벨 + error_code 가 사람이 읽는 사유로 표시.
    assert "실패" in body
    assert "브라우저 연결 실패" in body
    assert 'class="job-failed"' in body


def test_in_memory_dashboard_all_tenants_returns_targets_and_aggregates_channels() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        TargetHealthFacts(
            target_id="t-a",
            tenant_id=_TENANT,
            customer_name="고객A",
            name="가게A",
            center_name="센터",
            platform="BAEMIN",
            interval_minutes=10,
            last_success_at=None,
            last_delivery_at=None,
            last_failure_code=None,
            account_auth_state="ACTIVE",
            lifecycle_state="ACTIVE",
        )
    )
    repo.seed_target(
        TargetHealthFacts(
            target_id="t-b",
            tenant_id=_OTHER_TENANT,
            customer_name="고객B",
            name="가게B",
            center_name="센터",
            platform="BAEMIN",
            interval_minutes=10,
            last_success_at=None,
            last_delivery_at=None,
            last_failure_code=None,
            account_auth_state="ACTIVE",
            lifecycle_state="ACTIVE",
        )
    )
    repo.seed_channel_health(_TENANT, ChannelHealthRow(kakao_queue_lag_seconds=42, telegram_error_count=3))
    repo.seed_channel_health(_OTHER_TENANT, ChannelHealthRow(kakao_queue_lag_seconds=7, telegram_error_count=4))

    rows = asyncio.run(repo.target_health(tenant_id="all", now=_NOW))
    health = asyncio.run(repo.channel_health(tenant_id="all", now=_NOW))

    assert {(r.target_id, r.tenant_id, r.customer_name) for r in rows} == {
        ("t-a", _TENANT, "고객A"),
        ("t-b", _OTHER_TENANT, "고객B"),
    }
    assert health == ChannelHealthRow(kakao_queue_lag_seconds=42, telegram_error_count=7)


# ══════════════════════════════════════════════════════════════════════════
# (2) HTMX 라우트 — TestClient
# ══════════════════════════════════════════════════════════════════════════

def _client(repo: DashboardRepository) -> TestClient:
    app = _allow_viewer(create_app(_FAKE_SETTINGS, dashboard_repository=repo))
    return TestClient(app, raise_server_exceptions=False)


def test_dashboard_full_page_has_htmx_attributes() -> None:
    r = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "hx-get" in body and "hx-trigger" in body
    assert "/admin/static/htmx.min.js" in body
    assert "https://unpkg.com/htmx.org@2" not in body
    # 심각도 한글 라벨 매핑(코드값→정상/주의/위험/중지).
    assert "인증 필요" in body


def test_admin_db_failure_returns_operator_html_without_secret() -> None:
    class _FailingRepo(InMemoryDashboardRepository):
        async def target_health(self, **kw):  # type: ignore[override]
            raise RuntimeError("postgresql://user:super-secret@db:5432/rider")

    r = _client(_FailingRepo()).get(f"/admin?tenant={_TENANT}")

    assert r.status_code == 503
    assert "text/html" in r.headers["content-type"]
    assert "DB 연결 실패" in r.text
    assert "DATABASE_URL" in r.text
    assert "DB 실행 상태" in r.text
    assert "재시도" in r.text
    assert "super-secret" not in r.text


def test_jobs_fragment_route_renders_korean_labels_and_stuck() -> None:
    repo = _seeded_repo()
    repo.seed_active_job(
        _job(
            job_id="j-stuck",
            status="CLAIMED",
            job_type="CRAWL_COUPANG",
            stuck=True,
            agent_name="agent-pc",
            agent_online=False,
        )
    )
    repo.seed_active_job(_job(job_id="j-wait", status="PENDING", job_type="CRAWL_BAEMIN"))

    r = _client(repo).get(f"/admin/jobs?tenant={_TENANT}")

    assert r.status_code == 200
    body = r.text
    # job type/status 한글 라벨 + 멈춤(stuck) + offline Agent 표시.
    assert "쿠팡 수집" in body and "배민 수집" in body
    assert "배정됨" in body and "대기" in body
    assert "멈춤" in body and "offline" in body
    assert 'class="job-stuck"' in body


def test_jobs_fragment_route_is_tenant_scoped() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_active_job(_job(job_id="mine", tenant_id=_TENANT, job_type="CRAWL_COUPANG"))
    repo.seed_active_job(_job(job_id="theirs", tenant_id=_OTHER_TENANT, job_type="CRAWL_COUPANG"))

    body = _client(repo).get(f"/admin/jobs?tenant={_TENANT}").text
    assert "가게-mine" in body
    assert "가게-theirs" not in body


def test_dashboard_full_page_includes_realtime_queue_section() -> None:
    repo = _seeded_repo()
    repo.seed_active_job(_job(job_id="j1", status="RUNNING", job_type="CRAWL_COUPANG"))

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    # 모니터링 화면에 실시간 큐 섹션 + 5초 폴링 + job fragment 가 들어간다.
    assert "실시간 큐" in body
    assert "/admin/jobs?tenant=" in body
    assert "every 5s" in body


def test_targets_fragment_db_failure_returns_safe_partial() -> None:
    class _FailingRepo(InMemoryDashboardRepository):
        async def target_health(self, **kw):  # type: ignore[override]
            raise RuntimeError("postgresql://user:super-secret@db:5432/rider")

    r = _client(_FailingRepo()).get(f"/admin/targets?tenant={_TENANT}")

    assert r.status_code == 503
    assert "text/html" in r.headers["content-type"]
    assert "DB 연결 실패" in r.text
    assert "super-secret" not in r.text


def test_manage_tab_shows_customer_setup_flow_and_send_gate() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}&mode=manage").text

    assert "새 고객 세팅 시작" in body
    for label in ("고객", "플랫폼", "계정", "업체/센터", "채널", "테스트", "실제 메시지 보내기"):
        assert label in body
    assert "수집 테스트" in body
    assert "전송 테스트" in body
    assert "테스트 완료 전에는 실제 메시지 보내기를 켤 수 없습니다." in body
    assert 'id="tg-edit-sending"' in body
    assert "sending_enabled" not in body


def test_manage_tab_uses_operator_labels_for_statuses_and_channels() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}&mode=manage").text

    assert '<option value="PAYMENT_FAILED_GRACE">결제 유예</option>' in body
    assert '<option value="SUSPENDED">정지</option>' in body
    assert '<option value="CANCELLED">해지</option>' in body
    assert '<option value="TELEGRAM">텔레그램</option>' in body
    assert '<option value="KAKAO">카카오톡</option>' in body
    assert "<label>텔레그램 채팅 ID" in body
    assert "<label>텔레그램 토픽 ID" in body
    assert "<label>카카오톡 방 이름" in body
    assert "PAYMENT_FAILED_GRACE: 결제 유예" not in body
    assert "<label>telegram_chat_id" not in body
    assert "<label>thread_id" not in body


def test_dashboard_tabs_and_password_inputs_are_accessible_and_readable() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'aria-controls="view-monitor"' in body
    assert 'aria-controls="view-manage"' in body
    assert 'id="view-monitor" role="tabpanel"' in body
    assert 'id="view-manage" role="tabpanel"' in body
    assert '#view-manage input[type="password"]' in body
    assert '--font-sans: "Pretendard"' in body
    assert "body { font-size: 13px;" not in body


def test_admin_htmx_static_asset_is_local() -> None:
    r = _client(_seeded_repo()).get("/admin/static/htmx.min.js")

    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert b"htmx" in r.content[:2000]


def test_dashboard_fragments_return_html_partials() -> None:
    c = _client(_seeded_repo())
    for path in (f"/admin/targets?tenant={_TENANT}", "/admin/agents", f"/admin/channels?tenant={_TENANT}", f"/admin/auth-required?tenant={_TENANT}"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"], path
    # 채널 fragment 는 두 지표를 모두 노출(구분 표시).
    channels = c.get(f"/admin/channels?tenant={_TENANT}").text
    assert "KakaoTalk" in channels and "Telegram" in channels


def test_targets_fragment_paginates_large_target_sets() -> None:
    repo = InMemoryDashboardRepository()
    for idx in range(300):
        repo.seed_target(
            _target(
                target_id=f"target-{idx}",
                name=f"가게-{idx}",
                last_success_at=_NOW - timedelta(minutes=1),
            )
        )
    c = _client(repo)

    first = c.get(f"/admin/targets?tenant={_TENANT}&limit=100").text

    assert "가게-0" in first
    assert "가게-99" in first
    assert "가게-100" not in first
    assert "더 보기" in first
    assert 'data-next-offset="100"' in first
    assert "offset=100" in first

    second = c.get(f"/admin/targets?tenant={_TENANT}&limit=100&offset=100").text
    assert "가게-100" in second
    assert "가게-199" in second
    assert "가게-200" not in second


def test_targets_fragment_prefetches_critical_target_before_first_page() -> None:
    class _PagingRepo(InMemoryDashboardRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[dict[str, object]] = []

        async def target_health(self, **kw):  # type: ignore[override]
            self.calls.append(dict(kw))
            return await super().target_health(**kw)

    repo = _PagingRepo()
    fresh = datetime.now(timezone.utc)
    for idx in range(150):
        repo.seed_target(
            _target(
                target_id=f"target-{idx}",
                name=f"가게-{idx}",
                last_success_at=fresh,
            )
        )
    repo.seed_target(
        _target(
            target_id="target-critical",
            name="위험-업체",
            interval_minutes=5,
            last_success_at=fresh - timedelta(hours=1),
        )
    )
    c = _client(repo)

    first = c.get(f"/admin/targets?tenant={_TENANT}&limit=100").text

    assert "target-critical" in first
    assert "위험-업체" in first
    assert all(call["limit"] is not None for call in repo.calls)
    assert repo.calls[-1]["limit"] == 101
    assert repo.calls[-1]["offset"] == 0


def test_targets_fragment_passes_limit_and_offset_to_repository() -> None:
    class _PagingRepo(InMemoryDashboardRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[dict[str, object]] = []

        async def target_health(self, **kw):  # type: ignore[override]
            self.calls.append(dict(kw))
            return await super().target_health(**kw)

    repo = _PagingRepo()
    for idx in range(3):
        repo.seed_target(_target(target_id=f"target-{idx}", name=f"가게-{idx}"))

    _client(repo).get(f"/admin/targets?tenant={_TENANT}&limit=2&offset=1")

    assert repo.calls[-1]["tenant_id"] == _TENANT
    assert repo.calls[-1]["limit"] == 3
    assert repo.calls[-1]["offset"] == 1


def test_auth_required_fragment_lists_only_tenant_rows() -> None:
    c = _client(_seeded_repo())
    body = c.get(f"/admin/auth-required?tenant={_TENANT}").text
    assert "t-stopped" in body
    assert "t-other" not in body  # cross-tenant 누출 0
    assert ">t-stopped<" not in body
    assert ">p1<" not in body
    assert "상세 열기" in body


def test_auth_required_fragment_refreshes_section_summary_and_open_state() -> None:
    repo = InMemoryDashboardRepository()
    c = _client(repo)

    body = c.get(f"/admin/auth-required?tenant={_TENANT}").text

    assert 'id="auth-required-section"' in body
    assert '인증 필요 대상 <span class="tag">0건 · secret 비노출</span>' in body
    assert '<details id="auth-required-section" class="aux" open' not in body

    repo.seed_auth_required(
        AuthRequiredRow(
            tenant_id=_TENANT,
            target_id="target-needs-auth",
            profile_id=None,
            reason="ACCOUNT_AUTH_REQUIRED",
            target_name="인증업체",
        )
    )

    body = c.get(f"/admin/auth-required?tenant={_TENANT}").text

    assert '<details id="auth-required-section" class="aux" open' in body
    assert '인증 필요 대상 <span class="tag">1건 · secret 비노출</span>' in body
    assert "인증업체" in body


def test_auth_required_target_button_uses_data_attribute_not_inline_js_literal() -> None:
    # target_id 를 JS 문자열 안에 직접 끼우면 따옴표가 섞인 id 에서 깨질 수 있다.
    html = admin_routes.templates.env.get_template("_auth_required.html").render(
        auth_required=[
            AuthRequiredRow(
                tenant_id=_TENANT,
                target_id="bad'id",
                profile_id=None,
                reason="ACCOUNT_AUTH_REQUIRED",
            )
        ]
    )

    assert "openAuthRequiredTarget(this.dataset.target)" in html
    assert 'data-target="bad&#39;id"' in html
    assert "openAuthRequiredTarget('" not in html


def test_require_admin_session_seam_can_deny() -> None:
    app = create_app(_FAKE_SETTINGS, dashboard_repository=_seeded_repo())

    def _deny(request) -> None:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="admin session required")

    app.state.require_admin_session = _deny
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(f"/admin?tenant={_TENANT}")
    assert r.status_code == 401
    # 전역 핸들러 envelope 통과.
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_admin_routes_registered_under_admin_prefix_not_v1() -> None:
    app = create_app(_FAKE_SETTINGS, dashboard_repository=_seeded_repo())
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/admin" in paths
    assert {
        "/admin/targets",
        "/admin/agents",
        "/admin/channels",
        "/admin/registered-settings",
        "/admin/auth-required",
    } <= paths
    # /v1/ 운영 가드와 무관(admin 은 HTML).
    assert "/v1/admin" not in paths


# ══════════════════════════════════════════════════════════════════════════
# (3) 무회귀 lock — jinja2 server extra · 7-dep
# ══════════════════════════════════════════════════════════════════════════

def _pyproject() -> dict:
    return tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_jinja2_declared_in_server_extra_not_main_deps() -> None:
    data = _pyproject()
    server = data["project"]["optional-dependencies"]["server"]
    assert any(dep.replace(" ", "").startswith("jinja2") for dep in server), server
    main_deps = {d.replace(" ", "") for d in data["project"]["dependencies"]}
    assert not any(d.startswith("jinja2") for d in main_deps)


def test_main_dependencies_still_exactly_seven() -> None:
    # rider_agent stdlib-only 표면 보호 — Gmail OAuth 제거 + IMAPClient 추가 후 main deps 7개 고정.
    assert len(_pyproject()["project"]["dependencies"]) == 7


# ══════════════════════════════════════════════════════════════════════════
# (4) secret 위생 — read-model DTO 에 token/secret 류 필드 0
# ══════════════════════════════════════════════════════════════════════════

def test_readmodel_dtos_have_no_secret_shaped_fields() -> None:
    forbidden = ("token", "secret", "password", "otp", "passwd", "_ref")
    for dto in (TargetRow, AgentRow, ChannelHealthRow, AuthRequiredRow, admin_routes.SettingsRow):
        for field in fields(dto):
            lowered = field.name.lower()
            assert not any(bad in lowered for bad in forbidden), f"{dto.__name__}.{field.name}"


# ══════════════════════════════════════════════════════════════════════════
# QA 보강 (5) 서비스 — fail-closed 가 CRITICAL freshness 도 덮어씀(AC3 강화)
# ══════════════════════════════════════════════════════════════════════════

def test_target_row_failclosed_overrides_even_critical_freshness() -> None:
    # 마지막 성공이 오래(41분 → CRITICAL)인데 인증까지 필요 → 시간 경과를 덮고 STOPPED 우선.
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        _target(
            target_id="t-both",
            last_success_at=_NOW - timedelta(minutes=41),
            account_auth_state="AUTH_REQUIRED",
        )
    )
    rows = asyncio.run(DashboardService().target_rows(repo, tenant_id=_TENANT, now=_NOW))
    assert rows[0].severity == SEVERITY_STOPPED


# ══════════════════════════════════════════════════════════════════════════
# QA 보강 (6) 인증 seam — fragment 도 보호·async seam·403 매핑
# ══════════════════════════════════════════════════════════════════════════

def _denying_app(exc: HTTPException):
    app = create_app(_FAKE_SETTINGS, dashboard_repository=_seeded_repo())

    def _deny(request) -> None:
        raise exc

    app.state.require_admin_session = _deny
    return app


def test_all_fragments_also_require_admin_session() -> None:
    # 풀 페이지뿐 아니라 HTMX fragment 도 같은 seam 으로 보호되어야 한다(우회 차단).
    app = _denying_app(HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="x"))
    c = TestClient(app, raise_server_exceptions=False)
    for path in (
        f"/admin/targets?tenant={_TENANT}",
        "/admin/agents",
        f"/admin/channels?tenant={_TENANT}",
        f"/admin/registered-settings?tenant={_TENANT}",
        f"/admin/auth-required?tenant={_TENANT}",
    ):
        assert c.get(path).status_code == 401, path


def test_require_admin_session_supports_async_seam() -> None:
    # seam 이 async 여도(awaitable 분기) 통과/거부 모두 동작.
    app_allow = create_app(_FAKE_SETTINGS, dashboard_repository=_seeded_repo())

    async def _async_allow(request) -> None:
        return None

    app_allow.state.require_admin_session = _async_allow
    assert TestClient(app_allow).get(f"/admin?tenant={_TENANT}").status_code == 200

    app_deny = create_app(_FAKE_SETTINGS, dashboard_repository=_seeded_repo())

    async def _async_deny(request) -> None:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="async denied")

    app_deny.state.require_admin_session = _async_deny
    assert TestClient(app_deny, raise_server_exceptions=False).get(
        f"/admin?tenant={_TENANT}"
    ).status_code == 401


def test_admin_seam_can_return_403_forbidden() -> None:
    # 401(미인증)뿐 아니라 403(권한 부족)도 전역 envelope 로 매핑(5.8 4역할 대비 seam).
    app = _denying_app(HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="role required"))
    r = TestClient(app, raise_server_exceptions=False).get(f"/admin?tenant={_TENANT}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


# ══════════════════════════════════════════════════════════════════════════
# QA 보강 (7) 템플릿 — 빈 상태 렌더·심각도 4단계 라벨/CSS·무-tenant 안전
# ══════════════════════════════════════════════════════════════════════════

def test_empty_repo_renders_empty_state_messages() -> None:
    # 데이터 없는 tenant — 각 fragment 의 {% else %} 분기가 안내문을 렌더(크래시 0).
    c = _client(InMemoryDashboardRepository())
    assert "표시할 대상이 없습니다." in c.get("/admin/targets?tenant=none").text
    assert "등록된 Agent 가 없습니다." in c.get("/admin/agents").text
    assert "인증 필요 대상이 없습니다." in c.get("/admin/auth-required?tenant=none").text
    # 채널은 행 고정(seed 없으면 0/0 기본값).
    channels = c.get("/admin/channels?tenant=none").text
    assert "0초" in channels and "0건" in channels


def test_severity_label_and_class_filters_map_all_four_levels() -> None:
    # 코드값 → 한글 라벨/CSS class 매핑 전수 + 미지 코드 안전 기본값(라우트 필터 단위).
    expected = {
        SEVERITY_NORMAL: ("정상", "sev-normal"),
        SEVERITY_WARNING: ("주의", "sev-warning"),
        SEVERITY_CRITICAL: ("위험", "sev-critical"),
        SEVERITY_STOPPED: ("중지", "sev-stopped"),
    }
    for code, (label, css) in expected.items():
        assert admin_routes._severity_label(code) == label
        assert admin_routes._severity_class(code) == css
    assert admin_routes._severity_label("NOPE") == "NOPE"  # 미지값은 코드 그대로
    assert admin_routes._severity_class("NOPE") == "sev-normal"  # 미지값은 정상 class


def test_failclosed_display_labels_distinguish_operator_visible_causes() -> None:
    expected = {
        SEVERITY_AUTH_REQUIRED: ("인증 필요", "sev-stopped"),
        SEVERITY_TARGET_VALIDATION_FAILURE: ("대상 검증 실패", "sev-stopped"),
        SEVERITY_OPERATOR_STOPPED: ("운영자 중지", "sev-stopped"),
    }

    for code, (label, css) in expected.items():
        assert admin_routes._severity_label(code) == label
        assert admin_routes._severity_class(code) == css


def test_targets_partial_renders_label_and_class_for_each_severity() -> None:
    # 템플릿이 필터를 통해 4단계를 모두 한글 라벨/CSS class 로 렌더(시각 무관 — 주입 행으로 결정적).
    # 라우트는 실시간 now 를 쓰므로(시간 경과 심각도 비결정적) 템플릿 렌더만 직접 검증한다.
    def _row(sev: str) -> TargetRow:
        return TargetRow(
            target_id=f"t-{sev}", tenant_id=_TENANT, name="가게", center_name="센터",
            platform="BAEMIN", interval_minutes=10, last_success_at=None,
            last_delivery_at=None, last_failure_code=None, severity=sev,
        )

    rows = [
        _row(s)
        for s in (
            SEVERITY_NORMAL,
            SEVERITY_WARNING,
            SEVERITY_CRITICAL,
            SEVERITY_STOPPED,
            SEVERITY_AUTH_REQUIRED,
            SEVERITY_TARGET_VALIDATION_FAILURE,
        )
    ]
    html = admin_routes.templates.env.get_template("_targets.html").render(targets=rows)
    for label in ("정상", "주의", "위험", "중지", "인증 필요", "대상 검증 실패"):
        assert label in html, label
    for css in ("sev-normal", "sev-warning", "sev-critical", "sev-stopped"):
        assert css in html, css


def test_failclosed_display_severity_drives_primary_actions_without_failure_code() -> None:
    rows = [
        TargetRow(
            target_id="t-auth",
            tenant_id=_TENANT,
            name="인증가게",
            center_name="센터",
            platform="BAEMIN",
            interval_minutes=10,
            last_success_at=None,
            last_delivery_at=None,
            last_failure_code=None,
            severity=SEVERITY_AUTH_REQUIRED,
        ),
        TargetRow(
            target_id="t-center",
            tenant_id=_TENANT,
            name="센터가게",
            center_name="",
            platform="COUPANG",
            interval_minutes=10,
            last_success_at=None,
            last_delivery_at=None,
            last_failure_code=None,
            severity=SEVERITY_TARGET_VALIDATION_FAILURE,
        ),
    ]

    html = admin_routes.templates.env.get_template("_targets.html").render(targets=rows)

    assert 'data-primary-action="auth-start"' in html
    assert "/admin/targets/t-auth/auth-start" in html
    assert 'data-primary-action="center-name"' in html
    assert "로그인 만료 · 인증 확인 필요" in html
    assert "센터/상점명 불일치" in html
    # 센터명 정정 후 같은 행에서 바로 재검증할 수 있도록 test-crawl 버튼이 노출돼야 한다
    # (불일치 해소 여부를 다음 스케줄 주기까지 기다리지 않게 한다).
    assert "/admin/targets/t-center/test-crawl" in html
    assert "지금 수집(재검증)" in html


def test_auth_required_reason_takes_precedence_over_latest_profile_failure() -> None:
    row = TargetRow(
        target_id="t-auth",
        tenant_id=_TENANT,
        name="인증가게",
        center_name="센터",
        platform="COUPANG",
        interval_minutes=10,
        last_success_at=None,
        last_delivery_at=None,
        last_failure_code="PROFILE_UNAVAILABLE",
        severity=SEVERITY_AUTH_REQUIRED,
    )

    html = admin_routes.templates.env.get_template("_targets.html").render(targets=[row])

    assert 'data-reason="로그인 만료 · 인증 확인 필요"' in html
    assert "브라우저 프로필 준비 실패" not in html


def test_auth_session_pending_target_prompts_user_to_enter_code_and_recheck() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        _target(
            target_id="t-pending",
            last_success_at=_NOW - timedelta(minutes=1),
            account_auth_state="ACTIVE",
            lifecycle_state="ACTIVE",
            auth_session_pending=True,
        )
    )

    rows = asyncio.run(DashboardService().target_rows(repo, tenant_id=_TENANT, now=_NOW))
    display_rows = [
        admin_routes._target_row_for_display(facts, _NOW)
        for facts in asyncio.run(repo.target_health(tenant_id=_TENANT, now=_NOW))
    ]
    html = admin_routes.templates.env.get_template("_targets.html").render(
        targets=display_rows,
        tenant_id=_TENANT,
    )

    assert rows[0].severity == SEVERITY_STOPPED
    assert display_rows[0].severity == SEVERITY_AUTH_REQUIRED
    assert "인증번호 입력 필요" in html
    assert 'data-primary-action="auth-check"' in html
    assert "/admin/targets/t-pending/auth-check" in html
    assert "/admin/targets/t-pending/auth-start" not in html


def test_profile_unavailable_reason_is_operator_readable() -> None:
    assert admin_routes._reason_text("PROFILE_UNAVAILABLE") == "브라우저 프로필 준비 실패 — Agent/Chrome 확인 필요"


def test_target_rows_use_explicit_detail_button_and_local_result_region() -> None:
    # 실파이프라인이 현재 인증실패에 내는 값은 SEVERITY_AUTH_REQUIRED(STOPPED+묵은 코드 아님).
    # 배지 구동은 severity 단일 소스이므로 fixture 도 그 값을 쓴다(2026-06).
    row = TargetRow(
        target_id="t-auth",
        tenant_id=_TENANT,
        name="가게",
        center_name="센터",
        platform="BAEMIN",
        interval_minutes=10,
        last_success_at=None,
        last_delivery_at=None,
        last_failure_code="AUTH_REQUIRED",
        severity=SEVERITY_AUTH_REQUIRED,
    )

    html = admin_routes.templates.env.get_template("_targets.html").render(targets=[row])

    assert 'role="button"' not in html
    assert 'data-primary-action="auth-start"' in html
    assert 'aria-label="가게 상세 열기"' in html
    assert 'id="target-result-t-auth"' in html
    assert 'hx-target="#target-result-t-auth"' in html


def test_dashboard_counts_display_failclosed_states_as_action_required_work() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        _target(
            target_id="t-auth-state",
            last_success_at=_NOW - timedelta(minutes=1),
            account_auth_state="AUTH_REQUIRED",
            lifecycle_state="ACTIVE",
        )
    )

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    assert '<span class="n" id="target-count-action-required">1</span><span class="lbl">조치 필요</span>' in body
    assert '<span class="n">1</span><span class="lbl">중지</span>' not in body
    assert 'data-primary-action="auth-start"' in body
    assert "r.dataset.severity === \"AUTH_REQUIRED\"" in body


def test_manual_action_auth_states_render_as_auth_required_work() -> None:
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        _target(
            target_id="t-user-action",
            last_success_at=_NOW - timedelta(minutes=1),
            account_auth_state="USER_ACTION_PENDING",
            lifecycle_state="ACTIVE",
        )
    )
    repo.seed_target(
        _target(
            target_id="t-captcha",
            last_success_at=_NOW - timedelta(minutes=1),
            account_auth_state="BLOCKED_OR_CAPTCHA",
            lifecycle_state="ACTIVE",
        )
    )

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    assert body.count('data-severity="AUTH_REQUIRED"') == 2
    assert body.count('data-primary-action="auth-start"') == 2
    assert '<span class="n" id="target-count-action-required">2</span><span class="lbl">조치 필요</span>' in body


# ── 인증 완료 후 묵은 AUTH_REQUIRED 실패가 배지를 띄우지 않음(2026-06 회귀) ─────────

def test_auth_verified_with_stale_failure_renders_normal_not_auth_required() -> None:
    # 인증 완료(AUTH_VERIFIED) + 마지막 성공보다 과거의 AUTH_REQUIRED 실패 → 정상으로 표시.
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        _target(
            target_id="t-verified",
            last_success_at=_NOW - timedelta(minutes=1),
            last_failure_code="AUTH_REQUIRED",
            last_failure_at=_NOW - timedelta(minutes=30),
            account_auth_state="AUTH_VERIFIED",
        )
    )
    rows = asyncio.run(
        DashboardService().target_rows(repo, tenant_id=_TENANT, now=_NOW)
    )
    assert rows[0].severity == SEVERITY_NORMAL


def test_auth_verified_with_stale_failure_hides_auth_badge_in_dashboard() -> None:
    # 라우트는 실서버 now 를 쓰므로 최근 성공으로 보이도록 실시간 기준 시각을 쓴다(배지 부재만
    # 단언 — 작은 시계 오차에 견고). 인증 후 정상 상태 시나리오를 엔드투엔드로 잠근다.
    real_now = datetime.now(timezone.utc)
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        _target(
            target_id="t-verified",
            last_success_at=real_now - timedelta(minutes=1),
            last_failure_code="AUTH_REQUIRED",
            last_failure_at=real_now - timedelta(minutes=30),
            account_auth_state="AUTH_VERIFIED",
        )
    )

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    assert "로그인 만료 · 인증 확인 필요" not in body
    assert 'data-primary-action="auth-start"' not in body


def test_template_normal_severity_with_stale_failure_code_shows_no_auth_badge() -> None:
    # 템플릿 단독 락(Edit5) — severity 가 NORMAL 이면 묵은 last_failure_code 가 있어도 배지 없음.
    row = TargetRow(
        target_id="t-verified",
        tenant_id=_TENANT,
        name="가게",
        center_name="센터",
        platform="BAEMIN",
        interval_minutes=10,
        last_success_at=_NOW - timedelta(minutes=1),
        last_delivery_at=None,
        last_failure_code="AUTH_REQUIRED",
        severity=SEVERITY_NORMAL,
    )

    html = admin_routes.templates.env.get_template("_targets.html").render(targets=[row])

    assert "로그인 만료 · 인증 확인 필요" not in html
    assert 'data-primary-action="auth-start"' not in html


def test_dashboard_drawer_is_hidden_until_open_and_has_context_result_region() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'id="drawer" role="dialog"' in body
    assert 'hidden inert aria-hidden="true"' in body
    assert 'id="drawer-result"' in body
    assert 'id="drawer-actions"' in body
    assert 'renderDrawerActions' in body
    assert 'htmx:beforeSwap' in body
    assert 'isActionResultTarget' in body
    assert 'syncOpenDrawerFromRows' in body
    assert 'trapDrawerFocus' in body


def test_drawer_does_not_offer_pause_for_failclosed_display_states() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert "canPause" in body
    assert '["NORMAL", "WARNING", "CRITICAL"].indexOf(d.severity)' in body
    assert 'if (d.severity !== "STOPPED") box.appendChild(makeDrawerButton("비활성화"' not in body


def test_targets_refresh_on_entity_change_event() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'id="targets" hx-get="/admin/targets?tenant=tn-1"' in body
    assert 'hx-trigger="admin-action-refresh from:body, admin-entity-changed from:body delay:2s, every 30s"' in body


def test_dashboard_bursts_refresh_after_admin_action() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'admin-action-refresh from:body' in body
    assert 'triggerAdminRefreshBurst' in body
    assert "setTimeout(function () { htmx.trigger(document.body, \"admin-action-refresh\"); }, delay);" in body
    assert 'id="auth-required"' in body
    assert 'hx-get="/admin/auth-required?tenant=tn-1"' in body
    assert 'hx-trigger="admin-action-refresh from:body, every 30s"' in body
    assert 'hx-target="#auth-required-section"' in body
    assert 'id="agents" hx-get="/admin/agents" hx-trigger="admin-action-refresh from:body, every 30s"' in body


def test_target_deeplink_route_seeds_initial_drawer_target() -> None:
    body = _client(_seeded_repo()).get(f"/admin/t/t-stopped?tenant={_TENANT}").text

    assert 'data-initial-target="t-stopped"' in body
    assert "/admin/t/" in body
    assert "history.pushState" in body
    assert "keepUrl: true" in body
    assert "openInitialTarget" in body


def test_missing_target_deeplink_exposes_workbench_notice() -> None:
    body = _client(_seeded_repo()).get(f"/admin/t/no-such-target?tenant={_TENANT}").text

    assert 'data-initial-target="no-such-target"' in body
    assert 'id="target-notice"' in body
    assert "showTargetNotice" in body
    assert "업체를 찾지 못했습니다." in body


def test_drawer_activate_confirmation_uses_activate_wording() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'drawerConfirm("활성화")' in body
    assert 'drawerConfirm("비활성화")' in body
    assert 'drawerPost("/activate", {}, true)' not in body


def test_dashboard_drawer_contains_center_name_edit_flow() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'id="d-center-edit"' in body
    assert 'id="d-center-input"' in body
    assert 'drawerUpdateCenterName' in body
    assert "/admin/monitoring-targets/" in body


def test_drawer_refresh_sync_preserves_editor_and_result_state_contract() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text
    open_fn = body[body.index("function openTargetDrawer"):body.index("function closeDrawer")]
    sync_fn = body[body.index("function syncOpenDrawerFromRows"):body.index("function openInitialTarget")]

    preserve_guard = open_fn.index("if (!opts.preserveState)")
    assert open_fn.index('document.getElementById("d-center-input").value = d.center || "";') > preserve_guard
    assert open_fn.index('document.getElementById("d-center-edit").hidden = true;') > preserve_guard
    assert open_fn.index('document.getElementById("drawer-result").textContent = "";') > preserve_guard
    assert "preserveState: true" in sync_fn


def test_drawer_close_restores_focus_before_hiding_dialog_contract() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text
    close_fn = body[body.index("function closeDrawer"):body.index("function makeDrawerButton")]

    hide_at = close_fn.index("drawer.hidden = true")
    aria_hidden_at = close_fn.index('drawer.setAttribute("aria-hidden", "true")')
    assert close_fn.index("restore.focus()") < hide_at
    assert close_fn.index("active.blur()") < hide_at
    assert hide_at < aria_hidden_at


def test_dashboard_hides_raw_id_debug_action_panel_by_default() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert "subscription_id" not in body
    assert "dispatch_id" not in body
    assert "act-job-id" not in body


def test_dashboard_mobile_actions_keep_touch_target_size() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text
    mobile_css = body[body.index("@media (max-width: 720px)"):]

    assert "min-height: 44px" in mobile_css
    assert ".trow > .sev-badge { display: none; }" not in mobile_css
    assert '"bar badge badge"' in mobile_css
    assert ".trow > .sev-badge { grid-area: badge;" in mobile_css
    assert ".t-reason { grid-area: reason;" in mobile_css


def test_dashboard_mobile_status_text_can_wrap() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text
    mobile_css = body[body.index("@media (max-width: 720px)"):]

    assert ".ministatus .seg" in mobile_css
    assert "white-space: normal" in mobile_css
    assert "overflow-wrap: anywhere" in mobile_css


def test_auth_required_fragment_names_the_target_not_generic_label() -> None:
    body = _client(_seeded_repo()).get(f"/admin/auth-required?tenant={_TENANT}").text

    assert "가게" in body
    assert "로그인 만료 · 인증 확인 필요" in body
    assert "인증 필요 대상</td>" not in body
    assert "ACCOUNT_AUTH_REQUIRED" not in body
    assert "/admin/targets/t-stopped/auth-start" in body


def test_auth_required_fragment_uses_row_tenant_for_all_tenants_action() -> None:
    html = admin_routes.templates.env.get_template("_auth_required.html").render(
        auth_required=[
            AuthRequiredRow(
                tenant_id=_OTHER_TENANT,
                target_id="t-other",
                profile_id="p-other",
                reason="ACCOUNT_AUTH_REQUIRED",
                target_name="타고객가게",
            )
        ],
        tenant_id="all",
    )

    assert "/admin/targets/t-other/auth-start?tenant=tn-2" in html
    assert "/admin/targets/t-other/auth-start?tenant=all" not in html


def test_auth_required_fragment_offers_direct_status_recheck() -> None:
    html = admin_routes.templates.env.get_template("_auth_required.html").render(
        auth_required=[
            AuthRequiredRow(
                tenant_id=_TENANT,
                target_id="t-stopped",
                profile_id="p1",
                reason="ACCOUNT_AUTH_REQUIRED",
                target_name="가게",
            )
        ],
        tenant_id=_TENANT,
    )

    assert "상태 재확인" in html
    assert "/admin/targets/t-stopped/auth-check?tenant=tn-1" in html


def test_auth_required_fragment_names_auth_session_pending_action() -> None:
    html = admin_routes.templates.env.get_template("_auth_required.html").render(
        auth_required=[
            AuthRequiredRow(
                tenant_id=_TENANT,
                target_id="t-pending",
                profile_id="p1",
                reason="AUTH_SESSION_PENDING",
                target_name="가게",
            )
        ],
        tenant_id=_TENANT,
    )

    assert "가게" in html
    assert "인증번호 입력 필요" in html
    assert "오류 — 확인 필요" not in html
    assert "/admin/targets/t-pending/auth-check?tenant=tn-1" in html
    assert "/admin/targets/t-pending/auth-start?tenant=tn-1" not in html


def test_dashboard_full_page_without_tenant_param_renders() -> None:
    # ?tenant 미지정(빈 tenant seam) 이어도 200 — 대상은 빈 안내문, agent fleet 은 전역 표시.
    r = _client(_seeded_repo()).get("/admin")
    assert r.status_code == 200
    body = r.text
    assert "표시할 대상이 없습니다." in body  # tenant="" 데이터 없음
    assert "agent-online" in body  # fleet 은 tenant 무관 표시


def test_dashboard_without_tenant_uses_single_known_tenant() -> None:
    entity_repo = InMemoryAdminEntityRepository()
    entity_repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="고객",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
        )
    )
    app = _allow_viewer(
        create_app(
            _FAKE_SETTINGS,
            dashboard_repository=_seeded_repo(),
            admin_entity_service=AdminEntityService(entity_repo),
        )
    )
    r = TestClient(app, raise_server_exceptions=False).get("/admin")

    assert r.status_code == 200
    assert ">고객 · 고객</span>" in r.text
    assert 'title="tenant · tn-1"' in r.text
    assert 'hx-get="/admin/targets?tenant=tn-1"' in r.text


def test_dashboard_header_prefers_customer_name_over_tenant_id() -> None:
    entity_repo = InMemoryAdminEntityRepository()
    entity_repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="상호 고객",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
        )
    )
    app = _allow_viewer(
        create_app(
            _FAKE_SETTINGS,
            dashboard_repository=_seeded_repo(),
            admin_entity_service=AdminEntityService(entity_repo),
        )
    )

    body = TestClient(app, raise_server_exceptions=False).get(f"/admin?tenant={_TENANT}").text

    assert ">고객 · 상호 고객</span>" in body
    assert 'title="tenant · tn-1"' in body
    assert ">tenant · tn-1</span>" not in body


def test_dashboard_without_tenant_multiple_known_tenants_renders_switcher() -> None:
    entity_repo = InMemoryAdminEntityRepository()
    entity_repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="고객A",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
        )
    )
    entity_repo.seed_tenant(
        Tenant(
            id=_OTHER_TENANT,
            name="고객B",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
        )
    )
    app = _allow_viewer(
        create_app(
            _FAKE_SETTINGS,
            dashboard_repository=_seeded_repo(),
            admin_entity_service=AdminEntityService(entity_repo),
        )
    )
    body = TestClient(app, raise_server_exceptions=False).get("/admin").text

    assert 'id="tenant-switch"' in body
    assert 'value="tn-1"' in body
    assert 'value="tn-2"' in body
    assert "switchTenant" in body
    assert 'hx-get="/admin/targets?tenant=tn-1"' in body


def test_dashboard_all_tenants_renders_option_customer_names_and_safe_action_tenants() -> None:
    entity_repo = InMemoryAdminEntityRepository()
    entity_repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="고객A",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
        )
    )
    entity_repo.seed_tenant(
        Tenant(
            id=_OTHER_TENANT,
            name="고객B",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
        )
    )
    repo = InMemoryDashboardRepository()
    repo.seed_target(
        TargetHealthFacts(
            target_id="t-a",
            tenant_id=_TENANT,
            customer_name="고객A",
            name="가게A",
            center_name="센터",
            platform="BAEMIN",
            interval_minutes=10,
            last_success_at=_NOW,
            last_delivery_at=None,
            last_failure_code=None,
            account_auth_state="ACTIVE",
            lifecycle_state="ACTIVE",
        )
    )
    repo.seed_target(
        TargetHealthFacts(
            target_id="t-b",
            tenant_id=_OTHER_TENANT,
            customer_name="고객B",
            name="가게B",
            center_name="센터",
            platform="BAEMIN",
            interval_minutes=10,
            last_success_at=_NOW,
            last_delivery_at=None,
            last_failure_code=None,
            account_auth_state="ACTIVE",
            lifecycle_state="ACTIVE",
        )
    )
    app = _allow_viewer(
        create_app(
            _FAKE_SETTINGS,
            dashboard_repository=repo,
            admin_entity_service=AdminEntityService(entity_repo),
        )
    )

    body = TestClient(app, raise_server_exceptions=False).get("/admin?tenant=all").text

    assert '<option value="all" selected>전체 고객</option>' in body
    assert 'hx-get="/admin/targets?tenant=all"' in body
    assert "고객: 고객A" in body and "고객: 고객B" in body
    assert "/admin/targets/t-a/test-crawl?tenant=tn-1" in body
    assert "/admin/targets/t-b/test-crawl?tenant=tn-2" in body
    assert "/admin/targets/t-a/test-crawl?tenant=all" not in body
    assert "전체 고객 보기에서는 작업 고객을 먼저 선택하세요." in body


def test_dashboard_hash_manage_opens_manage_tab() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'function initialMode()' in body
    assert 'location.hash === "#manage"' in body
    assert 'switchMode(initialMode()' in body


def test_dashboard_query_mode_manage_opens_manage_tab_after_tenant_switch() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}&mode=manage").text

    assert 'new URLSearchParams(location.search).get("mode") === "manage"' in body
    assert 'switchMode(initialMode()' in body


class _FailingSessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        raise AssertionError("empty tenant should not query Postgres")

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_postgres_dashboard_repository_empty_tenant_is_empty_without_uuid_query() -> None:
    repo = PostgresDashboardRepository(_FailingSessionFactory())  # type: ignore[arg-type]

    assert asyncio.run(repo.target_health(tenant_id="", now=_NOW)) == []
    assert asyncio.run(repo.channel_health(tenant_id="", now=_NOW)) == ChannelHealthRow(
        kakao_queue_lag_seconds=0,
        telegram_error_count=0,
    )
    assert asyncio.run(repo.auth_required(tenant_id="")) == []


# ══════════════════════════════════════════════════════════════════════════
# QA 보강 (8) 읽기 전용 런타임 — 라우트가 read 메서드만 호출·포트 표면 lock
# ══════════════════════════════════════════════════════════════════════════

class _RecordingRepo(InMemoryDashboardRepository):
    """런타임에 호출된 메서드 이름을 기록(읽기 전용 행위 검증용)."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def target_health(self, **kw):  # type: ignore[override]
        self.calls.append("target_health")
        return await super().target_health(**kw)

    async def agent_health(self, **kw):  # type: ignore[override]
        self.calls.append("agent_health")
        return await super().agent_health(**kw)

    async def channel_health(self, **kw):  # type: ignore[override]
        self.calls.append("channel_health")
        return await super().channel_health(**kw)

    async def auth_required(self, **kw):  # type: ignore[override]
        self.calls.append("auth_required")
        return await super().auth_required(**kw)


def test_full_page_invokes_only_read_methods() -> None:
    repo = _RecordingRepo()
    TestClient(_allow_viewer(create_app(_FAKE_SETTINGS, dashboard_repository=repo))).get(
        f"/admin?tenant={_TENANT}"
    )
    # 풀 페이지는 4개 read 포트만 호출(write/전이 호출 0 — 읽기 전용 런타임).
    assert set(repo.calls) == {
        "target_health",
        "agent_health",
        "channel_health",
        "auth_required",
    }


def test_agent_summary_can_sync_after_agents_fragment_swap() -> None:
    repo = InMemoryDashboardRepository()
    real_now = datetime.now(timezone.utc)
    repo.seed_agent(
        AgentHealthFacts(
            agent_id="a-online",
            name="agent-online",
            version="1.0.0",
            last_heartbeat_at=real_now,
            current_job_type=None,
            capabilities=(),
        )
    )
    repo.seed_agent(
        AgentHealthFacts(
            agent_id="a-offline",
            name="agent-offline",
            version="1.0.0",
            last_heartbeat_at=real_now - timedelta(minutes=5),
            current_job_type=None,
            capabilities=(),
        )
    )

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    assert 'id="agent-summary-seg"' in body
    assert 'id="agent-summary-tag"' in body
    assert 'data-agent-total="2"' in body
    assert 'data-agent-online="1"' in body
    assert "function syncAgentSummaryFromFragment" in body
    assert 'e.target && e.target.id === "agents"' in body


def test_target_summary_can_sync_after_targets_fragment_swap() -> None:
    body = _client(_seeded_repo()).get(f"/admin?tenant={_TENANT}").text

    assert 'id="target-count-all"' in body
    assert 'data-target-total="3"' in body
    assert 'data-target-auth-required="1"' in body
    assert "function syncTargetSummaryFromRows" in body
    assert 'e.target && e.target.id === "targets"' in body


def test_dashboard_repository_port_exposes_only_read_methods() -> None:
    # 포트 표면에 write/전이 메서드가 아예 없음(타입으로 읽기 전용 보장 — AST 가드와 상보).
    public = {n for n in vars(DashboardRepository).keys() if not n.startswith("_")}
    assert public == {
        "target_health",
        "critical_target_health",
        "agent_health",
        "channel_health",
        "auth_required",
        "active_jobs",  # 실시간 큐 뷰(select 전용 읽기 — write/전이 아님)
    }


# ══════════════════════════════════════════════════════════════════════════
# "등록된 설정" 카드 — 등록 config(AdminEntityService) + 라이브 램프 조립(읽기 전용)
# ══════════════════════════════════════════════════════════════════════════

def _settings_app(
    *,
    sending_enabled: bool = True,
    seed_health: bool = True,
    seed_entities: bool = True,
) -> TestClient:
    """"등록된 설정" 카드용 app — dashboard repo(라이브 램프) + 엔티티 repo(등록 config) 동시 seed.

    tenant ``tn-1`` 에 BAEMIN/COUPANG 계정, 대상 2건(tg-a ACTIVE·예약 09:00~22:00,
    tg-b PAUSED·예약 off), TELEGRAM/KAKAO 채널, 전송규칙(tg-a→두 채널 enabled · tg-b→tel disabled)을
    심는다. tg-a 는 최근 수집 성공 health 를 심어 램프가 NORMAL 로 떨어지게 한다.
    """

    entity_repo = InMemoryAdminEntityRepository()
    if seed_entities:
        entity_repo.seed_tenant(
            Tenant(
                id=_TENANT,
                name="고객",
                status=CustomerLifecycleState.ACTIVE,
                created_at=_NOW,
                sending_enabled=sending_enabled,
            )
        )
        entity_repo.seed_platform_account(
            PlatformAccount(id="acc-b", tenant_id=_TENANT, platform=Platform.BAEMIN, label="배민계정")
        )
        entity_repo.seed_platform_account(
            PlatformAccount(id="acc-c", tenant_id=_TENANT, platform=Platform.COUPANG, label="쿠팡계정")
        )
        entity_repo.seed_monitoring_target(
            MonitoringTarget(
                id="tg-a",
                tenant_id=_TENANT,
                platform_account_id="acc-b",
                name="강남점",
                center_name="강남센터",
                interval_minutes=10,
                schedule_enabled=True,
                start_time="09:00",
                stop_time="22:00",
                status=MonitoringTargetStatus.ACTIVE,
            )
        )
        entity_repo.seed_monitoring_target(
            MonitoringTarget(
                id="tg-b",
                tenant_id=_TENANT,
                platform_account_id="acc-c",
                name="역삼점",
                center_name="역삼센터",
                interval_minutes=5,
                schedule_enabled=False,
                status=MonitoringTargetStatus.PAUSED,
            )
        )
        entity_repo.seed_messenger_channel(
            MessengerChannel(
                id="ch-t",
                tenant_id=_TENANT,
                messenger=Messenger.TELEGRAM,
                telegram_chat_id="100",
                state=MessengerChannelState.ACTIVE,
            )
        )
        entity_repo.seed_messenger_channel(
            MessengerChannel(
                id="ch-k",
                tenant_id=_TENANT,
                messenger=Messenger.KAKAO,
                kakao_room_name="강남방",
                state=MessengerChannelState.ACTIVE,
            )
        )
        # tg-a 는 두 채널로 fan-out(메신저 뱃지 2개), tg-b 는 비활성 규칙뿐(전송 OFF 검증).
        entity_repo.seed_delivery_rule(DeliveryRule(id="r-1", target_id="tg-a", channel_id="ch-t", enabled=True))
        entity_repo.seed_delivery_rule(DeliveryRule(id="r-2", target_id="tg-a", channel_id="ch-k", enabled=True))
        entity_repo.seed_delivery_rule(DeliveryRule(id="r-3", target_id="tg-b", channel_id="ch-t", enabled=False))

    repo = InMemoryDashboardRepository()
    if seed_health:
        # 라우트 fragment 는 실 now() 로 severity 를 합성하므로(주입 now 아님) 신선도가 NORMAL 로
        # 떨어지려면 실시간 기준 최근 성공이어야 한다(_NOW 는 2026 고정이라 stale 처리됨).
        recent = datetime.now(timezone.utc) - timedelta(minutes=2)
        repo.seed_target(_target(target_id="tg-a", name="강남점", last_success_at=recent))
    app = _allow_viewer(
        create_app(
            _FAKE_SETTINGS,
            dashboard_repository=repo,
            admin_entity_service=AdminEntityService(entity_repo),
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_registered_settings_fragment_renders_assembled_rows() -> None:
    r = _settings_app().get(f"/admin/registered-settings?tenant={_TENANT}")

    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    # 대상/센터·시간·주기.
    assert "강남점" in body and "역삼점" in body
    assert "강남센터" in body
    assert "09:00 ~ 22:00" in body  # tg-a 예약 시간창
    assert "상시" in body  # tg-b 예약 off → 상시
    assert "10분" in body  # tg-a 주기
    # 수집(크롤링) ON/OFF: tg-a ACTIVE → ON, tg-b PAUSED → OFF(둘 다 한 표에 존재).
    assert "toggle-pill on" in body and "toggle-pill off" in body
    # 메신저 fan-out: tg-a 가 텔레그램+카카오 둘 다.
    assert "텔레그램" in body and "카카오" in body
    # 플랫폼: 배민(tg-a)·쿠팡(tg-b).
    assert "배민" in body and "쿠팡" in body
    # 라이브 램프: tg-a 최근 성공 → 정상(sev-normal).
    assert "sev-normal" in body


def test_registered_settings_send_gate_respects_tenant_sending_enabled() -> None:
    # sending_enabled=False 면 enabled 규칙·ACTIVE 채널이 있어도 전송은 OFF 여야 한다.
    body_on = _settings_app(sending_enabled=True).get(
        f"/admin/registered-settings?tenant={_TENANT}"
    ).text
    body_off = _settings_app(sending_enabled=False).get(
        f"/admin/registered-settings?tenant={_TENANT}"
    ).text

    # 켜짐: 전송 ON pill 이 최소 1개(tg-a). 꺼짐: 전송 ON 은 사라지고 크롤링 ON(tg-a)만 남는다.
    assert body_on.count("toggle-pill on") > body_off.count("toggle-pill on")
    # 게이트 OFF 라도 크롤링(수집) ON 은 유지(tg-a ACTIVE) → ON pill 이 정확히 1개.
    assert body_off.count("toggle-pill on") == 1


def test_registered_settings_empty_state() -> None:
    body = _settings_app(seed_entities=False, seed_health=False).get(
        f"/admin/registered-settings?tenant={_TENANT}"
    ).text
    assert "등록된 설정이 없습니다." in body


def test_dashboard_full_page_includes_registered_settings_card() -> None:
    body = _settings_app().get(f"/admin?tenant={_TENANT}").text

    assert "등록된 설정" in body
    assert 'id="registered-settings"' in body
    assert "/admin/registered-settings?tenant=" in body
    # 등록 config 변경 후 갱신 트리거(쓰기 커밋 착지 대기 delay:2s, targets 카드 선례).
    assert "admin-entity-changed from:body delay:2s" in body
    # 첫 페인트 사전 렌더(무-플래시): 카드 안에 행 데이터가 이미 들어 있다.
    assert "강남점" in body


def test_registered_settings_messenger_label_filter() -> None:
    assert admin_routes._messenger_label("TELEGRAM") == "텔레그램"
    assert admin_routes._messenger_label("KAKAO") == "카카오"
    assert admin_routes._messenger_label("telegram") == "텔레그램"  # 대소문자 무관
    assert admin_routes._messenger_label("UNKNOWN") == "UNKNOWN"  # 미지 코드 그대로
    assert admin_routes._messenger_label(None) == ""

