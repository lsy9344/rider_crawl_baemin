# Test Automation Summary — Story 4.8 (배민 인증 필요 감지와 사람 개입형 재인증)

- 워크플로: `bmad-qa-generate-e2e-tests`
- 대상 피처: `src/rider_agent/auth/baemin_auth.py` (배민 auth 상태 분류기 + `AUTH_CHECK`/`OPEN_AUTH_BROWSER` 실행자 + bounded 재인증 대기 + `build_auth_execute_job` 라우터)
- 프레임워크: pytest (프로젝트 기존 설정 — `pyproject.toml` `pythonpath=["src"]`, `testpaths=["tests"]`)
- 실행 커맨드: `.venv/Scripts/python.exe -m pytest`
- 날짜: 2026-06-14

> 본 피처는 순수 동기 primitive(UI 없음)다. 따라서 "E2E"는 실 `claim→execute→complete` 루프(`run_agent`)를 통한 라우팅/보고 검증으로, "API/단위"는 분류기·실행자·라우터 직접 검증으로 매핑한다. 실 Chrome/실 배민 로그인/실 휴대폰 인증/실 시계/실 네트워크는 **한 줄도 호출하지 않고** 전부 주입 fake(`login_probe`/`open_auth_browser`/`detect_completion`/`now`/`sleep`/transport)로 결정적 검증한다.

## 발견·자동 적용한 커버리지 갭 (신규 6건)

기존 23건 위에 다음 경계/우선순위/실-루프 성공 경로 갭을 `tests/agent/test_baemin_auth.py` 에 추가했다:

| # | 테스트 | 갭 / 검증 내용 | AC |
|---|---|---|---|
| 1 | `test_classify_auth_required_error_wins_over_ok_snapshot` | fail-closed **우선순위** — `BrowserActionRequiredError` + `snapshot_ok=True` 동시 입력 시 `ACTIVE` 가 아니라 `AUTH_REQUIRED`(인증 신호가 정상-스냅샷보다 우선). 분류기 error-우선 분기 미검증분 | AC1 |
| 2 | `test_auth_check_blocked_or_captcha_is_fail_closed_to_auth_required` | `ACTIVE` 외 **정의된 비-active 상태**(`BLOCKED_OR_CAPTCHA`)도 fail-closed 로 `AUTH_REQUIRED` surfacing(기존엔 `UNKNOWN` 만 검증) | AC1·3 |
| 3 | `test_auth_check_logs_redacted_message_on_both_branches` | `ACTIVE`/`AUTH_REQUIRED` **두 분기 모두** log 콜백 호출 + 로그 누출 0(`ACTIVE` 분기 로그 라인은 기존 누출 테스트 미검증분) | AC1·3, NFR-5/8 |
| 4 | `test_open_auth_browser_immediate_completion_no_sleep` | 경계 — 1번째 polling 에서 사람-완료 감지 시 **sleep 0** 으로 즉시 `AUTH_VERIFIED`(불필요한 대기 0) | AC2 |
| 5 | `test_open_auth_browser_single_attempt_times_out_without_sleep` | 최소 상한 경계 — `max_attempts=1` 시 detect 1회·sleep 0 후 즉시 `AUTH_REQUIRED`/`auth_timeout`(off-by-one/무한 재시도 방지의 최소 경계) | AC3, NFR-4 |
| 6 | `test_run_agent_open_auth_browser_success_completes_with_verified` | 실 `run_agent` 루프로 `OPEN_AUTH_BROWSER` **성공(AUTH_VERIFIED)** 경로 `/complete` 보고(기존엔 timeout 실패 경로만 루프 검증). hang 0 | AC4 |

## 생성된 테스트

### 단위/계약 테스트 (분류기·실행자·라우터)
- [x] `tests/agent/test_baemin_auth.py` — 분류기(`classify_baemin_auth_state`), `AUTH_CHECK` 실행자, `OPEN_AUTH_BROWSER` 실행자, bounded timeout, `build_auth_execute_job` 라우터, AST 부정 가드(OTP/GUI 자동화 import 0), Windows-gated 기본 probe, 누출/값 정합

### E2E (실 claim→execute→complete 루프, `run_agent` 합성)
- [x] `test_run_agent_routes_auth_job_through_real_loop` — `AUTH_CHECK` 라우팅·`/complete` 성공 보고 (기존)
- [x] `test_run_agent_open_auth_browser_timeout_completes_without_hang` — `OPEN_AUTH_BROWSER` timeout `/complete` 실패 보고 (기존)
- [x] `test_run_agent_open_auth_browser_success_completes_with_verified` — `OPEN_AUTH_BROWSER` 성공(AUTH_VERIFIED) `/complete` 보고 **(신규)**

## 커버리지 (AC 매핑)

- **AC1** (인증 필요 감지 → `AUTH_REQUIRED`·메시지 미생성·보고): 분류기 4 케이스 + 우선순위 1 + `AUTH_CHECK` 5 케이스(트립와이어 포함) → ✅
- **AC2** (사람 개입형 재인증·OTP 취득/우회 0): 사람-완료 감지 2 + OTP-부재 시그니처 1 + AST 부정 가드 2 → ✅
- **AC3** (bounded 유지·무한 재시도 금지·timeout 운영 표면): attempts/wall-clock/최소-상한 timeout 3 + 유한 상한 1 → ✅
- **AC4** (라우터 배선·무회귀): 라우터 분기 3 + 실-루프 라우팅 3 → ✅
- **누출/단방향/값 정합**: 누출 가드 2 + Windows-gated 1 + `rider_server` 값 정합 1 → ✅

## 결과

- `tests/agent/test_baemin_auth.py`: **29 passed** (기존 23 + 신규 6)
- 전체 스위트(회귀 게이트): **1278 passed** (이전 1272 + 신규 6), 실패 0
- 4.1 패키지 가드(`tests/agent/test_agent_package.py`) green — third-party root == `rider_crawl`·sync·단방향(`rider_server` 0)·deps 정확히 9개·`rglob` 자동 검사가 `auth/` 서브패키지에도 적용됨
- 범위: **`src/` · `pyproject.toml` 0줄 변경** — 본 단계는 `tests/agent/test_baemin_auth.py` 만 수정

## 다음 단계

- 리뷰 단계에서 pass 수치 재측정(단일 정본) — dev 노트 잠정 수치는 qa-e2e append 후 stale 이므로 리뷰 시점 값 1개로 정정 (memory: stale-test-count-a2)
- 기본 real probe(`default_login_probe`/`open`/`detect_completion`) 정밀화 + `CRAWL_BAEMIN` 워커 연동은 후속 스토리/Epic 5 소유 (본 스토리 scope 밖)
