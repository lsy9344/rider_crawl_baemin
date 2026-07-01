"""Tests for RIDER_LOOKUP completion → scoped KAKAO_SEND reply (Phase 4)."""

import asyncio
from datetime import datetime, timezone

from rider_server.queue.backend import COMPLETE_ACCEPTED, CompleteOutcome
from rider_server.queue.states import (
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_KAKAO_SEND,
)
from rider_server.services.job_completion_service import JobCompletionService
from rider_server.services.kakao_lookup_reply_service import (
    LOOKUP_FAILURE_REPLY,
    KakaoLookupReplyService,
    decide_lookup_reply,
)


_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _success_result(**overrides):
    result = {
        "result_type": "rider_lookup",
        "reply_channel_id": "ch1",
        "reply_kakao_room_name": "운영방",
        "origin_event_key": "sha256:abc",
        "reply_text": "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다.",
    }
    result.update(overrides)
    return result


def _failed_result(**overrides):
    result = {
        "result_type": "rider_lookup_failed",
        "reply_channel_id": "ch1",
        "reply_kakao_room_name": "운영방",
        "origin_event_key": "sha256:abc",
    }
    result.update(overrides)
    return result


# --- pure decision --------------------------------------------------------

def test_success_reply_uses_rendered_text():
    reply = decide_lookup_reply(
        status=JOB_STATUS_SUCCEEDED, result_json=_success_result(),
        sending_enabled=True, channel_active=True,
    )
    assert reply is not None
    assert reply.message == "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다."
    assert reply.to_payload() == {
        "kakao_room_name": "운영방",
        "message": "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다.",
        "origin": "kakao_inbound",
        "origin_event_key": "sha256:abc",
    }


def test_failed_result_uses_fixed_failure_line():
    reply = decide_lookup_reply(
        status=JOB_STATUS_FAILED, result_json=_failed_result(),
        sending_enabled=True, channel_active=True,
    )
    assert reply is not None
    assert reply.message == LOOKUP_FAILURE_REPLY
    assert reply.kakao_room_name == "운영방"


def test_success_without_text_falls_back_to_failure_line():
    reply = decide_lookup_reply(
        status=JOB_STATUS_SUCCEEDED, result_json=_success_result(reply_text=""),
        sending_enabled=True, channel_active=True,
    )
    assert reply is not None
    assert reply.message == LOOKUP_FAILURE_REPLY


def test_no_reply_when_sending_disabled():
    assert decide_lookup_reply(
        status=JOB_STATUS_SUCCEEDED, result_json=_success_result(),
        sending_enabled=False, channel_active=True,
    ) is None


def test_no_reply_when_channel_inactive():
    assert decide_lookup_reply(
        status=JOB_STATUS_SUCCEEDED, result_json=_success_result(),
        sending_enabled=True, channel_active=False,
    ) is None


def test_no_reply_without_room():
    assert decide_lookup_reply(
        status=JOB_STATUS_SUCCEEDED, result_json=_success_result(reply_kakao_room_name=""),
        sending_enabled=True, channel_active=True,
    ) is None


def test_no_reply_for_non_lookup_result():
    assert decide_lookup_reply(
        status=JOB_STATUS_SUCCEEDED, result_json={"result_type": "snapshot"},
        sending_enabled=True, channel_active=True,
    ) is None
    assert decide_lookup_reply(
        status=JOB_STATUS_SUCCEEDED, result_json=None,
        sending_enabled=True, channel_active=True,
    ) is None


# --- async dispatcher -----------------------------------------------------

class _FakeQueue:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, *, job_type, target_id, payload_json, now):
        self.enqueued.append((job_type, target_id, payload_json))
        return "ksend-1"

    async def complete(self, **kwargs):
        return CompleteOutcome(COMPLETE_ACCEPTED, kwargs["job_id"])

    async def in_flight_job(self, **kwargs):
        return None


def _service(queue, *, sending=True, active=True):
    return KakaoLookupReplyService(
        queue_backend=queue,
        sending_enabled=lambda: sending,
        channel_active=lambda _cid: active,
    )


def test_dispatcher_enqueues_kakao_send_on_success():
    queue = _FakeQueue()
    job_id = asyncio.run(
        _service(queue).on_job_completed(
            job_id="j1", status=JOB_STATUS_SUCCEEDED, result_json=_success_result(), now=_NOW
        )
    )
    assert job_id == "ksend-1"
    assert len(queue.enqueued) == 1
    job_type, target_id, payload = queue.enqueued[0]
    assert job_type == JOB_TYPE_KAKAO_SEND
    assert target_id is None
    assert payload["kakao_room_name"] == "운영방"
    assert payload["message"].startswith("강민기1234")
    assert payload["origin_event_key"] == "sha256:abc"


def test_dispatcher_skips_when_gate_closed():
    queue = _FakeQueue()
    result = asyncio.run(
        _service(queue, sending=False).on_job_completed(
            job_id="j1", status=JOB_STATUS_SUCCEEDED, result_json=_success_result(), now=_NOW
        )
    )
    assert result is None
    assert queue.enqueued == []


def test_dispatcher_ignores_non_lookup_result():
    queue = _FakeQueue()
    asyncio.run(
        _service(queue).on_job_completed(
            job_id="j1", status=JOB_STATUS_SUCCEEDED, result_json={"result_type": "snapshot"}, now=_NOW
        )
    )
    assert queue.enqueued == []


def test_dispatcher_skips_when_already_replied():
    queue = _FakeQueue()
    service = KakaoLookupReplyService(
        queue_backend=queue,
        sending_enabled=lambda: True,
        channel_active=lambda _cid: True,
        already_replied=lambda _key: True,  # a KAKAO_SEND for this key already exists
    )
    result = asyncio.run(
        service.on_job_completed(
            job_id="j1", status=JOB_STATUS_SUCCEEDED, result_json=_success_result(), now=_NOW
        )
    )
    assert result is None
    assert queue.enqueued == []


# --- end-to-end: completion fires the hook --------------------------------

def test_job_completion_fires_reply_hook_and_enqueues():
    queue = _FakeQueue()
    reply_service = _service(queue)
    completion = JobCompletionService(
        queue_backend=queue, on_completed=reply_service.on_job_completed
    )
    result = asyncio.run(
        completion.complete(
            job_id="j1", agent_id="agent-1", status=JOB_STATUS_SUCCEEDED,
            result_json=_success_result(), ingest_result_json=_success_result(),
            error_code=None, duration_ms=None, result_schema_version=None, now=_NOW,
        )
    )
    assert result.job_id == "j1"
    assert len(queue.enqueued) == 1
    assert queue.enqueued[0][0] == JOB_TYPE_KAKAO_SEND


def test_reply_hook_failure_does_not_break_completion():
    class _BoomQueue(_FakeQueue):
        async def enqueue(self, **kwargs):
            raise RuntimeError("enqueue down")

    queue = _BoomQueue()
    reply_service = _service(queue)
    completion = JobCompletionService(
        queue_backend=queue, on_completed=reply_service.on_job_completed
    )
    # Completion still succeeds even though the reply enqueue raises.
    result = asyncio.run(
        completion.complete(
            job_id="j1", agent_id="agent-1", status=JOB_STATUS_SUCCEEDED,
            result_json=_success_result(), ingest_result_json=_success_result(),
            error_code=None, duration_ms=None, result_schema_version=None, now=_NOW,
        )
    )
    assert result.job_id == "j1"


def test_retry_outcome_does_not_fire_reply():
    # A failure the queue re-queued for retry returns COMPLETE_ACCEPTED with
    # final_status=PENDING; the hook must NOT fire a premature failure reply.
    class _RetryQueue(_FakeQueue):
        async def complete(self, **kwargs):
            return CompleteOutcome(COMPLETE_ACCEPTED, kwargs["job_id"], final_status="PENDING")

    queue = _RetryQueue()
    reply_service = _service(queue)
    completion = JobCompletionService(
        queue_backend=queue, on_completed=reply_service.on_job_completed
    )
    asyncio.run(
        completion.complete(
            job_id="j1", agent_id="agent-1", status=JOB_STATUS_FAILED,
            result_json=_failed_result(), ingest_result_json=_failed_result(),
            error_code="CDP_UNREACHABLE", duration_ms=None, result_schema_version=None, now=_NOW,
        )
    )
    assert queue.enqueued == []  # retrying, not terminal -> no reply yet
