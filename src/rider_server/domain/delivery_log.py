"""``DeliveryLog`` 도메인 모델(Story 3.5 / P2-05, FR-10) — 전송 결과·dedup 기록 레코드.

``data-api-contract`` 의 ``delivery_logs`` 모델(``id``·``message_id``·``channel_id``·
``status``·``dedup_key``·``error_code``·``sent_at``)을 순수 frozen dataclass로 둔다
(2.5/3.2/3.3 도메인 모델 패턴 계승 — 11번째 도메인 모델). ``DispatchJob``(3.4) +
``collected_at`` → ``DeliveryLog`` **변환·dedup 정책(insert-then-send)** 은
``services/idempotency.py`` 의 ``IdempotentDeliveryService`` 가 담당한다 — ``domain/`` 은
``rider_crawl`` 을 import하지 않는 순수 레코드로 유지한다(레이어 분리: domain=순수 레코드,
services=정책/변환).

dedup key 5차원(``monitoring_target_id``·``messenger_channel_id``·``snapshot_collected_at``·
``template_version``·``message_hash``)은 ``dedup_key`` **문자열 안에 합성**된다 —
``target_id``·``collected_at`` 은 본 레코드에 직접 컬럼으로 두지 않고, 계약 컬럼인
``message_id``(→Message FK)·``channel_id``(→MessengerChannel FK)만 보존한다.

위임처(여기서 하지 않는 것): ``error_code`` 분류·재시도·``AUTH_REQUIRED`` = Story 3.6
(본 스토리는 항상 ``None``), ``delivery_logs`` 테이블·``uq_delivery_logs_dedup_key`` DB
UNIQUE·ORM/Alembic·async wiring·런타임 교체·migration seed 적재 = Epic 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .states import DeliveryStatus


@dataclass(frozen=True)
class DeliveryLog:
    id: str  # 영속 시 delivery_logs.id — 호출부 주입(log_id_for; 서비스 내부 uuid4() 금지)
    message_id: str  # → Message FK. 어느 Message를 전송했는지 추적(계약 컬럼)
    channel_id: str  # → MessengerChannel FK. 어느 채널로 보냈는지(계약 컬럼·dedup 차원)
    status: DeliveryStatus  # SENT(성공 전송) | DUPLICATE_BLOCKED(중복 차단 audit)
    dedup_key: str  # 5차원 합성 idempotency key(build_dedup_key 산출 — 축소 금지)
    error_code: str | None = None  # 3.6 소유 — 본 스토리는 항상 None(실패 미분류)
    sent_at: datetime | None = None  # SENT만 값(주입), DUPLICATE_BLOCKED은 None
