# 5-10 Review Follow-up Implementation Plan

> 적용 완료: 2026-06-20에 본 작업지시서의 보안, Agent 안정성, dispatch/queue/scheduler/metrics, Admin 운영 안전, DB/migration/CI 항목을 코드·테스트·문서에 반영했다. 적용 후 전체 pytest와 배포 config 검증을 통과했다.

> 정합성 주의(2026-06-21 갱신): 본문 Task 체크박스(`- [ ]`)는 추적용 양식이며, 실제 반영 여부는 각 Task 의 "검증" 명령과 현재 코드/테스트를 기준으로 본다(상단 "적용 완료" 기준). 2026-06-21 후속 검증에서 두 결함을 추가로 고쳤다: (1) Task 2 dispatch worker 의 `apply_update` 가 `sqlalchemy.update` 를 섀도잉해 실전송 시 TypeError 로 깨지던 버그(파라미터를 `update_values` 로 변경, 실본문 실행 회귀 테스트 추가), (2) Task 4 atomic snapshot complete 경로가 `completion_id` 를 무시해 outbox replay 가 멱등 200 대신 LEASE_LOST 로 처리되던 갭(멱등 분기 추가, PG-gated 멱등 테스트 추가).

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for independent task slices, or `superpowers:executing-plans` if one worker executes this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 5-10번 검토에서 나온 보안, 중복 전송, Agent 안정성, 100+ job 확장성, Admin 운영 안전 문제를 실행 가능한 작업 단위로 고친다.

**Architecture:** 기존 FastAPI, SQLAlchemy async, PostgreSQL queue, Agent lease 모델은 유지한다. 보안은 fail-closed로 바꾸고, side effect가 있는 전송/운영 action은 DB 상태와 audit이 먼저 안전하게 기록되도록 만든다. 100개 이상 job에서는 batch limit, type별 capacity, 명확한 timestamp, DB index로 부하를 낮춘다.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, SQLAlchemy async, PostgreSQL, Alembic, pytest, Docker Compose, Windows Agent runtime.

---

## 작업 원칙

- 먼저 실패 테스트를 만들고 실패를 확인한 뒤 구현한다.
- 기존 사용자 변경을 되돌리지 않는다.
- secret 평문을 fixture, 로그, audit, 문서 예시에 새로 넣지 않는다.
- 운영 보안 설정은 unknown이면 fail-closed 또는 startup failure로 처리한다.
- Telegram, Kakao, browser crawl처럼 외부 side effect가 있는 작업은 "성공인지 모르는 상태"를 자동 성공이나 자동 재시도로 처리하지 않는다.
- 각 task는 가능한 작은 commit 단위로 끝낸다.

## 전체 완료 기준

- production에서 public admin access가 켜지면 앱이 뜨지 않는다.
- untrusted `X-Forwarded-For`로 admin IP allowlist를 우회할 수 없다.
- Telegram send 성공 후 DB update 전 worker가 죽어도 같은 delivery가 자동 재전송되지 않는다.
- Agent PC에서 register/run 동시 실행이 identity, token, profile 상태를 꼬이게 만들지 않는다.
- browser crawl timeout은 실제 child process와 Chrome 작업을 종료한다.
- `send_only_on_change=True` rule은 변경 없는 snapshot을 발송하지 않는다.
- CI deployment config gate가 dummy secret 환경에서 통과한다.
- 운영 문서에 `agreg_...` registration code가 남지 않는다.
- stale recovery, scheduler admission, metrics, DB index가 100+ job 운영 기준을 가진다.
- Admin action은 audit과 상태 변경이 어긋나지 않고, 위험 action은 server-side confirmation을 요구한다.

---

## Task 0: 긴급 비밀값 문서 제거와 CI config gate 복구

**Intent:** 구현 전에도 바로 줄일 수 있는 운영 위험과 CI 실패를 먼저 닫는다.

**Files:**

- Modify: `docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md`
- Modify: `.github/workflows/test.yml`
- Optional Modify: `scripts/test.ps1`

- [ ] **Step 1: registration code 문서 노출 실패 검사를 실행한다**

Run:

```powershell
rg -n "agreg_[A-Za-z0-9_-]+" docs deploy src tests
```

Expected before implementation:

```text
docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md:<line>: agreg_...
```

- [ ] **Step 2: 문서의 실제 registration code를 placeholder로 바꾼다**

Required replacement:

```markdown
<AGENT_REGISTRATION_CODE>
```

Do not keep the original value in comments, history notes, examples, or code blocks inside the document.

- [ ] **Step 3: 운영 절차에 revoke/rotate 지시를 남긴다**

Add a short note near the setup command:

```markdown
> 운영 주의: registration code는 일회성 비밀값이다. 실제 값을 문서에 저장하지 않는다. 노출되면 서버에서 즉시 revoke/rotate한 뒤 새 값을 안전한 채널로 전달한다.
```

- [ ] **Step 4: CI compose config 실패를 재현한다**

Run:

```powershell
$env:RIDER_POSTGRES_PASSWORD='rider'
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED='1'
docker compose -f deploy/docker-compose.yml config
```

Expected before implementation:

```text
RIDER_TELEGRAM_WEBHOOK_SECRET is missing a value
```

- [ ] **Step 5: `.github/workflows/test.yml` deployment-config env에 dummy secret을 넣는다**

Add non-secret dummy values only in the config validation step:

```yaml
          RIDER_TELEGRAM_WEBHOOK_SECRET: ci_dummy_webhook_secret
          RIDER_TELEGRAM_BOT_TOKEN: ci_dummy_bot_token
```

- [ ] **Step 6: 검증한다**

Run:

```powershell
rg -n "agreg_[A-Za-z0-9_-]+" docs deploy src tests

$env:RIDER_POSTGRES_PASSWORD='rider'
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED='1'
$env:RIDER_TELEGRAM_WEBHOOK_SECRET='ci_dummy_webhook_secret'
$env:RIDER_TELEGRAM_BOT_TOKEN='ci_dummy_bot_token'
docker compose -f deploy/docker-compose.yml config
```

Expected:

```text
rg exits with no matches.
docker compose config exits 0.
```

- [ ] **Step 7: Commit**

```powershell
git add docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md .github/workflows/test.yml
git commit -m "chore: remove exposed agent code and fix compose config gate"
```

---

## Task 1: Admin public access, XFF, boolean env fail-closed

**Intent:** production 보안 경계가 설정 실수와 spoofed header로 열리지 않게 한다.

**Files:**

- Modify: `src/rider_server/settings.py`
- Modify: `src/rider_server/main.py`
- Modify: `src/rider_server/security/access.py`
- Test: `tests/server/test_admin_security.py`
- Test: `tests/server/test_settings.py` or existing settings test file

- [ ] **Step 1: production public admin 실패 테스트를 추가한다**

Add test:

```python
def test_public_admin_access_rejected_in_production():
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://user:pass@db:5432/rider",
        admin_public_access=True,
    )

    with pytest.raises(RuntimeError, match="RIDER_ADMIN_PUBLIC_ACCESS"):
        create_app(settings=settings)
```

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_security.py::test_public_admin_access_rejected_in_production -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: malformed boolean env 실패 테스트를 추가한다**

Add tests:

```python
def test_malformed_admin_mfa_required_fails_closed():
    with pytest.raises(ValueError, match="RIDER_ADMIN_MFA_REQUIRED"):
        Settings.from_env({"RIDER_ADMIN_MFA_REQUIRED": "ture"})


def test_malformed_public_access_fails_closed():
    with pytest.raises(ValueError, match="RIDER_ADMIN_PUBLIC_ACCESS"):
        Settings.from_env({"RIDER_ADMIN_PUBLIC_ACCESS": "treu"})
```

Run the two tests and confirm failure before implementation.

- [ ] **Step 3: untrusted XFF allowlist bypass 실패 테스트를 추가한다**

Required behavior:

- request client host is not allowlisted
- request includes `X-Forwarded-For` with allowlisted IP
- access is denied unless request comes from trusted proxy

Test shape:

```python
def test_admin_allowlist_does_not_trust_xff_without_trusted_proxy(client):
    app = client.app
    app.state.admin_ip_allowlist = ("203.0.113.10",)
    app.state.trusted_proxy_cidrs = ()
    app.state.resolve_admin_principal = lambda request: AdminPrincipal(
        actor_id="admin",
        role=AdminRole.SECRET_ADMIN,
        mfa_verified=True,
        source="test",
    )

    response = client.get("/admin", headers={"X-Forwarded-For": "203.0.113.10"})

    assert response.status_code == 403
```

- [ ] **Step 4: 구현한다**

Required implementation:

- `Settings.from_env()` passes env var name into bool parser.
- `_env_bool()` accepts only explicit truthy and falsy values.
- unknown boolean value raises `ValueError`.
- `create_app()` calls a production guard that rejects `admin_public_access=True` when `APP_ENV=production`.
- `source_ip()` uses `request.client.host` unless trusted proxy support is explicitly configured.

- [ ] **Step 5: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_admin_security.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_settings.py
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```powershell
git add src/rider_server/settings.py src/rider_server/main.py src/rider_server/security/access.py tests/server/test_admin_security.py tests/server/test_settings.py
git commit -m "fix: fail closed admin security settings"
```

---

## Task 2: Telegram outbox ambiguous send state

**Intent:** Telegram이 메시지를 받았을 수 있는 상태에서 worker crash나 lock expiry가 생겨도 자동 중복 전송하지 않는다.

**Files:**

- Modify: `src/rider_server/db/models/messaging.py`
- Modify: `src/rider_server/domain/states.py` or delivery status enum location
- Modify: `src/rider_server/services/dispatch_worker.py`
- Modify: `src/rider_server/services/snapshot_repository_postgres.py`
- Create: `migrations/versions/0017_delivery_send_attempt_state.py`
- Test: `tests/server/test_dispatch_worker.py`
- Test: `tests/server/test_snapshot_repository_postgres.py`

- [ ] **Step 1: crash-after-send 실패 테스트를 추가한다**

Test intent:

```python
async def test_delivery_not_auto_retried_after_send_attempt_started(session_factory):
    # Arrange a RETRYING Telegram delivery.
    # Use a fake sender that records one send and then raises SystemExit or a synthetic crash before apply_update.
    # After lock timeout, run claim_pending again.
    # Expected: the row is HELD or UNKNOWN and is not returned for automatic retry.
```

Required assertions:

- fake sender called once
- delivery row has `send_attempted_at` or `status in {"SENDING", "UNKNOWN", "HELD"}`
- second worker does not claim the row for send

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_dispatch_worker.py::test_delivery_not_auto_retried_after_send_attempt_started -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: locked ownership update 실패 테스트를 추가한다**

Required behavior:

- `apply_update()` updates only when `id`, `locked_by`, and expected in-flight state match.
- stale worker update after another worker claimed the row affects 0 rows and raises conflict or returns false.

- [ ] **Step 3: migration을 만든다**

Required schema:

- Add `send_attempted_at TIMESTAMPTZ NULL`.
- Add `last_failed_at TIMESTAMPTZ NULL` if Task 8 is not already adding it.
- Add status value support for `SENDING` or `UNKNOWN` if statuses are string based.

- [ ] **Step 4: dispatch flow를 바꾼다**

Required behavior:

1. `claim_pending()` claims only safe-to-send states.
2. Before calling Telegram, worker persists `send_attempted_at=now`, `status=SENDING`, `locked_by=current_worker`.
3. If Telegram call returns success, row becomes `SENT`.
4. If Telegram call returns clear retryable failure before Telegram acceptance, row may become `RETRYING`.
5. If process dies or lock expires after `send_attempted_at`, recovery marks it `HELD/UNKNOWN`, not `RETRYING`.

- [ ] **Step 5: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_dispatch_worker.py tests/server/test_snapshot_repository_postgres.py
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```powershell
git add src/rider_server/db/models/messaging.py src/rider_server/services/dispatch_worker.py src/rider_server/services/snapshot_repository_postgres.py migrations/versions/0017_delivery_send_attempt_state.py tests/server/test_dispatch_worker.py tests/server/test_snapshot_repository_postgres.py
git commit -m "fix: prevent ambiguous telegram resend"
```

---

## Task 3: `send_only_on_change` runtime 적용

**Intent:** 변경 없는 snapshot은 change-only delivery rule에서 발송하지 않는다.

**Files:**

- Modify: `src/rider_server/services/snapshot_repository_postgres.py`
- Test: `tests/server/test_snapshot_repository_postgres.py`

- [ ] **Step 1: 실패 테스트를 추가한다**

Required setup:

- same tenant, target, channel, template
- first snapshot creates delivery
- second snapshot has same normalized message hash and `send_only_on_change=True`

Required assertions:

- second snapshot creates no new delivery log
- second snapshot still stores snapshot/message if current model requires it

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_snapshot_repository_postgres.py::test_send_only_on_change_skips_unchanged_delivery -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: implementation을 추가한다**

Required behavior:

- `_enqueue_delivery_logs()` checks `rule.send_only_on_change`.
- For those rules, load latest previous delivery/message hash by target/channel/template.
- If previous hash equals current `message.text_hash`, skip insert.
- Query must be batch-friendly and not do one DB roundtrip per rule.

- [ ] **Step 3: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_snapshot_repository_postgres.py
```

Expected:

```text
passed
```

- [ ] **Step 4: Commit**

```powershell
git add src/rider_server/services/snapshot_repository_postgres.py tests/server/test_snapshot_repository_postgres.py
git commit -m "fix: honor change-only delivery rules"
```

---

## Task 4: Atomic snapshot completion metadata 저장

**Intent:** atomic ingest path도 normal queue complete path와 같은 completion metadata를 남긴다.

**Files:**

- Modify: `src/rider_server/services/job_completion_service.py`
- Modify: `src/rider_server/services/snapshot_repository_postgres.py`
- Test: `tests/server/test_job_completion_service.py`
- Test: `tests/server/test_snapshot_repository_postgres.py`

- [ ] **Step 1: 실패 테스트를 추가한다**

Required assertions:

- successful atomic snapshot completion sets `completed_at`
- sets `duration_ms`
- sets `result_schema_version`
- failed terminal status sets `last_failed_at`

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_snapshot_repository_postgres.py::test_atomic_snapshot_completion_persists_completion_metadata -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: service interface를 확장한다**

Required behavior:

- `JobCompletionService` computes duration and schema exactly once.
- `_atomic_complete()` passes `duration_ms` and `result_schema_version`.
- `complete_snapshot_job()` persists the same metadata as `PostgresQueueBackend.complete()`.

- [ ] **Step 3: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_job_completion_service.py tests/server/test_snapshot_repository_postgres.py
```

Expected:

```text
passed
```

- [ ] **Step 4: Commit**

```powershell
git add src/rider_server/services/job_completion_service.py src/rider_server/services/snapshot_repository_postgres.py tests/server/test_job_completion_service.py tests/server/test_snapshot_repository_postgres.py
git commit -m "fix: persist atomic job completion metadata"
```

---

## Task 5: Agent register/run single-instance와 절대 runtime path

**Intent:** 같은 PC에서 동시 실행이 identity, token, profile, log 위치를 꼬이게 만들지 않는다.

**Files:**

- Modify: `src/rider_agent/registration.py`
- Modify: `src/rider_agent/secure_store.py`
- Modify: `src/rider_agent/__main__.py`
- Modify: `src/rider_agent/autostart.py`
- Modify: `src/rider_agent/worker_composition.py`
- Modify: `src/rider_agent/workers/crawl_worker.py`
- Test: `tests/agent/test_registration.py`
- Test: `tests/agent/test_job_loop.py`
- Test: `tests/agent/test_autostart.py`

- [ ] **Step 1: registration lock 실패 테스트를 추가한다**

Required behavior:

- two concurrent registration calls share the same state dir
- only one POST is accepted or only one final identity/token pair is saved
- stored config and token belong to the same agent

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_registration.py::test_concurrent_registration_preserves_identity_token_pair -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: run single-instance 실패 테스트를 추가한다**

Required behavior:

- first run holds lock
- second run returns not started
- second run does not claim jobs

- [ ] **Step 3: autostart cwd 실패 테스트를 추가한다**

Required behavior:

- generated `.cmd` either changes to install dir with `cd /d` or the runtime paths are absolute
- logs/profile paths do not depend on current working directory

- [ ] **Step 4: 구현한다**

Required implementation:

- Introduce a small lock helper in `rider_agent` using Windows named mutex when available, with file lock fallback for tests.
- Wrap registration and run startup with the lock.
- Use `app_state_root()` for logs and browser profiles.
- Do not silently change `max_profiles` to `max_jobs`; validate and fail or clamp with a clear message.

- [ ] **Step 5: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/agent/test_registration.py tests/agent/test_job_loop.py tests/agent/test_autostart.py
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```powershell
git add src/rider_agent tests/agent/test_registration.py tests/agent/test_job_loop.py tests/agent/test_autostart.py
git commit -m "fix: guard agent identity and runtime paths"
```

---

## Task 6: Browser crawl hard timeout/process boundary

**Intent:** stuck browser crawl이 timeout 후에도 PC에 남지 않게 한다.

**Files:**

- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify: `src/rider_agent/workers/crawl_process.py`
- Modify: `src/rider_agent/browser_profile.py`
- Test: `tests/agent/test_crawl_worker.py`
- Test: `tests/agent/test_browser_profile.py`

- [ ] **Step 1: stateful crawl timeout 실패 테스트를 추가한다**

Required behavior:

- worker has profile manager and secret resolver
- crawl function blocks longer than timeout
- timeout kills process boundary
- no background thread continues after result

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_crawl_worker.py::test_stateful_crawl_timeout_kills_process_boundary -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: browser profile recovery handle 테스트를 추가한다**

Required behavior:

- `recover_profile()` stores the new process handle
- `close_all()` closes recovered profile process

- [ ] **Step 3: 구현한다**

Required implementation:

- Stateful crawl runs in subprocess/process boundary too.
- Payload, resolved secret refs, and profile assignment are passed safely to the child.
- Timeout kills child process group and closes Chrome.
- Thread timeout path is not used for browser crawl.
- `recover_profile()` uses the same process-capturing wrapper as initial launch.

- [ ] **Step 4: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/agent/test_crawl_worker.py tests/agent/test_browser_profile.py
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```powershell
git add src/rider_agent/workers/crawl_worker.py src/rider_agent/workers/crawl_process.py src/rider_agent/browser_profile.py tests/agent/test_crawl_worker.py tests/agent/test_browser_profile.py
git commit -m "fix: enforce crawl timeout process boundary"
```

---

## Task 7: Queue stale recovery batch와 claim index

**Intent:** 100개 이상 stale/pending job에서 recovery와 claim latency를 안정화한다.

**Files:**

- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/settings.py`
- Modify: `src/rider_server/db/models/agent.py`
- Create: `migrations/versions/0018_jobs_claim_index_and_recovery_batch.py`
- Test: `tests/server/test_queue_backend.py`
- Test: `tests/server/test_db_schema.py`

- [ ] **Step 1: stale recovery batch 실패 테스트를 추가한다**

Required behavior:

- create more stale jobs than batch size
- first recovery call updates exactly batch size
- second call updates next batch

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_backend.py::test_recover_stale_respects_batch_size -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: claim index schema 테스트를 추가한다**

Required assertion:

- `jobs` has index covering status, type, run_after, id or an equivalent pending partial index.

- [ ] **Step 3: 구현한다**

Required implementation:

- Add `job_recovery_batch_size` setting with safe default.
- `recover_stale()` orders by `lease_expires_at`, `id` and limits by batch size.
- Migration creates claim-focused index.
- Keep existing stale lease index unless query plan proves it redundant.

- [ ] **Step 4: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_queue_backend.py tests/server/test_db_schema.py tests/server/test_migration.py
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```powershell
git add src/rider_server/queue/postgres_queue.py src/rider_server/settings.py src/rider_server/db/models/agent.py migrations/versions/0018_jobs_claim_index_and_recovery_batch.py tests/server/test_queue_backend.py tests/server/test_db_schema.py
git commit -m "perf: batch stale recovery and add claim index"
```

---

## Task 8: Scheduler capacity by job type and metrics timestamp

**Intent:** scheduler와 metrics가 실제 운영 상태를 더 정확히 반영하게 한다.

**Files:**

- Modify: `src/rider_server/scheduler/postgres_repository.py`
- Modify: `src/rider_server/scheduler/policy.py`
- Modify: `src/rider_server/metrics/repository_postgres.py`
- Modify: `src/rider_server/admin/dashboard_repository_postgres.py`
- Modify: `src/rider_server/db/models/messaging.py`
- Create: `migrations/versions/0019_delivery_failure_timestamps.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/negative/test_metrics_repository_pg.py`
- Test: `tests/server/test_admin_dashboard.py`

- [ ] **Step 1: capacity by job type 실패 테스트를 추가한다**

Required scenario:

- BAEMIN agent has free capacity
- COUPANG agent has no free capacity
- due target needs COUPANG
- scheduler does not enqueue COUPANG job

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py::test_scheduler_admits_by_job_type_capacity -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: Telegram error metric window 실패 테스트를 추가한다**

Required behavior:

- old failed delivery with `sent_at=None` and old `last_failed_at` is not counted in recent window
- recent failed delivery is counted

- [ ] **Step 3: 구현한다**

Required implementation:

- Capacity snapshot includes per job type capacity and in-flight counts.
- Scheduler policy checks the specific job type.
- Delivery failure update sets `last_failed_at`.
- Metrics count `last_failed_at >= since`, not `sent_at IS NULL`.
- Dashboard channel health uses the same timestamp rule.

- [ ] **Step 4: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_scheduler_tick.py tests/negative/test_metrics_repository_pg.py tests/server/test_admin_dashboard.py
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```powershell
git add src/rider_server/scheduler src/rider_server/metrics src/rider_server/admin/dashboard_repository_postgres.py src/rider_server/db/models/messaging.py migrations/versions/0019_delivery_failure_timestamps.py tests/server/test_scheduler_tick.py tests/negative/test_metrics_repository_pg.py tests/server/test_admin_dashboard.py
git commit -m "fix: use precise capacity and failure metrics"
```

---

## Task 9: Admin action atomic audit, rowcount conflict, confirmation

**Intent:** 운영 action이 실제 DB 상태, audit, 사용자 확인 절차와 어긋나지 않게 한다.

**Files:**

- Modify: `src/rider_server/services/admin_action_service.py`
- Modify: `src/rider_server/services/admin_action_repository_postgres.py`
- Modify: `src/rider_server/services/admin_entity_repository_postgres.py`
- Modify: `src/rider_server/admin/actions_routes.py`
- Modify: `src/rider_server/admin/templates/_targets.html`
- Modify: `src/rider_server/admin/templates/_actions.html`
- Test: `tests/server/test_admin_actions.py`
- Test: `tests/server/test_admin_entity_crud.py`

- [ ] **Step 1: enqueue+audit atomic 실패 테스트를 추가한다**

Required behavior:

- repo audit insert fails
- manual job is not left in queue
- API returns failure

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_actions.py::test_manual_enqueue_rolls_back_when_audit_fails -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: duplicate manual job 실패 테스트를 추가한다**

Required behavior:

- same target/job_type has active manual job
- second click returns conflict and does not enqueue another job

- [ ] **Step 3: rowcount 0 conflict 테스트를 추가한다**

Required behavior:

- update/delete affects 0 rows
- service returns not-found or conflict
- success audit is not written

- [ ] **Step 4: server-side confirmation 테스트를 추가한다**

Required behavior:

- direct POST for destructive or queue-spawning action without confirmation marker is rejected
- POST with confirmation marker and correct role succeeds

- [ ] **Step 5: 구현한다**

Required implementation:

- Add repository method that creates manual job and audit in one transaction.
- Add active manual job uniqueness or cooldown check.
- Check SQLAlchemy update/delete `rowcount`.
- Route requires confirmation marker or reason for risky actions.
- Templates send the marker through HTMX form data.

- [ ] **Step 6: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_admin_actions.py tests/server/test_admin_entity_crud.py
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit**

```powershell
git add src/rider_server/services/admin_action_service.py src/rider_server/services/admin_action_repository_postgres.py src/rider_server/services/admin_entity_repository_postgres.py src/rider_server/admin/actions_routes.py src/rider_server/admin/templates tests/server/test_admin_actions.py tests/server/test_admin_entity_crud.py
git commit -m "fix: make admin actions auditable and confirmed"
```

---

## Task 10: Admin dashboard fragment failure and target severity ordering

**Intent:** 운영 화면이 DB 장애와 100+ target 상태를 정확히 보여준다.

**Files:**

- Modify: `src/rider_server/admin/routes.py`
- Modify: `src/rider_server/admin/dashboard_repository_postgres.py`
- Create/Modify: `src/rider_server/admin/templates/_db_failure_fragment.html`
- Test: `tests/server/test_admin_dashboard.py`

- [ ] **Step 1: HTMX fragment DB failure 실패 테스트를 추가한다**

Required behavior:

- targets/agents/channels/auth-required fragment repository raises DB error
- response status is 503
- response is safe HTML partial, not traceback JSON

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py::test_targets_fragment_db_failure_returns_safe_partial -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: severity ordering 실패 테스트를 추가한다**

Required behavior:

- more than 100 targets
- critical target would be outside name/id first page
- first fragment still includes critical target or critical bucket

- [ ] **Step 3: 구현한다**

Required implementation:

- Add shared fragment error wrapper.
- Add `_db_failure_fragment.html`.
- Move severity priority into repository/read-model query, or add critical-target prefetch bucket before normal pagination.

- [ ] **Step 4: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_admin_dashboard.py
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```powershell
git add src/rider_server/admin/routes.py src/rider_server/admin/dashboard_repository_postgres.py src/rider_server/admin/templates/_db_failure_fragment.html tests/server/test_admin_dashboard.py
git commit -m "fix: harden admin dashboard fragments"
```

---

## Task 11: API body bounds and job event ownership

**Intent:** Agent API가 큰 payload와 cross-agent event pollution을 막는다.

**Files:**

- Modify: `src/rider_server/api/agents.py`
- Modify: `src/rider_server/api/jobs.py`
- Modify: `src/rider_server/queue/backend.py`
- Modify: `src/rider_server/queue/postgres_queue.py`
- Test: `tests/server/test_agents_api.py`
- Test: `tests/server/test_jobs_api.py`

- [ ] **Step 1: oversized heartbeat 실패 테스트를 추가한다**

Required behavior:

- `browser_profiles` too long returns 422
- huge metrics dict returns 422
- normal heartbeat still succeeds

- [ ] **Step 2: oversized job complete/event 실패 테스트를 추가한다**

Required behavior:

- too-large `result_json` or `artifact_refs` returns 422
- redacted normal event succeeds

- [ ] **Step 3: cross-agent event ownership 실패 테스트를 추가한다**

Required behavior:

- agent B token posts event to job claimed by agent A
- route returns 404/409 or no-op without writing event

- [ ] **Step 4: 구현한다**

Required implementation:

- Add Pydantic bounds for list/dict/string fields.
- Add backend method for `emit_event` with ownership/live lease check.
- Postgres implementation locks or checks job owner/status/lease before insert.

- [ ] **Step 5: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_agents_api.py tests/server/test_jobs_api.py
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```powershell
git add src/rider_server/api/agents.py src/rider_server/api/jobs.py src/rider_server/queue/backend.py src/rider_server/queue/postgres_queue.py tests/server/test_agents_api.py tests/server/test_jobs_api.py
git commit -m "fix: bound agent api input and event ownership"
```

---

## Task 12: Browser profile and Kakao active-room DB constraints

**Intent:** 동시 운영 action이나 여러 Agent 상황에서도 DB가 핵심 중복을 막는다.

**Files:**

- Modify: `src/rider_server/db/models/agent.py`
- Modify: `src/rider_server/db/models/messaging.py` or channel model location
- Create: `migrations/versions/0020_profile_and_channel_uniqueness.py`
- Modify: `src/rider_server/services/admin_entity_service.py`
- Modify: `src/rider_server/services/channel_registration.py`
- Test: `tests/server/test_db_schema.py`
- Test: `tests/server/test_admin_entity_crud.py`
- Test: `tests/server/test_channel_registration.py`

- [ ] **Step 1: browser profile uniqueness schema 테스트를 추가한다**

Required constraints:

- unique `(agent_id, target_id)`
- partial unique `(agent_id, cdp_port) WHERE cdp_port IS NOT NULL`

- [ ] **Step 2: active Kakao room uniqueness 테스트를 추가한다**

Required behavior:

- same tenant, same Kakao room, active state cannot be inserted twice
- inactive old row can coexist if intended by lifecycle

- [ ] **Step 3: 구현한다**

Required implementation:

- Add Alembic migration with safe names.
- Add SQLAlchemy model indexes/constraints.
- Map `IntegrityError` to existing duplicate/collision domain error.

- [ ] **Step 4: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_db_schema.py tests/server/test_admin_entity_crud.py tests/server/test_channel_registration.py tests/server/test_migration.py
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```powershell
git add src/rider_server/db/models src/rider_server/services/admin_entity_service.py src/rider_server/services/channel_registration.py migrations/versions/0020_profile_and_channel_uniqueness.py tests/server/test_db_schema.py tests/server/test_admin_entity_crud.py tests/server/test_channel_registration.py
git commit -m "fix: enforce profile and channel uniqueness"
```

---

## Task 13: Migration 0015 legacy delivery guard

**Intent:** migration 후 redacted preview가 실전송 본문으로 나가는 일을 막는다.

**Files:**

- Modify: `migrations/versions/0015_delivery_outbox.py`
- Modify: `docs/runbooks/**` or relevant migration runbook
- Test: `tests/server/test_migration.py`

- [ ] **Step 1: pending delivery guard 실패 테스트를 추가한다**

Required behavior:

- if legacy delivery rows are pending/retryable and only redacted preview exists, migration fails with clear message or converts them to `HELD`
- no row becomes sendable with preview text as full message text

- [ ] **Step 2: 구현한다**

Required implementation options:

Option A:

- migration checks for pending/retryable delivery rows and raises clear migration error.

Option B:

- migration marks legacy pending rows `HELD` and records a redacted reason.

Choose one option and document the operator action in runbook.

- [ ] **Step 3: 검증한다**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_migration.py
```

Expected:

```text
passed
```

- [ ] **Step 4: Commit**

```powershell
git add migrations/versions/0015_delivery_outbox.py docs/runbooks tests/server/test_migration.py
git commit -m "fix: guard delivery outbox migration"
```

---

## 최종 통합 검증

모든 task가 끝난 뒤 실행한다.

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_admin_security.py tests/server/test_agents_api.py tests/server/test_jobs_api.py
.venv\Scripts\python.exe -m pytest -q tests/agent/test_registration.py tests/agent/test_job_loop.py tests/agent/test_crawl_worker.py tests/agent/test_autostart.py tests/agent/test_browser_profile.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_dispatch_worker.py tests/server/test_snapshot_repository_postgres.py tests/server/test_job_completion_service.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_scheduler_tick.py tests/server/test_queue_backend.py tests/negative/test_metrics_repository_pg.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_admin_actions.py tests/server/test_admin_dashboard.py tests/server/test_admin_entity_crud.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_db_schema.py tests/server/test_migration.py
```

배포 config:

```powershell
$env:RIDER_POSTGRES_PASSWORD='rider'
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED='1'
$env:RIDER_TELEGRAM_WEBHOOK_SECRET='ci_dummy_webhook_secret'
$env:RIDER_TELEGRAM_BOT_TOKEN='ci_dummy_bot_token'
docker compose -f deploy/docker-compose.yml config
```

비밀값 스캔:

```powershell
rg -n "agreg_[A-Za-z0-9_-]+" docs deploy src tests
rg -n "(bot_token|webhook_secret|password)\s*[:=]\s*['\"][^<'\"]" docs deploy src tests
```

Expected:

```text
All selected pytest commands pass.
docker compose config exits 0.
registration code scan has no matches.
secret scan has no real secret values; false positives are documented placeholders or env var names.
```
