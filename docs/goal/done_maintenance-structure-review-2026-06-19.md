# 유지보수성·확장성·구조 개선 검토

완료: 2026-06-19 기준, 이 검토 결과에 따른 작업을 끝냈습니다.

작성일: 2026-06-19  
대상: `rider_result_mornitoring` 현재 작업트리  
범위: 읽기 전용 구조 감사. 코드 수정은 하지 않았다.  
검토 방식: `src/`, `tests/`, `deploy/`, 기존 운영 문서, 테스트 실행 전략을 대조했다.

## 요약

현재 구조는 `rider_crawl`, `rider_agent`, `rider_server`의 큰 경계가 비교적 잘 잡혀 있다. 패키지 의존 방향, Agent sync 경계, Server async 경계, secret redaction 규칙도 테스트와 문서로 많이 잠겨 있다.

가장 먼저 손볼 부분은 두 갈래다.

1. 운영 배포 기본값과 secret 전달 방식이다. `backend-api.env`가 공개 Admin을 기본으로 켜고 있고, Telegram secret ref가 실제 컨테이너 env로 어떻게 전달되는지 작업 지시가 부족하다.
2. 서버 완료 workflow와 snapshot ingest/dispatch 경계다. 라우트와 저장소가 workflow, 보상, 외부 전송을 직접 조립하고 있어 장애 복구와 재시도 정책이 한 곳에서 보이지 않는다.

대규모 재작성보다 작은 application service, typed runtime container, delivery log 기반 dispatch worker, scheduler bulk query를 순서대로 세우는 것이 비용 대비 효과가 크다.

## 긍정적 구조

- `rider_server/domain/states.py`처럼 서버 내부 도메인 언어가 모인 파일이 있다.
- 여러 서비스가 외부 I/O를 주입 seam으로 둔다. 예: `CrawlService.crawl`, `DispatchService.dispatch`, `CentralTelegramSender`.
- `Settings`는 frozen dataclass와 `from_env(environ)` 형태라 테스트에서 환경 의존을 끊기 쉽다.
- `scripts/test.ps1`과 `tests/conftest.py`가 `quick`, `architecture`, `postgres`, `docs` 같은 검증 단계를 분리한다.
- Alembic URL과 운영 secret은 코드 파일에 직접 박지 않는 방향으로 설계되어 있다.

## 우선순위 기준

- P1: 보안, 장애 복구, 중복 전송, 데이터 정합성, 운영 배포 실패에 직접 영향을 줄 수 있는 구조 문제
- P2: 기능 추가 속도를 늦추고 회귀 위험을 키우는 구조 문제
- P3: 지금은 동작하지만 운영 규모가 커질수록 비용이 늘어나는 개선 후보

## P1. 운영 env가 공개 Admin과 secret 전달 책임을 섞고 있다

근거:

- `deploy/env/backend-api.env:36`에 `RIDER_ADMIN_PUBLIC_ACCESS=1`이 활성값으로 들어 있다.
- `src/rider_server/main.py:409`의 `_resolve_public_admin_principal()`은 공개 Admin 모드에서 `src/rider_server/main.py:412`의 `SECRET_ADMIN` 권한을 만든다.
- `deploy/env/telegram-webhook.env:11`과 `deploy/env/telegram-webhook.env:14`는 각각 `env:RIDER_TELEGRAM_WEBHOOK_SECRET`, `env:RIDER_TELEGRAM_BOT_TOKEN` ref를 가리킨다.
- `src/rider_server/main.py:259` 이후 secret resolver는 `env:` ref를 실제 `os.environ`에서 찾는다.
- `deploy/docker-compose.yml:54`는 backend env file을 읽지만, ref가 가리키는 실제 secret env를 어떤 방식으로 컨테이너에 전달할지 작업 지시가 명확하지 않다.

왜 문제인가:

운영 기본 env가 공개 Admin을 켜면 새 환경을 만들 때 안전하지 않은 값이 그대로 따라갈 수 있다. 또한 secret ref만 있고 실제 env 전달 절차가 명확하지 않으면, webhook 검증이나 중앙 Telegram send가 fail-closed로 막히거나 운영자가 임시 파일에 secret을 쓰는 우회가 생긴다.

개선 방향:

- 기본 `backend-api.env`는 `RIDER_ADMIN_PUBLIC_ACCESS=0` 또는 주석 처리된 예시로 둔다.
- 공개 Admin은 별도 dev override env/compose 파일로만 opt-in한다.
- 현재 운영 문서가 공개 Admin 사용을 사용자 결정으로 기록하고 있으므로, 전환 전 대체 인증, IP 제한, 배포 절차, 운영 승인 기록을 먼저 정한다.
- `env:` secret ref를 유지한다면 compose/service environment에 실제 `RIDER_TELEGRAM_WEBHOOK_SECRET` 전달을 명시한다. tenant DB secret을 정본으로 쓸 경우에는 env fallback 미사용 조건을 문서와 테스트에 분명히 남긴다.

## P1. jobs API 라우트가 완료 workflow와 보상 로직을 직접 조립한다

근거:

- `src/rider_server/api/jobs.py:295`의 `complete_job()`이 HTTP 라우트인데, `src/rider_server/api/jobs.py:316` 이후 snapshot ingest 준비, in-flight job 조회, atomic complete 분기, queue complete fallback을 직접 처리한다.
- `src/rider_server/api/jobs.py:368`부터 `complete_snapshot_job` optional 메서드를 런타임에 찾아 분기한다.
- `src/rider_server/api/jobs.py:418` 이후 ingest 실패 시 backend의 optional `restore_claimed_after_snapshot_failure`를 찾아 보상한다.

왜 문제인가:

라우트는 인증, 입력 검증, 응답 코드 변환에 집중할수록 안전하다. 지금처럼 workflow를 직접 조립하면 job 완료 정책을 바꿀 때 API 라우트, queue backend, ingest service를 함께 이해해야 한다.

개선 방향:

- `JobCompletionService`를 새로 두고 `complete_job()`의 핵심 흐름을 옮긴다.
- 라우트는 `service.complete(...)` 결과를 HTTP status로 바꾸는 얇은 계층으로 줄인다.
- optional method 탐색 대신 명시적인 Protocol 또는 service 타입을 둔다.

## P1. Snapshot ingest repository가 저장소를 넘어 외부 전송까지 소유한다

근거:

- `src/rider_server/services/snapshot_repository_postgres.py:133`의 `complete_snapshot_job()`이 job 완료, snapshot 저장, message 저장, dispatch record 생성을 한 번에 처리한다.
- 같은 파일 `src/rider_server/services/snapshot_repository_postgres.py:196`에서 dispatch record 생성을 호출하고, `src/rider_server/services/snapshot_repository_postgres.py:204` 이후에는 DB commit 밖에서 Telegram 전송까지 수행한다.
- `src/rider_server/services/snapshot_repository_postgres.py:343`은 Telegram 전송 또는 상태 갱신 실패를 조용히 무시한다.
- `src/rider_server/db/models/messaging.py:103`의 `DeliveryLog`에는 status/error 중심 필드는 있지만, worker claim용 `available_at`, `attempt_count`, `locked_at` 같은 스케줄링 필드는 아직 없다.
- `src/rider_server/domain/delivery_log.py:3`도 현재 delivery log 도메인을 `id`, `message_id`, `channel_id`, `status`, `dedup_key`, `error_code`, `sent_at` 중심으로 설명한다.

왜 문제인가:

저장소가 DB 기록과 네트워크 전송을 함께 맡으면 재시도, 중복 차단, 실패 알림, 운영 복구를 따로 검증하기 어렵다. 전송 실패를 조용히 무시하면 운영자는 “snapshot은 성공했지만 전송은 실패한 상태”를 늦게 알 수 있다.

개선 방향:

- DB 트랜잭션은 “job 완료 + snapshot/message/delivery_log 생성”까지만 담당하게 한다.
- 이 프로젝트는 14개 필수 테이블을 고정하는 규칙이 있으므로, 별도 outbox 테이블보다 `delivery_logs`에 필요한 claim/backoff 컬럼을 additive로 추가하는 쪽을 우선 검토한다.
- Telegram 실전송은 별도 dispatch worker/service가 pending delivery log를 claim하고 `SENT`, `RETRYING`, `HELD`, `FAILED`로 갱신하게 분리한다.
- 기존 `CentralTelegramSender`, `DeliveryFailurePolicy`, `IdempotentDeliveryService`, `DispatchFanoutService`는 재사용하고, 새 worker가 전송 정책을 다시 구현하지 않게 한다.
- 구조 가드인 `tests/server/test_postgres_runtime_guards.py`도 함께 갱신한다.

## P2. app factory가 service locator와 composition root를 동시에 맡는다

근거:

- `src/rider_server/main.py:109`부터 `_default_*` factory가 계속 늘어난다.
- `src/rider_server/main.py:433`의 `create_app()` 인자가 queue, repository, metrics, admin, token, registry, ingest service까지 확장되어 있다.
- `src/rider_server/main.py:473`부터 여러 객체가 `app.state.*`에 직접 붙는다.
- `src/rider_server/main.py:97`의 `_postgres_session_factory()`는 `session_factory`가 전달되지 않으면 새 engine을 만들 수 있다. 다만 현재 `create_app()` 기본 경로는 `src/rider_server/main.py:462`에서 만든 `db_session_factory`를 하위 factory에 넘기므로, 위험은 “현재 기본 경로의 확정 버그”가 아니라 “helper 단독 호출 또는 새 wiring 추가 시 중복 engine이 생길 수 있는 구조”로 보는 것이 맞다.

왜 문제인가:

`app.state`는 타입이 약하다. 어떤 라우트가 어떤 state를 필요로 하는지 코드만 보고 찾기 어렵고, 기능이 늘수록 `main.py`가 모든 의존성의 병목이 된다.

개선 방향:

- `RuntimeDeps` 또는 `AppContainer` dataclass를 만들어 queue, repositories, services, resolvers를 명시한다.
- `app.state.container = container`로 붙이고, 기존 `app.state.*`는 호환용으로 잠시 유지한다.
- `create_app()`에서 DB engine/session factory가 한 번만 만들어지는지 테스트로 잠근다.

## P2. scheduler가 대상 수에 비례해 DB round-trip을 반복한다

근거:

- `src/rider_server/scheduler/postgres_repository.py:101`의 `due_targets()`는 due 대상을 제한 없이 읽는다.
- `src/rider_server/scheduler/service.py:181`은 due target 전체를 받은 뒤 처리한다.
- `src/rider_server/scheduler/service.py:203` 이후 대상마다 `tenant_gate()`를 호출한다.
- `src/rider_server/scheduler/service.py:238`에서 대상마다 `has_active_crawl_job()`을 호출한다.
- 각 호출은 `src/rider_server/scheduler/postgres_repository.py:152`, `src/rider_server/scheduler/postgres_repository.py:193`의 별도 query로 이어진다.

왜 문제인가:

대상이 적을 때는 괜찮지만, 고객과 target 수가 늘면 한 tick 안에서 DB 쿼리가 급격히 늘어난다. scheduler tick이 늦어지면 실행 간격이 흔들리고 큐 생성이 몰릴 수 있다.

개선 방향:

- `SchedulerService(batch_size=...)`와 `due_targets(limit=...)`를 도입한다.
- due target들의 tenant gate를 한 번에 조회한다.
- active crawl job 존재 여부도 target id 목록 기준 bulk query로 가져온다.
- bulk 메서드는 optional이 아니라 `SchedulerRepository`의 필수 계약으로 추가하고 모든 fake/Postgres 구현을 같은 패치에서 갱신한다.

## P2. Admin Entity CRUD가 하나의 거대한 service/port/fake로 묶여 있다

근거:

- `src/rider_server/services/admin_entity_service.py:161`의 `AdminEntityRepository`가 tenant, subscription, platform account, monitoring target, messenger channel, delivery rule CRUD를 모두 포함한다.
- 같은 파일 `src/rider_server/services/admin_entity_service.py:321` 이후에 scope 검증과 각 엔티티 command가 이어진다.
- `src/rider_server/services/admin_entity_service.py:1232` 이후에는 in-memory fake까지 같은 파일에 있다.
- `src/rider_server/admin/crud_routes.py:249` 이후 하나의 라우트 파일에 여러 엔티티 CRUD fragment가 계속 누적된다.

왜 문제인가:

새 admin 엔티티나 규칙이 생길 때 한 파일과 한 protocol이 계속 커진다. 서로 다른 엔티티 변경이 같은 파일에서 충돌하고, 테스트 fake도 같이 커져 수정 비용이 오른다.

개선 방향:

- 한 번에 나누지 말고 변경이 잦은 aggregate부터 작게 분리한다.
- 예: `admin_entities/tenant_service.py`, `admin_entities/target_service.py`, `admin_entities/channel_service.py`.
- 공통 audit helper와 tenant scope helper는 공유 모듈로 둔다.
- in-memory fake는 테스트/개발용 fake 모듈로 옮긴다.

## P2. QueueBackend가 queue, event audit, stale recovery를 모두 포함한다

근거:

- `src/rider_server/queue/backend.py:102`의 `in_flight_job()`, `src/rider_server/queue/backend.py:147`의 `recover_stale()`, `src/rider_server/queue/backend.py:154`의 `emit_event()`가 모두 queue port에 있다.
- `src/rider_server/api/jobs.py:236`의 `_recover_stale_if_due()`는 claim 라우트 안에서 stale recovery를 주기적으로 실행한다.

왜 문제인가:

PostgreSQL queue 안에서는 자연스럽지만, Redis, SQS, 별도 worker queue로 바꾸려면 audit event와 stale recovery 의미까지 새 backend가 모두 구현해야 한다. queue 추상화가 더 무거워진다.

개선 방향:

- claim/complete/extend lease는 queue port에 남긴다.
- job event 기록은 `JobEventRepository`로 분리한다.
- stale recovery는 `QueueMaintenanceService`로 분리하고 API claim 라우트가 아니라 scheduler나 별도 maintenance loop에서 호출한다.

## P2. 서버 서비스가 `rider_crawl.AppConfig`를 넓은 운반체로 재사용한다

근거:

- `src/rider_crawl/config.py:29`에 큰 실행 설정 객체인 `AppConfig`가 있다.
- `src/rider_server/services/telegram_central_dispatch.py:47`은 서버 Telegram 전송에서 `AppConfig`를 import한다.
- 같은 파일 `src/rider_server/services/telegram_central_dispatch.py:109`은 send에 무관한 필드를 placeholder로 채운다고 설명하고, `src/rider_server/services/telegram_central_dispatch.py:114`에서 실제로 `AppConfig(...)`를 만든다.
- `src/rider_server/services/crawl_service.py:27`도 `AppConfig`를 import한다.

왜 문제인가:

서버 전송 경계가 크롤러 앱의 전체 설정 모양에 묶인다. `AppConfig` 필드가 늘거나 필수값 의미가 바뀌면, 서버 전송 코드가 실제로 쓰지 않는 placeholder까지 맞춰야 한다.

개선 방향:

- Telegram 전송에는 token/chat_id/thread_id만 가진 작은 DTO 또는 Protocol을 둔다.
- crawl 실행에는 `CrawlRequest`나 `BrowserConfig` 같은 서버 경계 DTO를 둔다.
- `AppConfig` 변환은 adapter 한 곳에서만 수행한다.

## P2. Agent 재사용 경계가 너무 넓은 facade가 되었다

근거:

- `src/rider_agent/reuse.py:1`은 `rider_crawl` 재사용 seam을 단일 chokepoint로 둔다.
- 같은 파일 `src/rider_agent/reuse.py:23`, `src/rider_agent/reuse.py:29`, `src/rider_agent/reuse.py:47`, `src/rider_agent/reuse.py:52`에서 crawler/parser, browser launcher, auth recovery, sender까지 한 파일에서 re-export한다.

왜 문제인가:

단일 경계는 import 방향을 지키는 데 도움이 되지만, 지금은 수집, 파서, 브라우저, 인증, 메신저 전송이 모두 한 facade에 모였다. Agent worker가 필요한 역할보다 넓은 표면을 보게 된다.

개선 방향:

- `crawl_port.py`, `browser_port.py`, `auth_port.py`, `messenger_port.py`처럼 역할별 포트로 점진 분리한다.
- worker는 필요한 포트만 import하게 한다.

## P2. Agent runtime bootstrap이 여러 worker 조립 규칙을 한 함수에 품고 있다

근거:

- `src/rider_agent/job_loop.py:700`의 `run_agent()` 인자와 내부 분기가 많다.
- `src/rider_agent/job_loop.py:773`부터 auth worker, crawl worker, kakao sender worker를 순서대로 합성한다.
- `src/rider_agent/job_loop.py:881` 이후 최종 runner/reporter를 만든다.

왜 문제인가:

Agent는 sync-only와 import 경계가 중요해서 lazy import 패턴은 이유가 있다. 다만 worker가 늘수록 `run_agent()`가 모든 조립 규칙의 중심이 되어 테스트 조합이 많아진다.

개선 방향:

- `compose_execute_job()` 같은 작은 조립 함수를 만든다.
- `run_agent()`는 identity/token 확인, heartbeat 시작, runner 실행만 담당한다.
- 새 composition 모듈이 `job_loop.py`를 다시 import해 순환 import를 만들지 않도록 타입 위치를 먼저 정한다.

## P2. 배포 의존성과 CI smoke 검증이 운영 실행까지 보장하지 않는다

근거:

- `deploy/Dockerfile.server:20` 이후 Dockerfile이 서버 의존성을 직접 나열한다.
- `pyproject.toml:23`의 `[project.optional-dependencies].server`도 같은 의존성 정본을 가진다.
- `.github/workflows/test.yml:165`와 `.github/workflows/test.yml:171`은 compose config와 Docker build를 확인하지만, 실행된 컨테이너의 `/health`, migration one-shot, env 전달까지 검증하지는 않는다.

왜 문제인가:

로컬 테스트와 운영 이미지의 의존성 목록이 갈라질 수 있다. 또한 build 성공만으로는 env 전달, migration, healthcheck, production startup 실패를 잡기 어렵다.

개선 방향:

- Dockerfile 의존성 목록을 `pyproject.toml` server extra와 동기화하는 정적 테스트나 생성 스크립트를 둔다.
- schedule/push CI에는 최소 `docker compose up --wait` 또는 `docker run` 기반 `/health` smoke를 추가한다.

## P3. CrawlWorker가 payload 해석, secret 해석, config 생성, 실행, 결과 직렬화를 함께 맡는다

근거:

- `src/rider_agent/workers/crawl_worker.py:104`의 `execute()`가 job type 확인, payload 파싱, secret 금지 검사, config 준비, auth probe, crawl 실행, mismatch 검사, 결과 payload 생성을 모두 처리한다.
- `src/rider_agent/workers/crawl_worker.py:250`의 `payload_from_job()`과 `src/rider_agent/workers/crawl_worker.py:356`의 `_build_config()`도 같은 파일에 있다.
- `src/rider_agent/workers/crawl_worker.py:450`의 `_snapshot_payload()`가 서버 ingest용 JSON 모양까지 만든다.

개선 방향:

- `crawl_job_payload.py`: payload 파싱과 secret ref 해석
- `crawl_job_config.py`: `CrawlJobPayload` -> `AppConfig`
- `crawl_result_serializer.py`: snapshot -> result_json
- `crawl_worker.py`: 위 조각을 호출하는 orchestration

## P3. legacy desktop UI는 구조적으로 9탭 모델에 묶여 있다

근거:

- `src/rider_crawl/ui_settings.py:226`의 `load_all(max_tabs=9)`가 기본 탭 수를 고정한다.
- `src/rider_crawl/ui.py:330`에서 `vars_by_tab` 전체를 `크롤링N` notebook 탭으로 만든다.
- `src/rider_crawl/ui.py:193` 이후 `RiderBotUi`가 설정 UI, runtime 상태, scheduler thread, Telegram poller, Kakao/Telegram send lock을 한 클래스에서 관리한다.

개선 방향:

- legacy UI는 “로컬 운영/디버그 도구”로 역할을 문서와 UI 문구에서 제한한다.
- 다고객 확장은 Admin UI와 server/agent 경로에만 추가한다.
- legacy UI 변경은 버그 수정과 호환 유지 위주로 제한한다.

## P3. 상태 문자열과 운영 정책 상수가 여러 곳에 복사되어 있다

근거:

- `src/rider_agent/auth/baemin_auth.py:69`은 server import를 피하기 위해 상태값만 베낀다고 설명한다.
- `src/rider_agent/workers/crawl_worker.py:38`에도 auth state 문자열이 있다.
- `src/rider_server/domain/states.py:53`에는 서버 domain enum 값이 있다.
- `src/rider_server/metrics/policy.py:45`의 `TELEGRAM_ERROR_WINDOW`와 `src/rider_server/admin/dashboard_repository_postgres.py:64`의 `_TELEGRAM_ERROR_WINDOW`가 같은 10분 값을 따로 가진다.

개선 방향:

- server 구현 패키지가 아니라 중립 계약 패키지나 생성된 JSON/OpenAPI enum을 두어 Agent와 Server가 함께 보게 한다.
- 운영 지표 윈도는 public 정책 모듈 하나로 올린다.

## P3. 환경 샘플과 루트 config의 정본이 흐리다

근거:

- `.env.example:38` 이후는 쿠팡 2FA를 UI 탭별 IMAP 앱 비밀번호 방식으로 설명한다.
- `docs/config-samples/.env.sample:40` 이후는 Gmail OAuth와 `secrets/google/` 파일을 요구한다.
- `config.json:2`에는 샘플보다 실제 운영값처럼 보이는 키워드가 들어 있고, `docs/config-samples/config.sample.json:2`에는 별도 샘플이 있다.

개선 방향:

- 2FA 샘플 env는 IMAP 앱 비밀번호 정책으로 통일하고 오래된 Gmail OAuth 설명은 legacy 문서로 이동한다.
- 루트 `config.json`이 로컬 파일이면 `.gitignore` 대상과 샘플 복사 절차를 정한다. 계속 추적해야 한다면 값은 완전한 placeholder로 바꾼다.

## 권장 작업 순서

1. P1-01: 운영 env 공개 Admin 기본값과 Telegram secret handoff를 정리한다.
2. P1-02: `JobCompletionService`를 만들어 `jobs.py` 완료 workflow를 라우트 밖으로 옮긴다.
3. P1-03: snapshot ingest DB 저장과 Telegram 실전송을 delivery-log dispatch worker 경계로 분리한다.
4. P2-01: `RuntimeDeps` dataclass로 `main.py`의 `app.state` wiring을 정리한다.
5. P2-02: scheduler due target batch와 bulk query를 도입한다.
6. P2-03: Admin Entity CRUD를 aggregate별 파일로 점진 분리한다.
7. P2-04: Agent worker composition과 `reuse.py` 역할별 port 분리를 진행한다.
8. P2/P3 backlog: `AppConfig` carrier 축소, Docker 의존성 정본화, CI smoke, env 샘플 정리, 상태 계약/정책 상수 정리.

## 검증 원칙

- 배포 env 변경: `.\scripts\test.ps1 quick tests\server\test_deployment_config.py`, `.\scripts\test.ps1 docs`
- 서버 workflow 변경: `.\scripts\test.ps1 quick tests\server\test_jobs_api.py tests\server\test_snapshot_telegram_runtime.py`, `.\scripts\test.ps1 architecture`
- snapshot/dispatch/DB 변경: `.\scripts\test.ps1 quick tests\server\test_snapshot_telegram_runtime.py tests\server\test_telegram_central_dispatch.py`, `.\scripts\test.ps1 architecture`, `TEST_DATABASE_URL` 설정 후 `.\scripts\test.ps1 postgres`
- scheduler 변경: `.\scripts\test.ps1 quick tests\server\test_scheduler_tick.py tests\server\test_scheduler_repository.py`, `TEST_DATABASE_URL` 설정 후 `.\scripts\test.ps1 postgres tests\negative\test_scheduler_idempotency.py`
- 구조 경계 변경: `.\scripts\test.ps1 architecture`
- Agent 조립 변경: `.\scripts\test.ps1 quick tests\agent\test_job_loop.py`, `.\scripts\test.ps1 architecture tests\agent\test_agent_package.py`
- 문서와 샘플 변경: `.\scripts\test.ps1 docs`
