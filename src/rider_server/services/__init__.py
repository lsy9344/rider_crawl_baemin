"""rider_server 서비스 레이어(정책/전이 로직) — 명시 재노출.

본 패키지의 첫 코드는 Story 2.6의 순수 ``SubscriptionGate`` 게이트 정책이다(FR-6·FR-30).
Story 3.1(Epic 3, P2-01)이 ``run_once`` 분해 결과인 ``CrawlService``/
``MessageRenderService``/``DispatchService`` 를 같은 디렉터리에 additive로 추가했고,
``idempotency``/async wiring은 Story 3.5/Epic 5가 덧붙인다(architecture 425-429).
Story 3.2(P2-02, FR-7)가 ``SnapshotNormalizer``/``MissingSnapshotDataError`` (수집 결과
정규화 ``Snapshot`` 변환 + 필수데이터 fail-closed)를 같은 레이어에 additive로 추가했다.
Story 3.3(P2-03, FR-8)가 ``MessageRenderService.render_message``/``Message`` (렌더 레코드 +
안정적 ``text_hash``)를 ``render`` 옆에 additive로 추가했다(``Message`` 는 domain 소속이라
재노출 심볼 변화 없음).
Story 3.4(P2-04, FR-9)가 ``DispatchFanoutService``/``DispatchJob``/``FanoutOutcome``
(한 Message → N 채널 fan-out + 채널 격리 전송)을 ``DispatchService`` 옆에 additive로 추가했다
(``DispatchJob``/``FanoutOutcome`` 은 ``DispatchResult`` 선례를 따라 services 소속 값 객체다).
Story 3.5(P2-05, FR-10, ADD-5)가 ``IdempotentDeliveryService``(``build_dedup_key``=5필드
dedup key, ``deliver_once``=insert-then-send + ``DeliveryLog`` 생성)를 additive로 추가했다
(architecture 428 ``idempotency.py`` 정본 — ``DeliveryLog``/``DeliveryStatus`` 는 domain
소속이라 여기서 재노출하지 않는다).
Story 3.6(P2-06, FR-11·26)이 ``DeliveryFailurePolicy``(실패 분류·error_code별 backoff 재시도
결정·parser 반복 실패 경고·``attempt_delivery``=``deliver_once`` 위 실패-인지 전송)와 그
값 객체 ``RetryDecision``/``DeliveryAttemptResult`` 를 additive로 추가했다 —
``FailureCategory``/``DeliveryStatus`` 신규 멤버는 domain 소속이라 여기서 재노출하지 않는다.
Story 3.7(P2-07, FR-24·FR-26, ADD-11)이 ``CentralTelegramSender``(중앙 send-only Telegram
어댑터 — legacy ``send_telegram_text`` 재사용·transport/token 주입·``dispatch_all``/
``attempt_delivery`` 의 ``send`` seam 제공)·``TelegramRoute``(전송 scope=(chat_id, thread_id))·
활성 토픽 충돌 검출(``find_telegram_topic_collisions``/``assert_unique_telegram_topics`` +
``TelegramTopicCollisionError``)을 additive로 추가했다 — 인바운드 webhook/``/register``·async
dispatcher·실제 ``DeliveryLog`` 영속은 Epic 5 소유다(``TelegramRoute`` 는 services 값 객체라
domain 재노출 변화 없음).
``pythonpath = ["src"]`` 덕분에 별도 설치 없이
``from rider_server.services import SubscriptionGate`` 가 동작한다.
"""

from __future__ import annotations

from .crawl_service import CrawlService
from .delivery_failure_policy import (
    DeliveryAttemptResult,
    DeliveryFailurePolicy,
    RetryDecision,
)
from .dispatch_fanout_service import (
    DispatchFanoutService,
    DispatchJob,
    FanoutOutcome,
)
from .dispatch_service import DispatchResult, DispatchService
from .idempotency import IdempotentDeliveryService
from .message_render_service import MessageRenderService
from .snapshot_normalizer import MissingSnapshotDataError, SnapshotNormalizer
from .telegram_central_dispatch import (
    CentralTelegramSender,
    TelegramRoute,
    TelegramTopicCollisionError,
    assert_unique_telegram_topics,
    find_telegram_topic_collisions,
)
from .channel_registration import (
    ALLOWED_CHANNEL_TRANSITIONS,
    ChannelNotFoundError,
    ChannelRegistrationService,
    ChannelRepository,
    InMemoryChannelRepository,
    InvalidChannelTransition,
    KakaoRoomCollisionError,
    RegisterResult,
    assert_channel_transition,
    assert_unique_kakao_rooms,
    find_kakao_room_collisions,
    is_allowed_channel_transition,
    is_operational,
    operational_channels,
    operational_delivery_rules,
)
from .subscription_gate import (
    DispatchJobStatus,
    GateDecision,
    HeldDisposition,
    SubscriptionGate,
    SubscriptionStateChange,
)

__all__ = [
    "SubscriptionGate",
    "GateDecision",
    "DispatchJobStatus",
    "HeldDisposition",
    "SubscriptionStateChange",
    "CrawlService",
    "MessageRenderService",
    "DispatchService",
    "DispatchResult",
    "DispatchFanoutService",
    "DispatchJob",
    "FanoutOutcome",
    "SnapshotNormalizer",
    "MissingSnapshotDataError",
    "IdempotentDeliveryService",
    "DeliveryFailurePolicy",
    "RetryDecision",
    "DeliveryAttemptResult",
    "CentralTelegramSender",
    "TelegramRoute",
    "TelegramTopicCollisionError",
    "find_telegram_topic_collisions",
    "assert_unique_telegram_topics",
    # Story 5.5 — 채널 등록/검증/활성 lifecycle + 운영 전송 게이트 + Kakao 방명 고유성
    "ChannelRegistrationService",
    "ChannelRepository",
    "InMemoryChannelRepository",
    "RegisterResult",
    "ChannelNotFoundError",
    "InvalidChannelTransition",
    "ALLOWED_CHANNEL_TRANSITIONS",
    "is_allowed_channel_transition",
    "assert_channel_transition",
    "is_operational",
    "operational_channels",
    "operational_delivery_rules",
    "KakaoRoomCollisionError",
    "find_kakao_room_collisions",
    "assert_unique_kakao_rooms",
]
