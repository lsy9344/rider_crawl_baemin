"""Story 3.8 / AC1~AC9 (FR-3, NFR-22·24·25, ADD-16 6~8단계) — dry-run 비교·승인·cutover 잠금.

신규 경로(`CrawlService`(3.1)·`MessageRenderService`(3.3))를 실발송 없이 dry-run으로 돌려
신규 메시지를 만들고, 2.7 ``MigrationSeed`` 기준선과 hash로 비교하고, 차이 시 자동 활성화를
막고(승인 게이트), cutover 동시전송 방지·rollback dedup 보존을 단언한다.

외부 호출 없음 — fake ``crawl_snapshot``·in-memory 값 객체만. 평면 ``tests/server/`` 컨벤션
(conftest 공유 없이 자급자족, ``__init__.py`` 미추가). fixture는 가짜 id·가짜 64-hex hash·
고정 ``datetime`` 만 쓴다(실 토큰/chat_id/비밀번호 0 — 2.7 ``test_migration.py`` 선례).
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from rider_crawl.config import AppConfig
from rider_crawl.message import render_current_screen_message
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)
from rider_crawl.parser import MissingPerformanceDataError
from rider_crawl.redaction import REDACTED, redact
from rider_server.domain import DeliveryLog, DeliveryRule, DeliveryStatus
from rider_server.migration import (
    CutoverApprovalError,
    CutoverResult,
    DryRunComparison,
    DryRunResult,
    DualSendError,
    MigrationSeed,
    MigrationState,
    RollbackResult,
    TargetMigration,
    activate_cutover,
    approve_after_review,
    assert_no_dual_active_send,
    compare_to_baseline,
    mark_dry_run_passed,
    roll_back_cutover,
    run_dry_run,
)

# 결정성: dry-run 내부 now() 금지 — 호출부가 고정값을 주입한다(2026-01-05 = 월요일/주중).
_FIXED_AT = datetime(2026, 1, 5, 14, 2)

# 가짜 64-hex hash(실 secret 아님 — sha256 모양만).
_HASH_OTHER = "a" * 64


# ── fixture: 대표 배민/쿠팡 snapshot(test_message_render.py 골든 fixture와 동일 값) ──


def _baemin_snapshot() -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name="제이앤에이치플러스 의정부남부",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        afternoon_non_peak_count=41.8,
        dinner_peak_count=0,
        dinner_non_peak_count=3,
        non_peak_count=44.8,
        active_riders=5,
        reject_rate=2.3,
    )


def _coupang_current_screen() -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name="제이앤에이치플러스 의정부남부",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        dinner_peak_count=0,
        non_peak_count=41.8,
        active_riders=3,
    )


def _coupang_snapshot() -> PerformanceSnapshot:
    return PerformanceSnapshot(
        current_screen=_coupang_current_screen(),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=18, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )


def _config(tmp_path) -> AppConfig:
    # test_message_render.py 의 _config 동등(가짜 값만). config 는 fake crawler 로만 흐른다.
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        platform_name="baemin",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser-profile",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_message_thread_id="",
        crawl_name="",
    )


def _fake_crawl(snapshot):
    # 외부 브라우저/Telegram 미호출 — 주입된 snapshot 을 그대로 반환(config 무시).
    def _crawl(config):
        return snapshot

    return _crawl


def _dry_run(tmp_path, snapshot, *, target_id="target-1", source_label="센터"):
    return run_dry_run(
        _config(tmp_path),
        crawl_snapshot=_fake_crawl(snapshot),
        target_id=target_id,
        message_id="msg-1",
        snapshot_id="snap-1",
        source_label=source_label,
        now=_FIXED_AT,
    )


def _target(state: MigrationState) -> TargetMigration:
    return TargetMigration(
        crawling_index=1,
        legacy_alias="크롤링1",
        state=state,
        mapping=None,
        seed=None,
        state_copied_to=None,
    )


def _rule(*, rule_id="rule-1", target_id="target-1", channel_id="chan-1", enabled=True) -> DeliveryRule:
    return DeliveryRule(id=rule_id, target_id=target_id, channel_id=channel_id, enabled=enabled)


def _log(*, log_id="log-1", dedup_key="dedup-1") -> DeliveryLog:
    return DeliveryLog(
        id=log_id,
        message_id="msg-1",
        channel_id="chan-1",
        status=DeliveryStatus.SENT,
        dedup_key=dedup_key,
    )


# ── AC1 — 실발송 없는 dry-run: 대표 배민/쿠팡 성공·no-send ──────────────────────


def test_run_dry_run_baemin_succeeds_without_sending(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())

    assert isinstance(result, DryRunResult)
    assert result.target_id == "target-1"
    assert result.sent is False  # 불변식: dry-run 은 실발송하지 않는다
    assert result.message.text_hash  # 신규 메시지 hash 채워짐
    assert result.message.text_hash == hashlib.sha256(
        result.message.text.encode("utf-8")
    ).hexdigest()


def test_run_dry_run_coupang_succeeds_without_sending(tmp_path):
    result = _dry_run(tmp_path, _coupang_snapshot(), target_id="target-2")

    assert result.target_id == "target-2"
    assert result.sent is False
    assert result.message.text_hash


def test_run_dry_run_has_no_sender_argument():
    # 구조적 no-send(AC1): run_dry_run 시그니처에 sender/send/dispatch 인자가 0이다.
    params = set(inspect.signature(run_dry_run).parameters)
    for forbidden in ("send", "sender", "send_message", "dispatch", "dispatcher"):
        assert forbidden not in params


def test_cutover_module_does_not_import_dispatch_path():
    # 실발송 경로(dispatch/중앙전송)를 cutover 모듈 namespace 로 끌어오지 않는다(구조 보장).
    import rider_server.migration.cutover as cutover

    for name in (
        "DispatchService",
        "DispatchFanoutService",
        "CentralTelegramSender",
        "IdempotentDeliveryService",
    ):
        assert not hasattr(cutover, name), f"실발송 경로가 import됨(no-send 위반): {name}"


def test_run_dry_run_calls_crawl_exactly_once(tmp_path):
    calls = []

    def spy(config):
        calls.append(config)
        return _baemin_snapshot()

    run_dry_run(
        _config(tmp_path),
        crawl_snapshot=spy,
        target_id="target-1",
        message_id="m",
        snapshot_id="s",
        now=_FIXED_AT,
    )
    assert len(calls) == 1


def test_run_dry_run_propagates_collection_failure(tmp_path):
    # 필수 데이터 누락(파서 fail-closed)을 fake 가 재현 → run_dry_run 이 삼키지 않고 전파해
    # 잘못된 dry-run 메시지를 만들지 않는다(오발송보다 미발송).
    def failing(config):
        raise MissingPerformanceDataError("배민 배달현황 테이블을 찾지 못했습니다")

    with pytest.raises(MissingPerformanceDataError):
        run_dry_run(
            _config(tmp_path),
            crawl_snapshot=failing,
            target_id="target-1",
            message_id="m",
            snapshot_id="s",
            now=_FIXED_AT,
        )


def test_run_dry_run_propagates_render_type_error(tmp_path):
    # 예상 외 snapshot 타입 → render_message 가 TypeError(재구현 0·방어적) → 전파(fail-closed).
    with pytest.raises(TypeError):
        run_dry_run(
            _config(tmp_path),
            crawl_snapshot=_fake_crawl(object()),
            target_id="target-1",
            message_id="m",
            snapshot_id="s",
            now=_FIXED_AT,
        )


# ── AC2 — 기준선 비교(old seed hash vs new text_hash) ─────────────────────────


def test_compare_matches_when_baseline_hash_equals_new(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())
    seed = MigrationSeed(
        monitoring_target_id="target-1",
        message_hash=result.message.text_hash,  # 동일 hash = 메시지 무변경
        scope_hash="scope-1",
    )

    comparison = compare_to_baseline(result, seed=seed)

    assert isinstance(comparison, DryRunComparison)
    assert comparison.matches is True
    assert comparison.baseline_hash == result.message.text_hash
    assert comparison.new_hash == result.message.text_hash
    assert comparison.target_id == "target-1"


def test_compare_differs_when_baseline_hash_changes(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())
    seed = MigrationSeed(
        monitoring_target_id="target-1",
        message_hash=_HASH_OTHER,  # 다른 hash = 차이
        scope_hash="scope-1",
    )

    comparison = compare_to_baseline(result, seed=seed)

    assert comparison.matches is False
    assert comparison.baseline_hash == _HASH_OTHER
    assert comparison.new_hash == result.message.text_hash


def test_compare_treats_missing_seed_as_diff(tmp_path):
    # 첫 발송 전 탭(seed=None) → matches=False(차이로 취급, fail-closed: 자동 활성화 금지).
    result = _dry_run(tmp_path, _baemin_snapshot())

    comparison = compare_to_baseline(result, seed=None)

    assert comparison.matches is False
    assert comparison.baseline_hash is None
    assert comparison.new_hash == result.message.text_hash


def test_compare_preview_reuses_redacted_preview(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())
    comparison = compare_to_baseline(result, seed=None)

    assert comparison.preview_redacted == result.message.text_redacted_preview


def test_compare_preview_masks_secret_shaped_label(tmp_path):
    # 미리보기는 redact 통과(NFR-5) — 비밀처럼 보이는 라벨이 섞여도 본문이 남지 않는다.
    fake_token_body = "AAE-fake-token-xyz"
    result = _dry_run(tmp_path, _baemin_snapshot(), source_label=f"8:{fake_token_body}")

    comparison = compare_to_baseline(result, seed=None)

    assert fake_token_body not in comparison.preview_redacted
    assert REDACTED in comparison.preview_redacted


# ── AC2 — 승인 게이트(차이 시 자동 활성화 차단) ───────────────────────────────


def test_approve_after_review_blocks_unacknowledged_diff(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())
    comparison = compare_to_baseline(result, seed=None)  # matches=False
    target = _target(MigrationState.DRY_RUN_PASSED)

    with pytest.raises(CutoverApprovalError):
        approve_after_review(target, comparison, operator_acknowledged_diff=False)


def test_approve_after_review_passes_when_match(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())
    seed = MigrationSeed(
        monitoring_target_id="target-1",
        message_hash=result.message.text_hash,
        scope_hash="scope-1",
    )
    comparison = compare_to_baseline(result, seed=seed)  # matches=True
    target = _target(MigrationState.DRY_RUN_PASSED)

    approved = approve_after_review(target, comparison, operator_acknowledged_diff=False)

    assert approved.state == MigrationState.APPROVED


def test_approve_after_review_passes_when_operator_acknowledges_diff(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())
    comparison = compare_to_baseline(result, seed=None)  # matches=False
    target = _target(MigrationState.DRY_RUN_PASSED)

    approved = approve_after_review(target, comparison, operator_acknowledged_diff=True)

    assert approved.state == MigrationState.APPROVED


def test_approve_after_review_does_not_weaken_state_machine(tmp_path):
    # DRY_RUN_PASSED 가 아닌 target 에 호출 → 2.7 _transition 이 ValueError(게이트 약화 0).
    result = _dry_run(tmp_path, _baemin_snapshot())
    seed = MigrationSeed(
        monitoring_target_id="target-1",
        message_hash=result.message.text_hash,
        scope_hash="scope-1",
    )
    comparison = compare_to_baseline(result, seed=seed)  # matches=True
    target = _target(MigrationState.MAPPED)  # 잘못된 선행 상태

    with pytest.raises(ValueError):
        approve_after_review(target, comparison, operator_acknowledged_diff=True)


def test_full_state_flow_dry_run_to_active(tmp_path):
    # 2.7 상태머신 그대로: MAPPED → DRY_RUN_PASSED → APPROVED → ACTIVE.
    result = _dry_run(tmp_path, _baemin_snapshot())
    seed = MigrationSeed(
        monitoring_target_id="target-1",
        message_hash=result.message.text_hash,
        scope_hash="scope-1",
    )
    comparison = compare_to_baseline(result, seed=seed)

    target = _target(MigrationState.MAPPED)
    target = mark_dry_run_passed(target)
    assert target.state == MigrationState.DRY_RUN_PASSED

    target = approve_after_review(target, comparison, operator_acknowledged_diff=False)
    assert target.state == MigrationState.APPROVED

    cutover = activate_cutover(target, [_rule()], legacy_path_active=False)
    assert cutover.target.state == MigrationState.ACTIVE


# ── AC3 — cutover 동시전송 방지(NFR-24) ───────────────────────────────────────


def test_assert_no_dual_active_send_raises_when_both_active():
    with pytest.raises(DualSendError):
        assert_no_dual_active_send(
            target_id="target-1", legacy_path_active=True, new_rule_enabled=True
        )


@pytest.mark.parametrize(
    "legacy,new",
    [(True, False), (False, True), (False, False)],
)
def test_assert_no_dual_active_send_ok_when_not_both(legacy, new):
    # 한쪽만/둘 다 꺼짐 → 통과(None 반환).
    assert (
        assert_no_dual_active_send(
            target_id="target-1", legacy_path_active=legacy, new_rule_enabled=new
        )
        is None
    )


def test_activate_cutover_blocks_when_legacy_active():
    target = _target(MigrationState.APPROVED)

    with pytest.raises(DualSendError):
        activate_cutover(target, [_rule()], legacy_path_active=True)


def test_activate_cutover_success_when_legacy_off():
    target = _target(MigrationState.APPROVED)
    rules = [_rule(rule_id="r1", enabled=False), _rule(rule_id="r2", enabled=False)]

    result = activate_cutover(target, rules, legacy_path_active=False)

    assert isinstance(result, CutoverResult)
    assert result.target.state == MigrationState.ACTIVE
    assert result.legacy_path_active is False
    assert len(result.enabled_rules) == 2
    assert all(rule.enabled is True for rule in result.enabled_rules)


def test_activate_cutover_requires_approved_state():
    # APPROVED 가 아니면 2.7 activate 가 ValueError(승인 없는 활성화 차단).
    target = _target(MigrationState.DRY_RUN_PASSED)

    with pytest.raises(ValueError):
        activate_cutover(target, [_rule()], legacy_path_active=False)


def test_activate_cutover_guard_runs_before_transition():
    # legacy 켜짐 + 잘못된 상태 → 동시전송 가드가 먼저 → DualSendError(전이 시도 전 차단).
    target = _target(MigrationState.MAPPED)

    with pytest.raises(DualSendError):
        activate_cutover(target, [_rule()], legacy_path_active=True)


# ── AC3 — rollback dedup 보존(NFR-25) ─────────────────────────────────────────


def test_roll_back_cutover_disables_rules_without_mutating_inputs():
    target = _target(MigrationState.ACTIVE)
    rules = [_rule(rule_id="r1", enabled=True), _rule(rule_id="r2", enabled=True)]

    result = roll_back_cutover(target, rules, delivery_logs=[])

    assert isinstance(result, RollbackResult)
    assert all(rule.enabled is False for rule in result.disabled_rules)
    # 원본 rules 는 frozen — replace 산출물이라 입력은 그대로 enabled=True(불변).
    assert all(rule.enabled is True for rule in rules)


def test_roll_back_cutover_preserves_logs_for_dedup():
    target = _target(MigrationState.ACTIVE)
    logs = [_log(log_id="l1", dedup_key="dk-1"), _log(log_id="l2", dedup_key="dk-2")]

    result = roll_back_cutover(target, [_rule()], delivery_logs=logs)

    # dedup 기록을 변경·삭제 없이 그대로 보존 → rollback 후 재전송이 중복을 만들지 않는다.
    assert result.preserved_logs == tuple(logs)
    assert len(result.preserved_logs) == 2
    assert [log.dedup_key for log in result.preserved_logs] == ["dk-1", "dk-2"]


def test_roll_back_cutover_state_and_legacy_restored():
    target = _target(MigrationState.ACTIVE)

    result = roll_back_cutover(target, [_rule()], delivery_logs=[_log()])

    assert result.target.state == MigrationState.ROLLED_BACK
    assert result.legacy_path_restored is True


def test_roll_back_cutover_works_from_any_state():
    # 2.7 roll_back 은 선행 상태 제약이 없다(임의 상태 → ROLLED_BACK).
    for state in (MigrationState.MAPPED, MigrationState.APPROVED, MigrationState.PAUSED):
        result = roll_back_cutover(_target(state), [_rule()], delivery_logs=[])
        assert result.target.state == MigrationState.ROLLED_BACK


def test_roll_back_cutover_accepts_iterable_logs_once():
    # delivery_logs 가 1회성 iterator 여도 tuple 로 안전 고정(소비 후 비지 않음).
    target = _target(MigrationState.ACTIVE)
    logs_iter = iter([_log(log_id="l1"), _log(log_id="l2")])

    result = roll_back_cutover(target, [_rule()], delivery_logs=logs_iter)

    assert len(result.preserved_logs) == 2


# ── AC4·AC5 — 재사용·결정성·비노출·frozen·재노출 ──────────────────────────────


def test_run_dry_run_is_deterministic(tmp_path):
    # 같은 입력(같은 now 주입) → 같은 결과(결정적·내부 now()/uuid4() 미호출).
    first = _dry_run(tmp_path, _coupang_snapshot())
    second = _dry_run(tmp_path, _coupang_snapshot())

    assert first.message.text == second.message.text
    assert first.message.text_hash == second.message.text_hash


def test_run_dry_run_reuses_renderer_not_reimplements(tmp_path):
    # 렌더는 3.3 → render_current_screen_message 경유만(재구현 0 — FR-2). 바이트 동등.
    snapshot = _baemin_snapshot()
    result = _dry_run(tmp_path, snapshot, source_label="센터")

    assert result.message.text == render_current_screen_message(
        snapshot, source_label="센터", now=_FIXED_AT
    )


def test_dry_run_result_sent_invariant_is_false(tmp_path):
    # DryRunResult.sent 는 항상 False(생성 default — True 로 만드는 경로가 없다).
    result = _dry_run(tmp_path, _baemin_snapshot())
    assert result.sent is False
    assert DryRunResult(target_id="t", message=result.message).sent is False


def test_value_objects_are_frozen(tmp_path):
    result = _dry_run(tmp_path, _baemin_snapshot())
    comparison = compare_to_baseline(result, seed=None)
    cutover = activate_cutover(_target(MigrationState.APPROVED), [_rule()], legacy_path_active=False)
    rollback = roll_back_cutover(_target(MigrationState.ACTIVE), [_rule()], delivery_logs=[_log()])

    with pytest.raises(FrozenInstanceError):
        result.sent = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        comparison.matches = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        cutover.legacy_path_active = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        rollback.legacy_path_restored = False  # type: ignore[misc]


def test_exception_breadcrumbs_pass_redaction(tmp_path):
    # 예외 breadcrumb 은 redact() 통과(idempotent) — 평문 token/chat_id 가 남지 않는다.
    result = _dry_run(tmp_path, _baemin_snapshot())
    comparison = compare_to_baseline(result, seed=None)

    with pytest.raises(CutoverApprovalError) as approval_exc:
        approve_after_review(_target(MigrationState.DRY_RUN_PASSED), comparison, operator_acknowledged_diff=False)
    with pytest.raises(DualSendError) as dual_exc:
        assert_no_dual_active_send(target_id="target-1", legacy_path_active=True, new_rule_enabled=True)

    for message in (str(approval_exc.value), str(dual_exc.value)):
        assert redact(message) == message  # 이미 redact 통과(잔여 secret 0)


def test_story_3_8_symbols_reexported_from_package():
    import rider_server.migration as migration

    for name in (
        "run_dry_run",
        "DryRunResult",
        "compare_to_baseline",
        "DryRunComparison",
        "approve_after_review",
        "CutoverApprovalError",
        "assert_no_dual_active_send",
        "activate_cutover",
        "CutoverResult",
        "DualSendError",
        "roll_back_cutover",
        "RollbackResult",
    ):
        assert hasattr(migration, name), f"재노출 누락: {name}"
        assert name in migration.__all__, f"__all__ 누락: {name}"


def test_2_7_symbols_still_reexported():
    # 2.7 심볼 무삭제(additive) — 회귀 0.
    import rider_server.migration as migration

    for name in (
        "run_migration",
        "MigrationState",
        "MigrationSeed",
        "TargetMigration",
        "mark_dry_run_passed",
        "approve",
        "activate",
        "roll_back",
    ):
        assert hasattr(migration, name)
        assert name in migration.__all__


# ── QA 자동 보완 갭(qa-generate-e2e-tests) — AC1~AC5 미커버 경계 ────────────────


def test_cutover_errors_are_valueerror_subclasses():
    # 설계 계약(AC2·AC3): 승인 게이트·동시전송 가드 예외는 2.7 전이 ValueError 를 계승해
    # 호출부가 `except ValueError` 로 일관되게 잡을 수 있어야 한다. 둘은 서로 구별되는 타입.
    assert issubclass(CutoverApprovalError, ValueError)
    assert issubclass(DualSendError, ValueError)
    assert CutoverApprovalError is not DualSendError

    # 기능적 확인: ValueError 핸들러가 두 예외를 모두 포착한다.
    try:
        assert_no_dual_active_send(
            target_id="target-1", legacy_path_active=True, new_rule_enabled=True
        )
    except ValueError:
        pass
    else:  # pragma: no cover - 가드가 raise 하지 않으면 계약 위반
        raise AssertionError("DualSendError 가 ValueError 로 잡히지 않음")


def test_run_dry_run_injects_message_and_snapshot_ids(tmp_path):
    # 결정성(AC4): 내부 uuid4() 금지 — 주입된 message_id/snapshot_id 가 그대로 Message 로 흐른다.
    result = run_dry_run(
        _config(tmp_path),
        crawl_snapshot=_fake_crawl(_baemin_snapshot()),
        target_id="target-1",
        message_id="msg-42",
        snapshot_id="snap-42",
        now=_FIXED_AT,
    )

    assert result.message.id == "msg-42"
    assert result.message.snapshot_id == "snap-42"


def test_run_dry_run_with_now_none_still_no_send(tmp_path):
    # now=None 분기(AC4): 렌더러의 기존 now() 동작을 보존하되 본 함수가 새 발송을 만들지 않는다.
    result = run_dry_run(
        _config(tmp_path),
        crawl_snapshot=_fake_crawl(_baemin_snapshot()),
        target_id="target-1",
        message_id="m",
        snapshot_id="s",
        now=None,
    )

    assert result.sent is False
    assert result.message.text_hash  # 렌더는 정상 수행(no-send 와 무관)


def test_run_dry_run_coupang_reuses_renderer_not_reimplements(tmp_path):
    # 렌더 재구현 0(AC4) — 더 복잡한 PerformanceSnapshot(쿠팡) 경로도 3.3 렌더러 바이트 동등.
    snapshot = _coupang_snapshot()
    result = _dry_run(tmp_path, snapshot, target_id="target-2", source_label="센터")

    assert result.message.text == render_current_screen_message(
        snapshot, source_label="센터", now=_FIXED_AT
    )


def test_compare_uses_result_target_id_not_seed(tmp_path):
    # 비교의 target_id 는 dry-run 결과에서 도출(seed 의 monitoring_target_id 가 아님) — AC2.
    result = _dry_run(tmp_path, _baemin_snapshot(), target_id="target-1")
    seed = MigrationSeed(
        monitoring_target_id="OTHER-TARGET",  # 결과와 다른 식별자
        message_hash=result.message.text_hash,
        scope_hash="scope-1",
    )

    comparison = compare_to_baseline(result, seed=seed)

    assert comparison.target_id == "target-1"
    assert comparison.matches is True


def test_activate_cutover_with_no_rules_succeeds(tmp_path):
    # rules 가 비어도(legacy off) cutover 는 ACTIVE 로 전이하고 enabled_rules 는 빈 튜플.
    target = _target(MigrationState.APPROVED)

    result = activate_cutover(target, [], legacy_path_active=False)

    assert result.target.state == MigrationState.ACTIVE
    assert result.enabled_rules == ()
    assert result.legacy_path_active is False


def test_activate_cutover_guard_breadcrumb_uses_legacy_alias_without_rules(tmp_path):
    # rules 가 없으면 _target_id_for 가 legacy_alias 로 breadcrumb 를 도출한다(가드 fallback).
    target = _target(MigrationState.APPROVED)  # legacy_alias="크롤링1", mapping=None

    with pytest.raises(DualSendError) as exc:
        activate_cutover(target, [], legacy_path_active=True)

    assert "크롤링1" in str(exc.value)  # 평문 secret 아님(legacy 별칭) — redact 통과 후 보존


def test_roll_back_cutover_with_no_rules_preserves_logs(tmp_path):
    # rules 가 비어도 rollback 은 동작 — disabled_rules 빈 튜플, dedup 로그는 그대로 보존.
    target = _target(MigrationState.ACTIVE)
    logs = [_log(log_id="l1", dedup_key="dk-1")]

    result = roll_back_cutover(target, [], delivery_logs=logs)

    assert result.disabled_rules == ()
    assert result.preserved_logs == tuple(logs)
    assert result.target.state == MigrationState.ROLLED_BACK
    assert result.legacy_path_restored is True
