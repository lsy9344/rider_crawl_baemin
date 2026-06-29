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
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from rider_server.domain import CustomerLifecycleState, SubscriptionStatus
from rider_server.security import AdminRole, require_role
from rider_server.services.admin_action_service import (
    UNAUTHENTICATED_ACTOR,
    AdminActionNotFound,
)
from rider_server.services.admin_entity_service import is_center_name_risky  # noqa: F401  (재노출/문서용)
from rider_server.services.admin_entity_service import AdminEntityDeleteBlockedError
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


def _channel_test_service(request: Request):
    """채널 전송 테스트 service(0023) — 미구성(무-DB dev 등)이면 None."""
    return getattr(request.app.state, "channel_test_service", None)


def _now() -> datetime:
    """현재 시각(UTC). 라우트는 주입 불가한 실 ``now()`` 를 쓴다(시각 단언은 service 레이어)."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """신규 엔티티 id — client-side ``uuid4``(ORM ``uuid_pk`` default 와 동형, 결정성=호출부 주입)."""
    return str(uuid.uuid4())


def _tenant_id(request: Request) -> str:
    """tenant 선택 seam — ``?tenant=<id>``(5.6/5.7 선례)."""
    return request.query_params.get("tenant", "").strip()


def _missing_tenant_fragment(request: Request) -> HTMLResponse:
    return _fragment(
        request,
        "먼저 고객을 생성하거나 선택하세요. 고객 생성 후 플랫폼 계정을 등록할 수 있습니다.",
        ok=False,
    )


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


def _optional_select_bool(value: str | None) -> bool | None:
    if value is None or not value.strip():
        return None
    return value.strip().lower() in {"true", "on", "1", "yes"}


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _int_or(form: dict, key: str, default: int = 0) -> int:
    try:
        return int(form.get(key, "").strip())
    except (TypeError, ValueError):
        return default


def _customer_status_or_400(raw: str | None) -> CustomerLifecycleState | None:
    if raw is None or not raw.strip():
        return None
    try:
        return CustomerLifecycleState(raw.strip().upper())
    except ValueError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "알 수 없는 고객 상태") from exc


def _subscription_status_or_400(raw: str | None) -> SubscriptionStatus:
    try:
        return SubscriptionStatus((raw or "").strip().upper())
    except ValueError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "알 수 없는 구독 상태") from exc


ENTITY_OPTIONS_CHANGED = "admin-entity-changed"


def _completion_label(message: str, ok: bool) -> str:
    if not ok:
        return message
    if "생성됨" in message:
        return "생성 완료"
    if "편집됨" in message or "저장" in message:
        return "저장 완료"
    if "복구됨" in message:
        return "복구 완료"
    if "비활성화됨" in message:
        return "비활성화 완료"
    return "완료"


def _fragment(
    request: Request,
    message: str,
    *,
    ok: bool = True,
    trigger: str | None = None,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "_action_result.html",
        {"message": message, "display_message": _completion_label(message, ok), "ok": ok},
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
    """메신저 채널 드롭다운 라벨 — messenger + 라우팅 식별자(비밀 아님) + 상태. id 노출 최소."""
    routing = getattr(channel, "telegram_chat_id", None) or getattr(
        channel, "kakao_room_name", None
    )
    state = getattr(channel, "state", None)
    state_str = f" [{state.value}]" if state and state.value != "ACTIVE" else ""
    label = f"{channel.messenger.value} · {routing}" if routing else channel.messenger.value
    return f"{label}{state_str}"


def _rule_label(rule) -> str:
    flags = "변경시만" if rule.send_only_on_change else "항상"
    template = rule.template_id or "기본"
    return f"{template} · {flags}"


def _target_summary(target) -> str:
    if target.schedule_enabled and target.start_time and target.stop_time:
        return f"{target.name} · 전송 {target.start_time}~{target.stop_time}"
    return target.name


def _raise_for(exc: Exception) -> None:
    """service 예외 → HTTP 상태(전역 핸들러가 envelope 로 변환). NotFound/scope→404, ValueError→400."""

    if isinstance(exc, AdminActionNotFound):
        raise HTTPException(HTTPStatus.NOT_FOUND, "대상을 찾을 수 없습니다") from exc
    if isinstance(exc, AdminEntityDuplicateError):
        raise HTTPException(HTTPStatus.CONFLICT, str(exc)) from exc
    if isinstance(exc, AdminEntityDeleteBlockedError):
        raise HTTPException(
            HTTPStatus.CONFLICT, "연결 데이터가 있어 고객을 삭제할 수 없습니다"
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "허용되지 않은 입력입니다") from exc
    raise exc


def _configured_label(value: str | None) -> str:
    """비밀값을 노출하지 않고 설정 여부만 반환한다(값 자체는 절대 반환 금지)."""
    return "설정됨" if (value or "").strip() else "미설정"


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


@router.get("/telegram-settings", response_class=HTMLResponse)
async def list_telegram_settings(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_tenants()
    return templates.TemplateResponse(
        request,
        "_tenant_telegram.html",
        {
            "rows": [
                {
                    "id": t.id,
                    "name": t.name,
                    "bot_token_label": "설정됨" if (t.telegram_bot_token or "").strip() else "미설정",
                    "webhook_secret_label": "설정됨" if (t.telegram_webhook_secret or "").strip() else "미설정",
                    "sending_enabled": t.sending_enabled,
                    # 전송 테스트 게이트(0023): send_test_passed_at 이 있으면 통과 → ON 허용 가능.
                    "send_test_passed": t.send_test_passed_at is not None,
                }
                for t in rows
            ],
        },
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


@router.get("/subscriptions", response_class=HTMLResponse)
async def list_subscriptions(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_subscriptions(_tenant_id(request))
    tenants = {t.id: t.name for t in await _service(request).list_tenants()}
    return _entities(
        request,
        "구독",
        [
            {
                "id": s.id,
                "summary": f"{tenants.get(s.tenant_id, s.tenant_id)} · {s.plan}",
                "state": s.status.value,
            }
            for s in rows
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
        [{"id": t.id, "summary": _target_summary(t), "state": t.status.value} for t in rows],
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


@router.get("/subscriptions/options", response_class=HTMLResponse)
async def subscription_options(
    request: Request, _principal=Depends(require_viewer)
) -> HTMLResponse:
    rows = await _service(request).list_subscriptions(_tenant_id(request))
    return _options(
        request,
        [{"id": s.id, "label": f"{s.plan} · {s.status.value}"} for s in rows],
        placeholder="구독 선택…",
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
        [{
            "id": c.id,
            "label": _channel_label(c),
            "data": {
                "chat": c.telegram_chat_id or "",
                "thread": c.thread_id or "",
                "kakao": c.kakao_room_name or "",
                "messenger": c.messenger.value,
                # 빠른 연결 CTA 가 '활성 채널 1개' 조건을 정확히 보게 채널 상태를 같이 싣는다(전체 채널은
                # 드롭다운에 그대로 두되, ACTIVE 만 자동 선택/CTA 대상으로 센다 — 실 dispatch 와 같은 기준).
                "state": c.state.value,
            },
        } for c in rows],
        placeholder="채널 선택…",
    )


# ══════════════════════════════════════════════════════════════════════════
# GET — edit-state JSON(선택 항목 현재값 로드, operator-only, tenant scope)
# 일반 설정값은 그대로, 비밀값은 설정됨/미설정 라벨만. /options 와 달리 단건·operator 전용.
# ══════════════════════════════════════════════════════════════════════════

@router.get("/monitoring-targets/{target_id}/edit-state")
async def monitoring_target_edit_state(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> JSONResponse:
    try:
        target = await _service(request).get_monitoring_target_for_edit(
            target_id, tenant_id=_tenant_id(request)
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return JSONResponse(
        {
            "id": target.id,
            "name": target.name,
            "center_name": target.center_name,
            "external_id": target.external_id,
            "url": target.url,
            "interval_minutes": target.interval_minutes,
            "schedule_enabled": target.schedule_enabled,
            "start_time": target.start_time,
            "stop_time": target.stop_time,
            "status": target.status.value,
        }
    )


@router.get("/platform-accounts/{account_id}/edit-state")
async def platform_account_edit_state(
    request: Request, account_id: str, _principal=Depends(require_operator)
) -> JSONResponse:
    try:
        account = await _service(request).get_platform_account_for_edit(
            account_id, tenant_id=_tenant_id(request)
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return JSONResponse(
        {
            "id": account.id,
            "platform": account.platform.value,
            "label": account.label,
            "username_label": _configured_label(account.username),
            "password_label": _configured_label(account.password),
            "verification_email_address_label": _configured_label(
                account.verification_email_address
            ),
            "verification_email_app_password_label": _configured_label(
                account.verification_email_app_password
            ),
            "verification_email_subject_keyword": account.verification_email_subject_keyword,
            "verification_email_sender_keyword": account.verification_email_sender_keyword,
            "auth_state": account.auth_state.value,
        }
    )


@router.get("/customers/{tenant_id}/edit-state")
async def customer_edit_state(
    request: Request, tenant_id: str, _principal=Depends(require_operator)
) -> JSONResponse:
    # 고객은 루트 엔티티라 자기 자신이 scope 다. 형제 endpoint 와 일관되게, 활성 tenant(?tenant=)
    # 와 path tenant 가 다르면 cross-tenant 조회로 보고 404(존재 누설 방지). 빈 ?tenant= 는 허용.
    active_tenant = _tenant_id(request)
    if active_tenant and active_tenant != tenant_id:
        raise HTTPException(HTTPStatus.NOT_FOUND, "대상을 찾을 수 없습니다")
    try:
        tenant = await _service(request).get_tenant_for_edit(tenant_id)
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return JSONResponse(
        {
            "id": tenant.id,
            "name": tenant.name,
            "status": tenant.status.value,
            "telegram_bot_token_label": _configured_label(tenant.telegram_bot_token),
            "telegram_webhook_secret_label": _configured_label(
                tenant.telegram_webhook_secret
            ),
            "sending_enabled": tenant.sending_enabled,
            "send_test_passed": tenant.send_test_passed_at is not None,
        }
    )


@router.get("/subscriptions/{subscription_id}/edit-state")
async def subscription_edit_state(
    request: Request, subscription_id: str, _principal=Depends(require_operator)
) -> JSONResponse:
    try:
        subscription = await _service(request).get_subscription_for_edit(
            subscription_id, tenant_id=_tenant_id(request)
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return JSONResponse({"id": subscription.id, "status": subscription.status.value})


@router.get("/delivery-rules/{rule_id}/edit-state")
async def delivery_rule_edit_state(
    request: Request, rule_id: str, _principal=Depends(require_operator)
) -> JSONResponse:
    try:
        rule = await _service(request).get_delivery_rule_for_edit(
            rule_id, tenant_id=_tenant_id(request)
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return JSONResponse(
        {
            "id": rule.id,
            "enabled": rule.enabled,
            "send_only_on_change": rule.send_only_on_change,
        }
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
    response.headers["HX-Redirect"] = (
        f"/admin?tenant={quote(tenant.id, safe='')}&mode=manage#manage"
    )
    return response


@router.post("/customers/{tenant_id}", response_class=HTMLResponse)
async def update_customer(
    request: Request, tenant_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    # tenant 별 텔레그램 설정(0012). 토큰/secret 은 폼에 키가 없으면 None(유지), 있으면 set/clear.
    # crudButton(filledValues) 는 빈 값을 아예 보내지 않으므로 "비우면 유지" 시맨틱이 자연스럽다.
    # sending_enabled 는 명시 토글 — "true"/"false" 문자열로 보낸다(없으면 None=유지).
    try:
        tenant = await _service(request).update_tenant(
            tenant_id,
            name=form.get("name"),
            status=_customer_status_or_400(form.get("status")),
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


@router.post("/customers/{tenant_id}/delete", response_class=HTMLResponse)
async def delete_customer(
    request: Request, tenant_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        tenant = await _service(request).delete_tenant(
            tenant_id,
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request, f"고객 삭제됨 ({tenant.name})", trigger=ENTITY_OPTIONS_CHANGED
    )


# ══════════════════════════════════════════════════════════════════════════
# 구독 Subscription — create/update(status)
# ══════════════════════════════════════════════════════════════════════════

@router.post("/subscriptions", response_class=HTMLResponse)
async def create_subscription(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    tenant_id = (form.get("tenant_id") or _tenant_id(request)).strip()
    if not tenant_id:
        return _missing_tenant_fragment(request)
    try:
        subscription = await _service(request).create_subscription(
            entity_id=_new_id(),
            tenant_id=tenant_id,
            plan=form.get("plan", "basic"),
            status=_subscription_status_or_400(
                form.get("status") or SubscriptionStatus.PAYMENT_ACTIVE.value
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
        f"구독 생성됨 ({subscription.status.value})",
        trigger=ENTITY_OPTIONS_CHANGED,
    )


@router.post("/subscriptions/{subscription_id}", response_class=HTMLResponse)
async def update_subscription(
    request: Request, subscription_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    try:
        subscription = await _service(request).update_subscription(
            subscription_id,
            tenant_id=_tenant_id(request),
            status=_subscription_status_or_400(form.get("status")),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        f"구독 저장됨 ({subscription.status.value})",
        trigger=ENTITY_OPTIONS_CHANGED,
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
    if not _tenant_id(request):
        return _missing_tenant_fragment(request)
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
            schedule_enabled=_bool(form, "schedule_enabled"),
            start_time=form.get("start_time", "").strip(),
            stop_time=form.get("stop_time", "").strip(),
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
            schedule_enabled=_optional_select_bool(form.get("schedule_enabled")),
            start_time=_optional_text(form.get("start_time")),
            stop_time=_optional_text(form.get("stop_time")),
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
# 메시지 채널 MessengerChannel — create(TELEGRAM=PENDING, KAKAO room=ACTIVE)/update/deactivate
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
            telegram_chat_id=form.get("telegram_chat_id", "").strip() or None,
            thread_id=form.get("thread_id", "").strip() or None,
            kakao_room_name=form.get("kakao_room_name", "").strip() or None,
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


@router.post("/messenger-channels/{channel_id}/activate", response_class=HTMLResponse)
async def activate_messenger_channel(
    request: Request, channel_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        channel = await _service(request).activate_messenger_channel_manual(
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
        f"메시지 채널 활성화됨 (상태: {channel.state.value})",
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


# ── 채널 전송 테스트(0023) — 실발송 게이트를 켜기 위한 채널별 전송 검증 ──────────────────────
# 텔레그램은 동기 직접 전송이라 즉시 PASSED/FAILED, 카카오는 KAKAO_SEND 잡 enqueue 후 PENDING →
# 운영자 화면이 status 라우트로 폴링해 에이전트 결과(SUCCEEDED/FAILED)로 자동 판정한다. 테스트가
# 통과하면 tenant.send_test_passed_at 이 스탬프돼 '실제 메시지 보내기' OFF→ON 이 허용된다.

# 전송 테스트 결과 코드 → action-result CSS state class. PASSED=ok, PENDING=warn, FAILED=err.
_TEST_STATE_CLASS = {"PASSED": "ok", "PENDING": "warn", "FAILED": "err"}


def _channel_test_fragment(request: Request, outcome) -> HTMLResponse:
    """전송 테스트 결과 fragment. PASSED 면 게이트 옵션 갱신 위해 entity-changed 를 발화한다."""
    response = templates.TemplateResponse(
        request,
        "_channel_test_result.html",
        {
            "message": outcome.message,
            "state_class": _TEST_STATE_CLASS.get(outcome.result, "warn"),
            "job_id": outcome.job_id or "",
        },
    )
    if outcome.result == "PASSED":
        response.headers["HX-Trigger"] = ENTITY_OPTIONS_CHANGED
    return response


@router.post("/channel-test", response_class=HTMLResponse)
async def run_channel_test(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    """선택 채널로 전송 테스트를 실행한다(텔레그램=동기, 카카오=잡 enqueue)."""
    svc = _channel_test_service(request)
    if svc is None:
        return _fragment(request, "전송 테스트가 구성되지 않았습니다", ok=False)
    form = await _form(request)
    channel_id = form.get("channel_id", "").strip()
    if not channel_id:
        return _fragment(request, "테스트할 채널을 선택하세요", ok=False)
    try:
        outcome = await svc.run_test(
            channel_id,
            tenant_id=_tenant_id(request),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _channel_test_fragment(request, outcome)


@router.post("/channel-test/status", response_class=HTMLResponse)
async def check_channel_test(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    """enqueue 된 카카오 테스트 잡의 상태를 폴링해 결과를 판정한다(SUCCEEDED 면 PASSED 스탬프)."""
    svc = _channel_test_service(request)
    if svc is None:
        return _fragment(request, "전송 테스트가 구성되지 않았습니다", ok=False)
    form = await _form(request)
    job_id = form.get("job_id", "").strip()
    if not job_id:
        return _fragment(request, "확인할 테스트 작업이 없습니다", ok=False)
    try:
        outcome = await svc.check_kakao_test(
            job_id,
            tenant_id=_tenant_id(request),
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _channel_test_fragment(request, outcome)
