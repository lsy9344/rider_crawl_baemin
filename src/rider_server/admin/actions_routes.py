"""Admin 수동 운영 액션 라우트(POST, HTMX fragment) — Story 5.7 (AC1·AC2·AC3).

5.6 **읽기 전용** 대시보드(``routes.py``)와 **물리적으로 분리** 한 쓰기 라우트다 — 이렇게 두면
5.6 의 "대시보드=읽기 전용" 불변식(``test_admin_readonly_guard``)을 유지하면서 5.7 쓰기를 얹을
수 있다(가드 scope 는 읽기 전용 파일 화이트리스트로 좁혀짐, 본 모듈은 별도 가드 대상).

**라우트는 직접 ORM write/상태 전이를 하지 않는다** — 오직 ``app.state.admin_action_service``
(:class:`AdminActionService`) 만 호출한다(architecture #Service-Boundaries). write·전이·audit 는
service(+repository)가 같은 트랜잭션으로 수행한다. 라우트는 (1) ``require_admin_session`` 통과,
(2) actor 식별자 해석(``resolve_admin_actor`` seam — 미인증이면 sentinel), (3) tenant scope
입력, (4) service 호출, (5) 갱신된 HTMX fragment 반환만 한다.

에러: service 의 ``ValueError``/:class:`InvalidJobTransition`(잘못된 dispose/retry) → 400,
:class:`AdminActionNotFound`/:class:`TenantScopeViolation`(미존재/cross-tenant) → 404 로
``HTTPException`` 변환 → 전역 핸들러가 ``{"error":{"code","message_redacted"}}`` envelope 로 통일.
fail-closed: 모호하면 실행 거부.
"""

from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from rider_server.queue.states import (
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
)
from rider_server.security import AdminRole, require_role
from rider_server.services.admin_action_service import (
    ACTION_TEST_SEND,
    TARGET_TYPE_TARGET,
    UNAUTHENTICATED_ACTOR,
    AdminActionNotFound,
)
from rider_server.services.recovery import effective_send_enabled
from rider_server.services.subscription_gate import HeldDisposition, SubscriptionStatus

router = APIRouter(prefix="/admin", tags=["admin-actions"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── 역할 게이트(5.8) — 운영 액션=OPERATOR↑, secret/token=SECRET_ADMIN↑(Task 4) ──────
# 게이트는 security 레이어에서 강제한다(fail-closed + audit-on-deny). 라우트는 직접 ORM write
# 0·service 위임만(test_admin_actions_guard). 통과 principal 은 request.state 에 저장된다.
require_operator = require_role(AdminRole.OPERATOR)
require_secret_admin = require_role(AdminRole.SECRET_ADMIN)


# ── actor/source 해석 seam(5.8: principal 에서 도출) ─────────────────────────────────

def _default_resolve_admin_actor(request: Request) -> str:
    """기본 actor seam(5.8) — 역할 게이트가 ``request.state.admin_principal`` 에 둔 principal 의
    실 actor 식별자(UUID)를 audit 에 기록한다. principal 미해결이면 명시적 sentinel(미인증 추적).
    """

    principal = getattr(request.state, "admin_principal", None)
    if principal is not None:
        return principal.actor_id
    return UNAUTHENTICATED_ACTOR


def _resolve_actor(request: Request) -> str:
    seam = getattr(request.app.state, "resolve_admin_actor", _default_resolve_admin_actor)
    return seam(request)


def _resolve_source(request: Request) -> str | None:
    """audit ``source`` — principal 출처(역할/IP 라벨). 미해결이면 None(seam 거부 경로)."""

    principal = getattr(request.state, "admin_principal", None)
    return principal.source if principal is not None else None


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────────

def _service(request: Request):
    return request.app.state.admin_action_service


def _token_service(request: Request):
    """server-side token revoke/rotate service(``AgentTokenService``) — SECRET_ADMIN 게이트 뒤."""
    return request.app.state.agent_token_service


def _now() -> datetime:
    """현재 시각(UTC). 라우트는 주입 불가한 실 ``now()`` 를 쓴다(시각 단언은 service 레이어)."""
    return datetime.now(timezone.utc)


def _tenant_id(request: Request) -> str:
    """tenant 선택 seam — ``?tenant=<id>``(5.8 이 세션 바인딩으로 교체, 5.6 선례)."""
    return request.query_params.get("tenant", "").strip()


async def _form(request: Request) -> dict:
    """urlencoded 폼 본문을 stdlib 로 파싱해 dict 로 읽는다(HTMX 기본 content-type).

    Starlette ``request.form()`` 은 urlencoded 에도 ``python-multipart`` 를 요구하지만, 본
    프로젝트는 신규 deps 0(9-dep lock·server extra 고정)이라 ``urllib.parse.parse_qs``(stdlib)
    로 직접 파싱한다 — ``application/x-www-form-urlencoded`` 만 다룬다(multipart 미사용). 같은 키
    중복 시 마지막 값을 취한다. 본문 없음/디코드 실패는 빈 dict(값 검증은 각 라우트가 한다).
    """

    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    except (UnicodeDecodeError, ValueError):
        return {}
    return {key: values[-1] for key, values in parsed.items()}


def _fragment(request: Request, message: str, *, ok: bool = True) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_action_result.html", {"message": message, "ok": ok}
    )


def _raise_for(exc: Exception) -> None:
    """service 예외를 HTTP 상태로 매핑한다(전역 핸들러가 envelope 로 변환).

    순서 주의: :class:`AdminActionNotFound`(``LookupError``)를 먼저, 그다음 ``ValueError``
    (:class:`InvalidJobTransition`/게이트 거부 포함). cross-tenant 는 not-found 동급 404.
    """

    if isinstance(exc, AdminActionNotFound):
        raise HTTPException(HTTPStatus.NOT_FOUND, "대상을 찾을 수 없습니다") from exc
    if isinstance(exc, ValueError):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "허용되지 않은 액션입니다") from exc
    raise exc


# ── 대상 활성/비활성(AC1) ─────────────────────────────────────────────────────────

@router.post("/targets/{target_id}/activate", response_class=HTMLResponse)
async def activate_target(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        target = await _service(request).set_target_status(
            target_id,
            active=True,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"대상 활성화됨 (상태: {target.status.value})")


@router.post("/targets/{target_id}/pause", response_class=HTMLResponse)
async def pause_target(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        target = await _service(request).set_target_status(
            target_id,
            active=False,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"대상 비활성화됨 (상태: {target.status.value})")


# ── test crawl / auth-check / dry-run(AC1) ───────────────────────────────────────

@router.post("/targets/{target_id}/test-crawl", response_class=HTMLResponse)
async def test_crawl(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    platform = (await _form(request)).get("platform", "BAEMIN").strip().upper()
    job_type = JOB_TYPE_CRAWL_COUPANG if platform == "COUPANG" else JOB_TYPE_CRAWL_BAEMIN
    try:
        job_id = await _service(request).test_crawl(
            target_id=target_id,
            job_type=job_type,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"test crawl enqueue됨 (job {job_id})")


@router.post("/targets/{target_id}/auth-check", response_class=HTMLResponse)
async def auth_check(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    try:
        job_id = await _service(request).auth_check(
            target_id=target_id,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"인증 확인(AUTH_CHECK) 트리거됨 (job {job_id})")


@router.post("/targets/{target_id}/dry-run", response_class=HTMLResponse)
async def dry_run(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    """dry-run render — 실발송 없이 렌더 결과만(FR-3). 렌더 소스는 ``admin_render_preview`` seam.

    스냅샷↔렌더 영속 연결은 Epic 5 reconcile 이라, 기본 seam 은 안내 문구를 반환한다(미발송
    불변식은 service 가 구조적으로 보장 — send/queue 미호출).
    """

    seam = getattr(
        request.app.state,
        "admin_render_preview",
        lambda target_id: "(미리보기 없음 — 스냅샷 렌더 연결은 Epic 5)",
    )
    try:
        text = await _service(request).dry_run_render(
            lambda: seam(target_id),
            target_id=target_id,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"dry-run 렌더 결과(미발송): {text}")


@router.post("/targets/{target_id}/test-send", response_class=HTMLResponse)
async def test_send(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    """test send — 운영자 지정 **단일 테스트 채널** 로만(실 고객 fan-out 0, dedup 우회 0).

    실 DispatchJob/렌더/``reserve`` 영속 배선은 Epic 5 reconcile 이므로, ``admin_test_send`` seam
    이 (단일 job + reserve/send seam) 을 구성해 ``service.test_send`` 를 호출한다. seam 미설정이면
    fail-closed 로 거부한다(모호하면 미발송). dedup/단일채널 불변식은 service 가 보장(우회 금지).
    """

    seam = getattr(request.app.state, "admin_test_send", None)
    if seam is None:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "test send 채널이 설정되지 않았습니다(fail-closed)"
        )
    channel_id = (await _form(request)).get("channel_id", "").strip()
    if not channel_id:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "테스트 채널 channel_id 가 필요합니다")
    # 전역 dispatch kill switch(5.10/AC3): 복구/신규 환경(``sending_enabled`` 기본 OFF)에서는
    # seam(실 ``send``) 을 **호출하기 전에** 차단한다(fail-closed — seam 이 게이트를 잊고 우회하지
    # 못하게 라우트가 1차 게이트). service.test_send 도 같은 게이트를 갖지만(직접 호출자/미래
    # seam 방어), 여기서 우회 불가를 보장한다. 차단 시도도 ``result=DENIED`` audit(5.8 선례).
    # NOTE: enqueue-only 액션(test-crawl/auth-check/retry)·구조적 미발송 dry-run 은 실 ``send`` 를
    #       호출하지 않으므로 게이트 대상이 아니다(Task 1.3). 미래 중앙 dispatch 루프 도입 시 그
    #       실 ``send`` 호출부에 동일 ``effective_send_enabled`` 게이트를 compose해야 한다.
    sending_enabled = getattr(request.app.state, "sending_enabled", False)
    if not effective_send_enabled(send_enabled=True, sending_enabled=sending_enabled):
        await _service(request).record_denied(
            actor_id=_resolve_actor(request),
            action=ACTION_TEST_SEND,
            source=_resolve_source(request),
            reason="전역 발송 비활성(sending_enabled=False) — test send 차단",
            at=_now(),
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
        )
        return _fragment(
            request,
            "전역 발송 차단(sending_enabled=False) — test send 미발송(fail-closed)",
            ok=False,
        )
    try:
        result = await seam(
            _service(request),
            target_id=target_id,
            channel_id=channel_id,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"test send 결과(단일 채널): {result.status.value}")


# ── job retry(AC1) ───────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/retry", response_class=HTMLResponse)
async def retry_job(
    request: Request, job_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        status = await _service(request).retry_job(
            job_id,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=reason,
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"job 재시도 재진입됨 (상태: {status})")


# ── Agent 배정(AC1) ──────────────────────────────────────────────────────────────

@router.post("/agents/assign", response_class=HTMLResponse)
async def assign_agent(
    request: Request, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    target_id = form.get("target_id", "").strip()
    agent_id = form.get("agent_id", "").strip()
    if not target_id or not agent_id:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "target_id 와 agent_id 가 필요합니다")
    try:
        await _service(request).assign_agent(
            target_id=target_id,
            agent_id=agent_id,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, "Agent 배정됨")


# ── 구독 중지/복구(AC2) ──────────────────────────────────────────────────────────

@router.post("/subscriptions/{subscription_id}/suspend", response_class=HTMLResponse)
async def suspend_subscription(
    request: Request, subscription_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    reason = (await _form(request)).get("reason", "")
    try:
        sub = await _service(request).suspend_subscription(
            subscription_id,
            reason=reason,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"구독 중지됨 (상태: {sub.status.value})")


@router.post("/subscriptions/{subscription_id}/resume", response_class=HTMLResponse)
async def resume_subscription(
    request: Request, subscription_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    reason = form.get("reason", "")
    to_status_raw = form.get("to_status", "").strip()
    kwargs = {}
    if to_status_raw:
        try:
            kwargs["to_status"] = SubscriptionStatus(to_status_raw)
        except ValueError as exc:
            raise HTTPException(HTTPStatus.BAD_REQUEST, "알 수 없는 구독 상태") from exc
    try:
        sub = await _service(request).resume_subscription(
            subscription_id,
            reason=reason,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            at=_now(),
            **kwargs,
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(
        request,
        "구독 복구됨 (상태: "
        + sub.status.value
        + "). HELD Dispatch 는 자동 발송되지 않습니다 — 별도 폐기/재개 필요.",
    )


# ── HELD Dispatch 폐기/재개(AC2) ─────────────────────────────────────────────────

@router.post("/dispatch/{dispatch_id}/dispose", response_class=HTMLResponse)
async def dispose_held_dispatch(
    request: Request, dispatch_id: str, _principal=Depends(require_operator)
) -> HTMLResponse:
    form = await _form(request)
    disposition_raw = form.get("disposition", "").strip().upper()
    try:
        disposition = HeldDisposition(disposition_raw)
    except ValueError as exc:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "disposition 은 DISCARD 또는 RESUME"
        ) from exc
    try:
        new_status = await _service(request).dispose_held_dispatch(
            dispatch_id,
            disposition,
            tenant_id=_tenant_id(request),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
            at=_now(),
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return _fragment(request, f"HELD Dispatch 처리됨 (상태: {new_status})")


# ── token revoke/rotate(AC3) — SECRET_ADMIN↑ 게이트 ──────────────────────────────

@router.post("/agents/{agent_id}/token/revoke", response_class=HTMLResponse)
async def revoke_agent_token(
    request: Request, agent_id: str, _principal=Depends(require_secret_admin)
) -> HTMLResponse:
    """``POST /admin/agents/{id}/token/revoke`` — Agent token server-side revoke(이후 401).

    revoke 가 반영되면 같은 bearer 의 claim/heartbeat/complete 가 401 이 된다(resolver→None).
    write+audit 는 ``AgentTokenService`` 가 동일 트랜잭션으로 수행한다(라우트 직접 write 0).
    """

    reason = (await _form(request)).get("reason", "")
    await _token_service(request).revoke(
        agent_id,
        at=_now(),
        actor_id=_resolve_actor(request),
        source=_resolve_source(request),
        reason=reason,
    )
    return _fragment(request, f"Agent token revoke됨 (agent {agent_id})")


@router.post("/agents/{agent_id}/token/rotate", response_class=HTMLResponse)
async def rotate_agent_token(
    request: Request, agent_id: str, _principal=Depends(require_secret_admin)
) -> HTMLResponse:
    """``POST /admin/agents/{id}/token/rotate`` — 기존 token 무효화 + 재발급 경로 개방(audit)."""

    reason = (await _form(request)).get("reason", "")
    await _token_service(request).rotate(
        agent_id,
        at=_now(),
        actor_id=_resolve_actor(request),
        source=_resolve_source(request),
        reason=reason,
    )
    return _fragment(request, f"Agent token rotate됨 (agent {agent_id})")


@router.post("/channels/{channel_id}/token/rotate", response_class=HTMLResponse)
async def rotate_channel_token(
    request: Request, channel_id: str, _principal=Depends(require_secret_admin)
) -> HTMLResponse:
    """``POST /admin/channels/{id}/token/rotate`` — 외부 service token ``*_ref`` 회전(평문 DB 0).

    ``new_secret_ref`` 는 ``*_ref`` 핸들이어야 한다 — 평문 secret 이면 fail-closed 400(평문을
    응답/로그/audit 에 싣지 않는다). 실제 Secrets Manager 호출은 배포 인프라(runbook 절차).
    """

    form = await _form(request)
    new_secret_ref = form.get("new_secret_ref", "").strip()
    if not new_secret_ref:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "new_secret_ref(핸들)가 필요합니다")
    try:
        await _token_service(request).rotate_external_token(
            channel_id=channel_id,
            new_secret_ref=new_secret_ref,
            at=_now(),
            actor_id=_resolve_actor(request),
            source=_resolve_source(request),
            reason=form.get("reason", ""),
        )
    except ValueError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "평문 secret 금지 — *_ref 핸들만") from exc
    return _fragment(request, f"채널 token ref 회전됨 (channel {channel_id})")
