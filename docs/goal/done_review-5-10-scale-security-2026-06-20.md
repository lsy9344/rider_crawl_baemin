# 5-10 영역 확장성·보안·운영 검토 기록

> 적용 완료: 2026-06-20에 검토 내용과 작업지시서 기준으로 코드·테스트·운영 문서에 반영했다. 적용 후 전체 pytest와 배포 config 검증을 통과했다.

작성일: 2026-06-20  
대상:

- 5. Agent PC 클라이언트: `src/rider_agent/**`, `tests/agent/**`
- 6. 서버 API·인증·보안 경계: `src/rider_server/main.py`, `settings.py`, `api/**`, `security/**`, `runtime.py`, 관련 tests
- 7. 서버 도메인·비즈니스 서비스: `src/rider_server/domain/**`, `src/rider_server/services/**`
- 8. Queue·Scheduler·Dispatch·Metrics: `src/rider_server/queue/**`, `scheduler/**`, `dispatch/**`, `metrics/**`, 관련 tests
- 9. Admin UI·운영 액션: `src/rider_server/admin/**`, `src/rider_server/services/admin_*`, `src/rider_server/services/admin_entities/**`, 관련 tests
- 10. DB·마이그레이션·배포·CI: `src/rider_server/db/**`, `migrations/**`, `deploy/**`, `.github/workflows/test.yml`, `scripts/test.ps1`, `docs/runbooks/**`, `docs/operations/**`

검토 방식:

- 코드와 테스트를 read-only로 확인했다.
- Agent PC, API/security, domain/service, queue/scheduler/dispatch/metrics, admin, DB/deploy/CI 영역을 병렬로 나누어 확인했다.
- 직접 확인한 명령:

```powershell
$env:RIDER_POSTGRES_PASSWORD='rider'
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED='1'
docker compose -f deploy/docker-compose.yml config
```

결과:

```text
error while interpolating services.backend-api.environment.RIDER_TELEGRAM_WEBHOOK_SECRET: required variable RIDER_TELEGRAM_WEBHOOK_SECRET is missing a value: set RIDER_TELEGRAM_WEBHOOK_SECRET
```

주의:

- 이 문서는 구현 전 검토 기록이다.
- pytest 전체 suite는 실행하지 않았다.
- 운영 문서 안에서 발견된 등록 코드는 전체 값을 다시 적지 않는다. 비밀값처럼 보이는 문자열은 `agreg_...` 형태로만 표시한다.

---

## 결론

현재 구조는 여러 Agent PC가 서버 job queue를 나눠 처리하는 방향은 맞다. `FOR UPDATE SKIP LOCKED`, lease, stale recovery, scheduler tick, dispatch outbox 같은 기본 부품도 있다.

하지만 100개 이상 crawling job과 여러 Agent를 안정적으로 운영하려면 아래 문제가 먼저 닫혀야 한다.

1. Admin 보안 경계가 일부 설정에서 너무 쉽게 열린다.
2. Telegram outbox는 성공 전송 후 worker crash가 나면 중복 전송될 수 있다.
3. Agent PC는 단일 실행과 registration/token 저장을 프로세스 간에 보호하지 않는다.
4. 실제 browser crawl timeout 경로가 Chrome 작업을 강제로 죽이지 못할 수 있다.
5. Scheduler, queue, metrics는 100개 이상 장애/복구 상황에서 batch, index, timestamp 기준이 부족하다.
6. Admin action은 audit, 확인 절차, 중복 enqueue, rowcount 검증이 약하다.
7. CI compose 검증은 현재 환경값 부족으로 실패한다.
8. 운영 문서에 실제처럼 보이는 agent registration code가 남아 있다.

---

## 우선순위 요약

| 우선순위 | 항목 | 영향 |
| --- | --- | --- |
| P0 | Telegram outbox 중복 전송 가능 | 고객/운영 채널에 같은 알림이 여러 번 갈 수 있음 |
| P0 | public admin production 차단 없음 + XFF 신뢰 | 외부 접근 시 Admin 권한이 열릴 수 있음 |
| P0 | 운영 문서 `agreg_...` 등록 코드 노출 | 다른 PC가 agent로 등록될 수 있음 |
| P1 | Agent 단일 실행/등록 저장 lock 없음 | 같은 PC에서 agent identity와 job 처리가 꼬일 수 있음 |
| P1 | crawl timeout이 thread timeout 경로에 머묾 | stuck Chrome/thread 누적, profile 오염 |
| P1 | CI deployment config gate 실패 | PR/배포 검증이 막힘 |
| P1 | `send_only_on_change` 무시 | 변경 없는 crawl도 계속 발송 |
| P1 | Admin manual enqueue와 audit이 분리됨 | unaudited job, 중복 job flood |
| P2 | stale recovery batch 없음, claim index 약함 | 장애 후 복구 tick이 커지고 queue latency 증가 |
| P2 | metrics window 기준 오류 | 오래된 Telegram 실패가 계속 최근 오류로 보임 |
| P2 | API body bound 부족 | 악성/오동작 agent가 JSON 파싱과 DB를 압박 |

---

## 5. Agent PC 클라이언트

### P1. registration/token 저장이 프로세스 간에 원자적이지 않음

근거:

- `src/rider_agent/registration.py:234`: local identity를 먼저 읽고 있으면 바로 반환한다.
- `src/rider_agent/registration.py:251`: 없으면 서버 등록 POST를 보낸다.
- `src/rider_agent/secure_store.py:250`: token을 secure store에 먼저 저장한다.
- `src/rider_agent/secure_store.py:254`: identity config를 별도로 atomic write 한다.

문제:

- 두 등록 프로세스가 동시에 실행되면 `load -> POST -> token 저장 -> config 저장`이 서로 섞일 수 있다.
- 결과적으로 config는 agent A, token은 agent B가 되는 상태가 생길 수 있다.
- 이후 heartbeat, claim, complete가 token mismatch로 실패하거나, 운영자가 원인을 찾기 어렵다.

권장 조치:

- registration 전체를 Windows named mutex 또는 state-dir lock file로 감싼다.
- `save_agent_identity()`는 token과 config 쌍을 저장한 뒤 다시 읽어 pair 검증을 한다.
- 동시에 등록을 시도하면 둘 중 하나는 "already registered" 또는 "registration in progress"로 끝나게 한다.

검증 기준:

- 같은 temp state dir에서 두 process/thread가 같은 registration code로 동시에 실행될 때 identity/token pair가 하나만 저장된다.
- 실패한 쪽은 기존 저장값을 덮어쓰지 않는다.

### P1. `rider_agent run` 단일 실행 guard가 없음

근거:

- `src/rider_agent/__main__.py:185`: run command가 바로 `runner(...)`를 호출한다.
- `src/rider_agent/__main__.py:196`: interactive session probe만 넘기고 single-instance guard는 없다.
- `src/rider_agent/browser_profile.py`의 profile registry는 process memory 안에 있다.

문제:

- autostart와 수동 실행이 동시에 뜨면 같은 `agent_id`와 token으로 job을 claim한다.
- 서버는 하나의 Agent로 보지만 실제 active jobs와 browser profile 상태는 두 프로세스에 나뉜다.
- 같은 target profile, 같은 CDP port, 같은 heartbeat가 꼬일 수 있다.

권장 조치:

- `run` 시작 시 per-agent named mutex 또는 state-dir lock을 잡는다.
- 이미 실행 중이면 non-zero exit와 명확한 메시지를 남긴다.

검증 기준:

- 두 번째 `run`은 job loop를 시작하지 않는다.
- lock stale 상황은 PID/process 존재 확인 후 회수한다.

### P1. autostart가 working directory를 고정하지 않음

근거:

- `src/rider_agent/autostart.py:224`: `.cmd` 내용이 명령 한 줄뿐이다.
- `src/rider_agent/worker_composition.py:94`: browser profile root가 `runtime/agent-browser-profiles` 상대 경로다.
- `src/rider_agent/__main__.py:137`: 기본 log path가 `logs/agent.log` 상대 경로다.
- `src/rider_agent/workers/crawl_worker.py:503`: crawl config log dir도 `logs` 상대 경로다.

문제:

- Windows Startup 또는 Task Scheduler에서 실행 위치가 달라질 수 있다.
- profile 재사용이 깨지고, 로그와 runtime 파일이 예상 못 한 위치에 생길 수 있다.

권장 조치:

- Agent state, logs, browser profiles를 `app_state_root()` 기반 절대 경로로 통일한다.
- 또는 autostart `.cmd`가 설치 디렉터리로 `cd /d` 후 실행하게 한다.

검증 기준:

- 임의 working directory에서 autostart command를 실행해도 같은 state/log/profile 경로를 사용한다.

### P1. 실제 browser crawl timeout이 hard kill이 아님

근거:

- `src/rider_agent/workers/crawl_worker.py:143`: process boundary는 default snapshot, profile manager 없음, secret resolver 없음일 때만 켜진다.
- `src/rider_agent/workers/crawl_worker.py:342`: fallback은 daemon thread timeout이다.
- `src/rider_agent/workers/crawl_worker.py:344`: timeout 후 cleanup만 호출하고 thread 자체는 죽이지 않는다.

문제:

- 실제 운영 조합은 profile manager와 secret resolver가 들어가므로 subprocess timeout이 꺼질 수 있다.
- timeout 결과를 서버에 완료해도 실제 Chrome/thread가 계속 profile을 만질 수 있다.
- 100개 이상 job에서 stuck browser가 누적되면 PC 전체가 불안정해진다.

권장 조치:

- stateful crawl도 kill 가능한 subprocess/process boundary에서 실행한다.
- timeout 시 process group과 child Chrome을 종료한다.
- thread timeout은 browser 작업에 쓰지 않는다.

검증 기준:

- 고의로 멈추는 crawl을 timeout 시켰을 때 child process와 Chrome handle이 남지 않는다.

### P2. `max_profiles < max_jobs`가 조용히 상향됨

근거:

- `src/rider_agent/job_loop.py:874`: `effective_max_jobs` 계산
- `src/rider_agent/job_loop.py:881`: `max_profiles`가 `max_jobs`보다 작으면 `max_jobs`로 올린다.

문제:

- 운영자가 `--max-jobs 100 --max-profiles 20`처럼 안전 cap을 주어도 profile cap이 100으로 올라갈 수 있다.
- 작은 PC에서 Chrome을 과도하게 띄워 CPU/RAM/port 고갈이 생길 수 있다.

권장 조치:

- `max_jobs`와 `max_profiles`를 독립 제한으로 유지한다.
- 설정이 충돌하면 시작 실패 또는 낮은 값으로 clamp하고 로그를 남긴다.

---

## 6. 서버 API·인증·보안 경계

### P0. public admin mode가 production에서도 full admin을 열 수 있음

근거:

- `src/rider_server/settings.py:57`: `admin_public_access` 설명
- `src/rider_server/settings.py:94`: env에서 `RIDER_ADMIN_PUBLIC_ACCESS`를 읽음
- `src/rider_server/main.py:442`: hard-coded `SECRET_ADMIN`, MFA true principal 생성
- `src/rider_server/main.py:550`: public access가 켜지면 해당 principal resolver 사용

문제:

- `RIDER_ADMIN_PUBLIC_ACCESS=1`이면 외부 principal 없이 Admin 권한을 얻는다.
- production guard는 DB 유무만 확인한다. public admin 차단은 없다.
- token revoke/rotate, 위험 action route까지 권한이 열린다.

권장 조치:

- `APP_ENV=production`에서 `admin_public_access=True`면 app startup을 실패시킨다.
- dev/test라도 loopback 전용 또는 별도 admin secret을 요구한다.
- deploy env에는 public admin 파일이 production compose와 섞이지 않게 분리한다.

검증 기준:

- production settings에서 `RIDER_ADMIN_PUBLIC_ACCESS=1`이면 `create_app()`이 실패한다.

### P0. Admin IP allowlist가 spoof 가능한 XFF를 무조건 신뢰

근거:

- `src/rider_server/security/access.py:46`: `source_ip()`
- `src/rider_server/security/access.py:51`: `X-Forwarded-For` 첫 값을 바로 사용

문제:

- 앱이 reverse proxy 뒤가 아니라 직접 노출되면 클라이언트가 XFF를 마음대로 넣을 수 있다.
- allowlist에 있는 IP를 XFF에 넣으면 우회될 수 있다.

권장 조치:

- trusted proxy CIDR에서 온 요청일 때만 XFF를 신뢰한다.
- 아니면 ASGI proxy middleware에서 검증된 client host만 사용하고 여기서는 raw XFF를 보지 않는다.

검증 기준:

- untrusted client가 XFF를 넣어도 allowlist 우회가 되지 않는다.

### P1. malformed boolean env가 fail-closed가 아님

근거:

- `src/rider_server/settings.py:138`: `_env_bool`
- `src/rider_server/settings.py:146`: truthy set에 없으면 전부 False

문제:

- `RIDER_ADMIN_MFA_REQUIRED=ture` 같은 오타가 MFA 비활성화로 해석된다.
- 운영 설정 오타가 보안 기능을 끄는 방향으로 작동한다.

권장 조치:

- truthy와 falsy 값을 명시적으로 나눈다.
- 알 수 없는 값은 production에서 startup error로 처리한다.
- MFA 같은 보안 설정은 unknown이면 secure default true로 둔다.

### P1. Webhook secret provider 장애 때 global env secret으로 fallback

근거:

- `src/rider_server/main.py:310`: tenant provider secret을 추가
- `src/rider_server/main.py:317`: provider exception 시 env secret만 반환
- `src/rider_server/api/telegram_webhook.py`: route가 resolver 결과로 secret 검증

문제:

- production DB 장애 때 tenant secret set이 사라지고 global env secret만 받아들인다.
- tenant별 secret isolation이 약해질 수 있다.

권장 조치:

- production에서는 tenant provider lookup 실패 시 503 또는 fail-closed로 처리한다.
- global-only compatibility mode가 필요하면 명시 env와 테스트를 둔다.

### P2. Agent-facing request body bound가 부족

근거:

- `src/rider_server/api/agents.py:46`: heartbeat `metrics` unbounded
- `src/rider_server/api/agents.py:49`: `kakao_status` unbounded
- `src/rider_server/api/agents.py:50`: `browser_profiles` max length 없음
- `src/rider_server/api/jobs.py:102`: complete `result_json` unbounded
- `src/rider_server/api/jobs.py:114`: event `artifact_refs` unbounded

문제:

- 오동작 또는 악성 Agent가 큰 JSON을 보내면 API parsing, memory, DB JSON write를 압박한다.

권장 조치:

- ASGI/server request-size limit을 둔다.
- Pydantic model에서 list length, dict key count, string length를 제한한다.
- backend truncation은 유지하되 API 앞단에서 oversized input을 거절한다.

### P2. Job event route가 live lease ownership을 확인하지 않음

근거:

- `src/rider_server/api/jobs.py:344`: event route
- `src/rider_server/api/jobs.py:356`: backend `emit_event` 호출

문제:

- claim/complete와 달리 event는 해당 agent가 그 job을 소유했는지 확인하지 않는다.
- valid token을 가진 agent가 다른 job audit trail을 오염시킬 수 있다.

권장 조치:

- event 저장 전에 job 존재, owner, live lease를 확인한다.
- mismatch는 404/409 또는 no-op으로 처리한다.

---

## 7. 서버 도메인·비즈니스 서비스

### P0. Telegram delivery outbox가 중복 전송될 수 있음

근거:

- `src/rider_server/services/snapshot_repository_postgres.py:239`: delivery log를 `RETRYING` 상태로 생성
- `src/rider_server/services/dispatch_worker.py:92`: batch claim 후 순차 처리
- `src/rider_server/services/dispatch_worker.py:181`: Telegram send 실행
- `src/rider_server/services/dispatch_worker.py:156`: send 이후 DB update

문제:

- Telegram이 메시지를 받은 뒤 worker가 죽으면 DB는 아직 `SENT`가 아니다.
- lock timeout 후 같은 row가 다시 claim되어 재전송될 수 있다.
- batch가 크고 send가 느리면 lock timeout을 넘을 수도 있다.

권장 조치:

- side effect 전에 `SENDING` 또는 `SEND_ATTEMPTED` 같은 상태와 timestamp를 저장한다.
- send attempt가 시작된 stale row는 자동 재전송하지 말고 `HELD/UNKNOWN`으로 보낸다.
- `apply_update`는 `locked_by`, `locked_at`, expected status를 조건에 넣어 owner만 갱신하게 한다.

검증 기준:

- send 성공 직후 DB update 전에 worker crash를 시뮬레이션해도 row가 자동 재전송되지 않는다.

### P1. `send_only_on_change`가 runtime fan-out에서 무시됨

근거:

- `src/rider_server/domain/delivery_rule.py:21`: `send_only_on_change`
- `src/rider_server/services/snapshot_repository_postgres.py:213`: enabled rule을 모두 조회
- `src/rider_server/services/snapshot_repository_postgres.py:230`: dedup key에 `collected_at` 포함

문제:

- 변경 없는 snapshot도 수집 시간만 다르면 새 dedup key가 만들어진다.
- 고객이 "변경 때만 발송"을 기대해도 매 crawl마다 발송될 수 있다.

권장 조치:

- `send_only_on_change=True`이면 같은 target/channel/template의 최근 message hash와 비교한다.
- hash가 같으면 delivery log를 만들지 않는다.
- prior hash는 batch로 가져와 N+1을 피한다.

### P1. Atomic snapshot completion이 metadata를 잃음

근거:

- `src/rider_server/services/job_completion_service.py:150`: atomic path 진입
- `src/rider_server/services/job_completion_service.py:161`: non-atomic path는 duration/schema를 queue complete로 넘김
- `src/rider_server/services/snapshot_repository_postgres.py:168`: atomic path는 status/result/error/lease만 갱신

문제:

- DB-backed snapshot job은 `completed_at`, `duration_ms`, `result_schema_version`을 잃는다.
- scheduler breaker와 metrics가 claim time에 의존하게 되어 긴 crawl에서 window가 틀어질 수 있다.

권장 조치:

- `complete_snapshot_job()` signature에 `duration_ms`, `result_schema_version`을 추가한다.
- `completed_at=now`, `duration_ms`, `result_schema_version`, `last_failed_at`를 terminal status에 맞게 저장한다.

### P2. Generic delivery failure policy가 ambiguous Telegram send의 dedup key를 release할 수 있음

근거:

- `src/rider_server/services/delivery_failure_policy.py:260`: send error 처리
- `src/rider_server/services/delivery_failure_policy.py:279`: retryable이면 dedup key release
- `src/rider_server/services/telegram_central_dispatch.py:135`: ambiguous send failure는 release 금지 의미를 가짐

문제:

- 미래에 generic policy와 Telegram sender가 조합되면, 이미 전달됐을 수 있는 메시지의 dedup key를 풀 수 있다.

권장 조치:

- failure policy에 release predicate를 넣는다.
- Telegram ambiguous error는 `HELD` 또는 non-release retry로 분류한다.

### P2. Kakao active-room uniqueness가 check-then-write

근거:

- `src/rider_server/services/admin_entity_service.py:680`
- `src/rider_server/services/admin_entity_service.py:750`
- `src/rider_server/services/admin_entity_service.py:806`
- `src/rider_server/services/channel_registration.py:371`

문제:

- 두 admin action이 동시에 실행되면 같은 Kakao room을 둘 다 active로 만들 수 있다.

권장 조치:

- tenant별 active Kakao room partial unique index를 DB에 둔다.
- `IntegrityError`를 기존 duplicate/collision error로 매핑한다.

---

## 8. Queue·Scheduler·Dispatch·Metrics

### P2. stale lease recovery에 batch limit이 없음

근거:

- `src/rider_server/queue/postgres_queue.py:432`: `recover_stale`
- `src/rider_server/queue/postgres_queue.py:443`: 모든 row를 `.all()`로 가져옴

문제:

- Agent 장애로 stale job이 많이 쌓이면 한 tick에서 큰 lock/update transaction이 생긴다.
- 100개 이상 stuck job에서는 recovery worker가 DB를 오래 잡을 수 있다.

권장 조치:

- configurable batch size를 추가한다.
- `lease_expires_at`, `id` 순으로 정렬하고 `limit`을 건다.
- 여러 tick 또는 chunk로 나눠 처리한다.

### P2. scheduler capacity가 job type별이 아니라 aggregate

근거:

- `src/rider_server/scheduler/postgres_repository.py` capacity snapshot은 전체 online capacity와 capability union을 만든다.
- `src/rider_server/scheduler/policy.py`는 job type이 capabilities set에 있으면 total capacity로 판단한다.

문제:

- BAEMIN agent capacity는 남았지만 COUPANG agent가 꽉 찬 상태에서 COUPANG job을 enqueue할 수 있다.

권장 조치:

- `capacity_by_job_type`, `in_flight_by_job_type`을 계산한다.
- admission은 job type별 free capacity로 판단한다.

### P2. job claim index가 claim query와 완전히 맞지 않음

근거:

- `src/rider_server/db/models/agent.py:100`: `ix_jobs_status(status, run_after)`
- `src/rider_server/queue/postgres_queue.py:214`: status filter
- `src/rider_server/queue/postgres_queue.py:216`: type filter

문제:

- mixed job type이 많아지면 agent가 실행할 수 없는 due job까지 index scan할 수 있다.

권장 조치:

- `(status, type, run_after, id)` 또는 pending partial index를 추가한다.
- 실제 PostgreSQL query plan을 확인한 뒤 old index drop 여부를 정한다.

### P1. Telegram error metric이 실제 최근 window가 아님

근거:

- `src/rider_server/metrics/repository_postgres.py:125`: `telegram_error_count`
- `src/rider_server/metrics/repository_postgres.py:133`: `sent_at IS NULL OR sent_at >= since`
- `src/rider_server/services/dispatch_worker.py:209`: 실패 update는 `sent_at=None`

문제:

- 오래된 실패 row도 `sent_at IS NULL`이면 계속 최근 오류로 카운트된다.
- 운영 dashboard가 영구 장애처럼 보일 수 있다.

권장 조치:

- `delivery_logs.last_failed_at` 또는 `last_attempted_at`를 추가한다.
- metric은 해당 timestamp 기준으로 window를 계산한다.

### P2. dispatch worker가 sync sender thread 안에서 async DB lookup을 반복

근거:

- `src/rider_server/dispatch/__main__.py`의 sender wrapper가 `asyncio.run(provider.get(...))`를 사용한다.
- `src/rider_server/services/dispatch_worker.py:181`에서 이 sender를 `asyncio.to_thread()`로 호출한다.

문제:

- message마다 tenant config DB lookup이 반복된다.
- thread 안에서 event loop를 새로 만들며 async engine/session factory와 섞인다.

권장 조치:

- `claim_pending()` 또는 dispatch batch 시작 전에 tenant config/token을 async로 batch load한다.
- thread sender는 순수 sync HTTP send만 하게 한다.

---

## 9. Admin UI·운영 액션

### P1. manual enqueue와 audit이 원자적이지 않고 중복 job flood 가능

근거:

- `src/rider_server/services/admin_action_service.py:524`: `test_crawl` enqueue
- `src/rider_server/services/admin_action_service.py:539`: audit은 나중에 기록
- `src/rider_server/services/admin_action_service.py:556`: `auth_check` enqueue
- `src/rider_server/services/admin_action_service.py:571`: audit은 나중에 기록

문제:

- audit 실패 후에도 job은 이미 queue에 남는다.
- 반복 클릭이나 여러 운영자가 같은 target에 manual job을 많이 만들 수 있다.

권장 조치:

- job enqueue와 audit insert를 같은 DB transaction으로 묶는다.
- target+job_type별 active manual job dedup 또는 cooldown을 둔다.

### P1. update/delete repository가 rowcount 0이어도 success audit을 남길 수 있음

근거:

- `src/rider_server/services/admin_action_repository_postgres.py`의 subscription/target/job/agent assignment update가 rowcount를 보지 않는다.
- `src/rider_server/services/admin_entity_repository_postgres.py`의 update/delete도 rowcount 검증이 약하다.

문제:

- 다른 운영자 또는 scheduler가 먼저 상태를 바꾼 뒤에도 성공 audit이 남을 수 있다.
- 실제 DB 상태와 audit trail이 어긋난다.

권장 조치:

- `UPDATE/DELETE`에 tenant, expected status, id 조건을 넣는다.
- `rowcount == 1`을 확인하고 0이면 conflict/not-found로 처리한다.

### P2. DB failure display가 full dashboard에만 있음

근거:

- `src/rider_server/admin/routes.py:319`: full page DB failure는 `_db_failure.html`로 처리
- fragment endpoints는 같은 wrapper가 없다.

문제:

- 30초 HTMX polling 중 DB 장애가 나면 panel이 generic 500/JSON으로 깨질 수 있다.

권장 조치:

- fragment 공통 error wrapper를 둔다.
- 작은 safe HTML partial과 503을 반환한다.

### P2. 위험 action confirmation이 client-side 위주

근거:

- 일부 template은 `window.confirm`만 사용한다.
- `auth-check`, `test-crawl` 같은 queue-spawning action에는 확인 절차가 약하다.
- server route는 confirmation marker 없이 POST를 받는다.

문제:

- direct POST, script, repeat click으로 확인 절차를 우회할 수 있다.

권장 조치:

- destructive 또는 queue-spawning action은 server-checked confirmation field 또는 reason을 요구한다.
- direct POST without confirmation test를 추가한다.

### P2. Dashboard target severity 정렬이 page 안에서만 됨

근거:

- `src/rider_server/admin/routes.py:50`: default target fragment limit 100
- `src/rider_server/admin/routes.py:248`: repo target health를 limit/offset으로 먼저 조회
- `src/rider_server/admin/routes.py:258`: 가져온 rows 안에서 severity sort

문제:

- target이 100개를 넘으면 첫 page 밖의 critical target이 첫 화면에 안 보일 수 있다.

권장 조치:

- read model 또는 DB query 단계에서 severity key를 계산해 우선순위 정렬한다.
- 최소한 critical/attention bucket을 별도 조회해 상단에 고정한다.

---

## 10. DB·마이그레이션·배포·CI

### P1. CI deployment config gate가 현재 실패

근거:

- `.github/workflows/test.yml:165`: `docker compose -f deploy/docker-compose.yml config`
- `.github/workflows/test.yml:167`: Postgres password만 주입
- `deploy/docker-compose.yml:68`: `RIDER_TELEGRAM_WEBHOOK_SECRET` 필수
- `deploy/docker-compose.yml:69`: `RIDER_TELEGRAM_BOT_TOKEN` 필수
- `deploy/docker-compose.yml:157`: dispatch service도 bot token 필수

직접 확인:

```powershell
$env:RIDER_POSTGRES_PASSWORD='rider'
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED='1'
docker compose -f deploy/docker-compose.yml config
```

결과:

```text
RIDER_TELEGRAM_WEBHOOK_SECRET is missing a value
```

권장 조치:

- CI config step에 dummy non-secret 값을 주입한다.
- 또는 secret-required service를 compose profile/override로 분리한다.

### P0. 운영 문서에 실제처럼 보이는 agent registration code가 있음

근거:

- `docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md:13`
- `docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md:105`
- `docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md:171`

문제:

- `agreg_...` 형태의 registration code가 문서에 남아 있다.
- 아직 유효하면 다른 PC가 agent로 등록할 수 있다.

권장 조치:

- 해당 code를 즉시 revoke/rotate한다.
- 문서는 `<AGENT_REGISTRATION_CODE>` placeholder로 바꾼다.
- docs secret scan에 `agreg_` pattern을 추가한다.

### P2. browser profile collision을 DB schema가 막지 않음

근거:

- `src/rider_server/db/models/agent.py:63`: `BrowserProfile`
- `src/rider_server/db/models/agent.py:67`: `agent_id`
- `src/rider_server/db/models/agent.py:68`: `target_id`
- `src/rider_server/db/models/agent.py:70`: `cdp_port`

문제:

- 같은 agent/target, 같은 agent/CDP port, 같은 profile ref 중복을 schema가 막지 않는다.

권장 조치:

- `UNIQUE(agent_id, target_id)`를 추가한다.
- `cdp_port IS NOT NULL` 조건의 partial unique `(agent_id, cdp_port)`를 추가한다.
- profile ref가 전역 고유라면 `profile_path_ref` uniqueness도 검토한다.

### P2. 0015 migration이 full message text를 redacted preview로 backfill

근거:

- `migrations/versions/0015_delivery_outbox.py:20`: `messages.text` 추가
- `migrations/versions/0015_delivery_outbox.py:21`: `text_redacted_preview`로 채움
- `migrations/versions/0015_delivery_outbox.py:22`: nullable false

문제:

- migration 시 pending/retryable delivery가 있으면 dispatcher가 redacted/truncated preview를 실제 본문으로 보낼 수 있다.

권장 조치:

- production migration 전 pending/retryable delivery가 0인지 확인한다.
- legacy pending row는 `HELD/FAILED`로 전환한다.
- 원문 복원이 가능할 때만 `messages.text`를 채운다.

---

## 권장 실행 순서

1. P0 보안/비밀/중복 전송 차단
   - public admin production guard
   - XFF trust guard
   - 운영 문서 registration code 제거 및 revoke
   - Telegram outbox unknown state 도입

2. P1 운영 차단 요소 해결
   - CI compose config gate 복구
   - Agent single instance/register lock
   - browser crawl hard timeout
   - `send_only_on_change` 적용
   - Admin enqueue+audit 원자화

3. P2 100+ scale 안정화
   - stale recovery batch
   - job claim index
   - capacity by job type
   - metrics failure timestamp
   - API body bounds
   - Admin fragment failure partial
   - browser profile DB unique constraints

## 검증 묶음 제안

항목별 구현 후 다음 묶음을 우선 실행한다.

```powershell
.venv\Scripts\python.exe -m pytest -q tests/server/test_admin_security.py tests/server/test_agents_api.py tests/server/test_jobs_api.py
.venv\Scripts\python.exe -m pytest -q tests/agent/test_job_loop.py tests/agent/test_crawl_worker.py tests/agent/test_autostart.py tests/agent/test_browser_profile.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_dispatch_worker.py tests/server/test_snapshot_repository_postgres.py tests/server/test_metrics_repository.py
.venv\Scripts\python.exe -m pytest -q tests/server/test_scheduler_tick.py tests/server/test_queue_backend.py tests/server/test_admin_dashboard.py
```

배포 검증:

```powershell
$env:RIDER_POSTGRES_PASSWORD='rider'
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED='1'
$env:RIDER_TELEGRAM_WEBHOOK_SECRET='ci_dummy_webhook_secret'
$env:RIDER_TELEGRAM_BOT_TOKEN='ci_dummy_bot_token'
docker compose -f deploy/docker-compose.yml config
```

문서/비밀 스캔:

```powershell
rg -n "agreg_[A-Za-z0-9_-]+" docs deploy src tests
rg -n "(bot_token|webhook_secret|password)\\s*[:=]\\s*['\\\"][^<'\\\"]" docs deploy src tests
```
