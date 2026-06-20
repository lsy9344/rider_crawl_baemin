"""Admin 접근 강제(게이트) — MFA·4역할·IP allowlist + audit-on-deny — Story 5.8 / AC2.

:mod:`principal` 의 순수 정책(역할 rank·principal) 위에 **fail-closed 강제기** 를 얹는다. 모든
기본값은 **deny**(게이트레일 #4): principal 미해결 → 401, IP 불허 → 403, MFA 미검증(privileged)
→ 403, 역할 부족 → 403. 거부된 시도는 ``result=DENIED`` 로 **audit**(보안 audit 핵심 — 시도
자체를 남긴다). audit-write 는 routes.py(읽기 전용)가 아니라 :class:`AdminActionService` 경유라
read-only 가드(게이트레일 #1)와 정합한다.

**자격 저장·MFA 챌린지 인프라는 외부**(auth front/IdP/config registry — 신규 DB 테이블 0). 서버는
주입된 principal 의 ``role``/``mfa_verified``/``source`` 를 **강제·audit** 만 한다. 운영/테스트는
``app.state.resolve_admin_principal`` seam(``request → AdminPrincipal | None``)으로 principal 을
주입하고, ``app.state.admin_ip_allowlist``/``admin_mfa_required`` 로 강제 정책을 설정한다.

IP allowlist 판정(:func:`ip_allowed`)은 stdlib ``ipaddress`` 만 쓰는 **순수 함수** 라 always-run
단위로 잠근다(신규 deps 0 — 게이트레일 #7; memory pg-gated-files-hide-pure-helpers).
"""

from __future__ import annotations

import inspect
import ipaddress
from collections.abc import Sequence
from datetime import datetime, timezone
from http import HTTPStatus
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from rider_server.services.admin_action_service import (
    ACTION_ACCESS_DENIED,
    AdminActionService,
)

from .principal import (
    AdminPrincipal,
    AdminRole,
    is_privileged,
    role_satisfies,
)


# ── 순수 정책: source IP 도출 + allowlist 판정(stdlib ipaddress, 신규 deps 0) ───────

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _ip_matches_any(ip: str, entries: Sequence[str]) -> bool:
    """Return True when ``ip`` is in one of the configured exact IP/CIDR entries."""

    if not entries:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False

def source_ip(request: Request) -> str:
    """요청의 source IP 를 도출한다.

    기본은 ``request.client.host`` 이다. ``X-Forwarded-For`` 는 직접 클라이언트가 spoof할 수
    있으므로, 요청이 명시적으로 설정된 trusted proxy CIDR에서 왔을 때만 선두 토큰을 신뢰한다.
    """

    client = request.client
    raw_client = client.host if client is not None else ""
    trusted_proxy_cidrs = getattr(request.app.state, "trusted_proxy_cidrs", ()) or ()
    xff = request.headers.get("x-forwarded-for", "")
    if xff and _ip_matches_any(raw_client, trusted_proxy_cidrs):
        return xff.split(",")[0].strip()
    return raw_client


def ip_allowed(ip: str, allowlist: Sequence[str]) -> bool:
    """``ip`` 가 ``allowlist`` 에 속하면 True(allowlist 미설정이면 제한 없음 → True).

    allowlist 항목은 정확 IP(``203.0.113.5``) 또는 CIDR 네트워크(``10.0.0.0/8``)다. ``ip`` 가
    파싱 불가하면 **fail-closed(False)** — allowlist 가 설정된 상태에서 source 를 모르면 거부.
    빈 allowlist 는 "추가 제한 없음"으로 해석한다(IP allowlist 는 opt-in 추가 제한, AC2).
    """

    if not allowlist:
        return True
    return _ip_matches_any(ip, allowlist)


def _normalize_origin(value: str) -> str | None:
    """URL/Origin 문자열을 ``scheme://host[:port]`` 형태로 정규화한다. 파싱 실패는 None."""

    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if not parsed.scheme or not parsed.hostname:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _admin_allowed_origins(request: Request) -> set[str]:
    origins = {_normalize_origin(str(request.base_url))}
    for origin in getattr(request.app.state, "admin_allowed_origins", ()) or ():
        origins.add(_normalize_origin(str(origin)))
    return {origin for origin in origins if origin}


def admin_origin_allowed(request: Request) -> bool:
    """Admin 쓰기 요청의 Origin/Referer same-origin 가드.

    브라우저가 ``Origin`` 을 보내면 그것을 우선 검증하고, 없지만 ``Referer`` 가 있으면 Referer 를
    검증한다. 둘 다 없으면 cookie/session 기반 admin write 는 CSRF 방어를 위해 거부한다.
    """

    if request.method.upper() not in _UNSAFE_METHODS:
        return True
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    normalized = _normalize_origin(source)
    return normalized is not None and normalized in _admin_allowed_origins(request)


# ── app.state seam 접근자 + 기본값(fail-closed) ─────────────────────────────────

def _default_resolve_admin_principal(request: Request) -> AdminPrincipal | None:
    """principal 해석 기본 seam — 자격/MFA 인프라 부재라 **None**(fail-closed deny, 401).

    운영/테스트는 ``app.state.resolve_admin_principal`` 을 외부 auth front 신뢰 헤더 해석기 또는
    주입 principal 로 교체한다(5.3 ``resolve_agent_id``·5.6 ``require_admin_session`` 선례).
    """

    return None


def _now() -> datetime:
    """audit timestamp — 라우트/게이트는 주입 불가한 실 ``now()`` 를 쓴다(시각 단언은 service 레이어)."""

    return datetime.now(timezone.utc)


async def resolve_principal(request: Request) -> AdminPrincipal | None:
    """주입된 ``app.state.resolve_admin_principal`` seam 으로 principal 을 해석한다(sync/async 모두)."""

    seam = getattr(
        request.app.state, "resolve_admin_principal", _default_resolve_admin_principal
    )
    result = seam(request)
    if inspect.isawaitable(result):
        result = await result
    return result


def _allowlist(request: Request) -> Sequence[str]:
    return getattr(request.app.state, "admin_ip_allowlist", ()) or ()


def _mfa_required(request: Request) -> bool:
    return bool(getattr(request.app.state, "admin_mfa_required", True))


def _action_service(request: Request) -> AdminActionService | None:
    return getattr(request.app.state, "admin_action_service", None)


async def _audit_denied(
    request: Request, principal: AdminPrincipal | None, code: str, min_role: AdminRole
) -> None:
    """거부 시도를 ``result=DENIED`` 로 audit(service 경유 — read-only 가드 정합).

    ``principal`` 이 None(미인증 401)일 때는 audit-write 를 하지 않는다 — 미인증 POST 폭주가
    audit write 를 증폭하는 것을 막는다(보안 audit 의 핵심 가치는 **인증된 주체의 무권한 시도**
    를 남기는 것 = insider 추적). 인증된 주체의 거부(IP/MFA/역할)는 반드시 남긴다.
    """

    if principal is None:
        return
    service = _action_service(request)
    if service is None:
        return
    await service.record_denied(
        actor_id=principal.actor_id,
        action=ACTION_ACCESS_DENIED,
        source=principal.source or source_ip(request),
        reason=f"{code}: {request.method} {request.url.path} (min_role={min_role.value})",
        at=_now(),
    )


async def _audit_break_glass(request: Request, principal: AdminPrincipal) -> None:
    service = _action_service(request)
    if service is None:
        return
    await service.record_break_glass(
        actor_id=principal.actor_id,
        source=principal.source or source_ip(request),
        reason=f"BREAK_GLASS: {request.method} {request.url.path}",
        at=_now(),
    )


# ── 강제기: 세션(VIEWER) + 역할 게이트(privileged) ───────────────────────────────

async def enforce_session(request: Request) -> AdminPrincipal:
    """VIEWER 수준 세션 강제(읽기 전용 대시보드용) — principal 해석 + IP allowlist 만.

    읽기 경로라 **write-free**(audit-on-deny 0 — 게이트레일 #1: 읽기 전용은 write 금지).
    principal 미해결 → 401, IP 불허 → 403. 통과 principal 은 ``request.state.admin_principal`` 에
    저장해 하위 actor/source 도출에 쓴다.
    """

    principal = await resolve_principal(request)
    if principal is None:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "admin authentication required")
    if not ip_allowed(source_ip(request), _allowlist(request)):
        raise HTTPException(HTTPStatus.FORBIDDEN, "source not allowed")
    request.state.admin_principal = principal
    return principal


def require_role(min_role: AdminRole):
    """``min_role`` 이상 역할을 요구하는 라우트 의존성을 만든다(fail-closed + audit-on-deny).

    순서: principal 해석(None→401) → IP allowlist(불허→403) → MFA(privileged·강제 시 미검증→403)
    → 역할 rank(부족→403). 거부는 모두 ``DENIED`` audit(인증된 주체일 때). break-glass 가
    privileged 게이트를 통과하면 강하게 audit 한다(AC2 — 모든 break-glass 사용 기록). 통과
    principal 은 ``request.state.admin_principal`` 에 저장한다(라우트 actor/source 도출).
    """

    async def _dep(request: Request) -> AdminPrincipal:
        principal = await resolve_principal(request)
        if principal is None:
            raise HTTPException(
                HTTPStatus.UNAUTHORIZED, "admin authentication required"
            )
        if not ip_allowed(source_ip(request), _allowlist(request)):
            await _audit_denied(request, principal, "IP_NOT_ALLOWED", min_role)
            raise HTTPException(HTTPStatus.FORBIDDEN, "source not allowed")
        if is_privileged(min_role) and not admin_origin_allowed(request):
            await _audit_denied(request, principal, "ORIGIN_NOT_ALLOWED", min_role)
            raise HTTPException(HTTPStatus.FORBIDDEN, "admin origin not allowed")
        if is_privileged(min_role) and _mfa_required(request) and not principal.mfa_verified:
            await _audit_denied(request, principal, "MFA_REQUIRED", min_role)
            raise HTTPException(HTTPStatus.FORBIDDEN, "MFA verification required")
        if not role_satisfies(principal.role, min_role):
            await _audit_denied(request, principal, "ROLE_INSUFFICIENT", min_role)
            raise HTTPException(HTTPStatus.FORBIDDEN, "insufficient admin role")
        if principal.role is AdminRole.BREAK_GLASS and is_privileged(min_role):
            await _audit_break_glass(request, principal)
        request.state.admin_principal = principal
        return principal

    return _dep
