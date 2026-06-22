"""``DeliveryRule`` 도메인 모델(Story 2.5 / AC1·AC3) — 대상 → 채널 매핑(fan-out 토대, FR-9).

``(target_id, channel_id)`` 매핑이라, **같은 ``target_id`` 에 ``channel_id`` 가 다른 여러
인스턴스**로 "한 대상 → 2개 이상 채널" fan-out을 표현한다. 비활성화는 물리 삭제가 아니라
``enabled=False`` 상태값으로 표현한다(soft delete, FR-4). ``template_id`` 의 Message
template은 Epic 3 소유라 ``str`` FK placeholder(forward-reference)로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryRule:
    id: str
    target_id: str  # → MonitoringTarget
    channel_id: str  # → MessengerChannel
    template_id: str = ""  # → Message template (Epic 3 — str FK placeholder)
    enabled: bool = True  # soft delete: False = 비활성(물리 삭제 아님)
    send_only_on_change: bool = False
