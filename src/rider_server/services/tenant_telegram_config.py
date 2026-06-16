"""tenant 별 텔레그램 설정 조회(0012) — 봇 토큰·webhook secret·실발송 게이트.

Admin UI 가 ``tenants`` 행에 평문 저장한 tenant 별 텔레그램 설정을 런타임 경로(중앙 전송·webhook·
send 게이트)에서 읽기 위한 **읽기 전용** provider 다. 쓰기는 5.11 ``AdminEntityService`` 소유이고
여기서는 조회만 한다(상태 전이/INSERT/UPDATE 0).

async 경계: ``rider_server/**`` 는 async-only 라 조회는 async ``get``/``list_active`` 로 노출한다.
중앙 전송 경로(``CentralTelegramSender``)는 ``asyncio.to_thread`` 워커(=러닝 루프 없음)에서 sync
콜백으로 호출되므로, 그 안에서 ``asyncio.run`` 으로 async 조회를 안전하게 구동할 수 있다(메인
이벤트 루프를 막지 않는다). webhook 라우트는 async 라 직접 ``await`` 한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from rider_server.db.models.tenancy import Tenant as TenantRow


@dataclass(frozen=True)
class TenantTelegramSettings:
    """tenant 한 행의 텔레그램 설정 스냅샷(불변). 토큰/secret 은 평문(redaction 으로 마스킹)."""

    tenant_id: str
    telegram_bot_token: str
    telegram_webhook_secret: str
    sending_enabled: bool


def _uuid(value: str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class TenantTelegramConfigProvider:
    """``tenants`` 에서 tenant 별 텔레그램 설정을 읽는 async 읽기 전용 provider."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def get(self, tenant_id: str) -> TenantTelegramSettings | None:
        """tenant_id 의 텔레그램 설정을 조회한다(없으면 None)."""
        if not tenant_id:
            return None
        try:
            key = _uuid(tenant_id)
        except (ValueError, AttributeError):
            return None
        async with self._session_factory() as session:
            row = (
                await session.execute(select(TenantRow).where(TenantRow.id == key))
            ).scalar_one_or_none()
        if row is None:
            return None
        return TenantTelegramSettings(
            tenant_id=str(row.id),
            telegram_bot_token=row.telegram_bot_token or "",
            telegram_webhook_secret=row.telegram_webhook_secret or "",
            sending_enabled=bool(row.sending_enabled),
        )

    async def list_active_webhook_secrets(self) -> list[str]:
        """비어있지 않은 모든 tenant webhook secret 목록(webhook 검증용).

        단일 webhook 엔드포인트가 본문 파싱 **이전** 에 secret 을 검증해야 하므로(보안 불변식),
        들어온 헤더를 모든 tenant 의 설정 secret 과 상수시간 비교한다(하나라도 일치하면 통과).
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TenantRow.telegram_webhook_secret).where(
                        TenantRow.telegram_webhook_secret != ""
                    )
                )
            ).scalars().all()
        return [s for s in rows if s]
