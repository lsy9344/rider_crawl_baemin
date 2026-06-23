# Queue Backlog and Browser Job Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task, or `superpowers:executing-plans` if one worker executes it inline. Keep checkbox state in this document as work lands.

작성일: 2026-06-23  
상태: 작업 전  
대상 저장소: `rider_result_mornitoring`  
근거 문서: `docs/operations/queue-backlog-handling-policy.md`  
검토 근거: 2026-06-23 문서 리뷰와 병렬 서브에이전트 검토 결과

**Goal:** 서버 또는 Agent 재시작 뒤 오래된 브라우저 작업과 stale crawl backlog가 무제한 재실행되지 않게 하고, Coupang 자동 복구는 한 번만 안전하게 허용한다.

**Architecture:** 현재 PostgreSQL job queue와 Agent lease 모델은 유지한다. 1차 구현은 새 job status를 추가하지 않고 `FAILED + safe reason`으로 stale/expired 작업을 닫아 상태 전이 영향 범위를 줄인다. Scheduler는 계정 인증 상태와 자동 복구 cooldown을 보고 job 생성 여부를 결정하고, Agent는 브라우저/profile 준비 전에 서버 preflight와 payload TTL을 확인한다.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, PostgreSQL 16, Alembic, pytest, Windows Agent runtime, Playwright/CDP.

---

## 작업 원칙

- 브라우저를 열 수 있는 작업은 실행 직전에 서버 상태와 TTL을 다시 확인한다.
- `OPEN_AUTH_BROWSER`는 operator intent가 짧게 살아 있는 interactive job으로 취급한다.
- `CRAWL_COUPANG`은 scheduled crawl이며, 인증 복구 버튼으로 사용하지 않는다.
- Coupang 자동 이메일 2FA는 설정이 완전할 때만 한 번 시도하고, 실패 후 cooldown 없이 반복하지 않는다.
- result, audit, log에는 비밀번호, 인증번호, 이메일 앱 비밀번호, secret ref 값을 남기지 않는다.
- 기존 unrelated dirty worktree는 되돌리지 않는다.

## 완료 기준

- `OPEN_AUTH_BROWSER`의 expired `PENDING/CLAIMED/RUNNING` job은 다시 `PENDING`이 되지 않는다.
- `CRAWL_BAEMIN`과 `CRAWL_COUPANG`의 stale scheduled backlog는 target별로 새 작업 하나만 남기거나 안전한 reason으로 닫힌다.
- Scheduler는 `PlatformAccount.auth_state`와 Coupang auto recovery cooldown을 보고 enqueue를 허용/차단한다.
- `AUTH_REQUIRED` Coupang 계정은 자동 이메일 2FA 설정이 완전하고 cooldown이 없을 때만 recovery crawl 1건을 받는다.
- `USER_ACTION_PENDING`, `BLOCKED_OR_CAPTCHA`, `UNKNOWN` 계정은 scheduled crawl을 받지 않는다.
- Agent는 preflight denied 또는 expired payload일 때 browser/profile을 열지 않는다.
- `인증 시작`은 계속 `OPEN_AUTH_BROWSER`만 enqueue한다.

---

## Task 0: 기준선 확인

**Intent:** 현재 동작과 테스트 상태를 기록해 정책 변경으로 생긴 의도적 실패를 구분한다.

**Files:** 없음

- [x] **Step 1: 작업 전 변경 상태 확인**

Run:

```powershell
git status --short
```

Expected:

- 이 작업과 무관한 변경은 기록만 하고 되돌리지 않는다.
- 새 작업 지시서 파일 외 기존 파일 변경은 구현 Task에서만 만든다.

- [x] **Step 2: 관련 테스트 기준선 실행**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_recovery.py tests/server/test_scheduler_tick.py tests/server/test_scheduler_repository.py tests/server/test_admin_actions.py -q
.venv\Scripts\python.exe -m pytest tests/agent/test_job_loop.py tests/agent/test_crawl_worker.py tests/agent/test_baemin_auth.py -q
```

Expected:

- 현재 실패가 있으면 test name과 실패 원인을 이 문서의 구현 기록에 남긴다.

---

## Task 1: job TTL payload vocabulary 추가

**Intent:** server, scheduler, Agent가 같은 시간 필드와 reason vocabulary로 stale 여부를 판단하게 한다.

**Files:**

- Modify: `src/rider_server/services/admin_action_service.py`
- Modify: `src/rider_server/scheduler/service.py`
- Modify: `src/rider_server/queue/states.py`
- Modify: `src/rider_agent/workers/crawl_worker.py`
- Test: `tests/server/test_admin_actions.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/agent/test_crawl_worker.py`

- [x] **Step 1: auth start payload TTL 실패 테스트 추가**

Add test in `tests/server/test_admin_actions.py`:

```python
def test_start_auth_payload_contains_requested_at_and_expires_at() -> None:
    """OPEN_AUTH_BROWSER carries a short operator-intent TTL."""
```

Required assertions:

- `payload_json["job_type"] == "OPEN_AUTH_BROWSER"`.
- `payload_json["requested_at"]` is an ISO timestamp equal to the injected `at`.
- `payload_json["expires_at"]` is `10` to `15` minutes after `requested_at`.
- No `CRAWL_COUPANG` job is created by `start_auth`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_actions.py::test_start_auth_payload_contains_requested_at_and_expires_at -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: scheduled crawl payload TTL 실패 테스트 추가**

Add test in `tests/server/test_scheduler_tick.py`:

```python
def test_scheduler_crawl_payload_contains_scheduled_at_expires_at_and_origin() -> None:
    """Scheduled crawl payloads are bounded and identify their source."""
```

Required assertions:

- `payload_json["job_origin"] == "scheduler"`.
- `payload_json["scheduled_at"]` is the scheduler tick `now`.
- `payload_json["expires_at"]` is at most one interval after `scheduled_at`.
- Coupang payload keeps `coupang_auto_email_2fa_enabled` but does not store verification code values.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py::test_scheduler_crawl_payload_contains_scheduled_at_expires_at_and_origin -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 3: safe reason constants 추가**

Required constants in `src/rider_server/queue/states.py`:

```python
RESULT_REASON_STALE_AUTH_JOB_EXPIRED = "stale_auth_job_expired"
RESULT_REASON_STALE_CRAWL_SKIPPED = "stale_crawl_skipped"
RESULT_REASON_CRAWL_RECOVERY_COOLDOWN = "coupang_auto_recovery_cooldown"
RESULT_REASON_CRAWL_RECOVERY_NOT_ALLOWED = "coupang_auto_recovery_not_allowed"
RESULT_REASON_PAYLOAD_EXPIRED = "payload_expired"
```

Completion criteria:

- Server and Agent tests import these constants instead of duplicating strings.
- The constants contain no tenant, account, email, password, or verification code values.

---

## Task 2: queue recovery가 stale browser/crawl job을 재실행하지 않게 변경

**Intent:** lease가 만료된 browser-opening job이나 오래된 crawl job이 recovery 뒤 다시 실행되지 않게 한다.

**Files:**

- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/queue/memory_queue.py`
- Modify: `src/rider_server/queue/recovery.py`
- Test: `tests/server/test_queue_recovery.py`
- Test: `tests/server/test_queue_backend.py`

- [x] **Step 1: expired `OPEN_AUTH_BROWSER` recovery 실패 테스트 추가**

Add test in `tests/server/test_queue_recovery.py`:

```python
async def test_recovery_expires_open_auth_browser_instead_of_repending() -> None:
    """Expired auth browser jobs are terminal, not replayed after restart."""
```

Required assertions:

- Given a `CLAIMED` or `RUNNING` `OPEN_AUTH_BROWSER` with expired lease and payload `expires_at < now`.
- `recover_once()` returns `recovered_count == 1`.
- The job final status is `FAILED`.
- `job.error_code` is an existing safe failure category.
- `job.result_json["reason"] == "stale_auth_job_expired"`.
- A later `claim()` with `OPEN_AUTH_BROWSER` capability returns no job.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_recovery.py::test_recovery_expires_open_auth_browser_instead_of_repending -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: stale crawl recovery 실패 테스트 추가**

Add test in `tests/server/test_queue_recovery.py`:

```python
async def test_recovery_skips_expired_scheduled_crawl_instead_of_backlog_replay() -> None:
    """Expired scheduled crawls are closed with a safe reason."""
```

Required assertions:

- Given `CRAWL_COUPANG` with `job_origin="scheduler"` and `expires_at < now`.
- Recovery does not set status back to `PENDING`.
- Final status is `FAILED`.
- `result_json["reason"] == "stale_crawl_skipped"`.
- `last_failed_at` or `completed_at` is set to `now`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_recovery.py::test_recovery_skips_expired_scheduled_crawl_instead_of_backlog_replay -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 3: PostgreSQL and memory queue 동작 일치**

Required behavior:

- `PostgresQueueBackend.recover_stale()` and `MemoryQueueBackend.recover_stale()` apply the same stale rules.
- Non-expired retryable jobs keep the existing retry behavior.
- Delivery jobs keep existing idempotent recovery behavior and are not changed by crawl/auth rules.

Verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_recovery.py tests/server/test_queue_backend.py -q
```

---

## Task 3: scheduler auth_state와 Coupang recovery gate 추가

**Intent:** 인증이 필요한 계정에 scheduled crawl이 반복 생성되는 것을 scheduler 단계에서 막는다.

**Files:**

- Modify: `src/rider_server/scheduler/postgres_repository.py`
- Modify: `src/rider_server/scheduler/service.py`
- Modify: `src/rider_server/scheduler/policy.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/server/test_scheduler_repository.py`
- Test: `tests/server/test_scheduler_policy.py`

- [x] **Step 1: `DueTarget`에 auth facts 추가**

Required fields:

```python
auth_state: str = ""
auto_recovery_attempted_at: datetime | None = None
auto_recovery_failed_at: datetime | None = None
auto_recovery_cooldown_until: datetime | None = None
```

Repository requirements:

- `due_targets()` selects `PlatformAccount.auth_state`.
- It selects recovery metadata fields added in Task 4.
- Missing or unknown auth state is treated as `UNKNOWN`.

- [x] **Step 2: auth_state별 enqueue 정책 실패 테스트 추가**

Add tests in `tests/server/test_scheduler_tick.py`:

```python
async def test_scheduler_blocks_crawl_when_coupang_auth_required_without_auto_2fa() -> None:
    """AUTH_REQUIRED Coupang without complete email 2FA does not enqueue crawl."""

async def test_scheduler_blocks_crawl_for_user_action_pending_blocked_and_unknown() -> None:
    """Unsafe auth states do not open scheduled browser crawl attempts."""

async def test_scheduler_allows_one_coupang_recovery_crawl_when_auto_2fa_ready() -> None:
    """Complete auto 2FA allows one bounded recovery crawl."""

async def test_scheduler_blocks_coupang_recovery_during_cooldown() -> None:
    """Recent failed recovery suppresses new crawl attempts."""
```

Required reason codes:

- `AUTH_REQUIRED_NO_AUTO_RECOVERY`
- `AUTH_STATE_USER_ACTION_PENDING`
- `AUTH_STATE_BLOCKED_OR_CAPTCHA`
- `AUTH_STATE_UNKNOWN`
- `COUPANG_AUTO_RECOVERY_COOLDOWN`
- `ENQUEUED` for the allowed recovery crawl

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py::test_scheduler_blocks_crawl_when_coupang_auth_required_without_auto_2fa tests/server/test_scheduler_tick.py::test_scheduler_blocks_crawl_for_user_action_pending_blocked_and_unknown tests/server/test_scheduler_tick.py::test_scheduler_allows_one_coupang_recovery_crawl_when_auto_2fa_ready tests/server/test_scheduler_tick.py::test_scheduler_blocks_coupang_recovery_during_cooldown -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 3: recovery crawl payload에 bounded metadata 추가**

Required payload fields for allowed Coupang recovery:

```json
{
  "job_origin": "scheduler",
  "recovery_mode": "coupang_auto_email_2fa",
  "recovery_attempt": 1,
  "expires_at": "...",
  "coupang_auto_email_2fa_enabled": true
}
```

Completion criteria:

- Recovery crawl has the same target/platform payload needed by normal crawl.
- It carries no verification code.
- It is assigned to the same target affinity behavior as normal scheduled crawl.

---

## Task 4: Coupang auto recovery state를 DB에 저장

**Intent:** “한 번만 자동 복구”와 “실패 뒤 cooldown”을 계정 단위로 강제한다.

**Files:**

- Modify: `src/rider_server/db/models/account.py`
- Create: `migrations/versions/0022_coupang_auto_recovery_state.py`
- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/services/job_result_ingest_service.py` if result ingestion owns auth updates
- Test: `tests/server/test_db_schema.py`
- Test: `tests/server/test_migration.py`
- Test: `tests/server/test_queue_backend.py`

- [x] **Step 1: migration schema 실패 테스트 추가**

Add test in `tests/server/test_db_schema.py`:

```python
def test_platform_accounts_have_coupang_auto_recovery_columns() -> None:
    """Recovery cooldown is persisted on platform account rows."""
```

Required columns:

- `auto_recovery_attempted_at`
- `auto_recovery_failed_at`
- `auto_recovery_cooldown_until`

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_db_schema.py::test_platform_accounts_have_coupang_auto_recovery_columns -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: recovery result persistence 테스트 추가**

Add tests in `tests/server/test_queue_backend.py`:

```python
async def test_coupang_auto_recovery_failure_sets_cooldown_on_account() -> None:
    """Failed auto recovery suppresses future scheduler attempts."""

async def test_coupang_auto_recovery_success_clears_cooldown_on_account() -> None:
    """Successful recovery returns account to normal crawl scheduling."""
```

Required behavior:

- Failure result with `result_json["recovery_mode"] == "coupang_auto_email_2fa"` sets `auto_recovery_failed_at` and `auto_recovery_cooldown_until`.
- Success result clears `auto_recovery_cooldown_until`.
- `auth_state` update still follows existing auth result handling.

Verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_db_schema.py tests/server/test_migration.py tests/server/test_queue_backend.py -q
```

---

## Task 5: Agent preflight와 payload expiry fail-fast 추가

**Intent:** Agent가 브라우저 또는 profile을 준비하기 전에 job이 아직 유효한지 확인한다.

**Files:**

- Modify: `src/rider_server/api/jobs.py`
- Modify: `src/rider_agent/job_loop.py`
- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify: `src/rider_agent/auth/baemin_auth.py`
- Modify: `src/rider_agent/worker_composition.py`
- Test: `tests/server/test_jobs_api.py`
- Test: `tests/agent/test_job_loop.py`
- Test: `tests/agent/test_crawl_worker.py`
- Test: `tests/agent/test_baemin_auth.py`

- [x] **Step 1: server preflight API 실패 테스트 추가**

Add tests in `tests/server/test_jobs_api.py`:

```python
def test_job_preflight_denies_expired_open_auth_browser() -> None:
    """Expired browser-opening jobs are denied before Agent side effects."""

def test_job_preflight_denies_crawl_when_account_recovery_not_allowed() -> None:
    """Server state can stop a stale scheduled crawl before browser launch."""

def test_job_preflight_allows_active_normal_crawl() -> None:
    """Valid non-expired active crawl still runs."""
```

Required response shape:

```json
{
  "allowed": false,
  "reason": "payload_expired",
  "server_time": "..."
}
```

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_jobs_api.py::test_job_preflight_denies_expired_open_auth_browser tests/server/test_jobs_api.py::test_job_preflight_denies_crawl_when_account_recovery_not_allowed tests/server/test_jobs_api.py::test_job_preflight_allows_active_normal_crawl -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: Agent job loop preflight 실패 테스트 추가**

Add test in `tests/agent/test_job_loop.py`:

```python
def test_runner_does_not_call_worker_when_preflight_denies_job() -> None:
    """Preflight denial completes safely without opening browser/profile."""
```

Required assertions:

- Transport returns a claimed `OPEN_AUTH_BROWSER` or `CRAWL_COUPANG`.
- Preflight returns `allowed=false`.
- Injected `execute_job` spy is not called.
- Complete body has failure status and `result_json["reason"]`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_job_loop.py::test_runner_does_not_call_worker_when_preflight_denies_job -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 3: Agent worker defensive expiry tests 추가**

Add tests:

```python
def test_crawl_worker_rejects_expired_payload_before_profile_prepare() -> None:
    """crawl_worker checks expires_at before ensure_profile."""

def test_open_auth_browser_rejects_expired_payload_before_browser_open() -> None:
    """auth worker checks expires_at before browser interaction."""
```

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_crawl_worker.py::test_crawl_worker_rejects_expired_payload_before_profile_prepare tests/agent/test_baemin_auth.py::test_open_auth_browser_rejects_expired_payload_before_browser_open -q
```

Expected before implementation:

```text
FAILED
```

---

## Task 6: pending crawl coalescing과 stale backlog 정리

**Intent:** 서버 downtime 뒤 missed interval마다 crawl job이 쌓여 한 번에 재생되지 않게 한다.

**Files:**

- Modify: `src/rider_server/scheduler/postgres_repository.py`
- Modify: `src/rider_server/scheduler/service.py`
- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/queue/memory_queue.py`
- Test: `tests/server/test_scheduler_repository.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/server/test_queue_recovery.py`

- [x] **Step 1: target별 active crawl 하나만 유지하는 테스트 추가**

Add test in `tests/server/test_scheduler_repository.py`:

```python
async def test_scheduler_does_not_create_second_pending_crawl_for_same_target_and_platform() -> None:
    """Backlog is coalesced to one useful crawl per target/platform."""
```

Required assertions:

- Existing active `CRAWL_COUPANG` for a target prevents a new scheduler enqueue.
- `next_run_at` handling does not spin every tick on the same stale target.
- Reason is `ACTIVE_JOB_EXISTS`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_repository.py::test_scheduler_does_not_create_second_pending_crawl_for_same_target_and_platform -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: stale pending crawl cleanup 테스트 추가**

Add test in `tests/server/test_queue_recovery.py`:

```python
async def test_queue_recovery_closes_expired_pending_scheduled_crawls() -> None:
    """Pending scheduled jobs with expired payload are not later claimed."""
```

Required assertions:

- `PENDING` scheduled crawl with `expires_at < now` becomes `FAILED`.
- It cannot be claimed after recovery.
- `result_json["reason"] == "stale_crawl_skipped"`.

Verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_repository.py tests/server/test_scheduler_tick.py tests/server/test_queue_recovery.py -q
```

---

## Task 7: 운영 문서와 검증 matrix 갱신

**Intent:** 정책 문서가 “현재 동작”, “구현된 영구 수정”, “긴급 수동 조치”를 헷갈리지 않게 한다.

**Files:**

- Modify: `docs/operations/queue-backlog-handling-policy.md`
- Modify: `docs/runbooks/auth_required.md`
- Modify: `docs/runbooks/agent_offline.md`
- Modify: `tests/server/test_runbooks_present.py`
- Modify: `tests/server/test_job_vocab.py`

- [x] **Step 1: 정책 문서 상태 구분**

Required sections in `docs/operations/queue-backlog-handling-policy.md`:

- `Current Implemented Behavior`
- `Target Permanent Behavior`
- `Emergency Operator Action`
- `Verification Matrix`

Required content:

- Current behavior states that old code re-PENDINGs stale leased jobs until this work is implemented.
- Emergency action says deactivate target first, then stop Windows Agent if already-queued work keeps opening windows.
- Verification matrix includes server startup, Agent startup, scheduler tick, manual auth start, recovery success, recovery failure.

- [x] **Step 2: docs guard 테스트 추가**

Add test in `tests/server/test_runbooks_present.py`:

```python
def test_queue_backlog_policy_mentions_implemented_and_target_behavior_sections() -> None:
    """Runbook separates present behavior from future policy."""
```

Required assertions:

- The document mentions `Current Implemented Behavior`.
- The document mentions `Target Permanent Behavior`.
- The document mentions `stale_auth_job_expired`.
- The document mentions `stale_crawl_skipped`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_queue_backlog_policy_mentions_implemented_and_target_behavior_sections -q
```

Expected before implementation:

```text
FAILED
```

---

## 전체 검증 명령

Quick server/agent:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_recovery.py tests/server/test_queue_backend.py tests/server/test_jobs_api.py -q
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py tests/server/test_scheduler_repository.py tests/server/test_scheduler_policy.py -q
.venv\Scripts\python.exe -m pytest tests/server/test_admin_actions.py tests/agent/test_job_loop.py tests/agent/test_crawl_worker.py tests/agent/test_baemin_auth.py -q
```

Schema and docs:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_db_schema.py tests/server/test_migration.py tests/server/test_runbooks_present.py tests/server/test_job_vocab.py -q
```

PostgreSQL-gated:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://rider:rider@localhost:5432/rider_test"
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py tests/negative/test_scheduler_idempotency.py -q
```

## 수동 운영 확인

- Admin에서 `인증 시작`을 눌렀을 때 생성 job type이 `OPEN_AUTH_BROWSER`인지 DB에서 확인한다.
- Agent를 중지한 뒤 `OPEN_AUTH_BROWSER` lease가 만료되게 두고 Agent를 다시 켜도 브라우저가 열리지 않는지 확인한다.
- Coupang auto email 2FA가 실패한 계정은 cooldown 동안 scheduled crawl이 생성되지 않는지 확인한다.
- Coupang captcha 또는 unsupported auth 화면이 보인 계정은 자동 재시도 없이 운영자 조치 상태로 남는지 확인한다.

## 리스크와 대응

- **기존 retry 기대 테스트 실패:** stale crawl을 다시 `PENDING`으로 돌리던 테스트는 새 정책에 맞게 terminal failure expected로 바꾼다.
- **계정 단위 cooldown 영향:** 한 Coupang 계정에 여러 target이 연결되어 있으면 한 target의 자동 복구 실패가 같은 계정의 다른 target scheduling도 막는다. 문서에 계정 단위 정책임을 명시한다.
- **Agent preflight API 장애:** preflight 호출 실패는 browser open을 막는 fail-closed로 처리하고, result reason을 `preflight_unavailable`로 남긴다.
- **상태 enum 확장 위험:** 1차 구현은 새 status `SKIPPED`를 만들지 않는다. 운영 UI에서 skip과 failed를 구분해야 할 때 별도 migration으로 확장한다.
