"""Story 3.7 / AC1~AC8 (P2-07, FR-24·FR-26, NFR-1·5, ADD-11) — 중앙 send-only Telegram 어댑터.

(1) AC1 — 중앙 send-only 경로: legacy ``send_telegram_text`` 재사용으로 ``sendMessage`` 만 1회,
    올바른 chat_id+message_thread_id payload. ``getUpdates``/poller 미호출(send-only).
(2) AC2 — 전송 scope=(chat_id, thread_id)=``TelegramRoute``; 3.6 ``attempt_delivery`` 에 compose
    하면 채널별 ``DeliveryLog``(SENT / FAILED·RETRYING + error_code=TELEGRAM_FAILURE).
(3) AC2.5 — ambiguous 실패는 release 안 함 → 2라운드 reserve 충돌→DUPLICATE_BLOCKED(재전송 0).
(4) AC3 — 같은 (chat_id, thread_id) 활성 Telegram 채널 충돌 검출(비활성·Kakao·다른 조합 제외).
(5) AC4 — retry_attempts=1 단일 시도(이중 재시도 없음)·결정성·예외 breadcrumb redact 통과.
(6) 재노출·frozen.

외부 호출 없음 — fake urlopen/in-memory seam·가짜 token/chat_id만. 평면 ``tests/server/``
컨벤션(conftest 공유 없이 자급자족, ``__init__.py`` 미추가). 평문 secret/식별자 금지.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import pytest

from rider_crawl.redaction import REDACTED
from rider_crawl.sender import TelegramSendError
from rider_server.domain import (
    DeliveryStatus,
    Message,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
)
from rider_server.services import (
    CentralTelegramSender,
    DeliveryFailurePolicy,
    DispatchFanoutService,
    IdempotentDeliveryService,
    TelegramRoute,
    TelegramTopicCollisionError,
    assert_unique_telegram_topics,
    find_telegram_topic_collisions,
)
from rider_server.services.dispatch_fanout_service import DispatchJob, UnknownChannelError
from rider_server.services.telegram_central_dispatch import is_ambiguous_send_failure

# ── fixture: 가짜 값만(가짜 token/chat_id·sha256 형태 hash) ──────────────────────
_FAKE_TOKEN = "FAKE-TELEGRAM-TOKEN"
_FAKE_CHAT_ID = "-100fake"
_TARGET_ID = "mt-1"
_CHANNEL_ID = "ch-tg"
_MESSAGE_ID = "msg-1"
_TEMPLATE_VERSION = "baemin.realtime.v1"
_MESSAGE_HASH = "a" * 64  # sha256 형태(가짜 — 실제 secret 아님)
_COLLECTED_AT = datetime(2026, 1, 1, 9, 30, 0)
_SENT_AT = datetime(2026, 1, 1, 9, 30, 5)
_TEXT = "[실시간 실적봇]\n오후논피크 : 41.8건"


def _channel(
    *,
    id: str = _CHANNEL_ID,
    chat_id: str | None = _FAKE_CHAT_ID,
    thread_id: str | None = None,
    messenger: Messenger = Messenger.TELEGRAM,
    state: MessengerChannelState = MessengerChannelState.ACTIVE,
) -> MessengerChannel:
    return MessengerChannel(
        id=id,
        tenant_id="tn-1",
        messenger=messenger,
        telegram_chat_id=chat_id,
        thread_id=thread_id,
        state=state,
    )


def _job(*, id: str = "dj-1", channel_id: str = _CHANNEL_ID) -> DispatchJob:
    return DispatchJob(
        id=id,
        target_id=_TARGET_ID,
        channel_id=channel_id,
        message_id=_MESSAGE_ID,
        messenger=Messenger.TELEGRAM,
        template_version=_TEMPLATE_VERSION,
        message_hash=_MESSAGE_HASH,
    )


class _FakeResponse:
    """``send_telegram_text`` 가 read/decode 하는 최소 응답 객체(성공 payload)."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _ok_urlopen(calls: list):
    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data, timeout))
        return _FakeResponse({"ok": True, "result": {"message_id": 10}})

    return fake_urlopen


def _resolve_token_for(seen: list):
    def resolve_token(channel: MessengerChannel) -> str:
        seen.append(channel.id)
        return _FAKE_TOKEN

    return resolve_token


def _sender(channels, *, urlopen, resolve_token=None, token_seen=None) -> CentralTelegramSender:
    return CentralTelegramSender(
        channels={ch.id: ch for ch in channels},
        resolve_token=resolve_token or _resolve_token_for(token_seen if token_seen is not None else []),
        urlopen=urlopen,
    )


# ── AC1 — 중앙 send-only 경로(sendMessage 1회·올바른 payload) ─────────────────────


def test_send_posts_exactly_one_send_message_with_route_payload():
    from urllib.parse import parse_qs

    calls: list = []
    channel = _channel(thread_id="7")
    sender = _sender([channel], urlopen=_ok_urlopen(calls))

    sender.send(_job(), _TEXT)

    # 정확히 sendMessage 1회(send-only — getUpdates 등 다른 호출 없음).
    assert len(calls) == 1
    assert calls[0][0] == f"https://api.telegram.org/bot{_FAKE_TOKEN}/sendMessage"
    payload = parse_qs(calls[0][1].decode("utf-8"))
    assert payload["chat_id"] == [_FAKE_CHAT_ID]
    assert payload["text"] == [_TEXT]
    # 전송 scope: chat_id + topic_id(message_thread_id) 가 payload에 포함된다.
    assert payload["message_thread_id"] == ["7"]


def test_send_without_thread_id_omits_message_thread_id():
    from urllib.parse import parse_qs

    calls: list = []
    sender = _sender([_channel(thread_id=None)], urlopen=_ok_urlopen(calls))

    sender.send(_job(), _TEXT)

    payload = parse_qs(calls[0][1].decode("utf-8"))
    assert "message_thread_id" not in payload


def test_module_is_send_only_no_getupdates_or_poller():
    # send-only 구조 보장: 신규 중앙 경로는 어떤 getUpdates/수신 polling 심볼도 import하지
    # 않는다(AC1.2). import하지 않은 모듈-레벨 함수/클래스는 호출할 수 없으므로, 실제 import
    # 엣지만 본다(docstring/주석의 언급은 무시 — 본 모듈은 "안 한다"를 문서화한다).
    import ast

    source = Path(
        "src/rider_server/services/telegram_central_dispatch.py"
    ).read_text(encoding="utf-8")
    imported_names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "get_telegram_updates" not in imported_names
    assert "TelegramUpdatePoller" not in imported_names
    # legacy sender에서 재사용하는 건 send-only 심볼뿐이다.
    assert "send_telegram_text" in imported_names


def test_send_uses_resolve_token_seam_for_each_call():
    seen: list = []
    sender = _sender([_channel()], urlopen=_ok_urlopen([]), token_seen=seen)

    sender.send(_job(), _TEXT)

    assert seen == [_CHANNEL_ID]  # token은 resolve_token 주입 seam으로만 들어온다.


def test_send_unknown_channel_fails_closed():
    sender = _sender([_channel()], urlopen=_ok_urlopen([]))

    with pytest.raises(UnknownChannelError):
        sender.send(_job(channel_id="ch-missing"), _TEXT)


def test_send_non_telegram_channel_fails_closed():
    kakao = _channel(id="ch-kakao", messenger=Messenger.KAKAO, chat_id=None)
    sender = _sender([kakao], urlopen=_ok_urlopen([]))

    with pytest.raises(ValueError):
        sender.send(_job(channel_id="ch-kakao"), _TEXT)


# ── AC2 — TelegramRoute scope + 3.6 attempt_delivery compose(채널별 DeliveryLog) ──


def test_telegram_route_from_channel_derives_scope():
    assert TelegramRoute.from_channel(_channel(thread_id="7")) == TelegramRoute(
        chat_id=_FAKE_CHAT_ID, thread_id="7"
    )
    # thread_id None/빈문자는 None으로 정규화(scope 일관).
    assert TelegramRoute.from_channel(_channel(thread_id=None)).thread_id is None
    assert TelegramRoute.from_channel(_channel(thread_id="")).thread_id is None


def test_telegram_route_fail_closed_on_non_telegram_or_empty_chat_id():
    with pytest.raises(ValueError):
        TelegramRoute.from_channel(_channel(messenger=Messenger.KAKAO, chat_id=None))
    with pytest.raises(ValueError):
        TelegramRoute.from_channel(_channel(chat_id=""))


def _log_id_for(job: DispatchJob) -> str:
    return f"dl-{job.id}"


def test_compose_success_with_attempt_delivery_records_sent():
    calls: list = []
    channel = _channel()
    sender = _sender([channel], urlopen=_ok_urlopen(calls))
    seen: set[str] = set()

    result = DeliveryFailurePolicy.attempt_delivery(
        _job(),
        collected_at=_COLLECTED_AT,
        reserve=lambda key: key not in seen and (seen.add(key) or True),
        send=lambda job: sender.send(job, _TEXT),
        release=lambda key: seen.discard(key),
        classify=lambda exc: DeliveryFailurePolicy.channel_failure_category(Messenger.TELEGRAM),
        log_id_for=_log_id_for,
        sent_at=_SENT_AT,
        attempt=1,
        max_attempts=3,
    )

    assert result.log.status is DeliveryStatus.SENT
    assert result.log.error_code is None
    assert result.decision is None
    assert len(calls) == 1  # 직접 로깅 재구현 없음 — deliver_once가 SENT 기록.


def test_compose_failure_with_attempt_delivery_records_telegram_failure():
    def failing_urlopen(request, timeout):
        raise TelegramSendError("Telegram Bot API error: bad request")

    channel = _channel()
    sender = _sender([channel], urlopen=failing_urlopen)
    seen: set[str] = set()

    result = DeliveryFailurePolicy.attempt_delivery(
        _job(),
        collected_at=_COLLECTED_AT,
        reserve=lambda key: key not in seen and (seen.add(key) or True),
        send=lambda job: sender.send(job, _TEXT),
        release=lambda key: seen.discard(key),
        classify=lambda exc: DeliveryFailurePolicy.channel_failure_category(Messenger.TELEGRAM),
        log_id_for=_log_id_for,
        sent_at=_SENT_AT,
        attempt=1,
        max_attempts=3,
    )

    # 채널별 DeliveryLog: 실패 → error_code=TELEGRAM_FAILURE, status∈{FAILED, RETRYING}.
    assert result.log.channel_id == _CHANNEL_ID
    assert result.log.error_code == "TELEGRAM_FAILURE"
    assert result.log.status in {DeliveryStatus.FAILED, DeliveryStatus.RETRYING}


# ── AC2.5 — ambiguous 실패는 release 안 함(재전송 0 → DUPLICATE_BLOCKED) ──────────


class _Seam:
    """in-memory reserve/send/release 레코더(3.5 test_idempotency 패턴)."""

    def __init__(self, sender: CentralTelegramSender) -> None:
        self.sender = sender
        self.seen: set[str] = set()
        self.sent: list[str] = []
        self.released: list[str] = []

    def reserve(self, key: str) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def send(self, job: DispatchJob) -> None:
        self.sent.append(job.id)
        self.sender.send(job, _TEXT)

    def release(self, key: str) -> None:
        self.released.append(key)
        self.seen.discard(key)


def _attempt_ambiguity_safe(seam: _Seam, job: DispatchJob):
    """안전 wiring: ambiguous 실패는 release하지 않는다(오발송보다 미발송, AC2.5).

    실제 release/재시도 wiring은 3.6 ``attempt_delivery``/Epic 5 소유 — 여기서는 본 스토리가
    제공하는 ``is_ambiguous_send_failure`` 헬퍼로 "ambiguous → 비-release" 안전 기본값을
    in-memory seam으로 단언한다.
    """

    key = IdempotentDeliveryService.build_dedup_key(
        target_id=job.target_id,
        channel_id=job.channel_id,
        collected_at=_COLLECTED_AT,
        template_version=job.template_version,
        message_hash=job.message_hash,
    )
    try:
        return IdempotentDeliveryService.deliver_once(
            job,
            collected_at=_COLLECTED_AT,
            reserve=seam.reserve,
            send=seam.send,
            log_id_for=_log_id_for,
            sent_at=_SENT_AT,
        )
    except Exception as exc:
        if not is_ambiguous_send_failure(exc):
            seam.release(key)  # definite 실패만 재시도 길을 연다.
        raise


def test_ambiguous_failure_is_not_released_and_blocks_resend():
    def ambiguous_urlopen(request, timeout):
        # POST 후 응답 못 읽음 → send_telegram_text가 ambiguous=True로 표시.
        raise OSError("response lost after request")

    sender = _sender([_channel()], urlopen=ambiguous_urlopen)
    seam = _Seam(sender)
    job = _job()

    # 라운드 1: reserve 성공 → send → ambiguous 실패 → release 안 함.
    with pytest.raises(TelegramSendError) as exc_info:
        _attempt_ambiguity_safe(seam, job)
    assert is_ambiguous_send_failure(exc_info.value) is True
    assert seam.released == []  # ambiguous는 key를 풀지 않는다(미발송 우선).
    assert seam.sent == ["dj-1"]

    # 라운드 2: key가 유지돼 reserve 충돌 → DUPLICATE_BLOCKED, send 미호출(재전송 0).
    log = _attempt_ambiguity_safe(seam, job)
    assert log.status is DeliveryStatus.DUPLICATE_BLOCKED
    assert seam.sent == ["dj-1"]  # 두 번째 send 없음.


def test_definite_failure_is_released_and_allows_retry():
    def definite_urlopen(request, timeout):
        # 명확한 검증 실패(전송 안 됨) — ambiguous 아님 → 재시도 가능.
        return _FakeResponse({"ok": False, "description": "bad request"})

    sender = _sender([_channel()], urlopen=definite_urlopen)
    seam = _Seam(sender)
    job = _job()

    with pytest.raises(TelegramSendError) as exc_info:
        _attempt_ambiguity_safe(seam, job)
    assert is_ambiguous_send_failure(exc_info.value) is False
    assert len(seam.released) == 1  # definite 실패는 release(재시도 길 열림).

    # 라운드 2: key가 풀려 reserve 다시 성공 → send 재시도(재전송 경로).
    with pytest.raises(TelegramSendError):
        _attempt_ambiguity_safe(seam, job)
    assert seam.sent == ["dj-1", "dj-1"]


# ── AC3 — 활성 Telegram 채널 간 (chat_id, thread_id) 충돌 검출 ─────────────────────


def test_find_collisions_detects_two_active_channels_sharing_route():
    a = _channel(id="ch-1", chat_id=_FAKE_CHAT_ID, thread_id="7")
    b = _channel(id="ch-2", chat_id=_FAKE_CHAT_ID, thread_id="7")

    collisions = find_telegram_topic_collisions([a, b])

    assert len(collisions) == 1
    route, members = collisions[0]
    assert route == TelegramRoute(chat_id=_FAKE_CHAT_ID, thread_id="7")
    assert [m.id for m in members] == ["ch-1", "ch-2"]
    with pytest.raises(TelegramTopicCollisionError):
        assert_unique_telegram_topics([a, b])


def test_find_collisions_ignores_inactive_kakao_and_distinct_routes():
    active = _channel(id="ch-1", chat_id=_FAKE_CHAT_ID, thread_id="7")
    inactive = _channel(
        id="ch-2", chat_id=_FAKE_CHAT_ID, thread_id="7", state=MessengerChannelState.INACTIVE
    )
    pending = _channel(
        id="ch-3", chat_id=_FAKE_CHAT_ID, thread_id="7", state=MessengerChannelState.PENDING
    )
    kakao = MessengerChannel(
        id="ch-4", tenant_id="tn-1", messenger=Messenger.KAKAO,
        kakao_room_name="room", state=MessengerChannelState.ACTIVE,
    )
    other_topic = _channel(id="ch-5", chat_id=_FAKE_CHAT_ID, thread_id="8")
    other_chat = _channel(id="ch-6", chat_id="-100other", thread_id="7")

    # 활성 1개 + 비활성·Kakao·다른 조합 → 충돌 없음.
    assert find_telegram_topic_collisions(
        [active, inactive, pending, kakao, other_topic, other_chat]
    ) == []
    assert_unique_telegram_topics([active, inactive, pending, kakao, other_topic, other_chat])


def test_find_collisions_treats_none_and_empty_thread_id_as_same_route():
    a = _channel(id="ch-1", thread_id=None)
    b = _channel(id="ch-2", thread_id="")

    collisions = find_telegram_topic_collisions([a, b])

    assert len(collisions) == 1  # None ↔ "" 동일 키 → 충돌(정규화 일관).


def test_collision_error_message_is_redacted():
    a = _channel(id="ch-1", chat_id=_FAKE_CHAT_ID, thread_id="7")
    b = _channel(id="ch-2", chat_id=_FAKE_CHAT_ID, thread_id="7")

    with pytest.raises(TelegramTopicCollisionError) as exc_info:
        assert_unique_telegram_topics([a, b])

    message = str(exc_info.value)
    # chat_id/thread_id 값(숫자)은 redact 통과로 평문 노출되지 않는다(NFR-5).
    assert _FAKE_CHAT_ID not in message
    assert REDACTED in message
    # 채널 id(불투명 FK)는 운영자 진단용으로 남는다.
    assert "ch-1" in message and "ch-2" in message


# ── AC4 — 단일 시도(이중 재시도 없음)·결정성·비노출 ───────────────────────────────


def test_send_uses_single_attempt_no_double_retry():
    # legacy send_telegram_text는 retryable 실패에 내부 재시도하지만, 중앙 경로는
    # retry_attempts=1로 단일 시도(재시도/backoff=3.6) — 같은 호출 안에서 재시도 0.
    attempts = 0

    def rate_limited_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        return _FakeResponse(
            {"ok": False, "error_code": 429, "description": "Too Many Requests",
             "parameters": {"retry_after": 2}}
        )

    sender = _sender([_channel()], urlopen=rate_limited_urlopen)
    with pytest.raises(TelegramSendError):
        sender.send(_job(), _TEXT)

    assert attempts == 1  # 이중 재시도 없음(legacy 내부 재시도 미발동).


def test_send_is_deterministic_for_same_input():
    calls_a: list = []
    calls_b: list = []
    sender_a = _sender([_channel(thread_id="7")], urlopen=_ok_urlopen(calls_a))
    sender_b = _sender([_channel(thread_id="7")], urlopen=_ok_urlopen(calls_b))

    sender_a.send(_job(), _TEXT)
    sender_b.send(_job(), _TEXT)

    # 같은 입력 → 같은 요청(url·payload). timeout 필드는 비교에서 제외.
    assert calls_a[0][:2] == calls_b[0][:2]


# ── 재노출·frozen ────────────────────────────────────────────────────────────────


def test_symbols_reexported_from_services_package():
    import rider_server.services as services

    for name in (
        "CentralTelegramSender",
        "TelegramRoute",
        "TelegramTopicCollisionError",
        "find_telegram_topic_collisions",
        "assert_unique_telegram_topics",
    ):
        assert hasattr(services, name)
        assert name in services.__all__

    # 3.6/이전 심볼은 그대로 유지(무삭제 — additive).
    for name in ("DeliveryFailurePolicy", "IdempotentDeliveryService", "SubscriptionGate"):
        assert name in services.__all__


def test_telegram_route_is_frozen():
    route = TelegramRoute(chat_id=_FAKE_CHAT_ID, thread_id="7")
    with pytest.raises(FrozenInstanceError):
        route.chat_id = "-100other"  # type: ignore[misc]


# ── 갭 보강: as_send_callback seam을 실제 dispatch_all fan-out에 꽂기(AC1 — 한 대상 → N 채널) ──


def _message() -> Message:
    return Message(
        id=_MESSAGE_ID,
        snapshot_id="snap-1",
        template_version=_TEMPLATE_VERSION,
        text=_TEXT,
        text_hash=_MESSAGE_HASH,
        text_redacted_preview=_TEXT,
    )


def test_as_send_callback_drives_dispatch_all_fanout_send_only():
    from urllib.parse import parse_qs

    # as_send_callback()는 (job, text) -> None 콜백이라 3.4 dispatch_all(send=...) seam에
    # 그대로 꽂힌다(채널별 라우팅 = 중앙 send-only). 한 대상 → 두 활성 Telegram 채널.
    calls: list = []
    ch_a = _channel(id="ch-a", chat_id="-100aaa", thread_id="7")
    ch_b = _channel(id="ch-b", chat_id="-100bbb", thread_id=None)
    sender = _sender([ch_a, ch_b], urlopen=_ok_urlopen(calls))

    jobs = [_job(id="dj-a", channel_id="ch-a"), _job(id="dj-b", channel_id="ch-b")]
    outcomes = DispatchFanoutService.dispatch_all(
        _message(), jobs, send=sender.as_send_callback()
    )

    # 채널마다 격리 전송 성공(입력 순서 보존).
    assert [o.sent for o in outcomes] == [True, True]
    # 채널마다 정확히 sendMessage 1회씩(send-only — getUpdates 등 다른 호출 0).
    assert len(calls) == 2
    sent_chat_ids = {parse_qs(c[1].decode("utf-8"))["chat_id"][0] for c in calls}
    assert sent_chat_ids == {"-100aaa", "-100bbb"}


def test_dispatch_all_isolates_central_send_failure_fail_closed():
    # 중앙 sender의 fail-closed(미등록 채널 → UnknownChannelError)가 dispatch_all 격리로
    # contain된다 — 한 채널 실패가 다른 채널 성공을 무효화하지 않는다(AC1·AC5).
    calls: list = []
    ch_ok = _channel(id="ch-ok", chat_id="-100ok", thread_id="7")
    sender = _sender([ch_ok], urlopen=_ok_urlopen(calls))

    jobs = [_job(id="dj-x", channel_id="ch-missing"), _job(id="dj-ok", channel_id="ch-ok")]
    outcomes = DispatchFanoutService.dispatch_all(
        _message(), jobs, send=sender.as_send_callback()
    )

    assert [o.sent for o in outcomes] == [False, True]
    assert len(calls) == 1  # 실패한 채널은 sendMessage 미호출, 정상 채널만 1회.


# ── 갭 보강: is_ambiguous_send_failure 직접 계약(예외 타입 경계, AC2.5) ────────────────


def test_is_ambiguous_send_failure_classifies_by_exception_type():
    # ambiguous=True인 TelegramSendError만 ambiguous로 본다.
    assert is_ambiguous_send_failure(TelegramSendError("lost", ambiguous=True)) is True
    # 명확한 실패(기본 ambiguous=False)는 ambiguous 아님 → 재시도 가능.
    assert is_ambiguous_send_failure(TelegramSendError("bad request")) is False
    # TelegramSendError가 아닌 예외(아직 legacy sender가 감싸기 전 raw 예외)는 ambiguous로
    # 단정하지 않는다 — 잘못 ambiguous로 보면 definite 실패를 재시도 못 하게 막는다.
    assert is_ambiguous_send_failure(OSError("connection dropped")) is False
    assert is_ambiguous_send_failure(ValueError("nope")) is False


# ── 갭 보강: 충돌 검출 결정성(3개 그룹·다중 그룹 입력 순서 보존, AC3) ──────────────────


def test_find_collisions_groups_three_members_and_preserves_input_order():
    # route A(3개) + route B(2개)가 뒤섞여 들어와도 입력 순서대로 그룹·멤버를 반환한다
    # (docstring "2개 이상·입력 순서 보존·결정적" 계약).
    a1 = _channel(id="ch-a1", chat_id="-100aaa", thread_id="7")
    b1 = _channel(id="ch-b1", chat_id="-100bbb", thread_id="9")
    a2 = _channel(id="ch-a2", chat_id="-100aaa", thread_id="7")
    a3 = _channel(id="ch-a3", chat_id="-100aaa", thread_id="7")
    b2 = _channel(id="ch-b2", chat_id="-100bbb", thread_id="9")

    collisions = find_telegram_topic_collisions([a1, b1, a2, a3, b2])

    # route A가 먼저 등장 → 첫 그룹, route B가 둘째 그룹(입력 순서 보존).
    assert [route for route, _ in collisions] == [
        TelegramRoute(chat_id="-100aaa", thread_id="7"),
        TelegramRoute(chat_id="-100bbb", thread_id="9"),
    ]
    assert [[m.id for m in members] for _, members in collisions] == [
        ["ch-a1", "ch-a2", "ch-a3"],
        ["ch-b1", "ch-b2"],
    ]
    with pytest.raises(TelegramTopicCollisionError):
        assert_unique_telegram_topics([a1, b1, a2, a3, b2])


# ── 갭 보강: timeout_seconds 주입이 transport로 전달(AC4 — 주입 seam 완결) ──────────────


def test_send_forwards_injected_timeout_seconds():
    calls: list = []
    sender = CentralTelegramSender(
        channels={_CHANNEL_ID: _channel()},
        resolve_token=_resolve_token_for([]),
        urlopen=_ok_urlopen(calls),
        timeout_seconds=42,
    )

    sender.send(_job(), _TEXT)

    # 주입한 timeout_seconds가 send_telegram_text → urlopen(timeout=...)로 그대로 전달된다.
    assert calls[0][2] == 42


def test_send_default_timeout_seconds_is_forwarded():
    calls: list = []
    sender = _sender([_channel()], urlopen=_ok_urlopen(calls))  # 기본 timeout_seconds=10

    sender.send(_job(), _TEXT)

    assert calls[0][2] == 10
