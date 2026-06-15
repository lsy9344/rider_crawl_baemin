"""``PlatformAccount`` 도메인 모델(Story 2.5 / AC1) — 배민/쿠팡 로그인 계정.

자격증명은 **평문이 아니라 ``SecretRef`` 참조**(``username_ref``/``password_ref``)로만
가리킨다(data-api-contract: "uses secret refs, not raw credentials"). ``auth_state`` 는
배민 auth state 정본(``BaeminAuthState``). 쿠팡 인증 메일도 앱 비밀번호 원문 대신
``SecretRef`` 핸들만 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .secret_ref import SecretRef
from .states import BaeminAuthState, Platform, SecretStorageClass


def _empty_secret_ref() -> SecretRef:
    return SecretRef(ref="", storage_class=SecretStorageClass.CENTRAL)


@dataclass(frozen=True)
class PlatformAccount:
    id: str
    tenant_id: str  # → Tenant
    platform: Platform
    label: str
    username_ref: SecretRef  # → SecretRef (평문 자격증명 아님)
    password_ref: SecretRef  # → SecretRef (평문 자격증명 아님)
    verification_email_address_ref: SecretRef = field(default_factory=_empty_secret_ref)
    verification_email_app_password_ref: SecretRef = field(default_factory=_empty_secret_ref)
    verification_email_subject_keyword: str = "인증번호"
    verification_email_sender_keyword: str = "coupang"
    auth_state: BaeminAuthState = BaeminAuthState.UNKNOWN
