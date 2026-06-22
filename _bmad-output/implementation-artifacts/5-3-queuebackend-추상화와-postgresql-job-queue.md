---
baseline_commit: 97580a496e288e663017583dc6316e4216cc68b3
---

# Story 5.3: QueueBackend 추상화와 PostgreSQL job queue

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want job queue를 `QueueBackend` 인터페이스로 추상화하고 PostgreSQL `jobs` 테이블 구현(`FOR UPDATE SKIP LOCKED` claim + lease)을 제공하고, Agent API의 `claim`/`complete`/`events` 라우트를 이 backend에 배선하고 싶다,
So that Redis 미도입으로 idempotency·exactly-one-claim을 DB 트랜잭션 한 곳에 두면서도 추후 Redis/SQS로 교체할 길을 열고, 실제 `rider_agent`가 mock이 아닌 실 서버 큐에서 claim→complete 하는 Epic 4↔5 end-to-end 경로를 잠근다.

## Acceptance Criteria

**AC1 — QueueBackend 인터페이스 + PostgreSQL 구현 (P4-05, ADD-4)**
**Given** queue가 필요할 때
**When** `QueueBackend` 인터페이스(ABC)와 PostgreSQL 구현을 만들면
**Then** PostgreSQL `jobs` 테이블 + `SELECT … FOR UPDATE SKIP LOCKED` 로 job claim이 구현되고
**And** backend-중립 `QueueBackend` **계약 테스트 suite**가 in-memory 구현과 PostgreSQL 구현 **양쪽에 동일하게** 통과해, 구현을 Redis/SQS로 옮길 수 있음(인터페이스가 PG 세부에 새지 않음)이 보장된다.

**AC2 — at-least-once lease 의미론 + exactly-one-claim (FR-13 서버 측 보장, ADD-5)**
**Given** at-least-once 의미론을 가정할 때
**When** job을 claim/lease 하면
**Then** claim 시 `lease_expires_at`(timezone-aware) + `claimed_at` + `agent_id`가 한 트랜잭션에서 부여되고, lease 만료 시 stale job이 회수되어(`PENDING` 재진입) 다른 Agent에 재할당 가능하며
**And** 두 Agent(또는 두 claim 요청)가 같은 job을 동시에 claim해도 **정확히 하나만** 성공하고 나머지는 그 job을 받지 못한다(`FOR UPDATE SKIP LOCKED`). `complete` 시점에도 lease 소유 검증으로 재할당된 job의 이중 success 기록을 차단한다(409/410).

**AC3 — job type 어휘 + 상태 전이 (ADD, architecture.md:308-322)**
**Given** job type/status가 정의돼야 할 때
**When** job을 생성/전이 하면
**Then** job type은 UPPER_SNAKE **정본 6종**(`CRAWL_BAEMIN`, `CRAWL_COUPANG`, `AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `KAKAO_SEND`, `CAPTURE_DIAGNOSTIC`) — Agent `DEFAULT_CAPABILITIES`와 1:1 — 이고(epic 초안의 `CRAWL`/`RENDER`/`DISPATCH_TELEGRAM`/`BAEMIN_AUTH_OPEN`은 **구표기 — 사용 금지**)
**And** job status는 **정의된 전이 set만** 허용한다(미정의 전이는 거부). 상태 전이는 backend/service 경계에서만 일어나고 DB 컬럼을 임의로 직접 변경하지 않는다.

**AC4 — Epic 4↔5 통합: 실 Agent ↔ 실 서버 큐 end-to-end (Epic 4↔5 통합 검증, Story 5.10 연계, FR-13)**
**Given** Epic 4 Agent claim 루프가 Epic 3·4에서 서버 stub/mock에 대해서만 검증됐을 때
**When** 실제 `jobs` 테이블 backend + `api/jobs.py`(claim/complete/events) 라우트가 `create_app`에 배선되면
**Then** 실제 `rider_agent.job_loop`(JobRunner/`claim_jobs`/`complete_job`)이 mock이 아닌 **실 backend**에 대해 `POST /v1/jobs/claim` → 실행 → `POST /v1/jobs/{id}/complete` 하는 end-to-end 경로가 통합 테스트로 검증되고
**And** `double Agent claim` negative test에서 두 claim 요청이 같은 job을 동시에 가져갈 때 정확히 하나만 success를 기록하고 나머지는 빈 응답/충돌을 받으며
**And** lease 만료 후 stale 회수 → 재할당이 **실 PostgreSQL**에서 동작함을 확인한다(mock 경계가 아닌 실제 동작 검증; PG 미가용 환경에선 in-memory 계약 테스트로 단일-claim·lease 의미론을 잠그고 PG-gated는 skip + Completion Notes 명기).

## Tasks / Subtasks

- [x] **Task 1 — `QueueBackend` 인터페이스 정의 (`queue/backend.py`) (AC1·AC2·AC3)**
  - [x] `src/rider_server/queue/` 패키지 신설(`queue/` 디렉터리는 **현재 없음** — 이 스토리가 처음 만든다). `queue/backend.py`에 `abc.ABC` `QueueBackend`를 정의: `enqueue(...)`(job 생성 — 5.4 scheduler가 호출), `claim(agent_id, capabilities, max_jobs, lease_seconds, now) -> list[ClaimedJobRecord]`, `complete(job_id, agent_id, status, result_json, error_code, ...) -> CompleteOutcome`, `extend_lease(job_id, agent_id, new_expiry)`(heartbeat 연장 입력), `recover_stale(now) -> int`(만료 lease 회수), `emit_event(...)`(또는 events는 라우트에서 직접 — Task 4 참고). 메서드 시그니처는 **PG/Redis/SQS 어디서든 구현 가능**한 중립형(원시 타입/dataclass)만 노출 — `AsyncSession`·SQL·`FOR UPDATE` 같은 PG 세부를 인터페이스에 새지 않는다(AC1).
  - [x] **job type 상수**(UPPER_SNAKE, plain-string): 정본 6종을 `queue/`(또는 `domain/`)에 module 상수/`tuple`로 둔다. **count-lock enum으로 만들지 않는다**(`heartbeat.DEFAULT_CAPABILITIES`·`job_loop` plain-string 선례 — 후속이 type을 늘려도 "정확히 N개" 테스트가 깨지지 않게). 값은 `rider_agent.heartbeat.CAPABILITY_*`와 **문자열로 일치**시키되 **import 하지 않는다**(단방향 의존: `rider_server`는 `rider_agent`를 import 금지 — 값만 미러). [Source: agent-job-type-vocab memory, architecture.md:308-313]
  - [x] **job status 상수 + 전이표**(UPPER_SNAKE, plain-string): 권장 set `PENDING`→`CLAIMED`→`RUNNING`→(`SUCCEEDED`|`FAILED`), 재시도 시 `FAILED`/`RETRY`→`PENDING`(attempts++ + `run_after` backoff), lease 만료 시 `CLAIMED`/`RUNNING`→`PENDING`(stale 회수). 허용 전이를 명시적 set/dict로 정의하고 미정의 전이는 예외(AC3). `subscription_gate.DispatchJobStatus`(PENDING/HELD/SUCCEEDED — **게이트-facing 최소 부분집합**)와 **reconcile**: 같은 의미값은 같은 문자열, 다른 레이어는 분리(서로 import 강결합 금지). [Source: src/rider_server/services/subscription_gate.py:46-56]
  - [x] **검증**: 새 enum을 추가하더라도 `tests/server/test_domain_states.py`의 count-lock(`CustomerLifecycleState==11`, `SubscriptionStatus==4`, `FailureCategory==7`)을 **건드리지 않는다**(상수 권장 — enum 신설 시 기존 멤버 수 불변 필수). [Source: tests/server/test_domain_states.py:74,85,129, enum-member-count-locks memory]
- [x] **Task 2 — `jobs` 테이블 lease 컬럼 additive 마이그레이션 (AC2)**
  - [x] 현재 `jobs` ORM(`db/models/agent.py`)에는 lease/claim 컬럼이 **없다**(id, type, target_id, agent_id, status, run_after, attempts, error_code). lease 의미론을 위해 **additive**로 추가: `lease_expires_at`(`DateTime(timezone=True)`, nullable), `claimed_at`(`DateTime(timezone=True)`, nullable), `result_json`(`json_variant()` = Postgres JSONB, nullable — complete 결과 저장). 선택적으로 `created_at`/`updated_at`(tz-aware) 추가 가능(운영 가시성). **계약 Required fields(8개)는 그대로 유지** — 컬럼은 superset(5.2 `test_each_table_has_required_fields`가 superset 단언이라 무회귀). [Source: src/rider_server/db/models/agent.py:55-65, tests/server/test_db_schema.py:54,108]
  - [x] **새 Alembic 리비전** `migrations/versions/0002_*.py`를 만든다(`down_revision="0001"`). `0001_initial_schema.py`는 **수정 금지**(이미 done·커밋됨). `upgrade()`=`op.add_column`(jobs), `downgrade()`=`op.drop_column` round-trip. `5.2` env.py(async, offline/online) 그대로 사용. naming_convention으로 인덱스 이름 결정적 — claim 성능을 위해 `ix_jobs_status`(또는 `(status, run_after)` 복합) 인덱스 추가 권장(SKIP LOCKED 대상 행 스캔 최소화). [Source: 5-2 Task 4, migrations/versions/0001_initial_schema.py]
  - [x] `jobs` ORM(`db/models/agent.py`)에 동일 컬럼 추가 → autogenerate drift 0(모델↔마이그레이션 일치, offline SQL 렌더로 1차 확인). `Base.metadata.tables` 키 집합은 여전히 **14개**(테이블 추가 0 — 컬럼만). [Source: tests/server/test_db_schema.py:97]
- [x] **Task 3 — In-memory `QueueBackend` 구현 + PostgreSQL 구현 (`queue/postgres_queue.py`) (AC1·AC2·AC3)**
  - [x] **In-memory 구현**(`queue/memory_queue.py` 또는 backend.py 내): `threading.Lock`(또는 동등)으로 atomic claim을 강제해 단일-claim·lease 의미론·상태 전이를 **DB 없이** 구현한다. 이게 AC1 계약 테스트의 always-run 대상이자 AC4 end-to-end의 DB-less 경로다(빈 stub 금지 — 실제로 동작하는 fake). 주입 `now`로 lease 만료를 결정적으로 검증.
  - [x] **PostgreSQL 구현**(`queue/postgres_queue.py`): `AsyncSession` 기반. claim = `SELECT … FROM jobs WHERE status='PENDING' AND (run_after IS NULL OR run_after<=now) AND type = ANY(capabilities) ORDER BY run_after NULLS FIRST LIMIT max_jobs FOR UPDATE SKIP LOCKED` → 잡은 행을 `CLAIMED` + `agent_id` + `lease_expires_at=now+lease` + `claimed_at=now` 로 UPDATE, 같은 트랜잭션 commit. `recover_stale` = `UPDATE jobs SET status='PENDING', agent_id=NULL, lease_expires_at=NULL WHERE status IN ('CLAIMED','RUNNING') AND lease_expires_at < now`. **async 경계 가드 준수**: async 함수 본문에서 `time.sleep`/`subprocess.*` 직접 호출 금지(전 `rider_server/**` rglob 스캔). [Source: architecture.md:432-434, tests/server/test_server_async_boundary.py:66-71]
  - [x] **secret/redaction**: job `result_json`·`error_*`에 평문 token/OTP/password/raw HTML이 들어가지 않게 한다(Agent가 이미 `error_message_redacted`로 보냄 — 서버는 재마스킹 중복 금지, 그대로 저장/로그 시 한 번 더 redact 통과). DB 에러를 클라이언트로 흘릴 때는 5.1 `_error_response`/`redacted_error_event` envelope 재사용. [Source: src/rider_server/main.py:39-51, project-context.md L89,333]
- [x] **Task 4 — Agent API 라우트 `api/jobs.py` (claim/complete/events) + `create_app` 배선 (AC4)**
  - [x] `src/rider_server/api/` 패키지 신설(현재 없음). `api/jobs.py`에 FastAPI `APIRouter`로 `POST /v1/jobs/claim`, `POST /v1/jobs/{job_id}/complete`, `POST /v1/jobs/{job_id}/events` 정의(전부 `async def`). Pydantic v2 요청/응답 스키마(`schemas/`)로 본문 검증(snake_case, camelCase 변환 금지). [Source: architecture.md:438-440, project-context.md API 규약]
  - [x] **claim 라우트**: 본문 `{agent_id, capabilities, max_jobs}` → `QueueBackend.claim(...)` → 응답 `{"jobs":[{"job_id","type","target_id","lease_expires_at", …}]}`. `lease_expires_at`는 **ISO 8601 UTC 문자열 또는 epoch** — Agent `_coerce_lease_epoch`가 둘 다 수용하지만 **ISO 8601 UTC(`…Z`)로 통일**(ADD-13, 5.1 `_iso_utc_now` 재사용). job 없으면 `{"jobs":[]}`. [Source: src/rider_agent/job_loop.py:103-144,291-299, src/rider_server/main.py:29-36]
  - [x] **complete 라우트**: 본문 `{status, result_json, error_code, error_message_redacted, metrics, agent_id, started_at, finished_at}`. Agent는 **소문자** `status`(`"success"`/`"failed"`)를 보낸다 → 서버가 job 상태머신값으로 매핑(`success`→`SUCCEEDED`, `failed`→`FAILED`/retry). **lease 소유 검증**: 이 job이 여전히 `agent_id` 소유 + 미만료면 complete, 만료/재할당됐으면 **409/410** 반환(Agent `_complete`가 409/410을 `lease_lost`로 흡수 — 이중 success 차단). token revoke는 **401**. [Source: src/rider_agent/job_loop.py:302-329,546-574]
  - [x] **events 라우트**: 본문 `{event_type, severity, message_redacted, artifact_refs}` 수신(Agent가 claim 직후 `JOB_STARTED` emit). 본문에 secret/OTP 금지(이미 redact 통과값) — 저장/로깅 시 추가 redact 통과. [Source: src/rider_agent/job_loop.py:332-354, architecture.md:314-315]
  - [x] **auth seam**: 라우트는 `Authorization: Bearer <token>` → agent identity 해석 의존성(주입 가능 seam). **전체 token 발급/revoke/MFA/4역할은 5.8 소유** — 5.3은 bearer→agent_id 해석 + 401 경로만(테스트가 알려진 agent/token 주입). token을 로그/payload/예외에 평문 출력 금지(헤더에서만). [Source: src/rider_agent/job_loop.py:268-271, architecture.md:446]
  - [x] `create_app`에 `app.include_router(jobs_router)` 배선 + `app.state`에 backend 주입 seam(테스트가 in-memory/PG backend를 주입). 5.1 `create_app(settings)` 팩토리·전역 에러 envelope·`/v1/` 접두 규약을 그대로 계승(운영 엔드포인트는 root-level 유지). [Source: src/rider_server/main.py:54-127]
- [x] **Task 5 — 테스트 (AC1~AC4) — 5.1/5.2 `tests/server/` 패턴 계승 + `tests/negative/`**
  - [x] **(a) QueueBackend 계약 suite(항상 실행, DB-less)**: backend fixture로 **parametrize**(in-memory 항상, PostgreSQL은 `TEST_DATABASE_URL` 있을 때만 추가)해 동일 테스트가 양쪽에 통과: enqueue→claim→complete 해피패스, 빈 큐 claim→`[]`, capabilities 불일치 job 미claim, lease 만료 후 `recover_stale`→재claim 가능, 미정의 상태 전이 거부. 이게 "구현을 Redis/SQS로 옮길 수 있음"(P4-05)을 잠그는 1차 가드. (`tests/server/test_queue_backend.py` 권장)
  - [x] **(b) 단일-claim/exactly-once(in-memory, 항상 실행)**: 같은 PENDING job에 두 claim 요청 → 정확히 하나만 받고 다른 하나는 빈 응답. in-memory backend의 lock 기반 atomic claim으로 DB 없이 결정적 검증.
  - [x] **(c) Agent↔Server end-to-end(in-memory, 항상 실행)**: `rider_agent.job_loop`의 **실제** `JobRunner`/`claim_jobs`/`complete_job`을 `httpx.AsyncClient + ASGITransport`(5.1 `test_server_async_e2e.py` 패턴, `asyncio.run` — `pytest-asyncio` 미도입)로 `create_app`(in-memory backend 주입)에 in-process 연결. enqueue→Agent claim→`default_execute_job`(UNSUPPORTED_JOB_TYPE) 또는 주입 executor→complete까지 mock 없이 한 바퀴. **주의**: Agent transport는 `registration.Transport`/`HttpTransport` seam — ASGITransport를 그 seam에 어댑트하는 얇은 어댑터가 필요(또는 httpx 직접). [Source: tests/server/test_server_async_e2e.py:1-60, src/rider_agent/registration.py Transport]
  - [x] **(d) PostgreSQL-gated(skipif `TEST_DATABASE_URL` 없음) — `tests/negative/`**: 실 PG에서 (1) `FOR UPDATE SKIP LOCKED` 동시 claim(두 세션/태스크) → 정확히 하나만 success(`double Agent claim` negative, FR-13, Story 5.10 연계), (2) lease 만료→`recover_stale`→재할당, (3) 재할당된 job의 옛 소유자 complete가 409/410. 현 WSL/로컬 venv엔 Postgres 부재 가능 → skip + Completion Notes에 투명 명기(5.1 LOW-3·5.2 선례). **SQLite로 SKIP LOCKED를 흉내내지 않는다**(의미가 달라 오탐). [Source: 5-2 Task 5(c), operations-security-test-contract.md:47]
  - [x] **(e) job type 어휘 가드**: 정본 6종 == Agent `DEFAULT_CAPABILITIES` 값(문자열 비교, import 아님), 구표기(`CRAWL`/`RENDER`/`DISPATCH_TELEGRAM`/`BAEMIN_AUTH_OPEN`) 부재. **단방향 import 가드 green**: `tests/agent/test_agent_package.py` AST 가드가 `rider_server`→`rider_agent`/역방향을 잡는다 — queue가 `rider_agent`를 import하지 않음을 확인. [Source: tests/agent/test_agent_package.py, agent-job-type-vocab memory]
  - [x] **(f) 회귀/9-dep/async 가드**: 전체 스위트를 `.venv/Scripts/python.exe -m pytest`로 통과(직전 baseline **1428 passed, 1 skipped** 회귀 0)시키고 최종 수치를 Completion Notes에 **재측정** 기록(dev 단계와 QA gap-fill 후 수치 구분 — stale count 반복 지적). `test_pyproject_dependencies_unchanged_pins`(len==9)·단방향 import·async 경계 가드 green 확인. **새 third-party dep 추가 금지**(SQLAlchemy/asyncpg/FastAPI/httpx는 이미 `server`/`dev` extra에 존재). [Source: 5-2 Completion Notes, tests/agent/test_agent_package.py:217-225, server-deps-go-in-optional-group memory]

## Dev Notes

### 컨텍스트와 범위 경계 (가장 먼저 읽을 것)

- 이 스토리는 Epic 5의 **세 번째 스토리**다. 5.1이 FastAPI 런타임(`create_app`·전역 에러 envelope·async 핸들러)을, 5.2가 PostgreSQL 14테이블 ORM(`db/models/`)·Alembic async 스캐폴드(`migrations/`)·`db/base.py`(async engine/session)를 올렸다. 5.3은 그 위에 **첫 queue 추상화 + job claim 동작**을 추가하고, **Epic 4 Agent claim 루프와 실제로 연결**한다. `src/rider_server/queue/`와 `src/rider_server/api/`는 **현재 존재하지 않으며 이 스토리가 처음 만든다**. [Source: src/rider_server/ 트리, architecture.md:432-440]
- **5.3 범위 = QueueBackend 인터페이스 + in-memory/PG 구현 + lease·상태머신 + jobs lease 컬럼 additive 마이그레이션 + `api/jobs.py`(claim/complete/events) 라우트 배선 + 계약/통합/negative 테스트.**
- **5.3 범위 아님**(후속, 미리 만들지 말 것): scheduler의 due-target→CrawlJob 생성·jitter·circuit breaker(**5.4**), Telegram webhook/`/register`(**5.5**), Admin UI/대시보드/수동 액션(**5.6~5.7**), audit log·MFA·4역할·full token 발급/revoke(**5.8**), 100 fake target 부하 smoke(**5.10**, 단 double-claim negative는 5.3에서 1차 잠금 후 5.10이 부하 차원 확장). register/heartbeat **HTTP 라우트**는 5.3 필수 아님(lease 연장은 backend `extend_lease` 메서드로 제공; heartbeat 라우트 배선은 5.8/통합 스토리에서). `enqueue`는 5.4가 호출할 메서드만 제공(scheduler 로직은 5.4).
- **Agent 측(Epic 4)은 이미 완성** — 절대 수정하지 않는다. `rider_agent.job_loop`의 `claim_jobs`/`complete_job`/`emit_job_event`/`JobRunner`가 **서버 계약의 정본**이다. 서버는 **Agent가 기대하는 요청/응답 모양에 맞춘다**(서버가 Agent를 바꾸지 않는다). 단방향 의존(`rider_server`는 `rider_agent`를 import 금지)이라 값은 **미러**만 한다. [Source: src/rider_agent/job_loop.py, project-context.md L64]

### 🚨 절대 놓치면 안 되는 가드레일

1. **job type 정본은 6종 — epic 초안 이름은 구표기(사용 금지).** epics.md:972의 `CRAWL`/`RENDER`/`DISPATCH_TELEGRAM`/`KAKAO_SEND`/`BAEMIN_AUTH_OPEN`은 **outdated**다. architecture.md:308-313이 명시: 정본은 `CRAWL_BAEMIN`/`CRAWL_COUPANG`/`AUTH_CHECK`/`OPEN_AUTH_BROWSER`/`KAKAO_SEND`/`CAPTURE_DIAGNOSTIC`(배민 인증 job은 `AUTH_CHECK`/`OPEN_AUTH_BROWSER`로 분리됨). 이 값들은 `rider_agent.heartbeat.DEFAULT_CAPABILITIES`·architecture-contract Agent Job Types와 1:1이다. **구표기로 구현하면 Agent capability 매칭이 깨져 claim이 0건이 된다.** [Source: architecture.md:308-313, architecture-contract.md:120-129, src/rider_agent/heartbeat.py:62-76, agent-job-type-vocab memory]
2. **exactly-one-claim은 FR-13 서버 측 보장의 핵심 — `FOR UPDATE SKIP LOCKED`가 정본.** PG 구현은 반드시 row-level lock + SKIP LOCKED로 동시 claim에서 정확히 하나만 행을 잡게 한다. in-memory는 `threading.Lock`으로 동형 보장. complete 시점에도 lease 소유(agent_id + 미만료) 재검증으로 재할당된 job의 이중 success를 막는다(409/410). Agent `_complete`가 409/410을 `lease_lost`로 흡수하도록 이미 짜여 있으니 **서버가 409/410을 정확히 내야** 이중 성공이 막힌다. [Source: src/rider_agent/job_loop.py:540-574, architecture.md:349,620, data-api-contract.md]
3. **Agent는 소문자 status를 보낸다 — 서버가 매핑한다.** `job_loop.JOB_STATUS_SUCCESS="success"`, `JOB_STATUS_FAILED="failed"`. complete 라우트는 이 소문자를 받아 job 상태머신(`SUCCEEDED`/`FAILED`)으로 매핑한다. **DB `jobs.status`는 UPPER_SNAKE**(5.2 컨벤션: 상태는 대문자 enum 문자열). 소문자를 그대로 저장하면 상태머신/조회가 깨진다. `lease_lost`는 Agent가 서버에 보내지 않고 abandon한다(서버는 받을 일 없음). [Source: src/rider_agent/job_loop.py:80-82,302-329]
4. **새 third-party dep 0 + 9-dep lock 유지.** queue/api는 이미 설치된 SQLAlchemy(async)·asyncpg·FastAPI·httpx(`server`/`dev` extra)만 쓴다. `[project].dependencies`(정확히 9개)는 절대 건드리지 않는다 — `test_pyproject_dependencies_unchanged_pins`(len==9)가 6에픽 연속 green. Redis/celery 등 큐 라이브러리를 도입하지 않는다(queue=PostgreSQL 결정, 추상화로 추후 교체 가능성만 연다). [Source: tests/agent/test_agent_package.py:217-225, server-deps-go-in-optional-group memory, architecture.md:174]
5. **count-lock enum을 건드리지 말 것 — job type/status는 plain-string 상수.** `test_domain_states.py`가 `CustomerLifecycleState==11`·`SubscriptionStatus==4`·`FailureCategory==7`을 잠근다. job type/status를 기존 enum에 멤버로 추가하지 않는다. `heartbeat.py`/`job_loop.py`가 capability·status를 plain-string 상수로 둔 선례를 따라 module 상수로 정의해 후속이 추가해도 "정확히 N개" 테스트가 안 깨지게 한다. [Source: tests/server/test_domain_states.py:74,85,129, src/rider_agent/job_loop.py:77-93, enum-member-count-locks memory]
6. **`DispatchJobStatus`(2.6)와 reconcile하되 강결합 금지.** `services/subscription_gate.py:46`의 `DispatchJobStatus`(PENDING/HELD/SUCCEEDED)는 **게이트-facing 최소 부분집합**이고 docstring이 "전체 jobs status 정본(CLAIMED/RUNNING/FAILED/RETRY)은 Epic 5 소유, 그때 reconcile"이라 명시한다 — 이 스토리가 그 "전체 정본"을 정의한다. 같은 의미값은 같은 문자열(`PENDING`/`SUCCEEDED`)로 두되, queue가 subscription_gate를 import해 강결합하지 않는다(레이어 분리). [Source: src/rider_server/services/subscription_gate.py:46-56]
7. **단방향 import + async 경계.** queue/api는 `rider_server` 내부 + `rider_crawl`(redaction 재사용)만 import. `rider_agent` import 금지(AST 가드). async 함수 본문에서 blocking sync(`time.sleep`/`subprocess.*`) 직접 호출 금지(전 `rider_server/**` rglob 스캔) — lease 만료 대기 등은 주입 `now`로 테스트하고 실행 시 async-native. [Source: tests/agent/test_agent_package.py:232-245, tests/server/test_server_async_boundary.py:66-71, project-context.md L64]
8. **secret 평문 0.** job `result_json`·`error_*`·events 본문에 token/OTP/password/raw HTML 금지. Agent가 보내는 `error_message_redacted`는 이미 redact 통과값이니 **재마스킹 중복 금지** — 저장/로그 시 5.1 `redacted_error_event`/`redact` 재사용으로 한 번 더 통과만. DB·로그·예외에 평문 secret 금지(NFR-8). [Source: project-context.md L89,333, operations-security-test-contract.md:93, src/rider_server/main.py:39-51]

### 아키텍처 패턴과 규약 (정본)

- **데이터/큐 아키텍처**(architecture.md:140,162-174,496): 단일 PostgreSQL, queue도 같은 DB(`jobs` 테이블) → 트랜잭션 일관성. Redis 미도입(캐싱 레이어 없음) → idempotency·exactly-one-claim이 DB 유니크/락에 응집. Queue backend = "PostgreSQL job table (FOR UPDATE SKIP LOCKED), QueueBackend interface". [Source: architecture.md:140,432-434]
- **Communication/Event-Job 패턴**(architecture.md:306-322): job type UPPER_SNAKE, 상태 전이는 정의된 set만. lease = claim 시 만료시각 부여, heartbeat로 연장, 만료 시 stale 회수. 상태값은 enum 문자열 단일 정본, 상태 전이는 service/backend 레이어에서만(직접 DB 컬럼 임의 변경 금지). [Source: architecture.md:306-322]
- **at-least-once + dedup + lease**(architecture.md:72-73,349): 분산 job은 at-least-once 가정 → DB 유니크 제약(`uq_delivery_logs_dedup_key`, 5.2 제공)·lease로 안전화. exactly-once를 오가정하지 않는다(crash-after-send/중복 claim/stale token/replay를 lease+유니크로 흡수). [Source: architecture.md:72-73,349,620]
- **API 규약**(project-context.md, architecture.md:290-298): `/v1/` 복수 명사, JSON snake_case(camelCase 변환 0), 에러 `{"error":{"code":"<UPPER_SNAKE>","message_redacted":"…"}}` + 의미 있는 HTTP 상태(400/401/403/404/409/422/429/503), 시각 ISO 8601 UTC(`…Z`, epoch 혼용 금지). 5.1 `create_app`·전역 exception handler·`_iso_utc_now`를 재사용. [Source: src/rider_server/main.py:29-127, architecture.md:290-298]
- **레이어 분리**(architecture.md:276-279,300-304): `domain/`=순수 dataclass, `db/models/`=ORM, `services/`=정책, `queue/`=backend 추상화/구현, `api/`=Pydantic 경계. API 경계는 Pydantic v2, 내부는 dataclass — 교차 시 명시적 변환. `ClaimedJobRecord` 등 backend 반환형은 중립 dataclass 권장(PG ORM Row를 api/agent로 직접 누출 금지). [Source: architecture.md:300-304,428-434]
- **시각/타입**: lease/claim 시각은 `DateTime(timezone=True)`. API 직렬화는 ISO 8601 UTC. `lease_expires_at` 응답은 ISO 8601 UTC 문자열로 통일(Agent `_coerce_lease_epoch`가 ISO `Z`·epoch·숫자문자열 모두 수용하므로 안전, 단 모르는 형은 Agent가 fail-closed로 abandon하니 표준형 준수가 중요). [Source: src/rider_agent/job_loop.py:360-384, architecture.md:297]

### Agent 클라이언트 계약 (서버가 맞춰야 할 정본 — `src/rider_agent/job_loop.py`)

| 엔드포인트 | 요청 본문 | 응답(서버가 내야 할 것) | Agent 처리 |
| --- | --- | --- | --- |
| `POST /v1/jobs/claim` | `{agent_id, capabilities[], max_jobs}` + `Authorization: Bearer` | `{"jobs":[{job_id*, type, target_id, lease_expires_at, …}]}` | `ClaimedJob.list_from_response` — `job_id` 없는 항목/비-list는 fail-closed로 버림 |
| `POST /v1/jobs/{id}/events` | `{event_type, severity, message_redacted, artifact_refs[]}` | 2xx | best-effort, 실패해도 루프 계속 |
| `POST /v1/jobs/{id}/complete` | `{status(소문자), result_json, error_code, error_message_redacted, metrics, agent_id, started_at, finished_at}` | 2xx=정상, **409/410**=lease lost/재할당, **401**=token revoke | 409/410→`lease_lost` 흡수, 401→재등록 surfacing |

- claim 응답 `lease_expires_at`는 ISO 8601 UTC 권장. complete의 lease 소유 검증으로 **재할당된 job에 옛 소유자가 success를 기록하지 못하게** 409/410을 내는 것이 이중 성공 방지의 서버 측 책임이다(Agent는 이미 흡수 로직 보유). [Source: src/rider_agent/job_loop.py:103-144,277-354,540-585]

### `jobs` 테이블 현재 상태 ↔ 추가 컬럼 (정본)

- **현재**(5.2, `db/models/agent.py:55-65`): `id`(UUID PK), `type`(String), `target_id`(FK→monitoring_targets, nullable), `agent_id`(FK→agents, nullable — claim 전 미할당), `status`(String), `run_after`(DateTime tz, nullable), `attempts`(Integer, default 0), `error_code`(String, nullable=FailureCategory 값).
- **추가(additive, 0002 마이그레이션)**: `lease_expires_at`(DateTime tz, nullable), `claimed_at`(DateTime tz, nullable), `result_json`(JSONB via `json_variant()`, nullable). 선택: `created_at`/`updated_at`(tz-aware). 인덱스 `ix_jobs_status`(또는 `(status, run_after)`) 권장.
- `attempts`/`run_after`는 이미 있으니 **재시도/backoff에 그대로 사용**(error_code별 backoff — 고정 5초 무한 재시도 금지, ADD-15). 계약 Required 8필드는 불변(superset 유지 → 5.2 schema 테스트 무회귀). [Source: src/rider_server/db/models/agent.py:55-65, architecture.md:330, tests/server/test_db_schema.py:54,108]

### 테스트 전략 (계층화 — 5.2 (a)/(b)/(c) 선례 계승)

- **항상 실행(DB-less)**: (a) QueueBackend 계약 suite(in-memory + PG-gated parametrize), (b) 단일-claim/exactly-once(in-memory lock), (c) Agent↔Server end-to-end(httpx ASGITransport, 실 `job_loop`), (e) job type 어휘·import 가드.
- **PG-gated(skipif `TEST_DATABASE_URL` 없음)**: (d) `FOR UPDATE SKIP LOCKED` 동시 claim exactly-once, lease 만료 stale 회수/재할당, 옛 소유자 complete 409/410 — `tests/negative/`. 현 환경 Postgres 부재 시 skip + Completion Notes 투명 명기.
- **anti-pattern 회피**: SQLite로 SKIP LOCKED를 흉내내지 않는다(락 의미가 달라 오탐). in-memory fake는 lock으로 단일-claim "의미"를 잠그고, PG-gated가 실제 SKIP LOCKED "동작"을 잠근다 — 둘을 분리한다(5.2의 metadata vs offline-SQL vs Postgres-gated 3분할과 동형).
- `tests/server/` 패턴 계승: 상단 `"""Story 5.3 / ACx …"""` docstring, `from __future__ import annotations`, fake fixture만(실제 토큰/전화/이메일/chat_id 형태 금지). `pytest-asyncio` 미도입 → `asyncio.run`(5.1 `test_server_async_e2e.py`). [Source: tests/server/test_server_async_e2e.py:1-60, tests/server/test_db_schema.py]

### Previous Story Intelligence (5.1·5.2 → 5.3 인계)

직접 적용:
- **5.2 `jobs` 테이블은 구조만 — claim 로직 0.** `db/models/agent.py` docstring이 "jobs 상태머신·claim(`FOR UPDATE SKIP LOCKED`)·lease는 Story 5.3 소유"라 명시. 5.3이 그 로직 + lease 컬럼을 채운다. [Source: src/rider_server/db/models/agent.py:1-6]
- **5.2 `db/base.py` 재사용**: `Base`·`json_variant()`(JSON→JSONB)·async engine/`async_sessionmaker`·naming_convention. PG queue는 이 세션 팩토리를 쓴다(새 engine 만들지 말 것). [Source: 5-2 Completion Task 2, src/rider_server/db/base.py]
- **5.1 `create_app(settings)` 팩토리·에러 envelope·`_iso_utc_now` 재사용**: 라우터 include + 에러 핸들러 계승. 운영 엔드포인트는 root-level, 리소스는 `/v1/`. [Source: src/rider_server/main.py:54-131]
- **editable 설치 회피(5.1 cp949 / 5.2 선례)**: 한글 경로 `.pth` UnicodeDecodeError 회피 — `uv pip install -e` 대신 third-party만 설치 + `pythonpath=["src"]` 의존. [Source: 5-2 Debug Log]
- **환경 제약 투명 문서화(5.1 LOW-3 / 5.2 선례)**: Postgres-gated 테스트는 미가용 시 skip + Completion Notes에 명기. AC2/AC4의 실DB literal fidelity는 `TEST_DATABASE_URL` 환경에서 확정. [Source: 5-2 Completion Notes 환경 제약]
- **stale test count 방지**: dev 종료 수치와 QA gap-fill 후 수치를 구분해 재측정 기록(5.2가 39/1402 → 65/1428로 보정한 선례). [Source: 5-2 Senior Developer Review MEDIUM, stale-test-count-a2 memory]

### Git Intelligence

- baseline=`97580a4`(story-5.2, `feat(story-5.1)`/`feat(story-5.2)` 컨벤션 계승 → `feat(story-5.3): …`). 직전 커밋이 5.2 DB 스키마라 `jobs` 테이블·`db/base.py`·`migrations/`가 이미 있고, `queue/`·`api/`는 0줄 — 5.3이 첫 queue/api 코드. [Source: git log]
- 트리가 CRLF/LF로 noisy할 수 있다 — 실제 변경 확인은 `git diff -w`, idempotent 파일 쓰기는 `\n`으로 빌드(text-mode `\r\n` 재변환 회피). pytest는 `.venv/Scripts/python.exe -m pytest`로 실행. [memory: dev-env-quirks, crlf-roundtrip-idempotency]

### Project Structure Notes

- 신설: `src/rider_server/queue/{__init__,backend,postgres_queue,memory_queue}.py`, `src/rider_server/api/{__init__,jobs}.py`, `src/rider_server/schemas/`(Pydantic 요청/응답 — 신설 또는 jobs 내), `migrations/versions/0002_*.py`, `tests/server/test_queue_backend.py`·`tests/server/test_jobs_api.py`(또는 통합), `tests/negative/`(신규 디렉터리 — PG-gated double-claim/stale). 수정: `src/rider_server/db/models/agent.py`(jobs lease 컬럼 additive), `src/rider_server/main.py`(라우터 include + backend seam). 기존 3패키지 구조·`tests/` 미러와 정합.
- 건드리지 않음: `src/rider_crawl/**`, `src/rider_agent/**`(특히 `job_loop.py`/`heartbeat.py` — 계약 정본, 읽기만), `src/rider_server/domain|migration/**`, `migrations/versions/0001_initial_schema.py`(done·커밋됨 — 새 0002로만), `runtime/`·`logs/`·`secrets/`·`build/`·`.venv/`.
- 변이/주의: (1) lease_expires_at 응답 포맷을 ISO 8601 UTC로 통일(Agent가 epoch도 수용하나 표준형 권장). (2) auth seam은 최소(bearer→agent_id) — full token lifecycle은 5.8, 미리 만들지 말 것. (3) `enqueue`는 메서드만 제공(scheduler 호출 흐름은 5.4). (4) `tests/negative/`는 신규 디렉터리 — architecture.md:466 트리에 정의된 위치.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.3 L952-978] — 스토리·AC 정본(BDD). **단, job type 초안 이름(CRAWL/RENDER/…)은 구표기** — architecture.md:308-313 정본 우선.
- [Source: _bmad-output/planning-artifacts/architecture.md L308-322] — job type 정본 6종·구표기 명시·상태 전이·lease 의미론.
- [Source: _bmad-output/planning-artifacts/architecture.md L140,162-174,432-434,496] — queue=PostgreSQL·`FOR UPDATE SKIP LOCKED`·QueueBackend·`queue/backend.py`+`postgres_queue.py`·Redis 미도입.
- [Source: _bmad-output/planning-artifacts/architecture.md L290-304,438-440] — API 규약(`/v1/` snake_case·에러 envelope·ISO UTC)·`api/jobs.py`(claim/events/complete).
- [Source: _bmad-output/planning-artifacts/architecture.md L72-73,349,620] — at-least-once + lease + DB 유니크로 분산 job 안전화.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md L74] — P4-05: "Build queue abstraction. QueueBackend interface tests pass and implementation can move to Redis/SQS."
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md L96-129] — main_loop(claim→execute→complete)·Agent Job Types 6종 정본.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md L36,71-81] — `jobs` Required fields·`/v1/jobs/claim`·`/complete`(status·result_json·error_code·error_message_redacted·metrics)·`/events`.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md L47,51] — Integration(Agent API·job lifecycle)·Load smoke(100 fake targets jitter/queue no job storm).
- [Source: src/rider_agent/job_loop.py L80-144,277-354,540-585] — **Agent 클라이언트 계약 정본**(claim/complete/events 본문·소문자 status·409/410/401 처리·lease self-check).
- [Source: src/rider_agent/heartbeat.py L62-76] — `DEFAULT_CAPABILITIES` = job type 정본 6종(문자열 미러 대상, import 금지).
- [Source: src/rider_server/db/models/agent.py L48-65] — 현 `jobs` ORM(lease 컬럼 없음 — 5.3이 additive 추가)·docstring("claim/lease = 5.3 소유").
- [Source: src/rider_server/db/base.py] — `Base`·`json_variant()`·async engine/session(재사용).
- [Source: src/rider_server/main.py L29-131] — 5.1 `create_app`·전역 에러 envelope·`_iso_utc_now`·`/v1/` 규약(계승).
- [Source: src/rider_server/services/subscription_gate.py L46-56] — `DispatchJobStatus` 게이트 부분집합(reconcile 대상, 강결합 금지).
- [Source: tests/server/test_server_async_e2e.py L1-60] — httpx ASGITransport + asyncio.run async e2e 패턴(pytest-asyncio 미도입).
- [Source: tests/server/test_db_schema.py L54,97,108] — schema 가드(테이블 14 고정·required fields superset — 컬럼 additive 무회귀).
- [Source: tests/server/test_domain_states.py L74,85,129] — enum count-lock(11/4/7 — 건드리지 말 것).
- [Source: tests/agent/test_agent_package.py L217-245] — 9-dep lock·단방향 import AST 가드.
- [Source: tests/server/test_server_async_boundary.py L66-71] — async 경계 가드(전 `rider_server/**` rglob).
- [Source: _bmad-output/project-context.md L20,64,81,89] — 3패키지·단방향 import·9-dep 고정·secret/`*_ref`·queue=PostgreSQL.
- [Source: _bmad-output/implementation-artifacts/5-2-….md] — 직전 스토리(jobs 테이블 구조·db/base·환경 제약·stale count 선례).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, BMAD dev-story workflow)

### Debug Log References

- 베이스라인 측정: `.venv/Scripts/python.exe -m pytest -q` → **1428 passed, 1 skipped**(dev 시작 시점).
- 편집 회피(5.1/5.2 선례): editable 설치 안 함 — `pythonpath=["src"]` 의존(한글 경로 `.pth` cp949 회피). 모든 신규 파일은 `\n` 으로 작성(CRLF 재변환 회피).
- 단일 회귀 1건 발생·수정: 5.3 이 첫 `/v1/` 리소스 라우트를 추가하자 5.1 가드 `test_registered_routes_have_no_v1_operational_paths`(전체 `/v1/` 금지 단언)가 깨짐 → **운영 엔드포인트가 root-level 인지**만 검증하도록 의도 보존 수정(리소스 `/v1/jobs/*` 허용). 그 외 회귀 0.
- 추가로 5.2 schema 가드 2건을 additive-migration 시대에 맞게 갱신(아래 Completion Notes 참고).
- 환경 제약: 현 WSL/venv 에 PostgreSQL 부재 → PG-gated 테스트(계약 suite의 postgres 파라미터 14건 + `tests/negative/` 3건 = 17건)는 **skip**. `TEST_DATABASE_URL` 환경에서 실 SKIP LOCKED/lease/409 확정 필요.

### Completion Notes List

**구현 요약(Task 1~5):**
- **Task 1 — `queue/backend.py` + `queue/states.py`**: 중립 async `QueueBackend` ABC(`enqueue`/`claim`/`complete`/`extend_lease`/`recover_stale`/`emit_event`) + 중립 dataclass(`ClaimedJobRecord`/`CompleteOutcome`). job type 6종·status 6종은 **plain-string 상수/tuple**(enum 아님 — count-lock 회피). 허용 전이표 + `assert_transition`(미정의 전이 `InvalidJobTransition`) + `map_agent_status`(소문자→UPPER_SNAKE). job type 값은 `rider_agent.heartbeat.DEFAULT_CAPABILITIES` 와 **문자열 미러**(import 0).
- **Task 2 — jobs lease 컬럼 additive**: `db/models/agent.py` 에 `lease_expires_at`/`claimed_at`(tz-aware) + `result_json`(JSONB) + 복합 인덱스 `ix_jobs_status (status, run_after)` 추가. 신규 Alembic `0002_jobs_lease_columns`(down_revision=`0001`, 0001 무수정). 계약 Required 8필드 불변(superset). 테이블 수 여전히 14.
- **Task 3 — in-memory + PostgreSQL 구현**: `memory_queue.py`(`threading.Lock` atomic claim — 실동작 fake), `postgres_queue.py`(`SELECT … FOR UPDATE SKIP LOCKED` claim + `recover_stale` bulk UPDATE). 둘 다 주입 `now` 로 lease 결정적 검증. async 경계 가드 준수(blocking sync 직접 호출 0).
- **Task 4 — `api/jobs.py` 라우트 + `create_app` 배선**: `POST /v1/jobs/claim`·`/{id}/complete`·`/{id}/events`(전부 async, Pydantic v2 snake_case). lease_expires_at 응답 ISO 8601 UTC(`…Z`). complete 시 lease 소유 검증 → 재할당/만료 **409**, 미존재 **404**, 알 수 없는 status **422**. bearer→agent_id 해석 seam(`app.state.resolve_agent_id`, 기본은 presence 게이트 + 401, token 평문 비반환) + backend 주입 seam(`app.state.queue_backend`). 5.1 에러 envelope 계승.
- **Task 5 — 테스트**: (a) backend-중립 계약 suite parametrize(in-memory 항상 + PG-gated), (b) in-memory exactly-once(`asyncio.gather` 두 claim → 정확히 하나), (c) **실 `rider_agent.JobRunner`↔실 서버 큐 e2e**(TestClient→Transport 어댑터, mock 0 — claim→execute→complete 한 바퀴, success/UNSUPPORTED 두 경로), (d) PG-gated `tests/negative/`(SKIP LOCKED 동시 claim·lease 회수 재할당·옛 소유자 409), (e) job type 어휘·구표기 부재·queue/api→rider_agent import 0 가드.

**테스트 수치(재측정 — 리뷰 단계 reconcile):**
- 베이스라인: **1428 passed, 1 skipped**.
- dev 종료 시점(스테일): 1453 passed, 11 skipped(아래 QA 갭 적용 전 측정값 — 보정됨).
- **5.3 최종(QA 갭 적용 후, 리뷰 재측정 정본): 1469 passed, 18 skipped**(`.venv/Scripts/python.exe -m pytest -q`). 신규 +41 passed, +17 skipped(PG-gated: 계약 suite postgres parametrize 14 + `tests/negative/` 3 = 17). 회귀 0. [stale-test-count 패턴 — `test-summary-5-3.md` 와 일치]
- 9-dep lock(`test_pyproject_dependencies_unchanged_pins` len==9)·단방향 import(agent↔server, queue/api→agent)·async 경계·enum count-lock(11/4/7) 전부 green. **새 third-party dep 0**(SQLAlchemy/asyncpg/FastAPI/httpx 기존 extra 재사용, Redis/celery 미도입).

**기존 가드 갱신(additive-migration / 첫 `/v1/` 라우트 도입에 따른 의도 보존 수정 — 3건):**
- `tests/server/test_db_schema.py::test_single_migration_head_with_initial_base`: 단일 head 가 `0001`→`0002` 로 이동(선형 체인 유지)에 맞게 갱신.
- `tests/server/test_db_schema.py::test_migration_renders_every_model_column`: additive 컬럼이 `ALTER TABLE jobs ADD COLUMN` 으로 렌더되므로 CREATE 블록 + 같은 테이블 ALTER 문까지 탐색(drift 가드 의도 유지).
- `tests/server/test_server_app.py::test_registered_routes_have_no_v1_operational_paths`: "전체 `/v1/` 금지" → "**운영** 엔드포인트만 root-level(리소스 `/v1/jobs/*` 허용)"로 의도 보존 수정. 테스트의 본래 주석이 이미 "/v1/ 는 리소스 엔드포인트(5.3+) 전용"이라 명시.

**환경 제약(투명 명기, 5.1 LOW-3 / 5.2 선례):** 현 WSL/venv 에 PostgreSQL 부재 → `FOR UPDATE SKIP LOCKED` 실동작·lease 만료 실 회수·옛 소유자 complete 409 의 **실 PG literal fidelity** 는 미확정(skip). in-memory 계약/exactly-once/e2e 가 단일-claim·lease·상태머신 의미를 항상 잠근다. `TEST_DATABASE_URL` 지정 시 PG-gated 17건(계약 parametrize 14 + negative 3)이 실행되어 AC2/AC4 의 실DB 동작을 확정한다. SQLite 로 SKIP LOCKED 를 흉내내지 않음(의미 차이 오탐 회피). [리뷰 보정: PG-gated 테스트의 agent_id 는 유효 UUID + `agents` 행 시드 필요 — 비-UUID 문자열은 `jobs.agent_id` FK·Uuid 타입에서 ValueError/FK 위반을 일으킴, Senior Developer Review HIGH-1 참고.]

**스코프 경계 준수:** `rider_agent/**`·`rider_crawl/**`·`0001_initial_schema.py`·`domain/`·`migration/` 무수정. scheduler(5.4)·Telegram(5.5)·Admin(5.6~7)·full token lifecycle/MFA/감사(5.8)·부하 smoke(5.10)는 미구현(미리 만들지 않음). `enqueue` 는 메서드만 제공(scheduler 호출은 5.4), heartbeat 라우트 미배선(lease 연장은 `extend_lease` 메서드로만 제공).

### File List

**신규(소스):**
- `src/rider_server/queue/__init__.py`
- `src/rider_server/queue/states.py`
- `src/rider_server/queue/backend.py`
- `src/rider_server/queue/memory_queue.py`
- `src/rider_server/queue/postgres_queue.py`
- `src/rider_server/api/__init__.py`
- `src/rider_server/api/jobs.py`
- `migrations/versions/0002_jobs_lease_columns.py`

**신규(테스트):**
- `tests/server/test_queue_backend.py`
- `tests/server/test_jobs_api.py`
- `tests/server/test_job_vocab.py`
- `tests/negative/test_queue_concurrency.py`

**수정:**
- `src/rider_server/main.py` (create_app: queue_backend/resolve_agent_id seam + `include_router(jobs_router)`)
- `src/rider_server/db/models/agent.py` (jobs lease 컬럼 additive + `ix_jobs_status` 인덱스 + docstring)
- `tests/server/test_db_schema.py` (additive-migration 대응: head=0002, ALTER 인지 drift 가드)
- `tests/server/test_server_app.py` (운영 엔드포인트 root-level 가드를 의도 보존 수정 — 리소스 `/v1/` 허용)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (5-3 → in-progress → review)

## Change Log

| 날짜 | 변경 | 작성자 |
| --- | --- | --- |
| 2026-06-14 | Story 5.3 구현: QueueBackend 추상화(in-memory + PostgreSQL `FOR UPDATE SKIP LOCKED`), jobs lease 컬럼 additive(0002), Agent API claim/complete/events 라우트 배선, 계약/통합/PG-gated negative 테스트. 1428→1453 passed(회귀 0). Status → review. | Amelia (dev-story) |
| 2026-06-14 | Senior Developer Review(AI, auto-fix): HIGH-1 PG-gated 테스트가 비-UUID agent_id + agents FK 미시드로 실 PG 실행 시 에러 → 유효 UUID agent_id + `agents` 시드로 수정(`test_queue_backend.py`·`tests/negative/test_queue_concurrency.py`). MEDIUM-1 스테일 테스트 수치 reconcile(1453/11 → 1469/18). 전체 스위트 1469 passed, 18 skipped(회귀 0). | Senior Developer Review (AI) |

## Senior Developer Review (AI)

**Reviewer:** 서영 (story-automator adversarial review, auto-fix mode) · **Date:** 2026-06-14 · **Outcome:** ✅ Approve (auto-fix 적용 후, 0 CRITICAL)

**Scope:** `src/rider_server/queue/**`, `src/rider_server/api/jobs.py`, `src/rider_server/main.py`, `src/rider_server/db/models/agent.py`, `migrations/versions/0002_*`, 5.3 테스트 4종 + 수정된 5.2 가드 2종. (`_bmad/`·`_bmad-output/` 제외.) git 변경 ↔ File List 일치(소스 불일치 0).

### Findings

| # | Severity | 내용 | 조치 |
| --- | --- | --- | --- |
| HIGH-1 | High | **PG-gated 테스트가 실 PG 에서 통과 불가(에러).** 계약 suite(`test_queue_backend.py`)·`tests/negative/`가 `agent_id="agent-1"/"agent-2"/"a"` 사용 → PG `_as_uuid("agent-1")` ValueError, 또 `jobs.agent_id`→`agents.id` FK(`fk_jobs_agent_id_agents`)에 `agents` 행 미시드 → FK 위반. AC1 "양쪽 동일 통과"·Completion Notes "TEST_DATABASE_URL 시 PG-gated 실행 확정" 주장이 실제로는 즉시 에러. (현 환경 PG 부재로 dev 가 실행 못 해 미발견.) | **Fixed** — 유효 UUID agent_id(`_AGENT_1/_AGENT_2`) + PG fixture 에 `agents` 행 시드(`_seed_agents`). in-memory 경로 무영향(41 passed 유지). PG 실행은 환경 부재로 미검증이나 구성상 정합. |
| MEDIUM-1 | Medium | **스테일 테스트 수치.** Dev Agent Record 가 `1453 passed, 11 skipped` 기재, 실제 `1469 passed, 18 skipped`(+41/+17). QA 갭(test-summary-5-3.md)이 이미 보정·reconcile 요청. (recurring stale-count 패턴.) | **Fixed** — Completion Notes·Debug Log·Change Log 를 `1469/18`(PG-gated 17 = 계약 14 + negative 3)로 정정. |
| LOW-1 | Low | `complete` 라우트의 `except InvalidJobTransition`(`api/jobs.py:182`)는 사실상 도달 불가(backend 가 in-flight 아니면 `assert_transition` 전에 LEASE_LOST 반환, CLAIMED/RUNNING→SUCCEEDED/FAILED 는 항상 허용). 방어 코드로 무해. | 유지(방어). |
| LOW-2 | Low | bearer 해석 `agent_id`(`resolve_agent`)는 presence/401 만 강제하고 실제 queue 연산은 `body.agent_id` 사용 — token↔body 미결속. 5.3 최소 seam(전체 auth=5.8)으로 문서화됨. | 유지(스코프). |

### 검증

- AC1~AC4: in-memory 계약/exactly-once/실 Agent e2e/어휘·import 가드 always-run green. PG-gated 동작(SKIP LOCKED/lease/409)은 `TEST_DATABASE_URL` 환경 필요(현 부재 — skip, 투명 명기). HIGH-1 수정으로 그 환경에서 실제 실행 가능해짐.
- 가드 무회귀: 9-dep lock(len==9)·단방향 import(queue/api↛rider_agent)·async 경계·enum count-lock(11/4/7)·테이블 14 고정 — 전부 green.
- 전체 스위트: **1469 passed, 18 skipped**(회귀 0).
