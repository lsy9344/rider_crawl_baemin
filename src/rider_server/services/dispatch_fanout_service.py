"""DispatchFanoutService — 한 Message → N 채널 fan-out + 채널 격리 전송(Story 3.4 / P2-04, FR-9).

책임: 하나의 렌더된 ``Message``(3.3)를 그 대상에 연결된 **활성 ``DeliveryRule``**(2.5)마다
별도의 fan-out 단위 ``DispatchJob`` 으로 펼치고(``plan``), 채널별 전송을 서로 격리해
한 채널 실패가 다른 채널 성공을 무효화하지 않게 한다(``dispatch_all``). 3.1
``DispatchService.dispatch``(단일 전송 parity)는 그대로 두고 fan-out만 additive로 붙인다.

범위 경계(반드시 — 여기서 하지 않는 것):
  - DeliveryLog/idempotency dedup key(``target_id + channel_id + collected_at +
    template_version + message_hash``)·insert-then-send·중복 차단 = Story 3.5.
  - 채널별 실패 상태 분류·재시도·``AUTH_REQUIRED``·backoff = Story 3.6.
    ``FanoutOutcome.error_redacted`` 는 redaction 통과한 **분류 안 된 breadcrumb** 일 뿐
    운영 상태값이 아니다.
  - Telegram 중앙 sendMessage/webhook = Story 3.7. ``dispatch_all`` 은 **주입된 sender
    콜백**만 호출한다(중앙/per-Agent 경로 선택은 호출부 책임).
  - ``jobs``/``delivery_logs`` 테이블·ORM/Alembic·Pydantic·async wiring·런타임 교체·
    tenant 템플릿(``template_id``) = Epic 5. Kakao 실제 PC 자동화 전송 = Epic 4.

영속 매핑 의도: ``DispatchJob`` 은 독립 계약 테이블이 아니라 영속 시 generic ``jobs``
(type=DISPATCH_TELEGRAM/KAKAO_SEND) + ``delivery_logs`` 로 매핑되는 전송 파이프라인
단위다 — 그래서 ``DispatchResult``(3.1)와 같은 services 레이어 값 객체로 둔다(domain 아님).

설계 불변식:
  - 순수·결정적·의존성 0: 내부 ``datetime.now()``/``uuid4()`` 미호출 — ``DispatchJob.id``
    는 ``job_id_for`` 콜백으로 호출부가 주입한다(2.5/3.2/3.3 선례).
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만(``redact`` 재사용), 역방향 0.
  - frozen 불변: ``DispatchJob``/``FanoutOutcome`` 은 ``@dataclass(frozen=True)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from rider_crawl.redaction import redact
from rider_server.domain import DeliveryRule, Message, Messenger, MessengerChannel


class UnknownChannelError(KeyError):
    """``plan`` 의 ``channels`` 맵에 ``rule.channel_id`` 가 없을 때(dangling FK).

    설정 무결성 버그를 조용히 미전송으로 삼키지 않고 surface 한다(fail-closed,
    project-context 36 "조용히 기본값 금지"). ``KeyError`` 하위라 기존 ``KeyError``
    처리부와 호환된다.
    """


@dataclass(frozen=True)
class DispatchJob:
    """한 ``DeliveryRule`` × 한 ``Message`` 의 fan-out 전송 단위(불변).

    필드의 ``target_id``·``channel_id``·``template_version``·``message_hash`` 4개는
    3.5 dedup key 5차원 중 4개를 **보존만** 한다(``collected_at`` 은 ``message_id``→snapshot
    조인으로 도달). 본 스토리는 key를 조립·비교·기록하지 않는다(3.5 소유). ``channel_id``
    보존이 AC3 scope 비축소(채널별 독립 변경 판단)의 핵심이다. ``text`` 는 중복 보관하지
    않는다(단일 정본 = ``Message.text`` — ``dispatch_all`` 이 sender에 넘긴다).
    """

    id: str  # 영속 시 jobs.id — 호출부 주입(job_id_for; 서비스 내부 uuid4() 금지)
    target_id: str  # rule.target_id → MonitoringTarget (dedup 차원)
    channel_id: str  # rule.channel_id → MessengerChannel (dedup 차원 — scope 비축소 핵심)
    message_id: str  # message.id → Message(→ snapshot_id → collected_at, 3.5 조인)
    messenger: Messenger  # channel.messenger 로 derive (TELEGRAM/KAKAO 라우팅 enum)
    template_version: str  # message.template_version (dedup 차원)
    message_hash: str  # message.text_hash = sha256(text) (dedup 차원)


@dataclass(frozen=True)
class FanoutOutcome:
    """한 ``DispatchJob`` 전송 시도의 격리된 결과(불변).

    ``error_redacted`` 는 ``redact(...)`` 통과한 breadcrumb(또는 None)일 뿐 — error_code
    분류·재시도·``AUTH_REQUIRED`` 상태 전이를 하지 않는다(3.6 위임). 운영 카테고리 필드를
    여기 추가하지 않는다(3.6 선점 금지).
    """

    job: DispatchJob
    sent: bool
    error_redacted: str | None = None


class DispatchFanoutService:
    """한 Message → 활성 채널마다 ``DispatchJob`` 생성 + 채널 격리 전송(순수 정적)."""

    @staticmethod
    def plan(
        message: Message,
        rules: Sequence[DeliveryRule],
        *,
        channels: Mapping[str, MessengerChannel],
        job_id_for: Callable[[DeliveryRule], str],
    ) -> list[DispatchJob]:
        """활성 ``rules`` 순서대로 ``DispatchJob`` 을 생성해 반환(입력 순서 보존).

        호출부 계약: ``rules`` 는 **이미 해당 대상(``target_id``)으로 필터된** 활성/비활성
        혼합 후보다(대상 scope 쿼리는 Epic 5 소유). ``plan`` 은 ``target_id`` 일관성을
        message로부터 재검증하지 않는다(``Message`` 는 ``snapshot_id`` 만 보유). ``channels``
        는 ``channel_id → MessengerChannel`` 조회 맵(라우팅 enum derive용).

        - ``rule.enabled`` 가 False면 skip(soft delete 제외, AC1.2).
        - ``channels`` 에 ``rule.channel_id`` 가 없으면 :class:`UnknownChannelError`
          (dangling FK = 설정 무결성 버그 → surface, fail-closed).
        - 내부 ``uuid4()``/``now()`` 미호출 — ``id`` 는 ``job_id_for`` 주입(결정적).
        """

        jobs: list[DispatchJob] = []
        for rule in rules:
            if not rule.enabled:
                continue
            try:
                channel = channels[rule.channel_id]
            except KeyError as exc:
                raise UnknownChannelError(rule.channel_id) from exc
            jobs.append(
                DispatchJob(
                    id=job_id_for(rule),
                    target_id=rule.target_id,
                    channel_id=rule.channel_id,
                    message_id=message.id,
                    messenger=channel.messenger,
                    template_version=message.template_version,
                    message_hash=message.text_hash,
                )
            )
        return jobs

    @staticmethod
    def dispatch_all(
        message: Message,
        jobs: Sequence[DispatchJob],
        *,
        send: Callable[[DispatchJob, str], None],
    ) -> list[FanoutOutcome]:
        """각 ``job`` 을 ``send`` 로 전송하되 서로 격리한다(입력 순서 보존, AC2).

        한 ``job`` 의 예외가 다음 ``job`` 전송을 막지 않는다(채널 격리) — 실패는
        ``FanoutOutcome(sent=False, error_redacted=redact(repr(exc)))`` 로 contain 만
        하고 error_code 분류/재시도/상태 전이는 하지 않는다(3.6 위임). ``send`` 는 필수
        주입 seam — 중앙 Telegram(3.7)/Kakao Agent(Epic 4)/실 sender 배선은 호출부 책임이며
        본 서비스는 기본 adapter를 두지 않는다(채널별 라우팅이 Epic 5 config 배선 전이므로).
        """

        outcomes: list[FanoutOutcome] = []
        for job in jobs:
            try:
                send(job, message.text)
                outcomes.append(FanoutOutcome(job=job, sent=True, error_redacted=None))
            except Exception as exc:  # 채널 격리: 한 채널 실패가 루프를 중단시키지 않음(AC2)
                outcomes.append(
                    FanoutOutcome(job=job, sent=False, error_redacted=redact(repr(exc)))
                )
        return outcomes
