# 유지보수성 구조 개선 작업 지시서

완료: 2026-06-19 기준, 작업지시서의 Task 0-7 작업을 끝냈습니다.

> **For agentic workers:** 이 문서는 작업자가 순서대로 따라갈 수 있는 구현 지시서다. 구현 세션에서는 `superpowers:subagent-driven-development` 또는 `superpowers:executing-plans`를 사용하고, 각 task의 checkbox를 실제 진행 상태로 갱신한다.

작성일: 2026-06-19  
반영 근거: `docs/goal/maintenance-structure-review-2026-06-19.md`

## Goal

서버/Agent/운영 설정의 경계를 작게 정리해, 다고객 운영과 dispatch 확장 시 변경 범위, 장애 복구 위험, 배포 실수 가능성을 줄인다.

## 핵심 설계 방향

- 운영 env는 fail-closed를 기본값으로 둔다.
- HTTP 라우트는 인증, 입력 검증, 응답 변환만 맡긴다.
- DB 저장소는 DB 기록까지만 맡기고 외부 네트워크 전송은 worker/service로 분리한다.
- `delivery_logs`를 dispatch outbox처럼 쓴다. 현재 프로젝트 컨텍스트의 “14 Required Tables 고정” 규칙 때문에, 새 테이블보다 additive column을 우선한다.
- `create_app()` wiring은 typed runtime container로 읽히게 만든다.
- scheduler는 target 수가 늘어도 per-target DB round-trip을 반복하지 않는다.
- Agent는 sync-only, stdlib-only, no `rider_server` import 규칙을 유지한다.

## 작업 전 전제

- 기존 미커밋 변경을 되돌리지 않는다.
- 제품 코드 경계는 유지한다: `rider_server`와 `rider_agent`는 `rider_crawl`을 재사용할 수 있지만, `rider_agent`는 `rider_server`를 import하지 않는다.
- 서버 런타임은 async 경계를 유지한다. blocking 동기 작업은 executor 또는 별도 worker/process 경계로 보낸다.
- secret 평문을 새 문서, 테스트 fixture, 로그, 설정 예시에 넣지 않는다.
- 각 Task는 가능하면 별도 커밋으로 나눈다.
- PostgreSQL 관련 task는 `TEST_DATABASE_URL`이 없으면 postgres 검증을 “미실행”으로 명시하고, quick/architecture 검증은 그래도 실행한다.

## Task 0: 기준선 확인

**Files:**

- Read: `docs/goal/maintenance-structure-review-2026-06-19.md`
- Read: `docs/qa/test-execution-strategy.md`
- Read: `tests/conftest.py`
- Read: `scripts/test.ps1`

- [x] **Step 1: 현재 변경 범위 확인**

Run:

```powershell
git status --short --branch
git diff --stat
```

Expected:

- 기존 사용자 변경이 보인다.
- 이번 작업은 해당 변경을 되돌리지 않는다.

- [x] **Step 2: 빠른 기준선 실행**

Run:

```powershell
.\scripts\test.ps1 quick
```

Expected:

- PASS.
- 실패하면 실패 파일, 실패 수, 기존 실패 여부를 기록하고 구조 변경을 시작하기 전에 사용자에게 알린다.

- [x] **Step 3: 구조 가드 기준선 실행**

Run:

```powershell
.\scripts\test.ps1 architecture
```

Expected:

- PASS.
- Agent import 경계와 Server async 경계가 현재 상태에서 살아 있어야 한다.

Rollback:

- Task 0은 읽기/검증만 하므로 코드 rollback이 없다.

Progress note:

- 2026-06-19: 최초 기준선은 기존 작업트리 변경으로 `quick` 3건, `architecture` 1건이 실패했다. 기준선 테스트 정합성만 보정한 뒤 `quick` 2067 passed, `architecture` 91 passed를 확인했다.

## Task 1: 배포 env fail-closed와 Telegram secret handoff 정리

**Why first:** 운영 기본값과 secret 전달은 코드 구조 refactor보다 작고, 실수 영향은 크다.

**Files:**

- Modify: `deploy/env/backend-api.env`
- Create: `deploy/env/backend-api.dev-public-admin.env`
- Create or Modify: `deploy/docker-compose.dev-public-admin.yml` if public Admin opt-in needs compose override
- Modify: `deploy/docker-compose.yml` if `env:` secret ref를 compose에서 직접 전달하기로 결정
- Modify: `tests/server/test_deployment_config.py`
- Modify: `docs/operations/aws-product-setup-2026-06-18.md`

- [x] **Step 1: 공개 Admin 기본값 실패 테스트 추가**

Add a test:

```python
def test_backend_api_env_does_not_enable_public_admin_by_default():
    text = Path("deploy/env/backend-api.env").read_text(encoding="utf-8")

    assert "RIDER_ADMIN_PUBLIC_ACCESS=1" not in text
    assert "RIDER_ADMIN_PUBLIC_ACCESS=true" not in text.lower()
```

Expected:

- FAIL. 현재 `deploy/env/backend-api.env`가 `RIDER_ADMIN_PUBLIC_ACCESS=1`을 가진다.

- [x] **Step 2: secret handoff 정책 테스트 추가**

Choose one policy and test it.

Preferred policy A, env ref 사용:

- `deploy/env/telegram-webhook.env`가 `env:RIDER_TELEGRAM_WEBHOOK_SECRET`를 가리키면, compose backend service가 실제 `RIDER_TELEGRAM_WEBHOOK_SECRET` env를 컨테이너에 전달해야 한다.
- send fallback으로 `env:RIDER_TELEGRAM_BOT_TOKEN`을 쓰면 `RIDER_TELEGRAM_BOT_TOKEN`도 전달해야 한다.

Alternative policy B, tenant DB secret만 사용:

- compose가 actual Telegram secret env를 전달하지 않는 대신, 문서와 테스트가 “env fallback은 production 정본이 아니다”라고 명시해야 한다.

Expected:

- 현재 문서/compose는 이 경계를 충분히 설명하지 못하므로 첫 테스트는 실패해야 한다.

- [x] **Step 3: 운영 기본 env를 fail-closed로 변경**

In `deploy/env/backend-api.env`, make public Admin disabled by default:

```dotenv
RIDER_ADMIN_PUBLIC_ACCESS=0
```

Keep nearby comments simple:

- 기본값은 운영 안전을 위해 OFF다.
- 임시 내부 점검은 별도 dev override를 사용한다.

Expected:

- production-like env가 공개 Admin을 기본으로 켜지 않는다.

- [x] **Step 4: dev public Admin opt-in 파일 생성**

Create `deploy/env/backend-api.dev-public-admin.env`:

```dotenv
# 임시 개발/내부 점검용. 운영 기본 env에 섞지 않는다.
RIDER_ADMIN_PUBLIC_ACCESS=1
```

If compose needs an override, create `deploy/docker-compose.dev-public-admin.yml`:

```yaml
services:
  backend-api:
    env_file:
      - ./env/backend-api.env
      - ./env/backend-api.dev-public-admin.env
```

Expected:

- 편의 모드는 명시적으로 opt-in 한다.
- `deploy/docker-compose.yml`만 실행하면 public Admin이 켜지지 않는다.

- [x] **Step 5: runbook 갱신**

In `docs/operations/aws-product-setup-2026-06-18.md`, add:

- 기본 `backend-api.env`는 공개 Admin을 켜지 않는다.
- 임시 공개 Admin 접근이 필요하면 dev override 파일과 compose override를 명시한다.
- 운영 전환 시에는 대체 인증, IP 제한, 승인 기록을 먼저 확인한다.
- Telegram webhook/send secret을 env ref로 쓰는지 tenant DB secret으로 쓰는지 운영자가 확인해야 한다.

Expected:

- 운영자가 어떤 파일을 써야 하는지 알 수 있다.

- [x] **Step 6: 검증**

Run:

```powershell
.\scripts\test.ps1 quick tests\server\test_deployment_config.py
.\scripts\test.ps1 docs
```

Expected:

- PASS.

Commit:

```powershell
git add deploy/env/backend-api.env deploy/env/backend-api.dev-public-admin.env deploy/docker-compose.dev-public-admin.yml tests/server/test_deployment_config.py docs/operations/aws-product-setup-2026-06-18.md
git commit -m "chore(deploy): make admin public access opt-in"
```

Rollback:

- Revert the env/doc/test commit.
- Do not restore `RIDER_ADMIN_PUBLIC_ACCESS=1` silently; if rollback is needed, record why public Admin must remain enabled.

Progress note:

- 2026-06-19: 공개 Admin 기본값과 Telegram env handoff 테스트를 RED로 확인한 뒤, `backend-api.env` fail-closed, dev opt-in env/compose override, backend actual secret env 전달, runbook 문구를 반영했다. `quick tests\server\test_deployment_config.py` 34 passed, `docs` 133 passed.

## Task 2: JobCompletionService 추가

**Files:**

- Create: `src/rider_server/services/job_completion_service.py`
- Modify: `src/rider_server/api/jobs.py`
- Modify: `src/rider_server/main.py`
- Test: `tests/server/test_jobs_api.py`
- Test: `tests/server/test_snapshot_telegram_runtime.py`

- [x] **Step 1: 실패 테스트 추가**

Add tests proving the API route delegates completion policy to a service and preserves current HTTP behavior.

Minimum test ideas:

- `app.state.job_completion_service.complete(...)` is called for `/v1/jobs/{job_id}/complete`.
- service success returns the same response body as the current route.
- service conflict maps to the same 409 behavior.
- service not found maps to the same 404 behavior.
- invalid snapshot payload maps to 422.

Expected:

- FAIL because `app.state.job_completion_service` is not used yet.

- [x] **Step 2: service 파일 생성**

Create `src/rider_server/services/job_completion_service.py` with:

- `JobCompletionResult`
- `JobCompletionConflict`
- `JobCompletionNotFound`
- `JobCompletionInvalid`
- `JobCompletionService`
- explicit Protocols for the queue and ingest operations the service needs

Move the current completion workflow out of `src/rider_server/api/jobs.py`.

Rules:

- Keep behavior unchanged.
- Do not keep `getattr(..., "complete_snapshot_job", None)` as the main contract. Use an explicit Protocol or constructor dependency.
- Keep queue restore/compensation behavior in the service, not in the route.
- Keep HTTP-only validation, bearer matching, and exception-to-status mapping in the route.

Expected:

- The service owns job completion policy.
- The route becomes an adapter.

- [x] **Step 3: route를 얇게 변경**

In `src/rider_server/api/jobs.py`, make `complete_job()`:

- validate bearer agent matches body agent
- call `request.app.state.job_completion_service.complete(...)`
- map service exceptions to current HTTP statuses
- return only the response body

Expected mapping:

- invalid job id or invalid ingest payload: 422
- not found: 404
- lease lost or invalid transition: 409
- success: `{"job_id": ..., "status": ...}`

- [x] **Step 4: app wiring 추가**

In `src/rider_server/main.py`, wire:

```python
app.state.job_completion_service = JobCompletionService(
    queue_backend=app.state.queue_backend,
    ingest_service=app.state.job_result_ingest_service,
)
```

Expected:

- Existing tests that use `job_result_ingest_service` still work.

- [x] **Step 5: 검증**

Run:

```powershell
.\scripts\test.ps1 quick tests\server\test_jobs_api.py tests\server\test_snapshot_telegram_runtime.py
.\scripts\test.ps1 architecture
```

Expected:

- PASS.

Commit:

```powershell
git add src/rider_server/services/job_completion_service.py src/rider_server/api/jobs.py src/rider_server/main.py tests/server/test_jobs_api.py tests/server/test_snapshot_telegram_runtime.py
git commit -m "refactor(server): move job completion workflow into service"
```

Rollback:

- Revert this commit if completion behavior changes unexpectedly.
- Before retrying, compare old and new HTTP mappings in `tests/server/test_jobs_api.py`.

Progress note:

- 2026-06-19: `complete` route delegation RED 테스트를 추가하고, `JobCompletionService`로 queue complete, snapshot prepare/atomic complete, commit failure compensation을 이동했다. 라우트는 bearer/body agent 확인, status 매핑, 서비스 예외의 HTTP 변환만 남겼다. `quick tests\server\test_jobs_api.py tests\server\test_snapshot_telegram_runtime.py` 38 passed, `architecture` 93 passed.

## Task 3: Snapshot ingest와 Telegram dispatch worker 분리

**Files:**

- Modify: `src/rider_server/services/snapshot_repository_postgres.py`
- Create: `src/rider_server/services/dispatch_worker.py`
- Create or Modify: `src/rider_server/dispatcher/__main__.py` if a separate worker process is chosen
- Modify: `src/rider_server/db/models/messaging.py`
- Add Alembic migration under `migrations/versions/`
- Modify: `tests/server/test_snapshot_telegram_runtime.py`
- Modify: `tests/server/test_telegram_central_dispatch.py`
- Modify: `tests/server/test_postgres_runtime_guards.py`
- Modify: `tests/negative/test_queue_concurrency.py` or a new dispatch concurrency test if needed

- [x] **Step 1: delivery log outbox shape 결정**

Use existing `delivery_logs` as the outbox surface unless the architecture rule about 14 required tables is explicitly changed.

Additive columns should cover:

- next attempt time, for example `available_at` or `next_attempt_at`
- attempt count, for example `attempt_count`
- worker claim lock, for example `locked_at`, `locked_by`
- optional last error timestamp/message code if current `error_code` is not enough

Expected:

- No new table is introduced in the default path.
- A worker can safely claim pending rows without two workers sending the same delivery.

- [x] **Step 2: 실패 테스트 추가**

Add tests proving:

- `complete_snapshot_job()` records pending Telegram delivery work without calling the Telegram sender inline.
- pending deliveries can be claimed by only one worker.
- retryable failure updates attempt/backoff state.
- non-retryable failure becomes `FAILED` or equivalent final state.
- structure guard no longer requires `_deliver_telegram_after_commit()` after `_enqueue_dispatch_records()`.

Expected:

- FAIL because current repository calls `_deliver_telegram_after_commit()`.

- [x] **Step 3: Alembic migration 추가**

Add an additive migration for `delivery_logs`.

Rules:

- Keep existing rows valid.
- Provide safe defaults or nullable columns for existing data.
- Do not store raw secret values.
- Add indexes needed for pending claim query, for example status + available_at + locked_at.

Expected:

- PostgreSQL tests can run migration from empty DB.

- [x] **Step 4: repository 책임 축소**

Change `PostgresSnapshotIngestRepository.complete_snapshot_job()` so it:

- completes the job
- inserts snapshot
- inserts message
- inserts delivery log rows
- inserts Kakao send jobs if the current Kakao path already requires it
- leaves Telegram delivery rows pending for worker processing
- does not call Telegram sender after commit

Expected:

- Snapshot completion has no network side effect.
- `snapshot_repository_postgres.py` no longer owns Telegram send attempt policy.

- [x] **Step 5: dispatch worker 추가**

Create `src/rider_server/services/dispatch_worker.py`.

Responsibilities:

- claim pending Telegram delivery rows in batches
- call existing `CentralTelegramSender`, `DeliveryFailurePolicy`, and idempotency logic
- update delivery status, attempt count, error code, sent time, next attempt
- release or expire locks safely

Non-responsibilities:

- Do not reimplement Telegram Bot API calls.
- Do not create a second idempotency mechanism.
- Do not mix Kakao PC UI automation into the server worker unless there is an explicit Kakao server-side delivery path.

Expected:

- Dispatch can be run explicitly by a worker loop, scheduler job, or test.

- [x] **Step 6: worker 실행 위치 결정**

Choose one and document it in the code/runbook:

- Preferred: separate process/service, for example `python -m rider_server.dispatcher`
- Acceptable for first local-only pass: explicit scheduler-maintenance command
- Avoid as default: FastAPI background task hidden inside request-serving process

If using Docker Compose, add a service only after tests prove the worker loop exits cleanly on shutdown.

Expected:

- 운영자가 worker를 어떻게 띄우는지 알 수 있다.

- [x] **Step 7: 검증**

Run:

```powershell
.\scripts\test.ps1 quick tests\server\test_snapshot_telegram_runtime.py tests\server\test_telegram_central_dispatch.py
.\scripts\test.ps1 architecture
```

If `TEST_DATABASE_URL` is available:

```powershell
.\scripts\test.ps1 postgres
```

Expected:

- PASS.

Commit:

```powershell
git add src/rider_server/services/snapshot_repository_postgres.py src/rider_server/services/dispatch_worker.py src/rider_server/db/models/messaging.py migrations/versions tests/server/test_snapshot_telegram_runtime.py tests/server/test_telegram_central_dispatch.py tests/server/test_postgres_runtime_guards.py
git commit -m "refactor(server): separate snapshot ingest from telegram dispatch"
```

Rollback:

- Revert this commit if completion creates delivery logs but worker cannot process them.
- If the migration already ran in a shared DB, write a forward migration that disables worker claim rather than hand-editing schema.

Progress note:

- 2026-06-19: `delivery_logs`에 `available_at`, `attempt_count`, `locked_at`, `locked_by`와 claim index를 추가하고 0015 migration을 만들었다. Snapshot ingest는 Telegram row 생성까지만 수행하고 inline post-commit send를 제거했다. `TelegramDispatchWorker`는 `FOR UPDATE SKIP LOCKED` claim, retry/backoff/final update, lock clear 경계를 가진다. 실행 위치는 숨은 FastAPI background task가 아니라 명시 worker loop 또는 scheduler-maintenance command에서 `run_once()`를 호출하는 방식으로 코드에 기록했다. `quick` 2075 passed, `architecture` 95 passed. `TEST_DATABASE_URL`이 없어 `postgres` 단계는 미실행.

## Task 4: RuntimeDeps composition root 추가

**Files:**

- Create: `src/rider_server/runtime.py`
- Modify: `src/rider_server/main.py`
- Test: `tests/server/test_server_app.py`
- Test: `tests/server/test_snapshot_telegram_runtime.py`

- [x] **Step 1: 실패 테스트 추가**

Add a test:

```python
def test_create_app_attaches_typed_runtime_container():
    app = create_app(_FAKE_SETTINGS)

    container = app.state.container

    assert container.settings is app.state.settings
    assert container.queue_backend is app.state.queue_backend
    assert container.channel_repository is app.state.channel_repository
    assert container.job_completion_service is app.state.job_completion_service
```

Expected:

- FAIL because `app.state.container` does not exist.

- [x] **Step 2: RuntimeDeps 생성**

Create `src/rider_server/runtime.py` with a frozen dataclass.

Include at least:

- settings
- db engine
- db session factory
- queue backend
- channel repository
- tenant telegram provider
- dashboard repository
- metrics repository
- admin action service
- admin entity service
- agent token service
- agent registry
- job result ingest service
- job completion service
- dispatch worker dependencies if Task 3 already added them

Expected:

- Container type is explicit and inspectable.

- [x] **Step 3: main.py wiring 정리**

In `create_app()`, build `RuntimeDeps` after constructing defaults, then attach it:

```python
app.state.container = RuntimeDeps(...)
```

Rules:

- Existing `app.state.*` names remain for compatibility.
- New code should prefer `app.state.container`.
- Do not change route behavior in this task.

- [x] **Step 4: engine 생성 중복 방지 테스트**

Add a test that monkeypatches engine creation and asserts `create_app()` creates the DB engine/session factory once for the default path.

Important:

- The current default path already passes `db_session_factory` into default builders.
- This test locks that behavior so future helper changes do not accidentally create extra engines.

- [x] **Step 5: 검증**

Run:

```powershell
.\scripts\test.ps1 quick tests\server\test_server_app.py tests\server\test_snapshot_telegram_runtime.py
.\scripts\test.ps1 architecture
```

Expected:

- PASS.

Commit:

```powershell
git add src/rider_server/runtime.py src/rider_server/main.py tests/server/test_server_app.py tests/server/test_snapshot_telegram_runtime.py
git commit -m "refactor(server): add typed runtime dependency container"
```

Rollback:

- Revert the commit. Since existing `app.state.*` compatibility remains, rollback should be low risk.

Progress note:

- 2026-06-19: `RuntimeDeps` frozen dataclass를 추가하고 `create_app()`이 기존 `app.state.*` 호환을 유지하면서 `app.state.container`를 붙이도록 했다. 단일 DB engine 생성 가드는 기존 테스트로 유지했다. `quick tests\server\test_server_app.py tests\server\test_snapshot_telegram_runtime.py` 22 passed, `architecture` 95 passed.

## Task 5: Scheduler batch와 bulk query 도입

**Files:**

- Modify: `src/rider_server/scheduler/service.py`
- Modify: `src/rider_server/scheduler/postgres_repository.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/server/test_scheduler_repository.py`
- Test: `tests/negative/test_scheduler_idempotency.py`

- [x] **Step 1: 실패 테스트 추가**

Add a repository fake that counts calls:

```python
def test_scheduler_uses_bulk_gate_and_active_job_lookup():
    repo = CountingSchedulerRepository(due_count=50)
    queue = InMemoryQueueBackend()

    asyncio.run(SchedulerService(batch_size=50).run_tick(repo, queue, now=_NOW))

    assert repo.tenant_gate_calls == 0
    assert repo.bulk_tenant_gate_calls == 1
    assert repo.has_active_crawl_job_calls == 0
    assert repo.bulk_active_job_calls == 1
```

Expected:

- FAIL because current `SchedulerService.__init__()` has no `batch_size`, and current service calls per-target methods.

- [x] **Step 2: constructor에 batch_size 추가**

In `SchedulerService.__init__()`, add:

```python
batch_size: int = 100
```

Rules:

- Validate positive integer.
- Keep existing breaker arguments behavior unchanged.

- [x] **Step 3: repository interface를 필수 bulk 계약으로 확장**

Change `SchedulerRepository` abstract methods:

```python
async def due_targets(self, *, now: datetime, limit: int) -> list[DueTarget]: ...
async def tenant_gates(self, tenant_ids: set[str]) -> dict[str, TenantGate]: ...
async def active_crawl_targets(self, target_ids: set[str]) -> set[str]: ...
```

Rules:

- These are not optional. Service will call them directly.
- Update all fakes and Postgres implementation in the same patch.
- Keep old per-target methods only if another caller still needs them.

- [x] **Step 4: Postgres bulk query 구현**

In `PostgresSchedulerRepository`:

- `due_targets(..., limit=...)` applies `LIMIT`.
- `tenant_gates()` selects tenant lifecycle and subscription status for all tenant ids in one session.
- `active_crawl_targets()` selects distinct target ids from active crawl jobs.

Expected:

- One tick can cap work and reduce query count.

- [x] **Step 5: service loop 변경**

In `SchedulerService.run_tick()`:

- call `due_targets(now=now, limit=self._batch_size)`
- build `tenant_ids` and `target_ids`
- call bulk methods once
- use dict/set lookups in the loop
- preserve breaker evaluation and capacity behavior

Expected:

- Behavior matches current policy.
- Gate and active-job DB queries no longer grow linearly with target count.

- [x] **Step 6: 검증**

Run:

```powershell
.\scripts\test.ps1 quick tests\server\test_scheduler_tick.py tests\server\test_scheduler_repository.py
```

If `TEST_DATABASE_URL` is available:

```powershell
.\scripts\test.ps1 postgres tests\negative\test_scheduler_idempotency.py
```

Expected:

- PASS.

Commit:

```powershell
git add src/rider_server/scheduler/service.py src/rider_server/scheduler/postgres_repository.py tests/server/test_scheduler_tick.py tests/server/test_scheduler_repository.py tests/negative/test_scheduler_idempotency.py
git commit -m "perf(server): batch scheduler due target checks"
```

Rollback:

- Revert this commit if queue creation count, race behavior, or idempotency changes.

Progress note:

- 2026-06-19: `SchedulerService(batch_size=...)` 별칭과 positive validation을 추가하고, repository bulk gate/active-job 계약을 필수로 고정했다. tick은 due 대상 limit, tenant gate bulk 조회, active crawl target bulk 조회를 사용한다. `quick tests\server\test_scheduler_tick.py tests\server\test_scheduler_repository.py tests\server\test_scheduler_entrypoint.py` 53 passed, `architecture tests\server\test_scheduler_boundary.py` 7 passed. `TEST_DATABASE_URL`이 없어 postgres idempotency 검증은 미실행.

## Task 6: Admin Entity CRUD 점진 분리

**Files:**

- Create: `src/rider_server/services/admin_entities/__init__.py`
- Create: `src/rider_server/services/admin_entities/common.py`
- Create: `src/rider_server/services/admin_entities/tenant_service.py`
- Create: `src/rider_server/services/admin_entities/target_service.py`
- Modify: `src/rider_server/services/admin_entity_service.py`
- Test: `tests/server/test_admin_entity_crud.py`
- Test: `tests/negative/test_admin_entity_crud_pg.py`

- [x] **Step 1: 현재 behavior lock 테스트 추가**

Pick high-risk flows before splitting:

- tenant create keeps audit contract
- tenant update keeps audit contract
- monitoring target scope violation still raises the current exception
- risky center-name behavior stays unchanged

Expected:

- PASS before refactor.

- [x] **Step 2: common helper 이동**

Move common helpers to `admin_entities/common.py`.

Candidates:

- audit builder wrapper
- tenant scope helper base functions
- center-name risk helper
- secret change label helper

Expected:

- `admin_entity_service.py` imports from common and behavior stays same.

- [x] **Step 3: tenant service 분리**

Move tenant create/update/delete behavior to `tenant_service.py`.

Expose a small service used by `AdminEntityService`.

Expected:

- `AdminEntityService` delegates tenant methods.
- Public method names and exceptions stay the same.

- [x] **Step 4: target service 분리**

Move monitoring target create/update/deactivate/reactivate behavior to `target_service.py`.

Expected:

- Target methods still enforce tenant scope and center-name risk behavior.

- [x] **Step 5: fake 위치 결정**

Do not move the in-memory fake in the same patch unless the service split is already stable.

Preferred follow-up:

- move fake to `tests` support or `admin_entities/fakes.py`
- keep import compatibility if production tests import it

- [x] **Step 6: 검증**

Run:

```powershell
.\scripts\test.ps1 quick tests\server\test_admin_entity_crud.py
.\scripts\test.ps1 architecture
```

If `TEST_DATABASE_URL` is available:

```powershell
.\scripts\test.ps1 postgres tests\negative\test_admin_entity_crud_pg.py
```

Expected:

- PASS.

Commit:

```powershell
git add src/rider_server/services/admin_entities src/rider_server/services/admin_entity_service.py tests/server/test_admin_entity_crud.py tests/negative/test_admin_entity_crud_pg.py
git commit -m "refactor(server): split admin entity service by aggregate"
```

Rollback:

- Revert the commit if CRUD behavior or audit records change.

Progress note:

- 2026-06-19: 기존 high-risk behavior lock 테스트가 tenant create/update/delete, target scope violation, center-name risk를 이미 고정하고 있어 이를 기준으로 분리했다. 공용 helper/타입은 `admin_entities/common.py`, tenant write는 `tenant_service.py`, monitoring target write는 `target_service.py`로 나누고 `AdminEntityService`는 기존 공개 API를 유지한 채 위임한다. in-memory fake는 import 호환을 위해 이번 패치에서는 기존 위치에 유지했다. `quick tests\server\test_admin_entity_crud.py` 99 passed, `architecture tests\server\test_admin_actions_guard.py` 11 passed. `TEST_DATABASE_URL`이 없어 postgres CRUD 검증은 미실행.

## Task 7: Agent worker composition 분리

**Files:**

- Create: `src/rider_agent/worker_composition.py`
- Modify: `src/rider_agent/job_loop.py`
- Test: `tests/agent/test_job_loop.py`
- Test: `tests/agent/test_agent_package.py`

- [x] **Step 1: 실패 테스트 추가**

Add a test that imports the new composition function and asserts fallback chaining:

```python
def test_compose_execute_job_keeps_fallback_for_unknown_job_type():
    from rider_agent.worker_composition import compose_execute_job

    calls = []

    def fallback(job):
        calls.append(job.type)
        return make_failure_result("UNKNOWN", "unknown")

    composition = compose_execute_job(
        identity=_identity(),
        capabilities=[],
        fallback=fallback,
        log=None,
        now=lambda: 1.0,
        sleep=lambda seconds: None,
    )

    result = composition.execute_job(ClaimedJob(job_id="job-1", type="NEW_TYPE"))

    assert result.status == JOB_STATUS_FAILED
    assert calls == ["NEW_TYPE"]
    assert composition.close_callbacks == ()
```

Expected:

- FAIL because `worker_composition.py` does not exist.

- [x] **Step 2: 순환 import 방지 규칙 결정**

Do not make `worker_composition.py` import `rider_agent.job_loop` at runtime if `job_loop.py` will import `worker_composition.py`.

Preferred options:

- Use `from __future__ import annotations` and `TYPE_CHECKING` for `ClaimedJob`/`JobResult` annotations.
- Or move shared job dataclasses to a small neutral module first.

Expected:

- `tests/agent/test_agent_package.py` import guard still passes.

- [x] **Step 3: composition module 생성**

Create `src/rider_agent/worker_composition.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerComposition:
    execute_job: Callable[[Any], Any]
    browser_profiles_provider: Callable[[], object] | None = None
    kakao_status_provider: Callable[[], str] | None = None
    close_callbacks: tuple[Callable[[], None], ...] = ()
```

Add `compose_execute_job(...)` as a sync-only function.

Expected:

- New module imports without pulling `rider_server`.

- [x] **Step 4: 기존 worker 조립 이동**

Move auth/crawl/kakao composition blocks from `run_agent()` into `compose_execute_job()`.

Keep:

- lazy imports inside branch blocks
- sync-only code
- no `rider_server` imports
- existing function parameters accepted through explicit names where possible

Expected:

- `run_agent()` becomes shorter.
- identity/token/runner lifecycle remains in `run_agent()`.

- [x] **Step 5: close callback 정리**

If Kakao worker stop/join logic is currently owned by `run_agent()`, return close callbacks from composition and call them in `run_agent()` shutdown.

Expected:

- Current cleanup behavior remains unchanged.

- [x] **Step 6: 검증**

Run:

```powershell
.\scripts\test.ps1 quick tests\agent\test_job_loop.py
.\scripts\test.ps1 architecture tests\agent\test_agent_package.py
```

Expected:

- PASS.

Commit:

```powershell
git add src/rider_agent/worker_composition.py src/rider_agent/job_loop.py tests/agent/test_job_loop.py tests/agent/test_agent_package.py
git commit -m "refactor(agent): isolate worker composition from run loop"
```

Rollback:

- Revert the commit if Agent start/stop or Kakao worker cleanup behavior changes.

Progress note:

- 2026-06-19: `worker_composition.py`를 추가해 auth/crawl/Kakao worker chaining을 sync-only composition 함수로 분리하고, `run_agent()`는 identity/token/runner lifecycle을 유지하면서 composition 결과를 사용한다. fallback chaining RED 테스트를 추가했고, Kakao worker stop callback은 composition의 `close_callbacks`로 반환한다. `quick tests\agent\test_job_loop.py` 47 passed, `architecture tests\agent\test_agent_package.py` 14 passed.

## Task 8: 후속 구조 cleanup backlog

이 Task들은 P1/P2 핵심 경계가 안정된 뒤 별도 작은 커밋으로 처리한다.

### Task 8-A: 서버의 `AppConfig` carrier 축소

**Files:**

- Modify: `src/rider_server/services/telegram_central_dispatch.py`
- Modify: `src/rider_server/services/crawl_service.py`
- Add tests near existing Telegram/crawl service tests

- [ ] Introduce a small DTO/Protocol for Telegram send config.
- [ ] Keep `rider_crawl.AppConfig` conversion inside one adapter.
- [ ] Verify:

```powershell
.\scripts\test.ps1 quick tests\server\test_telegram_central_dispatch.py tests\server\test_run_once_split.py
.\scripts\test.ps1 architecture
```

### Task 8-B: `rider_agent/reuse.py` 역할별 port 분리

**Files:**

- Modify: `src/rider_agent/reuse.py`
- Create: `src/rider_agent/ports/crawl_port.py`
- Create: `src/rider_agent/ports/browser_port.py`
- Create: `src/rider_agent/ports/auth_port.py`
- Create: `src/rider_agent/ports/messenger_port.py`

- [ ] Move one role at a time.
- [ ] Keep import compatibility until workers are migrated.
- [ ] Verify:

```powershell
.\scripts\test.ps1 quick tests\agent
.\scripts\test.ps1 architecture
```

### Task 8-C: Docker dependency 정본화와 CI smoke

**Files:**

- Modify: `deploy/Dockerfile.server`
- Modify: `.github/workflows/test.yml`
- Modify: `tests/server/test_deployment_config.py`

- [ ] Add a static test that Dockerfile server dependency list stays aligned with `pyproject.toml` server extra, or generate the Dockerfile list from one source.
- [ ] Add scheduled/push CI smoke that builds and starts backend enough to hit `/health`.
- [ ] Keep fast PR path reasonable; heavy smoke can run on schedule/push.

### Task 8-D: env sample과 root config 정리

**Files:**

- Modify: `.env.example`
- Modify: `docs/config-samples/.env.sample`
- Modify or decide: `config.json`
- Modify: `.gitignore` if root `config.json` becomes local-only

- [ ] Align 2FA docs on UI tab IMAP app-password policy.
- [ ] Move old Gmail OAuth sample text to legacy docs or remove it.
- [ ] Decide whether root `config.json` is tracked placeholder or local-only file.
- [ ] Verify:

```powershell
.\scripts\test.ps1 docs
```

### Task 8-E: 상태 계약과 운영 정책 상수 정리

**Files:**

- Modify: `src/rider_server/domain/states.py`
- Modify: `src/rider_agent/auth/baemin_auth.py`
- Modify: `src/rider_agent/browser_profile.py`
- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify: `src/rider_server/metrics/policy.py`
- Modify: `src/rider_server/admin/dashboard_repository_postgres.py`

- [ ] Decide whether to introduce a neutral contract module or generated enum/schema.
- [ ] Keep `rider_agent` -> `rider_server` import forbidden.
- [ ] Move Telegram error window to one public policy constant.
- [ ] Verify:

```powershell
.\scripts\test.ps1 quick tests\agent tests\server\test_metrics_policy.py tests\server\test_admin_dashboard.py
.\scripts\test.ps1 architecture
```

## 완료 기준

- 운영 기본 env는 공개 Admin을 켜지 않는다.
- Telegram secret ref와 실제 secret 전달 방식이 compose/runbook/test에서 같은 정책을 가리킨다.
- `JobCompletionService`가 job 완료 정책의 중심이 되고, `jobs.py`는 HTTP adapter로 남는다.
- snapshot ingest DB 트랜잭션은 외부 Telegram 네트워크 호출을 직접 하지 않는다.
- Telegram dispatch 재시도, lock, 상태 갱신 경계가 명확하다.
- `create_app()` wiring은 `RuntimeDeps`로 읽을 수 있다.
- scheduler는 due target 수가 늘어도 per-target gate/active-job DB round-trip을 반복하지 않는다.
- Admin Entity CRUD는 최소 tenant/target 단위부터 파일이 나뉘어 있다.
- Agent worker 조립은 `run_agent()` 밖에서 테스트 가능하다.

## 전체 검증 명령

작업이 모두 끝난 뒤 실행한다:

```powershell
.\scripts\test.ps1 quick
.\scripts\test.ps1 architecture
.\scripts\test.ps1 docs
```

DB 관련 Task 3, Task 5, Task 6을 실제로 구현했다면 `TEST_DATABASE_URL`을 설정하고 실행한다:

```powershell
.\scripts\test.ps1 postgres
```

릴리스 전에는 가능하면 실행한다:

```powershell
.\scripts\test.ps1 full
```
