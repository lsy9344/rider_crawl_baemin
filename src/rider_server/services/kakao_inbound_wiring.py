"""Kakao inbound orchestration wiring — bind the decision service to real DB/queue.

The pure decision core + async orchestration live in
``kakao_inbound_event_service``. This module builds the production data-access
seams over the async session factory and queue backend: KAKAO channel loader,
channel→delivery_rule→target loader (with platform), jobs-based dedupe and
in-flight readers, and the chat_id binder. It reuses the same read patterns as
existing code (``list_delivery_rules``/``get_target_platform`` joins, jobs JSONB
filter) **without importing or modifying** the protected ``postgres_queue``
module — reads go through fresh sessions, enqueue goes through the injected
``QueueBackend.enqueue`` seam.

DB-less mode (no session factory, e.g. dev/tests without Postgres) yields
fail-closed empty loaders: every event is rejected as ``unknown_room``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, update

from ..db.models.account import MonitoringTarget as MonitoringTargetRow
from ..db.models.account import PlatformAccount as PlatformAccountRow
from ..db.models.agent import Job
from ..db.models.messaging import DeliveryRule as DeliveryRuleRow
from ..db.models.messaging import MessengerChannel as MessengerChannelRow
from ..queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RETRY,
    JOB_STATUS_RUNNING,
    JOB_TYPE_RIDER_LOOKUP,
)
from .kakao_inbound_event_service import (
    MESSENGER_KAKAO,
    ChannelView,
    KakaoInboundEventService,
    TargetView,
)

# A RIDER_LOOKUP occupying any non-terminal state counts as in-flight for a
# target (mirrors the ix_jobs_active_crawl_target_type partial-index states).
_IN_FLIGHT_STATUSES = (
    JOB_STATUS_PENDING,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_RETRY,
)


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def build_kakao_inbound_event_service(
    *,
    db_session_factory: Any,
    queue_backend: Any,
    sending_enabled_getter: Callable[[], bool],
) -> KakaoInboundEventService:
    """Assemble a ``KakaoInboundEventService`` bound to the real DB/queue seams."""

    enqueue = _make_enqueue(queue_backend)

    if db_session_factory is None:
        async def _no_channels() -> tuple[ChannelView, ...]:
            return ()

        async def _no_targets(_channel_id: str) -> tuple[TargetView, ...]:
            return ()

        return KakaoInboundEventService(
            load_channels=_no_channels,
            load_targets=_no_targets,
            enqueue=enqueue,
            sending_enabled=sending_enabled_getter,
        )

    async def load_channels() -> list[ChannelView]:
        # All KAKAO channels regardless of state, so the decision core can tell
        # channel_inactive apart from unknown_room.
        stmt = select(MessengerChannelRow).where(
            MessengerChannelRow.messenger == MESSENGER_KAKAO
        )
        async with db_session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [
            ChannelView(
                channel_id=str(row.id),
                tenant_id=str(row.tenant_id),
                messenger=MESSENGER_KAKAO,
                kakao_room_name=row.kakao_room_name,
                kakao_chat_id=row.kakao_chat_id,
                state=row.state,
                command_trigger_enabled=bool(row.command_trigger_enabled),
            )
            for row in rows
        ]

    async def load_targets(channel_id: str) -> list[TargetView]:
        # channel → enabled delivery_rules → monitoring_targets (+ platform join),
        # same join shape as get_target_platform.
        stmt = (
            select(MonitoringTargetRow, PlatformAccountRow.platform)
            .select_from(DeliveryRuleRow)
            .join(
                MonitoringTargetRow,
                (DeliveryRuleRow.target_id == MonitoringTargetRow.id)
                & (DeliveryRuleRow.tenant_id == MonitoringTargetRow.tenant_id),
            )
            .join(
                PlatformAccountRow,
                (MonitoringTargetRow.platform_account_id == PlatformAccountRow.id)
                & (MonitoringTargetRow.tenant_id == PlatformAccountRow.tenant_id),
            )
            .where(
                DeliveryRuleRow.channel_id == _as_uuid(channel_id),
                DeliveryRuleRow.enabled.is_(True),
            )
        )
        async with db_session_factory() as session:
            rows = (await session.execute(stmt)).all()
        return [
            TargetView(
                target_id=str(target.id),
                tenant_id=str(target.tenant_id),
                platform=str(platform),
                platform_account_id=str(target.platform_account_id),
                primary_url=target.url,
                expected_display_name=target.name,
                status=target.status,
                external_id=target.external_id or "",
            )
            for (target, platform) in rows
        ]

    async def is_duplicate(origin_event_key: str) -> bool:
        stmt = (
            select(Job.id)
            .where(
                Job.type == JOB_TYPE_RIDER_LOOKUP,
                Job.payload_json["origin_event_key"].as_string() == origin_event_key,
            )
            .limit(1)
        )
        async with db_session_factory() as session:
            return (await session.execute(stmt)).first() is not None

    async def in_flight(target_id: str) -> bool:
        stmt = (
            select(Job.id)
            .where(
                Job.type == JOB_TYPE_RIDER_LOOKUP,
                Job.target_id == _as_uuid(target_id),
                Job.status.in_(_IN_FLIGHT_STATUSES),
            )
            .limit(1)
        )
        async with db_session_factory() as session:
            return (await session.execute(stmt)).first() is not None

    async def bind_chat_id(channel_id: str, chat_id: str) -> None:
        # Conditional on kakao_chat_id IS NULL: first accepted event wins, later
        # races are no-ops (never overwrite an already-bound chat_id).
        stmt = (
            update(MessengerChannelRow)
            .where(
                MessengerChannelRow.id == _as_uuid(channel_id),
                MessengerChannelRow.kakao_chat_id.is_(None),
            )
            .values(kakao_chat_id=chat_id)
        )
        async with db_session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    return KakaoInboundEventService(
        load_channels=load_channels,
        load_targets=load_targets,
        enqueue=enqueue,
        sending_enabled=sending_enabled_getter,
        bind_chat_id=bind_chat_id,
        is_duplicate=is_duplicate,
        in_flight=in_flight,
    )


def _make_enqueue(queue_backend: Any) -> Callable[..., Any]:
    async def enqueue(*, job_type: str, target_id: str | None, payload_json: dict | None) -> str:
        return await queue_backend.enqueue(
            job_type=job_type,
            target_id=target_id,
            payload_json=payload_json,
            now=datetime.now(timezone.utc),
        )

    return enqueue
