"""Story 3.5 / AC1~AC8 (P2-05, FR-10, ADD-5) — DeliveryLog + idempotency(insert-then-send).

(1) 5필드 dedup key(target_id·channel_id·collected_at·template_version·message_hash) 결정성,
(2) 성공 전송 idempotency(같은 key 재시도 = 재전송 안 함),
(3) insert-then-send 순서 + crash-after-send 안전(reserve가 send보다 먼저),
(4) send 예외 전파·미분류(error_code=None, 3.6 경계),
(5) 오차단 방지(한 차원만 달라도 다른 key) + duplicate_blocked 기록(관측 가능),
(6) DeliveryLog/DeliveryStatus 계약·frozen·기본값·재노출·비노출.

외부 호출 없음 — fake/in-memory·가짜 값만. 평면 ``tests/server/`` 컨벤션(conftest 공유 없이
자급자족, ``__init__.py`` 미추가). 평문 secret/식별자 금지(봇토큰/chat_id 숫자/전화/이메일 원문).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from rider_server.domain import DeliveryLog, DeliveryStatus, Messenger
from rider_server.services import IdempotentDeliveryService
from rider_server.services.dispatch_fanout_service import DispatchJob

# ── fixture: 가짜 값만(가짜 target/channel/message id·sha256 형태 hash) ───────────
_TARGET_ID = "mt-1"
_CHANNEL_ID = "ch-tg"
_MESSAGE_ID = "msg-1"
_TEMPLATE_VERSION = "baemin.realtime.v1"
_MESSAGE_HASH = "a" * 64  # sha256 형태(가짜 — 실제 secret 아님)
_COLLECTED_AT = datetime(2026, 1, 1, 9, 30, 0)
_SENT_AT = datetime(2026, 1, 1, 9, 30, 5)


def _job(
    *,
    id: str = "dj-1",
    target_id: str = _TARGET_ID,
    channel_id: str = _CHANNEL_ID,
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
    """in-memory reserve/send 레코더 — 성공 key 집합(``seen``)·호출 순서·send 횟수 기록.

    ``reserve(key)`` = key를 새로 확보하면 True(전송 진행), 이미 확보됐으면 False(중복 차단).
    실제 DB ``uq_delivery_logs_dedup_key`` UNIQUE를 in-memory로 모사(성공 레코드만 모델링).
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.events: list[tuple[str, str]] = []  # ("reserve", key) | ("send", job.id)
        self.sent_jobs: list[str] = []

    def reserve(self, key: str) -> bool:
        self.events.append(("reserve", key))
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def send(self, job: DispatchJob) -> None:
        self.events.append(("send", job.id))
        self.sent_jobs.append(job.id)


def _deliver(service_seam: _Seam, job: DispatchJob, **overrides) -> DeliveryLog:
    kwargs = dict(
        collected_at=_COLLECTED_AT,
        reserve=service_seam.reserve,
        send=service_seam.send,
        log_id_for=_log_id_for,
        sent_at=_SENT_AT,
    )
    kwargs.update(overrides)
    return IdempotentDeliveryService.deliver_once(job, **kwargs)


# ── AC1 — dedup key 5차원·결정성 ─────────────────────────────────────────────────


def test_build_dedup_key_includes_all_five_dimensions():
    base = dict(
        target_id=_TARGET_ID,
        channel_id=_CHANNEL_ID,
        collected_at=_COLLECTED_AT,
        template_version=_TEMPLATE_VERSION,
        message_hash=_MESSAGE_HASH,
    )
    key = IdempotentDeliveryService.build_dedup_key(**base)

    # 한 차원씩 바꾸면 key가 달라진다(5개 distinct — 축소 금지·오차단 방지).
    variants = [
        {**base, "target_id": "mt-2"},
        {**base, "channel_id": "ch-kakao"},
        {**base, "collected_at": datetime(2026, 1, 1, 9, 31, 0)},
        {**base, "template_version": "coupang.peak.v1"},
        {**base, "message_hash": "b" * 64},
    ]
    keys = {key} | {IdempotentDeliveryService.build_dedup_key(**v) for v in variants}
    assert len(keys) == 6  # 원본 + 5변형 모두 distinct → 5차원 전부 반영


def test_build_dedup_key_is_deterministic_and_normalizes_collected_at():
    # 같은 입력 두 번 → 동일 key(결정적). 같은 값의 별개 datetime 객체도 같은 key.
    k1 = IdempotentDeliveryService.build_dedup_key(
        target_id=_TARGET_ID,
        channel_id=_CHANNEL_ID,
        collected_at=datetime(2026, 1, 1, 9, 30, 0),
        template_version=_TEMPLATE_VERSION,
        message_hash=_MESSAGE_HASH,
    )
    k2 = IdempotentDeliveryService.build_dedup_key(
        target_id=_TARGET_ID,
        channel_id=_CHANNEL_ID,
        collected_at=datetime(2026, 1, 1, 9, 30, 0),
        template_version=_TEMPLATE_VERSION,
        message_hash=_MESSAGE_HASH,
    )
    assert k1 == k2
    # collected_at 은 안정적 직렬화(.isoformat())로 정규화돼 key 안에 들어간다.
    assert _COLLECTED_AT.isoformat() in k1
    # 5차원 전량이 key 안에 보존됨(논리 key는 5필드를 결정).
    for part in (_TARGET_ID, _CHANNEL_ID, _TEMPLATE_VERSION, _MESSAGE_HASH):
        assert part in k1


def test_deliver_once_uses_jobs_five_dimensions_for_key():
    # deliver_once 가 build_dedup_key(job 4차원 + 주입 collected_at)로 같은 key를 만든다.
    seam = _Seam()
    job = _job()
    log = _deliver(seam, job)

    expected = IdempotentDeliveryService.build_dedup_key(
        target_id=job.target_id,
        channel_id=job.channel_id,
        collected_at=_COLLECTED_AT,
        template_version=job.template_version,
        message_hash=job.message_hash,
    )
    assert log.dedup_key == expected


# ── AC1.2 — 성공 후 재시도 차단(정확히 1회 전송) ─────────────────────────────────


def test_first_send_succeeds_retry_is_duplicate_blocked():
    seam = _Seam()
    job = _job()

    first = _deliver(seam, job)
    second = _deliver(seam, job)  # 같은 job(=같은 dedup key) 재시도

    # 1회차: SENT(send 1회 호출·sent_at 주입값).
    assert first.status is DeliveryStatus.SENT
    assert first.sent_at == _SENT_AT
    assert first.error_code is None
    assert first.id == "dl-dj-1"
    assert first.message_id == _MESSAGE_ID
    assert first.channel_id == _CHANNEL_ID
    # 2회차: DUPLICATE_BLOCKED(send 미호출·sent_at None·같은 key).
    assert second.status is DeliveryStatus.DUPLICATE_BLOCKED
    assert second.sent_at is None
    assert second.error_code is None
    assert second.dedup_key == first.dedup_key
    # 정확히 1회만 실제 전송됨(재시도는 재전송 안 함).
    assert seam.sent_jobs == ["dj-1"]


# ── AC2 — insert-then-send 순서 + crash-after-send 안전 ───────────────────────────


def test_reserve_happens_before_send():
    seam = _Seam()
    job = _job()

    log = _deliver(seam, job)

    # 유니크 제약(reserve)이 전송(send)보다 먼저 — insert-then-send.
    assert [kind for kind, _ in seam.events] == ["reserve", "send"]
    assert seam.events[0] == ("reserve", log.dedup_key)
    assert seam.events[1] == ("send", "dj-1")


def test_crash_after_send_blocks_resend_on_retry():
    # crash-after-send 모사: 1회차 reserve 성공·send 발생 후 SENT 기록을 '유실'(버림).
    # 같은 key로 2회차 호출 → reserve 충돌 → DUPLICATE_BLOCKED·send 미호출(재전송 없음).
    seam = _Seam()
    job = _job()

    first = _deliver(seam, job)
    del first  # SENT DeliveryLog 기록 유실(crash로 상태 못 남김) 모사

    second = _deliver(seam, job)

    assert second.status is DeliveryStatus.DUPLICATE_BLOCKED
    assert second.sent_at is None
    assert seam.sent_jobs == ["dj-1"]  # 크래시 후 재시도해도 한 번만 전송됨


# ── AC2 — send 예외 전파·미분류(3.6 경계) ────────────────────────────────────────


def test_send_exception_propagates_and_is_not_classified():
    seam = _Seam()
    job = _job()
    reserved: list[str] = []

    def reserve(key: str) -> bool:
        reserved.append(key)
        return True  # 새 key 확보 성공

    def send(_job: DispatchJob) -> None:
        raise RuntimeError("telegram 5xx")

    # deliver_once 는 예외를 삼키거나 error_code 로 분류하지 않고 그대로 전파한다.
    with pytest.raises(RuntimeError, match="telegram 5xx"):
        IdempotentDeliveryService.deliver_once(
            job,
            collected_at=_COLLECTED_AT,
            reserve=reserve,
            send=send,
            log_id_for=_log_id_for,
            sent_at=_SENT_AT,
        )

    # insert-then-send: 예외 시점에 이미 reserve 가 호출된 상태(유니크 제약 선확보).
    assert len(reserved) == 1


# ── AC3 — 오차단 방지(한 차원만 달라도 다른 key → 둘 다 전송) ─────────────────────


def test_different_jobs_are_not_falsely_blocked():
    seam = _Seam()
    # target/channel/message/template/hash 중 하나만 다른 job들 + collected_at 변형.
    base = _deliver(seam, _job(id="dj-base"))
    others = [
        _deliver(seam, _job(id="dj-t", target_id="mt-2")),
        _deliver(seam, _job(id="dj-c", channel_id="ch-kakao")),
        _deliver(seam, _job(id="dj-tpl", template_version="coupang.peak.v1")),
        _deliver(seam, _job(id="dj-h", message_hash="b" * 64)),
        _deliver(seam, _job(id="dj-col"), collected_at=datetime(2026, 1, 1, 9, 31, 0)),
    ]

    # 모두 서로 다른 dedup key → 전부 reserve 성공 → 전부 SENT(오차단 0).
    all_logs = [base, *others]
    assert all(log.status is DeliveryStatus.SENT for log in all_logs)
    assert len({log.dedup_key for log in all_logs}) == len(all_logs)
    assert len(seam.sent_jobs) == len(all_logs)


def test_same_message_same_target_different_channel_not_blocked():
    # 3.4 fan-out 산출: 같은 Message·같은 target, channel만 다른 두 job은 서로 안 막는다
    # (scope 비축소 — 한 채널의 중복 판단이 다른 채널을 막지 않음).
    seam = _Seam()
    tg = _deliver(seam, _job(id="dj-tg", channel_id="ch-tg"))
    kakao = _deliver(seam, _job(id="dj-kakao", channel_id="ch-kakao"))

    assert tg.status is DeliveryStatus.SENT
    assert kakao.status is DeliveryStatus.SENT
    assert tg.dedup_key != kakao.dedup_key
    assert seam.sent_jobs == ["dj-tg", "dj-kakao"]


# ── AC3/AC6 — duplicate_blocked 기록(관측 가능, audit) ────────────────────────────


def test_duplicate_blocked_is_recorded_as_observable_delivery_log():
    seam = _Seam()
    job = _job()
    _deliver(seam, job)  # 1회차 SENT
    blocked = _deliver(seam, job)  # 2회차 차단

    # 차단된 전송도 DeliveryLog로 남아 관측 가능(NFR-15) — status/dedup_key/sent_at/error_code.
    assert blocked.status is DeliveryStatus.DUPLICATE_BLOCKED
    assert blocked.dedup_key  # 추적용 동일 key 보유
    assert blocked.sent_at is None
    assert blocked.error_code is None
    assert blocked.message_id == _MESSAGE_ID
    assert blocked.channel_id == _CHANNEL_ID
    # DUPLICATE_BLOCKED 는 reserve 를 다시 시도하지 않는 audit 기록(유니크 제약은 SENT에만).
    # → 2회차에서 send 는 호출되지 않았다.
    assert seam.sent_jobs == ["dj-1"]


# ── AC4 — DeliveryLog/DeliveryStatus 계약·frozen·기본값 ───────────────────────────


def test_delivery_status_members_include_dedup_vocabulary():
    # Story 3.6 계약 반영 갱신(3.5가 "2개"로 박아둔 lock — 3.6이 실패/재시도/보류 멤버를
    # additive로 추가하므로 갱신이지 회귀 아님): 3.5의 dedup 결과 어휘 2개는 그대로 보존되고
    # 3.6이 FAILED/RETRYING/HELD 를 더해 총 5멤버다.
    names = {s.name for s in DeliveryStatus}
    assert {"SENT", "DUPLICATE_BLOCKED"} <= names  # 3.5 어휘 보존
    assert names == {"SENT", "DUPLICATE_BLOCKED", "FAILED", "RETRYING", "HELD"}
    assert DeliveryStatus.SENT == "SENT"  # (str, Enum)
    assert DeliveryStatus.DUPLICATE_BLOCKED == "DUPLICATE_BLOCKED"


def test_delivery_log_is_frozen_with_contract_defaults():
    log = DeliveryLog(
        id="dl-1",
        message_id=_MESSAGE_ID,
        channel_id=_CHANNEL_ID,
        status=DeliveryStatus.SENT,
        dedup_key="k",
    )
    assert log.error_code is None  # 본 스토리 항상 None(3.6 소유)
    assert log.sent_at is None  # 기본 None
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
    with pytest.raises(FrozenInstanceError):
        log.dedup_key = "k2"  # type: ignore[misc]


# ── 재노출 — domain/services __all__ 포함 ─────────────────────────────────────────


def test_reexports_from_domain_and_services():
    import rider_server.domain as domain
    import rider_server.services as services

    assert domain.DeliveryLog is DeliveryLog
    assert domain.DeliveryStatus is DeliveryStatus
    assert services.IdempotentDeliveryService is IdempotentDeliveryService
    assert "DeliveryLog" in domain.__all__
    assert "DeliveryStatus" in domain.__all__
    assert "IdempotentDeliveryService" in services.__all__


# ── 비노출 — dedup key·DeliveryLog 에 평문 secret/식별자 원문 없음 ────────────────


def test_no_plaintext_secret_in_dedup_key_or_delivery_log():
    seam = _Seam()
    log = _deliver(seam, _job())

    # dedup key·DeliveryLog 필드는 불투명 id·sha256 hex·iso8601 시각만 — 평문 secret 0.
    blob = "|".join(
        [log.dedup_key, log.id, log.message_id, log.channel_id, str(log.sent_at)]
    )
    assert "987654321" not in blob  # chat_id 숫자 형태 없음
    assert "token" not in blob.lower()
    assert "password" not in blob.lower()
    # 구성 차원은 가짜 id·sha256 형태 hash·iso8601 뿐.
    assert _MESSAGE_HASH in log.dedup_key
    assert _COLLECTED_AT.isoformat() in log.dedup_key
