"""rider_server security 패키지 — Story 5.8 (Admin 접근 보안: MFA·4역할·IP allowlist).

architecture 정본 위치(line 446 ``security/``)다. admin/ 와 **분리** 해 read-only 가드 scope
(admin/ 화이트리스트)와 충돌 없이 접근 게이트를 얹는다. 순수 정책(:mod:`principal`)과
강제기(:mod:`access`)를 재노출한다 — 단방향 import(``rider_agent`` 0, ``rider_server`` →
``rider_crawl`` 만)·service 위임(audit-on-deny 는 :class:`AdminActionService` 경유).
"""

from __future__ import annotations

from .access import (
    enforce_session,
    ip_allowed,
    require_role,
    resolve_principal,
    source_ip,
)
from .principal import (
    PRIVILEGED_MIN_ROLE,
    AdminPrincipal,
    AdminRole,
    is_privileged,
    role_satisfies,
)

__all__ = [
    "AdminRole",
    "AdminPrincipal",
    "role_satisfies",
    "is_privileged",
    "PRIVILEGED_MIN_ROLE",
    "require_role",
    "enforce_session",
    "resolve_principal",
    "ip_allowed",
    "source_ip",
]
