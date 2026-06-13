# 테스트 자동화 요약 — Story 4.5 (BrowserProfileManager)

**워크플로:** bmad-qa-generate-e2e-tests
**대상:** Story 4.5 — `BrowserProfileManager`(per-target 프로필/CDP 격리 + 대상 검증 + heartbeat `browser_profiles` 소스 배선)
**테스트 프레임워크:** pytest (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`)
**실행:** `.venv/Scripts/python.exe -m pytest -q`
**날짜:** 2026-06-13

> QA 자동화 역할 — 기존 구현(상태 `review`)에 대해 **AC 대비 커버리지 갭만 발굴·보강**했다. 코드 리뷰/스토리 검증은 본 워크플로 범위 아님. **제품 코드(`src/`)는 0줄 변경**, 테스트만 추가.

## 생성/보강 테스트

이 프로젝트의 Agent 측 도메인은 순수 동기(브라우저/UI/네트워크 부작용은 주입 fake)라 UI E2E 표면이 없다 → 단위/통합 레벨 pytest로 검증.

신규 9건 — `tests/agent/test_browser_profile.py` (기존 23건 → 32건):

- [x] `test_duplicate_profile_key_to_other_target_is_rejected` — **프로필-키 중복 거부 fail-closed 분기**(포트는 다르나 프로필이 같으면 둘째 대상 미시작). 기존엔 포트-중복만 검증됨. (AC1.2/1.3)
- [x] `test_recover_unregistered_target_raises` — 등록부에 없는 대상 복구 요청 → `BrowserLaunchError`. (AC3)
- [x] `test_release_unknown_target_is_noop` — 미등록 대상 `release`는 예외 없이 무시(idempotent). (AC1)
- [x] `test_check_health_without_probe_returns_current_state` — `cdp_probe` 미주입 시 현재 상태 반환 경로. (AC3)
- [x] `test_map_empty_exception_uses_default_headline` — 빈 예외 본문 → 기본 헤드라인 surfacing(raw 본문 없음). (AC2)
- [x] `test_map_center_unconfirmed_runtimeerror_is_mapped` — **"센터 미확인"** RuntimeError도 `CENTER_MISMATCH`로 매핑·설정 센터명 비노출. (AC2.6 세 번째 케이스)
- [x] `test_recover_exhaustion_sets_state_unknown_in_projection` — 재시작 한도 소진 후 상태 `UNKNOWN`이 heartbeat 투영에 반영. (AC3/AC4)
- [x] `test_recover_auth_required_is_reflected_in_browser_profiles` — `AUTH_REQUIRED` 전이가 heartbeat 투영에 반영(운영이 조치 필요를 봄). (AC3/AC4)
- [x] `test_allocate_local_port_returns_valid_local_port` — 기본 포트 할당기(stdlib `socket`)가 유효 로컬 포트 반환. (AC1)

신규 1건(보강) — `tests/agent/test_agent_package.py`:

- [x] `test_reuse_seam_reexports_same_objects` 확장 — **4.5 재사용 심볼의 `is` identity 잠금**: `prepare_chrome`·`ensure_local_cdp_address`·`BrowserLaunchError`·`CdpUnavailableError`·`BrowserActionRequiredError`·`RunLock`·`coupang_center_name_risk`. 스토리의 "재구현 금지" 가드 #1을 테스트로 고정(Task 5.2의 "(선택) identity 단언" 갭). (AC1/AC2)

## 발굴한 갭 → 보강 매핑

| 갭 | 미검증 경로 | AC | 보강 테스트 |
|---|---|---|---|
| 프로필-키 중복 거부 | `browser_profile.py:262-266` | AC1.2/1.3 | `test_duplicate_profile_key_to_other_target_is_rejected` |
| 복구 대상 미등록 | `:355-356` | AC3 | `test_recover_unregistered_target_raises` |
| release 미등록 no-op | `:310-311` | AC1 | `test_release_unknown_target_is_noop` |
| probe 미주입 health | `:329-330` | AC3 | `test_check_health_without_probe_returns_current_state` |
| 빈 예외 기본 헤드라인 | `:167` | AC2 | `test_map_empty_exception_uses_default_headline` |
| 센터 미확인 매핑 | `:152-173` | AC2.6 | `test_map_center_unconfirmed_runtimeerror_is_mapped` |
| 소진 후 UNKNOWN 투영 | `:389` | AC3/AC4 | `test_recover_exhaustion_sets_state_unknown_in_projection` |
| AUTH_REQUIRED 투영 | `:373-377` | AC3/AC4 | `test_recover_auth_required_is_reflected_in_browser_profiles` |
| 기본 포트 할당기 | `:129-138` | AC1 | `test_allocate_local_port_returns_valid_local_port` |
| 재사용 identity 미잠금 | `reuse.py` re-export | AC1/AC2 | `test_reuse_seam_reexports_same_objects`(확장) |

## 커버리지

- **AC1**(per-target 격리 + 중복 미시작): 포트·**프로필-키** 양쪽 중복 거부 + 원격 CDP + 기본 포트 할당기 모두 검증.
- **AC2**(센터/상점 검증 + CENTER_MISMATCH 미발송 + `target_validation_failure`): 위험 분류 재사용 + 불일치/**센터 미확인**/빈 본문 매핑 + redaction 모두 검증.
- **AC3**(약화 금지 + 건강/복구): N개 대상 약화 금지 + 재시작 bounded + **소진→UNKNOWN** + **AUTH_REQUIRED 무한재시도 금지** + probe 무주입 경로 검증.
- **AC4**(heartbeat 배선 + raw 경로 비노출): provider 투영(id/ref only) + `build_agent_components`/`run_agent` 배선 + **복구 상태(UNKNOWN/AUTH_REQUIRED) 투영 반영** 검증.
- **재사용 가드**: 7개 4.5 심볼 `is` identity로 "재구현 금지" 고정.

## 검증 결과

- `pytest tests/agent/test_browser_profile.py tests/agent/test_agent_package.py -q` → **46 passed** (32 + 14).
- 전체 스위트 `pytest -q` → **1174 passed** (보강 전 1165 → 신규 9건 반영, 0 회귀).
- 누출 가드: 신규 테스트에 실 토큰/경로/PII 0건(가짜값만; 유일한 PII-형식 문자열은 *기존* redaction 단언 테스트의 의도된 가짜 번호).
- 스코프: 제품 코드(`src/`) 0줄 변경 — 테스트 파일만 추가(`test_browser_profile.py`는 untracked 신규라 `git diff`에 안 보임).

## 다음 단계

- CI에서 신규 케이스 포함 실행.
- Epic 5(서버 heartbeat 수신·`browser_profiles` 저장·Admin runbook) 구현 시, 서버 측 수신/저장 round-trip E2E 추가.
