"""14개 ORM 모델 재노출 — Story 5.2 (AC1).

모든 모델을 import 해 ``Base.metadata`` 에 등록한다(Alembic ``target_metadata`` 가 14개를
누락 없이 감지하려면 필수). 14 = domain dataclass 미러 10(tenants·subscriptions·
platform_accounts·monitoring_targets·browser_profiles·messenger_channels·delivery_rules·
snapshots·messages·delivery_logs) + 계약 직접 정의 4(agents·jobs·auth_sessions·audit_logs).

**SecretRef 는 모델이지만 테이블이 아니다**(secret 은 DB 밖 — ``*_ref`` 컬럼만; ``secret_refs``
테이블을 만들지 않는다). ``jobs``/``audit_logs`` 는 도메인 모델 목록엔 없지만 테이블은 있다.
"""

from .account import AuthSession, MonitoringTarget, PlatformAccount
from .agent import Agent, BrowserProfile, Job
from .audit import AuditLog
from .messaging import (
    DeliveryLog,
    DeliveryRule,
    Message,
    MessengerChannel,
    Snapshot,
)
from .tenancy import Subscription, Tenant

__all__ = [
    # domain dataclass 미러 10
    "Tenant",
    "Subscription",
    "PlatformAccount",
    "MonitoringTarget",
    "BrowserProfile",
    "MessengerChannel",
    "DeliveryRule",
    "Snapshot",
    "Message",
    "DeliveryLog",
    # 계약 직접 정의 4
    "Agent",
    "Job",
    "AuthSession",
    "AuditLog",
]
