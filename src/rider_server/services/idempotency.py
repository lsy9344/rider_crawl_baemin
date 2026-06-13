"""IdempotentDeliveryService — 5필드 dedup key + insert-then-send 멱등 전송(Story 3.5 / P2-05, FR-10, ADD-5).

책임: 3.4가 만든 한 ``DispatchJob`` 을 실제 전송하는 경계에 **5필드 dedup key**
(``monitoring_target_id + messenger_channel_id + snapshot_collected_at +
template_version + message_hash``)로 idempotency를 강제하고 그 결과를 ``DeliveryLog``
로 남긴다. 핵심은 **성공 전송 전에 유니크 제약을 먼저 확보하는 insert-then-send**
(``reserve(key)`` → 성공이면 ``send(job)``)라, 전송 직후 상태 기록 전 크래시
(crash-after-send)에도 재시도 시 같은 key의 reserve 충돌로 **재전송되지 않는다**.
architecture 428이 ``services/idempotency.py # dedup key + insert-then-send`` 를 정본
위치로 못 박았다.

범위 경계(반드시 — 여기서 하지 않는 것):
  - 채널별 실패 error_code 분류·재시도·``AUTH_REQUIRED``·backoff·circuit breaker·
    reserve **release**(미발송 key 회수) = Story 3.6. ``deliver_once`` 의 ``send``
    예외는 분류·삼킴 없이 **호출부로 전파**하고 ``DeliveryLog.error_code`` 는 항상 None.
  - 채널 격리 fan-out 루프(``dispatch_all``) = Story 3.4. 본 서비스는 **단일 job
    idempotent primitive(``deliver_once``)** 만 제공한다 — 이를 ``dispatch_all`` 의
    ``send`` 콜백에 조립하는 것은 Epic 5 wiring이다(``deliver_all`` 배치 메서드 미추가).
  - Telegram 중앙 sendMessage/webhook = Story 3.7. ``send`` 는 **주입된 콜백**일 뿐.
  - ``delivery_logs`` 테이블·``uq_delivery_logs_dedup_key`` DB UNIQUE·ORM/Alembic·
    Pydantic·async session·런타임 sender 배선·migration seed 적재 = Epic 5. 본 스토리의
    "유니크 제약"은 **주입된 ``reserve`` 콜백 seam**(in-memory fake로 테스트)으로 표현하고,
    실제 DB UNIQUE 인덱스는 Epic 5가 같은 dedup key 위에 건다.

설계 불변식(2.5/2.6/3.1~3.4 계승):
  - 순수·결정적·의존성 0: FastAPI/SQLAlchemy/async 없음. 내부에서 ``datetime.now()``/
    ``uuid4()`` 를 호출하지 않는다 — ``DeliveryLog.id``(``log_id_for``)·``sent_at``·
    ``collected_at`` 은 모두 호출부 주입.
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만, 역방향 0.
  - 비노출(NFR-5, ADD-15): dedup key는 ``target_id``·``channel_id``(불투명 FK)·
    ``collected_at``·``template_version``·``message_hash``(이미 sha256 hex)로만 구성 —
    chat_id 숫자·room_name 원문·봇 토큰·비밀번호·OTP를 담지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from rider_server.domain import DeliveryLog, DeliveryStatus
from rider_server.services.dispatch_fanout_service import DispatchJob

# dedup key 구분자: 불투명 id(mt-1/ch-tg)·sha256 hex·ISO-8601·template_version 어디에도
# 등장하지 않아 차원 경계가 모호해지지 않는다(app.py:_message_scope_key 의 join 스타일 계승).
_KEY_SEP = "|"


class IdempotentDeliveryService:
    """5필드 dedup key 합성 + insert-then-send 멱등 전송 primitive(순수 정적)."""

    @staticmethod
    def build_dedup_key(
        *,
        target_id: str,
        channel_id: str,
        collected_at: datetime,
        template_version: str,
        message_hash: str,
    ) -> str:
        """5차원을 안정적·결정적으로 합성한 dedup key를 반환한다(축소 금지).

        ``monitoring_target_id``(=``target_id``) + ``messenger_channel_id``
        (=``channel_id``) + ``snapshot_collected_at``(=``collected_at`` ``.isoformat()``
        정규화) + ``template_version`` + ``message_hash`` **5필드 전량**을 ``"|"`` 로
        join한다(data-api-contract 146-156). 같은 입력 → 같은 key, 어느 한 차원이라도
        다르면 key가 달라진다(오차단 방지·scope 비축소). 내부 ``now()`` 미호출 —
        시각은 ``collected_at`` 인자(``Snapshot.collected_at`` 호출부 주입).

        DB 컬럼 길이를 위한 sha256-wrapping 여부는 Epic 5 영속 레이어 결정으로 두되,
        **논리 key는 5필드 전량을 결정**한다.
        """

        return _KEY_SEP.join(
            (
                target_id,
                channel_id,
                collected_at.isoformat(),
                template_version,
                message_hash,
            )
        )

    @staticmethod
    def deliver_once(
        job: DispatchJob,
        *,
        collected_at: datetime,
        reserve: Callable[[str], bool],
        send: Callable[[DispatchJob], None],
        log_id_for: Callable[[DispatchJob], str],
        sent_at: datetime,
    ) -> DeliveryLog:
        """단일 ``job`` 을 insert-then-send로 멱등 전송하고 ``DeliveryLog`` 를 반환한다.

        순서가 전부다: ``reserve(key)`` → (성공이면) ``send(job)`` → ``SENT`` 기록.
        - ``reserve(dedup_key) -> bool`` 은 **성공 전송 전 유니크 제약 확보(INSERT) seam**.
          새로 확보=``True``(전송 진행), 이미 확보됨(=SENT 레코드 존재)=``False``(중복 차단).
          유니크 제약은 **성공(``SENT``) 레코드에만** 적용된다(architecture 173) — 실제 DB
          ``uq_delivery_logs_dedup_key`` UNIQUE는 Epic 5가 같은 key 위에 건다.
        - reserve 충돌(``False``)이면 ``send`` 를 **호출하지 않은 채**
          ``DeliveryLog(status=DUPLICATE_BLOCKED, sent_at=None)`` 를 반환한다 — 차단된
          전송도 ``DeliveryLog`` 로 관측 가능한 audit 기록이다(NFR-15). 이 레코드는
          reserve를 다시 시도하지 않으므로 유니크 제약과 충돌하지 않는다.
        - reserve 성공이면 ``send(job)`` 호출(유니크 제약 확보 **후** 전송 →
          crash-after-send 안전) 후 ``DeliveryLog(status=SENT, sent_at=주입)`` 반환.
        - ``send`` 예외는 try/except로 삼키지 않고 **호출부로 전파**한다(분류·재시도·
          release = 3.6). ``error_code`` 는 항상 None.

        ``id``/``sent_at``/``collected_at`` 은 모두 주입 — 내부 ``uuid4()``/``now()`` 미호출.
        """

        key = IdempotentDeliveryService.build_dedup_key(
            target_id=job.target_id,
            channel_id=job.channel_id,
            collected_at=collected_at,
            template_version=job.template_version,
            message_hash=job.message_hash,
        )

        # insert-then-send: 유니크 제약을 send 보다 먼저 확보한다(AC2). 충돌이면 중복 차단.
        if not reserve(key):
            return DeliveryLog(
                id=log_id_for(job),
                message_id=job.message_id,
                channel_id=job.channel_id,
                status=DeliveryStatus.DUPLICATE_BLOCKED,
                dedup_key=key,
                error_code=None,
                sent_at=None,
            )

        # key 확보 성공 → 그제서야 전송(send 예외는 분류 없이 전파 — 3.6 경계).
        send(job)
        return DeliveryLog(
            id=log_id_for(job),
            message_id=job.message_id,
            channel_id=job.channel_id,
            status=DeliveryStatus.SENT,
            dedup_key=key,
            error_code=None,
            sent_at=sent_at,
        )
