# done_Crawl Fleet Scale Review and Work Order

작성일: 2026-06-20  
상태: 적용 완료  
적용일: 2026-06-20  
적용 내용: 이 작업지시서의 Agent affinity, server-side capacity clamp, complete outbox/idempotency, heartbeat lease extension surfacing, recovery batch size, DB index/connection budget, stale SENDING hold, runbook 보강을 코드/테스트/문서에 적용했다.  
범위: 여러 Agent, 단일 Agent 수십 개 이상 crawl, 서버 통신, PostgreSQL/배포, 기능 공백

## 1. 현재 판정

현재 구조는 "여러 PC의 여러 Agent가 서버의 PostgreSQL queue에서 작업을 claim해서 처리하는 모델"을 갖추고 있다. `jobs` claim은 PostgreSQL `FOR UPDATE SKIP LOCKED`를 쓰고, complete는 owner/lease를 검사하며, scheduler는 due target을 batch로 enqueue하고 conditional update로 중복 tick을 줄인다.

다만 운영 가능 판정은 제한적이다.

- 여러 Agent 운영: 구조상 가능하지만, 현재 claim이 capability만 보고 job을 배정한다. 같은 capability를 가진 Agent가 여러 대면 target/profile을 실제로 가진 Agent가 아닌 다른 Agent도 job을 가져갈 수 있다.
- 단일 Agent 수십 개 이상 target: 기본 모델은 수십 개 동시 crawl이 아니라 backlog를 순차 또는 소수 병렬로 처리하는 모델이다. CLI 기본값은 `--max-jobs=1`이고 runbook도 50~100개 target을 동시 crawl이 아닌 backlog로 설명한다.
- 서버 통신: claim/complete/heartbeat API 계약은 있다. 그러나 complete 실패 시 Agent 로컬에 성공 결과를 durable하게 보관하지 않아 "crawl 성공, complete 실패" 상황에서 재전송보다 재크롤에 의존한다.
- DB/배포: queue용 핵심 index와 lock은 있으나 DB connection pool 총량, snapshot/message 조회 index, 실제 PostgreSQL 다중 Agent 부하 검증이 부족하다.

## 2. 좋은 근거

- `src/rider_server/queue/postgres_queue.py:211`의 claim query는 `status=PENDING`, `run_after`, `type in capabilities` 조건과 `FOR UPDATE SKIP LOCKED`를 사용한다.
- `src/rider_server/queue/postgres_queue.py:263`의 complete는 agent owner, in-flight status, lease 만료를 검사해 이중 success를 막는다.
- `src/rider_server/queue/postgres_queue.py:432`의 stale recovery는 만료 lease를 회수하고 retry/failed 상태로 전이한다.
- `src/rider_server/scheduler/postgres_repository.py:347`은 target `next_run_at` 전진과 job insert를 같은 transaction으로 묶는다.
- `src/rider_agent/browser_profile.py:226` 이후는 target별 profile directory와 CDP port를 분리하고, 중복 port/profile을 막는다.
- `src/rider_agent/workers/crawl_process.py:16`은 기본 crawl을 child process에서 실행하고 timeout 시 terminate/kill한다.
- `deploy/docker-compose.yml`은 `backend-api`, `scheduler`, `queue-recovery`, `telegram-dispatch`를 별도 프로세스로 띄운다.

## 3. 주요 문제

### Critical 1: 여러 Agent 환경에서 잘못된 Agent가 job을 claim할 수 있음

증거:

- `src/rider_server/api/jobs.py:363`은 claim 시 `agent_id`, `capabilities`, `max_jobs`만 backend에 넘긴다.
- `src/rider_server/queue/postgres_queue.py:211`의 claim filter도 capability만 본다.
- `src/rider_server/scheduler/service.py:353`의 payload는 `browser_profile_ref = profile:{target_id}`만 넣고, 특정 Agent/profile affinity를 강제하지 않는다.

영향:

- 같은 `CRAWL_BAEMIN` 또는 `CRAWL_COUPANG` capability를 가진 Agent가 여러 대면, 로컬 로그인/profile이 없는 Agent가 target job을 가져갈 수 있다.
- 다중 Agent 운영에서 login state, browser profile, target affinity가 깨질 수 있다.

### High 1: Agent별 claim 상한과 공정성이 서버에서 강제되지 않음

증거:

- `src/rider_server/api/jobs.py:44`는 `MAX_CLAIM_JOBS=50`이다.
- `src/rider_server/queue/postgres_queue.py:218`은 오래된 pending job부터 `max_jobs`만큼 claim한다.
- scheduler는 enqueue 전 capacity를 보지만, claim 시점에는 해당 Agent의 현재 in-flight와 `max_in_flight`를 다시 확인하지 않는다.

영향:

- 빠르게 polling하는 Agent 하나가 많은 job을 먼저 잡을 수 있다.
- 운영자가 한 Agent를 `--max-jobs` 크게 실행하면 서버가 Agent capacity보다 큰 claim을 막지 못한다.

### High 2: complete 실패 시 Agent 로컬 결과 보존이 없음

증거:

- `src/rider_agent/job_loop.py:569` 이후 complete는 3회 재시도 후 실패를 기록하고 반환한다.
- `src/rider_agent/job_loop.py:561`의 finally는 complete 성공 여부와 무관하게 in-flight에서 제거한다.

영향:

- crawl은 성공했지만 서버/DB 장애로 complete가 실패하면 결과를 재전송할 수 없다.
- lease 만료 후 job이 재할당되어 같은 target을 다시 crawl해야 한다.

### High 3: Telegram outbox가 `SENDING` 상태에서 멈출 수 있음

증거:

- `src/rider_server/services/dispatch_worker.py:116`은 `RETRYING` 상태만 claim한다.
- `src/rider_server/services/dispatch_worker.py:160`은 send 직전에 status를 `SENDING`으로 바꾼다.
- worker가 send 중 죽으면 `SENDING` row를 다시 claim하거나 HELD로 회수하는 경로가 보이지 않는다.

영향:

- 외부 전송 성공 여부가 모호한 row가 영구 정체될 수 있다.
- 자동 재전송으로 바꾸면 중복 전송 위험이 있으므로 "모호 상태는 HELD/수동 확인" 정책이 필요하다.

### High 4: DB connection pool 총량 관리가 배포에서 보장되지 않음

증거:

- `src/rider_server/settings.py:20`의 기본 pool은 `pool_size=5`, `max_overflow=10`이다.
- `deploy/docker-compose.yml`은 API, scheduler, queue recovery, telegram dispatch 각각에 같은 pool 값을 준다.
- API는 `RIDER_UVICORN_WORKERS`로 worker 수를 늘릴 수 있다.

영향:

- worker 수와 별도 프로세스 수가 늘면 PostgreSQL connection 수가 급증한다.
- `max_connections` 또는 PgBouncer 기준 검증이 없다.

### High 5: snapshot/message 조회 경로에 scale index가 부족함

증거:

- `src/rider_server/db/models/messaging.py:91`의 `snapshots` 모델에는 `target_id`, `collected_at` index가 없다.
- `src/rider_server/services/snapshot_repository_postgres.py:332`는 `Snapshot.target_id`, `DeliveryLog.channel_id`, `Message.template_version`로 필터하고 `Snapshot.collected_at desc`로 정렬한다.

영향:

- target 수와 snapshot 수가 늘면 change-only delivery 조회가 느려질 수 있다.
- 수십 개 target을 장기 운영할수록 DB read 병목이 될 수 있다.

### Medium 1: heartbeat lease 연장 실패가 Agent에 전달되지 않음

증거:

- `src/rider_server/api/agents.py:171`은 heartbeat active jobs를 bulk lease extension에 사용한다.
- `src/rider_server/api/agents.py:190`은 extension 실패를 warning만 남기고 정상 heartbeat 응답을 반환한다.

영향:

- Agent는 lease 연장 실패를 모르고 계속 처리한다.
- recovery가 같은 job을 다른 Agent에 재할당할 수 있고, 원 Agent complete는 나중에 409가 된다.

### Medium 2: recovery batch 설정이 실제 loop에 연결되지 않음

증거:

- `src/rider_server/settings.py`에는 `job_recovery_batch_size`가 있다.
- `src/rider_server/queue/postgres_queue.py:432`의 `recover_stale`은 batch_size를 받을 수 있다.
- `src/rider_server/queue/recovery.py`와 `src/rider_server/queue/__main__.py` 경로는 설정값을 넘기지 않는다.

영향:

- stale job이 많아지면 recovery tick 하나가 너무 커질 수 있다.

### Medium 3: 실제 PostgreSQL 경합/부하 테스트가 선택 실행임

증거:

- `tests/negative/test_queue_concurrency.py`는 `TEST_DATABASE_URL`이 없으면 skip된다.
- `tests/negative/test_scheduler_idempotency.py`도 `TEST_DATABASE_URL`이 없으면 skip된다.
- `tests/server/test_scale_readiness.py`는 설정/문서 guard 중심이며 다수 Agent claim 부하를 직접 검증하지 않는다.

영향:

- CI green이 실제 PostgreSQL 다중 Agent 안정성을 증명하지 않는다.
- 10~20 Agent, 100~500 pending jobs 조건의 lock/contention 증거가 없다.

### Medium 4: 단일 Agent 수십 개 동시 crawl은 현재 보장 대상이 아님

증거:

- `src/rider_agent/__main__.py:113`의 기본 `--max-jobs`는 1이다.
- `src/rider_agent/__main__.py:127`의 기본 `--max-profiles`는 20이다.
- `src/rider_agent/job_loop.py:514`는 `ThreadPoolExecutor`로 병렬 처리할 수 있지만, 실제 Chrome 다중 실행 부하 테스트는 없다.
- `docs/runbooks/crawl-scale-runbook.md`는 50~100 target을 동시 crawl이 아니라 backlog 모델로 설명한다.

영향:

- 운영자가 "수십 개 동시"로 이해하면 PC CPU/RAM/Chrome process 한계를 넘길 수 있다.
- 현재 안전 기준은 `max_jobs=1`, 필요 시 2~3부터 검증하는 방식이다.

### Medium 5: token rotate 의미가 실제 claim 차단과 어긋날 수 있음

증거:

- `src/rider_server/main.py:571`은 job API resolver를 `agent_registry.resolve_agent_id`로 연결한다.
- `src/rider_server/services/agent_registry_postgres.py:105` 이후 resolver/heartbeat는 `token_revoked_at`을 본다.
- rotate repository는 `token_rotated_at`을 기록하지만 resolver가 이를 무효화 조건으로 보지 않는다.

영향:

- "rotate가 기존 token을 무효화한다"는 운영 기대와 실제 동작이 다를 수 있다.

### Low 1: runbook 일부가 현재 구현과 불일치

증거:

- `docs/runbooks/crawl-scale-runbook.md`는 profile-managed crawl이 in-process timeout helper를 쓴다고 설명한다.
- 현재 기본 crawl worker는 조건이 맞으면 subprocess boundary를 탄다.

영향:

- 운영자가 timeout 격리 수준과 `--max-jobs` 기준을 잘못 이해할 수 있다.

## 4. 작업지시서

### Task 1. Agent-target affinity와 claim 필터를 추가한다

목표: job을 처리할 수 있는 Agent가 단순 capability가 아니라 target/profile 소유 조건까지 만족해야 claim할 수 있게 한다.

작업:

- `jobs` 또는 별도 assignment 모델에 `assigned_agent_id` 또는 `agent_affinity_key`를 추가한다.
- scheduler가 target의 현재 profile 소유 Agent 또는 운영자가 지정한 Agent를 job payload/column에 기록한다.
- queue claim이 `assigned_agent_id is null OR assigned_agent_id = requester` 조건을 적용하게 한다.
- Admin의 `assign_agent` 액션과 scheduler가 같은 assignment source를 보게 한다.
- affinity가 없는 신규 target은 명시 정책을 둔다. 예: "모든 capable Agent 허용" 또는 "첫 claim Agent가 profile owner로 고정".

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_jobs_api.py::test_claim_does_not_return_job_assigned_to_other_agent -q
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py::test_many_agents_claim_only_affine_jobs_real_pg -q
```

완료 기준:

- Agent A에 할당된 target job은 Agent B가 같은 capability를 가져도 claim하지 못한다.
- 10개 Agent, 100개 job 조건에서 잘못된 Agent가 claim한 job이 0건이다.
- 잘못 배정된 job은 silent skip이 아니라 운영 metric 또는 audit event로 드러난다.

### Task 2. Agent별 capacity와 claim 공정성을 서버에서 강제한다

목표: 빠른 Agent 하나가 queue를 독점하지 않게 한다.

작업:

- claim 전에 해당 Agent의 현재 `CLAIMED/RUNNING` 수를 계산한다.
- heartbeat의 `capacity_json.max_in_flight`를 서버 claim 상한으로 사용한다.
- request body `max_jobs`는 `server_remaining_capacity`보다 클 수 없게 clamp한다.
- type별 capacity가 있으면 `CRAWL_BAEMIN`, `CRAWL_COUPANG`, `KAKAO_SEND`를 따로 계산한다.
- claim 결과에 `server_limited=true` 같은 진단을 넣을지 결정한다. API 호환성이 부담이면 metric/audit만 남긴다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_jobs_api.py::test_claim_clamps_to_agent_remaining_capacity -q
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py::test_fast_agent_cannot_exceed_server_capacity_real_pg -q
```

완료 기준:

- Agent `max_in_flight=2`가 이미 2개 job을 소유하면 추가 claim은 빈 배열이다.
- `max_jobs=50` 요청도 서버 capacity가 1이면 1개만 반환한다.
- scheduler enqueue capacity와 claim capacity의 기준이 문서상 일치한다.

### Task 3. Agent complete 로컬 outbox를 추가한다

목표: crawl 성공 후 서버 complete 실패가 있어도 결과를 잃지 않는다.

작업:

- Agent state root 아래 durable outbox 파일 또는 SQLite를 둔다.
- crawl 성공/실패 결과를 complete 전 outbox에 기록한다.
- complete 성공 시 outbox record를 삭제한다.
- complete 실패/네트워크 실패 시 다음 loop에서 먼저 outbox replay를 시도한다.
- lease lost(409/410)인 경우 outbox record를 "discarded: lease_lost"로 남기거나 삭제하되, 운영 로그에 남긴다.
- outbox에는 token, raw HTML, screenshot path 등 민감값을 저장하지 않는다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_job_loop.py::test_complete_failure_keeps_result_in_local_outbox -q
.venv\Scripts\python.exe -m pytest tests/agent/test_job_loop.py::test_outbox_replays_before_claiming_new_jobs -q
```

완료 기준:

- 서버 down 중 complete 실패가 나도 outbox 파일에 결과가 남는다.
- 서버 복구 후 같은 Agent가 결과를 재전송한다.
- outbox replay 중 409이면 재전송을 멈추고 lease lost로 기록한다.

### Task 4. complete API idempotency key를 추가한다

목표: Agent가 같은 complete를 재시도할 때 이미 성공 처리된 결과를 오류처럼 보지 않게 한다.

작업:

- complete request에 `completion_id` 또는 `result_id`를 추가한다.
- `jobs`에 terminal complete id/hash를 저장한다.
- 같은 Agent, 같은 job, 같은 completion id가 다시 오면 이전 성공 응답과 같은 200을 반환한다.
- 다른 payload/hash면 conflict로 처리한다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_jobs_api.py::test_complete_retry_with_same_completion_id_is_idempotent -q
.venv\Scripts\python.exe -m pytest tests/server/test_queue_backend.py::test_duplicate_complete_same_id_returns_accepted -q
```

완료 기준:

- complete 후 client timeout이 발생해 같은 요청이 재전송되어도 200이다.
- 다른 Agent 또는 다른 payload는 409로 유지된다.

### Task 5. heartbeat lease extension 실패를 Agent가 알 수 있게 한다

목표: lease 연장 실패가 조용히 묻히지 않게 한다.

작업:

- `/v1/agents/heartbeat` 응답에 `lease_extension` 결과를 포함한다.
- 예: `{"extended_job_ids": [...], "failed_job_ids": [...]}`.
- Agent는 active job 중 extension 실패가 있으면 local warning을 남기고, 가능하면 새 claim을 잠시 멈춘다.
- DB 장애로 extension 자체가 실패하면 heartbeat 200을 유지할지 503으로 바꿀지 결정한다. 권장: heartbeat 자체는 200, lease_extension status는 degraded.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_agents_api.py::test_heartbeat_reports_failed_lease_extensions -q
.venv\Scripts\python.exe -m pytest tests/agent/test_heartbeat.py::test_agent_surfaces_lease_extension_degraded -q
```

완료 기준:

- unowned active job은 extended list에 포함되지 않는다.
- backend exception은 응답에 degraded로 보이고 warning log에도 남는다.

### Task 6. Telegram `SENDING` 복구 정책을 만든다

목표: 외부 전송 성공 여부가 모호한 delivery가 자동 중복 전송되거나 영구 정체되지 않게 한다.

작업:

- `SENDING` 상태가 lock timeout을 넘기면 자동 `RETRYING`이 아니라 `HELD` 또는 `UNKNOWN`으로 이동한다.
- operator가 Admin에서 확인 후 retry/discard할 수 있게 한다.
- `claim_pending()`은 safe-to-send 상태만 claim한다.
- `mark_send_started()` 전후 상태 전이를 테스트한다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_snapshot_telegram_runtime.py::test_stale_sending_delivery_is_held_not_auto_retried -q
```

완료 기준:

- worker가 `SENDING` 상태에서 죽은 row는 lock timeout 뒤에도 자동 send 대상이 아니다.
- Admin 또는 recovery job이 HELD로 전이한 기록을 볼 수 있다.

### Task 7. queue recovery batch 설정을 실제 loop에 연결한다

목표: stale job이 많을 때 recovery tick 하나가 DB를 오래 잡지 않게 한다.

작업:

- `recover_once()`가 `batch_size`를 받게 한다.
- `python -m rider_server.queue`가 `Settings.job_recovery_batch_size`를 읽어 넘긴다.
- compose에 `RIDER_JOB_RECOVERY_BATCH_SIZE`를 노출한다.
- recovery log에 recovered count와 batch size를 함께 남긴다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_queue_recovery.py::test_queue_recovery_loop_uses_configured_batch_size -q
.venv\Scripts\python.exe -m pytest tests/server/test_scale_readiness.py::test_compose_exposes_recovery_batch_size -q
```

완료 기준:

- stale job 250개, batch 100이면 한 tick은 100개만 회수한다.
- 다음 tick에서 다음 batch를 처리한다.

### Task 8. scale index를 추가한다

목표: target 수와 snapshot 수가 늘어도 주요 조회가 느려지지 않게 한다.

작업:

- `snapshots(target_id, collected_at DESC, id)` index를 추가한다.
- `messages(snapshot_id, template_version)` 또는 조회 계획에 맞는 index를 추가한다.
- `delivery_logs(channel_id, message_id)` 또는 `_latest_message_hashes_by_channel` query에 맞는 index를 추가한다.
- active crawl job 중복 방지를 위한 partial unique/index를 검토한다. 예: active status에서 `(target_id, type)` unique. retry/수동 job 정책과 충돌하면 unique 대신 partial non-unique + transactional check를 쓴다.
- 대형 테이블 운영을 고려해 index migration은 `CONCURRENTLY` 또는 maintenance window runbook을 둔다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_db_schema.py::test_snapshot_latest_message_query_indexes_exist -q
.venv\Scripts\python.exe -m pytest tests/server/test_db_schema.py::test_active_crawl_job_guard_index_exists -q
```

완료 기준:

- schema test가 새 index를 확인한다.
- PostgreSQL `EXPLAIN` runbook에 `_latest_message_hashes_by_channel` query plan 확인 절차가 있다.

### Task 9. DB connection budget guard를 추가한다

목표: 배포 설정만으로 PostgreSQL connection 수가 `max_connections`를 넘지 않게 한다.

작업:

- runbook 산식만 두지 말고 검증 스크립트를 추가한다.
- 입력: `RIDER_UVICORN_WORKERS`, scheduler/recovery/dispatch process 수, `RIDER_DB_POOL_SIZE`, `RIDER_DB_MAX_OVERFLOW`, Postgres `max_connections`.
- 계산 결과가 한도를 넘으면 startup 또는 CI config check가 실패하게 한다.
- 운영 규모가 커지면 PgBouncer 사용 지시를 추가한다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scale_readiness.py::test_db_connection_budget_rejects_oversubscription -q
```

완료 기준:

- 기본 compose 기준 최소 connection budget이 문서와 테스트에서 일치한다.
- `RIDER_UVICORN_WORKERS=8`, pool 15 같은 위험 설정은 검증에서 실패한다.

### Task 10. 실제 PostgreSQL 다중 Agent scale test를 CI 또는 nightly에 추가한다

목표: in-memory 테스트가 아닌 실제 PostgreSQL lock/contention을 검증한다.

작업:

- GitHub Actions 또는 별도 nightly job에 PostgreSQL service를 띄우고 `TEST_DATABASE_URL`을 설정한다.
- 최소 시나리오:
  - 20 Agent가 200 pending jobs를 동시에 claim.
  - 같은 job이 중복 claim되지 않음.
  - affinity가 있는 job은 다른 Agent가 claim하지 않음.
  - stale recovery와 concurrent claim이 동시에 돌아도 terminal 이중 success가 없음.
  - scheduler 2개 tick이 동시에 돌아도 target당 active crawl job이 1개 이하.
- test runtime이 길면 `tests/scale/`로 분리하고 nightly로 둔다.

검증 명령:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py tests/negative/test_scheduler_idempotency.py tests/server/test_scale_readiness.py -q
```

완료 기준:

- PR CI 또는 nightly 중 하나에서 실제 PostgreSQL 경합 테스트가 자동 실행된다.
- skip되는 경우 최종 release checklist에서 배포 불가로 처리한다.

### Task 11. 단일 Agent 수십 target 운영 기준을 명확히 하고 smoke를 추가한다

목표: "수십 개 target 처리"와 "수십 개 동시 crawl"을 구분하고, 실제 PC 기준을 만든다.

작업:

- `docs/runbooks/crawl-scale-runbook.md`를 현재 구현에 맞춘다.
- 기본 권장값을 명확히 한다.
  - 일반 운영: `--max-jobs=1`
  - 검증 후: `--max-jobs=2~3`
  - 수십 개 target: backlog 처리
  - 수십 개 동시 Chrome: 지원하지 않음
- 실제 Windows Agent smoke를 만든다.
  - 50 target backlog enqueue
  - max_jobs=1에서 누락 없이 순차 처리
  - max_jobs=2 또는 3에서 profile leak/process leak이 없는지 확인
- Chrome이 필요한 smoke는 manual 또는 lab test로 분리한다.

완료 기준:

- 운영 문서가 "수십 개 동시 crawl 가능"으로 오해되지 않는다.
- smoke 결과에 CPU/RAM/process count 기준이 남는다.

### Task 12. Agent polling/heartbeat jitter와 backoff를 추가한다

목표: 서버 복구 시 여러 Agent가 동시에 몰리지 않게 한다.

작업:

- claim 실패/5xx/network error에 exponential backoff와 jitter를 넣는다.
- empty queue polling은 기본 간격을 유지하되 Agent별 stable jitter를 더한다.
- heartbeat interval도 Agent별 stable jitter를 둔다.
- 401/revoke는 backoff가 아니라 재등록 필요 상태로 유지한다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_job_loop.py::test_claim_failures_backoff_with_jitter -q
.venv\Scripts\python.exe -m pytest tests/agent/test_heartbeat.py::test_heartbeat_interval_has_stable_jitter -q
```

완료 기준:

- 20 Agent가 동시에 시작해도 claim request가 같은 초에 집중되지 않는다.
- 장애 중 busy loop가 없다.

### Task 13. token rotate/revoke 의미를 정리한다

목표: 운영자가 rotate를 누르면 기존 token이 계속 쓰이는 혼선을 없앤다.

작업:

- rotate의 의미를 둘 중 하나로 정한다.
  - A안: rotate는 기존 token을 즉시 revoke하고 새 registration flow를 요구한다.
  - B안: rotate는 새 token 발급까지는 준비 상태이고 기존 token은 유지된다.
- 권장: A안. `token_rotated_at` 또는 별도 `token_generation`을 resolver가 보게 한다.
- Admin action 문구와 runbook을 실제 동작에 맞춘다.

실패 테스트부터 작성:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_agent_token_revoke.py::test_rotated_agent_token_cannot_claim_jobs -q
```

완료 기준:

- rotate 후 기존 token의 claim/heartbeat/complete 동작이 문서와 테스트에서 일치한다.

## 5. 전체 검증 명령

빠른 항상 실행:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/agent/test_job_loop.py tests/agent/test_crawl_worker.py tests/agent/test_browser_profile.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_agents_api.py tests/server/test_jobs_api.py tests/server/test_queue_backend.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_scheduler_tick.py tests/server/test_scale_readiness.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_snapshot_telegram_runtime.py tests/server/test_db_schema.py
```

실제 PostgreSQL 필요:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest -q tests/negative/test_queue_concurrency.py tests/negative/test_scheduler_idempotency.py tests/negative/test_dashboard_repository_pg.py tests/negative/test_metrics_repository_pg.py
```

배포 config:

```powershell
$env:RIDER_POSTGRES_PASSWORD="rider"
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED="1"
$env:RIDER_TELEGRAM_WEBHOOK_SECRET="ci_dummy_webhook_secret"
$env:RIDER_TELEGRAM_BOT_TOKEN="ci_dummy_bot_token"
docker compose -f deploy/docker-compose.yml config
```

## 6. 완료 기준

- 같은 capability를 가진 여러 Agent가 있어도 target/profile affinity가 맞지 않는 job은 claim되지 않는다.
- Agent별 `max_in_flight`를 서버가 강제한다.
- crawl complete 결과는 서버 장애 중에도 Agent local outbox에 남고 복구 후 재전송된다.
- Telegram `SENDING` stale row는 자동 재전송되지 않고 HELD/UNKNOWN으로 운영자 확인 대상이 된다.
- stale recovery는 batch size를 지킨다.
- snapshot/message 최신 hash 조회용 index가 있다.
- DB connection budget 초과 설정은 배포 전 검증에서 실패한다.
- 실제 PostgreSQL 다중 Agent 경합 테스트가 자동 또는 release gate로 실행된다.
- runbook은 "수십 target backlog 처리"와 "수십 동시 crawl 미지원"을 명확히 구분한다.
