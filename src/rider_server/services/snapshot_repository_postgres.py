"""PostgreSQL snapshot ingest repository."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)
from rider_crawl.redaction import redact
from rider_server.db.models.agent import Job as JobRow
from rider_server.db.models.messaging import (
    DeliveryLog as DeliveryLogRow,
    DeliveryRule as DeliveryRuleRow,
    Message as MessageRow,
    MessengerChannel as MessengerChannelRow,
    Snapshot as SnapshotRow,
)
from rider_server.domain import (
    DeliveryLog,
    DeliveryStatus,
    FailureCategory,
    Message,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
)
from rider_server.queue import (
    COMPLETE_ACCEPTED,
    COMPLETE_LEASE_LOST,
    COMPLETE_NOT_FOUND,
    CompleteOutcome,
)
from rider_server.queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_KAKAO_SEND,
    assert_transition,
)
from rider_server.services.idempotency import IdempotentDeliveryService
from rider_server.services.message_render_service import MessageRenderService

from .delivery_failure_policy import DeliveryFailurePolicy
from .dispatch_fanout_service import DispatchJob
from .job_result_ingest_service import JobResultIngestService, SnapshotIngestRecord

TelegramSender = Callable[[MessengerChannel, DispatchJob, str], None]


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _snapshot_id_for_job(job_id: str) -> uuid.UUID:
    """Stable snapshot id for a completed crawl job."""

    return uuid.uuid5(uuid.NAMESPACE_URL, f"rider-result-monitoring:snapshot:{job_id}")


def _message_id_for_job(job_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"rider-result-monitoring:message:{job_id}")


def _delivery_log_id_for_job(job_id: str, channel_id: str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"rider-result-monitoring:delivery-log:{job_id}:{channel_id}",
    )


def _dispatch_job_id_for_job(job_id: str, channel_id: str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"rider-result-monitoring:dispatch-job:{job_id}:{channel_id}",
    )


class PostgresSnapshotIngestRepository(JobResultIngestService):
    """Persist prepared Agent snapshot ingest records to ``snapshots``."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        telegram_sender: TelegramSender | None = None,
    ) -> None:
        super().__init__(save_snapshot=self.save_snapshot)
        self._session_factory = session_factory
        self._telegram_sender = telegram_sender

    async def save_snapshot(self, record: SnapshotIngestRecord) -> None:
        async with self._session_factory() as session:
            await session.execute(
                insert(SnapshotRow).values(
                    id=_snapshot_id_for_job(record.job_id),
                    target_id=_uuid(record.target_id),
                    collected_at=record.collected_at,
                    normalized_json=record.normalized_json,
                    parser_version=record.parser_version,
                    quality_state=record.quality_state,
                )
            )
            await session.commit()

    async def complete_snapshot_job(
        self,
        record: SnapshotIngestRecord,
        *,
        agent_id: str,
        status: str,
        result_json: dict | None,
        error_code: str | None,
        now: datetime,
    ) -> CompleteOutcome:
        """Atomically complete a crawl job and persist its downstream records."""

        job_uuid = _uuid(record.job_id)
        agent_uuid = _uuid(agent_id)
        snapshot_id = _snapshot_id_for_job(record.job_id)
        message_id = _message_id_for_job(record.job_id)
        message = _message_from_record(record, snapshot_id=snapshot_id, message_id=message_id)

        async with self._session_factory() as session:
            async with session.begin():
                job = (
                    await session.execute(
                        select(JobRow).where(JobRow.id == job_uuid).with_for_update()
                    )
                ).scalar_one_or_none()
                if job is None:
                    return CompleteOutcome(COMPLETE_NOT_FOUND, record.job_id)
                owner_mismatch = job.agent_id != agent_uuid
                not_in_flight = job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
                expired = job.lease_expires_at is None or now >= job.lease_expires_at
                if owner_mismatch or not_in_flight or expired:
                    return CompleteOutcome(COMPLETE_LEASE_LOST, record.job_id)

                assert_transition(job.status, status)
                job.status = status
                job.result_json = result_json
                job.error_code = error_code
                job.lease_expires_at = None

                await session.execute(
                    insert(SnapshotRow).values(
                        id=snapshot_id,
                        target_id=_uuid(record.target_id),
                        collected_at=record.collected_at,
                        normalized_json=record.normalized_json,
                        parser_version=record.parser_version,
                        quality_state=record.quality_state,
                    )
                )
                await session.execute(
                    insert(MessageRow).values(
                        id=message_id,
                        snapshot_id=snapshot_id,
                        template_version=message.template_version,
                        text_hash=message.text_hash,
                        text_redacted_preview=message.text_redacted_preview,
                    )
                )
                await self._enqueue_dispatch_records(
                    session,
                    record=record,
                    message=message,
                    message_id=message_id,
                    now=now,
                )

        return CompleteOutcome(COMPLETE_ACCEPTED, record.job_id, final_status=status)

    async def _enqueue_dispatch_records(
        self,
        session: AsyncSession,
        *,
        record: SnapshotIngestRecord,
        message: Message,
        message_id: uuid.UUID,
        now: datetime,
    ) -> None:
        rows = (
            await session.execute(
                select(DeliveryRuleRow, MessengerChannelRow)
                .join(MessengerChannelRow, DeliveryRuleRow.channel_id == MessengerChannelRow.id)
                .where(
                    DeliveryRuleRow.target_id == _uuid(record.target_id),
                    DeliveryRuleRow.enabled.is_(True),
                    MessengerChannelRow.tenant_id == _uuid(record.tenant_id),
                    MessengerChannelRow.state == "ACTIVE",
                )
            )
        ).all()

        for rule, channel in rows:
            channel_id = str(channel.id)
            dedup_key = IdempotentDeliveryService.build_dedup_key(
                target_id=record.target_id,
                channel_id=channel_id,
                collected_at=record.collected_at,
                template_version=message.template_version,
                message_hash=message.text_hash,
            )
            log_id = _delivery_log_id_for_job(record.job_id, channel_id)
            status = DeliveryStatus.RETRYING.value
            error_code = None
            sent_at = None
            if channel.messenger == Messenger.TELEGRAM.value:
                if self._telegram_sender is None:
                    status = DeliveryStatus.HELD.value
                    error_code = FailureCategory.TELEGRAM_FAILURE.value

            await session.execute(
                insert(DeliveryLogRow).values(
                    id=log_id,
                    message_id=message_id,
                    channel_id=channel.id,
                    status=status,
                    dedup_key=dedup_key,
                    error_code=error_code,
                    sent_at=sent_at,
                )
            )

            if channel.messenger == Messenger.TELEGRAM.value:
                if self._telegram_sender is not None:
                    job = DispatchJob(
                        id=str(_dispatch_job_id_for_job(record.job_id, channel_id)),
                        target_id=record.target_id,
                        channel_id=channel_id,
                        message_id=message.id,
                        messenger=Messenger.TELEGRAM,
                        template_version=message.template_version,
                        message_hash=message.text_hash,
                    )
                    log = await _attempt_telegram_delivery(
                        channel=_channel_to_domain(channel),
                        job=job,
                        message=message,
                        log_id=str(log_id),
                        collected_at=record.collected_at,
                        now=now,
                        send=self._telegram_sender,
                    )
                    await session.execute(
                        update(DeliveryLogRow)
                        .where(DeliveryLogRow.id == log_id)
                        .values(
                            status=log.status.value,
                            error_code=log.error_code,
                            sent_at=log.sent_at,
                        )
                    )
                continue

            if channel.messenger != Messenger.KAKAO.value:
                continue
            await session.execute(
                insert(JobRow).values(
                    id=_dispatch_job_id_for_job(record.job_id, channel_id),
                    type=JOB_TYPE_KAKAO_SEND,
                    target_id=_uuid(record.target_id),
                    agent_id=None,
                    status=JOB_STATUS_PENDING,
                    run_after=now,
                    attempts=0,
                    error_code=None,
                    payload_json={
                        "delivery_log_id": str(log_id),
                        "message_id": str(message_id),
                        "channel_id": channel_id,
                        "kakao_room_name": channel.kakao_room_name or "",
                        "message": message.text,
                    },
                    lease_expires_at=None,
                    claimed_at=None,
                    result_json=None,
                )
            )


async def _attempt_telegram_delivery(
    *,
    channel: MessengerChannel,
    job: DispatchJob,
    message: Message,
    log_id: str,
    collected_at: datetime,
    now: datetime,
    send: TelegramSender,
) -> DeliveryLog:
    """Send one Telegram dispatch outside the event loop and return its log values."""

    def _send(dispatch_job: DispatchJob) -> None:
        send(channel, dispatch_job, message.text)

    result = await asyncio.to_thread(
        DeliveryFailurePolicy.attempt_delivery,
        job,
        collected_at=collected_at,
        reserve=lambda _key: True,
        send=_send,
        release=lambda _key: None,
        classify=lambda _exc: DeliveryFailurePolicy.channel_failure_category(Messenger.TELEGRAM),
        log_id_for=lambda _job: log_id,
        sent_at=now,
        attempt=1,
        max_attempts=3,
    )
    return result.log


def _channel_to_domain(row: MessengerChannelRow) -> MessengerChannel:
    return MessengerChannel(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        messenger=Messenger(row.messenger),
        telegram_chat_id=row.telegram_chat_id,
        thread_id=row.thread_id,
        kakao_room_name=row.kakao_room_name,
        state=MessengerChannelState(row.state),
    )


def _message_from_record(
    record: SnapshotIngestRecord,
    *,
    snapshot_id: uuid.UUID,
    message_id: uuid.UUID,
) -> Message:
    raw = _snapshot_result_from_record(record)
    if raw is not None:
        return MessageRenderService.render_message(
            raw,
            message_id=str(message_id),
            snapshot_id=str(snapshot_id),
            source_label=_source_label(record),
            now=record.collected_at,
        )

    text = json.dumps(record.normalized_json, ensure_ascii=False, sort_keys=True)
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Message(
        id=str(message_id),
        snapshot_id=str(snapshot_id),
        template_version=f"{record.platform.casefold()}.realtime.v1",
        text=text,
        text_hash=text_hash,
        text_redacted_preview=redact(text)[:500],
    )


def _snapshot_result_from_record(
    record: SnapshotIngestRecord,
) -> CurrentScreenSnapshot | PerformanceSnapshot | None:
    try:
        if record.platform.casefold() == "baemin":
            return CurrentScreenSnapshot(**record.normalized_json)
        if record.platform.casefold() == "coupang":
            peak = dict(record.normalized_json.get("peak_dashboard") or {})
            current = record.normalized_json.get("current_screen")
            return PerformanceSnapshot(
                current_screen=CurrentScreenSnapshot(**current) if isinstance(current, dict) else None,
                peak_dashboard=PeakDashboardSnapshot(
                    updated_at=peak["updated_at"],
                    assigned_count=peak["assigned_count"],
                    processed_count=peak["processed_count"],
                    reject_rate=peak["reject_rate"],
                    morning=_peak_period(peak["morning"]),
                    lunch_peak=_peak_period(peak["lunch_peak"]),
                    lunch_non_peak=_peak_period(peak["lunch_non_peak"]),
                    dinner_peak=_peak_period(peak["dinner_peak"]),
                    dinner_non_peak=_peak_period(peak["dinner_non_peak"]),
                ),
            )
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _peak_period(value: object) -> PeakPeriodSnapshot:
    raw = value if isinstance(value, dict) else {}
    return PeakPeriodSnapshot(done=raw["done"], total=raw["total"])


def _source_label(record: SnapshotIngestRecord) -> str:
    if record.platform.casefold() == "baemin":
        return str(record.normalized_json.get("center_name") or "")
    current = record.normalized_json.get("current_screen")
    if isinstance(current, dict):
        return str(current.get("center_name") or "")
    return ""
