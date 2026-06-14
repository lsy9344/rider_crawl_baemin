"""Admin 접근 주체(principal)와 4역할 정본 — Story 5.8 / AC2.

5.6/5.7 이 일부러 비워 둔 인증 seam(``require_admin_session``/``resolve_admin_actor``)의
**아래쪽 진실** 을 여기서 모델링한다. 서버는 자격 저장·MFA 챌린지 인프라를 갖지 않는다
(외부 auth front/IdP/reverse-proxy 신뢰 헤더 + config operator registry — **신규 DB 테이블 0**,
14표 lock). 서버의 책임은 principal 의 ``role``·``mfa_verified``·``source`` 를 **강제·audit**
하는 것뿐이다(architecture #Access-Model line 144-149: MFA 필수·4역할).

**4역할 = architecture 결정(line 144-145) — 임의 5번째 추가 금지(count-lock 4):**
  * ``VIEWER`` — 읽기 전용 대시보드(GET)만.
  * ``OPERATOR`` — 5.7 운영 액션(activate/pause/retry/test-send/구독 전이 등).
  * ``SECRET_ADMIN`` — secret/token revoke·rotate(AC3).
  * ``BREAK_GLASS`` — 긴급 override(전 권한, 강하게 audit).

역할은 **단조 rank**(VIEWER<OPERATOR<SECRET_ADMIN<BREAK_GLASS)로 비교한다 — 상위 역할이 하위
권한을 포함한다(break-glass = 전 권한). ``role_satisfies(principal_role, min_role)`` 는 순수
함수라 always-run 단위로 잠근다(memory pg-gated-files-hide-pure-helpers).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AdminRole(str, Enum):
    """Admin 4역할 정본(architecture line 144-145 순서). ``(str, Enum)`` + 멤버 이름 == 대문자 값
    (2.5 enum 컨벤션). **count-lock 4 — 5번째 멤버 추가 금지.**
    """

    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    SECRET_ADMIN = "SECRET_ADMIN"
    BREAK_GLASS = "BREAK_GLASS"


#: 역할 단조 rank — 상위가 하위 권한을 포함(break-glass = 전 권한). 게이트 비교의 유일한 정본.
_ROLE_RANK: dict[AdminRole, int] = {
    AdminRole.VIEWER: 0,
    AdminRole.OPERATOR: 1,
    AdminRole.SECRET_ADMIN: 2,
    AdminRole.BREAK_GLASS: 3,
}

#: privileged(쓰기) 게이트의 최소 역할 — VIEWER 초과면 MFA 강제 대상(읽기 전용 view 는 제외).
PRIVILEGED_MIN_ROLE = AdminRole.OPERATOR


def role_satisfies(principal_role: AdminRole, min_role: AdminRole) -> bool:
    """``principal_role`` 의 rank 가 ``min_role`` 이상이면 True(상위 역할이 하위 권한 포함).

    예: SECRET_ADMIN(2) 은 OPERATOR(1) 게이트 통과, OPERATOR(1) 는 SECRET_ADMIN(2) 게이트 불통과.
    BREAK_GLASS(3) 는 모든 게이트 통과(전 권한). 미정의 역할은 fail-closed(False).
    """

    try:
        return _ROLE_RANK[principal_role] >= _ROLE_RANK[min_role]
    except KeyError:
        return False


def is_privileged(min_role: AdminRole) -> bool:
    """게이트가 privileged(쓰기)인가 — VIEWER 초과(OPERATOR↑). MFA 강제·audit-on-deny 판단 기준."""

    return _ROLE_RANK.get(min_role, 0) >= _ROLE_RANK[PRIVILEGED_MIN_ROLE]


@dataclass(frozen=True)
class AdminPrincipal:
    """해석된 Admin 접근 주체(불변).

    ``actor_id`` 는 운영자 식별자(UUID 문자열 권장 — audit ``actor_id`` 컬럼에 그대로 들어감,
    UUID 아니면 sentinel 로 ``diff_redacted`` 보존). ``role`` 은 :class:`AdminRole`,
    ``mfa_verified`` 는 외부 auth front 가 MFA 챌린지를 통과시켰는지, ``source`` 는 출처 라벨
    (예 ``ADMIN_UI``/역할/source IP 조합 — audit ``source`` 에 기록, redaction 통과).
    """

    actor_id: str
    role: AdminRole
    mfa_verified: bool = False
    source: str | None = None
