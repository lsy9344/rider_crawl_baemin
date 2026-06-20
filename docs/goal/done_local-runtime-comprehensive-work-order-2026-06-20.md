# 로컬 런타임/메시징 안정화 작업 지시서

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` when splitting the implementation across independent workers, or `superpowers:executing-plans` if one worker executes this checklist. Keep this document as the source checklist and update checkboxes as work lands.

작성일: 2026-06-20  
반영 상태: 코드/문서 적용 완료 (2026-06-20)  
대상 저장소: `rider_result_mornitoring`  
근거 문서: `docs/goal/done_local-runtime-comprehensive-review-2026-06-20.md`

**Goal:** 로컬 UI 설정 유실, 쿠팡 잘못된 계정/센터 처리, 카카오/텔레그램 중복 전송, 로컬 비밀값 노출, 문서와 코드 불일치를 줄여 장시간 운영과 다중 대상 운영을 안전하게 만든다.

**Architecture:** 서버/Agent/로컬 UI의 역할 분리는 유지한다. 로컬 UI는 소규모 수동 운영 도구로 남기고, 다중 대상과 장시간 운영은 서버/Agent/dispatcher 경로를 문서상 기본 운영 경로로 둔다. lock은 실제 공유 자원 소유자 경계에 둔다. secret은 설정 파일에서 분리하고 OS 보호 저장소를 기본값으로 한다.

**Tech Stack:** Python 3.10+, Tkinter, Playwright/CDP, FastAPI, SQLAlchemy async, pytest, Windows DPAPI 또는 Credential Manager, Telegram Bot API, KakaoTalk desktop automation.

## 적용 기록

2026-06-20에 Task 1-7의 코드, 테스트, 문서 변경을 적용했다. 적용 범위는 UI 설정 보존과 전체 활성 탭 검증, secret store DPAPI 기본값, run/CDP/profile/Kakao lock, 쿠팡 센터/2FA fail-closed, 쿠팡/배민 parser 보강, Telegram routing snapshot/keyword lock/redaction/local rate limit, architecture docs와 `rider_crawl -> rider_server` import guard다.

Task 8의 자동 검증과 문서 갱신은 반영했다. 로컬 UI 저장, 카카오톡 두 프로세스 전송, 실 쿠팡 계정 mismatch/2FA 수신자 mismatch 수동 점검은 실제 Windows UI/카카오톡/운영 계정이 필요해 이 반영 시점에는 실행하지 않았고, 관련 자동 회귀 테스트로 보강했다.

검증 기록:

- `.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py tests/test_ui_helpers.py tests/test_config.py tests/test_secret_store.py tests/test_architecture.py -q` → 190 passed
- `.venv\Scripts\python.exe -m pytest tests/test_coupang_crawler.py tests/test_coupang_email_2fa.py tests/test_coupang_parser.py tests/test_parser.py -q` → 118 passed
- `.venv\Scripts\python.exe -m pytest tests/test_sender.py tests/test_telegram_commands.py tests/test_redaction.py tests/test_coupang_message.py -q` → 143 passed
- `.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_browser_launcher.py tests/test_baemin_parser.py tests/test_telegram_sender.py tests/server/test_telegram_central_dispatch.py tests/agent/test_agent_package.py -q` → 90 passed

---

## 작업 원칙

- 코드 변경 전 실패 테스트를 먼저 만든다.
- 사용자 설정 파일을 수정하는 작업은 데이터 보존 테스트를 먼저 둔다.
- 오발송 가능성이 있는 경로는 fail-closed를 기본으로 한다.
- lock은 호출자가 아니라 실제 공유 자원을 만지는 함수 안에 둔다.
- 문서 수정은 실제 `deploy/docker-compose.yml`과 실행 entrypoint를 기준으로 한다.
- 기존 unrelated dirty worktree는 되돌리지 않는다.

## 완료 기준

- 10개 이상 UI 설정이 있는 파일에서 일부 탭만 수정해 저장해도 나머지 설정이 보존된다.
- 쿠팡 기대 센터명이 있으면 화면에서 명시적으로 검증되지 않는 한 수집/발송이 중단된다.
- 쿠팡 이메일 2FA는 수신자 계정 확인 전에는 인증 코드를 보내지 않는다.
- 카카오톡 전송은 direct messenger 호출, UI 호출, Agent 호출 모두 같은 process/cross-process lock을 사용한다.
- 같은 CDP/profile은 `LOG_DIR`가 달라도 동시에 준비/실행되지 않는다.
- 로컬 secret store 기본값이 Windows 보호 저장소를 사용하고, 파일 fallback은 명시 opt-in이다.
- 텔레그램 routing 갱신과 message handling은 immutable snapshot으로 동작한다.
- keyword 자동 응답은 긴 crawl lock에 막히지 않는다.
- 개인정보성 명령 원문이 로그에 남지 않는다.
- README와 architecture docs가 backend, scheduler, queue recovery, telegram dispatch, Agent, local UI 역할을 모두 설명한다.
- 관련 pytest가 모두 통과한다.

---

## Task 0: 기준 상태 고정

**Intent:** 작업 시작 전 현재 테스트와 문서 상태를 기록해 회귀를 구분한다.

**Files:** 없음

- [x] **Step 1: 현재 변경 상태 확인**

Run:

```powershell
git status --short
```

Expected:

- 이번 작업과 무관한 변경은 기록만 하고 되돌리지 않는다.

- [x] **Step 2: 관련 테스트 현재 상태 확인**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py tests/test_config.py tests/test_secret_store.py tests/test_architecture.py -q
.venv\Scripts\python.exe -m pytest tests/test_coupang_crawler.py tests/test_coupang_email_2fa.py tests/test_coupang_parser.py tests/test_parser.py -q
.venv\Scripts\python.exe -m pytest tests/test_sender.py tests/test_telegram_commands.py tests/test_redaction.py tests/test_coupang_message.py -q
```

Expected:

- 현재 실패가 있으면 실패 테스트 이름과 원인을 이 작업 문서에 남긴다.

---

## Task 1: UI 설정 저장 유실 방지와 전체 탭 검증

**Intent:** 설정 파일의 10번째 이후 항목을 보존하고, 저장 대상 전체 탭의 위험 설정을 검증한다.

**Files:**

- Modify: `src/rider_crawl/ui_settings.py`
- Modify: `src/rider_crawl/ui.py`
- Test: `tests/test_ui_settings.py`
- Test: `tests/test_ui_helpers.py` 또는 관련 UI test 파일

- [x] **Step 1: 10번째 이후 설정 보존 실패 테스트 추가**

Add test:

```python
def test_save_all_preserves_settings_beyond_rendered_tabs(tmp_path):
    ...
```

Required assertions:

- JSON에 10개 설정을 준비한다.
- UI/store가 `max_tabs=9`로 9개만 로드한다.
- 첫 번째 설정만 수정해 저장한다.
- 저장 후 10번째 설정이 그대로 남아 있다.
- 10번째 설정의 secret ref도 유지된다.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py::test_save_all_preserves_settings_beyond_rendered_tabs -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: 저장 로직을 merge 방식으로 변경**

Required behavior:

- `load_all(max_tabs=9)`가 잘라낸 non-rendered settings를 store가 기억하거나, save 시 기존 파일을 다시 읽어 merge한다.
- UI가 렌더한 index는 새 값으로 교체한다.
- UI가 렌더하지 않은 index는 기존 값을 그대로 유지한다.
- 명시 삭제 기능이 없는 한 save가 항목을 삭제하지 않는다.

- [x] **Step 3: 모든 저장 대상 탭의 쿠팡 2FA 검증 테스트 추가**

Add test:

```python
def test_save_all_validates_coupang_auto_2fa_for_all_active_tabs(...):
    ...
```

Required assertions:

- 선택되지 않은 탭이 Coupang이고 auto 2FA가 켜져 있다.
- email address 또는 app password가 비어 있다.
- 저장 시 validation error가 발생한다.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui_helpers.py::test_save_all_validates_coupang_auto_2fa_for_all_active_tabs -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 4: 저장 전 전체 탭 검증 구현**

Required behavior:

- 저장 대상 전체 settings를 순회한다.
- inactive/empty tab 기준을 명확히 둔다.
- active Coupang tab의 auto 2FA 필수값을 모두 검사한다.
- error message는 몇 번째 탭이 문제인지 포함한다.

- [x] **Step 5: Task 1 검증**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py tests/test_ui_helpers.py -q
```

---

## Task 2: 로컬 secret 저장 정책 통일과 안전 저장소 기본화

**Intent:** 로컬 비밀값을 평문 JSON 기본값에서 OS 보호 저장소 기본값으로 바꾸고, 이메일 인증 주소 정책을 통일한다.

**Files:**

- Modify: `src/rider_crawl/secret_store.py`
- Modify: `src/rider_crawl/ui_settings.py`
- Modify: `src/rider_crawl/config.py` if store selection needs config
- Test: `tests/test_secret_store.py`
- Test: `tests/test_ui_settings.py`
- Test: `tests/test_config.py`

- [x] **Step 1: `verification_email_address` 정책 결정**

Decision:

- 이메일 주소는 개인정보성 인증 값으로 보고 secret store 대상에 포함한다.

Required behavior:

- `_SECRET_FIELDS`에 `verification_email_address`를 포함한다.
- 기존 JSON에 평문 이메일 주소가 있으면 load/save 과정에서 secret ref로 이동한다.

- [x] **Step 2: 이메일 주소 secret migration 테스트 추가**

Add test:

```python
def test_verification_email_address_is_migrated_to_secret_ref(tmp_path):
    ...
```

Required assertions:

- legacy JSON에 `verification_email_address`가 평문으로 있다.
- load/save 후 settings JSON에는 평문 이메일이 없다.
- secret store에는 이메일 주소가 저장된다.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py::test_verification_email_address_is_migrated_to_secret_ref -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 3: Windows 보호 저장소 구현 추가**

Required behavior:

- Windows에서는 DPAPI 또는 Credential Manager store를 기본으로 사용한다.
- 파일 기반 `LocalFileSecretStore`는 fallback으로 유지하되 명시 설정이 있을 때만 사용한다.
- 파일 fallback 사용 시 파일 권한을 현재 사용자 전용으로 제한한다.
- 저장소 종류와 경로는 로그에 남기되 secret 값은 절대 로그에 남기지 않는다.

- [x] **Step 4: secret store 선택 테스트 추가**

Add tests:

```python
def test_default_secret_store_uses_windows_protected_store_on_windows(...):
    ...

def test_file_secret_store_requires_explicit_opt_in(...):
    ...
```

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_secret_store.py tests/test_config.py -q
```

---

## Task 3: CDP/profile 실행 lock과 카카오톡 OS 자동화 lock 강화

**Intent:** 같은 PC에서 shared resource를 두 프로세스가 동시에 만지지 못하게 한다.

**Files:**

- Modify: `src/rider_crawl/config.py`
- Modify: `src/rider_crawl/app.py`
- Modify: `src/rider_crawl/browser_launcher.py`
- Modify: `src/rider_crawl/messengers/kakao.py`
- Modify: `src/rider_crawl/sender.py`
- Test: `tests/test_config.py`
- Test: `tests/test_browser_launcher.py` or existing browser launcher tests
- Test: `tests/test_sender.py`

- [x] **Step 1: run lock root 분리 테스트 추가**

Add test:

```python
def test_runtime_lock_is_independent_from_log_dir_for_same_cdp_profile(...):
    ...
```

Required assertions:

- 서로 다른 `LOG_DIR`를 가진 두 config가 있다.
- CDP endpoint와 browser profile path가 같으면 같은 resource lock key를 만든다.
- lock root가 log directory parent에 묶이지 않는다.

- [x] **Step 2: CDP/profile key 기반 resource lock 구현**

Required behavior:

- normalized CDP endpoint와 normalized profile path를 lock key로 만든다.
- lock directory는 고정된 app state/runtime 위치를 쓴다.
- 기존 run lock은 유지하되 shared browser resource lock을 추가한다.

- [x] **Step 3: `prepare_chrome()` 전체를 lock으로 감싸는 테스트 추가**

Required assertions:

- probe, profile free check, launch, wait가 같은 lock context 안에서 호출된다.
- lock 획득 실패 시 사용자용 메시지가 반환된다.

- [x] **Step 4: 카카오 direct messenger lock 테스트 추가**

Add test:

```python
def test_send_kakao_text_uses_global_lock_for_clipboard_and_hotkeys(...):
    ...
```

Required assertions:

- `send_kakao_text()` 직접 호출도 lock을 사용한다.
- selection, paste, enter, verify가 lock 안에서 실행된다.
- UI가 넘기는 lock callback에 의존하지 않는다.

- [x] **Step 5: cross-process lock 구현**

Required behavior:

- Windows named mutex 또는 lock file을 사용한다.
- lock timeout은 설정 가능하되 기본값을 짧게 두고 사용자 안내를 명확히 한다.
- lock 획득 실패는 재시도 가능한 오류로 처리한다.

- [x] **Step 6: Task 3 검증**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_browser_launcher.py tests/test_sender.py -q
```

---

## Task 4: 쿠팡 센터 검증, 자동 2FA, parser 강화

**Intent:** 잘못된 계정/센터의 데이터를 보내지 않도록 쿠팡 흐름을 fail-closed로 바꾼다.

**Files:**

- Modify: `src/rider_crawl/platforms/coupang/crawler.py`
- Modify: `src/rider_crawl/platforms/coupang/parser.py`
- Modify: `src/rider_crawl/auth/coupang_email_2fa.py`
- Test: `tests/test_coupang_crawler.py`
- Test: `tests/test_coupang_parser.py`
- Test: `tests/test_coupang_email_2fa.py`
- Fixture: `tests/fixtures/` coupang HTML variants if present

- [x] **Step 1: 센터 heading 없음 fail-closed 테스트 추가**

Add tests:

```python
def test_coupang_peak_dashboard_requires_center_confirmation_when_expected_center_set(...):
    ...

def test_coupang_center_mismatch_is_not_swallowed_by_screen_detection(...):
    ...
```

Required assertions:

- expected center가 설정되어 있고 화면 heading이 없으면 crawl fails.
- center mismatch exception은 `current_screen=None`로 삼켜지지 않는다.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_coupang_crawler.py::test_coupang_peak_dashboard_requires_center_confirmation_when_expected_center_set tests/test_coupang_crawler.py::test_coupang_center_mismatch_is_not_swallowed_by_screen_detection -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: 센터 검증 예외 타입 분리**

Required behavior:

- screen detection 실패와 center mismatch를 다른 exception type으로 구분한다.
- expected center가 있으면 명시 검증 성공 전에는 결과를 반환하지 않는다.
- error message에는 expected와 observed를 redacted/safe form으로 포함한다.

- [x] **Step 3: 2FA 계정 확인 전 코드 발송 금지 테스트 추가**

Add tests:

```python
def test_coupang_email_2fa_does_not_send_code_before_recipient_is_verified(...):
    ...

def test_coupang_email_2fa_rejects_domain_only_match(...):
    ...
```

Required assertions:

- 화면 수신자가 없으면 send code button을 누르지 않는다.
- 같은 도메인이지만 local part가 다른 경우 거부한다.
- masked local part가 비교 가능한 경우에만 자동 진행한다.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_coupang_email_2fa.py -q
```

- [x] **Step 4: 2FA 수신자 검증 구현**

Required behavior:

- 화면에서 recipient를 읽는다.
- 설정 email과 full 또는 masked local part 기준으로 비교한다.
- ambiguous하면 수동 확인 오류를 반환한다.
- code 발송은 검증 성공 후에만 수행한다.

- [x] **Step 5: 쿠팡 parser 변형 fixture 추가**

Add fixtures for:

- `목표 / 완료`
- comma numbers like `1,234`
- unit suffix around numbers
- extra whitespace and line breaks
- label alias if 업무 화면에서 확인된 alias가 있다

Required tests:

```python
def test_coupang_parser_accepts_goal_done_label_variants(...):
    ...

def test_coupang_parser_accepts_comma_and_unit_numbers(...):
    ...
```

- [x] **Step 6: Task 4 검증**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_coupang_crawler.py tests/test_coupang_email_2fa.py tests/test_coupang_parser.py -q
```

---

## Task 5: 텔레그램 routing, keyword 응답, 개인정보 마스킹 강화

**Intent:** 다중 chat/topic 운영에서 상태 race와 개인정보 로그를 줄인다.

**Files:**

- Modify: `src/rider_crawl/telegram_commands.py`
- Modify: `src/rider_crawl/keyword_responder.py`
- Modify: `src/rider_crawl/redaction.py`
- Modify: `src/rider_crawl/messengers/__init__.py` if rate limiting is added locally
- Modify: `src/rider_crawl/sender.py`
- Test: `tests/test_telegram_commands.py`
- Test: `tests/test_keyword_responder.py` if present
- Test: `tests/test_redaction.py`
- Test: `tests/test_sender.py`

- [x] **Step 1: routing snapshot race 테스트 추가**

Add test:

```python
def test_telegram_command_handler_uses_single_routing_snapshot_per_update(...):
    ...
```

Required assertions:

- `handle_text()` 시작 후 `update_routing()`이 호출되어도 해당 update는 처음 잡은 snapshot으로 처리된다.
- config와 routing dict가 서로 다른 세대의 값을 섞어 쓰지 않는다.

- [x] **Step 2: immutable routing snapshot 구현**

Required behavior:

- routing config와 derived maps를 하나의 frozen object로 묶는다.
- `update_routing()`은 새 snapshot을 만들어 atomic하게 교체한다.
- `handle_text()`는 시작 시 snapshot 하나를 local variable로 잡는다.

- [x] **Step 3: keyword 응답 lock 분리 테스트 추가**

Required assertions:

- long crawl lock이 잡혀 있어도 keyword auto response는 별도 send lock 또는 queue로 처리된다.
- 동일 keyword 반복은 cooldown으로 제한된다.

- [x] **Step 4: keyword 응답 lock/queue 구현**

Required behavior:

- crawl command lock과 keyword response send lock을 분리한다.
- queue를 쓴다면 queue size와 overflow behavior를 명확히 한다.
- overflow 시 사용자에게 조용히 실패하지 않고 safe log를 남긴다.

- [x] **Step 5: rider command redaction 테스트 추가**

Add tests:

```python
def test_redaction_masks_rider_lookup_command_name_and_phone_suffix(...):
    ...

def test_telegram_command_logs_do_not_include_raw_text_for_rider_lookup(...):
    ...
```

Required assertions:

- `!홍길동1234` 같은 원문은 로그에 그대로 남지 않는다.
- log에는 command type, update id, safe chat/topic만 남는다.

- [x] **Step 6: local Telegram rate limiter 검토 구현**

Required behavior:

- 로컬 direct send는 token/chat 단위 최소 interval을 둔다.
- 서버 dispatch queue가 있는 운영에서는 local direct send를 소규모/수동 경로로 문서화한다.

- [x] **Step 7: Task 5 검증**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_telegram_commands.py tests/test_redaction.py tests/test_sender.py -q
```

---

## Task 6: 배민 parser와 메시지 시간 규칙 보강

**Intent:** HTML 변경과 업무 시간표 오해에 대한 회귀 감지를 추가한다.

**Files:**

- Modify: `src/rider_crawl/parser.py`
- Modify: `src/rider_crawl/message.py` only if 업무 규칙 변경이 확정됨
- Test: `tests/test_parser.py`
- Test: `tests/test_coupang_message.py`
- Fixture: parser HTML variants

- [x] **Step 1: 배민 parser 변형 fixture 추가**

Required variants:

- header order changed
- extra cell inserted
- missing optional cell
- malformed candidate row

Required assertions:

- header가 있으면 header based mapping을 우선한다.
- malformed candidate는 전체 parse를 망치지 않되 warning/debug signal을 남긴다.

- [x] **Step 2: parser fallback 구현**

Required behavior:

- fixed offset만 사용하지 않는다.
- header mapping 실패 시에만 기존 fallback을 사용한다.
- parse failure count를 debug log나 result metadata로 확인 가능하게 한다.

- [x] **Step 3: 주말 피크 시간 업무 규칙 확인**

Decision required:

- `10:55~01:59`가 다음날 새벽을 뜻하는지 확인한다.

If current rule is correct:

- test name and message formatter comment에 "다음날 새벽 포함"을 명시한다.

If current rule is wrong:

- 업무 기준에 맞게 `src/rider_crawl/message.py` 상수를 수정하고 fixture expected message를 갱신한다.

- [x] **Step 4: Task 6 검증**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_parser.py tests/test_coupang_message.py -q
```

---

## Task 7: 아키텍처 문서와 import guard 정리

**Intent:** 문서가 현재 실행 구조를 설명하게 만들고, 패키지 경계 회귀를 테스트로 막는다.

**Files:**

- Modify: `README.md`
- Modify: `docs/module-architecture.md`
- Modify: `docs/project-current-state-and-structure.md`
- Modify: `tests/test_architecture.py`
- Modify: `tests/agent/test_agent_package.py` if existing package boundary tests live there

- [x] **Step 1: 현재 실행 프로세스 목록 문서화**

Required README content:

- backend API
- scheduler
- queue recovery worker
- telegram dispatch worker
- Agent
- local UI
- 각 프로세스의 책임과 함께 실행해야 하는 경우

Required architecture content:

- collect job과 dispatch job 흐름
- local direct send와 server dispatch send의 차이
- 운영 기본 경로와 개발/수동 경로

- [x] **Step 2: Docker Compose와 문서 일치 테스트 추가**

Add test:

```python
def test_architecture_docs_mention_deployed_worker_services(...):
    ...
```

Required assertions:

- `deploy/docker-compose.yml`에 있는 핵심 worker service 이름이 README 또는 architecture docs에 나온다.
- service name만 맞추지 말고 역할 키워드도 확인한다.

- [x] **Step 3: `rider_crawl -> rider_server` import guard 추가**

Add test:

```python
def test_rider_crawl_does_not_import_rider_server(...):
    ...
```

Required assertions:

- `src/rider_crawl/**` AST import에서 `rider_server`가 나오면 실패한다.
- test fixture나 docs 파일은 제외한다.

- [x] **Step 4: dependency count 문서 수정**

Required behavior:

- `docs/module-architecture.md`의 고정 dependency 숫자를 제거하거나 실제 값과 자동 검증되게 한다.
- 숫자 자체가 중요하지 않다면 표현을 "runtime dependency set is intentionally small"로 바꾼다.

- [x] **Step 5: Task 7 검증**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_architecture.py tests/agent/test_agent_package.py -q
```

---

## Task 8: 통합 검증과 운영 체크리스트 갱신

**Intent:** 개별 수정이 서로 충돌하지 않는지 확인하고 운영자가 따라 할 수 있는 체크리스트를 남긴다.

**Files:**

- Modify: `README.md`
- Modify: `docs/project-current-state-and-structure.md`
- Create or Modify: `docs/operations/` runbook if the project already has one

- [ ] **Step 1: 로컬 UI 안전 저장 시나리오 수동 점검** (수동 미실행, 자동 회귀 테스트 보강)

Manual check:

1. 10개 이상 설정이 있는 JSON을 준비한다.
2. UI는 9개 탭만 표시한다.
3. 첫 탭 값을 수정하고 저장한다.
4. 10번째 설정과 secret ref가 남아 있는지 확인한다.
5. 잘못된 쿠팡 2FA 탭이 있으면 저장이 막히는지 확인한다.

- [ ] **Step 2: 카카오 lock 수동 점검** (수동 미실행, 자동 회귀 테스트 보강)

Manual check:

1. 두 프로세스에서 카카오 전송을 거의 동시에 호출한다.
2. 두 번째 프로세스가 lock 대기 또는 명확한 실패를 보이는지 확인한다.
3. clipboard 내용이 서로 섞이지 않는지 확인한다.

- [ ] **Step 3: 쿠팡 fail-closed 수동 점검** (수동 미실행, 자동 회귀 테스트 보강)

Manual check:

1. expected center와 다른 계정으로 로그인한다.
2. 센터 검증 실패가 발송 전 중단되는지 확인한다.
3. 2FA 수신자 불일치 시 코드 발송 전 중단되는지 확인한다.

- [x] **Step 4: 전체 관련 테스트 실행**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py tests/test_ui_helpers.py tests/test_config.py tests/test_secret_store.py tests/test_architecture.py -q
.venv\Scripts\python.exe -m pytest tests/test_coupang_crawler.py tests/test_coupang_email_2fa.py tests/test_coupang_parser.py tests/test_parser.py -q
.venv\Scripts\python.exe -m pytest tests/test_sender.py tests/test_telegram_commands.py tests/test_redaction.py tests/test_coupang_message.py -q
```

- [x] **Step 5: 문서 갱신 확인**

Required content:

- 로컬 UI는 소규모/수동 운영용임을 명시한다.
- 다중 대상 운영 기본 경로는 server, scheduler, recovery, dispatch, Agent임을 명시한다.
- secret store 기본값과 fallback 위험을 설명한다.
- KakaoTalk desktop automation은 단일 PC 공유 자원이라 lock이 필수임을 설명한다.

---

## 권장 작업 순서

1. Task 1: UI 설정 유실 방지
2. Task 4 중 센터 검증과 2FA 계정 확인
3. Task 3: 카카오/CDP/profile lock
4. Task 2: secret store 안전 저장소
5. Task 5: 텔레그램 routing/redaction/keyword
6. Task 7: 문서와 import guard
7. Task 6: parser와 시간 규칙 보강
8. Task 8: 통합 검증

이 순서는 사용자 데이터 손실과 오발송 위험을 먼저 줄이는 순서다.
