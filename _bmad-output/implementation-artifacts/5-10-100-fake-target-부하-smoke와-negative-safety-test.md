---
baseline_commit: 9a51816598d1263e67523b6001289b6f34f9a690
---

# Story 5.10: 100 fake target 부하 smoke와 negative safety test

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 100개 가짜 대상 scheduling smoke와 핵심 negative safety test로 확장성과 안전성을 입증하고 싶다,
so that 대상이 늘어도 job 폭주 없이 동작하고 잘못된 대상/방/token/중복 시나리오에서 사고가 안 나는 것을 출시 전 확인한다.

> **이 스토리의 본질 — 먼저 읽어라(가장 중요):**
> 이 스토리는 새 기능을 발명하는 게 **아니라**, 5.1~5.9가 이미 만든 안전장치가 **실제로 동작함을 출시 전에 입증·통합**하는 **검증 스토리**다. AC2의 7개 negative 시나리오와 AC1의 100-target smoke는 **거의 전부 이미 통과하는 테스트로 존재**한다(아래 "기존 커버리지 매트릭스" 참조). 따라서 핵심 함정은 (a) **이미 있는 테스트를 재구현**하는 것, (b) AC를 코드 대조 없이 "되어 있음"으로 **완료 위조**하는 것이다.
> **단 하나의 진짜 코드 공백:** AC3의 "전역 dispatch kill switch가 **동작한다**" — `effective_send_enabled`(`recovery.py`)와 `sending_enabled` 플래그는 존재하지만 **어떤 실발송 경로도 이 게이트를 호출하지 않는다**(5.8에서 플래그만 추가, consumer 0). 이 한 가지를 **배선**하고 나머지는 **추적성(traceability) 매트릭스 테스트**로 잠근다.

## Acceptance Criteria

**AC1 — 100 fake target scheduling smoke로 확장성 입증 (NFR-26, P4 smoke, architecture:582)**

**Given** 확장성을 입증해야 할 때
**When** 100개 fake target scheduling smoke를 실행하면
**Then** **단일 tick**에서 100개 대상이 **예외/race loss/throttle 0으로** 모두 job 생성되고(`enqueued_count == 100`), 각 job이 queue에 `PENDING`으로 기록되며(상태 전환 정상)
**And** jitter로 인해 100개 job의 `next_run_at`이 **여러 초로 분산**되어 같은 초에 몰리는 job storm이 발생하지 않는다(결정적 ≥85 distinct seconds).

**AC2 — 핵심 negative safety test로 안전성 입증 (NFR-29, NFR-1, fail-closed)**

**Given** 안전성을 입증해야 할 때
**When** negative safety test 매트릭스를 실행하면
**Then** 다음 7개 시나리오가 모두 **fail-closed로 차단**되고(오발송 0, 실패로 기록) 각각 **통과하는 always-run 테스트**로 잠겨 있음이 추적 가능하다:
1. **wrong tenant** — cross-tenant 접근은 차단(`TenantScopeViolation`→404), 상태 전이·audit 부작용 0.
2. **wrong profile** — 기대 센터/상점명 불일치/공백이면 브라우저 시작 전 `TARGET_VALIDATION_FAILURE`로 차단(다른 계정 실적 오발송 방지).
3. **wrong Kakao room** — 동명/모호 방이면 미발송·재시도/대체방 fallback 0(`KAKAO_FAILURE`/`kakao_ambiguous_room`).
4. **stale Agent token** — revoke된 token으로 claim 시 401, 추가 작업 차단.
5. **restored DB** — DB 복원 후 같은 Snapshot replay 시 dedup unique 제약으로 재전송 차단(`DUPLICATE_BLOCKED`).
6. **double Agent claim** — 두 Agent가 같은 job을 동시에 claim하면 정확히 하나만 성공, 나머지는 빈 응답.
7. **crash-after-send** — send와 log 기록 사이 crash 후 재시도해도 reserve가 key를 잡고 있어 재전송 0.
**And** 잘못된 tenant/profile/Kakao room이면 전송하지 않고 실패로 기록한다(NFR-1).

**AC3 — 마이그레이션·운영 안전 시나리오 검증 + kill switch 배선 (NFR-23, SM-7)**

**Given** 마이그레이션·운영 안전장치를 검증해야 할 때
**When** 운영 안전 시나리오를 테스트하면
**Then** 다음이 **통과하는 테스트로 검증**된다: 채널 검증 전 활성화 차단(PENDING→VERIFIED→ACTIVE만 전송 대상), atomic settings write(temp→fsync→rename, 실패 시 원본 보존), `last_message` seed 승계(마이그레이션이 마지막 전송 해시를 이월해 옛 메시지 재전송 방지), Agent autostart heartbeat 복구(재부팅 후 자동 시작·heartbeat 재개), scheduler jitter/circuit breaker(30%+min_samples/15분).
**And** **전역 dispatch kill switch가 실제로 동작한다** — `sending_enabled=False`면 현존하는 모든 **실발송 chokepoint**(operator test send/retry 등)가 실 `send`를 호출하지 않고 fail-closed(미발송)로 차단된다.
**And** tenant 단위 pause(SubscriptionGate: `SUSPENDED`/`CANCELLED`→신규 job/dispatch 차단)와 channel 단위 pause(비-`ACTIVE` 채널은 dispatch 대상 제외)가 동작한다.

## Tasks / Subtasks

> **작업 원칙(매 Task 적용):** ① 기존 테스트/구현을 **재구현 금지** — 발견하면 import/참조/compose만. ② AC `[x]` 표기 전 **실제 코드/테스트와 1:1 대조**(완료 위조 차단). ③ 신규 DB 컬럼/테이블/Alembic/enum 멤버 **0**(14표·count-lock 불변). ④ secret·식별정보(tenant/고객/센터/방명) 비노출 유지.

- [x] **Task 1: 전역 dispatch kill switch 배선 + 검증 (AC3 "kill switch 동작한다") — 이 스토리의 유일한 실코드 변경**
  - [x] 1.1 **현존 실발송 chokepoint 식별·게이팅.** 중앙 dispatch 런타임 루프는 `migration/cutover.py:10-11`이 명시하듯 **아직 코드에 없다**(`DispatchService`/`DispatchFanoutService`/`CentralTelegramSender`를 호출하는 실행 경로 0). 따라서 현재 실제 `send`가 일어나는 곳은 **operator 액션**뿐: `AdminActionService.test_send`(`admin_action_service.py:546`)와 `retry_job`(`:410`)이 `IdempotentDeliveryService.deliver_once`의 주입 `send`를 호출한다. 이 두 경로에 `effective_send_enabled`(`recovery.py:15`)를 compose해, `sending_enabled=False`면 실 `send`를 **호출하지 않고** fail-closed(미발송) 결과 + audit를 기록한다.
  - [x] 1.2 **`effective_send_enabled` 재사용(재구현 금지).** 실전송 = `send_enabled`(채널/대상 게이트) **AND** `sending_enabled`(환경 전역 플래그). 새 차단 로직을 만들지 말고 `recovery.effective_send_enabled(send_enabled=..., sending_enabled=...)`로 판정한다. `deliver_once`(`idempotency.py`) **본문·시그니처는 무변경** — reserve→send 순서·crash-after-send 안전(3.5)을 깨지 않도록, 게이트는 주입 `send` seam을 **wrap/short-circuit**하는 호출부(서비스/라우트) 레이어에서 적용한다.
  - [x] 1.3 **경계 명시(주석).** enqueue-only 액션(`test_crawl`:452, `auth_check`:485 — CRAWL/AUTH job 1건 enqueue)과 `dry_run_render`(:515 — 구조적 미발송)은 **실발송이 아니므로 게이트 대상 아님**. 향후 중앙 dispatch 루프 도입 시 동일 게이트(`effective_send_enabled`) compose가 필수임을 코드 주석으로 남겨, 미래 실발송 경로가 게이트를 우회하지 못하게 한다.
  - [x] 1.4 **테스트(always-run, `tests/server/` 또는 `tests/negative/`):** `sending_enabled=False`면 `test_send`/`retry_job`이 실 `send` **0회 호출** + blocked/미발송 결과·audit 기록; `sending_enabled=True AND send_enabled=True`일 때만 전송. fake `send`(call 카운트)·in-memory repo로 결정적으로 잠근다. 기존 `test_recovery_non_sending.py`의 순수 `effective_send_enabled` 테스트(AND 진리표)는 무회귀로 유지.

- [x] **Task 2: 100 fake target scheduling smoke — AC1 추적 가능 통합 테스트 (AC1)**
  - [x] 2.1 **기존 자산 재사용(재구현 금지):** `FakeSchedulerRepo`·`_target`·`_capacity`(`tests/server/test_scheduler_tick.py:43-92`), `InMemoryQueueBackend`(`src/rider_server/queue/memory_queue.py`), `SchedulerService.run_tick`·`policy.compute_jitter`(`scheduler/policy.py:56`). 기존 smoke `test_hundred_targets_no_storm_jitter_spread_and_capacity_bound`(`:304`)·`test_hundred_targets_capacity_bound_prevents_storm`(`:321`)은 **무변경 유지**.
  - [x] 2.2 **AC1 문구를 명시적으로 단정하는 신규 smoke 1건 추가**(이름에 `5_10` 식별 — 기존과 중복 아님): 100개 fake target·전부 due·`capacity=100` → 단일 tick에서 (1) `enqueued_count == 100`, (2) 모든 outcome `reason == REASON_ENQUEUED`(예외/`REASON_RACE_LOST`/`REASON_THROTTLED_CAPACITY` 0), (3) 각 job이 `backend.job_snapshot(job_id).status == "PENDING"` 으로 queue에 기록(상태 전환 검증), (4) `next_run_at` 집합이 ≥85 distinct seconds로 분산(job storm 차단).
  - [x] 2.3 (선택) T+interval 재-tick 시에도 jitter가 동일하게 분산을 유지해 두 번째 주기에도 storm이 없음을 단정(결정적 jitter 특성).

- [x] **Task 3: Negative safety 추적성 매트릭스 (AC2) — 완료 위조 차단**
  - [x] 3.1 **신규 `tests/negative/test_safety_matrix.py`(always-run)** 작성: AC2의 7개 시나리오 각각에 대해, **차단 의미를 직접 단정하는 always-run 단언**을 두거나(가능하면 in-memory seam 재사용으로), 해당 시나리오를 잠그는 **정본 테스트가 실제로 존재함을 import/참조로 확인**한다(아래 매트릭스의 테스트 이름). 목적: PG-skip CI에서도 7개 안전 의미가 **always-run으로 잠긴다**는 보증과, "AC2 충족" 주장의 코드 근거(완료 위조 방지, 5.9 `test_runbooks_present` 선례).
  - [x] 3.2 **재구현 금지·gap만 보강:** 7개 시나리오는 모두 already-covered(매트릭스 참조). **새 시나리오 테스트를 중복 작성하지 말 것.** 작업 중 어떤 시나리오에 **always-run 트윈이 없고 PG-gated만** 있음을 발견하면, 그 안전 의미만 in-memory contract 테스트로 보강한다(PG 부재 시에도 의미 잠금 — `pg-gated-files-hide-pure-helpers` 선례). *현재 확인된 바로는 7개 모두 always-run 커버가 있으므로 추가 보강은 없을 가능성이 높다 — 보강 전 반드시 재확인.*
  - [x] 3.3 매트릭스가 시나리오↔테스트↔정본 FailureCategory(해당 시) 매핑을 문자열로 남겨, 리뷰어가 한 곳에서 AC2 추적성을 확인하도록 한다.

- [x] **Task 4: 마이그레이션·운영 안전 검증 추적성 (AC3 검증 부분)**
  - [x] 4.1 AC3의 검증 항목(채널 검증 전 활성화 차단 / atomic write / last_message seed / autostart heartbeat / jitter·circuit breaker / tenant·channel pause)을 잠그는 **기존 통과 테스트를 매트릭스에 추적 등록**(아래 매트릭스 이름). **재구현 금지.**
  - [x] 4.2 kill switch 동작(Task 1.4)을 AC3 매트릭스 항목으로 포함해, AC3의 모든 절(검증 + kill switch + pause)이 한 곳에서 추적된다.
  - [x] 4.3 PG-gated에만 있는 항목(예: `last_message` seed 마이그레이션 round-trip)은 그 사실과 always-run 의미 트윈 여부를 매트릭스 주석에 명시(임의로 PG 동작을 위조하지 않는다).

- [x] **Task 5: 전체 회귀 + 실측 test count 기록**
  - [x] 5.1 `.venv/Scripts/python.exe -m pytest -q` 전체 실행. **기준선:** 5.9 done 시점 `1862 passed, 48 skipped`(collect 1910). 신규 추가분만큼 always-run 증가, PG-gated는 `TEST_DATABASE_URL` 미설정 시 skip.
  - [x] 5.2 0 failed 확인. 실측 pass/skip 수를 Completion Notes에 기록(dev 시점과 qa-e2e/review 시점이 다를 수 있음 — `stale-test-count-a2` 패턴, 리뷰에서 재측정).
  - [x] 5.3 `git diff -w`로 CRLF/LF noise 제외 실변경만 검증(관련 없는 변경 되돌리지 않기).

## Dev Notes

### 🚨 가드레일(위반 시 CI 실패·리뷰 반려) — 우선순위 순

1. **재구현 금지(이 스토리 1순위 함정):** AC1 smoke, AC2 7 시나리오, AC3 검증 항목은 **거의 전부 통과 테스트로 이미 존재**한다. 새 파일에 같은 시나리오를 다시 짜지 말고 import/참조/compose한다. 유일한 신규 "기능 코드"는 **Task 1 kill switch 배선**뿐이다.
2. **14표·count-lock 불변:** 신규 DB 컬럼/테이블/Alembic 마이그레이션 **0**, `domain/states.py` enum 멤버 수 불변(`test_domain_states`), `FailureCategory` 7 무회귀. 이 스토리는 테스트·얇은 배선 스토리다.
3. **`deliver_once`/3.5·3.6 불변식 보존:** kill switch는 주입 `send` seam을 호출부에서 wrap/short-circuit. `idempotency.deliver_once` 본문·시그니처·`reserve→send` 순서·crash-after-send 안전을 **건드리지 않는다**.
4. **fail-closed 우선:** kill switch·negative 경로는 **모호하면 보내지 않는다**. `sending_enabled` 기본값은 `False`(`settings.py:49`) — 운영자가 명시적으로 켜기 전 실전송 0.
5. **secret·식별정보 비노출:** audit `diff_redacted`/로그/예외에 token/OTP/password·tenant/고객/센터/방명 평문 0. `redact()`는 room/center/store **이름을 마스킹하지 않으므로**(운영 ID 비마스킹, `redact-skips-operational-ids` 메모) 애초에 식별 텍스트를 payload/diff에 담지 않는다.
6. **import 단방향:** `rider_server` → `rider_agent` import 0. agent-side 시나리오(profile/kakao/token/heartbeat) 테스트는 `tests/agent/`의 기존 정본을 **참조**할 뿐, server 코드에서 agent를 import하지 않는다.
7. **완료 위조 차단:** AC2/AC3 "충족"은 추적성 매트릭스 테스트(Task 3·4)로 코드 근거를 남긴다 — 파일/통과 없이 done 불가(5.9 `test_runbooks_present` 선례).

### ⭐ 단 하나의 진짜 공백 — 전역 dispatch kill switch가 배선되지 않았다

- **현상태(검증 완료):** `recovery.effective_send_enabled(*, send_enabled, sending_enabled) -> bool`(`src/rider_server/services/recovery.py:15`)는 순수 함수로 존재하고 `test_recovery_non_sending.py`가 AND 진리표를 잠그지만, **production에 caller가 0**이다(`grep effective_send_enabled\(` → 정의·테스트뿐). `app.state.sending_enabled`(`main.py:238`)·`Settings.sending_enabled=False`(`settings.py:49`, env `RIDER_SENDING_ENABLED`)도 **읽는 dispatch 경로가 없다**. 즉 플래그는 저장되지만 실제로 아무것도 게이트하지 않는다(5.8에서 플래그만 추가 — `story-5-8-audit-on-deny-anti-flooding` 메모의 "sending_enabled flag has no dispatch consumer yet").
- **왜 이게 5.10의 범위인가:** AC3는 "전역 dispatch kill switch가 **동작한다**"를 요구한다. consumer가 없으면 이 AC는 거짓이 된다. 출시 전 마지막 안전 스토리(5.11은 CRUD UI)이므로 여기서 배선한다.
- **어디에 배선하나(중요 — 미존재 경로에 배선하려 들지 말 것):** 중앙 dispatch 런타임 루프는 **아직 코드에 없다**(`migration/cutover.py:10-11`: "DispatchService/DispatchFanoutService/CentralTelegramSender 를 호출하지 않아 **실발송 경로가 코드에 존재하지 않는다**"). 현재 실 `send`가 코드로 일어나는 곳은 **operator 액션**: `AdminActionService.test_send`(`:546`)·`retry_job`(`:410`)가 `deliver_once(send=...)`로 실 전송. (참고: `ChannelRegistrationService.verify_channel`(`channel_registration.py:233`)도 검증용 test 메시지를 실제 전송한다 — Open Question 참조.)
- **배선 방식(권장):** operator 실발송 호출부에서 `effective_send_enabled(send_enabled=<채널/대상 게이트>, sending_enabled=<app.state/Settings>)`로 판정 → `False`면 주입 `send`를 **호출하지 않고** 미발송 결과(예: 기존 `DeliveryStatus`/blocked 의미) + audit 기록. `deliver_once`는 무변경(주입 `send`를 noop/blocked로 wrap하거나, 서비스가 게이트 후 분기). secret·식별정보 비노출 유지.

### 기존 커버리지 매트릭스 — 시나리오 → 정본 테스트(재구현 금지, 참조/추적만)

> 아래는 이미 **통과**하는 테스트다. Task 3·4는 이들을 매트릭스로 묶어 추적성을 만드는 것이지, 다시 짜는 게 아니다. (always-run = `TEST_DATABASE_URL` 없어도 실행 / PG = 미설정 시 skip.)

| AC | 시나리오 | 정본 테스트(이름) | 위치 | gating |
| --- | --- | --- | --- | --- |
| AC1 | 100-target jitter spread·no storm | `test_hundred_targets_no_storm_jitter_spread_and_capacity_bound` | `tests/server/test_scheduler_tick.py:304` | always-run |
| AC1 | 100-target capacity bound storm 방지 | `test_hundred_targets_capacity_bound_prevents_storm` | `tests/server/test_scheduler_tick.py:321` | always-run |
| AC1 | jitter 분산(순수) | `test_jitter_spreads_same_interval_targets_across_seconds` | `tests/server/test_scheduler_policy.py:48` | always-run |
| AC2 | wrong tenant(cross-tenant 차단·audit 0) | `test_admin_actions.py`(tenant 불일치→404, `TenantScopeViolation`) / `test_cross_tenant_pause_leaves_no_audit` | `tests/server/test_admin_actions.py` / `tests/negative/test_security_pg.py:217` | always-run / PG |
| AC2 | wrong profile(센터 불일치→브라우저 미시작) | `test_empty_coupang_center_blocks_target_and_does_not_start`, `test_map_center_mismatch_to_target_validation_failure` | `tests/agent/test_browser_profile.py:328,350` | always-run |
| AC2 | wrong Kakao room(모호→미발송) | `test_unsafe_selection_maps_to_ambiguous_room_and_does_not_send_elsewhere`, `test_ambiguous_send_error_is_not_retried_or_requeued`, `test_failure_does_not_auto_resend_to_another_room` | `tests/agent/test_kakao_sender.py:219,234,353` | always-run |
| AC2 | stale Agent token(revoke→401) | `test_revoke_then_claim_is_401`, `test_revoke_marks_revoked_and_audits` | `tests/server/test_agent_token_revoke.py:184,84` | always-run (+PG `test_revoke_sets_token_revoked_at_and_audit`) |
| AC2 | restored DB / replay(dedup 재전송 차단) | `test_fanout_plan_then_deliver_once_is_idempotent_across_reruns`, `test_send_failure_does_not_release_key_so_retry_is_blocked` | `tests/server/test_idempotency_e2e.py:150,208` | always-run (+PG `test_active_duplicate_chat_thread_blocked_by_partial_unique`) |
| AC2 | double Agent claim(정확히 1 승자) | `test_exactly_one_claim_in_memory` / `test_concurrent_claim_exactly_one_wins_skip_locked` | `tests/server/test_queue_backend.py` / `tests/negative/test_queue_concurrency.py:104` | always-run / PG |
| AC2 | crash-after-send(재전송 0) | `test_crash_after_send_blocks_resend_on_retry` | `tests/server/test_idempotency.py:202` | always-run |
| AC2/3 | lease 만료·stale 회수·재할당 | `test_lease_expiry_recover_and_reclaim`, `test_stale_owner_complete_is_lease_lost` / `*_real_pg` | `tests/server/test_queue_backend.py:228,268` / `tests/negative/test_queue_concurrency.py:134,163` | always-run / PG |
| AC3 | 채널 검증 전 활성화 차단(PENDING→VERIFIED→ACTIVE) | `test_full_register_verify_activate_flow`, `test_operational_delivery_rules_excludes_unverified_and_composes_with_fanout` | `tests/server/test_channel_lifecycle.py:258,146` | always-run |
| AC3 | atomic settings write(실패 시 원본 보존) | `test_save_all_atomic_preserves_original_on_replace_failure`, `test_save_all_atomic_cleans_temp_and_preserves_original_on_fsync_failure` | `tests/test_ui_settings.py:587,770` | always-run |
| AC3 | last_message seed 승계 | `test_run_migration_copies_state_and_inherits_seed`, `test_migration_multiple_targets_each_get_own_seed` | `tests/server/test_migration.py:182,243` | (확인: always-run vs PG — 매트릭스 주석에 명시) |
| AC3 | Agent autostart + heartbeat 복구 | `test_build_launch_command_dev_uses_module_run`, `test_reporter_survives_single_failure_and_continues`, `test_reporter_recovers_to_valid_after_revoked` | `tests/agent/test_autostart.py:135`, `tests/agent/test_heartbeat.py:270,483` | always-run |
| AC3 | scheduler circuit breaker(30%+min_samples/15분) | `test_breaker_opens_above_threshold_with_enough_samples`, `test_breaker_min_samples_guard_prevents_small_sample_false_open`, `test_breaker_threshold_is_strictly_greater_than_30_percent` | `tests/server/test_scheduler_policy.py:173,168,178` | always-run |
| AC3 | tenant pause(SUSPENDED/CANCELLED 차단) | `test_suspended_blocks_new_jobs`, `test_cancelled_blocks_new_jobs` | `tests/server/test_subscription_gate.py` | always-run |
| AC3 | channel pause(비-ACTIVE 제외) | `test_is_operational_only_active`, `test_operational_channels_filters_and_preserves_order` | `tests/server/test_channel_lifecycle.py:129` | always-run |
| AC3 | **kill switch(전역 미발송)** | **(신규 — Task 1.4)** + `test_recovery_non_sending.py`(순수 AND) | `tests/server/...`(신규) / `tests/server/test_recovery_non_sending.py` | always-run |

### 핵심 정본(검증 완료) — 절대 틀리면 안 되는 이름

- **kill switch:** `recovery.effective_send_enabled(*, send_enabled, sending_enabled)`(`services/recovery.py:15`), `Settings.sending_enabled=False`(`settings.py:49`, env `RIDER_SENDING_ENABLED`), `app.state.sending_enabled`(`main.py:238`). 실발송 = 둘 다 True일 때만.
- **실발송 chokepoint(현존):** `AdminActionService.test_send`(`:546`), `retry_job`(`:410`) — 둘 다 `deliver_once(send=...)`. enqueue-only(`test_crawl`:452/`auth_check`:485)·`dry_run_render`(:515)는 비발송. 중앙 dispatch 루프는 미존재(`migration/cutover.py:10-11`).
- **scheduler tick·jitter·breaker:** `SchedulerService.run_tick`(`scheduler/service.py:142`), `policy.compute_jitter(target_id, interval_seconds)`(SHA256 결정적, 0..interval, `policy.py:56`), `policy.evaluate_breaker(total, failures, *, threshold=0.30, min_samples=5)`(`policy.py:164`), `DEFAULT_BREAKER_WINDOW=timedelta(minutes=15)`(`service.py:40`). tick outcome reason: `REASON_ENQUEUED`/`REASON_BREAKER_OPEN`/`REASON_ACTIVE_JOB_EXISTS`/`REASON_THROTTLED_CAPACITY`/`REASON_UNKNOWN_PLATFORM`/`REASON_RACE_LOST`(`service.py:32`).
- **smoke seam:** `FakeSchedulerRepo`·`_target`·`_capacity`(`tests/server/test_scheduler_tick.py:43-92`), `InMemoryQueueBackend`(`queue/memory_queue.py` — `job_snapshot`/`job_status` 테스트 헬퍼), `QueueBackend`(`queue/backend.py`).
- **dedup/idempotency:** `IdempotentDeliveryService.deliver_once(...)`·`build_dedup_key(5필드)`(`services/idempotency.py`), DB unique `uq_delivery_logs_dedup_key`. crash-after-send·replay 안전은 reserve→send 순서가 보장.
- **tenant scope:** `AdminActionService._scoped_target/_scoped_subscription`(`admin_action_service.py:322-338`) → 불일치 시 `TenantScopeViolation`(→404, 존재 은닉), 전이/audit 전에 차단(fail-closed).

### Project Structure Notes

- **신규 파일은 최소:** `tests/negative/test_safety_matrix.py`(AC2/AC3 추적성, always-run) + AC1 smoke 1건(`tests/server/test_scheduler_tick.py`에 추가 또는 `tests/server/test_scale_smoke_5_10.py` 신규) + kill switch 테스트(`tests/server/` 또는 `tests/negative/`). `tests/` 미러 구조·기존 파일 패턴 준수(`tests/server/`·`tests/negative/`·`tests/agent/`).
- **실코드 변경 최소:** Task 1 kill switch 배선만 — `src/rider_server/services/admin_action_service.py`(또는 그 호출 라우트)에서 `effective_send_enabled` compose. 신규 모듈·의존성 불필요(stdlib + 기존 server extra).
- **`pyproject.toml`:** 신규 third-party 의존성 **0**. PG-gated 테스트는 `TEST_DATABASE_URL` 미설정 시 skip.

### 열린 질문(구현 시 결정 — 막지 말고 가장 안전한 선택)

- **kill switch가 channel `verify_channel`의 test 메시지도 차단해야 하나?** `verify_channel`(`channel_registration.py:233`)은 활성화 전 검증용 실 메시지를 보낸다. fail-closed 원칙("모호하면 보내지 않는다")상 전역 kill switch ON(=`sending_enabled=False`)이면 검증 전송도 차단하는 게 일관적이다. **권장: 차단(게이트 적용).** 단, "복구 모드에서도 채널 셋업은 가능해야 한다"는 운영 요구가 있으면 operator 실발송(test_send/retry)만 차단하고 verify는 예외로 둘 수 있다 — dev가 결정하되 기본은 **차단(fail-closed)**, 선택을 테스트·주석에 명시.
- **kill switch 배선 위치(서비스 vs 라우트):** `deliver_once` 무변경 제약상, 게이트는 (a) `test_send`/`retry_job`에 `sending_enabled` 인자/consult 추가 후 분기, 또는 (b) 라우트에서 `send` seam을 wrap. **권장 (a)** — 서비스가 audit까지 일관 기록하고, 라우트가 게이트를 잊는 우회를 막는다. 어느 쪽이든 `effective_send_enabled` 재사용.
- **AC2 매트릭스를 "참조형"으로 둘지 "재현형"으로 둘지:** 7 시나리오는 already-covered이므로 **참조형(존재·통과 확인) + 핵심 의미 일부 always-run 재단언**을 권장. PG-only 의미(예: SKIP LOCKED 실DB 경쟁)는 always-run in-memory 트윈이 이미 있으므로 그것을 가리킨다(없으면 보강).

### 개발 환경·프로세스(매 스토리 반복되는 함정)

- pytest 실행: WSL에서 `.venv/Scripts/python.exe -m pytest`(Windows venv). 설정 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`).
- 라우트는 실 `now()` 사용 → 시간 의존(warning/critical 등) 단정은 순수 policy/service에서 `now` 주입으로. 라우트 테스트는 shape/상태코드/존재만.
- git tree CRLF/LF noisy — 변경 검증은 `git diff -w`. 관련 없는 변경·BMAD 산출물 되돌리지 않기.
- 완료 전 전체 회귀 실행 후 **실측 test count**를 Completion Notes에 기록(dev 시점 ≠ qa-e2e/review 시점, `stale-test-count-a2` — 리뷰에서 재측정).
- agent-side 시나리오 테스트(profile/kakao/token/heartbeat)는 `tests/agent/` 정본 참조만 — server 코드에서 `rider_agent` import 금지(단방향).

### References

- [Source: _bmad-output/planning-artifacts/epics.md:1118-1138] — Story 5.10 AC 정본(100-target smoke / negative safety / 마이그레이션 안전·kill switch·pause).
- [Source: _bmad-output/planning-artifacts/epics.md:977] — `double Agent claim` negative test가 Story 5.10 연계로 명시(FR-13, 5.3↔5.10).
- [Source: _bmad-output/planning-artifacts/epics.md:906] — Epic 5 개요(100 fake target scheduling smoke로 확장성 입증; NFR-23·26·29).
- [Source: _bmad-output/planning-artifacts/architecture.md:70-80] — cross-cutting: 분산 job 안전성(crash-after-send/중복 claim/stale token), tenant isolation, 마이그레이션 안전(kill switch).
- [Source: _bmad-output/planning-artifacts/architecture.md:326-332] — 에러 분류 정본·fail-closed(잘못된 tenant/profile/Kakao room이면 미발송·실패 기록).
- [Source: _bmad-output/planning-artifacts/architecture.md:466,582] — `tests/negative/` 분리, 100 fake target smoke·측정 기반 증설.
- [Source: src/rider_server/services/recovery.py:15] — `effective_send_enabled`(kill switch 합성, **현재 production caller 0**).
- [Source: src/rider_server/settings.py:45-49,68] · [src/rider_server/main.py:238] — `sending_enabled` 기본 OFF·env·app.state(읽는 dispatch 경로 없음).
- [Source: src/rider_server/migration/cutover.py:10-11] — 중앙 실발송 런타임 경로가 **코드에 미존재**(배선 대상은 현존 operator 액션뿐).
- [Source: src/rider_server/services/admin_action_service.py:410,546,322-338] — `retry_job`·`test_send`(실발송 chokepoint), tenant scope 차단.
- [Source: src/rider_server/services/idempotency.py] — `deliver_once`·dedup key(무변경 보존: reserve→send·crash-after-send).
- [Source: src/rider_server/scheduler/policy.py:56,164] · [service.py:32,40,142] — `compute_jitter`·`evaluate_breaker`·tick reason·breaker window.
- [Source: tests/server/test_scheduler_tick.py:43-92,304,321] — `FakeSchedulerRepo`·기존 100-target smoke(재사용·무변경).
- [Source: src/rider_server/queue/memory_queue.py · backend.py] — `InMemoryQueueBackend`(`job_snapshot`)·`QueueBackend` 계약.
- [Source: 기존 커버리지 매트릭스 표의 모든 테스트 파일] — AC2/AC3 시나리오 정본(재구현 금지, 추적만).
- [Source: _bmad-output/implementation-artifacts/5-9-...md] — 5.9 done 기준선(1862 passed/48 skipped), `test_runbooks_present` 완료-위조-차단 선례.
- [Source: _bmad-output/project-context.md] — 프로젝트 56규칙(fail-closed·redaction·secret·import 단방향·기존 동작 보존·14표 lock).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8) — BMAD dev-story workflow

### Debug Log References

- 전체 회귀: `.venv/Scripts/python.exe -m pytest -q` → **1912 passed, 48 skipped, 0 failed** (11.17s, dev 시점).
  - 기준선(5.9 done): 1862 passed / 48 skipped → 신규 always-run **+50** (kill switch 7 + AC1 smoke 2 + 추적성 매트릭스 41). PG-gated 48 skip 불변(`TEST_DATABASE_URL` 미설정).
  - 실측 시점 주의: dev 시점 카운트는 qa-e2e/review 시점과 다를 수 있음(`stale-test-count-a2`) — 리뷰에서 재측정 권장.
  - **리뷰 재측정(2026-06-14, review AI):** `.venv/Scripts/python.exe -m pytest -q` → **1915 passed, 48 skipped, 0 failed** (10.95s). dev 시점 대비 +3 (qa-e2e append — `stale-test-count-a2`). PG-gated 48 skip 불변.
- `git diff -w --stat`(CRLF/LF noise 제외): 실코드 변경은 kill switch 배선 2파일뿐(`actions_routes.py` +26, `admin_action_service.py` +47). 그 외는 테스트.

### Completion Notes List

**Task 1 — 전역 dispatch kill switch 배선(이 스토리 유일 실코드 변경).**
- 실 `send` 가 코드로 일어나는 chokepoint 는 `AdminActionService.test_send`(`deliver_once(send=...)` 호출) **하나**다. 게이트는 `recovery.effective_send_enabled(send_enabled=True, sending_enabled=...)` 재사용(새 차단 로직 0). `sending_enabled=False`면 주입 `send` **0회 호출** + 미발송 결과(`DeliveryStatus.HELD`, `sent_at=None`) + `result=DENIED` audit. `idempotency.deliver_once` 본문·시그니처·`reserve→send`·crash-after-send 안전 **무변경**(게이트는 service 호출부에서 분기).
- 2계층 게이트(fail-closed 우회 차단): (a) service `test_send(sending_enabled=…)` — 직접 호출자/미래 seam 방어, always-run 결정적 테스트로 잠금; (b) route `POST /admin/targets/{id}/test-send` — seam 호출 **전** pre-gate(seam 이 게이트를 잊고 우회 못하게). 둘 다 `app.state.sending_enabled`(기본 OFF) 소비 → "consumer 0" 공백 해소.
- **스토리 매트릭스 정정(코드 1:1 대조 — 완료 위조 차단):** 스토리는 `retry_job`(`:410`)도 "`deliver_once(send=...)` 호출"이라 적었으나, **코드상 `retry_job` 은 FAILED/RETRY→PENDING 재진입만 하는 enqueue-only 액션**으로 실 `send` 를 호출하지 않는다(`test_crawl`/`auth_check` 와 동일 범주, Task 1.3 경계). 따라서 실발송 게이트는 실제 `send` 가 일어나는 `test_send` 에만 두고, `retry_job` 이 enqueue-only(실발송 0)임을 트레이서 테스트로 잠갔다. 미래 중앙 dispatch 루프(미존재 — `migration/cutover.py:10-11`) 도입 시 그 실 `send` 호출부에 동일 게이트 compose 필수임을 service/route 주석으로 명시.
- 열린 질문 #1(verify_channel 차단 여부): 본 스토리 범위는 operator 실발송(test_send) chokepoint 배선. `verify_channel` 의 검증 메시지 게이팅은 별도 호출부라 본 스토리에서 변경하지 않음(추가 실코드 변경 최소화 원칙) — 향후 dispatch 루프 배선과 함께 동일 게이트 compose 대상으로 주석에 남김.

**Task 2 — 100 fake target scheduling smoke(AC1).** 기존 5.4 smoke 2건 무변경 유지. `tests/server/test_scheduler_tick.py` 에 `5_10` 식별 신규 2건 추가: 100 대상·전부 due·`capacity=100` 단일 tick → `enqueued_count==100`, 모든 outcome `reason==REASON_ENQUEUED`(예외/RACE_LOST/THROTTLED 0), 각 job `job_snapshot(...).status=="PENDING"`, `next_run_at` ≥85 distinct seconds(storm 차단). + T+2·interval 재-tick 도 결정적 jitter 분산 유지(중복 0).

**Task 3·4 — negative safety + 운영 안전 추적성 매트릭스(AC2·AC3).** `tests/negative/test_safety_matrix.py`(always-run) 신규: AC2 7 시나리오 + AC3 검증 항목(채널 검증 전 활성화 차단/atomic write/last_message seed/autostart·heartbeat/breaker/tenant·channel pause/kill switch)을 시나리오↔정본테스트↔FailureCategory 로 매핑하고, **각 정본 테스트가 실제 정의돼 있음을 `ast` 로 확인**(import 부작용·PG 의존 0 → always-run; 완료 위조 차단, 5.9 `test_runbooks_present` 선례). 재구현 0 — 정본 테스트는 참조/추적만. 매트릭스 주석에 stale 이름 정정(스토리 표의 `test_migration_multiple_targets_each_get_own_seed` 는 미존재 → 실재명 `test_run_migration_copies_state_and_inherits_seed` + `..._active_tab_without_prior_state_has_no_seed` 로 추적)·PG-only 항목 gating 명시.

**가드레일 점검:** 신규 third-party deps 0(`pyproject.toml` 무변경), 신규 DB 컬럼/테이블/Alembic/enum 멤버 0(14표·count-lock·`FailureCategory` 7·`DeliveryStatus` 5 무회귀 — 전체 회귀로 확인), `rider_server`→`rider_agent` import 0, secret/식별정보 비노출 유지(미발송 audit `diff_redacted` 는 불투명 channel_id 만).

### File List

- `src/rider_server/services/admin_action_service.py` (수정 — `test_send` kill switch 게이트 + `effective_send_enabled`/`DeliveryLog`/`DeliveryStatus` import)
- `src/rider_server/admin/actions_routes.py` (수정 — `test_send` 라우트 pre-gate + import)
- `tests/server/test_kill_switch_5_10.py` (신규 — kill switch always-run 테스트, Task 1.4)
- `tests/server/test_scheduler_tick.py` (수정 — AC1 100-target smoke 2건 추가, Task 2)
- `tests/negative/test_safety_matrix.py` (신규 — AC2/AC3 추적성 매트릭스, Task 3·4)
- `tests/server/test_admin_actions.py` (수정 — 기존 test-send 라우트 happy-path 에 `sending_enabled=True` 설정, kill switch 무회귀)
- `src/rider_server/services/channel_registration.py` (수정[review] — `verify` 의 `send_test`(실 send)에 미래 게이트 compose 의무 NOTE 추가; 현 reachable chokepoint 아님이라 동작 무변경)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (상태 backlog→in-progress→review→done)

## Senior Developer Review (AI)

**Reviewer:** 이성열 · **Date:** 2026-06-14 · **Model:** claude-opus-4-8 (adversarial story-automator review)

**Outcome:** ✅ **Approve → done** (CRITICAL 0, HIGH 0, MEDIUM 1 auto-fixed, LOW 2 auto-fixed)

### 검증 결과 (모든 claim 을 코드/git/회귀로 대조)

- **AC1 (100-target smoke):** 충족. 신규 `test_5_10_hundred_targets_single_tick_all_enqueued_pending_and_jitter_spread` 가 `enqueued_count==100`·`reason=={REASON_ENQUEUED}`(race/throttle 0)·각 job `job_snapshot(...).status=="PENDING"`·`≥85 distinct seconds` 를 직접 단정. 2nd-cycle smoke 도 결정적 jitter 분산 재확인. 5.4 기존 smoke 2건 무변경. ✅
- **AC2 (7 negative 시나리오):** 충족. `test_safety_matrix.py` 의 36개 매트릭스 항목 정본 테스트명을 **전수 독립 검증 → 0 missing**(7 시나리오 + lease 회수 + AC3 항목). `test_canonical_safety_test_exists`(AST 파싱, import 부작용 0)가 완료 위조를 always-run 으로 차단. 재구현 0. ✅
- **AC3 (운영 안전 + kill switch):** 충족. 유일 실코드 변경인 kill switch 가 **2계층**(service `test_send(sending_enabled=…)` + route pre-gate)으로 배선돼 `sending_enabled=False` 면 주입 `send` **0회**·`DeliveryStatus.HELD`·`result=DENIED` audit. `effective_send_enabled` 재사용(재구현 0), `deliver_once`/reserve→send/crash-after-send 무변경(블록 전 short-circuit → dedup key 미소비 회귀까지 테스트로 잠금). ✅
- **회귀:** 리뷰 재측정 `1915 passed, 48 skipped, 0 failed`(dev 기록 1912 — `stale-test-count-a2` 패턴, +3 qa-e2e append). PG-gated 48 skip 불변. ✅
- **가드레일:** 신규 third-party dep 0·신규 DB/Alembic/enum 멤버 0(`FailureCategory` 7·`DeliveryStatus` 5 무회귀)·`rider_server`→`rider_agent` import 0·secret/식별정보 비노출(미발송 audit diff 는 불투명 channel_id 만). ✅

### Findings & auto-fix

| # | Severity | Finding | Action |
| - | -------- | ------- | ------ |
| 1 | MEDIUM | Debug Log 의 test count(1912)가 stale — 리뷰 재측정 1915 | Debug Log 에 review 재측정(1915/48) 추가 |
| 2 | LOW | `verify_channel.send_test`(실 send)가 ungated. dev 노트의 사유("별도 호출부라")가 실제 이유(live caller/route 0 → reachable chokepoint 아님)를 과소설명 | `channel_registration.py:verify` 에 미래 게이트 compose 의무 NOTE 추가(중앙 dispatch·test_send 와 동일 패턴). 동작 무변경 — gate 강제는 reachable route 생길 때 | 
| 3 | LOW | atomic-write 0-byte orphan tmp 파일(`5-10-….md.tmp.2982603.…`) 잔존 | 삭제 |

검토했으나 **버그 아님으로 판정:** route 가 seam 에 `sending_enabled` 미전달 → 그러나 route 가 이미 pre-gate 하므로 service 기본값(True)로 안전(2계층 defense-in-depth, 직접 호출자/미설정 fallback 까지 테스트로 잠금). 차단 시 단일 DENIED audit(중복 0).

## Change Log

| Date       | Version | Description                                                                 | Author |
| ---------- | ------- | --------------------------------------------------------------------------- | ------ |
| 2026-06-14 | 0.1     | dev-story 구현: 전역 dispatch kill switch 배선(Task 1) + AC1 100-target smoke(Task 2) + AC2/AC3 negative safety·운영 안전 추적성 매트릭스(Task 3·4) + 전체 회귀 1912 passed/48 skipped(Task 5). Status → review. | claude-opus-4-8 (Amelia/dev) |
| 2026-06-14 | 0.2     | Senior Developer Review (AI): CRITICAL 0/HIGH 0 — AC1·AC2·AC3 코드 대조로 충족 확인, 회귀 재측정 1915 passed/48 skipped. MEDIUM 1(test count stale)+LOW 2(`verify` 미래 게이트 NOTE, orphan tmp 삭제) auto-fix. Status → done. | claude-opus-4-8 (review AI) |
