# IMAP Branch Alignment Work Order

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 현재 로컬 `design_develop` 코드의 크롤링, 쿠팡 로그인 자동 제출, 이메일 2FA 자동복구를 GitHub `origin/imap` 브랜치의 동작과 같게 맞춘다.

**Architecture:** `origin/imap`은 `main`에서 갈라진 안정 브랜치이며, 현재 로컬은 중앙 서버와 `rider_agent` 리팩터링이 추가된 브랜치다. 따라서 전체 덮어쓰기가 아니라 `origin/imap`의 핵심 동작을 현재 구조에 forward-port 한다.

**Tech Stack:** Python 3.10+, Playwright CDP, Tkinter UI, pytest, IMAPClient, 현재 로컬의 `rider_crawl`/`rider_agent`/`rider_server` 구조.

---

## 1. 비교 기준

- 현재 로컬 브랜치: `design_develop`
- 현재 로컬 HEAD: `8dfdb1c feat(admin-ui): darkmatter 테마 적용 + 운영 대시보드 UX 개편`
- 기준 브랜치: `origin/imap`
- 기준 브랜치 HEAD: `d38000a Fix Coupang active rider count`
- 공통 조상: `a34d0d0 chore: gitignore per-tab log dirs (logs*)`
- `origin/imap`은 다른 PC에서 100% 동작하는 기준이므로 동작 의미는 `origin/imap`을 정본으로 본다.
- 현재 로컬에는 커밋되지 않은 변경과 신규 파일이 많다. 구현자는 `git checkout origin/imap -- .` 같은 전체 덮어쓰기를 금지한다.

## 2. 핵심 결론

현재 로컬은 `origin/imap`의 핵심 변경 중 상당 부분이 빠져 있거나 Gmail OAuth 기준으로 되돌아가 있다.

반드시 맞춰야 할 핵심은 4가지다.

1. Gmail OAuth/Google API 방식 제거, Gmail/Naver 공용 IMAP 방식 도입.
2. 쿠팡 로그인 화면에서 React/antd 입력이 제대로 인식되도록 실제 키 입력과 Enter 제출 적용.
3. 쿠팡 2FA 화면에서 재요청 버튼, 보이는 버튼 클릭, 화면 이메일 도메인 교차검증 적용.
4. 쿠팡/배민 크롤링 파서와 수집 흐름을 `origin/imap` 최신 방식으로 복구.

### 2.1 2026-06-15 검토 반영 상세 결론

이 문서는 2026-06-15에 `origin/imap`과 현재 `design_develop` 작업트리를 다시 대조한 결과를 반영한다. 대조는 체크아웃 없이 `git diff --name-status origin/imap -- ...`, `git grep origin/imap`, `rg`, 보조 에이전트 3개 병렬 검토로 수행했다.

현재 상태는 단순한 문구 차이가 아니라 실행 경로의 동작 차이다. UI/exe 단독 실행에서 쿠팡 자동복구, 쿠팡 수행중인원, 배민 달성현황이 `origin/imap`과 다르게 동작한다.

| 영역 | 현재 확인된 상태 | 실행 영향 | 반영 Task |
| --- | --- | --- | --- |
| IMAP 공용 코드 | `src/rider_crawl/auth/codes.py`, `src/rider_crawl/auth/imap_2fa.py`, `tests/test_codes.py`, `tests/test_imap_2fa.py`가 현재 기준으로 삭제 상태 | Gmail/Naver 앱 비밀번호 기반 2FA 수신 불가 | Task 2 |
| AppConfig/UI 설정 | `gmail_2fa_query`, `gmail_credentials_path`, `gmail_token_path`가 남아 있고 `verification_email_*` 필드가 없다 | UI에서 네이버/Gmail IMAP 설정을 입력/저장/실행할 수 없음 | Task 3, Task 4, Task 5 |
| 쿠팡 2FA 복구 | `coupang_email_2fa.py`가 `rider_crawl.auth.gmail`을 import하고 Gmail credentials/token/query를 fetcher에 넘김 | Google OAuth 토큰 없이는 자동복구 실패, 네이버 메일함 사용 불가 | Task 6 |
| 쿠팡 2FA 화면 보강 | `인증 재요청`, `resend`, `_account_matches_screen()` 도메인 교차검증이 없다 | 이미 이메일 패널에 멈춘 화면에서 새 코드 발송 실패, 다른 이메일 도메인 코드 오입력 위험 | Task 6 |
| 쿠팡 수행중인원 | `crawl_performance_snapshot()`가 `peak-dashboard`만 읽고 `current_screen=None`으로 고정 | peak 탭만 열려 있으면 수행중인원 누락, rider 탭만 열려 있으면 peak 수집 실패 가능 | Task 7 |
| 쿠팡 임시 탭 보완 | `_open_target_in_new_tab()`, `_coupang_logged_in_context()`, `_log_page_selection_failure()`가 없다 | peak/rider 한쪽 탭 부재를 로그인 만료처럼 오판하거나 수집 중단 가능 | Task 7 |
| 쿠팡 parser fallback | record table fallback, active rider total 보조 추출이 없다 | 쿠팡 화면 변형에서 current screen 파싱 실패 | Task 7 |
| 배민 달성현황 | `오늘 배달현황` 표 파싱과 `주간 배달 현황` 목표건수 결합이 없다 | 오늘 수행건수/달성률 대신 과거 또는 주간 표 값으로 메시지 전송 가능 | Task 8 |
| 배민 수집 대기 | 센터 ID가 보이면 바로 반환하고 `오늘 배달현황` 렌더를 기다리지 않는다 | 오늘 표가 늦게 뜨는 시간대에 수행건수가 0 또는 오래된 값으로 고정 가능 | Task 8 |
| Agent/reuse | `rider_agent/reuse.py`와 `rider_agent/auth/coupang_gmail_2fa.py`가 Gmail OAuth token primitive를 유지 | `src/rider_crawl/auth/gmail.py` 삭제를 막고 Google 의존성을 계속 요구 | Task 9 |
| 의존성/빌드 | `pyproject.toml`은 Google API 의존성을 유지하고 `rider_crawl_onefile.spec`에 `imapclient` hidden import가 없다 | exe 빌드에서 IMAPClient 누락 또는 런타임 import 실패 가능 | Task 11 |
| 문서/샘플 | README, `.env.example`, `secrets/google/README.md`, `docs/config-samples/ui_settings.sample.json`이 Gmail OAuth/token 기준 | 운영자가 더 이상 맞지 않는 Google Cloud/OAuth 절차를 따르게 됨 | Task 11 |

### 2.2 현재 반영된 항목과 보존할 항목

아래 항목은 누락이 아니라 현재 구조로 이미 존재하거나, `origin/imap`에는 없지만 보존해야 하는 로컬 리팩터링이다.

- Python UI/exe 실행 경계는 유지한다: `python -m rider_crawl`, `run_once()`, Tkinter 9개 탭, 플랫폼 registry.
- 쿠팡 `peak-dashboard` 기본 파싱과 센터 검증 일부는 유지하되, `rider-performance` 보조 조회를 다시 추가한다.
- 쿠팡 parser의 `online_riders -> active_riders` 의미 일부는 이미 들어와 있으므로, 구현자는 이를 되돌리지 않고 record-table fallback만 보강한다.
- 현재 로컬의 `secret_store.py`와 `*_ref` 저장 정책은 보존한다. `origin/imap`처럼 이메일 앱 비밀번호를 UI JSON에 평문 저장하지 않는다.
- 서버/Admin/Agent 구조는 `origin/imap`에 없는 새 구조다. 전체 덮어쓰기로 없애지 않고, IMAP 설정값을 현재 구조에 맞게 연결한다.

### 2.3 실행 우선순위

UI/exe 단독 실행 복구가 1차 목표다. 아래 순서로 작업한다.

1. Task 2~6: IMAP 2FA, UI 설정, 쿠팡 자동복구를 먼저 복구한다.
2. Task 7~8: 쿠팡/배민 수집 값이 `origin/imap`과 같아지게 한다.
3. Task 9: Agent가 Gmail OAuth primitive를 계속 붙잡지 않게 한다.
4. Task 10: 중앙 서버/Admin에서도 IMAP 자동복구가 필요할 때만 DB/API/Admin까지 확장한다.
5. Task 11~12: 문서, 의존성, exe 빌드, 전체 검증으로 정리한다.

## 3. `origin/imap`에서 가져올 커밋 의미

구현자는 아래 커밋을 기능 단위로 읽고 반영한다.

- `afb01c4 쿠팡 로그인자동 로직 수정`
  - 쿠팡 로그인 입력을 `.fill()` 중심에서 실제 키 입력과 Enter 제출 중심으로 바꾼다.
- `480f82d feat: 이메일 2FA 자동복구를 IMAP(Gmail/Naver 공용)으로 통일`
  - `auth/codes.py`, `auth/imap_2fa.py` 추가.
  - Gmail OAuth, token 파일, credentials 파일 경로 제거.
- `5c9b03f fix(imap): INTERNALDATE를 aware로 받아 requested_after 컷오프 정확화`
  - `IMAPClient.normalise_times = False` 적용.
- `bbac0ee fix(2fa): 이메일 패널 재발송 상태에서 '인증 재요청'으로 코드 발송 트리거`
  - 이미 코드 입력 단계에 멈춘 화면에서도 새 인증코드를 받는다.
- `2ade51d fix(ui): 자동복구 자격증명 검증을 시작/실행하는 탭에만 적용`
  - 다른 탭 설정 때문에 현재 시작 탭이 막히지 않게 한다.
- `09ef669 fix(imap): INBOX 외 분류/스팸 폴더도 검색`
  - 네이버 프로모션 등 자동 분류 폴더까지 검색한다.
- `37a18f2 feat(coupang): 모든 쿠팡이츠 탭에서 수행중인원 수집`
  - `rider-performance` 보조 조회, 임시 탭 열기, false login-expired 방지.
- `456d9a6 feat(baemin): 달성현황 메시지를 오늘 수행건수/주간 목표건수로 결합`
  - `오늘 배달현황` 표와 `주간 배달 현황` 표를 합친다.
- `d38000a Fix Coupang active rider count`
  - 쿠팡 수행중인원은 `활성 라이더 총계`가 아니라 `온라인 n명`을 쓴다.

## 4. 변경 대상 파일 맵

### 4.1 인증/2FA

- Add: `src/rider_crawl/auth/codes.py`
- Add: `src/rider_crawl/auth/imap_2fa.py`
- Modify: `src/rider_crawl/auth/coupang_email_2fa.py`
- Delete or stop importing: `src/rider_crawl/auth/gmail.py`
- Delete or stop using: `scripts/gmail_authorize.py`
- Modify: `src/rider_crawl/config.py`
- Modify: `src/rider_crawl/ui_settings.py`
- Modify: `src/rider_crawl/ui.py`
- Modify: `src/rider_crawl/secret_store.py`
- Modify: `src/rider_agent/reuse.py`
- Modify: `src/rider_agent/auth/coupang_gmail_2fa.py` or replace it with an IMAP-named compatibility module.

### 4.2 크롤링/파서

- Modify: `src/rider_crawl/crawler.py`
- Modify: `src/rider_crawl/parser.py`
- Modify: `src/rider_crawl/platforms/coupang/crawler.py`
- Modify: `src/rider_crawl/platforms/coupang/parser.py`
- Modify: `src/rider_crawl/message.py`

### 4.3 서버/Agent 연동

- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/domain/platform_account.py`
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/db/models/account.py`
- Add Alembic migration if DB columns are added.
- Modify when Admin UI edits those refs: `src/rider_server/admin/templates/_entity_admin.html`
- Modify when Admin CRUD validates those refs: `src/rider_server/services/admin_entity_service.py`
- Modify when PostgreSQL repository persists those refs: `src/rider_server/services/admin_entity_repository_postgres.py`

### 4.4 테스트

- Add: `tests/test_codes.py`
- Add: `tests/test_imap_2fa.py`
- Delete or rewrite: `tests/test_gmail_2fa.py`
- Modify: `tests/test_coupang_email_2fa.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_ui_settings.py`
- Modify: `tests/test_ui_helpers.py`
- Modify: `tests/test_coupang_crawler.py`
- Modify: `tests/test_coupang_parser.py`
- Modify: `tests/test_crawler.py`
- Modify: `tests/test_parser.py`
- Modify: `tests/agent/test_agent_package.py`
- Modify: `tests/agent/test_coupang_gmail_2fa.py`

### 4.5 문서/의존성/빌드

- Modify: `pyproject.toml`
- Regenerate: `uv.lock`
- Modify: `rider_crawl_onefile.spec`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/config-samples/ui_settings.sample.json`
- Modify: `secrets/google/README.md`
- Modify or mark obsolete: `docs/coupang-gmail-2fa-implementation.md`
- Restore or supersede: `docs/imap/MERGE_PLAN_naver_2fa.md`

## 5. 작업 원칙

- `origin/imap`의 동작을 정본으로 삼는다.
- 현재 로컬의 서버, Agent, secret-store 리팩터링은 보존한다.
- 즉, UI/exe 단독 실행 경로는 `origin/imap`과 같은 UX와 런타임 동작을 가져야 한다.
- 서버/Agent 경로는 `origin/imap`에 없는 새 구조이므로, 같은 IMAP 설정값을 현재 구조에 맞게 연결한다.
- Gmail OAuth 관련 이름은 새 코드에서 만들지 않는다. 기존 호환 테스트가 필요하면 일시 alias만 두고 최종 public 용어는 `email_2fa` 또는 `imap_2fa`로 바꾼다.
- 인증번호, 쿠팡 비밀번호, 이메일 앱 비밀번호는 로그, 예외, audit, result JSON에 남기지 않는다.
- 이메일 주소 전체도 로그에는 남기지 않는다. 필요하면 `r***@naver.com`처럼 마스킹한다.

## 6. Task 1 - 기준 안전망과 브랜치 상태 고정

**Files:**
- No code changes.

- [ ] **Step 1: 현재 상태 기록**

Run:

```powershell
git status --short --branch
git log --oneline --decorate --left-right --cherry-pick origin/imap...HEAD
git diff --name-status main..origin/imap -- src tests pyproject.toml README.md .env.example
```

Expected:

- `origin/imap` 쪽에는 `a7a2c56`부터 `d38000a`까지 IMAP/크롤링 커밋이 보인다.
- 현재 로컬에는 중앙 서버/Admin/Agent 관련 변경이 별도로 많다.

- [ ] **Step 2: 줄끝 차이 확인**

Run:

```powershell
git diff --check -- src/rider_crawl/auth src/rider_crawl/platforms/coupang src/rider_crawl/crawler.py src/rider_crawl/parser.py
```

Expected:

- 실제 공백 오류가 있으면 먼저 고친다.
- 단순 CRLF 경고만 보고 전체 파일을 무의미하게 재저장하지 않는다.

- [ ] **Step 3: 검토 반영 누락 상태 재확인**

Run:

```powershell
git diff --name-status origin/imap -- `
  src/rider_crawl/auth/codes.py `
  src/rider_crawl/auth/imap_2fa.py `
  tests/test_codes.py `
  tests/test_imap_2fa.py `
  src/rider_crawl/config.py `
  src/rider_crawl/ui_settings.py `
  src/rider_crawl/ui.py `
  src/rider_crawl/auth/coupang_email_2fa.py `
  src/rider_crawl/platforms/coupang/crawler.py `
  src/rider_crawl/platforms/coupang/parser.py `
  src/rider_crawl/crawler.py `
  src/rider_crawl/parser.py `
  pyproject.toml `
  rider_crawl_onefile.spec `
  README.md `
  .env.example `
  docs/config-samples/ui_settings.sample.json

rg "rider_crawl.auth.gmail|gmail_2fa|gmail_credentials|gmail_token|GMAIL_|Google Cloud|gmail_authorize|credentials.gmail|token.gmail" `
  src tests pyproject.toml rider_crawl_onefile.spec README.md .env.example docs/config-samples secrets/google scripts

rg "verification_email|imap_2fa|IMAP_HOST_BY_DOMAIN|인증 재요청|_account_matches_screen|has_today_delivery_status|_open_target_in_new_tab|force_new_tab" `
  src/rider_crawl tests
```

Expected before implementation:

- `src/rider_crawl/auth/codes.py`, `src/rider_crawl/auth/imap_2fa.py`, `tests/test_codes.py`, `tests/test_imap_2fa.py` are shown as missing relative to `origin/imap`.
- Gmail OAuth/token matches remain in `src/rider_crawl/config.py`, `src/rider_crawl/ui.py`, `src/rider_crawl/ui_settings.py`, `src/rider_crawl/auth/coupang_email_2fa.py`, docs, and tests.
- IMAP names are mostly absent from runtime code except historical docs or this work order.

Expected after implementation:

- No runtime file imports `rider_crawl.auth.gmail`.
- No runtime file reads `gmail_credentials_path`, `gmail_token_path`, or `gmail_2fa_query`.
- `verification_email_*`, `imap_2fa`, `IMAP_HOST_BY_DOMAIN`, `인증 재요청`, `_account_matches_screen`, `has_today_delivery_status`, `_open_target_in_new_tab`, and `force_new_tab` exist in the relevant runtime/test files.

## 7. Task 2 - IMAP 공용 코드 도입

**Files:**
- Create: `src/rider_crawl/auth/codes.py`
- Create: `src/rider_crawl/auth/imap_2fa.py`
- Test: `tests/test_codes.py`
- Test: `tests/test_imap_2fa.py`

- [ ] **Step 1: `origin/imap`의 두 파일을 그대로 가져온다**

Use source:

```powershell
git show origin/imap:src/rider_crawl/auth/codes.py
git show origin/imap:src/rider_crawl/auth/imap_2fa.py
```

Implementation requirements:

- `codes.py`
  - `extract_verification_code(text, *, code_digits)` 제공.
  - 인증 문맥 단어 근처 코드를 우선 추출.
  - fallback은 인증 의도 단어가 있고 같은 자리수 숫자가 유일할 때만 허용.
- `imap_2fa.py`
  - `IMAP_HOST_BY_DOMAIN`에 `naver.com`, `mail.naver.com`, `gmail.com`, `googlemail.com` 포함.
  - `domain_of`, `imap_host_for_email`, `fetch_latest_verification_code`, `Imap2faError` 제공.
  - Gmail 앱 비밀번호 공백 제거.
  - `server.normalise_times = False` 적용.
  - `INTERNALDATE`를 UTC aware datetime으로 비교.
  - `BODY.PEEK[]`와 readonly select 사용.
  - INBOX뿐 아니라 분류/스팸/프로모션 후보 폴더도 검색하되 보낸함, 임시보관함, 휴지통, 전체보관함, 보관함은 제외.
  - 제목 키워드와 발신자 키워드는 서버 검색이 아니라 클라이언트 필터로 적용.

- [ ] **Step 2: 테스트를 추가한다**

Use source:

```powershell
git show origin/imap:tests/test_codes.py
git show origin/imap:tests/test_imap_2fa.py
```

Required cases:

- naver/gmail 도메인 호스트 선택.
- 미지원 도메인 실패.
- 앱 비밀번호 공백 제거.
- `INTERNALDATE` 컷오프.
- 제목 키워드 필터.
- 발신자 키워드 필터.
- 최신 메일 선택.
- INBOX 외 후보 폴더 검색.
- 코드 미추출 시 `Imap2faError`.

- [ ] **Step 3: 단위 테스트 실행**

Run:

```powershell
python -m pytest -q tests/test_codes.py tests/test_imap_2fa.py
```

Expected:

- All tests pass.

## 8. Task 3 - AppConfig를 Gmail OAuth에서 IMAP 설정으로 전환

**Files:**
- Modify: `src/rider_crawl/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Gmail OAuth 기본값을 제거한다**

Remove from `src/rider_crawl/config.py`:

```python
DEFAULT_GMAIL_CREDENTIALS_PATH
DEFAULT_GMAIL_TOKEN_PATH
DEFAULT_COUPANG_CREDENTIALS_PATH
DEFAULT_GMAIL_2FA_QUERY
DEFAULT_GMAIL_2FA_POLL_SECONDS
DEFAULT_GMAIL_2FA_POLL_INTERVAL_SECONDS
gmail_2fa_settings_from_env()
```

Remove from `AppConfig`:

```python
gmail_credentials_path
gmail_token_path
gmail_2fa_query
gmail_2fa_poll_seconds
gmail_2fa_poll_interval_seconds
coupang_credentials_path
```

- [ ] **Step 2: IMAP 필드를 추가한다**

Add defaults:

```python
DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD = "인증번호"
DEFAULT_EMAIL_2FA_SENDER_KEYWORD = "coupang"
DEFAULT_EMAIL_2FA_POLL_SECONDS = 120
DEFAULT_EMAIL_2FA_POLL_INTERVAL_SECONDS = 5
DEFAULT_COUPANG_2FA_CODE_DIGITS = 6
```

Add to `AppConfig`:

```python
coupang_auto_email_2fa_enabled: bool = False
coupang_login_id: str = ""
coupang_login_password: str = field(default="", repr=False)
verification_email_address: str = ""
verification_email_app_password: str = field(default="", repr=False)
verification_email_subject_keyword: str = DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
verification_email_sender_keyword: str = DEFAULT_EMAIL_2FA_SENDER_KEYWORD
email_2fa_poll_seconds: int = DEFAULT_EMAIL_2FA_POLL_SECONDS
email_2fa_poll_interval_seconds: int = DEFAULT_EMAIL_2FA_POLL_INTERVAL_SECONDS
coupang_2fa_code_digits: int = DEFAULT_COUPANG_2FA_CODE_DIGITS
```

Also make `telegram_bot_token` `repr=False` if not already done:

```python
telegram_bot_token: str = field(default="", repr=False)
```

- [ ] **Step 3: `from_env()`에서 2FA 비밀값 읽기를 제거한다**

`AppConfig.from_env()` must not read:

- `COUPANG_AUTO_EMAIL_2FA_ENABLED`
- `COUPANG_CREDENTIALS_PATH`
- `GMAIL_CREDENTIALS_PATH`
- `GMAIL_TOKEN_PATH`
- `GMAIL_2FA_QUERY`
- `GMAIL_2FA_POLL_SECONDS`
- `GMAIL_2FA_POLL_INTERVAL_SECONDS`

Reason:

- CLI `--once`는 탭을 특정할 수 없다.
- IMAP 자동복구는 UI 탭별 설정에서만 켠다.

- [ ] **Step 4: config 테스트 갱신**

Required assertions:

- `repr(AppConfig)`에 `telegram_bot_token`, `coupang_login_password`, `verification_email_app_password`가 나오지 않는다.
- `from_env()`는 Gmail OAuth/env 2FA 값을 읽지 않는다.
- 새 기본값이 `origin/imap`과 같다.

Run:

```powershell
python -m pytest -q tests/test_config.py
```

## 9. Task 4 - UI 설정 모델을 IMAP으로 전환하되 로컬 secret-store 정책 보존

**Files:**
- Modify: `src/rider_crawl/ui_settings.py`
- Modify: `src/rider_crawl/secret_store.py`
- Modify: `tests/test_ui_settings.py`
- Modify: `tests/test_secret_store.py` if classification tests exist.

**Important local difference:**

`origin/imap`은 UI 설정 JSON에 이메일 앱 비밀번호를 평문 저장한다. 현재 로컬은 이미 `telegram_bot_token`, `coupang_login_id`, `coupang_login_password`를 `*_ref`로 분리하는 secret-store 정책이 있다. 이 로컬 정책을 깨지 않는다. 동작은 `origin/imap`과 맞추되, 저장은 현재 로컬 방식에 맞춘다.

- [ ] **Step 1: `UiSettings` 필드 교체**

Remove:

```python
gmail_2fa_query
gmail_credentials_path
gmail_token_path
```

Add:

```python
verification_email_address: str = ""
verification_email_app_password: str = ""
verification_email_subject_keyword: str = DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
verification_email_sender_keyword: str = DEFAULT_EMAIL_2FA_SENDER_KEYWORD
verification_email_app_password_ref: str = ""
```

Keep current local identity fields:

```python
customer_id
customer_name
platform_account_id
monitoring_target_id
legacy_alias
telegram_bot_token_ref
coupang_login_password_ref
coupang_login_id_ref
```

- [ ] **Step 2: secret-store 대상에 이메일 앱 비밀번호를 추가**

In `src/rider_crawl/ui_settings.py`, extend `_SECRET_FIELDS`:

```python
_SECRET_FIELDS = (
    "telegram_bot_token",
    "coupang_login_password",
    "coupang_login_id",
    "verification_email_app_password",
)
```

In `src/rider_crawl/secret_store.py`, extend `SECRET_STORAGE_CLASSIFICATION`:

```python
"verification_email_app_password": SECRET_STORAGE_AGENT_LOCAL,
"verification_email_address": SECRET_STORAGE_AGENT_LOCAL,
```

If the team decides email address is operational metadata rather than secret, keep `verification_email_address` out of `_SECRET_FIELDS`, but still never log the full address.

- [ ] **Step 3: `to_app_config()` 매핑 변경**

Map these fields:

```python
coupang_auto_email_2fa_enabled=self.coupang_auto_email_2fa_enabled,
coupang_login_id=self.coupang_login_id,
coupang_login_password=self.coupang_login_password,
verification_email_address=self.verification_email_address.strip(),
verification_email_app_password=self.verification_email_app_password,
verification_email_subject_keyword=(
    self.verification_email_subject_keyword or DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
),
verification_email_sender_keyword=(
    self.verification_email_sender_keyword or DEFAULT_EMAIL_2FA_SENDER_KEYWORD
),
```

- [ ] **Step 4: 로드 마이그레이션 정책 고정**

Old keys must be ignored:

```json
{
  "gmail_2fa_query": "from:(donotreply@coupang.com) subject:(인증번호)",
  "gmail_credentials_path": "secrets/google/credentials.gmail.json",
  "gmail_token_path": "secrets/google/token.gmail.json"
}
```

Expected after load:

- No crash.
- New verification email fields default to empty/default values.
- User must enter app password again.

- [ ] **Step 5: 테스트 실행**

Run:

```powershell
python -m pytest -q tests/test_ui_settings.py tests/test_secret_store.py
```

Expected:

- Legacy `gmail_*` keys are ignored.
- New fields round-trip.
- Secret JSON output does not contain app password plaintext if local secret-store policy is retained.

## 10. Task 5 - UI 화면과 저장 검증을 IMAP 방식으로 변경

**Files:**
- Modify: `src/rider_crawl/ui.py`
- Modify: `tests/test_ui_helpers.py`

- [ ] **Step 1: imports 변경**

Add:

```python
from .auth.imap_2fa import IMAP_HOST_BY_DOMAIN
```

Use new config defaults from `config.py` as needed.

- [ ] **Step 2: `coerce_settings()`에서 새 필드 수집**

Remove Gmail fields:

```python
gmail_2fa_query
gmail_credentials_path
gmail_token_path
```

Add:

```python
verification_email_address=str(values.get("verification_email_address", "")).strip(),
verification_email_app_password=str(values.get("verification_email_app_password", "")),
verification_email_subject_keyword=str(
    values.get("verification_email_subject_keyword", defaults.verification_email_subject_keyword)
).strip() or defaults.verification_email_subject_keyword,
```

Do not strip passwords at UI collection time.

- [ ] **Step 3: Tk variables 변경**

Remove:

```python
"gmail_2fa_query"
"gmail_credentials_path"
"gmail_token_path"
```

Add:

```python
"verification_email_address": StringVar(value=settings.verification_email_address),
"verification_email_app_password": StringVar(value=settings.verification_email_app_password),
"verification_email_subject_keyword": StringVar(value=settings.verification_email_subject_keyword),
```

- [ ] **Step 4: 입력 필드 라벨 변경**

Remove labels:

- `Gmail 인증메일 검색식(2FA)`
- `Gmail 자격증명 파일 경로`
- `Gmail 토큰 파일 경로`

Add labels:

- `인증 이메일 주소(naver/gmail)`
- `인증 이메일 비밀번호(앱 비밀번호)`
- `인증 메일 제목 키워드(기본 인증번호)`

Password field condition:

```python
if key in ("coupang_login_password", "verification_email_app_password"):
    entry_kwargs["show"] = "*"
```

- [ ] **Step 5: 시작 전 안내 문구 교체**

The UI must not say Gmail OAuth, token, credentials JSON, or Google Cloud Console.

It must say:

- 인증 이메일은 Gmail/Naver를 IMAP으로 사용한다.
- 공급자는 이메일 주소 도메인으로 자동 선택된다.
- Gmail은 앱 비밀번호의 공백을 그대로 붙여넣어도 된다.
- 자동복구는 탭별 설정이다.
- CLI `--once`는 이메일 자동복구를 지원하지 않는다.

- [ ] **Step 6: 현재 시작/저장 대상 탭에만 2FA 자격증명 검증 적용**

Add or update helper:

```python
def _validate_coupang_auto_2fa_credentials(index: int, settings: UiSettings) -> None:
    if settings.platform_name != "coupang" or not settings.coupang_auto_email_2fa_enabled:
        return
    if not settings.coupang_login_id.strip():
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 쿠팡 로그인 아이디를 입력하세요.")
    if not settings.coupang_login_password:
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 쿠팡 로그인 비밀번호를 입력하세요.")
    address = settings.verification_email_address.strip()
    if not address:
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 인증 이메일 주소를 입력하세요.")
    if not settings.verification_email_app_password:
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 인증 이메일 앱 비밀번호를 입력하세요.")
    domain = address.rsplit("@", 1)[-1].strip().casefold() if "@" in address else ""
    if domain not in IMAP_HOST_BY_DOMAIN:
        raise ValueError(f"크롤링{index + 1} 인증 이메일은 naver.com 또는 gmail.com 주소여야 합니다.")
```

Call this only for the active tab being saved or started. Do not block `크롤링2` because `크롤링7` has missing 2FA values.

- [ ] **Step 7: UI helper tests 실행**

Run:

```powershell
python -m pytest -q tests/test_ui_helpers.py tests/test_ui_settings.py
```

Expected:

- Missing app password blocks only the active Coupang auto-2FA tab.
- Baemin tabs and Coupang tabs with auto-2FA off are not blocked.
- Unsupported email domain is rejected.

## 11. Task 6 - 쿠팡 이메일 2FA 복구 로직을 IMAP으로 교체

**Files:**
- Modify: `src/rider_crawl/auth/coupang_email_2fa.py`
- Modify: `tests/test_coupang_email_2fa.py`

- [ ] **Step 1: imports 교체**

Remove:

```python
import json
from rider_crawl.auth.gmail import Gmail2faError, fetch_latest_verification_code
```

Add:

```python
import re
from rider_crawl.auth.imap_2fa import IMAP_HOST_BY_DOMAIN, Imap2faError, domain_of
```

- [ ] **Step 2: 재요청 버튼 후보 추가**

`_SEND_CODE_TEXTS` must include:

```python
"인증 재요청",
"인증코드 재전송",
"인증번호 재전송",
"인증 재전송",
"resend",
```

- [ ] **Step 3: `_fetch_code()`를 IMAP 인자로 바꾼다**

Required implementation:

```python
def _fetch_code(config: AppConfig, *, requested_after: datetime, fetch_code: Callable | None) -> str:
    fetcher = fetch_code or _imap_fetch
    try:
        code = fetcher(
            email_address=config.verification_email_address,
            app_password=config.verification_email_app_password,
            subject_keyword=config.verification_email_subject_keyword,
            sender_keyword=config.verification_email_sender_keyword,
            requested_after=requested_after,
            poll_seconds=config.email_2fa_poll_seconds,
            poll_interval_seconds=config.email_2fa_poll_interval_seconds,
            code_digits=config.coupang_2fa_code_digits,
        )
    except Imap2faError as exc:
        raise Coupang2faError(str(exc)) from exc
    if not code:
        raise Coupang2faError("이메일에서 인증번호를 받지 못했습니다.")
    return code


def _imap_fetch(**kwargs: Any) -> str:
    from rider_crawl.auth.imap_2fa import fetch_latest_verification_code

    return fetch_latest_verification_code(**kwargs)
```

- [ ] **Step 4: 화면 이메일 도메인 교차검증 추가**

Add:

```python
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%*+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_SUPPORTED_SCREEN_DOMAINS = set(IMAP_HOST_BY_DOMAIN)
```

Add helpers:

```python
def _onscreen_domains(page: Any) -> set[str]:
    text = _safe_page_text(page)
    return {
        domain
        for m in _EMAIL_RE.finditer(text)
        if (domain := m.group(1).casefold()) in _SUPPORTED_SCREEN_DOMAINS
    }


def _account_matches_screen(page: Any, account_address: str) -> bool:
    screen = _onscreen_domains(page)
    if not screen:
        return True
    return domain_of(account_address) in screen
```

After send/resend click and before IMAP polling:

```python
if not _account_matches_screen(page, config.verification_email_address):
    return False
```

- [ ] **Step 5: 쿠팡 계정 JSON fallback 제거**

`_load_coupang_credentials()` must only use:

```python
ui_username = str(getattr(config, "coupang_login_id", "") or "").strip()
ui_password = str(getattr(config, "coupang_login_password", "") or "")
if ui_username and ui_password:
    return ui_username, ui_password
return None
```

Do not read `coupang_credentials_path`.

- [ ] **Step 6: React/antd 로그인 입력을 `origin/imap` 방식으로 맞춘다**

Add `_enter_text()`:

```python
def _enter_text(locator: Any, value: str, timeout: int) -> None:
    try:
        locator.click(timeout=timeout)
        try:
            locator.press("Control+a", timeout=timeout)
            locator.press("Delete", timeout=timeout)
        except Exception:
            pass
        locator.press_sequentially(value, timeout=timeout, delay=30)
        return
    except (AttributeError, TypeError):
        pass
    locator.fill(value, timeout=timeout)
```

Use `_enter_text()` in `_fill_first_input()` and `_fill_code_input()`.

Add `_press_enter_first()` and use it in `_submit_primary_login()` before button click:

```python
_press_enter_first(page, _PASSWORD_INPUT_SELECTORS, config)
_click_first_by_text(page, _LOGIN_BUTTON_TEXTS, config, roles=("button",))
return True
```

- [ ] **Step 7: 보이는 버튼만 클릭하도록 변경**

Add `_click_first_visible()` from `origin/imap` and make `_click_first_by_text()` use it.

Reason:

- 쿠팡 2FA 화면에는 숨은 휴대폰 탭의 같은 버튼이 DOM에 남는다.
- 숨은 버튼을 `.first`로 누르면 timeout이 나거나 다른 인증 수단으로 빠진다.

- [ ] **Step 8: tests 실행**

Run:

```powershell
python -m pytest -q tests/test_coupang_email_2fa.py
```

Expected:

- injected IMAP fetcher receives `email_address`, `app_password`, `subject_keyword`, `sender_keyword`.
- mismatched onscreen domain returns `False`.
- masked/unknown onscreen domain does not block.
- resend button path works.
- JSON credentials fallback tests are removed.

## 12. Task 7 - 쿠팡 크롤링을 `origin/imap` 방식으로 복구

**Files:**
- Modify: `src/rider_crawl/platforms/coupang/crawler.py`
- Modify: `src/rider_crawl/platforms/coupang/parser.py`
- Modify: `src/rider_crawl/message.py`
- Modify: `tests/test_coupang_crawler.py`
- Modify: `tests/test_coupang_parser.py`
- Modify: `tests/test_coupang_message.py`

- [ ] **Step 1: 쿠팡 snapshot 수집 흐름 변경**

`crawl_performance_snapshot()` must:

1. `rider-performance`를 best-effort로 읽어 `current_screen`을 채운다.
2. 보조 페이지 실패는 전체 실패로 보지 않고 `current_screen = None`으로 둔다.
3. `peak-dashboard`는 권위 페이지로 읽고 센터 검증을 수행한다.
4. 반환값은 `PerformanceSnapshot(current_screen=current_screen, peak_dashboard=parse_peak_dashboard_html(peak_dashboard_html))`.

Required behavior:

```python
current_screen = None
try:
    current_screen_html = fetch_page_html(config, target_url=_rider_performance_url(config))
    try:
        current_screen = parse_current_screen_html(current_screen_html)
    except MissingPerformanceDataError:
        current_screen_html = fetch_page_html(
            config,
            target_url=_rider_performance_url(config),
            force_new_tab=True,
        )
        current_screen = parse_current_screen_html(current_screen_html)
    _validate_coupang_center(config, current_screen)
except (BrowserActionRequiredError, MissingPerformanceDataError, RuntimeError):
    current_screen = None
```

- [ ] **Step 2: URL 유도 함수 추가**

Add:

- `_rider_performance_url(config: AppConfig) -> str`
- `_peak_dashboard_url(config: AppConfig) -> str`
- `_path_is(url: str, path: str) -> bool`
- `_replace_url_path(url: str, path: str) -> str`

Rules:

- primary가 `/page/peak-dashboard`면 rider URL은 같은 host의 `/page/rider-performance`.
- primary가 `/page/rider-performance`면 peak URL은 같은 host의 `/page/peak-dashboard`.
- `peak_dashboard_url`이 있으면 peak URL로 우선 사용.

- [ ] **Step 3: `force_new_tab` 인자 추가**

Update signatures:

```python
def fetch_page_html(config: AppConfig, *, target_url: str | None = None, force_new_tab: bool = False) -> str:
def fetch_page_html_via_cdp(config: AppConfig, *, target_url: str | None = None, force_new_tab: bool = False) -> str:
```

Also add `force_new_tab: bool = False` to the existing `_fetch_target_page_content()` keyword-only argument list. Pass `force_new_tab` through CDP only. Persistent context path can keep existing behavior unless it already supports temporary page opening.

- [ ] **Step 4: 임시 탭 열기 추가**

Add `_open_target_in_new_tab()` and `_coupang_logged_in_context()` from `origin/imap`.

Rules:

- Only open for `/page/rider-performance` or `/page/peak-dashboard`.
- Do not open if login-required page is already open.
- Do not open if no logged-in `partner.coupangeats.com` context exists.
- Close the temporary page in `finally`.
- If `force_new_tab=True`, allow a fresh temporary tab even when an existing target tab exists.

- [ ] **Step 5: 탭 탐색 실패 진단 로그 추가**

Add `_log_page_selection_failure()` from `origin/imap`.

Log only:

- CDP URL.
- target host/path.
- open tab host/path list.
- exact/path match counts.
- login page exists.
- logged-in context exists.

Do not log URL query strings.

- [ ] **Step 6: 2FA 복구 실패 로그를 IMAP 기준으로 수정**

`_log_recovery_failure()` must log:

- provider: `naver`, `gmail`, or `unknown`.
- masked email like `r***@naver.com`.
- fixed safe exception message.

It must not log:

- full email.
- app password.
- OTP.
- old `token=`.
- old `query=`.

- [ ] **Step 7: parser를 `origin/imap` 최신으로 맞춘다**

In `src/rider_crawl/platforms/coupang/parser.py`:

- `parse_current_screen_html()` calls `parse_current_screen_text(html_to_text(html))`.
- `parse_current_screen_text()` sets `active_riders=online_riders`.
- available count pattern accepts `0/15` and `0 / 15 명`.
- If standard heading is missing, call `_parse_record_table_current_screen_text()`.
- `_record_table_center_name()` skips:
  - `라이더 기록 - vendor-portal`
  - `Hi there! Please`
  - `enable Javascript`
  - date/time separator lines.
- `_scrapling_text()` tries `Selector`, then `Adaptor`.

- [ ] **Step 8: message 주석과 테스트를 맞춘다**

`src/rider_crawl/message.py` behavior:

- If `snapshot.current_screen is not None`, append `수행중인원: {snapshot.current_screen.active_riders}명`.
- 의미는 온라인 수행중 인원이다.
- `수행중인인원`처럼 `인`이 중복된 기존 문구를 남기지 않는다.
- `origin/imap`의 쿠팡 메시지 기대값과 맞춘다:
  - `거절률: 6.5%`
  - `수행중인원: 3명`
  - 점심 피크 시간 라벨은 `10:55` 기준.
- `current_screen is None`이면 수행중인원 줄을 생략한다. peak-dashboard만으로 임의 수행중인원을 만들지 않는다.

Required tests:

- `tests/test_coupang_message.py` must assert `수행중인원: N명`.
- It must assert the old typo `수행중인인원` is absent.
- It must assert `current_screen=None` does not render a 수행중인원 line.

- [ ] **Step 9: 쿠팡 tests 실행**

Run:

```powershell
python -m pytest -q tests/test_coupang_parser.py tests/test_coupang_crawler.py tests/test_coupang_message.py
```

Expected:

- peak-only 기대 테스트는 없어져야 한다.
- `rider-performance` 탭이 없어도 peak-dashboard 수집은 성공한다.
- 임시 탭은 읽은 뒤 닫힌다.
- 온라인 0명이면 수행중인원도 0명이다.

## 13. Task 8 - 배민 달성현황 파싱과 수집을 `origin/imap` 방식으로 복구

**Files:**
- Modify: `src/rider_crawl/crawler.py`
- Modify: `src/rider_crawl/parser.py`
- Modify: `tests/test_crawler.py`
- Modify: `tests/test_parser.py`

- [ ] **Step 1: parser에 오늘 배달현황 결합 로직 추가**

In `src/rider_crawl/parser.py`, add:

```python
@dataclass(frozen=True)
class _TodayDeliveryStatus:
    lunch_peak: _AchievementPeriod
    afternoon_non_peak: _AchievementPeriod
    dinner_peak: _AchievementPeriod
    dinner_non_peak: _AchievementPeriod
```

Add:

- `_parse_today_delivery_status(text: str, *, center_id: str) -> _TodayDeliveryStatus | None`
- `has_today_delivery_status(text: str, *, center_id: str) -> bool`
- `_combine_today_and_weekly(weekly: _AchievementPeriod, today: _AchievementPeriod | None) -> _AchievementPeriod`

Behavior:

- `오늘 배달현황` 표에서 수행건수와 달성률을 읽는다.
- `주간 배달 현황` 표의 오늘 행에서 목표건수를 읽는다.
- 두 표 모두 설정 센터 ID 행만 사용한다.
- 오늘 행이 있으면 오늘 행을 쓴다. 오늘 값이 0이어도 어제로 내려가지 않는다.
- 오늘 행이 아예 없을 때만 과거 행으로 fallback한다.

- [ ] **Step 2: 수집기는 오늘 표가 렌더될 때까지 기다린다**

In async `_collect_baemin_achievement_report_text()` and sync `_collect_baemin_achievement_report_text_sync()`:

- `has_today_delivery_status(text, center_id=expected_id)`가 true면 바로 반환.
- 주간 표만 있으면 `last_report_text`에 보관하고 더 기다림.
- timeout까지 오늘 표가 없으면 `last_report_text` 반환.
- 설정 센터 ID가 없는 주간 표만 보였으면 명확한 센터 ID 오류를 낸다.

- [ ] **Step 3: scrapling fallback 보존**

If local `parser.py` has `_scrapling_text()`, make it match `origin/imap`:

```python
try:
    from scrapling.parser import Selector
except ImportError:
    try:
        from scrapling.parser import Adaptor
    except ImportError:
        return ""
    page = Adaptor(html)
    text = page.get_all_text()
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
```

- [ ] **Step 4: 배민 tests 실행**

Run:

```powershell
python -m pytest -q tests/test_parser.py tests/test_crawler.py
```

Expected:

- 오늘 행이 0이어도 오늘 날짜를 유지.
- 오늘 배달현황의 수행건수와 달성률을 사용.
- 주간 표의 목표건수를 사용.
- 오늘 배달현황이 늦게 뜨면 수집기가 기다림.

## 14. Task 9 - Agent/reuse 경로를 IMAP 방식으로 재배선

**Files:**
- Modify: `src/rider_agent/reuse.py`
- Modify: `src/rider_agent/auth/coupang_gmail_2fa.py`
- Modify: `tests/agent/test_agent_package.py`
- Modify: `tests/agent/test_coupang_gmail_2fa.py`

**Why this task exists:**

`origin/imap`에는 `rider_agent`가 없다. 현재 로컬에는 Gmail OAuth token 분리용 Agent primitive가 있다. 이 코드는 IMAP 전환 후 그대로 두면 Google OAuth 의존성을 계속 요구하고, `src/rider_crawl/auth/gmail.py` 삭제를 막는다.

- [ ] **Step 1: `rider_agent/reuse.py` export 변경**

Replace:

```python
from rider_crawl.auth.gmail import fetch_latest_verification_code
```

With:

```python
from rider_crawl.auth.imap_2fa import fetch_latest_verification_code
```

Docstring and `__all__` labels must say email/IMAP 2FA, not Gmail OAuth.

- [ ] **Step 2: Gmail token primitive를 IMAP credential primitive로 바꾼다**

Current concepts to remove or rename:

```python
mailbox_token_ref
store_mailbox_token
resolve_mailbox_token
mailbox_token_path
STATE_GMAIL_REAUTH_REQUIRED
ERROR_GMAIL_REAUTH_REQUIRED
REASON_GMAIL_REAUTH
```

Replacement concepts:

```python
mailbox_credential_ref
store_mailbox_app_password
resolve_mailbox_app_password
STATE_EMAIL_AUTH_REQUIRED
ERROR_EMAIL_AUTH_REQUIRED
REASON_EMAIL_AUTH
```

Compatibility option:

- If many tests or server states still depend on `GMAIL_REAUTH_REQUIRED`, keep it temporarily as a deprecated alias, but no runtime behavior may use Google OAuth/token files.

- [ ] **Step 3: `build_coupang_recover()` no longer mutates `gmail_token_path`**

Old behavior:

```python
replace(config, gmail_token_path=mailbox_token_path(config.gmail_token_path, mailbox_id))
```

New behavior:

- Build config with `verification_email_address` and `verification_email_app_password`.
- If values come from job payload or local secret store, resolve them before creating `AppConfig`.
- Do not create per-mailbox token file paths.

Example shape:

```python
mailbox_config = replace(
    config,
    verification_email_address=email_address,
    verification_email_app_password=app_password,
)
```

- [ ] **Step 4: lock remains mailbox based**

Keep `MailboxLockRegistry`.

Reason:

- Same email inbox must not receive overlapping 인증코드 requests.
- Different inboxes may run in parallel.

- [ ] **Step 5: tests rewrite**

Update tests so they assert:

- No `rider_crawl.auth.gmail` import.
- No token path derivation.
- Mailbox refs do not expose full email.
- App password is never in result JSON or logs.
- OTP is not stored.
- Import works without Google packages.

Run:

```powershell
python -m pytest -q tests/agent/test_agent_package.py tests/agent/test_coupang_gmail_2fa.py
```

## 15. Task 10 - Server/Admin 모델에 IMAP 자격증명 refs 연결

**Files:**
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/domain/platform_account.py`
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/db/models/account.py`
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/services/admin_entity_service.py`
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/services/admin_entity_repository_postgres.py`
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/admin/crud_routes.py`
- Modify when server/Agent IMAP recovery is in scope: `src/rider_server/admin/templates/_entity_admin.html`
- Add if DB columns are changed: `migrations/versions/0008_platform_account_email_2fa_refs.py`
- Tests: `tests/server/test_admin_entity_crud.py`, `tests/server/test_db_schema.py`, `tests/server/test_agent_registration_admin.py` if affected.

**Decision point:**

If only the legacy UI/exe path must run, this task can be deferred. If the local central server/Agent path must also perform 쿠팡 IMAP 2FA, this task is required.

- [ ] **Step 1: Add fields for verification email config**

Recommended domain fields:

```python
verification_email_address_ref: SecretRef
verification_email_app_password_ref: SecretRef
verification_email_subject_keyword: str = "인증번호"
verification_email_sender_keyword: str = "coupang"
```

Alternative:

- Store `verification_email_address` as non-secret metadata and only keep `verification_email_app_password_ref` as secret.
- If this alternative is chosen, all logs must still mask full email addresses.

- [ ] **Step 2: Add DB columns**

Recommended PostgreSQL columns:

```python
verification_email_address_ref: Mapped[str] = mapped_column(String, nullable=False, default="")
verification_email_app_password_ref: Mapped[str] = mapped_column(String, nullable=False, default="")
verification_email_subject_keyword: Mapped[str] = mapped_column(String, nullable=False, default="인증번호")
verification_email_sender_keyword: Mapped[str] = mapped_column(String, nullable=False, default="coupang")
```

No column may store app password plaintext.

- [ ] **Step 3: Admin CRUD validation**

Reuse `_secret_ref_or_reject()` for new `*_ref` fields.

Rules:

- Plaintext app password in Admin form must be rejected.
- Empty app password ref is rejected when auto-2FA is enabled for a Coupang account.
- Audit diff contains only refs and keyword metadata.

- [ ] **Step 4: Agent job payload includes only refs or resolved local values**

Preferred contract:

```json
{
  "platform": "coupang",
  "verification_email_address_ref": "vault://platform-account/acct-123/verification-email-address",
  "verification_email_app_password_ref": "vault://platform-account/acct-123/verification-email-app-password",
  "verification_email_subject_keyword": "인증번호",
  "verification_email_sender_keyword": "coupang"
}
```

The Agent must resolve refs through its configured secret resolver before calling `recover_coupang_session_with_email_2fa`.

- [ ] **Step 5: tests 실행**

Run:

```powershell
python -m pytest -q tests/server/test_admin_entity_crud.py tests/server/test_db_schema.py tests/agent/test_crawl_worker.py
```

Expected:

- No plaintext password columns.
- Admin rejects plaintext secrets.
- Agent can construct `AppConfig` with IMAP fields when provided.

## 16. Task 11 - Gmail OAuth 코드, 문서, 의존성 제거

**Files:**
- Modify: `pyproject.toml`
- Regenerate: `uv.lock`
- Modify: `rider_crawl_onefile.spec`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `secrets/google/README.md`
- Modify: `docs/config-samples/ui_settings.sample.json`
- Modify: `docs/coupang-gmail-2fa-implementation.md`
- Restore or replace: `docs/imap/MERGE_PLAN_naver_2fa.md`
- Delete: `scripts/gmail_authorize.py`
- Delete or leave as compatibility shim only during migration: `src/rider_crawl/auth/gmail.py`
- Delete: `tests/test_gmail_2fa.py`

- [ ] **Step 1: dependencies 변경**

Remove:

```toml
"google-api-python-client>=2.0.0"
"google-auth-oauthlib>=1.0.0"
"google-auth-httplib2>=0.2.0"
```

Add:

```toml
"IMAPClient>=3.0.1"
```

Keep if already used by parser:

```toml
"scrapling==0.1.2"
```

Run:

```powershell
uv lock
```

- [ ] **Step 2: PyInstaller hidden import 추가**

In `rider_crawl_onefile.spec`, add `imapclient`:

```python
hiddenimports = [
    "playwright.async_api",
    "playwright.sync_api",
    "pywinauto",
    "psutil",
    "imapclient",
]
```

- [ ] **Step 3: docs에서 Gmail OAuth 안내 제거**

Search:

```powershell
rg "Gmail OAuth|GMAIL_|gmail_credentials|gmail_token|Google Cloud|gmail_authorize|credentials.gmail|token.gmail" README.md .env.example docs secrets src tests
```

Expected after cleanup:

- Only historical docs may mention Gmail OAuth, and those docs must clearly say "현행 아님".
- Runtime docs must describe IMAP + app password.
- `README.md` must not tell users to create Google Cloud OAuth clients for current operation.
- `.env.example` must not expose `GMAIL_*` or `COUPANG_CREDENTIALS_PATH` as active runtime settings.
- `docs/config-samples/ui_settings.sample.json` must use `verification_email_address`, `verification_email_subject_keyword`, and a secret-store-safe `verification_email_app_password_ref` policy if local secret-store is retained.
- `secrets/google/README.md` must either be rewritten as historical/obsolete or reduced to a pointer that Google OAuth is no longer the current 쿠팡 2FA path.
- If `docs/imap/MERGE_PLAN_naver_2fa.md` is restored, it must be clearly marked as background merge history. If it is replaced, the replacement must explain Gmail/Naver IMAP setup and app-password requirements.

- [ ] **Step 4: import smoke**

Run:

```powershell
python -c "import imapclient; import rider_crawl.auth.imap_2fa; import rider_crawl.auth.coupang_email_2fa"
python -c "import rider_agent.reuse"
```

Expected:

- No `ModuleNotFoundError` for Google packages.
- No import of deleted `rider_crawl.auth.gmail`.

## 17. Task 12 - 전체 검증 순서

Run in this order.

- [ ] **Step 1: IMAP unit tests**

```powershell
python -m pytest -q tests/test_codes.py tests/test_imap_2fa.py
```

- [ ] **Step 2: 2FA recovery tests**

```powershell
python -m pytest -q tests/test_coupang_email_2fa.py tests/test_config.py tests/test_ui_settings.py tests/test_ui_helpers.py
```

- [ ] **Step 3: crawler/parser tests**

```powershell
python -m pytest -q tests/test_coupang_parser.py tests/test_coupang_crawler.py tests/test_crawler.py tests/test_parser.py tests/test_coupang_message.py
```

- [ ] **Step 4: Agent/server affected tests**

```powershell
python -m pytest -q tests/agent/test_agent_package.py tests/agent/test_coupang_gmail_2fa.py tests/agent/test_crawl_worker.py
python -m pytest -q tests/server/test_admin_entity_crud.py tests/server/test_db_schema.py
```

- [ ] **Step 5: architecture and full suite**

```powershell
python -m pytest -q tests/test_architecture.py
python -m pytest -q
```

- [ ] **Step 6: diff hygiene**

```powershell
git diff --check
rg "rider_crawl.auth.gmail|gmail_2fa|gmail_credentials|gmail_token|GMAIL_|COUPANG_CREDENTIALS_PATH" src tests pyproject.toml .env.example README.md
```

Expected:

- No runtime references to Gmail OAuth/token path.
- Remaining matches, if any, are explicitly marked historical/obsolete docs.

## 18. Manual 운영 검증 체크리스트

- [ ] 네이버 계정에서 IMAP/SMTP 사용을 켠다.
- [ ] 네이버 2단계 인증 계정이면 앱 비밀번호를 발급한다.
- [ ] Gmail 계정에서 IMAP 사용을 켠다.
- [ ] Gmail 2단계 인증 계정이면 앱 비밀번호를 발급한다.
- [ ] Gmail 앱 비밀번호를 공백 포함 형태로 UI에 붙여넣고 자동복구가 성공하는지 확인한다.
- [ ] 쿠팡 탭에서 자동복구 on, 쿠팡 ID/PW, 인증 이메일 주소, 앱 비밀번호를 입력한다.
- [ ] 로그인 만료 화면에서 1차 로그인 자동 제출이 실제로 진행되는지 확인한다.
- [ ] 이메일 인증 화면에서 `인증코드 전송` 또는 `인증 재요청`이 눌리는지 확인한다.
- [ ] 인증 이메일 도메인과 화면 도메인이 다르면 자동복구가 중단되는지 확인한다.
- [ ] 네이버 프로모션/스팸 등 INBOX 외 폴더에 들어간 인증 메일도 찾는지 확인한다.
- [ ] 로그에 OTP, 앱 비밀번호, 쿠팡 비밀번호, 전체 이메일 주소가 없는지 확인한다.
- [ ] 쿠팡 peak-dashboard 탭만 열려 있어도 rider-performance 임시 탭으로 수행중인원이 채워지는지 확인한다.
- [ ] rider-performance 탭만 열려 있어도 peak-dashboard 임시 탭으로 피크 실적이 수집되는지 확인한다.
- [ ] 배민 달성현황에서 오늘 표가 늦게 뜨는 경우에도 수행건수가 0으로 고정되지 않는지 확인한다.

## 19. 완료 기준

이 작업은 아래 조건을 모두 만족하면 완료다.

- Gmail OAuth/Google API runtime 경로가 제거됐다.
- `src/rider_crawl/auth/imap_2fa.py`가 Gmail/Naver 모두를 처리한다.
- 쿠팡 자동복구는 UI 탭별 쿠팡 계정 + 인증 이메일 앱 비밀번호로 동작한다.
- 쿠팡 로그인 화면은 실제 키 입력과 Enter 제출로 자동 로그인한다.
- 쿠팡 2FA 화면은 전송/재요청 버튼 모두 처리한다.
- 쿠팡 인증 이메일 도메인 교차검증이 적용됐다.
- 쿠팡 수행중인원은 온라인 인원 기준이다.
- 쿠팡 peak/rider 탭 중 하나만 열려 있어도 임시 탭으로 보완한다.
- 배민 달성현황은 오늘 수행건수와 주간 목표건수를 결합한다.
- 현재 로컬의 서버/Admin/Agent 구조가 전체 덮어쓰기 때문에 사라지지 않았다.
- `pyproject.toml`과 `rider_crawl_onefile.spec`가 IMAPClient/exe hidden import를 포함한다.
- README, `.env.example`, config sample, `secrets/google/README.md`가 현행 Gmail OAuth 절차를 안내하지 않는다.
- `tests/test_codes.py`, `tests/test_imap_2fa.py`, `tests/test_coupang_email_2fa.py`, `tests/test_ui_helpers.py`, `tests/test_coupang_crawler.py`, `tests/test_parser.py`가 검토에서 발견된 누락 동작을 직접 검증한다.
- `rg "rider_crawl.auth.gmail|gmail_2fa|gmail_credentials|gmail_token|GMAIL_|COUPANG_CREDENTIALS_PATH" src tests pyproject.toml .env.example README.md` 결과에 runtime 사용이 남지 않는다. 남는 문서 언급은 "현행 아님"으로 표시한다.
- 위 테스트 묶음이 모두 통과한다.

## 20. 금지 사항

- 전체 작업트리를 `origin/imap`으로 덮어쓰기 금지.
- `git reset --hard`, `git checkout -- .` 금지.
- Gmail OAuth token/credentials 경로를 새 코드에 다시 추가 금지.
- `scripts/gmail_authorize.py`를 새 자동복구 플로우에 다시 연결 금지.
- 쿠팡 계정 JSON fallback 재도입 금지.
- 이메일 앱 비밀번호, 쿠팡 비밀번호, OTP를 로그/예외/audit/result JSON에 남기기 금지.
- 다른 탭의 자동복구 설정 오류 때문에 현재 시작하는 탭을 막기 금지.
