"""Story 5.5 / AC2·AC3 (FR-29, ADD-7) — 채널 등록/검증/활성 lifecycle + 운영 전송 게이트.

(1) always-run 순수 정책(DB 불필요): 상태 전이 허용/거부, 운영 게이트(``ACTIVE`` 만 전송 대상),
    Kakao 활성 방명 고유성, Telegram 충돌은 3.7 ``assert_unique_telegram_topics`` **재사용**,
    ``thread_id`` None↔"" 정규화.
(2) always-run 오케스트레이션(in-memory fake repo): webhook→register(PENDING)→verify(VERIFIED,
    테스트 메시지 fake 성공)→activate(ACTIVE) 전체 흐름; 동일 chat 재등록 idempotent; 활성
    ``(chat_id, thread_id)``·Kakao 방명 중복 활성화 거부; 테스트 메시지 실패 시 검증 안 됨.
(3) reuse/boundary 가드(AST): 신규 모듈이 ``rider_agent`` 를 import 하지 않음(단방향); Telegram
    충돌 함수가 3.7 정본과 동일 객체(재구현 아님).

외부 호출 없음 — fake 값만(실제 토큰/전화/이메일/chat_id 형태 금지).
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from rider_crawl.redaction import REDACTED
from rider_crawl.sender import TelegramSendError
from rider_server.domain import (
    DeliveryRule,
    Message,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
)
from rider_server.services import (
    ChannelNotFoundError,
    ChannelRegistrationService,
    DispatchFanoutService,
    InMemoryChannelRepository,
    InvalidChannelTransition,
    KakaoRoomCollisionError,
    TelegramTopicCollisionError,
    assert_channel_transition,
    assert_unique_kakao_rooms,
    find_kakao_room_collisions,
    is_allowed_channel_transition,
    is_operational,
    operational_channels,
    operational_delivery_rules,
)

_FAKE_CHAT = "-100fake"


def _tg(
    id: str,
    *,
    chat: str | None = _FAKE_CHAT,
    thread: str | None = None,
    state: MessengerChannelState = MessengerChannelState.ACTIVE,
) -> MessengerChannel:
    return MessengerChannel(
        id=id,
        tenant_id="tn-1",
        messenger=Messenger.TELEGRAM,
        telegram_chat_id=chat,
        thread_id=thread,
        state=state,
    )


def _kakao(
    id: str,
    *,
    room: str | None = "room",
    state: MessengerChannelState = MessengerChannelState.ACTIVE,
) -> MessengerChannel:
    return MessengerChannel(
        id=id,
        tenant_id="tn-1",
        messenger=Messenger.KAKAO,
        kakao_room_name=room,
        state=state,
    )


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 — 상태 전이 허용/거부
# ══════════════════════════════════════════════════════════════════════════

S = MessengerChannelState


def test_allowed_transitions_lifecycle_path():
    assert is_allowed_channel_transition(S.PENDING, S.VERIFIED)
    assert is_allowed_channel_transition(S.VERIFIED, S.ACTIVE)
    assert is_allowed_channel_transition(S.ACTIVE, S.INACTIVE)
    assert is_allowed_channel_transition(S.INACTIVE, S.PENDING)  # 재등록 reactivate


def test_denied_transitions_are_fail_closed():
    # 검증/활성 건너뛰기·터미널 후 부활 등 미정의 전이는 거부.
    assert not is_allowed_channel_transition(S.PENDING, S.ACTIVE)  # 검증 건너뛰기
    assert not is_allowed_channel_transition(S.VERIFIED, S.PENDING)
    assert not is_allowed_channel_transition(S.INACTIVE, S.ACTIVE)
    with pytest.raises(InvalidChannelTransition):
        assert_channel_transition(S.PENDING, S.ACTIVE)


def test_soft_delete_allowed_from_non_active_states():
    # 활성 전(PENDING/VERIFIED) 채널도 INACTIVE 로 soft-delete 할 수 있다(등록만 하고 폐기 등).
    assert is_allowed_channel_transition(S.PENDING, S.INACTIVE)
    assert is_allowed_channel_transition(S.VERIFIED, S.INACTIVE)
    assert_channel_transition(S.PENDING, S.INACTIVE)  # 예외 없음
    assert_channel_transition(S.VERIFIED, S.INACTIVE)


def test_self_and_unknown_transitions_are_denied():
    # 같은 상태로의 self-전이(no-op)·역행은 미정의라 거부(직접 컬럼 변경 차단).
    assert not is_allowed_channel_transition(S.PENDING, S.PENDING)
    assert not is_allowed_channel_transition(S.ACTIVE, S.ACTIVE)
    assert not is_allowed_channel_transition(S.ACTIVE, S.VERIFIED)
    assert not is_allowed_channel_transition(S.ACTIVE, S.PENDING)


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 — 운영 전송 게이트(ACTIVE 만)
# ══════════════════════════════════════════════════════════════════════════


def test_is_operational_only_active():
    assert is_operational(_tg("a", state=S.ACTIVE)) is True
    for state in (S.PENDING, S.VERIFIED, S.INACTIVE):
        assert is_operational(_tg("a", state=state)) is False


def test_operational_channels_filters_and_preserves_order():
    chans = [
        _tg("c-pending", state=S.PENDING),
        _tg("c-active1", state=S.ACTIVE),
        _tg("c-verified", state=S.VERIFIED),
        _tg("c-active2", state=S.ACTIVE),
        _tg("c-inactive", state=S.INACTIVE),
    ]
    assert [c.id for c in operational_channels(chans)] == ["c-active1", "c-active2"]


def test_operational_delivery_rules_excludes_unverified_and_composes_with_fanout():
    # 게이트가 fan-out 경로를 거치게 한다(AC2): 운영(ACTIVE) 채널 rule 만 통과 → plan 이 그 채널만 펼침.
    channels = {
        "c-active": _tg("c-active", chat="-100a", thread="7", state=S.ACTIVE),
        "c-pending": _tg("c-pending", chat="-100b", state=S.PENDING),
        "c-inactive": _tg("c-inactive", chat="-100c", state=S.INACTIVE),
    }
    rules = [
        DeliveryRule(id="r1", target_id="mt-1", channel_id="c-active"),
        DeliveryRule(id="r2", target_id="mt-1", channel_id="c-pending"),
        DeliveryRule(id="r3", target_id="mt-1", channel_id="c-inactive"),
    ]
    gated = operational_delivery_rules(rules, channels=channels)
    assert [r.channel_id for r in gated] == ["c-active"]

    message = Message(
        id="msg-1",
        snapshot_id="snap-1",
        template_version="v1",
        text="[실적]",
        text_hash="a" * 64,
        text_redacted_preview="[실적]",
    )
    jobs = DispatchFanoutService.plan(
        message, gated, channels=channels, job_id_for=lambda rule: f"dj-{rule.id}"
    )
    # 미검증/소프트삭제 채널은 fan-out 대상에서 빠진다 → 운영 전송은 ACTIVE 채널로만.
    assert [j.channel_id for j in jobs] == ["c-active"]


def test_operational_delivery_rules_excludes_dangling_channel_id():
    # 채널 맵에 없는 rule(dangling)은 게이트에서 제외만 한다(여기는 ACTIVE 필터링 책임만 —
    # UnknownChannel surface 는 plan 이 담당). KeyError 로 터지지 않는다.
    channels = {"c-active": _tg("c-active", chat="-100a", state=S.ACTIVE)}
    rules = [
        DeliveryRule(id="r1", target_id="mt-1", channel_id="c-active"),
        DeliveryRule(id="r-dangling", target_id="mt-1", channel_id="c-missing"),
    ]
    gated = operational_delivery_rules(rules, channels=channels)
    assert [r.channel_id for r in gated] == ["c-active"]


def test_operational_channels_empty_when_none_active():
    chans = [_tg("a", state=S.PENDING), _tg("b", state=S.INACTIVE), _tg("c", state=S.VERIFIED)]
    assert operational_channels(chans) == []


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 — Kakao 활성 방명 고유성 + Telegram 재사용
# ══════════════════════════════════════════════════════════════════════════


def test_find_kakao_collisions_detects_active_duplicates_only():
    a = _kakao("k-1", room="동일방")
    b = _kakao("k-2", room="동일방")
    inactive = _kakao("k-3", room="동일방", state=S.INACTIVE)
    pending = _kakao("k-4", room="동일방", state=S.PENDING)
    other = _kakao("k-5", room="다른방")
    empty = _kakao("k-6", room="")

    collisions = find_kakao_room_collisions([a, b, inactive, pending, other, empty])
    assert len(collisions) == 1
    room, members = collisions[0]
    assert room == "동일방"
    assert [m.id for m in members] == ["k-1", "k-2"]


def test_assert_unique_kakao_rooms_raises_without_leaking_room_name():
    a = _kakao("k-1", room="비밀가게이름")
    b = _kakao("k-2", room="비밀가게이름")
    with pytest.raises(KakaoRoomCollisionError) as exc:
        assert_unique_kakao_rooms([a, b])
    message = str(exc.value)
    # 방명(운영 식별자)은 메시지에 싣지 않는다 — 충돌 채널 id(불투명 FK)만 남는다.
    assert "비밀가게이름" not in message
    assert "k-1" in message and "k-2" in message


def test_assert_unique_kakao_rooms_passes_when_unique():
    assert_unique_kakao_rooms([_kakao("k-1", room="방A"), _kakao("k-2", room="방B")])


def test_find_kakao_collisions_normalizes_surrounding_whitespace():
    # 방명은 strip 후 비교 — "동일방" 과 "  동일방  " 은 같은 방으로 충돌(공백 차이 오발송 회피).
    a = _kakao("k-1", room="동일방")
    b = _kakao("k-2", room="  동일방  ")
    collisions = find_kakao_room_collisions([a, b])
    assert len(collisions) == 1
    room, members = collisions[0]
    assert room == "동일방"
    assert {m.id for m in members} == {"k-1", "k-2"}


def test_telegram_collision_function_is_reused_not_reimplemented():
    # 재사용(재구현 금지): channel_registration 이 쓰는 충돌 함수가 3.7 정본과 동일 객체.
    import rider_server.services.channel_registration as cr
    from rider_server.services.telegram_central_dispatch import assert_unique_telegram_topics as orig

    assert cr.assert_unique_telegram_topics is orig


# ══════════════════════════════════════════════════════════════════════════
# (2) 오케스트레이션 — register→verify→activate (in-memory fake repo)
# ══════════════════════════════════════════════════════════════════════════


def _seeded_repo(channel: MessengerChannel, *, code: str = "CODE-1") -> InMemoryChannelRepository:
    repo = InMemoryChannelRepository()
    repo.seed(channel, registration_code=code)
    return repo


def test_full_register_verify_activate_flow():
    repo = _seeded_repo(_tg("ch-1", chat=None, state=S.PENDING))
    svc = ChannelRegistrationService(repo)
    send_calls: list[str] = []

    async def _run():
        reg = await svc.register(code="CODE-1", chat_id="-100777", thread_id="7")
        assert reg.registered is True
        assert reg.channel.state is S.PENDING
        assert reg.channel.telegram_chat_id == "-100777"

        verified = await svc.verify("ch-1", send_test=lambda ch: send_calls.append(ch.id))
        assert verified.state is S.VERIFIED
        assert send_calls == ["ch-1"]  # 테스트 메시지 발송 후에만 검증

        activated = await svc.activate("ch-1")
        assert activated.state is S.ACTIVE

    asyncio.run(_run())
    final = asyncio.run(repo.get("ch-1"))
    assert final.state is S.ACTIVE


def test_register_thread_id_empty_normalizes_to_none():
    repo = _seeded_repo(_tg("ch-1", chat=None, state=S.PENDING))
    svc = ChannelRegistrationService(repo)
    reg = asyncio.run(svc.register(code="CODE-1", chat_id="-100777", thread_id="   "))
    assert reg.channel.thread_id is None


def test_register_unknown_code_does_not_register():
    repo = _seeded_repo(_tg("ch-1", chat=None, state=S.PENDING))
    svc = ChannelRegistrationService(repo)
    reg = asyncio.run(svc.register(code="NOPE", chat_id="-100777"))
    assert reg.registered is False
    assert reg.reason == "unknown_registration_code"
    assert reg.channel is None


def test_register_known_code_empty_chat_id_is_fail_closed():
    # 코드는 알지만 chat_id 가 비었으면(공백/None) 라우팅을 못 채우므로 갱신 없이 registered=False.
    # 단, 채널은 식별됐으니 channel 은 non-None(unknown-code 와 구분).
    repo = _seeded_repo(_tg("ch-1", chat=None, state=S.PENDING))
    svc = ChannelRegistrationService(repo)
    for bad in ("", "   ", None):
        reg = asyncio.run(svc.register(code="CODE-1", chat_id=bad))
        assert reg.registered is False
        assert reg.reason == "missing_chat_id"
        assert reg.channel is not None and reg.channel.id == "ch-1"
        assert reg.channel.telegram_chat_id is None  # 라우팅 미기록


def test_register_updates_routing_when_chat_id_changes():
    # 같은 코드로 다른 chat 재등록 → 라우팅 갱신 + PENDING 으로(재검증 필요). 멱등 no-op 아님.
    repo = _seeded_repo(_tg("ch-1", chat=None, state=S.PENDING))
    svc = ChannelRegistrationService(repo)

    async def _run():
        await svc.register(code="CODE-1", chat_id="-100111", thread_id="7")
        again = await svc.register(code="CODE-1", chat_id="-100222", thread_id="9")
        assert again.registered is True
        assert again.channel.telegram_chat_id == "-100222"
        assert again.channel.thread_id == "9"
        assert again.channel.state is S.PENDING

    asyncio.run(_run())


def test_register_reactivates_inactive_channel_to_pending():
    # soft-delete(INACTIVE) 된 채널을 같은 코드로 재등록하면 PENDING 으로 되살아난다(reactivate).
    # already_routed no-op 은 ``state != INACTIVE`` 일 때만 적용되므로 INACTIVE 는 재등록된다.
    repo = _seeded_repo(_tg("ch-1", chat="-100777", thread="7", state=S.INACTIVE))
    svc = ChannelRegistrationService(repo)
    reg = asyncio.run(svc.register(code="CODE-1", chat_id="-100777", thread_id="7"))
    assert reg.registered is True
    assert reg.channel.state is S.PENDING  # INACTIVE → PENDING reactivate
    assert asyncio.run(repo.get("ch-1")).state is S.PENDING


def test_duplicate_register_is_idempotent_at_service_level():
    repo = _seeded_repo(_tg("ch-1", chat=None, state=S.PENDING))
    svc = ChannelRegistrationService(repo)

    async def _run():
        await svc.register(code="CODE-1", chat_id="-100777", thread_id="7")
        # 검증·활성까지 진행한 뒤 동일 chat 재등록이 ACTIVE 를 PENDING 으로 되돌리지 않는다.
        await svc.verify("ch-1", send_test=lambda ch: None)
        await svc.activate("ch-1")
        again = await svc.register(code="CODE-1", chat_id="-100777", thread_id="7")
        assert again.registered is True
        assert again.channel.state is S.ACTIVE  # 멱등 no-op — 되돌리지 않음

    asyncio.run(_run())


def test_verify_reuses_central_telegram_sender_send_only():
    # Task 5.1·5.2: 검증용 테스트 메시지는 3.7 CentralTelegramSender(send-only) 재사용 + token 은
    # resolve_token seam 주입(로그/응답 비노출). verify 는 동기 send 를 executor 경계로 감싼다.
    import json
    from urllib.parse import parse_qs

    from rider_server.services import CentralTelegramSender
    from rider_server.services.dispatch_fanout_service import DispatchJob

    calls: list = []
    token_seen: list[str] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def read(self) -> bytes:
            return json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")

    def _urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        return _Resp()

    channel = _tg("ch-1", chat="-100777", thread="7", state=S.PENDING)
    repo = _seeded_repo(channel)
    sender = CentralTelegramSender(
        channels={channel.id: channel},
        resolve_token=lambda ch: token_seen.append(ch.id) or "FAKE-TELEGRAM-TOKEN",
        urlopen=_urlopen,
    )

    def _send_test(ch: MessengerChannel) -> None:
        job = DispatchJob(
            id="test-msg",
            target_id="mt-1",
            channel_id=ch.id,
            message_id="m-1",
            messenger=Messenger.TELEGRAM,
            template_version="v1",
            message_hash="a" * 64,
        )
        sender.send(job, "[검증] 테스트 메시지")

    svc = ChannelRegistrationService(repo)
    verified = asyncio.run(svc.verify("ch-1", send_test=_send_test))

    assert verified.state is S.VERIFIED
    assert len(calls) == 1  # send-only — sendMessage 정확히 1회
    assert calls[0][0].endswith("/sendMessage")
    payload = parse_qs(calls[0][1].decode("utf-8"))
    assert payload["chat_id"] == ["-100777"]
    assert payload["message_thread_id"] == ["7"]
    assert token_seen == ["ch-1"]  # token 은 seam 으로만


def test_verify_failure_keeps_channel_pending():
    repo = _seeded_repo(_tg("ch-1", chat="-100777", state=S.PENDING))
    svc = ChannelRegistrationService(repo)

    def _failing_send(_ch):
        raise TelegramSendError("테스트 메시지 실패", ambiguous=False)

    with pytest.raises(TelegramSendError):
        asyncio.run(svc.verify("ch-1", send_test=_failing_send))
    # 테스트 메시지 실패 → 검증 전이 없음(PENDING 유지 — 미검증 채널 활성 금지).
    assert asyncio.run(repo.get("ch-1")).state is S.PENDING


def test_verify_requires_pending_state():
    repo = _seeded_repo(_tg("ch-1", chat="-100777", state=S.ACTIVE))
    svc = ChannelRegistrationService(repo)
    with pytest.raises(InvalidChannelTransition):
        asyncio.run(svc.verify("ch-1", send_test=lambda ch: None))


def test_activate_rejects_duplicate_active_telegram_topic():
    repo = InMemoryChannelRepository()
    repo.seed(_tg("ch-existing", chat="-100A", thread="7", state=S.ACTIVE))
    repo.seed(_tg("ch-cand", chat="-100A", thread="7", state=S.VERIFIED), registration_code="C")
    svc = ChannelRegistrationService(repo)
    with pytest.raises(TelegramTopicCollisionError):
        asyncio.run(svc.activate("ch-cand"))
    # 충돌로 활성화 차단 → 후보는 VERIFIED 유지(전송 대상 안 됨).
    assert asyncio.run(repo.get("ch-cand")).state is S.VERIFIED


def test_activate_allows_unique_telegram_topic():
    repo = InMemoryChannelRepository()
    repo.seed(_tg("ch-existing", chat="-100A", thread="7", state=S.ACTIVE))
    repo.seed(_tg("ch-cand", chat="-100A", thread="8", state=S.VERIFIED), registration_code="C")
    svc = ChannelRegistrationService(repo)
    activated = asyncio.run(svc.activate("ch-cand"))
    assert activated.state is S.ACTIVE


def test_activate_rejects_duplicate_active_kakao_room():
    repo = InMemoryChannelRepository()
    repo.seed(_kakao("k-existing", room="동일방", state=S.ACTIVE))
    repo.seed(_kakao("k-cand", room="동일방", state=S.VERIFIED), registration_code="C")
    svc = ChannelRegistrationService(repo)
    with pytest.raises(KakaoRoomCollisionError):
        asyncio.run(svc.activate("k-cand"))
    # 충돌로 활성화 차단 → 후보는 VERIFIED 유지(전송 대상 안 됨).
    assert asyncio.run(repo.get("k-cand")).state is S.VERIFIED


def test_activate_allows_unique_kakao_room():
    # Kakao 양성 경로(기존엔 거부만 잠겨 있었다): 방명이 다르면 활성화 성공.
    repo = InMemoryChannelRepository()
    repo.seed(_kakao("k-existing", room="가게A", state=S.ACTIVE))
    repo.seed(_kakao("k-cand", room="가게B", state=S.VERIFIED), registration_code="C")
    svc = ChannelRegistrationService(repo)
    activated = asyncio.run(svc.activate("k-cand"))
    assert activated.state is S.ACTIVE


def test_activate_requires_verified_state_at_service_level():
    # 검증 건너뛰기 차단: PENDING 채널 activate 는 전이표에서 막혀 InvalidChannelTransition.
    repo = _seeded_repo(_tg("ch-1", chat="-100777", state=S.PENDING))
    svc = ChannelRegistrationService(repo)
    with pytest.raises(InvalidChannelTransition):
        asyncio.run(svc.activate("ch-1"))
    assert asyncio.run(repo.get("ch-1")).state is S.PENDING


def test_deactivate_soft_deletes_to_inactive():
    repo = _seeded_repo(_tg("ch-1", chat="-100777", state=S.ACTIVE))
    svc = ChannelRegistrationService(repo)
    updated = asyncio.run(svc.deactivate("ch-1"))
    assert updated.state is S.INACTIVE


def test_deactivate_allowed_from_pending_without_activation():
    # 활성화하지 않은 PENDING 채널도 soft-delete 가능(등록만 하고 폐기).
    repo = _seeded_repo(_tg("ch-1", chat="-100777", state=S.PENDING))
    svc = ChannelRegistrationService(repo)
    assert asyncio.run(svc.deactivate("ch-1")).state is S.INACTIVE


def test_transition_on_missing_channel_raises_not_found():
    svc = ChannelRegistrationService(InMemoryChannelRepository())
    with pytest.raises(ChannelNotFoundError):
        asyncio.run(svc.activate("nope"))


def test_verify_and_deactivate_on_missing_channel_raise_not_found():
    # activate 외 다른 전이도 채널 부재 시 동일하게 fail-closed(ChannelNotFoundError).
    svc = ChannelRegistrationService(InMemoryChannelRepository())
    with pytest.raises(ChannelNotFoundError):
        asyncio.run(svc.verify("nope", send_test=lambda ch: None))
    with pytest.raises(ChannelNotFoundError):
        asyncio.run(svc.deactivate("nope"))


def test_active_kakao_command_channels_filters_to_kakao_active_command_on():
    # Agent watchlist source: only ACTIVE, command_trigger_enabled, KAKAO channels.
    repo = InMemoryChannelRepository()
    repo.seed(MessengerChannel(
        id="k-on", tenant_id="tn-1", messenger=Messenger.KAKAO,
        kakao_room_name="운영방", state=S.ACTIVE, command_trigger_enabled=True))
    repo.seed(MessengerChannel(  # command trigger off (opt-in) -> excluded
        id="k-off", tenant_id="tn-1", messenger=Messenger.KAKAO,
        kakao_room_name="상담방", state=S.ACTIVE, command_trigger_enabled=False))
    repo.seed(MessengerChannel(  # not ACTIVE -> excluded
        id="k-inactive", tenant_id="tn-1", messenger=Messenger.KAKAO,
        kakao_room_name="구방", state=S.INACTIVE, command_trigger_enabled=True))
    repo.seed(_tg("t-on", state=S.ACTIVE))  # telegram -> excluded

    result = asyncio.run(repo.active_kakao_command_channels())

    assert [c.id for c in result] == ["k-on"]


# ══════════════════════════════════════════════════════════════════════════
# (2b) PG repository 순수 converter — always-run(DB 불필요, pg-gated 가 가린 helper 추출)
# ══════════════════════════════════════════════════════════════════════════
# PostgresChannelRepository 의 DB I/O 는 PG-gated 라 CI 에서 skip 되지만, ORM 행 → domain 변환
# (``_to_domain``)은 세션 없이 호출 가능한 순수 함수다. enum 강제·id 문자열화·라우팅 컬럼 통과를
# always-run 으로 잠근다(memory/pg-gated-files-hide-pure-helpers).


def test_postgres_repo_to_domain_coerces_row_without_db():
    import uuid

    from rider_server.db.models.messaging import MessengerChannel as Row
    from rider_server.services.channel_repository_postgres import _to_domain

    row = Row(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        tenant_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        messenger="TELEGRAM",
        telegram_chat_id=_FAKE_CHAT,
        thread_id="7",
        kakao_room_name=None,
        state="ACTIVE",
    )
    domain = _to_domain(row)
    assert isinstance(domain.id, str) and isinstance(domain.tenant_id, str)
    assert domain.id == "11111111-1111-1111-1111-111111111111"
    assert domain.messenger is Messenger.TELEGRAM  # 문자열 → enum 강제
    assert domain.state is MessengerChannelState.ACTIVE
    assert domain.telegram_chat_id == _FAKE_CHAT and domain.thread_id == "7"


def test_postgres_repo_to_domain_maps_kakao_row():
    import uuid

    from rider_server.db.models.messaging import MessengerChannel as Row
    from rider_server.services.channel_repository_postgres import _to_domain

    row = Row(
        id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        tenant_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        messenger="KAKAO",
        telegram_chat_id=None,
        thread_id=None,
        kakao_room_name="가게방",
        state="PENDING",
    )
    domain = _to_domain(row)
    assert domain.messenger is Messenger.KAKAO
    assert domain.state is MessengerChannelState.PENDING
    assert domain.kakao_room_name == "가게방"


# ══════════════════════════════════════════════════════════════════════════
# (3) reuse/boundary 가드 — 신규 모듈 단방향 import(rider_agent import 0)
# ══════════════════════════════════════════════════════════════════════════


def _import_roots(path: str) -> set[str]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_new_modules_never_import_rider_agent():
    modules = [
        "src/rider_server/api/telegram_webhook.py",
        "src/rider_server/services/channel_registration.py",
        "src/rider_server/services/channel_repository_postgres.py",
    ]
    offenders = [m for m in modules if "rider_agent" in _import_roots(m)]
    assert offenders == [], offenders


def test_new_modules_third_party_within_one_way_allowed():
    # 신규 모듈의 third-party root 는 단방향 허용집합(rider_crawl/fastapi/pydantic/sqlalchemy)뿐.
    import sys

    stdlib = set(sys.stdlib_module_names)
    self_roots = {"rider_server", "__future__"}
    allowed = {"rider_crawl", "fastapi", "pydantic", "sqlalchemy"}
    for path in (
        "src/rider_server/api/telegram_webhook.py",
        "src/rider_server/services/channel_registration.py",
        "src/rider_server/services/channel_repository_postgres.py",
    ):
        third_party = _import_roots(path) - stdlib - self_roots
        assert third_party <= allowed, f"{path}: {third_party}"
