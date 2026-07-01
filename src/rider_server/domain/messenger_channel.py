"""``MessengerChannel`` 도메인 모델(Story 2.5 / AC1·AC3) — 텔레그램 chat/topic 또는 Kakao room.

텔레그램(``telegram_chat_id``/``thread_id``) 또는 Kakao(``kakao_room_name``) 중 한쪽만
채워진다. ``telegram_chat_id``/``thread_id`` 는 **라우팅 식별자라 secret이 아니다**
(2.4 결정 계승 — ref화 금지). 비활성화는 물리 삭제가 아니라
``state=MessengerChannelState.INACTIVE`` 상태 전이로 표현한다(soft delete, FR-4).
"""

from __future__ import annotations

from dataclasses import dataclass

from .states import Messenger, MessengerChannelState


@dataclass(frozen=True)
class MessengerChannel:
    id: str
    tenant_id: str  # → Tenant
    messenger: Messenger
    telegram_chat_id: str | None = None  # 라우팅 식별자 (secret 아님)
    thread_id: str | None = None  # 라우팅 식별자 (secret 아님)
    kakao_room_name: str | None = None
    state: MessengerChannelState = MessengerChannelState.PENDING
    # Phase 3 카카오 인바운드 명령 트리거(additive, default 보수적):
    # ``kakao_chat_id`` 는 라우팅 식별자(secret 아님)로 룸명만 설정된 채널은 첫 인바운드 매칭 시
    # 서버가 바인딩한다. ``command_trigger_enabled`` 가 False(기본)면 명령 트리거를 받지 않는다.
    kakao_chat_id: str | None = None
    command_trigger_enabled: bool = False
