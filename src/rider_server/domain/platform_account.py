"""``PlatformAccount`` 도메인 모델(Story 2.5 / AC1) — 배민/쿠팡 로그인 계정.

``username``/``password``/``verification_email_*`` 는 호환 컬럼명이며 값은 SecretRef 핸들이다.
``auth_state`` 는 배민 auth state 정본(``BaeminAuthState``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .states import BaeminAuthState, Platform


@dataclass(frozen=True)
class PlatformAccount:
    id: str
    tenant_id: str  # → Tenant
    platform: Platform
    label: str
    username: str = ""  # 배민/쿠팡 로그인 ID ref
    password: str = ""  # 배민/쿠팡 로그인 비밀번호 ref
    verification_email_address: str = ""  # 2차인증 이메일 주소 ref
    verification_email_app_password: str = ""  # IMAP 앱 비밀번호 ref
    verification_email_subject_keyword: str = "인증번호"
    verification_email_sender_keyword: str = "coupang"
    auth_state: BaeminAuthState = BaeminAuthState.UNKNOWN
