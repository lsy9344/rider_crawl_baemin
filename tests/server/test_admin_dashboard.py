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
from rider_server.domain import CustomerLifecycleState, Tenant
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
    account_auth_state: str | None = "ACTIVE",
    lifecycle_state: str | None = "ACTIVE",
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
        account_auth_state=account_auth_state,
        lifecycle_state=lifecycle_state,
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
    assert {"/admin/targets", "/admin/agents", "/admin/channels", "/admin/auth-required"} <= paths
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
    for dto in (TargetRow, AgentRow, ChannelHealthRow, AuthRequiredRow):
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


def test_profile_unavailable_reason_is_operator_readable() -> None:
    assert admin_routes._reason_text("PROFILE_UNAVAILABLE") == "브라우저 프로필 준비 실패 — Agent/Chrome 확인 필요"


def test_target_rows_use_explicit_detail_button_and_local_result_region() -> None:
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
        severity=SEVERITY_STOPPED,
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

    assert '<span class="n">1</span><span class="lbl">조치 필요</span>' in body
    assert '<span class="n">1</span><span class="lbl">중지</span>' not in body
    assert 'data-primary-action="auth-start"' in body
    assert "r.dataset.severity === \"AUTH_REQUIRED\"" in body


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
    assert 'id="auth-required" hx-get="/admin/auth-required?tenant=tn-1" hx-trigger="admin-action-refresh from:body, every 30s"' in body
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


def test_dashboard_repository_port_exposes_only_read_methods() -> None:
    # 포트 표면에 write/전이 메서드가 아예 없음(타입으로 읽기 전용 보장 — AST 가드와 상보).
    public = {n for n in vars(DashboardRepository).keys() if not n.startswith("_")}
    assert public == {
        "target_health",
        "critical_target_health",
        "agent_health",
        "channel_health",
        "auth_required",
    }

