---
baseline_commit: eb3d9e1
---

# Story 4.7: Agent 실행 조건과 재부팅 후 자동 시작

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 작업 노드 관리자,
I want **Kakao 작업 Agent가 interactive Windows 세션에서만 실행되고(Session 0 service-only 의존 금지)**, **재부팅 후 사용자 로그인 시 자동 시작되도록 Windows Startup 또는 Task Scheduler로 등록**되며(자동 시작 후 `run` 루프가 그대로 진입해 heartbeat가 복구됨), **crawler-only 노드와 Kakao sender 노드의 실행 조건·처리 가능 job type이 capability로 구분**되고(현재 일반 Windows PC를 Agent #1로 그대로 사용), 이 모든 것을 **OS 부작용(세션 탐지·Startup 파일 쓰기·`schtasks` 호출)을 전부 주입 가능하게 한 순수 동기 primitive `src/rider_agent/autostart.py` + `__main__.py`의 얇은 `autostart` 서브커맨드 + `run_agent`의 additive interactive-session 게이트**로 갖고 싶다,
so that PC 재부팅·잠금 상태에서도 Agent가 안정적으로 복구되고, Kakao UI 자동화가 비대화형(Session 0) 환경에서 오작동하지 않으며, 작업 유형별로 올바른 실행 환경에서 동작한다(P3-07, FR-32·28, ADD-15).

> **이 스토리의 성격 — "실행 조건 게이트 + autostart 등록 primitive + 노드 역할 구분"만. Epic 4 런타임-환경 시리즈(4.1~4.6)의 마지막 primitive.** 4.1~4.6이 만든 토대 위에 **(a) interactive-session 게이트**(Kakao 노드가 Session 0면 워커 미기동·fail-closed surfacing), **(b) Windows autostart 등록/해제 primitive**(Startup 폴더 또는 Task Scheduler — 등록 메커니즘 주입 가능, 멱등, 제거 가능), **(c) crawler-only vs Kakao-sender 노드 역할 resolver**(capability로 실행 조건·job type 구분)를 얹는다. **실제 KakaoTalk/Chrome 자동화·실 `schtasks`·실 세션 API는 본 스토리가 한 줄도 "테스트에서" 호출하지 않는다** — 전부 주입 fake로 결정적 검증한다. [Source: implementation-contract.md P3-07(64), architecture-contract.md Local-Agent-Runtime(68-85)·startup(90-94), architecture.md tree autostart.py(457), epics.md Story 4.7(833-852)]
>
> **서버가 아직 없다 — "서버 stub/mock에 대한 동작 검증"이 4.x 테스트 형태(절대 전제, 4.1~4.6 계승).** autostart는 서버를 호출하지 않는 **순수 로컬 OS 통합**이라 더더욱 외부 의존이 없다. 본 스토리는 실제 OS·네트워크 없이 **주입된 fake `runner`(schtasks)/`writer`(Startup 파일)/`session_probe`(세션 탐지) + tmp_path**에 대해 launch-command 합성·등록 멱등성·해제·세션 게이트·노드 역할 resolver를 결정적으로 검증한다. epic-3-retro(108): "Epic 4는 서버 측이 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." [Source: epic-3-retro-2026-06-13.md(108), 4-6 스토리(19)]
>
> **재사용 = 재구현 금지(핵심 가드 #1).** crawler-only vs Kakao-sender 구분의 **메커니즘은 이미 존재한다** — `heartbeat.py`의 `CAPABILITY_*`(62-67)·`DEFAULT_CAPABILITIES`(70-77)와 4.6 `start_kakao_sender_worker_if_enabled`의 `CAPABILITY_KAKAO_SEND in capabilities` 게이트(kakao_sender.py:208-209·436-440)다. 4.7은 이 capability 게이트를 **재사용**해 노드 역할을 명시적으로 노출(`resolve_node_role`/`requires_interactive_session`)할 뿐, 새 역할 enum이나 별도 실행 경로를 만들지 않는다. DPAPI/세션-게이팅의 **Windows-gated lazy import 패턴**도 `secure_store._dpapi_crypt`(87-131)를 그대로 본뜬다. [Source: src/rider_agent/heartbeat.py(62-77), src/rider_agent/workers/kakao_sender.py(208-209·414-455), src/rider_agent/secure_store.py(87-131)]
>
> **autostart는 4.4가 깐 `run` 진입을 launch한다(새 루프 0).** 재부팅 후 복구의 "heartbeat 복구"는 **이미 `run_agent`가 한다**(identity 로드 → token 검증 → heartbeat thread 기동 → main loop, job_loop.py:755-872). autostart는 **그 진입을 OS에 등록**할 뿐 — 새 heartbeat/루프 로직을 짜지 않는다. launch 커맨드는 `python -m rider_agent run`(개발) 또는 packaged exe(`sys.frozen`)이며 **token/registration code를 절대 포함하지 않는다**(identity는 run 시점에 DPAPI store에서 로드 — secure_store.py:273-297). [Source: src/rider_agent/job_loop.py(755-872), src/rider_agent/__main__.py(112-161), architecture-contract.md(72-85·90-94)]
>
> **interactive-session 게이트는 `run_agent`에 additive로 배선한다(4.6 `start_kakao_sender` 옆, 무회귀).** 4.6이 `start_kakao_sender`/`kakao_send`/`kakao_build_config`를 `run_agent`에 더한 자리(job_loop.py:774-833) **그 옆에** optional `session_probe`(또는 `enforce_interactive_session`)를 더한다 — **미주입이면 게이트 없음 = 4.6 동작 그대로**(무회귀). 게이트는 노드가 `KAKAO_SEND` 보유 + 주입 probe가 비대화형(Session 0)일 때만 Kakao 워커를 **띄우지 않고**(`kakao_status`=4.3 기본 `"disabled"`) 명확히 surfacing한다. **`kakao_sender.py`·`heartbeat.py`는 0줄**(게이트 판정은 `autostart.py`, 소비는 `job_loop.run_agent`). [Source: src/rider_agent/job_loop.py(774-833), 4-6 스토리(23·60)]
>
> **노드 역할/세션-사유 상수는 평문 문자열 — enum/"정확히 N개" lock 금지(핵심 가드 #2, memory: enum-member-count-locks).** `NODE_ROLE_*`·세션 사유(`SESSION_0_SERVICE` 등)는 `secure_store.TOKEN_STATUS_*`(52-54)·`heartbeat.DEFAULT_CAPABILITIES`·`kakao_sender.KAKAO_OUTCOME_*`(74-77) 선례대로 **평문 상수**로 두고 어떤 "정확히 N개" 테스트로도 잠그지 않는다 — 후속(4.8/4.9)이 역할/사유를 늘려도 다른 lock을 깨지 않게. `rider_server` 도메인 enum은 import하지 않는다(단방향). [Source: src/rider_agent/secure_store.py(50-54), src/rider_agent/heartbeat.py(58-77), memory/enum-member-count-locks]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리 산출물: 신규 `src/rider_agent/autostart.py` + 신규 `tests/agent/test_autostart.py` + `src/rider_agent/__main__.py`의 **얇은 `autostart` 서브커맨드(additive)** + `src/rider_agent/job_loop.py`의 **interactive-session 게이트(additive, 무회귀 기본값)**. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(절대 규칙·무회귀 안전 마진).** autostart는 OS 통합이라 `rider_crawl` 도메인을 거의 안 쓴다(필요 시 `redaction.redact`만 import). epic-3-retro(158): "**`rider_crawl` 0줄**이 안전 마진." [Source: project-context.md(64·82), epic-3-retro-2026-06-13.md(158)]
> - **`pyproject.toml` `[project].dependencies` — 0줄 변경.** autostart는 **stdlib만**(`subprocess`/`os`/`sys`/`pathlib`/`ctypes` Windows-gated). `pywin32`/`winshell` 등 새 third-party를 도입하면 4.1 가드 `test_pyproject_dependencies_unchanged_pins`(deps **정확히 9개**)와 `test_rider_agent_only_third_party_root_is_rider_crawl`(third-party root == `{rider_crawl}`)가 **둘 다** 깨진다. [Source: tests/agent/test_agent_package.py(206-225), src/rider_agent/secure_store.py(94-101)]
> - **`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py`·`workers/kakao_sender.py` — 0줄 변경(reuse only).** 노드 역할은 `heartbeat.CAPABILITY_*` import만, 세션 게이트는 `job_loop`가 소비. [Source: src/rider_agent/heartbeat.py(62-77), src/rider_agent/workers/kakao_sender.py(414-455)]
> - **PyInstaller exe **빌드**·버전 manifest·rollback 바이너리·tray-app GUI** → deploy/인프라(ADD-12, Epic 5). autostart는 launch **커맨드를 합성**할 뿐(packaged exe 경로는 `sys.frozen`/주입으로 수용)이고 exe를 **빌드하지 않으며**, GUI tray-app도 만들지 않는다(console `run` 진입으로 충분 — architecture-contract 70은 "tray app **또는** console app"). [Source: architecture.md(212-213), architecture-contract.md(70)]
> - **서버 측 capability 수신/노드 배정·Admin autostart 상태 표시·`agent_offline` runbook** → Epic 5. 본 스토리는 client 등록 + 주입 fake. [Source: data-api-contract.md(35·69), operations-security-test-contract.md(62)]
> - **배민 auth(4.8)·쿠팡 Gmail 2FA(4.9)** → 각 후속. autostart는 어떤 auth 흐름도 트리거하지 않는다. [Source: epics.md Story 4.8~4.9(854-903)]
>
> **secret/원시정보 비노출(ADD-15·NFR-5/8 — 핵심 가드 #3).** **launch 커맨드·Startup 파일·`schtasks` 인자·로그 어디에도 agent_token·registration code가 들어가지 않는다** — autostart는 `run`(identity는 DPAPI store에서 로드)만 등록한다. 커맨드에 들어가는 실행 경로/`--server-url`은 secret이 아니다. 로그는 고정 메시지(`autostart registered (method=...)`)만 남기고 `redact()`를 통과시킨다(자유 텍스트 경로/사용자명이 섞여도 한 번 더 마스킹). 테스트 fixture는 가짜 경로/명령만(실제 token·chat_id·휴대폰·이메일·OTP 금지). [Source: operations-security-test-contract.md(87-95), project-context.md(81·89·90), memory/redact-skips-operational-ids]
>
> **sync 런타임 + 단방향 import + Windows-gated import-safety(4.1 규약 계승 — 자동 검증됨).** 신규 `autostart.py`는 **순수 동기**(no `async def`/`await`/직접 `import asyncio`)이고 stdlib + `rider_crawl`(redact만, 선택) + 자기 패키지만 import한다(역방향 0, `rider_server` 0). 실 `schtasks`/`ctypes`/Startup-파일 쓰기는 **함수 내부 lazy + Windows-gated**라 `import rider_agent.autostart`가 비-Windows(WSL/CI)에서도 import-safe하다(`secure_store._dpapi_crypt` 선례). 4.1이 `src/rider_agent/`를 **`rglob("*.py")`(재귀)** AST 가드로 검사하므로 **신규 `autostart.py`도 자동 적용**된다. [Source: tests/agent/test_agent_package.py(33-34·188-245), src/rider_agent/secure_store.py(20-26·87-101), memory/negative-guard-tests-use-ast]

## Acceptance Criteria

**AC1 — Kakao sender Agent는 interactive Windows 세션에서만 실행 (FR-32, ADD-15)**

1. **Given** Kakao 작업이 PC 앱 UI 자동화일 때 **When** Kakao sender Agent를 실행하면 **Then** **interactive user session에서 실행되고 Session 0 service-only 방식에 의존하지 않는다**: `autostart.py`가 **주입 가능한 세션 probe**(`is_interactive_session(*, probe=...)`)를 제공하고, 기본 probe는 **stdlib `ctypes` + Windows-gated**(예: `ProcessIdToSessionId`로 session ≠ 0 / interactive window station 확인)로 비-Windows·테스트에선 호출되지 않는다. probe가 비대화형(Session 0)이면 게이트가 **fail-closed**(Kakao 비허용). [Source: architecture-contract.md(70), src/rider_agent/secure_store.py(87-101), operations-security-test-contract.md(91)]
2. **And** 이 게이트는 **`run_agent`에 additive로 배선**된다(4.6 `start_kakao_sender` 옆): `run_agent(..., start_kakao_sender=True, session_probe=<probe>)`에서 노드가 `KAKAO_SEND` 보유 + probe가 비대화형이면 **Kakao 워커를 띄우지 않고**(`start_kakao_sender_worker_if_enabled` 호출 생략 → `kakao_worker=None` → heartbeat `kakao_status`=4.3 기본 `"disabled"`) 명확한 상태를 `on_status`/`log`로 surfacing한다. **probe 미주입(`session_probe=None`)이면 게이트 없음 = 4.6 동작 그대로(무회귀).** [Source: src/rider_agent/job_loop.py(774-833), src/rider_agent/heartbeat.py(86·138-142), 4-6 스토리(60)]
3. **And** 게이트/probe는 **순수 동기·Windows-gated·주입 가능**(테스트에서 실 OS 세션 API 미호출)하고 **secret/원시정보를 노출하지 않는다**(상태 사유는 평문 상수 `SESSION_0_SERVICE`/`SESSION_INTERACTIVE`, 로그는 고정 메시지·redact 통과). crawler-only 노드(= `KAKAO_SEND` 없음)는 게이트 대상이 아니다(AC3와 정합). [Source: project-context.md(81·89), memory/enum-member-count-locks, src/rider_agent/workers/kakao_sender.py(208-209)]

**AC2 — Windows Startup 또는 Task Scheduler 자동 시작 + 재부팅 후 복구 (P3-07)**

4. **Given** PC가 재부팅될 수 있을 때 **When** Windows Startup 또는 Task Scheduler로 Agent 자동 시작을 구성하면 **Then** 사용자 로그인 시 Agent를 띄우는 **launch 항목이 생성**된다: `autostart.py`가 (a) **launch-command 합성**(`build_agent_launch_command(*, executable=sys.executable, frozen=getattr(sys,"frozen",False), server_url=None)` → 개발은 `[sys.executable, "-m", "rider_agent", "run"]`, frozen은 `[exe_path, "run"]`, 선택 `--server-url`), (b) **등록 primitive**(`register_autostart(*, command, method=..., writer=.../runner=...)` — Startup 폴더 `.cmd` 쓰기 **또는** `schtasks /create /sc ONLOGON /it /f` 호출), (c) **해제**(`unregister_autostart`), (d) **조회**(`is_autostart_registered`)를 제공한다. **모든 OS 부작용(파일 쓰기·subprocess)은 주입 가능**(기본은 Windows-gated 실 호출, 테스트는 fake `writer`/`runner` + tmp_path). 등록은 **멱등**(같은 커맨드 재등록 시 중복 생성 0 — `DpapiSecretStore.put` 멱등 선례). [Source: implementation-contract.md P3-07(64), architecture-contract.md(70·72-85), architecture.md(213·457), src/rider_agent/secure_store.py(164-176)]
5. **And** **launch 커맨드·Startup 파일·`schtasks` 인자 어디에도 token/registration code가 포함되지 않는다**(secret 비노출): autostart는 `run` 진입(identity는 run 시점 DPAPI store 로드)만 등록한다. 커맨드의 실행 경로/`--server-url`은 secret이 아니다. 등록/해제 로그는 고정 메시지만(예: `autostart registered (method=startup)`)·redact 통과. [Source: src/rider_agent/secure_store.py(273-297), operations-security-test-contract.md(93), project-context.md(81)]
6. **And** **재부팅 후 사용자 로그인 시 Agent가 자동 시작되고 heartbeat가 복구된다**: 등록된 launch가 4.4의 `run` 진입(`run_agent` startup = identity 로드 → token 검증 → heartbeat thread 기동 → main loop)을 그대로 띄우므로 **새 복구 로직을 짜지 않는다** — autostart 테스트는 합성 커맨드가 `rider_agent ... run`(또는 frozen exe + `run`)을 가리킴을 단언하고, heartbeat 복구 자체는 4.3/4.4 테스트가 이미 잠근다. [Source: src/rider_agent/job_loop.py(755-872), src/rider_agent/__main__.py(112-161·172-173), epics.md AC(845-847)]

**AC3 — crawler-only vs Kakao sender 노드 실행 조건·job type 구분 + Agent #1 재사용 (FR-32, FR-28)**

7. **Given** crawler-only 노드와 Kakao sender 노드의 요구가 다를 때 **When** Agent를 구성하면 **Then** **순수 crawler Agent와 Kakao sender Agent의 실행 조건과 처리 가능 job type이 구분된다**: `autostart.py`가 capability로 노드 역할을 도출하는 resolver를 제공한다 — `resolve_node_role(capabilities) -> "kakao_sender" | "crawler_only"`(= `CAPABILITY_KAKAO_SEND in capabilities`), `requires_interactive_session(capabilities) -> bool`(Kakao sender만 True), `handleable_job_types(capabilities)`(= capability 집합, `heartbeat.CAPABILITY_*` 재사용). **새 역할 enum/별도 실행 경로를 만들지 않고** 4.6 capability 게이트를 노출만 한다. crawler-only 노드는 interactive-session 게이트 비대상·Kakao 워커 미기동. [Source: src/rider_agent/heartbeat.py(62-77), src/rider_agent/workers/kakao_sender.py(208-209·436-440), epics.md AC(849-851)]
8. **And** **현재 일반 Windows PC를 Agent #1로 사용해 기존 Chrome/Kakao 환경을 활용한다**(FR-28): 기본 capability 전체집합(`DEFAULT_CAPABILITIES` 6종)은 `resolve_node_role` 상 **Kakao sender 노드**(interactive 필요)이고, crawler-only는 `KAKAO_SEND`를 뺀 **capability 부분집합**일 뿐 별도 코드 경로/탭 9→100 확장이 아니다(ADD-15 금지행위). [Source: src/rider_agent/heartbeat.py(70-77), operations-security-test-contract.md(89), epics.md FR-28(71)·AC(852)]

## Tasks / Subtasks

- [x] **Task 1 — `autostart.py`: launch-command 합성 + 노드 역할 resolver (AC: 2, 3)**
  - [x] `src/rider_agent/autostart.py` 신설. architecture.md(457) 트리(`autostart.py # Windows Startup/Task Scheduler`)·4.6 forward-commit("autostart(4.7)")의 실현 — 계획된 신설. 모듈 docstring에 범위(실행 조건 게이트 + autostart 등록 + 노드 역할; 실 OS 부작용은 Windows-gated lazy·주입 가능)와 sync/단방향/import-safety 규약을 4.1~4.6 모듈과 동형으로 명시. [Source: architecture.md(446-457), 4-6 스토리(33)]
  - [x] `build_agent_launch_command(*, executable=sys.executable, frozen=getattr(sys,"frozen",False), server_url=None, module="rider_agent") -> list[str]`: frozen이면 `[executable, "run"]`, 아니면 `[executable, "-m", module, "run"]`. `server_url` 주면 `--server-url <url>` 추가. **token/code 절대 미포함**(`run`만 — identity는 DPAPI). `sys`는 모듈 상단 import 가능(stdlib). [Source: src/rider_agent/__main__.py(99-109·172-173), architecture-contract.md(72-85)]
  - [x] 노드 역할 상수(**평문**, enum/"정확히 N" lock 금지): `NODE_ROLE_KAKAO_SENDER = "kakao_sender"`, `NODE_ROLE_CRAWLER_ONLY = "crawler_only"`. `resolve_node_role(capabilities=DEFAULT_CAPABILITIES) -> str`(= `CAPABILITY_KAKAO_SEND in capabilities` 분기), `requires_interactive_session(capabilities=DEFAULT_CAPABILITIES) -> bool`, `handleable_job_types(capabilities=DEFAULT_CAPABILITIES) -> tuple[str, ...]`(= `tuple(capabilities)`). `heartbeat`에서 `CAPABILITY_KAKAO_SEND`/`DEFAULT_CAPABILITIES` import(재사용). [Source: src/rider_agent/heartbeat.py(62-77), src/rider_agent/workers/kakao_sender.py(208-209), memory/enum-member-count-locks]
  - [x] **순수 동기 + stdlib(+선택 `rider_crawl.redaction.redact`)/자기 패키지만 import** — 4.1 AST 가드 `rglob` 자동 검사. async 0, `rider_server` 0. [Source: tests/agent/test_agent_package.py(33-34·188-245)]
- [x] **Task 2 — interactive-session probe + Kakao 세션 게이트 (AC: 1, 3)**
  - [x] 세션 사유 상수(**평문**): `SESSION_INTERACTIVE = "interactive"`, `SESSION_0_SERVICE = "session0_service"`. `is_interactive_session(*, probe=None) -> bool`: `probe` 주입이면 그 결과, 미주입이면 기본 **Windows-gated lazy probe**(`_default_session_probe`) — 비-Windows에선 보수적으로 처리(예: 게이트는 호출자가 결정; probe 자체는 win32에서만 실 판정, 그 외엔 `True`/명시 정책 — Dev Notes 열린 질문 참조). 실 `ctypes`/win32 호출은 **함수 내부 lazy**(import-safety). [Source: src/rider_agent/secure_store.py(87-101), architecture-contract.md(70)]
  - [x] Kakao 세션 게이트 판정 헬퍼: `kakao_session_allowed(capabilities, *, session_probe=None) -> bool`(또는 `(allowed, reason)` 튜플) — 노드가 `KAKAO_SEND` **없으면** 게이트 무관(crawler-only → `True`/`None` 사유), 있으면 `is_interactive_session(probe=session_probe)`로 판정(비대화형 → fail-closed `False`, 사유 `SESSION_0_SERVICE`). **이 판정 로직은 `autostart.py`에 두고 `kakao_sender.py`/`heartbeat.py`는 0줄.** [Source: src/rider_agent/workers/kakao_sender.py(208-209·436-440), 4-6 스토리(23)]
  - [x] 로그/사유에 raw OS 식별자(전체 경로·사용자명) 평문 노출 금지 — 고정 사유 상수만, 자유 텍스트는 `redact()` 통과(`redact`는 운영 식별자를 못 가리므로 처음부터 안 넣는다). [Source: project-context.md(81), memory/redact-skips-operational-ids]
- [x] **Task 3 — autostart 등록/해제/조회 primitive (Startup 폴더 + Task Scheduler, 주입 가능·멱등) (AC: 2)**
  - [x] 등록 메서드 상수(**평문**): `METHOD_STARTUP = "startup"`, `METHOD_TASK_SCHEDULER = "task_scheduler"`. 고정 식별자: `TASK_NAME = "RiderBotAgent"`(Task Scheduler), `STARTUP_FILENAME = "rider_agent.cmd"`(Startup 폴더). [Source: architecture-contract.md(70·73-83)]
  - [x] 경로 헬퍼(주입 가능): `default_startup_dir(*, environ=os.environ) -> Path`(= `%APPDATA%/Microsoft/Windows/Start Menu/Programs/Startup`). 테스트는 `tmp_path`/fake environ 주입(실 `%APPDATA%` 미사용). [Source: src/rider_agent/secure_store.py(60-67)]
  - [x] `register_autostart(*, command, method=METHOD_STARTUP, startup_dir=None, writer=None, runner=None, log=None) -> dict`: **Startup** = 커맨드를 감싼 `.cmd` 텍스트를 `STARTUP_FILENAME`에 **atomic/멱등 쓰기**(같은 내용이면 재쓰기 0 — `DpapiSecretStore.put`/`_atomic_write_text` 선례, 단 `rider_crawl.ui_settings._atomic_write_text` 재사용은 선택; stdlib `Path.write_text`도 허용). **Task Scheduler** = `schtasks /create /tn TASK_NAME /tr "<command>" /sc ONLOGON /it /f` 인자 리스트를 만들어 주입 `runner`(기본 Windows-gated `subprocess.run`) 호출(`/it`=interactive, `/sc ONLOGON`=로그인 시, `/f`=멱등 덮어쓰기). 결과는 `{method, target(경로/태스크명), command}` dict. [Source: src/rider_agent/secure_store.py(164-176·202-207), architecture-contract.md(70)]
  - [x] `unregister_autostart(*, method=METHOD_STARTUP, startup_dir=None, remover=None, runner=None) -> bool`(Startup 파일 삭제 / `schtasks /delete /tn ... /f`), `is_autostart_registered(*, method=METHOD_STARTUP, startup_dir=None, runner=None) -> bool`(파일 존재 / `schtasks /query /tn ...` 성공). 멱등(미존재 해제는 무해). 실 subprocess/파일 I/O는 주입으로 대체. [Source: src/rider_agent/secure_store.py(178-207)]
  - [x] **secret 비노출:** 등록 커맨드는 `build_agent_launch_command` 산출물(= `run`)이라 token/code가 없다. `schtasks` 인자/`.cmd` 내용/로그에 secret 0. [Source: operations-security-test-contract.md(93), project-context.md(81)]
- [x] **Task 4 — `__main__.py` 얇은 `autostart` 서브커맨드 + `run_agent` 세션 게이트 배선 (additive) (AC: 1, 2)**
  - [x] `src/rider_agent/__main__.py` **additive**: `register`/`run`과 동형의 **얇은** `autostart` 서브커맨드 추가(`main()`에 `if argv[0] == "autostart"` 분기 — 그 외 토큰은 기존대로 배너 폴백, 무회귀). `_run_autostart(argv, *, ...)`: `--register`/`--unregister`/`--status`(+`--method startup|task_scheduler`, `--server-url`) 파싱 → autostart import는 **함수 내부 defer**(import-safety·runpy 경고 회피) → 호출 → redact 통과 한 줄 출력. **tkinter/레거시 UI import 0**(4.1 가드 `test_main_does_not_import_tkinter_or_legacy_ui` green 유지). 인자 없는 실행은 **여전히 배너**(`test_main_returns_zero_and_prints_sync_banner` green). [Source: src/rider_agent/__main__.py(34-96·164-180), tests/agent/test_agent_package.py(259-313)]
  - [x] `src/rider_agent/job_loop.py` **additive(무회귀)**: `run_agent(...)`에 optional `session_probe: Callable[[], bool] | None = None`(필요 시 `enforce_interactive_session: bool = False`) 추가. `if start_kakao_sender:` 블록(808-833)에서 `start_kakao_sender_worker_if_enabled` **호출 전에** `autostart.kakao_session_allowed(capabilities, session_probe=session_probe)`를 lazy import해 판정 — 비허용이면 워커를 **띄우지 않고**(`kakao_worker=None` 유지) `on_status`/`log`로 surfacing. **`session_probe=None`이면 게이트 통과(=4.6 동작 그대로)** — 기존 `test_job_loop.py`(609-676)·`test_kakao_sender.py`의 `run_agent(start_kakao_sender=True)` 전부 무회귀. lazy import로 순환 import 회피(4.6 선례). [Source: src/rider_agent/job_loop.py(774-833), tests/agent/test_job_loop.py(609-676), 4-6 스토리(204)]
  - [x] **빈 호출/빈 stub 금지 — 실제 배선만**(4.4 규율 계승). 게이트는 실제로 워커 기동을 막아야 하고, 서브커맨드는 실제 등록 primitive를 호출해야 한다. [Source: 4-4 스토리(seam만/빈 호출 금지), 4-6 스토리(80)]
- [x] **Task 5 — 테스트: `tests/agent/test_autostart.py` (AC: 1~8)** — 외부 호출 없음(fake `writer`/`runner`/`session_probe` + tmp_path/fake environ), 가짜 경로/명령만:
  - [x] **위치/네이밍:** `tests/agent/test_autostart.py`(평면, `__init__.py` 미추가 — 4.1~4.6 미러). 신규 basename. `rider_agent.__main__`을 **모듈 top에서 import 금지**(필요 시 함수 내부 defer — runpy 경고 회피). [Source: architecture.md(461), memory/agent-main-runpy-warning]
  - [x] **(AC2 — launch-command):** 개발(`frozen=False`) → `[..., "-m", "rider_agent", "run"]`; `frozen=True` → `[exe, "run"]`; `server_url` 주면 `--server-url` 포함; **모든 케이스에 token/code/`--code` 0**(secret 비노출 단언). [Source: src/rider_agent/__main__.py(99-109)]
  - [x] **(AC2 — 등록 멱등·해제·조회):** fake `writer`로 Startup `.cmd` 1회 생성, **재등록 시 중복/재쓰기 0**(멱등); `unregister`로 제거; `is_autostart_registered` True→False. Task Scheduler 경로는 fake `runner`가 받은 인자에 `/create`·`/sc`·`ONLOGON`·`/it`·`TASK_NAME` 포함·`/f` 멱등 단언. **실 `schtasks`/실 `%APPDATA%` 미호출**(주입·tmp_path). [Source: src/rider_agent/secure_store.py(164-176)]
  - [x] **(AC1·AC3 — 세션 게이트·노드 역할):** `is_interactive_session(probe=lambda:False)` → 게이트 fail-closed; `resolve_node_role(DEFAULT_CAPABILITIES)`==`kakao_sender`, `requires_interactive_session` True; `KAKAO_SEND` 뺀 부분집합 → `crawler_only`·`requires_interactive_session` False·게이트 무관(`kakao_session_allowed` True). 사유 상수가 평문임 단언. [Source: src/rider_agent/heartbeat.py(70-77), src/rider_agent/workers/kakao_sender.py(208-209)]
  - [x] **(AC1 — run_agent 게이트 배선·무회귀):** `run_agent(start_kakao_sender=True, capabilities=<KAKAO 포함>, session_probe=lambda:False, ...)` → Kakao 워커 미기동(`summary.kakao_worker is None`)·`kakao_status` `"disabled"`·상태 surfacing; **`session_probe=None`(미주입)이면 기존처럼 워커 기동**(무회귀). 주입 fake transport/store + 즉시 정지 `stop_event`/주입 `sleep`으로 hang 0. [Source: src/rider_agent/job_loop.py(774-833), tests/agent/test_job_loop.py(628-676)]
  - [x] **(AC2 — 서브커맨드):** `__main__.main(["autostart","--status"])`/`--register`/`--unregister`가 주입 의존성으로 0/1 반환·redact 출력(token/code 0); 인자 없는 `main([])`은 여전히 배너(무회귀). [Source: src/rider_agent/__main__.py(164-174)]
  - [x] **(누출 가드):** 모든 fixture는 가짜 경로/명령만 — 실제 token·chat_id·휴대폰·이메일·OTP 금지. 실 OS(schtasks/세션 API/Startup 폴더) 미호출. 로그 캡처·출력·등록 산출물에 secret 0건 단언. [Source: project-context.md(55·81), operations-security-test-contract.md(93), 4-6 스토리(87)]
- [x] **Task 6 — 회귀·범위·누출 검증 및 마무리 (AC: 1~8)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/agent/test_agent_package.py`·`test_job_loop.py`·`test_kakao_sender.py`·`test_heartbeat.py`), 신규 케이스만큼만 증가가 정상. [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] **4.1 가드 green 재확인(핵심):** `pytest tests/agent/test_agent_package.py -q`의 (a) third-party root == `{rider_crawl}`, (b) sync(자기 모듈 async 0·`import asyncio` 0), (c) 단방향(`rider_server` 0), (d) pyproject deps **정확히 9개·핀 불변**, (e) `__main__` tkinter/legacy UI import 0 + 배너 0 반환이 **신규 `autostart.py` + `__main__.py`/`job_loop.py` additive 편집 후에도 통과**. `rglob`이 `autostart.py`를 자동 검사하므로 stdlib(+`rider_crawl`)만 썼다면 green. [Source: tests/agent/test_agent_package.py(188-313)]
  - [x] **enum/lock 무회귀:** 노드 역할·세션 사유·등록 메서드는 평문 상수(새 enum/"정확히 N" lock 0). `rider_server` 도메인 enum 테스트 무회귀(import 0). [Source: memory/enum-member-count-locks, src/rider_agent/heartbeat.py(58-77)]
  - [x] **무회귀 확인:** `git diff -w --stat`에 **신규 `autostart.py` + 신규 `tests/agent/test_autostart.py` + `__main__.py`(additive) + `job_loop.py`(additive) + sprint-status/스토리**만 보이고 **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py`·`workers/kakao_sender.py` 변경 0줄**임을 확인(CRLF/LF 노이즈 무시 — `git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 raw token/code/평문 secret 0건, launch 커맨드/등록 산출물에 `--code`/token 0건, `src/rider_crawl/`에 `rider_agent` import 신규 0건, `autostart.py`에 `rider_server` import 0건. [Source: project-context.md(64·81), operations-security-test-contract.md(93)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(dev 노트에 잠정 수치 박지 말 것 — 4.1~4.6에서 stale 수치 MEDIUM 재발: qa-e2e가 dev 노트 뒤에 케이스를 append). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리 산출물: 신규 `src/rider_agent/autostart.py`(launch-command + 노드 역할 resolver + interactive-session probe/게이트 + autostart 등록/해제/조회) + 신규 `tests/agent/test_autostart.py` + `src/rider_agent/__main__.py`의 **얇은 `autostart` 서브커맨드(additive)** + `src/rider_agent/job_loop.py`의 **`run_agent` interactive-session 게이트(additive, `session_probe=None`이면 무회귀)**. **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py`·`workers/kakao_sender.py`는 무변경(reuse only).**
- **건드리지 않는다:** PyInstaller exe **빌드**·버전 manifest·rollback 바이너리·tray-app GUI(deploy/인프라·ADD-12, Epic 5 — autostart는 커맨드만 합성), 서버 측 capability 수신/노드 배정·Admin autostart 표시·`agent_offline` runbook(Epic 5), 배민 auth(4.8)·쿠팡 Gmail 2FA(4.9). **다른 type용 빈 stub/빈 GUI 파일도 만들지 않는다.** [Source: epics.md Story 4.8~4.9(854-903), architecture.md(212-213·437), operations-security-test-contract.md(62)]

### 열린 질문 / 의도된 부분 구현 (반드시 읽을 것)

- **등록 메커니즘: Startup 폴더(권장 기본) vs Task Scheduler.** architecture-contract(70)은 "tray app 또는 console app + **Task Scheduler 또는 Startup** 등록" 둘 다 허용한다. **Startup 폴더 `.cmd`를 기본(`METHOD_STARTUP`)** 으로 권장한다 — (a) 관리자 권한 불필요, (b) **사용자 로그인 시 = 본질적으로 interactive 세션**(AC1 자연 충족), (c) 파일 쓰기라 멱등/주입/테스트가 단순. **Task Scheduler(`METHOD_TASK_SCHEDULER`)** 는 `/sc ONLOGON /it`로 동일하게 로그인-시-interactive 실행을 주되 `schtasks` subprocess가 필요해 대안으로 둔다. **두 메서드 모두 실제 배선**하고(빈 stub 금지), 메서드는 인자로 선택한다. [Source: architecture-contract.md(70), implementation-contract.md(64)]
- **비-Windows에서 기본 세션 probe의 정책.** `_default_session_probe`는 win32에서만 실 판정(session ≠ 0)한다. 비-Windows(WSL/CI 개발)에서의 기본 반환값은 **명시 정책**으로 정한다 — 권장: 게이트는 `session_probe` **명시 주입 시에만** 작동(미주입이면 무게이트=무회귀), 기본 probe는 win32 외에선 호출되지 않거나 `True`(개발 편의)로 둔다. 운영(win32)에서만 실제 Session 0 차단이 의미를 갖는다. **핵심: 미주입 시 4.6 동작 보존**(AC1.2 무회귀)이 절대 불변. [Source: src/rider_agent/secure_store.py(94-101), src/rider_agent/job_loop.py(774-833)]
- **launch 커맨드 인용/이스케이프(Windows).** Startup `.cmd`/`schtasks /tr`은 경로에 공백이 있을 수 있다 — 리스트→문자열 변환 시 `subprocess.list2cmdline`(stdlib) 등으로 안전 인용한다. 커맨드 자체에 secret이 없으므로 인용 실패가 누출로 이어지진 않으나, 재부팅 자동 시작 안정성을 위해 경로 인용을 빠뜨리지 않는다. [Source: architecture-contract.md(73-83)]

### 설계 결정 — 무엇을 재사용하고 무엇이 신규인가 (반드시 읽을 것)

- **crawler-only vs Kakao-sender 구분은 이미 capability로 존재 — resolver는 "노출"만(재구현 금지).** 4.6 `start_kakao_sender_worker_if_enabled`가 `CAPABILITY_KAKAO_SEND in capabilities`로 Kakao 워커 기동 여부를 이미 가른다(kakao_sender.py:208-209·436-440). 4.7 `resolve_node_role`/`requires_interactive_session`/`handleable_job_types`는 이 **같은 capability 신호**를 명시적 API로 노출할 뿐 — 새 역할 enum·별도 실행 경로·새 job-type 목록을 만들지 않는다(`heartbeat.CAPABILITY_*` import 재사용). crawler-only는 `KAKAO_SEND`를 뺀 부분집합이지 다른 코드 경로가 아니다(ADD-15 "탭 9→100 확장 금지"와 정합). [Source: src/rider_agent/heartbeat.py(62-77), src/rider_agent/workers/kakao_sender.py(208-209·436-440)]
- **interactive-session 게이트는 `autostart.py`에서 판정, `run_agent`에서 소비(분리).** 판정 로직(`is_interactive_session`/`kakao_session_allowed`)은 `autostart.py`에 응집하고, `run_agent`는 `start_kakao_sender_worker_if_enabled` **호출 전에** 그 판정을 consult해 비허용이면 워커를 안 띄운다. 이렇게 하면 **`kakao_sender.py`·`heartbeat.py`는 0줄**이고 게이트는 `job_loop.run_agent`의 additive 한 곳에만 들어간다. `session_probe=None` 기본이 4.6 동작을 그대로 보존한다(무회귀 절대 불변). [Source: src/rider_agent/job_loop.py(808-833), src/rider_agent/workers/kakao_sender.py(414-455), 4-6 스토리(23·60)]
- **Windows-gated lazy import = `secure_store._dpapi_crypt` 패턴 그대로.** 실 `ctypes`(세션 probe)·`subprocess`(schtasks)·Startup 파일 쓰기는 **함수 내부 lazy**로 두고 win32에서만 실행한다 → `import rider_agent.autostart`가 비-Windows에서도 import-safe(4.1 import-safety 가드 green). 모듈 상단 import는 stdlib(`os`/`sys`/`pathlib`/`subprocess`는 import 자체는 안전; 실 호출만 gated)와 `heartbeat`(capability 상수)·선택 `rider_crawl.redaction.redact`만. [Source: src/rider_agent/secure_store.py(20-26·87-101), tests/agent/test_agent_package.py(277-300)]
- **autostart는 `run`(4.4)을 launch — 새 루프 0.** 재부팅 복구의 heartbeat 재기동은 `run_agent` startup이 이미 한다(job_loop.py:755-872, identity→token→heartbeat thread→loop). autostart는 그 진입을 OS에 등록만 하고(4.4 `__main__ run` 서브커맨드 재사용), launch 커맨드는 `run`만(token/code 0 — identity는 DPAPI store). [Source: src/rider_agent/job_loop.py(755-872), src/rider_agent/__main__.py(112-161)]
- **등록 멱등 = `DpapiSecretStore.put` 선례.** Startup `.cmd`는 같은 내용이면 재쓰기하지 않고(churn 방지), Task Scheduler는 `/f`로 덮어쓴다 — 재등록이 중복 항목을 만들지 않는다. [Source: src/rider_agent/secure_store.py(164-176)]

### 재사용 대상 공개 표면 (재구현 금지 — import/주입만)

| 도메인 | 공개 심볼 | 파일/행 | 4.7 사용 |
|---|---|---|---|
| capability 상수/기본집합 | `CAPABILITY_KAKAO_SEND`, `DEFAULT_CAPABILITIES`(6종) | rider_agent/heartbeat.py(66·70-77) | 노드 역할 resolver·세션 게이트 활성 조건(재사용·무변경) |
| Kakao 워커 기동 게이트 | `start_kakao_sender_worker_if_enabled(*, capabilities, ...)` | rider_agent/workers/kakao_sender.py(414-455) | `run_agent`가 세션 게이트 통과 후에만 호출(무변경) |
| Kakao 활성 신호 | `KakaoSenderWorker._enabled = CAPABILITY_KAKAO_SEND in capabilities` | rider_agent/workers/kakao_sender.py(208-209) | crawler-only/kakao-sender 구분 기준(동일 신호 재사용) |
| startup 오케스트레이션 | `run_agent(...)`, `AgentRunSummary(kakao_worker=...)` | rider_agent/job_loop.py(742-872) | `session_probe` additive 배선·`kakao_worker is None` 검증(additive 편집 대상) |
| run 진입 | `_run_agent_loop`/`run` 서브커맨드 | rider_agent/__main__.py(112-173) | autostart launch 커맨드가 가리키는 진입(무변경) |
| Windows-gated lazy 패턴 | `_dpapi_crypt`(ctypes·win32-gated·함수 내부 import) | rider_agent/secure_store.py(87-131) | 세션 probe/schtasks를 동형으로 lazy·gated 구현(패턴 선례) |
| 경로/상태 루트 패턴 | `default_agent_state_dir`/`default_identity_path` | rider_agent/secure_store.py(60-75) | `default_startup_dir`/경로 헬퍼 주입-가능 동형 설계(선례) |
| 멱등 쓰기 패턴 | `DpapiSecretStore.put`(같은 값 재쓰기 0), `_atomic_write_text`(2.2, 선택) | rider_agent/secure_store.py(164-176·202-207) | Startup `.cmd` 멱등/atomic 쓰기(선례·선택 재사용) |
| redaction | `redact` | rider_crawl/redaction.py | 등록/해제 로그·서브커맨드 출력 마스킹(선택 import) |
| identity 게이트(참고) | `load_local_agent_identity`(run 시 DPAPI 로드) | rider_agent/secure_store.py(273-297) | autostart가 token을 커맨드에 안 넣어도 되는 근거(무변경) |

- **주의 — 단방향 import:** `rider_server`를 `autostart.py`가 import하면 `test_rider_agent_never_imports_rider_server`가 깨진다. 노드 역할/세션 사유는 `rider_agent` 안 **평문 상수**로 두고 `rider_server`를 import하지 않는다. [Source: tests/agent/test_agent_package.py(240-245), memory/enum-member-count-locks]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl`·`rider_server`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py`·`workers/kakao_sender.py` 무변경** — `git diff -w` = 신규 `autostart.py` + 신규 테스트 + `__main__.py`(additive) + `job_loop.py`(additive) + sprint-status/스토리.
- (b) **`run_agent` 무회귀** — `session_probe=None`(기본)이면 4.6 동작 그대로(start_kakao_sender 경로 포함). 기존 `test_job_loop.py`(609-676)·`test_kakao_sender.py`의 `run_agent` 케이스 전부 통과.
- (c) **`__main__` 무회귀** — 인자 없는 실행은 여전히 sync 배너 0 반환(`test_main_returns_zero_and_prints_sync_banner`), tkinter/legacy UI import 0(`test_main_does_not_import_tkinter_or_legacy_ui`). `autostart`는 `register`/`run`처럼 명시 토큰일 때만, import는 함수 내부 defer.
- (d) **의존성 단방향·sync** — 신규 `autostart.py`는 stdlib(+`rider_crawl.redaction`·`rider_agent.heartbeat`)만 import(reuse seam), async 0, `rider_server` 0. `rglob`이 자동 검사.
- (e) **새 프레임워크/의존 0** — autostart는 stdlib `subprocess`/`os`/`sys`/`pathlib`/`ctypes`(win32-gated). `pywin32`/`winshell` 등 third-party 0, deps 정확히 9개 → 4.1 가드 green.
- (f) **import-safety** — 실 `schtasks`/`ctypes`/Startup 파일 I/O는 함수 내부 lazy·win32-gated. `import rider_agent.autostart`가 무거운/플랫폼 의존을 끌지 않는다.
- (g) **secret 비노출** — launch 커맨드/Startup `.cmd`/`schtasks` 인자/로그/서브커맨드 출력에 token·registration code 평문 0(`run`만, identity는 DPAPI).
- (h) **interactive-session fail-closed** — Kakao 노드가 Session 0면 워커를 **띄우지 않고** surfacing(임의 실행 0). crawler-only는 게이트 무관.
- (i) **enum/역할 lock 무회귀** — 노드 역할·세션 사유·등록 메서드는 평문 상수("정확히 N" lock 0), `rider_server` enum 무회귀.
- (j) **누출 0** — 테스트 실 OS(schtasks/세션 API/Startup 폴더) 미호출, 가짜 경로/명령만.
[Source: project-context.md(46·64·81·82·89·90), operations-security-test-contract.md(87-95), tests/agent/test_agent_package.py(188-313), src/rider_agent/job_loop.py(774-833)]

### 이전 스토리/회고 인텔리전스 (4.1~4.6 → 4.7 이월 교훈)

- **4.4/4.6이 깐 startup 토대 위에 빌드(직접 계승):** 4.4 `run_agent`가 startup(identity→token→heartbeat→loop)을 구현하고 `__main__`에 `run` 서브커맨드를 더했다. 4.6은 `start_kakao_sender`/`kakao_send`/`kakao_build_config`를 `run_agent`에 더하고 `start_kakao_sender_worker_if_enabled`를 capability 게이트로 배선했다(forward-point: "autostart(4.7)"). 4.7은 그 **옆에** `session_probe`를 동형으로 더하고(무회귀 기본값), 4.1/4.4의 `__main__` 서브커맨드 패턴으로 `autostart`를 더한다 — 새 seam 발명 0. [Source: src/rider_agent/job_loop.py(774-833), src/rider_agent/__main__.py(164-174), 4-6 스토리(146)]
- **Windows-gated lazy = `secure_store`(4.2) 선례 그대로:** 4.2가 DPAPI를 `ctypes`+함수 내부 lazy+win32-gated로 짜 비-Windows import-safety를 지켰다. 4.7의 세션 probe(`ctypes`)·`schtasks`(`subprocess`)·Startup 파일 쓰기도 **같은 패턴** — 모듈 상단에서 실 OS를 끌지 않는다. [Source: src/rider_agent/secure_store.py(20-26·87-101)]
- **enum/lock 전수 점검(memory):** 노드 역할/세션 사유/등록 메서드를 enum이나 "정확히 N개" 테스트로 잠그면 후속(4.8/4.9)·`rider_server` 도메인 lock이 깨질 수 있다 → **평문 상수**로 두고 count-lock 0(`TOKEN_STATUS_*`·`DEFAULT_CAPABILITIES`·`KAKAO_OUTCOME_*` 선례). [Source: src/rider_agent/secure_store.py(50-54), src/rider_agent/heartbeat.py(58-77), memory/enum-member-count-locks]
- **부정 가드는 AST로(4.1 계승, 자동 적용):** 단방향·sync·no-new-framework 가드는 4.1이 AST로 `src/rider_agent/`를 `rglob`한다 — 신규 `autostart.py`는 **자동 검사**된다. 새 가드를 raw grep으로 짜지 말 것(scope docstring이 `rider_server`/`async`/`schtasks`/`Session 0` 같은 금지·OS 심볼명을 문자열로 언급해 오탐). [Source: tests/agent/test_agent_package.py(33-34·188-245), memory/negative-guard-tests-use-ast]
- **redact는 운영 식별자를 못 가린다(4.6 핵심 교훈):** 자유 텍스트 `redact()`는 경로/사용자명/방명 같은 운영 식별자를 마스킹하지 못한다 → 로그/사유에 raw OS 경로·사용자명을 **처음부터 넣지 않고** 고정 사유 상수만 쓴다(4.6 `_failure`가 raw 예외 본문을 안 실은 것과 동형). [Source: 4-6 스토리(202), memory/redact-skips-operational-ids, src/rider_agent/workers/kakao_sender.py(357-375)]
- **테스트 수치/File List 단일 정본(A2″ 계승):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. 4.1~4.6 모두 qa-e2e append 후 stale로 MEDIUM이 났다. [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2, 4-6 스토리(194)]
- **runpy 경고(memory):** 테스트가 `rider_agent.__main__`을 모듈 top에서 import하면 runpy RuntimeWarning이 난다. `test_autostart.py`는 `__main__`을 top-import하지 말고(필요 시 함수 내부 defer) `autostart`/`job_loop` 심볼만 import한다. [Source: memory/agent-main-runpy-warning]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest는 **WSL `python3`가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`**로 돌린다(WSL python엔 pytest 미설치). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w`로 하고 무관한 EOL flip을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]
- **OS 통합 테스트 주의:** 실제 `schtasks`·실 세션 API(`ctypes`)·실 `%APPDATA%` Startup 폴더 쓰기를 쓰지 말고 **주입 fake `runner`/`writer`/`session_probe`/`remover` + `tmp_path`/fake environ + 호출 인자 캡처**로 등록 멱등·해제·세션 게이트·노드 역할을 결정적으로 검증한다(테스트 OS-오염/flaky 방지). 비-Windows CI에서도 통과해야 한다(import-safety). [Source: src/rider_agent/secure_store.py(87-101), 4-6 스토리(157)]

### Project Structure Notes

- 신규 파일은 architecture.md(457) 트리와 정렬: `src/rider_agent/autostart.py`(= `# Windows Startup/Task Scheduler`). 4.5/4.6이 forward-commit한 "`autostart.py`는 4.7이 만든다"의 실현 — **계획된 신설이지 이탈이 아니다.** (`workers/`처럼 서브패키지가 아니라 평면 모듈 — 트리가 `rider_agent/autostart.py`로 평면 배치.) [Source: architecture.md(446-457), 4-6 스토리(161)]
- 테스트는 `tests/agent/test_autostart.py`(평면, `__init__.py` 미추가). 기존 `tests/agent/test_{agent_package,registration,secure_store,heartbeat,job_loop,browser_profile,kakao_sender}.py`와 별 basename. [Source: architecture.md(461), 4-6 스토리(162)]
- **변이/충돌:** `project-context.md`의 `rider_agent` 진전 반영(autostart/노드 역할 신설)은 **Epic 4 retro**에서 한다(rider_server를 Epic 2 retro에서 반영한 선례). 본 스토리에서 project-context.md는 수정하지 않는다. [Source: 4-6 스토리(163), project-context.md(114)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.7(833-852)] — user story + AC(interactive session·Session 0 금지·Windows Startup/Task Scheduler autostart·재부팅 후 자동 시작·heartbeat 복구·crawler-only vs kakao sender 실행 조건/job type 구분·Agent #1 재사용).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-32(75·185)·FR-28(71·181)·FR-12(43)] — Local Agent 실제 실행 조건(interactive session·Session 0 service-only 금지·autostart·crawler/kakao 구분)·현재 PC를 Agent #1로·heartbeat 보고.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P3-07(64)] — "Add Windows Startup or Task Scheduler launch." → "Agent starts after reboot and user login."
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md#Local-Agent-Runtime(68-85)·startup(90-94)·Agent-Job-Types(120-129)] — interactive desktop 필요·Session 0 service-only 불가·tray/console + Task Scheduler/Startup·`C:\RiderBot\` 레이아웃·startup 순서(load identity→validate token→heartbeat→kakao worker→loop)·job type 목록.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#reboot-risk(62)·Agent#1(55)·forbidden(87-95)] — 재부팅/sleep 운영 위험·현재 PC를 Agent #1로·금지 행위(같은 세션 Kakao 병렬·탭 9→100 확장·secret 로깅).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md#agents(35)·heartbeat(69)] — agents 테이블(capacity_json·status)·heartbeat가 capabilities 포함(서버 수신은 Epic 5).
- [Source: src/rider_agent/job_loop.py(742-872)] — `run_agent` startup 배선(`start_kakao_sender`/`kakao_send`/`kakao_build_config` 자리)·`AgentRunSummary.kakao_worker`·`start_kakao_sender_worker_if_enabled` 호출 블록(808-833), `session_probe` additive 대상.
- [Source: src/rider_agent/__main__.py(34-180)] — `register`/`run` 얇은 서브커맨드 패턴(deferred import·redact 출력·배너 폴백), `autostart` 서브커맨드 additive 대상.
- [Source: src/rider_agent/workers/kakao_sender.py(208-209·414-455)] — `_enabled = CAPABILITY_KAKAO_SEND in capabilities`·`start_kakao_sender_worker_if_enabled`(capability 게이트) — crawler/kakao 구분 신호 재사용·무변경.
- [Source: src/rider_agent/heartbeat.py(58-86·138-142)] — `CAPABILITY_*`(평문)·`DEFAULT_CAPABILITIES`(6종)·`DEFAULT_KAKAO_STATUS="disabled"`·`kakao_status` provider 자리 — 노드 역할/게이트 재사용·무변경.
- [Source: src/rider_agent/secure_store.py(20-26·60-101·164-176·273-297)] — Windows-gated lazy import(import-safety)·경로 헬퍼·멱등 put·identity DPAPI 로드 — autostart 동형 설계 선례·무변경.
- [Source: tests/agent/test_agent_package.py(33-34·188-313)] — 4.1 가드(`rglob` 재귀·sync·third-party root==rider_crawl·단방향·deps 9핀·`__main__` tkinter/legacy 0·배너 0) — 신규 `autostart.py`·additive 편집 자동 적용·green 유지.
- [Source: tests/agent/test_job_loop.py(605-676)] — `run_agent` startup 테스트(identity 없음 미진입·정상 기동·heartbeat thread 정리) — `session_probe=None` 무회귀 기준.
- [Source: _bmad-output/implementation-artifacts/4-6-kakaosenderworker-fifo-직렬-전송과-정확한-채팅방-검증.md(19·23·60·146·202·204)] — 서버 stub 검증·provider thread-through·startup 게이트 배선·enum lock 회피·redact 한계·`run_agent` additive 선례.
- [Source: _bmad-output/implementation-artifacts/epic-3-retro-2026-06-13.md(59·108·158)] — stub/mock 검증·수치 단일 정본·rider_crawl 0줄.
- [Source: _bmad-output/project-context.md(46·53·64·75·81·82·89·90·114)] — 기본 브라우저 cdp·pytest 실행·단방향 import·누출 금지·git diff·카카오 interactive 제약·배민 사람 개입·retro 반영.
- [Source: memory/dev-env-quirks, memory/stale-test-count-a2, memory/negative-guard-tests-use-ast, memory/enum-member-count-locks, memory/agent-main-runpy-warning, memory/redact-skips-operational-ids] — venv pytest·`git diff -w`, 수치 단일 정본, AST 부정 가드, enum/lock 전수 점검, `__main__` runpy 경고, redact 운영 식별자 한계.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, BMAD dev-story workflow)

### Debug Log References

- 멱등 회귀 1건(수정 완료): Startup `.cmd` 내용을 리터럴 `\r\n` 으로 만들면 text-mode writer 의
  newline 변환과 read-back round-trip 이 어긋나 재등록 시 재쓰기가 발생했다. `_startup_cmd_text`
  를 `\n` 으로 바꿔(Windows text-mode writer 가 CRLF 로 변환) round-trip 일관성을 회복 →
  멱등(재쓰기 0) 통과.

### Completion Notes List

- **신규 `src/rider_agent/autostart.py`** (순수 동기·import-safe): (a) `build_agent_launch_command`
  (frozen/dev 분기·선택 `--server-url`, **token/code 0**), (b) 노드 역할 resolver
  `resolve_node_role`/`requires_interactive_session`/`handleable_job_types`(4.6 `CAPABILITY_KAKAO_SEND`
  신호 노출만, 새 enum/경로 0), (c) interactive-session probe `is_interactive_session`
  (Windows-gated lazy `ctypes` ProcessIdToSessionId)+게이트 `kakao_session_allowed`(fail-closed,
  `session_probe=None`이면 무게이트=무회귀), (d) autostart 등록/해제/조회 primitive(Startup `.cmd`
  멱등 쓰기 + Task Scheduler `schtasks /sc ONLOGON /it /f`, 모든 OS 부작용 주입 가능).
- **`__main__.py` additive**: 얇은 `autostart` 서브커맨드(`--register`/`--unregister`/`--status`
  +`--method`/`--server-url`) — autostart import 함수 내부 defer, redact 통과 고정 메시지 한 줄
  출력. 인자 없는 실행은 여전히 4.1 배너(무회귀), tkinter/legacy UI import 0.
- **`job_loop.py run_agent` additive**: optional `session_probe` 추가. `start_kakao_sender` 블록
  에서 `start_kakao_sender_worker_if_enabled` 호출 **전에** `autostart.kakao_session_allowed`(lazy
  import)로 판정 — 비대화형이면 워커 미기동(`kakao_worker=None`→`kakao_status`="disabled")·
  `on_status`/`log` surfacing. `session_probe=None`이면 4.6 동작 그대로(무회귀).
- **재사용/무변경 가드 충족**: `heartbeat.py`·`kakao_sender.py`·`secure_store.py`·`registration.py`·
  `browser_profile.py`·`reuse.py`·`rider_crawl/`·`rider_server/`·`pyproject.toml` 0줄 변경.
  노드 역할·세션 사유·등록 메서드는 **평문 상수**(새 enum/"정확히 N" lock 0). `rider_server` import 0,
  async 0 — 4.1 AST 가드(`rglob` 자동 검사)가 신규 `autostart.py` 포함 green.
- **secret/누출 가드**: launch 커맨드·Startup `.cmd`·`schtasks` 인자·서브커맨드 출력·게이트 로그/
  상태에 token/registration code/raw 경로 0(테스트 단언). 실 `schtasks`/세션 API/`%APPDATA%` 미호출
  (주입 fake `writer`/`runner`/`session_probe`/`remover` + `tmp_path`/fake environ).
- **테스트(리뷰 시점 재측정, 단일 정본)**: 전체 스위트 `.venv/Scripts/python.exe -m pytest -q`
  **1249 passed**(신규 `tests/agent/test_autostart.py` **41건** 포함 — dev-story 28건 + qa-e2e 갭 보완 13건, 회귀 0). 4.1 가드·`test_job_loop`·
  `test_kakao_sender`·`test_heartbeat` 전부 green.

### File List

- `src/rider_agent/autostart.py` (신규)
- `tests/agent/test_autostart.py` (신규)
- `src/rider_agent/__main__.py` (수정 — `autostart` 서브커맨드 additive)
- `src/rider_agent/job_loop.py` (수정 — `run_agent` `session_probe` 게이트 additive)
- `_bmad-output/implementation-artifacts/4-7-agent-실행-조건과-재부팅-후-자동-시작.md` (스토리: 체크박스/Dev Agent Record/Status)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (4-7 → review)

## Change Log

| 날짜 | 변경 | 작성자 |
|---|---|---|
| 2026-06-14 | Story 4.7 구현: autostart.py(launch-command·노드 역할 resolver·interactive-session 게이트·autostart 등록 primitive) 신설 + `__main__` autostart 서브커맨드/`run_agent` 세션 게이트 additive 배선. 전체 1249 passed. | Amelia (dev-story) |
| 2026-06-14 | Senior Developer Review (AI, 자동 리뷰): AC1~8 구현·테스트 모두 검증(0 Critical/0 High). MEDIUM 1건 수정 — Dev Agent Record/Change Log의 stale 테스트 수치(1236→1249, 신규 28→41)를 리뷰 시점 재측정값으로 정정. Status → done. | Senior Developer Review (AI) |

## Senior Developer Review (AI)

**Reviewer:** 이수열 · **Date:** 2026-06-14 · **Outcome:** ✅ Approve (auto-fix applied)

### Summary

`autostart.py` primitive + `__main__` autostart 서브커맨드 + `run_agent` 세션 게이트 모두 스토리 계약대로 구현됐다. 전체 스위트 **1249 passed**(회귀 0), 신규 `tests/agent/test_autostart.py` **41건**. 범위 클린(`git diff -w` = `autostart.py`·`test_autostart.py` 신규 + `__main__.py`/`job_loop.py` additive만; `rider_crawl`/`rider_server`/`pyproject.toml`/`heartbeat.py`/`kakao_sender.py`/`secure_store.py`/`registration.py`/`browser_profile.py`/`reuse.py` 0줄).

### AC 검증

| AC | 상태 | 근거 |
|---|---|---|
| AC1.1 주입 가능 세션 probe·Windows-gated·fail-closed | ✅ | `is_interactive_session`/`_default_session_probe`(ctypes lazy), `test_is_interactive_session_uses_injected_probe`·`test_default_session_probe_non_windows_returns_true` |
| AC1.2 `run_agent` additive 게이트·무회귀(probe=None) | ✅ | `job_loop.py:826-839`(lazy import 판정), `test_run_agent_session_gate_blocks_kakao_on_session0`·`test_run_agent_session_probe_none_is_no_regression` |
| AC1.3 sync·평문 사유 상수·secret 0·crawler-only 무관 | ✅ | `test_session_reason_constants_are_plain_strings`·`test_run_agent_session_gate_no_token_leak_in_status`·`test_kakao_session_gate_irrelevant_for_crawler_only` |
| AC2.4 launch-command·등록/해제/조회·멱등 | ✅ | `build_agent_launch_command`/`register_autostart`(read-before-write 멱등)/`unregister`/`is_registered`, startup+task_scheduler 양 경로 테스트 |
| AC2.5 커맨드/cmd/schtasks/로그 token·code 0 | ✅ | `test_launch_command_never_contains_token_or_code`·`test_no_secret_in_startup_and_schtasks_artifacts`·`test_register_log_is_fixed_message_no_path` |
| AC2.6 launch가 `run` 진입 가리킴 | ✅ | 전 command 테스트가 `"run" in cmd` 단언 |
| AC3.7 노드 역할 resolver(재구현 0, capability 노출만) | ✅ | `resolve_node_role`/`requires_interactive_session`/`handleable_job_types`, `test_resolve_node_role_*` |
| AC3.8 DEFAULT_CAPABILITIES=kakao_sender·crawler-only=부분집합 | ✅ | `test_resolve_node_role_kakao_sender_for_default_caps`·`test_resolve_node_role_crawler_only_without_kakao` |

### Findings

- **[MEDIUM·수정완료]** Dev Agent Record/Change Log stale 수치(1236/28건) → 재측정 1249/41건(28 dev + 13 qa-e2e)으로 정정. [memory: stale-test-count-a2]
- **[LOW·노트]** File List가 `_bmad-output/.../tests/test-summary-4.7.md`를 누락 — `_bmad-output/`는 리뷰 제외 대상이라 미수정.
- **[LOW·노트]** `default_startup_dir`는 `APPDATA` 미설정 시 상대경로(`Path("")`)를 반환 — 대화형 Windows 세션(AC1 전제)에선 도달 불가한 엣지라 미수정.
- **[LOW·관찰]** 운영 `run` 서브커맨드(`_run_agent_loop`)는 `start_kakao_sender`/`session_probe`를 아직 전달하지 않음 — 4.6 상태 계승·AC1.2 범위(게이트는 `run_agent` 함수에 배선)와 정합, 서버측 capability 활성화는 Epic 5.
