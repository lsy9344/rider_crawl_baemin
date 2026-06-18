"""Admin 엔티티 CRUD 라우트(POST 생성/편집/비활성화 + GET 목록/폼 fragment) — Story 5.11 (AC1·AC2·AC4).

5.6 읽기 전용 대시보드(``routes.py``)·5.7 운영 액션(``actions_routes.py``)과 **물리적으로 분리** 한
엔티티 CRUD 라우트다. **라우트는 직접 ORM write/상태 전이를 하지 않는다** — 오직
``app.state.admin_entity_service``(:class:`AdminEntityService`)만 호출한다(architecture
#Service-Boundaries). write·audit 는 service(+repository)가 같은 트랜잭션으로 수행한다.

URL 충돌 회피: 읽기 전용 대시보드가 ``GET /admin/targets``·``/channels``·``/agents``·
``/auth-required`` 를, 5.7 액션이 ``/admin/targets/{id}/...`` 등을 점유한다. 본 모듈은 **복수 명사
리소스 경로**(``/admin/customers``·``/platform-accounts``·``/monitoring-targets``·
``/messenger-channels``·``/delivery-rules``·``/entities``)로 겹치지 않게 둔다.

변경 라우트는 ``require_role(AdminRole.OPERATOR)``(VIEWER 는 읽기 전용), 조회 fragment 는
``require_role(AdminRole.VIEWER)``. 폼은 stdlib ``parse_qs``(python-multipart 미사용 — 7-dep lock),
신규 ``id``/시각은 라우트에서 ``uuid4``/실 ``now()`` 주입(시각 단언은 service/순수 레이어). 예외는
``_raise_for``(NotFound/TenantScopeViolation→404, ValueError→400 — 순서 주의).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from rider_server.security import AdminRole, require_role
from rider_server.services.admin_action_service import (
    UNAUTHENTICATED_ACTOR,
    AdminActionNotFound,
)
from rider_server.services.admin_entity_service import is_center_name_risky  # noqa: F401  (재노출/문서용)
from rider_server.services.admin_entity_service import AdminEntityDuplicateError

router = APIRouter(prefix="/admin", tags=["admin-crud"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── 역할 게이트(5.8) — 변경=OPERATOR↑, 조회=VIEWER↑(fail-closed + audit-on-deny) ──────
require_operator = require_role(AdminRole.OPERATOR)
require_viewer = require_role(AdminRole.VIEWER)


# ── actor/source 해석 seam(5.7/5.8 동형) ─────────────────────────────────────────────

def _resolve_actor(request: Request) -> str:
    seam = getattr(request.app.state, "resolve_admin_actor", None)
    if seam is not None:
        return seam(request)
    principal = getattr(request.state, "admin_principal", None)
    return principal.actor_id if principal is not None else UNAUTHENTICATED_ACTOR


def _resolve_source(request: Request) -> str | None:
    principal = getattr(request.state, "admin_principal", None)
    return principal.source if principal is not None else None


# ── 헬퍼(actions_routes.py 동형) ─────────────────────────────────────────────────────

def _service(request: Request):
    return request.app.state.admin_entity_service


def _now() -> datetime:
    """현재 시각(UTC). 라우트는 주입 불가한 실 ``now()`` 를 쓴다(시각 단언은 service 레이어)."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """신규 엔티티 id — client-side ``uuid4``(ORM ``uuid_pk`` default 와 동형, 결정성=호출부 주입)."""
    return str(uuid.uuid4())


def _tenant_id(request: Request) -> str:
    """tenant 선택 seam — ``?tenant=<id>``(5.6/5.7 선례)."""
    return request.query_params.get("tenant", "").strip()


async def _tenant_rows(request: Request):
    try:
        return await _service(request).list_tenants()
    except Exception:
        return []


async def _form(request: Request) -> dict:
    """urlencoded 폼 본문을 stdlib ``parse_qs`` 로 파싱(HTMX 기본 content-type, multipart 미사용)."""

    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    except (UnicodeDecodeError, ValueError):
        return {}
    return {key: values[-1] for key, values in parsed.items()}


def _bool(form: dict, key: str) -> bool:
    return form.get(key, "").strip().lower() in {"true", "on", "1", "yes"}


def _optional_bool(value: str | None) -> bool | None:
    """명시 3-state 토글 — None(키 없음=유지) / True / False. 편집 폼의 부분 갱신용."""
    if value is None:
        return None
    return value.strip().lower() in {"true", "on", "1", "yes"}


def _int_or(form: dict, key: str, default: int = 0) -> int:
    try:
        return int(form.get(key, "").strip())
    except (TypeError, ValueError):
        return default


ENTITY_OPTIONS_CHANGED = "admin-entity-changed"


def _fragment(
    request: Request,
    message: str,
    *,
    ok: bool = True,
    trigger: str | None = None,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request, "_action_result.html", {"message": message, "ok": ok}
    )
    if trigger:
        response.headers["HX-Trigger"] = trigger
    return response


def _entities(request: Request, title: str, rows: list[dict]) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_entities.html", {"title": title, "rows": rows}
    )


def _options(
    request: Request, options: list[dict], *, placeholder: str | None = None
) -> HTMLResponse:
    """드롭다운 <option> fragment(HTMX 로 <select> 안에 swap) — raw-id 직접 입력 제거용."""
    return templates.TemplateResponse(
        request, "_options.html", {"options": options, "placeholder": placeholder}
    )


def _channel_label(channel) -> str:
    """메신저 채널 드롭다운 라벨 — messenger + 라우팅 식별자(비밀 아님). id 노출 최소."""
    routing = getattr(channel, "telegram_chat_id", None) or getattr(
        channel, "kakao_room_name", None
    )
    return f"{channel.messenger.value} · {routing}" if routing else channel.messenger.value


def _rule_label(rule) -> str:
    flags = "변경시만" if rule.send_only_on_change else "항상"
    template = rule.template_id or "기본"
    return f"{template} · {flags}"


def _raise_for(exc: Exception) -> None:
    """service 예외 → HTTP 상태(전역 핸들러가 envelope 로 변환). NotFound/scope→404, ValueError→400."""

    if isinstance(exc, AdminActionNotFound):
        raise HTTPException(HTTPStatus.NOT_FOUND, "대상을 찾을 수 없습니다") from exc
    if isinstance(exc, AdminEntityDuplicateError):
        raise HTTPException(HTTPStatus.CONFLICT, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "허용되지 않은 입력입니다") from exc
    raise exc


# ══════════════════════════════════════════════════════════════════════════
# GET — 엔티티 관리 폼 + 목록 fragment(조회, AC1)
# ══════════════════════════════════════════════════════════════════════════

@router.get("/entities", response_class=HTMLResponse)
async def entities_admin(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    """``GET /admin/entities`` — 엔티티 생성/편집 폼 섹션(정적 폼 + 목록 polling 컨테이너)."""

    return templates.TemplateResponse(
        request,
        "_entity_admin.html",
        {"tenant_id": _tenant_id(request), "tenants": await _tenant_rows(request)},
    )


@router.get("/customers", response_class=HTMLResponse)
async def list_customers(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_tenants()
    return _entities(
        request,
        "고객(Tenant)",
        [{"id": t.id, "summary": t.name, "state": t.status.value} for t in rows],
    )


@router.get("/platform-accounts", response_class=HTMLResponse)
async def list_platform_accounts(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_platform_accounts(_tenant_id(request))
    return _entities(
        request,
        "플랫폼 계정",
        [
            {"id": a.id, "summary": f"{a.platform.value} · {a.label}", "state": a.auth_state.value}
            for a in rows
        ],
    )


@router.get("/monitoring-targets", response_class=HTMLResponse)
async def list_monitoring_targets(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_monitoring_targets(_tenant_id(request))
    return _entities(
        request,
        "모니터링 대상",
        [{"id": t.id, "summary": t.name, "state": t.status.value} for t in rows],
    )


@router.get("/messenger-channels", response_class=HTMLResponse)
async def list_messenger_channels(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_messenger_channels(_tenant_id(request))
    return _entities(
        request,
        "메시지 채널",
        [{"id": c.id, "summary": c.messenger.value, "state": c.state.value} for c in rows],
    )


@router.get("/delivery-rules", response_class=HTMLResponse)
async def list_delivery_rules(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    target_id = request.query_params.get("target_id", "").strip()
    rows = (
        await _service(request).list_delivery_rules(
            target_id, tenant_id=_tenant_id(request)
        )
        if target_id
        else []
    )
    return _entities(
        request,
        "전송 규칙",
        [
            {
                "id": r.id,
                "summary": f"전송 규칙 · {'변경시만' if r.send_only_on_change else '항상'}",
                "state": "ENABLED" if r.enabled else "DISABLED",
            }
            for r in rows
        ],
    )


@router.get("/delivery-rules/options", response_class=HTMLResponse)
async def delivery_rule_options(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    target_id = request.query_params.get("target_id", "").strip()
    rows = (
        await _service(request).list_delivery_rules(
            target_id, tenant_id=_tenant_id(request)
        )
        if target_id
        else []
    )
    return _options(
        request,
        [{"id": r.id, "label": _rule_label(r)} for r in rows],
        placeholder="규칙 선택…",
    )


# ══════════════════════════════════════════════════════════════════════════
# GET — 드롭다운 <option> fragment(연결 선택용 — raw-id 직접 입력 제거, AC: 비개발 운영자)
# ══════════════════════════════════════════════════════════════════════════

@router.get("/customers/options", response_class=HTMLResponse)
async def customer_options(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_tenants()
    return _options(
        request, [{"id": t.id, "label": t.name} for t in rows], placeholder="고객 선택…"
    )


@router.get("/platform-accounts/options", response_class=HTMLResponse)
async def platform_account_options(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_platform_accounts(_tenant_id(request))
    return _options(
        request,
        [{"id": a.id, "label": f"{a.platform.value} · {a.label}"} for a in rows],
        placeholder="계정 선택…",
    )


@router.get("/monitoring-targets/options", response_class=HTMLResponse)
async def monitoring_target_options(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_monitoring_targets(_tenant_id(request))
    return _options(
        request, [{"id": t.id, "label": t.name} for t in rows], placeholder="업체 선택…"
    )


@router.get("/messenger-channels/options", response_class=HTMLResponse)
async def messenger_channel_options(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_messenger_channels(_tenant_id(request))
    return _options(
        request,
        [{"id": c.id, "label": _channel_label(c)} for c in rows],
        placeholder="채널 선택…",
    )


# ══════════════════════════════════════════════════════════════════════════
# 고객 Tenant — create/update
# ══════════════════════════════════════════════════════════════════════════

@router.post("/customers", response_class=HTMLResponse)
async def create_customer(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    try:
        tenant = await _service(request).create_tenant(
            entity_id=_new_id(),
            name=form.get("name", "").strip(),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    response = _fragment(request, f"고객 생성됨 ({tenant.name})", trigger=ENTITY_OPTIONS_CHANGED)
    response.headers["HX-Redirect"] = f"/admin?tenant={tenant.id}"
    return response


@router.post("/customers/{tenant_id}", response_class=HTMLResponse)
async def update_customer(
    request: Request, tenant_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    # tenant 별 텔레그램 설정(0012). 토큰/secret 은 폼에 키가 없으면 None(유지), 있으면 set/clear.
    # crudUpdate(filledValues) 는 빈 값을 아예 보내지 않으므로 "비우면 유지" 시맨틱이 자연스럽다.
    # sending_enabled 는 명시 토글 — "true"/"false" 문자열로 보낸다(없으면 None=유지).
    try:
        tenant = await _service(request).update_tenant(
            tenant_id,
            name=form.get("name"),
            telegram_bot_token=form.get("telegram_bot_token"),
            telegram_webhook_secret=form.get("telegram_webhook_secret"),
            sending_enabled=_optional_bool(form.get("sending_enabled")),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request, f"고객 편집됨 ({tenant.name})", trigger=ENTITY_OPTIONS_CHANGED
    )


# ══════════════════════════════════════════════════════════════════════════
# 플랫폼 계정 PlatformAccount — create/update(secret=*_ref 핸들만, AC3)
# ══════════════════════════════════════════════════════════════════════════

def _platform_or_400(raw: str):
    from rider_server.domain import Platform

    try:
        return Platform(raw.strip().upper())
    except ValueError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "알 수 없는 플랫폼") from exc


@router.post("/platform-accounts", response_class=HTMLResponse)
async def create_platform_account(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    platform = _platform_or_400(form.get("platform", ""))
    try:
        account = await _service(request).create_platform_account(
            entity_id=_new_id(),
            tenant_id=_tenant_id(request),
            platform=platform,
            label=form.get("label", "").strip(),
            username=form.get("username", ""),
            password=form.get("password", ""),
            at=_now(),
            actor_id=_resolve_actor(request),
            verification_email_address=form.get("verification_email_address", ""),
            verification_email_app_password=form.get("verification_email_app_password", ""),
            verification_email_subject_keyword=form.get("verification_email_subject_keyword", ""),
            verification_email_sender_keyword=form.get("verification_email_sender_keyword", ""),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request, f"플랫폼 계정 생성됨 ({account.label})", trigger=ENTITY_OPTIONS_CHANGED
    )


@router.post("/platform-accounts/{account_id}", response_class=HTMLResponse)
async def update_platform_account(
    request: Request, account_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    try:
        account = await _service(request).update_platform_account(
            account_id,
            tenant_id=_tenant_id(request),
            label=form.get("label"),
            username=form.get("username"),
            password=form.get("password"),
            at=_now(),
            actor_id=_resolve_actor(request),
            verification_email_address=form.get("verification_email_address"),
            verification_email_app_password=form.get("verification_email_app_password"),
            verification_email_subject_keyword=form.get("verification_email_subject_keyword"),
            verification_email_sender_keyword=form.get("verification_email_sender_keyword"),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request, f"플랫폼 계정 편집됨 ({account.label})", trigger=ENTITY_OPTIONS_CHANGED
    )


# ══════════════════════════════════════════════════════════════════════════
# 모니터링 대상 MonitoringTarget — create/update/deactivate(soft delete)
# ══════════════════════════════════════════════════════════════════════════

def _target_message(prefix: str, result) -> str:
    msg = f"{prefix} ({result.target.name})"
    if result.center_name_risky:
        msg += " — ⚠️ 쿠팡 기대 센터/상점명(center_name)이 비었거나 배민 기본값입니다(오발송 위험 경고)"
    return msg


@router.post("/monitoring-targets", response_class=HTMLResponse)
async def create_monitoring_target(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    try:
        result = await _service(request).create_monitoring_target(
            entity_id=_new_id(),
            tenant_id=_tenant_id(request),
            platform_account_id=form.get("platform_account_id", "").strip(),
            name=form.get("name", "").strip(),
            center_name=form.get("center_name", "").strip(),
            external_id=form.get("external_id", "").strip(),
            url=form.get("url", "").strip(),
            interval_minutes=_int_or(form, "interval_minutes", 0),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        _target_message("모니터링 대상 생성됨", result),
        ok=not result.center_name_risky,
        trigger=ENTITY_OPTIONS_CHANGED,
    )


@router.post("/monitoring-targets/{target_id}", response_class=HTMLResponse)
async def update_monitoring_target(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    try:
        result = await _service(request).update_monitoring_target(
            target_id,
            tenant_id=_tenant_id(request),
            name=form.get("name"),
            center_name=form.get("center_name"),
            external_id=form.get("external_id"),
            url=form.get("url"),
            interval_minutes=(
                _int_or(form, "interval_minutes") if form.get("interval_minutes") else None
            ),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        _target_message("모니터링 대상 편집됨", result),
        ok=not result.center_name_risky,
        trigger=ENTITY_OPTIONS_CHANGED,
    )


@router.post("/monitoring-targets/{target_id}/deactivate", response_class=HTMLResponse)
async def deactivate_monitoring_target(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        target = await _service(request).deactivate_monitoring_target(
            target_id,
            tenant_id=_tenant_id(request),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"모니터링 대상 비활성화됨 (상태: {target.status.value})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )


@router.post("/monitoring-targets/{target_id}/reactivate", response_class=HTMLResponse)
async def reactivate_monitoring_target(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        target = await _service(request).reactivate_monitoring_target(
            target_id,
            tenant_id=_tenant_id(request),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"모니터링 대상 복구됨 (상태: {target.status.value})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 메시지 채널 MessengerChannel — create(PENDING)/update(라우팅)/deactivate
# ══════════════════════════════════════════════════════════════════════════

def _messenger_or_400(raw: str):
    from rider_server.domain import Messenger

    try:
        return Messenger(raw.strip().upper())
    except ValueError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "알 수 없는 메신저") from exc


@router.post("/messenger-channels", response_class=HTMLResponse)
async def create_messenger_channel(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    messenger = _messenger_or_400(form.get("messenger", ""))
    try:
        channel = await _service(request).create_messenger_channel(
            entity_id=_new_id(),
            tenant_id=_tenant_id(request),
            messenger=messenger,
            telegram_chat_id=form.get("telegram_chat_id", "").strip() or None,
            thread_id=form.get("thread_id", "").strip() or None,
            kakao_room_name=form.get("kakao_room_name", "").strip() or None,
            registration_code=form.get("registration_code", "").strip() or None,
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"메시지 채널 생성됨 (상태: {channel.state.value})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )


@router.post("/messenger-channels/{channel_id}", response_class=HTMLResponse)
async def update_messenger_channel(
    request: Request, channel_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    try:
        channel = await _service(request).update_messenger_channel(
            channel_id,
            tenant_id=_tenant_id(request),
            telegram_chat_id=form.get("telegram_chat_id"),
            thread_id=form.get("thread_id"),
            kakao_room_name=form.get("kakao_room_name"),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"메시지 채널 라우팅 편집됨 ({channel.messenger.value})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )


@router.post("/messenger-channels/{channel_id}/deactivate", response_class=HTMLResponse)
async def deactivate_messenger_channel(
    request: Request, channel_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        channel = await _service(request).deactivate_messenger_channel(
            channel_id,
            tenant_id=_tenant_id(request),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"메시지 채널 비활성화됨 (상태: {channel.state.value})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 전송 규칙 DeliveryRule — create(1:N fan-out)/update/deactivate
# ══════════════════════════════════════════════════════════════════════════

@router.post("/delivery-rules", response_class=HTMLResponse)
async def create_delivery_rule(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    target_id = form.get("target_id", "").strip()
    channel_id = form.get("channel_id", "").strip()
    if not target_id or not channel_id:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "target_id 와 channel_id 가 필요합니다")
    try:
        rule = await _service(request).create_delivery_rule(
            entity_id=_new_id(),
            tenant_id=_tenant_id(request),
            target_id=target_id,
            channel_id=channel_id,
            template_id=form.get("template_id", "").strip(),
            send_only_on_change=_bool(form, "send_only_on_change"),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"전송 규칙 생성됨 ({'활성' if rule.enabled else '비활성'})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )


@router.post("/delivery-rules/{rule_id}", response_class=HTMLResponse)
async def update_delivery_rule(
    request: Request, rule_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    try:
        rule = await _service(request).update_delivery_rule(
            rule_id,
            tenant_id=_tenant_id(request),
            template_id=form.get("template_id"),
            send_only_on_change=(
                _bool(form, "send_only_on_change") if "send_only_on_change" in form else None
            ),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, "전송 규칙 편집됨", trigger=ENTITY_OPTIONS_CHANGED)


@router.post("/delivery-rules/{rule_id}/deactivate", response_class=HTMLResponse)
async def deactivate_delivery_rule(
    request: Request, rule_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        rule = await _service(request).deactivate_delivery_rule(
            rule_id,
            tenant_id=_tenant_id(request),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"전송 규칙 비활성화됨 (enabled={rule.enabled})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )
