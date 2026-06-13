# Test Automation Summary — Story 4.4 (outbound HTTPS job 폴링/claim/complete + lease)

작성: 2026-06-13 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest

## 컨텍스트

스토리 4.4는 UI 없는 **Agent job 루프 client**(`claim`/`complete`/`events` HTTP client + `JobRunner` claim→execute→complete 루프 primitive + lease 인지(client) + `run_agent`/`start_heartbeat_thread` startup 배선)와 `__main__.py` 의 thin `run` 서브커맨드다. 서버 측 queue·단일-claim 강제·lease 부여/연장/stale 회수/재할당·Admin 은 Epic 5 소유라, 테스트는 **주입 fake transport + 주입 sleep/now/stop/executor** 에 대한 client-side 동작 검증 형태다(4.x 표준 — epic-3-retro 108). 외부(브라우저/네트워크/Telegram/Kakao/Gmail) 미호출, 가짜 값만(`agtok-fake-…`/`agent-fake-…`/`job-fake-…`).

dev-story가 `tests/agent/test_job_loop.py`에 35건을 만든 상태에서, 구현 분기 중 **테스트가 비어 있던 격차**를 찾아 자동 보강(auto-apply)했다.

## Generated / 보강 테스트

`tests/agent/test_job_loop.py` (+16건, 기존 35 → **51건**). 기존 헬퍼(`FakeTransport`·`StoppingSleep`·`SequenceClock`·`FakeStore`·`_runner`) 재사용, 신규 추상화 0.

| # | 테스트 | 커버한 격차 | AC |
|---|--------|-------------|----|
| Gap1 | `test_claim_jobs_uses_injected_capabilities` | capabilities 가 **주입 가능**하고 claim 본문에 그대로 실림. 기존 본문 단언은 `capabilities == list(capabilities)` 의 동어반복이라 주입 전파 미검증 | AC1.2 |
| Gap2 | `test_runner_passes_injected_capabilities_through_to_claim` | 루프(`JobRunner`)에 주입한 capabilities 가 claim 호출로 전파 | AC1.2 |
| Gap3 | `test_runner_surfaces_revoked_on_complete_401` | **complete 401** → `needs_registration`/`REVOKED` surfacing. 기존엔 complete 409/410(lease-lost)만 검증 — `_complete` 의 `elif 401` 분기 미검증 | AC1.3·AC2.5 |
| Gap4 | `test_runner_records_error_on_generic_complete_failure_and_survives` | **complete 일반 5xx** → crash 없이 기록·in-flight 제거·루프 생존(상태 VALID 유지). `_complete` 의 `else` 분기 미검증 | AC1.3 |
| Gap5 | `test_runner_survives_started_event_failure_and_still_completes` | **`/events`(started) 실패**해도 job 실행·complete 정상(best-effort). `_emit_started` 의 `except` 분기 + `events_error` 경로 미검증 | AC1.3·AC4 |
| Gap6 | `test_runner_reports_failure_result_even_when_lease_expired` | lease self-check 는 **성공만** 막고, 실패 결과는 lease 만료여도 그대로 보고(과잉 abandon 방지). `status == SUCCESS and expired` 의 음성 분기 미검증 | AC2.5 |
| Gap7 | `test_runner_processes_multiple_claimed_jobs` | claim 한 job 이 **복수**면 모두 실행·complete 후 in-flight 정리. 기존엔 단일 job 만 | AC1 |
| Gap8 | `test_run_agent_does_not_start_loop_when_token_revoked` | `run_agent` startup 게이트 — **token revoke**(server_check=False) 면 루프 미진입(claim/heartbeat 미전송). 기존엔 identity 없음만 검증 | AC1.2 |
| Gap9 | `test_coerce_lease_epoch_parses_or_fails_closed` (7 params) | lease 시각 파싱 입력(epoch float/int·숫자 문자열) + 누락/빈/비-숫자/bool → **fail-closed(`None`)**. self-check 입력 정규화 미검증 | AC2.5 |
| Gap10 | `test_coerce_lease_epoch_parses_iso8601_forms` | ISO 8601(`+00:00`/`Z` 접미사) → 동일 양수 epoch | AC2.5 |

기존 35건(유지): claim/complete client URL·본문·파싱·env 폴백, events redact·enum-lock 금지, 기본 executor `UNSUPPORTED_JOB_TYPE`, token 게이트(revoked) claim 차단, best-effort(claim 503/401·executor 예외), lease 기록·active_jobs·self-check(만료/누락)·complete 409/410 흡수, AC3 결과 필드, heartbeat active_jobs 배선·`start_heartbeat_thread`, `run_agent`(identity 없음/정상 기동·정지), Bearer 헤더+평문 비노출, `__main__ run` 무회귀.

## Coverage

| Acceptance Criterion | 커버 |
|---|---|
| AC1 — outbound claim/complete·claim 한 job 만(단·복수)·token 게이트·capabilities 주입 | ✅ 기존 + Gap1/Gap2/Gap7 |
| AC1.3 — best-effort(claim 5xx/401·executor 예외·complete 401/5xx·event 실패) | ✅ 기존 + Gap3/Gap4/Gap5 |
| AC2 — lease 기록·active_jobs 노출·heartbeat 연장 배선 | ✅ 기존 다수 |
| AC2.5 — self-check(만료/누락/파싱)·성공만 abandon·complete 409/410 흡수 | ✅ 기존 + Gap6/Gap9/Gap10 |
| AC3 — 결과 필드(agent_id·started/finished_at·status·error_code·error_message_redacted·metrics) | ✅ 기존 2건 |
| AC4 — events redact(message_redacted·artifact ref)·event_type/severity 비-lock | ✅ 기존 + Gap5 |
| 보안 — Bearer 헤더 전용·로그/payload/예외 평문 token 0 | ✅ 기존 2건 + Gap3/4/5 누출 단언 |
| startup — `run_agent` 게이트(missing/revoked)·heartbeat thread 기동·정지 | ✅ 기존 + Gap8 |

`job_loop.py` 공개 표면 전부 커버. 보강 후 complete 401/5xx 분기·started-event best-effort·lease self-check 음성 분기·복수 job·startup revoked 게이트·lease 파싱 정규화/ISO/fail-closed 까지 커버.

## Validation Results (단일 정본)

운영 venv `.venv/Scripts/python.exe -m pytest`:

- `tests/agent/test_job_loop.py -q` → **51 passed** (dev 35 + QA 16)
- 전체 스위트 `-q` → **1142 passed, 0 failed** (기존 1126 + 신규 16, 순수 additive·회귀 0)
- 4.1 가드 + 4.2/4.3 무회귀(`test_agent_package`·`test_registration`·`test_secure_store`·`test_heartbeat`) → **97 passed**
- 범위: `git diff -w` 상 QA 변경은 `tests/agent/test_job_loop.py` 1개뿐(`src/` 코드·`pyproject.toml`·reuse 모듈 0줄). `src/` 내 평문 fake token 0건.

## Next Steps

- CI 에서 전체 스위트 실행(운영 venv pytest).
- Epic 5(서버 측 queue/단일-claim/lease 강제·연장·stale sweep/재할당) 구현 후 client↔server 왕복 통합 계약 테스트 추가.
- 실제 워커(4.5/4.6/4.8/4.9) `execute_job` 주입 시 type 별 결과/이벤트 테스트 보강.
