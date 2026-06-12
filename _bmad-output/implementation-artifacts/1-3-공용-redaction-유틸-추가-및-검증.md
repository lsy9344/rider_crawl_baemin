---
baseline_commit: f47080d2c63a9020eae89ded760fe2e487e6f631
---

# Story 1.3: 공용 redaction 유틸 추가 및 검증

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want 토큰·비밀번호·OTP·전화번호·이메일 등 민감값을 로그·예외 메시지에서 가리는 공용 redaction 유틸(`src/rider_crawl/redaction.py`)을 추가하고 단위 테스트로 검증하고,
so that 이후 모든 신규 Cloud(`rider_server`)/Agent(`rider_agent`) 코드가 한 곳의 일관된 마스킹 정책을 재사용해 민감값 노출(NFR-5)을 막을 수 있다.

> 본 스토리는 Epic 1(기준선 안전망, P0)의 세 번째 스토리이며 spec 단계 **P0-04**를 구현한다. **Story 1.1/1.2와 달리 실제 제품 코드(파일 1개 신규 + 테스트 1개 신규)를 추가하는 코드 스토리**다. 단, 범위는 **유틸 자체와 그 단위 테스트로 한정**한다 — 기존 모듈(`sender.py`, `auth/coupang_email_2fa.py`, `app.py` 등)의 로그/예외 경계에 redaction을 **이번 스토리에서 끼워 넣지 않는다**(아래 Dev Notes "범위 경계" 참조). 그 retrofit은 각 경계를 소유한 후속 에픽(P3 Agent 이벤트, P4 에러 응답)의 책임이다. 본 스토리의 산출물: ① `src/rider_crawl/redaction.py`(신규 공용 유틸), ② `tests/test_redaction.py`(신규 단위 테스트). **현재 HEAD(`f47080d`) 기준선 전체 스위트(Story 1.2 시점 439 passed — 복사 금지, 실측 확인)는 그대로 통과해야 한다(NFR-20).**

## Acceptance Criteria

**AC1 — 공용 redaction 유틸 추가와 마스킹 대상 (P0-04, NFR-5)**
1. **Given** 신규 redaction 유틸이 필요할 때 **When** `src/rider_crawl/redaction.py`에 공용 redaction 함수를 추가하면 **Then** **password, token, refresh token, authorization code, OTP, full phone number, full email**이 출력 문자열에서 마스킹된다(operations-security-test-contract Log And Artifact Redaction의 "Never log …" 목록과 동일 범위).
2. **And** 동일 목록을 추가 식별 가능한 **Telegram bot token, `chat_id`/`message_thread_id`, 쿠팡 비밀번호, Gmail OAuth/refresh token** 같은 이 프로젝트 고유 민감값도 마스킹된다(project-context 민감값 정책 §81·88·89, NFR-5).
3. **And** 해당 동작을 검증하는 redaction 단위 테스트(`tests/test_redaction.py`)가 통과한다 — 정상 마스킹 케이스를 포함한다.

**AC2 — 원본 secret 부분 문자열 비잔존 + 운영 식별자 정책 (operations-security-test-contract Log And Artifact Redaction)**
4. **Given** redaction 유틸이 로그/예외 경계에 쓰일 때 **When** 민감값을 포함한 임의 문자열(자유 텍스트 + 구조화된 매핑 모두)을 redaction에 통과시키면 **Then** 마스킹된 결과 문자열 안에 **원본 secret의 어떤 연속 부분 문자열(예: 토큰 끝 6자리, OTP 전체, 전화번호/이메일 원문)도 남지 않는다.**
5. **And** 고객명·센터명 같은 운영 식별자(`customer_name`, `center_name`)는 **기본적으로 보존**(운영자 로그에서 필요)되되, 외부 진단 산출물용으로 호출자가 옵션을 켜면(`mask_operational_ids=True` 등) 마스킹된다(operations-security-test-contract: "Customer and center names may exist in operator logs … external diagnostic artifacts need masking options").
6. **And** AC2의 비잔존·운영 식별자 양 방향(보존 기본 / 옵션 마스킹)을 검증하는 테스트가 통과한다.

**AC3 — `message_redacted`/`error_message_redacted` 에러 이벤트 헬퍼 (ADD-6, ADD-13)**
7. **Given** 에러 이벤트 포맷이 필요할 때 **When** 에러를 기록하려고 헬퍼를 호출하면 **Then** `message_redacted`(그리고 예외 객체가 있으면 `error_message_redacted`) 키를 가진 redaction 통과 결과를 만들어 주는 헬퍼가 제공된다 — 출력 형태는 architecture 에러 포맷 `{"error": {"code": "<UPPER_SNAKE>", "message_redacted": "..."}}`(ADD-13)과 job 이벤트 `event_type/severity/message_redacted`(ADD-6)에 그대로 끼울 수 있는 dict를 반환한다.
8. **And** 헬퍼가 만든 dict의 `message_redacted`/`error_message_redacted` 값에도 원본 secret 부분 문자열이 남지 않고(= 내부적으로 AC1/AC2의 `redact()`를 통과), 본문에 secret/OTP가 포함되지 않음을 검증하는 테스트가 통과한다(ADD-6 "본문에 secret/OTP 금지").

## Tasks / Subtasks

- [x] **Task 1 — `src/rider_crawl/redaction.py` 공용 유틸 작성 (AC: 1, 2)**
  - [x] 새 모듈 `src/rider_crawl/redaction.py`를 만든다(architecture Source Tree §409가 지정한 위치·`[신규] 공용 redaction 유틸(P0-04)`). **표준 라이브러리 `re`만 사용**하고 신규 의존성을 추가하지 않는다(project-context 기술스택 규칙 — `crawl4ai`/`playwright` 등 고정 버전 환경에 dep 추가 금지).
  - [x] 핵심 진입 함수 `redact(text: str, *, mask_operational_ids: bool = False) -> str`를 구현한다. 자유 텍스트에서 아래 패턴을 placeholder(예: `***REDACTED***`, 종류별 라벨 가능)로 **완전 치환**한다(부분 노출 금지 — AC2):
    - Telegram bot token (`\d{6,}:[A-Za-z0-9_-]{30,}` 형태),
    - full email (`local@domain` 전체), full phone(한국 `01X-XXXX-XXXX`/하이픈 없는 형태 + 국제 `+82…`),
    - OTP/인증번호(4–8자리 코드 — `code`/`otp`/`인증번호` 문맥 키 인접 값 우선),
    - `key=value`/`key: value`/JSON `"key": "value"` 형태에서 키 이름이 token·password·secret·refresh·authorization·code·otp·chat_id·(message_)?thread_id·credential 류면 값 마스킹.
  - [x] 구조화 입력용 보조 함수 `redact_mapping(data, *, mask_operational_ids=False) -> dict`(권장)를 둔다: dict의 **키 이름 기반**으로 민감 값을 마스킹(자유 텍스트 정규식보다 신뢰도 높음). 중첩 dict/list도 재귀 처리. 키 이름 정본은 data-api-contract/project-context의 민감 키(`telegram_bot_token`, `telegram_chat_id`, `telegram_message_thread_id`, `password`, `*_ref`는 ref이므로 **마스킹 대상 아님** 주의)와 정렬한다.
  - [x] 운영 식별자(`customer_name`, `center_name`, 카카오 방명 등)는 **기본 보존**, `mask_operational_ids=True`일 때만 마스킹하는 분기를 둔다(AC2 #5). 어떤 키/패턴을 운영 식별자로 보는지 모듈 상단에 짧은 정책 주석으로 명시한다(project-context 주석 규칙: 코드만으로 알기 어려운 정책에만 짧게).
  - [x] 마스킹 placeholder는 결과를 다시 redaction에 통과시켜도 안정(idempotent)해야 한다 — placeholder 자체가 secret 패턴에 매칭돼 무한·중복 치환되지 않도록 한다.
- [x] **Task 2 — 에러 이벤트 헬퍼 구현 (AC: 3)**
  - [x] `redacted_error_event(code: str, message: str, error: BaseException | None = None) -> dict`(또는 동등 시그니처)를 구현한다. 반환 dict는 최소 `{"code": code, "message_redacted": redact(message)}`이고, `error`가 주어지면 `"error_message_redacted": redact(str(error))`를 추가한다. `code`는 그대로 두되(UPPER_SNAKE는 호출자 책임) 값 자체는 secret이 아니라고 가정한다.
  - [x] 이 헬퍼가 **내부적으로 `redact()`를 호출**해 AC2(부분 문자열 비잔존)를 자동 보장하도록 한다 — 별도 마스킹 로직을 중복 구현하지 않는다(wheel 재발명 금지).
  - [x] 반환 형태가 architecture 에러 응답(`{"error": {"code","message_redacted"}}` — ADD-13)과 job 이벤트(`event_type/severity/message_redacted` — ADD-6)에 **그대로 합성 가능한 평면 dict**임을 docstring으로 명시한다. 여기서 응답 envelope(`{"error": …}`)까지 만들지는 않는다(그건 P4 API 레이어 책임).
- [x] **Task 3 — `tests/test_redaction.py` 단위 테스트 작성 (AC: 1, 2, 3)**
  - [x] 기존 테스트 컨벤션을 따른다: `tests/` 평면 구조, `test_<module>.py` 명명, `pythonpath=["src"]`로 `from rider_crawl.redaction import redact, redacted_error_event, …` import(pyproject 설정 그대로). [Source: project-context §53–54, pyproject.toml]
  - [x] **AC1 정상 마스킹**: password, token, refresh token, authorization code, OTP, full phone, full email 각각에 대해 redaction 후 원문이 사라졌는지 단언. Telegram bot token/`chat_id`/`thread_id`/쿠팡 비밀번호/Gmail token 케이스도 포함.
  - [x] **AC2 비잔존**: secret을 임의 운영 로그 문장(예: `"sending to chat_id=12345 with token 8:AAExxxx… failed"`)에 섞은 입력에서, 출력에 원본 token 꼬리/`chat_id` 숫자/OTP 전체가 substring으로 없음을 단언(`assert <secret> not in redacted`). 자유 텍스트와 `redact_mapping` 양쪽.
  - [x] **AC2 운영 식별자**: 기본 호출은 `center_name`/`customer_name`을 **보존**, `mask_operational_ids=True`는 마스킹하는 양 방향 단언.
  - [x] **AC3 헬퍼**: `redacted_error_event(code, message, error)`가 `message_redacted`/`error_message_redacted` 키를 만들고, 그 값에 secret 부분 문자열이 없으며, `code`는 보존됨을 단언. envelope 합성 예(`{"error": redacted_error_event(...)}`)가 ADD-13 형태와 일치하는지도 가볍게 확인.
  - [x] **idempotency**: `redact(redact(x)) == redact(x)` 단언(placeholder 재마스킹 안정성).
  - [x] **테스트 fixture에 실제 secret 금지** — 명백히 가짜인 값만 사용(`token "8:AAE-fake-…"`, `phone "010-0000-0000"`, `email "rider@example.com"` 등). 실제 토큰/`chat_id`/전화/이메일을 넣지 않는다(project-context 보안 규칙 §81, ADD-15).
- [x] **Task 4 — 회귀·누출·정합 검증 및 마무리 (AC: 1, 2, 3)**
  - [x] 운영 venv로 전체 스위트를 1회 실행해 회귀 0을 확인한다: `.venv/Scripts/python.exe -m pytest -q`. 기준선(Story 1.2 시점 HEAD `f47080d`에서 439 passed — 참고값, 복사하지 말고 실측) 대비 **기준선에서 통과하던 테스트가 새로 깨지면 안 된다**. 신규 `test_redaction.py` 케이스 수만큼만 passed가 증가하는 게 정상이다. [Source: memory/dev-env-quirks, 1-2 Dev Agent Record]
  - [x] WSL 시스템 `python3`로 실행하지 않는다(pytest 미설치·버전 불일치). Windows `.venv`로만 검증한다.
  - [x] `redaction.py`·`test_redaction.py`에 실제 secret/운영 식별자 원문이 들어가지 않았는지 grep으로 점검한다(NFR-5, ADD-15: token/password/OTP/full phone/email/`chat_id` 평문 금지).
  - [x] 기존 모듈을 건드리지 않았음을 확인한다: `git diff -w --stat`에 `src/rider_crawl/redaction.py`(신규)와 `tests/test_redaction.py`(신규) 외 제품 코드 변경이 없어야 한다(범위 경계 — 본 스토리는 retrofit 금지). CRLF/LF 노이즈·무관 파일은 되돌리지 않는다(project-context 워크플로 규칙).
  - [x] 생성/수정 파일을 File List에 기록한다.

## Dev Notes

### 이 스토리의 성격과 범위 경계 (스코프 크립 방지 — 중요)

- **코드 스토리다(1.1/1.2와 다름).** 산출물은 `src/rider_crawl/redaction.py`(신규)와 `tests/test_redaction.py`(신규) **2개 파일뿐**이다.
- **retrofit 금지(이번 스토리 범위 밖).** 에픽 AC와 P0-04는 "유틸을 **추가하거나 검증**하고 단위 테스트를 통과"시키는 것이다. 기존 `sender.py`·`auth/coupang_email_2fa.py`·`app.py`·`messengers/*`의 print/log/예외 경계에 `redact()`를 끼워 넣는 작업은 **하지 않는다.** 이유: (1) 그 경계들은 각자 후속 에픽(P3 job 이벤트 `message_redacted`/ADD-6, P4 에러 응답 `{"error":{message_redacted}}`/ADD-13)이 소유한다. (2) 기존 동작(렌더링 결과·로그 형식)을 의도 없이 바꾸면 회귀로 취급된다(architecture Enforcement §344–345). 본 스토리는 **재사용 가능한 단일 정책 지점을 먼저 만들어 두는** 토대 작업이다.
- 다만 기존 `src/rider_crawl/auth/coupang_email_2fa.py:10`에는 이미 "인증번호와 토큰 값은 예외 메시지/로그에 넣지 않는다"는 **정책 주석만 있고 재사용 유틸은 없다.** 본 스토리가 그 정책을 코드로 구현한 공용 지점을 제공한다 — 향후 그 모듈이 이 유틸을 import해 정책을 강제할 수 있게 된다(이번엔 연결만 가능하게 두고 실제 연결은 안 함).

### 모듈 배치와 import 재사용 경계

- 위치는 **`src/rider_crawl/redaction.py`**로 못박혀 있다(에픽 본문 + architecture Source Tree §409). 다른 위치(예: `rider_server/`, `rider_agent/`)에 두지 않는다 — 이유: architecture Source Tree상 `rider_crawl/`, `rider_server/`, `rider_agent/`가 모두 `src/` 하위 형제 패키지라, **공용 유틸을 가장 기존이자 공통인 `rider_crawl`에 두면 Cloud/Agent 양쪽이 `from rider_crawl.redaction import redact`로 재사용**할 수 있다(이게 "이후 모든 신규 코드가 일관된 정책 재사용"이라는 So that의 핵심).
- 따라서 이 유틸은 **무거운 의존성을 끌어오면 안 된다**(`rider_crawl` 패키지 import 비용이 Agent/Server까지 전파). `re` + 표준 타이핑만 쓴다. tkinter/playwright/crawl4ai를 import하지 않는다.

### API 설계 가이드 (모호함 제거 — 그대로 구현 권장)

```python
# src/rider_crawl/redaction.py (시그니처 가이드 — 이름은 합의된 형태)
def redact(text: str, *, mask_operational_ids: bool = False) -> str: ...
def redact_mapping(data, *, mask_operational_ids: bool = False) -> dict: ...
def redacted_error_event(
    code: str, message: str, error: BaseException | None = None,
) -> dict: ...
#   -> {"code": code, "message_redacted": redact(message),
#       "error_message_redacted": redact(str(error))?}  # error 있을 때만 후자
```

- **완전 치환 원칙(AC2 핵심):** secret은 부분 노출(끝 4자리 등) 하지 말고 통째로 placeholder로 바꾼다. AC2 #4는 "원본 secret의 어떤 부분 문자열도 남지 않을 것"을 요구한다 — partial-reveal 마스킹은 이 AC를 위반한다.
- **키 기반 > 정규식:** 구조화된 dict(설정 스냅샷, job 이벤트 payload)는 `redact_mapping`의 **키 이름 매칭**으로 처리하는 게 자유 텍스트 정규식보다 안전하다. 자유 텍스트 `redact()`는 알려진 패턴(Telegram token, email, phone, OTP 문맥)에 대한 best-effort임을 docstring에 명시한다. 두 함수를 함께 제공해, 호출자가 구조를 알면 `redact_mapping`을, 모르면 `redact`를 쓰게 한다.
- **`*_ref`는 secret이 아니다:** architecture는 secret을 컬럼/로그에 평문으로 두지 않고 `password_ref`/`username_ref` 같은 **참조만** 남기는 정책이다(architecture §257·343). 따라서 `*_ref` 값은 마스킹 대상이 아니다 — 오히려 ref는 로그에 남아야 추적 가능. 키 매칭 시 `password`는 마스킹, `password_ref`는 보존하도록 구분한다.
- **idempotent:** placeholder(`***REDACTED***` 등)가 다시 secret 패턴(특히 숫자/`:` 포함 토큰 패턴)에 걸려 재치환되지 않도록 placeholder 형태를 고른다. `redact(redact(x)) == redact(x)` 테스트로 고정한다.

### 마스킹 대상 정본 (operations-security-test-contract + project-context)

- **절대 로그 금지(계약 원문):** password, token, refresh token, authorization code, OTP, full phone number, full email. [Source: operations-security-test-contract §15]
- **이 프로젝트 고유 추가 민감값:** Telegram bot token, `telegram_chat_id`, `telegram_message_thread_id`, 쿠팡 비밀번호, Gmail OAuth/refresh token, 인증번호(OTP). [Source: project-context §81·88·89·90·91]
- **운영 식별자(조건부):** customer/center name, 카카오 방명은 운영자 로그엔 **보존 가능**, 외부 진단 산출물엔 **마스킹 옵션 필요**. 기본 보존 + 옵션 마스킹으로 구현. [Source: operations-security-test-contract §16]
- 주의: 쿠팡 탭에서 `baemin_center_name`은 실제로 기대 센터/상점명으로 재사용된다(project-context §88). 즉 "center name" 마스킹 옵션을 만들 때 이 키도 운영 식별자군으로 본다(secret 아님 — 기본 보존).

### 테스트 규칙 (project-context 테스트 규칙 준수)

- `tests/test_redaction.py` 신규. 외부 브라우저/텔레그램/카카오/Gmail을 호출하지 않는 **순수 함수 단위 테스트**라 fake/monkeypatch도 거의 불필요 — 입력 문자열/딕셔너리 → 출력 단언이면 충분하다.
- **fixture에 실제 secret 금지.** 가짜임이 명백한 값만(`010-0000-0000`, `rider@example.com`, `8:AAE-fake-token-0000`, `chat_id=12345`). 이는 ADD-15(secret 평문 저장 금지)·NFR-5와 정렬하며, 동시에 이 테스트 자체가 secret을 흘리지 않게 한다.
- 기존 테스트 패턴 참고: 단순 함수 검증은 `test_lock.py`/`test_config.py` 톤, 다중 케이스는 `pytest.mark.parametrize`를 써도 좋다(기존 스위트에서 사용 중).
- 회귀 기준선: Story 1.2가 `refactoring` HEAD(현재 `f47080d`)에서 캡처한 제품 스위트가 기준이다. 본 스토리는 **신규 파일만 추가**하므로 기존 테스트 수는 그대로 + `test_redaction.py` 케이스만큼 증가해야 한다. 기존 테스트가 새로 깨지면 redaction.py가 의도치 않게 다른 동작을 바꿨다는 신호다(그런 일은 없어야 함 — 신규 독립 모듈이므로).

### Project Structure Notes

- `src/rider_crawl/redaction.py` 신규는 architecture가 명시적으로 예약한 자리다(`└── redaction.py  # [신규] 공용 redaction 유틸(P0-04)`). 기존 패키지 구조·명명(snake_case 모듈, 함수 snake_case)과 정렬. [Source: architecture.md#Source-Tree(409), project-context §66]
- `tests/test_redaction.py`는 기존 `tests/` 평면 미러 구조·`test_<module>.py` 컨벤션과 정렬(co-located 아님). [Source: architecture.md(280–282), project-context §53–54]
- 변경/충돌 없음: 이 스토리는 **순수 추가**다. 기존 공개 동작(렌더링·저장 JSON 호환·탭 로딩·쿠팡 추론)에 영향을 주는 경로가 없다. `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context §64·70]
- dataclass 선호 규칙(project-context §32)은 DTO에 적용된다. redaction은 함수형 유틸이 자연스러우므로 dataclass를 강제하지 않되, 에러 이벤트를 dataclass로 만들 필요는 없다(평면 dict가 ADD-6/ADD-13 합성에 더 직접적).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.3(287-307)] — 본 스토리 user story·AC 원문(3개 Given/When/Then), 경로 `src/rider_crawl/redaction.py` 지정, ADD-6/ADD-13 헬퍼 요구.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P0-04(29)] — "Add or verify redaction utility for token/password/OTP logs / Redaction unit test passes" 계약·수용 조건.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Log-And-Artifact-Redaction(13-19)] — 마스킹 금지 목록(password/token/refresh/authorization code/OTP/full phone/full email), 운영 식별자(고객·센터명) 조건부 보존/마스킹, `message_redacted`/`error_message_redacted` 요구.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Tests(46)] — Unit 테스트 scope에 "redaction" 명시, "All pass in CI".
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Forbidden-Behaviors(93)] — "Store token/password/OTP in logs, DB text fields, screenshots, config files, or error messages" 금지(ADD-15).
- [Source: _bmad-output/planning-artifacts/architecture.md#Source-Tree(409)] — `redaction.py` `[신규] 공용 redaction 유틸(P0-04)` 위치 정본.
- [Source: _bmad-output/planning-artifacts/architecture.md#Security(183-185)] — redaction 로깅 금지 목록 + error 이벤트 `message_redacted`/`error_message_redacted`.
- [Source: _bmad-output/planning-artifacts/architecture.md#Error-Handling(330), #Format(295), #Event(311-312)] — "모든 로그/예외 메시지는 redaction 통과", 에러 포맷 `{"error":{"code","message_redacted"}}`(ADD-13), job 이벤트 `event_type/severity/message_redacted` 본문 secret/OTP 금지(ADD-6).
- [Source: _bmad-output/planning-artifacts/epics.md#ADD-6(134), #ADD-13(141), #NFR-5(90)] — 에러 응답/job 이벤트 포맷 정의, NFR-5 redaction 대상 목록.
- [Source: src/rider_crawl/auth/coupang_email_2fa.py:10] — 기존 "인증번호·토큰 값을 예외/로그에 넣지 않는다" 정책 주석(유틸 없음 — 본 스토리가 공용 구현 제공).
- [Source: project-context.md(36·81·88·89·90·91)] — 파서 오류 명시 예외, 민감값 로컬 파일 한정, 쿠팡 기대 센터명 재사용, OTP/token/chat_id 비노출, 텔레그램 토큰/`chat_id+topic` 정책.
- [Source: project-context.md(52-60·64-70)] — pytest 규칙, `tests/` 컨벤션, 신규 의존성·위치 규칙, 제품 코드 경계(`src/rider_crawl/`만).
- [Source: pyproject.toml(27-32)] — `pythonpath=["src"]`, `testpaths=["tests"]`, `filterwarnings`.
- [Source: _bmad-output/implementation-artifacts/1-2-pytest-기준선-실행과-결과-분류-보관.md] — 직전 스토리: 기준선 ref(`refactoring` HEAD `d4211c3` 당시 422 passed 제품 스위트), venv 실행·secret 비노출·작업 트리 무변경 원칙, `docs/qa/` 컨벤션.
- 요구사항 추적: P0-04(redaction 유틸), FR-2(자산 재사용·기존 테스트 유지), NFR-5(secret 비노출), NFR-20(각 단계 기존 테스트 실행 가능), ADD-6(job 이벤트 포맷), ADD-13(에러 응답 포맷), ADD-15(secret 평문 저장 금지). [Source: epics.md#FR-Coverage-Map(154-189)]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, dev-story 워크플로)

### Debug Log References

- 기준선 실측: `f47080d` HEAD에서 `.venv/Scripts/python.exe -m pytest -q` → **439 passed in 2.37s** (스토리 참고값과 일치, 복사 아닌 실측).
- 신규 단위 테스트: `pytest tests/test_redaction.py -v` → **25 passed in 0.11s**.
- 전체 회귀: `.venv/Scripts/python.exe -m pytest -q` → **464 passed in 3.48s** (= 439 + 25, 신규 케이스 수만큼만 증가, 회귀 0).
- 누출 점검: `grep -nE` 로 `redaction.py`/`test_redaction.py` 안에 실제 토큰/전화/이메일/`chat_id` 평문 없음 확인 — fixture는 모두 명백한 가짜값(`8:AAE-fake-…`, `010-0000-0000`, `rider@example.com`, `1234567`). 9자리+ `digits:token` 실토큰 리터럴 0건.
- 범위 점검: `git status --short` + `git diff -w --stat` — 제품 코드 변경은 신규 2파일뿐(`src/rider_crawl/redaction.py`, `tests/test_redaction.py`). 기존 모듈 retrofit 없음.

### Completion Notes List

- **AC1** — `redact()`가 password/token/refresh token/authorization code/OTP/full phone/full email을 마스킹하고, 프로젝트 고유 민감값(Telegram bot token, `chat_id`, `message_thread_id`, 쿠팡 비밀번호, Gmail refresh token)도 `key=value`/문맥 패턴으로 마스킹. `tests/test_redaction.py`의 AC1 parametrize 13케이스 통과.
- **AC2 비잔존** — 자유 텍스트(`redact`)·구조화 매핑(`redact_mapping`) 양쪽에서 원본 secret의 어떤 연속 부분 문자열(토큰 끝 6자리/OTP 전체/전화·이메일 원문)도 결과에 남지 않음을 단언. 완전 치환(`***REDACTED***`) 원칙으로 partial-reveal 없음.
- **AC2 ref/운영 식별자** — `*_ref` 키는 보존(추적용 참조), `customer_name`/`center_name`/`baemin_center_name`은 기본 보존 + `mask_operational_ids=True`에서만 마스킹하는 양방향 단언 통과.
- **AC3** — `redacted_error_event(code, message, error)`가 `message_redacted`(및 error 있을 때 `error_message_redacted`) 평면 dict 생성, 내부적으로 `redact()`를 호출해 비잔존 자동 보장. `code` 보존. `{"error": …}`(ADD-13) envelope 합성 호환 확인.
- **idempotency** — `redact(redact(x)) == redact(x)` parametrize 3케이스 통과. placeholder가 어떤 secret 패턴에도 재매칭되지 않도록 숫자/`:`/`@` 없는 형태로 설계.
- **범위 경계 준수** — 기존 `sender.py`/`auth/coupang_email_2fa.py`/`app.py`/`messengers/*`의 로그·예외 경계에 redaction을 끼워 넣지 않음(retrofit은 후속 P3/P4 책임). 표준 라이브러리 `re`만 사용, 신규 의존성 0.

### File List

- `src/rider_crawl/redaction.py` (신규) — 공용 redaction 유틸: `redact()`, `redact_mapping()`, `redacted_error_event()`, `REDACTED`. 리뷰 단계에서 `Authorization` 헤더(`_AUTH_HEADER_RE`/`_mask_auth_header`) 마스킹 추가.
- `tests/test_redaction.py` (신규) — 단위 테스트 60케이스(dev-story 25 + QA 갭 보강 27 + 리뷰 후속 8; AC1/AC2/AC3 + idempotency + Authorization 헤더 누출 회귀 가드).

## Change Log

| Date       | Version | Description                                                                 | Author |
|------------|---------|-----------------------------------------------------------------------------|--------|
| 2026-06-13 | 1.0     | 공용 redaction 유틸(`redaction.py`)·단위 테스트(`test_redaction.py`) 신규 추가. 전체 스위트 464 passed(회귀 0). 상태 review로 전환. | Amelia (dev-story) |
| 2026-06-13 | 1.1     | QA 갭 보강 테스트 +27(`test_redaction.py` 25→52). 전체 스위트 491 passed(회귀 0). | QA (bmad-qa-generate-e2e-tests) |
| 2026-06-13 | 1.2     | 코드 리뷰: `Authorization: Bearer <token>` 자격증명 누출(AC2 위반) 수정 — 헤더 전체 마스킹. 회귀 가드 테스트 +8(52→60). 전체 스위트 499 passed(회귀 0). 상태 done으로 전환. | Noah Lee (story-automator-review) |

## Senior Developer Review (AI)

**Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Outcome:** Approved (auto-fix applied)

리뷰는 `.venv/Scripts/python.exe`(Windows venv)로 독립 실측했다. dev/QA 기록의 핵심 수치를 재현 검증: 신규 단위 테스트 통과, 전체 스위트 무회귀, fixture 내 실제 secret 0건, 제품 코드 변경은 신규 2파일뿐(retrofit 없음 — 범위 경계 준수).

### Acceptance Criteria 검증
- **AC1 (마스킹 대상):** PASS — password/token/refresh/authorization code/OTP/full phone/full email + 프로젝트 고유값(Telegram bot token, `chat_id`, `message_thread_id`, 쿠팡 비밀번호, Gmail refresh token) 마스킹 실측 확인.
- **AC2 (부분 문자열 비잔존 + 운영 식별자):** PASS (수정 후) — 자유 텍스트/매핑 양방향 비잔존, `*_ref` 보존, 운영 식별자 보존 기본/옵션 마스킹 모두 확인. 단, 아래 MED-1(`Authorization: Bearer <token>` 누출)을 리뷰에서 수정해 완전 충족시켰다.
- **AC3 (에러 이벤트 헬퍼):** PASS — `redacted_error_event`가 `message_redacted`/`error_message_redacted` 평면 dict 생성, 내부적으로 `redact()` 통과, `code` 보존, ADD-13 envelope 합성 호환.

### Findings
- **CRITICAL:** 없음. `[x]` 표시된 4개 Task 모두 실제 구현 확인, ACL 누락 없음.
- **MED-1 (수정함):** `redact()` 자유 텍스트에서 `Authorization: Bearer <jwt>` 입력 시 `_KEY_VALUE_RE`의 값 캡처가 첫 공백에서 끊겨 스킴 단어(`Bearer`)만 가리고 토큰 본문이 평문 잔존 → AC2("원본 secret의 어떤 연속 부분 문자열도 남지 않는다") 위반. `_AUTH_HEADER_RE`/`_mask_auth_header`로 `authorization` 키 뒤 줄 끝까지 통째 마스킹하도록 수정. `authorization_code=` 경로는 그대로 `key=value`가 처리(회귀 가드 테스트 포함).
- **MED-2 (수정함):** 스토리 File List/Change Log가 dev-story 시점(25케이스/464 passed)에 머물러 현재 파일(52→60케이스/499 passed)과 불일치 → 문서 정정.
- **LOW-1 (수용, best-effort 경계):** OTP 자유 텍스트는 라벨 인접 값만 매칭 → `"OTP is 482913"`처럼 라벨·숫자 사이에 단어가 끼면 미마스킹. test-summary에 known limitation으로 명시됨. 구조를 아는 호출자는 `redact_mapping` 사용 권장. 정규식을 넓히면 임의 4–8자리 ID 오탐이 커져 net-negative라 수정하지 않음.
- **LOW-2 (수용, best-effort 경계):** `_PHONE_RE`가 통신사 prefix로 시작하는 일부 비전화 숫자(예: 주문번호)를 과잉 마스킹할 수 있음. redaction 유틸 특성상 과잉 마스킹은 안전측 trade-off라 유지.

### 실측 (Windows venv)
- `pytest tests/test_redaction.py -q` → **60 passed** (25 dev + 27 QA + 8 리뷰).
- `.venv/Scripts/python.exe -m pytest -q` → **499 passed in 2.33s** (491 → +8, 회귀 0).
- 누출 grep `[0-9]{9,}:[A-Za-z0-9_-]{20,}` → 0건. 범위: 신규 2파일 외 제품 코드 변경 없음, retrofit import 0건.
