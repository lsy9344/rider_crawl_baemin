"""Story 3.4 / AC1~AC8 (P2-04, FR-9) — DeliveryRule fan-out + 채널 격리 전송.

(1) 한 Message → 활성 DeliveryRule마다 별도 DispatchJob(≥2채널 fan-out, 입력 순서 보존),
(2) 채널 격리(한 채널 전송 실패가 다른 채널 성공을 무효화하지 않음, 분류 없이 contain),
(3) scope 비축소(DispatchJob 이 target_id+channel_id 둘 다 보존 → 채널별 독립 dedup 차원),
(4) 순수 additive·결정성·frozen·fail-closed·redaction·재노출.

외부 호출 없음 — fake/in-memory·가짜 값만. 평면 ``tests/server/`` 컨벤션(conftest 공유 없이
자급자족, ``__init__.py`` 미추가). 평문 secret/식별자 금지(봇토큰/chat_id 숫자/전화/이메일 원문).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from rider_crawl.redaction import redact
from rider_server.domain import DeliveryRule, Message, Messenger, MessengerChannel
from rider_server.services import DispatchFanoutService, DispatchJob, FanoutOutcome
from rider_server.services.dispatch_fanout_service import UnknownChannelError

# ── fixture: 가짜 값만(가짜 target/channel id·sha256 형태 hash) ───────────────────

_TARGET_ID = "mt-1"
_TEMPLATE_VERSION = "baemin.realtime.v1"
_MESSAGE_HASH = "a" * 64  # sha256 형태(가짜 — 실제 secret 아님)


def _message() -> Message:
    return Message(
        id="msg-1",
        snapshot_id="snap-1",
        template_version=_TEMPLATE_VERSION,
        text="[택트런 실적봇]\n오후논피크 : 41.8건",
        text_hash=_MESSAGE_HASH,
        text_redacted_preview="[택트런 실적봇]\n오후논피크 : 41.8건",
    )


def _telegram_channel() -> MessengerChannel:
    return MessengerChannel(id="ch-tg", tenant_id="tn-1", messenger=Messenger.TELEGRAM)


def _kakao_channel() -> MessengerChannel:
    return MessengerChannel(id="ch-kakao", tenant_id="tn-1", messenger=Messenger.KAKAO)


def _channels() -> dict[str, MessengerChannel]:
    tg, kakao = _telegram_channel(), _kakao_channel()
    return {tg.id: tg, kakao.id: kakao}


def _rule(channel_id: str, *, enabled: bool = True) -> DeliveryRule:
    return DeliveryRule(
        id=f"dr-{channel_id}",
        target_id=_TARGET_ID,
        channel_id=channel_id,
        enabled=enabled,
    )


def _job_id_for(rule: DeliveryRule) -> str:
    return f"dj-{rule.channel_id}"


# ── AC1·AC3 — fan-out 필드·≥2채널(Telegram + Kakao) ──────────────────────────────


def test_plan_fans_out_one_message_to_at_least_two_channels():
    message = _message()
    rules = [_rule("ch-tg"), _rule("ch-kakao")]

    jobs = DispatchFanoutService.plan(
        message, rules, channels=_channels(), job_id_for=_job_id_for
    )

    # 활성 rule마다 별도 DispatchJob — 1 Message → 2 채널(입력 순서 보존).
    assert len(jobs) == 2
    assert [j.channel_id for j in jobs] == ["ch-tg", "ch-kakao"]
    assert [j.messenger for j in jobs] == [Messenger.TELEGRAM, Messenger.KAKAO]
    # 각 job 필드: id 주입, target/message/template/hash 동일, messenger·channel만 다름.
    assert [j.id for j in jobs] == ["dj-ch-tg", "dj-ch-kakao"]
    for job in jobs:
        assert isinstance(job, DispatchJob)
        assert job.target_id == _TARGET_ID
        assert job.message_id == "msg-1"
        assert job.message_hash == _MESSAGE_HASH
        assert job.template_version == _TEMPLATE_VERSION
    # 같은 message_id·message_hash·template_version, 서로 다른 channel_id·messenger.
    assert jobs[0].message_id == jobs[1].message_id
    assert jobs[0].message_hash == jobs[1].message_hash
    assert jobs[0].template_version == jobs[1].template_version
    assert jobs[0].channel_id != jobs[1].channel_id
    assert jobs[0].messenger != jobs[1].messenger


# ── AC1.2 — disabled(soft delete) rule 제외 ──────────────────────────────────────


def test_plan_excludes_disabled_rules():
    message = _message()
    rules = [_rule("ch-tg"), _rule("ch-kakao", enabled=False)]

    jobs = DispatchFanoutService.plan(
        message, rules, channels=_channels(), job_id_for=_job_id_for
    )

    # enabled=False rule은 fan-out에서 빠짐(물리 삭제 아닌 비활성 상태값).
    assert len(jobs) == 1
    assert jobs[0].channel_id == "ch-tg"


# ── AC3 — scope 비축소: (target_id, channel_id) distinct + dedup 차원 보존 ────────


def test_plan_preserves_channel_dimension_for_scope_non_reduction():
    message = _message()
    rules = [_rule("ch-tg"), _rule("ch-kakao")]

    jobs = DispatchFanoutService.plan(
        message, rules, channels=_channels(), job_id_for=_job_id_for
    )

    # 같은 target_id·같은 Message·다른 channel 2개 → (target_id, channel_id) distinct.
    scope_pairs = {(j.target_id, j.channel_id) for j in jobs}
    assert scope_pairs == {(_TARGET_ID, "ch-tg"), (_TARGET_ID, "ch-kakao")}
    # 미래 dedup 차원 튜플(target_id, channel_id, template_version, message_hash):
    # channel_id 만 다르고 나머지는 같음(전송 대상 scope 가 target_id 로 축소되지 않음).
    dedup_dims = [
        (j.target_id, j.channel_id, j.template_version, j.message_hash) for j in jobs
    ]
    assert dedup_dims[0] != dedup_dims[1]  # channel_id 차원에서 distinct
    non_channel = [(t, tv, mh) for (t, _c, tv, mh) in dedup_dims]
    assert non_channel[0] == non_channel[1]  # 나머지 차원은 동일


# ── AC2 — 채널 격리: 한 채널 실패가 다른 채널 성공을 무효화하지 않음 ─────────────


def test_dispatch_all_isolates_channel_failure():
    message = _message()
    jobs = DispatchFanoutService.plan(
        message,
        [_rule("ch-kakao"), _rule("ch-tg")],
        channels=_channels(),
        job_id_for=_job_id_for,
    )
    calls: list[tuple[str, str]] = []

    def send(job: DispatchJob, text: str) -> None:
        calls.append((job.channel_id, text))
        if job.channel_id == "ch-kakao":  # 첫 job(Kakao)만 실패
            raise RuntimeError("kakao send failed")

    outcomes = DispatchFanoutService.dispatch_all(message, jobs, send=send)

    # 첫 채널(Kakao) 실패가 둘째 채널(Telegram) 성공을 무효화하지 않음(FR-9).
    assert [o.sent for o in outcomes] == [False, True]
    assert outcomes[0].error_redacted is not None
    assert outcomes[1].error_redacted is None
    # 결과 순서·각 job 1회 시도 보존, 둘째 채널이 실제 전송됨(message.text 전달).
    assert [o.job.channel_id for o in outcomes] == ["ch-kakao", "ch-tg"]
    assert calls == [("ch-kakao", message.text), ("ch-tg", message.text)]


def test_dispatch_all_all_success_when_no_failure():
    message = _message()
    jobs = DispatchFanoutService.plan(
        message, [_rule("ch-tg"), _rule("ch-kakao")], channels=_channels(), job_id_for=_job_id_for
    )

    outcomes = DispatchFanoutService.dispatch_all(message, jobs, send=lambda _j, _t: None)

    assert all(o.sent for o in outcomes)
    assert all(o.error_redacted is None for o in outcomes)
    assert [isinstance(o, FanoutOutcome) for o in outcomes] == [True, True]


# ── AC2/AC5 — 분류 안 함·누출 방지(error_redacted = redact(repr(exc))) ────────────


def test_dispatch_all_error_is_redacted_and_unclassified():
    message = _message()
    [job] = DispatchFanoutService.plan(
        message, [_rule("ch-tg")], channels=_channels(), job_id_for=_job_id_for
    )
    # 예외 메시지에 봇토큰/chat_id 형태를 일부러 섞어도 error_redacted 에 원문이 안 남음.
    leaky = "send failed chat_id=987654321 token 8:AAE-fake-token-bodyxyz"
    exc = RuntimeError(leaky)

    def send(_job: DispatchJob, _text: str) -> None:
        raise exc

    [outcome] = DispatchFanoutService.dispatch_all(message, [job], send=send)

    # error_redacted 는 정확히 redact(repr(exc)) 와 일치(분류·재시도·상태 전이 없음).
    assert outcome.sent is False
    assert outcome.error_redacted == redact(repr(exc))
    # 누출 방지: 봇토큰/chat_id 원문이 남지 않음(redaction 통과).
    assert "987654321" not in outcome.error_redacted
    assert "8:AAE-fake-token-bodyxyz" not in outcome.error_redacted
    assert "***REDACTED***" in outcome.error_redacted
    # FanoutOutcome 에 error_code/category/retry 운영 필드가 없음(3.6 미선점).
    field_names = {f.name for f in FanoutOutcome.__dataclass_fields__.values()}
    assert field_names == {"job", "sent", "error_redacted"}


# ── fail-closed — unknown channel(dangling FK)은 조용히 미전송하지 않고 surface ──


def test_plan_raises_on_unknown_channel():
    message = _message()
    rules = [_rule("ch-missing")]  # channels 맵에 없는 channel_id

    with pytest.raises(KeyError):  # UnknownChannelError(KeyError) — fail-closed
        DispatchFanoutService.plan(
            message, rules, channels=_channels(), job_id_for=_job_id_for
        )

    with pytest.raises(UnknownChannelError):
        DispatchFanoutService.plan(
            message, rules, channels=_channels(), job_id_for=_job_id_for
        )


# ── frozen·결정성 ────────────────────────────────────────────────────────────────


def test_dispatch_job_and_outcome_are_frozen():
    message = _message()
    [job] = DispatchFanoutService.plan(
        message, [_rule("ch-tg")], channels=_channels(), job_id_for=_job_id_for
    )
    outcome = FanoutOutcome(job=job, sent=True)

    with pytest.raises(FrozenInstanceError):
        job.channel_id = "ch-x"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        outcome.sent = False  # type: ignore[misc]


def test_plan_is_deterministic():
    message = _message()
    rules = [_rule("ch-tg"), _rule("ch-kakao")]

    # 같은 입력 두 번 호출 → 동일 DispatchJob 들(내부 uuid4()/now() 미호출 — id는 주입 결정).
    first = DispatchFanoutService.plan(message, rules, channels=_channels(), job_id_for=_job_id_for)
    second = DispatchFanoutService.plan(message, rules, channels=_channels(), job_id_for=_job_id_for)

    assert first == second


# ── 재노출 — services.__all__ 포함 ───────────────────────────────────────────────


def test_reexported_from_services_package():
    import rider_server.services as services

    assert services.DispatchFanoutService is DispatchFanoutService
    assert services.DispatchJob is DispatchJob
    assert services.FanoutOutcome is FanoutOutcome
    for name in ("DispatchFanoutService", "DispatchJob", "FanoutOutcome"):
        assert name in services.__all__


# ╔════════════════════════════════════════════════════════════════════════════╗
# ║ QA gap coverage (qa-generate-e2e-tests) — 경계값·격리 강화·계약 잠금 보강.    ║
# ║ 기존 10케이스가 happy path/주요 AC를 덮고, 아래는 미커버 경계·순서·전파를    ║
# ║ 잠근다(순수 additive — 신규 케이스만 추가, 기존 동작 무변경).               ║
# ╚════════════════════════════════════════════════════════════════════════════╝


# ── plan 경계값 — 빈 rules / 전부 disabled → 빈 fan-out([], 결정적) ──────────────


def test_plan_returns_empty_when_no_rules():
    # rules 가 비면 DispatchJob 0개(빈 리스트 반환 — 예외 아님, 정상 no-op).
    jobs = DispatchFanoutService.plan(
        _message(), [], channels=_channels(), job_id_for=_job_id_for
    )

    assert jobs == []


def test_plan_returns_empty_when_all_rules_disabled():
    # 모든 rule 이 enabled=False(soft delete) → 활성 채널 0개 → fan-out 없음.
    rules = [_rule("ch-tg", enabled=False), _rule("ch-kakao", enabled=False)]

    jobs = DispatchFanoutService.plan(
        _message(), rules, channels=_channels(), job_id_for=_job_id_for
    )

    assert jobs == []


# ── plan 순서/스킵 계약 — disabled 는 채널 해석 '전에' 스킵(AC1.2 ⟂ fail-closed) ─


def test_plan_skips_disabled_rule_before_channel_lookup():
    # disabled rule 은 enabled 체크가 채널 조회보다 먼저라, channels 맵에 없는
    # channel_id 를 가져도 UnknownChannelError 가 나지 않고 조용히 제외된다.
    # (soft-delete 제외가 fail-closed 채널 검증보다 선행함을 잠근다.)
    rules = [_rule("ch-missing", enabled=False), _rule("ch-tg")]

    jobs = DispatchFanoutService.plan(
        _message(), rules, channels=_channels(), job_id_for=_job_id_for
    )

    assert [j.channel_id for j in jobs] == ["ch-tg"]


def test_plan_preserves_order_with_interspersed_disabled():
    # [활성, 비활성, 활성] → 비활성만 빠지고 나머지는 입력 순서 보존(2개).
    channels = {
        "ch-a": MessengerChannel(id="ch-a", tenant_id="tn-1", messenger=Messenger.TELEGRAM),
        "ch-b": MessengerChannel(id="ch-b", tenant_id="tn-1", messenger=Messenger.KAKAO),
        "ch-c": MessengerChannel(id="ch-c", tenant_id="tn-1", messenger=Messenger.TELEGRAM),
    }
    rules = [_rule("ch-a"), _rule("ch-b", enabled=False), _rule("ch-c")]

    jobs = DispatchFanoutService.plan(
        _message(), rules, channels=channels, job_id_for=_job_id_for
    )

    assert [j.channel_id for j in jobs] == ["ch-a", "ch-c"]


# ── AC3 강화 — 같은 messenger 의 채널 2개도 channel_id 로 distinct(scope 비축소) ──


def test_plan_fans_out_to_two_channels_of_same_messenger():
    # 같은 target·같은 messenger(둘 다 TELEGRAM)라도 channel_id 가 다르면 별도 fan-out.
    # scope 의 식별 차원이 messenger 가 아니라 channel_id 임을 잠근다(AC3 핵심).
    channels = {
        "ch-tg-1": MessengerChannel(
            id="ch-tg-1", tenant_id="tn-1", messenger=Messenger.TELEGRAM
        ),
        "ch-tg-2": MessengerChannel(
            id="ch-tg-2", tenant_id="tn-1", messenger=Messenger.TELEGRAM
        ),
    }
    rules = [_rule("ch-tg-1"), _rule("ch-tg-2")]

    jobs = DispatchFanoutService.plan(
        _message(), rules, channels=channels, job_id_for=_job_id_for
    )

    assert len(jobs) == 2
    # messenger 동일해도 channel_id 차원에서 distinct → scope 가 target_id 로 축소되지 않음.
    assert [j.messenger for j in jobs] == [Messenger.TELEGRAM, Messenger.TELEGRAM]
    assert {(j.target_id, j.channel_id) for j in jobs} == {
        (_TARGET_ID, "ch-tg-1"),
        (_TARGET_ID, "ch-tg-2"),
    }


# ── fail-closed 강화 — UnknownChannelError 가 원인 channel_id 를 surface + KeyError 체이닝 ─


def test_plan_unknown_channel_error_carries_channel_id_and_chains():
    rules = [_rule("ch-missing")]

    with pytest.raises(UnknownChannelError) as exc_info:
        DispatchFanoutService.plan(
            _message(), rules, channels=_channels(), job_id_for=_job_id_for
        )

    # dangling FK 를 조용히 삼키지 않고 어떤 channel_id 가 문제인지 드러낸다.
    assert exc_info.value.args[0] == "ch-missing"
    # 원래 KeyError 로부터 체이닝(`raise ... from exc`) — 디버깅 가능성 보존.
    assert isinstance(exc_info.value.__cause__, KeyError)


# ── dispatch_all 경계값 — 빈 jobs → 빈 outcomes(send 미호출) ──────────────────────


def test_dispatch_all_returns_empty_when_no_jobs():
    calls: list[tuple[str, str]] = []

    outcomes = DispatchFanoutService.dispatch_all(
        _message(), [], send=lambda j, t: calls.append((j.channel_id, t))
    )

    assert outcomes == []
    assert calls == []  # 보낼 job 이 없으면 sender 도 호출하지 않음.


# ── AC2 강화 — 중간 채널 실패가 양옆(앞·뒤) 성공을 모두 무효화하지 않음(3채널) ──


def test_dispatch_all_isolates_middle_channel_failure():
    message = _message()
    channels = {
        "ch-a": MessengerChannel(id="ch-a", tenant_id="tn-1", messenger=Messenger.TELEGRAM),
        "ch-b": MessengerChannel(id="ch-b", tenant_id="tn-1", messenger=Messenger.KAKAO),
        "ch-c": MessengerChannel(id="ch-c", tenant_id="tn-1", messenger=Messenger.TELEGRAM),
    }
    jobs = DispatchFanoutService.plan(
        message,
        [_rule("ch-a"), _rule("ch-b"), _rule("ch-c")],
        channels=channels,
        job_id_for=_job_id_for,
    )
    calls: list[str] = []

    def send(job: DispatchJob, _text: str) -> None:
        calls.append(job.channel_id)
        if job.channel_id == "ch-b":  # 가운데 채널만 실패
            raise RuntimeError("middle channel send failed")

    outcomes = DispatchFanoutService.dispatch_all(message, jobs, send=send)

    # 가운데 실패가 루프를 끊지 않음 → 앞(ch-a)·뒤(ch-c) 모두 성공, 셋 다 1회씩 시도.
    assert [o.sent for o in outcomes] == [True, False, True]
    assert calls == ["ch-a", "ch-b", "ch-c"]
    assert outcomes[1].error_redacted is not None


def test_dispatch_all_contains_every_failure_independently():
    # 모든 채널이 실패해도 각 job 이 정확히 한 번씩 시도되고 서로 독립 기록된다
    # (한 실패가 다음 시도를 막지 않음 — 격리의 극단 케이스).
    message = _message()
    jobs = DispatchFanoutService.plan(
        message,
        [_rule("ch-tg"), _rule("ch-kakao")],
        channels=_channels(),
        job_id_for=_job_id_for,
    )
    attempts: list[str] = []

    def send(job: DispatchJob, _text: str) -> None:
        attempts.append(job.channel_id)
        raise RuntimeError("all channels down")

    outcomes = DispatchFanoutService.dispatch_all(message, jobs, send=send)

    assert attempts == ["ch-tg", "ch-kakao"]  # 둘 다 시도됨(첫 실패가 둘째를 막지 않음)
    assert [o.sent for o in outcomes] == [False, False]
    assert all(o.error_redacted is not None for o in outcomes)


# ── 격리 경계 잠금 — 구조적 contain 은 Exception 만, BaseException 은 전파 ─────────


def test_dispatch_all_does_not_swallow_base_exception():
    # 채널 격리는 운영성 실패(Exception)만 contain 한다. KeyboardInterrupt/SystemExit
    # 같은 제어흐름 BaseException 은 삼키면 안 됨 — except Exception 경계를 잠근다.
    message = _message()
    [job] = DispatchFanoutService.plan(
        message, [_rule("ch-tg")], channels=_channels(), job_id_for=_job_id_for
    )

    def send(_job: DispatchJob, _text: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        DispatchFanoutService.dispatch_all(message, [job], send=send)
