---
baseline_commit: bb91410
---

# Story 4.3: Agent heartbeat 보고

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 작업 노드 관리자,
I want 등록된 Agent가 **30~60초마다** `POST /v1/agents/heartbeat`로 자신의 상태(metrics)·처리 가능 job type(capabilities)·현재 작업(active_jobs)·KakaoTalk 상태(kakao_status)·Browser Profile 상태(browser_profiles)와 **자기 버전(agent_version)** 을 보고하고, 그 보고를 **자기 코드가 죽지 않게(best-effort) 주기적으로** 수행하는 **heartbeat 리포터 primitive**를 갖고 싶다,
so that 운영 화면(Epic 5)이 **2분 이상 heartbeat가 없으면 offline/degraded로**, **버전이 서버 기대와 다르면 식별 가능하도록** 판정할 데이터가 서버에 기록되고(P3-03, FR-12, NFR-14), 이후 4.4 job claim 루프 startup이 이 리포터를 `start_heartbeat_thread()`로 배선하기만 하면 된다.

> **이 스토리의 성격 — "heartbeat payload 빌더 + 단발 send + 주기 리포터 loop primitive"만.** job claim/lease도, BrowserProfileManager도, KakaoSenderWorker도, autostart도 아니다. P3-03 deliverable은 **"Report heartbeat every 30-60 seconds. Admin shows online/offline state."** 가 전부다(implementation-contract P3-03:60). **outbound HTTPS job polling/claim/complete+lease는 4.4(P3-04), BrowserProfileManager는 4.5(P3-05), KakaoSenderWorker FIFO queue는 4.6(P3-06), autostart는 4.7(P3-07), 배민/쿠팡 인증은 4.8·4.9, 서버 측 heartbeat 수신·offline 판정·Admin 표시는 Epic 5 소유다.** 본 스토리는 4.2가 깐 identity 토대(`load_local_agent_identity`/`AgentIdentity`) + 4.2의 outbound HTTP seam(`Transport`/`HttpTransport`) 위에 **`heartbeat.py` + `tests/agent/test_heartbeat.py`만** 얹는다(+ auth 헤더를 위한 `registration.py`의 **선택 인자 한 개** 후방호환 추가 — 아래 범위 경계 참조). [Source: implementation-contract.md P3-03(60), epics.md Story 4.3(738-758)·4.4~4.9(760-903), architecture.md(450·88-107)]
>
> **서버가 아직 없다 — "서버 stub/mock에 대한 동작 검증"이 4.x 테스트 형태(절대 전제, 4.1·4.2 계승).** `POST /v1/agents/heartbeat` 의 **서버 측 수신·`last_heartbeat_at` 기록·offline/degraded 판정·버전 drift 표시는 Epic 5 소유**(FastAPI/PostgreSQL `agents` 테이블·Admin). 따라서 본 스토리는 실제 HTTP 서버를 띄우지 않고 **주입된 fake transport**(canned `{server_time, config_version, commands}` 응답)에 대해 payload 구성·주기 보고·실패 복원력·auth 실패 시나리오를 검증한다. epic-3-retro(108): "Epic 4는 서버 측 job 생성·queue·Admin이 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." [Source: epics.md Epic 4(696), epic-3-retro-2026-06-13.md(108), data-api-contract.md(67-69)]
>
> **AC2의 소유 분리(가장 헷갈리는 경계).** "2분 이상 heartbeat 없으면 offline/degraded" 와 "버전이 서버 기대와 다르면 식별 가능"의 **판정 로직 자체는 서버(Epic 5)** 다. **Agent(4.3) 측 책임은 두 가지뿐**: (a) interval을 **30~60초 범위로 보장**해 2분 무신호가 "정상 주기의 일시 지연"이 아니라 **확실히 ≥2회 누락(=offline 신호)** 이 되게 한다, (b) payload에 **`agent_version`(`rider_agent.__version__`)을 실어** 서버가 버전 drift를 판정할 입력을 제공한다. **Agent가 스스로 자기를 offline 판정하거나 Admin 화면을 그리지 않는다** — 그건 Epic 5. [Source: epics.md AC(751-758), operations-security-test-contract.md(25), NFR-14, architecture.md(190·476)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **거의 순수 additive**다: 신규 `src/rider_agent/heartbeat.py` + 신규 `tests/agent/test_heartbeat.py`, 그리고 auth 헤더 전달을 위한 `registration.py` `Transport.post_json`/`HttpTransport.post_json`의 **선택 인자(`headers=None`) 한 개 후방호환 추가**만. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(절대 규칙·무회귀 안전 마진).** `rider_crawl.redaction`·`rider_crawl.config.app_state_root`·`rider_crawl.secret_store.SecretStore`를 **import해서 재사용만** 한다. epic-3-retro(158): "**`rider_crawl` 0줄**이 안전 마진." [Source: project-context.md(64·82), epic-3-retro-2026-06-13.md(158)]
> - **`pyproject.toml` `[project].dependencies` — 0줄 변경(권장 설계로 달성).** HTTPS는 stdlib `urllib`(4.2 `HttpTransport` 재사용), 주기 루프는 stdlib `threading`/`time`, 시스템 metrics는 **stdlib만**(아래 설계 결정). **`psutil` 등 새 third-party 의존을 추가하지 않는다** — 추가하면 4.1이 잠근 `tests/agent/test_agent_package.py`의 (a) "third-party root == `{rider_crawl}`", (b) "deps 정확히 9개" 가드가 **둘 다 깨진다**(memory: enum-member-count-locks와 동형 "추가가 기존 lock을 깬다"). [Source: project-context.md(24), tests/agent/test_agent_package.py(194-213), 4-2 스토리(23)]
> - **job claim/lease/active_jobs 실제 소스** → **4.4**. heartbeat payload의 `active_jobs`는 본 스토리에서 **주입 가능한 provider(기본 빈 리스트)** 로만 둔다. 실제 "현재 claim된 job 목록" 배선은 4.4 `job_loop.py` 소유. `job_loop.py` 미생성. [Source: epics.md Story 4.4(760-785), architecture.md(451)]
> - **kakao_status 실제 소스** → **4.6**. payload의 `kakao_status`는 **주입 provider(기본 `"disabled"`/`"unknown"` 등 안전 기본값)** 로만. 실제 KakaoSenderWorker FIFO queue 상태/lag는 4.6 소유. `workers/kakao_sender.py` 미생성. [Source: epics.md Story 4.6(810-832), architecture.md(456)]
> - **browser_profiles 실제 소스** → **4.5**. payload의 `browser_profiles`는 **주입 provider(기본 빈 리스트)** 로만. 실제 프로필/CDP 포트 상태는 4.5 BrowserProfileManager 소유. `browser_profile.py` 미생성. [Source: epics.md Story 4.5(787-808), architecture.md(452)]
> - **startup `start_heartbeat_thread()` 실제 배선 / 메인 run 루프** → **4.4**. 본 스토리는 리포터 **primitive**(thread로 돌릴 수 있는 sync loop 객체/함수)만 제공한다 — 4.2가 `validate_agent_token()` primitive만 주고 claim 배선을 4.4로 위임한 것과 동형. `__main__.py`에 자동 heartbeat 기동을 **추가하지 않는다**(register thin CLI는 4.2 그대로 보존). [Source: architecture-contract.md(88-94), 4-2 스토리(24·99), epics.md Story 4.4(760-785)]
> - **autostart / 배민·쿠팡 auth / 서버 측 register·heartbeat 수신·Admin** → **4.7~4.9 / Epic 5**. 미생성. [Source: epics.md Story 4.7~4.9(833-903)·Epic 5(904-)]
>
> **secret 비노출(ADD-15·NFR-5 — 본 스토리의 핵심 가드).** heartbeat는 **agent_token으로 인증**한다(Agent API = token-auth, architecture 476). token은 **Authorization 헤더에만** 싣고 **로그·payload 본문·`commands` 파싱 출력·예외 메시지·에러 이벤트 어디에도 평문으로 남기지 않는다.** 헤더 dict를 통째로 로깅하지 않는다. 로그/배너 출력은 `rider_crawl.redaction.redact`를 통과시킨다. 테스트 fixture·docstring에도 실제 토큰을 넣지 않고 명백한 가짜값(`agtok-fake-…`)만 쓴다. [Source: project-context.md(81), operations-security-test-contract.md(14-19·93), architecture.md(183-185·476), epic-3-retro-2026-06-13.md(109·118)]
>
> **sync 런타임 + 단방향 import(4.1 규약 계승 — 자동 검증됨).** 신규 `heartbeat.py`는 **순수 동기**(no `async def`/`await`/직접 `import asyncio`)이고 `rider_crawl`/자기 패키지만 import한다(역방향 0, `rider_server` import 0). 주기 루프는 `asyncio`가 아니라 **`threading` + `time.sleep`(주입 가능)** 로 짠다. 4.1이 `src/rider_agent/*.py` **전체를 glob**하는 AST 가드로 검사하므로 신규 모듈도 자동 적용된다 — 규약을 깨면 4.1 테스트가 실패한다. [Source: 4-1 스토리 AC3·AC5, tests/agent/test_agent_package.py(176-233), architecture.md(484), memory/negative-guard-tests-use-ast]

## Acceptance Criteria

**AC1 — 30~60초 주기 `POST /v1/agents/heartbeat` + 5필드 payload + agent_version + 응답 파싱 (P3-03, FR-12)**

1. **Given** 등록된 Agent(유효 `AgentIdentity` = `agent_id` + token)가 동작할 때 **When** heartbeat 리포터가 보고하면 **Then** `POST /v1/agents/heartbeat` 요청 본문에 **`agent_id`, `metrics`, `capabilities`, `active_jobs`, `kakao_status`, `browser_profiles`** 5필드가 모두 포함되고(data-api-contract 67-69), 추가로 **`agent_version`(`rider_agent.__version__`)** 이 실린다(버전 drift 입력 — AC2). 보고는 **주입된 transport**(4.2 `Transport`/`HttpTransport` 재사용, stdlib `urllib`)로 수행하며, 본 스토리 테스트는 실제 서버 없이 fake transport의 canned 응답으로 검증한다. [Source: data-api-contract.md(67-69), epics.md AC(746-749), project-context.md(24·35), src/rider_agent/registration.py(63-114)]
2. **And** 리포터의 보고 주기는 **30~60초 범위로 보장**된다: interval은 주입/설정 가능하되 **`[30, 60]`초로 검증/clamp**되어, 그 범위 밖 값을 넣어도 범위 안으로 보정된다(2분 무신호가 정상 주기 지연이 아닌 ≥2회 누락이 되도록 — AC2의 Agent 측 책임). [Source: implementation-contract.md P3-03(60), operations-security-test-contract.md(25), epics.md AC(747·751-753)]
3. **And** 응답의 `server_time`, `config_version`, `commands`(data-api-contract 69)를 파싱해 호출자가 쓸 수 있는 구조로 반환한다. `commands` 실행/적용 로직은 본 스토리 범위 아님(후속/Epic 5) — 파싱·전달까지만. token/secret이 응답 처리 경로 로그에 새지 않는다. [Source: data-api-contract.md(69), operations-security-test-contract.md(14-19)]

**AC2 — offline/버전-drift "판정 입력" 제공(판정은 서버) + best-effort 복원력 (NFR-14, FR-12)**

4. **Given** 서버(Epic 5)가 마지막 heartbeat 2분 경과로 offline/degraded를 **판정해야** 할 때 **When** Agent가 30~60초 주기로 정상 보고하면 **Then** Agent 측은 **그 판정이 가능하도록 일정 주기 보고를 유지**하고 payload에 **`agent_version`** 을 실어 버전 drift 식별 입력을 제공한다. **Agent가 스스로 offline 판정/Admin 표시를 하지 않는다**(서버 소유). [Source: epics.md AC(751-755), operations-security-test-contract.md(25), architecture.md(190·476)]
5. **And** **단발 heartbeat 실패가 리포터 thread를 죽이지 않는다(best-effort)**: 한 번의 전송이 `TransportError`(네트워크/5xx)로 실패해도 루프는 **다음 주기에 재시도**하고, 에러는 `redact`/`redacted_error_event`로 마스킹해 기록하며 token을 노출하지 않는다. 서버가 token을 revoke해 `401`/거부가 오면 **재등록 필요 상태를 surfacing**하되(예: 상태 플래그/콜백) **crash·무한 즉시 스핀 없이** 처리한다(실제 재등록/중단 반응은 4.4/운영 소유). [Source: operations-security-test-contract.md(93·25), architecture.md(178-179), src/rider_agent/secure_store.py(300-338), 4-2 스토리 AC3]

**AC3 — capabilities = 처리 가능 job type 보고 (FR-12)**

6. **Given** 운영자가 Agent별 처리 능력을 알아야 할 때 **When** heartbeat가 `capabilities`를 보고하면 **Then** Agent가 처리 가능한 **job type 목록**이 실린다 — 기본값은 architecture-contract의 Agent Job Types 6종(`CRAWL_BAEMIN`, `CRAWL_COUPANG`, `AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `KAKAO_SEND`, `CAPTURE_DIAGNOSTIC`)이며 **주입 가능**(실제 enable 여부 감지는 후속 워커 4.5/4.6/4.8/4.9 소유). capabilities 목록은 **"정확히 N개" lock으로 잠그지 않는다** — 후속 스토리가 job type을 늘려도 다른 테스트가 깨지지 않게(memory: enum-member-count-locks). [Source: epics.md AC(756-758), architecture-contract.md(120-129), memory/enum-member-count-locks]

## Tasks / Subtasks

- [x] **Task 1 — `heartbeat.py`: payload 빌더 + capabilities 기본값 + provider seam (AC: 1, 3)**
  - [x] `src/rider_agent/heartbeat.py` 신설. `build_heartbeat_payload(identity, *, capabilities=..., metrics_provider=..., active_jobs_provider=..., kakao_status_provider=..., browser_profiles_provider=...) -> dict`(가칭)를 둔다 — `agent_id`(identity)·`agent_version`(`rider_agent.__version__`)·`metrics`·`capabilities`·`active_jobs`·`kakao_status`·`browser_profiles`를 합성한다. **token은 payload 본문에 넣지 않는다**(인증은 헤더 — Task 2). [Source: data-api-contract.md(67-69), src/rider_agent/secure_store.py(213-230), src/rider_agent/__init__.py(32)]
  - [x] **capabilities 기본값:** 모듈 상수로 Agent Job Types 6종(`CRAWL_BAEMIN`/`CRAWL_COUPANG`/`AUTH_CHECK`/`OPEN_AUTH_BROWSER`/`KAKAO_SEND`/`CAPTURE_DIAGNOSTIC`)을 둔다. **enum/"정확히 N개" lock 금지** — `TOKEN_STATUS_*`를 평문 상수로 둔 `secure_store.py` 선례를 따른다(후속 워커가 늘려도 무탈). 주입 가능. [Source: architecture-contract.md(120-129), src/rider_agent/secure_store.py(50-54), memory/enum-member-count-locks]
  - [x] **provider seam(후속 스토리 소유 소스의 placeholder):** `active_jobs`(4.4)·`kakao_status`(4.6)·`browser_profiles`(4.5)·`metrics`는 **주입 가능한 callable(또는 값)** 로 두고 **기본은 안전한 빈/idle 값**(`active_jobs=[]`, `browser_profiles=[]`, `kakao_status="disabled"` 등). 실제 소스 배선은 각 후속 스토리가 provider를 주입해서 한다(`run_once`가 crawler/sender를 주입하는 규율과 동형 — 빈 stub 파일 만들지 않는다). [Source: project-context.md(35·42), epics.md Story 4.4~4.6(760-832)]
  - [x] **metrics는 stdlib만:** 시스템 metrics가 필요하면 **`psutil` 등 새 의존을 추가하지 말고** stdlib(예: `time`/`platform`/`os`)나 주입 provider로 충당한다(기본 최소 dict 허용). 새 의존 추가는 4.1 deps-pin·import-root 가드를 깬다. [Source: project-context.md(24), tests/agent/test_agent_package.py(194-213)]
  - [x] 자기 코드 **순수 동기 + `rider_crawl`/자기 패키지만 import**(역방향/`rider_server` 0) — 4.1 AST 가드가 자동 검사. [Source: 4-1 스토리 AC3·AC5, tests/agent/test_agent_package.py(176-233)]
- [x] **Task 2 — `heartbeat.py`: 단발 `send_heartbeat` + token-auth 헤더(평문 비노출) + 응답 파싱 (AC: 1, 2)**
  - [x] `send_heartbeat(identity, *, transport, base_url=None, ...) -> HeartbeatResult`(가칭)를 둔다 — Task 1의 payload로 `POST /v1/agents/heartbeat`를 호출하고 응답(`server_time`/`config_version`/`commands`)을 파싱해 반환한다. URL은 4.2 `registration._register_url` 패턴(`base_url`/`RIDER_AGENT_SERVER_URL` env)과 정합하게 구성한다(`HEARTBEAT_PATH = "/v1/agents/heartbeat"`). [Source: data-api-contract.md(67-69), src/rider_agent/registration.py(43-48·156-158)]
  - [x] **token-auth 헤더(핵심):** `agent_token`을 `Authorization: Bearer <token>` 헤더로 싣는다(Agent API = token-auth). **헤더는 로그·예외·에러 이벤트에 통째로 출력하지 않는다.** 정확한 헤더명/scheme은 서버 계약(Epic 5)이라 본 스토리는 합리적 기본(`Bearer`)을 쓰고 **Epic 4↔5 통합 시 재조정 가능**한 seam으로 둔다 — stub 테스트는 "헤더에 token이 실렸고 로그엔 안 샌다"만 검증. [Source: architecture.md(178-179·476), operations-security-test-contract.md(10·93), project-context.md(81)]
  - [x] **transport seam 재사용 + auth 헤더 후방호환 추가:** 4.2 `Transport.post_json(url, body)`/`HttpTransport.post_json`에 **선택 인자 `headers: dict[str, str] | None = None`** 을 **후방호환으로 추가**한다(기본 `None` → register 호출 경로 무변경, register는 본문의 일회용 코드로 인증하므로 헤더 불필요). 새 HTTP seam을 만들지 않는다(재발명 금지 — 단일 outbound seam 유지). **4.2 register 테스트와 4.1 가드(stdlib·deps 핀·third-party root)가 그대로 green** 이어야 한다. [Source: src/rider_agent/registration.py(63-114), project-context.md(24), tests/agent/test_agent_package.py(194-213), 4-2 스토리 Findings(214)]
  - [x] **헤더 병합(주의):** `HttpTransport.post_json`은 이미 `Request(headers={"Content-Type": "application/json"})`를 세팅한다(registration.py:86-91). 주입 `headers`는 그 기존 dict에 **병합**한다 — `Content-Type`을 **덮어쓰지(drop) 말 것**(예: `{**{"Content-Type": ...}, **(headers or {})}`). [Source: src/rider_agent/registration.py(86-91)]
  - [x] **에러 메시지 문맥(주의·관측성):** 현 `HttpTransport.post_json`의 `TransportError` 메시지는 register 전용 문자열(`"agent register HTTP error"` 등, registration.py:96·100·106·111)이다. heartbeat 5xx가 "agent **register** …"로 뜨면 운영 로그가 오해된다. **권장:** 메시지를 operation-label 인자(예: `op="agent register"|"agent heartbeat"`)로 파라미터화하는 **작은 후방호환 리팩터**(기본값=현 문자열 → register 테스트 green). 부담되면 4.3에서는 그대로 두되(메시지엔 secret 없음, status_code만 surfacing) **이 결정을 Completion Notes에 1줄 명시**한다. 어느 쪽이든 secret 비노출 정책은 불변. [Source: src/rider_agent/registration.py(55-114·92-111)]
  - [x] `HttpTransport`처럼 **비-2xx 본문을 읽지 않고 status_code만 surfacing**(`TransportError`)하는 기존 정책을 유지/계승해 응답 본문에 섞인 secret 노출을 막는다. [Source: src/rider_agent/registration.py(92-114)]
- [x] **Task 3 — `heartbeat.py`: 주기 리포터 loop primitive(30~60s·sync threading·복원력) (AC: 1, 2)**
  - [x] interval `[30, 60]`초 검증/clamp 헬퍼 + 주기 보고 루프 primitive를 둔다(예: `HeartbeatReporter`(stop event·interval·주입 transport/identity/sleep) 또는 `run_heartbeat_loop(...)`). **offline 판정(서버 2분 임계)에 load-bearing한 것은 상한 clamp(≤60)** 다 — 60 초과를 막아야 2분 무신호가 정상 지연이 아닌 ≥2회 누락이 된다. 하한 30은 서버 rate-limit 보호일 뿐. **`asyncio` 금지** — `threading.Event`(stop)와 **주입 가능한 `sleep`**(기본 `time.sleep`)으로 짠다(테스트가 fake sleep으로 결정적 검증). [Source: implementation-contract.md P3-03(60), architecture-contract.md(88-94), 4-1 sync 가드(176-187)]
  - [x] **best-effort 복원력(AC2.5 — 핵심):** 한 주기 `send_heartbeat`가 예외(`TransportError` 등)를 던져도 **루프가 죽지 않고 다음 주기로 진행**한다. 에러는 `redact`/`redacted_error_event`로 마스킹해 기록. `401`/revoke 응답은 `TransportError.status_code == 401`로 분기 감지해 **재등록 필요 상태로 surfacing**하되 crash·무한 즉시 스핀 없음. **surfacing 어휘는 4.2 `secure_store`의 `TOKEN_STATUS_*`/`TokenValidation.needs_registration`(또는 그와 호환되는 enum-free 상태)를 재사용**한다 — 새 ad-hoc 플래그를 발명해 4.2/4.4와 drift 만들지 않는다. 실제 중단/재등록 반응 배선은 4.4/운영 소유. [Source: operations-security-test-contract.md(93·25), src/rider_crawl/redaction.py(130·248), src/rider_agent/secure_store.py(50-54·300-338), src/rider_agent/registration.py(55-60)]
  - [x] **start_heartbeat_thread 배선은 4.4 — 본 스토리는 thread로 띄울 수 있는 primitive만 제공**한다(`threading.Thread(target=reporter.run)` 형태로 4.4가 startup에서 띄움). `__main__.py`/메인 run 루프에 자동 기동을 **추가하지 않는다**. [Source: architecture-contract.md(88-94), epics.md Story 4.4(760-785)]
- [x] **Task 4 — 테스트: `tests/agent/test_heartbeat.py` (AC: 1~6)** — 외부 호출 없음(fake transport/주입 sleep/주입 identity), 가짜 값만:
  - [x] **위치/네이밍:** `tests/agent/test_heartbeat.py`(평면, `__init__.py` 미추가 — 4.1/4.2 미러 컨벤션). 신규 basename. [Source: 4-2 스토리(68), architecture.md(461), pyproject.toml(testpaths)]
  - [x] **(AC1 — payload·5필드·agent_version):** fake transport에 대해 `build_heartbeat_payload`/`send_heartbeat`가 본문에 `agent_id`/`metrics`/`capabilities`/`active_jobs`/`kakao_status`/`browser_profiles`/`agent_version` 7키를 올바로 구성하고, 주입 provider가 payload에 반영됨을 단언. POST URL == `…/v1/agents/heartbeat`. [Source: data-api-contract.md(67-69)]
  - [x] **(AC1 — interval clamp):** `[30,60]` 밖 값(예: 5, 600)을 넣으면 범위 안으로 보정됨을 단언(경계 30·60 포함). [Source: implementation-contract.md P3-03(60)]
  - [x] **(AC1 — 응답 파싱):** fake가 `{"server_time","config_version","commands"}`를 주면 그 셋이 결과로 파싱됨을 단언. `commands` 실행 로직은 검사 대상 아님(파싱까지만). [Source: data-api-contract.md(69)]
  - [x] **(AC2 — 주기·복원력):** 주입 fake sleep + stop event + 호출 카운터로 루프가 **N회 보고 후 정지**(결정적, 실 sleep/네트워크 0)함을 단언. 한 주기 `TransportError` → **루프 계속**(다음 주기 호출됨)·thread 미사망·에러 redact됨을 단언. `401`/revoked stub → 재등록필요 surfacing(평문 token 없음)·crash 없음. [Source: operations-security-test-contract.md(93·25)]
  - [x] **(AC3 — capabilities):** 기본 capabilities에 6 job type이 모두 포함됨을 단언하되 **"정확히 6개"로 잠그지 않는다**(superset 허용 — 후속 확장 무탈). 주입 capabilities가 반영됨도 단언. [Source: architecture-contract.md(120-129), memory/enum-member-count-locks]
  - [x] **(token-auth 헤더·비노출):** `send_heartbeat`가 `Authorization` 헤더에 token을 실음을 (전달 인자/주입 transport 캡처로) 단언하고, **로그 캡처 텍스트·예외 메시지·payload 본문 어디에도 평문 token이 없음**(`assert fake_token not in captured_log and fake_token not in json.dumps(body)`)을 단언. 헤더 dict 통째 로깅 금지 확인. [Source: operations-security-test-contract.md(14-19·93), src/rider_crawl/redaction.py(130)]
  - [x] **(누출 가드):** 모든 fixture는 가짜 값만 — 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/agent token/`chat_id=<digits>`/한국 휴대폰/이메일·OTP 원문 금지. 실제 Telegram/Kakao/Gmail/브라우저/네트워크 미호출. [Source: project-context.md(55·81), epic-3-retro-2026-06-13.md(109·118)]
  - [x] **(`__main__` import 주의):** 만약 어떤 테스트가 `rider_agent.__main__`을 건드리면 **모듈 top-level import를 피하고 함수 내부로 defer**한다(runpy RuntimeWarning 회피). 본 스토리 테스트는 heartbeat만 보므로 보통 불필요. [Source: memory/agent-main-runpy-warning]
- [x] **Task 5 — 회귀·범위·누출 검증 및 마무리 (AC: 1~6)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/agent/test_agent_package.py`·`test_registration.py`·`test_secure_store.py`·`tests/server/`·`tests/test_*.py`), 신규 케이스만큼만 증가가 정상. [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] **4.1 가드 green 재확인(핵심):** `pytest tests/agent/test_agent_package.py -q` 의 (a) third-party root == `{rider_crawl}`, (b) sync(자기 모듈 async 0), (c) 단방향 import, (d) pyproject deps **정확히 9개·핀 불변**이 **신규 `heartbeat.py` 추가 + `registration.py` `headers` 인자 추가 후에도 통과**함을 확인한다. stdlib(`urllib`/`threading`/`time`/`json`/`socket`/`platform`/`os`)만 썼다면 green. 깨지면 새 third-party import가 샌 것이니 제거. [Source: tests/agent/test_agent_package.py(194-213), 4-2 스토리(77)]
  - [x] **4.2 register 무회귀:** `Transport`/`HttpTransport`에 `headers` 추가가 **후방호환**(기본 None)임을 `tests/agent/test_registration.py` green으로 확인 — register 호출은 헤더를 전달하지 않으므로 동작 불변. [Source: src/rider_agent/registration.py(63-114), 4-2 스토리 File List]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `src/rider_agent/heartbeat.py` + 신규 `tests/agent/test_heartbeat.py` + `src/rider_agent/registration.py`(headers 선택 인자만) + sprint-status만** 보이고 **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`·`__main__.py` 변경 0줄**임을 확인(CRLF/LF 노이즈 무시 — `git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 token 0건, `src/rider_crawl/`에 `rider_agent` import 신규 0건. **agent token은 텔레그램 봇-토큰 shape(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)와 다를 수 있으니** shape grep만 믿지 말고 테스트가 쓰는 가짜 token 리터럴(`agtok-fake-…`)이 비-테스트 코드/로그에 안 새는지도 별도 grep으로 교차 확인한다. [Source: project-context.md(64·81), epic-3-retro-2026-06-13.md(118)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(dev 노트에 잠정 수치 박지 말 것 — Epic 2/3·4.1·4.2에서 stale 수치 MEDIUM 재발). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **거의 순수 additive**다: 신규 `src/rider_agent/heartbeat.py` + 신규 `tests/agent/test_heartbeat.py` + `src/rider_agent/registration.py`의 **`headers` 선택 인자 후방호환 추가**. **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`·`__main__.py`는 무변경.**
- **건드리지 않는다:** `rider_crawl` 전부(재사용만), job claim/lease/active_jobs 소스(4.4), BrowserProfileManager/browser_profiles 소스(4.5), KakaoSenderWorker/kakao_status 소스(4.6), autostart(4.7), 배민·쿠팡 auth(4.8·4.9), 서버 측 heartbeat 수신·offline 판정·Admin 표시(Epic 5), `start_heartbeat_thread()` 실제 배선·메인 run 루프(4.4). **빈 stub 파일도 만들지 않는다.** [Source: epics.md Story 4.4~4.9(760-903), architecture.md(450-457)]

### 설계 결정 — 왜 stdlib 재사용이고 `pyproject`/`rider_crawl` 무변경인가 (반드시 읽을 것)

- **HTTP = 4.2 `Transport`/`HttpTransport` 재사용(새 seam·새 의존 금지).** 4.2가 이미 stdlib `urllib` 기반 주입 가능 outbound JSON POST seam을 만들었다(`registration.py:63-114`). heartbeat는 같은 seam을 쓰되 **auth 헤더 전달용 선택 인자 `headers=None` 만 후방호환 추가**한다 — `requests`/`httpx`/별도 HTTP 클라이언트는 ADD-3(새 프레임워크 0)·4.1 import-root 가드 위반. 단발 테스트는 fake transport로 네트워크 없이 검증(`run_once`가 crawler/sender를 주입하는 규율). [Source: src/rider_agent/registration.py(63-114), project-context.md(24·35·42)]
- **주기 루프 = stdlib `threading`+`time`(asyncio 금지).** Agent는 sync 런타임이다(`__init__` 규약·4.1 가드). 주기 heartbeat는 `threading.Event`(stop)+주입 `sleep`로 짠다 — `asyncio`를 직접 import하면 4.1 `test_rider_agent_modules_are_pure_sync` 가 실패한다. sleep 주입으로 테스트는 실 대기 없이 결정적. [Source: tests/agent/test_agent_package.py(176-187), src/rider_agent/__init__.py(5-6), architecture-contract.md(88-94)]
- **metrics에 `psutil` 금지(deps-pin 가드).** 시스템 metrics가 탐나도 새 third-party를 추가하면 `test_pyproject_dependencies_unchanged_pins`(정확히 9개)와 `test_rider_agent_only_third_party_root_is_rider_crawl`이 **둘 다** 깨진다. stdlib나 주입 provider로 충당하고, 실제 풍부한 metrics는 후속/운영 소유. [Source: tests/agent/test_agent_package.py(194-213), 4-2 스토리(23), memory/enum-member-count-locks]
- **`rider_crawl` 무변경 = 무회귀 안전 마진.** epic-3-retro(158): "**`rider_crawl` 0줄**이 안전 마진 … Epic 4 `rider_agent`도 동일 규약." heartbeat는 `redaction`·`config.app_state_root`·`secret_store.SecretStore`를 **import만** 한다. [Source: epic-3-retro-2026-06-13.md(158), project-context.md(64)]
- **capabilities/status는 평문 상수(enum·"정확히 N" lock 금지).** `secure_store.py`가 `TOKEN_STATUS_*`를 enum 대신 평문 상수로 둔 이유(50-54)와 동일 — 후속 워커(4.5/4.6/4.8/4.9)가 job type·kakao 상태를 늘려도 "정확히 N개" lock이 없어 다른 테스트를 깨지 않는다. capabilities 테스트는 **superset 단언**(포함 확인)으로만 짠다. [Source: src/rider_agent/secure_store.py(50-54), memory/enum-member-count-locks]

### 서버 부재 — stub/mock 검증이 4.x 형태 (4.1·4.2 계승)

- `POST /v1/agents/heartbeat` 의 **서버 측 수신·`agents.last_heartbeat_at` 기록·offline/degraded 판정·버전 drift 표시는 Epic 5**(FastAPI/PostgreSQL `agents` 테이블·Admin). 4.3은 **client + 주입 transport stub**로 (a) 정상 보고·5필드+버전 payload, (b) 단발 실패 복원력, (c) 401/revoke surfacing, (d) interval clamp 4 시나리오를 검증한다. epic-3-retro(108): "서버 측 …은 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." [Source: epic-3-retro-2026-06-13.md(108), data-api-contract.md(67-69·35 `agents` 테이블)]
- **AC2 판정 소유 분리(재강조):** "2분 무신호 → offline" 임계와 "버전 ≠ 기대 → 식별"의 **판정은 서버**. Agent는 (a) 30~60s 주기 유지, (b) `agent_version` 동봉 — 두 입력 제공까지만. monitoring metric `agent_last_heartbeat`(2분 임계)는 운영 contract 25행 정의. [Source: operations-security-test-contract.md(25), epics.md AC(751-758)]
- **primitive 소유 분리:** 4.3은 heartbeat 리포터 **primitive**(payload·send·loop)만. `start_heartbeat_thread()`로 startup에 **배선**하는 것은 Story 4.4(메인 run 루프)다 — 4.2가 `validate_agent_token()`만 주고 claim 배선을 4.4로 위임한 것과 동형. architecture-contract(88-94) startup `…→ start_heartbeat_thread() → …`에서 4.3은 thread가 돌릴 loop를, 4.4가 thread 기동·메인 루프를 채운다. [Source: architecture-contract.md(88-94), 4-2 스토리(99), epics.md Story 4.4(760-785)]

### 재사용 대상 공개 표면 (재구현 금지 — import만)

| 도메인 | 공개 심볼 | 파일/행 | 4.3 사용 |
|---|---|---|---|
| Agent identity | `AgentIdentity`(`agent_id`/`agent_token`/`config_version`), `load_local_agent_identity` | rider_agent/secure_store.py(213·273) | heartbeat 주체(agent_id·인증 token) |
| outbound HTTP seam | `Transport` Protocol(`post_json`), `HttpTransport`, `TransportError`, `_register_url` 패턴 | rider_agent/registration.py(63·73·55·156) | heartbeat POST(+`headers` 후방호환 추가) |
| token 게이트 | `validate_agent_token`/`TokenValidation`(`needs_registration`) | rider_agent/secure_store.py(303·319) | 401/revoke surfacing 정합 |
| redaction | `redact(text)`, `redacted_error_event(code,msg,err)`, `REDACTED` | rider_crawl/redaction.py(130·248·44) | 로그/에러에서 token 마스킹 |
| 상태 루트 | `app_state_root()`, `default_agent_state_dir()` | rider_crawl/config.py(158), rider_agent/secure_store.py(60) | (필요 시) 경로 — per-machine 단일 |
| 버전 | `rider_agent.__version__` | rider_agent/__init__.py(32) | heartbeat payload `agent_version` |

- 모두 **import/재사용만** — 시그니처 변경·`rider_crawl` 수정 금지. `Transport`/`HttpTransport`만 **후방호환 선택 인자 추가** 허용. [Source: 위 파일/행, project-context.md(64)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl`·`rider_server`·`pyproject.toml`·`__main__.py` 무변경** — `git diff -w` = 신규 `heartbeat.py` + 신규 테스트 + `registration.py`(headers 선택 인자) + sprint-status만. (b) **의존성 단방향·sync** — 신규 모듈도 `rider_crawl`/자기 패키지만 import, async 0, `threading`로 주기(4.1 가드 자동). (c) **새 프레임워크/의존 0** — third-party root는 `rider_crawl`만, deps 정확히 9개 유지(psutil 금지) → 4.1 가드 green. (d) **token 평문 0** — Authorization 헤더에만, 로그/payload/예외/에러 이벤트에 token 평문·헤더 dict 통째 출력 없음. (e) **best-effort 복원력** — 단발 실패가 리포터 thread를 죽이지 않음, 401/revoke는 crash·무한 스핀 없이 surfacing. (f) **4.2 register 무회귀** — `headers` 기본 None이라 register 경로 불변. (g) **누출 0** — 테스트 실제 외부 미호출, 가짜 값만. [Source: project-context.md(24·55·64·81·82), operations-security-test-contract.md(14-19·93), tests/agent/test_agent_package.py]

### 이전 스토리/회고 인텔리전스 (4.1·4.2 → 4.3 이월 교훈)

- **4.2가 깐 토대 위에 빌드(직접 계승):** 4.2는 `AgentIdentity`/`load_local_agent_identity`(identity)·`Transport`/`HttpTransport`(outbound HTTP seam)·`validate_agent_token`(게이트)을 만들고 "heartbeat는 Story 4.3"으로 명시 위임했다(4-2 스토리 17·24). 4.3은 그 identity로 heartbeat 주체를 식별하고 그 transport seam으로 POST한다 — 4.2가 "건드리지 않는다(4.3)"로 비워둔 바로 그 영역. [Source: 4-2 스토리(17·24·99), src/rider_agent/{secure_store,registration}.py]
- **secret 누출 비용 급등(A1″ 계승):** 4.2부터 실제 token을 다루므로 누출 비용이 크다(retro 109). heartbeat는 매 주기 token을 헤더에 싣는 **반복 노출 표면**이라 더 주의 — 헤더/에러/로그에서 token이 새지 않게 `redact` + 가짜값 규칙을 계속 적용한다. [Source: epic-3-retro-2026-06-13.md(109·118·168), 4-2 스토리(120)]
- **테스트 수치/File List 단일 정본(A2″ 계승):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. 4.1(dev 9→리뷰 14)·4.2(dev 1037→리뷰 1060) 모두 stale로 MEDIUM이 났다. [Source: epic-3-retro-2026-06-13.md(59), 4-2 스토리(167·213), memory/stale-test-count-a2]
- **부정 가드는 AST로(4.1 계승, 자동 적용):** 단방향·sync·no-new-framework 가드는 4.1이 AST로 짜 `src/rider_agent/*.py`를 glob한다 — 신규 `heartbeat.py`는 자동 검사. 새 가드를 raw grep으로 짜지 말 것(scope docstring이 `asyncio`/`rider_server`/`active_jobs`(4.4) 같은 금지·후속 심볼명을 문자열로 언급해 오탐). [Source: tests/agent/test_agent_package.py(1-8·176-233), memory/negative-guard-tests-use-ast]
- **enum/lock 전수 점검(memory):** capabilities·status를 enum이나 "정확히 N개" 테스트로 잠그지 말 것 — 후속 job type 추가가 여러 테스트의 "정확히 N" lock을 깨는 패턴(secure_store가 평문 상수로 피한 이유). [Source: memory/enum-member-count-locks, src/rider_agent/secure_store.py(50-54)]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest는 **WSL의 `python3`가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`** 로 돌린다(WSL python엔 pytest 미설치). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w`로 하고 무관한 EOL flip을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]
- **주기 루프 테스트 주의:** 실 `time.sleep`/실 thread 장기 대기를 쓰지 말고 **주입 fake sleep + stop event + 호출 카운터**로 N회 후 정지를 결정적으로 검증한다(테스트 hang/flaky 방지). [Source: architecture-contract.md(88-94)]

### Project Structure Notes

- 신규 파일은 architecture.md(450) 트리와 정렬: `src/rider_agent/heartbeat.py`(= `# 30~60s`). 트리의 `job_loop.py`/`browser_profile.py`/`workers/`/`auth/`/`autostart.py`는 각 후속 스토리(4.4~4.9)가 만든다 — **계획된 부분 구현이지 이탈이 아니다**(4.1·4.2·Epic 2/3 retro의 "부분 구현은 계획" 판정). [Source: architecture.md(448-457), 4-2 스토리(133)]
- 테스트는 `tests/agent/test_heartbeat.py`(평면, `__init__.py` 미추가). 기존 `tests/agent/test_{agent_package,registration,secure_store}.py`와 별 basename. [Source: architecture.md(461), 4-2 스토리(68·134)]
- **변이/충돌:** `project-context.md`의 `rider_agent` 진전 반영은 **Epic 4 retro**에서 한다(rider_server를 Epic 2 retro에서 반영한 선례). 본 스토리에서 project-context.md는 수정하지 않는다. [Source: 4-2 스토리(136), project-context.md(114)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.3(738-758)] — user story + AC(30~60s heartbeat·5필드 payload·offline/버전·capabilities).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-4(694-696)] — Epic 4 범위(서버 stub/mock 검증, FR-12~20·25·28·32).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P3-03(60)] — "Report heartbeat every 30-60 seconds. Admin shows online/offline state."
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md#Agent-API(67-69)] — `POST /v1/agents/heartbeat` 요청(agent_id·metrics·capabilities·active_jobs·kakao_status·browser_profiles)/응답(server_time·config_version·commands). `agents` 테이블(35).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md#Agent-Loop(88-107)·Job-Types(120-129)] — startup `start_heartbeat_thread()`·6 job type(capabilities 기본값).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Monitoring-Metrics(22-31)·Redaction(14-19)·Forbidden(87-95)] — `agent_last_heartbeat` 2분 임계·token 로깅 금지.
- [Source: _bmad-output/planning-artifacts/architecture.md#Auth-Security(178-185)·API-Comm(189-191)·Boundaries(476)·heartbeat-tree(450)] — token-auth·outbound-only·Agent API 5종·heartbeat.py 위치.
- [Source: src/rider_agent/registration.py(43-48·55-114·156-158)] — `Transport`/`HttpTransport`/`TransportError`/`_register_url`(재사용·`headers` 후방호환 추가 대상).
- [Source: src/rider_agent/secure_store.py(50-54·213-230·273-338)] — `AgentIdentity`·`load_local_agent_identity`·`TOKEN_STATUS_*`(상수 패턴)·`validate_agent_token`.
- [Source: src/rider_agent/__init__.py(32)] — `__version__`(payload `agent_version`).
- [Source: src/rider_crawl/redaction.py(44·130·248)] — `REDACTED`/`redact`/`redacted_error_event`(로그·에러 마스킹 재사용).
- [Source: tests/agent/test_agent_package.py(176-233)] — 4.1 가드(sync·third-party root==rider_crawl·단방향·deps 9핀) — 신규 모듈 자동 적용·green 유지.
- [Source: _bmad-output/implementation-artifacts/4-2-등록-코드-입력과-agent-토큰-보안-저장.md(17·24·99·133·167·213)] — 4.2 위임(4.3 소유)·transport/identity 토대·stale 수치 교훈.
- [Source: _bmad-output/implementation-artifacts/epic-3-retro-2026-06-13.md(59·108·109·118·158)] — stub/mock 검증·A1″/A2″·rider_crawl 0줄.
- [Source: _bmad-output/project-context.md(24·35·42·53·64·75·81·82·114)] — urllib 정책·주입·run_once 경계·pytest 실행·단방향 import·누출 금지·git diff·retro 반영.
- [Source: memory/dev-env-quirks, memory/stale-test-count-a2, memory/negative-guard-tests-use-ast, memory/enum-member-count-locks, memory/agent-main-runpy-warning] — venv pytest·`git diff -w`, 수치 단일 정본, AST 부정 가드, enum/lock 전수 점검, __main__ runpy 경고.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `.venv/Scripts/python.exe -m pytest -q` — 전체 스위트 green (리뷰 시점 재측정: **1091 passed**).
- `.venv/Scripts/python.exe -m pytest tests/agent/test_agent_package.py tests/agent/test_registration.py -q` — 4.1 가드 + 4.2 register **42 passed**(third-party root==rider_crawl·sync·단방향·deps 9핀·register 무회귀 모두 green).
- 범위 점검: `git diff -w --name-only` → `src/rider_agent/registration.py`(headers/op_label 후방호환) + sprint-status 만 변경, 신규 `src/rider_agent/heartbeat.py` + `tests/agent/test_heartbeat.py`. `rider_crawl/`·`rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`·`__main__.py` **0줄**.
- 누출 grep: 비-테스트 src 에 `agtok-fake` 0건, 신규 파일에 텔레그램 봇-토큰 shape 0건, `rider_crawl/`→`rider_agent` import 0건.

### Completion Notes List

- **Task 1·3 — payload 빌더 + capabilities + provider seam:** 신규 `src/rider_agent/heartbeat.py` 에 `build_heartbeat_payload`(7키: `agent_id`/`agent_version`/`metrics`/`capabilities`/`active_jobs`/`kakao_status`/`browser_profiles`, token 본문 미포함). capabilities 기본값은 평문 상수 `DEFAULT_CAPABILITIES`(6 job type, enum/"정확히 N" lock 없음 — `TOKEN_STATUS_*` 선례). `active_jobs`(4.4)/`kakao_status`(4.6)/`browser_profiles`(4.5)/`metrics` 는 **값-또는-callable provider**(`_resolve`)로 두고 안전 기본값(`[]`/`"disabled"`/stdlib `default_metrics`). metrics 는 `platform` stdlib 만 — `psutil` 등 새 의존 0.
- **Task 2 — 단발 send + token-auth 헤더 + 응답 파싱:** `send_heartbeat(...) -> HeartbeatResult`. `Authorization: Bearer <token>` 헤더로만 인증(본문/로그/예외에 평문 token 없음). 응답 `server_time`/`config_version`/`commands` 파싱(`commands` 실행은 범위 밖). URL 은 4.2 `_register_url` 패턴과 정합(`HEARTBEAT_PATH`/`RIDER_AGENT_SERVER_URL` env).
- **transport seam — 후방호환 2건:** `Transport.post_json`/`HttpTransport.post_json` 에 선택 인자 `headers: dict|None = None` 추가(기본 None → register 경로 불변, Content-Type 보존 병합). 추가로 **op-label 리팩터를 수행함**(권장안): `HttpTransport(op_label="agent register")` 생성자 인자 — 기본값이 현 문자열이라 register 메시지/테스트 불변이고, heartbeat 호출자는 `op_label="agent heartbeat"` 로 5xx/네트워크 오류 메시지를 구분(운영 로그 오해 방지). 비-2xx 본문 미읽음·status_code 만 surfacing 정책 계승.
- **Task 3 — 주기 loop primitive:** `HeartbeatReporter`(stop `threading.Event` + 주입 `sleep`, `asyncio` 0) + `clamp_interval` `[30,60]`(상한이 offline 판정에 load-bearing). best-effort: 단발 예외가 thread 를 죽이지 않고 다음 주기로 진행, 에러는 `redact`/`redacted_error_event` 마스킹. `401`/revoke 는 `TOKEN_STATUS_REVOKED`/`needs_registration`/`on_status` 콜백으로 surfacing(4.2 어휘 재사용, 새 ad-hoc 플래그 없음) — crash·무한 즉시 스핀 없이 매 주기 sleep. `start_heartbeat_thread()` 배선·`__main__` 자동 기동은 4.4 소유라 추가 0.
- **AC2 소유 분리 준수:** Agent 는 (a) 30~60s 주기 유지 (b) `agent_version` 동봉의 "판정 입력" 만 제공 — offline/버전-drift 판정·Admin 표시는 Epic 5. Agent 스스로 offline 판정/화면 그리기 0.
- **테스트:** `tests/agent/test_heartbeat.py` **31 케이스**(test 함수 25개, `clamp_interval` parametrize 7) — fake transport/주입 sleep+stop event+카운터/주입 identity, 가짜 값만, 실 네트워크·sleep·thread 0. payload 7키+버전, interval clamp(경계 포함), 응답 파싱, N회 후 정지, 단발 실패 후 계속, 401 surfacing, capabilities superset, Bearer 헤더+평문 비노출, 실 `HttpTransport`(fake urlopen) 헤더 병합·op-label·E2E.

### File List

- `src/rider_agent/heartbeat.py` (신규) — heartbeat payload 빌더 + 단발 send + 주기 리포터 loop primitive.
- `src/rider_agent/registration.py` (수정) — `Transport`/`HttpTransport.post_json` 에 후방호환 `headers` 선택 인자 + `HttpTransport` `op_label` 생성자 인자(기본값=현 문자열, register 불변).
- `tests/agent/test_heartbeat.py` (신규) — 4.3 heartbeat 검증 31 케이스(test 함수 25개, `clamp_interval` parametrize 7).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정) — 4.3 상태 ready-for-dev → in-progress → review.

### Change Log

- 2026-06-13 — Story 4.3 구현: heartbeat payload 빌더 + 단발 `send_heartbeat`(token-auth 헤더) + 주기 `HeartbeatReporter` loop primitive 신설. `registration.py` 에 후방호환 `headers` 선택 인자 + `op_label` 추가(register 동작 불변). 전체 스위트 1091 passed, 4.1 가드·4.2 register green, 범위/누출 점검 clean. Status → review.
- 2026-06-13 — Story 4.3 리뷰(자동, adversarial): AC1~3 모두 IMPLEMENTED, secret 비노출·scope·4.1/4.2 무회귀 확인. CRITICAL/HIGH 0건. MEDIUM 2건(stale 수치)만 발견·자동 수정 — 전체 스위트 1086→**1091 passed** 재측정, heartbeat 케이스 26→**31** 정정. Status → done.

## Senior Developer Review (AI)

- **Reviewer:** 이상윤 (story-automator-review, adversarial) · **Date:** 2026-06-13 · **Outcome:** ✅ Approve (auto-fix 적용 후)
- **검토 범위:** File List 전 파일 정독 + `git status/diff -w` 대조 + AC1~3 구현 검증 + 운영 venv 전체 스위트 재측정. `_bmad/`·`_bmad-output/` 산출물은 리뷰 제외(automation 아티팩트).

### AC 검증 (모두 IMPLEMENTED)

- **AC1 — 30~60s POST + 7키 payload + agent_version + 응답 파싱:** `build_heartbeat_payload`(7키, token 본문 미포함) · `send_heartbeat` → `POST /v1/agents/heartbeat` · `clamp_interval` `[30,60]`(경계 포함) · `_result_from_response`(server_time/config_version/commands, 비-list commands→`[]` 방어). ✓
- **AC2 — 판정 입력 제공 + best-effort 복원력:** 상한 clamp(≤60) load-bearing · `report_once` 가 `TransportError`/일반 예외를 모두 흡수해 thread 미사망 · `401`→`TOKEN_STATUS_REVOKED`/`needs_registration`/`on_status` surfacing, 매 주기 sleep(무한 스핀 없음) · 판정/Admin 은 Epic 5 소유로 정확히 위임. ✓
- **AC3 — capabilities = job type 6종:** `DEFAULT_CAPABILITIES` 평면 상수 tuple, 테스트는 **superset** 단언("정확히 N" lock 없음). ✓

### 보안 / 회귀 / 범위

- **Secret 비노출:** `agent_token` 은 `Authorization: Bearer` 헤더에만 — payload/로그/예외/에러 이벤트 평문 0. `redacted_error_event`+`redact` 마스킹, 헤더 dict 통째 로깅 없음, 비-2xx 본문 미읽음. 테스트 + 코드로 교차 확인. ✓
- **무회귀:** `registration.py` `headers`/`op_label` 모두 기본값=현 동작이라 후방호환. 4.1 가드(third-party root==rider_crawl·sync·단방향·deps 9핀) + 4.2 register **42 passed**. ✓
- **범위:** `git diff -w` = `heartbeat.py`(신규)+`registration.py`(선택 인자 2)+`test_heartbeat.py`(신규)+`sprint-status.yaml` 만. `rider_crawl/`·`rider_server/`·`pyproject.toml`·`__main__.py` **0줄**. ✓

### Findings

- 🟡 **MEDIUM(수정 완료):** Dev Agent Record 의 stale 수치 — 전체 스위트 1086→**1091**, heartbeat 케이스 26→**31** 로 정정(qa-e2e 가 dev 노트 이후 케이스 추가한 재발 패턴; memory: stale-test-count).
- 🟢 **LOW(정보):** 재사용 표는 `TokenValidation.needs_registration` 을 인용하나 구현은 `TOKEN_STATUS_*` 상수 + 자체 `needs_registration` 프로퍼티(호환 enum-free 상태)를 씀 — 스토리가 명시 허용한 형태라 조치 불필요.
- **CRITICAL/HIGH: 없음.**

### 검증 명령(리뷰 시점 재측정)

- `.venv/Scripts/python.exe -m pytest -q` → **1091 passed**
- `.venv/Scripts/python.exe -m pytest tests/agent/test_heartbeat.py tests/agent/test_agent_package.py tests/agent/test_registration.py -q` → **73 passed**(heartbeat 31 + 4.1 가드/4.2 register 42)
