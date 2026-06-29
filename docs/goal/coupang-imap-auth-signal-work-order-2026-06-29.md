# 작업 지시서: 쿠팡 이메일 2FA IMAP 인증 실패 실제 신호 세분화

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

작성일: 2026-06-29
상태: 작업 전
대상 저장소: `rider_result_mornitoring`
근거: 2026-06-29 `해운대플러스 수영중앙` 쿠팡 인증 필요 상태 조사, IMAP app password 실패 표시 경로 코드 검토

**Goal:** 쿠팡 이메일 2FA에서 메일함 로그인이 실패했을 때 모든 경우를 `메일 인증 필요`로 뭉개지 않고, 실제 IMAP/provider 신호를 안전한 reason 코드로 분류해 Admin 모니터링 화면에 더 정확한 상태를 표시한다.

**Architecture:** 계정의 큰 gate 상태는 기존처럼 `AUTH_REQUIRED` / `EMAIL_AUTH_REQUIRED` 흐름을 유지한다. 세부 원인은 `result_json.reason`에 안전한 고정 코드로만 남기고, Admin UI는 이 코드를 한글 라벨로 변환한다. 원본 IMAP 서버 응답, 이메일 app password, OTP, 쿠팡 비밀번호는 저장하거나 화면에 노출하지 않는다.

**Tech Stack:** Python, IMAPClient, Playwright/CDP, FastAPI, SQLAlchemy/PostgreSQL, Jinja admin templates, pytest.

---

## 1. 현재 확정 사실

- `src/rider_crawl/auth/imap_2fa.py`의 `_imap_connect()`는 `server.login(email_address, app_password)` 실패를 모두 `ImapAuthError("IMAP 로그인 실패...")`로 바꾼다.
- `src/rider_crawl/auth/imap_2fa.py`의 `imap_host_for_email()`은 미지원 도메인을 `ImapAuthError`로 올리지만, 현재 reason 코드는 없다.
- `src/rider_crawl/auth/coupang_email_2fa.py`의 `_fetch_code()`는 `ImapAuthError`를 `Coupang2faError(email_auth_required=True)`로 감싼다.
- `src/rider_agent/auth/coupang_gmail_2fa.py`의 `recover_coupang_mailbox()`는 `EMAIL_AUTH_REQUIRED` 상태일 때 reason을 항상 `email_auth_required`로 둔다.
- `src/rider_agent/auth/coupang_gmail_2fa.py`의 `execute_auth_coupang_2fa_job()`은 실패 시 바깥 `error_code`를 `AUTH_REQUIRED`로 두고, 세부값을 `result_json.auth_recovery_state`와 `result_json.reason`에 저장한다.
- `src/rider_server/queue/postgres_queue.py`는 `result_json.auth_recovery_state`를 계정 coarse gate로 반영한다.
- `src/rider_server/admin/severity.py`는 `EMAIL_AUTH_REQUIRED` / `email_auth_required`를 `메일 인증 필요`로 매핑한다.
- `src/rider_server/admin/templates/_targets.html`은 대상 카드에 `auth_recovery_detail`을 표시할 수 있다.
- `src/rider_server/admin/templates/_auth_required.html`과 `_jobs_queue.html`은 아직 세부 `result_json.reason`을 우선 표시하지 못하고, 일반 reason/error code 라벨로 떨어질 수 있다.

## 2. 문제 정의

현재는 앱 비밀번호가 틀려도, IMAP 사용이 꺼져 있어도, 미지원 메일 도메인이어도 운영 화면에는 대체로 `메일 인증 필요`로 보인다.

이 상태는 거짓은 아니지만 운영자가 바로 조치하기에는 너무 넓다. 특히 실제 원인이 앱 비밀번호인 경우 운영자는 쿠팡 화면 인증 문제로 오해할 수 있다.

단, 모든 IMAP 로그인 실패를 `앱 비밀번호 틀림`으로 표시하면 눈속임이다. provider가 정확한 신호를 주지 않는 경우가 있으므로, 확실한 신호가 있을 때만 앱 비밀번호 계열로 표시해야 한다.

## 3. 작업 원칙

- 실제 신호만 사용한다.
  - 허용 신호: IMAP exception type, provider가 반환한 인증 실패 응답 문자열, 로컬 도메인 지원 여부.
  - 금지 신호: 단순 추측, 고객명 기반 추정, 모든 `ImapAuthError`를 앱 비밀번호 오류로 일괄 매핑.
- 원본 provider 응답은 저장하지 않는다.
  - classifier 내부에서만 읽고, 결과는 안전한 reason 코드로 버린다.
- 앱 비밀번호나 OTP는 절대 로그, job result, DB, 화면에 남기지 않는다.
- 확실하지 않으면 더 넓은 라벨로 둔다.
  - 예: `mailbox_login_failed` → `메일함 로그인 실패`
  - 기존 호환: `email_auth_required` → `메일 인증 필요`
- Coupang 2FA 보호 계약을 지킨다.
  - selector, timeout, routing, primary login, OTP 입력 흐름은 이번 작업의 변경 대상이 아니다.
  - 필요한 변경은 IMAP 실패 분류와 표시 배선으로 제한한다.

## 4. reason 어휘

신규 reason은 plain string으로 둔다. DB enum migration을 하지 않는다.

| reason 코드 | 화면 라벨 | 사용 조건 |
| --- | --- | --- |
| `mail_app_password_invalid` | `앱 비밀번호 오류` | IMAP provider가 invalid credentials, app-specific password required/invalid 등 앱 비밀번호 또는 인증정보 오류를 강하게 반환한 경우 |
| `imap_access_disabled` | `IMAP 사용 꺼짐` | provider 응답이 IMAP disabled/not enabled/access disabled 계열을 명확히 말하는 경우 |
| `unsupported_email_domain` | `지원하지 않는 메일 도메인` | 로컬 설정상 IMAP host를 지원하지 않는 도메인인 경우 |
| `mailbox_auth_blocked` | `메일함 인증 차단` | provider가 계정 보호, too many attempts, security block, temporary auth block 계열을 명확히 말하는 경우 |
| `mailbox_login_failed` | `메일함 로그인 실패` | 로그인 실패는 맞지만 앱 비밀번호/IMAP 차단을 특정하기 어려운 경우 |
| `email_auth_required` | `메일 인증 필요` | 기존 결과 호환용 fallback |

운영 문구를 더 직접적으로 하고 싶으면 `mail_app_password_invalid`의 라벨만 `앱 비밀번호 틀림`으로 바꿀 수 있다. 단, 이 라벨은 위 조건처럼 실제 provider 신호가 잡힌 경우에만 사용한다.

## 5. 구현 작업

## Task 0 - 기준선 확인과 보호 흐름 추적

**Intent:** 기존 Coupang email 2FA 보호 계약을 깨지 않기 위해 현재 호출 경로와 테스트 기준선을 확인한다.

**Files:** 없음

- [ ] 작업 전 git 상태를 확인한다.

```powershell
git status --short
```

- [ ] 아래 보호 경로의 caller/payload 흐름을 확인한다.

```powershell
rg -n "recover_coupang_session_with_email_2fa|AUTH_COUPANG_2FA|auth_recovery_state|EMAIL_AUTH_REQUIRED|ImapAuthError" src tests
```

- [ ] 현재 관련 테스트 기준선을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\server\test_dashboard_severity.py tests\server\test_admin_dashboard.py tests\server\test_queue_backend.py -q
```

Expected: 기존 main 기준으로 통과해야 한다. 기존 실패가 있으면 실패 테스트명과 원인을 먼저 기록하고, 이번 작업 실패와 분리한다.

## Task 1 - IMAP 인증 실패 reason classifier 추가

**Intent:** 원본 IMAP/provider 신호를 안전한 reason 코드로 바꾼다.

**Files:**

- `src/rider_crawl/auth/imap_2fa.py`
- `tests/test_imap_2fa.py`

### 구현 요구

- [ ] `ImapAuthError`에 선택 필드 `reason: str = "email_auth_required"`를 추가한다.
  - 기존 `ImapAuthError("message")` 호출이 깨지면 안 된다.
  - `str(exc)`는 기존 운영 메시지를 유지한다.

- [ ] 순수 helper를 추가한다.

권장 이름:

```python
def classify_imap_auth_failure(exc: BaseException) -> str:
    ...
```

규칙:

- exception class와 안전하게 정규화한 메시지만 본다.
- raw 메시지를 반환하지 않는다.
- message 정규화는 lowercase, whitespace 축약 정도만 한다.
- email address, app password, OTP처럼 secret일 수 있는 값을 결과에 넣지 않는다.
- 매칭이 애매하면 `mailbox_login_failed`로 둔다.

- [ ] `imap_host_for_email()`에서 미지원 도메인은 `reason="unsupported_email_domain"`을 넣어 `ImapAuthError`를 올린다.
- [ ] `_imap_connect()`에서 `server.login()` 실패 시 `classify_imap_auth_failure(exc)` 결과를 `ImapAuthError.reason`에 넣는다.
- [ ] classifier 테스트를 먼저 추가한다.

권장 테스트:

- `Invalid credentials` 계열 응답 → `mail_app_password_invalid`
- `Application-specific password required` 계열 응답 → `mail_app_password_invalid`
- `IMAP access is disabled` / `IMAP not enabled` 계열 응답 → `imap_access_disabled`
- `too many login attempts` / `security block` 계열 응답 → `mailbox_auth_blocked`
- `LOGIN failed`처럼 너무 넓은 응답 → `mailbox_login_failed`
- 미지원 도메인 → `unsupported_email_domain`
- 원본 메시지에 비밀번호처럼 보이는 값이 있어도 classifier 반환값에는 포함되지 않음

## Task 2 - Coupang 2FA 예외와 auth job result로 reason 전파

**Intent:** IMAP reason이 `AUTH_COUPANG_2FA` job의 `result_json.reason`까지 보존되게 한다.

**Files:**

- `src/rider_crawl/auth/coupang_email_2fa.py`
- `src/rider_agent/auth/coupang_gmail_2fa.py`
- `tests/test_coupang_email_2fa.py`
- `tests/agent/test_coupang_gmail_2fa.py`

### 구현 요구

- [ ] `Coupang2faError`에 선택 필드 `email_auth_reason: str | None = None`을 추가한다.
  - 기존 `email_auth_required` boolean은 유지한다.
  - 기존 테스트와 호출부가 깨지면 안 된다.

- [ ] `_fetch_code()`가 `ImapAuthError`를 잡으면 `getattr(exc, "reason", None)`을 `Coupang2faError.email_auth_reason`으로 넘긴다.
- [ ] `recover_coupang_mailbox()`가 `STATE_EMAIL_AUTH_REQUIRED` 실패 결과를 만들 때 reason을 무조건 `email_auth_required`로 고정하지 않는다.
  - exception chain에서 `Coupang2faError.email_auth_reason` 또는 `ImapAuthError.reason`을 찾아 사용한다.
  - 못 찾으면 기존처럼 `email_auth_required`를 사용한다.

권장 helper:

```python
def default_email_auth_reason(exc: BaseException) -> str:
    ...
```

주의:

- 이 helper는 safe reason 코드만 반환한다.
- raw exception message를 job result에 넣지 않는다.
- `is_email_auth_required` predicate의 기존 의미는 유지한다.

### 테스트 요구

- [ ] `ImapAuthError(reason="mail_app_password_invalid")`가 `Coupang2faError.email_auth_reason`으로 보존되는지 확인한다.
- [ ] `recover_coupang_mailbox()`가 `EMAIL_AUTH_REQUIRED` 결과의 `reason`을 `mail_app_password_invalid`로 남기는지 확인한다.
- [ ] reason이 없으면 기존 `email_auth_required` fallback이 유지되는지 확인한다.
- [ ] `execute_auth_coupang_2fa_job()` 실패 결과의 `result_json.reason`이 safe reason 코드인지 확인한다.
- [ ] `result_json`에 app password, OTP, 원본 IMAP 응답 전문이 들어가지 않는지 확인한다.

## Task 3 - queue/account 저장 동작 회귀 확인

**Intent:** 세부 reason이 queue complete 이후에도 보존되고, 계정 coarse gate는 기존처럼 안전하게 유지되게 한다.

**Files:**

- `src/rider_server/queue/postgres_queue.py` (변경이 꼭 필요할 때만)
- `tests/server/test_queue_backend.py`

### 구현 요구

- [ ] 현재 `postgres_queue.py`가 `result_json.reason`을 그대로 보존한다면 런타임 파일은 변경하지 않는다.
- [ ] 필요한 경우에만 테스트를 보강한다.

테스트 요구:

- `AUTH_COUPANG_2FA` 실패 result:

```json
{
  "auth_state": "AUTH_REQUIRED",
  "auth_recovery_state": "EMAIL_AUTH_REQUIRED",
  "reason": "mail_app_password_invalid"
}
```

Expected:

- job `error_code`는 기존처럼 `AUTH_REQUIRED`
- account auth state는 기존처럼 `AUTH_REQUIRED`
- job `result_json.reason`은 `mail_app_password_invalid`로 보존
- retry 폭주를 만들지 않음

## Task 4 - Admin detail label 매핑 추가

**Intent:** safe reason 코드를 운영자가 바로 이해하는 한글 상태로 보여준다.

**Files:**

- `src/rider_server/admin/severity.py`
- `src/rider_server/admin/routes.py` (필요 시)
- `tests/server/test_dashboard_severity.py`

### 구현 요구

- [ ] `COUPANG_RECOVERY_DETAIL_BY_REASON`에 신규 reason 매핑을 추가한다.

필수 매핑:

```python
"mail_app_password_invalid": "앱 비밀번호 오류",
"imap_access_disabled": "IMAP 사용 꺼짐",
"unsupported_email_domain": "지원하지 않는 메일 도메인",
"mailbox_auth_blocked": "메일함 인증 차단",
"mailbox_login_failed": "메일함 로그인 실패",
```

- [ ] 기존 `email_auth_required` → `메일 인증 필요` 매핑은 유지한다.
- [ ] `coupang_recovery_detail_label()`은 reason 매핑을 state 매핑보다 우선해야 한다.

테스트 요구:

- 신규 reason별 한글 라벨 테스트
- 기존 `EMAIL_AUTH_REQUIRED`만 있는 결과는 계속 `메일 인증 필요`
- `reason="mail_app_password_invalid"`와 `auth_recovery_state="EMAIL_AUTH_REQUIRED"`가 같이 있으면 `앱 비밀번호 오류`가 우선

## Task 5 - Admin target card, 인증 필요 목록, jobs queue 표시 보강

**Intent:** 같은 원인이 화면마다 다르게 보이지 않게 한다.

**Files:**

- `src/rider_server/admin/dashboard_repository_postgres.py`
- `src/rider_server/admin/dashboard_service.py`
- `src/rider_server/admin/templates/_targets.html`
- `src/rider_server/admin/templates/_auth_required.html`
- `src/rider_server/admin/templates/_jobs_queue.html`
- `tests/server/test_admin_dashboard.py`

### 구현 요구

- [ ] 대상 카드는 이미 `auth_recovery_detail`을 우선 표시하므로 신규 label 회귀 테스트만 추가한다.
- [ ] 인증 필요 목록(`_auth_required.html`)도 최신 `AUTH_COUPANG_2FA` result detail을 우선 표시하게 한다.
  - `AuthRequiredRow`에 `auth_recovery_detail: str | None`을 추가하는 방식을 우선 검토한다.
  - 표시 우선순위: `auth_recovery_detail` → `reason | reason_text`
- [ ] jobs queue(`_jobs_queue.html`)도 최근 실패 job의 `result_json.auth_recovery_state` / `result_json.reason` detail을 표시한다.
  - `JobQueueRow`에 `auth_recovery_detail: str | None`을 추가하는 방식을 우선 검토한다.
  - 표시 우선순위: `auth_recovery_detail` → `error_code | reason_text`
- [ ] repository에서 latest job `result_json`을 읽을 때 tenant/target scope를 반드시 지킨다.
- [ ] 기존 generic `AUTH_REQUIRED` 표시가 필요한 다른 job type은 깨지면 안 된다.

테스트 요구:

- `_targets.html`: `mail_app_password_invalid` detail이 있으면 대상 카드 reason에 `앱 비밀번호 오류` 표시
- `_auth_required.html`: 같은 대상이 인증 필요 목록에서도 `앱 비밀번호 오류` 표시
- `_jobs_queue.html`: 최근 실패 job에서도 `앱 비밀번호 오류` 표시
- detail이 없는 기존 row는 기존 `reason_text` fallback 유지
- tenant scope가 다른 고객의 최신 job detail을 섞지 않음

## Task 6 - 보호 테스트와 전체 회귀 검증

**Intent:** Coupang 2FA 보호 계약과 Admin 표시 회귀를 함께 확인한다.

- [ ] focused test를 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_imap_2fa.py tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\server\test_dashboard_severity.py tests\server\test_admin_dashboard.py tests\server\test_queue_backend.py -q
```

- [ ] 보호 테스트 묶음을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
```

- [ ] selector, wait, login, 2FA, CDP, agent-routing 변경이 들어갔다면 실제 headed browser 흐름도 확인한다.

Expected:

- 기존 Coupang primary login / email 2FA / send-code / OTP input / target reopen 흐름은 변하지 않는다.
- `EMAIL_AUTH_REQUIRED`의 coarse gate는 유지된다.
- 세부 reason만 더 정확해진다.

## 6. 수동 검증 시나리오

가능하면 운영 계정이 아닌 테스트 메일함으로 확인한다.

### Scenario A - 앱 비밀번호 오류 신호

1. 쿠팡 인증 계정의 verification email은 유지한다.
2. 테스트용 잘못된 app password를 넣는다.
3. `AUTH_COUPANG_2FA`를 1회 실행한다.
4. DB에서 최신 job result를 확인한다.

```sql
select
  id,
  type,
  status,
  error_code,
  result_json ->> 'auth_recovery_state' as auth_recovery_state,
  result_json ->> 'reason' as reason
from jobs
where type = 'AUTH_COUPANG_2FA'
order by coalesce(completed_at, last_failed_at, claimed_at, created_at) desc
limit 5;
```

Expected:

- `error_code = AUTH_REQUIRED`
- `auth_recovery_state = EMAIL_AUTH_REQUIRED`
- provider 신호가 충분하면 `reason = mail_app_password_invalid`
- Admin 대상 카드 / 인증 필요 목록 / jobs queue에 `앱 비밀번호 오류`

### Scenario B - provider 신호가 애매한 로그인 실패

Expected:

- `reason = mailbox_login_failed`
- Admin label: `메일함 로그인 실패`
- `앱 비밀번호 오류`로 과장 표시하지 않음

### Scenario C - IMAP 사용 꺼짐

Expected:

- provider가 명확히 IMAP disabled/not enabled를 반환하면 `reason = imap_access_disabled`
- Admin label: `IMAP 사용 꺼짐`

### Scenario D - 기존 결과 호환

Expected:

- 과거 job result처럼 `reason = email_auth_required`만 있는 경우 계속 `메일 인증 필요`
- `reason`이 없는 `EMAIL_AUTH_REQUIRED`도 계속 `메일 인증 필요`

## 7. 완료 기준

- 앱 비밀번호/IMAP 로그인 실패가 실제 provider 신호에 따라 안전한 reason 코드로 분류된다.
- 확실한 앱 비밀번호/인증정보 오류 신호가 있을 때 Admin 화면이 `앱 비밀번호 오류`를 표시한다.
- 확실하지 않은 로그인 실패는 `메일함 로그인 실패` 또는 기존 `메일 인증 필요`로 남는다.
- `result_json.reason`에는 safe reason 코드만 저장된다.
- raw IMAP 서버 응답, 이메일 app password, OTP, 쿠팡 비밀번호는 DB/log/UI 어디에도 남지 않는다.
- 대상 카드, 인증 필요 목록, jobs queue가 같은 detail label을 보여준다.
- 기존 `email_auth_required` 데이터와 화면은 하위 호환된다.
- 보호 테스트 묶음이 통과한다.

## 8. 비범위

- Coupang selector, timeout, primary login, send-code, OTP 입력 방식 변경
- `CRAWL_COUPANG` / `AUTH_COUPANG_2FA` job routing 정책 변경
- queue retry 정책 변경
- DB enum migration
- raw IMAP response 저장 또는 화면 표시
- 고객별 임시 하드코딩
