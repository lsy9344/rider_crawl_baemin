from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from rider_server.admin.dashboard_repository_postgres import PostgresDashboardRepository
from rider_server.db.models.account import AuthSession, MonitoringTarget, PlatformAccount
from rider_server.db.models.agent import Agent, BrowserProfile
from rider_server.db.models.tenancy import Tenant


_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ACC_BAEMIN = "11111111-1111-1111-1111-111111111111"
_ACC_COUPANG = "22222222-2222-2222-2222-222222222222"
_TARGET_BAEMIN = "33333333-3333-3333-3333-333333333333"
_TARGET_COUPANG_INACTIVE = "44444444-4444-4444-4444-444444444444"


class _SqliteDashboardRepository(PostgresDashboardRepository):
    async def _latest_auth_recovery_details(self, session, target_ids):  # type: ignore[override]
        return {}


class _SqliteUuidText(str):
    @property
    def hex(self) -> str:
        return uuid.UUID(self).hex


async def _auth_required_rows_with_inactive_coupang_target():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            for table in (
                Tenant.__table__,
                PlatformAccount.__table__,
                MonitoringTarget.__table__,
                Agent.__table__,
                BrowserProfile.__table__,
                AuthSession.__table__,
            ):
                await conn.run_sync(table.create)

        async with session_factory() as session:
            session.add(
                Tenant(
                    id=uuid.UUID(_TENANT),
                    name="고객",
                    status="ACTIVE",
                    created_at=_NOW,
                )
            )
            session.add(
                PlatformAccount(
                    id=uuid.UUID(_ACC_BAEMIN),
                    tenant_id=uuid.UUID(_TENANT),
                    platform="BAEMIN",
                    label="배민계정",
                    username="vault://baemin-id",
                    password="vault://baemin-pw",
                    auth_state="AUTH_REQUIRED",
                )
            )
            session.add(
                PlatformAccount(
                    id=uuid.UUID(_ACC_COUPANG),
                    tenant_id=uuid.UUID(_TENANT),
                    platform="COUPANG",
                    label="쿠팡계정",
                    username="vault://coupang-id",
                    password="vault://coupang-pw",
                    auth_state="AUTH_REQUIRED",
                )
            )
            session.add(
                MonitoringTarget(
                    id=uuid.UUID(_TARGET_BAEMIN),
                    tenant_id=uuid.UUID(_TENANT),
                    platform_account_id=uuid.UUID(_ACC_BAEMIN),
                    name="표준경기남양주C팀100퍼센트",
                    center_name="남양주C",
                    external_id="",
                    url="",
                    interval_minutes=10,
                    status="ACTIVE",
                )
            )
            session.add(
                MonitoringTarget(
                    id=uuid.UUID(_TARGET_COUPANG_INACTIVE),
                    tenant_id=uuid.UUID(_TENANT),
                    platform_account_id=uuid.UUID(_ACC_COUPANG),
                    name="설정하지 않은 쿠팡",
                    center_name="",
                    external_id="",
                    url="",
                    interval_minutes=10,
                    status="INACTIVE",
                )
            )
            await session.commit()

        repo = _SqliteDashboardRepository(session_factory)
        return await repo.auth_required(tenant_id=_SqliteUuidText(_TENANT))
    finally:
        await engine.dispose()


def test_auth_required_ignores_inactive_targets_even_when_account_requires_auth() -> None:
    rows = asyncio.run(_auth_required_rows_with_inactive_coupang_target())

    assert [(row.target_id, row.platform) for row in rows] == [
        (_TARGET_BAEMIN, "BAEMIN")
    ]
