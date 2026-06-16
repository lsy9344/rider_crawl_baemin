"""Story 5.8 / AC2 — Admin 접근 제어(MFA·4역할·IP allowlist) + audit-on-deny.

4-tier 중 always-run 순수/service + 라우트(TestClient) 계층:
  (1) 순수 정책: ``AdminRole`` count-lock 4·``role_satisfies`` rank 단조·``ip_allowed``(exact/CIDR/
      deny/empty/garbage fail-closed)·``is_privileged``.
  (2) 라우트 게이트: principal 미해결 401·VIEWER 가 운영 액션 403·MFA 미검증 403·IP 불허 403·
      OPERATOR 통과 200·SECRET_ADMIN 가 OPERATOR 게이트 통과·BREAK_GLASS override+강제 audit.
  (3) 거부 시도는 ``result=DENIED`` audit(service 경유 — read-only 가드 정합).
  (4) security 모듈 단방향 import 가드(AST — ``rider_agent`` 0, ``rider_server`` → ``rider_crawl`` 만).

fake 값만(실제 토큰/전화/이메일/chat_id 형태 0). 평면 ``tests/server/`` 컨벤션. 라우트는 실
``now()`` 라 시각 단언은 하지 않고 인가 성공/거부/audit result 만 본다(memory admin-routes-wallclock).
"""

from __future__ import annotations

import ast
import asyncio
import sys
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rider_server.domain import MonitoringTarget, MonitoringTargetStatus
from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.security import (
    AdminPrincipal,
    AdminRole,
    ip_allowed,
    is_privileged,
    role_satisfies,
)
from rider_server.services.admin_action_service import (
    AdminActionService,
    InMemoryAdminActionRepository,
)
from rider_server.settings import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_DIR = _REPO_ROOT / "src" / "rider_server" / "security"
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_TENANT = "tn-1"
_ACTOR = "11111111-1111-1111-1111-111111111111"
_SAME_ORIGIN_HEADERS = {"Origin": "http://testserver"}


def _run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 정책 — count-lock·rank·IP allowlist (DB 불필요, 결정적)
# ══════════════════════════════════════════════════════════════════════════

def test_admin_role_has_exact_4_members_in_contract_order() -> None:
    # architecture line 144-145 정본 4역할 — 5번째 추가 금지(자체 count-lock).
    assert [m.name for m in AdminRole] == ["VIEWER", "OPERATOR", "SECRET_ADMIN", "BREAK_GLASS"]
    assert [m.value for m in AdminRole] == ["VIEWER", "OPERATOR", "SECRET_ADMIN", "BREAK_GLASS"]
    assert len(list(AdminRole)) == 4
    for m in AdminRole:
        assert isinstance(m, str) and m.value == m.name  # (str, Enum) 컨벤션


def test_role_satisfies_is_monotonic_rank() -> None:
    # 상위 역할이 하위 권한 포함(break-glass = 전 권한).
    assert role_satisfies(AdminRole.SECRET_ADMIN, AdminRole.OPERATOR)
    assert role_satisfies(AdminRole.BREAK_GLASS, AdminRole.SECRET_ADMIN)
    assert role_satisfies(AdminRole.OPERATOR, AdminRole.OPERATOR)
    assert role_satisfies(AdminRole.VIEWER, AdminRole.VIEWER)
    # 하위는 상위 게이트 불통과.
    assert not role_satisfies(AdminRole.VIEWER, AdminRole.OPERATOR)
    assert not role_satisfies(AdminRole.OPERATOR, AdminRole.SECRET_ADMIN)


def test_is_privileged_only_above_viewer() -> None:
    assert not is_privileged(AdminRole.VIEWER)
    assert is_privileged(AdminRole.OPERATOR)
    assert is_privileged(AdminRole.SECRET_ADMIN)
    assert is_privileged(AdminRole.BREAK_GLASS)


def test_ip_allowed_exact_cidr_and_failclosed() -> None:
    assert ip_allowed("10.0.0.5", ("10.0.0.5",)) is True
    assert ip_allowed("10.1.2.3", ("10.0.0.0/8",)) is True
    assert ip_allowed("192.168.1.1", ("10.0.0.0/8",)) is False
    # allowlist 미설정 → 추가 제한 없음(opt-in).
    assert ip_allowed("anything", ()) is True
    # source 미상(파싱 불가)인데 allowlist 설정됨 → fail-closed deny.
    assert ip_allowed("not-an-ip", ("10.0.0.0/8",)) is False
    # 잘못된 allowlist 항목은 무시하고 나머지로 판정.
    assert ip_allowed("10.0.0.1", ("bogus", "10.0.0.0/8")) is True


# ══════════════════════════════════════════════════════════════════════════
# (2)(3) 라우트 게이트 — 주입 principal 별 인가 + audit-on-deny
# ══════════════════════════════════════════════════════════════════════════

def _target(status=MonitoringTargetStatus.ACTIVE) -> MonitoringTarget:
    return MonitoringTarget(
        id="mt-1", tenant_id=_TENANT, platform_account_id="pa-1",
        name="가게", center_name="센터", status=status,
    )


def _app(principal: AdminPrincipal | None, *, ip_allowlist=(), mfa_required=True):
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    app = create_app(
        _FAKE_SETTINGS,
        admin_action_service=AdminActionService(repo, InMemoryQueueBackend()),
    )
    app.state.resolve_admin_principal = lambda request: principal
    app.state.admin_ip_allowlist = ip_allowlist
    app.state.admin_mfa_required = mfa_required
    return app, repo


def _principal(role: AdminRole, *, mfa=True, source="ADMIN_UI") -> AdminPrincipal:
    return AdminPrincipal(actor_id=_ACTOR, role=role, mfa_verified=mfa, source=source)


def test_no_principal_is_401() -> None:
    app, _ = _app(None)
    resp = TestClient(app).post("/admin/targets/mt-1/pause?tenant=tn-1")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_operator_passes_action_gate() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    )
    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.PAUSED
    # 성공 액션 audit 에 source 가 principal 출처로 채워진다(redaction 통과).
    assert repo.audits[-1].source == "ADMIN_UI"
    assert repo.audits[-1].result == "SUCCESS"


def test_admin_post_same_origin_header_is_allowed() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    )

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.PAUSED


def test_admin_post_same_origin_referer_is_allowed_when_origin_missing() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers={"Referer": "http://testserver/admin?tenant=tn-1"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.PAUSED


def test_admin_post_cross_origin_header_is_denied_and_audited() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers={"Origin": "https://evil.example"},
    )

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.ACTIVE
    assert repo.audits[-1].result == "DENIED"
    assert "ORIGIN_NOT_ALLOWED" in (repo.audits[-1].reason or "")


def test_admin_post_configured_origin_allows_https_proxy_origin() -> None:
    # TLS 종료 proxy 뒤에서는 앱이 내부 http base_url 을 보고, 브라우저 Origin 은 https 일 수 있다.
    repo = InMemoryAdminActionRepository()
    repo.seed_target(_target())
    settings = Settings(
        app_env="test",
        app_version="9.9.9",
        build_sha=None,
        build_time=None,
        admin_allowed_origins=("https://admin.example",),
    )
    app = create_app(
        settings,
        admin_action_service=AdminActionService(repo, InMemoryQueueBackend()),
    )
    app.state.resolve_admin_principal = lambda request: _principal(AdminRole.OPERATOR)

    resp = TestClient(app, base_url="http://admin.example").post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers={"Origin": "https://admin.example"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.PAUSED


def test_admin_post_cross_site_referer_is_denied_when_origin_missing() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers={"Referer": "https://evil.example/admin"},
    )

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.ACTIVE


def test_admin_post_without_origin_or_referer_is_denied_and_audited() -> None:
    # 쿠키/세션 기반 admin 쓰기는 Origin/Referer 가 둘 다 없으면 CSRF 방어를 위해 거부한다.
    app, repo = _app(_principal(AdminRole.OPERATOR))
    resp = TestClient(app).post("/admin/targets/mt-1/pause?tenant=tn-1")

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.ACTIVE
    assert repo.audits[-1].result == "DENIED"
    assert "ORIGIN_NOT_ALLOWED" in (repo.audits[-1].reason or "")


def test_secret_admin_satisfies_operator_gate() -> None:
    # rank 단조 — SECRET_ADMIN(2) 은 OPERATOR(1) 게이트 통과.
    app, repo = _app(_principal(AdminRole.SECRET_ADMIN))
    assert TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    ).status_code == HTTPStatus.OK


def test_viewer_denied_on_action_gate_and_audited() -> None:
    app, repo = _app(_principal(AdminRole.VIEWER))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    )
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    assert repo.audits[-1].result == "DENIED"  # 거부 시도도 남는다
    assert repo.audits[-1].action == "ACCESS_DENIED"
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.ACTIVE  # 전이 0


def test_mfa_unverified_privileged_denied_and_audited() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR, mfa=False))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    )
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert repo.audits[-1].result == "DENIED"


def test_mfa_toggle_off_allows_unverified() -> None:
    # MFA 강제 토글 off → 미검증이어도 통과(task 5.3 토글).
    app, repo = _app(_principal(AdminRole.OPERATOR, mfa=False), mfa_required=False)
    assert TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    ).status_code == HTTPStatus.OK


def test_ip_not_in_allowlist_is_denied_and_audited() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR), ip_allowlist=("10.0.0.0/8",))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers={**_SAME_ORIGIN_HEADERS, "X-Forwarded-For": "192.168.1.1"},
    )
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert repo.audits[-1].result == "DENIED"


def test_ip_in_allowlist_passes() -> None:
    app, repo = _app(_principal(AdminRole.OPERATOR), ip_allowlist=("10.0.0.0/8",))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers={**_SAME_ORIGIN_HEADERS, "X-Forwarded-For": "10.1.2.3"},
    )
    assert resp.status_code == HTTPStatus.OK


def test_break_glass_override_passes_and_strongly_audited() -> None:
    app, repo = _app(_principal(AdminRole.BREAK_GLASS, source="ADMIN_UI/break-glass"))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    )
    assert resp.status_code == HTTPStatus.OK
    # break-glass 사용이 강제 audit(action·result·source).
    bg = [a for a in repo.audits if a.action == "BREAK_GLASS_OVERRIDE"]
    assert bg and bg[-1].result == "SUCCESS" and bg[-1].source == "ADMIN_UI/break-glass"


def test_viewer_can_read_dashboard() -> None:
    # VIEWER 는 읽기 전용 대시보드(GET)는 통과(privileged 아님 — MFA 무관).
    repo = InMemoryAdminActionRepository()
    app = create_app(_FAKE_SETTINGS, admin_action_service=AdminActionService(repo, InMemoryQueueBackend()))
    app.state.resolve_admin_principal = lambda request: _principal(AdminRole.VIEWER, mfa=False)
    assert TestClient(app).get("/admin?tenant=tn-1").status_code == HTTPStatus.OK


# ══════════════════════════════════════════════════════════════════════════
# (3-QA) 보강 — enforce_session(읽기 경로) deny 분기·anti-flooding·source 미상·async seam
# ══════════════════════════════════════════════════════════════════════════
# (qa-generate-e2e 보강: 라우트 게이트 테스트는 happy/role/MFA/IP(XFF) 만 덮었다. 읽기 전용
#  GET 의 enforce_session deny 분기(401/403, write-free), 미인증 POST 가 audit 를 증폭하지
#  않는 anti-flooding 불변식, source 미상(XFF 없음) fail-closed, async principal seam 은
#  always-run 빈틈이었다 — memory pg-gated-files-hide-pure-helpers / admin-routes-wallclock.)


def test_enforce_session_get_no_principal_is_401_and_write_free() -> None:
    # 읽기 전용 대시보드도 fail-closed — principal 미해결이면 401. 읽기 경로라 audit write 0.
    app, repo = _app(None)
    resp = TestClient(app).get("/admin?tenant=tn-1")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED
    assert repo.audits == []  # 읽기 경로는 write-free(게이트레일 #1 — audit-on-deny 없음)


def test_enforce_session_get_ip_not_allowed_is_403_and_write_free() -> None:
    # 읽기 경로 IP allowlist 거부(enforce_session) — VIEWER 라도 source 불허면 403, audit 0.
    app, repo = _app(_principal(AdminRole.VIEWER), ip_allowlist=("10.0.0.0/8",))
    resp = TestClient(app).get(
        "/admin?tenant=tn-1", headers={"X-Forwarded-For": "192.168.1.1"}
    )
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert repo.audits == []  # 읽기 경로 write-free


def test_no_principal_post_does_not_amplify_audit() -> None:
    # anti-flooding(access._audit_denied 주석) — 미인증(principal None) POST 는 401 이되 DENIED
    # audit 를 쓰지 않는다(보안 audit 가치는 인증된 주체의 무권한 시도 추적 — insider).
    app, repo = _app(None)
    resp = TestClient(app).post("/admin/targets/mt-1/pause?tenant=tn-1")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED
    assert repo.audits == []  # 미인증 거부는 audit 증폭 0


def test_allowlist_set_without_source_header_is_failclosed_403() -> None:
    # source 미상(XFF 없음 → client host 가 IP 아님)인데 allowlist 설정 → fail-closed 403 + DENIED.
    app, repo = _app(_principal(AdminRole.OPERATOR), ip_allowlist=("10.0.0.0/8",))
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    )  # XFF 헤더 없음
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert repo.audits[-1].result == "DENIED"  # 인증된 주체의 거부는 남는다


def test_async_resolve_admin_principal_seam_supported() -> None:
    # principal seam 은 sync/async 모두 지원(access.resolve_principal 의 isawaitable 분기).
    app, repo = _app(None)

    async def _async_operator(request):
        return _principal(AdminRole.OPERATOR)

    app.state.resolve_admin_principal = _async_operator
    resp = TestClient(app).post(
        "/admin/targets/mt-1/pause?tenant=tn-1",
        headers=_SAME_ORIGIN_HEADERS,
    )
    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_target("mt-1")).status is MonitoringTargetStatus.PAUSED


# ══════════════════════════════════════════════════════════════════════════
# (4) security 모듈 단방향 import 가드(AST — rider_agent 0)
# ══════════════════════════════════════════════════════════════════════════

def _py_files(pkg_dir: Path) -> list[Path]:
    return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)


def _abs_import_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_security_module_never_imports_rider_agent() -> None:
    files = _py_files(_SECURITY_DIR)
    assert files, "security 패키지에 .py 가 있어야 한다(빈 스캔 = vacuous pass 방지)"
    offenders = [p.name for p in files if "rider_agent" in _abs_import_roots(ast.parse(p.read_text(encoding="utf-8")))]
    assert offenders == [], offenders


def test_security_third_party_imports_within_allowlist() -> None:
    # 단방향 — security 는 fastapi/rider_crawl(redaction 경유 service) + 자기 패키지·stdlib 만.
    allowed = {"fastapi", "starlette", "pydantic", "rider_crawl", "rider_server"}
    stdlib = set(sys.stdlib_module_names)
    for path in _py_files(_SECURITY_DIR):
        roots = _abs_import_roots(ast.parse(path.read_text(encoding="utf-8")))
        third_party = roots - stdlib - allowed
        assert third_party == set(), f"{path.name}: {third_party}"


def test_guard_is_not_vacuous() -> None:
    planted = ast.parse("import rider_agent.job_loop\n")
    assert "rider_agent" in _abs_import_roots(planted)
