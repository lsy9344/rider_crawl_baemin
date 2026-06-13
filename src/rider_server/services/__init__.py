"""rider_server 서비스 레이어(정책/전이 로직) — 명시 재노출.

본 패키지의 첫 코드는 Story 2.6의 순수 ``SubscriptionGate`` 게이트 정책이다(FR-6·FR-30).
Story 3.1(Epic 3, P2-01)이 ``run_once`` 분해 결과인 ``CrawlService``/
``MessageRenderService``/``DispatchService`` 를 같은 디렉터리에 additive로 추가했고,
``idempotency``/async wiring은 Story 3.5/Epic 5가 덧붙인다(architecture 425-429).
Story 3.2(P2-02, FR-7)가 ``SnapshotNormalizer``/``MissingSnapshotDataError`` (수집 결과
정규화 ``Snapshot`` 변환 + 필수데이터 fail-closed)를 같은 레이어에 additive로 추가했다.
``pythonpath = ["src"]`` 덕분에 별도 설치 없이
``from rider_server.services import SubscriptionGate`` 가 동작한다.
"""

from __future__ import annotations

from .crawl_service import CrawlService
from .dispatch_service import DispatchResult, DispatchService
from .message_render_service import MessageRenderService
from .snapshot_normalizer import MissingSnapshotDataError, SnapshotNormalizer
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
    "SnapshotNormalizer",
    "MissingSnapshotDataError",
]
