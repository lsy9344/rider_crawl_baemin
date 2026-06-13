# Test Automation Summary — Story 5.3 (QueueBackend 추상화와 PostgreSQL job queue)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`) · 모드: 발견 갭 자동 적용

## 컨텍스트

대상: `_bmad-output/implementation-artifacts/5-3-queuebackend-추상화와-postgresql-job-queue.md`
구현 코드(`src/rider_server/queue/**`, `src/rider_server/api/jobs.py`, `src/rider_server/main.py` 배선)와
dev 단계 기존 테스트(계약 suite·HTTP 라우트·실 Agent e2e·어휘 가드·PG-gated negative)를 AC1~AC4
대비 정독해 커버리지 갭 8건을 발견하고 **모두 자동 적용**했다. append-only(기존 테스트/소스 무수정),
신규 third-party dep 0, enum count-lock·9-dep·단방향 import·async 경계 가드 무회귀.

## 발견·적용한 갭

| # | 갭 | AC/근거 | 위치 |
| --- | --- | --- | --- |
| A | events 라우트 redaction 미검증(`redact()` 호출은 있으나 마스킹 잠금 테스트 없음) | guardrail #8 secret 평문 0 | test_jobs_api.py |
| B | `extend_lease` 계약 미흡(in-memory 해피 1건만, PG/negative 없음) | AC2 heartbeat lease 연장 | test_queue_backend.py |
| C | claim `max_jobs` 한도 미검증 | AC1/AC2 claim 의미 | test_queue_backend.py |
| D | claim 엣지(`max_jobs<=0`/빈 capabilities → `[]`) | AC1 | test_queue_backend.py |
| E | `map_agent_status` 직접 단위 테스트 부재 | AC3 소문자→상태머신 | test_job_vocab.py |
| F | 전이표 직접 검증(`is_allowed_transition`·SUCCEEDED 터미널) 부재 | AC3 | test_job_vocab.py |
| G | complete 해피패스 HTTP 200 매핑(success→SUCCEEDED/failed→FAILED) 부재 | AC4 HTTP 계약 | test_jobs_api.py |
| H | claim 시 owner+claimed_at+lease 한 번에 부여 검증 부재 | AC2 "한 트랜잭션에서 부여" | test_queue_backend.py |

## 생성/보강된 테스트

### 계약 suite (in-memory 항상 실행 + PostgreSQL `TEST_DATABASE_URL` parametrize) — `tests/server/test_queue_backend.py`
- [x] `test_claim_respects_max_jobs_limit` — (C) 한도 준수·나머지 잔류·재claim(누락/중복 0)
- [x] `test_claim_zero_max_jobs_returns_empty` — (D) `max_jobs<=0` → `[]`
- [x] `test_claim_empty_capabilities_returns_empty` — (D) 빈 capabilities → `[]`
- [x] `test_extend_lease_prevents_stale_recovery` — (B) 연장 후 원래 만료시점 미회수
- [x] `test_extend_lease_rejects_non_owner` — (B) 다른 Agent 연장 거부
- [x] `test_extend_lease_rejects_expired` — (B) 만료 lease 연장 불가
- [x] `test_extend_lease_unknown_job_returns_false` — (B) 미존재 job(유효 UUID) → False
- [x] `test_claim_assigns_owner_lease_and_claimed_at_in_memory` — (H) AC2 스냅샷(agent_id+claimed_at+lease)

### HTTP 라우트 계약 — `tests/server/test_jobs_api.py`
- [x] `test_events_redacts_secret_in_message` — (A) token/phone 평문이 서버 redact 통과로 기록에 미잔류
- [x] `test_complete_success_returns_200_with_succeeded` — (G) 200 + success→SUCCEEDED + Agent full 본문 수용
- [x] `test_complete_failed_returns_200_with_failed` — (G) failed→FAILED 매핑·200

### 상태머신/어휘 단위 — `tests/server/test_job_vocab.py`
- [x] `test_map_agent_status_maps_lowercase_to_state_machine` — (E) success/failed 매핑
- [x] `test_map_agent_status_rejects_unknown` — (E) lease_lost/임의값/대문자값 거부
- [x] `test_allowed_transitions_cover_claim_run_complete_and_recovery` — (F) 정의 전이 + 회수 전이
- [x] `test_undefined_transitions_rejected` — (F) 미정의 전이 거부
- [x] `test_succeeded_is_terminal` — (F) SUCCEEDED 종단

## 커버리지 (AC 매핑)

- **AC1** QueueBackend 계약(in-memory+PG parametrize): max_jobs 한도·엣지·extend_lease 보강 ✅
- **AC2** lease/owner/claimed_at 한-트랜잭션 부여·연장·만료 회수·exactly-one·이중 success 차단 ✅
- **AC3** job type 6종·전이표·소문자 status 매핑 ✅
- **AC4** 실 Agent↔실 서버 e2e(기존)·complete HTTP 200 매핑·events redaction·PG-gated 동시성(기존) ✅

## 테스트 수치 (재측정)

| 구분 | 전체 스위트 | 5.3 4개 파일 |
| --- | --- | --- |
| Dev 종료(baseline) | 1453 passed, 11 skipped | 25 passed, 10 skipped |
| **QA 갭 적용 후** | **1469 passed, 18 skipped** | **41 passed, 17 skipped** |
| 증분 | +16 passed, +7 skipped | +16 passed, +7 skipped |

- 회귀 **0**. 명령: `.venv/Scripts/python.exe -m pytest -q`.
- +7 skipped = 신규 계약 테스트(C/D/B)의 PostgreSQL parametrize 파라미터(`TEST_DATABASE_URL` 미설정 → skip).
  현 WSL/venv 에 Postgres 부재 → in-memory 가 단일-claim·lease·상태머신 의미를 항상 잠그고, PG-gated 는
  `TEST_DATABASE_URL` 환경에서 실 `FOR UPDATE SKIP LOCKED`/lease/409 동작을 확정한다(SQLite 흉내 금지).
- ⚠️ **stale count 주의**(memory: stale-test-count-a2): 본 표의 "QA 갭 적용 후" 수치가 정본이다. story
  Dev Agent Record 의 "1453/11"·"25/10" 은 dev 종료 시점값이며 본 QA 단계에서 +16/+7 갱신됐다.

## 품질 원칙 준수

- DB-less always-run + PG-gated 분리 유지(SQLite 로 SKIP LOCKED 흉내 금지 — 오탐 회피).
- 주입 `now` 로 lease 결정적 검증, `sleep`/하드코딩 대기 0.
- 테스트 독립성: 테스트마다 fresh backend(memory 신규 / PG 스키마 재적용).
- complete 해피패스 HTTP 테스트는 라우트의 실 wall-clock `now` 특성에 맞춰 실 현재시각 기준 claim(e2e 동형) — lease 만료 오탐 회피.
- append-only: 기존 테스트/소스 무수정, 신규 dep 0.

## Next Steps

- `TEST_DATABASE_URL` 지정 환경에서 전체 PG-gated(계약 parametrize + `tests/negative/`) 실행 → 실DB literal fidelity 확정.
- 리뷰 시 story Dev Agent Record 테스트 수치를 본 요약 기준으로 reconcile(stale count 보정).
