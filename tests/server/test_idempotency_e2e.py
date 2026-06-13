"""Story 3.5 — DeliveryLog + idempotency **E2E/통합** 커버리지(QA qa-generate-e2e-tests).

dev가 쓴 ``test_idempotency.py`` 는 합성 ``_job()`` 1개로 ``deliver_once`` 단위를 잘
덮지만, **실제 상류 합성**(``Snapshot``(3.2)→``Message``(3.3)→``DeliveryRule``(2.5)
→``DispatchFanoutService.plan``(3.4)→``IdempotentDeliveryService.deliver_once``(3.5))을
관통하는 테스트와 몇몇 경계 의미론이 비어 있었다. 본 모듈이 그 gap을 채운다:

  (A) plan→deliver_once fan-out 멱등성: 한 Snapshot이 2채널로 펼쳐져 전부 전송되고,
      같은 Snapshot 재실행(크래시/재시도)은 같은 5필드 key라 전부 DUPLICATE_BLOCKED
      (재전송 0). dedup 차원이 **실제 도메인 객체에서 유래**(message_hash·collected_at)함을 확인.
  (B) send 실패 후 reserve **release 안 함**(release=3.6 경계) → 재시도는 DUPLICATE_BLOCKED
      (fail-closed: 오발송보다 미발송). 기존 테스트는 예외 전파만 보고 후속 재시도는 안 봄.
  (C) collected_at 정규화 경계: tz-aware 결정성 + 마이크로초 구분(절삭/반올림 병합 없음).
  (D) 성공 후 내용 변경/새 수집 시각은 오차단 없이 다시 전송(AC3 — 순차 시나리오).

이 모듈이 test 안에서 plan↔deliver_once 를 손수 조립하는 것은 **Epic 5 런타임 wiring을
seam 수준에서 시뮬레이션**한 것이다(제품 코드는 무변경 — ``deliver_once`` 를
``dispatch_all`` 에 조립하는 배선은 Epic 5 소유). 외부 호출 없음 — fake/in-memory·가짜
값만(평문 secret/식별자 원문 금지). 평면 ``tests/server/`` 컨벤션(``__init__.py`` 미추가).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from rider_server.domain import (
    DeliveryRule,
    DeliveryStatus,
    Message,
    Messenger,
    MessengerChannel,
    Platform,
    Snapshot,
    SnapshotQualityState,
)
from rider_server.services import DispatchFanoutService, IdempotentDeliveryService
from rider_server.services.dispatch_fanout_service import DispatchJob

# ── fixture: 가짜 값만(불투명 id·sha256 형태 hash·평문 secret 0) ──────────────────
_TARGET_ID = "mt-1"
_TENANT_ID = "tn-1"
_TEMPLATE_VERSION = "baemin.realtime.v1"
_T1 = datetime(2026, 1, 1, 9, 30, 0)
_T2 = datetime(2026, 1, 1, 9, 35, 0)
_SENT_AT = datetime(2026, 1, 1, 9, 30, 5)

# 채널 라우팅 식별자(telegram_chat_id/kakao_room_name)는 본 경로에서 안 읽혀 비워 둔다
# (deliver_once 는 channel_id 만 dedup 차원으로 사용 — chat_id 원문은 key에 안 들어감).
_CHANNELS = {
    "ch-tg": MessengerChannel(
        id="ch-tg", tenant_id=_TENANT_ID, messenger=Messenger.TELEGRAM
    ),
    "ch-kakao": MessengerChannel(
        id="ch-kakao", tenant_id=_TENANT_ID, messenger=Messenger.KAKAO
    ),
}


def _snapshot(*, id: str = "snap-1", collected_at: datetime = _T1) -> Snapshot:
    return Snapshot(
        id=id,
        target_id=_TARGET_ID,
        platform=Platform.BAEMIN,
        collected_at=collected_at,
        normalized_json={"orders": 12},
        parser_version="baemin.v1",
        quality_state=SnapshotQualityState.OK,
    )


def _message(
    *, id: str = "msg-1", snapshot_id: str = "snap-1", text: str = "실적 12건"
) -> Message:
    # text_hash = sha256(text) — 3.3 render_message·3.1 message_hash 와 동일 계산(3.5 정합).
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Message(
        id=id,
        snapshot_id=snapshot_id,
        template_version=_TEMPLATE_VERSION,
        text=text,
        text_hash=text_hash,
        text_redacted_preview=text,
    )


def _rule(channel_id: str, *, enabled: bool = True) -> DeliveryRule:
    return DeliveryRule(
        id=f"rule-{channel_id}",
        target_id=_TARGET_ID,
        channel_id=channel_id,
        enabled=enabled,
    )


def _job_id_factory():
    """plan 호출마다 단조 증가 job id 부여 — 재실행 시 job.id 가 달라짐을 보장."""
    seq = {"n": 0}

    def _id(rule: DeliveryRule) -> str:
        seq["n"] += 1
        return f"job-{seq['n']}"

    return _id


class _Seam:
    """in-memory reserve/send seam — 성공 key 집합·실제 전송 job id·실패 주입 모사.

    ``reserve`` 는 DB ``uq_delivery_logs_dedup_key`` UNIQUE(성공 레코드만)를 흉내내고,
    ``fail_on`` 에 든 job 의 ``send`` 는 분류 안 된 전송 실패(3.6 경계)를 던진다.
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.sent: list[str] = []
        self.fail_on: set[str] = set()

    def reserve(self, key: str) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def send(self, job: DispatchJob) -> None:
        if job.id in self.fail_on:
            raise RuntimeError("messenger 5xx")  # 분류 안 된 실패 — 호출부로 전파(3.6)
        self.sent.append(job.id)


def _deliver_all(seam: _Seam, jobs, collected_at: datetime, sent_at: datetime):
    return [
        IdempotentDeliveryService.deliver_once(
            job,
            collected_at=collected_at,
            reserve=seam.reserve,
            send=seam.send,
            log_id_for=lambda j: f"dl-{j.id}",
            sent_at=sent_at,
        )
        for job in jobs
    ]


# ── Gap A — plan → deliver_once fan-out 멱등성(재실행 안전) ───────────────────────


def test_fanout_plan_then_deliver_once_is_idempotent_across_reruns():
    snap = _snapshot()
    msg = _message(snapshot_id=snap.id)
    rules = [_rule("ch-tg"), _rule("ch-kakao")]
    job_id_for = _job_id_factory()
    seam = _Seam()

    # Run 1: 한 Snapshot 수집 → 2채널 fan-out(plan) → 각 job 멱등 전송(deliver_once).
    jobs1 = DispatchFanoutService.plan(
        msg, rules, channels=_CHANNELS, job_id_for=job_id_for
    )
    logs1 = _deliver_all(seam, jobs1, snap.collected_at, _SENT_AT)

    assert len(jobs1) == 2
    assert all(log.status is DeliveryStatus.SENT for log in logs1)
    assert len({log.dedup_key for log in logs1}) == 2  # 채널별 distinct key(scope 비축소)
    assert seam.sent == [j.id for j in jobs1]  # 두 채널 모두 정확히 1회 전송
    # dedup 차원이 실제 도메인 객체에서 유래(provenance): message_hash·collected_at·channel.
    for log, job in zip(logs1, jobs1):
        assert log.message_id == msg.id
        assert log.channel_id == job.channel_id
        assert msg.text_hash in log.dedup_key
        assert snap.collected_at.isoformat() in log.dedup_key

    # Run 2: 같은 Snapshot 재실행(크래시 후 재시도) → 같은 5필드 key → 전부 차단.
    jobs2 = DispatchFanoutService.plan(
        msg, rules, channels=_CHANNELS, job_id_for=job_id_for
    )
    logs2 = _deliver_all(seam, jobs2, snap.collected_at, _SENT_AT)

    assert [j.id for j in jobs2] != [j.id for j in jobs1]  # 새 job id — dedup 은 job.id 비의존
    assert all(log.status is DeliveryStatus.DUPLICATE_BLOCKED for log in logs2)
    assert seam.sent == [j.id for j in jobs1]  # 재실행해도 추가 전송 0
    # 차단 로그도 1회차와 같은 dedup key 로 관측 가능(audit, NFR-15).
    assert {log.dedup_key for log in logs2} == {log.dedup_key for log in logs1}


def test_fanout_disabled_rule_is_not_dispatched_and_active_channel_still_sends():
    # plan 이 비활성 rule(soft delete)을 skip 하므로 그 채널은 dedup/전송 대상이 아니고,
    # 활성 채널은 정상 전송된다(fan-out × idempotency 합성 — 오차단/오발송 0).
    snap = _snapshot()
    msg = _message(snapshot_id=snap.id)
    rules = [_rule("ch-tg"), _rule("ch-kakao", enabled=False)]
    seam = _Seam()

    jobs = DispatchFanoutService.plan(
        msg, rules, channels=_CHANNELS, job_id_for=_job_id_factory()
    )
    logs = _deliver_all(seam, jobs, snap.collected_at, _SENT_AT)

    assert [j.channel_id for j in jobs] == ["ch-tg"]  # 비활성 ch-kakao 는 빠짐
    assert [log.status for log in logs] == [DeliveryStatus.SENT]
    assert seam.sent == [jobs[0].id]


# ── Gap B — send 실패 후 release 안 함 → 재시도 차단(3.6 경계·fail-closed) ─────────


def test_send_failure_does_not_release_key_so_retry_is_blocked():
    # AC2 경계: reserve release(미발송 key 회수)는 Story 3.6 소유. 3.5 는 send 실패 후
    # key 를 풀지 않으므로, 같은 job 재시도는 DUPLICATE_BLOCKED 다(오발송보다 미발송 — 수용).
    snap = _snapshot()
    msg = _message(snapshot_id=snap.id)
    job = DispatchFanoutService.plan(
        msg, [_rule("ch-tg")], channels=_CHANNELS, job_id_for=_job_id_factory()
    )[0]

    seam = _Seam()
    seam.fail_on.add(job.id)  # 1회차 send 가 예외(분류 안 된 전송 실패)

    # insert-then-send: reserve 로 key 선확보 후 send 호출 → send 예외는 전파(미분류).
    with pytest.raises(RuntimeError, match="messenger 5xx"):
        _deliver_all(seam, [job], snap.collected_at, _SENT_AT)

    # 재시도: send 는 성공할 수 있는 상태로 풀어도, key 가 이미 확보돼 있어 차단된다.
    seam.fail_on.clear()
    retry = _deliver_all(seam, [job], snap.collected_at, _SENT_AT)[0]

    assert retry.status is DeliveryStatus.DUPLICATE_BLOCKED  # 재전송 안 함
    assert retry.sent_at is None
    assert retry.error_code is None  # 본 스토리는 실패 미분류
    assert seam.sent == []  # 실패한 1회차도, 차단된 재시도도 실제 전송 0


# ── Gap C — collected_at 정규화 경계(tz-aware 결정성·마이크로초 구분) ─────────────


def test_dedup_key_collected_at_normalization_tz_and_subsecond():
    base = dict(
        target_id=_TARGET_ID,
        channel_id="ch-tg",
        template_version=_TEMPLATE_VERSION,
        message_hash="a" * 64,
    )
    aware = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # tz-aware collected_at 도 결정적(같은 값 → 같은 key), 오프셋 보존.
    k_aware_1 = IdempotentDeliveryService.build_dedup_key(collected_at=aware, **base)
    k_aware_2 = IdempotentDeliveryService.build_dedup_key(collected_at=aware, **base)
    assert k_aware_1 == k_aware_2
    assert aware.isoformat() in k_aware_1  # "+00:00" 오프셋이 key 안에 보존

    # 1 마이크로초 차이도 다른 key — 시각 절삭/반올림으로 두 Snapshot 이 병합되지 않음.
    k_us = IdempotentDeliveryService.build_dedup_key(
        collected_at=aware + timedelta(microseconds=1), **base
    )
    assert k_us != k_aware_1

    # naive vs aware(같은 벽시계 성분)는 다른 key — 호출부가 일관된 Snapshot.collected_at
    # 을 주입해야 함을 문서화(혼용 시 같은 전송이 두 key 로 갈려 오발송 가능).
    naive = datetime(2026, 1, 1, 9, 30)
    k_naive = IdempotentDeliveryService.build_dedup_key(collected_at=naive, **base)
    assert k_naive != k_aware_1


# ── Gap D — 성공 후 내용 변경/새 수집 시각은 오차단 없이 다시 전송(AC3 순차) ──────


def test_changed_content_or_new_collection_time_is_not_blocked_after_send():
    # 같은 target·channel·template 라도 (a) 메시지 내용이 바뀌거나 (b) 새 Snapshot
    # (다른 collected_at)이면 dedup key 가 달라 오차단 없이 다시 전송된다(각 수집=별개 이벤트).
    seam = _Seam()
    job_id_for = _job_id_factory()

    snap1 = _snapshot(id="snap-1", collected_at=_T1)
    msg1 = _message(id="msg-1", snapshot_id="snap-1", text="실적 12건")
    job1 = DispatchFanoutService.plan(
        msg1, [_rule("ch-tg")], channels=_CHANNELS, job_id_for=job_id_for
    )[0]
    log1 = _deliver_all(seam, [job1], snap1.collected_at, _SENT_AT)[0]
    assert log1.status is DeliveryStatus.SENT

    # (a) 내용 변경: 같은 수집 시각이라도 text_hash 차원이 달라 새 전송.
    msg2 = _message(id="msg-2", snapshot_id="snap-1", text="실적 19건")
    job2 = DispatchFanoutService.plan(
        msg2, [_rule("ch-tg")], channels=_CHANNELS, job_id_for=job_id_for
    )[0]
    log2 = _deliver_all(seam, [job2], snap1.collected_at, _SENT_AT)[0]
    assert log2.status is DeliveryStatus.SENT
    assert msg1.text_hash != msg2.text_hash
    assert log2.dedup_key != log1.dedup_key  # message_hash 차원이 달라짐

    # (b) 새 수집 시각: 동일 내용이라도 collected_at 차원이 달라 새 전송.
    snap3 = _snapshot(id="snap-3", collected_at=_T2)
    msg3 = _message(id="msg-3", snapshot_id="snap-3", text="실적 12건")  # msg1 과 동일 내용
    job3 = DispatchFanoutService.plan(
        msg3, [_rule("ch-tg")], channels=_CHANNELS, job_id_for=job_id_for
    )[0]
    log3 = _deliver_all(seam, [job3], snap3.collected_at, _SENT_AT)[0]
    assert log3.status is DeliveryStatus.SENT
    assert msg3.text_hash == msg1.text_hash  # 내용은 같지만…
    assert log3.dedup_key != log1.dedup_key  # …collected_at 차원이 달라 다른 key

    # 세 전송 모두 실제로 나갔다(오차단 0).
    assert seam.sent == [job1.id, job2.id, job3.id]
