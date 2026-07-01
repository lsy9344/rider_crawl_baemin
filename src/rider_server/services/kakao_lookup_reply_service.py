"""RIDER_LOOKUP completion → one scoped KAKAO_SEND reply (Phase 4).

When a RIDER_LOOKUP job completes, the server sends exactly one Kakao reply to
the requesting room — the rendered result on success, or one fixed failure line
on failure — re-checking the global send gate and channel state first. This is
wired as an ``on_completed`` hook on ``JobCompletionService`` so the completion
workflow stays unaware of Kakao. RIDER_LOOKUP never enters snapshot ingest or
delivery fanout (its result_type is not "snapshot").

The reply decision is a pure function over the already-loaded gate/state booleans
(exhaustively testable); the async dispatcher only resolves those booleans and
enqueues. ``reply_text`` (name + phone suffix) is used solely to build the
KAKAO_SEND payload — never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from inspect import isawaitable
from typing import Any, Awaitable, Callable

from ..queue.states import JOB_STATUS_SUCCEEDED, JOB_TYPE_KAKAO_SEND

RESULT_TYPE_RIDER_LOOKUP = "rider_lookup"
RESULT_TYPE_RIDER_LOOKUP_FAILED = "rider_lookup_failed"
LOOKUP_RESULT_TYPES = (RESULT_TYPE_RIDER_LOOKUP, RESULT_TYPE_RIDER_LOOKUP_FAILED)

ORIGIN_KAKAO_INBOUND = "kakao_inbound"
# Fixed failure reply — no raw exception text ever reaches the room.
LOOKUP_FAILURE_REPLY = "조회 중 오류가 발생했습니다."


@dataclass(frozen=True)
class LookupReply:
    kakao_room_name: str
    message: str
    origin_event_key: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "kakao_room_name": self.kakao_room_name,
            "message": self.message,
            "origin": ORIGIN_KAKAO_INBOUND,
            "origin_event_key": self.origin_event_key,
        }


def is_lookup_result(result_json: Any) -> bool:
    return isinstance(result_json, dict) and result_json.get("result_type") in LOOKUP_RESULT_TYPES


def decide_lookup_reply(
    *,
    status: str,
    result_json: dict[str, Any] | None,
    sending_enabled: bool,
    channel_active: bool,
) -> LookupReply | None:
    """Pure decision: the single Kakao reply for a completed RIDER_LOOKUP, or None.

    Fail-closed: no reply unless it is a rider-lookup result, the room is known,
    and both the global send gate and the channel are still active. Success with
    a rendered ``reply_text`` sends that text; anything else (failure result, or a
    success missing text) sends the fixed failure line.
    """

    if not is_lookup_result(result_json):
        return None
    assert isinstance(result_json, dict)  # narrowed by is_lookup_result
    room = str(result_json.get("reply_kakao_room_name") or "").strip()
    if not room:
        return None
    if not sending_enabled or not channel_active:
        return None

    origin_event_key = str(result_json.get("origin_event_key") or "")
    message: str | None = None
    if (
        result_json.get("result_type") == RESULT_TYPE_RIDER_LOOKUP
        and status == JOB_STATUS_SUCCEEDED
    ):
        message = str(result_json.get("reply_text") or "").strip() or None
    if message is None:
        message = LOOKUP_FAILURE_REPLY
    return LookupReply(kakao_room_name=room, message=message, origin_event_key=origin_event_key)


async def _maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


class KakaoLookupReplyService:
    """Async ``on_completed`` hook: resolve gates, then enqueue one KAKAO_SEND."""

    def __init__(
        self,
        *,
        queue_backend: Any,
        sending_enabled: Callable[[], Awaitable[bool] | bool],
        channel_active: Callable[[str], Awaitable[bool] | bool],
    ) -> None:
        self._queue_backend = queue_backend
        self._sending_enabled = sending_enabled
        self._channel_active = channel_active

    async def on_job_completed(
        self,
        *,
        job_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        now: datetime,
    ) -> str | None:
        if not is_lookup_result(result_json):
            return None
        assert isinstance(result_json, dict)
        channel_id = str(result_json.get("reply_channel_id") or "").strip()

        sending = bool(await _maybe_await(self._sending_enabled()))
        active = bool(await _maybe_await(self._channel_active(channel_id))) if channel_id else False

        reply = decide_lookup_reply(
            status=status,
            result_json=result_json,
            sending_enabled=sending,
            channel_active=active,
        )
        if reply is None:
            return None
        return await self._queue_backend.enqueue(
            job_type=JOB_TYPE_KAKAO_SEND,
            target_id=None,
            payload_json=reply.to_payload(),
            now=now,
        )
