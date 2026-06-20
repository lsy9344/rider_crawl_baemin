# 수백 개 크롤링 운영 구조 검토

작성일: 2026-06-19  
검토 범위: `rider_server` 스케줄러/큐/API/대시보드, `rider_agent` 실행 루프/브라우저 프로필/워커, PostgreSQL/배포 설정

## 결론

현재 구조는 수백 개의 "예약된 크롤링 대상"을 중앙 서버에 쌓고 여러 Agent PC가 나눠 처리하는 방향으로 잘 설계되어 있습니다. PostgreSQL job queue, lease, `FOR UPDATE SKIP LOCKED`, stale lease 회수, scheduler capacity throttle, deterministic jitter가 이미 있어 큐의 기본 골격은 확장 가능한 편입니다.

다만 지금 상태를 그대로 두고 "수백 개 브라우저 크롤링을 안정적으로 동시에 돌릴 수 있다"고 보기는 어렵습니다. 현재 실행 모델은 Agent 1개가 기본적으로 1개 job을 순차 처리하는 구조이고, 크롤러는 같은 Agent 프로세스 안에서 직접 호출됩니다. 그래서 수백 개 운영은 "수백 개 동시 브라우저"가 아니라 "여러 Agent PC가 각자 감당 가능한 수만 처리하는 fleet 모델"로 잡아야 합니다.

짧게 말하면:

- 수백 개 pending job과 여러 Agent의 분산 claim은 현재 구조로 가능성이 높습니다.
- 수백 개 동시 Chromium/Playwright 크롤링은 단일 서버나 단일 Agent PC로는 위험합니다.
- 운영 전에는 비밀값, scheduler/read-model N+1, stale recovery 분리, heartbeat bulk lease, 브라우저 프로필 수명, retry/status 저장, DB pool/load test가 보강되어야 합니다.

## 검토 기준

이 문서에서 "수백 개"는 세 가지를 분리해서 봅니다.

1. 등록된 대상 수: `monitoring_targets`가 수백 개 있는 상태
2. 대기/진행 job 수: `jobs` 테이블에 pending/claimed/running job이 수백 개 있는 상태
3. 실제 동시 브라우저 수: Chrome/CDP 프로필이 동시에 수십에서 수백 개 뜨는 상태

현재 코드는 1번과 2번을 향해 가는 구조입니다. 3번은 별도 worker pool, 프로세스 격리, hard timeout/kill, PC별 리소스 제한이 필요합니다.

## Scale Readiness 요약

| 영역 | 현재 판단 | 이유 |
| --- | --- | --- |
| Queue 동시 claim | 양호 | `PostgresQueueBackend.claim()`이 `FOR UPDATE SKIP LOCKED`를 사용합니다. |
| Lease/소유권 검증 | 양호 | complete 시 agent 소유와 lease 만료를 검증합니다. |
| Agent 장애 복구 | 보통 | stale 회수는 있지만 claim 요청 경로에 붙어 있습니다. |
| Scheduler admission | 보통 | capacity와 active job 체크는 있으나 due 전체 조회와 target별 조회가 있습니다. |
| Agent 실행 | 보수적 | 기본 `max_jobs=1`, 순차 처리입니다. 안전하지만 처리량은 Agent 수에 의존합니다. |
| 브라우저 프로필 | 보통 이하 | 대상별 격리는 좋지만 idle cleanup과 release 정책이 약합니다. |
| 비밀값 | 위험 | DB 컬럼과 job payload에 로그인 정보가 평문 의미로 흐릅니다. |
| Retry/backoff | 미완 | 정책 함수는 있으나 queue complete 재진입에 연결되어 있지 않습니다. |
| 운영 대시보드 | 보통 이하 | read-model도 target/agent별 N+1 조회와 30초 전체 fragment polling이 있습니다. |
| 배포/DB pool | 미완 | DB pool, API worker, Postgres 연결 수가 운영 env로 충분히 열려 있지 않습니다. |

## 현재 구조의 장점

1. 큐 추상화가 잘 되어 있습니다.
   `QueueBackend`가 in-memory와 PostgreSQL 구현을 분리합니다. API와 Agent는 DB 세부를 직접 알지 않습니다. 근거: `src/rider_server/queue/backend.py`.

2. 동시 claim 안전장치가 있습니다.
   PostgreSQL 구현은 `with_for_update(skip_locked=True)`를 사용합니다. 여러 Agent가 동시에 claim해도 같은 job을 동시에 잡을 가능성을 줄입니다. 근거: `src/rider_server/queue/postgres_queue.py:201`.

3. lease 기반 소유권 검증이 있습니다.
   claim 시 `DEFAULT_LEASE_SECONDS = 120.0`을 부여하고, complete 때 owner mismatch, status, lease 만료를 확인합니다. 근거: `src/rider_server/api/jobs.py:48`, `src/rider_server/queue/postgres_queue.py:251`.

4. stale 회수용 인덱스가 있습니다.
   `jobs(status, lease_expires_at)` partial index가 있어 만료 lease 회수 쿼리를 키울 수 있습니다. 근거: `src/rider_server/db/models/agent.py:95`, `migrations/versions/0013_jobs_stale_lease_index.py`.

5. 스케줄러에 폭주 방지 개념이 있습니다.
   스케줄러는 online Agent capacity와 현재 in-flight 수를 읽고, tick 안에서 `aggregate_in_flight`를 증가시키며 신규 enqueue를 제한합니다. 100 target smoke 테스트도 있습니다. 근거: `src/rider_server/scheduler/service.py:197`, `tests/server/test_scheduler_tick.py:395`, `tests/server/test_scheduler_tick.py:430`.

6. 대상별 브라우저 프로필 격리 방향은 좋습니다.
   `BrowserProfileManager`는 tenant/target별 profile dir과 CDP port를 나누고, 중복 포트/프로필을 거부합니다. 근거: `src/rider_agent/browser_profile.py:224`.

7. production DB guard가 있습니다.
   `create_app()`은 production에서 DB 없이 in-memory backend로 뜨는 것을 막습니다. 근거: `src/rider_server/main.py:461`, README 배포 섹션.

## 주요 리스크

### P0. 크롤링 계정 비밀값이 DB와 job payload에 평문 의미로 흐릅니다

`PlatformAccount.username`, `password`, `verification_email_app_password`가 DB 컬럼으로 있고, scheduler가 이 값을 job payload에 넣습니다. Agent의 plaintext 차단 목록은 현재 비어 있습니다.

근거:

- `src/rider_server/db/models/account.py:31`: `username`, `password`, `verification_email_app_password`
- `src/rider_server/scheduler/postgres_repository.py:110`: platform account secret성 필드 조회
- `src/rider_server/scheduler/service.py:317`: payload에 `username`, `password`, `verification_email_app_password` 삽입
- `src/rider_server/api/jobs.py:280`: claim 응답이 raw payload를 Agent에 전달
- `src/rider_agent/workers/crawl_worker.py:57`: `_PLAINTEXT_SECRET_KEYS = frozenset[str]()`
- `tests/agent/test_crawl_worker.py:205`: 현재 평문 secret field 수락을 잠그는 테스트가 있음

영향:

- 수백 개 운영에서는 DB 백업, job payload, 장애 분석, audit, 로그 표면이 모두 커집니다.
- ref 기반인지 평문 기반인지 코드와 테스트가 섞여 있어 유지보수자가 헷갈리기 쉽습니다.
- 한 경로만 새도 여러 플랫폼 계정이 같이 노출됩니다.

판단: 수백 개 운영 전 반드시 처리해야 하는 P0입니다.

### P1. Scheduler tick이 due target 전체 조회와 target별 쿼리에 의존합니다

현재 tick은 `due_targets(now=now)`로 due target을 제한 없이 읽고, 각 target마다 tenant gate와 active crawl job 여부를 다시 조회합니다.

근거:

- `src/rider_server/scheduler/postgres_repository.py:101`: `due_targets()`에 limit과 안정 정렬 없음
- `src/rider_server/scheduler/service.py:181`: due 전체 조회
- `src/rider_server/scheduler/service.py:203`: target loop
- `src/rider_server/scheduler/service.py:205`: target별 `tenant_gate()`
- `src/rider_server/scheduler/service.py:238`: target별 `has_active_crawl_job()`

영향:

- 100개 smoke는 통과하지만 500개, 1,000개로 커지면 tick마다 DB 왕복 수가 늘어납니다.
- scheduler interval이 짧으면 DB 부하와 tick 시간이 같이 커집니다.

판단: batch limit, stable order, tenant gate bulk 조회, active crawl target bulk 조회가 필요합니다.

### P1. Dashboard/read-model도 수백 개에서 N+1 병목이 됩니다

운영 화면은 scheduler와 다른 경로지만, 수백 개 운영 중 사람이 계속 보는 화면이라 DB 부하를 만들 수 있습니다.

근거:

- `src/rider_server/admin/dashboard_repository_postgres.py:82`: `target_health()`가 대상 목록을 읽음
- `src/rider_server/admin/dashboard_repository_postgres.py:117`: 대상별 loop
- `src/rider_server/admin/dashboard_repository_postgres.py:119`: 대상별 last collect success 조회
- `src/rider_server/admin/dashboard_repository_postgres.py:120`: 대상별 last delivery success 조회
- `src/rider_server/admin/dashboard_repository_postgres.py:121`: 대상별 latest failure 조회
- `src/rider_server/admin/dashboard_repository_postgres.py:122`: 대상별 auth pending 조회
- `src/rider_server/admin/dashboard_repository_postgres.py:211`: Agent 목록 후 agent별 current job 조회
- `src/rider_server/admin/templates/dashboard.html:376`: targets fragment 30초 polling
- `src/rider_server/admin/templates/dashboard.html:385`: agents fragment 30초 polling

영향:

- 수백 target에서 운영자가 dashboard를 열어두면 read query가 계속 반복됩니다.
- scheduler 성능을 고쳐도 dashboard가 DB를 흔들 수 있습니다.

판단: read-model bulk aggregation, pagination/limit, fragment별 갱신 주기 조절이 필요합니다.

### P1. stale recovery가 claim API 요청 경로에 붙어 있습니다

`POST /v1/jobs/claim`은 claim 전 `_recover_stale_if_due()`를 호출합니다. lock과 last timestamp는 app process 메모리입니다.

근거:

- `src/rider_server/api/jobs.py:49`: stale recovery interval 30초
- `src/rider_server/api/jobs.py:236`: `_recover_stale_if_due()`
- `src/rider_server/api/jobs.py:270`: claim 요청 안에서 stale 회수 호출
- `tests/server/test_jobs_api.py:216`: 기존 테스트가 inline recovery를 전제로 함

영향:

- 단일 API 프로세스에서는 단순합니다.
- API 프로세스가 여러 개면 process별로 stale 회수가 중복될 수 있습니다.
- bulk recovery가 claim latency에 붙습니다.

판단: 별도 recovery worker 또는 DB advisory lock 기반 회수로 분리해야 합니다.

### P1. heartbeat lease 연장이 active job마다 개별 DB update입니다

heartbeat route는 `body.active_jobs`를 순회하며 job마다 `extend_lease()`를 호출합니다.

근거:

- `src/rider_server/api/agents.py:125`: active job loop
- `src/rider_server/api/agents.py:131`: 단건 `extend_lease()`
- `src/rider_server/queue/backend.py:136`: backend contract도 단건 연장만 정의
- `src/rider_server/queue/postgres_queue.py:349`: PostgreSQL 단건 lease 연장

영향:

- 현재 Agent 기본값이 `max_jobs=1`이라 지금은 큰 문제는 아닙니다.
- 나중에 Agent concurrency를 올리면 heartbeat update 수가 active job 수만큼 늘어납니다.

판단: worker concurrency를 올리기 전에 bulk lease extension이 필요합니다.

### P1. Agent는 기본적으로 한 번에 한 job만 순차 처리합니다

`JobRunner`와 `run_agent()`의 기본 `max_jobs`는 1이고, claim된 job을 같은 loop/thread에서 순차 처리합니다. CLI도 현재 이를 직접 올리는 옵션이 보이지 않습니다.

근거:

- `src/rider_agent/job_loop.py:384`: `max_jobs: int = 1`
- `src/rider_agent/job_loop.py:482`: claim된 job을 순차 처리
- `src/rider_agent/job_loop.py:493`: `_execute_job(job)` 직접 호출
- `src/rider_agent/job_loop.py:710`: `run_agent()` 기본 `max_jobs: int = 1`
- `src/rider_agent/__main__.py:102`: run CLI에 concurrency 옵션 없음

영향:

- 단일 Agent PC에서 수백 개 동시 크롤링은 불가능합니다.
- 보수적이라 안전하지만 처리량은 Agent PC 수와 각 PC의 안정 처리량에 의존합니다.
- 크롤러 hang에 대한 hard kill boundary가 약합니다. lease는 서버 재할당을 돕지만, 같은 Agent slot은 묶일 수 있습니다.

판단: 수백 개 운영은 "많은 Agent + capacity 제어"가 기본입니다. 단일 Agent concurrency는 profile lifecycle과 process isolation 뒤에 올려야 합니다.

### P1. 브라우저 프로필 수명 관리가 아직 운영 정책으로 닫히지 않았습니다

`BrowserProfileManager.ensure_profile()`은 대상별 assignment를 registry에 저장합니다. `release()`는 있지만 크롤 완료 경로에서 호출되지 않고, 기존 assignment 재사용 시 `last_used_at` 같은 갱신도 없습니다.

근거:

- `src/rider_agent/browser_profile.py:245`: 기존 assignment 즉시 재사용
- `src/rider_agent/browser_profile.py:298`: registry 저장
- `src/rider_agent/browser_profile.py:304`: `release()`
- `src/rider_agent/workers/crawl_worker.py:210`: worker가 `ensure_profile()` 사용

영향:

- 로그인 세션 재사용에는 좋습니다.
- 한 Agent가 많은 target을 순회하면 프로필, CDP port, Chrome 프로세스가 장기간 남을 수 있습니다.
- "프로필을 오래 유지한다"면 이것은 성능 최적화가 아니라 capacity 비용으로 문서화되어야 합니다.

판단: idle cleanup, 최대 보유 수, release 기준, 재로그인 필요 상태 처리 기준이 필요합니다.

### P2. Retry 정책은 있지만 queue complete 재진입에 연결되어 있지 않습니다

`scheduler.policy.retry_run_after()`는 있지만, `PostgresQueueBackend.complete()`는 실패 job을 받은 status로 종결하고 attempts 증가나 `run_after` 재설정으로 `PENDING`에 되돌리지 않습니다.

근거:

- `src/rider_server/scheduler/policy.py:219`: `retry_run_after()`
- `src/rider_server/queue/postgres_queue.py:257`: complete에서 status를 그대로 설정
- `src/rider_server/db/models/agent.py:83`: `attempts` 컬럼은 있으나 complete 경로에서 증가하지 않음

영향:

- 일시 장애가 수백 개 작업에 퍼질 때 운영자가 수동 재처리를 해야 할 수 있습니다.
- 실패가 `result_json` blob 안에 남아 운영 지표로 집계하기 어렵습니다.

판단: retry decider를 queue complete 경로에 주입하고, deterministic failure와 transient failure를 분리해야 합니다.

### P2. Job result/status 저장이 운영 분석에 부족합니다

`jobs`에는 `payload_json`, `result_json`, `claimed_at`은 있지만 `completed_at`, `duration_ms`, `result_schema_version`, 상태 이력 테이블은 없습니다.

근거:

- `src/rider_server/db/models/agent.py:81`: status
- `src/rider_server/db/models/agent.py:85`: payload_json
- `src/rider_server/db/models/agent.py:88`: claimed_at
- `src/rider_server/db/models/agent.py:89`: result_json
- `src/rider_server/scheduler/postgres_repository.py:178`: failure window가 claimed_at으로 활동 시각을 근사
- `src/rider_server/admin/dashboard_repository_postgres.py:171`: latest failure도 claimed_at/run_after 근사 사용

영향:

- p95 duration, 최근 완료 시각, retry count, result schema drift를 정확히 보기 어렵습니다.
- 수백 개 운영에서 "느린지", "죽었는지", "재시도 중인지"를 분리하기 어렵습니다.

판단: 최소한 `completed_at`, `duration_ms`, `result_schema_version` 또는 job event/history 정리가 필요합니다.

### P2. DB pool과 배포 scale knob이 부족합니다

DB 엔진은 `create_async_engine(database_url, echo=echo, future=True, **kwargs)`를 사용하고, pool 설정은 `Settings` env로 노출되어 있지 않습니다. Dockerfile은 단일 uvicorn command입니다.

근거:

- `src/rider_server/settings.py:58`: `from_env()`
- `src/rider_server/db/base.py:55`: `create_engine()`
- `deploy/Dockerfile.server:37`
- `deploy/docker-compose.yml:40`: backend-api
- `deploy/docker-compose.yml:67`: scheduler
- README 단일 EC2/로컬 PostgreSQL 배포 설명

영향:

- 수백 Agent heartbeat/claim이 동시에 오면 DB connection, API worker 수, Postgres max_connections가 병목이 됩니다.
- 단일 EC2/로컬 DB 구성은 장애 도메인이 하나입니다.

판단: DB pool env, API worker 수, scheduler/recovery 별도 프로세스, Postgres 리소스 기준을 runbook에 남겨야 합니다.

## 수백 개 운영 기준 목표

1. 서버는 수백 개 pending/running job을 PostgreSQL에서 안정적으로 관리한다.
2. 같은 job은 동시에 하나의 Agent만 claim한다.
3. Agent 장애, 네트워크 단절, 프로세스 종료 후 stale job은 자동 회수된다.
4. scheduler tick은 due target 수가 커져도 한 tick 처리량과 DB 왕복 수를 제한한다.
5. 운영 dashboard도 수백 target에서 bulk read와 pagination으로 동작한다.
6. 계정 비밀값은 job payload, 로그, audit, metrics, DB 백업에 평문으로 남지 않는다.
7. Agent PC는 자신이 감당 가능한 `max_in_flight`만 보고하고 서버는 이를 넘겨 enqueue하지 않는다.
8. 브라우저 프로필은 재사용하되 idle cleanup, 최대 보유 수, 강제 release 기준이 있다.
9. transient failure는 backoff로 자동 재시도되고, auth/target mismatch 같은 사람 개입 실패는 보류된다.
10. 운영 지표가 queue depth, claim latency, heartbeat latency, stale recovery count, retry count, agent online capacity, browser profile count를 보여준다.
11. 100, 300, 500 target 시뮬레이션과 PostgreSQL 동시성 테스트가 릴리스 gate에 포함된다.

## 권장 우선순위

1. P0: PlatformAccount secret을 ref 의미로 정리하고 job payload 평문 제거
2. P1: Scheduler batching/pagination 및 tenant/active job bulk 조회
3. P1: Dashboard/read-model bulk 조회 및 fragment pagination
4. P1: stale recovery를 claim 요청 경로에서 분리
5. P1: heartbeat bulk lease extension
6. P1: Agent browser profile lifecycle과 hard timeout/process isolation 정책
7. P2: retry 재진입과 job status/history 저장 보강
8. P2: DB pool/env, API worker, Postgres 운영 설정 및 load test

## 최종 판단

현재 코드는 "확장 가능한 방향으로 잘 정리된 MVP"입니다. 큐와 lease의 핵심 동시성 설계는 좋고, scheduler에도 100개 대상 smoke가 있습니다. 하지만 수백 개 장기 운영을 바로 보장하려면 보안, DB read/write 부하, Agent 자원 회수, retry, dashboard, 배포 스케일 검증을 먼저 닫아야 합니다.

## 작업 완료

작업지시서 기준 구현과 검증을 끝냈습니다.
