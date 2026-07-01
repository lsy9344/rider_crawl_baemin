"""PostgreSQL ``ChannelRepository`` 구현 — Story 5.5 (AC2·AC3).

:class:`rider_server.services.channel_registration.ChannelRepository` 포트의 실 DB 구현. 5.2
``db/base.py`` 의 ``async_sessionmaker`` 를 그대로 주입받아 쓰고 새 엔진을 만들지 않는다
(``PostgresQueueBackend``/``PostgresSchedulerRepository`` 선례). async 본문은 DB I/O 만 하고
blocking sync 직접 호출은 하지 않는다(async 경계 가드 준수).

ORM(``db.models.messaging.MessengerChannel``) ↔ 순수 domain(``domain.MessengerChannel``)
경계를 명시적으로 변환한다(레이어 분리 — domain 은 SQLAlchemy import 0). ``registration_code``
는 0004 가 추가한 라우팅 컬럼으로 ``/register <code>`` 조회에만 쓰고 domain dataclass 에는 싣지
않는다(secret 아님·운영 코드).
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.messaging import MessengerChannel as MessengerChannelRow
from rider_server.domain import Messenger, MessengerChannel, MessengerChannelState

from .channel_registration import ChannelNotFoundError, ChannelRepository, KakaoRoomCollisionError


def _to_domain(row: MessengerChannelRow) -> MessengerChannel:
    """ORM 행 → 순수 domain ``MessengerChannel`` (문자열 컬럼을 enum 으로 강제)."""

    return MessengerChannel(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        messenger=Messenger(row.messenger),
        telegram_chat_id=row.telegram_chat_id,
        thread_id=row.thread_id,
        kakao_room_name=row.kakao_room_name,
        state=MessengerChannelState(row.state),
        kakao_chat_id=row.kakao_chat_id,
        command_trigger_enabled=bool(row.command_trigger_enabled),
    )


class PostgresChannelRepository(ChannelRepository):
    """async SQLAlchemy 기반 ``ChannelRepository``.

    상태 전이 결정은 :class:`ChannelRegistrationService`(service 레이어)가 하고, 본 repository 는
    그 결과를 영속만 한다(``save`` 는 라우팅 컬럼+state UPDATE; ``registration_code`` 는 보존).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, channel_id: str) -> MessengerChannel | None:
        stmt = select(MessengerChannelRow).where(MessengerChannelRow.id == channel_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def get_by_registration_code(self, code: str) -> MessengerChannel | None:
        stmt = select(MessengerChannelRow).where(
            MessengerChannelRow.registration_code == code
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalars().first()
        return None if row is None else _to_domain(row)

    async def save(self, channel: MessengerChannel) -> None:
        stmt = (
            update(MessengerChannelRow)
            .where(MessengerChannelRow.id == channel.id)
            .values(
                telegram_chat_id=channel.telegram_chat_id,
                thread_id=channel.thread_id,
                kakao_room_name=channel.kakao_room_name,
                state=channel.state.value,
            )
        )
        async with self._session_factory() as session:
            try:
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    raise ChannelNotFoundError(channel.id)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _channel_integrity_error(exc) from exc

    async def active_channels(self) -> list[MessengerChannel]:
        stmt = select(MessengerChannelRow).where(
            MessengerChannelRow.state == MessengerChannelState.ACTIVE.value
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]


def _channel_integrity_error(exc: IntegrityError) -> ValueError:
    text = str(getattr(exc, "orig", exc)).lower()
    if "kakao" in text or "messenger_channels" in text:
        return KakaoRoomCollisionError("활성 Kakao 채널 방명이 중복되었습니다")
    return ValueError("중복된 메시지 채널입니다")
