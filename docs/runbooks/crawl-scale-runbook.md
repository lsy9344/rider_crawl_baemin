# Crawl Scale Runbook

> `CRAWL_TIMEOUT` 이 반복되거나 고객 재활성화 직후 잠깐 떴다면 먼저
> `docs/runbooks/crawl-timeout-investigation.md` 의 단계별 조사 순서를 본다. timeout 은 로그인
> 실패가 아니라 수집 완료 시간 초과(안전장치)다 — 값을 늘리거나 selector 를 추측 수정하기 전에
> job timeline·Agent 로그 증거를 먼저 확인한다.

## 기본 운영 모델

- Agent 기본 동시 처리 1: `rider_agent run` 의 기본 `--max-jobs` 는 1이다. 동시 처리 수를 올리기 전에 CPU/RAM, 브라우저 profile 수, heartbeat lease 연장 상태를 먼저 확인한다.
- Scheduler는 별도 프로세스(`python -m rider_server.scheduler`)로 돌린다. 기본 `SCHEDULER_INTERVAL_SECONDS=30`, `SCHEDULER_DUE_BATCH_SIZE=100` 이다.
- Queue recovery는 별도 프로세스(`python -m rider_server.queue`)로 돌린다. 기본 `QUEUE_RECOVERY_INTERVAL_SECONDS=30`, `RIDER_JOB_RECOVERY_BATCH_SIZE=100` 이다.
- Telegram dispatch는 별도 프로세스(`python -m rider_server.dispatch --interval-seconds 5`)로 돌린다. FastAPI request background task가 아니며, compose 서비스명은 `telegram-dispatch` 이다.
- Telegram dispatch가 오래된 `SENDING` delivery log를 발견하면 자동 재전송하지 않고 `HELD`로 보낸다. 전송 성공 여부가 애매한 상태라 운영자가 확인한 뒤 재개/폐기해야 한다.
- Job claim lease_seconds 기본값은 `RIDER_JOB_LEASE_SECONDS=120` 이다. 일반적으로 crawl timeout보다 길게 두되, 너무 길면 Agent 장애 후 stale job 회수가 늦어진다.
- Agent heartbeat interval 기본 범위는 30~60초(`MIN_HEARTBEAT_INTERVAL_SECONDS=30`, `MAX_HEARTBEAT_INTERVAL_SECONDS=60`)이다.
- 기본 crawler만 쓰고 auth seam이 없는 경로는 subprocess timeout 경계를 쓴다. 일반 `rider_agent run` 의 profile-managed crawl도 parent가 profile/CDP 값을 준비한 뒤 child process에서 crawl을 실행하며, timeout 시 해당 target profile release와 idle cleanup을 수행한다.
- subprocess로 넘어가는 job payload에는 SecretRef handle만 들어가야 한다. `username`, `password`, `verification_email_address`, `verification_email_app_password` 같은 plaintext secret 필드는 임시 job 파일을 쓰기 전에 fail-closed 된다.
- 테스트나 특수 주입 경로처럼 custom crawl/auth seam을 쓰면 subprocess 경계가 비활성화되고 in-process timeout helper가 fallback으로 동작할 수 있다.

## DB Pool / Postgres 연결 수

- API worker 1개는 `RIDER_DB_POOL_SIZE + RIDER_DB_MAX_OVERFLOW` 만큼 연결을 열 수 있다. 기본값은 `RIDER_DB_POOL_SIZE=5`, `RIDER_DB_MAX_OVERFLOW=10` 이다.
- Scheduler, queue recovery, Telegram dispatch도 각각 같은 pool 기준을 사용한다.
- Postgres `max_connections` 산식:

```text
(RIDER_UVICORN_WORKERS + scheduler_processes + recovery_processes + telegram_dispatch_processes)
  * (RIDER_DB_POOL_SIZE + RIDER_DB_MAX_OVERFLOW)
  + migration/admin 여유분
```

- 운영 시작값은 `RIDER_UVICORN_WORKERS=1`, `scheduler_processes=1`, `recovery_processes=1`, `telegram_dispatch_processes=1`, `migration/admin 여유분=10` 으로 잡는다. 기본 pool이면 최소 70개 이상을 준비한다.
- 단일 EC2 + local PostgreSQL memory hardening 권장값은 `RIDER_UVICORN_WORKERS=1`, `RIDER_DB_POOL_SIZE=2`, `RIDER_DB_MAX_OVERFLOW=2` 이다. 이때 산식은 `(1 + 1 + 1 + 1) * (2 + 2) + 10 = 26` 이므로 Postgres `max_connections=100` 아래에 머문다.
- Postgres 연결 부족이 보이면 먼저 `RIDER_DB_MAX_OVERFLOW` 를 낮추고, API worker 수를 늘릴 때마다 위 산식을 다시 계산한다.
- 배포 전 connection budget guard를 실행한다.

```powershell
.venv\Scripts\python.exe scripts\check_db_connection_budget.py --postgres-max-connections 100
```

## Agent 용량 기준

- per-Agent CPU/RAM/profile count 기준: 기본은 `--max-jobs 1`, `--max-profiles 20`, `--profile-idle-ttl-seconds 3600` 이다.
- `--max-jobs` 를 2 이상으로 올릴 때는 브라우저 프로필이 동시에 열릴 수 있으므로 CPU 코어, 메모리, 디스크 프로필 수를 먼저 확인한다.
- 일반 노트북/소형 PC는 `--max-jobs 2` 또는 `--max-jobs 3` 정도부터 단계적으로 확인한다. 수십 개 Chrome을 한 Agent에서 동시에 여는 운영은 기본 모델이 아니다.
- 한 Agent의 50~100개 target은 기본적으로 예약 backlog로 안전하게 흘리는 모델이다. 50~100개 동시 crawl 모델이 아니다.
- 한 Agent에서 profile count가 계속 늘면 profile idle TTL을 줄이거나 Agent 수를 늘린다.
- Kakao 전송 노드는 interactive session이 필요하므로 server-only service로 scale out하지 않는다.

## Rollback

- scheduler rollback: `scheduler` 서비스를 이전 이미지로 되돌리거나 일시 중지한다. 중지 중에는 새 crawl job enqueue가 멈추지만 기존 PENDING/RUNNING job은 queue/Agent 흐름을 따른다.
- recovery rollback: `queue-recovery` 서비스를 이전 이미지로 되돌리거나 일시 중지한다. 중지 중에는 stale RUNNING job 회수가 늦어지므로 queue lag와 RUNNING age를 본다.
- Telegram dispatch rollback: `telegram-dispatch` 서비스를 이전 이미지로 되돌리거나 일시 중지한다. 중지 중에는 Telegram delivery log가 `RETRYING`/claim 가능 상태로 쌓이고, 서비스 재개 후 worker가 처리한다.
- API rollback: API worker 수를 먼저 `RIDER_UVICORN_WORKERS=1` 로 낮춘 뒤 이전 이미지로 되돌린다. pool 설정도 기본값으로 돌려 Postgres 연결 폭증을 막는다.

## scale smoke commands

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scheduler_tick.py::test_5_10_hundred_targets_single_tick_all_enqueued_pending_and_interval_preserved -q
.venv\Scripts\python.exe -m pytest tests/server/test_scale_readiness.py -q
```

PostgreSQL 연결 검증이 가능한 환경에서는 아래도 실행한다.

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
.venv\Scripts\python.exe -m pytest tests/negative/test_queue_concurrency.py tests/negative/test_scheduler_idempotency.py tests/negative/test_dashboard_repository_pg.py -q
```

배포 후 최소 smoke:

```powershell
curl http://SERVER/health
curl http://SERVER/metrics
docker compose -f deploy/docker-compose.yml ps
```
