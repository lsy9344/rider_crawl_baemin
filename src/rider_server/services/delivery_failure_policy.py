"""DeliveryFailurePolicy — 실패 분류·error_code별 backoff 재시도 결정·실패-인지 전송(Story 3.6 / P2-06, FR-11·26, NFR-4·15, ADD-15).

책임: 3.1~3.5가 분리해 둔 수집/렌더/전송 단계가 던지는 실패를 운영 카테고리
(``FailureCategory``)로 분류하고, **인증 필요(사람 개입)** 와 **일시적(재시도 가능)** 실패를
구분해 ``RetryDecision`` 으로 판정한다 — 일시 실패는 ``error_code`` 별 **제한된 backoff
재시도**(고정 5초·무한 재시도 금지), 인증 필요는 **무한 재시도 없이 ``HELD`` 보류**. 그리고
3.5 ``deliver_once`` 위에 분류 + release + 재시도 결정을 얹은 **실패-인지 전송 primitive**
(``attempt_delivery``)를 제공해, 같은 Snapshot에서 **실패한 채널만 재시도**(미발송 dedup key
회수)하고 **이미 성공한 채널은 3.5 idempotency로 중복 발송되지 않게** 한다.

범위 경계(반드시 — 여기서 하지 않는 것):
  - scheduler 레벨 **circuit breaker·schedule jitter** = Story 5.4(FR-33). 본 서비스는
    **단일 작업의 error_code별 backoff·제한 재시도·parser 반복 실패 경고 판정**까지만 한다.
    ``backoff_delay_seconds`` 는 **결정적**(jitter 미포함 — jitter는 5.4 주입).
  - Telegram 중앙 sendMessage/webhook = Story 3.7. ``attempt_delivery`` 의 ``send``/
    ``classify`` 는 **주입 콜백** — 중앙/per-Agent 경로 선택·실 sender는 호출부 책임.
  - ``jobs``/``delivery_logs`` 테이블·``attempts``/``run_after``/``error_code`` 컬럼 영속·
    DB UNIQUE·ORM/Alembic·Pydantic·async wiring·실제 release(미발송 reservation 행 삭제)·
    재큐잉·배민 auth 실제 감지·Kakao 실전송 = Epic 5/4. 본 서비스는 **결정(release 할지)** 과
    **주입 seam**(``reserve``/``send``/``release``/``classify``/``log_id_for``/``sent_at``/
    ``attempt``)만 제공한다(in-memory fake로 테스트).

설계 불변식(2.5/2.6/3.1~3.5 계승):
  - 순수·결정적·의존성 0: FastAPI/SQLAlchemy/async 없음. 내부에서 ``datetime.now()``/
    ``uuid4()``/``random`` 을 호출하지 않는다 — ``attempt``·``sent_at``·``log_id_for``·
    release/send/reserve/classify는 호출부 주입, backoff는 ``attempt`` 의 결정적 함수.
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만, 역방향 0.
  - frozen 불변: ``RetryDecision``/``DeliveryAttemptResult`` 은 ``@dataclass(frozen=True)``.
  - 비노출(NFR-5, ADD-15): ``DeliveryLog.error_code`` 에는 ``FailureCategory`` 코드 값만
    담는다 — 예외 원문·chat_id·봇 토큰·비밀번호·OTP를 담지 않는다(``classify`` 가 예외를
    카테고리로 매핑하므로 예외 내용이 로그로 새지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from rider_server.domain import DeliveryLog, DeliveryStatus, FailureCategory, Messenger
from rider_server.services.dispatch_fanout_service import DispatchJob
from rider_server.services.idempotency import IdempotentDeliveryService

# 분류 집합 정본(architecture 326-327·329). 모듈 상수로 둬 decide/is_retryable 가 공유한다.
# 일시(재시도 가능) 실패: 네트워크/서버/페이지 일시 오류 → error_code별 backoff 제한 재시도.
_RETRYABLE: frozenset[FailureCategory] = frozenset(
    {
        FailureCategory.TELEGRAM_FAILURE,
        FailureCategory.KAKAO_FAILURE,
        FailureCategory.CRAWL_FAILURE,
    }
)
# 사람-개입 필요 실패: 무한 재시도 금지 → HELD 보류(운영자 조치 전 fail-closed).
_HUMAN_INTERVENTION: frozenset[FailureCategory] = frozenset(
    {
        FailureCategory.AUTH_REQUIRED,
        FailureCategory.TARGET_VALIDATION_FAILURE,
    }
)
# RENDER_FAILURE 는 결정적(같은 Snapshot 재렌더 = 동일 실패) → FAILED(재시도 무의미·가시화).
# DUPLICATE_BLOCKED 는 실패가 아니라 정상 결과(3.5 deliver_once 처리) — decide 실패 경로 밖.

# backoff 기본값(합리적 예 — 호출부 조정 가능). 고정 5초·0초·무한 금지(ADD-15·FR-33).
_DEFAULT_BASE_SECONDS = 30
_DEFAULT_FACTOR = 2
_DEFAULT_CAP_SECONDS = 900

# parser 반복 실패 경고 기본 임계치(운영자 가시화 — circuit breaker는 5.4).
_DEFAULT_PARSER_WARNING_THRESHOLD = 3


@dataclass(frozen=True)
class RetryDecision:
    """실패 판정 결과 값 객체(불변). 2.6 ``GateDecision`` 패턴 계승.

    ``delay_seconds`` 는 **재시도(``RETRYING``)일 때만** 값을 갖고, 보류(``HELD``)·소진/결정적
    (``FAILED``)은 ``None`` 이다(다음 실행 시각 ``jobs.run_after`` = ``now + delay`` 계산은
    Epic 5 — 본 결정은 delay만 결정).
    """

    should_retry: bool
    status: DeliveryStatus
    error_code: FailureCategory
    attempt: int
    delay_seconds: int | None = None


@dataclass(frozen=True)
class DeliveryAttemptResult:
    """``attempt_delivery`` 한 번의 결과 값 객체(불변).

    ``decision`` 은 **실패 시에만** 분류 결정을 담는다 — ``SENT``/``DUPLICATE_BLOCKED``
    (실패 아님, ``deliver_once`` 위임)은 ``None`` 이다.
    """

    log: DeliveryLog
    decision: RetryDecision | None = None


class DeliveryFailurePolicy:
    """실패 분류·재시도 결정 + ``deliver_once`` 위 실패-인지 전송(순수 정적 서비스)."""

    @staticmethod
    def is_retryable(category: FailureCategory) -> bool:
        """일시(재시도 가능) 실패면 True. 사람-개입/결정적/비실패는 False.

        ``TELEGRAM_FAILURE``/``KAKAO_FAILURE``/``CRAWL_FAILURE`` = True;
        ``AUTH_REQUIRED``/``TARGET_VALIDATION_FAILURE``/``RENDER_FAILURE`` = False;
        ``DUPLICATE_BLOCKED`` = False(실패 아님).
        """

        return category in _RETRYABLE

    @staticmethod
    def channel_failure_category(messenger: Messenger) -> FailureCategory:
        """채널 전송 실패를 채널 카테고리로 매핑(``TELEGRAM`` → ``TELEGRAM_FAILURE``,
        ``KAKAO`` → ``KAKAO_FAILURE``). 호출부(3.7/Epic 5)가 채널 예외를 분류할 때 쓰는 helper.

        미지 messenger는 fail-closed(``ValueError``) — 조용히 임의 카테고리로 삼키지 않는다.
        """

        if messenger is Messenger.TELEGRAM:
            return FailureCategory.TELEGRAM_FAILURE
        if messenger is Messenger.KAKAO:
            return FailureCategory.KAKAO_FAILURE
        raise ValueError(f"unknown messenger for failure category: {messenger!r}")

    @staticmethod
    def backoff_delay_seconds(
        attempt: int,
        *,
        base_seconds: int = _DEFAULT_BASE_SECONDS,
        factor: int = _DEFAULT_FACTOR,
        cap_seconds: int = _DEFAULT_CAP_SECONDS,
    ) -> int:
        """``attempt``(1-기반)의 **결정적·단조 증가·상한** backoff 지연(초)을 반환.

        ``min(cap_seconds, base_seconds * factor**(attempt-1))``. 고정 5초·0초·무한 금지
        (ADD-15 "backoff 없는 빠른 재시도 금지", FR-33 "error code별 backoff로 폭주 방지").
        내부 ``now()``/``random`` 미호출 — 같은 ``attempt`` → 같은 값(jitter는 5.4 주입).
        ``attempt`` 가 1 미만이어도 ``base_seconds`` 미만으로 내려가지 않게 지수는 0으로 클램프.
        """

        exponent = max(0, attempt - 1)
        return min(cap_seconds, base_seconds * factor**exponent)

    @staticmethod
    def decide(
        *,
        category: FailureCategory,
        attempt: int,
        max_attempts: int,
        base_seconds: int = _DEFAULT_BASE_SECONDS,
        factor: int = _DEFAULT_FACTOR,
        cap_seconds: int = _DEFAULT_CAP_SECONDS,
    ) -> RetryDecision:
        """실패 카테고리·시도 횟수로 재시도/보류/실패를 판정한다(architecture 326-327·329).

        (1) 사람-개입(``AUTH_REQUIRED``·``TARGET_VALIDATION_FAILURE``) → ``HELD``
            (``should_retry=False``, ``delay_seconds=None`` — 무한 재시도 금지, NFR-4).
        (2) 일시 실패 & ``attempt < max_attempts`` → ``RETRYING``
            (``should_retry=True``, ``delay_seconds=backoff_delay_seconds(attempt)``).
        (3) 일시 실패 & ``attempt >= max_attempts`` → ``FAILED``(소진·운영자 가시화).
        (4) 결정적 실패(``RENDER_FAILURE``) 및 그 외 비재시도 → ``FAILED``(재시도 무의미).
        모든 경우 ``error_code = category``.
        """

        if category in _HUMAN_INTERVENTION:
            return RetryDecision(
                should_retry=False,
                status=DeliveryStatus.HELD,
                error_code=category,
                attempt=attempt,
                delay_seconds=None,
            )
        if category in _RETRYABLE:
            if attempt < max_attempts:
                return RetryDecision(
                    should_retry=True,
                    status=DeliveryStatus.RETRYING,
                    error_code=category,
                    attempt=attempt,
                    delay_seconds=DeliveryFailurePolicy.backoff_delay_seconds(
                        attempt,
                        base_seconds=base_seconds,
                        factor=factor,
                        cap_seconds=cap_seconds,
                    ),
                )
            return RetryDecision(
                should_retry=False,
                status=DeliveryStatus.FAILED,
                error_code=category,
                attempt=attempt,
                delay_seconds=None,
            )
        # 결정적(RENDER_FAILURE) 또는 비재시도 → FAILED(재시도해도 동일, 가시화).
        return RetryDecision(
            should_retry=False,
            status=DeliveryStatus.FAILED,
            error_code=category,
            attempt=attempt,
            delay_seconds=None,
        )

    @staticmethod
    def parser_warning(
        consecutive_failures: int,
        *,
        threshold: int = _DEFAULT_PARSER_WARNING_THRESHOLD,
    ) -> bool:
        """parser 연속 실패가 ``threshold`` 이상이면 운영자 경고(True), 미만이면 False.

        반복 parser 실패를 조용히 빠르게 재시도하지 않고 가시화한다(FR-11 AC3, ADD-15).
        platform-wide circuit breaker는 Story 5.4 소유 — 본 함수는 boolean 경고 판정만.
        """

        return consecutive_failures >= threshold

    @staticmethod
    def attempt_delivery(
        job: DispatchJob,
        *,
        collected_at: datetime,
        reserve: Callable[[str], bool],
        send: Callable[[DispatchJob], None],
        release: Callable[[str], None],
        classify: Callable[[Exception], FailureCategory],
        log_id_for: Callable[[DispatchJob], str],
        sent_at: datetime,
        attempt: int,
        max_attempts: int,
        base_seconds: int = _DEFAULT_BASE_SECONDS,
        factor: int = _DEFAULT_FACTOR,
        cap_seconds: int = _DEFAULT_CAP_SECONDS,
    ) -> DeliveryAttemptResult:
        """``deliver_once`` 위에 분류 + release + 재시도 결정을 얹은 실패-인지 전송(단일 job).

        happy/dup 경로(``SENT``/``DUPLICATE_BLOCKED``)는 ``deliver_once`` 에 **위임**
        (그대로 반환·재시도 없음, ``decision=None``). **send 예외만** 잡아 ``classify`` 로
        분류하고 ``decide`` 로 판정한다 — **재시도 가능(``should_retry=True``) 실패에만**
        ``release(key)`` 로 insert-then-send가 선확보한 미발송 dedup key를 회수해 재시도 길을
        연다(``HELD``/``FAILED`` 보류·종료는 release 안 함, fail-closed: 오발송보다 미발송).

        ``deliver_once`` 본문·시그니처는 무변경(compose만) — reserve→send 순서·crash-after-send
        안전(3.5)은 보존되고, 본 메서드는 ``error_code`` 에 ``FailureCategory`` 값을 채운다
        (3.5는 항상 None). 내부 ``now()``/``uuid4()`` 미호출(``attempt``/``sent_at``/
        ``log_id_for`` 주입). ``classify``/``send``/``reserve``/``release`` 는 전부 주입 seam.
        """

        # release 용 key 재계산(deliver_once 와 동일 5필드 합성 — 같은 key).
        key = IdempotentDeliveryService.build_dedup_key(
            target_id=job.target_id,
            channel_id=job.channel_id,
            collected_at=collected_at,
            template_version=job.template_version,
            message_hash=job.message_hash,
        )

        try:
            log = IdempotentDeliveryService.deliver_once(
                job,
                collected_at=collected_at,
                reserve=reserve,
                send=send,
                log_id_for=log_id_for,
                sent_at=sent_at,
            )
        except Exception as exc:  # send 예외만 분류(reserve 충돌은 DUPLICATE_BLOCKED 반환).
            category = classify(exc)
            decision = DeliveryFailurePolicy.decide(
                category=category,
                attempt=attempt,
                max_attempts=max_attempts,
                base_seconds=base_seconds,
                factor=factor,
                cap_seconds=cap_seconds,
            )
            # 재시도 가능 실패만 미발송 key 회수 — 보류/종료는 회수 안 함(fail-closed).
            if decision.should_retry:
                release(key)
            log = DeliveryLog(
                id=log_id_for(job),
                message_id=job.message_id,
                channel_id=job.channel_id,
                status=decision.status,
                dedup_key=key,
                error_code=category.value,
                sent_at=None,
            )
            return DeliveryAttemptResult(log=log, decision=decision)

        # SENT/DUPLICATE_BLOCKED = 실패 아님 → deliver_once 결과 그대로(재시도/분류/release 없음).
        return DeliveryAttemptResult(log=log, decision=None)
