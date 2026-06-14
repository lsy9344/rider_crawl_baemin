"""채널 등록/검증/활성 lifecycle service + 운영 전송 게이트 — Story 5.5 (P4-06, FR-29, ADD-7·11).

3.7 이 5.5 로 위임한 "인바운드 등록/검증/활성 lifecycle + 운영 전송 게이트"를 구현한다. 두 층으로
나눈다:
  (1) **순수 정책**(DB 불필요·always-run): 상태 전이 허용표(``assert_channel_transition``),
      운영 전송 게이트(``is_operational``/``operational_channels``/``operational_delivery_rules`` —
      ``state == ACTIVE`` 채널만 실서비스 전송 대상), 활성 Kakao 방명 고유성
      (``assert_unique_kakao_rooms``). Telegram ``(chat_id, thread_id)`` 활성 충돌은 3.7 의
      ``assert_unique_telegram_topics`` 를 **재사용**(재구현 금지)한다.
  (2) **async 오케스트레이션**(:class:`ChannelRegistrationService`): 주입된 repository 로
      register→verify→activate→deactivate 전이를 수행한다. **상태 전이는 이 service 레이어에서만**
      일어난다(라우트/DB 직접 컬럼 변경 금지, architecture #State-Management).

설계 불변식(2.5~3.7 계승):
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만(``redact`` 재사용), ``rider_agent`` import 0.
  - 순수 정책은 SQLAlchemy/async 의존 0 — PG 부재 CI 에서도 always-run.
  - secret 비노출: 봇 토큰·webhook secret 은 이 모듈에 평문으로 들어오지 않는다(전송 token 은
    ``CentralTelegramSender.resolve_token`` 주입 seam). ``telegram_chat_id``/``thread_id``/
    ``kakao_room_name`` 은 라우팅·운영 식별자(secret 아님)지만, 예외 breadcrumb 는 chat_id/
    thread_id 를 ``redact()`` 통과로 가리고 Kakao 방명 값은 메시지에 싣지 않는다(자유 텍스트
    redact 는 운영 식별자를 마스킹하지 않음 — memory/redact-skips-operational-ids).

동기 send-only 어댑터(``CentralTelegramSender.send``=urllib)는 async ``verify`` 안에서
``asyncio.to_thread`` executor 경계로 감싸 이벤트 루프를 블로킹하지 않는다(async-boundary 가드).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from rider_crawl.redaction import redact
from rider_server.domain import (
    DeliveryRule,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
)
from rider_server.services.telegram_central_dispatch import assert_unique_telegram_topics


def _normalize_thread_id(raw: str | None) -> str | None:
    """``thread_id`` 정규화: ``None``/빈문자/공백 → ``None``, 그 외 strip 값.

    3.7 ``telegram_central_dispatch._normalize_thread_id`` 와 동형 의미(``None``↔``""`` 동일 키).
    등록 시점에 라우팅 식별자를 정규화해 ``assert_unique_telegram_topics`` 의 충돌 키와 일치시킨다.
    """

    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


# ── 상태 전이 허용표(정본) — 미정의 전이는 거부(직접 컬럼 변경 차단) ─────────────────
# register=진입(→PENDING), verify=PENDING→VERIFIED(테스트 메시지 성공 후), activate=VERIFIED→
# ACTIVE, deactivate=→INACTIVE(soft delete). INACTIVE→PENDING 은 재등록 reactivate.
# **`REGISTERED` 멤버는 없다** — 등록 진입 상태는 PENDING(정본 4멤버, count-lock).
ALLOWED_CHANNEL_TRANSITIONS: dict[MessengerChannelState, frozenset[MessengerChannelState]] = {
    MessengerChannelState.PENDING: frozenset(
        {MessengerChannelState.VERIFIED, MessengerChannelState.INACTIVE}
    ),
    MessengerChannelState.VERIFIED: frozenset(
        {MessengerChannelState.ACTIVE, MessengerChannelState.INACTIVE}
    ),
    MessengerChannelState.ACTIVE: frozenset({MessengerChannelState.INACTIVE}),
    MessengerChannelState.INACTIVE: frozenset({MessengerChannelState.PENDING}),
}


class InvalidChannelTransition(ValueError):
    """정의되지 않은 채널 상태 전이 시도(AC3). ``current``/``target`` 은 secret 아님."""

    def __init__(
        self, current: MessengerChannelState, target: MessengerChannelState
    ) -> None:
        super().__init__(
            f"invalid messenger channel transition: {current.value} -> {target.value}"
        )
        self.current = current
        self.target = target


def is_allowed_channel_transition(
    current: MessengerChannelState, target: MessengerChannelState
) -> bool:
    """``current`` → ``target`` 가 허용 전이인가(미정의 상태는 False)."""

    return target in ALLOWED_CHANNEL_TRANSITIONS.get(current, frozenset())


def assert_channel_transition(
    current: MessengerChannelState, target: MessengerChannelState
) -> None:
    """허용되지 않은 전이면 :class:`InvalidChannelTransition` 를 올린다."""

    if not is_allowed_channel_transition(current, target):
        raise InvalidChannelTransition(current, target)


# ── 운영 전송 게이트(순수) — `state == ACTIVE` 채널만 실서비스 전송 대상(AC2) ──────────


def is_operational(channel: MessengerChannel) -> bool:
    """채널이 실 운영 전송 대상인가 = ``state == ACTIVE`` 뿐.

    PENDING/VERIFIED/INACTIVE 는 검증 전·소프트삭제라 전송 대상이 아니다(미검증 채널로 실서비스
    전송 금지, FR-29). 3.7 ``find_telegram_topic_collisions`` 가 ACTIVE 만 보는 것과 일관.
    """

    return channel.state is MessengerChannelState.ACTIVE


def operational_channels(
    channels: Iterable[MessengerChannel],
) -> list[MessengerChannel]:
    """입력 순서를 보존해 운영 전송 대상(ACTIVE) 채널만 거른다(순수·결정적)."""

    return [channel for channel in channels if is_operational(channel)]


def operational_delivery_rules(
    rules: Sequence[DeliveryRule],
    *,
    channels: Mapping[str, MessengerChannel],
) -> list[DeliveryRule]:
    """DeliveryRule fan-out 전, 채널이 운영 대상(ACTIVE)인 rule 만 통과시킨다(순수).

    ``DispatchFanoutService.plan`` 앞단에 끼워 미검증/소프트삭제 채널로의 fan-out 을 차단한다
    (게이트를 거치게 함 — AC2). 채널 맵에 없는 rule(dangling)은 게이트에서 제외만 하고 surface
    는 ``plan`` 의 ``UnknownChannelError`` 에 맡긴다(여기는 ACTIVE 필터링 책임만).
    """

    return [
        rule
        for rule in rules
        if rule.channel_id in channels and is_operational(channels[rule.channel_id])
    ]


# ── 활성 Kakao 방명 고유성(순수) — Telegram 은 3.7 assert_unique_telegram_topics 재사용 ──


class KakaoRoomCollisionError(ValueError):
    """둘 이상의 활성 Kakao 채널이 같은 ``kakao_room_name`` 을 공유할 때(고유 방명 정책 위반).

    같은 이름의 방이 둘이면 오발송 위험(project-context: Kakao 는 정확한 방명 필수)이라 활성화를
    차단한다(fail-closed). ``ValueError`` 하위라 기존 처리부와 호환된다.
    """


def find_kakao_room_collisions(
    channels: Iterable[MessengerChannel],
) -> list[tuple[str, list[MessengerChannel]]]:
    """활성 Kakao 채널 중 같은 ``kakao_room_name`` 을 공유하는 충돌 그룹을 반환한다(순수).

    ``messenger == KAKAO and state == ACTIVE`` 이고 방명이 비어있지 않은 채널만 대상으로 방명별
    그룹핑해 **2개 이상** 묶인 그룹만 반환한다(입력 순서 보존·결정적). 빈 방명은 고유 키가 될 수
    없어 충돌 검출에서 제외한다(활성화 차단은 :class:`ChannelRegistrationService.activate` 의
    fail-closed 가 별도로 처리).
    """

    groups: dict[str, list[MessengerChannel]] = {}
    for channel in channels:
        if channel.messenger is not Messenger.KAKAO:
            continue
        if channel.state is not MessengerChannelState.ACTIVE:
            continue
        room = (channel.kakao_room_name or "").strip()
        if not room:
            continue
        groups.setdefault(room, []).append(channel)
    return [(room, members) for room, members in groups.items() if len(members) >= 2]


def assert_unique_kakao_rooms(channels: Iterable[MessengerChannel]) -> None:
    """활성 Kakao 채널 간 방명 충돌이 있으면 :class:`KakaoRoomCollisionError` 를 올린다.

    Kakao 방명은 운영 식별자(가게/방 이름일 수 있음)라 자유 텍스트 ``redact()`` 로는 마스킹되지
    않으므로(memory/redact-skips-operational-ids), 예외 메시지에 방명 값을 **싣지 않고** 충돌
    채널 id(불투명 FK — secret 아님)만 남긴다. 방어적으로 ``redact()`` 도 한 번 통과시킨다.
    """

    collisions = find_kakao_room_collisions(channels)
    if not collisions:
        return

    parts = [", ".join(channel.id for channel in members) for _room, members in collisions]
    message = (
        "활성 Kakao 채널이 같은 방명(kakao_room_name)을 공유합니다(고유 방명 정책 위반): "
        + "; ".join(f"[{ids}]" for ids in parts)
    )
    raise KakaoRoomCollisionError(redact(message))


# ── repository 포트 + in-memory 구현 ─────────────────────────────────────────────


class ChannelNotFoundError(LookupError):
    """전이 대상 채널이 repository 에 없을 때(``channel_id`` 는 불투명 id — secret 아님)."""

    def __init__(self, channel_id: str) -> None:
        super().__init__(f"messenger channel not found: {channel_id}")
        self.channel_id = channel_id


class ChannelRepository(Protocol):
    """채널 영속 포트 — register/verify/activate 가 의존하는 최소 인터페이스.

    PG 구현은 :class:`rider_server.services.channel_repository_postgres.PostgresChannelRepository`,
    테스트/무-DB 기본값은 :class:`InMemoryChannelRepository`.
    """

    async def get(self, channel_id: str) -> MessengerChannel | None: ...

    async def get_by_registration_code(
        self, code: str
    ) -> MessengerChannel | None: ...

    async def save(self, channel: MessengerChannel) -> None: ...

    async def active_channels(self) -> list[MessengerChannel]: ...


class InMemoryChannelRepository:
    """프로세스-내 채널 repository(무-DB 기본값 + 테스트 fake — ``InMemoryQueueBackend`` 선례)."""

    def __init__(self) -> None:
        self._by_id: dict[str, MessengerChannel] = {}
        self._code_to_id: dict[str, str] = {}

    def seed(
        self, channel: MessengerChannel, *, registration_code: str | None = None
    ) -> None:
        """사전 생성된 PENDING 채널 + 1회용 등록 코드를 주입한다(운영자 pre-provision 모사)."""

        self._by_id[channel.id] = channel
        if registration_code is not None:
            self._code_to_id[registration_code] = channel.id

    async def get(self, channel_id: str) -> MessengerChannel | None:
        return self._by_id.get(channel_id)

    async def get_by_registration_code(self, code: str) -> MessengerChannel | None:
        channel_id = self._code_to_id.get(code)
        return None if channel_id is None else self._by_id.get(channel_id)

    async def save(self, channel: MessengerChannel) -> None:
        self._by_id[channel.id] = channel

    async def active_channels(self) -> list[MessengerChannel]:
        return [
            channel
            for channel in self._by_id.values()
            if channel.state is MessengerChannelState.ACTIVE
        ]


# ── register 결과 값 객체 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegisterResult:
    """``/register <code>`` 처리 결과(불변).

    ``registered`` 가 False 면 알 수 없는 코드/빈 chat_id 라 아무 행도 갱신하지 않았다는 뜻이다
    (webhook 은 그래도 ``200 {"ok": true}`` 로 응답해 Telegram 재전송 폭주를 막는다).
    """

    channel: MessengerChannel | None
    registered: bool


# ── async lifecycle service(상태 전이는 여기서만) ──────────────────────────────────


class ChannelRegistrationService:
    """채널 register/verify/activate/deactivate 오케스트레이션(상태 전이 단일 소유처)."""

    def __init__(self, repository: ChannelRepository) -> None:
        self._repo = repository

    async def register(
        self, *, code: str, chat_id: str | None, thread_id: str | None = None
    ) -> RegisterResult:
        """``/register <code>`` — 코드로 식별된 채널에 chat_id/thread_id 를 저장한다(멱등).

        같은 chat 에서 재등록(중복 update)해도 깨지지 않는다: 동일 라우팅이 이미 기록된
        비-INACTIVE 채널은 no-op 으로 둔다(ACTIVE/VERIFIED 를 PENDING 으로 되돌리지 않음).
        알 수 없는 코드/빈 chat_id 는 갱신 없이 ``registered=False`` 로 반환한다(fail-closed).
        """

        channel = await self._repo.get_by_registration_code(code)
        if channel is None:
            return RegisterResult(channel=None, registered=False)

        normalized_chat = (chat_id or "").strip()
        if not normalized_chat:
            return RegisterResult(channel=channel, registered=False)
        normalized_thread = _normalize_thread_id(thread_id)

        already_routed = (
            channel.telegram_chat_id == normalized_chat
            and _normalize_thread_id(channel.thread_id) == normalized_thread
            and channel.state is not MessengerChannelState.INACTIVE
        )
        if already_routed:
            return RegisterResult(channel=channel, registered=True)  # 멱등 no-op

        updated = replace(
            channel,
            telegram_chat_id=normalized_chat,
            thread_id=normalized_thread,
            state=MessengerChannelState.PENDING,
        )
        await self._repo.save(updated)
        return RegisterResult(channel=updated, registered=True)

    async def verify(
        self,
        channel_id: str,
        *,
        send_test: Callable[[MessengerChannel], None],
    ) -> MessengerChannel:
        """PENDING→VERIFIED — 테스트 메시지가 성공한 **뒤에만** 검증으로 전이한다(AC2).

        동기 send-only 어댑터(``CentralTelegramSender.send``=urllib)를 ``asyncio.to_thread`` 로
        감싸 이벤트 루프 블로킹을 피한다. ``send_test`` 가 예외를 내면(테스트 메시지 실패) 전이
        없이 전파돼 채널은 PENDING 으로 남는다(미검증 채널 활성 금지).
        """

        channel = await self._repo.get(channel_id)
        if channel is None:
            raise ChannelNotFoundError(channel_id)
        assert_channel_transition(channel.state, MessengerChannelState.VERIFIED)
        # NOTE(5.10/AC3 kill switch): ``send_test`` 는 실 ``send`` (검증용 test 메시지)다. 현재
        #   ``verify`` 를 호출하는 live route/webhook 이 없어(인바운드 webhook 은 ``register`` 만
        #   처리) 현존 reachable chokepoint 가 아니므로 본 스토리는 게이트를 배선하지 않았다.
        #   향후 운영자 채널 검증 route 가 ``verify`` 를 ``send_test=CentralTelegramSender.send`` 로
        #   배선하면, 그 호출부에 ``recovery.effective_send_enabled(send_enabled=..,
        #   sending_enabled=app.state.sending_enabled)`` 게이트를 compose해야 한다(test_send·중앙
        #   dispatch 루프와 동일 — 미발송 fail-closed, 우회 금지).
        await asyncio.to_thread(send_test, channel)
        verified = replace(channel, state=MessengerChannelState.VERIFIED)
        await self._repo.save(verified)
        return verified

    async def activate(self, channel_id: str) -> MessengerChannel:
        """VERIFIED→ACTIVE — 활성 채널 간 고유성 강제 후에만 전송 대상으로 전이한다(AC2).

        Telegram 은 활성 ``(chat_id, thread_id)`` 충돌(3.7 ``assert_unique_telegram_topics``
        재사용), Kakao 는 활성 ``kakao_room_name`` 고유성(``assert_unique_kakao_rooms``)을 강제하고
        위반이면 활성화를 차단한다(fail-closed).
        """

        channel = await self._repo.get(channel_id)
        if channel is None:
            raise ChannelNotFoundError(channel_id)
        assert_channel_transition(channel.state, MessengerChannelState.ACTIVE)

        candidate = replace(channel, state=MessengerChannelState.ACTIVE)
        others = [c for c in await self._repo.active_channels() if c.id != channel.id]
        prospective = [*others, candidate]
        if channel.messenger is Messenger.TELEGRAM:
            assert_unique_telegram_topics(prospective)
        elif channel.messenger is Messenger.KAKAO:
            assert_unique_kakao_rooms(prospective)

        await self._repo.save(candidate)
        return candidate

    async def deactivate(self, channel_id: str) -> MessengerChannel:
        """현재 상태 → INACTIVE soft-delete(물리 삭제 금지, FR-4)."""

        channel = await self._repo.get(channel_id)
        if channel is None:
            raise ChannelNotFoundError(channel_id)
        assert_channel_transition(channel.state, MessengerChannelState.INACTIVE)
        updated = replace(channel, state=MessengerChannelState.INACTIVE)
        await self._repo.save(updated)
        return updated
