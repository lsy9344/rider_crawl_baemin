"""로컬 UI 미리보기 서버 — PostgreSQL 없이 in-memory 데이터로 재설계 Admin 을 띄운다.

실행:  PYTHONPATH=src .venv/Scripts/python.exe _bmad-output/.../.working/preview_server.py
열기:  http://127.0.0.1:8011/admin   (인증 seam = OPERATOR 로 통과)

제품 코드가 아니라 UX 검증용 스크립트다(워크스페이스 .working/). 데이터는 전부 가짜이며
tenant_id="" 로 시드해 ?tenant 쿼리 없이 바로 보이게 한다.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")

import uvicorn

from rider_server.admin.dashboard_service import (
    AgentHealthFacts,
    AuthRequiredRow,
    ChannelHealthRow,
    InMemoryDashboardRepository,
    TargetHealthFacts,
)
from rider_server.domain import (
    BaeminAuthState,
    CustomerLifecycleState,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    SecretRef,
    SecretStorageClass,
    Tenant,
)
from rider_server.main import create_app
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.admin_entity_service import (
    AdminEntityService,
    InMemoryAdminEntityRepository,
)
from rider_server.settings import Settings

NOW = datetime.now(timezone.utc)
TN = ""  # 빈 tenant 로 시드 → /admin (쿼리 없이) 바로 표시
REF = SecretRef(ref="vault://preview/handle", storage_class=SecretStorageClass.CENTRAL)


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


# ── 대시보드 read-model(읽기 전용) 시드: 다양한 심각도 ──────────────────────────
dash = InMemoryDashboardRepository()


def target(tid, name, center, platform, interval, success_min, fail=None, auth=None):
    dash.seed_target(TargetHealthFacts(
        target_id=tid, tenant_id=TN, name=name, center_name=center, platform=platform,
        interval_minutes=interval,
        last_success_at=ago(minutes=success_min) if success_min is not None else None,
        last_delivery_at=ago(minutes=success_min) if success_min is not None else None,
        last_failure_code=fail, account_auth_state=auth, lifecycle_state=None,
    ))


# STOPPED(로그인 만료) · STOPPED(센터 미설정) · CRITICAL(stale) · WARNING ×2 · NORMAL ×5
target("t1", "강남교자 2호점", "강남2", "BAEMIN", 5, 8, fail="AUTH_REQUIRED", auth="AUTH_REQUIRED")
target("t2", "신촌치킨마요", "", "COUPANG", 10, 2, fail="TARGET_VALIDATION_FAILURE")
target("t3", "동대문엽기떡볶이 본점", "동대문본점", "BAEMIN", 5, 47, fail="CRAWL_FAILURE")
target("t4", "합정피자스쿨", "합정", "COUPANG", 10, 25)
target("t5", "잠실국밥 본점", "잠실", "BAEMIN", 5, 12)
target("t6", "망원칼국수", "망원", "BAEMIN", 5, 1)
target("t7", "연남곱창", "연남", "BAEMIN", 5, 2)
target("t8", "성수족발", "성수", "COUPANG", 10, 3)
target("t9", "홍대버거 연남점", "연남", "BAEMIN", 5, 1)
target("t10", "이태원타코", "이태원", "COUPANG", 10, 4)

dash.seed_agent(AgentHealthFacts(agent_id="a1", name="PC-A", version="1.4.0",
    last_heartbeat_at=ago(seconds=8), current_job_type="CRAWL_BAEMIN",
    capabilities=("CRAWL_BAEMIN", "CRAWL_COUPANG", "AUTH_CHECK", "KAKAO_SEND")))
dash.seed_agent(AgentHealthFacts(agent_id="a2", name="PC-B", version="1.4.0",
    last_heartbeat_at=ago(seconds=11), current_job_type=None,
    capabilities=("CRAWL_BAEMIN", "CRAWL_COUPANG", "AUTH_CHECK", "KAKAO_SEND")))
dash.seed_channel_health(TN, ChannelHealthRow(kakao_queue_lag_seconds=0, telegram_error_count=0))
dash.seed_auth_required(AuthRequiredRow(tenant_id=TN, target_id="t1", profile_id="강남2", reason="ACCOUNT_AUTH_REQUIRED"))


# ── 엔티티 CRUD(관리 모드 드롭다운) 시드 ───────────────────────────────────────
ent = InMemoryAdminEntityRepository()
ent.seed_tenant(Tenant(id=TN, name="우리회사", status=CustomerLifecycleState.ACTIVE, created_at=NOW))
ent.seed_platform_account(PlatformAccount(id="pa-1", tenant_id=TN, platform=Platform.BAEMIN, label="본사계정(서울권)", username_ref=REF, password_ref=REF, auth_state=BaeminAuthState.ACTIVE))
ent.seed_platform_account(PlatformAccount(id="pa-2", tenant_id=TN, platform=Platform.COUPANG, label="본사계정", username_ref=REF, password_ref=REF, auth_state=BaeminAuthState.ACTIVE))
for tid, name in [("mt-1", "망원칼국수"), ("mt-2", "합정피자스쿨"), ("mt-3", "강남교자 2호점")]:
    ent.seed_monitoring_target(MonitoringTarget(id=tid, tenant_id=TN, platform_account_id="pa-1", name=name, center_name="센터", status=MonitoringTargetStatus.ACTIVE))
ent.seed_messenger_channel(MessengerChannel(id="ch-1", tenant_id=TN, messenger=Messenger.TELEGRAM, telegram_chat_id="-100123456", thread_id="7", state=MessengerChannelState.ACTIVE))
ent.seed_messenger_channel(MessengerChannel(id="ch-2", tenant_id=TN, messenger=Messenger.KAKAO, kakao_room_name="합정 점주방", state=MessengerChannelState.ACTIVE))


# ── 앱 + 인증 seam(OPERATOR 로 통과) ───────────────────────────────────────────
app = create_app(
    Settings(app_env="test", app_version="preview", build_sha=None, build_time=None),
    dashboard_repository=dash,
    admin_entity_service=AdminEntityService(ent),
)
app.state.resolve_admin_principal = lambda request: AdminPrincipal(
    actor_id="00000000-0000-0000-0000-0000000000aa", role=AdminRole.OPERATOR,
    mfa_verified=True, source="PREVIEW/operator",
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8011"))
    print(f"\n  ▶ 미리보기:  http://127.0.0.1:{port}/admin\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
