"""CentralTelegramSender — 중앙 send-only Telegram 어댑터 + 전송 scope·활성 토픽 충돌 검출(Story 3.7 / P2-07, FR-24·FR-26, NFR-1·5, ADD-11).

책임: 3.1~3.6이 분리해 둔 전송 경계(``DispatchFanoutService.dispatch_all``(3.4)·
``IdempotentDeliveryService.deliver_once``(3.5)·``DeliveryFailurePolicy.attempt_delivery``
(3.6))의 **주입 ``send`` 콜백**에 꽂아 쓸 **중앙(central) Telegram 전송 어댑터**를 제공한다.
기존 검증된 ``rider_crawl.sender.send_telegram_text``(Bot API 호출·슈퍼그룹 migrate·
ambiguous·retry-after 처리)를 **한 줄도 바꾸지 않고 재사용**하되, Agent별 ``getUpdates``
polling을 만들지 않는 **send-only 중앙 경로**로 둔다. 전송 대상 scope에
``chat_id + topic_id(message_thread_id)`` 조합(``TelegramRoute``)을 포함시키고, 둘 이상의
활성(ACTIVE) Telegram 채널이 같은 ``(chat_id, thread_id)`` 를 공유하지 않도록 검출하는
순수 정책 함수(``find_telegram_topic_collisions``/``assert_unique_telegram_topics``)를 둔다.

범위 경계(반드시 — 여기서 하지 않는 것):
  - 인바운드 webhook/``/register``/secret header·async dispatcher·실제 ``DeliveryLog``
    영속·DB UNIQUE(chat_id+topic)·등록 UI·scheduler 연동 = Epic 5(P4-06·FR-29). 본
    모듈은 **outbound sendMessage 어댑터**와 **순수 충돌 검출**만 정의한다.
  - dry-run 비교·old/new 동시 실전송 방지·legacy 폴러 물리 종료(cutover)·rollback =
    Story 3.8. legacy ``rider_crawl`` 폴러(``TelegramUpdatePoller``·``get_telegram_updates``)
    는 **무변경·보존** — 본 모듈은 그 폴러를 호출/생성/종료하지 않는다(send-only).
  - 채널별 ``DeliveryLog`` 생성·error_code 분류·재시도·release = 3.5/3.6. ``send`` 는
    실패 시 예외를 raise 만 하고(직접 로깅 재구현 없음), 분류·기록은 3.6 ``attempt_delivery``
    경계가 한다. ``classify`` 는 3.6 ``DeliveryFailurePolicy.channel_failure_category``
    (TELEGRAM → ``TELEGRAM_FAILURE``) 재사용 — 신규 매핑 미추가.
  - Bot API quirk(슈퍼그룹 ``migrate_to_chat_id``·``retry-after`` backoff·``ok!=true`` 에러·
    JSON 검증·ambiguous 표시)는 ``send_telegram_text`` 가 소유 — 여기서 재구현 금지.

설계 불변식(2.5/2.6/3.1~3.6 계승):
  - 순수·결정적·동기·의존성 0: FastAPI/SQLAlchemy/async 없음. 내부에서 ``datetime.now()``/
    ``uuid4()``/``random``/실 ``time.sleep`` 을 호출하지 않는다 — bot token은
    ``resolve_token`` 콜백 주입(secret store seam), HTTP transport는 ``urlopen`` 주입,
    재시도는 **단일 시도(``retry_attempts=1``)** 로 두고 backoff/재시도는 3.6이 소유
    (이중 재시도 금지).
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만(``send_telegram_text``·``redact``·
    ``AppConfig`` 재사용), 역방향 0.
  - frozen 불변: ``TelegramRoute`` 는 ``@dataclass(frozen=True)``.
  - 비노출(NFR-5, project-context 81): bot token은 ``resolve_token`` 주입으로만 들어오고
    어떤 로그/예외에도 평문으로 남지 않는다. ``telegram_chat_id``/``thread_id`` 는 라우팅
    식별자(secret 아님)지만, 본 모듈이 만드는 예외 breadcrumb는 ``redact()`` 를 통과한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from rider_crawl.config import AppConfig
from rider_crawl.redaction import redact
from rider_crawl.sender import TelegramSendError, send_telegram_text
from rider_server.domain import Messenger, MessengerChannel, MessengerChannelState
from rider_server.services.dispatch_fanout_service import DispatchJob, UnknownChannelError

# transport seam 타입(= ``send_telegram_text`` 의 ``urlopen`` 인자와 동일).
UrlOpen = Callable[..., object]

# placeholder 파일 경로(전송과 무관 — ``send_telegram_text`` 는 log_dir/browser dir를 읽지 않는다).
_PLACEHOLDER_PATH = Path(".")


def _normalize_thread_id(raw: str | None) -> str | None:
    """``thread_id`` 를 정규화한다: ``None``/빈문자/공백 → ``None``, 그 외는 strip 값.

    legacy ``telegram_commands`` 의 ``(chat_id, thread_id)`` 라우팅 키 정규화 의미와 동형 —
    ``None`` 과 ``""`` 를 동일 키로 취급해(AC3) 충돌 오검출을 막는다.
    """

    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


@dataclass(frozen=True)
class TelegramRoute:
    """Telegram 전송 대상 scope 식별자 = ``(chat_id, thread_id)`` (불변).

    ``GateDecision``(2.6)·``DispatchJob``/``FanoutOutcome``(3.4)·``RetryDecision``(3.6)
    선례처럼 domain 레코드가 아니라 services-레이어 값 객체다(``domain/`` 추가 아님).
    ``thread_id`` 는 ``_normalize_thread_id`` 로 정규화돼 ``None``↔``""`` 가 같은 scope로
    묶인다(라우팅·충돌 검출 일관).
    """

    chat_id: str
    thread_id: str | None = None

    @classmethod
    def from_channel(cls, channel: MessengerChannel) -> "TelegramRoute":
        """``MessengerChannel`` 에서 전송 scope를 도출한다(fail-closed).

        Telegram 채널이 아니거나 ``telegram_chat_id`` 가 비어 있으면 ``ValueError`` 로
        막는다 — 다른 chat/topic 오발송보다 미발송이 안전하다(NFR-1).
        """

        if channel.messenger is not Messenger.TELEGRAM:
            raise ValueError(
                f"TelegramRoute requires a TELEGRAM channel, got {channel.messenger!r}"
            )
        chat_id = (channel.telegram_chat_id or "").strip()
        if not chat_id:
            raise ValueError("TelegramRoute requires a non-empty telegram_chat_id")
        return cls(chat_id=chat_id, thread_id=_normalize_thread_id(channel.thread_id))


def _app_config_for(channel: MessengerChannel, token: str) -> AppConfig:
    """``send_telegram_text`` 재사용을 위한 per-channel ``AppConfig`` carrier를 만든다.

    ``send_telegram_text`` 는 ``telegram_bot_token``/``telegram_chat_id``(+옵션
    ``telegram_message_thread_id``)만 읽으므로(``sender.py`` 90-100) 그 셋만 의미값으로
    채우고, 나머지 required 필드는 send에 무관한 **안전 placeholder**(빈 문자열/False/0)로
    둔다. ``thread_id`` 는 ``send_telegram_text(message_thread_id=...)`` 인자로 넘기므로
    carrier에는 비워 둔다. token은 carrier 안에서만 쓰이고 로그/예외엔 남지 않는다.
    """

    return AppConfig(
        coupang_eats_url="",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="",
        cdp_url="",
        browser_user_data_dir=_PLACEHOLDER_PATH,
        headless=False,
        kakao_chat_name="",
        log_dir=_PLACEHOLDER_PATH,
        send_enabled=False,
        send_only_on_change=False,
        timezone="",
        run_lock_timeout_seconds=0,
        page_timeout_seconds=0,
        telegram_bot_token=token,
        telegram_chat_id=channel.telegram_chat_id or "",
        telegram_message_thread_id="",
    )


def is_ambiguous_send_failure(exc: Exception) -> bool:
    """전송 실패가 ambiguous(전송 성공/실패 불명)인지 판정한다(오발송보다 미발송).

    ``send_telegram_text`` 는 POST 후 응답을 못 읽거나 연결이 끊긴 경우
    ``TelegramSendError(ambiguous=True)`` 를 낸다(메시지가 이미 전달됐을 수 있음). 이 경우
    재전송하면 같은 실적이 중복 발송될 위험이 있으므로(disaster), 호출부(또는 3.6 compose
    시 release 결정)는 **미발송 dedup key를 release하지 않아야** 한다 — release하지 않으면
    3.5 insert-then-send로 선확보된 key가 유지돼 다음 라운드 reserve 충돌→
    ``DUPLICATE_BLOCKED`` 로 재전송이 구조적으로 차단된다(project-context 87·94, NFR-1).
    definite 실패(명확한 HTTP 4xx/검증)는 재시도 가능(``TELEGRAM_FAILURE`` release/재시도).
    """

    return isinstance(exc, TelegramSendError) and bool(getattr(exc, "ambiguous", False))


@dataclass(frozen=True)
class CentralTelegramSender:
    """중앙 send-only Telegram 전송 어댑터(주입 의존만 보유, 불변).

    ``dispatch_all(send=...)``(3.4)·``attempt_delivery(send=...)``(3.6) seam에 꽂을
    ``(job, text) -> None`` 콜백을 제공한다. 채널 라우팅 + token resolver 주입 + transport
    (``urlopen``) 주입으로 결정적·secret 비노출이며, ``send_telegram_text`` 를 단일 시도로
    재사용한다(getUpdates/polling 미호출).
    """

    channels: Mapping[str, MessengerChannel]
    resolve_token: Callable[[MessengerChannel], str]
    urlopen: UrlOpen
    timeout_seconds: int = 10

    def send(self, job: DispatchJob, text: str) -> None:
        """단일 ``job`` 을 중앙 경로로 ``sendMessage`` 한다(send-only, 단일 시도).

        실패 시 ``send_telegram_text`` 의 ``TelegramSendError`` 를 그대로 전파한다 —
        분류·``DeliveryLog`` 기록·재시도는 3.6 ``attempt_delivery`` 경계가 한다(compose).
        """

        try:
            channel = self.channels[job.channel_id]
        except KeyError as exc:
            # dangling channel = 설정 무결성 버그 → surface(fail-closed, 3.4 선례 재사용).
            raise UnknownChannelError(job.channel_id) from exc
        if channel.messenger is not Messenger.TELEGRAM:
            raise ValueError(
                f"CentralTelegramSender only sends TELEGRAM, got {channel.messenger!r}"
            )

        route = TelegramRoute.from_channel(channel)
        token = self.resolve_token(channel)
        config = _app_config_for(channel, token)

        # Bot API quirk(슈퍼그룹 migrate·retry-after·ambiguous·ok!=true)는 send_telegram_text
        # 가 소유 — 여기서 재구현 금지. retry_attempts=1로 단일 시도(재시도/backoff=3.6,
        # 이중 재시도 금지). transport(urlopen)·token(resolve_token) 주입으로 결정적·secret
        # 비노출(실 sleep 미호출 — 단일 시도라 어차피 안 불림).
        send_telegram_text(
            config,
            text,
            message_thread_id=int(route.thread_id) if route.thread_id else None,
            urlopen=self.urlopen,
            timeout_seconds=self.timeout_seconds,
            retry_attempts=1,
            sleep=lambda *_: None,
        )

    def as_send_callback(self) -> Callable[[DispatchJob, str], None]:
        """``dispatch_all``/``attempt_delivery`` 의 ``send`` seam에 그대로 꽂을 콜백 반환."""

        return self.send


class TelegramTopicCollisionError(ValueError):
    """둘 이상의 활성 Telegram 채널이 같은 ``(chat_id, thread_id)`` 를 공유할 때.

    설정 무결성 버그(오발송/명령 라우팅 혼선)를 조용히 삼키지 않고 surface 한다
    (fail-closed, project-context 91). ``ValueError`` 하위라 기존 처리부와 호환된다.
    """


def find_telegram_topic_collisions(
    channels: Iterable[MessengerChannel],
) -> list[tuple[TelegramRoute, list[MessengerChannel]]]:
    """활성 Telegram 채널 중 같은 전송 scope를 공유하는 충돌 그룹을 반환한다(순수).

    ``messenger == TELEGRAM and state == ACTIVE`` 만 대상으로 ``TelegramRoute``
    (정규화 thread_id)로 그룹핑해 **2개 이상** 묶인 그룹만 반환한다(입력 순서 보존·결정적).
    비활성(PENDING/VERIFIED/INACTIVE)·Kakao 채널·서로 다른 (chat,topic) 조합은 충돌로
    오검출되지 않는다. 등록 시점 실제 강제·DB UNIQUE는 Epic 5(FR-29) 소유 — 본 함수는
    순수 검출까지.
    """

    groups: dict[TelegramRoute, list[MessengerChannel]] = {}
    for channel in channels:
        if channel.messenger is not Messenger.TELEGRAM:
            continue
        if channel.state is not MessengerChannelState.ACTIVE:
            continue
        route = TelegramRoute.from_channel(channel)
        groups.setdefault(route, []).append(channel)
    # dict는 삽입 순서를 보존 → 입력 순서대로 결정적 반환.
    return [(route, members) for route, members in groups.items() if len(members) >= 2]


def assert_unique_telegram_topics(channels: Iterable[MessengerChannel]) -> None:
    """활성 Telegram 채널 간 전송 scope 충돌이 있으면 ``TelegramTopicCollisionError`` raise.

    예외 메시지는 ``redact()`` 를 통과해 chat_id 숫자·thread_id가 평문으로 남지 않는다
    (NFR-5). 충돌 그룹의 채널 id(불투명 FK — secret 아님)는 운영자 진단용으로 남긴다.
    """

    collisions = find_telegram_topic_collisions(channels)
    if not collisions:
        return

    parts = []
    for route, members in collisions:
        member_ids = ", ".join(channel.id for channel in members)
        # chat_id=/thread_id= key=value 형태로 둬 redact()가 값(숫자)을 마스킹하게 한다.
        parts.append(
            f"chat_id={route.chat_id} thread_id={route.thread_id}: [{member_ids}]"
        )
    message = (
        "활성 Telegram 채널이 같은 전송 scope(chat_id+topic_id)를 공유합니다"
        "(설정 무결성 오류): " + "; ".join(parts)
    )
    raise TelegramTopicCollisionError(redact(message))
