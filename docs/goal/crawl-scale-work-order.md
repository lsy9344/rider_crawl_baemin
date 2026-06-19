# Crawl Scale Readiness Work Order

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for implementation slices, or `superpowers:executing-plans` if one worker executes the whole plan. Keep this file as the task checklist and update checkboxes as work lands.

**Goal:** 수백 개 크롤링 대상을 안전하게 예약, 분산 실행, 복구, 관측할 수 있도록 서버/Agent/배포 구조를 보강한다.

**Architecture:** PostgreSQL job queue와 Agent lease 모델은 유지한다. 보강 범위는 secret ref, scheduler/read-model batching, stale recovery 분리, heartbeat bulk lease, browser profile lifecycle, retry/status 저장, DB pool/load 검증이다.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy async, PostgreSQL 16, Alembic, pytest, Docker Compose.

---

## 작업 원칙

- 코드 변경 전 실패 테스트를 먼저 만든다.
- 기존 `QueueBackend`, `SchedulerRepository`, `DashboardRepository`, `JobRunner` 경계를 유지한다.
- 수백 개 규모 검증은 always-run in-memory 테스트와 PostgreSQL-gated 테스트를 둘 다 둔다.
- 계정 비밀값은 job payload, 로그, audit, metrics, 문서 예시에 평문으로 넣지 않는다.
- 단일 Agent concurrency를 올리는 작업은 브라우저 프로필 수명 정책과 hard timeout/process isolation 기준을 먼저 정한 뒤 진행한다.
- 기존 dirty worktree가 있을 수 있으므로, 이 작업과 무관한 파일은 되돌리거나 정리하지 않는다.

## 완료 기준

- PlatformAccount secret성 값은 ref 의미로만 저장/전달된다.
- `POST /v1/jobs/claim` 응답 payload에 로그인 ID/비밀번호/앱 비밀번호 평문 키가 없다.
- scheduler는 batch size로 한 tick 처리량을 제한하고 tenant/active job 조회를 bulk로 수행한다.
- dashboard read-model은 수백 target에서 target별 N+1 조회를 하지 않는다.
- stale recovery는 claim route inline 작업이 아니라 별도 worker 또는 DB advisory lock 기반 작업이다.
- heartbeat는 active job lease를 bulk로 연장한다.
- Agent는 브라우저 프로필 idle cleanup/최대 보유 수/강제 release 기준을 가진다.
- transient crawl failure가 backoff 후 retry로 재진입한다.
- DB pool, API worker, scheduler, recovery worker, Agent capacity 기준이 runbook에 있다.
- always-run 테스트와 PostgreSQL-gated scale/concurrency 테스트가 통과한다.

---

## Task 1: P0 secret ref 정리와 payload 평문 제거

**Intent:** 수백 개 운영에서 계정 secret이 DB, job payload, audit/log에 평문으로 퍼지는 것을 막는다.

**Files:**

- Modify: `src/rider_server/domain/platform_account.py`
- Modify: `src/rider_server/db/models/account.py`
- Modify: `src/rider_server/services/admin_entity_service.py`
- Modify: `src/rider_server/services/admin_entity_repository_postgres.py`
- Modify: `src/rider_server/admin/crud_routes.py`
- Modify: `src/rider_server/scheduler/postgres_repository.py`
- Modify: `src/rider_server/scheduler/service.py`
- Modify: `src/rider_agent/workers/crawl_worker.py`
- Create/Modify: `migrations/versions/0014_platform_account_secret_refs.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/server/test_admin_entity_crud.py`
- Test: `tests/agent/test_crawl_worker.py`

- [ ] **Step 1: 현재 평문 수락 테스트를 실패 테스트로 뒤집는다**

`tests/agent/test_crawl_worker.py::test_coupang_job_plaintext_secret_fields_now_accepted`는 이름과 기대값을 바꾼다.

Expected new behavior:

- `username`, `password`, `coupang_login_id`, `coupang_login_password`, `verification_email_address`, `verification_email_app_password` 평문 키가 payload에 있으면 `ERROR_PLAINTEXT_SECRET_NOT_ALLOWED`
- crawler는 호출되지 않음

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_crawl_worker.py::test_coupang_job_plaintext_secret_fields_are_rejected -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: scheduler payload ref 테스트를 추가한다**

`tests/server/test_scheduler_tick.py`에 쿠팡 payload가 ref 키만 포함하는지 확인하는 테스트를 추가한다.

Required assertions:

- includes: `coupang_login_id_ref`, `coupang_login_password_ref`, `verification_email_address_ref`, `verification_email_app_password_ref`
- excludes: `username`, `password`, `coupang_login_id`, `coupang_login_password`, `verification_email_address`, `verification_email_app_password`
- `coupang_auto_email_2fa_enabled`는 ref 4개가 모두 있을 때만 `True`

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py::test_scheduler_enqueues_coupang_secret_refs_without_plaintext_values -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 3: DB/domain naming을 ref 의미로 정리한다**

권장 방향:

- `username_ref`
- `password_ref`
- `verification_email_address_ref`
- `verification_email_app_password_ref`

Migration rule:

- 기존 컬럼 값을 새 ref 컬럼으로 backfill한다.
- 값 자체가 이미 평문일 수 있으므로 migration 로그, audit diff, test fixture에 값을 출력하지 않는다.
- 새 코드가 안정화되기 전까지 rollback 가능한 additive migration을 우선한다. 바로 drop/rename이 위험하면 `*_ref` 추가 후 후속 migration에서 old column 제거를 한다.

- [ ] **Step 4: scheduler가 payload에 ref 키만 넣게 한다**

`_crawl_job_payload()`는 secret성 값에 대해 `_ref` suffix 키만 만든다. Agent claim 응답에 평문 의미 키가 남으면 안 된다.

- [ ] **Step 5: Agent payload parser와 resolver를 ref 우선으로 고친다**

Required behavior:

- `payload_from_job()`은 secret성 필드에서 `_ref` 키만 읽는다.
- `_PLAINTEXT_SECRET_KEYS`에 평문 키를 실제로 채운다.
- `_build_config()`는 ref를 `secret_resolver`로 해석한다.
- ref처럼 보이는데 resolver가 없거나 값을 못 찾으면 `SECRET_REF_UNRESOLVED`로 fail-closed한다.

- [ ] **Step 6: Task 1 테스트를 통과시킨다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py tests/server/test_admin_entity_crud.py tests/agent/test_crawl_worker.py -q
```

PostgreSQL migration check:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_migration.py tests/server/test_db_schema.py -q
```

---

## Task 2: Scheduler tick batch 제한과 N+1 제거

**Intent:** due target이 수백 개여도 한 tick에서 메모리와 DB 왕복 수를 제한한다.

**Files:**

- Modify: `src/rider_server/scheduler/service.py`
- Modify: `src/rider_server/scheduler/postgres_repository.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/server/test_scheduler_repository.py`
- Test: `tests/negative/test_scheduler_idempotency.py`

- [ ] **Step 1: batch limit 실패 테스트를 추가한다**

`SchedulerService(due_batch_size=10)`가 due target 25개 중 10개만 처리하는지 검증한다.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py::test_scheduler_tick_respects_due_batch_limit -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: repository contract에 limit과 bulk method를 추가한다**

Add/modify:

```python
async def due_targets(self, *, now: datetime, limit: int) -> list[DueTarget]: ...
async def tenant_gates(self, tenant_ids: Sequence[str]) -> dict[str, TenantGate]: ...
async def active_crawl_job_target_ids(self, target_ids: Sequence[str]) -> set[str]: ...
```

Keep old single-row methods only if needed for compatibility. `run_tick()` should use the bulk methods.

- [ ] **Step 3: PostgreSQL due query에 안정 정렬과 limit을 넣는다**

Required query behavior:

- active target only
- `next_run_at IS NULL OR next_run_at <= now`
- `ORDER BY next_run_at NULLS FIRST, monitoring_targets.id ASC`
- `LIMIT :limit`

- [ ] **Step 4: tenant gate와 active job 조회를 bulk로 바꾼다**

Test with fake call counters:

- `tenant_gate_calls == 0`
- `bulk_tenant_gate_calls == 1`
- `has_active_crawl_job_calls == 0`
- `bulk_active_job_calls == 1`

- [ ] **Step 5: PostgreSQL idempotency를 확인한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py tests/server/test_scheduler_repository.py -q
```

PostgreSQL-gated:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest tests/negative/test_scheduler_idempotency.py -q
```

---

## Task 3: Dashboard/read-model bulk 조회와 pagination

**Intent:** 운영자가 dashboard를 열어둔 상태에서도 수백 target/agent read가 DB를 흔들지 않게 한다.

**Files:**

- Modify: `src/rider_server/admin/dashboard_repository_postgres.py`
- Modify: `src/rider_server/admin/dashboard_service.py`
- Modify: `src/rider_server/admin/routes.py`
- Modify: `src/rider_server/admin/templates/dashboard.html`
- Modify: `src/rider_server/admin/templates/_targets.html`
- Modify: `src/rider_server/admin/templates/_agents.html`
- Test: `tests/server/test_admin_dashboard.py`
- Test: `tests/negative/test_dashboard_repository_pg.py`

- [ ] **Step 1: read-model N+1을 드러내는 테스트를 추가한다**

Add tests that seed many targets and assert the repository does not issue per-target helper calls. If exact SQL query counting is hard in always-run tests, add a fake repository/service-level test first and a PostgreSQL-gated query-count test second.

Minimum checks:

- 300 target facts can be rendered without timeout in always-run test
- PostgreSQL repository uses grouped/bulk queries for last success, last delivery, latest failure, auth pending
- agent current job is loaded with one grouped query, not one query per agent

- [ ] **Step 2: `target_health()`를 bulk aggregation으로 바꾼다**

Replace per-target calls:

- `_last_collect_success()`
- `_last_delivery_success()`
- `_latest_failure_code()`
- `_auth_session_pending()`

with grouped subqueries keyed by target/account id.

- [ ] **Step 3: fragment pagination/limit을 추가한다**

Required behavior:

- `/admin/targets` accepts `limit` and optional cursor/page.
- Default target fragment limit is documented, for example 100.
- UI has a clear "more" flow or keeps full list only behind explicit admin choice.
- polling interval can be adjusted separately per fragment.

- [ ] **Step 4: dashboard tests를 실행한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py -q
```

PostgreSQL-gated:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest tests/negative/test_dashboard_repository_pg.py -q
```

---

## Task 4: stale recovery를 claim 요청 경로에서 분리

**Intent:** claim latency와 stale recovery bulk update를 분리하고, API process 수가 늘어도 recovery가 중복 폭주하지 않게 한다.

**Files:**

- Modify: `src/rider_server/api/jobs.py`
- Create: `src/rider_server/queue/recovery.py`
- Create/Modify: `src/rider_server/queue/__main__.py`
- Modify: `deploy/docker-compose.yml`
- Test: `tests/server/test_jobs_api.py`
- Test: `tests/server/test_queue_backend.py`
- Test: `tests/negative/test_queue_concurrency.py`

- [ ] **Step 1: 기존 inline recovery 테스트를 새 계약으로 바꾼다**

Current tests to update:

- `tests/server/test_jobs_api.py::test_claim_recovers_expired_lease_before_selecting_jobs`
- `tests/server/test_jobs_api.py::test_claim_throttles_stale_recovery_between_fast_polls`

New contract:

- claim route does not call `recover_stale()`
- stale recovery service can recover expired leases when run separately
- claim still returns only currently pending jobs

- [ ] **Step 2: recovery service를 추가한다**

Add:

- `recover_once(backend, now=...)`
- `recover_loop(backend, interval_seconds=...)`
- result object with `recovered_count` and `ran_at`

- [ ] **Step 3: claim route에서 `_recover_stale_if_due()` 호출을 제거한다**

Remove inline recovery helpers if unused.

- [ ] **Step 4: recovery entrypoint와 deploy process를 추가한다**

Options:

- `python -m rider_server.queue --once`
- `python -m rider_server.queue --interval-seconds 30`
- docker compose service: `queue-recovery`

For multi-API or multi-recovery deployment, prefer PostgreSQL advisory lock around `recover_stale()` or ensure only one recovery process runs.

- [ ] **Step 5: 테스트를 실행한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_jobs_api.py tests/server/test_queue_backend.py -q
```

PostgreSQL-gated:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py -q
```

---

## Task 5: heartbeat lease extension을 bulk update로 변경

**Intent:** Agent concurrency가 커져도 heartbeat DB update가 active job 수만큼 개별 트랜잭션으로 늘지 않게 한다.

**Files:**

- Modify: `src/rider_server/queue/backend.py`
- Modify: `src/rider_server/queue/memory_queue.py`
- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/api/agents.py`
- Test: `tests/server/test_agents_api.py`
- Test: `tests/server/test_queue_backend.py`

- [ ] **Step 1: backend contract에 bulk method를 추가한다**

```python
async def extend_leases(
    self,
    *,
    job_ids: Sequence[str],
    agent_id: str,
    lease_seconds: float,
    now: datetime,
) -> int: ...
```

- [ ] **Step 2: in-memory와 PostgreSQL 구현을 추가한다**

PostgreSQL은 단일 `UPDATE jobs SET lease_expires_at = :new_lease WHERE id IN (...) AND agent_id = ... AND status IN (...) AND lease_expires_at > now` 형태로 구현한다.

- [ ] **Step 3: heartbeat route에서 단건 loop를 제거한다**

`body.active_jobs`에서 유효한 `job_id`만 모아 한 번만 호출한다. 실패는 기존처럼 heartbeat 전체를 죽이지 않는다.

- [ ] **Step 4: 테스트를 실행한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_agents_api.py tests/server/test_queue_backend.py -q
```

---

## Task 6: retry 재진입과 job status 저장 보강

**Intent:** 일시 장애는 backoff 후 자동 재시도하고, 운영자가 실패/지연/완료를 정확히 볼 수 있게 한다.

**Files:**

- Modify: `src/rider_server/queue/backend.py`
- Modify: `src/rider_server/queue/memory_queue.py`
- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/api/jobs.py`
- Modify: `src/rider_server/db/models/agent.py`
- Create/Modify: Alembic migration for job completion fields
- Test: `tests/server/test_queue_backend.py`
- Test: `tests/server/test_jobs_api.py`
- Test: `tests/server/test_scheduler_policy.py`

- [ ] **Step 1: retry red test를 추가한다**

Transient crawl failure example:

- job claimed by agent
- complete with `JOB_STATUS_FAILED`, `error_code="CRAWL_FAILURE"`
- retry decider says retry
- job becomes `PENDING`
- `attempts` increments
- `run_after` is set in the future
- `agent_id`, `lease_expires_at`, `claimed_at` are cleared

- [ ] **Step 2: 사람 개입 실패는 retry하지 않는 테스트를 추가한다**

No retry for:

- `AUTH_REQUIRED`
- `TARGET_VALIDATION_FAILURE`
- `SECRET_REF_UNRESOLVED`
- plaintext secret blocked

Expected status can be `FAILED` or future `HELD`, but it must not loop forever.

- [ ] **Step 3: retry policy seam을 주입한다**

Do not hard-code scheduler policy deep inside the abstract backend. Prefer a `RetryDecider` callable injected into concrete queue backend or app composition.

- [ ] **Step 4: job completion fields를 추가한다**

Recommended columns:

- `completed_at`
- `duration_ms`
- `result_schema_version`

If adding a separate `job_events`/history table, keep it append-only and avoid raw payload/result secret data.

- [ ] **Step 5: 테스트를 실행한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_backend.py tests/server/test_jobs_api.py tests/server/test_scheduler_policy.py -q
```

---

## Task 7: Agent browser profile lifecycle과 hard timeout/process boundary

**Intent:** 한 Agent PC가 많은 target을 순회해도 Chrome/CDP/profile 자원이 무한히 쌓이지 않게 한다.

**Files:**

- Modify: `src/rider_agent/browser_profile.py`
- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify: `src/rider_agent/job_loop.py`
- Modify: `src/rider_agent/__main__.py`
- Test: `tests/agent/test_browser_profile.py`
- Test: `tests/agent/test_crawl_worker.py`
- Test: `tests/agent/test_job_loop.py`

- [ ] **Step 1: profile `last_used_at`와 idle cleanup 테스트를 추가한다**

Required behavior:

- existing assignment reuse updates `last_used_at`
- `cleanup_idle_profiles(max_idle_seconds=...)` releases old assignments
- release removes registry, port index, profile key index

- [ ] **Step 2: profile retention policy를 구현한다**

Add knobs:

- `profile_idle_ttl_seconds`
- optional `max_profiles`
- cleanup on worker finally or periodic heartbeat loop

- [ ] **Step 3: hard timeout/process boundary 설계를 테스트로 잠근다**

Minimum safe behavior:

- a crawl that exceeds `timeout_seconds` returns `CRAWL_TIMEOUT`
- Agent loop does not hang forever
- stuck crawl does not block heartbeat shutdown

If process isolation is added:

- child process receives job payload without plaintext secret
- parent can terminate child on timeout
- result is serialized through a small safe contract

- [ ] **Step 4: CLI capacity/concurrency knob을 명확히 한다**

Do not simply expose `--max-jobs` and raise it by default. If exposed:

- default remains 1
- docs warn that concurrency > 1 requires profile cleanup and enough local CPU/RAM
- heartbeat capacity reports the same limit the runner actually uses

- [ ] **Step 5: 테스트를 실행한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_browser_profile.py tests/agent/test_crawl_worker.py tests/agent/test_job_loop.py -q
```

---

## Task 8: DB pool, deploy scale knobs, load tests, runbook

**Intent:** 수백 target 운영 전 필요한 설정값과 검증 명령을 운영자가 재현할 수 있게 한다.

**Files:**

- Modify: `src/rider_server/settings.py`
- Modify: `src/rider_server/db/base.py`
- Modify: `src/rider_server/main.py`
- Modify: `src/rider_server/scheduler/__main__.py`
- Modify: `deploy/docker-compose.yml`
- Create: `tests/server/test_scale_readiness.py`
- Create: `docs/runbooks/crawl-scale-runbook.md`

- [ ] **Step 1: Settings가 DB pool env를 읽는 테스트를 추가한다**

Env:

- `RIDER_DB_POOL_SIZE`
- `RIDER_DB_MAX_OVERFLOW`
- optional `RIDER_UVICORN_WORKERS` if deployment uses it

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scale_readiness.py::test_settings_reads_database_pool_controls -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: `create_engine()`에 pool 설정을 전달한다**

Only pass pool kwargs when values are not `None`, so tests using custom poolclass keep working.

- [ ] **Step 3: deploy compose에 scale env와 recovery service를 추가한다**

Required env:

- `RIDER_DB_POOL_SIZE`
- `RIDER_DB_MAX_OVERFLOW`
- scheduler interval/batch size if added
- recovery interval

- [ ] **Step 4: runbook을 작성한다**

`docs/runbooks/crawl-scale-runbook.md` must include:

- 기본 운영 모델: "Agent 기본 동시 처리 1"
- scheduler batch size
- queue lease seconds
- heartbeat interval
- DB pool and Postgres max connections guidance
- per-Agent CPU/RAM/profile count guidance
- rollback procedure for scheduler/recovery
- scale smoke commands

- [ ] **Step 5: scale tests를 추가한다**

Always-run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py::test_5_10_hundred_targets_single_tick_all_enqueued_pending_and_jitter_spread -q
```

PostgreSQL-gated examples:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py tests/negative/test_scheduler_idempotency.py tests/negative/test_dashboard_repository_pg.py -q
```

Add a 300/500 target PostgreSQL scale test if runtime is acceptable in CI, or mark it as release-gated/manual if too slow.

---

## 통합 검증 명령

Always-run core:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py tests/server/test_queue_backend.py tests/server/test_jobs_api.py tests/server/test_agents_api.py tests/server/test_admin_dashboard.py tests/agent/test_crawl_worker.py tests/agent/test_browser_profile.py tests/agent/test_job_loop.py -q
```

Migration/schema:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_migration.py tests/server/test_db_schema.py -q
```

PostgreSQL-gated:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py tests/negative/test_scheduler_idempotency.py tests/negative/test_dashboard_repository_pg.py -q
```

Manual smoke after deploy:

```powershell
curl http://SERVER/health
curl http://SERVER/metrics
```

## Implementation Notes

- Do not claim "hundreds of concurrent crawls" until real browser/process load tests prove it.
- The safe claim after these tasks should be: "hundreds of targets/jobs are managed safely; actual concurrent crawls are bounded by reported Agent capacity."
- Keep `max_jobs=1` as the default until profile cleanup and hard timeout/process isolation are proven.
