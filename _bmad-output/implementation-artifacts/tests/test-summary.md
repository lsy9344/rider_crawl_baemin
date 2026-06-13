# Test Automation Summary — Story 5.4 (Scheduler · interval·jitter·circuit breaker·구독 게이트)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`) · 모드: 발견 갭 자동 적용

> 직전 5.3 QA 요약은 본 파일을 덮어쓰며 갱신됐다(QA 런마다 `test-summary.md` 1개를 정본으로 둠). 5.3 내용은 story 5.3 Completion Notes 에 보존.

## 컨텍스트

대상: `_bmad-output/implementation-artifacts/5-4-scheduler-interval-jitter-circuit-breaker와-구독-게이트.md`

스토리 5.4는 HTTP 라우트/UI가 없는 **별도 process scheduler**다(`create_app` 노출 금지). 따라서 이
워크플로의 "API/E2E"는 **순수 정책 함수 + async tick 오케스트레이션 + PG-gated 동시성** 계층을
대상으로 한다. dev가 만든 4개 테스트 파일(`policy`/`tick`/`boundary`/PG-gated `negative`)을 AC1~AC4
대비 정독해 **미커버 분기·경계·AC 추적 공백 16건**을 발견하고 **모두 자동 적용**했다. 신규
third-party dep 0, enum count-lock(11/4/7)·9-dep·단방향 import·async 경계·테이블 14 가드 무회귀.

## 발견·적용한 갭 (AC 추적)

| # | 갭 (이전엔 미커버) | AC/근거 | 위치 |
| --- | --- | --- | --- |
| 1 | `decide_schedule` **warn_admin 보존**(SUSPENDED 차단 / NO_SUBSCRIPTION / GRACE+lifecycle 비활성) | AC2 "경고를 결정 결과에 남긴다" | policy ×3 |
| 2 | 비활성 lifecycle **전수 차단**(이전엔 9개 중 3개만 샘플) + 활성 집합 정확성 잠금 | AC2 | policy ×12 |
| 3 | breaker **`total==min_samples` 경계 · custom threshold · 100% 실패** | AC3 | policy ×3 |
| 4 | `retry_run_after` **`TARGET_VALIDATION_FAILURE`(HELD) · `RENDER_FAILURE`(결정적 FAILED)** | AC3 사람-개입/결정적 경로 | policy ×2 |
| 5 | `retry_run_after` **custom base/factor/cap passthrough · cap 상한 · error_code/attempt 전달**(미테스트 kwargs 빌드 로직) | AC3 | policy ×3 |
| 6 | `can_admit` **zero capacity · 마지막 슬롯 경계** | AC1 | policy ×2 |
| 7 | tick **`REASON_UNKNOWN_PLATFORM` 분기**(미지 플랫폼 fail-closed — 이전엔 한 번도 실행 안 됨) | AC1 | tick ×1 |
| 8 | tick **`REASON_RACE_LOST` 분기**(conditional advance 패배 → enqueue 0 — 이전엔 미실행) | AC4 | tick ×1 |
| 9 | tick **`warn_admin` → `ScheduleOutcome` 전파**(허용/차단 모두) | AC2 | tick ×2 |
| 10 | tick **기존 in-flight가 가용 슬롯 축소**(capacity 오프셋 로직) | AC1 | tick ×1 |
| 11 | tick **breaker 윈도 집계 tick당 1회/플랫폼**(대상별 중복 집계 회피) | AC3 | tick ×1 |
| 12 | tick **`run_after==now`(즉시 claim) · 빈 due no-op** | AC1 | tick ×2 |
| 13 | tick **precedence**(게이트 차단 > breaker, 활성-job 차단 > capacity) | AC2/AC4 | tick ×2 |
| 14 | tick **`SchedulerService(breaker_threshold=...)` 생성자 와이어링** | AC3 | tick ×1 |
| 15 | repo **DB 문자열→enum 미매핑 fail-closed**(`_to_subscription_status`/`_to_lifecycle_status`) + 게이트 합성 end-to-end | AC2 | repo ×13 |
| 16 | repo **스코프 상수 고정**(CrawlJob 2종 · 활성 status PENDING/CLAIMED/RUNNING) | AC4 | repo ×2 |

> **#15·#16 가 중요한 이유:** 이 순수 헬퍼/상수는 지금까지 오직 PG-gated 경로에서만 간접 실행됐고,
> Postgres 부재 CI에서 **한 번도 실행되지 않았다**. always-run 으로 끌어내 fail-closed 의미를 잠갔다.

## 생성/보강된 테스트

### 보강(기존 always-run 파일에 append)
- [x] `tests/server/test_scheduler_policy.py` — 순수 정책 경계/분기 **+25**
- [x] `tests/server/test_scheduler_tick.py` — tick 분기·전파·precedence **+11** (import 에 `REASON_RACE_LOST`/`REASON_UNKNOWN_PLATFORM` 추가)

### 신규(always-run, DB-less)
- [x] `tests/server/test_scheduler_repository.py` — `PostgresSchedulerRepository` 순수 헬퍼·스코프 상수 **+15**

## 커버리지 (AC 매핑)

- **AC1** jitter·due·job type 매핑·capacity throttle: 미지 플랫폼 fail-closed·in-flight 오프셋·run_after·zero/마지막 슬롯 경계까지 ✅
- **AC2** 구독 게이트 + lifecycle 합성: 비활성 lifecycle 전수·warn_admin 전파(허용/차단)·DB 문자열 미매핑 fail-closed end-to-end ✅
- **AC3** circuit breaker + error_code backoff: min_samples/threshold/100% 경계·사람-개입(HELD)·결정적(FAILED)·custom backoff·tick당 1회 집계·생성자 threshold ✅
- **AC4** 멱등 tick: race lost 분기·precedence·활성 status 스코프(PENDING/CLAIMED/RUNNING)·CrawlJob 2종 스코프 ✅
- **PG-gated**(`tests/negative/`): due 질의·동시 tick 멱등성·활성-job 재-enqueue 0 — Postgres 부재로 **3건 skip 유지**(`TEST_DATABASE_URL` 환경에서 확정. SQLite 흉내 금지 정책 준수).

## 테스트 수치 (재측정 — `stale-test-count-a2` 메모리 준수)

| 구분 | 전체 스위트 | scheduler 테스트 |
| --- | --- | --- |
| Dev 종료(baseline, story Completion Notes) | 1525 passed, 21 skipped | 56 passed, 3 skipped |
| **QA 갭 적용 후(현재 — 정본)** | **1576 passed, 21 skipped** | **107 passed, 3 skipped** |
| 증분 | **+51 passed, +0 skipped** | +51 passed, +0 skipped |

- 회귀 **0**. 명령: `.venv/Scripts/python.exe -m pytest -q` (1576 passed, 21 skipped in ~8.6s).
- skipped 변화 0 — 신규 51건은 전부 always-run(DB-less). PG-gated 3건 skip 은 5.4 dev 종료와 동일.
- 가드 green: 9-dep lock(`len==9`) · 단방향 import(scheduler→rider_agent 0) · async 경계(rglob) ·
  enum count-lock(11/4/7) · 테이블 14 · migration 단일 head(0003). 새 third-party dep 0.
- ⚠️ **stale count 주의**: 본 표의 "QA 갭 적용 후" 수치가 정본이다. story Dev Agent Record 의 "1525/21"은
  dev 종료 시점값이며 본 QA 단계에서 +51 갱신됐다.

## checklist.md 검증

- [x] API/오케스트레이션 테스트 생성(tick 6개 사유 코드 전수 실행) — UI/HTTP 없음(별도 process)이라 E2E 등가는 tick + PG-gated 동시성
- [x] 표준 프레임워크 API(pytest / `asyncio.run` — `pytest-asyncio` 미도입)
- [x] happy path(due→enqueue·허용 결정) + critical error(미지 플랫폼/race lost/breaker open/throttle/fail-closed) 커버
- [x] 전부 통과(1576 passed) · 명확한 설명(docstring + AC 매핑 주석)
- [x] sleep/하드코딩 대기 0 — 주입 `now`로 완전 결정적
- [x] 테스트 독립(테스트마다 fresh repo/backend — 순서 의존 0)
- [x] 요약 생성 + 적절한 디렉터리 저장(`tests/server/`)

## Next Steps

- CI에 `TEST_DATABASE_URL`(실 PostgreSQL 13)을 붙이면 `tests/negative/test_scheduler_idempotency.py` 3건이
  실행돼 동시 tick conditional-UPDATE 멱등성의 literal fidelity가 확정된다.
- 부하/타이밍 차원(100 fake target 부하 smoke)은 Story 5.10 소유(5.4는 storm 미발생 결정적 1차 잠금까지).
- 리뷰 시 story Dev Agent Record 테스트 수치를 본 요약(1576/21) 기준으로 reconcile.
