# 리팩토링 컨셉 검토 보고서

> 상태: 역사적 검토 기록입니다. 이 문서는 2026-06-15 당시 코드와 문서를 기준으로 작성된
> gap report이며, 이후 반영된 수정 사항을 모두 재기록한 최신 완료 판정서는 아닙니다.
> 최신 상태 판단은 현재 코드/테스트/deploy 검증을 기준으로 합니다.
> 2026-06-18 최신화: Agent API, 기본 Agent worker wiring, scheduler compose, 일부 DB guard는
> 이후 반영됐습니다. 남은 판단은 live dry-run/test-send/Postgres gate와 운영 리스크 승인 여부를
> 기준으로 합니다.

검토 시점: 2026-06-15 KST
검토 범위: `docs/refactoring/research.md`, `docs/refactoring/detailed_work_order.md`, `riderbot_detailed_work_order.docx`, `riderbot_architecture_diagrams.pptx`, 현재 `src/`, `tests/`, `deploy/`, `docs/qa/`

## 결론

리팩토링은 큰 방향이 맞다. 문서가 요구한 핵심 방향인 `cloud control plane + Windows Local Agent + 수집/전송 분리 + Telegram 중앙화 + Kakao 직렬 queue`에 맞춰 코드가 상당 부분 이동했다.

다만 지금 상태를 "문제없이 완료"로 보기는 어렵다. 더 정확한 판정은 **구조 기반은 강하게 구현됐지만, 판매형 MVP의 live runtime 연결은 아직 미완료**다. 특히 현재 일반 PC Agent가 서버에 등록되고 heartbeat를 올리는 경로, 실제 `CRAWL_BAEMIN`/`CRAWL_COUPANG` 실행 worker, scheduler 별도 실행 프로세스, `Snapshot -> Message -> DeliveryLog` 영속/dispatch 루프가 아직 닫히지 않았다.

가장 큰 차단점은 서버/에이전트 계약 불일치다. 작업 지시서는 `POST /v1/agents/register`, `POST /v1/agents/heartbeat`를 필수 Agent API로 둔다([detailed_work_order.md:468](./detailed_work_order.md:468), [detailed_work_order.md:471](./detailed_work_order.md:471)). 에이전트 클라이언트도 이 경로를 호출한다([registration.py:43](../../src/rider_agent/registration.py:43), [heartbeat.py:56](../../src/rider_agent/heartbeat.py:56)). 그러나 서버 앱은 현재 `/v1`에 `jobs_router`, `telegram_webhook_router`만 붙인다([main.py:351](../../src/rider_server/main.py:351)). `src/rider_server/api/`에도 `jobs.py`, `telegram_webhook.py`만 있다.

## 검토 방식

이번 검토는 세 번의 관점으로 나누어 봤다.

1. 요구사항 대조: `docs/refactoring`의 P1~P4, Agent API, DB 테이블, MVP 완료 기준을 코드와 테스트에 맞췄다.
2. 반증 검토: "완료됐다"는 주장을 깨는 증거를 찾았다. 특히 라우트 누락, stub executor, fail-closed secret resolver, 자동 실행되지 않는 scheduler/dispatch를 확인했다.
3. 운영 준비도 검토: 테스트가 통과해도 실제 고객 운영에서 필요한 registration, heartbeat, snapshot upload, messenger delivery log, live dry-run 근거가 있는지 확인했다.

## 요구사항별 판정

| 영역 | 판정 | 근거 | 남은 문제 |
| --- | --- | --- | --- |
| P1 로컬 설정/식별자 | 대체로 충족 | `UiSettings`에 `customer_id`, `platform_account_id`, `monitoring_target_id`, `*_ref`가 들어왔고([ui_settings.py:63](../../src/rider_crawl/ui_settings.py:63)), `targets/<monitoring_target_id>` state 경로와 atomic write가 구현됐다([ui.py:95](../../src/rider_crawl/ui.py:95), [ui_settings.py:301](../../src/rider_crawl/ui_settings.py:301)). | legacy local secret store는 분리 저장이지만 암호화는 아니다([README.md:106](../../README.md:106)). 로그 rotation은 크기 기준만 확인되며 문서의 용량/날짜 기준 완료에는 못 미친다([log_rotation.py:5](../../src/rider_crawl/log_rotation.py:5), [detailed_work_order.md:201](./detailed_work_order.md:201)). |
| P2 수집/렌더/전송 분리 | 부분 충족 | 서버 서비스 쪽에는 `CrawlService`, `MessageRenderService`, dispatch/idempotency/fan-out 서비스와 테스트가 있다. | live UI의 `run_once()`는 아직 수집, 렌더, 중복 hash, 전송을 한 함수에서 수행한다([app.py:23](../../src/rider_crawl/app.py:23), [app.py:36](../../src/rider_crawl/app.py:36), [app.py:46](../../src/rider_crawl/app.py:46)). `AppConfig` 실행 scope도 아직 `monitoring_target_id` 중심이 아니다([app.py:98](../../src/rider_crawl/app.py:98)). |
| P3 Local Agent | 부분 충족 | `python -m rider_agent`, registration client, heartbeat client, job polling, browser profile manager, Kakao single-consumer worker, DPAPI secure store, autostart가 있다. | 서버의 register/heartbeat API가 없고, 기본 job executor는 모든 실제 crawl job을 `UNSUPPORTED_JOB_TYPE`로 실패시킨다([job_loop.py:234](../../src/rider_agent/job_loop.py:234)). 실제 운영 PC도 아직 등록 완료 상태라는 근거가 없다. |
| P4 중앙 서버 | 부분 충족 | FastAPI 앱, `/health`, `/version`, `/metrics`, DB/Alembic, 14개 테이블, queue, scheduler service, Telegram webhook, Admin/metrics/security가 있다([main.py:225](../../src/rider_server/main.py:225), [main.py:268](../../src/rider_server/main.py:268), [detailed_work_order.md:447](./detailed_work_order.md:447)). | scheduler는 callable service일 뿐 별도 프로세스/컨테이너로 실행되지 않는다. compose에도 scheduler가 후속 placeholder다([docker-compose.yml:22](../../deploy/docker-compose.yml:22)). |
| Telegram 중앙화 | 부분 충족 | `/v1/telegram/webhook`와 `/register <code>` 처리는 구현됐다([telegram_webhook.py:37](../../src/rider_server/api/telegram_webhook.py:37), [telegram_webhook.py:124](../../src/rider_server/api/telegram_webhook.py:124)). | 기존 UI 경로에는 `getUpdates` poller가 남아 있고, 중앙 outbound dispatch loop와 실제 `DeliveryLog` 기록은 live runtime으로 묶이지 않았다. `CentralTelegramSender`도 자신은 outbound adapter와 충돌 검출만 한다고 범위를 제한한다([telegram_central_dispatch.py:13](../../src/rider_server/services/telegram_central_dispatch.py:13)). |
| Kakao 직렬 전송 | 부분 충족 | Agent의 `KakaoSenderWorker`는 FIFO queue와 단일 consumer thread로 직렬 처리한다. | 기존 `rider_crawl` UI 경로는 아직 `KakaoSendJob -> DeliveryLog` 구조가 아니고, 문서가 요구한 상세 오류 분류와 sanitized diagnostic 규칙이 완전히 닫히지 않았다([detailed_work_order.md:395](./detailed_work_order.md:395), [detailed_work_order.md:404](./detailed_work_order.md:404)). |
| 인증/재인증 | 부분 충족 | 배민은 자동 우회가 아니라 사람 개입형 `AUTH_CHECK`/`OPEN_AUTH_BROWSER` 방향이고, 쿠팡 Gmail 2FA는 mailbox token ref와 lock을 둔다. | 배민 상태 머신은 문서의 `USER_ACTION_PENDING` 단계를 명시적으로 갖지 않는다. 실제 OS/browser auth probe 기본 구현도 아직 주입 seam 수준이다([module-architecture.md:204](../module-architecture.md:204)). |
| 보안/secret | 부분 충족 | UI JSON에는 `*_ref`만 남기고, redaction/DPAPI/secret ref 방향은 맞다. | 서버의 기본 Telegram secret resolver는 실제 secret store가 없어 `None`을 반환하는 fail-closed 상태다([main.py:163](../../src/rider_server/main.py:163)). 운영 Secrets Manager resolver가 필요하다. |
| QA/회귀 기준 | 부분 충족 | 기준선 tag/백업/런북/pytest baseline 문서가 있고, 현재 전체 테스트는 통과한다. | live dry-run 문서의 실제 sha256와 캡처 일시는 `<운영자 캡처 필요>` placeholder다([dry-run-baseline-20260613.md:31](../qa/dry-run-baseline-20260613.md:31)). 즉 절차는 있으나 운영 실측 증거는 아직 없다. |
| 배포/패키징 | 부분 충족 | Dockerfile은 `PYTHONPATH=/app/src`로 server source를 복사해 uvicorn을 실행한다([Dockerfile.server:23](../../deploy/Dockerfile.server:23), [Dockerfile.server:30](../../deploy/Dockerfile.server:30)). | `pyproject.toml` wheel package는 `src/rider_crawl`만 포함한다([pyproject.toml:51](../../pyproject.toml:51)). Docker 경로는 우회하지만 wheel 기반 배포에서는 `rider_server`/`rider_agent`가 빠질 수 있다. |

## 잘 진행된 점

- 문서의 금지 방향은 대체로 지켜졌다. 탭을 100개로 늘리는 대신 ID 모델과 서버/Agent 구조를 만들었고, 배민 휴대폰 인증 우회를 시도하지 않았으며, Kakao 병렬 전송 대신 직렬 worker 방향을 잡았다([detailed_work_order.md:14](./detailed_work_order.md:14)).
- P1의 핵심인 ID 발급, `targets/<monitoring_target_id>` state 경로, atomic write, secret ref 분리는 실제 코드와 테스트로 확인된다.
- P4의 control plane은 단순 스캐폴드가 아니다. FastAPI, async DB/Alembic, 14개 테이블, queue backend, scheduler policy/service, Admin UI, metrics, Telegram webhook까지 의미 있는 구현이 있다.
- 전체 테스트는 현재 `.venv\Scripts\python.exe -m pytest -q` 기준 `1977 passed, 51 skipped`로 통과했다. 단위/서비스 경계의 회귀 방어는 강한 편이다.
- `docs/module-architecture.md`가 현재 상태를 솔직히 적고 있다. Epic 5는 control plane을 제공하지만 collect/render/dispatch loop는 아직 자동 실행 runtime이 아니라고 명시한다([module-architecture.md:271](../module-architecture.md:271)).

## 완료 판정을 막는 핵심 갭

### 1. Agent register/heartbeat 서버 API가 없다

문서의 MVP 완료 기준은 "현재 일반 PC Agent가 중앙 서버에 등록되고 heartbeat를 보고한다"이다([detailed_work_order.md:571](./detailed_work_order.md:571)). 에이전트 코드는 그 API를 호출하도록 만들어졌지만, 서버는 아직 받지 못한다. 이 상태에서는 P3-02, P3-03, MVP 완료 기준을 만족했다고 볼 수 없다.

개선 방향:
- `src/rider_server/api/agents.py`를 추가해 `POST /v1/agents/register`, `POST /v1/agents/heartbeat`를 구현한다.
- registration code 검증, agent token 발급/저장, `agents.last_heartbeat_at`, `capacity_json`, `browser_profiles`, `kakao_status` 갱신을 같은 DB 트랜잭션 경계로 묶는다.
- `rider_agent.registration`/`heartbeat`와 실제 FastAPI route의 contract test를 추가한다.

### 2. 실제 crawl job 실행 worker가 닫히지 않았다

`rider_agent.job_loop.default_execute_job()`는 기본적으로 모든 job type을 unsupported로 처리한다([job_loop.py:234](../../src/rider_agent/job_loop.py:234)). `docs/module-architecture.md`도 Agent가 의존하는 `workers/crawl_worker.py` collection executor와 실제 auth probe binding이 아직 없다고 말한다([module-architecture.md:204](../module-architecture.md:204)).

개선 방향:
- `CRAWL_BAEMIN`, `CRAWL_COUPANG` executor를 `BrowserProfileManager`와 기존 crawler/parser/renderer 재사용 경계에 연결한다.
- 결과를 서버가 소비할 수 있는 `Snapshot` payload로 올리고, 실패는 `AUTH_REQUIRED`, parser failure, timeout, profile mismatch 등으로 분리한다.
- 기본 executor가 운영 경로에 남지 않도록 startup composition에서 실제 executor map을 주입한다.

### 3. collect -> render -> dispatch runtime이 자동으로 돌지 않는다

서버에는 `Snapshot`, `Message`, `DeliveryLog` 모델과 fan-out/idempotency 서비스가 있지만, `jobs.complete(result_json)` 뒤에 snapshot/message/delivery log를 만드는 live ingest/dispatch loop는 확인되지 않았다. 현재 jobs API는 `result_json`을 queue backend complete에 넘기는 수준이다([jobs.py:153](../../src/rider_server/api/jobs.py:153), [jobs.py:174](../../src/rider_server/api/jobs.py:174)).

개선 방향:
- job complete 이후 server-side ingest service를 호출해 `snapshots`, `messages`를 만들고 `DeliveryRule` fan-out으로 dispatch jobs/logs를 만든다.
- Telegram outbound sender와 Kakao sender job 결과를 모두 `DeliveryLog`에 남긴다.
- retry/backoff와 non-retryable failure를 `DeliveryFailurePolicy` 기준으로 DB에 기록한다.

### 4. Scheduler는 구현됐지만 배포 runtime이 아니다

Scheduler service는 due target, subscription gate, breaker, capacity, jitter, idempotent enqueue를 조합한다. 그러나 패키지 설명과 compose가 모두 "별도 process/loop 배선은 후속"이라고 말한다([scheduler/__init__.py:8](../../src/rider_server/scheduler/__init__.py:8), [docker-compose.yml:22](../../deploy/docker-compose.yml:22)).

개선 방향:
- `python -m rider_server.scheduler` 또는 별도 worker entrypoint를 만들고, 주기 tick을 실행한다.
- compose에 scheduler service를 추가하고 health/metrics를 붙인다.
- scheduler가 만든 job이 Agent claim/complete/ingest까지 이어지는 integration test를 추가한다.

### 5. 운영 secret resolver가 없다

서버는 `*_ref` 방향은 맞지만, 기본 resolver가 fail-closed다([main.py:163](../../src/rider_server/main.py:163)). 이는 안전하지만, 운영에서는 Telegram webhook secret, bot token, platform credential ref를 실제 secret으로 풀어야 한다.

개선 방향:
- AWS Secrets Manager 또는 선택한 secret backend adapter를 `resolve_telegram_secret`, Telegram send token resolver, platform credential resolver에 붙인다.
- secret 값이 로그, DB text, error envelope, screenshot artifact에 남지 않는 negative test를 유지한다.
- legacy `secrets.local.json`은 local-only 전환 단계라고 문서에 명확히 표시하거나 OS 암호화 store로 옮긴다.

### 6. 운영 실측 QA가 비어 있다

문서화와 automated test는 좋지만, live dry-run 기준선은 운영자 캡처 placeholder다([dry-run-baseline-20260613.md:31](../qa/dry-run-baseline-20260613.md:31)). 작업 지시서의 테스트 기준은 기존 활성 배민/쿠팡 target dry-run, Telegram test chat, Kakao test room, DeliveryLog 기록까지 요구한다([detailed_work_order.md:545](./detailed_work_order.md:545)).

개선 방향:
- 운영 PC에서 배민 1개, 쿠팡 1개 target의 live dry-run sha256와 캡처 시간을 채운다.
- Telegram test chat과 Kakao test room으로 실제 test-send를 실행하고 DeliveryLog까지 남긴다.
- Postgres 빈 DB migration, staging tenant smoke, fake target 100개 load smoke를 CI 또는 release checklist에 넣는다.

## 우선순위별 개선 계획

1. **계약 복구**: `/v1/agents/register`, `/v1/agents/heartbeat` 서버 route를 먼저 구현한다. 이게 없으면 Agent #1 운영 검증이 시작되지 않는다.
2. **Agent 실행 닫기**: `CRAWL_BAEMIN`/`CRAWL_COUPANG` executor와 auth probe binding을 실제 Chrome/session 흐름에 연결한다.
3. **서버 runtime 연결**: scheduler process, job complete ingest, message render, dispatch worker, delivery log 기록을 하나의 실행 흐름으로 묶는다.
4. **전송 cutover**: Telegram은 중앙 send-only 기본값으로 바꾸고 legacy poller는 명시적 legacy mode로 격리한다. Kakao는 현재 lock을 유지하면서 queue item, 상세 오류 코드, sanitized artifact, DeliveryLog를 붙인다.
5. **secret 운영화**: `*_ref`를 실제 secret backend로 해석하는 resolver를 붙이고, legacy local plaintext store의 범위를 문서와 코드로 제한한다.
6. **검증 채우기**: live dry-run, messenger test, Postgres migration, 100-target load smoke, packaging check를 release gate로 만든다.
7. **문서 정합화**: README의 쿠팡 URL 설명처럼 현재 코드와 어긋난 운영 문서를 고친다([README.md:13](../../README.md:13), [.env.example:10](../../.env.example:10)).
8. **배포 방식 결정**: Docker가 정본이면 `PYTHONPATH=/app/src` 방식을 문서화하고, wheel 배포도 할 계획이면 `pyproject.toml` package 목록에 `rider_server`, `rider_agent`를 포함한다.

## 최종 판정

현재 리팩토링은 **컨셉을 잘못 이해해서 엉뚱한 방향으로 간 상태는 아니다.** 오히려 문서의 핵심 위험을 잘 반영해 ID 모델, 중앙 서버, Agent, queue, scheduler, secret ref, Kakao 직렬화, Telegram webhook 쪽으로 바르게 진행됐다.

하지만 **판매형 MVP 완료 상태도 아니다.** 지금은 control plane과 여러 핵심 primitive가 구현된 상태이며, 실제 운영 자동화의 마지막 연결부가 빠져 있다. 완료 판정은 최소한 다음 네 가지가 된 뒤에 내려야 한다.

- Agent가 서버에 등록되고 heartbeat가 Admin/metrics에 반영된다.
- 서버 scheduler가 CrawlJob을 만들고 Agent가 실제 배민/쿠팡 crawl을 실행해 Snapshot을 업로드한다.
- 서버가 Snapshot을 Message/DeliveryRule/DeliveryLog로 fan-out하고 Telegram/Kakao 전송 결과를 기록한다.
- 운영 PC에서 live dry-run과 test-send 결과가 문서와 DB에 남는다.

따라서 개선 방향은 새 아키텍처를 다시 바꾸는 것이 아니라, 이미 만든 조각들을 **운영 흐름으로 연결하고 실측 증거를 채우는 것**이다.
