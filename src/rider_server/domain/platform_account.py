"""``PlatformAccount`` 도메인 모델(Story 2.5 / AC1) — 배민/쿠팡 로그인 계정.

``username`` 과 ``verification_email_address`` 는 운영 식별값이고,
``password``/``verification_email_app_password`` 는 secret ref 핸들만 담는다.
실제 배민·쿠팡 비밀번호와 IMAP 앱 비밀번호는 DB 밖의 secret store에서 관리한다.
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
    username: str = ""  # 배민/쿠팡 로그인 ID 또는 ref 핸들
    password: str = ""  # 배민/쿠팡 로그인 비밀번호 ref 핸들
    verification_email_address: str = ""  # 2차인증 이메일 주소 또는 ref 핸들
    verification_email_app_password: str = ""  # IMAP 앱 비밀번호 ref 핸들
    verification_email_subject_keyword: str = "인증번호"
    verification_email_sender_keyword: str = "coupang"
    auth_state: BaeminAuthState = BaeminAuthState.UNKNOWN
