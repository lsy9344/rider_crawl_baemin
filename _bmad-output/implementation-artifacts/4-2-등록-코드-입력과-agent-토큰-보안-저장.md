---
baseline_commit: fbe3b33
---

# Story 4.2: 등록 코드 입력과 Agent 토큰 보안 저장

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 작업 노드 관리자,
I want 운영자가 발급한 **일회용 registration code**로 이 Windows PC(Agent #1)를 서버에 등록(`POST /v1/agents/register`)해 발급받은 `agent_id`/`agent_token`/`tenant_scope`/`config_version` 중 **secret인 `agent_token`은 Agent-local OS 보안 저장소(DPAPI)에**, 비밀이 아닌 식별/설정값은 `agent_config.json`에 분리 저장하고, **token이 없거나 만료/revoke되면 그 Agent가 job을 받지 못하도록** 막는 토큰 유효성 게이트(primitive)까지 세우고 싶다,
so that Agent가 자신을 안전하게 식별하면서도 **평문 token이 로그·config·디스크 텍스트에 절대 노출되지 않고**(ADD-15), 이후 4.3 heartbeat·4.4 job claim 루프가 이 "유효한 identity가 있어야만 동작" 게이트 위에 additive로 빌드된다(P3-02, FR-12·16, NFR-7·8, ADD-6·15).

> **이 스토리의 성격 — "등록 클라이언트(register) + `agent_token` DPAPI 보안 저장 + token 유효성 게이트 primitive"만.** heartbeat도, job claim/lease 루프도, BrowserProfileManager도, KakaoSenderWorker도 아니다. P3-02 deliverable은 **"registration code 입력과 secure `agent_id`/`token` 저장. 일회용 코드로 Agent가 서버에 등록된다"** 가 전부다(implementation-contract P3-02: "Implement registration code entry and secure agent_id/token storage. One-time code registers Agent in server."). **heartbeat(30~60s 보고)는 Story 4.3(P3-03), outbound HTTPS job polling/claim/complete+lease는 4.4(P3-04), BrowserProfileManager는 4.5(P3-05), KakaoSenderWorker FIFO queue는 4.6(P3-06), autostart는 4.7(P3-07), 배민/쿠팡 인증은 4.8·4.9, 서버 측 register/revoke 엔드포인트·queue·Admin은 Epic 5 소유다.** 본 스토리는 4.1이 깐 패키지 토대(`__init__`/`__main__`/`reuse`) 위에 **`registration.py`+`secure_store.py`(+ 식별값 영속 + token 게이트) + `tests/agent/` 테스트만** 얹는다. [Source: implementation-contract.md P3-02(59), epics.md Story 4.2(715-736)·Story 4.3~4.9(738-903), architecture.md(446-457)]
>
> **서버가 아직 없다 — "서버 stub/mock에 대한 동작 검증"이 4.x 테스트 형태(절대 전제).** `POST /v1/agents/register`·token revoke의 **서버 측 구현은 Epic 5 소유**다(FastAPI/DB). 따라서 본 스토리는 실제 HTTP 서버를 띄우지 않고 **주입된 fake transport**(canned JSON 응답)에 대해 등록/거부/revoke 시나리오를 검증한다. epic-3-retro(108): "Epic 4는 Epic 3의 정책/레코드를 처음 '런타임에 배선'하는 쪽으로 가지만, 서버 측 job 생성·queue·Admin은 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." [Source: epics.md Epic 4(696), epic-3-retro-2026-06-13.md(108), architecture-contract.md(87-107)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 `src/rider_agent/{registration.py, secure_store.py}` + 신규 `tests/agent/` 테스트, 그리고 등록 진입을 위한 `__main__.py`의 **얇은** CLI wiring(`register` 서브커맨드)만. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(절대 규칙·무회귀 안전 마진).** 본 스토리는 `rider_crawl.secret_store.SecretStore` Protocol·`rider_crawl.redaction`·`rider_crawl.config.app_state_root`를 **import해서 재사용만** 한다. epic-3-retro(158): "**`rider_crawl` 0줄**이 안전 마진 … Epic 4 `rider_agent`도 동일 규약(rider_crawl만 import, sync 유지)." [Source: project-context.md(64·82), architecture.md(482-484), epic-3-retro-2026-06-13.md(101·158)]
> - **`pyproject.toml` `[project].dependencies` — 0줄 변경(권장 설계로 달성).** HTTPS는 stdlib `urllib`(텔레그램 선례, project-context 24), DPAPI는 stdlib `ctypes`(crypt32.dll)로 구현해 **새 third-party 의존을 추가하지 않는다.** 이렇게 해야 4.1이 잠근 가드 `tests/agent/test_agent_package.py`(third-party import root == `{rider_crawl}`)가 **그대로 green**으로 유지된다. → **만약** 부득이 `pywin32` 등 새 의존을 추가하면 그 4.1 가드가 깨지므로 같은 스토리에서 해당 import-root allowlist 테스트를 함께 갱신해야 한다(memory: enum-member-count-locks와 동형의 "추가가 기존 lock을 깬다" 패턴 — 권장은 stdlib로 회피). [Source: project-context.md(24), architecture.md(107-108·124), 4-1 스토리 Completion Notes(184), memory/enum-member-count-locks]
> - **heartbeat / job claim·lease / events·complete** → **4.3 / 4.4**. `heartbeat.py`·`job_loop.py` 미생성. 본 스토리의 token 게이트는 "유효 token 없으면 job 미수신"의 **primitive(`validate_agent_token`)** 만 제공하고, 실제 claim 루프 배선은 4.4가 이 primitive를 import해서 한다. [Source: epics.md Story 4.3(738-758)·4.4(760-785), architecture-contract.md(88-107)]
> - **BrowserProfileManager / KakaoSenderWorker / autostart / 배민·쿠팡 auth** → **4.5~4.9**. `browser_profile.py`·`workers/`·`autostart.py`·`auth/` 미생성. [Source: epics.md Story 4.5~4.9(787-903)]
> - **서버 측 `POST /v1/agents/register`·token revoke·rotate 엔드포인트·tenant 격리 DB** → **Epic 5**(FastAPI/PostgreSQL). 본 스토리는 client + stub 검증만. [Source: epics.md Epic 5(904-), data-api-contract.md(42-65)]
> - **Gmail OAuth token 저장(고객/mailbox 격리)** → **Story 4.9**. `secure_store.py`는 **재사용 가능한 일반 DPAPI 백엔드 seam**을 만들되, Gmail token별 mailbox 격리 정책은 4.9 소유다. 본 스토리는 `agent_token` 저장만. [Source: epics.md Story 4.9(877-903), data-api-contract.md(139-140)]
>
> **secret 비노출(ADD-15·NFR-5 — 본 스토리의 핵심 가드).** `agent_token`은 **로그·`agent_config.json`·디스크 텍스트·예외 메시지·스크린샷·에러 이벤트 어디에도 평문으로 남기지 않는다.** 테스트 fixture·docstring에도 실제 토큰/registration code를 넣지 않고 명백한 가짜값만 쓴다(`agtok-fake-…`, `regcode-fake-…`). 로그 출력은 `rider_crawl.redaction.redact`를 통과시킨다. [Source: project-context.md(81), architecture.md(183-185), operations-security-test-contract.md(14-19), epic-3-retro-2026-06-13.md(109·118)]
>
> **sync 런타임 + 단방향 import(4.1 규약 계승 — 자동 검증됨).** 신규 `registration.py`/`secure_store.py`는 **순수 동기**(no `async def`/`await`/직접 `import asyncio`)이고 `rider_crawl`만 import한다(역방향 0, `rider_server` import 0). 이 둘은 4.1이 `src/rider_agent/*.py` **전체를 glob**하는 AST 가드로 이미 검사하므로 신규 모듈도 자동 적용된다 — 규약을 깨면 4.1 테스트가 실패한다. [Source: 4-1 스토리 AC3·AC5(48-51), architecture.md(333-335·484), memory/negative-guard-tests-use-ast]

## Acceptance Criteria

**AC1 — 일회용 registration code로 등록 + 발급 4값 처리 + 1회 등록 멱등 (P3-02, FR-12, ADD-6)**

1. **Given** 운영자가 발급한 일회용 `registration_code`가 있을 때 **When** Agent가 `POST /v1/agents/register`로 등록하면(요청 본문 = `registration_code`, `machine_fingerprint`, `hostname`, `os`, `agent_version` — data-api-contract 44-54) **Then** 응답의 `agent_id`, `agent_token`, `tenant_scope`, `config_version`(data-api-contract 56-64)을 파싱해 **secret(`agent_token`)과 비밀 아님(`agent_id`/`tenant_scope`/`config_version`)을 분리 저장**한다(저장 정책은 AC2). 등록 호출은 **주입된 transport**(stdlib `urllib` 기반 실제 구현 + 테스트용 fake)로 수행하며, 본 스토리 테스트는 실제 서버 없이 fake transport의 canned 응답으로 검증한다. [Source: data-api-contract.md(42-65), implementation-contract.md P3-02(59), architecture-contract.md(87-107), project-context.md(24·35)]
2. **And** 등록은 **로컬 identity 기준 멱등**이다: 이미 유효한 local identity(`agent_id`+유효 `agent_token`)가 있으면 **재등록 POST를 보내지 않고** 기존 identity를 반환한다(일회용 코드를 불필요하게 소모/덮어쓰지 않음). 서버 측 "같은 코드 1회만 유효" 강제는 Epic 5 소유이므로, 본 스토리는 **stub이 "코드 이미 사용됨/무효" 응답을 줄 때** Agent가 평문 token 노출 없이 명확한 실패로 처리하고 기존(있다면) identity를 덮어쓰지 않음을 검증한다. [Source: epics.md AC(723-726), implementation-contract.md P3-02(59), data-api-contract.md(42-65), epic-3-retro-2026-06-13.md(108)]

**AC2 — `agent_token` Agent-local DPAPI 보안 저장 + 평문 비노출 (NFR-8, ADD-15)**

3. **Given** 발급된 `agent_token`을 저장해야 할 때 **When** Agent가 token을 보관하면 **Then** `agent_token`은 **Agent-local OS 보안 저장소(DPAPI; stdlib `ctypes`로 crypt32 `CryptProtectData`/`CryptUnprotectData`)** 에 저장되고, 그 store 파일은 **`agent_config.json`과 물리적으로 다른 파일**이다(secret ↔ config 분리 — Story 2.4 `LocalFileSecretStore`가 ui_settings와 분리한 것과 동형). DPAPI 백엔드는 `rider_crawl.secret_store.SecretStore` **Protocol(`put`/`resolve`)을 그대로 구현**한다(재발명 금지 — seam 재사용). [Source: NFR-8(93), architecture.md(449·181-182·494), architecture-contract.md(85), src/rider_crawl/secret_store.py(42-99), operations-security-test-contract.md(10)]
4. **And** `agent_token`이 **평문으로 로그·`agent_config.json`·디스크 텍스트·예외 메시지에 남지 않는다**: `agent_config.json`에는 `agent_id`/`tenant_scope`/`config_version` 같은 **비밀 아님** 값만 들어가고 token 평문(또는 token ref가 곧 평문인 형태)은 없으며, store 파일을 디스크에서 읽어도 **평문 token 문자열이 존재하지 않는다**(DPAPI 암호화 blob; 비-Windows 테스트에선 주입 fake store로 분리 invariant만 검증). 로그/배너 출력은 `rider_crawl.redaction.redact`를 통과한다. [Source: ADD-15(143), 평문 금지(731), architecture.md(183-185·494), src/rider_crawl/redaction.py(130·44), operations-security-test-contract.md(14-19)]

**AC3 — token 없음/만료/revoke 시 job 미수신 게이트 primitive (NFR-7, FR-16)**

5. **Given** token이 유출·만료·revoke될 수 있을 때 **When** Agent가 시작·동작 중 token 유효성을 확인하면(`validate_agent_token()`) **Then** **유효한 token이 있을 때만** "job 수신 가능" 상태가 되고, **token이 없거나(미등록) 만료/revoke로 판정되면 "job 미수신"** 상태를 반환한다(FR-16: "토큰 없거나 만료 시 job 미수신"). 서버 측 revoke 동작 자체는 Epic 5 소유이므로, 본 스토리는 **stub transport가 revoked/만료(예: 401·`revoked`) 응답을 줄 때** Agent의 게이트가 "미수신"으로 떨어지고 평문 token 노출 없이 재등록 필요 상태로 surfacing함을 검증한다. **실제 claim 루프 배선(이 게이트를 호출해 job을 막는 곳)은 Story 4.4 소유** — 본 스토리는 그 게이트 primitive와 단위 검증만 제공한다. [Source: NFR-7(92), FR-16(47), epics.md AC(733-736), data-api-contract.md(71-73), architecture-contract.md(92·99)]

## Tasks / Subtasks

- [x] **Task 1 — `secure_store.py`: Agent-local DPAPI secret store(seam 재사용) + 식별값 영속 (AC: 2, 3)**
  - [x] `src/rider_agent/secure_store.py` 신설. `rider_crawl.secret_store.SecretStore` **Protocol을 구현**하는 `DpapiSecretStore`(가칭)를 둔다 — `put(value, *, ref="") -> str` / `resolve(ref) -> str | None`. 백엔드는 **stdlib `ctypes`로 crypt32 `CryptProtectData`/`CryptUnprotectData`** 를 호출해 값을 DPAPI 암호화 blob으로 디스크에 보관한다(새 third-party 의존 0). [Source: src/rider_crawl/secret_store.py(42-99), architecture.md(449), architecture-contract.md(85)]
  - [x] **import-safety(필수):** `ctypes`/crypt32 로드와 DPAPI 호출은 **함수 내부 lazy + Windows-gated**로 한다(`rider_crawl.sender`가 pyautogui/pywinauto를 함수 내부에서 lazy import하는 선례와 동형). 그래야 `import rider_agent`·`import rider_agent.secure_store`가 비-Windows(WSL/CI)에서도 **import-safe**하고, 4.1의 "third-party root == `{rider_crawl}`" AST 가드가 깨지지 않는다(`ctypes`는 stdlib라 허용). [Source: src/rider_crawl/sender.py(330-333·628-630), 4-1 스토리 Dev Notes(100-103·184)]
  - [x] **store 파일은 `agent_config.json`과 다른 경로**여야 한다(평문 분리 invariant). 저장 위치는 **per-machine 단일·cwd 독립**이라 `rider_crawl.config.app_state_root()` 아래(예: `app_state_root()/runtime/state/agent/`)를 쓰되, **경로는 주입 가능**하게 해 테스트가 `tmp_path`로 격리한다(텔레그램 offset이 `app_state_root()` 고정 루트를 쓰는 선례와 동형 — log_dir 스코프 아님). [Source: src/rider_crawl/config.py(158-182), src/rider_crawl/telegram_commands.py(607), src/rider_crawl/secret_store.py(60-63)]
  - [x] **식별값 영속 헬퍼:** `agent_config.json`(비밀 아님 — `agent_id`/`tenant_scope`/`config_version` 등)을 `ensure_ascii=False, indent=2` JSON으로 저장/로드한다(기존 설정 저장 스타일 유지). 쓰기는 Story 2.2 atomic write(`rider_crawl.ui_settings._atomic_write_text` — secret_store가 재사용한 것과 동일)를 재사용해 손상/`.tmp` 잔여물을 막는다. **token은 절대 이 파일에 넣지 않는다.** [Source: project-context.md(68·37), src/rider_crawl/secret_store.py(93-98), architecture-contract.md(85)]
  - [x] **`load_local_agent_identity()` / `validate_agent_token()` primitive:** 로컬 identity(config의 `agent_id` + store의 token)를 로드하고, token 유효성("존재 + 만료/ revoke 아님")을 **bool 또는 명시 상태**로 반환하는 sync 함수를 둔다(architecture-contract 91-92의 startup 계약명과 정합). 만료/revoke 판정의 **서버 확인 경로는 주입된 transport**로 추상화(실제 호출은 4.4가 배선; 본 스토리는 stub로 검증). 모든 자기 코드 **순수 동기**. [Source: architecture-contract.md(90-92·99), NFR-7(92), FR-16(47)]
- [x] **Task 2 — `registration.py`: 등록 클라이언트(register) + 멱등 (AC: 1, 2)**
  - [x] `src/rider_agent/registration.py` 신설. `register_agent(registration_code, *, transport, store, identity_path, machine_info=...) -> AgentIdentity`(가칭)를 둔다 — 요청 본문(`registration_code`/`machine_fingerprint`/`hostname`/`os`/`agent_version`)을 만들어 `POST /v1/agents/register`를 호출하고, 응답(`agent_id`/`agent_token`/`tenant_scope`/`config_version`)을 파싱해 **token은 `store`(Task 1)로, 나머지는 `agent_config.json`으로** 분리 저장한다. [Source: data-api-contract.md(42-65), implementation-contract.md P3-02(59)]
  - [x] **transport seam:** 실제 구현은 **stdlib `urllib.request`** 로 outbound HTTPS POST(JSON)한다(텔레그램이 `urllib`로 Bot API 호출하는 선례 — 새 HTTP 의존 금지). transport는 **주입 가능한 callable/Protocol**(예: `post_json(url, body) -> dict`)로 두어 단위 테스트가 fake로 대체한다(`run_once`가 crawler/sender를 주입하는 규율과 동형). [Source: project-context.md(24·35·42), architecture.md(190-191), src/rider_crawl/telegram_commands.py(urllib 사용)]
  - [x] **machine_info:** `hostname`(`socket.gethostname()`), `os`(`platform.platform()`), `agent_version`(`rider_agent.__version__`), `machine_fingerprint`(안정적 per-machine 해시 — 비밀 아님)를 모으되 **주입 가능**하게 해 테스트가 결정적 값을 넣는다. 실제 MAC/식별자 원문을 로그에 남기지 않는다. [Source: data-api-contract.md(48-53), src/rider_agent/__init__.py(__version__)]
  - [x] **멱등(AC1.2):** `register_agent`는 먼저 `load_local_agent_identity()`로 유효 identity가 있는지 보고, 있으면 **POST 없이 기존 identity 반환**. stub이 "코드 무효/이미 사용" 응답을 주면 `RegistrationError`(가칭, **token/code 평문 미포함** 메시지)로 올리고 기존 identity를 덮어쓰지 않는다. [Source: epics.md AC(723-726), ADD-15(143)]
  - [x] **자기 코드 순수 동기 + `rider_crawl`만 import**(역방향/`rider_server` import 0) — 4.1 AST 가드가 자동 검사. [Source: 4-1 스토리 AC3·AC5(48-51)]
- [x] **Task 3 — `__main__.py`에 `register` 진입 thin wiring (AC: 1)**
  - [x] `python -m rider_agent register --code <registration_code>` 형태의 **얇은** CLI 진입을 추가한다(argparse). 핵심 로직은 Task 2의 `register_agent`에 있고, `__main__`은 인자 파싱 → 실제 transport/store/identity_path 주입 → 호출 → **redaction 통과한** 한 줄 결과 출력(성공/이미 등록/실패)만 한다. **token/code를 출력하지 않는다.** [Source: implementation-contract.md P3-02(59), src/rider_agent/__main__.py(현재 thin bootstrap), src/rider_crawl/redaction.py(130)]
  - [x] 기존 인자 없는 `python -m rider_agent`(4.1 sync 배너)는 **회귀 없이 그대로 동작**해야 한다(서브커맨드 없을 때 기존 배너 경로 유지). GUI/네트워크/브라우저/Kakao 부작용 없음은 유지(register는 명시적으로 호출할 때만 네트워크). [Source: 4-1 스토리 AC1(41), src/rider_agent/__main__.py(17-25)]
  - [x] `__main__.py`도 **순수 동기 + `rider_crawl`만**(4.1 가드 자동 적용). argparse는 stdlib. [Source: 4-1 스토리 AC3(48-50)]
- [x] **Task 4 — 테스트: `tests/agent/` (AC: 1~5)** — 외부 호출 없음(fake transport/주입 store/`tmp_path`), 가짜 값만:
  - [x] **위치:** `tests/agent/`(평면, `__init__.py` 미추가 — 4.1·`tests/server/` 미러 컨벤션). 신규 basename(예: `test_registration.py`, `test_secure_store.py`)으로 고유하게. [Source: 4-1 스토리(76·140), architecture.md(461), pyproject.toml(testpaths)]
  - [x] **(AC1 — 등록·4값 파싱·분리 저장):** fake transport가 canned `{"agent_id","agent_token","tenant_scope","config_version"}`를 줄 때 `register_agent`가 POST 본문 5필드를 올바로 구성하고, token은 주입 store로·나머지는 `agent_config.json`(tmp_path)로 들어감을 단언. [Source: data-api-contract.md(42-65)]
  - [x] **(AC1.2 — 멱등):** 유효 identity 존재 시 transport가 **호출되지 않음**(fake에 호출 카운터)을 단언. "코드 무효/이미 사용" stub 응답 → `RegistrationError`가 오르고 기존 identity 미변경, 예외 메시지에 token/code 평문 없음. [Source: epics.md AC(723-726)]
  - [x] **(AC2 — DPAPI seam·평문 비노출):** `DpapiSecretStore`가 `SecretStore` Protocol을 만족(`put`/`resolve` round-trip)함을 단언. **핵심 invariant:** 등록 후 `agent_config.json` 텍스트와 store 파일 텍스트 어디에도 **평문 token 문자열이 없음**(`assert fake_token not in config_text and fake_token not in store_text`). 비-Windows 환경에선 주입 fake/파일 store로 "분리 + 평문 부재" invariant를 검증하고, **실제 ctypes DPAPI round-trip은 `@pytest.mark.skipif`(Windows 아닐 때 skip)** 단일 테스트로 가린다. [Source: src/rider_crawl/secret_store.py(42-99·70-78), ADD-15(143), 평문 금지(731)]
  - [x] **(AC2 — store 파일 분리):** store 경로 ≠ `agent_config.json` 경로, 둘 다 tmp_path 하위, `.tmp` 잔여물 0(atomic write). [Source: tests/test_secret_store.py(70-91), src/rider_crawl/secret_store.py(93-98)]
  - [x] **(AC3 — token 게이트):** `validate_agent_token()`이 (a) 유효 token → "job 수신 가능", (b) token 없음(미등록) → "미수신", (c) stub revoked/만료(401·`revoked`) 응답 → "미수신" + 평문 노출 없는 재등록필요 상태를 반환함을 단언. claim 루프 배선(4.4)은 검사 대상 아님. [Source: NFR-7(92), FR-16(47), data-api-contract.md(71-73)]
  - [x] **(누출 가드):** 모든 fixture는 가짜 값만 — 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/registration code/`chat_id=<digits>`/한국 휴대폰/이메일·OTP 원문 금지. 실제 Telegram/Kakao/Gmail/브라우저/네트워크/실 DPAPI(비-skip 경로) 미호출. 로그 캡처 시 token이 `redact`로 마스킹됨을 1건 단언. [Source: project-context.md(55·81), src/rider_crawl/redaction.py(130·44), epic-3-retro-2026-06-13.md(109·118)]
- [x] **Task 5 — 회귀·범위·누출 검증 및 마무리 (AC: 1~5)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/test_*.py`·`tests/server/`·**`tests/agent/test_agent_package.py`**), 신규 케이스만큼만 증가가 정상(순수 additive). [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] **4.1 가드 green 재확인(핵심):** `tests/agent/test_agent_package.py`의 (a) third-party import root == `{rider_crawl}`, (b) sync(자기 모듈 async 0), (c) 단방향 import, (d) pyproject deps 핀/개수 불변 단언이 **신규 모듈 추가 후에도 통과**함을 확인한다. stdlib(`urllib`/`ctypes`/`socket`/`platform`/`argparse`/`json`/`hashlib`)만 썼다면 그대로 green이다 — 만약 깨지면 새 third-party import가 새어든 것이니 제거(권장)하거나 해당 가드를 같은 스토리에서 갱신. [Source: 4-1 스토리 Completion Notes(184), memory/enum-member-count-locks]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `src/rider_agent/{registration,secure_store}.py` + `__main__.py`(register wiring) + 신규 `tests/agent/*` + sprint-status만** 보이고 **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md` 변경 0줄**임을 확인(CRLF/LF 노이즈·무관 파일 미수정 — `git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 token/registration code 0건, `src/rider_crawl/`에 `rider_agent` import 신규 0건(AST 가드와 별개의 수동 교차 확인). **A1″ 참고:** epic-3-retro가 secret 스캔 pre-commit 차단 게이트를 "4.2 착수 전 도입"으로 격상했다(118·168). 게이트 도구 자체 도입은 TEA/Amelia 소유로 본 스토리 AC 차단 조건은 아니나, dev는 4.1처럼 수동 grep + 가짜값 규칙을 계속 적용한다. [Source: project-context.md(64·81), epic-3-retro-2026-06-13.md(109·118·168)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(dev 노트에 잠정 수치 박지 말 것 — Epic 2/3·4.1에서 stale 수치 MEDIUM 재발). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `src/rider_agent/{registration.py, secure_store.py}` + `__main__.py`의 얇은 `register` CLI wiring + 신규 `tests/agent/*`. **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`는 무변경.**
- **건드리지 않는다:** `rider_crawl` 전부(재사용만), heartbeat(4.3), job claim/lease/events/complete(4.4), BrowserProfileManager(4.5), KakaoSenderWorker(4.6), autostart(4.7), 배민 재인증(4.8), 쿠팡 Gmail 2FA 메일함 분리·lock(4.9), 서버 측 register/revoke/queue/Admin/DB(Epic 5). **빈 stub 파일도 만들지 않는다**(후속 스토리 소유). [Source: epics.md Story 4.3~4.9(738-903), architecture.md(446-457)]

### 설계 결정 — 왜 stdlib `urllib`+`ctypes`이고 `rider_crawl`/`pyproject` 무변경인가 (반드시 읽을 것)

- **HTTPS = stdlib `urllib`(새 HTTP 의존 금지).** project-context.md(24): "텔레그램은 표준 라이브러리 `urllib`로 Bot API를 호출한다." Agent의 outbound HTTPS 등록도 같은 정책을 따른다 — `requests`/`httpx` 도입은 ADD-3(새 프레임워크 0)·4.1 import-root 가드 위반. transport는 주입 seam이라 단위 테스트는 네트워크 없이 fake로 검증한다(`run_once`가 crawler/sender를 주입하는 규율). [Source: project-context.md(24·35·42), architecture.md(190-191)]
- **DPAPI = stdlib `ctypes`(crypt32, 새 의존 금지).** architecture(449)·architecture-contract(85)가 token을 "DPAPI/Credential Manager"에 두라 한다. `pywin32`(`win32crypt`)를 쓰면 새 third-party import가 생겨 **4.1이 잠근 `test_agent_package.py`의 "third-party root == `{rider_crawl}`" 가드가 깨진다.** stdlib `ctypes`로 `CryptProtectData`/`CryptUnprotectData`를 직접 호출하면 의존 0·가드 green 유지. **이것이 권장 경로다.** 부득이 새 의존을 쓰면 같은 스토리에서 그 가드 allowlist를 갱신해야 한다(memory: enum-member-count-locks와 동형 — "추가가 기존 lock을 깬다"). [Source: architecture.md(449·107-108·124), architecture-contract.md(85), 4-1 스토리(184), memory/enum-member-count-locks]
- **`SecretStore` Protocol 재사용(재발명 금지).** `rider_crawl/secret_store.py`(42-52)의 `SecretStore` Protocol(`put`/`resolve`)과 docstring(45-47)이 이미 "DPAPI(Epic 4)·AWS Secrets Manager(Epic 5)는 같은 seam에 **백엔드만** 끼운다"고 못 박았다. 4.2의 `DpapiSecretStore`는 그 Protocol을 구현하는 **새 백엔드**일 뿐, 새 인터페이스를 만들지 않는다. atomic write(`_atomic_write_text`)·store↔config 분리·fail-closed(`resolve`→`None`) 패턴도 그대로 계승. [Source: src/rider_crawl/secret_store.py(42-99), tests/test_secret_store.py(70-91)]
- **`rider_crawl` 무변경 = 무회귀 안전 마진.** epic-3-retro(158): "**`rider_crawl` 0줄**이 실행 흐름 재배선의 안전 마진 … Epic 4 `rider_agent`도 동일 규약." 특히 `rider_crawl/secret_store.py`의 `SECRET_STORAGE_CLASSIFICATION`/`classify_secret_storage`에 **`agent_token`을 추가하지 않는다** — `tests/test_secret_store.py`가 "정확히 3분류·5 secret kind"를 잠갔으므로(30·123-132) 거기 키를 더하면 그 lock이 깨지고 `rider_crawl`이 변경된다. agent token의 분류값(`agent_local`)이 필요하면 **rider_agent 자기 쪽 상수**로 두거나 분류에 의존하지 않는다(저장 위치는 이미 DPAPI로 고정). [Source: epic-3-retro-2026-06-13.md(158), src/rider_crawl/secret_store.py(27-39), tests/test_secret_store.py(30-36·123-132), memory/enum-member-count-locks]

### 서버 부재 — stub/mock 검증이 4.x 형태 (배선 첫 스토리)

- `POST /v1/agents/register`·token revoke의 **서버 측은 Epic 5**(FastAPI/PostgreSQL)다. 4.2는 **client + 주입 transport stub**로 (a) 정상 등록, (b) 코드 무효/이미 사용, (c) revoked/만료 응답 3 시나리오를 검증한다. epic-3-retro(108): "서버 측 job 생성·queue·Admin은 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." 4.1은 서버 호출 자체가 없는 토대였고, **4.2가 Agent↔Server 계약을 처음 (stub에 대해) 배선**하는 스토리다. [Source: epic-3-retro-2026-06-13.md(108), data-api-contract.md(42-73), architecture-contract.md(87-107)]
- **token 게이트의 소유 분리:** 4.2는 `validate_agent_token()` **primitive**만 제공한다("유효 token 있어야 job 수신 가능"). 이 게이트를 **실제 claim 흐름에 배선**(job을 막는 곳)하는 것은 Story 4.4(`job_loop.py`)다. architecture-contract(90-99) startup 시퀀스 `load_local_agent_identity()`→`validate_agent_token()`→…→`claim_next_job(...)`에서 4.2는 앞의 두 함수를, 4.4가 claim 루프를 채운다. [Source: architecture-contract.md(90-99), epics.md Story 4.4(760-785)]

### 재사용 대상 공개 표면 (재구현 금지 — import만)

| 도메인 | rider_crawl 공개 심볼 | 파일/행 | 4.2 사용 |
|---|---|---|---|
| secret store seam | `SecretStore` Protocol(`put`/`resolve`), `LocalFileSecretStore`(패턴 참고), `classify_secret_storage` | secret_store.py(42·55·36) | DPAPI 백엔드를 같은 Protocol로 구현 |
| atomic write | `ui_settings._atomic_write_text` | ui_settings.py(secret_store가 96-98로 재사용) | config/store 쓰기 손상 방지 |
| redaction | `redact(text)`, `redacted_error_event(...)`, `REDACTED` | redaction.py(130·248·44) | 로그/배너/에러에서 token 마스킹 |
| 상태 루트 | `app_state_root()` | config.py(158-182) | per-machine 단일 identity 경로(주입 가능) |
| 버전 | `rider_agent.__version__` | rider_agent/__init__.py(32) | register 요청의 `agent_version` |

- 모두 **import/재사용만** — 시그니처 변경·`rider_crawl` 수정 금지. [Source: 위 파일/행, project-context.md(64)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl`·`rider_server`·`pyproject.toml` 무변경** — `git diff -w` = `src/rider_agent/` 신규 2파일 + `__main__.py` register wiring + 신규 테스트 + sprint-status만. (b) **의존성 단방향·sync** — 신규 모듈도 `rider_crawl`만 import, async 0(4.1 가드 자동 적용). (c) **새 프레임워크 0** — third-party import root는 `rider_crawl`만(stdlib `urllib`/`ctypes` 사용), deps 핀 유지 → 4.1 가드 green. (d) **token 평문 0** — 로그/config/디스크 텍스트/예외에 token 평문 없음, store↔config 분리. (e) **import-safety** — `import rider_agent`(+ 신규 모듈)가 비-Windows에서 import-safe(ctypes/crypt32는 함수 내부 lazy·Windows-gated). (f) **기존 `python -m rider_agent` 무회귀** — 인자 없을 때 4.1 sync 배너 그대로. (g) **누출 0** — 테스트 실제 외부 미호출, 가짜 값만. [Source: project-context.md(24·35·55·64·81·82), architecture.md(183-185·333-335·494), epic-3-retro-2026-06-13.md(158)]

### 이전 스토리/회고 인텔리전스 (4.1 → 4.2 이월 교훈)

- **4.1이 깐 토대 위에 빌드(직접 계승):** 4.1은 `__init__`/`__main__`(thin sync 배너)/`reuse`(seam)만 만들고 "등록 코드+token DPAPI 보안 저장은 Story 4.2(P3-02)"로 명시 위임했다(4-1 스토리 18·21). 4.2는 그 `__main__`을 additive로 확장(register 서브커맨드)하고 `registration.py`/`secure_store.py`를 추가한다 — 4.1이 "빈 stub도 금지"로 비워둔 바로 그 파일들. [Source: 4-1 스토리(18·21·89), architecture.md(448-449)]
- **A1″(secret 스캔 차단 게이트)는 "4.2 착수 전":** epic-3-retro(118·168)가 pre-commit secret 스캔을 "Epic 4 **4.2(Agent 토큰) 착수 전 실제 도입**"으로 격상했다(봇 토큰·OAuth refresh·OTP·email·KR 휴대폰·`chat_id` 검출). 게이트 **도구** 도입은 TEA/Amelia 소유라 본 스토리 코드 AC의 차단 조건은 아니지만, 4.2가 처음으로 실제 token을 다루므로 누출 비용이 급등한다(retro 109). dev는 4.1처럼 신규 코드·테스트에 평문 secret 0건을 **수동 grep + 가짜값 규칙**으로 계속 보장한다. [Source: epic-3-retro-2026-06-13.md(109·118·168)]
- **A2″(테스트 수치/File List 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. 4.1도 dev 9건→리뷰 14건 stale로 MEDIUM이 났다(4-1 스토리 223). [Source: epic-3-retro-2026-06-13.md(59), 4-1 스토리(170·178·223), memory/stale-test-count-a2]
- **부정 가드는 AST로(4.1 계승):** 단방향·sync·no-new-framework 가드는 이미 4.1이 AST로 짰고 `src/rider_agent/*.py`를 glob한다 — 신규 모듈은 자동 검사된다. 새 가드를 raw grep으로 짜지 말 것(scope 경계 docstring의 금지 심볼명을 오탐). [Source: 4-1 스토리(73·75·129), memory/negative-guard-tests-use-ast]
- **enum/lock 전수 점검(memory):** `rider_crawl.secret_store`의 분류 맵·`tests/test_secret_store.py`의 "정확히 3분류/5 kind" lock을 건드리지 않도록 주의(agent_token을 거기 추가 금지). 도메인 멤버 추가가 여러 테스트의 "정확히 N개" lock을 깨는 패턴. [Source: memory/enum-member-count-locks, tests/test_secret_store.py(30-36·123-132)]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest는 **WSL의 `python3`가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`** 로 돌린다(WSL python엔 pytest 미설치). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w`로 하고 무관한 EOL flip을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]
- **DPAPI 테스트 주의:** 운영 venv는 Windows Python이라 실제 `ctypes` DPAPI가 동작할 수 있다. 그래도 단위 테스트는 **주입 fake/파일 store + tmp_path**로 결정적으로 검증하고, 실제 OS-protected round-trip은 `@pytest.mark.skipif`(비-Windows skip) **단일** 테스트로만 가린다(실 OS store에 영속 쓰기 금지). [Source: memory/dev-env-quirks, src/rider_crawl/secret_store.py(60-63)]

### Project Structure Notes

- 신규 파일은 architecture.md(446-457) 트리와 정렬: `src/rider_agent/registration.py`(= registration code → agent_id/token), `src/rider_agent/secure_store.py`(= DPAPI/Credential Manager). 트리의 `heartbeat.py`/`job_loop.py`/`browser_profile.py`/`workers/`/`auth/`/`autostart.py`는 각 후속 스토리(4.3~4.9)가 만든다 — **계획된 부분 구현이지 이탈이 아니다**(4.1·Epic 2/3 retro의 "부분 구현은 계획" 판정). [Source: architecture.md(446-457), 4-1 스토리(139)]
- 테스트는 `tests/agent/`(평면, `__init__.py` 미추가). 기존 `tests/agent/test_agent_package.py`(4.1)와 별 basename으로 둔다. [Source: architecture.md(461), 4-1 스토리(76·140), pyproject.toml(testpaths)]
- **identity 디스크 레이아웃(참고):** architecture-contract(72-83)의 실제 배포 경로는 `C:\RiderBot\agent\data\agent_config.json` + `secrets\`다. 개발/테스트 레이아웃에선 `app_state_root()` 하위(주입 가능)로 매핑한다 — per-machine 단일이라 log_dir 스코프가 아닌 고정 루트(텔레그램 offset 선례). [Source: architecture-contract.md(72-85), src/rider_crawl/config.py(158-168), src/rider_crawl/telegram_commands.py(607)]
- **변이/충돌:** `project-context.md`의 `rider_agent` 신설·secret 정책 반영은 **Epic 4 retro**에서 한다(rider_server를 Epic 2 retro에서 반영한 선례). 본 스토리에서 project-context.md는 수정하지 않는다. [Source: 4-1 스토리(141), project-context.md(114)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.2(715-736)] — user story + AC(register·token DPAPI 저장·revoke 게이트).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-4(694-696)] — Epic 4 범위(서버 stub/mock 검증, FR-12~20·25·28·32).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-12·16(43·47)·NFR-7·8·9(92-94)·ADD-6·15(134·143)] — 등록/outbound-only/token revoke/secret 저장 분류/금지행위.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P3-02(59)] — "registration code entry and secure agent_id/token storage. One-time code registers Agent."
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md#Agent-API(42-73)] — `POST /v1/agents/register` 요청/응답 스키마 + claim 응답(token-auth).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md#Local-Agent-Runtime(68-107)] — Agent 디스크 레이아웃·`agent_config.json` 평문 금지·startup `load_local_agent_identity`/`validate_agent_token`.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Secret-Storage(5-19)] — Agent token=Agent-local secure store·server-side revoke·redaction 규칙.
- [Source: _bmad-output/planning-artifacts/architecture.md#Project-Structure(446-457)] — `registration.py`/`secure_store.py` 위치.
- [Source: _bmad-output/planning-artifacts/architecture.md#Auth-Security(176-185)·Data-Boundaries(494)] — token tenant+job-type scope·revoke/rotate·secret DB 밖·redaction.
- [Source: src/rider_crawl/secret_store.py(36-99)] — `SecretStore` Protocol·`LocalFileSecretStore`·`classify_secret_storage`(재사용 seam, 무변경).
- [Source: tests/test_secret_store.py(30-36·70-91·123-132)] — store put/resolve·파일 분리·atomic·"정확히 3분류" lock(건드리지 말 것).
- [Source: src/rider_crawl/redaction.py(44·130·248)] — `REDACTED`/`redact`/`redacted_error_event`(로그·에러 마스킹 재사용).
- [Source: src/rider_crawl/config.py(158-182)] — `app_state_root()`(per-machine 단일 identity 경로).
- [Source: src/rider_agent/__init__.py(32)·__main__.py(현재 thin bootstrap)·reuse.py] — 4.1 토대(확장 대상).
- [Source: _bmad-output/implementation-artifacts/4-1-rider-agent-패키지-생성과-기존-도메인-재사용.md(18·21·184·223)] — 4.1 위임(4.2 소유)·import-root 가드·stale 수치 교훈.
- [Source: _bmad-output/implementation-artifacts/epic-3-retro-2026-06-13.md(59·108·109·118·158·168)] — stub/mock 검증·A1″(4.2 전 secret 게이트)·A2″·rider_crawl 0줄.
- [Source: _bmad-output/project-context.md(24·35·53·64·68·81·82)] — urllib 정책·sync·pytest 실행·단방향 import·JSON 저장 스타일·누출 금지.
- [Source: memory/dev-env-quirks, memory/stale-test-count-a2, memory/negative-guard-tests-use-ast, memory/enum-member-count-locks] — venv pytest·`git diff -w`, 수치 단일 정본, AST 부정 가드, enum/lock 전수 점검.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- 운영 venv 전체 스위트: `.venv/Scripts/python.exe -m pytest -q` → **1060 passed**(baseline 1008 + 본 스토리 신규 52 — `tests/agent/test_registration.py` 28 + `tests/agent/test_secure_store.py` 24). 경고 0. (리뷰 시점 재측정 — dev 노트의 잠정 "1037/29 신규"는 qa-generate-e2e 가 G1~G8 gap 케이스를 추가하기 전 수치라 stale 였음. memory: stale-test-count-a2)
- 4.1 가드 단독: `pytest tests/agent/test_agent_package.py -q` → 14 passed(third-party root=={rider_crawl}·sync·단방향·deps 핀 모두 green).
- 실 DPAPI round-trip(`test_dpapi_real_round_trip_on_windows`)은 운영 venv(win32)에서 **skip 아님 — 실제 crypt32 `CryptProtectData`/`CryptUnprotectData` 실행 후 PASS**(평문 부재까지 단언).

### Completion Notes List

- **AC1(등록·4값·분리저장·멱등):** `registration.register_agent`가 `registration_code`+`machine_fingerprint`/`hostname`/`os`/`agent_version` 5필드로 `POST /v1/agents/register`를 호출(stdlib `urllib` 기반 `HttpTransport`, 주입 가능 seam)하고 응답 4값을 파싱해 **`agent_token`은 DPAPI store로, `agent_id`/`tenant_scope`/`config_version`은 `agent_config.json`으로** 분리 저장한다. 멱등: 유효 local identity가 이미 있으면 POST 없이 기존 반환(일회용 코드 미소모). 코드 무효/이미사용(stub 4xx) → `RegistrationError`(token/code 평문 미포함, 상태코드만) + 기존 미변경.
- **AC2(DPAPI 보안 저장·평문 비노출):** `secure_store.DpapiSecretStore`가 `rider_crawl.secret_store.SecretStore` Protocol(`put`/`resolve`)을 **그대로 구현**(새 인터페이스 0). 백엔드는 stdlib `ctypes`로 crypt32 `CryptProtectData`/`CryptUnprotectData` 직접 호출(새 third-party 의존 0) — **함수 내부 lazy + Windows-gated**라 비-Windows에서 `import rider_agent.secure_store`가 import-safe. store 파일은 `agent_config.json`과 다른 경로(`app_state_root()/runtime/state/agent/` 하위, 주입 가능). 핵심 불변식: 등록 후 config·store 텍스트 어디에도 평문 token 없음(테스트 단언). `AgentIdentity.__repr__`는 token을 `REDACTED`로 가린다.
- **AC3(token 게이트 primitive):** `validate_agent_token()`이 (a) 유효 token→`valid`(`can_receive_jobs=True`), (b) 미등록/빈 token→`missing`(미수신), (c) 주입 `server_check`가 False(stub 401·revoked/만료)→`revoked`(미수신·`needs_registration=True`)를 반환. 실제 claim 루프 배선은 Story 4.4 소유 — 본 스토리는 primitive + 단위 검증만.
- **`__main__` thin wiring:** `python -m rider_agent register --code <code>`(argparse) 추가. 핵심 로직은 `register_agent`에 위임, `__main__`은 주입·호출·**redaction 통과한 한 줄 결과**(registered/already-registered/failed)만 출력(token/code 미출력). 인자 없는 `python -m rider_agent`는 4.1 sync 배너 그대로(무회귀) — runpy-under-pytest의 argv 오염에도 'register'가 아니면 배너로 폴백.
- **범위/무회귀:** 순수 additive. `src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md` **0줄 변경**. 신규 모듈은 자기 코드 순수 동기 + `rider_crawl`만 import(4.1 AST 가드가 신규 파일 glob으로 자동 검사 → green). stdlib(`urllib`/`ctypes`/`socket`/`platform`/`argparse`/`json`/`hashlib`/`base64`)만 사용해 4.1 import-root·deps 핀 가드 불변.
- **누출 가드:** 신규 코드·테스트에 실 token/registration code 0건(가짜값 `agtok-fake-…`/`regcode-fake-…`/`agent-fake-…`만). `src/rider_crawl/`에 `rider_agent` import 신규 0건(수동 교차 확인).

### Change Log

- 2026-06-13: Story 4.2 구현 — `src/rider_agent/secure_store.py`(DPAPI secret store + identity 영속 + token 게이트 primitive), `src/rider_agent/registration.py`(urllib 등록 클라이언트 + 멱등), `src/rider_agent/__main__.py`에 `register` thin CLI 추가, `tests/agent/test_secure_store.py`·`tests/agent/test_registration.py` 신규. 순수 additive(`rider_crawl`/`pyproject` 무변경). 전체 스위트 1060 passed.
- 2026-06-13: Senior Developer Review (AI) by Noah Lee — outcome **Approve**. 재측정 스위트 1060 passed(경고 0). 적용 수정: (MEDIUM) Dev Agent Record stale 테스트 수치 1037/29신규 → 1060/52신규 정정; (LOW) `register_agent`/`save_agent_identity`/`load_local_agent_identity` store 파라미터 타입 `Any` → `rider_crawl.secret_store.SecretStore`(문서화된 seam 계약 복원, 타입 힌트 전용·런타임 무영향). CRITICAL/HIGH 0. 상태 review → done.

### File List

- `src/rider_agent/secure_store.py` (신규)
- `src/rider_agent/registration.py` (신규)
- `src/rider_agent/__main__.py` (수정 — `register` 서브커맨드 thin wiring 추가)
- `tests/agent/test_secure_store.py` (신규)
- `tests/agent/test_registration.py` (신규)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정 — 4-2 상태 in-progress→review→done)

## Senior Developer Review (AI)

**Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Outcome: ✅ Approve (Changes applied automatically)**

### 검증 요약

- **AC 전수 검증 — 모두 IMPLEMENTED.**
  - **AC1**(일회용 코드 등록·발급 4값 분리 저장·멱등): `register_agent`가 5필드 본문(`registration_code`/`machine_fingerprint`/`hostname`/`os`/`agent_version`)으로 POST하고 4값을 파싱해 `agent_token`→DPAPI store, 나머지→`agent_config.json`으로 분리 저장(`registration.py:189-230`, `secure_store.py:241-256`). 멱등: 유효 local identity 존재 시 POST 없이 기존 반환(`registration.py:205-208`). 거부/무효 코드→`RegistrationError`(평문 미포함). 단위·E2E(실 `HttpTransport`+fake urlopen) 모두 검증.
  - **AC2**(DPAPI 보안 저장·평문 비노출): `DpapiSecretStore`가 `rider_crawl.secret_store.SecretStore` Protocol을 구현(새 인터페이스 0), stdlib `ctypes` crypt32 `CryptProtectData/CryptUnprotectData` 직접 호출(새 의존 0)·함수 내부 lazy+Windows-gated(`secure_store.py:86-137`). store 파일 ≠ config 파일, 양쪽 텍스트에 평문 token 부재(`test_secure_store.py:95-132`). 실 DPAPI round-trip은 Windows skipif 단일 테스트로 검증(운영 venv=win32에서 실제 실행·PASS).
  - **AC3**(token 게이트 primitive): `validate_agent_token`이 missing/valid/revoked 3상태 반환, `can_receive_jobs`/`needs_registration` 파생(`secure_store.py:302-337`). claim 루프 배선은 4.4 소유로 정확히 위임.
- **Task 전수 감사 — 5개 task 모두 [x] 실제 완료**(코드 대조). 빈 stub 파일 미생성, heartbeat/job_loop 등 후속 스토리 파일 미생성 — 계획된 부분 구현(이탈 아님).
- **스코프/무회귀**: `git diff -w` 소스 = `__main__.py` 수정 + 신규 2모듈 + 신규 테스트만. `src/rider_crawl/`·`src/rider_server/`·`pyproject.toml` **0줄**. 단방향 import(`rider_crawl`→`rider_agent` 0)·sync·third-party root=={rider_crawl}·deps 핀 4.1 AST 가드 모두 green.
- **누출 가드**: 신규 코드/테스트에 실 token·registration code 0건(전부 `*-fake-*`). 봇 토큰 shape grep 0. `AgentIdentity.__repr__`·`redact` token 마스킹 단언 통과.
- **테스트**: `.venv/Scripts/python.exe -m pytest -q` → **1060 passed**, 경고 0(타입 힌트 수정 후 재실행도 1060 green).

### Findings

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | MEDIUM | Dev Agent Record 테스트 수치 stale — Debug Log/Change Log가 "1037 passed / 신규 29"였으나 qa-generate-e2e의 G1~G8 gap 케이스 추가 후 실측은 1060 passed / 신규 52. 스토리 Task 5·memory(stale-test-count-a2)가 경고한 A2″ 패턴 재발. | **Fixed** — 재측정값 1060/52(28+24)로 정정, stale 사유 명시. |
| 2 | LOW | `register_agent`/`save_agent_identity`/`load_local_agent_identity`의 주입 store 파라미터가 `store: Any`로 타입되어 본 스토리의 핵심 설계(=`SecretStore` Protocol seam 재사용) 계약이 시그니처에서 소실. | **Fixed** — `store: SecretStore`(`rider_crawl.secret_store`)로 정정. 타입 힌트 전용·런타임 무영향, third-party root=={rider_crawl} 유지(가드 green). |

CRITICAL/HIGH: **0건**. Git vs File List 불일치: **0건**(소스 5파일 정확 일치).

### Action Items

- 없음 — 발견 항목(MEDIUM 1·LOW 1) 모두 본 리뷰에서 자동 수정 완료. 후속 스토리 차단 항목 없음.
