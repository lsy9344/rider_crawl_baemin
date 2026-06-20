"""Delivery-log based Telegram dispatch worker primitives.

First integration point: call ``TelegramDispatchWorker.run_once()`` from an
explicit worker loop or scheduler-maintenance command. It is intentionally not
wired as a hidden FastAPI request-process background task.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.messaging import (
    DeliveryLog as DeliveryLogRow,
    Message as MessageRow,
    MessengerChannel as MessengerChannelRow,
    Snapshot as SnapshotRow,
)
from rider_server.domain import (
    DeliveryStatus,
    FailureCategory,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
)
from rider_server.services.delivery_failure_policy import DeliveryFailurePolicy
from rider_server.services.dispatch_fanout_service import DispatchJob
from rider_server.services.telegram_central_dispatch import is_ambiguous_send_failure

TelegramSender = Callable[[MessengerChannel, DispatchJob, str], None]


@dataclass(frozen=True)
class ClaimedTelegramDelivery:
    log_id: uuid.UUID
    channel: MessengerChannel
    job: DispatchJob
    message_text: str
    collected_at: datetime
    attempt_count: int


@dataclass(frozen=True)
class DeliveryLogUpdate:
    status: str
    error_code: str | None
    sent_at: datetime | None
    available_at: datetime | None
    attempt_count: int
    locked_at: datetime | None
    locked_by: str | None
    last_failed_at: datetime | None = None


class TelegramDispatchWorker:
    """Process claimed Telegram delivery log rows.

    Database claiming/updating can wrap this primitive. The send attempt itself
    stays small and reuses ``DeliveryFailurePolicy`` instead of duplicating
    retry/backoff rules.
    """

    def __init__(
        self,
        *,
        telegram_sender: TelegramSender,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        worker_id: str = "telegram-dispatch-worker",
        batch_size: int = 50,
        max_attempts: int = 3,
        lock_timeout_seconds: int = 300,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if lock_timeout_seconds < 1:
            raise ValueError("lock_timeout_seconds must be positive")
        self._telegram_sender = telegram_sender
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._lock_timeout = timedelta(seconds=lock_timeout_seconds)

    async def run_once(self, *, now: datetime) -> int:
        await self.hold_stale_sending(now=now)
        deliveries = await self.claim_pending(now=now)
        for delivery in deliveries:
            await self.mark_send_started(delivery.log_id, now=now)
            update_values = await self.attempt_delivery(delivery, now=now)
            await self.apply_update(delivery.log_id, update_values)
        return len(deliveries)

    async def hold_stale_sending(self, *, now: datetime) -> int:
        if self._session_factory is None:
            return 0
        async with self._session_factory() as session:
            result = await session.execute(
                update(DeliveryLogRow)
                .where(
                    DeliveryLogRow.status == DeliveryStatus.SENDING.value,
                    DeliveryLogRow.locked_at.is_not(None),
                    DeliveryLogRow.locked_at <= now - self._lock_timeout,
                )
                .values(
                    status=DeliveryStatus.HELD.value,
                    error_code=FailureCategory.TELEGRAM_FAILURE.value,
                    available_at=None,
                    locked_at=None,
                    locked_by=None,
                    last_failed_at=now,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def claim_pending(self, *, now: datetime) -> list[ClaimedTelegramDelivery]:
        if self._session_factory is None:
            return []
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        select(DeliveryLogRow, MessageRow, SnapshotRow, MessengerChannelRow)
                        .join(MessageRow, DeliveryLogRow.message_id == MessageRow.id)
                        .join(SnapshotRow, MessageRow.snapshot_id == SnapshotRow.id)
                        .join(
                            MessengerChannelRow,
                            DeliveryLogRow.channel_id == MessengerChannelRow.id,
                        )
                        .where(
                            DeliveryLogRow.status == DeliveryStatus.RETRYING.value,
                            MessengerChannelRow.messenger == Messenger.TELEGRAM.value,
                            MessengerChannelRow.state == MessengerChannelState.ACTIVE.value,
                            or_(
                                DeliveryLogRow.locked_at.is_(None),
                                DeliveryLogRow.locked_at <= now - self._lock_timeout,
                            ),
                            or_(
                                DeliveryLogRow.available_at.is_(None),
                                DeliveryLogRow.available_at <= now,
                            ),
                        )
                        .order_by(
                            DeliveryLogRow.available_at.asc().nullsfirst(),
                            DeliveryLogRow.id,
                        )
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True, of=DeliveryLogRow)
                    )
                ).all()
                deliveries = [
                    self._delivery_from_rows(log, message, snapshot, channel)
                    for log, message, snapshot, channel in rows
                ]
                for delivery in deliveries:
                    await session.execute(
                        update(DeliveryLogRow)
                        .where(DeliveryLogRow.id == delivery.log_id)
                        .values(
                            locked_at=now,
                            locked_by=self._worker_id,
                        )
                    )
                return deliveries

    async def mark_send_started(self, log_id: uuid.UUID, *, now: datetime) -> None:
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            result = await session.execute(
                update(DeliveryLogRow)
                .where(
                    DeliveryLogRow.id == log_id,
                    DeliveryLogRow.locked_by == self._worker_id,
                    DeliveryLogRow.status == DeliveryStatus.RETRYING.value,
                )
                .values(
                    status=DeliveryStatus.SENDING.value,
                    send_attempted_at=now,
                    locked_at=now,
                )
            )
            await session.commit()
            if int(result.rowcount or 0) != 1:
                raise RuntimeError("delivery log send-start ownership was lost")

    async def apply_update(
        self,
        log_id: uuid.UUID,
        update: DeliveryLogUpdate,
    ) -> None:
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            result = await session.execute(
                update(DeliveryLogRow)
                .where(
                    DeliveryLogRow.id == log_id,
                    DeliveryLogRow.locked_by == self._worker_id,
                    DeliveryLogRow.status == DeliveryStatus.SENDING.value,
                )
                .values(
                    status=update.status,
                    error_code=update.error_code,
                    sent_at=update.sent_at,
                    last_failed_at=update.last_failed_at,
                    available_at=update.available_at,
                    attempt_count=update.attempt_count,
                    locked_at=update.locked_at,
                    locked_by=update.locked_by,
                )
            )
            await session.commit()
            if int(result.rowcount or 0) != 1:
                raise RuntimeError("delivery log update ownership was lost")

    async def attempt_delivery(
        self,
        delivery: ClaimedTelegramDelivery,
        *,
        now: datetime,
    ) -> DeliveryLogUpdate:
        attempt = max(0, delivery.attempt_count) + 1
        error_code = FailureCategory.TELEGRAM_FAILURE.value
        try:
            await asyncio.to_thread(
                self._telegram_sender,
                delivery.channel,
                delivery.job,
                delivery.message_text,
            )
        except Exception as exc:
            if is_ambiguous_send_failure(exc):
                return DeliveryLogUpdate(
                    status=DeliveryStatus.HELD.value,
                    error_code=error_code,
                    sent_at=None,
                    available_at=None,
                    attempt_count=attempt,
                    locked_at=None,
                    locked_by=None,
                    last_failed_at=now,
                )

            decision = DeliveryFailurePolicy.decide(
                category=DeliveryFailurePolicy.channel_failure_category(Messenger.TELEGRAM),
                attempt=attempt,
                max_attempts=self._max_attempts,
            )
            available_at = (
                now + timedelta(seconds=decision.delay_seconds)
                if decision.delay_seconds is not None
                else None
            )
            return DeliveryLogUpdate(
                status=decision.status.value,
                error_code=error_code,
                sent_at=None,
                available_at=available_at,
                attempt_count=attempt,
                locked_at=None,
                locked_by=None,
                last_failed_at=now,
            )

        return DeliveryLogUpdate(
            status=DeliveryStatus.SENT.value,
            error_code=None,
            sent_at=now,
            available_at=None,
            attempt_count=attempt,
            locked_at=None,
            locked_by=None,
            last_failed_at=None,
        )

    @staticmethod
    def _delivery_from_rows(
        log: DeliveryLogRow,
        message: MessageRow,
        snapshot: SnapshotRow,
        channel: MessengerChannelRow,
    ) -> ClaimedTelegramDelivery:
        domain_channel = MessengerChannel(
            id=str(channel.id),
            tenant_id=str(channel.tenant_id),
            messenger=Messenger(channel.messenger),
            telegram_chat_id=channel.telegram_chat_id,
            thread_id=channel.thread_id,
            kakao_room_name=channel.kakao_room_name,
            state=MessengerChannelState(channel.state),
        )
        job = DispatchJob(
            id=str(log.id),
            target_id=str(snapshot.target_id),
            channel_id=str(channel.id),
            message_id=str(message.id),
            messenger=Messenger.TELEGRAM,
            template_version=message.template_version,
            message_hash=message.text_hash,
        )
        return ClaimedTelegramDelivery(
            log_id=log.id,
            channel=domain_channel,
            job=job,
            message_text=message.text,
            collected_at=snapshot.collected_at,
            attempt_count=log.attempt_count,
        )
