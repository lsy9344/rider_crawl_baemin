---
baseline_commit: e01683c
---

# Story 4.6: KakaoSenderWorker — FIFO 직렬 전송과 정확한 채팅방 검증

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want KakaoTalk 전송을 **단일 Windows 세션의 FIFO 직렬 queue**로 처리하고(한 Agent에서 동시에 여러 방에 병렬 입력 금지), **전송 전 정확한 채팅방 검증을 통과하지 못하면 보내지 않으며**(채팅방명 중복·창 확인 실패·포커스 실패·전송 결과 확인 실패는 임의 전송 없이 `kakao_failure`/`kakao_ambiguous_room`으로 실패 기록, 고유 방명 또는 동등한 식별 정책 통과 시에만 전송 대상), **전송량·queue lag를 heartbeat `kakao_status`로 보고**해 운영 화면(Epic 5)이 표시할 수 있고 **전송 실패를 자동으로 다른 방에 보내는 식으로 복구하지 않는**(제한/best-effort) **`KakaoSenderWorker`(FIFO 단일 세션 직렬 + 방 검증 결과 매핑 + `KAKAO_SEND` `execute_job` 어댑터) + heartbeat `kakao_status` 소스 배선 + `start_kakao_sender_worker_if_enabled()` startup 배선**을 갖고 싶다,
so that 같은 세션에서 병렬 입력이나 애매한 방으로의 **오발송 없이** Kakao 메시지를 안전하게 보낸다(P3-06, FR-15·25, NFR-9, ADD-15).

> **이 스토리의 성격 — "FIFO 단일-세션 직렬 워커 + 방 검증 결과 매핑 + heartbeat `kakao_status` 소스 + startup 배선"만.** 채팅방 정확 검증(고유-제목 exact-match 선택·중복/모호 방 거부·입력창 비우기/붙여넣기 동등성 검증/전송 결과 확인)은 **이미 `rider_crawl.sender.send_kakao_text`에 구현되어 있고 `tests/test_sender.py`가 잠근다** — 본 스토리는 그것을 **재구현하지 않고 import/주입으로 재사용**하며, "FIFO 직렬 큐잉(단일 세션) + 실패를 운영 카테고리(`KAKAO_FAILURE`)·하위 사유(`kakao_ambiguous_room`)로 매핑 + 전송량/queue lag를 heartbeat `kakao_status`로 노출 + `KAKAO_SEND` job을 `execute_job` 으로 라우팅 + startup 워커 기동"만 얹는다. **실제 KakaoTalk PC 앱 UI 자동화·창 스캔·키 입력은 본 스토리가 한 줄도 새로 짜지 않는다.** [Source: src/rider_crawl/sender.py(312-367·448-499), implementation-contract.md P3-06(63), architecture-contract.md Agent-Loop(94)·KAKAO_SEND(128), epics.md Story 4.6(810-831)]
>
> **서버가 아직 없다 — "서버 stub/mock에 대한 동작 검증"이 4.x 테스트 형태(절대 전제, 4.1~4.5 계승).** heartbeat의 `kakao_status` 필드를 **수신·저장**하고 `KAKAO_SEND` job을 **생성/queue**하는 서버 측은 Epic 5 소유다. 본 스토리는 실제 KakaoTalk/네트워크 없이 **주입된 fake `send`/`sleep`/`now`/`stop_event` + fake transport**에 대해 FIFO 직렬·방 검증 실패 매핑·queue lag 계산·heartbeat provider shape·`execute_job` 라우팅을 결정적으로 검증한다. epic-3-retro(108): "Epic 4는 서버 측 job 생성·queue·Admin이 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." [Source: epic-3-retro-2026-06-13.md(108), data-api-contract.md(69), 4-5 스토리(19)]
>
> **재사용 = 재구현 금지(이 스토리의 핵심 가드 #1).** `send_kakao_text(config, message)`는 **이미** (a) `KAKAO_CHAT_NAME` 비어 있으면 거부, (b) `_select_kakao_chat_window`로 **고유 제목 exact-match** 선택(0개/복수 매치 → `KakaoSendError`, 모호/스캔불가 → `KakaoUnsafeSelectionError` — main-window 검색 fallback도 안 함), (c) 입력창 비우기/붙여넣기 **완전 동일** 검증/전송 후 비워짐 확인, (d) Enter-눌렀으나-미확인은 `ambiguous=True`로 마킹(빠른 재시도 금지)을 수행한다. **이 검증을 다시 짜지 말고** `rider_agent.reuse`(이미 `send_kakao_text`·`KakaoSendError`·`KakaoUnsafeSelectionError`·`KakaoMessenger` re-export 보유)로 import/주입해 재사용하라. [Source: src/rider_crawl/sender.py(24-47·312-367·448-499), src/rider_agent/reuse.py(50-56·79-84), tests/test_sender.py]
>
> **`kakao_status` 실제 소스를 4.6이 채운다(4.3이 비워둔 곳, 4.4 `active_jobs`/4.5 `browser_profiles`와 동형).** 4.3 `heartbeat.py`는 `kakao_status_provider`(기본 `DEFAULT_KAKAO_STATUS="disabled"`)를 **전 구간 배선**해 두고 docstring(line 23)이 "실제 소스 배선은 후속 — `kakao_status`(4.6)"로 명시 위임했다. 4.6은 `KakaoSenderWorker.kakao_status`(callable)를 `build_agent_components`/`run_agent`에서 `HeartbeatReporter(kakao_status_provider=...)`로 배선한다 — **`heartbeat.py`는 0줄 변경**(이미 인자 보유, 주입만), **`job_loop.py`만 최소 additive 편집**(인자 thread-through + `execute_job` 라우팅 + `start_kakao_sender_worker_if_enabled()` 배선). 4.5가 `browser_profiles_provider`로 깐 thread-through **그 옆에** 동형으로 더한다. [Source: src/rider_agent/heartbeat.py(23·86·120·138-142·198·266·280·321), src/rider_agent/job_loop.py(678-735·749-827), 4-5 스토리(23·202)]
>
> **`kakao_ambiguous_room`은 새 `FailureCategory` 멤버가 아니다 — 하위 사유다(핵심 가드 #2, memory: enum-member-count-locks).** `rider_server.domain.states.FailureCategory`는 docstring이 "**정확히 7 멤버**"로 잠겨 있고 `KAKAO_FAILURE`만 있다(`kakao_ambiguous_room` 없음). 따라서 job-level `error_code`(=`jobs.error_code`/`FailureCategory` 어휘)는 **모든 Kakao 전송 실패에 `KAKAO_FAILURE`** 를 쓰고, `kakao_ambiguous_room`은 그 안의 **하위 사유**(예: `metrics.kakao_outcome` 또는 result/event detail)로 구별한다. **새 카테고리를 추가하거나 kakao 하위 상태를 "정확히 N개" 테스트로 잠그지 말 것** — 후속 추가가 여러 lock을 깬다(`secure_store.TOKEN_STATUS_*`·`heartbeat.DEFAULT_CAPABILITIES`·4.5 평문 상수 선례). [Source: src/rider_server/domain/states.py(165-184), architecture.md(324-325), memory/enum-member-count-locks, 4-5 스토리(119)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리 산출물: 신규 `src/rider_agent/workers/kakao_sender.py`(+ `workers/__init__.py`) + 신규 `tests/agent/test_kakao_sender.py` + `src/rider_agent/job_loop.py`의 **`kakao_status_provider` thread-through + `execute_job` 라우팅 + `start_kakao_sender_worker_if_enabled()` 배선(additive)**. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(절대 규칙·무회귀 안전 마진).** 방 검증·UI 자동화·진단(`send_kakao_text`·`KakaoSendError`·`KakaoUnsafeSelectionError`·`KakaoMessenger`·`kakao_diagnostics.log` rotation)은 **이미 구현·테스트로 잠겨 있다** → import/주입만. epic-3-retro(158): "**`rider_crawl` 0줄**이 안전 마진." [Source: project-context.md(64·82), epic-3-retro-2026-06-13.md(158)]
> - **`pyproject.toml` `[project].dependencies` — 0줄 변경.** FIFO 큐는 **stdlib `queue`/`threading`/`time`**. `pyautogui`/`pyperclip`/`pywinauto`는 `send_kakao_text` **함수 내부**에서만 lazy-import되므로 워커 모듈은 top-level에서 끌지 않는다(import-safe 유지). 새 third-party 의존을 추가하면 4.1 가드 `test_pyproject_dependencies_unchanged_pins`(deps **정확히 9개**)와 `test_rider_agent_only_third_party_root_is_rider_crawl`(third-party root == `{rider_crawl}`)가 **둘 다** 깨진다. [Source: tests/agent/test_agent_package.py(206-225), src/rider_crawl/sender.py(329-333)]
> - **`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py` — 0줄 변경(reuse only).** `heartbeat.py`는 이미 `kakao_status_provider`를 받으므로 시그니처 변경 불필요 — 주입만. [Source: src/rider_agent/heartbeat.py(120·138-142·198·266·280·321)]
> - **`reuse.py` — 0줄 변경(이미 충분).** 4.1이 Kakao 심볼(`send_kakao_text`·`KakaoSendError`·`KakaoUnsafeSelectionError`·`KakaoMessenger`·`dispatch_text_message`)을 **이미 re-export**했고 docstring이 "kakao_sender 4.6"을 이 seam의 소비자로 지목한다 → 워커는 그대로 import. (선택) `is` identity 단언을 `test_agent_package.py`에 더하면 재사용 잠금이 강해진다(4.5 선례, count-lock 없음). [Source: src/rider_agent/reuse.py(3·50-56·79-84), tests/agent/test_agent_package.py(176-181)]
> - **서버 측 `KAKAO_SEND` job 생성·queue·`kakao_status` 수신/저장·`messenger_channels.kakao_room_name` 등록 검증·Admin kakao lag runbook 표시** → **Epic 5.** 본 스토리는 client 워커 + 주입 fake. [Source: data-api-contract.md(30·69), operations-security-test-contract.md(61), architecture.md(155·215)]
> - **CRAWL_* `execute_job`(4.5 deferred)·autostart(4.7)·배민 auth(4.8)·쿠팡 Gmail 2FA(4.9)** → 각 후속 스토리. `execute_job` 라우팅은 KAKAO_SEND만 워커로 보내고 **나머지는 기존 `default_execute_job`(또는 주입 fallback) 유지**(다른 type용 빈 stub 워커 파일 금지). [Source: 4-5 스토리(29·107), architecture.md(452-457)]
>
> **secret/원시정보 비노출(ADD-15·NFR-9 — 핵심 가드 #3).** Kakao는 **채팅방명·진단(창 제목/핸들/입력값)** 이라는 민감 표면을 다룬다. heartbeat `kakao_status`·로그·예외·job 결과 어디에도 **raw 채팅방명·창 제목·붙여넣은 메시지 본문·token이 평문으로** 남지 않게 한다 — 실패 메시지는 `make_failure_result`(=`redacted_error_event`)로 이미 redact 통과시키고(중복 마스킹 금지), `kakao_status`는 **집계 수치만**(queue depth/lag/sent/failed/마지막 error_code) 노출하고 방명/메시지 본문을 넣지 않는다. 테스트 fixture는 가짜 방명/메시지만(실제 `chat_id`·한국 휴대폰·이메일·OTP·token 금지). [Source: src/rider_agent/job_loop.py(187-207), operations-security-test-contract.md(16·18·91), project-context.md(81·94), 4-5 스토리(33·149)]
>
> **sync 런타임 + 단방향 import(4.1 규약 계승 — 자동 검증됨).** 신규 `workers/kakao_sender.py`(+`workers/__init__.py`)는 **순수 동기**(no `async def`/`await`/직접 `import asyncio`)이고 `rider_crawl`/자기 패키지만 import한다(역방향 0, `rider_server` import 0). 큐 대기·재시도 backoff는 주입 가능한 `sleep`/`now`/`stop_event`로 짠다. 4.1이 `src/rider_agent/`를 **`rglob("*.py")`(재귀)** AST 가드로 검사하므로 **`workers/` 하위 신규 모듈도 자동 적용**된다. [Source: tests/agent/test_agent_package.py(33-34·188-245), memory/negative-guard-tests-use-ast]

## Acceptance Criteria

**AC1 — FIFO 단일-세션 직렬 전송 + 병렬 입력 금지 (P3-06, FR-15·25, ADD-15)**

1. **Given** 여러 Kakao 전송 작업(`KAKAO_SEND` job)이 쌓일 때 **When** `KakaoSenderWorker`가 처리하면 **Then** 전송이 **같은 Windows 세션에서 FIFO(enqueue 순서)로 직렬 처리**된다: 워커는 **단일 소비자 thread**가 stdlib `queue.Queue`에서 순서대로 꺼내 한 번에 하나씩만 `send`를 호출하고(실 KakaoTalk UI 자동화 직렬화), 처리 순서는 enqueue 순서를 보존한다. [Source: implementation-contract.md P3-06(63), architecture.md(155), architecture-contract.md(94·128)]
2. **And** 한 Agent의 Kakao 전송이 **동시에 여러 방에 병렬 입력하지 않는다**: 단일 소비자 thread(= 직렬화 장치)가 보장하며, 두 전송을 같은 세션에서 동시 실행하는 경로를 만들지 않는다(ADD-15 금지행위 = "Run two Kakao sends in parallel in the same Windows session"). 동시 `enqueue`가 들어와도 `send` 호출은 겹치지 않음을 단언한다. [Source: operations-security-test-contract.md(70·91), epics.md AC(820-821)]
3. **And** 워커는 4.3/4.4/4.5 primitive 규율을 계승한다: `send`/`sleep`/`now`/`stop_event`/`log` **전부 주입 가능**(실 KakaoTalk·실 thread 장기 대기·실 시계 없이 결정적 테스트), 단발 전송 실패가 소비자 thread를 죽이지 않고(best-effort) 다음 항목으로 진행, `stop()`으로 정지(어떤 분기에서도 즉시 재호출=무한 스핀 없음). [Source: src/rider_agent/job_loop.py(390-403·471-480), src/rider_agent/heartbeat.py(300-308), src/rider_agent/browser_profile.py(184-208)]

**AC2 — 정확한 채팅방 검증: 통과 못하면 임의 전송 없이 실패 기록 (FR-15)**

4. **Given** 채팅방을 정확히 검증해야 할 때 **When** 전송 전 방을 확인하면 **Then** **채팅방명 중복·창 확인 실패·포커스 실패·전송 결과 확인 실패는 실패로 기록되고 임의 전송하지 않는다**: 검증은 `send_kakao_text`(고유 제목 exact-match 선택·입력 동등성/전송 후 확인)를 **재사용**하고, 워커는 그 예외를 흡수해 매핑한다 — `KakaoUnsafeSelectionError`(방명 중복/모호·창 스캔 불가) → `error_code="KAKAO_FAILURE"` + 하위 사유 `kakao_ambiguous_room`; 그 외 `KakaoSendError`(포커스/비우기/전송 결과 확인 실패) → `error_code="KAKAO_FAILURE"`(`kakao_failure`). **검증 자체를 우회·재구현하지 않는다**(검증 없는 별도 전송 경로 신설 금지). [Source: src/rider_crawl/sender.py(40-47·312-367·448-472), epics.md AC(823-826), architecture.md(325·329)]
5. **And** **고유 방명 또는 동등한 식별 정책을 통과해야 전송 대상이 된다**: `_select_kakao_chat_window`가 정규화 제목 **정확히 1개** 매치만 허용하므로(0개/복수 → 실패) 워커는 이 단일-매치 통과 후에만 실제 전송이 일어남을 보장한다 — 모호하면 **보내지 않고** `kakao_ambiguous_room`으로 surfacing한다(fail-closed: "잘못된 Kakao room이면 전송하지 않고 실패 기록"). [Source: src/rider_crawl/sender.py(475-499), implementation-contract.md(85 — unique room name), architecture.md(329)]
6. **And** Enter는 눌렀으나 결과 확인이 안 된 **ambiguous 실패는 자동 빠른 재시도하지 않는다**(같은 메시지 이중 전송 방지): `KakaoSendError.ambiguous=True`를 보존해 워커가 **재-enqueue/재전송하지 않고** 실패로 종결한다(하위 사유로 unconfirmed 구분). [Source: src/rider_crawl/sender.py(24-37·361-367), epics.md AC(831)]

**AC3 — 제한/best-effort 운영: 전송량·queue lag 보고 + 자동 다른-방 복구 금지 (FR-25)**

7. **Given** Kakao를 제한/best-effort 채널로 운영할 때 **When** 전송량·지연을 관리하면 **Then** **전송량과 queue lag가 보고되어 운영 화면(Epic 5)에 표시 가능하다**: `KakaoSenderWorker.kakao_status() -> dict`가 **집계 수치만** 돌려준다(예: `enabled`, `queue_depth`, `queue_lag_seconds`(now − 가장 오래된 대기 항목 enqueue 시각, 빈 큐면 0), `sent`, `failed`, `last_error_code`). 방명/메시지 본문/raw 진단을 넣지 않는다(NFR-9). queue lag는 주입 `now`로 결정적 계산. [Source: operations-security-test-contract.md(28·61), architecture.md(215), data-api-contract.md(69)]
8. **And** **Kakao 전송 실패를 자동으로 다른 방에 보내는 방식으로 복구하지 않는다**: 실패 시 다른 `kakao_room_name`/다른 채널로의 자동 재라우팅 경로를 만들지 않는다(`dispatch_text_message`의 messenger 라우팅을 KAKAO_SEND 워커에 쓰지 않음 — Telegram 등으로 새지 않게 `send_kakao_text` 직접 경로 사용). 실패는 실패로 종결·기록만 한다(best-effort). [Source: epics.md AC(831), src/rider_crawl/messengers/__init__.py(28-29), architecture.md(155)]

**AC4 — heartbeat `kakao_status` 소스 + `KAKAO_SEND` 라우팅 + startup 배선 + secret 비노출 (P3-03 계승, NFR-9, ADD-15)**

9. **Given** 운영 화면(Epic 5)이 Agent의 Kakao 상태를 알아야 할 때 **When** `KakaoSenderWorker.kakao_status`를 `HeartbeatReporter(kakao_status_provider=...)`에 배선하고(`build_agent_components`/`run_agent` thread-through, 4.5 `browser_profiles` 배선과 동형), `KAKAO_SEND` job을 워커의 `execute_job`으로 라우팅하며, `start_kakao_sender_worker_if_enabled()`가 startup에서 (capability에 `KAKAO_SEND` 포함 등) 활성 조건일 때만 소비자 thread를 기동하면 **Then** (a) heartbeat payload `kakao_status`가 워커 상태를 반영하고(미배선/비활성이면 4.3 기본 `"disabled"` 유지 — 무회귀), (b) `KAKAO_SEND` job은 워커가 FIFO로 처리하고 그 외 type은 기존 executor 유지, (c) **raw 방명/메시지/token이 payload·로그·예외에 평문으로 포함되지 않는다**(집계 수치만, redact 통과). `heartbeat.py`는 0줄 변경(주입만). [Source: src/rider_agent/heartbeat.py(120·138-142·321), src/rider_agent/job_loop.py(678-735·749-827), architecture-contract.md(94), data-api-contract.md(69), operations-security-test-contract.md(16)]

## Tasks / Subtasks

- [x] **Task 1 — `workers/kakao_sender.py`: 도메인 dataclass + FIFO 큐 + 단일-소비자 워커 thread (AC: 1, 3)**
  - [x] `src/rider_agent/workers/__init__.py` 신설(가벼운 패키지 — docstring만, 무거운 import 0)과 `src/rider_agent/workers/kakao_sender.py` 신설. architecture.md(455) 트리(`workers/kakao_sender.py # FIFO queue, 단일 세션 직렬`) 정합 + 4.5 forward-commit("workers/는 후속 스토리가 만든다"). [Source: architecture.md(453-455), 4-5 스토리(159)]
  - [x] frozen dataclass `KakaoSendRequest`(`job_id: str`·`room_name: str`·`message: str` + 필요한 최소 식별자) — server로 내보내지 않는 **내부 작업 항목**(heartbeat에 raw 방명/메시지 안 실음). 큐 항목은 `(KakaoSendRequest, enqueue_ts, result_holder)` 형태. [Source: data-api-contract.md(30·69)]
  - [x] `KakaoSenderWorker(*, send=send_kakao_text, build_config=..., sleep=time.sleep, now=time.time, stop_event=None, log=None, capabilities=DEFAULT_CAPABILITIES)`: stdlib `queue.Queue`(FIFO) + **단일 소비자 thread**(`run()` 루프, `stop_event`/`queue` sentinel로 정지). 모든 외부 부작용(전송·시간) 주입 가능. `threading.Lock`으로 카운터/상태 보호(heartbeat thread가 `kakao_status()` 동시 읽음). [Source: src/rider_agent/job_loop.py(405-440·471-480), src/rider_agent/browser_profile.py(184-213)]
  - [x] `enqueue(request) -> ticket`(enqueue 시각 기록) + `run()`이 FIFO로 꺼내 한 번에 하나씩 처리 → **단일 세션 직렬·병렬 입력 없음**(AC1.2). `stop()`은 sentinel/이벤트로 즉시 깨움(무한 블록 금지). 상태 상수는 **평문 문자열**(enum/"정확히 N개" lock 금지). [Source: memory/enum-member-count-locks, src/rider_agent/heartbeat.py(58-77)]
  - [x] **순수 동기 + `rider_crawl`/자기 패키지만 import**(reuse seam 경유) — 4.1 AST 가드 `rglob` 자동 검사(`workers/` 하위 포함). [Source: tests/agent/test_agent_package.py(33-34·188-245)]
- [x] **Task 2 — 정확한 방 검증 reuse + 실패 매핑 (AC: 2)**
  - [x] 소비자가 항목당 `send(config, message)` 호출(기본 `send_kakao_text` — reuse seam). `build_config(*, room_name, ...)`로 `kakao_chat_name`·`log_dir` 등을 담은 `AppConfig` 호환 객체를 구성해 주입(4.5 `BrowserProfileManager.build_config` 패턴). **방 검증/UI 자동화는 `send_kakao_text`가 수행** — 재구현 0. [Source: src/rider_crawl/sender.py(312-336), src/rider_agent/browser_profile.py(224-241)]
  - [x] **실패 매핑(재구현 금지·흡수만):** `except KakaoUnsafeSelectionError` → `make_failure_result("KAKAO_FAILURE", ..., error=exc)` + 하위 사유 `kakao_ambiguous_room`(metrics/detail); `except KakaoSendError as exc` → `make_failure_result("KAKAO_FAILURE", ..., error=exc)`이되 `getattr(exc,"ambiguous",False)`면 **재시도/재-enqueue 안 함**(AC2.6) + unconfirmed 하위 사유. `error_code`는 `FailureCategory.KAKAO_FAILURE`(=`"KAKAO_FAILURE"`) **평문 상수**로 — `rider_server` 직접 import 금지, **새 카테고리 추가/“정확히 N” lock 금지**. [Source: src/rider_server/domain/states.py(165-184), src/rider_agent/job_loop.py(187-207), memory/enum-member-count-locks]
  - [x] 성공이면 `make_success_result(...)`. 결과를 `JobResult`로 만들어 `complete_job` 보고 경로에 태운다(다음 Task의 `execute_job` 어댑터). 실패 메시지는 `make_failure_result`가 이미 redact → **추가 마스킹/방명 노출 금지**. [Source: src/rider_agent/job_loop.py(175-207)]
- [x] **Task 3 — `kakao_status` provider(전송량 + queue lag) + 자동 다른-방 복구 금지 (AC: 3)**
  - [x] `kakao_status() -> dict`: thread-safe 스냅샷으로 **집계 수치만** — `enabled`/`queue_depth`(큐 길이)/`queue_lag_seconds`(주입 `now` − 가장 오래된 대기 항목 enqueue 시각, 빈 큐면 0)/`sent`/`failed`/`last_error_code`. **방명·메시지 본문·raw 진단 미포함**(NFR-9). [Source: operations-security-test-contract.md(28·16), architecture.md(215), data-api-contract.md(69)]
  - [x] **자동 다른-방 복구 금지(AC3.2):** 실패 시 다른 방/채널 자동 재전송 경로를 만들지 않는다. 기본 `send`는 `send_kakao_text`(Kakao 직접 경로) — `dispatch_text_message`(messenger 라우팅, Telegram 가능)를 워커 전송 경로에 쓰지 않는다. [Source: src/rider_crawl/messengers/__init__.py(28-29), epics.md AC(831)]
- [x] **Task 4 — `job_loop` 배선: `kakao_status_provider` thread-through + `KAKAO_SEND` 라우팅 + `start_kakao_sender_worker_if_enabled()` (AC: 1, 3, 4)**
  - [x] `src/rider_agent/job_loop.py` **additive 편집**: `build_agent_components(...)`/`run_agent(...)`에 `kakao_status_provider: Any = None` 추가 → `HeartbeatReporter(..., kakao_status_provider=kakao_status_provider)`로 전달(현재 `browser_profiles_provider=...` 옆, 4.5와 동형). 미전달이면 `None`→4.3 기본 `"disabled"`(무회귀). **`heartbeat.py` 0줄.** [Source: src/rider_agent/job_loop.py(678-735·749-827), src/rider_agent/heartbeat.py(120·138-142·321)]
  - [x] **`execute_job` 라우팅:** `KAKAO_SEND` job은 워커로, 그 외는 기존 executor(기본 `default_execute_job` 또는 주입 fallback). 얇은 라우터 헬퍼(`build_execute_job(*, kakao_worker, fallback=default_execute_job)`)를 워커 모듈에 두고, payload에서 room/message를 추출해 `KakaoSendRequest`로 만든 뒤 워커에 enqueue→결과 대기→`JobResult` 반환. payload 누락은 fail-closed(`KAKAO_FAILURE`). [Source: src/rider_agent/job_loop.py(234-245·410·514-525·757), architecture-contract.md(128)]
  - [x] **`start_kakao_sender_worker_if_enabled(...)`:** architecture-contract startup(94)이 명시한 진입을 배선한다. **활성 조건**(예: `capabilities`에 `KAKAO_SEND` 포함 또는 명시 플래그)일 때만 소비자 thread를 daemon으로 띄우고 `kakao_status_provider`/라우터를 연결한다. 비활성(crawler-only 4.7 노드)이면 띄우지 않고 `kakao_status`는 `"disabled"`. `run_agent` 종료 시 `worker.stop()` + join으로 정리(4.4 heartbeat thread 정리 패턴). **빈 호출/빈 stub 금지** — 실제 배선만. [Source: architecture-contract.md(70·94), src/rider_agent/job_loop.py(664-675·812-819·775-776), 4-4 스토리(seam만, 빈 호출 금지)]
- [x] **Task 5 — 테스트: `tests/agent/test_kakao_sender.py` (AC: 1~9)** — 외부 호출 없음(fake `send`/`build_config`/주입 `sleep`·`now`·`stop_event` + fake transport), 가짜 값만:
  - [x] **위치/네이밍:** `tests/agent/test_kakao_sender.py`(평면, `__init__.py` 미추가 — 4.1~4.5 미러). 신규 basename. [Source: architecture.md(461), 4-5 스토리(160)]
  - [x] **(AC1 — FIFO 직렬·병렬 금지):** enqueue 순서 == 처리 순서; 동시 enqueue여도 `send` 호출이 **겹치지 않음**(fake `send`가 진입/이탈을 기록해 중첩 0 단언); `stop()`이 즉시 깨워 thread 종료(실 대기 0). [Source: operations-security-test-contract.md(91)]
  - [x] **(AC2 — 방 검증 매핑):** fake `send`가 `KakaoUnsafeSelectionError` → `error_code="KAKAO_FAILURE"` + `kakao_ambiguous_room` 하위 사유·**전송 안 함**; `KakaoSendError(ambiguous=True)` → `KAKAO_FAILURE`·**재-enqueue/재시도 0**; `KakaoSendError(ambiguous=False)` → `KAKAO_FAILURE`; 성공 → `make_success_result`. 실패 메시지·`kakao_status`에 raw 방명/메시지 0(redact 단언). [Source: src/rider_crawl/sender.py(40-47·361-367)]
  - [x] **(AC3 — kakao_status·복구 금지):** 주입 `now`로 `queue_lag_seconds` 결정적 계산(빈 큐=0, 대기 항목 있을 때 lag>0); `sent`/`failed`/`queue_depth`/`last_error_code` 집계; 실패가 다른 방/채널로 자동 재전송되지 않음(추가 `send` 호출 0)·기본 경로가 `send_kakao_text`(=`dispatch_text_message` 아님)임을 단언. [Source: operations-security-test-contract.md(28)]
  - [x] **(AC4 — heartbeat·라우팅·startup·비노출):** `build_agent_components`/`run_agent`가 `HeartbeatReporter(kakao_status_provider=worker.kakao_status)`로 배선해 payload `kakao_status`에 반영(미배선=`"disabled"` 무회귀); `KAKAO_SEND` job은 워커로·그 외 type은 기존 executor; `start_kakao_sender_worker_if_enabled`가 활성 조건에서만 thread 기동·비활성이면 미기동; `run_agent` 종료 시 `worker.stop()`/join. heartbeat payload(`json.dumps`)에 raw 방명/메시지/token 0건. (4.3 reporter·주입 transport·실 네트워크 0.) [Source: src/rider_agent/heartbeat.py(138-142·321), src/rider_agent/job_loop.py(694-735·812-819)]
  - [x] **(누출 가드):** 모든 fixture는 가짜 방명/메시지만(`room-fake-…`/`msg-fake-…`) — 실제 `chat_id`·한국 휴대폰·이메일·OTP·token 금지. 실제 KakaoTalk/UI/네트워크 미호출. 로그 캡처·payload·예외·`kakao_status`에 raw 방명/메시지/secret 0건 단언. [Source: project-context.md(55·81·94), operations-security-test-contract.md(16·91), 4-5 스토리(94)]
- [x] **Task 6 — 회귀·범위·누출 검증 및 마무리 (AC: 1~9)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/agent/test_agent_package.py`·`test_job_loop.py`·`test_heartbeat.py`·`tests/test_sender.py`), 신규 케이스만큼만 증가가 정상. [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] **4.1 가드 green 재확인(핵심):** `pytest tests/agent/test_agent_package.py -q`의 (a) third-party root == `{rider_crawl}`, (b) sync(자기 모듈 async 0·`import asyncio` 0), (c) 단방향(`rider_server` 0), (d) pyproject deps **정확히 9개·핀 불변**, (e) `reuse.__all__` resolvable이 **신규 `workers/kakao_sender.py`+`workers/__init__.py` 추가 + `job_loop.py` additive 편집 후에도 통과**. `rglob`이 `workers/` 하위까지 검사하므로 신규 모듈도 stdlib(`queue`/`threading`/`time`)+`rider_crawl`만 썼다면 green. [Source: tests/agent/test_agent_package.py(33-34·188-245)]
  - [x] **`FailureCategory` 7-멤버 lock 무회귀:** `rider_server` 도메인 enum 테스트가 깨지지 않음(새 카테고리 0). `kakao_ambiguous_room`은 하위 사유로만(카테고리 아님). [Source: src/rider_server/domain/states.py(165-184), memory/enum-member-count-locks]
  - [x] **무회귀 확인:** `git diff -w --stat`에 **신규 `workers/__init__.py`+`workers/kakao_sender.py` + 신규 `tests/agent/test_kakao_sender.py` + `job_loop.py`(additive) + sprint-status/스토리**만 보이고 **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py` 변경 0줄**임을 확인(CRLF/LF 노이즈 무시 — `git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 raw 방명/메시지/평문 token 0건, `src/rider_crawl/`에 `rider_agent` import 신규 0건, `workers/kakao_sender.py`에 `rider_server` import 0건. [Source: project-context.md(64·81·94)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(dev 노트에 잠정 수치 박지 말 것 — 4.1~4.5에서 stale 수치 MEDIUM 재발: qa-e2e가 dev 노트 뒤에 케이스를 append). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리 산출물: 신규 `src/rider_agent/workers/__init__.py` + `src/rider_agent/workers/kakao_sender.py`(KakaoSenderWorker + 실패 매핑 + kakao_status + execute_job 라우터 + start helper) + 신규 `tests/agent/test_kakao_sender.py` + `src/rider_agent/job_loop.py`의 **`kakao_status_provider` thread-through + `KAKAO_SEND` 라우팅 + `start_kakao_sender_worker_if_enabled()` 배선(additive)**. **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py`는 무변경(reuse only).**
- **건드리지 않는다:** `rider_crawl` 전부(방 검증·UI 자동화·진단 재사용만), CRAWL_* `execute_job`(4.5 deferred·후속), autostart(4.7), 배민·쿠팡 auth(4.8·4.9), 서버 측 `KAKAO_SEND` job 생성/queue·`kakao_status` 수신·`messenger_channels` 등록 검증·Admin kakao lag runbook(Epic 5). **다른 type용 빈 stub 워커 파일도 만들지 않는다.** [Source: epics.md Story 4.7~4.9(833-903), architecture.md(437·452-457), 4-5 스토리(107)]

### 열린 질문 / 의도된 부분 구현 (반드시 읽을 것)

- **워커 모델: FIFO 큐+단일 소비자 thread(권장) vs lock-only.** architecture-contract(94·128)이 `start_kakao_sender_worker_if_enabled()`(별도 startup 진입)와 "FIFO queue, 단일 세션 직렬"을 명시하므로 **FIFO `queue.Queue` + 단일 소비자 thread** 모델을 권장한다(AC1 FIFO·AC3 queue lag를 실제 의미로 충족). `execute_job`은 워커에 enqueue→결과 대기로 KAKAO_SEND를 라우팅한다. 만약 dev/리뷰가 "현 단계는 lock-only 직렬(job_loop가 이미 직렬 claim)도 충분"이라 판단하면, 그 경우에도 **단일 직렬화 장치 + queue lag/volume 집계 노출 + 병렬-입력 금지 단언**은 유지해야 한다(AC1·AC3 불변). 어느 쪽이든 실 KakaoTalk 자동화는 `send_kakao_text` 재사용이고 `start_kakao_sender_worker_if_enabled()`는 실제 배선해야 한다(4.4가 남긴 seam). [Source: architecture-contract.md(94·128), src/rider_agent/job_loop.py(96-107·775-776)]
- **`KAKAO_SEND` job payload 형식(서버 미확정).** 서버(Epic 5)가 room/message를 어떤 키로 줄지 미확정 → 워커는 payload에서 room/message를 **방어적으로 추출**(누락 시 fail-closed `KAKAO_FAILURE`)하고, `build_config`로 `AppConfig` 호환 객체를 구성하는 **주입 seam**을 둔다(4.5 `BrowserProfileManager.build_config` 선례). 흔한 키(`kakao_room_name`/`room_name`, `message`/`text`)를 수용하되 모르는 형태는 보수적으로 실패 처리한다. [Source: data-api-contract.md(30·69), src/rider_agent/browser_profile.py(224-241), src/rider_agent/job_loop.py(360-384 — 보수적 파싱 선례)]

### 설계 결정 — 무엇을 재사용하고 무엇이 신규인가 (반드시 읽을 것)

- **방 검증·UI 자동화는 이미 존재 — `send_kakao_text` 한 곳에 응집(재구현 금지).** `send_kakao_text`(→ `_find_or_open_kakao_chat_window`→`_select_kakao_chat_window`)가 **이미** 고유 제목 exact-match(0/복수→실패), 모호/스캔불가→`KakaoUnsafeSelectionError`(main-window fallback도 안 함), 입력창 비우기/붙여넣기 **완전 동일** 검증, 전송 후 비워짐 확인, ambiguous 마킹을 수행하고 `tests/test_sender.py`가 잠근다. 워커는 이 public 함수를 **호출**해 결과/예외를 매핑할 뿐, 창 스캔·키 입력·검증을 **다시 짜지 않는다**. [Source: src/rider_crawl/sender.py(312-367·448-499)]
- **`kakao_status` 실제 소스를 4.6이 채운다(4.4 `active_jobs`/4.5 `browser_profiles`와 동형).** `heartbeat.py`(line 23)가 "실제 소스 배선은 후속 — `kakao_status`(4.6)"로 위임했고 인자(`kakao_status_provider`)는 이미 `build_heartbeat_payload`→`send_heartbeat`→`HeartbeatReporter` 전 구간에 배선돼 있다(120·138-142·198·266·280·321). 4.6은 `job_loop.build_agent_components`/`run_agent`에 인자를 thread-through해 `worker.kakao_status`를 주입한다 — **`heartbeat.py` 0줄 변경**. 기본값 `DEFAULT_KAKAO_STATUS="disabled"`는 미배선/비활성 노드에서 그대로 유지(무회귀). [Source: src/rider_agent/heartbeat.py(23·86·120·138-142·321), src/rider_agent/job_loop.py(678-735·749-827)]
- **`kakao_ambiguous_room`은 하위 사유, `KAKAO_FAILURE`가 카테고리(enum lock 회피).** `FailureCategory`는 "**정확히 7 멤버**"로 잠겨 있고 `KAKAO_FAILURE`만 있다 — `kakao_ambiguous_room`을 새 멤버로 추가하면 `rider_server` 도메인 enum 테스트가 깨진다(그리고 4.6은 `rider_server` 0줄). 따라서 job `error_code`는 `"KAKAO_FAILURE"` 평문 상수(값 정합, 직접 import 금지 — 4.5 선례)로 두고, `kakao_ambiguous_room`/unconfirmed는 `metrics`/result detail의 **하위 사유**로 구별한다. agent 측 kakao 하위 상태도 **평문 상수**로 두고 "정확히 N개" 테스트로 잠그지 않는다. [Source: src/rider_server/domain/states.py(165-184), memory/enum-member-count-locks, 4-5 스토리(119·136)]
- **포트/세션 직렬화 = 단일 소비자 thread.** 별도 `Lock` 없이도 단일 소비자 thread가 "동시에 한 전송만"을 보장한다(병렬 입력 금지). 카운터/큐 메타 읽기(heartbeat thread 동시 접근)는 `threading.Lock`으로 보호한다(4.4 in-flight 등록부·4.5 registry 선례). 큐 대기/정지는 stdlib `queue`(블로킹 get + sentinel) 또는 주입 `sleep`+`stop_event`로 — 무한 블록/스핀 금지. [Source: src/rider_agent/job_loop.py(435-440·471-480), src/rider_agent/browser_profile.py(208-213)]

### 재사용 대상 공개 표면 (재구현 금지 — import/주입만)

| 도메인 | 공개 심볼 | 파일/행 | 4.6 사용 |
|---|---|---|---|
| Kakao 직접 전송(방 검증·UI 자동화 포함) | `send_kakao_text(config, message, *, platform_name=None)` | rider_crawl/sender.py(312) | 워커 소비자가 항목당 호출(기본 `send`) — 검증/자동화 재사용 |
| Kakao 예외 | `KakaoSendError(message, *, ambiguous=False)`, `KakaoUnsafeSelectionError(KakaoSendError)` | rider_crawl/sender.py(24·40) | 실패 매핑(`kakao_ambiguous_room` vs `kakao_failure`·ambiguous→재시도 금지) |
| Kakao messenger(선택) | `KakaoMessenger.send_text(config, message)` | rider_crawl/messengers/kakao.py(18) | (대안) `send_text` 경계로 주입 — `dispatch_text_message`(라우팅)는 쓰지 않음 |
| 재사용 seam | `rider_agent.reuse`(이미 kakao 5심볼 보유) | rider_agent/reuse.py(50-56·79-84) | 워커가 이 chokepoint로 import(docstring이 "kakao_sender 4.6" 지목) — **무변경** |
| job 결과/이벤트 | `make_success_result`, `make_failure_result(code,msg,*,error)`, `JobResult`, `ClaimedJob`, `default_execute_job` | rider_agent/job_loop.py(148·175·187·234) | `execute_job` 어댑터 결과 생성(redact 통과·재구현 0) |
| job 배선 | `build_agent_components(...)`, `run_agent(...)`, `start_heartbeat_thread` | rider_agent/job_loop.py(664·678·749) | `kakao_status_provider` thread-through·`execute_job` 라우팅·startup thread |
| heartbeat reporter | `HeartbeatReporter(kakao_status_provider=…)`, `DEFAULT_KAKAO_STATUS`, `CAPABILITY_KAKAO_SEND` | rider_agent/heartbeat.py(86·66·120·266·321) | `kakao_status` 소스 주입(무변경)·활성 조건 capability |
| redaction | `redact`, `redacted_error_event`(via `make_failure_result`) | rider_crawl/redaction.py | 실패 사유/로그/status에서 방명/메시지/secret 마스킹 |
| 도메인 어휘(값) | `FailureCategory.KAKAO_FAILURE = "KAKAO_FAILURE"` (7-멤버 lock) | rider_server/domain/states.py(183·165-184) | 문자열 상수로 반영(직접 import는 단방향 위반 — 값만; 새 멤버 추가 금지) |

- **주의 — 단방향 import:** `rider_server`를 `rider_agent`가 import하면 `test_rider_agent_never_imports_rider_server`가 깨진다. `KAKAO_FAILURE`는 `rider_agent` 안에 **평문 상수**(`ERROR_KAKAO_FAILURE = "KAKAO_FAILURE"`)로 반영하고 `rider_server`를 import하지 않는다(테스트에서 값 정합만 확인하려면 테스트 코드에서 `rider_server` import — agent 가드는 `src/rider_agent/`만 검사). [Source: tests/agent/test_agent_package.py(240-245), src/rider_server/domain/states.py(183), 4-5 스토리(136)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl`·`rider_server`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py` 무변경** — `git diff -w` = 신규 `workers/{__init__,kakao_sender}.py` + 신규 테스트 + `job_loop.py`(additive) + sprint-status/스토리.
- (b) **의존성 단방향·sync** — 신규 모듈도 `rider_crawl`/자기 패키지만 import(reuse seam), async 0, `rider_server` 0, `threading`/`queue`/주입 sleep으로 동작. `rglob`이 `workers/` 하위까지 검사.
- (c) **새 프레임워크/의존 0** — FIFO는 stdlib `queue`/`threading`/`time`, `pyautogui`/`pyperclip`는 `send_kakao_text` 내부 lazy-import(워커 top-level 0), third-party root는 `rider_crawl`만, deps 정확히 9개 → 4.1 가드 green.
- (d) **단일-세션 직렬·병렬 입력 금지** — 단일 소비자 thread, 동시 `send` 중첩 0(ADD-15 금지행위).
- (e) **방 검증 fail-closed** — 모호/중복/확인 실패면 **보내지 않고** 실패 기록(`KAKAO_FAILURE`+하위 사유), ambiguous→자동 빠른 재시도 0, 검증 우회 경로 0.
- (f) **자동 다른-방 복구 금지** — 실패를 다른 방/채널로 자동 재전송하지 않음(best-effort), `dispatch_text_message` 라우팅 미사용.
- (g) **secret/방명/메시지 비노출** — heartbeat `kakao_status`·로그·예외에 raw 방명·메시지 본문·token 평문 0(집계 수치만), redact 통과.
- (h) **enum/카테고리 lock 무회귀** — `FailureCategory` 7-멤버 유지(새 카테고리 0), kakao 하위 상태 "정확히 N" lock 0.
- (i) **누출 0** — 테스트 실제 KakaoTalk/UI/네트워크 미호출, 가짜 방명/메시지만.
[Source: project-context.md(46·64·81·82·94), operations-security-test-contract.md(16·70·91), tests/agent/test_agent_package.py(188-245), src/rider_server/domain/states.py(165-184)]

### 이전 스토리/회고 인텔리전스 (4.1~4.5 → 4.6 이월 교훈)

- **4.3/4.4/4.5가 깐 토대 위에 빌드(직접 계승):** 4.3은 `HeartbeatReporter(kakao_status_provider=)`를 깔고 "실제 소스 배선은 4.6"으로 위임했다(heartbeat.py:23). 4.4는 `active_jobs`를 `build_agent_components`에서 배선하는 패턴을 만들고 `run_agent` docstring(775-776)에 "`start_kakao_sender_worker_if_enabled()`(4.6)는 배선하지 않는다(seam만)"로 forward-point했다. 4.5는 `browser_profiles_provider` thread-through를 **정확한 패턴**으로 추가했다. 4.6은 그 옆에 `kakao_status_provider`를 **동형으로** 더하고 4.4가 남긴 startup seam을 **실제 배선**한다 — 새 seam 발명 0. [Source: src/rider_agent/heartbeat.py(23·321), src/rider_agent/job_loop.py(718-731·775-776), 4-5 스토리(144·202)]
- **reuse seam은 4.6의 명시 소비자(재구현 방지 장치):** `reuse.py` docstring(3)이 "후속 워커(crawl_worker 4.5, **kakao_sender 4.6**, auth 4.8·4.9)가 rider_crawl 도메인을 **이 한 곳에서** 가져오도록" 의도하고 Kakao 5심볼을 **이미** re-export한다(50-56·79-84). 4.6은 seam을 **확장하지 않고**(이미 충분) 그대로 소비한다 — 모듈마다 흩어 import하지 않는다. [Source: src/rider_agent/reuse.py(3·50-56·79-84)]
- **enum/lock 전수 점검(memory — 본 스토리에서 특히 위험):** `FailureCategory`는 "정확히 7 멤버" lock이 명문화돼 있다 → `kakao_ambiguous_room`을 카테고리로 추가하면 즉시 깨진다. kakao 하위 사유/상태는 **평문 상수**로 두고 어떤 "정확히 N개" 테스트도 추가하지 말 것(`secure_store`/`heartbeat`/4.5 선례). [Source: src/rider_server/domain/states.py(165-184), memory/enum-member-count-locks, src/rider_agent/heartbeat.py(58-77)]
- **부정 가드는 AST로(4.1 계승, 자동 적용):** 단방향·sync·no-new-framework 가드는 4.1이 AST로 `src/rider_agent/`를 `rglob`한다 — 신규 `workers/kakao_sender.py`·`workers/__init__.py`는 **자동 검사**된다. 새 가드를 raw grep으로 짜지 말 것(scope docstring이 `rider_server`/`async`/`dispatch_text_message` 같은 금지·후속 심볼명을 문자열로 언급해 오탐). [Source: tests/agent/test_agent_package.py(33-34·188-245), memory/negative-guard-tests-use-ast]
- **테스트 수치/File List 단일 정본(A2″ 계승):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. 4.1(9→14)·4.2·4.3·4.4·4.5(잠정 23→재측정 32, 1165→1174) 모두 qa-e2e append 후 stale로 MEDIUM이 났다. [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2, 4-5 스토리(193)]
- **runpy 경고(memory):** 테스트가 `rider_agent.__main__`을 모듈 top에서 import하면 runpy RuntimeWarning이 난다. `test_kakao_sender.py`는 `__main__`을 top-import하지 말고(필요하면 함수 내부로 defer) 워커/`job_loop` 심볼만 import한다. [Source: memory/agent-main-runpy-warning]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest는 **WSL `python3`가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`**로 돌린다(WSL python엔 pytest 미설치). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w`로 하고 무관한 EOL flip을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]
- **KakaoTalk/UI/네트워크 테스트 주의:** 실제 KakaoTalk PC 앱·`pyautogui`/`pyperclip` 키 입력·실 `time.sleep`·실 thread 장기 대기를 쓰지 말고 **주입 fake `send`(예외/성공 시나리오)·`build_config`·`sleep`/`now`/`stop_event` + 호출 카운터/타임스탬프**로 FIFO·직렬·queue lag·실패 매핑·startup을 결정적으로 검증한다(테스트 hang/flaky 방지). [Source: architecture-contract.md(94·128), src/rider_crawl/sender.py(329-333), 4-5 스토리(155)]

### Project Structure Notes

- 신규 파일은 architecture.md(453-455) 트리와 정렬: `src/rider_agent/workers/kakao_sender.py`(= `# FIFO queue, 단일 세션 직렬`) + `workers/__init__.py`(트리가 `workers/` 서브패키지를 전제). 4.5가 forward-commit한 "`workers/`·`auth/`·`autostart.py`는 각 후속 스토리(4.6~4.9)가 만든다"의 첫 실현 — **계획된 신설이지 이탈이 아니다**. (변이/대안: 4.5의 `browser_profile.py`처럼 평면 `src/rider_agent/kakao_sender.py`도 4.1 `rglob` 가드를 통과하나, 트리·4.5 hand-off를 따르는 `workers/` 배치를 정본으로 한다.) [Source: architecture.md(446-457), 4-5 스토리(159·107)]
- 테스트는 `tests/agent/test_kakao_sender.py`(평면, `__init__.py` 미추가). 기존 `tests/agent/test_{agent_package,registration,secure_store,heartbeat,job_loop,browser_profile}.py`와 별 basename. [Source: architecture.md(461), 4-5 스토리(160)]
- **변이/충돌:** `project-context.md`의 `rider_agent` 진전 반영(워커/큐 신설)은 **Epic 4 retro**에서 한다(rider_server를 Epic 2 retro에서 반영한 선례). 본 스토리에서 project-context.md는 수정하지 않는다. [Source: 4-5 스토리(161), project-context.md(114)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.6(810-831)] — user story + AC(FIFO 단일 세션 직렬·병렬 입력 금지·방 정확 검증·중복/확인 실패→kakao_failure/kakao_ambiguous_room·고유 방명·전송량/queue lag 보고·자동 다른-방 복구 금지).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-15(168)·FR-25(178)·NFR-9(94 근방)] — KakaoTalk 직렬 queue + 정확한 채팅방 검증·제한/best-effort(queue lag·자동 복구 금지)·redaction/encryption.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P3-06(63)·unique-room(85)] — "Split KakaoSenderWorker into a queue worker." → "Kakao send is serialized in the same Windows session." + "Kakao registration requires unique room name and test send."
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md#Agent-Loop(87-107·94)·KAKAO_SEND(128)·Agent-#1(70)] — `start_kakao_sender_worker_if_enabled()` startup 진입·`KAKAO_SEND`=serialized UI automation·interactive desktop 필요.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#kakao_queue_lag(28)·single-session(70·91)·FIFO-unique-room(77)·kakao-screenshot(18)·kakao-lag-runbook(61)] — queue lag>120s 알림·같은 세션 병렬 전송 금지·FIFO/고유 방명/테스트 검증·스크린샷 마스킹.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md#messenger_channels(30)·heartbeat(69)] — `kakao_room_name` 매핑·heartbeat가 `kakao_status` 포함.
- [Source: src/rider_crawl/sender.py(24-47·312-367·370-388·448-499)] — `send_kakao_text`/`KakaoSendError(ambiguous)`/`KakaoUnsafeSelectionError`/`_select_kakao_chat_window`(고유 제목 exact-match)·입력 동등성/전송 후 확인(재사용·무변경).
- [Source: src/rider_crawl/messengers/kakao.py(18-24)·messengers/__init__.py(28-29)] — `KakaoMessenger.send_text`·`dispatch_text_message`(라우팅 — KAKAO_SEND 워커엔 미사용).
- [Source: src/rider_agent/heartbeat.py(23·58-77·86·120·138-142·198·266·280·321)] — `kakao_status_provider` 전 구간 배선(소스만 비어 있음 — 4.6이 채움)·`DEFAULT_KAKAO_STATUS="disabled"`·`CAPABILITY_KAKAO_SEND`, 무변경.
- [Source: src/rider_agent/job_loop.py(148-245·390-480·664-735·749-827)] — `execute_job` seam·`make_success_result`/`make_failure_result`·`build_agent_components`/`run_agent` 배선(`browser_profiles_provider` thread-through 패턴·`start_kakao_sender_worker_if_enabled()` 미배선 명시), additive 대상.
- [Source: src/rider_agent/reuse.py(3·50-56·79-84)] — 단일 chokepoint(docstring이 "kakao_sender 4.6" 소비자 지목)·Kakao 5심볼 이미 re-export — 무변경.
- [Source: src/rider_agent/browser_profile.py(184-213·224-241·396-414)] — 주입 가능 manager·`build_config` 패턴·thread-safe registry·heartbeat provider(id/ref만) — 동형 설계 선례.
- [Source: src/rider_server/domain/states.py(165-184)] — `FailureCategory`(정확히 7 멤버 lock)·`KAKAO_FAILURE`(값 정합용, import 금지·새 멤버 추가 금지).
- [Source: tests/agent/test_agent_package.py(33-34·176-181·188-245)] — 4.1 가드(`rglob` 재귀·kakao reuse `is` 단언·sync·third-party root==rider_crawl·단방향·deps 9핀) — 신규 `workers/` 모듈 자동 적용·green 유지.
- [Source: _bmad-output/implementation-artifacts/4-5-browserprofilemanager-chrome-프로필-cdp-격리와-대상-검증.md(17·23·119·144·155·159)] — provider thread-through 패턴·primitive+deferred·enum lock 회피·주입 fake 규율·workers/ forward-commit.
- [Source: _bmad-output/implementation-artifacts/epic-3-retro-2026-06-13.md(59·108·158)] — stub/mock 검증·수치 단일 정본·rider_crawl 0줄.
- [Source: _bmad-output/project-context.md(46·48·49·53·64·75·81·82·93·94·114)] — 카카오 전역 lock·정확 방명 검증·텔레그램 큐 제약·pytest 실행·단방향 import·누출 금지·git diff·평문 token 금지·retro 반영.
- [Source: memory/dev-env-quirks, memory/stale-test-count-a2, memory/negative-guard-tests-use-ast, memory/enum-member-count-locks, memory/agent-main-runpy-warning] — venv pytest·`git diff -w`, 수치 단일 정본, AST 부정 가드, enum/lock 전수 점검, __main__ runpy 경고.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 운영 venv 전체 스위트: `.venv/Scripts/python.exe -m pytest -q` → 1208 passed (리뷰 시점 재측정값, 단일 정본 — dev 노트의 잠정 1202 는 qa-e2e 6 케이스 append 전 측정값이라 stale 이었음, 리뷰에서 정정).
- 4.1 가드: `pytest tests/agent/test_agent_package.py` green — `rglob`이 신규 `workers/` 하위까지 검사(third-party root==rider_crawl, sync, 단방향, deps 9핀, reuse `is` identity 모두 통과).
- 범위 점검: `git diff -w --stat` = `job_loop.py`(additive) + `sprint-status.yaml` + 스토리만(src 중 job_loop만). `rider_crawl/`·`rider_server/`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py` 0줄.

### Completion Notes List

- **신규 `src/rider_agent/workers/` 서브패키지 + `kakao_sender.py`** — `KakaoSenderWorker`(stdlib `queue.Queue` FIFO + 단일 소비자 thread = 세션 직렬화 장치; `send`/`build_config`/`sleep`/`now`/`stop_event`/`log` 전부 주입 가능). 정확한 방 검증·UI 자동화는 `reuse.send_kakao_text`를 **호출만**(재구현 0).
- **실패 매핑(AC2)** — `KakaoUnsafeSelectionError`→`KAKAO_FAILURE`+하위 사유 `kakao_ambiguous_room`(전송 안 함); `KakaoSendError(ambiguous=True)`→`KAKAO_FAILURE`+`kakao_unconfirmed`(재-enqueue/재시도 0); 그 외 `KakaoSendError`→`KAKAO_FAILURE`+`kakao_failure`. `error_code`는 `FailureCategory.KAKAO_FAILURE` 값과 정합하는 **평문 상수**(`rider_server` 직접 import 0, 새 enum 멤버/"정확히 N" lock 0). 하위 사유는 `metrics.kakao_outcome`로만 구별.
- **누출 가드(AC4·NFR-9, 설계 결정)** — `redact()`(자유 텍스트)는 **운영 식별자인 방명을 가리지 못하므로**(redaction.py 181-191: room_name은 `redact_mapping`의 `mask_operational_ids`에서만 마스킹) 실패 메시지에 raw 예외 본문(=`send_kakao_text` 진단=방명 포함)을 **싣지 않고** 고정 사유(`kakao send failed (outcome)`)만 담는다. `kakao_status`는 집계 수치만(`enabled`/`queue_depth`/`queue_lag_seconds`/`sent`/`failed`/`last_error_code`). 테스트가 예외에 방명이 들어 있어도 결과/상태/로그/heartbeat payload에 raw 방명·메시지·token 0건임을 단언.
- **kakao_status 배선(AC3·AC4)** — `job_loop.build_agent_components`/`run_agent`에 `kakao_status_provider` thread-through 추가(4.5 `browser_profiles_provider` 옆, 동형). `heartbeat.py` 0줄(이미 인자 보유, 주입만). 미배선/비활성이면 4.3 기본 `"disabled"` 유지(무회귀).
- **execute_job 라우팅 + startup(AC4)** — `build_execute_job(kakao_worker, fallback=default_execute_job)`이 `KAKAO_SEND`만 워커로, 그 외 type은 기존 executor로. `start_kakao_sender_worker_if_enabled()`는 capability에 `KAKAO_SEND` 있을 때만 daemon 소비자 thread 기동(crawler-only면 미기동·`disabled`). `run_agent(start_kakao_sender=True)`가 실제 배선(빈 호출 0)하고 종료 시 `worker.stop()`+join. 순환 import 회피를 위해 `job_loop`→`kakao_sender` import는 `run_agent` 내부 lazy import.
- **payload 방어적 파싱** — 서버(Epic 5) 미확정 형식 대비 `kakao_room_name`/`room_name`·`message`/`text` 수용, 누락은 fail-closed `KAKAO_FAILURE`(임의 전송 0).

### File List

- `src/rider_agent/workers/__init__.py` (신규) — 가벼운 워커 서브패키지(eager import 0).
- `src/rider_agent/workers/kakao_sender.py` (신규) — `KakaoSenderWorker` + 실패 매핑 + `kakao_status` + `build_execute_job` 라우터 + `start_kakao_sender_worker_if_enabled` + `request_from_job`/`default_build_kakao_config`.
- `src/rider_agent/job_loop.py` (수정, additive) — `build_agent_components`/`run_agent`에 `kakao_status_provider` thread-through + `start_kakao_sender`/`kakao_send`/`kakao_build_config` 배선 + `AgentRunSummary.kakao_worker`.
- `tests/agent/test_kakao_sender.py` (신규) — AC1~9 결정적 검증(주입 fake, 외부 호출 0).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정) — 4.6 in-progress→review.

### Change Log

- 2026-06-13: Story 4.6 생성 — create-story 워크플로(ultimate context engine). Status: ready-for-dev.
- 2026-06-14: dev-story 구현 — `KakaoSenderWorker`(FIFO 단일-세션 직렬 + 방 검증 reuse + 실패 매핑 + `kakao_status` + `execute_job` 라우팅 + startup 배선). 전체 스위트 1208 passed, `rider_crawl`/`rider_server`/`pyproject.toml`/`heartbeat.py`/`reuse.py` 등 0줄(무회귀). Status: review.
- 2026-06-14: Senior Developer Review (AI) — CRITICAL 0. MEDIUM 1(Dev 노트 테스트 수치 1202 → 재측정 1208 정정, stale-test-count-a2 패턴) auto-fix. LOW 2(동시 enqueue 시 `queue_lag` 근사·production `execute` 무타임아웃)는 AC 위반 아님 → 관찰만(코드 무변경). Status: done.

## Senior Developer Review (AI)

**Reviewer:** lsy9344 · **Date:** 2026-06-14 · **Outcome:** Approve (status → done)

**범위/방법:** File List 전 파일 정독 + git 현실 대조(`git diff -w --stat`) + 운영 venv 전체 스위트(`.venv/Scripts/python.exe -m pytest -q`) + 4.6 테스트 + 4.1 가드(`tests/agent/test_agent_package.py`) 재실행.

**AC 검증(전부 IMPLEMENTED):**
- **AC1** — 단일 소비자 thread = 세션 직렬화 장치(`send` 호출 중첩 0). `test_single_consumer_never_overlaps_sends_under_concurrent_enqueue` 가 `max_active == 1` 와 enter/exit 짝짓기로 ADD-15 병렬-입력 금지 단언. FIFO 순서 보존·`stop()` 즉시 깨움·주입 primitive 확인.
- **AC2** — `send_kakao_text` **호출만**(검증/UI 자동화 재구현 0). `KakaoUnsafeSelectionError`→`KAKAO_FAILURE`+`kakao_ambiguous_room`(전송 안 함); `KakaoSendError(ambiguous=True)`→`kakao_unconfirmed`(재-enqueue/재시도 0); 그 외→`kakao_failure`. fail-closed(payload 누락·결과 미수신).
- **AC3** — `kakao_status()` 집계 수치만(방명/메시지/raw 진단 미포함). 주입 `now` 로 `queue_lag_seconds` 결정적; 드레인 후 0 복귀. 기본 경로 `send_kakao_text`(≠`dispatch_text_message`)로 자동 다른-방/채널 복구 없음.
- **AC4** — `build_agent_components`/`run_agent` 가 `kakao_status_provider` thread-through(4.5 `browser_profiles` 동형), `heartbeat.py` **0줄**. `build_execute_job` 가 `KAKAO_SEND` 만 워커로·그 외 fallback. `start_kakao_sender_worker_if_enabled` 가 활성 조건에서만 daemon thread 기동·종료 시 `stop()`+join. payload/로그/예외/status 에 raw 방명·메시지·token 0(누출 가드 통과).

**무회귀/범위:** 소스 변경은 `job_loop.py`(additive)뿐. `rider_crawl`·`rider_server`·`pyproject.toml`·`registration.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`reuse.py` 0줄. 신규 third-party 의존 0(stdlib `queue`/`threading`/`time`). `FailureCategory` 7-멤버 lock 무회귀(`kakao_ambiguous_room` 은 하위 사유, 새 enum 멤버 아님). 4.1 AST 가드(third-party root==rider_crawl·sync·단방향·deps 9핀·reuse identity) green — `rglob` 이 `workers/` 하위 자동 검사. 순환 import 회피(`job_loop`→`kakao_sender` lazy import) 확인. `test_kakao_sender.py` 는 `rider_agent.__main__` top-import 안 함(runpy 경고 0).

**Findings:**
- 🟡 **MEDIUM (auto-fixed)** — Dev Agent Record 테스트 수치 stale: `1202 passed` → 재측정 **`1208 passed`**(qa-e2e 6 케이스 append 후). Debug Log References·Change Log 정정. [memory/stale-test-count-a2]
- 🟢 **LOW (관찰, 무변경)** — 동시 producer enqueue 경합 시 `_pending_ts.pop(0)`(append 순서)가 큐 dequeue 순서와 어긋나 `queue_lag_seconds`/`queue_depth` 가 근사값이 될 수 있음. AC3 의 결정적 계산(주입 `now`·단일 thread 측정 경로)은 충족하고 드레인 시 자기보정되며 job-loop 경로 depth 는 ~0–1. 통과 코드 재구성은 회귀 위험 > 이득이라 변경 안 함.
- 🟢 **LOW (관찰, 무변경)** — production 배선의 `execute()` 는 `submit_timeout=None` 이라 소비자가 결과를 채울 때까지 블록한다. "확정 완료까지 대기 = 이중 전송 방지"라는 의도적 설계이며 임의 타임아웃 도입은 진행 중 전송 중단 위험이 있어 변경 안 함(AC 미요구).

**결론:** CRITICAL 0 → Status `done`. MEDIUM 1 auto-fix 완료, LOW 2 관찰 기록.

_Reviewer: lsy9344 on 2026-06-14_
