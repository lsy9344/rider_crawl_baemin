# Crawl Coupang Auth Separation Work Order

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for implementation slices, or `superpowers:executing-plans` if one worker executes the whole plan. Keep checkbox state in this document as work lands.

작성일: 2026-06-23  
상태: 작업 전  
대상 저장소: `rider_result_mornitoring`  
대상 범위: `CRAWL_COUPANG` 실행 경로와 Coupang email 2FA 인증 경로 분리  
검토 근거: 사용자 제공 분석, 현행 코드 확인, 기존 `docs/goal/queue-backlog-hardening-work-order-2026-06-23.md`

**Goal:** `CRAWL_COUPANG` job 안에서 조용히 로그인과 email 2FA를 처리하는 구조를 끝내고, 서버가 `platform_accounts.auth_state`를 기준으로 인증 상태를 명시적으로 전이시키는 구조로 바꾼다.

**Architecture:** 크롤 job은 "세션이 이미 유효하다"는 전제로 데이터만 읽는다. 로그인 화면을 만나면 즉시 `AUTH_REQUIRED`를 반환한다. Coupang email 2FA는 신규 인증 job `AUTH_COUPANG_2FA`가 담당한다. 사람 개입이 필요한 캡차, 이상 로그인, 메일 인증 재승인, 반복 실패는 `OPEN_AUTH_BROWSER` 또는 운영자 조치 상태로 보낸다.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy async, PostgreSQL, Windows Agent runtime, Playwright/CDP, IMAPClient, pytest.

---

## 검토 결론

현재 코드는 "서버 주도 인증 상태 전이"로 가려다가 중간에서 멈춘 상태다. 인증 책임이 세 곳에 나뉘어 있다.

- `src/rider_crawl/platforms/coupang/crawler.py:525-561`  
  크롤 중 대상 탭이 없거나 로그인 화면이면 `_try_recover_coupang_session()`으로 email 2FA를 시도한다.
- `src/rider_crawl/platforms/coupang/crawler.py:661-694`  
  `_try_recover_coupang_session()`이 `coupang_auto_email_2fa_enabled`를 보고 복구 함수를 호출한다.
- `src/rider_crawl/platforms/coupang/crawler.py:697-709`  
  같은 메일함 중복 접근을 막기 위해 파일 기반 `mailbox_locks`를 쓴다.
- `src/rider_agent/auth/baemin_auth.py:214-237`  
  `OPEN_AUTH_BROWSER` 기본 구현이 Coupang이면 `_drive_coupang_email_2fa_flow()`를 호출한다. 이름은 수동 브라우저 열기인데 실제로는 자동 email 2FA까지 한다.
- `src/rider_agent/auth/baemin_auth.py:360-445`  
  `_drive_coupang_email_2fa_flow()`가 로그인 화면 대기 버그를 피하면서 email 2FA 복구를 직접 운전한다.
- `src/rider_agent/auth/coupang_gmail_2fa.py:25-45`  
  Coupang 전용 상태 어휘(`ACTIVE`, `USER_ACTION_REQUIRED`, `EMAIL_AUTH_REQUIRED`, `RECOVERY_FAILED`)와 에러 어휘가 이미 있다.
- `src/rider_agent/auth/coupang_gmail_2fa.py:162-200`  
  `recover_coupang_mailbox()`는 mailbox lock, bounded attempt, reason metric을 이미 제공한다.
- `src/rider_agent/workers/crawl_worker.py:127-128`, `src/rider_agent/workers/crawl_worker.py:236-237`  
  크롤 job은 `BrowserActionRequiredError`를 `AUTH_REQUIRED` 실패 결과로 바꾼다.
- `src/rider_server/queue/states.py:21-39`, `src/rider_agent/heartbeat.py:63-78`  
  서버 job type과 Agent capability는 아직 `AUTH_CHECK`, `OPEN_AUTH_BROWSER`까지만 있다.
- `src/rider_server/api/jobs.py:44-45`, `src/rider_server/api/jobs.py:439-444`  
  claim lease 기본값은 120초이고 모든 claimed job에 같은 lease를 부여한다.
- `src/rider_server/api/agents.py:182-189`  
  heartbeat가 active job lease를 연장할 수 있으므로, long-running auth job은 in-flight 노출과 bounded timeout을 잘 쓰면 된다.
- `src/rider_server/queue/postgres_queue.py:125-156`, `src/rider_server/queue/postgres_queue.py:319-323`  
  queue complete 시 `result_json.auth_state`나 `AUTH_REQUIRED` error code를 보고 `platform_accounts.auth_state`를 갱신한다.

따라서 핵심 변경은 2FA 로직을 새로 쓰는 것이 아니라, 호출 지점을 크롤러 안쪽에서 인증 job worker로 옮기는 것이다.

---

## 설계 결정

### Decision 1: `AUTH_COUPANG_2FA` job type을 새로 둔다

`OPEN_AUTH_BROWSER`는 "사람이 브라우저에서 직접 조치하는 job"으로 남긴다. Coupang email 2FA 자동복구는 `AUTH_COUPANG_2FA`가 담당한다.

이유:

- `OPEN_AUTH_BROWSER`라는 이름과 실제 자동 OTP 입력 동작이 충돌한다.
- 자동 email 2FA는 메일 대기, IMAP 실패, 중복 OTP 요청 같은 별도 정책이 필요하다.
- 서버는 `AUTH_COUPANG_2FA`와 `OPEN_AUTH_BROWSER`를 구분해야 운영 화면과 retry 정책을 정확히 만들 수 있다.

### Decision 2: 크롤 job은 자동 2FA를 하지 않는다

`CRAWL_COUPANG` job은 로그인 화면을 만나면 바로 `AUTH_REQUIRED`를 반환한다. `recover_coupang_session_with_email_2fa()`를 호출하지 않는다.

1차 구현에서는 shared `rider_crawl`의 local desktop 호환을 바로 깨지 않기 위해, Agent `CRAWL_COUPANG` payload에서는 `coupang_auto_email_2fa_enabled=False`를 강제하거나 무시한다. 그 다음 단계에서 desktop 경로까지 신규 인증 job 모델로 옮긴 뒤 `crawler.py`의 inline 복구 분기를 제거한다.

### Decision 3: account `auth_state`는 coarse gate, job result는 detailed recovery state

현행 서버의 `PlatformAccount.auth_state` 타입은 `BaeminAuthState`이고 값은 `UNKNOWN`, `ACTIVE`, `AUTH_REQUIRED`, `USER_ACTION_PENDING`, `AUTH_VERIFIED`, `CENTER_MISMATCH`, `BLOCKED_OR_CAPTCHA`다. `EMAIL_AUTH_REQUIRED`와 `RECOVERY_FAILED`는 아직 서버 account enum에 없다.

1차 구현은 다음처럼 분리한다.

- `platform_accounts.auth_state`: 스케줄러와 대시보드가 쓰는 gate 상태
  - success: `ACTIVE` 또는 기존 호환을 위해 `AUTH_VERIFIED`
  - user/captcha/manual needed: `USER_ACTION_PENDING`
  - mail delay/recovery failed: `AUTH_REQUIRED`
- auth job `result_json.auth_recovery_state`: Coupang 2FA 세부 상태
  - `ACTIVE`
  - `USER_ACTION_REQUIRED`
  - `EMAIL_AUTH_REQUIRED`
  - `RECOVERY_FAILED`
- auth job `result_json.reason` 또는 `metrics.reason`: 고정 reason 문자열
  - `captcha_or_abnormal_login`
  - `email_auth_required`
  - `verification_mail_delayed`
  - `repeated_recovery_failure`

이 방식은 서버 enum count-lock을 바로 깨지 않으면서도, 운영자가 세부 원인을 볼 수 있게 한다. 나중에 계정 상태 자체를 `EMAIL_AUTH_REQUIRED`까지 표현해야 하면 별도 migration으로 `PlatformAuthState`를 도입한다.

### Decision 4: long lease보다 heartbeat 연장과 bounded timeout을 우선한다

현행 `claim` API는 한 번의 claim 묶음에 같은 lease를 준다. job type별 lease를 넣으려면 queue backend 계약을 넓혀야 한다. 1차 구현은 다음 정책을 쓴다.

- `AUTH_COUPANG_2FA`는 Agent `JobRunner`의 in-flight job에 들어간다.
- heartbeat가 `active_jobs`로 lease를 계속 연장한다.
- 인증 worker 자체에는 `max_attempts`, `max_wait_seconds`, `poll_seconds` 상한을 둔다.
- heartbeat lease extension 실패가 감지되면 complete 시 `LEASE_LOST`를 흡수하고 중복 OTP 요청은 하지 않는다.

job type별 lease는 2차 개선으로만 검토한다.

### Decision 5: retry는 서버 queue retry가 아니라 auth 상태 전이가 맡는다

`AUTH_COUPANG_2FA` 실패는 자동 retry하지 않는다. 실패 결과는 계정 상태와 cooldown을 갱신하고 멈춘다.

권장 result:

```json
{
  "target_id": "...",
  "platform": "coupang",
  "platform_account_id": "...",
  "auth_state": "AUTH_REQUIRED",
  "auth_recovery_state": "RECOVERY_FAILED",
  "reason": "verification_mail_delayed",
  "recovery_mode": "coupang_auto_email_2fa"
}
```

`error_code`는 서버 retry 정책이 이미 사람 개입으로 보류하는 `AUTH_REQUIRED`를 우선 사용한다. 세부 원인은 `auth_recovery_state`와 `reason`으로 둔다.

### Decision 6: 기존 queue backlog 문서의 "recovery crawl" 표현은 이 문서로 대체한다

`docs/goal/queue-backlog-hardening-work-order-2026-06-23.md`는 `AUTH_REQUIRED` Coupang 계정에 "recovery crawl"을 1건 허용하는 방향을 적고 있다. 이 문서 이후 구현에서는 그 표현을 `AUTH_COUPANG_2FA` 인증 job으로 바꾼다.

대체 규칙:

- `recovery_mode == "coupang_auto_email_2fa"`는 crawl job payload가 아니라 auth job result/payload에 둔다.
- `CRAWL_COUPANG`은 복구 수단이 아니다.
- scheduler는 인증 필요 계정에 crawl을 만들지 않고 auth job을 만든다.

---

## 목표 흐름

### 정상 크롤

```text
Scheduler
  -> CRAWL_COUPANG enqueue
Agent CrawlWorker
  -> session active 가정
  -> 데이터 수집
  -> success snapshot
Server complete
  -> platform_accounts.auth_state = ACTIVE 유지
```

### 로그인 만료

```text
Scheduler
  -> CRAWL_COUPANG enqueue
Agent CrawlWorker
  -> 로그인 화면 감지
  -> AUTH_REQUIRED 반환, 자동 2FA 시도 0
Server complete
  -> platform_accounts.auth_state = AUTH_REQUIRED
Scheduler/Admin Action
  -> AUTH_COUPANG_2FA enqueue
Agent CoupangAuthWorker
  -> IMAP email 2FA 자동복구 1회
  -> ACTIVE / USER_ACTION_REQUIRED / EMAIL_AUTH_REQUIRED / RECOVERY_FAILED 반환
Server complete
  -> account auth_state와 recovery metadata 갱신
Scheduler
  -> ACTIVE면 다음 due crawl 재개
```

### 자동 2FA 실패 후 수동 조치

```text
AUTH_COUPANG_2FA
  -> CAPTCHA / abnormal / mailbox auth failure / repeated failure
  -> auth_recovery_state != ACTIVE
Server
  -> account auth_state = USER_ACTION_PENDING 또는 AUTH_REQUIRED
Operator
  -> OPEN_AUTH_BROWSER 실행
Agent
  -> 브라우저만 열고 사람 조치 대기
  -> 완료 감지 시 AUTH_VERIFIED 또는 ACTIVE
```

---

## 작업 원칙

- 크롤 job 안에서 IMAP, OTP 입력, 로그인 제출을 하지 않는다.
- `OPEN_AUTH_BROWSER`는 자동 OTP를 호출하지 않는다.
- `AUTH_COUPANG_2FA`는 같은 mailbox에 대해 동시에 두 번 실행되지 않는다.
- 인증 job은 재시도 폭주를 만들지 않는다. 한 번 실패하면 상태와 reason을 남기고 멈춘다.
- OTP, 쿠팡 비밀번호, email app password, 평문 이메일 주소는 job result, log, audit, metrics에 남기지 않는다.
- `rider_agent`는 `rider_server`를 import하지 않는다. job type과 상태 값은 plain-string 상수로 미러링한다.
- 기존 dirty worktree는 되돌리지 않는다.

## 완료 기준

- `CRAWL_COUPANG` 실행 중 로그인 화면을 만나도 `recover_coupang_session_with_email_2fa()`가 호출되지 않는다.
- `CRAWL_COUPANG`은 `BrowserActionRequiredError`를 `AUTH_REQUIRED`로 반환하고 끝난다.
- `AUTH_COUPANG_2FA` job type과 Agent capability가 생긴다.
- `AUTH_COUPANG_2FA` worker가 기존 `rider_agent/auth/coupang_gmail_2fa.py` primitive를 재사용한다.
- `OPEN_AUTH_BROWSER` Coupang 경로는 email 2FA 자동복구를 하지 않는다.
- `platform_accounts.auth_state`는 auth job 결과로 갱신된다.
- `EMAIL_AUTH_REQUIRED`, `RECOVERY_FAILED` 같은 세부 상태는 `auth_recovery_state`와 고정 reason으로 보존된다.
- scheduler는 `AUTH_REQUIRED` Coupang 계정에 crawl 대신 auth job을 만들거나 보류한다.
- 자동 2FA 실패 후 같은 계정에 중복 auth job이 계속 생기지 않는다.
- 관련 단위 테스트는 외부 브라우저, 실 IMAP, 실 DPAPI를 쓰지 않는다.

---

## Task 0: 기준선과 충돌 문서 확인

**Intent:** 현재 dirty worktree와 기존 설계 문서 충돌을 기록한다.

**Files:** 없음

- [ ] **Step 1: 작업 전 변경 상태 확인**

Run:

```powershell
git status --short
```

Expected:

- 이 문서 작성 시점에 이미 `src/rider_agent/workers/crawl_process.py`, `src/rider_agent/workers/crawl_worker.py`, `tests/agent/test_crawl_worker.py` 변경이 있었다.
- 구현자는 본인 작업과 무관한 변경을 되돌리지 않는다.

- [ ] **Step 2: 기존 queue backlog 문서와 충돌점 표시**

Review:

```powershell
rg -n "recovery crawl|coupang_auto_email_2fa|AUTH_REQUIRED Coupang" docs/goal/queue-backlog-hardening-work-order-2026-06-23.md
```

Expected:

- `recovery crawl` 방향은 이 문서의 `AUTH_COUPANG_2FA` 방향으로 대체한다.
- 필요하면 후속 PR에서 queue backlog 문서의 Task 3, Task 4 표현을 수정한다.

---

## Task 1: job type과 capability vocabulary 추가

**Intent:** 서버와 Agent가 `AUTH_COUPANG_2FA`를 같은 문자열로 이해하게 한다.

**Files:**

- Modify: `src/rider_server/queue/states.py`
- Modify: `src/rider_agent/heartbeat.py`
- Modify: `tests/server/test_queue_states.py` 또는 기존 상태 테스트
- Modify: `tests/agent/test_heartbeat.py`

- [ ] **Step 1: 서버 job type 상수 추가**

Required constant:

```python
JOB_TYPE_AUTH_COUPANG_2FA = "AUTH_COUPANG_2FA"
```

Rules:

- `JOB_TYPES`에 추가한다.
- 기존 type 문자열은 바꾸지 않는다.
- count-lock을 만들지 않는다. 기존 파일 주석처럼 후속 type 확장을 허용한다.

- [ ] **Step 2: Agent capability 상수 추가**

Required constant:

```python
CAPABILITY_AUTH_COUPANG_2FA = "AUTH_COUPANG_2FA"
```

Rules:

- `DEFAULT_CAPABILITIES`에 추가한다. 이 Agent가 auth worker를 시작하지 않으면 fallback이 unsupported를 반환할 수 있으므로, 실제 실행 배선은 Task 4에서 한다.
- `rider_agent`는 `rider_server.queue.states`를 import하지 않는다.

- [ ] **Step 3: vocabulary drift 테스트 추가**

Add assertions:

- server `JOB_TYPE_AUTH_COUPANG_2FA == "AUTH_COUPANG_2FA"`
- agent `CAPABILITY_AUTH_COUPANG_2FA == "AUTH_COUPANG_2FA"`
- default heartbeat capabilities includes it.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_domain_states.py tests/agent/test_heartbeat.py -q
```

---

## Task 2: `OPEN_AUTH_BROWSER`에서 Coupang 자동 2FA 제거

**Intent:** 수동 인증 job과 자동 email 2FA job의 책임을 분리한다.

**Files:**

- Modify: `src/rider_agent/auth/baemin_auth.py`
- Modify: `tests/agent/test_baemin_auth.py`

- [ ] **Step 1: 실패 테스트 추가**

Add test:

```python
def test_open_auth_browser_for_coupang_does_not_run_email_2fa() -> None:
    """OPEN_AUTH_BROWSER opens/prepares browser only; automatic Coupang 2FA is a separate job."""
```

Required assertions:

- `default_open_auth_browser()` for Coupang calls `prepare_chrome`.
- It does not call `recover_coupang_session_with_email_2fa`.
- It returns `None` or a manual-in-progress value, not `True` from auto recovery.

Expected before implementation:

```text
FAILED
```

- [ ] **Step 2: `default_open_auth_browser()` Coupang branch 축소**

Current:

- `src/rider_agent/auth/baemin_auth.py:233-237` calls `_drive_coupang_email_2fa_flow(config)`.

Change:

- Coupang branch only prepares or opens the profile browser.
- It may navigate to `coupang_eats_url` or login page, but it must not submit login, click send-code, read IMAP, fill OTP, or submit 2FA.
- Keep `default_detect_completion()` read-only behavior.

- [ ] **Step 3: `_drive_coupang_email_2fa_flow()` 이동 준비**

Do not delete logic immediately. Move or wrap it in `rider_agent/auth/coupang_gmail_2fa.py` as an internal helper for `AUTH_COUPANG_2FA`.

Rules:

- Existing tests that expected Coupang auto 2FA inside `OPEN_AUTH_BROWSER` must be rewritten to target `AUTH_COUPANG_2FA`.
- Baemin behavior must remain unchanged.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_baemin_auth.py -q
```

---

## Task 3: Coupang 2FA auth worker 추가

**Intent:** 기존 Coupang email 2FA primitive를 전용 job worker로 승격한다.

**Files:**

- Modify: `src/rider_agent/auth/coupang_gmail_2fa.py`
- Modify: `src/rider_agent/auth/__init__.py` if exports are needed
- Modify: `src/rider_agent/worker_composition.py`
- Test: `tests/agent/test_coupang_gmail_2fa.py`
- Test: `tests/agent/test_baemin_auth.py` if auth router tests live there

- [ ] **Step 1: job 실행자 실패 테스트 추가**

Add tests:

```python
def test_execute_auth_coupang_2fa_job_returns_active_on_recovered() -> None:
    """Successful email 2FA reports account ACTIVE."""

def test_execute_auth_coupang_2fa_job_maps_false_to_user_action_pending() -> None:
    """CAPTCHA/abnormal/manual screens stop without retry."""

def test_execute_auth_coupang_2fa_job_maps_mail_auth_to_auth_required_detail() -> None:
    """Mailbox re-auth is visible as auth_recovery_state without leaking secrets."""

def test_auth_coupang_2fa_job_uses_mailbox_lock_once() -> None:
    """Same mailbox is serialized and recovery is bounded."""
```

Required result shape on success:

```json
{
  "target_id": "target-1",
  "platform": "coupang",
  "platform_account_id": "account-1",
  "auth_state": "ACTIVE",
  "auth_recovery_state": "ACTIVE",
  "recovery_mode": "coupang_auto_email_2fa"
}
```

Required result shape on failure:

```json
{
  "target_id": "target-1",
  "platform": "coupang",
  "platform_account_id": "account-1",
  "auth_state": "AUTH_REQUIRED",
  "auth_recovery_state": "RECOVERY_FAILED",
  "reason": "verification_mail_delayed",
  "recovery_mode": "coupang_auto_email_2fa"
}
```

Secrets must not appear in:

- `result_json`
- `metrics`
- `error_message_redacted`
- logs

- [ ] **Step 2: `execute_auth_coupang_2fa_job()` 구현**

Recommended signature:

```python
def execute_auth_coupang_2fa_job(
    job: ClaimedJob,
    *,
    recover: Callable[[], bool] | None = None,
    secret_resolver: Callable[[str], str | None] | None = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    log: Callable[[str], None] | None = None,
) -> JobResult:
    ...
```

Implementation rules:

- Use `recover_coupang_mailbox()` for lock, attempt, reason, and failure classification.
- Use `build_coupang_recover()` to adapt the Playwright page and AppConfig to `recover_coupang_session_with_email_2fa()`.
- Resolve `coupang_login_id_ref`, `coupang_login_password_ref`, `verification_email_address_ref`, `verification_email_app_password_ref` through `secret_resolver`.
- If a required ref cannot be resolved, fail closed with `AUTH_REQUIRED` and a fixed reason such as `secret_ref_unresolved`.
- Do not pass raw email address as `mailbox_ref`. Use existing `mailbox_credential_ref()` or a hashed mailbox handle.

- [ ] **Step 3: Playwright/CDP page acquisition helper**

The auth worker needs a page for `recover_coupang_session_with_email_2fa(page, config)`.

Rules:

- Reuse the safe parts of current `_drive_coupang_email_2fa_flow()` from `src/rider_agent/auth/baemin_auth.py:360-445`.
- Do not wait for dashboard readiness before the login/2FA attempt when the page is already a login screen.
- Avoid the historical `page_timeout_seconds * 2` dead wait.
- After recovery success, reload the target page and verify readiness only if the target URL is a known Coupang page.

- [ ] **Step 4: auth execute router에 연결**

Options:

- Preferred: create `build_coupang_auth_execute_job()` in `coupang_gmail_2fa.py`, then compose it in `worker_composition.py`.
- Acceptable: extend `baemin_auth.build_auth_execute_job()` to route `CAPABILITY_AUTH_COUPANG_2FA`, but do not put new Coupang implementation details in `baemin_auth.py`.

Required behavior:

- `AUTH_CHECK` continues to route to existing auth check.
- `OPEN_AUTH_BROWSER` continues to route to manual auth browser.
- `AUTH_COUPANG_2FA` routes to the new Coupang 2FA worker.
- Unknown job types fall through to existing fallback.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_coupang_gmail_2fa.py tests/agent/test_baemin_auth.py -q
```

---

## Task 4: `CRAWL_COUPANG`에서 inline 2FA 비활성화

**Intent:** 크롤 job이 로그인 복구를 직접 하지 않게 한다.

**Files:**

- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify: `src/rider_crawl/platforms/coupang/crawler.py` if needed
- Test: `tests/agent/test_crawl_worker.py`
- Test: `tests/test_coupang_crawler.py`

- [ ] **Step 1: Agent crawl path 실패 테스트 추가**

Add tests:

```python
def test_crawl_coupang_job_does_not_enable_email_2fa_from_payload() -> None:
    """Crawl jobs never run automatic Coupang email recovery."""

def test_crawl_coupang_login_screen_returns_auth_required_without_recovery() -> None:
    """Login screen in crawl path is surfaced to server as AUTH_REQUIRED."""
```

Required assertions:

- Even if payload contains `coupang_auto_email_2fa_enabled=True`, `AppConfig.coupang_auto_email_2fa_enabled` is `False` for `CRAWL_COUPANG`.
- `recover_coupang_session_with_email_2fa` is not called.
- Result is failed with `error_code == "AUTH_REQUIRED"` and `result_json.auth_state == "AUTH_REQUIRED"`.

- [ ] **Step 2: `CrawlWorker._prepare_config()` 정책 변경**

Current:

- `src/rider_agent/workers/crawl_worker.py:585` sets `coupang_auto_email_2fa_enabled=enable_email_2fa`.

Change:

- For job type `CRAWL_COUPANG`, force `coupang_auto_email_2fa_enabled=False`.
- Keep secret resolution fields only if still needed for local compatibility, but they should not drive 2FA in crawl job.
- For future cleanup, mark `coupang_auto_email_2fa_enabled` in crawl payload as deprecated.

- [ ] **Step 3: shared crawler compatibility decision**

There are two acceptable cuts:

1. Low-risk first cut: leave `src/rider_crawl/platforms/coupang/crawler.py` inline recovery code in place, but make Agent crawl never enable it.
2. Full cut: remove `_recover_login_page_to_target()` and `_try_recover_coupang_session()` calls from crawler and keep email 2FA only in auth worker.

Choose option 1 unless desktop UI has already moved to server-driven auth. Document the chosen cut in PR notes.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_crawl_worker.py tests/test_coupang_crawler.py -q
```

---

## Task 5: 서버 auth enqueue 경로 변경

**Intent:** 서버가 Coupang 인증 필요 상태를 보고 `AUTH_COUPANG_2FA`를 명시적으로 enqueue한다.

**Files:**

- Modify: `src/rider_server/services/admin_action_service.py`
- Modify: `src/rider_server/admin/actions_routes.py` if UI text/action split is needed
- Modify: `tests/server/test_admin_actions.py`

- [ ] **Step 1: `_auth_start_payload()` 테스트 변경**

Add tests:

```python
async def test_start_auth_for_coupang_enqueues_auth_coupang_2fa_when_auto_info_complete() -> None:
    """Coupang auto auth uses AUTH_COUPANG_2FA, not OPEN_AUTH_BROWSER."""

async def test_start_auth_for_coupang_manual_fallback_uses_open_auth_browser() -> None:
    """Manual browser auth remains available when auto 2FA cannot run."""
```

Required assertions:

- Coupang with complete refs returns job type `AUTH_COUPANG_2FA`.
- Payload includes:
  - `platform == "coupang"`
  - `platform_account_id`
  - `browser_profile_ref`
  - `primary_url`
  - login and email refs
  - `recovery_mode == "coupang_auto_email_2fa"`
- Payload excludes:
  - OTP
  - raw password
  - raw app password
- Manual fallback uses `OPEN_AUTH_BROWSER` and does not include `coupang_auto_email_2fa_enabled=True`.

- [ ] **Step 2: action naming 정리**

Current:

- `ACTION_AUTH_START` is used for auth start.

Recommended:

- Keep `ACTION_AUTH_START` for audit compatibility.
- Add audit diff field `job_type`.
- UI copy can say:
  - auto: `쿠팡 자동 인증 시작됨`
  - manual: `인증 브라우저 열기 시작됨`

- [ ] **Step 3: duplicate active auth job guard**

Before enqueueing `AUTH_COUPANG_2FA`, server must avoid duplicate active auth jobs for the same account/target.

Implementation options:

- Add repository method `active_auth_job_exists(target_id, job_types=("AUTH_COUPANG_2FA", "OPEN_AUTH_BROWSER"))`.
- Or add unique partial DB index later. For first cut, service-level check is enough.

Failure behavior:

- If active auth job exists, return existing "이미 진행 중인 인증 작업이 있습니다" flow.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_actions.py tests/server/test_admin_action_audit.py -q
```

---

## Task 6: queue complete가 Coupang auth result를 account state로 반영

**Intent:** auth job 결과가 `platform_accounts.auth_state`의 단일 진실 원천이 되게 한다.

**Files:**

- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/queue/memory_queue.py` if in-memory read model needs parity
- Test: `tests/server/test_queue_backend.py`

- [ ] **Step 1: persistence 실패 테스트 추가**

Add tests:

```python
async def test_auth_coupang_2fa_success_marks_account_active() -> None:
    """AUTH_COUPANG_2FA success moves account to ACTIVE."""

async def test_auth_coupang_2fa_email_auth_required_keeps_account_auth_required_with_detail() -> None:
    """Detailed Coupang recovery state is preserved without inventing retry."""

async def test_auth_coupang_2fa_user_action_required_marks_account_user_action_pending() -> None:
    """Manual intervention states are visible to scheduler/dashboard."""
```

Required behavior:

- `result_json.auth_state` in existing allowed values updates `PlatformAccount.auth_state`.
- If `auth_recovery_state == "USER_ACTION_REQUIRED"`, normalized account state is `USER_ACTION_PENDING`.
- If `auth_recovery_state == "EMAIL_AUTH_REQUIRED"` or `RECOVERY_FAILED`, normalized account state is `AUTH_REQUIRED` unless a later schema adds `auth_state_detail`.
- Existing `CENTER_MISMATCH` behavior is unchanged.

- [ ] **Step 2: detail preservation**

If schema already has or gains recovery metadata columns, store:

- `last_auth_recovery_state`
- `last_auth_recovery_reason`
- `auto_recovery_attempted_at`
- `auto_recovery_failed_at`
- `auto_recovery_cooldown_until`

If these columns are not part of the current implementation slice, at minimum keep detail in `jobs.result_json`; do not drop it in complete processing.

- [ ] **Step 3: retry policy 확인**

Auth job failure should not requeue automatically.

Rules:

- Use `error_code == "AUTH_REQUIRED"` for terminal auth-required failure categories.
- Do not emit unknown transient error code that `retry_decider` may treat as retryable.
- `RECOVERY_FAILED` is a detail state, not the queue retry category.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_backend.py tests/server/test_postgres_runtime_guards.py -q
```

---

## Task 7: scheduler가 auth_state 기준으로 crawl/auth를 분기

**Intent:** 인증 필요 계정에 scheduled crawl이 반복 생성되지 않게 하고, 필요한 경우 auth job을 만든다.

**Files:**

- Modify: `src/rider_server/scheduler/postgres_repository.py`
- Modify: `src/rider_server/scheduler/service.py`
- Modify: `src/rider_server/scheduler/policy.py`
- Test: `tests/server/test_scheduler_tick.py`
- Test: `tests/server/test_scheduler_repository.py`
- Test: `tests/server/test_scheduler_policy.py`

- [ ] **Step 1: scheduler policy 테스트 변경**

Add or update tests:

```python
async def test_scheduler_enqueues_auth_coupang_2fa_instead_of_crawl_when_auth_required() -> None:
    """AUTH_REQUIRED Coupang account gets auth job, not crawl job."""

async def test_scheduler_blocks_coupang_crawl_while_auth_job_active() -> None:
    """Duplicate auth jobs and crawl jobs are both suppressed."""

async def test_scheduler_resumes_crawl_after_coupang_auth_active() -> None:
    """ACTIVE auth state allows normal crawl scheduling again."""

async def test_scheduler_requires_manual_action_for_user_action_pending() -> None:
    """CAPTCHA/manual states do not create auto 2FA attempts."""
```

Required reason codes:

- `ENQUEUED_CRAWL`
- `ENQUEUED_AUTH_COUPANG_2FA`
- `AUTH_JOB_ALREADY_ACTIVE`
- `AUTH_STATE_USER_ACTION_PENDING`
- `AUTH_STATE_AUTH_REQUIRED_NO_AUTO_CONFIG`
- `COUPANG_AUTO_RECOVERY_COOLDOWN`

- [ ] **Step 2: DueTarget auth facts**

Required fields:

```python
auth_state: str = ""
platform_account_id: str = ""
verification_email_address_ref: str = ""
verification_email_app_password_ref: str = ""
auto_recovery_cooldown_until: datetime | None = None
active_auth_job_count: int = 0
```

Rules:

- Repository must load account auth state in the same query or bulk query.
- Avoid target-by-target N+1.
- Missing auth state is treated as `UNKNOWN`, which blocks crawl.

- [ ] **Step 3: enqueue behavior**

Rules:

- `ACTIVE` or `AUTH_VERIFIED`: enqueue normal crawl.
- `AUTH_REQUIRED` with complete Coupang auto 2FA refs and no cooldown: enqueue `AUTH_COUPANG_2FA`.
- `AUTH_REQUIRED` without complete refs: do not enqueue crawl. Surface reason for admin.
- `USER_ACTION_PENDING` or `BLOCKED_OR_CAPTCHA`: do not enqueue auto auth or crawl.
- `UNKNOWN`: do not enqueue crawl until `AUTH_CHECK` or operator action clarifies state.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py tests/server/test_scheduler_repository.py tests/server/test_scheduler_policy.py -q
```

---

## Task 8: stale recovery와 lease 경합 방지

**Intent:** 2FA가 진행 중인데 lease 만료로 job이 회수되고 중복 OTP가 요청되는 상황을 막는다.

**Files:**

- Modify: `src/rider_server/queue/memory_queue.py`
- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_agent/job_loop.py` if active job tracking gap is found
- Test: `tests/server/test_queue_backend.py`
- Test: `tests/agent/test_job_loop.py`

- [ ] **Step 1: heartbeat extension 확인 테스트**

Add test:

```python
def test_auth_coupang_2fa_job_is_exposed_as_active_job_for_heartbeat() -> None:
    """Long auth jobs are lease-extended while running."""
```

Required assertions:

- Claimed auth job appears in `JobRunner.active_jobs()` during execution.
- Heartbeat payload contains its `job_id`.

- [ ] **Step 2: stale auth job recovery 정책 테스트**

Add tests:

```python
async def test_stale_auth_coupang_2fa_is_not_retried_by_recovery_loop() -> None:
    """Expired auth job does not trigger repeated OTP request."""
```

Required behavior:

- If `AUTH_COUPANG_2FA` lease expires, stale recovery should mark it failed or held with a safe reason, not blindly `PENDING`.
- Account should remain `AUTH_REQUIRED` or `USER_ACTION_PENDING`.
- Scheduler duplicate guard should prevent immediate re-enqueue without cooldown/operator action.

- [ ] **Step 3: no job-type lease change in first cut**

Do not widen `QueueBackend.claim()` for per-job lease unless the first-cut tests prove heartbeat extension is insufficient. If widening is required, update all queue backends and API tests together.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_backend.py tests/agent/test_job_loop.py -q
```

---

## Task 9: 운영 화면과 runbook 갱신

**Intent:** 운영자가 "크롤 실패"와 "인증 진행/실패"를 구분해서 볼 수 있게 한다.

**Files:**

- Modify: `src/rider_server/admin/routes.py`
- Modify: `src/rider_server/admin/severity.py`
- Modify: `src/rider_server/admin/dashboard_repository_postgres.py`
- Modify: `docs/runbooks/auth_required.md`
- Modify: `docs/operations/queue-backlog-handling-policy.md`
- Test: `tests/server/test_admin_dashboard.py`
- Test: `tests/server/test_dashboard_severity.py`

- [ ] **Step 1: severity 표시 테스트**

Add tests:

```python
def test_dashboard_surfaces_coupang_email_auth_required_detail() -> None:
    """Email mailbox auth issue is visible as auth-required detail."""

def test_dashboard_surfaces_coupang_recovery_failed_detail() -> None:
    """Repeated/mail-delay recovery failures are not shown as generic crawl failure."""
```

Required display behavior:

- Account gate state still drives red/yellow severity.
- Detail reason can show:
  - `메일 인증 필요`
  - `인증 메일 지연`
  - `캡차/이상 로그인`
  - `자동 인증 실패`

- [ ] **Step 2: runbook 갱신**

Runbook must explain:

- `CRAWL_COUPANG` no longer performs automatic 2FA.
- `AUTH_COUPANG_2FA` is the auto recovery job.
- `OPEN_AUTH_BROWSER` is manual only.
- If `EMAIL_AUTH_REQUIRED`, operator must check mailbox app password/IMAP auth.
- If `USER_ACTION_REQUIRED`, operator must use browser and solve captcha/abnormal login.
- Never rerun repeated auto 2FA blindly.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py tests/server/test_dashboard_severity.py tests/server/test_runbooks_present.py -q
```

---

## 전체 검증 명령

Run focused tests first:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_coupang_gmail_2fa.py tests/agent/test_baemin_auth.py tests/agent/test_crawl_worker.py -q
.venv\Scripts\python.exe -m pytest tests/server/test_admin_actions.py tests/server/test_queue_backend.py tests/server/test_scheduler_tick.py tests/server/test_scheduler_policy.py -q
```

Then run broader affected suites:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent -q
.venv\Scripts\python.exe -m pytest tests/server -q
.venv\Scripts\python.exe -m pytest tests/test_coupang_crawler.py tests/test_coupang_email_2fa.py tests/test_coupang_parser.py -q
```

Full suite before merge:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

---

## 수동 운영 확인

- 만료된 Coupang 세션으로 `CRAWL_COUPANG` 실행:
  - Expected: crawl job fails quickly with `AUTH_REQUIRED`.
  - Expected: IMAP access does not happen.
- Same target에 `AUTH_COUPANG_2FA` 실행:
  - Expected: mailbox lock is acquired once.
  - Expected: one email code request only.
  - Expected: success updates account to `ACTIVE`.
- CAPTCHA or abnormal login page:
  - Expected: `auth_recovery_state == USER_ACTION_REQUIRED`.
  - Expected: no repeated OTP requests.
  - Expected: operator can run `OPEN_AUTH_BROWSER`.
- Mail app password revoked:
  - Expected: `auth_recovery_state == EMAIL_AUTH_REQUIRED`.
  - Expected: dashboard/runbook points to mailbox credential action.
- Slow mail arrival beyond bounded timeout:
  - Expected: `RECOVERY_FAILED` with `verification_mail_delayed`.
  - Expected: cooldown or duplicate guard prevents immediate loop.

---

## 리스크와 대응

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| Desktop local crawler가 inline 2FA에 의존 | 갑자기 자동복구가 사라질 수 있음 | 1차 구현은 Agent crawl path에서만 비활성화하고 shared crawler 제거는 후속으로 분리 |
| `BaeminAuthState` enum에 Coupang 세부 상태를 바로 추가 | 기존 exact-member 테스트와 도메인 의미가 깨질 수 있음 | account state는 coarse gate로 유지하고 `auth_recovery_state`에 세부 상태 저장 |
| Auth job이 120초 lease를 넘김 | stale recovery가 job을 회수할 수 있음 | JobRunner active_jobs + heartbeat lease extension을 검증하고 worker timeout을 bounded로 둠 |
| 실패 error_code를 새 값으로 만들면 retry policy가 오해 | 자동 retry 또는 상태 미갱신 위험 | queue retry category는 `AUTH_REQUIRED`로 유지하고 detail은 result_json/metrics에 둠 |
| `OPEN_AUTH_BROWSER` 테스트가 기존 자동 Coupang 2FA를 기대 | 회귀처럼 보일 수 있음 | 해당 기대값을 `AUTH_COUPANG_2FA` 테스트로 이동 |
| 같은 mailbox 동시 auth job | 중복 OTP 요청, 메일 오인식 | Agent mailbox lock 유지 + server active auth job guard 추가 |

---

## 구현 순서 요약

1. `AUTH_COUPANG_2FA` vocabulary 추가.
2. `OPEN_AUTH_BROWSER`에서 Coupang 자동 2FA 제거.
3. `coupang_gmail_2fa.py`에 `AUTH_COUPANG_2FA` 실행자 추가.
4. `CRAWL_COUPANG` Agent path에서 inline 2FA 비활성화.
5. 서버 `start_auth`와 scheduler가 `AUTH_COUPANG_2FA`를 enqueue하도록 변경.
6. queue complete가 auth job 결과를 account state와 detail로 저장.
7. stale recovery, duplicate guard, dashboard/runbook까지 닫는다.

이 순서대로 가면 첫 PR부터 "크롤 job이 IMAP을 만지지 않는다"는 핵심 효과를 얻고, 이후 서버 주도 상태 전이를 단계적으로 붙일 수 있다.
