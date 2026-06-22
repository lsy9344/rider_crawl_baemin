# 테스트 자동화 요약 — Story 1.3 (공용 redaction 유틸)

- **워크플로:** bmad-qa-generate-e2e-tests
- **대상 기능:** `src/rider_crawl/redaction.py` (`redact`, `redact_mapping`, `redacted_error_event`, `REDACTED`)
- **테스트 프레임워크:** pytest (`pyproject.toml` — `pythonpath=["src"]`, `testpaths=["tests"]`)
- **실행 인터프리터:** `.venv/Scripts/python.exe` (Windows venv, WSL `python3` 미사용 — 메모리 `dev-env-quirks` 준수)
- **날짜:** 2026-06-13

## 적용 범위

redaction 유틸은 순수 함수 모듈이라 HTTP API/UI E2E 표면이 없다. 따라서 API/브라우저 E2E 대신
**함수 단위 동작 테스트**로 커버리지 갭을 메웠다. 기능 코드(`redaction.py`)는 변경하지 않았고,
`tests/test_redaction.py`에 **갭 보강 테스트만 추가**했다(범위 경계 — 사용자 지시 "auto-apply all discovered gaps in tests").

## 생성/보강한 테스트

### `tests/test_redaction.py` (기존 25 → 52 케이스, +27)

| 테스트 | AC | 메운 갭 |
|---|---|---|
| `test_ac1_phone_variants_are_masked` (6) | AC1 | **하이픈 없는 전화(`01000000000`), 국제 표기(`+82 …`/`+82-…`), 011/017 통신사 변형** — AC1·Task1이 명시하나 기존엔 하이픈 010 한 케이스만 검증 |
| `test_ac1_otp_context_label_variants_are_masked` (5) | AC1 | OTP 문맥 라벨 `code:`/`verification code`/`auth code`/`인증 코드`/`인증코드=` — 기존엔 `인증번호`·`otp=`만 |
| `test_ac1_additional_sensitive_keys_are_masked` (8) | AC1 | `client_secret`/`access_token`/`id_token`/`bot_token`/`api_key`/`apikey`/`credential(s)` key=value — 정규식엔 있으나 미검증 |
| `test_ac2_mapping_does_not_mutate_original` | AC2 | `redact_mapping` 비변경 계약("원본은 변경하지 않는다") |
| `test_ac2_mapping_preserves_tuple_type_and_recurses` | AC2 | tuple 타입 보존 + 튜플 내부 문자열 재귀 마스킹 |
| `test_ac2_mapping_handles_top_level_list` | AC2 | 최상위 list 입력 재귀 처리 |
| `test_ac2_mapping_is_idempotent` | AC2 | `redact_mapping(redact_mapping(x)) == redact_mapping(x)` (기존엔 free-text `redact`만 멱등 검증) |
| `test_redact_coerces_non_string_without_error` (4) | 견고성 | 비문자열 입력(`int`/`None`/`float`/`list`) `str()` 강제 변환, 예외 없이 처리 |

## 커버리지

- **AC1(마스킹 대상):** password/token/refresh/authorization/OTP/phone/email + 프로젝트 고유값(Telegram bot token, `chat_id`, `message_thread_id`, 쿠팡 비밀번호, Gmail token) — 전화/OTP/민감키 **변형까지 확장**.
- **AC2(비잔존·운영 식별자·ref):** free-text + mapping 양방향, `*_ref` 보존, 운영 식별자 보존/옵션 마스킹 — **컨테이너 의미 보존·멱등·비변경 계약 추가**.
- **AC3(에러 이벤트 헬퍼):** `message_redacted`/`error_message_redacted` 생성·비잔존·`code` 보존·ADD-13 envelope 합성 (기존 케이스 유지, 신규 갭 없음).

## 결과

- `pytest tests/test_redaction.py -v` → **52 passed** (25 기존 + 27 신규)
- 전체 회귀 `.venv/Scripts/python.exe -m pytest -q` → **491 passed in 2.45s**
  - 직전 기준선 464 passed → **+27**(신규 케이스 수와 정확히 일치, 회귀 0)
- 누출 점검: `grep -nE '[0-9]{9,}:[A-Za-z0-9_-]{20,}'` → **0건**. fixture는 모두 명백한 가짜값(`8:AAE-fake-…`, `010-0000-0000`, `+82 10-0000-0000`, `rider@example.com`, `fake-secret-value-…`).
- 범위: `git diff -w --stat` 상 제품 코드 변경 없음. 테스트 변경은 `tests/test_redaction.py` 추가분뿐. 기능 코드 `redaction.py` 무변경, 기존 모듈 retrofit 없음.

## 알려진 한계 (테스트로 잠그지 않음 — 의도된 best-effort 경계)

- `redact()`의 OTP 자유 텍스트 매칭은 **라벨 인접 값**만 잡는다. 예: `"OTP is 482913"`처럼
  라벨과 숫자 사이에 단어가 끼면 마스킹되지 않는다(docstring상 best-effort). 구조를 아는
  호출자는 키 기반 `redact_mapping`을 써야 안전하다. → 이 동작을 "secret 누출 허용"으로
  테스트에 박지 않았다. 강화가 필요하면 `_OTP_RE`를 넓히는 **후속 기능 작업**으로 다룬다(이번 스토리 범위 밖).

## 다음 단계

- CI에서 전체 스위트 실행(operations-security-test-contract: "All pass in CI").
- 후속 P3(Agent job 이벤트 `message_redacted`/ADD-6)·P4(에러 응답 `{"error":{message_redacted}}`/ADD-13)에서
  실제 로그/예외 경계에 `redact()`/`redacted_error_event()`를 연결할 때, 그 통합 지점의 E2E/계약 테스트를 별도로 추가한다.
