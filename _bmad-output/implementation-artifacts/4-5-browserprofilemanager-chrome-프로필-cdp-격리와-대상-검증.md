---
baseline_commit: 4c44dcb
---

# Story 4.5: BrowserProfileManager — Chrome 프로필/CDP 격리와 대상 검증

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 작업 노드 관리자,
I want **계정/대상별 Chrome 프로필 디렉터리와 CDP 포트를 격리**하고(대상마다 독립 User Data Dir, 기본 Chrome 프로필 재사용 금지, 사용 가능한 `127.0.0.1:<port>` 할당), **포트/프로필 중복이 감지되면 작업을 시작하지 않으며**(in-Agent 등록부 + 기존 `prepare_chrome` 중복 가드 + `RunLock` 재사용), CDP 미응답 시 재시작·로그인 필요 시 AUTH_REQUIRED 전이로 건강/복구를 다루고, **기대 센터/상점명 검증**을 수행해(쿠팡 기대 센터/상점명이 비었거나 배민 기본값이면 위험 상태로 보고, 기대와 다른 화면=CENTER_MISMATCH면 메시지를 만들거나 보내지 않음) 실패를 운영자가 조치 가능한 `target_validation_failure`로 표시하는 **`BrowserProfileManager`(per-target 프로필/포트 격리 + 대상 검증) + heartbeat `browser_profiles` 소스 배선**을 갖고 싶다,
so that 서로 다른 고객/계정이 같은 Browser Profile을 잘못 공유하거나, 포트/프로필이 꼬여 다른 계정 화면을 수집해 **다른 계정 실적을 오발송**하는 일을 막는다(P3-05, FR-14·20, NFR-2·4·9·15, ADD-15).

> **이 스토리의 성격 — "per-target 프로필/CDP 격리 manager + 대상(센터/상점) 검증 매핑 + heartbeat `browser_profiles` 소스 배선"만.** CDP 중복 검사·프로필 점유 검사·로컬 주소 강제·CDP 준비 대기·쿠팡 센터 exact-match 검증은 **이미 `rider_crawl`에 구현되어 있고 테스트로 잠겨 있다** — 본 스토리는 그것들을 **재구현하지 않고 import/주입으로 재사용**하며, "대상별 격리 오케스트레이션 + 검증 결과를 운영 카테고리(`target_validation_failure`)·상태(`CENTER_MISMATCH`)로 매핑 + heartbeat 노출"만 얹는다. **실제 crawl 수집·Snapshot 업로드(`CRAWL_BAEMIN`/`CRAWL_COUPANG` `execute_job` 오케스트레이션)는 본 스토리 범위가 아니다**(아래 "범위 경계"의 열린 질문 참조 — 4.5 ACs는 수집/업로드를 한 줄도 요구하지 않는다). [Source: implementation-contract.md P3-05(62), architecture-contract.md BrowserProfileManager(109-118)·Job-Types(120-129), epics.md Story 4.5(787-808)]
>
> **서버가 아직 없다 — "서버 stub/mock에 대한 동작 검증"이 4.x 테스트 형태(절대 전제, 4.1~4.4 계승).** heartbeat의 `browser_profiles` 필드를 **수신·`browser_profiles` 테이블에 저장**하는 서버 측은 Epic 5 소유다. 본 스토리는 실제 Chrome/네트워크 없이 **주입된 fake `run_command`/`cdp_probe`/socket/clock**에 대해 프로필/포트 할당·중복 거부·건강/복구·센터 검증 매핑·heartbeat provider shape를 검증한다. epic-3-retro(108): "Epic 4는 서버 측 job 생성·queue·Admin이 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." [Source: epic-3-retro-2026-06-13.md(108), data-api-contract.md(29·67-69)]
>
> **재사용 = 재구현 금지(이 스토리의 핵심 가드 #1).** `rider_crawl.browser_launcher.prepare_chrome`는 **이미** (a) `ensure_local_cdp_address`(원격 CDP 차단), (b) `_ensure_cdp_endpoint_unused`(CDP 포트 사용 중이면 차단), (c) `_ensure_chrome_profile_free`(같은 프로필 Chrome 점유 시 차단), (d) `_wait_for_cdp_ready`(준비 대기)를 수행한다. 쿠팡 센터 검증은 **이미** `platforms/coupang/crawler.py._validate_coupang_center`/`_validate_coupang_center_in_peak_html`가 exact-match로 수행하며 불일치 시 `RuntimeError`로 **수집을 중단(=메시지 미생성)**한다. 위험 분류는 **이미** `config.coupang_center_name_risk(platform_name, center_name) -> (is_risky, reason)`가 노출한다(`coupang_center_name_risk` docstring: "실제 작업 차단·상태 전이는 **Epic 4(FR-14/FR-20) 소유**" — 즉 본 스토리). **이 함수들을 다시 짜지 말고 import/주입으로 재사용**하라. [Source: src/rider_crawl/browser_launcher.py(40-65·82-92·126-139·160-180·261-294), src/rider_crawl/platforms/coupang/crawler.py(50-110), src/rider_crawl/config.py(300-324)]
>
> **`browser_profiles` 실제 소스를 4.5가 채운다(4.3이 비워둔 곳, 4.4의 `active_jobs`와 동형).** 4.3 `heartbeat.py`는 `browser_profiles_provider`(기본 `[]`)를 받도록 설계됐고 docstring(line 23)이 "실제 소스 배선은 후속 — `active_jobs`(4.4)·`browser_profiles`(4.5)"로 명시 위임했다. 4.5는 `BrowserProfileManager.browser_profiles`(callable)를 `build_agent_components`/`run_agent`에서 `HeartbeatReporter(browser_profiles_provider=manager.browser_profiles)`로 배선한다 — **`heartbeat.py`는 0줄 변경**(이미 인자가 있음, 주입만), **`job_loop.py`만 최소 additive 편집**(인자 thread-through). [Source: src/rider_agent/heartbeat.py(23·121·143-156·199·215·267·281·322), src/rider_agent/job_loop.py(678-730·744-803)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리 산출물: 신규 `src/rider_agent/browser_profile.py` + 신규 `tests/agent/test_browser_profile.py` + `src/rider_agent/job_loop.py`의 **`browser_profiles_provider` thread-through(additive)** + (권장) `src/rider_agent/reuse.py`에 browser_launcher/config/lock/센터검증 **re-export 추가**. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(절대 규칙·무회귀 안전 마진).** 필요한 모든 빌딩블록(`prepare_chrome`·`ensure_local_cdp_address`·예외 3종·`RunLock`·`coupang_center_name_risk`·쿠팡 센터 검증)은 **이미 public 또는 호출 가능**하다 → import/주입만. private helper(`_ensure_cdp_endpoint_unused`·`_profile_dir_key`·`_run_scope_key`)를 직접 import하지 말고 **public 경계(`prepare_chrome`·`RunLock`)를 통해** 재사용한다(약화·우회 금지). epic-3-retro(158): "**`rider_crawl` 0줄**이 안전 마진." [Source: project-context.md(64·82), epic-3-retro-2026-06-13.md(158)]
> - **`pyproject.toml` `[project].dependencies` — 0줄 변경.** 포트 할당은 **stdlib `socket`**(bind→0→읽기). 새 third-party 의존을 추가하면 4.1 가드 `test_pyproject_dependencies_unchanged_pins`(deps **정확히 9개**)와 `test_rider_agent_only_third_party_root_is_rider_crawl`(third-party root == `{rider_crawl}`)가 **둘 다** 깨진다(memory: enum-member-count-locks 와 동형). [Source: tests/agent/test_agent_package.py(194-213)]
> - **`registration.py`·`secure_store.py`·`heartbeat.py` — 0줄 변경(reuse only).** `heartbeat.py`는 이미 `browser_profiles_provider`를 받으므로 시그니처 변경 불필요 — 주입만. [Source: src/rider_agent/heartbeat.py(121·199·267·281·322)]
> - **CRAWL_* `execute_job` 오케스트레이션(수집→Snapshot 업로드)** → Epic 4↔5 통합/후속. `default_execute_job`는 그대로 `UNSUPPORTED_JOB_TYPE`를 돌린다 — 본 스토리는 `run_agent`에 CRAWL executor를 **배선하지 않는다**(BrowserProfileManager는 후속 crawl 워커가 주입할 **primitive**로만 제공). **빈 stub 워커 파일(`workers/`·`auth/`)을 만들지 않는다**(4.3·4.4 provider seam 규율). [Source: epics.md Story 4.5 ACs(793-808 — 수집/업로드 요구 0), architecture.md(452-457), 4-4 스토리(71·119)]
> - **서버 측 heartbeat 수신·`browser_profiles` 테이블 저장·Admin `profile_mismatch` runbook 표시·job 생성/queue** → **Epic 5.** 본 스토리는 client provider + 주입 fake. [Source: data-api-contract.md(29·67-69), architecture.md(437·440), epics.md Epic 5(904-)]
> - **autostart(4.7)·배민 auth(4.8)·쿠팡 Gmail 2FA(4.9)·KakaoSenderWorker(4.6)** → 각 후속 스토리. 단, AC3은 "대상 추가/자동 실행 시 포트/프로필 중복 검증을 약화하지 않는다"를 **본 스토리에서 보장**하라고 요구하므로, manager의 중복 거부는 후속 기능이 우회할 수 없는 형태로 설계한다(아래 AC3). [Source: epics.md AC(806-808), project-context.md(78·93)]
>
> **secret/원시경로 비노출(ADD-15·NFR-9 — 본 스토리의 핵심 가드 #2).** 서버로 보내는 heartbeat `browser_profiles`에는 **raw 프로필 디스크 경로를 넣지 않는다** — `id`/`target_id`/`agent_id`/`cdp_port`/`state`만(data-api-contract: "server stores **profile id/ref, not raw sensitive path** as primary identity"). 로그/예외/heartbeat 어디에도 raw 경로·쿠팡 로그인·token이 평문으로 남지 않게 `redact` 통과. 테스트 fixture는 가짜 경로/포트/센터명만(실제 토큰·실 경로·한국 휴대폰·이메일·OTP 금지). [Source: operations-security-test-contract.md(11·16·87-95), data-api-contract.md(29), project-context.md(81)]
>
> **sync 런타임 + 단방향 import(4.1 규약 계승 — 자동 검증됨).** 신규 `browser_profile.py`는 **순수 동기**(no `async def`/`await`/직접 `import asyncio`)이고 `rider_crawl`/자기 패키지만 import한다(역방향 0, `rider_server` import 0). 포트 할당 대기·재시작 backoff는 주입 가능한 `sleep`/`now`로 짠다. 4.1이 `src/rider_agent/*.py` **전체를 glob**하는 AST 가드로 검사하므로 신규 모듈도 자동 적용된다. [Source: tests/agent/test_agent_package.py(176-233), memory/negative-guard-tests-use-ast]

## Acceptance Criteria

**AC1 — per-target Chrome 프로필/CDP 격리 + 중복 시 작업 미시작 (P3-05, FR-14, ADD-15)**

1. **Given** 여러 대상이 각자 Chrome 프로필/CDP 포트를 써야 할 때 **When** `BrowserProfileManager`가 한 대상(`tenant_id`+`target_id`)의 프로필을 확보하면 **Then** 대상마다 **독립 User Data Directory**(`profiles/<tenant_id>/<target_id>/` 정책)와 **사용 가능한 `127.0.0.1:<port>` CDP 포트**를 할당하고, **기본 Chrome 프로필을 재사용하지 않으며**(`--user-data-dir`가 대상별 경로), Chrome 실행은 **`rider_crawl.browser_launcher.prepare_chrome`를 재사용**(주입 `run_command`/`cdp_probe`)해 띄운다. [Source: architecture-contract.md(78·113-116), src/rider_crawl/browser_launcher.py(40-65), epics.md AC(795-797)]
2. **And** 서로 다른 고객/계정이 **같은 Browser Profile을 잘못 공유하지 않는다**: manager는 (tenant_id, target_id)별로 (프로필 경로, cdp_port)를 in-Agent 등록부에 유지하고, **이미 할당된 포트/프로필을 다른 대상에 재배정하지 않는다**. [Source: epics.md AC(797·806-808), project-context.md(78·93)]
3. **And** **CDP 포트나 프로필 중복이 감지되면 작업을 시작하지 않는다(fail-closed)**: (a) `prepare_chrome`가 던지는 `BrowserLaunchError`(CDP 포트 사용 중/프로필 점유 — `_ensure_cdp_endpoint_unused`/`_ensure_chrome_profile_free`), (b) manager 등록부의 중복, (c) 원격 CDP 주소(`ensure_local_cdp_address` 위반) **중 하나라도** 감지되면 그 대상은 시작하지 않고 오류 상태로 surfacing한다. 검사를 우회·약화하는 경로를 만들지 않는다. [Source: src/rider_crawl/browser_launcher.py(261-294·82-92·126-139), epics.md AC(798)]

**AC2 — 기대 센터/상점명 대상 검증 + CENTER_MISMATCH면 미생성·미발송 + 조치 가능 오류 (FR-20, NFR-2·15)**

4. **Given** 수집한 화면이 기대 대상과 일치해야 할 때 **When** 대상 검증을 수행하면 **Then** 쿠팡 **기대 센터/상점명이 비어 있거나 배민 기본값이면**(=`config.coupang_center_name_risk(platform_name, center_name)`가 `(True, reason)`) 그 대상을 **위험 상태로 보고**하고 작업을 진행하지 않는다(fail-closed). **이 분류기를 재구현하지 말고 재사용**한다. [Source: src/rider_crawl/config.py(300-324·282-297), epics.md AC(800-802)]
5. **And** 기대 대상과 **다른 화면(CENTER_MISMATCH)이면 메시지를 만들거나 보내지 않는다**: 쿠팡 센터 exact-match 검증(`platforms/coupang/crawler.py._validate_coupang_center`/`_validate_coupang_center_in_peak_html`)은 불일치 시 `RuntimeError`로 **수집을 중단**해 이미 fail-closed다 — manager/검증 매핑 계층은 이 검증을 **우회하지 않고**(검증 없는 별도 수집 경로를 만들지 않음), 불일치를 흡수해 **상태 `CENTER_MISMATCH`**(`BaeminAuthState.CENTER_MISMATCH` 어휘)로 매핑한다. [Source: src/rider_crawl/platforms/coupang/crawler.py(50-110), src/rider_server/domain/states.py(58), epics.md AC(803)]
6. **And** 검증 실패(위험/CENTER_MISMATCH/센터 미확인)는 운영자가 조치할 수 있는 **오류 카테고리 `target_validation_failure`**(`FailureCategory.TARGET_VALIDATION_FAILURE` 어휘 = 문자열 `"TARGET_VALIDATION_FAILURE"`)로 표시되며 사유는 `redact` 통과한다(raw 경로/로그인 비노출). enum/"정확히 N개" lock으로 잠그지 않고 **문자열 상수**로 둔다. [Source: src/rider_server/domain/states.py(165-186·179-186), epics.md AC(804), operations-security-test-contract.md(16·87-95), memory/enum-member-count-locks]

**AC3 — CDP/프로필은 계정 격리 장치 — 중복 검증 약화 금지 + 건강/복구 (FR-14, NFR-4, project-context)**

7. **Given** CDP 포트와 프로필이 계정 격리 장치일 때 **When** 대상을 추가하거나(다수 대상) 자동 실행 흐름에서 manager를 쓰면 **Then** **포트/프로필 중복 검증이 약화되지 않는다** — 새 대상 추가가 기존 대상의 포트/프로필을 재사용하거나 중복 가드를 건너뛰는 경로를 만들지 않는다(AC1.2·AC1.3 보장이 대상 수와 무관하게 유지). [Source: project-context.md(78·93), epics.md AC(806-808)]
8. **And** **건강 점검/복구**: CDP 엔드포인트 응답(주입 `cdp_probe`)·상태(`READY`/`IN_USE`/`INACTIVE`/`UNKNOWN` 어휘)를 보고하고, **CDP 미응답 시 재시작**(`CdpUnavailableError` 흡수 후 재준비)하되 **로그인 필요 시 AUTH_REQUIRED로 전이**하고 **무한 재시도하지 않는다**(`BrowserActionRequiredError` 어휘 + backoff, NFR-4). 재시작/대기는 주입 `sleep`/`now`로 결정적 테스트 가능. [Source: architecture-contract.md(116·118), src/rider_crawl/browser_launcher.py(19-33), epics.md Epic 4(696)]

**AC4 — heartbeat `browser_profiles` 소스 배선 + raw 경로 비노출 (P3-03 계승, NFR-9, ADD-15)**

9. **Given** 운영 화면(Epic 5)이 Agent의 프로필 상태를 알아야 할 때 **When** `BrowserProfileManager.browser_profiles`(callable)를 `HeartbeatReporter(browser_profiles_provider=...)`에 배선하면(`build_agent_components`/`run_agent` thread-through, 4.4 `active_jobs` 배선과 동형) **Then** heartbeat payload의 `browser_profiles`가 manager의 현재 프로필을 반영하고(각 항목 `id`/`target_id`/`agent_id`/`cdp_port`/`state` — `browser_profiles` 테이블 필드 정합), **And** **raw 프로필 디스크 경로·secret이 payload·로그에 평문으로 포함되지 않는다**(server stores id/ref, not raw path). `heartbeat.py`는 0줄 변경(주입만). [Source: data-api-contract.md(29·67-69), src/rider_agent/heartbeat.py(121·143-156·322), src/rider_agent/job_loop.py(718-730·788-803), operations-security-test-contract.md(11·16)]

## Tasks / Subtasks

- [x] **Task 1 — `browser_profile.py`: 도메인 dataclass + 포트 할당 + 프로필 경로 정책 (AC: 1, 4)**
  - [x] `src/rider_agent/browser_profile.py` 신설. frozen dataclass `ProfileAssignment`(`id`·`tenant_id`·`target_id`·`agent_id`·`profile_dir: Path`·`cdp_port: int`·`cdp_url: str`·`state: str`). **server로 내보내는 표면엔 raw `profile_dir`를 넣지 않는다**(내부 보관용; heartbeat projection은 Task 4에서 id/ref만). [Source: data-api-contract.md(29), src/rider_server/domain/browser_profile.py]
  - [x] **프로필 경로 정책:** `profiles/<tenant_id>/<target_id>/` (architecture-contract C:\RiderBot\agent\profiles\ 트리). 베이스 루트는 주입 가능(테스트 `tmp_path`). 기본 Chrome 프로필 경로를 **절대 재사용하지 않는다**. [Source: architecture-contract.md(78), epics.md AC(795-797)]
  - [x] **포트 할당:** stdlib `socket`로 사용 가능한 `127.0.0.1` 포트를 얻는 헬퍼(`_allocate_local_port()` — bind(`("127.0.0.1", 0)`)→getsockname→close). 새 의존 0. cdp_url = `f"http://127.0.0.1:{port}"`. 할당 후 `ensure_local_cdp_address(cdp_url)`로 재확인(원격 차단 — 재사용). [Source: src/rider_crawl/browser_launcher.py(284-290·277-281)]
  - [x] 상태 문자열 상수: `STATE_UNKNOWN/READY/IN_USE/INACTIVE`(평문, `BrowserProfileState` 어휘 정합) + `ERROR_TARGET_VALIDATION_FAILURE = "TARGET_VALIDATION_FAILURE"`. **enum/"정확히 N개" lock 금지**(`secure_store.TOKEN_STATUS_*`·`heartbeat.DEFAULT_CAPABILITIES` 선례). [Source: src/rider_server/domain/states.py(114-120·179-186), memory/enum-member-count-locks]
  - [x] **순수 동기 + `rider_crawl`/자기 패키지만 import** — 4.1 AST 가드 자동 검사. [Source: tests/agent/test_agent_package.py(176-233)]
- [x] **Task 2 — `BrowserProfileManager`: per-target 프로필/CDP 격리 + 중복 거부 (AC: 1, 3)**
  - [x] `BrowserProfileManager(*, profiles_root, agent_id, prepare=prepare_chrome, sleep=time.sleep, now=time.time, run_command=None, cdp_probe=None, log=None)` — 모든 외부 부작용(Chrome 실행·CDP probe·시간)을 주입 가능하게. [Source: architecture-contract.md(109-118), 4-4 스토리(주입 규율)]
  - [x] `ensure_profile(tenant_id, target_id, *, build_config) -> ProfileAssignment`: (a) in-Agent 등록부(`dict[(tenant_id,target_id)] -> ProfileAssignment` + 할당된 포트/프로필-키 set)에서 기존 할당 재사용 또는 신규 할당, (b) **포트/프로필 중복 거부**: 다른 (tenant,target)이 같은 포트나 같은 프로필-키를 쓰면 `BrowserLaunchError`로 거부(작업 미시작), (c) `prepare_chrome(config, run_command=…, cdp_probe=…)` 호출로 실제 격리 가드(CDP-unused/profile-free/local-addr/대기) **재사용** — 여기서 던지는 `BrowserLaunchError`도 흡수해 "시작 안 함"으로 surfacing. `build_config`는 호출자가 대상 설정(cdp_url·user_data_dir·platform_name·center_name)을 담은 `AppConfig` 호환 객체를 만들어 주입(또는 manager가 최소 필드로 구성). [Source: src/rider_crawl/browser_launcher.py(40-65·261-294), epics.md AC(797-798·806-808)]
  - [x] **프로필-키 정규화:** 중복 비교는 `prepare_chrome`가 쓰는 것과 **동일 정책**(case-fold+resolve)으로 한다 — `browser_launcher`의 정규화 경계를 우회하지 않게, public `prepare_chrome`에 위임하고 manager 등록부 키는 `str(profile_dir.expanduser().resolve()).casefold()`로 일관. (private `_profile_dir_key` 직접 import 금지 — 동일 규칙을 자체 적용.) [Source: src/rider_crawl/browser_launcher.py(253-258), project-context.md(78·93)]
  - [x] **`RunLock` 재사용(교차 프로세스 이중 오픈 방지):** `rider_crawl.lock.RunLock`을 (선택) 사용해 같은 scope(cdp 모드=cdp_url) double-open을 막되, **app의 lock scope 정책을 약화하지 않는다**(cdp 모드→cdp_url 기준). 본 스토리에서 새 lock 의미를 발명하지 말고 기존 클래스/scope를 그대로 쓴다. [Source: src/rider_crawl/lock.py, src/rider_crawl/app.py(73-95), project-context.md(46)]
  - [x] **release/teardown:** `release(tenant_id, target_id)`로 등록부에서 제거(포트/프로필 키 회수) — 누수 없이 재할당 가능하게(thread-safe: heartbeat thread가 `browser_profiles()`를 동시 읽음 → `threading.Lock` 보호). [Source: 4-4 스토리(in-flight 등록부 thread-safe 선례)]
- [x] **Task 3 — 대상(센터/상점) 검증 매핑 — 재사용 위에 카테고리/상태 매핑 (AC: 2)**
  - [x] `classify_target_risk(platform_name, center_name) -> (is_risky, reason)`: **`config.coupang_center_name_risk`를 그대로 호출**(재구현 금지). 위험이면 manager가 그 대상을 진행하지 않고 `target_validation_failure`로 surfacing. [Source: src/rider_crawl/config.py(300-324)]
  - [x] **CENTER_MISMATCH 매핑:** 수집 경로가 쿠팡 센터 검증 `RuntimeError`(이미 fail-closed)를 던지면 이를 흡수해 `state=STATE-비정상`+`error_code="TARGET_VALIDATION_FAILURE"`+`mismatch=CENTER_MISMATCH` 어휘로 매핑하는 헬퍼(`map_target_validation_failure(exc) -> dict`)를 둔다. **검증 자체를 재구현하거나 우회하지 않는다** — 검증 없는 별도 수집 경로 신설 금지. 사유 문자열은 `redact` 통과(raw 화면 센터명/경로 마스킹 정책 준수). [Source: src/rider_crawl/platforms/coupang/crawler.py(50-110), src/rider_server/domain/states.py(58·186), operations-security-test-contract.md(16·87-95)]
  - [x] **배민 센터 검증은 reuse(재구현 0):** 배민 센터 identity 검증은 `rider_crawl`(ui/crawler) 소유다 — 본 스토리는 배민 검증 로직을 새로 짜지 않고, 쿠팡과 동일하게 실패를 `target_validation_failure`로 매핑하는 경계만 제공. [Source: src/rider_crawl/config.py(307-308 — 배민 센터 규칙은 별도 소유), src/rider_crawl/crawler.py(배민 center 검증)]
- [x] **Task 4 — heartbeat `browser_profiles` provider + `job_loop` 배선 (AC: 4)**
  - [x] `BrowserProfileManager.browser_profiles() -> list[dict]`: 현재 등록부를 **id/ref만** 투영(`{"id":…, "target_id":…, "agent_id":…, "cdp_port":…, "state":…}`). **raw `profile_dir`를 넣지 않는다**(server stores profile id/ref). thread-safe 읽기. [Source: data-api-contract.md(29·67-69), operations-security-test-contract.md(11·16)]
  - [x] `src/rider_agent/job_loop.py` **additive 편집**: `build_agent_components(...)`와 `run_agent(...)`에 `browser_profiles_provider: Any = None` 인자 추가 → `HeartbeatReporter(..., browser_profiles_provider=browser_profiles_provider)`로 전달(현재 `active_jobs_provider=runner.active_jobs` 옆). 기존 호출자(인자 미전달)는 `None`→`[]`로 무회귀. **`heartbeat.py`는 0줄 변경**(이미 인자 보유). [Source: src/rider_agent/job_loop.py(678-730·744-803), src/rider_agent/heartbeat.py(199·215·267·281·322)]
  - [x] **`run_agent`에 CRAWL executor를 배선하지 않는다** — `execute_job`는 기본값(`default_execute_job`) 유지. manager는 호출자가 주입할 primitive로만 노출(빈 stub 워커 파일 금지). [Source: 4-4 스토리(71·119), architecture.md(452-457)]
- [x] **Task 5 — (권장) `reuse.py` 확장: browser_launcher/config/lock/센터검증 re-export (AC: 1, 2)**
  - [x] `src/rider_agent/reuse.py`의 단일 chokepoint에 **재사용 심볼 추가**(docstring이 명시적으로 "crawl_worker 4.5"를 이 seam의 소비자로 지목): `prepare_chrome`, `ensure_local_cdp_address`, `BrowserLaunchError`, `CdpUnavailableError`, `BrowserActionRequiredError`(browser_launcher), `RunLock`(lock), `coupang_center_name_risk`(config). `__all__`에 추가. [Source: src/rider_agent/reuse.py(docstring 1-5), src/rider_crawl/browser_launcher.py, lock.py, config.py(300)]
  - [x] **count-lock 없음 확인:** `test_reuse_seam_reexports_same_objects`는 **나열된** 심볼만 `is`로 검사하고, `test_reuse_all_names_are_resolvable`는 `__all__` 전체가 해석되는지만 본다 — **"정확히 N개 export" lock은 없다**. 따라서 추가는 안전하나, 추가한 이름은 반드시 실제 import되어 attribute로 해석돼야 한다(drift 금지). (선택) 추가 심볼에 `is` identity 단언을 test에 더하면 재사용 보증이 강해진다. [Source: tests/agent/test_agent_package.py(139-169·304-310), memory/enum-member-count-locks]
  - [x] **import-safety 유지:** 추가 re-export가 무거운/GUI 의존을 eager-load하면 `test_reuse_seam_is_import_safe_no_heavy_deps`가 깨진다. browser_launcher/lock/config는 top-level에서 crawl4ai/playwright/pyautogui/google를 끌지 않는지 확인(끌면 함수-내부-import인 모듈만 노출하거나 re-export를 보류). [Source: tests/agent/test_agent_package.py(265-275)]
  - [x] `browser_profile.py`는 이 seam(또는 직접 `rider_crawl.*`)으로 import한다. 어느 쪽이든 third-party root는 `rider_crawl`뿐 → 4.1 가드 green. [Source: tests/agent/test_agent_package.py(194-203)]
- [x] **Task 6 — 테스트: `tests/agent/test_browser_profile.py` (AC: 1~9)** — 외부 호출 없음(fake `run_command`/`cdp_probe`/socket/주입 sleep·now), 가짜 값만:
  - [x] **위치/네이밍:** `tests/agent/test_browser_profile.py`(평면, `__init__.py` 미추가 — 4.1~4.4 미러). 신규 basename. [Source: architecture.md(461), 4-4 스토리(164)]
  - [x] **(AC1 — 격리/할당):** `ensure_profile`이 대상별 독립 `profile_dir`(tenant/target 분리)와 고유 `127.0.0.1:<port>`를 할당하고 기본 프로필을 재사용하지 않음; `prepare_chrome`가 주입 `run_command`/`cdp_probe`로 호출됨(실 Chrome 0)을 단언. [Source: src/rider_crawl/browser_launcher.py(40-65)]
  - [x] **(AC1.2/1.3·AC3 — 중복 거부·약화 금지):** 같은 포트/프로필을 둘째 대상에 배정하려 하면 `BrowserLaunchError`로 거부·작업 미시작; `prepare_chrome`가 `BrowserLaunchError`(CDP 사용 중/프로필 점유, fake probe가 성공 응답)를 던지면 흡수해 시작 안 함; 원격 cdp_url은 `ensure_local_cdp_address` 위반으로 거부; 대상 N개 추가 후에도 중복 가드가 유지됨을 단언. [Source: src/rider_crawl/browser_launcher.py(261-294), epics.md AC(798·806-808)]
  - [x] **(AC2 — 센터 검증 매핑):** `classify_target_risk("coupang","")`/배민 기본값 → `(True, …)`·진행 안 함·`target_validation_failure` surfacing(`coupang_center_name_risk` 재사용 확인); 쿠팡 센터 불일치 `RuntimeError`를 흡수해 `error_code="TARGET_VALIDATION_FAILURE"`+`CENTER_MISMATCH` 매핑·메시지 미생성; 사유에 raw 화면 센터명/경로가 redact됨을 단언. [Source: src/rider_crawl/config.py(300-324), src/rider_crawl/platforms/coupang/crawler.py(50-110), src/rider_server/domain/states.py(58·186)]
  - [x] **(AC3 — 건강/복구):** CDP 미응답(fake probe 예외) → 재시작 시도(재 `prepare_chrome`)·주입 sleep backoff; 로그인 필요(`BrowserActionRequiredError`) → AUTH_REQUIRED 어휘로 전이·**무한 재시도 안 함**(호출 횟수 상한/주입 now로 결정적). [Source: src/rider_crawl/browser_launcher.py(19-33), architecture-contract.md(118)]
  - [x] **(AC4 — heartbeat provider·경로 비노출):** `manager.browser_profiles()`가 `id/target_id/agent_id/cdp_port/state`만 돌려주고 **raw `profile_dir`/secret 미포함**; `build_agent_components`(또는 `run_agent`)가 `HeartbeatReporter(browser_profiles_provider=manager.browser_profiles)`로 배선해 heartbeat payload `browser_profiles`에 반영됨을 단언(4.3 reporter 재사용·주입 transport·실 네트워크 0). [Source: src/rider_agent/heartbeat.py(143-156·322), src/rider_agent/job_loop.py(718-730), data-api-contract.md(29·67-69)]
  - [x] **(누출 가드):** 모든 fixture는 가짜 값만 — 실 프로필 경로/실제 봇 토큰/agent token/`chat_id`/한국 휴대폰/이메일·OTP 원문 금지. 실제 Chrome/CDP/네트워크 미호출. 로그 캡처·payload·예외에 raw 경로/secret 0건 단언. [Source: project-context.md(55·81), operations-security-test-contract.md(16·87-95), 4-4 스토리(96)]
- [x] **Task 7 — 회귀·범위·누출 검증 및 마무리 (AC: 1~9)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/agent/test_agent_package.py`·`test_job_loop.py`·`test_heartbeat.py`·`tests/test_browser_launcher.py`·`test_lock.py`·`test_coupang_crawler.py`·`test_config.py`), 신규 케이스만큼만 증가가 정상. [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] **4.1 가드 green 재확인(핵심):** `pytest tests/agent/test_agent_package.py -q`의 (a) third-party root == `{rider_crawl}`, (b) sync(자기 모듈 async 0·`import asyncio` 0), (c) 단방향 import(`rider_server` 0), (d) pyproject deps **정확히 9개·핀 불변**, (e) `reuse.__all__` 전부 resolvable·동일 객체가 **신규 `browser_profile.py` 추가 + `reuse.py` 확장 + `job_loop.py` additive 편집 후에도 통과**. stdlib(`socket`/`threading`/`time`/`pathlib`)+`rider_crawl`만 썼다면 green. [Source: tests/agent/test_agent_package.py(139-233·304-310), 4-4 스토리(100)]
  - [x] **무회귀 확인:** `git diff -w --stat`에 **신규 `src/rider_agent/browser_profile.py` + 신규 `tests/agent/test_browser_profile.py` + `src/rider_agent/job_loop.py`(browser_profiles_provider thread-through만) + `src/rider_agent/reuse.py`(re-export 추가만) + sprint-status**만 보이고 **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py` 변경 0줄**임을 확인(CRLF/LF 노이즈 무시 — `git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 raw 프로필 경로/평문 token 0건, `src/rider_crawl/`에 `rider_agent` import 신규 0건, `browser_profile.py`에 `rider_server` import 0건. [Source: project-context.md(64·81), 4-4 스토리(103)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(dev 노트에 잠정 수치 박지 말 것 — 4.1~4.4에서 stale 수치 MEDIUM 재발). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리 산출물: 신규 `src/rider_agent/browser_profile.py`(BrowserProfileManager + 대상 검증 매핑) + 신규 `tests/agent/test_browser_profile.py` + `src/rider_agent/job_loop.py`의 **`browser_profiles_provider` thread-through(additive)** + (권장) `src/rider_agent/reuse.py` **re-export 확장**. **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`는 무변경(reuse only).**
- **건드리지 않는다:** `rider_crawl` 전부(재사용만), CRAWL_* `execute_job` 수집/Snapshot-업로드 오케스트레이션(BrowserProfileManager는 primitive로만 제공 — 후속 워커가 주입), KakaoSenderWorker(4.6), autostart(4.7), 배민·쿠팡 auth(4.8·4.9), 서버 측 heartbeat 수신/`browser_profiles` 저장/Admin runbook/job 생성/queue(Epic 5). **빈 stub 파일(`workers/`·`auth/`)도 만들지 않는다.** [Source: epics.md Story 4.6~4.9(810-903), architecture.md(437·440·452-457)]

### 열린 질문 / 의도된 부분 구현 (반드시 읽을 것)

- **CRAWL_* 워커 소유권(의도된 deferral).** 4.4 dev 노트는 "CRAWL_BAEMIN/CRAWL_COUPANG=4.5(BrowserProfileManager)·crawl 워커"라고 forward-pointer를 남겼다. 그러나 **Story 4.5의 ACs(793-808)는 수집/Snapshot 업로드를 한 줄도 요구하지 않는다** — 전부 (a) 프로필/CDP 격리·중복 거부, (b) 센터/상점 대상 검증, (c) 약화 금지·건강/복구다. 또한 실제 Snapshot **업로드 API는 Epic 5 소유**(서버 미구현)라 full CRAWL 오케스트레이션은 지금 end-to-end가 안 된다. **결정: 4.5는 BrowserProfileManager + 대상 검증 + heartbeat 배선까지의 primitive만 제공하고, CRAWL_* `execute_job` 오케스트레이션은 배선하지 않는다**(Epic 4↔5 통합 또는 별도 후속에서 manager를 주입해 조립). 이는 4.3/4.4의 "primitive + seam, 워커 deferred" 판정과 정합한 **계획된 부분 구현**이다. 만약 dev/리뷰 과정에서 "4.5가 thin CRAWL executor까지 포함"이 더 맞다고 판단되면, 그 executor는 manager를 주입받아 `prepare_chrome`+센터검증+`run_once` 경계를 reuse하는 **얇은** 어댑터여야 하고 새 수집/렌더 로직을 재구현해선 안 된다. [Source: 4-4 스토리(17·71·111·119), epics.md Story 4.5(793-808), architecture.md(452-457·524-526)]

### 설계 결정 — 무엇을 재사용하고 무엇이 신규인가 (반드시 읽을 것)

- **CDP/프로필 격리 가드는 이미 존재 — `prepare_chrome` 한 곳에 응집(재구현 금지).** `prepare_chrome`(→ `prepare_windows_chrome`/`prepare_mac_chrome`)가 `ensure_local_cdp_address`+`_ensure_cdp_endpoint_unused`+`_ensure_chrome_profile_free`+`_wait_for_cdp_ready`를 **이미** 수행하고 `tests/test_browser_launcher.py`가 잠근다. BrowserProfileManager는 이 public 진입을 **대상별로 오케스트레이션**(포트 할당→config 구성→`prepare_chrome` 호출→등록부 갱신)할 뿐, 포트/프로필 검사를 **다시 짜지 않는다**. private helper(`_ensure_*`·`_profile_dir_key`) 직접 import 금지 — public 경계로 위임. [Source: src/rider_crawl/browser_launcher.py(40-139·261-294)]
- **센터/상점 검증도 이미 존재 — 4.5는 "차단/상태 매핑"만 추가.** `coupang_center_name_risk`(config.py:300) docstring이 직접 말한다: "실제 작업 차단·상태 전이는 **Epic 4(FR-14/FR-20) 소유**". 즉 위험 분류기·exact-match 검증(`platforms/coupang/crawler.py`)은 이미 있고, **본 스토리가 그 결과를 `target_validation_failure`(FailureCategory) + `CENTER_MISMATCH`(BaeminAuthState) 어휘로 매핑하고 fail-closed를 보장**한다. 쿠팡 검증은 불일치 시 `RuntimeError`로 수집을 중단 → 메시지 미생성이 이미 보장됨(AC2.5). [Source: src/rider_crawl/config.py(300-324), src/rider_crawl/platforms/coupang/crawler.py(50-110), src/rider_server/domain/states.py(58·165-186)]
- **`browser_profiles` 실제 소스를 4.5가 채운다(4.4 `active_jobs`와 동형).** `heartbeat.py`(line 23)가 "실제 소스 배선은 후속 — `browser_profiles`(4.5)"로 위임했고 인자(`browser_profiles_provider`)는 이미 build_payload→send_heartbeat→HeartbeatReporter 전 구간에 배선돼 있다(143-156·215·322). 4.5는 `job_loop.build_agent_components`/`run_agent`에 인자를 thread-through해 `manager.browser_profiles`를 주입한다 — `heartbeat.py` 0줄 변경. [Source: src/rider_agent/heartbeat.py(23·121·143-156·199·215·267·281·322), src/rider_agent/job_loop.py(678-730·744-803)]
- **포트 할당은 stdlib `socket`(새 의존 0).** bind→0→getsockname→close 패턴. 경합(TOCTOU)은 `prepare_chrome`의 `_ensure_cdp_endpoint_unused`가 후속 방어 — 할당 직후 사용 중이면 `BrowserLaunchError`로 시작 안 함. [Source: src/rider_crawl/browser_launcher.py(261-269)]
- **상태/오류는 평문 문자열 상수(enum·"정확히 N" lock 금지).** `BrowserProfileState`/`FailureCategory` **값**(문자열)과 정합하되, agent 측은 `secure_store.TOKEN_STATUS_*`·`heartbeat.DEFAULT_CAPABILITIES` 선례대로 평문 상수로 둔다 — 후속 상태 추가가 다른 테스트를 깨지 않게. 테스트는 superset/포함 단언. [Source: src/rider_server/domain/states.py(114-120·179-186), src/rider_agent/heartbeat.py(58-77), memory/enum-member-count-locks]

### 재사용 대상 공개 표면 (재구현 금지 — import/주입만)

| 도메인 | 공개 심볼 | 파일/행 | 4.5 사용 |
|---|---|---|---|
| Chrome 실행+격리 가드 | `prepare_chrome(config, *, run_command, cdp_probe, …)`, `ensure_local_cdp_address(cdp_url)` | rider_crawl/browser_launcher.py(40·284) | per-target 격리 오케스트레이션(CDP-unused/profile-free/local-addr/대기 reuse) |
| 격리/복구 예외 | `BrowserLaunchError`, `CdpUnavailableError`, `BrowserActionRequiredError` | rider_crawl/browser_launcher.py(15·19·30) | 중복/원격→시작 안 함, CDP 미응답→재시작, 로그인 필요→AUTH_REQUIRED |
| 실행 락 | `RunLock(path, *, stale_timeout_seconds)` | rider_crawl/lock.py(14) | (선택) 교차 프로세스 이중 오픈 방지(scope 정책 유지) |
| 쿠팡 위험 분류 | `coupang_center_name_risk(platform_name, center_name) -> (bool, str)` | rider_crawl/config.py(300) | 기대 센터/상점 비었/배민기본값 → 위험·진행 안 함 |
| 쿠팡 센터 검증 | `crawl_current_screen`/`crawl_performance_snapshot`(내부 `_validate_coupang_center*`) | rider_crawl/platforms/coupang/crawler.py(15·26·50·84) | CENTER_MISMATCH 시 `RuntimeError`(이미 fail-closed) — 흡수·매핑만 |
| 도메인 어휘(값) | `BrowserProfileState`(UNKNOWN/READY/IN_USE/INACTIVE), `BaeminAuthState.CENTER_MISMATCH`, `FailureCategory.TARGET_VALIDATION_FAILURE` | rider_server/domain/states.py(114-120·58·179-186) | 상태/오류 문자열 정합(직접 import는 단방향 위반 — 값만 평문 상수로 반영) |
| heartbeat reporter | `HeartbeatReporter(browser_profiles_provider=…)`, `build_heartbeat_payload`/`send_heartbeat` | rider_agent/heartbeat.py(143-156·199·239·267·322) | `browser_profiles` 소스 주입(무변경 재사용) |
| job 배선 | `build_agent_components(...)`, `run_agent(...)` | rider_agent/job_loop.py(678-730·744-803) | `browser_profiles_provider` thread-through(additive) |
| redaction | `redact(text)` | rider_crawl/redaction.py | 오류 사유/로그에서 raw 경로/센터/secret 마스킹 |
| 재사용 seam | `rider_agent.reuse`(+ 본 스토리 확장) | rider_agent/reuse.py | rider_crawl 빌딩블록 단일 chokepoint(docstring이 "crawl_worker 4.5" 지목) |

- **주의 — 단방향 import:** `rider_server.domain.states`를 `rider_agent`가 import하면 4.1 가드 위반은 아니나(가드는 `rider_server` import만 금지) → **금지된다**(`test_rider_agent_never_imports_rider_server`). 따라서 상태/오류 enum **값**은 `rider_agent` 안에 **평문 문자열 상수로 반영**(예: `STATE_READY = "READY"`, `ERROR_TARGET_VALIDATION_FAILURE = "TARGET_VALIDATION_FAILURE"`)하고 `rider_server`를 import하지 않는다. [Source: tests/agent/test_agent_package.py(228-233), src/rider_server/domain/states.py(58·179-186)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl`·`rider_server`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py` 무변경** — `git diff -w` = 신규 `browser_profile.py` + 신규 테스트 + `job_loop.py`(provider thread-through) + `reuse.py`(re-export 추가) + sprint-status. (b) **의존성 단방향·sync** — 신규 모듈도 `rider_crawl`/자기 패키지만 import, async 0, `rider_server` import 0, `threading`/주입 sleep으로 동작. (c) **새 프레임워크/의존 0** — 포트 할당은 stdlib `socket`, third-party root는 `rider_crawl`만, deps 정확히 9개 → 4.1 가드 green. (d) **중복 검증 약화 금지** — 포트/프로필 중복·원격 CDP는 fail-closed(시작 안 함), 우회 경로 신설 0. (e) **대상 검증 fail-closed** — 위험/CENTER_MISMATCH면 메시지 미생성·미발송, 검증 우회 수집 경로 0. (f) **raw 경로/secret 비노출** — heartbeat `browser_profiles`·로그·예외에 raw 프로필 경로·쿠팡 로그인·token 평문 0(id/ref만), redact 통과. (g) **무한 재시도 금지** — 로그인 필요는 AUTH_REQUIRED 전이(NFR-4). (h) **reuse 재구현 0** — `prepare_chrome`/`coupang_center_name_risk`/쿠팡 센터 검증을 다시 짜지 않음. (i) **누출 0** — 테스트 실제 외부 미호출, 가짜 값만. [Source: project-context.md(46·64·78·81·82·93), operations-security-test-contract.md(11·16·87-95), tests/agent/test_agent_package.py(176-233)]

### 이전 스토리/회고 인텔리전스 (4.1~4.4 → 4.5 이월 교훈)

- **4.3/4.4가 깐 토대 위에 빌드(직접 계승):** 4.3은 `HeartbeatReporter(browser_profiles_provider=)`를 깔고 "실제 소스 배선은 4.5"로 위임했다(heartbeat.py:23). 4.4는 `active_jobs`를 `build_agent_components`에서 `runner.active_jobs`로 배선하는 **정확한 패턴**을 만들었다(job_loop.py:726). 4.5는 그 옆에 `browser_profiles_provider=manager.browser_profiles`를 **동형으로** 더한다 — 새 seam 발명 없이 배선만. [Source: src/rider_agent/heartbeat.py(23·322), src/rider_agent/job_loop.py(718-730), 4-4 스토리(72-74·118)]
- **reuse seam은 4.5의 명시 소비자(재구현 방지 장치):** `reuse.py` docstring(1-5)이 "후속 워커(**crawl_worker 4.5**, kakao_sender 4.6, auth 4.8·4.9)가 rider_crawl 도메인을 **이 한 곳에서** 가져오도록" 의도한다. 4.5는 이 seam을 확장해(browser_launcher/lock/config 추가) rider_crawl 빌딩블록을 단일 chokepoint로 재사용한다 — 모듈마다 흩어 import하지 않는다. [Source: src/rider_agent/reuse.py(1-5)]
- **enum/lock 전수 점검(memory):** 프로필 상태·오류 카테고리를 enum이나 "정확히 N개" 테스트로 잠그지 말 것 — 후속 상태/카테고리 추가가 여러 lock을 깨는 패턴(`secure_store`/`heartbeat`가 평문 상수로 피한 이유). `reuse.__all__`엔 count-lock이 없으나(확인됨), 추가 심볼은 반드시 resolvable해야 한다. [Source: memory/enum-member-count-locks, tests/agent/test_agent_package.py(139-169·304-310)]
- **부정 가드는 AST로(4.1 계승, 자동 적용):** 단방향·sync·no-new-framework 가드는 4.1이 AST로 `src/rider_agent/*.py`를 glob한다 — 신규 `browser_profile.py`는 자동 검사. 새 가드를 raw grep으로 짜지 말 것(scope docstring이 `rider_server`/`workers`/`async` 같은 금지·후속 심볼명을 문자열로 언급해 오탐). [Source: tests/agent/test_agent_package.py(176-233), memory/negative-guard-tests-use-ast]
- **테스트 수치/File List 단일 정본(A2″ 계승):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. 4.1(9→14)·4.2(1037→1060)·4.3(26→31)·4.4 모두 stale로 MEDIUM이 났다. [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]
- **secret/원시경로 누출 비용 급등:** 4.5는 **프로필 디스크 경로**라는 새 민감 표면을 다룬다 — heartbeat·로그·예외·테스트 fixture에서 raw 경로/쿠팡 로그인이 새지 않게 `redact` + id/ref-만-전송 + 가짜값 규칙을 적용한다. [Source: operations-security-test-contract.md(11·16·87-95), project-context.md(81), epic-3-retro-2026-06-13.md(109·118)]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest는 **WSL `python3`가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`**로 돌린다(WSL python엔 pytest 미설치). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w`로 하고 무관한 EOL flip을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]
- **Chrome/CDP/네트워크 테스트 주의:** 실제 Chrome 실행·실 CDP probe·실 `time.sleep`·실 socket 바인딩 대기를 쓰지 말고 **주입 fake `run_command`/`cdp_probe`/sleep/now + 호출 카운터**로 격리·중복·재시작·AUTH_REQUIRED를 결정적으로 검증한다(테스트 hang/flaky 방지). 포트 할당 테스트는 stdlib `socket`을 `tmp` 범위에서만 쓰거나 할당 헬퍼를 주입 가능하게 한다. [Source: architecture-contract.md(109-118), 4-4 스토리(159)]

### Project Structure Notes

- 신규 파일은 architecture.md(452) 트리와 정렬: `src/rider_agent/browser_profile.py`(= `# BrowserProfileManager(port/profile 격리)`). 트리의 `workers/`·`auth/`·`autostart.py`는 각 후속 스토리(4.6~4.9)가 만든다 — **계획된 부분 구현이지 이탈이 아니다**(4.1~4.4 retro의 "부분 구현은 계획" 판정). [Source: architecture.md(446-457), 4-4 스토리(163)]
- 테스트는 `tests/agent/test_browser_profile.py`(평면, `__init__.py` 미추가). 기존 `tests/agent/test_{agent_package,registration,secure_store,heartbeat,job_loop}.py`와 별 basename. [Source: architecture.md(461), 4-4 스토리(164)]
- **변이/충돌:** `project-context.md`의 `rider_agent` 진전 반영은 **Epic 4 retro**에서 한다(rider_server를 Epic 2 retro에서 반영한 선례). 본 스토리에서 project-context.md는 수정하지 않는다. [Source: 4-4 스토리(166), project-context.md(114)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.5(787-808)] — user story + AC(프로필/CDP 격리·중복 미시작·센터/상점 검증·CENTER_MISMATCH 미발송·target_validation_failure·약화 금지).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-14(45)·FR-20(54)·NFR-2(84)·NFR-4(86)·NFR-9(94)·NFR-15(103)·NFR-17(105 — profile_mismatch runbook)] — Browser Profile/CDP 격리·플랫폼 대상 검증·미발송·인증 무한재시도 금지·encryption·조치 가능 분류.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P3-05(62)·Coupang-parser(8)] — "Implement BrowserProfileManager." → "Profile/port duplicate use is prevented." + 쿠팡 store/center 검증.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md#BrowserProfileManager(109-118)·Agent-Loop(87-107)·Job-Types(120-129)·tree(72-83)] — profile 생성/port 할당/launch/health/duplicate prevention/recovery·profiles/<tenant_id>/<target_id>/.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md#browser_profiles(29)·heartbeat(67-69)·CENTER_MISMATCH(129)] — `browser_profiles`(id·agent_id·target_id·profile_path_ref·cdp_port·state), heartbeat가 browser_profiles 포함, server stores id/ref not raw path.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Chrome-profile(11)·Logs(16)·Chrome-growth(79)·Forbidden(94)] — Agent-local disk·BitLocker·profile id/ref not raw path·CDP 직접 접속 금지·BrowserProfileManager capacity.
- [Source: src/rider_crawl/browser_launcher.py(15-33·40-65·82-92·126-139·160-180·261-294)] — `prepare_chrome`/`ensure_local_cdp_address`/예외 3종·CDP-unused·profile-free·CDP 대기(재사용·무변경).
- [Source: src/rider_crawl/platforms/coupang/crawler.py(15-23·26-47·50-110)] — `crawl_current_screen`/`crawl_performance_snapshot`·`_validate_coupang_center*`(불일치 `RuntimeError`, fail-closed) — 재사용·무변경.
- [Source: src/rider_crawl/config.py(258-345)] — `coupang_center_name_risk`(위험 분류 read-only)·`_coupang_center_name_issue`·`_require_coupang_center`·`DEFAULT_BAEMIN_CENTER_NAME`.
- [Source: src/rider_crawl/lock.py·src/rider_crawl/app.py(67-99)] — `RunLock`·run lock scope(cdp 모드=cdp_url, persistent=user_data_dir) — 정책 유지.
- [Source: src/rider_server/domain/states.py(45-59·114-120·165-186)·browser_profile.py] — `BaeminAuthState.CENTER_MISMATCH`·`BrowserProfileState`·`FailureCategory.TARGET_VALIDATION_FAILURE`·`BrowserProfile` 모델 필드(값 정합용, import는 금지).
- [Source: src/rider_agent/heartbeat.py(23·121·143-156·199·215·239-322)] — `browser_profiles_provider` 전 구간 배선(소스만 비어 있음 — 4.5가 채움), 무변경.
- [Source: src/rider_agent/job_loop.py(678-730·744-803)] — `build_agent_components`/`run_agent`의 `active_jobs_provider=runner.active_jobs` 배선 패턴(동형으로 `browser_profiles_provider` 추가).
- [Source: src/rider_agent/reuse.py(1-58)] — 단일 chokepoint(docstring이 "crawl_worker 4.5" 소비자 지목) — 확장 대상.
- [Source: tests/agent/test_agent_package.py(139-233·265-310)] — 4.1 가드(sync·third-party root==rider_crawl·단방향·deps 9핀·reuse __all__ resolvable·import-safety) — 신규 모듈 자동 적용·green 유지.
- [Source: _bmad-output/implementation-artifacts/4-4-outbound-https-job-polling-claim-complete와-lease.md(17·71-74·111·118-119·163-166)] — active_jobs 배선 패턴·provider seam·primitive+deferred 워커·project-context 반영 시점.
- [Source: _bmad-output/implementation-artifacts/epic-3-retro-2026-06-13.md(59·108·109·118·158)] — stub/mock 검증·A1″/A2″·rider_crawl 0줄.
- [Source: _bmad-output/project-context.md(46·53·64·75·78·81·82·93·114)] — CDP lock scope·pytest 실행·단방향 import·포트/프로필 격리 약화 금지·누출 금지·git diff·retro 반영.
- [Source: memory/dev-env-quirks, memory/stale-test-count-a2, memory/negative-guard-tests-use-ast, memory/enum-member-count-locks, memory/agent-main-runpy-warning] — venv pytest·`git diff -w`, 수치 단일 정본, AST 부정 가드, enum/lock 전수 점검, __main__ runpy 경고.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, bmad-dev-story 워크플로)

### Debug Log References

- 전체 스위트 1회(운영 venv): `.venv/Scripts/python.exe -m pytest -q` → **1174 passed** (리뷰 시점 재측정 단일 정본). 신규 `tests/agent/test_browser_profile.py` **32건** 포함(dev 노트 잠정 23건은 QA 보강 9건 append 전 수치라 리뷰에서 재측정 정정 — memory/stale-test-count-a2).
- 4.1 가드 재확인: `pytest tests/agent/test_agent_package.py -q` → 14 passed (third-party root==rider_crawl, sync, 단방향 import, deps 9핀 불변, reuse.__all__ resolvable).
- 재사용 모듈 무회귀: `test_job_loop.py`·`test_heartbeat.py`·`tests/test_browser_launcher.py`·`test_lock.py`·`test_config.py` 전부 green.

### Completion Notes List

- **AC1 (per-target 프로필/CDP 격리 + 중복 미시작):** `BrowserProfileManager.ensure_profile`가 대상별 독립 User Data Dir(`profiles/<tenant_id>/<target_id>/`)와 사용 가능한 `127.0.0.1:<port>`(stdlib `socket` 할당, 새 의존 0)를 할당하고, 실제 격리 가드는 `prepare_chrome`(주입 `run_command`/`cdp_probe`)를 재사용한다. 포트/프로필 중복(in-Agent 등록부)·원격 CDP(`ensure_local_cdp_address`)·`prepare_chrome` 가드 위반 중 하나라도 감지되면 등록하지 않고 `BrowserLaunchError`로 surfacing(fail-closed). 같은 (tenant,target)은 idempotent 재사용 — 다른 대상에 포트/프로필 재배정 안 함.
- **AC2 (대상 검증 + CENTER_MISMATCH 미생성 + 조치 가능 오류):** `classify_target_risk`는 `config.coupang_center_name_risk`를 그대로 호출(재구현 0); 위험(쿠팡 센터 비었/배민기본값)이면 `prepare`도 호출하지 않고 `TargetValidationError`(error_code=`TARGET_VALIDATION_FAILURE`). `map_target_validation_failure`는 쿠팡 센터 검증 `RuntimeError`(이미 fail-closed)를 흡수해 `state=CENTER_MISMATCH`+`mismatch=CENTER_MISMATCH`+`error_code=TARGET_VALIDATION_FAILURE`로 매핑하고, 사유는 헤드라인만 `redact` 통과(raw 화면/설정 센터명·secret 비노출).
- **AC3 (약화 금지 + 건강/복구):** 대상 N개 추가 후에도 포트/프로필 중복 가드 유지(우회 경로 0). `check_health`는 주입 `cdp_probe`로 READY/UNKNOWN 보고. `recover_profile`은 `CdpUnavailableError` 흡수 후 재시작(주입 `sleep` backoff)하되 `max_attempts`로 bounded(무한 재시도 금지), 로그인 필요(`BrowserActionRequiredError`)는 `AUTH_REQUIRED`로 전이하고 즉시 멈춘다.
- **AC4 (heartbeat 소스 배선 + raw 경로 비노출):** `browser_profiles()`는 `id`/`target_id`/`agent_id`/`cdp_port`/`state`만 투영(raw `profile_dir` 미포함). `job_loop.build_agent_components`/`run_agent`에 `browser_profiles_provider`를 additive thread-through해 `HeartbeatReporter(browser_profiles_provider=manager.browser_profiles)`로 배선(4.4 `active_jobs` 배선과 동형). **`heartbeat.py`는 0줄 변경**(이미 인자 보유 — 주입만).
- **재사용 = 재구현 금지:** `reuse.py`에 `prepare_chrome`·`ensure_local_cdp_address`·예외 3종·`RunLock`·`coupang_center_name_risk` re-export 추가(import-safe 유지). `browser_profile.py`는 이 seam을 통해 import. private helper(`_ensure_*`/`_profile_dir_key`) 직접 import 0 — public 경계만 사용, 프로필-키 정규화는 동일 규칙(`expanduser().resolve().casefold()`) 자체 적용.
- **의도된 부분 구현(스코프 경계):** CRAWL_* `execute_job` 수집/Snapshot 업로드 오케스트레이션은 본 스토리 범위 아님 — manager는 후속 crawl 워커가 주입할 primitive로만 제공(`run_agent`에 CRAWL executor 미배선, 빈 stub 워커 파일 미생성). `RunLock`은 (선택)이라 manager 코어 경로에 배선하지 않고 seam re-export로만 노출(결정적 테스트 유지 + lock scope 정책 불변). 상태/오류 어휘는 `rider_server` 직접 import 대신 평문 문자열 상수로 반영(단방향 가드 준수; 테스트만 값 정합 확인용으로 `rider_server.domain.states` import — agent 가드는 `src/rider_agent/*`만 검사).
- **무회귀/범위:** `git diff -w`는 신규 `browser_profile.py`+신규 테스트 + `job_loop.py`(provider thread-through) + `reuse.py`(re-export) + sprint-status/스토리만. `rider_crawl/`·`rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py` 0줄 변경. 신규 코드/테스트 raw 경로·평문 token 0건, `rider_crawl→rider_agent` 신규 import 0, `browser_profile.py` third-party root == `rider_crawl`.

### File List

- `src/rider_agent/browser_profile.py` (신규) — `ProfileAssignment`/`BrowserProfileManager`/`TargetValidationError` + `classify_target_risk`/`map_target_validation_failure` + 상태/오류 평문 상수.
- `src/rider_agent/reuse.py` (수정, additive) — browser_launcher(`prepare_chrome`·`ensure_local_cdp_address`·예외 3종)·`RunLock`·`coupang_center_name_risk` re-export + `__all__` 확장.
- `src/rider_agent/job_loop.py` (수정, additive) — `build_agent_components`/`run_agent`에 `browser_profiles_provider` thread-through(→ `HeartbeatReporter`).
- `tests/agent/test_browser_profile.py` (신규) — AC1~9 검증(격리/할당·중복 거부·센터 검증 매핑·건강/복구·heartbeat provider·누출 가드·값 정합).
- `tests/agent/test_agent_package.py` (수정, additive) — 4.5 reuse 심볼(`prepare_chrome`·`ensure_local_cdp_address`·예외 3종·`RunLock`·`coupang_center_name_risk`)에 `is` identity 단언 추가(재구현 금지 잠금). 리뷰에서 File List 누락 정정.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정) — 4.5 상태 전이.

### Change Log

- 2026-06-13: Story 4.5 구현 — `BrowserProfileManager`(per-target 프로필/CDP 격리 + 중복 거부 fail-closed + 건강/복구) + 대상(센터/상점) 검증 매핑(`target_validation_failure`/`CENTER_MISMATCH`) + heartbeat `browser_profiles` 소스 배선. `rider_crawl` 0줄 변경(reuse only), `heartbeat.py` 0줄 변경(주입만). Status: ready-for-dev → in-progress → review.
- 2026-06-13: Senior Developer Review (AI) — CRITICAL 0. AC1~9 구현 검증 통과, 범위 경계(`rider_crawl`/`rider_server`/`pyproject.toml`/`registration.py`/`secure_store.py`/`heartbeat.py` 0줄) 무회귀, 재사용=재구현 금지 준수. MEDIUM 2건 자동 수정: (1) Debug Log 잠정 수치(1165 passed/23건)를 리뷰 재측정값(**1174 passed/32건**)으로 정정, (2) File List에 누락된 `tests/agent/test_agent_package.py`(reuse identity 단언 +12줄) 추가. LOW 1건(관찰): `check_health`/복구는 `READY`/`UNKNOWN`/`AUTH_REQUIRED`만 방출하고 `IN_USE`/`INACTIVE`는 어휘 상수로만 정의(능동 사용 추적은 CRAWL `execute_job` 미배선으로 범위 밖 — 계획된 부분 구현과 정합). Status: review → done.

## Senior Developer Review (AI)

- **Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Outcome:** Approve (CRITICAL 0)
- **검증 범위:** 신규 `src/rider_agent/browser_profile.py` + `tests/agent/test_browser_profile.py`(32건) + `job_loop.py`/`reuse.py`/`test_agent_package.py` additive diff. 전체 스위트 `1174 passed`, 4.1 가드 14 passed.
- **AC 매핑(전부 IMPLEMENTED):** AC1 per-target 프로필/CDP 격리 + 중복 거부 fail-closed(`ensure_profile`가 stdlib `socket` 포트 할당·`profiles/<tenant>/<target>/` 경로·`prepare_chrome` 재사용·in-Agent 등록부 중복 거부). AC2 `classify_target_risk`(=`coupang_center_name_risk` 재사용)·`map_target_validation_failure`(쿠팡 `RuntimeError` 흡수→`CENTER_MISMATCH`+`TARGET_VALIDATION_FAILURE`, 헤드라인만 `redact`). AC3 약화 금지(N대상 가드 유지)·`recover_profile` bounded 재시작 + `AUTH_REQUIRED` 즉시 전이. AC4 `browser_profiles()` id/ref 투영(raw 경로 비노출) + `job_loop` thread-through(heartbeat 0줄).
- **재사용=재구현 금지:** `prepare_chrome`/`ensure_local_cdp_address`/예외 3종/`RunLock`/`coupang_center_name_risk` 모두 `reuse.py` chokepoint로 import, private helper 직접 import 0, 프로필-키 정규화 동일 규칙 자체 적용.
- **보안/누출:** heartbeat·로그·예외에 raw `profile_dir`/secret 평문 0(테스트 `json.dumps` blob 단언). `redact` 통과, 테스트 fixture 가짜값만.
- **재현/측정:** test_browser_profile.py 32 funcs(grep), 전체 `.venv/Scripts/python.exe -m pytest -q` → 1174 passed. dev 잠정 수치(1165/23)는 QA 보강 9건 append 전이라 stale → 정정.
