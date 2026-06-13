"""``PlatformAccount`` 도메인 모델(Story 2.5 / AC1) — 배민/쿠팡 로그인 계정.

자격증명은 **평문이 아니라 ``SecretRef`` 참조**(``username_ref``/``password_ref``)로만
가리킨다(data-api-contract: "uses secret refs, not raw credentials"). ``auth_state`` 는
배민 auth state 정본(``BaeminAuthState``) — 쿠팡 Gmail reauth 전용 상태 확장은 Epic 4 소유.
"""

from __future__ import annotations

from dataclasses import dataclass

from .secret_ref import SecretRef
from .states import BaeminAuthState, Platform


@dataclass(frozen=True)
class PlatformAccount:
    id: str
    tenant_id: str  # → Tenant
    platform: Platform
    label: str
    username_ref: SecretRef  # → SecretRef (평문 자격증명 아님)
    password_ref: SecretRef  # → SecretRef (평문 자격증명 아님)
    auth_state: BaeminAuthState = BaeminAuthState.UNKNOWN
