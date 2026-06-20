"""Story 3.6 / AC1~AC8 (P2-06, FR-11·26, NFR-4·15, ADD-15) — 실패 분류·재시도 결정·실패-인지 전송.

(1) 수집/렌더/전송 실패를 서로 다른 status/error_code(FailureCategory)로 분류(단계·채널 독립),
(2) 인증 필요(HELD·무한 재시도 금지) vs 일시 실패(RETRYING·error_code별 backoff·소진 시 FAILED),
(3) parser 반복 실패 → 운영자 경고 boolean,
(4) 같은 Snapshot 일부 채널 실패: 재시도 가능 실패만 release(key) → 실패 채널만 재시도,
    이미 성공한 채널은 3.5 idempotency로 DUPLICATE_BLOCKED(중복 발송 없음); 보류는 release 안 함,
(5) deliver_once happy/dup 경로 위임(SENT/DUPLICATE_BLOCKED 그대로·분류/release 없음),
(6) RetryDecision/DeliveryAttemptResult frozen·계약·재노출·비노출.

외부 호출 없음 — fake/in-memory·가짜 값만. 평면 ``tests/server/`` 컨벤션(conftest 공유 없이
자급자족, ``__init__.py`` 미추가). 평문 secret/식별자 금지(봇토큰/chat_id 숫자/전화/이메일 원문).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from rider_server.domain import (
    DeliveryLog,
    DeliveryStatus,
    FailureCategory,
    Messenger,
)
from rider_server.services import (
    DeliveryAttemptResult,
    DeliveryFailurePolicy,
    RetryDecision,
)
from rider_server.services.dispatch_fanout_service import DispatchJob

# ── fixture: 가짜 값만(가짜 target/channel/message id·sha256 형태 hash) ───────────
_TARGET_ID = "mt-1"
_MESSAGE_ID = "msg-1"
_TEMPLATE_VERSION = "baemin.realtime.v1"
_MESSAGE_HASH = "a" * 64  # sha256 형태(가짜 — 실제 secret 아님)
_COLLECTED_AT = datetime(2026, 1, 1, 9, 30, 0)
_SENT_AT = datetime(2026, 1, 1, 9, 30, 5)


def _job(
    *,
    id: str = "dj-1",
    target_id: str = _TARGET_ID,
    channel_id: str = "ch-tg",
    message_id: str = _MESSAGE_ID,
    messenger: Messenger = Messenger.TELEGRAM,
    template_version: str = _TEMPLATE_VERSION,
    message_hash: str = _MESSAGE_HASH,
) -> DispatchJob:
    return DispatchJob(
        id=id,
        target_id=target_id,
        channel_id=channel_id,
        message_id=message_id,
        messenger=messenger,
        template_version=template_version,
        message_hash=message_hash,
    )


def _log_id_for(job: DispatchJob) -> str:
    return f"dl-{job.id}"


class _Seam:
    """in-memory reserve/send/**release** 레코더 — 성공 key 집합·호출 순서·send 횟수 기록.

    ``test_idempotency._Seam`` 에 release(미발송 key 회수)를 더한 변형이다. ``release(key)``
    는 ``seen`` 에서 key를 제거해 재시도 시 reserve 재확보를 가능케 한다(실제 DB reservation
    행 삭제 = Epic 5). ``fail_channels`` 에 든 channel_id 는 send 시 예외(일시 실패 모사).
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.events: list[tuple[str, str]] = []
        self.sent_jobs: list[str] = []
        self.released: list[str] = []
        self.fail_channels: set[str] = set()

    def reserve(self, key: str) -> bool:
        self.events.append(("reserve", key))
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def send(self, job: DispatchJob) -> None:
        self.events.append(("send", job.id))
        if job.channel_id in self.fail_channels:
            raise RuntimeError("transient channel error")  # 평문 secret 없음
        self.sent_jobs.append(job.id)

    def release(self, key: str) -> None:
        self.events.append(("release", key))
        self.released.append(key)
        self.seen.discard(key)


def _classify_to(category: FailureCategory):
    """예외를 고정 카테고리로 매핑하는 주입 classify(테스트용 — Epic 5/3.7 wiring 대역)."""

    def classify(_exc: Exception) -> FailureCategory:
        return category

    return classify


def _attempt(
    seam: _Seam,
    job: DispatchJob,
    *,
    classify,
    attempt: int = 1,
    max_attempts: int = 3,
    **overrides,
) -> DeliveryAttemptResult:
    kwargs = dict(
        collected_at=_COLLECTED_AT,
        reserve=seam.reserve,
        send=seam.send,
        release=seam.release,
        classify=classify,
        log_id_for=_log_id_for,
        sent_at=_SENT_AT,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    kwargs.update(overrides)
    return DeliveryFailurePolicy.attempt_delivery(job, **kwargs)


# ── AC1 — 단계/채널 독립 분류 + enum 정본 ─────────────────────────────────────────


def test_collect_success_and_kakao_failure_are_separate_states() -> None:
    # 수집 성공(Telegram SENT)과 같은 흐름의 Kakao 전송 실패가 서로 다른 status/error_code로
    # 따로 보인다 — 한 채널 실패가 다른 채널 성공을 덮어쓰지 않는다(단계·채널 독립).
    seam = _Seam()
    seam.fail_channels.add("ch-kakao")

    tg = _attempt(
        seam,
        _job(id="dj-tg", channel_id="ch-tg", messenger=Messenger.TELEGRAM),
        classify=_classify_to(FailureCategory.TELEGRAM_FAILURE),
    )
    kakao = _attempt(
        seam,
        _job(id="dj-kakao", channel_id="ch-kakao", messenger=Messenger.KAKAO),
        classify=_classify_to(FailureCategory.KAKAO_FAILURE),
    )

    assert tg.log.status is DeliveryStatus.SENT
    assert tg.log.error_code is None  # 성공(deliver_once) — 미분류
    assert tg.decision is None
    # Kakao는 실패 → 다른 status·운영 카테고리 KAKAO_FAILURE.
    assert kakao.log.status is DeliveryStatus.RETRYING
    assert kakao.log.error_code == "KAKAO_FAILURE"
    assert kakao.decision is not None
    assert kakao.decision.error_code is FailureCategory.KAKAO_FAILURE


def test_failure_category_matches_nfr15_canon() -> None:
    # FailureCategory 7멤버가 NFR-15/architecture 324-325 정본과 값·이름·개수 일치.
    assert [m.value for m in FailureCategory] == [
        "CRAWL_FAILURE",
        "AUTH_REQUIRED",
        "RENDER_FAILURE",
        "TELEGRAM_FAILURE",
        "KAKAO_FAILURE",
        "DUPLICATE_BLOCKED",
        "TARGET_VALIDATION_FAILURE",
    ]
    assert len(list(FailureCategory)) == 7


def test_delivery_status_expresses_fr26_four_states_plus_dedup() -> None:
    # FR-26 "성공·실패·재시도·보류"(+dedup) + outbox send-start 중간 상태.
    assert {s.value for s in DeliveryStatus} == {
        "SENT",
        "DUPLICATE_BLOCKED",
        "SENDING",
        "FAILED",
        "RETRYING",
        "HELD",
    }


# ── AC2 — 인증 필요 vs 일시 실패·backoff·소진 ─────────────────────────────────────


def test_auth_required_is_held_without_retry() -> None:
    # 인증 필요는 무한 재시도 금지 → HELD 보류(delay None).
    decision = DeliveryFailurePolicy.decide(
        category=FailureCategory.AUTH_REQUIRED, attempt=1, max_attempts=5
    )
    assert decision.should_retry is False
    assert decision.status is DeliveryStatus.HELD
    assert decision.delay_seconds is None
    assert decision.error_code is FailureCategory.AUTH_REQUIRED


def test_target_validation_failure_is_held() -> None:
    # 기대 센터/상점 불일치 등 사람-개입 → HELD(오발송 위험, 운영자 조치 전 보류).
    decision = DeliveryFailurePolicy.decide(
        category=FailureCategory.TARGET_VALIDATION_FAILURE, attempt=1, max_attempts=5
    )
    assert decision.should_retry is False
    assert decision.status is DeliveryStatus.HELD


def test_transient_failure_retries_with_backoff_until_exhausted() -> None:
    # 일시 실패: attempt<max → RETRYING(backoff), attempt>=max → FAILED(소진).
    first = DeliveryFailurePolicy.decide(
        category=FailureCategory.TELEGRAM_FAILURE, attempt=1, max_attempts=3
    )
    assert first.should_retry is True
    assert first.status is DeliveryStatus.RETRYING
    assert first.delay_seconds == DeliveryFailurePolicy.backoff_delay_seconds(1)
    assert first.delay_seconds is not None and first.delay_seconds > 0

    exhausted = DeliveryFailurePolicy.decide(
        category=FailureCategory.TELEGRAM_FAILURE, attempt=3, max_attempts=3
    )
    assert exhausted.should_retry is False
    assert exhausted.status is DeliveryStatus.FAILED
    assert exhausted.delay_seconds is None


def test_render_failure_is_deterministic_failed_no_retry() -> None:
    # 결정적 실패(같은 Snapshot 재렌더 = 동일 실패) → 재시도 안 함·FAILED(가시화).
    decision = DeliveryFailurePolicy.decide(
        category=FailureCategory.RENDER_FAILURE, attempt=1, max_attempts=3
    )
    assert decision.should_retry is False
    assert decision.status is DeliveryStatus.FAILED
    assert decision.delay_seconds is None


def test_backoff_is_monotonic_capped_deterministic_not_fixed_five() -> None:
    delays = [DeliveryFailurePolicy.backoff_delay_seconds(a) for a in range(1, 8)]
    # 단조 증가(비감소).
    assert all(b >= a for a, b in zip(delays, delays[1:]))
    # 상한 존재(무한 증가 금지).
    assert max(delays) <= DeliveryFailurePolicy.backoff_delay_seconds(
        100
    )  # 큰 attempt는 cap에 수렴
    assert DeliveryFailurePolicy.backoff_delay_seconds(100) <= 900
    # 고정 5초·0초 아님.
    assert all(d != 5 for d in delays)
    assert all(d > 0 for d in delays)
    # 결정적: 같은 attempt 두 번 호출 동일값(내부 now()/random 미호출).
    assert DeliveryFailurePolicy.backoff_delay_seconds(
        3
    ) == DeliveryFailurePolicy.backoff_delay_seconds(3)


def test_backoff_honors_custom_params() -> None:
    # 호출부 조정 가능(base/factor/cap). base=10,factor=3 → 10,30,90,270,... cap=100에서 상한.
    assert DeliveryFailurePolicy.backoff_delay_seconds(
        1, base_seconds=10, factor=3, cap_seconds=100
    ) == 10
    assert DeliveryFailurePolicy.backoff_delay_seconds(
        2, base_seconds=10, factor=3, cap_seconds=100
    ) == 30
    assert DeliveryFailurePolicy.backoff_delay_seconds(
        9, base_seconds=10, factor=3, cap_seconds=100
    ) == 100  # cap


def test_is_retryable_partitions_categories() -> None:
    retryable = {
        FailureCategory.TELEGRAM_FAILURE,
        FailureCategory.KAKAO_FAILURE,
        FailureCategory.CRAWL_FAILURE,
    }
    for cat in retryable:
        assert DeliveryFailurePolicy.is_retryable(cat) is True
    for cat in (
        FailureCategory.AUTH_REQUIRED,
        FailureCategory.TARGET_VALIDATION_FAILURE,
        FailureCategory.RENDER_FAILURE,
        FailureCategory.DUPLICATE_BLOCKED,
    ):
        assert DeliveryFailurePolicy.is_retryable(cat) is False


def test_channel_failure_category_maps_and_fails_closed() -> None:
    assert (
        DeliveryFailurePolicy.channel_failure_category(Messenger.TELEGRAM)
        is FailureCategory.TELEGRAM_FAILURE
    )
    assert (
        DeliveryFailurePolicy.channel_failure_category(Messenger.KAKAO)
        is FailureCategory.KAKAO_FAILURE
    )


# ── AC3 — parser 반복 실패 경고 ──────────────────────────────────────────────────


def test_parser_warning_triggers_at_threshold() -> None:
    assert DeliveryFailurePolicy.parser_warning(3, threshold=3) is True
    assert DeliveryFailurePolicy.parser_warning(2, threshold=3) is False  # 경계
    assert DeliveryFailurePolicy.parser_warning(5, threshold=3) is True


# ── AC4 — 실패 채널만 재시도·성공 채널 중복 발송 없음 ─────────────────────────────


def test_only_failed_channel_retries_success_channel_not_resent() -> None:
    seam = _Seam()
    seam.fail_channels.add("ch-kakao")  # Kakao만 1라운드에서 일시 실패
    tg = _job(id="dj-tg", channel_id="ch-tg", messenger=Messenger.TELEGRAM)
    kakao = _job(id="dj-kakao", channel_id="ch-kakao", messenger=Messenger.KAKAO)
    classify_kakao = _classify_to(FailureCategory.KAKAO_FAILURE)
    classify_tg = _classify_to(FailureCategory.TELEGRAM_FAILURE)

    # 1라운드: 성공 채널 SENT(key 확보 유지), 실패 채널 RETRYING + release(key 회수).
    r1_tg = _attempt(seam, tg, classify=classify_tg, attempt=1)
    r1_kakao = _attempt(seam, kakao, classify=classify_kakao, attempt=1)

    assert r1_tg.log.status is DeliveryStatus.SENT
    assert r1_kakao.log.status is DeliveryStatus.RETRYING
    assert r1_kakao.decision is not None and r1_kakao.decision.should_retry is True
    # 재시도 가능 실패 → release 호출됨(미발송 dedup key 회수).
    assert seam.released == [r1_kakao.log.dedup_key]

    # 2라운드: 일시 오류 회복(Kakao send 성공하도록). 같은 job 재처리.
    seam.fail_channels.discard("ch-kakao")
    r2_tg = _attempt(seam, tg, classify=classify_tg, attempt=2)
    r2_kakao = _attempt(seam, kakao, classify=classify_kakao, attempt=2)

    # 성공했던 Telegram 채널은 reserve 충돌 → DUPLICATE_BLOCKED(send 미호출·재전송 0).
    assert r2_tg.log.status is DeliveryStatus.DUPLICATE_BLOCKED
    assert r2_tg.decision is None
    # release됐던 Kakao 채널은 reserve 재확보 → 재전송 SENT.
    assert r2_kakao.log.status is DeliveryStatus.SENT

    # 성공 채널 send 총 1회(중복 발송 없음), 실패 채널만 재시도되어 결국 1회 전송.
    assert seam.sent_jobs.count("dj-tg") == 1
    assert seam.sent_jobs.count("dj-kakao") == 1
    assert seam.sent_jobs == ["dj-tg", "dj-kakao"]


def test_held_failure_is_not_released() -> None:
    # 보류(HELD·사람 개입)는 release 안 함 — 보류 유지, fail-closed(오발송보다 미발송).
    seam = _Seam()
    seam.fail_channels.add("ch-baemin")
    job = _job(id="dj-auth", channel_id="ch-baemin", messenger=Messenger.TELEGRAM)

    result = _attempt(
        seam, job, classify=_classify_to(FailureCategory.AUTH_REQUIRED), attempt=1
    )

    assert result.log.status is DeliveryStatus.HELD
    assert result.log.error_code == "AUTH_REQUIRED"
    assert result.decision is not None and result.decision.should_retry is False
    # 재시도 가능 실패가 아니므로 release 미호출(key 회수 안 함).
    assert seam.released == []
    assert ("release", result.log.dedup_key) not in seam.events


def test_exhausted_transient_failure_is_failed_and_not_released() -> None:
    # 소진(FAILED)도 종료 상태 — release 안 함(재시도 길 안 엶).
    seam = _Seam()
    seam.fail_channels.add("ch-tg")
    job = _job(id="dj-x", channel_id="ch-tg")

    result = _attempt(
        seam,
        job,
        classify=_classify_to(FailureCategory.TELEGRAM_FAILURE),
        attempt=3,
        max_attempts=3,
    )

    assert result.log.status is DeliveryStatus.FAILED
    assert result.log.error_code == "TELEGRAM_FAILURE"
    assert result.decision is not None and result.decision.should_retry is False
    assert seam.released == []


# ── AC4/compose — happy/dup 위임(deliver_once 그대로) ─────────────────────────────


def test_send_success_delegates_to_deliver_once_sent() -> None:
    seam = _Seam()
    job = _job(id="dj-ok")

    result = _attempt(seam, job, classify=_classify_to(FailureCategory.TELEGRAM_FAILURE))

    assert isinstance(result, DeliveryAttemptResult)
    assert result.log.status is DeliveryStatus.SENT
    assert result.log.sent_at == _SENT_AT
    assert result.log.error_code is None  # deliver_once 위임 — 항상 None(3.5)
    assert result.decision is None  # 실패 아님 → 분류 결정 없음
    assert seam.released == []  # 성공은 release 경로 밖


def test_duplicate_is_delegated_without_classify_or_release() -> None:
    seam = _Seam()
    job = _job(id="dj-dup")
    classify = _classify_to(FailureCategory.TELEGRAM_FAILURE)

    first = _attempt(seam, job, classify=classify)
    second = _attempt(seam, job, classify=classify)  # 같은 key 재처리

    assert first.log.status is DeliveryStatus.SENT
    assert second.log.status is DeliveryStatus.DUPLICATE_BLOCKED
    assert second.decision is None  # 중복은 실패 아님 — 분류/release 없음
    assert second.log.error_code is None
    assert seam.released == []
    assert seam.sent_jobs == ["dj-dup"]  # 1회만 실제 전송


def test_failure_log_carries_category_value_not_none() -> None:
    # 3.5(error_code 항상 None)와 대비 — 실패 경로는 error_code=category.value.
    seam = _Seam()
    seam.fail_channels.add("ch-kakao")
    job = _job(id="dj-k", channel_id="ch-kakao", messenger=Messenger.KAKAO)

    result = _attempt(seam, job, classify=_classify_to(FailureCategory.KAKAO_FAILURE))

    assert result.log.error_code == FailureCategory.KAKAO_FAILURE.value
    assert result.log.sent_at is None


# ── 계약·frozen ──────────────────────────────────────────────────────────────────


def test_retry_decision_is_frozen() -> None:
    decision = RetryDecision(
        should_retry=True,
        status=DeliveryStatus.RETRYING,
        error_code=FailureCategory.TELEGRAM_FAILURE,
        attempt=1,
        delay_seconds=30,
    )
    with pytest.raises(FrozenInstanceError):
        decision.should_retry = False  # type: ignore[misc]


def test_delivery_attempt_result_is_frozen() -> None:
    log = DeliveryLog(
        id="dl-1",
        message_id=_MESSAGE_ID,
        channel_id="ch-tg",
        status=DeliveryStatus.SENT,
        dedup_key="k",
    )
    result = DeliveryAttemptResult(log=log, decision=None)
    with pytest.raises(FrozenInstanceError):
        result.decision = None  # type: ignore[misc]


def test_delivery_log_field_set_unchanged_by_3_6() -> None:
    # DeliveryLog 필드 무증가 — error_code/status 값만 채운다(3.6).
    field_names = {f.name for f in DeliveryLog.__dataclass_fields__.values()}
    assert field_names == {
        "id",
        "message_id",
        "channel_id",
        "status",
        "dedup_key",
        "error_code",
        "sent_at",
    }


# ── 재노출·비노출·방향 ───────────────────────────────────────────────────────────


def test_reexports_from_domain_and_services() -> None:
    import rider_server.domain as domain
    import rider_server.services as services

    assert domain.FailureCategory is FailureCategory
    assert "FailureCategory" in domain.__all__
    assert services.DeliveryFailurePolicy is DeliveryFailurePolicy
    assert services.RetryDecision is RetryDecision
    assert services.DeliveryAttemptResult is DeliveryAttemptResult
    for name in ("DeliveryFailurePolicy", "RetryDecision", "DeliveryAttemptResult"):
        assert name in services.__all__


def test_no_plaintext_secret_in_failure_outputs_or_module_source() -> None:
    # error_code·dedup_key·breadcrumb 어디에도 평문 secret/식별자 원문 0(카테고리 코드만).
    seam = _Seam()
    seam.fail_channels.add("ch-kakao")
    result = _attempt(
        seam,
        _job(id="dj-k", channel_id="ch-kakao", messenger=Messenger.KAKAO),
        classify=_classify_to(FailureCategory.KAKAO_FAILURE),
    )
    blob = "|".join(
        [result.log.error_code or "", result.log.dedup_key, result.log.id]
    )
    assert "987654321" not in blob  # chat_id 숫자 형태 없음
    assert "token" not in blob.lower()
    assert "password" not in blob.lower()
    assert result.log.error_code == "KAKAO_FAILURE"  # 운영 카테고리 코드만

    # 모듈 소스에 평문 secret 패턴 0(가짜 fixture만 — A1 secret 게이트).
    import rider_server.services.delivery_failure_policy as mod

    with open(mod.__file__, encoding="utf-8") as fh:
        source = fh.read()
    for needle in ("password", "bot_token", "chat_id=", "010-"):
        assert needle not in source.lower()


def test_policy_module_does_not_import_back_into_rider_crawl_reverse() -> None:
    # 의존성 단방향: 신규 서비스 모듈이 rider_crawl→rider_server 역방향 import를 만들지 않는다
    # (이 파일은 rider_server 소속 — rider_crawl 을 import해도 정방향). 소스에 역참조 0 확인.
    import rider_server.services.delivery_failure_policy as mod

    with open(mod.__file__, encoding="utf-8") as fh:
        source = fh.read()
    # rider_crawl 패키지 안에서 rider_server 를 끌어오는 패턴이 이 파일에 없다.
    assert "rider_crawl import rider_server" not in source
    assert "rider_crawl.app import rider_server" not in source


# ── QA E2E 보강(gap coverage) — 미커버 분기·경계·기본값·plumbing ──────────────────
# 아래는 qa-generate-e2e-tests 워크플로가 발견한 커버리지 공백을 메우는 추가 케이스다.
# 전부 additive(소스 무변경) — 기존 fixture(`_Seam`/`_attempt`/`_classify_to`) 재사용.


def test_channel_failure_category_fails_closed_on_unknown_messenger() -> None:
    # GAP1: 정본 두 매핑(TELEGRAM/KAKAO) 밖의 미지/미래 messenger 는 조용히 임의 카테고리로
    # 삼키지 않고 ValueError 로 fail-closed 한다(impl 126행 — 기존 테스트명은 fails_closed
    # 이나 이 분기를 단언하지 않았다). `is` 식별 비교라 정본 멤버가 아닌 stand-in 으로 모사.
    class _FutureMessenger:  # 아직 매핑되지 않은 가상의 신규 채널(미래 enum 멤버 대역).
        def __repr__(self) -> str:
            return "<FutureMessenger>"  # 평문 secret 없음

    with pytest.raises(ValueError):
        DeliveryFailurePolicy.channel_failure_category(_FutureMessenger())  # type: ignore[arg-type]


def test_backoff_clamps_exponent_for_nonpositive_attempt() -> None:
    # GAP2: attempt 가 1 미만이어도 지수가 0으로 클램프돼 base_seconds 밑으로 내려가지 않고
    # (음수 지수·0초·소수 없음) ValueError 도 없다(impl 144행 max(0, attempt-1)).
    base = DeliveryFailurePolicy.backoff_delay_seconds(1)
    assert DeliveryFailurePolicy.backoff_delay_seconds(0) == base
    assert DeliveryFailurePolicy.backoff_delay_seconds(-5) == base
    assert DeliveryFailurePolicy.backoff_delay_seconds(0) > 0


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.TELEGRAM_FAILURE,
        FailureCategory.KAKAO_FAILURE,
        FailureCategory.CRAWL_FAILURE,
    ],
)
def test_decide_retryable_matrix_is_uniform_across_all_transient_categories(
    category: FailureCategory,
) -> None:
    # GAP3: decide 의 일시 실패 분기가 TELEGRAM 뿐 아니라 KAKAO·CRAWL 에도 동일하게 적용된다
    # (AC2 정본 표 — 3 카테고리 동형: attempt<max → RETRYING+backoff, 소진 → FAILED).
    retrying = DeliveryFailurePolicy.decide(category=category, attempt=1, max_attempts=3)
    assert retrying.should_retry is True
    assert retrying.status is DeliveryStatus.RETRYING
    assert retrying.error_code is category
    assert retrying.delay_seconds == DeliveryFailurePolicy.backoff_delay_seconds(1)

    exhausted = DeliveryFailurePolicy.decide(category=category, attempt=3, max_attempts=3)
    assert exhausted.should_retry is False
    assert exhausted.status is DeliveryStatus.FAILED
    assert exhausted.delay_seconds is None


def test_decide_retry_exhaustion_boundary_is_strict() -> None:
    # GAP4: <(retry) vs >=(exhaust) 경계를 max_attempts=2 로 정밀 고정 —
    # attempt == max-1 은 마지막 재시도, attempt == max 는 소진(FAILED).
    last_retry = DeliveryFailurePolicy.decide(
        category=FailureCategory.KAKAO_FAILURE, attempt=1, max_attempts=2
    )
    assert last_retry.should_retry is True
    assert last_retry.status is DeliveryStatus.RETRYING

    exhausted = DeliveryFailurePolicy.decide(
        category=FailureCategory.KAKAO_FAILURE, attempt=2, max_attempts=2
    )
    assert exhausted.should_retry is False
    assert exhausted.status is DeliveryStatus.FAILED


def test_attempt_delivery_threads_custom_backoff_params_into_decision() -> None:
    # GAP5: attempt_delivery 가 base/factor/cap 을 decide→backoff 로 그대로 전달해
    # decision.delay_seconds 에 반영한다(기본값과 구분되는 커스텀값으로 plumbing 확인).
    seam = _Seam()
    seam.fail_channels.add("ch-tg")
    job = _job(id="dj-custom", channel_id="ch-tg")

    result = _attempt(
        seam,
        job,
        classify=_classify_to(FailureCategory.TELEGRAM_FAILURE),
        attempt=2,
        max_attempts=5,
        base_seconds=10,
        factor=3,
        cap_seconds=1000,
    )

    assert result.log.status is DeliveryStatus.RETRYING
    assert result.decision is not None
    custom = DeliveryFailurePolicy.backoff_delay_seconds(
        2, base_seconds=10, factor=3, cap_seconds=1000
    )
    assert result.decision.delay_seconds == custom  # 10*3^1 = 30
    # 기본 backoff(30*2^1=60)와 달라야 — 커스텀 인자가 실제로 흘러갔음을 확인.
    assert custom != DeliveryFailurePolicy.backoff_delay_seconds(2)


def test_parser_warning_uses_default_threshold_when_omitted() -> None:
    # GAP6: threshold 인자 생략 시 기본 임계치(3)로 판정한다(기본값 경로 커버).
    assert DeliveryFailurePolicy.parser_warning(3) is True
    assert DeliveryFailurePolicy.parser_warning(2) is False  # 경계(기본 threshold-1)
    assert DeliveryFailurePolicy.parser_warning(0) is False


def test_already_sent_channel_stays_duplicate_even_if_it_would_now_fail() -> None:
    # GAP7: 이미 SENT 로 key 를 확보한 채널은 재처리 시 그 채널이 지금은 실패 상태여도
    # reserve 충돌로 DUPLICATE_BLOCKED 가 되고 send/classify/release 어느 것도 호출되지 않는다
    # (멱등성 견고성: 이미 보낸 채널을 실패로 재분류하거나 재전송하지 않는다).
    def _classify_must_not_run(_exc: Exception) -> FailureCategory:
        raise AssertionError("classify must not run on a reserve-conflict duplicate")

    seam = _Seam()
    job = _job(id="dj-sent-then-flaky", channel_id="ch-tg")

    # 1라운드: 정상 전송 → SENT(key 확보).
    first = _attempt(seam, job, classify=_classify_must_not_run, attempt=1)
    assert first.log.status is DeliveryStatus.SENT

    # 2라운드: 같은 채널이 이제 일시 실패 상태가 되어도 reserve 충돌이 send 보다 먼저라
    # 전송 시도 자체가 일어나지 않는다.
    seam.fail_channels.add("ch-tg")
    second = _attempt(seam, job, classify=_classify_must_not_run, attempt=2)

    assert second.log.status is DeliveryStatus.DUPLICATE_BLOCKED
    assert second.decision is None  # 실패 아님 — 분류 결정 없음
    assert second.log.error_code is None
    assert seam.released == []  # release 없음
    assert seam.sent_jobs.count("dj-sent-then-flaky") == 1  # 재전송 0(총 1회)
    # send 는 1라운드 1회만 — reserve 충돌 후 2라운드엔 send 이벤트가 없다.
    assert [e for e in seam.events if e[0] == "send"] == [("send", "dj-sent-then-flaky")]
