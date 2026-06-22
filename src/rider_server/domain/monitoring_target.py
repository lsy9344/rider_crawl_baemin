"""``MonitoringTarget`` 도메인 모델(Story 2.5 / AC1·AC3) — 실제 수집 단위.

비활성화는 **물리 삭제가 아니라 상태 전이**다(soft delete, FR-4): 비활성은
``status=MonitoringTargetStatus.INACTIVE`` 로 표현하고, frozen이라 전이는
``dataclasses.replace(target, status=INACTIVE)`` 로 나머지 식별·이력 필드를 보존한 새
인스턴스를 만든다. ``is_deleted`` 플래그나 필드/이력 제거는 쓰지 않는다.

``name`` = 표시명(2.3 ``display_name`` 대응), ``center_name`` = 기대 센터/상점명 검증 정본
(FR-20 쿠팡 검증 — 2.3 중립 ``center_name`` 을 도메인 모델로 승격). 2.1~2.3 ``UiSettings``
중립 필드와의 wiring은 Story 2.7 소유(여기서는 매핑 문서화만).
"""

from __future__ import annotations

from dataclasses import dataclass

from .states import MonitoringTargetStatus


@dataclass(frozen=True)
class MonitoringTarget:
    id: str
    tenant_id: str  # → Tenant
    platform_account_id: str  # → PlatformAccount
    name: str  # 표시명 (2.3 display_name)
    center_name: str  # 기대 센터/상점명 — FR-20 검증 정본 (2.3 center_name)
    external_id: str = ""  # 2.3 target_external_id
    url: str = ""  # 2.3 primary_url
    interval_minutes: int = 0
    schedule_enabled: bool = False  # 전송 허용 시간창 사용 여부(Asia/Seoul HH:MM)
    start_time: str = ""  # 전송 시작 HH:MM
    stop_time: str = ""  # 전송 종료 HH:MM
    status: MonitoringTargetStatus = MonitoringTargetStatus.ACTIVE
