"""Story 5.10 / AC2·AC3 — negative safety + 운영 안전 **추적성 매트릭스**(always-run, 무 DB).

**재구현 금지(이 스토리 1순위 함정).** AC2 의 7개 negative 시나리오와 AC3 의 마이그레이션·운영
안전 검증 항목은 5.1~5.9 가 이미 만든 **통과하는 정본 테스트로 존재**한다. 본 파일은 그것들을
다시 짜지 않고, **시나리오 ↔ 정본 테스트 ↔ 정본 FailureCategory** 매핑을 한 곳에 묶어 리뷰어가
AC2/AC3 충족의 코드 근거를 한 번에 확인하게 한다.

**완료 위조 차단(5.9 ``test_runbooks_present`` 선례).** 각 정본 테스트가 **실제로 정의되어 있음**을
``ast`` 로 파싱해 확인한다(import 부작용·runpy 경고·DB 드라이버 import 회피 — 메모
``negative-guard-tests-use-ast``). 정본 테스트가 사라지거나 이름이 바뀌면 본 매트릭스가 깨져
"AC2/AC3 충족" 주장이 근거를 잃는다(파일/통과 없이 done 불가).

**always-run 보증.** PG-gated 정본(``TEST_DATABASE_URL`` 없으면 skip)도 본 추적은 파일 정의를
AST 로 확인하므로 **항상 실행**된다. 추가로 핵심 안전 어휘(FailureCategory 7·DeliveryStatus·전역
kill switch AND 의미)를 always-run 으로 재단언해 PG 부재 CI 에서도 의미가 잠긴다.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

from rider_server.domain.states import DeliveryStatus, FailureCategory
from rider_server.services.recovery import effective_send_enabled

_REPO_ROOT = Path(__file__).resolve().parents[2]

# always-run = TEST_DATABASE_URL 없어도 실행 / PG = 미설정 시 skip(본 추적은 양쪽 다 always-run).
ALWAYS = "always-run"
PG = "PG"


# ── 매트릭스 한 행: (AC, 시나리오, 파일 경로(repo-relative), 정본 테스트명, gating, FailureCategory) ──
# FailureCategory 가 None 이면 해당 시나리오의 차단 의미가 enum error_code 가 아님(예: 404/401/race).
MATRIX: tuple[tuple[str, str, str, str, str, FailureCategory | None], ...] = (
    # ── AC2: 7개 핵심 negative safety 시나리오(fail-closed 차단) ──────────────────
    # 1. wrong tenant — cross-tenant 접근 차단(→404), 상태/audit 부작용 0.
    ("AC2", "wrong tenant",
     "tests/server/test_admin_actions.py", "test_route_cross_tenant_is_404", ALWAYS, None),
    ("AC2", "wrong tenant",
     "tests/server/test_admin_actions.py", "test_cross_tenant_subscription_blocked", ALWAYS, None),
    ("AC2", "wrong tenant (no audit side-effect)",
     "tests/negative/test_security_pg.py", "test_cross_tenant_pause_leaves_no_audit", PG, None),
    # 2. wrong profile — 센터/상점명 불일치/공백 → 브라우저 시작 전 TARGET_VALIDATION_FAILURE.
    ("AC2", "wrong profile",
     "tests/agent/test_browser_profile.py",
     "test_empty_coupang_center_blocks_target_and_does_not_start", ALWAYS,
     FailureCategory.TARGET_VALIDATION_FAILURE),
    ("AC2", "wrong profile",
     "tests/agent/test_browser_profile.py",
     "test_map_center_mismatch_to_target_validation_failure", ALWAYS,
     FailureCategory.TARGET_VALIDATION_FAILURE),
    # 3. wrong Kakao room — 동명/모호 방 → 미발송·재시도/대체방 fallback 0(KAKAO_FAILURE).
    ("AC2", "wrong Kakao room",
     "tests/agent/test_kakao_sender.py",
     "test_unsafe_selection_maps_to_ambiguous_room_and_does_not_send_elsewhere", ALWAYS,
     FailureCategory.KAKAO_FAILURE),
    ("AC2", "wrong Kakao room",
     "tests/agent/test_kakao_sender.py",
     "test_ambiguous_send_error_is_not_retried_or_requeued", ALWAYS,
     FailureCategory.KAKAO_FAILURE),
    ("AC2", "wrong Kakao room",
     "tests/agent/test_kakao_sender.py",
     "test_failure_does_not_auto_resend_to_another_room", ALWAYS,
     FailureCategory.KAKAO_FAILURE),
    # 4. stale Agent token — revoke 된 token claim → 401, 추가 작업 차단.
    ("AC2", "stale Agent token",
     "tests/server/test_agent_token_revoke.py", "test_revoke_then_claim_is_401", ALWAYS, None),
    ("AC2", "stale Agent token",
     "tests/server/test_agent_token_revoke.py", "test_revoke_marks_revoked_and_audits", ALWAYS, None),
    # 5. restored DB / replay — 같은 Snapshot replay → dedup 으로 재전송 차단(DUPLICATE_BLOCKED).
    ("AC2", "restored DB / replay",
     "tests/server/test_idempotency_e2e.py",
     "test_fanout_plan_then_deliver_once_is_idempotent_across_reruns", ALWAYS,
     FailureCategory.DUPLICATE_BLOCKED),
    ("AC2", "restored DB / replay",
     "tests/server/test_idempotency_e2e.py",
     "test_send_failure_does_not_release_key_so_retry_is_blocked", ALWAYS,
     FailureCategory.DUPLICATE_BLOCKED),
    ("AC2", "restored DB (real PG partial-unique)",
     "tests/negative/test_messenger_channel_unique.py",
     "test_active_duplicate_chat_thread_blocked_by_partial_unique", PG,
     FailureCategory.DUPLICATE_BLOCKED),
    # 6. double Agent claim — 동시 claim → 정확히 하나만 성공.
    ("AC2", "double Agent claim",
     "tests/server/test_queue_backend.py", "test_exactly_one_claim_in_memory", ALWAYS, None),
    ("AC2", "double Agent claim (real PG SKIP LOCKED)",
     "tests/negative/test_queue_concurrency.py",
     "test_concurrent_claim_exactly_one_wins_skip_locked", PG, None),
    # 7. crash-after-send — send 와 log 기록 사이 crash 후 재시도해도 재전송 0(reserve 가 key 보유).
    ("AC2", "crash-after-send",
     "tests/server/test_idempotency.py", "test_crash_after_send_blocks_resend_on_retry", ALWAYS,
     FailureCategory.DUPLICATE_BLOCKED),
    # (AC2/3) lease 만료·stale 회수·재할당.
    ("AC2/AC3", "lease expiry recover/reclaim",
     "tests/server/test_queue_backend.py", "test_lease_expiry_recover_and_reclaim", ALWAYS, None),
    ("AC2/AC3", "stale owner complete is lease-lost",
     "tests/server/test_queue_backend.py", "test_stale_owner_complete_is_lease_lost", ALWAYS, None),

    # ── AC3: 마이그레이션·운영 안전 검증 + kill switch + pause ─────────────────────
    # 채널 검증 전 활성화 차단(PENDING→VERIFIED→ACTIVE 만 전송 대상).
    ("AC3", "channel verify-before-activate",
     "tests/server/test_channel_lifecycle.py", "test_full_register_verify_activate_flow", ALWAYS, None),
    ("AC3", "unverified channel excluded from dispatch",
     "tests/server/test_channel_lifecycle.py",
     "test_operational_delivery_rules_excludes_unverified_and_composes_with_fanout", ALWAYS, None),
    # atomic settings write(temp→fsync→rename, 실패 시 원본 보존).
    ("AC3", "atomic settings write (replace failure)",
     "tests/test_ui_settings.py",
     "test_save_all_atomic_preserves_original_on_replace_failure", ALWAYS, None),
    ("AC3", "atomic settings write (fsync failure)",
     "tests/test_ui_settings.py",
     "test_save_all_atomic_cleans_temp_and_preserves_original_on_fsync_failure", ALWAYS, None),
    # last_message seed 승계(마지막 전송 해시 이월 → 옛 메시지 재전송 방지). 정본 이름은
    # 스토리 매트릭스의 ``test_migration_multiple_targets_each_get_own_seed`` 가 아니라 아래 둘이다
    # (스토리 표의 이름은 stale — 실재명으로 추적). always-run.
    ("AC3", "last_message seed inheritance",
     "tests/server/test_migration.py", "test_run_migration_copies_state_and_inherits_seed", ALWAYS, None),
    ("AC3", "no-seed is fail-closed (first-send tab)",
     "tests/server/test_migration.py",
     "test_run_migration_active_tab_without_prior_state_has_no_seed", ALWAYS, None),
    # Agent autostart + heartbeat 복구(재부팅 후 자동 시작·heartbeat 재개).
    ("AC3", "agent autostart launch command",
     "tests/agent/test_autostart.py", "test_build_launch_command_dev_uses_module_run", ALWAYS, None),
    ("AC3", "heartbeat survives single failure",
     "tests/agent/test_heartbeat.py", "test_reporter_survives_single_failure_and_continues", ALWAYS, None),
    ("AC3", "heartbeat recovers after revoked",
     "tests/agent/test_heartbeat.py", "test_reporter_recovers_to_valid_after_revoked", ALWAYS, None),
    # scheduler circuit breaker(30%+min_samples/15분).
    ("AC3", "breaker opens above threshold",
     "tests/server/test_scheduler_policy.py",
     "test_breaker_opens_above_threshold_with_enough_samples", ALWAYS, None),
    ("AC3", "breaker min-samples guard",
     "tests/server/test_scheduler_policy.py",
     "test_breaker_min_samples_guard_prevents_small_sample_false_open", ALWAYS, None),
    ("AC3", "breaker threshold strictly > 30%",
     "tests/server/test_scheduler_policy.py",
     "test_breaker_threshold_is_strictly_greater_than_30_percent", ALWAYS, None),
    # tenant pause(SubscriptionGate: SUSPENDED/CANCELLED → 신규 job 차단).
    ("AC3", "tenant pause SUSPENDED blocks",
     "tests/server/test_subscription_gate.py", "test_suspended_blocks_new_jobs", ALWAYS, None),
    ("AC3", "tenant pause CANCELLED blocks",
     "tests/server/test_subscription_gate.py", "test_cancelled_blocks_new_jobs", ALWAYS, None),
    # channel pause(비-ACTIVE 채널 dispatch 제외).
    ("AC3", "channel pause: only ACTIVE operational",
     "tests/server/test_channel_lifecycle.py", "test_is_operational_only_active", ALWAYS, None),
    ("AC3", "channel pause: filter preserves order",
     "tests/server/test_channel_lifecycle.py",
     "test_operational_channels_filters_and_preserves_order", ALWAYS, None),
    # 전역 dispatch kill switch(이 스토리 Task 1.4 신규 — 실 send 0 + 미발송) + 순수 AND 정본.
    ("AC3", "kill switch: service blocks real send",
     "tests/server/test_kill_switch_5_10.py",
     "test_test_send_blocked_when_sending_disabled_calls_send_zero_times", ALWAYS, None),
    ("AC3", "kill switch: route pre-gate blocks seam",
     "tests/server/test_kill_switch_5_10.py",
     "test_route_test_send_blocked_when_sending_disabled_does_not_call_seam", ALWAYS, None),
    ("AC3", "kill switch: pure effective_send_enabled AND",
     "tests/server/test_recovery_non_sending.py",
     "test_effective_send_enabled_is_and_of_both", ALWAYS, None),
)


@lru_cache(maxsize=None)
def _defined_functions(rel_path: str) -> frozenset[str]:
    """주어진 테스트 파일에 정의된 함수명 집합(``ast`` 파싱 — import/실행 부작용 0)."""

    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel_path)
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _matrix_id(entry) -> str:
    ac, scenario, path, test_name, gating, _cat = entry
    return f"{ac}|{scenario}|{test_name}"


# ══════════════════════════════════════════════════════════════════════════
# 추적성: 매트릭스의 각 정본 테스트가 **실제로 정의되어 있다**(완료 위조 차단)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("entry", MATRIX, ids=[_matrix_id(e) for e in MATRIX])
def test_canonical_safety_test_exists(entry) -> None:
    _ac, scenario, rel_path, test_name, _gating, _cat = entry
    path = _REPO_ROOT / rel_path
    assert path.is_file(), f"정본 파일 없음: {rel_path} (시나리오 {scenario})"
    defined = _defined_functions(rel_path)
    assert test_name in defined, (
        f"정본 테스트 '{test_name}' 가 {rel_path} 에 없음 — AC 추적성 깨짐(시나리오 {scenario}). "
        f"이름이 바뀌었으면 매트릭스를 갱신하라(완료 위조 차단)."
    )


# ══════════════════════════════════════════════════════════════════════════
# 커버리지 완전성: AC2 7 시나리오·AC3 항목이 모두 매트릭스에 등록되어 있다
# ══════════════════════════════════════════════════════════════════════════

def test_ac2_seven_scenarios_all_present() -> None:
    # AC2 의 7개 핵심 시나리오 라벨이 모두 매트릭스에 등장(시나리오 누락=추적성 공백).
    required = {
        "wrong tenant",
        "wrong profile",
        "wrong Kakao room",
        "stale Agent token",
        "restored DB / replay",
        "double Agent claim",
        "crash-after-send",
    }
    present = {scenario for ac, scenario, *_ in MATRIX if ac.startswith("AC2")}
    # 라벨 변형(접미사 포함)도 시작 매칭으로 흡수.
    missing = {r for r in required if not any(p.startswith(r) for p in present)}
    assert not missing, f"AC2 매트릭스 누락 시나리오: {missing}"


def test_ac3_verification_items_all_present() -> None:
    # AC3 검증 항목(채널 검증/atomic write/seed/heartbeat/breaker/tenant·channel pause/kill switch).
    required_keywords = (
        "channel verify",
        "atomic settings write",
        "seed",
        "autostart",
        "heartbeat",
        "breaker",
        "tenant pause",
        "channel pause",
        "kill switch",
    )
    ac3_scenarios = [scenario for ac, scenario, *_ in MATRIX if ac.startswith("AC3")]
    for kw in required_keywords:
        assert any(kw in s for s in ac3_scenarios), f"AC3 매트릭스 누락 항목: {kw}"


# ══════════════════════════════════════════════════════════════════════════
# 의미 잠금(always-run): 매트릭스가 가리키는 정본 FailureCategory/어휘가 실재한다
# ══════════════════════════════════════════════════════════════════════════

def test_referenced_failure_categories_are_canonical_members() -> None:
    # 매트릭스가 인용한 FailureCategory(TARGET_VALIDATION_FAILURE/KAKAO_FAILURE/DUPLICATE_BLOCKED)가
    # 정본 7 멤버에 실재함을 always-run 으로 잠근다(enum 멤버 수 불변 — 14표·count-lock).
    referenced = {cat for *_rest, cat in MATRIX if cat is not None}
    assert referenced  # 최소 한 개는 error_code 분류로 잠긴다.
    for cat in referenced:
        assert isinstance(cat, FailureCategory)
        assert FailureCategory[cat.name] is cat


def test_kill_switch_meaning_is_locked_always_run() -> None:
    # AC3 전역 kill switch 의미를 PG 부재에서도 잠근다: 실전송 = send_enabled AND sending_enabled.
    assert effective_send_enabled(send_enabled=True, sending_enabled=True) is True
    assert effective_send_enabled(send_enabled=True, sending_enabled=False) is False
    assert effective_send_enabled(send_enabled=False, sending_enabled=True) is False
    # 차단된 test send 결과는 미발송 어휘(HELD, sent_at=None)로 관측된다(idempotency.py 무회귀).
    assert DeliveryStatus.HELD.value == "HELD"
