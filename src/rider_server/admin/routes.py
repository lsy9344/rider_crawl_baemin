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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..security.access import enforce_session
from .dashboard_service import ALL_TENANTS, DashboardRepository, DashboardService
from . import severity as severity_policy
from .severity import (
    SEVERITY_AUTH_REQUIRED,
    SEVERITY_CRITICAL,
    SEVERITY_KAKAO_MISDELIVERY_RISK,
    SEVERITY_NORMAL,
    SEVERITY_OPERATOR_STOPPED,
    SEVERITY_STOPPED,
    SEVERITY_TARGET_VALIDATION_FAILURE,
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
    SEVERITY_AUTH_REQUIRED: "인증 필요",
    SEVERITY_TARGET_VALIDATION_FAILURE: "대상 검증 실패",
    SEVERITY_KAKAO_MISDELIVERY_RISK: "카카오 오발송 위험",
    SEVERITY_OPERATOR_STOPPED: "운영자 중지",
}
_SEVERITY_CLASSES: dict[str, str] = {
    SEVERITY_NORMAL: "sev-normal",
    SEVERITY_WARNING: "sev-warning",
    SEVERITY_CRITICAL: "sev-critical",
    SEVERITY_STOPPED: "sev-stopped",
    SEVERITY_AUTH_REQUIRED: "sev-stopped",
    SEVERITY_TARGET_VALIDATION_FAILURE: "sev-stopped",
    SEVERITY_KAKAO_MISDELIVERY_RISK: "sev-stopped",
    SEVERITY_OPERATOR_STOPPED: "sev-stopped",
}


def _severity_label(code: str) -> str:
    return _SEVERITY_LABELS.get(code, code)


def _severity_class(code: str) -> str:
    return _SEVERITY_CLASSES.get(code, "sev-normal")


templates.env.filters["severity_label"] = _severity_label
templates.env.filters["severity_class"] = _severity_class


# ── 표현 전용 Jinja 필터(재설계) — 기계 코드/절대시각을 사람이 읽는 문장/상대시간으로 ──────
# 모두 순수 표시 변환이다(상태 변경 0, DB 0). FailureCategory/플랫폼 값은 plain-string 으로만
# 비교한다(domain enum import 불필요 — 어휘는 코드값 그대로). 읽기 전용 가드 무관(write 호출 0).
_REASON_TEXT: dict[str, str] = {
    "ACCOUNT_AUTH_REQUIRED": "로그인 만료 · 인증 확인 필요",
    "AUTH_REQUIRED": "로그인 만료 · 인증 확인 필요",
    "TARGET_VALIDATION_FAILURE": "센터/상점명 불일치 — 오발송 위험",
    "CRAWL_FAILURE": "수집 실패 — 확인 필요",
    "RENDER_FAILURE": "메시지 생성 실패",
    "TELEGRAM_FAILURE": "텔레그램 전송 오류",
    "KAKAO_FAILURE": "카카오톡 전송 오류",
    "DUPLICATE_BLOCKED": "중복으로 전송 보류",
}
_PLATFORM_LABELS: dict[str, str] = {"BAEMIN": "배민", "COUPANG": "쿠팡"}
_PLATFORM_CLASSES: dict[str, str] = {"BAEMIN": "plat-baemin", "COUPANG": "plat-coupang"}


def _reason_text(code: str | None) -> str:
    """실패 코드 → 사람이 읽는 사유 문장. 미지 코드는 코드값을 괄호로 보조."""
    if not code:
        return ""
    return _REASON_TEXT.get(code, f"오류 — 확인 필요 ({code})")


def _platform_label(code: str | None) -> str:
    return _PLATFORM_LABELS.get((code or "").upper(), code or "")


def _platform_class(code: str | None) -> str:
    return _PLATFORM_CLASSES.get((code or "").upper(), "")


def _relative_time(value: datetime | None) -> str:
    """datetime → '3분 전' 상대시간(읽기 전용 표시라 실 now 기준 — jobs.py 선례)."""
    if value is None:
        return ""
    try:
        ts = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
    except (TypeError, AttributeError):
        return str(value)
    if delta < 60:
        return "방금"
    if delta < 3600:
        return f"{int(delta // 60)}분 전"
    if delta < 86400:
        return f"{int(delta // 3600)}시간 전"
    return f"{int(delta // 86400)}일 전"


def _freshness_class(value: datetime | None) -> str:
    """상대시간 신선도 색 class — 없음/주의(15분 초과)/위험(1시간 초과)."""
    if value is None:
        return "fresh-none"
    try:
        ts = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
    except (TypeError, AttributeError):
        return ""
    if delta > 3600:
        return "fresh-dead"
    if delta > 900:
        return "fresh-stale"
    return ""


templates.env.filters["reason_text"] = _reason_text
templates.env.filters["platform_label"] = _platform_label
templates.env.filters["platform_class"] = _platform_class
templates.env.filters["relative_time"] = _relative_time
templates.env.filters["freshness_class"] = _freshness_class


# ── 인증 seam(5.8 이 MFA/4역할/세션으로 교체) ───────────────────────────────────────

async def _default_require_admin_session(request: Request) -> None:
    """기본 admin 세션 seam(5.8 — fail-closed VIEWER 게이트).

    5.6 의 permissive no-op 기본을 5.8 이 **deny** 로 바꾼다(게이트레일 #4). 주입된
    ``app.state.resolve_admin_principal`` seam 으로 principal 을 해석해 VIEWER 수준 세션을
    강제한다(principal 미해결 → 401, IP 불허 → 403 — :func:`enforce_session`). 읽기 전용
    대시보드라 MFA·audit-on-deny 는 두지 않는다(게이트레일 #1: 읽기 경로는 write-free).
    운영/테스트는 ``app.state.resolve_admin_principal`` 로 principal 을 주입하거나
    ``app.state.require_admin_session`` 자체를 교체해 통과/거부를 제어한다.
    """

    await enforce_session(request)


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


async def _dashboard_tenants(request: Request):
    service = getattr(request.app.state, "admin_entity_service", None)
    if service is None:
        return []
    try:
        return await service.list_tenants()
    except Exception:
        return []


async def _dashboard_tenant_id(request: Request, *, tenants=None) -> str:
    tenant_id = _tenant_id(request)
    if tenant_id == ALL_TENANTS:
        return ALL_TENANTS
    if tenant_id:
        return tenant_id
    if tenants is None:
        tenants = await _dashboard_tenants(request)
    if not tenants:
        return ""
    active = [t for t in tenants if getattr(t, 'status', '') == 'ACTIVE']
    return (active or tenants)[0].id


async def _target_rows_for_display(
    repo: DashboardRepository, *, tenant_id: str, now: datetime
):
    facts_rows = await repo.target_health(tenant_id=tenant_id, now=now)
    rows = []
    for facts in facts_rows:
        row = _service.target_row(facts, now)
        rows.append(replace(row, severity=_display_severity(row.severity, facts)))
    rows.sort(key=lambda r: severity_policy.severity_rank(r.severity), reverse=True)
    return rows


def _display_severity(code: str, facts) -> str:
    if code != SEVERITY_STOPPED:
        return code
    signals = severity_policy.failclosed_signals_from(
        account_auth_state=facts.account_auth_state,
        lifecycle_state=facts.lifecycle_state,
        latest_failure_code=facts.last_failure_code,
        auth_session_pending=facts.auth_session_pending,
    )
    if signals.auth_required:
        return SEVERITY_AUTH_REQUIRED
    if signals.target_validation_failed:
        return SEVERITY_TARGET_VALIDATION_FAILURE
    if signals.kakao_misdelivery_risk:
        return SEVERITY_KAKAO_MISDELIVERY_RISK
    return SEVERITY_OPERATOR_STOPPED


# ── 라우트 ───────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin`` — 대시보드 풀 페이지(4개 섹션 + HTMX polling 부착)."""

    return await _dashboard_response(request, initial_target_id="")


@router.get("/t/{target_id}", response_class=HTMLResponse)
async def target_deeplink(
    target_id: str,
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/t/{target_id}`` — 특정 업체 drawer 를 여는 딥링크 진입점."""

    return await _dashboard_response(request, initial_target_id=target_id)


async def _dashboard_response(request: Request, *, initial_target_id: str) -> HTMLResponse:
    now = _now()
    repo = _repo(request)
    tenants = await _dashboard_tenants(request)
    tenant_id = await _dashboard_tenant_id(request, tenants=tenants)
    targets = await _target_rows_for_display(repo, tenant_id=tenant_id, now=now)
    agents = await _service.agent_rows(repo, now=now)
    channels = await _service.channel_health(repo, tenant_id=tenant_id, now=now)
    auth_required = await _service.auth_required_rows(repo, tenant_id=tenant_id, now=now)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "tenant_id": tenant_id,
            "tenants": tenants,
            "targets": targets,
            "agents": agents,
            "channels": channels,
            "auth_required": auth_required,
            "initial_target_id": initial_target_id,
            "show_debug_actions": False,
        },
    )


@router.get("/targets", response_class=HTMLResponse)
async def targets_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/targets`` — HTMX 부분 fragment(대상 상태 표)."""

    rows = await _target_rows_for_display(_repo(request), tenant_id=_tenant_id(request), now=_now())
    return templates.TemplateResponse(
        request, "_targets.html", {"targets": rows, "tenant_id": _tenant_id(request)}
    )


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

    rows = await _service.auth_required_rows(_repo(request), tenant_id=_tenant_id(request), now=_now())
    return templates.TemplateResponse(request, "_auth_required.html", {"auth_required": rows})
