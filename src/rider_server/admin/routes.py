"""Admin 대시보드 라우트 + Jinja2/HTMX 서버 렌더 — Story 5.6 (AC1·AC4).

**HTML 응답**(``Jinja2Templates.TemplateResponse``)이라 ``/v1/`` JSON 리소스 규약과 별개다 —
``/admin`` 프리픽스로 둔다(``/v1/`` 가드는 health/version/metrics 만 대상이라 충돌 없음, JSON
snake_case 가드는 JSON 응답에만 적용). 풀 페이지(``GET /admin``)와 HTMX 부분 fragment
(``/admin/targets``·``/admin/agents``·``/admin/channels``·``/admin/auth-required``)를 제공해 별도
JS 빌드 없이(HTMX CDN) 서버 렌더 부분 갱신한다.

**읽기 전용:** 라우트는 주입된 :class:`DashboardRepository` 의 read 메서드와 순수
:class:`DashboardService` 조립만 호출한다 — ``session.commit()``·상태 전이·INSERT/UPDATE 0.
인증은 ``app.state.require_admin_session`` seam 으로 통과한다(5.8 이 MFA/4역할/세션으로 교체;
5.6 기본값은 최소 seam — 5.3 ``resolve_agent_id`` 선례). 템플릿 렌더는 sync(CPU)라 async
핸들러에서 직접 호출 가능하고 blocking I/O 금지 목록(``time.sleep``/subprocess)과 무관하다.

tenant 선택은 5.6 단계에선 ``?tenant=<id>`` 쿼리 seam 으로 둔다 — 5.7/5.8 이 세션 바인딩으로
교체한다(agent fleet 상태는 tenant 무관 전역).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .dashboard_service import DashboardRepository, DashboardService
from .severity import (
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_STOPPED,
    SEVERITY_WARNING,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_service = DashboardService()

# 심각도 코드값 → UI 한글 라벨/CSS class(템플릿 표현 — 어휘 자체는 plain-string 상수).
_SEVERITY_LABELS: dict[str, str] = {
    SEVERITY_NORMAL: "정상",
    SEVERITY_WARNING: "주의",
    SEVERITY_CRITICAL: "위험",
    SEVERITY_STOPPED: "중지",
}
_SEVERITY_CLASSES: dict[str, str] = {
    SEVERITY_NORMAL: "sev-normal",
    SEVERITY_WARNING: "sev-warning",
    SEVERITY_CRITICAL: "sev-critical",
    SEVERITY_STOPPED: "sev-stopped",
}


def _severity_label(code: str) -> str:
    return _SEVERITY_LABELS.get(code, code)


def _severity_class(code: str) -> str:
    return _SEVERITY_CLASSES.get(code, "sev-normal")


templates.env.filters["severity_label"] = _severity_label
templates.env.filters["severity_class"] = _severity_class


# ── 인증 seam(5.8 이 MFA/4역할/세션으로 교체) ───────────────────────────────────────

def _default_require_admin_session(request: Request) -> None:
    """기본 admin 세션 seam(5.6 최소 — full MFA/4역할/audit 는 5.8).

    5.6 단계엔 운영자 인증 인프라가 아직 없어 **seam 만** 둔다(5.3 ``resolve_agent_id`` 가
    full lifecycle 을 5.8 로 미룬 선례와 동일). 기본값은 통과(no-op)이며, 대시보드는 읽기
    전용·secret 미노출이라 비프로덕션에서 안전하다. 운영/테스트는 ``app.state.require_admin_
    session`` 을 실제 강제기(401/403 raise)로 교체해 보호한다.
    """

    return None


async def require_admin_session(request: Request) -> None:
    """주입된 ``app.state.require_admin_session`` seam 을 호출하는 라우트 의존성.

    seam 이 동기/비동기 어느 쪽이든 받아들이고, seam 이 ``HTTPException`` 을 raise 하면 전역
    핸들러가 ``{"error":...}`` envelope 로 변환한다(인증 실패 401/403).
    """

    seam = getattr(request.app.state, "require_admin_session", _default_require_admin_session)
    result = seam(request)
    if inspect.isawaitable(result):
        await result


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    """현재 시각(UTC). 읽기 전용 표시라 라우트가 실시간을 쓴다(jobs.py 선례)."""
    return datetime.now(timezone.utc)


def _repo(request: Request) -> DashboardRepository:
    return request.app.state.dashboard_repository


def _tenant_id(request: Request) -> str:
    """tenant 선택 seam — ``?tenant=<id>``(5.7/5.8 이 세션 바인딩으로 교체)."""
    return request.query_params.get("tenant", "").strip()


# ── 라우트 ───────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin`` — 대시보드 풀 페이지(4개 섹션 + HTMX polling 부착)."""

    now = _now()
    repo = _repo(request)
    tenant_id = _tenant_id(request)
    targets = await _service.target_rows(repo, tenant_id=tenant_id, now=now)
    agents = await _service.agent_rows(repo, now=now)
    channels = await _service.channel_health(repo, tenant_id=tenant_id, now=now)
    auth_required = await _service.auth_required_rows(repo, tenant_id=tenant_id)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "tenant_id": tenant_id,
            "targets": targets,
            "agents": agents,
            "channels": channels,
            "auth_required": auth_required,
        },
    )


@router.get("/targets", response_class=HTMLResponse)
async def targets_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/targets`` — HTMX 부분 fragment(대상 상태 표)."""

    rows = await _service.target_rows(_repo(request), tenant_id=_tenant_id(request), now=_now())
    return templates.TemplateResponse(request, "_targets.html", {"targets": rows})


@router.get("/agents", response_class=HTMLResponse)
async def agents_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/agents`` — HTMX 부분 fragment(Agent fleet 상태)."""

    rows = await _service.agent_rows(_repo(request), now=_now())
    return templates.TemplateResponse(request, "_agents.html", {"agents": rows})


@router.get("/channels", response_class=HTMLResponse)
async def channels_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/channels`` — HTMX 부분 fragment(Kakao lag / Telegram 오류 구분)."""

    health = await _service.channel_health(
        _repo(request), tenant_id=_tenant_id(request), now=_now()
    )
    return templates.TemplateResponse(request, "_channels.html", {"channels": health})


@router.get("/auth-required", response_class=HTMLResponse)
async def auth_required_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/auth-required`` — AC4 인증 필요 대상 필터 fragment."""

    rows = await _service.auth_required_rows(_repo(request), tenant_id=_tenant_id(request))
    return templates.TemplateResponse(request, "_auth_required.html", {"auth_required": rows})
