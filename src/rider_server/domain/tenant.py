"""``Tenant`` 도메인 모델(Story 2.5 / AC1) — 구독 고객 조직."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .states import CustomerLifecycleState


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    status: CustomerLifecycleState
    created_at: datetime  # 자동 now() 기본값 금지 — 순수·결정적, 호출부가 주입
    # tenant 별 텔레그램 설정(0012). 봇 토큰/webhook secret 은 평문 저장(redaction 으로 마스킹),
    # sending_enabled 는 fail-closed 기본 OFF. 기존 positional 생성 호환 위해 default 필드로 둔다.
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    sending_enabled: bool = False
    # 전송 테스트 게이트(0023): 채널 전송 테스트가 마지막으로 성공한 시각. None 이면 미통과 →
    # sending_enabled OFF→ON 전이를 막는다(fail-closed). 한 번이라도 채널 테스트가 성공하면
    # 스탬프되고, 그 뒤 운영자가 실발송을 켤 수 있다. 호출부가 시각을 주입한다(순수·결정적).
    send_test_passed_at: datetime | None = None
