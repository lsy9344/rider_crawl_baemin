---
baseline_commit: b6d156f
---

# Story 4.4: outbound HTTPS job polling/claim/complete와 lease

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 작업 노드 관리자,
I want Agent가 방화벽 inbound 개방 없이 **outbound HTTPS로만** 서버 job 을 `POST /v1/jobs/claim`으로 claim 하고, 실행한 뒤 `POST /v1/jobs/{job_id}/complete`로 결과를 보고하며, 진행 이벤트는 `POST /v1/jobs/{job_id}/events`로 redact 된 본문으로 올리는 **job 폴링/claim/complete 루프 primitive + lease 인지(client) + startup 배선(`start_heartbeat_thread()` + 메인 run 루프)** 를 갖고 싶다,
so that 운영자 PC 에 inbound 포트를 열지 않고도 안전하게 작업을 수신·실행·보고하고, **claim 한 job 만** 실행하며, **lease(만료시각 부여 + heartbeat active_jobs 로 연장 + 만료 시 서버 stale 회수)** 로 두 Agent 가 같은 job 을 동시에 성공 처리하지 않는다(P3-04, FR-13·16, ADD-5·6, NFR-5).

> **이 스토리의 성격 — "outbound job 루프 client(claim/complete/events) + lease 인지(client) + startup 배선"만.** 실제 job 실행(crawl/kakao/auth)도, 서버 측 queue/단일-claim 강제/stale 회수/재할당도 아니다. P3-04 deliverable 은 **"Implement HTTPS outbound job polling, claim, and complete loop."** → **"Agent works behind firewall without inbound port."** 가 전부다(implementation-contract P3-04:61). architecture-contract Agent Loop(88-107)의 `main_loop`(`claim_next_job → execute_job → complete_job`)와 startup 의 `start_heartbeat_thread()` 가 본 스토리의 master 계약이다. **실제 `execute_job` 워커는 후속 소유**: `CRAWL_BAEMIN`/`CRAWL_COUPANG`=4.5(BrowserProfileManager)·crawl 워커, `KAKAO_SEND`=4.6(KakaoSenderWorker), `AUTH_CHECK`/`OPEN_AUTH_BROWSER`=4.8/4.9. **서버 측 job 생성·queue(`FOR UPDATE SKIP LOCKED`)·lease 강제/연장/stale sweep/재할당·Admin 표시는 Epic 5 소유**(`rider_server/queue/`, `rider_server/api/jobs.py`). 본 스토리는 4.1 토대 + 4.2 identity/transport seam + 4.3 heartbeat reporter 위에 **`job_loop.py` + `tests/agent/test_job_loop.py`** 를 얹고, **`__main__.py` 에 thin `run` 서브커맨드**(4.2 `register` 미러)만 추가한다. [Source: implementation-contract.md P3-04(61), architecture-contract.md Agent-Loop(87-107)·Job-Types(120-129), epics.md Story 4.4(760-785)·4.5~4.9(787-903), architecture.md(451)]
>
> **서버가 아직 없다 — "서버 stub/mock 에 대한 동작 검증"이 4.x 테스트 형태(절대 전제, 4.1·4.2·4.3 계승).** `POST /v1/jobs/claim`·`/complete`·`/events` 의 **서버 측 수신·queue·`jobs` 테이블·lease 강제/연장/stale 회수/재할당·Admin 표시는 Epic 5 소유**(FastAPI/PostgreSQL). 따라서 본 스토리는 실제 HTTP 서버를 띄우지 않고 **주입된 fake transport**(canned claim 응답 = job 목록 + `lease_expires_at`; complete/events 는 2xx 또는 409/410/401 stub)에 대해 claim→execute→complete 흐름, lease 인지, best-effort 복원력, secret 비노출, token 게이트를 검증한다. epic-3-retro(108): "Epic 4 는 서버 측 job 생성·queue·Admin 이 Epic 5 라 **서버 stub/mock 에 대한 동작 검증**이 4.x 의 테스트 형태." [Source: epic-3-retro-2026-06-13.md(108), data-api-contract.md(71-81·36), 4-3 스토리(19·107)]
>
> **lease 소유 분리(가장 헷갈리는 경계).** AC2/AC3 의 lease 책임은 **서버(Epic 5)** 와 **Agent(client, 4.4)** 로 갈린다. **서버 소유**: (a) claim 시 `lease_expires_at` 부여, (b) 단일-claim 강제(`FOR UPDATE SKIP LOCKED`), (c) heartbeat `active_jobs` 수신 시 lease 연장, (d) lease 만료 stale sweep·timeout 재할당/실패. **Agent(4.4 client) 소유 — 4가지뿐**: (a) claim 응답의 `lease_expires_at` 를 **기록**, (b) in-flight job_id 를 **heartbeat `active_jobs` provider 로 노출**해 서버가 연장하게 한다(= "heartbeat 로 연장"의 client 측 배선), (c) complete/success 직전 **자기 lease 만료 여부를 clock 으로 self-check** 해 만료면 성공 보고하지 않고 abandon/surfacing(서버 단일-claim 과 함께 "두 Agent 동시 성공" 이중 방어), (d) 서버가 complete 를 **거부(409/410 = lease lost/이미 재할당)** 하면 crash 없이 흡수·기록(이미 다른 Agent 소유). **Agent 가 스스로 재할당하거나 다른 Agent 의 lease 를 만료시키지 않는다** — 그건 서버. [Source: epics.md AC(773-781), architecture-contract.md(87-107), architecture.md(180·475-496), data-api-contract.md(36·73)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **거의 순수 additive**다: 신규 `src/rider_agent/job_loop.py` + 신규 `tests/agent/test_job_loop.py`, 그리고 `src/rider_agent/__main__.py` 에 **thin `run` 서브커맨드**(4.2 `register` 와 동형 — 인자 파싱 → 실제 deps 주입 → `run_agent` 호출, 배너·`register` 보존). 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(절대 규칙·무회귀 안전 마진).** `rider_crawl.redaction`(`redact`/`redacted_error_event`/`REDACTED`)만 **import 해서 재사용**한다. epic-3-retro(158): "**`rider_crawl` 0줄**이 안전 마진." [Source: project-context.md(64·82), epic-3-retro-2026-06-13.md(158)]
> - **`pyproject.toml` `[project].dependencies` — 0줄 변경(권장 설계로 달성).** HTTPS 는 stdlib `urllib`(4.2 `HttpTransport` 재사용), 루프는 stdlib `threading`/`time`. **새 third-party 의존을 추가하지 않는다** — 추가하면 4.1 이 잠근 `tests/agent/test_agent_package.py` 의 (a) "third-party root == `{rider_crawl}`", (b) "deps 정확히 9개" 가드가 **둘 다** 깨진다(memory: enum-member-count-locks 와 동형). [Source: project-context.md(24), tests/agent/test_agent_package.py(194-213)]
> - **`registration.py`·`secure_store.py`·`heartbeat.py` — 0줄 변경(reuse only).** 4.3 이 이미 `Transport.post_json(..., headers=...)`·`HttpTransport(op_label=...)` 후방호환 인자를 깔았고, `HeartbeatReporter` 가 `active_jobs_provider` 를 받는다. 4.4 는 이 seam 들을 **그대로 호출/주입**한다 — 시그니처 변경·새 인자 추가 불필요. [Source: src/rider_agent/registration.py(75-141), src/rider_agent/heartbeat.py(254-308)]
> - **실제 job 실행(`execute_job` 워커)** → **4.5/4.6/4.8/4.9.** `JobRunner` 는 `execute_job: Callable[[ClaimedJob], JobResult]` 를 **주입**받는다. 기본 executor 는 **"미지원 job type → 실패 결과(error_code=`UNSUPPORTED_JOB_TYPE`)"** 를 돌려 루프가 complete 로 깔끔히 보고하게 한다 — **빈 stub 워커 파일(`workers/`·`browser_profile.py`·`auth/`)을 만들지 않는다**(4.3 의 provider seam 규율과 동형). [Source: epics.md Story 4.5~4.9(787-903), architecture.md(452-457), 4-3 스토리(58)]
> - **서버 측 queue/단일-claim/lease 강제·연장·stale sweep·재할당·job 생성·Admin** → **Epic 5.** 미생성. 본 스토리는 client + 주입 transport stub. [Source: architecture.md(431·437), epics.md Epic 5(904-)]
> - **autostart / 배민·쿠팡 auth / pull_remote_config·commands 적용** → **4.7 / 4.8·4.9 / Epic 5.** architecture-contract `main_loop` 의 `pull_remote_config()` 는 본 스토리에서 **claim 폴링으로 충분**하며 별도 config/commands 적용 로직을 만들지 않는다(heartbeat 가 이미 `config_version`/`commands` 를 surfacing — 적용은 Epic 5). [Source: architecture-contract.md(96-107), epics.md Story 4.7(833-)·Epic 5, 4-3 스토리 AC1.3]
>
> **secret 비노출(ADD-15·NFR-5 — 본 스토리의 핵심 가드).** claim/complete/events 는 **agent_token 으로 인증**한다(Agent API = token-auth, architecture 476·179). token 은 **Authorization 헤더에만** 싣고 **로그·payload 본문·예외 메시지·에러 이벤트·artifact 어디에도 평문으로 남기지 않는다.** 헤더 dict 를 통째로 로깅하지 않는다. **`/events` 본문은 `message_redacted`, `/complete` 본문은 `error_message_redacted`** 만 쓰고 raw error/OTP/secret/HTML 을 넣지 않는다(operations-security 19·93, forbidden 93). artifact 는 **sanitized ref 만**(raw HTML/마스킹 안 된 스크린샷 금지 — forbidden). 로그/에러는 `redact`/`redacted_error_event` 통과. 테스트 fixture·docstring 에 실제 토큰을 넣지 않고 명백한 가짜값(`agtok-fake-…`)만 쓴다. [Source: project-context.md(81), operations-security-test-contract.md(14-19·93·87-95), architecture.md(183-185·476), epic-3-retro-2026-06-13.md(109·118)]
>
> **sync 런타임 + 단방향 import(4.1 규약 계승 — 자동 검증됨).** 신규 `job_loop.py` 는 **순수 동기**(no `async def`/`await`/직접 `import asyncio`)이고 `rider_crawl`/자기 패키지만 import 한다(역방향 0, `rider_server` import 0). 루프/주기 대기는 `asyncio` 가 아니라 **`threading.Event`(stop) + 주입 가능한 `sleep`/`now`** 로 짠다. 4.1 이 `src/rider_agent/*.py` **전체를 glob** 하는 AST 가드로 검사하므로 신규 모듈도 자동 적용된다 — 규약을 깨면 4.1 테스트가 실패한다. [Source: 4-1 스토리 AC3·AC5, tests/agent/test_agent_package.py(176-233), architecture.md(484), memory/negative-guard-tests-use-ast]

## Acceptance Criteria

**AC1 — outbound HTTPS only claim/complete + "claim 한 job 만 실행" + token 게이트 (P3-04, FR-13·16, ADD-6)**

1. **Given** 서버에 처리할 job 이 있을 때 **When** Agent 가 `POST /v1/jobs/claim`(본문: `agent_id`·`capabilities`·`max_jobs`)으로 claim 하고 실행 후 `POST /v1/jobs/{job_id}/complete`(본문: `status`·`result_json`·`error_code`·`error_message_redacted`·`metrics`)로 완료를 보고하면 **Then** **inbound 포트 개방 없이 outbound HTTPS(주입 transport = 4.2 `Transport`/`HttpTransport`, stdlib `urllib`)만으로** job 수신·결과 보고가 이뤄지고, **And** Agent 는 **claim 응답이 돌려준 job 만** 실행한다(임의 job 생성·실행 금지). 본 스토리 테스트는 실제 서버 없이 fake transport 의 canned 응답으로 검증한다. [Source: data-api-contract.md(71-81), architecture.md(191·476), epics.md AC(768-771)]
2. **And** claim 전 **token 게이트(4.2 `validate_agent_token`)** 를 통과해야 한다: identity 없음/token 없음/revoke(`needs_registration`)면 claim 을 보내지 않고(=job 미수신, FR-16) 재등록 필요로 surfacing 한다. `capabilities` 는 4.3 의 `DEFAULT_CAPABILITIES`(6 job type)를 기본값으로 쓰되 **주입 가능**하다. [Source: src/rider_agent/secure_store.py(303-338·50-54), src/rider_agent/heartbeat.py(70-77), epics.md AC(771), 4-2 스토리(11-12·24)]
3. **And** **단발 claim/execute/complete 실패가 루프 thread 를 죽이지 않는다(best-effort)**: 한 주기의 transport 실패(`TransportError` 네트워크/5xx)나 executor 예외가 나도 루프는 **다음 주기로 진행**하고, 에러는 `redact`/`redacted_error_event` 로 마스킹해 기록하며 token 을 노출하지 않는다. `401`/revoke 응답은 **재등록 필요 상태로 surfacing**(4.2 `TOKEN_STATUS_REVOKED`/`needs_registration` 어휘 재사용)하되 **crash·무한 즉시 스핀 없이**(매 주기 sleep) 처리한다. [Source: operations-security-test-contract.md(93), src/rider_agent/heartbeat.py(310-364 복원력 선례), src/rider_crawl/redaction.py(130·248)]

**AC2 — lease: 만료시각 기록 + heartbeat active_jobs 연장 배선 + 동시 성공 금지 (ADD-5, FR-13)**

4. **Given** 두 Agent 가 같은 job 을 동시에 가져가면 안 될 때 **When** Agent 가 job 을 claim 하면 **Then** claim 응답의 **`lease_expires_at`(서버 부여) 를 client 가 기록**하고, **And** in-flight job_id 목록을 **heartbeat 의 `active_jobs` provider 로 노출**해(4.3 `HeartbeatReporter(active_jobs_provider=...)` 에 배선) 서버가 heartbeat 수신 시 lease 를 연장할 입력을 제공한다(="heartbeat 로 연장"의 client 측 배선). **단일-claim 강제(`FOR UPDATE SKIP LOCKED`)·실제 lease 연장은 서버(Epic 5) 소유.** [Source: epics.md AC(773-776), architecture-contract.md(87-107·93), src/rider_agent/heartbeat.py(254-267·263), architecture.md(431·493)]
5. **And** complete/success 보고 **직전에 client 가 자기 lease 만료 여부를 주입 가능한 clock(`now`) 으로 self-check** 한다: 자기 lease 가 이미 만료되었으면 **성공으로 complete 하지 않고**(서버가 stale 회수했을 수 있음) abandon/surfacing 하며, 서버가 complete 를 **거부(예: 409/410 = lease lost·이미 재할당)** 하면 **crash 없이 흡수·기록**한다. 이로써 서버 단일-claim 과 함께 **두 Agent 가 같은 job 을 동시에 성공 처리하지 않는다**(이중 방어). [Source: epics.md AC(776), data-api-contract.md(73), operations-security-test-contract.md(93)]

**AC3 — lease 만료 시 결과 보고 필드 + 재할당/실패는 서버 (FR-13, ADD-6)**

6. **Given** Agent 가 작업 중 죽거나 lease 가 만료될 수 있을 때 **When** Agent 가 job 완료(또는 실패)를 보고하면 **Then** `complete` 본문에 **실행 Agent(`agent_id`)·시작/종료 시각(`started_at`/`finished_at`)·상태(`status`)·실패 사유(`error_code`·`error_message_redacted`)·`result_json`·`metrics`** 가 포함되고, **And** lease 만료로 인한 **timeout 후 재할당·실패 상태 전이는 서버(Epic 5) 소유**다 — Agent 는 만료를 감지(AC2.5)해 잘못된 성공 보고를 하지 않을 뿐, 스스로 재할당하지 않는다. `error_message_redacted` 는 `redact`/`redacted_error_event` 로 생성해 secret/OTP/raw error 평문이 없다. [Source: epics.md AC(778-781), data-api-contract.md(79-81), operations-security-test-contract.md(19·93), architecture.md(180)]

**AC4 — job events: redact 된 진행 이벤트 보고 (ADD-6, NFR-5)**

7. **Given** job 이벤트(시작/진행/진단)를 보고할 때 **When** Agent 가 `POST /v1/jobs/{job_id}/events` 를 호출하면 **Then** 본문에 **`event_type`·`severity`·`message_redacted`·artifact ref** 가 전달되고, **And** 본문에 **secret/OTP/raw error/raw HTML 이 포함되지 않는다**(`message_redacted` 는 `redact` 통과, artifact 는 sanitized ref 만 — raw HTML·마스킹 안 된 스크린샷 금지). `event_type`/`severity` 는 호출자 제공 문자열이며 **"정확히 N개" enum lock 으로 잠그지 않는다**(후속 워커가 늘려도 무탈). 본 스토리는 `emit_job_started`(claim 직후) 같은 최소 호출만 배선하고, 풍부한 진단 이벤트는 워커(4.5+) 소유. [Source: data-api-contract.md(75-77), operations-security-test-contract.md(14-19·87-95), epics.md AC(783-785), memory/enum-member-count-locks]

## Tasks / Subtasks

- [x] **Task 1 — `job_loop.py`: claim/complete/events HTTP client + 도메인 모델 + Bearer auth + redaction (AC: 1, 3, 4)**
  - [x] `src/rider_agent/job_loop.py` 신설. 경로 상수: `CLAIM_PATH = "/v1/jobs/claim"`, `_events_url(base, job_id)`·`_complete_url(base, job_id)`(`/v1/jobs/{job_id}/events`·`/complete`) — 4.2 `_register_url`·4.3 `_heartbeat_url`(base_url > `RIDER_AGENT_SERVER_URL` env > `DEFAULT_SERVER_BASE_URL`) 패턴과 정합. [Source: data-api-contract.md(71-81), src/rider_agent/registration.py(43-48·183-185), src/rider_agent/heartbeat.py(56·169-172)]
  - [x] 도메인 dataclass(frozen): `ClaimedJob`(`job_id`·`type`·`target_id`·`lease_expires_at`·`payload`/raw dict) / `JobResult`(`status`·`result_json`·`error_code`·`error_message_redacted`·`started_at`·`finished_at`·`agent_id`·`metrics`) / `JobEvent`(`event_type`·`severity`·`message_redacted`·`artifact_refs`). **token 필드 없음**(인증은 헤더). `from_response` 파서는 누락/비-dict 응답에 fail-closed(빈 리스트/None 방어 — 4.3 `_result_from_response` 선례). [Source: data-api-contract.md(73·77·81), src/rider_agent/heartbeat.py(181-187)]
  - [x] job status 는 **평문 상수**(`JOB_STATUS_SUCCESS = "success"`·`JOB_STATUS_FAILED = "failed"`, 필요 시 client-side `JOB_STATUS_LEASE_LOST`) — enum/"정확히 N개" lock 금지(`TOKEN_STATUS_*`/`DEFAULT_CAPABILITIES` 선례). [Source: src/rider_agent/secure_store.py(50-54), src/rider_agent/heartbeat.py(58-77), memory/enum-member-count-locks]
  - [x] client 함수: `claim_jobs(identity, *, transport, capabilities=DEFAULT_CAPABILITIES, max_jobs=1, base_url=None) -> list[ClaimedJob]` / `complete_job(identity, job_id, result, *, transport, base_url=None)` / `emit_job_event(identity, job_id, event, *, transport, base_url=None)`. 각자 `Authorization: Bearer <token>` 헤더(4.3 와 동일 Bearer 패턴 — `{"Authorization": f"Bearer {identity.agent_token}"}`)로 인증하고 `transport.post_json(url, body, headers=...)` 호출. **헤더는 로그·예외에 통째로 출력하지 않는다.** [Source: src/rider_agent/heartbeat.py(175-178·217-221), architecture.md(179·476)]
  - [x] **redaction(AC4·AC3):** `JobEvent.message_redacted`·`JobResult.error_message_redacted` 는 호출 시 `redact`/`redacted_error_event` 를 통과해 만든다(raw error/OTP/secret/HTML 비포함). artifact 는 **sanitized ref 만** 받는 계약(raw 본문 미수용). [Source: src/rider_crawl/redaction.py(130·248), operations-security-test-contract.md(19·87-95)]
  - [x] transport 비-2xx 는 4.2 `HttpTransport` 정책 계승(본문 미읽음·`TransportError(status_code)` 만). jobs 호출은 `HttpTransport(op_label="agent jobs")` 로 운영 로그 구분(4.3 op_label seam 재사용 — registration.py 무변경). [Source: src/rider_agent/registration.py(84-141·103-141), 4-3 스토리(184)]
  - [x] 자기 코드 **순수 동기 + `rider_crawl`/자기 패키지만 import** — 4.1 AST 가드 자동 검사. [Source: tests/agent/test_agent_package.py(176-233)]
- [x] **Task 2 — `job_loop.py`: `JobRunner` 루프 primitive(claim→execute→complete·short-poll·token 게이트·best-effort·executor seam) (AC: 1, 3)**
  - [x] `JobRunner`(stop `threading.Event` + 주입 `sleep`(기본 `time.sleep`) + 주입 `now`(기본 `time.time`) + 주입 `execute_job: Callable[[ClaimedJob], JobResult]` + `transport`/`identity`/`capabilities`/`max_jobs`/`short_poll_interval_seconds`/`token_check`/`on_status`/`log`). `run()` 은 architecture-contract `main_loop`(97-106)을 구현: `while not stop: job = claim; if none → sleep(short_poll) → continue; emit_job_started; result = execute_job(job); complete_job`. **`asyncio` 금지** — `threading.Event` + 주입 sleep 로 결정적 테스트. [Source: architecture-contract.md(96-106), src/rider_agent/heartbeat.py(300-308 run-loop 선례)]
  - [x] **token 게이트(AC1.2):** 매 claim 전 `validate_agent_token(identity, server_check=...)` 로 게이트 — `can_receive_jobs` 가 아니면 claim 생략·`needs_registration` surfacing(4.2 어휘). 실제 server_check 경로는 주입(stub). [Source: src/rider_agent/secure_store.py(303-338), 4-2 스토리(11-12·24)]
  - [x] **claim 한 job 만 실행(AC1):** `claim_jobs` 가 돌려준 `ClaimedJob` 만 `execute_job` 에 넘긴다. job 없으면 `short_poll_interval` 만큼 sleep 후 재폴링(폴링 = `pull_remote_config` 대체 — 별도 config 적용 안 함). [Source: architecture-contract.md(99-101), epics.md AC(771)]
  - [x] **best-effort 복원력(AC1.3 — 핵심):** 한 주기의 `TransportError`/executor 예외/complete 실패가 루프를 죽이지 않고 다음 주기로 진행. 에러는 `redacted_error_event`+`redact` 마스킹 기록. `401`/revoke 는 `TOKEN_STATUS_REVOKED`/`on_status` surfacing·매 주기 sleep(무한 스핀 없음) — 4.3 `report_once`/`_handle_transport_error` 패턴 그대로. [Source: src/rider_agent/heartbeat.py(310-364), operations-security-test-contract.md(93)]
  - [x] **기본 executor(워커 미생성):** `default_execute_job(job) -> JobResult`(status=`failed`, `error_code="UNSUPPORTED_JOB_TYPE"`, redact 된 사유)를 둔다 — 후속 워커(4.5/4.6/4.8/4.9)가 type 별 executor 를 주입한다. **빈 stub 워커 파일을 만들지 않는다**(4.3 provider seam 규율). [Source: epics.md Story 4.5~4.9, architecture.md(452-457), 4-3 스토리(58·95)]
- [x] **Task 3 — `job_loop.py`: lease 인지(client) — 기록·heartbeat active_jobs 노출·self-check·서버 거부 흡수 (AC: 2, 3)**
  - [x] **lease 기록(AC2.4):** claim 응답의 `lease_expires_at` 를 `ClaimedJob` 에 보존하고, runner 가 in-flight job 집합(예: `dict[job_id, ClaimedJob]`)을 유지한다. claim 시 추가, complete/abandon 시 제거(thread-safe — heartbeat thread 가 동시 읽음). [Source: epics.md AC(773-775), data-api-contract.md(73)]
  - [x] **heartbeat active_jobs provider(AC2 — 핵심 배선):** `runner.active_jobs()` 가 in-flight job 의 식별 목록(예: `job_id` 리스트 또는 `{"job_id":…, "lease_expires_at":…}`)을 돌려주는 **callable** 을 노출한다. Task 4 가 이를 `HeartbeatReporter(active_jobs_provider=runner.active_jobs)` 로 배선해 "heartbeat 로 lease 연장"을 완성한다(4.3 이 비워둔 `active_jobs` 실제 소스). [Source: src/rider_agent/heartbeat.py(119·135-137·263), 4-3 스토리(26·58)]
  - [x] **lease self-check(AC2.5):** complete/success 직전 `now() < lease_expires_at` 를 확인. 만료면 성공 보고 대신 abandon(또는 `JOB_STATUS_LEASE_LOST` 로 surfacing)하고 in-flight 에서 제거 — 서버가 회수했을 수 있으므로 이중 성공을 막는다. lease 시각 파싱 실패는 fail-closed(보수적 처리). [Source: epics.md AC(776), architecture.md(180)]
  - [x] **서버 거부 흡수(AC2.5):** complete 가 `409`/`410`(lease lost/이미 재할당) 등 비-2xx 면 crash 없이 흡수·`redacted_error_event` 기록·in-flight 제거(다른 Agent 소유로 본다). 재할당/실패 전이는 서버 소유라 client 는 보고만. [Source: data-api-contract.md(73), operations-security-test-contract.md(93)]
  - [x] **결과 필드(AC3):** `JobResult` 에 `agent_id`(=`identity.agent_id`)·`started_at`/`finished_at`(주입 `now` 로 측정)·`status`·`error_code`·`error_message_redacted`·`metrics` 채움. [Source: data-api-contract.md(81), epics.md AC(781)]
- [x] **Task 4 — `job_loop.py`: startup 배선 — `start_heartbeat_thread()` + `run_agent` 오케스트레이션 (AC: 1, 2)** — 4.3 이 4.4 로 위임한 배선:
  - [x] `start_heartbeat_thread(reporter) -> threading.Thread` 헬퍼(daemon thread, `target=reporter.run`). 4.3 `HeartbeatReporter` 를 **그대로** 띄운다(heartbeat.py 무변경). [Source: architecture-contract.md(93), src/rider_agent/heartbeat.py(300-308), 4-3 스토리(71·109)]
  - [x] `run_agent(*, transport, store, identity_path, sleep=…, now=…, execute_job=default_execute_job, stop_event=None, …)` 오케스트레이션 — architecture-contract startup(90-94) 구현: `load_local_agent_identity()` → `validate_agent_token()` → `HeartbeatReporter` 구성(**`active_jobs_provider=runner.active_jobs` 배선**) → `start_heartbeat_thread()` → `JobRunner.run()`(메인 루프). 종료 시 reporter.stop()+thread join. identity 없음/token revoke 면 명확히 surfacing 하고 루프 진입하지 않는다(재등록 필요). **`start_kakao_sender_worker_if_enabled()`(4.6)는 배선하지 않는다**(주석/seam 만, 빈 호출 금지). [Source: architecture-contract.md(87-94), src/rider_agent/secure_store.py(273-338), src/rider_agent/heartbeat.py(254-293)]
  - [x] 모든 주입점(transport/store/sleep/now/execute_job/stop_event)을 노출해 테스트가 실 네트워크·실 thread 장기 대기·실 sleep 없이 결정적으로 검증하게 한다(4.3 reporter 테스트 규율). [Source: 4-3 스토리(140), architecture-contract.md(88-94)]
- [x] **Task 5 — `__main__.py`: thin `run` 서브커맨드(4.2 `register` 미러) (AC: 1)**
  - [x] `main(argv)` 에 `run` 분기 추가: `argv[0] == "run"` → `_run_agent_loop(argv[1:])`. **배너(인자 없음)·`register` 서브커맨드는 그대로 보존**(무회귀 — 4.1 Gap1·4.2 계약). [Source: src/rider_agent/__main__.py(95-103), 4-2 스토리(__main__ register 패턴)]
  - [x] `_run_agent_loop(...)` 는 **얇게**: 실제 `HttpTransport(op_label="agent jobs")`·`DpapiSecretStore(default_secret_store_path())`·`default_identity_path()` 주입 → `job_loop.run_agent(...)` 호출. **import 는 함수 내부로 defer**(runpy RuntimeWarning 회피 — memory). GUI/tkinter/`rider_crawl.ui`·`app` 미import(4.1 Gap1 가드 유지). 출력은 `redact` 통과(token/code 미출력). [Source: src/rider_agent/__main__.py(43-92·55-71), memory/agent-main-runpy-warning, tests/agent/test_agent_package.py(238)]
  - [x] **주의:** 실 CLI `run` 은 무한 루프(정상)지만, 테스트는 `stop_event`/주입 sleep 으로 즉시 종료하거나 `run_agent` 를 fake 로 주입해 hang 을 막는다. [Source: 4-3 스토리(140)]
- [x] **Task 6 — 테스트: `tests/agent/test_job_loop.py` (AC: 1~7)** — 외부 호출 없음(fake transport/주입 sleep·now·executor·stop), 가짜 값만:
  - [x] **위치/네이밍:** `tests/agent/test_job_loop.py`(평면, `__init__.py` 미추가 — 4.1/4.2/4.3 미러). 신규 basename. [Source: tests/agent/(test_heartbeat.py 선례), architecture.md(461)]
  - [x] **(AC1 — claim/complete client):** fake transport 에 대해 `claim_jobs` 가 `POST …/v1/jobs/claim`(본문 `agent_id`·`capabilities`·`max_jobs`)을 호출하고 응답을 `ClaimedJob` 리스트로 파싱, `complete_job` 이 `…/v1/jobs/{job_id}/complete`(본문 `status`·`result_json`·`error_code`·`error_message_redacted`·`metrics`)을 호출함을 단언. POST URL 정확성. [Source: data-api-contract.md(71-81)]
  - [x] **(AC1 — claim 한 job 만·token 게이트):** claim 이 빈 리스트면 `execute_job` 미호출·short-poll sleep 호출 단언. token invalid/revoke(stub `server_check=False`)면 claim 미전송·`needs_registration` surfacing 단언. [Source: src/rider_agent/secure_store.py(319-338)]
  - [x] **(AC1.3 — best-effort):** 한 주기 `TransportError`/executor 예외 → 루프 계속(다음 주기 claim 호출됨)·thread 미사망·에러 redact 단언. `401` stub → `TOKEN_STATUS_REVOKED`/`on_status` surfacing·crash 없음·매 주기 sleep 단언. [Source: operations-security-test-contract.md(93), src/rider_agent/heartbeat.py(336-347)]
  - [x] **(AC2 — lease 기록·active_jobs·self-check·거부):** claim 응답 `lease_expires_at` 가 `ClaimedJob`·in-flight 에 기록됨; `runner.active_jobs()` 가 in-flight job_id 를 돌려줌(heartbeat 배선용); 주입 `now` 를 lease 이후로 밀면 complete-success 대신 abandon/lease-lost; complete `409`/`410` stub → crash 없이 흡수·in-flight 제거 단언. [Source: epics.md AC(773-776), src/rider_agent/heartbeat.py(263)]
  - [x] **(AC2 — heartbeat 연장 배선):** `run_agent`(또는 헬퍼)가 `HeartbeatReporter(active_jobs_provider=runner.active_jobs)` 로 배선함을 단언(reporter 의 active_jobs 가 runner in-flight 를 반영). `start_heartbeat_thread` 가 thread 를 띄우고 stop 으로 정지함을 주입 sleep/stop 으로 결정적 검증. [Source: src/rider_agent/heartbeat.py(254-308)]
  - [x] **(AC3 — 결과 필드):** complete 본문에 `agent_id`·`started_at`/`finished_at`(주입 now 기반)·`status`·`error_code`·`error_message_redacted` 포함 단언. 재할당/실패 전이는 검사 대상 아님(서버 소유). [Source: data-api-contract.md(81)]
  - [x] **(AC4 — events redact):** `emit_job_event` 가 `…/v1/jobs/{job_id}/events`(본문 `event_type`·`severity`·`message_redacted`·artifact ref)를 호출하고, **본문에 raw secret/OTP/원문 error/raw HTML 이 없음**(`message_redacted` 가 redact 통과)을 단언. `event_type`/`severity` 를 "정확히 N" 으로 잠그지 않음. [Source: data-api-contract.md(75-77), operations-security-test-contract.md(87-95)]
  - [x] **(token-auth 헤더·비노출):** claim/complete/events 가 `Authorization: Bearer` 헤더에 token 을 실음을(주입 transport 캡처) 단언하고, **로그 캡처·예외 메시지·payload 본문 어디에도 평문 token 없음**(`assert fake_token not in captured_log and fake_token not in json.dumps(body)`) 단언. 헤더 dict 통째 로깅 금지. [Source: operations-security-test-contract.md(14-19·93), src/rider_agent/heartbeat.py(175-178)]
  - [x] **(누출 가드):** 모든 fixture 는 가짜 값만 — 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/agent token(`agtok-fake-…`)/`chat_id=<digits>`/한국 휴대폰/이메일·OTP 원문 금지. 실제 Telegram/Kakao/Gmail/브라우저/네트워크 미호출. [Source: project-context.md(55·81), epic-3-retro-2026-06-13.md(109·118)]
  - [x] **(`run` 서브커맨드 — `__main__`):** `main(["run"])` 를 fake `run_agent`/주입 stop 으로 검증하되 **모듈 top-level `import …__main__` 회피·함수 내부 defer**(runpy RuntimeWarning — memory). 배너·`register` 무회귀 확인. [Source: memory/agent-main-runpy-warning, src/rider_agent/__main__.py(95-103)]
- [x] **Task 7 — 회귀·범위·누출 검증 및 마무리 (AC: 1~7)**
  - [x] 운영 venv 로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/agent/test_agent_package.py`·`test_registration.py`·`test_secure_store.py`·`test_heartbeat.py`·`tests/server/`·`tests/test_*.py`), 신규 케이스만큼만 증가가 정상. [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] **4.1 가드 green 재확인(핵심):** `pytest tests/agent/test_agent_package.py -q` 의 (a) third-party root == `{rider_crawl}`, (b) sync(자기 모듈 async 0·`import asyncio` 0), (c) 단방향 import(`rider_server` 0), (d) pyproject deps **정확히 9개·핀 불변**이 **신규 `job_loop.py` 추가 + `__main__.py` `run` 추가 후에도 통과**함을 확인. stdlib(`urllib` via transport/`threading`/`time`/`json`/`os`)만 썼다면 green. 깨지면 새 third-party import 가 샌 것이니 제거. [Source: tests/agent/test_agent_package.py(194-233), 4-3 스토리(84)]
  - [x] **4.2/4.3 무회귀:** `registration.py`·`secure_store.py`·`heartbeat.py` **0줄 변경**(reuse only)임을 확인하고 `tests/agent/test_registration.py`·`test_secure_store.py`·`test_heartbeat.py` green. `HeartbeatReporter(active_jobs_provider=...)` 를 **주입만** 했지 시그니처를 바꾸지 않았음을 확인. [Source: src/rider_agent/heartbeat.py(254-267), 4-3 스토리(85)]
  - [x] 범위 점검: `git diff -w --stat` 에 **신규 `src/rider_agent/job_loop.py` + 신규 `tests/agent/test_job_loop.py` + `src/rider_agent/__main__.py`(`run` 서브커맨드만) + sprint-status 만** 보이고 **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`·`registration.py`·`secure_store.py`·`heartbeat.py` 변경 0줄**임을 확인(CRLF/LF 노이즈 무시 — `git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 token 0건, `src/rider_crawl/` 에 `rider_agent` import 신규 0건. agent token shape grep 만 믿지 말고 가짜 token 리터럴(`agtok-fake-…`)이 비-테스트 코드/로그에 안 새는지 교차 확인. [Source: project-context.md(64·81), epic-3-retro-2026-06-13.md(118), 4-3 스토리(87)]
  - [x] 변경 파일을 File List 에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record 에 적는다(dev 노트에 잠정 수치 박지 말 것 — Epic 2/3·4.1·4.2·4.3 에서 stale 수치 MEDIUM 재발). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **거의 순수 additive**다: 신규 `src/rider_agent/job_loop.py` + 신규 `tests/agent/test_job_loop.py` + `src/rider_agent/__main__.py` 의 **thin `run` 서브커맨드**(4.2 `register` 미러). **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`·`registration.py`·`secure_store.py`·`heartbeat.py` 는 무변경(reuse only).**
- **건드리지 않는다:** `rider_crawl` 전부(재사용만), 실제 job 실행 워커(`CRAWL_*`=4.5, `KAKAO_SEND`=4.6, `AUTH_*`=4.8/4.9 — `execute_job` 주입), BrowserProfileManager(4.5), KakaoSenderWorker(4.6), autostart(4.7), 배민·쿠팡 auth(4.8·4.9), 서버 측 queue/단일-claim 강제/lease 연장·stale sweep/재할당/job 생성/Admin(Epic 5), `pull_remote_config`/commands 적용(Epic 5). **빈 stub 파일(`workers/`·`browser_profile.py`·`auth/`)도 만들지 않는다.** [Source: epics.md Story 4.5~4.9(787-903), architecture.md(431·437·452-457)]

### 설계 결정 — 왜 reuse 이고 무엇이 client/server 경계인가 (반드시 읽을 것)

- **HTTP = 4.2/4.3 `Transport`/`HttpTransport` 재사용(새 seam·새 의존 금지).** claim/complete/events 셋 다 같은 단일 outbound seam 을 `headers=Bearer` 로 호출한다. `requests`/`httpx` 도입은 ADD-3·4.1 import-root 가드 위반. `op_label="agent jobs"` 로 운영 로그를 구분(registration.py 무변경 — 4.3 이 깐 인자). 단위 테스트는 fake transport 로 네트워크 0. [Source: src/rider_agent/registration.py(84-141), 4-3 스토리(184)]
- **루프 = stdlib `threading`+주입 `sleep`/`now`(asyncio 금지).** Agent 는 sync 런타임. 메인 루프·heartbeat thread 모두 `threading.Event`(stop)+주입 sleep 로 짠다 — `asyncio` 직접 import 는 4.1 `test_rider_agent_modules_are_pure_sync` 실패. 주입 sleep/now 로 테스트는 실 대기·실 시계 없이 결정적. [Source: tests/agent/test_agent_package.py(176-187), src/rider_agent/heartbeat.py(300-308)]
- **lease 는 서버가 진실의 원천 — client 는 인지·협조만.** 서버가 `lease_expires_at` 부여·`FOR UPDATE SKIP LOCKED` 단일 claim·heartbeat 기반 연장·만료 sweep·재할당을 소유한다. client(4.4)는 (a) 기록, (b) in-flight job_id 를 heartbeat `active_jobs` 로 노출(연장 입력), (c) complete 전 lease self-check, (d) 서버 거부(409/410) 흡수 — **이중 방어이되 client 가 단독으로 동시성 보장을 하지 않는다.** 서버 미구현(Epic 5)이므로 테스트는 stub transport 로 client 행동만 검증. [Source: architecture.md(431·493·180), epics.md AC(773-781), epic-3-retro-2026-06-13.md(108)]
- **`active_jobs` 실제 소스를 4.4 가 채운다(4.3 이 비워둔 곳).** 4.3 `HeartbeatReporter` 는 `active_jobs_provider`(기본 `[]`)를 받도록 설계됐고 4.3 스토리(26·58)가 "실제 소스 배선은 4.4 `job_loop.py` 소유"로 명시 위임했다. 4.4 는 `HeartbeatReporter(active_jobs_provider=runner.active_jobs)` 로 배선해 heartbeat 가 in-flight job 을 실어 lease 연장을 트리거하게 한다. **heartbeat.py 는 0줄 변경**(주입만). [Source: src/rider_agent/heartbeat.py(119·135-137·263), 4-3 스토리(26·58·71)]
- **`execute_job` 은 주입 seam — 워커는 후속 소유.** `run_once` 가 crawler/sender 를 주입하는 규율과 동형. 기본 executor 는 `UNSUPPORTED_JOB_TYPE` 실패 결과를 돌려 루프가 complete 로 깔끔히 보고하게 한다(빈 stub 워커 금지). 4.5+ 가 type 별 executor 를 주입. [Source: project-context.md(35·42), 4-3 스토리(58·95), architecture.md(452-457)]
- **status/event_type 는 평문 상수(enum·"정확히 N" lock 금지).** `secure_store.TOKEN_STATUS_*`·`heartbeat.DEFAULT_CAPABILITIES` 선례 — 후속 워커가 job type/이벤트를 늘려도 다른 테스트를 깨지 않는다. 테스트는 superset/포함 단언. [Source: src/rider_agent/secure_store.py(50-54), src/rider_agent/heartbeat.py(58-77), memory/enum-member-count-locks]

### 서버 부재 — stub/mock 검증이 4.x 형태 (4.1·4.2·4.3 계승)

- `POST /v1/jobs/claim`·`/complete`·`/events` 의 **서버 측 수신·`jobs` 테이블·queue·lease 강제/연장/stale 회수/재할당·Admin 표시는 Epic 5**(FastAPI/PostgreSQL, `rider_server/queue/postgres_queue.py`·`api/jobs.py`). 4.4 는 **client + 주입 transport stub** 로 (a) outbound claim/complete/events·claim-한-job-만, (b) best-effort 복원력·401 surfacing, (c) lease 기록·active_jobs 노출·self-check·서버 거부 흡수, (d) redact 된 events/complete, (e) Bearer 헤더 비노출, (f) startup 배선(heartbeat thread + active_jobs)을 검증한다. [Source: epic-3-retro-2026-06-13.md(108), data-api-contract.md(71-81·36), architecture.md(431·437)]
- **lease 판정 소유 분리(재강조):** "lease 만료 → stale 회수·재할당"·"단일 claim 강제"의 **판정/강제는 서버**. Agent 는 협조 신호(active_jobs)·self-check·거부 흡수까지. monitoring metric `agent_last_heartbeat`(2분)·`target_last_success_at` 은 운영 contract(25-26). [Source: operations-security-test-contract.md(25-26), epics.md AC(773-781)]
- **primitive + 배선 소유:** 4.3 은 heartbeat **primitive** 만 주고 `start_heartbeat_thread()` 배선을 4.4 로 위임했다. 4.4 는 그 배선(+메인 run 루프)을 완성한다 — `run_agent` 가 architecture-contract startup(90-94)을 구현. `start_kakao_sender_worker_if_enabled()`(4.6)는 seam/주석만, 빈 호출 금지. [Source: architecture-contract.md(87-94), 4-3 스토리(71·109)]

### 재사용 대상 공개 표면 (재구현 금지 — import/주입만)

| 도메인 | 공개 심볼 | 파일/행 | 4.4 사용 |
|---|---|---|---|
| Agent identity | `AgentIdentity`(`agent_id`/`agent_token`/`config_version`), `load_local_agent_identity` | rider_agent/secure_store.py(213·273) | job 주체(agent_id·인증 token)·startup 로드 |
| token 게이트 | `validate_agent_token`/`TokenValidation`(`can_receive_jobs`/`needs_registration`), `TOKEN_STATUS_VALID/REVOKED/MISSING` | rider_agent/secure_store.py(303·319·52-54) | claim 전 게이트(4.2가 4.4로 위임)·401 surfacing 정합 |
| secret store | `DpapiSecretStore`, `default_identity_path`, `default_secret_store_path` | rider_agent/secure_store.py(141·70·74) | `__main__ run` 실제 deps 주입 |
| outbound HTTP seam | `Transport`(`post_json(...,headers=)`), `HttpTransport(op_label=…)`, `TransportError(status_code)`, `_register_url` 패턴, `DEFAULT_SERVER_BASE_URL`/`SERVER_URL_ENV` | rider_agent/registration.py(63·84·55·183·47-48) | claim/complete/events POST + Bearer 헤더(무변경 재사용) |
| heartbeat reporter | `HeartbeatReporter(active_jobs_provider=…)`, `send_heartbeat`, `DEFAULT_CAPABILITIES`, `HEARTBEAT_OP_LABEL` | rider_agent/heartbeat.py(239·190·70·89) | `start_heartbeat_thread` 로 띄움 + `active_jobs` 배선(lease 연장) |
| redaction | `redact(text)`, `redacted_error_event(code,msg,err)`, `REDACTED` | rider_crawl/redaction.py(130·248·44) | events `message_redacted`·complete `error_message_redacted`·로그 마스킹 |
| 버전 | `rider_agent.__version__` | rider_agent/__init__.py(32) | (선택) result metrics agent_version |

- 모두 **import/주입만** — 시그니처 변경·`rider_crawl`/`registration`/`secure_store`/`heartbeat` 수정 금지. [Source: 위 파일/행, project-context.md(64), 4-3 스토리(122)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl`·`rider_server`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py` 무변경** — `git diff -w` = 신규 `job_loop.py` + 신규 테스트 + `__main__.py`(`run` 서브커맨드) + sprint-status 만. (b) **의존성 단방향·sync** — 신규 모듈도 `rider_crawl`/자기 패키지만 import, async 0, `threading`/주입 sleep 으로 루프(4.1 가드 자동). (c) **새 프레임워크/의존 0** — third-party root 는 `rider_crawl` 만, deps 정확히 9개 유지 → 4.1 가드 green. (d) **token 평문 0** — Authorization 헤더에만, 로그/payload/예외/에러 이벤트·헤더 dict 통째 출력 없음. (e) **events/complete redact** — `message_redacted`/`error_message_redacted` 만, raw error/OTP/secret/HTML 0, artifact 는 sanitized ref. (f) **best-effort 복원력** — 단발 실패가 루프 thread 를 죽이지 않음, 401/revoke·서버 거부(409/410)는 crash·무한 스핀 없이 흡수. (g) **claim 한 job 만 실행** — 임의 job 생성·실행 0. (h) **`__main__` 무회귀** — 배너(인자 없음)·`register` 보존, tkinter/UI 미import(4.1 Gap1), import defer(runpy). (i) **누출 0** — 테스트 실제 외부 미호출, 가짜 값만. [Source: project-context.md(24·55·64·81·82), operations-security-test-contract.md(14-19·87-95), tests/agent/test_agent_package.py(176-233·238)]

### 이전 스토리/회고 인텔리전스 (4.1·4.2·4.3 → 4.4 이월 교훈)

- **4.2/4.3 이 깐 토대 위에 빌드(직접 계승):** 4.2 는 `AgentIdentity`/`load_local_agent_identity`(identity)·`Transport`/`HttpTransport`(outbound seam)·`validate_agent_token`(게이트)을 만들고 "claim 루프 배선은 4.4"로 명시 위임했다(4-2 스토리 11-12·24). 4.3 은 `Transport.post_json(headers=)`·`HttpTransport(op_label=)`·`HeartbeatReporter(active_jobs_provider=)` 를 깔고 "`active_jobs` 실제 소스·`start_heartbeat_thread()` 배선은 4.4"로 위임했다(4-3 스토리 26·58·71·109). 4.4 는 정확히 그 비워둔 두 영역(claim 루프 + startup 배선 + active_jobs)을 채운다 — **새 seam 을 만들지 않고 주입/배선만**. [Source: 4-2 스토리(11-12·24), 4-3 스토리(26·58·71·109)]
- **secret 누출 비용 급등(A1″ 계승):** 4.2 부터 실제 token 을 다룬다. job 루프는 claim/complete/events **3개 호출 × 매 주기** token 을 헤더에 싣는 **반복 노출 표면**이라 더 주의 — 헤더/에러/로그/events 본문에서 token·OTP·secret 이 새지 않게 `redact`/`redacted_error_event` + 가짜값 규칙을 계속 적용한다. events 는 진단 정보라 redaction 누락 위험이 특히 크다. [Source: epic-3-retro-2026-06-13.md(109·118·168), operations-security-test-contract.md(19·87-95), 4-3 스토리(131)]
- **테스트 수치/File List 단일 정본(A2″ 계승):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. 4.1(dev 9→리뷰 14)·4.2(dev 1037→리뷰 1060)·4.3(dev 26→리뷰 31, 전체 1086→1091) 모두 stale 로 MEDIUM 이 났다. [Source: epic-3-retro-2026-06-13.md(59), 4-3 스토리(132·199-200), memory/stale-test-count-a2]
- **부정 가드는 AST 로(4.1 계승, 자동 적용):** 단방향·sync·no-new-framework 가드는 4.1 이 AST 로 짜 `src/rider_agent/*.py` 를 glob 한다 — 신규 `job_loop.py` 는 자동 검사. 새 가드를 raw grep 으로 짜지 말 것(scope docstring 이 `asyncio`/`rider_server`/`workers`/`browser_profile`(4.5) 같은 금지·후속 심볼명을 문자열로 언급해 오탐). [Source: tests/agent/test_agent_package.py(176-233), memory/negative-guard-tests-use-ast]
- **enum/lock 전수 점검(memory):** job status·event_type 를 enum 이나 "정확히 N개" 테스트로 잠그지 말 것 — 후속 job type/이벤트 추가가 여러 lock 을 깨는 패턴(`secure_store`/`heartbeat` 가 평문 상수로 피한 이유). 테스트는 superset/포함 단언. [Source: memory/enum-member-count-locks, src/rider_agent/heartbeat.py(58-77)]
- **`__main__` runpy 경고(memory):** `__main__.py` 에 `run` 을 추가하므로, 그 테스트는 `rider_agent.__main__` 을 **모듈 top-level 로 import 하지 말고 함수 내부로 defer**한다(runpy RuntimeWarning 회피 — 4.1 가드에서 재발). `_run_agent_loop` 의 무거운 import(job_loop/secure_store)도 함수 내부 defer 로 두면 무인자 배너 경로 무부작용(4.2 `register` 선례). [Source: memory/agent-main-runpy-warning, src/rider_agent/__main__.py(55-71·95-103)]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest 는 **WSL 의 `python3` 가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`** 로 돌린다(WSL python 엔 pytest 미설치). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w` 로 하고 무관한 EOL flip 을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]
- **루프/thread 테스트 주의:** 실 `time.sleep`/실 thread 장기 대기·실 시계를 쓰지 말고 **주입 fake sleep/now + stop event + 호출 카운터**로 N회 후 정지·lease 만료를 결정적으로 검증한다(테스트 hang/flaky 방지). heartbeat thread 도 stop+join 으로 정리. [Source: architecture-contract.md(88-94), 4-3 스토리(140)]

### Project Structure Notes

- 신규 파일은 architecture.md(451) 트리와 정렬: `src/rider_agent/job_loop.py`(= `# outbound HTTPS poll/claim/complete`). 트리의 `browser_profile.py`/`workers/`/`auth/`/`autostart.py` 는 각 후속 스토리(4.5~4.9)가 만든다 — **계획된 부분 구현이지 이탈이 아니다**(4.1·4.2·4.3·Epic 2/3 retro 의 "부분 구현은 계획" 판정). startup 배선(`run_agent`)을 별도 `runtime.py` 로 빼지 않고 `job_loop.py` 에 둔다(트리에 `runtime.py` 없음 — job 루프 응집). [Source: architecture.md(446-457), 4-3 스토리(144)]
- 테스트는 `tests/agent/test_job_loop.py`(평면, `__init__.py` 미추가). 기존 `tests/agent/test_{agent_package,registration,secure_store,heartbeat}.py` 와 별 basename. [Source: architecture.md(461), 4-3 스토리(145)]
- `__main__.py` 는 4.2 가 `register`, 본 스토리가 `run` 을 더한다 — 둘 다 thin entry(핵심 로직은 `registration`/`job_loop`). 배너 폴백·무인자 무부작용 유지. [Source: src/rider_agent/__main__.py(95-103)]
- **변이/충돌:** `project-context.md` 의 `rider_agent` 진전 반영은 **Epic 4 retro** 에서 한다(rider_server 를 Epic 2 retro 에서 반영한 선례). 본 스토리에서 project-context.md 는 수정하지 않는다. [Source: 4-3 스토리(146), project-context.md(114)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.4(760-785)] — user story + AC(outbound claim/complete·lease 부여·연장·만료 stale 회수·재할당·결과 필드·events redact).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-4(694-696)] — Epic 4 범위(서버 stub/mock 검증, FR-12~20·25·28·32, ADD-4·6·15).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P3-04(61)] — "Implement HTTPS outbound job polling, claim, and complete loop." → firewall 없이 inbound 포트 0.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md#Agent-Loop(87-107)·Job-Types(120-129)] — startup(`load_identity→validate_token→start_heartbeat_thread→…`)·main_loop(`claim→execute→complete`)·6 job type.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md#Agent-API(71-81)·Tables(36)] — `POST /v1/jobs/claim`(agent_id·capabilities·max_jobs)·`/events`(event_type·severity·message_redacted·artifact)·`/complete`(status·result_json·error_code·error_message_redacted·metrics). `jobs` 테이블(id·type·target_id·agent_id·status·run_after·attempts·error_code).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Redaction(14-19)·Monitoring(22-31)·Forbidden(87-95)·Tests(42-51)] — message/error_message_redacted·`agent_last_heartbeat` 2분·token/OTP/HTML 로깅 금지·Agent API/job lifecycle 테스트.
- [Source: _bmad-output/planning-artifacts/architecture.md#Auth-Security(176-185)·API-Comm(187-196)·Boundaries(473-496)·tree(446-457)] — token-auth·outbound HTTPS only·replay 방지(서명 claim/lease)·Cloud=async/Agent=sync·job_loop.py 위치.
- [Source: src/rider_agent/registration.py(43-48·55-141·183-185)] — `Transport`(`headers=`)/`HttpTransport`(`op_label=`)/`TransportError`(status_code)/`_register_url`/server url 상수(재사용·무변경).
- [Source: src/rider_agent/secure_store.py(50-54·213-230·273-338)] — `TOKEN_STATUS_*`·`AgentIdentity`·`load_local_agent_identity`·`validate_agent_token`/`TokenValidation`·`default_*_path`/`DpapiSecretStore`.
- [Source: src/rider_agent/heartbeat.py(70-77·89·175-178·239-308)] — `DEFAULT_CAPABILITIES`·`HEARTBEAT_OP_LABEL`·Bearer 헤더 패턴·`HeartbeatReporter(active_jobs_provider=…)`/`run`(배선 대상·무변경).
- [Source: src/rider_agent/__main__.py(43-92·95-103)] — `register` thin entry 패턴(`run` 미러)·배너 폴백·import defer.
- [Source: src/rider_crawl/redaction.py(44·130·248)] — `REDACTED`/`redact`/`redacted_error_event`(events·complete·로그 마스킹).
- [Source: tests/agent/test_agent_package.py(176-233·238)] — 4.1 가드(sync·third-party root==rider_crawl·단방향·deps 9핀·__main__ tkinter 미import) — 신규 모듈 자동 적용·green 유지.
- [Source: _bmad-output/implementation-artifacts/4-2-등록-코드-입력과-agent-토큰-보안-저장.md(11-12·24)] — 4.2 위임(claim 루프=4.4)·transport/identity/게이트 토대.
- [Source: _bmad-output/implementation-artifacts/4-3-agent-heartbeat-보고.md(26·58·71·109·131-132·199-200)] — 4.3 위임(active_jobs 소스·start_heartbeat_thread 배선=4.4)·reporter primitive·secret 반복 노출·stale 수치 교훈.
- [Source: _bmad-output/implementation-artifacts/epic-3-retro-2026-06-13.md(59·108·109·118·158)] — stub/mock 검증·A1″/A2″·rider_crawl 0줄.
- [Source: _bmad-output/project-context.md(24·35·42·53·64·75·81·82·114)] — urllib 정책·주입·run_once 경계·pytest 실행·단방향 import·누출 금지·git diff·retro 반영.
- [Source: memory/dev-env-quirks, memory/stale-test-count-a2, memory/negative-guard-tests-use-ast, memory/enum-member-count-locks, memory/agent-main-runpy-warning] — venv pytest·`git diff -w`, 수치 단일 정본, AST 부정 가드, enum/lock 전수 점검, __main__ runpy 경고.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `.venv/Scripts/python.exe -m pytest tests/agent/test_job_loop.py -q` — 신규 모듈 단위 검증.
- `.venv/Scripts/python.exe -m pytest -q` — 전체 회귀.
- `.venv/Scripts/python.exe -m pytest tests/agent/test_agent_package.py tests/agent/test_registration.py tests/agent/test_secure_store.py tests/agent/test_heartbeat.py -q` — 4.1 가드 + 4.2/4.3 무회귀.
- `git diff -w --stat` + 누출/의존성-방향 grep(평문 token 0·`rider_crawl`→`rider_agent` import 0).

### Completion Notes List

- **거의 순수 additive**로 구현했다: 신규 `src/rider_agent/job_loop.py`(claim/complete/events client + `JobRunner` 루프 primitive + lease 인지 + `run_agent`/`start_heartbeat_thread` 배선) + 신규 `tests/agent/test_job_loop.py`, 그리고 `src/rider_agent/__main__.py` 에 thin `run` 서브커맨드(4.2 `register` 미러, 무거운 import 함수 내부 defer). `rider_crawl`·`rider_server`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`·`registration.py`·`secure_store.py`·`heartbeat.py` 는 **0줄 변경**(reuse only) — `git diff -w` 로 확인.
- **재사용/주입만 사용**: HTTPS 는 4.2 `Transport`/`HttpTransport`(stdlib `urllib`)를 `op_label="agent jobs"`·`Authorization: Bearer` 헤더로 호출, 루프/주기 대기는 stdlib `threading.Event`+주입 `sleep`/`now`(asyncio 0). redaction 은 `rider_crawl.redaction.redact`/`redacted_error_event` 재사용. `HeartbeatReporter(active_jobs_provider=runner.active_jobs)` 로 **주입만** 해 heartbeat.py 시그니처 무변경. 새 third-party 의존 0 → 4.1 가드(third-party root==`rider_crawl`, deps 정확히 9개) green 유지.
- **lease 소유 분리 준수(client 4가지만)**: (a) claim 응답 `lease_expires_at` 를 `ClaimedJob`/in-flight 에 기록, (b) `runner.active_jobs()` 로 heartbeat 에 노출(서버 연장 입력), (c) complete/success 직전 주입 `now` 로 lease self-check(만료/파싱불가 → fail-closed abandon, 성공 미보고), (d) 서버 거부(409/410/401) crash 없이 흡수·기록. 단일-claim 강제·실제 연장/stale sweep/재할당은 서버(Epic 5)에 남김.
- **secret 비노출**: claim/complete/events 3개 호출 모두 token 을 `Authorization` 헤더에만 싣고 본문/로그/예외/에러 이벤트에 평문 0(테스트로 단언). events `message_redacted`·complete `error_message_redacted` 는 redact 통과값만. status/event_type 은 평문 상수(enum/"정확히 N" lock 금지 — superset/포함 단언).
- **`execute_job` 는 주입 seam**: 기본 `default_execute_job` 가 `UNSUPPORTED_JOB_TYPE` 실패 결과를 돌려 후속 워커(4.5/4.6/4.8/4.9)가 type 별 executor 를 주입하게 했다. 빈 stub 워커 파일(`workers/`·`browser_profile.py`·`auth/`)은 만들지 않음. `start_kakao_sender_worker_if_enabled()`(4.6)는 배선하지 않음.
- **테스트**: `tests/agent/test_job_loop.py` 51 케이스(dev-story 35 + qa-generate-e2e-tests 16, fake transport URL 라우팅 + 주입 sleep/now/executor/stop, 실 네트워크·실 thread 장기 대기 0). AC1~7·token 게이트·best-effort 복원력·lease self-check/거부 흡수·heartbeat active_jobs 배선·`run` 서브커맨드·누출 가드 커버.
- **검증 결과(리뷰 시점 재측정 — 단일 정본)**: 전체 스위트 **1142 passed**(WSL `python3` 아님, 운영 venv `.venv/Scripts/python.exe`). 신규 `tests/agent/test_job_loop.py` = 51 케이스. 4.1 가드 + 4.2/4.3 무회귀 = 97 passed green. CRLF/LF 노이즈는 `git diff -w` 로 무시.

### File List

- `src/rider_agent/job_loop.py` (new) — outbound job claim/complete/events client + `JobRunner` 루프 primitive + lease 인지(record/active_jobs/self-check/거부 흡수) + `start_heartbeat_thread`/`build_agent_components`/`run_agent` startup 배선.
- `src/rider_agent/__main__.py` (modified) — thin `run` 서브커맨드 추가(`_parse_run_args`/`_run_agent_loop`, import 함수 내부 defer). 배너·`register` 무회귀.
- `tests/agent/test_job_loop.py` (new) — AC1~7 + token-auth 비노출 + `run` 서브커맨드 검증(35 케이스).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — 4.4 ready-for-dev → in-progress → review.

## Senior Developer Review (AI)

**Reviewer:** lsy9344 · **Date:** 2026-06-13 · **Outcome:** ✅ Approve (Status → done)

### 요약

adversarial 리뷰 결과 **AC1~AC7 전수 구현·검증**, **reuse-only 경계 완벽 준수**(`rider_crawl`/`rider_server`/`pyproject.toml`/`registration.py`/`secure_store.py`/`heartbeat.py` `git diff -w` = **0줄**), **4.1 AST 가드 + 4.2/4.3 무회귀 green**(97 passed). 거의 순수 additive 스토리로 스코프 크립 없음. CRITICAL/HIGH 0건, MEDIUM 1건(stale 수치)을 리뷰에서 자동 정정.

### AC 검증 (전수 통과)

- **AC1** (outbound HTTPS claim/complete + claim-한-job-만 + token 게이트): `claim_jobs`/`complete_job` 가 주입 `Transport.post_json(url, body, headers=Bearer)` 만 사용(stdlib urllib seam, 새 의존 0). `JobRunner` 는 `claim_jobs` 반환 job 만 실행. `_gate_token` 이 매 claim 전 `validate_agent_token` 게이트. ✓ (`job_loop.py:277-299,481-515`)
- **AC1.3** (best-effort): `run_once`/`_process_job`/`_complete` 모두 `TransportError`+광역 `except` 흡수, 401 → `TOKEN_STATUS_REVOKED` surfacing, 매 주기 끝 sleep(무한 스핀 없음). ✓
- **AC2** (lease 기록·active_jobs·self-check·거부 흡수): `ClaimedJob.lease_expires_at` 보존, `active_jobs()` thread-safe 스냅샷, `build_agent_components` 가 `HeartbeatReporter(active_jobs_provider=runner.active_jobs)` 배선, success 직전 `_is_lease_expired` self-check(fail-closed), 409/410 흡수. ✓
- **AC3** (결과 필드): `complete` 본문에 `agent_id`/`started_at`/`finished_at`(주입 `now`)/`status`/`error_code`/`error_message_redacted`/`metrics`. ✓
- **AC4** (events redact): `make_job_event` 가 `redact` 통과, 본문 secret/OTP/email 0(테스트 직렬화 단언). `event_type`/`severity` 평문 상수(enum-lock 없음). ✓

### 발견 사항

| # | 심각도 | 내용 | 처리 |
|---|---|---|---|
| 1 | MEDIUM | Dev Agent Record 가 `test_job_loop.py` 35건·전체 1126 passed 로 표기하나 리뷰 재측정값은 **51건·1142 passed**(qa-generate-e2e-tests 가 dev 노트 이후 16건 추가, Dev Agent Record 미반영). memory/stale-test-count-a2 패턴 재발(Epic 2/3·4.1·4.2·4.3 동일). | **자동 수정** — Completion Notes·Change Log 를 51/1142/97 로 정정. QA artifact `test-summary-4.4.md` 는 이미 정확. |
| 2 | LOW(무수정) | `_bmad-output/story-automator/orchestration-*.md` 가 git 변경됐으나 File List 미기재. | 리뷰 범위 제외(`_bmad-output/` 자동 관리 파일) — 무수정. |

### 검증 명령(리뷰 시점 재측정 — 단일 정본)

- `.venv/Scripts/python.exe -m pytest tests/agent/test_job_loop.py -q` → **51 passed**
- `.venv/Scripts/python.exe -m pytest -q` → **1142 passed**
- `.venv/Scripts/python.exe -m pytest tests/agent/test_agent_package.py test_registration.py test_secure_store.py test_heartbeat.py -q` → **97 passed**(4.1 가드 + 4.2/4.3 무회귀)
- `git diff -w` reuse-only(`rider_crawl`/`rider_server`/`pyproject.toml`/`registration`/`secure_store`/`heartbeat`/`*.spec`) = **0줄**

## Change Log

- 2026-06-13 — Story 4.4 구현 완료(dev-story). outbound HTTPS job claim/complete/events client + `JobRunner` 루프 primitive + lease 인지(client) + `run_agent`/`start_heartbeat_thread` startup 배선 + `__main__` thin `run` 서브커맨드. 신규 `job_loop.py`·`test_job_loop.py`(35 케이스), `__main__.py` `run` 추가; reuse-only 모듈(`rider_crawl`/`registration`/`secure_store`/`heartbeat`/`pyproject.toml`) 0줄 변경. 전체 1126 passed. Status → review.
- 2026-06-13 — Senior Developer Review(AI, story-automator) 완료. 리뷰 시점 재측정으로 stale 테스트 수치 1건(MEDIUM) 정정: `test_job_loop.py` 35→**51 케이스**, 전체 1126→**1142 passed**(qa-generate-e2e-tests 가 dev 노트 이후 16건 추가했으나 Dev Agent Record 미반영 — memory/stale-test-count-a2 패턴 재발). AC1~7 전수 구현·테스트 확인, reuse-only 모듈 `git diff -w` 0줄 변경 검증, 4.1 가드+4.2/4.3 무회귀 97 passed green. CRITICAL 0건 → Status → done.
