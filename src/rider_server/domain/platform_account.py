"""``PlatformAccount`` 도메인 모델(Story 2.5 / AC1) — 배민/쿠팡 로그인 계정.

자격증명은 DB에 평문으로 저장한다(운영 간소화). ``username``/``password`` 는
배민·쿠팡 사이트 로그인 ID/비밀번호, ``verification_email_address``/
``verification_email_app_password`` 는 쿠팡이츠 2차 인증용 IMAP 메일 주소와
앱 비밀번호다. ``auth_state`` 는 배민 auth state 정본(``BaeminAuthState``).
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
    username: str = ""  # 배민/쿠팡 로그인 ID(평문)
    password: str = ""  # 배민/쿠팡 로그인 비밀번호(평문)
    verification_email_address: str = ""  # 2차인증 이메일 주소(평문)
    verification_email_app_password: str = ""  # IMAP 앱 비밀번호(평문)
    verification_email_subject_keyword: str = "인증번호"
    verification_email_sender_keyword: str = "coupang"
    auth_state: BaeminAuthState = BaeminAuthState.UNKNOWN
