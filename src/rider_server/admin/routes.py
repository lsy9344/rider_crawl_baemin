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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..domain import MessengerChannelState, MonitoringTargetStatus
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
DEFAULT_TARGET_FRAGMENT_LIMIT = 100
MAX_TARGET_FRAGMENT_LIMIT = 300

# 심각도 코드값 → UI 한글 라벨/CSS class(템플릿 표현 — 어휘 자체는 plain-string 상수).
_SEVERITY_LABELS: dict[str, str] = {
    SEVERITY_NORMAL: "정상",
    SEVERITY_WARNING: "주의",
    SEVERITY_CRITICAL: "위험",
    SEVERITY_STOPPED: "중지",
    SEVERITY_AUTH_REQUIRED: "인증 필요",
    SEVERITY_TARGET_VALIDATION_FAILURE: "대상 검증 실패",
    SEVERITY_KAKAO_MISDELIVERY_RISK: "위험",
    SEVERITY_OPERATOR_STOPPED: "운영자 중지",
}
_SEVERITY_CLASSES: dict[str, str] = {
    SEVERITY_NORMAL: "sev-normal",
    SEVERITY_WARNING: "sev-warning",
    SEVERITY_CRITICAL: "sev-critical",
    SEVERITY_STOPPED: "sev-stopped",
    SEVERITY_AUTH_REQUIRED: "sev-stopped",
    SEVERITY_TARGET_VALIDATION_FAILURE: "sev-stopped",
    SEVERITY_KAKAO_MISDELIVERY_RISK: "sev-critical",
    SEVERITY_OPERATOR_STOPPED: "sev-stopped",
}


def _severity_label(code: str) -> str:
    return _SEVERITY_LABELS.get(code, code)


def _severity_class(code: str) -> str:
    return _SEVERITY_CLASSES.get(code, "sev-normal")


def _db_failure_fragment(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_db_failure_fragment.html",
        {},
        status_code=503,
    )


templates.env.filters["severity_label"] = _severity_label
templates.env.filters["severity_class"] = _severity_class


# ── 표현 전용 Jinja 필터(재설계) — 기계 코드/절대시각을 사람이 읽는 문장/상대시간으로 ──────
# 모두 순수 표시 변환이다(상태 변경 0, DB 0). FailureCategory/플랫폼 값은 plain-string 으로만
# 비교한다(domain enum import 불필요 — 어휘는 코드값 그대로). 읽기 전용 가드 무관(write 호출 0).
_REASON_TEXT: dict[str, str] = {
    "ACCOUNT_AUTH_REQUIRED": "로그인 만료 · 인증 확인 필요",
    "AUTH_REQUIRED": "로그인 만료 · 인증 확인 필요",
    "AUTH_SESSION_PENDING": "인증번호 입력 필요 · Chrome에서 인증번호 입력 후 상태를 재확인하세요",
    "TARGET_VALIDATION_FAILURE": "센터/상점명 불일치 — 오발송 위험",
    "CRAWL_FAILURE": "수집 실패 — 확인 필요",
    "PROFILE_UNAVAILABLE": "브라우저 프로필 준비 실패 — Agent/Chrome 확인 필요",
    "CDP_UNREACHABLE": "브라우저 연결 실패 — Agent/Chrome 확인 필요",
    "CRAWL_TIMEOUT": "수집 작업이 제한 시간 안에 완료되지 않음 — Agent/Chrome/페이지 로딩 확인(로그인 실패 아님)",
    "PARSER_MISSING_DATA": "수집 데이터 누락 — 확인 필요",
    "RENDER_FAILURE": "메시지 생성 실패",
    "TELEGRAM_FAILURE": "텔레그램 전송 오류",
    "KAKAO_FAILURE": "카카오톡 전송 오류",
    "DUPLICATE_BLOCKED": "중복으로 전송 보류",
}
_PLATFORM_LABELS: dict[str, str] = {"BAEMIN": "배민", "COUPANG": "쿠팡"}
_PLATFORM_CLASSES: dict[str, str] = {"BAEMIN": "plat-baemin", "COUPANG": "plat-coupang"}
_MESSENGER_LABELS: dict[str, str] = {"TELEGRAM": "텔레그램", "KAKAO": "카카오"}


def _reason_text(code: str | None) -> str:
    """실패 코드 → 사람이 읽는 사유 문장. 미지 코드는 코드값을 괄호로 보조."""
    if not code:
        return ""
    return _REASON_TEXT.get(code, f"오류 — 확인 필요 ({code})")


def _platform_label(code: str | None) -> str:
    return _PLATFORM_LABELS.get((code or "").upper(), code or "")


def _platform_class(code: str | None) -> str:
    return _PLATFORM_CLASSES.get((code or "").upper(), "")


def _messenger_label(code: str | None) -> str:
    """메신저 코드 → 사람이 읽는 라벨(TELEGRAM→텔레그램, KAKAO→카카오). 미지 코드는 그대로."""
    return _MESSENGER_LABELS.get((code or "").upper(), code or "")


# ── 실시간 큐 뷰 표시 라벨(job type/status → 한글) ──────────────────────────────────
_JOB_TYPE_LABELS: dict[str, str] = {
    "CRAWL_BAEMIN": "배민 수집",
    "CRAWL_COUPANG": "쿠팡 수집/상태 재확인",
    "AUTH_CHECK": "배민 인증 확인",
    "OPEN_AUTH_BROWSER": "수동 인증 브라우저",
    "AUTH_COUPANG_2FA": "쿠팡 자동 2차인증",
    "KAKAO_SEND": "카카오 전송",
    "CAPTURE_DIAGNOSTIC": "진단 캡처",
}
_JOB_STATUS_LABELS: dict[str, str] = {
    "PENDING": "대기",
    "CLAIMED": "배정됨",
    "RUNNING": "실행 중",
    "RETRY": "재시도 대기",
    "SUCCEEDED": "완료",
    "FAILED": "실패",
}
_JOB_STATUS_CLASSES: dict[str, str] = {
    "PENDING": "job-pending",
    "CLAIMED": "job-running",
    "RUNNING": "job-running",
    "RETRY": "job-retry",
    "FAILED": "job-failed-badge",
}


def _job_type_label(code: str | None) -> str:
    if not code:
        return ""
    return _JOB_TYPE_LABELS.get(code, code)


def _job_status_label(code: str | None) -> str:
    if not code:
        return ""
    return _JOB_STATUS_LABELS.get(code, code)


def _job_status_class(code: str | None) -> str:
    return _JOB_STATUS_CLASSES.get(code or "", "job-pending")


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
templates.env.filters["messenger_label"] = _messenger_label
templates.env.filters["relative_time"] = _relative_time
templates.env.filters["freshness_class"] = _freshness_class
templates.env.filters["job_type_label"] = _job_type_label
templates.env.filters["job_status_label"] = _job_status_label
templates.env.filters["job_status_class"] = _job_status_class


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


def _bounded_int(raw: str | None, *, default: int, maximum: int) -> int:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return default
    if value < 0:
        return default
    return min(value, maximum)


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
    repo: DashboardRepository,
    *,
    tenant_id: str,
    now: datetime,
    limit: int | None = None,
    offset: int = 0,
    include_critical_bucket: bool = False,
):
    facts_rows = await repo.target_health(
        tenant_id=tenant_id,
        now=now,
        limit=limit,
        offset=offset,
    )
    if include_critical_bucket and offset == 0 and limit is not None and limit > 0:
        seen_ids = {facts.target_id for facts in facts_rows}
        critical_rows = await repo.critical_target_health(
            tenant_id=tenant_id,
            now=now,
            limit=min(limit, 25),
        )
        critical_rows = [
            facts
            for facts in critical_rows
            if severity_policy.severity_rank(_target_row_for_display(facts, now).severity)
            >= severity_policy.severity_rank(SEVERITY_CRITICAL)
        ]
        facts_rows.extend(
            facts for facts in critical_rows if facts.target_id not in seen_ids
        )
    rows = [_target_row_for_display(facts, now) for facts in facts_rows]
    rows.sort(key=lambda r: severity_policy.severity_rank(r.severity), reverse=True)
    return rows


def _target_row_for_display(facts, now: datetime):
    row = _service.target_row(facts, now)
    return replace(row, severity=_display_severity(row.severity, facts))


def _display_severity(code: str, facts) -> str:
    if code != SEVERITY_STOPPED:
        return code
    if (
        getattr(facts, "kakao_delivery_enabled", False)
        and getattr(facts, "kakao_runtime_unavailable", False)
    ):
        return SEVERITY_CRITICAL
    signals = severity_policy.failclosed_signals_from(
        account_auth_state=facts.account_auth_state,
        lifecycle_state=facts.lifecycle_state,
        latest_failure_code=facts.last_failure_code,
        auth_session_pending=facts.auth_session_pending,
        last_success_at=facts.last_success_at,
        latest_failure_at=facts.last_failure_at,
    )
    if signals.auth_required:
        return SEVERITY_AUTH_REQUIRED
    if signals.target_validation_failed:
        return SEVERITY_TARGET_VALIDATION_FAILURE
    if signals.kakao_misdelivery_risk:
        return SEVERITY_CRITICAL
    return SEVERITY_OPERATOR_STOPPED


# ── 등록된 설정 카드(읽기 전용 — 등록 config + 라이브 severity 조립) ─────────────────────
@dataclass(frozen=True)
class SettingsRow:
    """모니터링 탭 "등록된 설정" 카드 한 행. 등록 config(AdminEntityService) + 라이브 램프 조립.

    secret 0 — 라우팅/상태/시각 표시값만 담는다(SettingsRow 에 자격증명·토큰 필드 없음).
    """

    target_id: str
    name: str
    center_name: str
    severity: str  # 라이브 severity(램프) — target_health join, 없으면 STOPPED(회색)
    crawl_enabled: bool  # MonitoringTarget.status == ACTIVE
    send_enabled: bool  # tenant 발송 활성화 상태(= '관리' ❸ 실제 메시지 보내기 토글)
    schedule_enabled: bool
    start_time: str
    stop_time: str
    interval_minutes: int
    messengers: tuple[str, ...]  # 연결 채널 메신저 distinct(TELEGRAM/KAKAO)
    platform: str  # BAEMIN/COUPANG
    # 전송 준비 readiness — '고객 전송 토글 ON'과 '대상별 채널 연결 완료'를 분리해 표시한다.
    # send_enabled(=customer_sending_enabled)만으론 "전송 ON 인데 메신저 —" 오해가 생겨서다.
    # delivery_ready 는 운영 UI 표시용이며, 실 dispatch 최종 게이트는 계속 runtime 이 본다.
    customer_sending_enabled: bool = False  # = send_enabled. tenant 발송 토글.
    has_active_delivery_rule: bool = False  # 대상에 활성 DeliveryRule 1건 이상.
    delivery_ready: bool = False  # customer_sending_enabled and has_active_delivery_rule.
    delivery_status_label: str = "OFF"  # 'OFF' | '연결 필요' | 'ON'
    customer_name: str = ""
    status: str = ""  # 원시 MonitoringTargetStatus 값


async def _settings_rows_for_display(
    request: Request, *, tenant_id: str, now: datetime
) -> list[SettingsRow]:
    """등록된 모니터링 설정을 한 행씩 조립한다(읽기 전용). 데이터는 5.11 엔티티 service 에서.

    조립 위치를 ``routes.py`` 로 둔 건 등록 config 가 ``AdminEntityService`` 에서 오고 라이브
    램프가 ``DashboardRepository`` 에서 와 두 소스를 합쳐야 하기 때문이다(어느 한 service 도
    상대에 의존하지 않는다). 엔티티 service 미주입/tenant 미선택이면 빈 목록을 돌려 안전하게
    무-카드로 렌더한다. ``전체 고객``(ALL_TENANTS) 선택 시엔 모든 tenant 의 설정을 합쳐 보여준다
    (``list_monitoring_targets`` 는 단일 tenant 전용이라 tenant 마다 조립 후 합친다).
    """

    service = getattr(request.app.state, "admin_entity_service", None)
    if service is None or not tenant_id:
        return []

    if tenant_id == ALL_TENANTS:
        rows: list[SettingsRow] = []
        for tenant in await service.list_tenants():
            rows.extend(
                await _settings_rows_for_tenant(
                    request, service, tenant=tenant, now=now
                )
            )
    else:
        tenant = next(
            (t for t in await service.list_tenants() if t.id == tenant_id), None
        )
        if tenant is None:
            return []
        rows = await _settings_rows_for_tenant(
            request, service, tenant=tenant, now=now
        )

    # 위험도 높은 순으로 정렬하되(targets 카드와 동일), 동순위는 이름 오름차순으로 안정화한다.
    rows.sort(key=lambda r: r.name)
    rows.sort(key=lambda r: severity_policy.severity_rank(r.severity), reverse=True)
    return rows


async def _settings_rows_for_tenant(
    request: Request, service, *, tenant, now: datetime
) -> list[SettingsRow]:
    """단일 tenant 의 등록 설정 행을 조립한다(정렬 전). ``_settings_rows_for_display`` 의 단위.

    tenant 객체를 받아 호출자가 ``list_tenants`` 를 1번만 돌게 한다(전체 고객 N tenant 루프에서
    tenant 마다 전체 목록을 다시 긁지 않도록).
    """

    tenant_id = tenant.id
    targets = await service.list_monitoring_targets(tenant_id)
    channels = await service.list_messenger_channels(tenant_id)
    accounts = await service.list_platform_accounts(tenant_id)

    tenant_sending_enabled = bool(getattr(tenant, "sending_enabled", False))
    customer_name = tenant.name

    account_platform = {a.id: a.platform.value for a in accounts}
    # 대상 연결 readiness/메신저 표시는 실 dispatch 게이트와 같은 기준을 본다 — 활성 규칙 + ACTIVE 채널
    # (snapshot_repository: enabled rule AND channel.state == ACTIVE). PENDING/INACTIVE 채널에 규칙이
    # 걸려도 실제로는 안 나가므로 readiness 에서 빼야 "대상 연결 ON 인데 미전송" 오해가 안 생긴다.
    channel_messenger = {
        c.id: c.messenger.value
        for c in channels
        if c.state is MessengerChannelState.ACTIVE
    }

    # 라이브 severity(램프)는 targets 카드와 동일 정제(_target_row_for_display)로 맞춘다 — STOPPED 가
    # AUTH_REQUIRED/검증실패 등으로 분기해 두 카드의 램프 색이 어긋나지 않게 한다.
    facts_rows = await _repo(request).target_health(tenant_id=tenant_id, now=now)
    sev_by_id = {
        f.target_id: _target_row_for_display(f, now).severity for f in facts_rows
    }

    rows: list[SettingsRow] = []
    for t in targets:
        # N+1: 대상별 delivery rule 조회. 벌크 list_* 가 없어 대상마다 1쿼리지만 ~100건 규모라 허용.
        rules = await service.list_delivery_rules(t.id, tenant_id=tenant_id)
        # ACTIVE 채널로 연결된 활성 규칙만 readiness/메신저에 센다(channel_messenger 는 ACTIVE 채널만
        # 담는다 — PENDING/INACTIVE 채널 연결은 실 dispatch 가 안 보내므로 여기서도 제외).
        active_messengers = sorted(
            {
                channel_messenger[r.channel_id]
                for r in rules
                if r.enabled and r.channel_id in channel_messenger
            }
        )
        messengers = tuple(active_messengers)
        # 전송 ON/OFF 는 '관리' 화면의 ❸ 실제 메시지 보내기 토글과 같은 의미 — tenant 발송 활성화
        # 상태(tenant.sending_enabled)만 반영한다. 전역 게이트/전송 시간창 같은 실 dispatch 게이트는
        # _enqueue_dispatch_records 가 실행 시점에 따로 본다(여긴 '등록 상태' 표시이지 실행 게이트가
        # 아니다 — 두 화면이 같은 활성화 상태를 보여 운영자가 헷갈리지 않게 한다).
        send_enabled = tenant_sending_enabled
        # 대상 연결 readiness: 고객 전송 ON 인데 ACTIVE 채널로 연결된 활성 규칙이 0건이면 '연결 필요'
        # (fail-closed). 이래야 "전송 ON 인데 메신저 —" 가 운영자에게 '연결 빠짐'으로 읽힌다.
        has_active_delivery_rule = bool(active_messengers)
        delivery_ready = send_enabled and has_active_delivery_rule
        if not send_enabled:
            delivery_status_label = "OFF"
        elif has_active_delivery_rule:
            delivery_status_label = "ON"
        else:
            delivery_status_label = "연결 필요"
        rows.append(
            SettingsRow(
                target_id=t.id,
                name=t.name,
                center_name=t.center_name,
                severity=sev_by_id.get(t.id, SEVERITY_STOPPED),
                crawl_enabled=t.status == MonitoringTargetStatus.ACTIVE,
                send_enabled=send_enabled,
                schedule_enabled=t.schedule_enabled,
                start_time=t.start_time,
                stop_time=t.stop_time,
                interval_minutes=t.interval_minutes,
                messengers=messengers,
                platform=account_platform.get(t.platform_account_id, ""),
                customer_sending_enabled=send_enabled,
                has_active_delivery_rule=has_active_delivery_rule,
                delivery_ready=delivery_ready,
                delivery_status_label=delivery_status_label,
                customer_name=customer_name,
                status=t.status.value,
            )
        )
    return rows


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
    try:
        tenants = await _dashboard_tenants(request)
        tenant_id = await _dashboard_tenant_id(request, tenants=tenants)
        targets = await _target_rows_for_display(
            repo,
            tenant_id=tenant_id,
            now=now,
            limit=DEFAULT_TARGET_FRAGMENT_LIMIT,
        )
        agents = await _service.agent_rows(repo, now=now)
        channels = await _service.channel_health(repo, tenant_id=tenant_id, now=now)
        auth_required = await _service.auth_required_rows(repo, tenant_id=tenant_id, now=now)
        jobs = await _service.job_queue_rows(repo, tenant_id=tenant_id, now=now, limit=100)
        settings_rows = await _settings_rows_for_display(
            request, tenant_id=tenant_id, now=now
        )
    except Exception:  # noqa: BLE001 - Admin UI returns operator-safe HTML instead of JSON 500.
        return templates.TemplateResponse(
            request,
            "_db_failure.html",
            {},
            status_code=503,
        )
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
            "jobs": jobs,
            "settings_rows": settings_rows,
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

    limit = _bounded_int(
        request.query_params.get("limit"),
        default=DEFAULT_TARGET_FRAGMENT_LIMIT,
        maximum=MAX_TARGET_FRAGMENT_LIMIT,
    )
    offset = _bounded_int(request.query_params.get("offset"), default=0, maximum=10_000)
    try:
        fetched_rows = await _target_rows_for_display(
            _repo(request),
            tenant_id=_tenant_id(request),
            now=_now(),
            limit=limit + 1,
            offset=offset,
            include_critical_bucket=True,
        )
    except Exception:  # noqa: BLE001 - fragment must remain operator-safe HTML.
        return _db_failure_fragment(request)
    rows = fetched_rows[:limit]
    next_offset = offset + limit
    has_more = len(fetched_rows) > limit
    return templates.TemplateResponse(
        request,
        "_targets.html",
        {
            "targets": rows,
            "tenant_id": _tenant_id(request),
            "limit": limit,
            "offset": offset,
            "next_offset": next_offset,
            "has_more": has_more,
        },
    )


@router.get("/agents", response_class=HTMLResponse)
async def agents_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/agents`` — HTMX 부분 fragment(Agent fleet 상태)."""

    try:
        rows = await _service.agent_rows(_repo(request), now=_now())
    except Exception:  # noqa: BLE001 - fragment must remain operator-safe HTML.
        return _db_failure_fragment(request)
    return templates.TemplateResponse(request, "_agents.html", {"agents": rows})


@router.get("/channels", response_class=HTMLResponse)
async def channels_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/channels`` — HTMX 부분 fragment(Kakao lag / Telegram 오류 구분)."""

    try:
        health = await _service.channel_health(
            _repo(request), tenant_id=_tenant_id(request), now=_now()
        )
    except Exception:  # noqa: BLE001 - fragment must remain operator-safe HTML.
        return _db_failure_fragment(request)
    return templates.TemplateResponse(request, "_channels.html", {"channels": health})


@router.get("/registered-settings", response_class=HTMLResponse)
async def registered_settings_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/registered-settings`` — 등록된 모니터링 설정 목록 fragment(읽기 전용).

    운영자가 "지금 시스템에 최종 등록된 모든 설정"(상태/수집·전송 활성/시간/주기/메신저/플랫폼)을
    한 표로 본다. 등록 config 와 라이브 램프를 :func:`_settings_rows_for_display` 로 조립한다.
    """

    try:
        rows = await _settings_rows_for_display(
            request, tenant_id=_tenant_id(request), now=_now()
        )
    except Exception:  # noqa: BLE001 - fragment must remain operator-safe HTML.
        return _db_failure_fragment(request)
    return templates.TemplateResponse(
        request,
        "_registered_settings.html",
        {"settings_rows": rows, "tenant_id": _tenant_id(request)},
    )


@router.get("/auth-required", response_class=HTMLResponse)
async def auth_required_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/auth-required`` — AC4 인증 필요 대상 필터 fragment."""

    try:
        rows = await _service.auth_required_rows(_repo(request), tenant_id=_tenant_id(request), now=_now())
    except Exception:  # noqa: BLE001 - fragment must remain operator-safe HTML.
        return _db_failure_fragment(request)
    return templates.TemplateResponse(
        request,
        "_auth_required_section.html",
        {"auth_required": rows, "tenant_id": _tenant_id(request)},
    )


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_queue_fragment(
    request: Request,
    _auth: None = Depends(require_admin_session),
) -> HTMLResponse:
    """``GET /admin/jobs`` — 실시간 큐(active job) fragment.

    운영자가 "이미 진행 중인 수집 작업이 있습니다"의 실체(어떤 job 이 어떤 상태로 막혀 있는지,
    배정 Agent 가 살아있는지)를 직접 보게 한다. 읽기 전용(상태 변경 0).
    """

    try:
        rows = await _service.job_queue_rows(
            _repo(request), tenant_id=_tenant_id(request), now=_now(), limit=100
        )
    except Exception:  # noqa: BLE001 - fragment must remain operator-safe HTML.
        return _db_failure_fragment(request)
    return templates.TemplateResponse(
        request,
        "_jobs_queue.html",
        {"jobs": rows, "tenant_id": _tenant_id(request)},
    )
