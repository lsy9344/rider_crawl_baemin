# Reactivation No-Catchup and Timeout Work Order

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:systematic-debugging` before changing behavior, then use `superpowers:executing-plans` or `superpowers:subagent-driven-development` to implement task-by-task. This work touches scheduling and may touch protected admin/action paths; do not start with a quick patch.

작성일: 2026-06-29
상태: 작업 전
대상 저장소: `rider_result_mornitoring`
근거: H&J / 팀100 남양주동부 고객을 inactive 후 active 했을 때 `CRAWL_TIMEOUT` 위험 표시가 잠깐 발생한 운영 검토, 2026-06-29 코드 확인

**Goal:** 고객 또는 대상이 inactive/paused 상태에서 active로 돌아올 때, 비활성 기간 동안 지난 수집 시간을 따라잡는 즉시 수집이 돌지 않게 한다. 일반 스케줄은 다음 주기부터 재개하고, timeout은 안전장치로 유지하되 원인 조사와 운영 표시를 명확히 한다.

**Architecture:** 새 테이블이나 backfill 큐를 만들지 않는다. 기존 `monitoring_targets.next_run_at`를 정본으로 사용해 재활성화 시 다음 예정 시각을 미래로 미룬다. 스케줄러의 기존 due 판정, jitter, active-job 중복 방지, timeout 경계는 유지한다.

**Tech Stack:** Python, FastAPI, SQLAlchemy/PostgreSQL, Jinja admin templates, pytest.

---

## 결론

이번 증상은 쿠팡 로그인/2FA 흐름 자체가 실패했다기보다, **reactivation 시 스케줄 기준 시각이 리셋되지 않아 즉시 due가 된 것**이 1차 원인이다.

현재 구조에서는 고객이나 대상이 비활성인 동안 새 수집은 막히지만, 각 대상의 `next_run_at`은 앞으로 밀리지 않는다. 따라서 10시간 동안 inactive였다가 active가 되면 `next_run_at <= now` 조건을 만족해 바로 수집 job이 만들어진다. 이 즉시 수집이 Chrome/쿠팡 로딩 지연과 만나면 `CRAWL_TIMEOUT`으로 실패 표시가 잠깐 뜬다. 이후 성공 수집이 들어오면 대시보드가 묵은 실패를 숨기고 정상으로 돌아온다.

요구사항은 다음처럼 고정한다.

1. inactive/paused 동안 밀린 스케줄은 replay하지 않는다.
2. active 복귀는 "지금 당장 수집"이 아니라 "다음 주기부터 수집"이다.
3. timeout은 제거하거나 무작정 늘리지 않는다. timeout은 멈춘 브라우저 작업을 닫는 안전장치다.
4. 일반 로그인 후 timeout이 반복되면 selector 추측 수정이 아니라 agent/job 단계별 증거로 원인을 찾는다.

---

## 현재 코드 근거

- 스케줄러는 `due_targets(now=now)`로 `next_run_at IS NULL OR next_run_at <= now`인 ACTIVE 대상을 가져온다.
  - `src/rider_server/scheduler/service.py`
  - `src/rider_server/scheduler/postgres_repository.py`
- 스케줄러가 정상 enqueue할 때만 `next_run_at = now + interval + jitter`로 전진한다.
  - `src/rider_server/scheduler/policy.py`
  - `src/rider_server/scheduler/postgres_repository.py`
- 고객 상태 변경은 tenant row의 `status`만 바꾼다. 대상들의 `next_run_at`은 바꾸지 않는다.
  - `src/rider_server/services/admin_entities/tenant_service.py`
- 운영 대상 활성/비활성 토글은 `MonitoringTarget.status`만 바꾼다. `next_run_at`은 바꾸지 않는다.
  - `src/rider_server/services/admin_action_service.py`
  - `src/rider_server/services/admin_action_repository_postgres.py`
- 설정 CRUD의 monitoring target reactivation도 `INACTIVE -> ACTIVE` 상태만 저장한다.
  - `src/rider_server/services/admin_entities/target_service.py`
- `CRAWL_TIMEOUT`은 두 경로에서 생길 수 있다.
  - Agent child process가 `timeout_seconds` 안에 끝나지 않음: `src/rider_agent/workers/crawl_process.py`
  - 서버 queue recovery가 lease 만료 작업을 회수함: `src/rider_server/queue/postgres_queue.py`
- 대시보드는 실패 시각보다 더 최근의 성공 수집이 있으면 실패 코드를 stale로 보고 표시에서 뺀다.
  - `src/rider_server/admin/dashboard_service.py`

---

## 비범위

- 쿠팡 로그인, 이메일 2FA selector, OTP 처리, CDP 연결 세부 흐름을 이 작업에서 바꾸지 않는다.
- timeout을 제거하지 않는다.
- timeout 값을 무작정 늘리는 것으로 해결하지 않는다.
- inactive 기간 동안의 누락 수집을 backfill하지 않는다.
- `snapshots`나 `delivery_logs`의 과거 성공/실패 시각을 수정하지 않는다.
- 수동 `지금 수집`, `test-crawl`, `auth-start`, `auth-check` 액션의 즉시 실행 의미는 유지한다.
- 신규 DB 테이블, 신규 queue type, 신규 enum member를 만들지 않는다.

---

## Protected Contract 주의

아래 파일을 수정해야 하면 AGENTS.md의 protected contract를 따른다.

- `src/rider_server/services/admin_action_service.py`
- `src/rider_server/scheduler/service.py`
- `src/rider_server/queue/postgres_queue.py`

특히 `src/rider_server/services/admin_action_service.py`는 운영 대상 활성/비활성 토글 때문에 수정 가능성이 있다. 수정 전에는 호출 경로와 payload path를 추적하고, 먼저 회귀 테스트를 추가한다.

이번 작업은 원칙적으로 쿠팡 로그인/2FA runtime 파일을 건드리지 않는다. 만약 조사 중 selector, wait, login, 2FA, CDP routing 변경이 필요해지면 이 작업을 멈추고 별도 작업지시서로 분리한다.

---

## 핵심 정책

### 1. Reactivation은 다음 주기부터 재개한다

비활성 상태에서 활성 상태로 돌아올 때 다음 수집 시각을 재설정한다.

권장 계산:

```python
interval_seconds = target.interval_minutes * 60
jitter = policy.compute_jitter(target.id, interval_seconds)
next_run_at = policy.next_run_at(now, interval_seconds, jitter)
```

주의:

- `last_enqueued_at`은 바꾸지 않는다. 실제 enqueue가 아니기 때문이다.
- `last_success_at`은 바꾸지 않는다. 이 값은 `snapshots`에서 파생된다.
- 이미 ACTIVE인 고객/대상을 다시 저장하는 no-op update는 `next_run_at`을 미루지 않는다. 그렇지 않으면 운영자가 설정 저장만 해도 수집이 밀린다.
- 수동 `지금 수집`은 이 정책과 무관하게 즉시 enqueue된다.

### 2. 고객 단위 active 전환은 모든 active 대상의 schedule을 reset한다

고객 `status`가 non-schedulable 상태에서 schedulable 상태로 바뀌는 경우:

- 해당 tenant의 `MonitoringTarget.status == ACTIVE` 대상만 찾는다.
- 각 대상의 `next_run_at`을 `now + interval + jitter`로 설정한다.
- `PAUSED` 또는 `INACTIVE` 대상은 건드리지 않는다.
- 같은 트랜잭션 안에서 tenant update, target schedule reset, audit이 일관되게 끝나야 한다.

Schedulable 상태 기준은 scheduler와 같아야 한다.

- 고객 lifecycle: `CustomerLifecycleState.ACTIVE`, `CustomerLifecycleState.PAYMENT_ACTIVE`
- 구독 상태는 기존 `SubscriptionGate` 판단을 재사용한다. 이 작업에서 구독 정책을 새로 만들지 않는다.

### 3. 대상 단위 active 전환도 schedule을 reset한다

대상 `PAUSED -> ACTIVE` 또는 `INACTIVE -> ACTIVE` 전환 시:

- 해당 대상 하나의 `next_run_at`을 `now + interval + jitter`로 설정한다.
- 이미 ACTIVE인 대상에 활성화를 다시 누른 경우에는 no-op으로 둔다.
- `INACTIVE -> ACTIVE`는 설정 CRUD reactivation 경로, `PAUSED -> ACTIVE`는 운영 토글 경로가 서로 다르므로 둘 다 테스트한다.

### 4. Timeout은 유지한다

timeout은 "브라우저 창이 열렸는데도 결과가 영원히 안 오는 상황"을 끊는 장치다.

유지해야 하는 이유:

- Chrome/CDP가 응답하지 않을 수 있다.
- 쿠팡 페이지가 로그인 후 대시보드로 넘어가지 않을 수 있다.
- selector 대기가 끝나지 않을 수 있다.
- child Python 또는 Chrome 자식 프로세스가 남아 다음 작업을 막을 수 있다.
- timeout이 없으면 한 고객 작업이 Agent 전체를 붙잡는다.

따라서 이 작업의 성공 기준은 timeout을 없애는 것이 아니라, **reactivation 직후 의도하지 않은 즉시 수집을 막아 timeout 노출 가능성을 줄이는 것**이다.

### 5. 일반 로그인 후 timeout 반복은 별도 증거로 조사한다

reactivation no-catchup 수정 후에도 일반 scheduled crawl에서 `CRAWL_TIMEOUT`이 반복되면 다음 순서로 조사한다.

1. `jobs`에서 해당 target의 최근 job timeline을 본다.
   - `status`
   - `error_code`
   - `attempts`
   - `claimed_at`
   - `lease_expires_at`
   - `completed_at`
   - `duration_ms`
   - `result_json`
2. timeout이 Agent child process timeout인지 server stale lease recovery인지 구분한다.
3. Agent PC 로그에서 단계별 시간을 확인한다.
   - profile prepare
   - Chrome launch/CDP connect
   - login/auth probe
   - page goto/load
   - table/selector wait
   - parser
   - complete report
4. 단계별 증거 없이 timeout 값을 늘리거나 selector를 바꾸지 않는다.

---

## 작업지시서

### Task 0: 회귀 재현 테스트를 먼저 추가한다

목표: 현재 버그를 테스트로 고정한다.

테스트 후보:

- `tests/server/test_admin_entity_crud.py`
  - 고객 `INACTIVE -> ACTIVE` update 후 해당 tenant의 ACTIVE targets `next_run_at`이 미래로 재설정되는지 검증.
  - 이미 ACTIVE인 고객을 다시 저장할 때 `next_run_at`이 밀리지 않는지 검증.
- `tests/server/test_admin_actions.py`
  - 대상 `PAUSED -> ACTIVE` 후 `next_run_at`이 미래로 재설정되는지 검증.
  - 대상이 이미 ACTIVE이면 schedule reset이 no-op인지 검증.
- `tests/server/test_scheduler_tick.py`
  - reactivation 직후 같은 `now`로 tick을 돌려도 enqueue되지 않는지 검증.
  - reset된 `next_run_at` 이후 tick에서는 정상 enqueue되는지 검증.

예상 테스트 이름:

```python
def test_customer_reactivation_defers_active_targets_until_next_interval() -> None: ...
def test_customer_active_noop_does_not_defer_schedule() -> None: ...
def test_target_activation_defers_schedule_until_next_interval() -> None: ...
def test_scheduler_does_not_catch_up_after_reactivation_reset() -> None: ...
```

성공 기준:

- 현재 코드에서 적어도 하나의 테스트가 실패해야 한다.
- 실패 이유가 `next_run_at` 미갱신 또는 immediate enqueue여야 한다.

### Task 1: schedule reset helper를 만든다

목표: scheduler와 같은 계산식을 재사용해 reactivation 다음 실행 시각을 만든다.

권장 위치:

- 순수 helper는 `src/rider_server/scheduler/policy.py` 또는 admin repository 내부 private helper 중 하나를 선택한다.
- scheduler policy를 import하면 기존 `compute_jitter()`와 `next_run_at()`을 그대로 쓸 수 있다.

설계:

```python
def reactivation_next_run_at(target_id: str, interval_minutes: int, now: datetime) -> datetime:
    interval_seconds = interval_minutes * 60
    jitter = policy.compute_jitter(target_id, interval_seconds)
    return policy.next_run_at(now, interval_seconds, jitter)
```

주의:

- 같은 target id는 항상 같은 jitter를 받아야 한다.
- interval이 비정상 값이면 기존 scheduler 정책과 충돌하지 않게 처리한다. 새 정책을 만들지 말고 기존 validation/behavior와 맞춘다.

### Task 2: 고객 reactivation 경로에서 tenant targets schedule을 reset한다

목표: 고객 status가 inactive 계열에서 active 계열로 전환될 때, 해당 고객의 ACTIVE targets가 즉시 due가 되지 않게 한다.

수정 후보:

- `src/rider_server/services/admin_entities/tenant_service.py`
- `src/rider_server/services/admin_entity_repository_postgres.py`
- `src/rider_server/services/admin_entity_service.py`의 in-memory repository/test seam

구현 지침:

- `existing.status`와 `updated.status`를 비교해 non-schedulable -> schedulable 전환만 감지한다.
- 같은 transaction에서:
  - tenant row update
  - audit insert
  - active monitoring targets `next_run_at` reset
- PostgreSQL 구현은 대상 목록을 조회한 뒤 target별 deterministic next_run_at을 계산해 update한다.
- in-memory fake도 같은 의미를 구현해 always-run tests가 DB 없이 통과하게 한다.

금지:

- 모든 tenant update마다 `next_run_at`을 밀지 않는다.
- PAUSED/INACTIVE targets를 ACTIVE로 바꾸지 않는다.
- 성공/실패 이력 테이블을 조작하지 않는다.

### Task 3: 대상 activation/reactivation 경로에서 해당 target schedule을 reset한다

목표: 대상 하나를 다시 ACTIVE로 만들 때도 즉시 due가 되지 않게 한다.

대상 경로:

- 운영 토글: `src/rider_server/services/admin_action_service.py::set_target_status`
- 설정 CRUD reactivation: `src/rider_server/services/admin_entities/target_service.py::reactivate_monitoring_target`
- PostgreSQL write: `src/rider_server/services/admin_action_repository_postgres.py`, `src/rider_server/services/admin_entity_repository_postgres.py`
- in-memory fakes

구현 지침:

- `PAUSED -> ACTIVE` 또는 `INACTIVE -> ACTIVE` 전환에서만 reset한다.
- 이미 ACTIVE이면 reset하지 않는다.
- audit diff에 secret은 넣지 않는다. 필요한 경우 `schedule_reset: true`, `next_run_at_policy: "NEXT_INTERVAL_WITH_JITTER"` 정도의 비식별 정보만 넣는다.
- `admin_action_service.py`를 수정하면 protected test set 실행이 필수다.

### Task 4: stale queued job 경계를 확인한다

목표: inactive 중 이미 큐에 남아 있던 scheduled crawl이 active 복귀 직후 실행되는 다른 경로가 없는지 확인한다.

조사:

- target/customer가 inactive가 된 뒤 이미 존재하던 `PENDING`, `RETRY`, `CLAIMED`, `RUNNING` crawl job이 어떻게 처리되는지 확인한다.
- `job preflight`, queue recovery, scheduler active-job dedup 중 어디서 막히는지 코드와 테스트로 확인한다.

결정:

- 이미 안전하면 그 증거를 테스트명/코드 위치로 문서에 남긴다.
- gap이 있으면 별도 좁은 수정으로 처리한다.
  - 후보: inactive target/customer의 stale scheduled crawl은 claim 전 fail-closed로 닫기.
  - 후보: pause/inactivate 시 아직 claim 전인 scheduled crawl을 terminal failed/skipped 처리.

주의:

- 이 작업은 "reactivation 시 과거 주기 replay 금지"가 핵심이다.
- 기존에 실행 중인 job을 강제로 죽이는 기능은 운영 영향이 커서 별도 판단이 필요하다.

### Task 5: timeout 운영 설명과 UI 문구를 정리한다

목표: timeout이 "로그인 실패"나 "활성화 실패"로 오해되지 않게 한다.

수정 후보:

- `src/rider_server/admin/routes.py`의 reason text
- `docs/operations/agent-browser-profile-observability.md`
- `docs/runbooks/crawl-scale-runbook.md`

문구 방향:

- `CRAWL_TIMEOUT`: "수집 작업이 제한 시간 안에 완료되지 않음 - Agent/Chrome/페이지 로딩 확인"
- 설명에는 다음을 포함한다.
  - Chrome 창 표시만으로 수집 성공을 뜻하지 않는다.
  - 성공은 snapshot 저장 또는 job complete success로 판단한다.
  - 반복 발생 시 Agent 로그와 job timeline을 먼저 본다.

주의:

- UI 문구 변경만으로 문제를 해결했다고 주장하지 않는다.
- timeout 값을 변경하지 않는다.

### Task 6: 일반 로그인 후 timeout 반복 조사 runbook을 추가한다

목표: reactivation no-catchup 이후에도 timeout이 반복될 때 확인 순서를 남긴다.

문서 위치 후보:

- `docs/runbooks/auth_required.md`
- `docs/runbooks/crawl-scale-runbook.md`
- 또는 새 문서 `docs/runbooks/crawl-timeout-investigation.md`

포함할 확인 쿼리:

```sql
SELECT
  j.id,
  j.type,
  j.status,
  j.error_code,
  j.attempts,
  j.claimed_at,
  j.lease_expires_at,
  j.completed_at,
  j.duration_ms,
  j.result_json
FROM jobs j
WHERE j.target_id = '<target_id>'
ORDER BY COALESCE(j.completed_at, j.claimed_at, j.run_after) DESC
LIMIT 20;
```

포함할 판단:

- `completed_at`이 있고 `error_code='CRAWL_TIMEOUT'`: Agent process timeout 가능성이 높다.
- `lease_expires_at <= now` 뒤 recovery가 실패 처리: lease/heartbeat/stale recovery 경로 가능성이 높다.
- Agent heartbeat가 끊겼으면 Chrome 문제가 아니라 Agent offline/lease 문제부터 본다.

---

## Acceptance Criteria

1. 고객 `INACTIVE -> ACTIVE` 후 같은 시각 scheduler tick은 해당 고객의 기존 ACTIVE targets를 즉시 enqueue하지 않는다.
2. 고객 active 복귀 후 각 ACTIVE target의 `next_run_at`은 `now + interval + deterministic jitter`로 설정된다.
3. 고객이 이미 ACTIVE인 상태에서 일반 설정 저장을 해도 `next_run_at`은 밀리지 않는다.
4. 대상 `PAUSED -> ACTIVE` 후 같은 시각 scheduler tick은 해당 target을 즉시 enqueue하지 않는다.
5. 설정 CRUD의 `INACTIVE -> ACTIVE` target reactivation도 같은 no-catchup 정책을 따른다.
6. 수동 `test-crawl` / "지금 수집"은 여전히 즉시 enqueue된다.
7. timeout 경계와 `CRAWL_TIMEOUT` error code는 유지된다.
8. UI/운영 문서는 `CRAWL_TIMEOUT`을 로그인 실패가 아니라 수집 완료 시간 초과로 설명한다.
9. protected 파일을 수정했다면 protected test set이 통과한다.

---

## Verification

기본 targeted tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\server\test_admin_entity_crud.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_scheduler_repository.py -q
```

protected 파일을 수정한 경우 최소 protected test set:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
```

PostgreSQL 의미를 바꾼 경우, `TEST_DATABASE_URL`이 가능한 환경에서 관련 PG-gated tests도 실행한다.

운영 확인:

1. 고객을 inactive로 바꾼다.
2. 해당 고객의 target `next_run_at`이 과거가 되도록 충분히 기다리거나 테스트 DB에서 과거로 설정한다.
3. 고객을 active로 바꾼다.
4. 즉시 jobs에 새 `CRAWL_*` job이 생기지 않는지 확인한다.
5. target `next_run_at`이 미래로 재설정되었는지 확인한다.
6. `next_run_at` 이후에는 정상적으로 1건만 enqueue되는지 확인한다.

확인 쿼리 예:

```sql
SELECT id, tenant_id, name, status, interval_minutes, next_run_at, last_enqueued_at
FROM monitoring_targets
WHERE tenant_id = '<tenant_id>'
ORDER BY name;
```

```sql
SELECT id, type, status, target_id, run_after, claimed_at, error_code
FROM jobs
WHERE target_id IN (
  SELECT id FROM monitoring_targets WHERE tenant_id = '<tenant_id>'
)
ORDER BY COALESCE(run_after, claimed_at, completed_at) DESC
LIMIT 20;
```

---

## Rollout Notes

- 이 변경은 "활성화하면 즉시 수집"이라는 숨은 동작을 "활성화하면 다음 주기부터 수집"으로 바꾼다.
- 운영자가 즉시 확인하고 싶을 때는 별도 `지금 수집` 버튼을 사용해야 한다.
- 배포 후 H&J와 팀100 남양주동부처럼 최근에 inactive/active를 반복한 고객을 대상으로 `next_run_at`과 jobs timeline을 확인한다.
- timeout이 계속 보이면 no-catchup 수정과 별개로 Agent PC/Chrome/CDP/page-load 조사를 진행한다.

---

## Open Questions

1. 고객 active 복귀 시 "다음 주기"를 `now + interval + jitter`로 둘지, `now + jitter only`로 둘지 결정해야 한다. 이 문서는 `now + interval + jitter`를 기본값으로 둔다. 이유는 "밀린 수집 금지" 요구에 가장 보수적이기 때문이다.
2. pause/inactive 시점에 이미 있던 pending scheduled crawl을 즉시 닫을지 여부는 Task 4 조사 후 결정한다.
3. 대시보드가 active 직후 "마지막 수집 성공 10시간 전" 때문에 계속 위험으로 보이는 UX는 별도 이슈다. no-catchup 정책은 새 수집 job을 막지만, 과거 성공 시각 기반 freshness 표시는 그대로다. 필요하면 "활성화 직후 grace window"를 별도 작업지시서로 분리한다.
