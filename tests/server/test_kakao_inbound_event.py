"""Tests for the Kakao inbound event decision core (Phase 3, pure logic).

These lock the server's mapping/gate/dedupe contract without FastAPI, the DB, or
the queue. The async orchestration + HTTP route are tested separately once wired.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from rider_server.queue.states import JOB_TYPE_KAKAO_SEND, JOB_TYPE_RIDER_LOOKUP
from rider_server.services.kakao_inbound_event_service import (
    ACTION_DUPLICATE,
    ACTION_ENQUEUE_LOOKUP,
    ACTION_REJECT,
    ACTION_REPLY,
    COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
    KakaoInboundEventService,
    LOOKUP_JOB_TYPE,
    REASON_CHANNEL_INACTIVE,
    REASON_COMMAND_DISABLED,
    REASON_INVALID_EVENT,
    REASON_RATE_LIMITED,
    REASON_SENDING_DISABLED,
    REASON_TARGET_UNMAPPED,
    REASON_TENANT_DISABLED,
    REASON_UNKNOWN_ROOM,
    REASON_UNSUPPORTED_PLATFORM,
    ChannelView,
    InboundCommandInput,
    InboundContext,
    InboundEventInput,
    TargetView,
    decide_inbound_event,
    origin_event_key,
)
from rider_server.services.kakao_inbound_wiring import build_kakao_inbound_event_service


def _channel(channel_id="ch1", tenant_id="t1", room="운영방", chat_id=None, state="ACTIVE", enabled=True):
    return ChannelView(
        channel_id=channel_id,
        tenant_id=tenant_id,
        messenger="KAKAO",
        kakao_room_name=room,
        kakao_chat_id=chat_id,
        state=state,
        command_trigger_enabled=enabled,
    )


def _target(target_id="tg1", tenant_id="t1", platform="baemin", status="ACTIVE"):
    return TargetView(
        target_id=target_id,
        tenant_id=tenant_id,
        platform=platform,
        platform_account_id="acc1",
        primary_url="https://deliverycenter.baemin.com/delivery/history",
        expected_display_name="남구센터",
        status=status,
        external_id="ext1",
    )


def _event(chat_id="111", room="운영방", name="강민기", phone="1234", log_id="1002",
           type=COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP, source="pc_kakao_db", digest="sha256:abc"):
    return InboundEventInput(
        source=source,
        kakao_user_hash_digest=digest,
        chat_id=chat_id,
        room_name=room,
        last_log_id=log_id,
        command=InboundCommandInput(type=type, name=name, phone_last4=phone),
    )


def _ctx(channels, targets_by_channel=None, **kw):
    return InboundContext(
        channels=tuple(channels),
        targets_by_channel=targets_by_channel if targets_by_channel is not None else {"ch1": (_target(),)},
        sending_enabled=kw.get("sending_enabled", True),
        existing_event_keys=frozenset(kw.get("existing_event_keys", ())),
        in_flight_target_ids=frozenset(kw.get("in_flight_target_ids", ())),
        inactive_tenant_ids=frozenset(kw.get("inactive_tenant_ids", ())),
        rate_limited_channel_ids=frozenset(kw.get("rate_limited_channel_ids", ())),
    )


# --- command validation ---------------------------------------------------

@pytest.mark.parametrize("command", [
    InboundCommandInput("WRONG_TYPE", "강민기", "1234"),
    InboundCommandInput(COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP, "", "1234"),
    InboundCommandInput(COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP, "강민기", "12"),
    InboundCommandInput(COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP, "강민기", "abcd"),
])
def test_invalid_command_rejected(command):
    event = InboundEventInput("pc_kakao_db", "sha256:abc", "111", "운영방", "1002", command)
    decision = decide_inbound_event(event, _ctx([_channel(chat_id="111")]))

    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_INVALID_EVENT


# --- room / channel mapping ----------------------------------------------

def test_unknown_room_rejected():
    decision = decide_inbound_event(_event(room="없는방", chat_id=""), _ctx([_channel(room="운영방")]))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_UNKNOWN_ROOM


def test_inactive_channel_rejected():
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel(state="PENDING")]))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_CHANNEL_INACTIVE


def test_command_disabled_rejected():
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel(enabled=False)]))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_COMMAND_DISABLED


def test_chat_id_conflict_fails_closed():
    # Stored chat_id differs from inbound chat_id; same room name must NOT match.
    channel = _channel(chat_id="999")
    decision = decide_inbound_event(_event(chat_id="111", room="운영방"), _ctx([channel]))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_UNKNOWN_ROOM


def test_ambiguous_room_across_channels_rejected():
    channels = [_channel(channel_id="ch1", tenant_id="t1"), _channel(channel_id="ch2", tenant_id="t2")]
    targets = {"ch1": (_target(),), "ch2": (_target(target_id="tg2", tenant_id="t2"),)}
    decision = decide_inbound_event(_event(chat_id=""), _ctx(channels, targets))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_UNKNOWN_ROOM


def test_matches_bound_channel_by_chat_id_regardless_of_room():
    # Bound channel matches by chat_id even when the inbound room name differs.
    channel = _channel(chat_id="111", room="저장된방")
    decision = decide_inbound_event(_event(chat_id="111", room="다른표시명"), _ctx([channel]))
    assert decision.action == ACTION_ENQUEUE_LOOKUP


# --- target mapping -------------------------------------------------------

def test_zero_targets_unmapped():
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], {"ch1": ()}))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_TARGET_UNMAPPED


def test_multiple_targets_unmapped():
    targets = {"ch1": (_target(target_id="a"), _target(target_id="b"))}
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], targets))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_TARGET_UNMAPPED


def test_only_active_target_counts():
    targets = {"ch1": (_target(target_id="a", status="INACTIVE"), _target(target_id="b"))}
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], targets))
    assert decision.action == ACTION_ENQUEUE_LOOKUP
    assert decision.target_id == "b"


# --- gates ----------------------------------------------------------------

def test_coupang_platform_enqueues_lookup():
    targets = {"ch1": (_target(platform="coupang"),)}
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], targets))

    assert decision.action == ACTION_ENQUEUE_LOOKUP
    assert decision.accepted is True
    assert decision.job_payload["platform"] == "coupang"


def test_unsupported_platform_enqueues_scoped_reply():
    targets = {"ch1": (_target(platform="naver"),)}
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], targets))
    assert decision.action == ACTION_REPLY
    assert decision.reason == REASON_UNSUPPORTED_PLATFORM
    assert decision.accepted is False
    assert decision.reply_text == "라이더 조회 명령은 배민/쿠팡 탭에서만 지원합니다."
    assert decision.reply_kakao_room_name == "운영방"


def test_tenant_disabled_rejected():
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], inactive_tenant_ids=["t1"]))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_TENANT_DISABLED


def test_sending_disabled_rejected():
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], sending_enabled=False))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_SENDING_DISABLED


def test_rate_limited_rejected():
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], rate_limited_channel_ids=["ch1"]))
    assert decision.action == ACTION_REJECT
    assert decision.reason == REASON_RATE_LIMITED


def test_lookup_in_flight_does_not_drop_distinct_command():
    decision = decide_inbound_event(_event(chat_id=""), _ctx([_channel()], in_flight_target_ids=["tg1"]))
    assert decision.action == ACTION_ENQUEUE_LOOKUP
    assert decision.reason == ""
    assert decision.target_id == "tg1"


# --- dedupe + enqueue -----------------------------------------------------

def test_duplicate_event_is_idempotent_accept():
    event = _event(chat_id="")
    key = origin_event_key(event)
    decision = decide_inbound_event(event, _ctx([_channel()], existing_event_keys=[key]))
    assert decision.action == ACTION_DUPLICATE
    assert decision.accepted is True
    assert decision.duplicate is True


def test_duplicate_event_remains_idempotent_while_lookup_in_flight():
    event = _event(chat_id="")
    key = origin_event_key(event)
    decision = decide_inbound_event(
        event,
        _ctx([_channel()], existing_event_keys=[key], in_flight_target_ids=["tg1"]),
    )
    assert decision.action == ACTION_DUPLICATE
    assert decision.accepted is True
    assert decision.duplicate is True


def test_enqueue_lookup_builds_payload_and_binds_chat_id():
    event = _event(chat_id="111", room="운영방")  # channel unbound -> should bind
    decision = decide_inbound_event(event, _ctx([_channel(chat_id=None)]))

    assert decision.action == ACTION_ENQUEUE_LOOKUP
    assert decision.accepted is True
    assert decision.duplicate is False
    assert decision.bind_chat_id == "111"
    assert LOOKUP_JOB_TYPE == JOB_TYPE_RIDER_LOOKUP

    payload = decision.job_payload
    assert payload["tenant_id"] == "t1"
    assert payload["target_id"] == "tg1"
    assert payload["platform"] == "baemin"
    assert payload["platform_account_id"] == "acc1"
    assert payload["primary_url"].endswith("/delivery/history")
    assert payload["expected_display_name"] == "남구센터"
    assert payload["reply_channel_id"] == "ch1"
    assert payload["reply_messenger"] == "KAKAO"
    assert payload["reply_kakao_room_name"] == "운영방"
    assert payload["origin"] == "kakao_inbound"
    assert payload["origin_event_key"] == decision.origin_event_key
    assert payload["command"] == {"type": COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP, "name": "강민기", "phone_last4": "1234"}
    assert payload["external_id"] == "ext1"


def test_enqueue_does_not_bind_when_channel_already_bound():
    decision = decide_inbound_event(_event(chat_id="111"), _ctx([_channel(chat_id="111")]))
    assert decision.action == ACTION_ENQUEUE_LOOKUP
    assert decision.bind_chat_id == ""


# --- origin_event_key -----------------------------------------------------

def test_origin_event_key_is_deterministic_and_prefixed():
    event = _event(chat_id="111")
    assert origin_event_key(event) == origin_event_key(event)
    assert origin_event_key(event).startswith("sha256:")


def test_origin_event_key_uses_room_name_when_chat_id_absent():
    by_chat = origin_event_key(_event(chat_id="111", room="운영방"))
    by_room = origin_event_key(_event(chat_id="", room="운영방"))
    # Different scope inputs (chat_id vs room) yield different keys.
    assert by_chat != by_room
    # Same room with empty chat_id is stable.
    assert by_room == origin_event_key(_event(chat_id="", room="운영방"))


# --- async orchestration (KakaoInboundEventService.handle) -----------------

def _service(channels, targets_by_channel, *, sending=True, dup=False, in_flight=False,
             tenant_active=True, rate_limited=False, already_replied=None, calls=None,
             observe_decision=None):
    calls = calls if calls is not None else {"enqueued": [], "bound": []}

    async def load_channels():
        return channels

    async def load_targets(channel_id):
        return targets_by_channel.get(channel_id, ())

    async def enqueue(*, job_type, target_id, payload_json):
        calls["enqueued"].append((job_type, target_id, payload_json))
        return "job-123"

    async def bind_chat_id(channel_id, chat_id):
        calls["bound"].append((channel_id, chat_id))

    service = KakaoInboundEventService(
        load_channels=load_channels,
        load_targets=load_targets,
        enqueue=enqueue,
        sending_enabled=lambda: sending,
        bind_chat_id=bind_chat_id,
        is_duplicate=lambda key: dup,
        in_flight=lambda target_id: in_flight,
        tenant_active=lambda tenant_id: tenant_active,
        rate_limited=lambda channel_id: rate_limited,
        already_replied=already_replied,
        observe_decision=observe_decision,
    )
    return service, calls


def test_handle_enqueues_lookup_and_binds_chat_id():
    service, calls = _service([_channel(chat_id=None)], {"ch1": (_target(),)})
    result = asyncio.run(service.handle(_event(chat_id="111", room="운영방")))

    assert result == {"accepted": True, "duplicate": False, "job_id": "job-123"}
    assert len(calls["enqueued"]) == 1
    job_type, target_id, payload = calls["enqueued"][0]
    assert job_type == JOB_TYPE_RIDER_LOOKUP
    assert target_id == "tg1"
    assert payload["command"]["name"] == "강민기"
    assert calls["bound"] == [("ch1", "111")]


def test_handle_stamps_expires_at_on_lookup_payload():
    fixed = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    calls = {"enqueued": [], "bound": []}

    async def load_channels():
        return [_channel(chat_id="111")]

    async def load_targets(channel_id):
        return (_target(),)

    async def enqueue(*, job_type, target_id, payload_json):
        calls["enqueued"].append(payload_json)
        return "job-1"

    service = KakaoInboundEventService(
        load_channels=load_channels,
        load_targets=load_targets,
        enqueue=enqueue,
        sending_enabled=lambda: True,
        now=lambda: fixed,
    )
    asyncio.run(service.handle(_event(chat_id="111")))

    payload = calls["enqueued"][0]
    # timeout_seconds (60) + LOOKUP_TTL_GRACE_SECONDS (60) = +120s
    assert payload["expires_at"] == "2026-07-01T00:02:00Z"


def test_handle_duplicate_does_not_enqueue():
    service, calls = _service([_channel(chat_id="111")], {"ch1": (_target(),)}, dup=True)
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result == {"accepted": True, "duplicate": True}
    assert calls["enqueued"] == []


def test_handle_coupang_platform_enqueues_lookup():
    service, calls = _service([_channel(chat_id="111")], {"ch1": (_target(platform="coupang"),)})
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result == {"accepted": True, "duplicate": False, "job_id": "job-123"}
    assert len(calls["enqueued"]) == 1
    job_type, target_id, payload = calls["enqueued"][0]
    assert job_type == JOB_TYPE_RIDER_LOOKUP
    assert target_id == "tg1"
    assert payload["platform"] == "coupang"


def test_handle_unsupported_platform_enqueues_kakao_send_reply():
    service, calls = _service([_channel(chat_id="111")], {"ch1": (_target(platform="naver"),)})
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result["accepted"] is False
    assert result["reason"] == REASON_UNSUPPORTED_PLATFORM
    assert len(calls["enqueued"]) == 1
    job_type, target_id, payload = calls["enqueued"][0]
    assert job_type == JOB_TYPE_KAKAO_SEND
    assert target_id is None
    assert payload["message"] == "라이더 조회 명령은 배민/쿠팡 탭에서만 지원합니다."
    assert payload["kakao_room_name"] == "운영방"


def test_handle_unsupported_reply_deduped_when_already_replied():
    # A resubmitted event (lost submit response) must not enqueue a second reply.
    service, calls = _service(
        [_channel(chat_id="111")],
        {"ch1": (_target(platform="naver"),)},
        already_replied=lambda key: True,
    )
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result == {"accepted": False, "duplicate": True, "reason": REASON_UNSUPPORTED_PLATFORM}
    assert calls["enqueued"] == []  # no second KAKAO_SEND reply


def test_handle_reject_does_not_enqueue_or_bind():
    service, calls = _service([_channel(room="운영방")], {"ch1": (_target(),)})
    result = asyncio.run(
        service.handle(_event(room="없는방", chat_id=""), agent_id="agent-pc-1")
    )

    assert result == {"accepted": False, "duplicate": False, "reason": REASON_UNKNOWN_ROOM}
    assert calls["enqueued"] == []
    assert calls["bound"] == []


def test_handle_reject_observes_fixed_reason_without_command_payload():
    observed = []
    service, calls = _service(
        [_channel(room="운영방")],
        {"ch1": (_target(),)},
        observe_decision=observed.append,
    )

    result = asyncio.run(
        service.handle(_event(room="없는방", chat_id=""), agent_id="agent-pc-1")
    )

    assert result == {"accepted": False, "duplicate": False, "reason": REASON_UNKNOWN_ROOM}
    assert calls["enqueued"] == []
    assert observed[0]["action"] == ACTION_REJECT
    assert observed[0]["accepted"] is False
    assert observed[0]["duplicate"] is False
    assert observed[0]["reason"] == REASON_UNKNOWN_ROOM
    assert observed[0]["agent_id"] == "agent-pc-1"
    assert observed[0]["source"] == "pc_kakao_db"
    assert observed[0]["command_type"] == COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP
    assert observed[0]["chat_id_present"] is False
    assert observed[0]["last_log_id"] == "1002"
    assert observed[0]["event_fingerprint"].startswith("sha256:")
    for forbidden in ("command", "room_name", "chat_id", "name", "phone", "message"):
        assert forbidden not in observed[0]


def test_handle_enqueue_observes_job_id_and_target_scope():
    observed = []
    service, _calls = _service(
        [_channel(chat_id="111")],
        {"ch1": (_target(),)},
        observe_decision=observed.append,
    )

    result = asyncio.run(service.handle(_event(chat_id="111"), agent_id="agent-pc-1"))

    assert result == {"accepted": True, "duplicate": False, "job_id": "job-123"}
    assert observed[0]["action"] == ACTION_ENQUEUE_LOOKUP
    assert observed[0]["accepted"] is True
    assert observed[0]["agent_id"] == "agent-pc-1"
    assert observed[0]["job_id"] == "job-123"
    assert observed[0]["channel_id"] == "ch1"
    assert observed[0]["tenant_id"] == "t1"
    assert observed[0]["target_id"] == "tg1"
    assert observed[0]["origin_event_key"].startswith("sha256:")
    assert observed[0]["event_fingerprint"] == observed[0]["origin_event_key"]
    for forbidden in ("command", "room_name", "chat_id", "name", "phone", "message"):
        assert forbidden not in observed[0]


def test_handle_sending_disabled_rejects_without_enqueue():
    service, calls = _service([_channel(chat_id="111")], {"ch1": (_target(),)}, sending=False)
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result == {"accepted": False, "duplicate": False, "reason": REASON_SENDING_DISABLED}
    assert calls["enqueued"] == []


def test_handle_tenant_inactive_rejects_without_enqueue():
    service, calls = _service(
        [_channel(chat_id="111")],
        {"ch1": (_target(),)},
        tenant_active=False,
    )
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result == {"accepted": False, "duplicate": False, "reason": REASON_TENANT_DISABLED}
    assert calls["enqueued"] == []


def test_handle_rate_limited_rejects_without_enqueue():
    service, calls = _service(
        [_channel(chat_id="111")],
        {"ch1": (_target(),)},
        rate_limited=True,
    )
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result == {"accepted": False, "duplicate": False, "reason": REASON_RATE_LIMITED}
    assert calls["enqueued"] == []


def test_handle_in_flight_enqueues_distinct_event():
    service, calls = _service([_channel(chat_id="111")], {"ch1": (_target(),)}, in_flight=True)
    result = asyncio.run(service.handle(_event(chat_id="111")))

    assert result == {"accepted": True, "duplicate": False, "job_id": "job-123"}
    assert len(calls["enqueued"]) == 1
    assert calls["enqueued"][0][0] == JOB_TYPE_RIDER_LOOKUP


def test_handle_burst_enqueues_each_distinct_lookup_for_same_target():
    service, calls = _service([_channel(chat_id="111")], {"ch1": (_target(),)}, in_flight=True)

    results = [
        asyncio.run(service.handle(_event(chat_id="111", log_id="1002", name="홍길동", phone="1234"))),
        asyncio.run(service.handle(_event(chat_id="111", log_id="1003", name="아무개", phone="4444"))),
        asyncio.run(service.handle(_event(chat_id="111", log_id="1004", name="심청이", phone="2222"))),
    ]

    assert results == [
        {"accepted": True, "duplicate": False, "job_id": "job-123"},
        {"accepted": True, "duplicate": False, "job_id": "job-123"},
        {"accepted": True, "duplicate": False, "job_id": "job-123"},
    ]
    assert [item[0] for item in calls["enqueued"]] == [
        JOB_TYPE_RIDER_LOOKUP,
        JOB_TYPE_RIDER_LOOKUP,
        JOB_TYPE_RIDER_LOOKUP,
    ]
    assert len({item[2]["origin_event_key"] for item in calls["enqueued"]}) == 3


def test_production_wiring_connects_tenant_and_rate_gate_seams():
    service = build_kakao_inbound_event_service(
        db_session_factory=lambda: None,
        queue_backend=object(),
        sending_enabled_getter=lambda: True,
    )

    assert service._tenant_active is not None
    assert service._rate_limited is not None


def test_handle_invalid_command_skips_channel_load():
    loaded = {"count": 0}

    async def load_channels():
        loaded["count"] += 1
        return []

    async def load_targets(channel_id):
        return ()

    async def enqueue(*, job_type, target_id, payload_json):
        return "job-x"

    service = KakaoInboundEventService(
        load_channels=load_channels,
        load_targets=load_targets,
        enqueue=enqueue,
        sending_enabled=lambda: True,
    )
    bad = InboundEventInput(
        source="pc_kakao_db", kakao_user_hash_digest="sha256:abc",
        chat_id="111", room_name="운영방", last_log_id="1",
        command=InboundCommandInput(type="WRONG", name="강민기", phone_last4="1234"),
    )
    result = asyncio.run(service.handle(bad))

    assert result == {"accepted": False, "duplicate": False, "reason": REASON_INVALID_EVENT}
    assert loaded["count"] == 0  # invalid command short-circuits before loading channels
