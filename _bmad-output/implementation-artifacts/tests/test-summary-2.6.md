# Test Automation Summary — Story 2.6 (SubscriptionGate)

생성일: 2026-06-13 · 작성: QA 자동화 (bmad-qa-generate-e2e-tests) · 프레임워크: pytest

- **Feature:** `SubscriptionGate` 순수 게이트 정책 (FR-6 · FR-30, ADD-9)
- **Run command:** `.venv/Scripts/python.exe -m pytest` (`pyproject.toml` `pythonpath=["src"]`, `testpaths=["tests"]`)

## 컨텍스트

Story 2.6은 **순수 정책/전이 로직** 모듈이라 HTTP/UI 표면이 없다 → 전통적 브라우저 E2E
대상이 아니다. 따라서 "E2E" 는 게이트의 **전체 정책 수명주기를 순수 함수 조합으로 잠그는
워크플로 테스트** + 함수-계약(API-level) 케이스로 실현했다. 기존 24개 테스트가 비운
AC/Task 명세 행위를 **추가(additive)** 로 채웠고, 구현·도메인은 **무변경**(테스트 파일만 확장).

## Generated Tests

### API/Unit Tests (function-contract)
- [x] `tests/server/test_subscription_gate.py` — `evaluate`/`evaluate_status`/`suspend`/`resume`/`hold_undelivered`/`dispose_held` 계약·불변식 잠금

### E2E (full policy-lifecycle workflow)
- [x] `test_full_lifecycle_suspend_hold_recover_dispose_workflow` — 정상 → 중지(작업 차단·미전송 보류·성공분 보존) → 복구 → 운영자 HELD 처리(재개/폐기)를 한 시나리오로 3 불변식 검증

## 적용한 갭 (auto-applied, +27 cases, 24 → 51)

| 갭 (이전 미커버) | AC / Task 근거 | 추가 테스트 |
|---|---|---|
| `suspend` 멱등(이미 SUSPENDED, from==to 기록) | Task 4 "from==to여도 결정론적 기록" | `test_suspend_is_idempotent_when_already_suspended` |
| `resume` 의 `to_status` 오버라이드 경로 | `resume()` 시그니처 affordance | `test_resume_honors_explicit_to_status` |
| fail-closed UNKNOWN 의 `warn_admin=True` | 불변식 ③ | `test_unknown_status_decision_also_warns_admin` |
| 알려진 4개 상태 전수 매핑(UNKNOWN 미누출) | AC1 매핑 완전성 | `test_evaluate_status_maps_every_known_status_without_fail_closed` (×4) |
| `evaluate` ⇔ `evaluate_status` 위임 일치 | delegation 계약 | `test_evaluate_agrees_with_evaluate_status` (×4) |
| 허용/차단 상태 그룹 매핑 | AC1 | `test_allowed_statuses_permit_*` (×2), `test_blocked_statuses_forbid_*` (×2) |
| enum 멤버 집합 잠금(드리프트 가드) | 2.5 `==` 컨벤션 계승 | `test_dispatch_job_status_members_are_locked`, `test_held_disposition_members_are_locked` |
| 불변식 ① 통합(어떤 함수도 SUCCEEDED 부활 금지) | AC2 / Task 5 | `test_no_gate_function_resurrects_succeeded` |
| `hold_undelivered` 합성 멱등(2회 == 1회) | AC2 robustness | `test_hold_undelivered_is_composition_idempotent` |
| `current_period_end=None` 경계 보존 | AC1 보존 edge | `test_suspend_and_resume_preserve_none_period_end` |
| 모든 enum 멤버 직렬화 round-trip | 직렬화 정본 | `test_every_dispatch_status_serializes_round_trip` (×4), `test_every_held_disposition_serializes_round_trip` (×2) |
| 전체 수명주기 E2E 워크플로 | AC1~3 통합 | `test_full_lifecycle_suspend_hold_recover_dispose_workflow` |

> 설계 원칙: `(str, Enum)` 의 `str()`/f-string 출력은 Python 버전에 민감한 함정(Dev Notes 142)이라
> **단언하지 않았다** — 정본 직렬화 경로(`.value` / `==` / `json.dumps`)만 잠갔다.
> 모든 fixture는 가짜 ID(`sub-*`/`tnt-*`)·고정 `datetime` 만 쓰고 실제 secret/식별자는 0건.

## Coverage

- **AC1 (예약/전송 게이트):** 4개 상태 행위 + 매핑 전수 + delegation + fail-closed(차단·경고) — 완전
- **AC2 (미전송→HELD, 성공 재발송 금지):** PENDING/HELD/SUCCEEDED/DISCARDED + 합성 멱등 + 통합 불변식 — 완전
- **AC3 (운영자 처리·복구·기록):** dispose 두 경로 + non-HELD 거부 + suspend/resume 전이·멱등·`to_status`·None 보존·frozen — 완전
- **게이트 함수 커버리지:** `evaluate`/`evaluate_status`/`suspend`/`resume`/`hold_undelivered`/`dispose_held` 6/6
- **fail-closed 불변식 3종:** ① 성공분 재발송 0, ② 복구≠자동발송, ③ 미지 상태 차단 — 모두 명시 잠금

## Results

- 신규 게이트 테스트: **51 passed** (기존 24 + 신규 27)
- 전체 스위트: **749 passed** (기준선 722 + 27, **회귀 0**)
- 범위: `tests/server/test_subscription_gate.py` 만 확장 — `src/rider_server/domain/`·`src/rider_crawl/`·`pyproject.toml`·게이트 구현 **무변경**(`git diff -w --stat` 확인)
- 누출 검사: 봇 토큰(`\d{6,}:[\w-]{30,}`)/`chat_id` digits/한국 휴대폰 평문 **0건**

## Next Steps

- CI에서 동일 스위트 실행 (`bmad-testarch-ci` 미설치 — 수동 게이트 유지)
- scheduler wiring(Story 5.4)·Admin 전이 UI(Story 5.7)·DB 영속(Epic 5) 합류 시 통합/계약 테스트 추가
