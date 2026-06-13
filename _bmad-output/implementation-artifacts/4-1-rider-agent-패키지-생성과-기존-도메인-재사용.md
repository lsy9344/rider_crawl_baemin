---
baseline_commit: 8619723
---

# Story 4.1: rider_agent 패키지 생성과 기존 도메인 재사용

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want 신규 프레임워크를 **하나도 도입하지 않고** 기존 `src/rider_crawl/`의 검증된 도메인(crawler/parser/renderer/Gmail 2FA/Kakao sender)을 **단방향 import로 재사용**하는 신규 패키지 `src/rider_agent/`를 만들어, `python -m rider_agent`가 **동기(sync) 런타임**으로 실행되는 Windows Local Agent의 **패키지 토대와 재사용 seam**만 세우고 싶다(`rider_crawl`은 한 줄도 바꾸지 않는다),
so that 이후 Epic 4(4.2 등록/토큰·4.3 heartbeat·4.4 job claim/lease·4.5 BrowserProfileManager·4.6 KakaoSenderWorker·4.7 autostart·4.8 배민 재인증·4.9 쿠팡 Gmail 2FA)가 이 **단방향 import + sync 런타임 경계** 위에 additive로 빌드되며, Epic 3가 입증한 "`rider_crawl` 0줄 = 무회귀 안전 마진" 규약(epic-3-retro 101·158)을 Agent 패키지에도 그대로 계승한다(P3-01, ADD-2·ADD-3, FR-12 토대).

> **이 스토리의 성격 — "신규 `rider_agent` 패키지의 토대(`__init__.py`+`__main__.py`+재사용 seam) + `python -m rider_agent` 실행 + sync 런타임/단방향 import 규약 잠금"만.** 등록/토큰 저장도, heartbeat도, job claim/lease 루프도, HTTP 클라이언트도, BrowserProfileManager도, KakaoSenderWorker queue도, autostart도, 배민/쿠팡 인증 흐름도 아니다. P3-01 deliverable은 **"기존 crawler/parser/renderer를 import하는 `rider_agent` 패키지를 만들고, `python -m rider_agent`가 실행된다"** 가 전부다(implementation-contract P3-01: "Create `rider_agent` package that imports existing crawler/parser/renderer. `python -m rider_agent` runs."). **등록 코드+token DPAPI 보안 저장은 Story 4.2(P3-02), heartbeat는 4.3(P3-03), outbound HTTPS job polling/claim/complete+lease는 4.4(P3-04), BrowserProfileManager(프로필/CDP 격리·대상 검증)는 4.5(P3-05), KakaoSenderWorker FIFO 직렬 queue는 4.6(P3-06), interactive session 실행 조건·재부팅 autostart는 4.7(P3-07), 배민 사람 개입형 재인증은 4.8, 쿠팡 Gmail 2FA 메일함 분리·lock은 4.9, 서버 측 job 생성·queue·Admin은 Epic 5 소유다.** 본 스토리는 그 위에 얹힐 **패키지 골격과 `rider_crawl` 재사용 chokepoint(seam)만** 둔다. [Source: implementation-contract.md(56-64), epics.md Epic 4(694-696)·Story 4.1(698-713)·Story 4.2~4.9(715-903), architecture.md(446-457·504-509), epic-3-retro-2026-06-13.md(95-110)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 패키지 `src/rider_agent/`(`__init__.py`, `__main__.py`, 재사용 seam 모듈 1개) + 신규 테스트 `tests/agent/`만 추가한다. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(가장 중요).** `app.py`/`crawler.py`/`parser.py`/`message.py`/`sender.py`/`platforms/`/`messengers/`/`auth/` 등 어떤 파일도 수정하지 않는다. 본 스토리는 그것들을 **import해서 재사용만** 한다. **이유 1(의존성 방향 — 절대 규칙):** project-context.md(64)·architecture.md(482·484)가 **`rider_server`/`rider_agent` → `rider_crawl` import는 허용, 역방향 `rider_crawl` → `rider_agent`는 금지**라 못 박았다. **이유 2(무회귀 안전 마진):** epic-3-retro(158)가 "**`rider_crawl` 0줄**이 실행 흐름 재배선의 안전 마진이었다 … Epic 4 `rider_agent`도 동일 규약(rider_crawl만 import, sync 유지)으로 가야 한다"고 명시했다. **이유 3(회귀 그물):** `rider_crawl`을 건드리면 기존 배민/쿠팡/Kakao/Telegram 골든 테스트(tests/test_*.py)가 깨질 수 있다(NFR-20·FR-2 위반). [Source: project-context.md(64·82), architecture.md(482-486), epic-3-retro-2026-06-13.md(101·158), epics.md NFR-20(200)]
> - **등록 코드 입력·`agent_id`/`token` DPAPI/Credential Manager 보안 저장·token revoke** → **Story 4.2**(P3-02, FR-12, NFR-8, ADD-15). 본 스토리는 `registration.py`/`secure_store.py`를 만들지 않는다(빈 stub도 금지). [Source: epics.md Story 4.2(715-736), architecture.md(448-449)]
> - **heartbeat(30~60s 보고)** → **Story 4.3**(P3-03). `heartbeat.py` 미생성. [Source: epics.md Story 4.3(738-758), architecture.md(450)]
> - **outbound HTTPS job polling/claim/complete + lease + `/jobs/{id}/events`** → **Story 4.4**(P3-04, FR-13·16, ADD-5·6). `job_loop.py`·HTTP 클라이언트 미생성. [Source: epics.md Story 4.4(760-785), architecture.md(451), architecture-contract.md(87-107)]
> - **BrowserProfileManager(프로필/CDP 포트 격리·중복 감지·기대 센터/상점명 검증·CENTER_MISMATCH)** → **Story 4.5**(P3-05, FR-14·20). `browser_profile.py` 미생성. [Source: epics.md Story 4.5(787-808), architecture.md(452)]
> - **KakaoSenderWorker(FIFO 단일 세션 직렬 queue·방명 검증·queue lag 보고)** → **Story 4.6**(P3-06, FR-15·25). `workers/kakao_sender.py` 미생성 — 본 스토리는 `rider_crawl.sender.send_kakao_text`를 **import만** 한다(worker 래핑은 4.6). [Source: epics.md Story 4.6(810-831), architecture.md(453-455)]
> - **interactive session 실행 조건·재부팅 autostart·crawler-only vs kakao-sender 노드 구분** → **Story 4.7**(P3-07, FR-28·32). `autostart.py` 미생성. [Source: epics.md Story 4.7(833-853), architecture.md(457), architecture-contract.md(68-70)]
> - **배민 사람 개입형 재인증(AUTH_REQUIRED 감지·우회 금지)** → **Story 4.8**, **쿠팡 Gmail 2FA 고객/메일함/token 분리·mailbox lock** → **Story 4.9**. `auth/` 서브패키지 미생성 — 본 스토리는 `rider_crawl.auth.{gmail,coupang_email_2fa}`를 **import만** 한다. [Source: epics.md Story 4.8(854-876)·Story 4.9(877-903), architecture.md(456)]
> - **서버 측 job 생성·queue(FOR UPDATE SKIP LOCKED)·Admin·FastAPI/async/DB·실제 PostgreSQL 통합** → **Epic 5**. 4.x의 서버 상호작용은 **서버 stub/mock에 대한 동작 검증** 형태다(이번 스토리엔 서버 호출 자체가 없음). [Source: epics.md Epic 4(696)·Epic 5(904-), epic-3-retro-2026-06-13.md(108)]
> - **`src/rider_crawl/` 전부·`src/rider_server/` 전부·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`** → 무변경. (Agent용 PyInstaller exe entry/wheel 패키징은 배포 관심사로 Epic 4 후속/배포 단계 소유, 4.1 AC 아님. project-context.md의 `rider_agent` 신설 반영은 Epic 4 retro에서 — rider_server를 Epic 2 retro에서 반영한 선례와 동일.) [Source: project-context.md(82·114), architecture.md(377·540-541), epic-3-retro-2026-06-13.md(137)]
>
> **sync 런타임 경계(ADD, project-context — Agent의 핵심 제약).** `rider_agent`의 **자기 코드(own modules)** 는 동기다: `async def`/`await`를 쓰지 않고, Cloud(FastAPI/SQLAlchemy)의 async 경계와 섞지 않는다(architecture 333-335·364·484). **주의 — 과잉 가드 금지:** 기존 `rider_crawl.crawler`는 **동기 공개 표면**을 제공하되 내부에서 `asyncio.run(...)`으로 crawl4ai의 async API를 감싼다(crawler.py 3·55·99-101). 즉 "Agent가 동기 `crawl_current_screen()`을 호출하는데 그 내부가 asyncio를 쓰는 것"은 **정상**이다(라이브러리 내부 관심사). 따라서 sync 가드는 **`rider_agent` 자기 모듈에 `async def`/`await`가 없고, 자기 모듈이 직접 `import asyncio`/이벤트 루프를 띄우지 않음**만 본다 — "transitive하게 asyncio를 import하지 않음" 같은 광범위 가드는 **틀렸다**(crawler가 asyncio를 import하므로 오탐). [Source: architecture.md(333-335·364·484), src/rider_crawl/crawler.py(3·55·99-101·580-581), project-context.md(35)]
>
> **단방향 import 규약(절대 규칙 — Epic 3 계승).** `rider_agent`는 `rider_crawl`만 import한다(역방향 `rider_crawl → rider_agent` 0, `rider_agent → rider_server` 0). 이 규칙은 **AST import-edge**로 검증한다(원문 grep 금지) — 본 스토리는 scope 경계 docstring에 `rider_agent`/`rider_server`/금지 심볼명을 **문자열로** 명시할 것이므로, raw 소스 매칭은 docstring 언급을 import로 오탐한다. [Source: project-context.md(64), architecture.md(482-484), memory/negative-guard-tests-use-ast]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** 본 스토리는 token/OTP/비밀번호를 **다루지 않는다**(그건 4.2/4.9). 그래도 코드·테스트 fixture·로그·예외 메시지·docstring에 실제 봇 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다(명백한 가짜값만). [Source: project-context.md(81), epic-3-retro-2026-06-13.md(109·118)]

## Acceptance Criteria

**AC1 — `rider_agent` 패키지 생성 + 기존 도메인 재사용 import + `python -m rider_agent` 실행 (P3-01, ADD-2)**

1. **Given** 기존 `src/rider_crawl/` 공유 도메인이 있을 때 **When** 신규 패키지 `src/rider_agent/`(`__init__.py`+`__main__.py`+재사용 seam 모듈)를 만들고 그 seam이 기존 **crawler/parser/renderer/Gmail 2FA/Kakao sender**를 import하면(P3-01, ADD-2) **Then** `python -m rider_agent`가 **동기로 실행되어 exit code 0으로 정상 종료**되고, 실행 중 tkinter GUI를 띄우거나 실제 브라우저/네트워크/KakaoTalk 전송 같은 부작용을 일으키지 않는다(등록/heartbeat/job 루프는 4.2~4.4이므로 본 스토리의 `__main__`은 thin bootstrap). [Source: implementation-contract.md P3-01(58), epics.md AC(706-709), architecture.md(447·538), architecture-contract.md(87-107)]
2. **And** 재사용 seam은 **기존 `rider_crawl` 빌딩블록을 재구현 없이 그대로 노출**한다(동일 객체 identity): 수집(`rider_crawl.platforms.crawl_snapshot` 및 배민 `crawler`/`parser`·쿠팡 `platforms.coupang`), 렌더(`rider_crawl.message.render_current_screen_message`), Gmail 2FA(`rider_crawl.auth.gmail.fetch_latest_verification_code`·`rider_crawl.auth.coupang_email_2fa.recover_coupang_session_with_email_2fa`), Kakao sender(`rider_crawl.sender.send_kakao_text`(+`KakaoSendError`/`KakaoUnsafeSelectionError`) 또는 `rider_crawl.messengers.KakaoMessenger`). 테스트가 `rider_agent` seam의 심볼이 `rider_crawl`의 동일 함수/클래스 객체임을 `is` 로 단언한다. [Source: epics.md AC(707), architecture.md(454·505·507-508), src/rider_crawl/platforms/__init__.py(crawl_snapshot), src/rider_crawl/message.py(35), src/rider_crawl/sender.py(312·24·40), src/rider_crawl/auth/gmail.py(33), src/rider_crawl/auth/coupang_email_2fa.py(76)]

**AC2 — 새 프레임워크 미도입 + 고정 버전 유지 (ADD-3)**

3. **Given** Playwright 1.60.0·crawl4ai 0.8.7 고정 버전 위에서 동작해야 할 때 **When** `rider_agent` 코드를 작성하면(ADD-3) **Then** 새 프레임워크/서드파티 의존을 **하나도 추가하지 않는다**: `pyproject.toml`의 `[project].dependencies`가 **0줄 변경**이고(playwright==1.60.0·crawl4ai==0.8.7 핀 그대로 유지), `rider_agent` 자기 모듈의 비표준(third-party) import root는 **오직 `rider_crawl`** 이다(그 외는 표준 라이브러리만). AST import-edge 단언으로 확인한다. [Source: epics.md AC(708-709), architecture.md(107-108·124·126-131), pyproject.toml(dependencies), memory/negative-guard-tests-use-ast]

**AC3 — sync 런타임 유지 + 단방향 import (ADD, project-context, Epic 3 규약 계승)**

4. **Given** Agent는 sync 런타임이어야 할 때 **When** Agent 코드를 작성하면(ADD, project-context 규칙) **Then** `rider_agent`의 **자기 모듈에 `async def`/`await`가 없고**, 자기 모듈이 직접 이벤트 루프를 띄우거나 `import asyncio` 하지 않으며, Cloud의 async 경계(FastAPI/SQLAlchemy)와 섞지 않는다. (기존 `rider_crawl.crawler`가 내부에서 `asyncio.run`으로 crawl4ai를 감싸 **동기 표면**을 제공하는 것은 정상이며, Agent가 그 동기 함수를 호출하는 것은 위반이 아니다.) AST 단언은 **`rider_agent` 자기 모듈만** 대상으로 한다. [Source: epics.md AC(711-713), architecture.md(333-335·364·484), src/rider_crawl/crawler.py(3·55·99-101), project-context.md(35)]
5. **And** 단방향 import가 유지된다: `rider_agent`는 `rider_crawl`만 import하고, `rider_crawl`은 `rider_agent`를 **import하지 않으며**(역방향 0), `rider_agent`는 `rider_server`를 import하지 않는다. **AST import-edge 스캔**(raw grep 아님)으로 검증하고, `git diff -w`에 `src/rider_crawl/`·`src/rider_server/`·`pyproject.toml` 변경이 **0줄**임을 확인한다. [Source: project-context.md(64·82), architecture.md(482-484), epic-3-retro-2026-06-13.md(158), memory/negative-guard-tests-use-ast]

## Tasks / Subtasks

- [x] **Task 1 — `rider_agent` 패키지 골격 생성 (AC: 1, 3)**
  - [x] `src/rider_agent/__init__.py` 신설. 짧은 docstring으로 패키지 책임(Windows Local Agent — `rider_crawl` 단방향 재사용, sync 런타임)과 **이번 스토리 범위(토대+seam)·후속 소유(등록 4.2/heartbeat 4.3/claim 4.4/profile 4.5/kakao 4.6/autostart 4.7/auth 4.8·4.9/서버 Epic 5)** 를 명시한다. `__version__` 문자열 상수를 둔다(예: `"0.1.0"` — pyproject `version`과 일치). **`__init__.py`는 가볍게 유지**(재사용 seam을 eager import하지 않음 → `import rider_agent`가 crawl4ai/google 등 무거운 import를 끌지 않게). [Source: architecture.md(446-447), src/rider_server/__init__.py(1-9 패턴), pyproject.toml(version)]
  - [x] **단방향만**: `rider_agent`의 어떤 모듈도 `rider_server`를 import하지 않고, `rider_crawl` import만 추가한다. `pythonpath = ["src"]` 덕분에 별도 설치 없이 동작한다(기존 `rider_server`와 동일 — wheel 패키징 미변경). [Source: project-context.md(64), pyproject.toml(pythonpath), architecture.md(484)]
- [x] **Task 2 — 재사용 seam 모듈 추가 (`src/rider_agent/reuse.py`) (AC: 1, 2, 3)**
  - [x] `rider_crawl`의 검증된 빌딩블록을 **재구현 없이 re-export**하는 단일 chokepoint를 만든다. 후속 워커(crawl_worker 4.5, kakao_sender 4.6, auth 4.8·4.9)가 여기서 import하도록 의도된 문서화된 경계다. 권장 노출:
    - 수집: `from rider_crawl.platforms import crawl_snapshot` (배민/쿠팡 registry 진입). 필요 시 배민 `from rider_crawl import crawler, parser`, 쿠팡 `from rider_crawl.platforms import coupang` 도 노출.
    - 렌더: `from rider_crawl.message import render_current_screen_message`.
    - Gmail 2FA: `from rider_crawl.auth.gmail import fetch_latest_verification_code`; `from rider_crawl.auth.coupang_email_2fa import recover_coupang_session_with_email_2fa`.
    - Kakao sender: `from rider_crawl.sender import send_kakao_text, KakaoSendError, KakaoUnsafeSelectionError` (또는 `from rider_crawl.messengers import KakaoMessenger, dispatch_text_message`).
    [Source: architecture.md(454·505·507-508), src/rider_crawl/platforms/__init__.py(crawl_snapshot 30-34), src/rider_crawl/message.py(35), src/rider_crawl/auth/gmail.py(33)·coupang_email_2fa.py(76), src/rider_crawl/sender.py(312·24·40), src/rider_crawl/messengers/__init__.py(KakaoMessenger·dispatch_text_message)]
  - [x] 이 import들은 **import-safe**다: `rider_crawl.platforms.baemin`은 `crawler`를 **lazy**(함수 내부)로 import하고(baemin.py 13-16), `rider_crawl.sender`는 `pyautogui`/`pywinauto`/`pyperclip`을 **lazy**로 import한다(sender.py 330-333·628-630). 따라서 seam을 eager import해도 crawl4ai/Windows GUI 의존을 끌지 않는다 — 이 동작을 깨는 eager 변경(예: 모듈 최상단에서 `crawl_current_screen` 실행)은 하지 않는다. [Source: src/rider_crawl/platforms/baemin.py(13-16), src/rider_crawl/sender.py(330-333·628-630), src/rider_crawl/messengers/kakao.py(11-15)]
  - [x] `reuse.py` 자기 코드는 **순수 동기**다: `async def`/`await`/직접 `import asyncio` 없음(re-export와 짧은 docstring만). [Source: architecture.md(333-335·484), project-context.md(35)]
- [x] **Task 3 — `python -m rider_agent` 동기 bootstrap (`src/rider_agent/__main__.py`) (AC: 1, 3)**
  - [x] `def main() -> int`(동기)에서 `rider_agent.reuse`를 import해 재사용 wiring이 로드됨을 보장하고, 한 줄짜리 sync 시작 배너(예: `rider_agent {__version__} (sync runtime; reuses rider_crawl, no new framework)`)를 출력한 뒤 `0`을 반환한다. 모듈 말미는 `if __name__ == "__main__": raise SystemExit(main())`. **GUI/브라우저/네트워크/Kakao 부작용 없음** — 등록(4.2)·heartbeat(4.3)·job 루프(4.4)는 후속 스토리가 이 `main()`을 확장한다. docstring으로 "본 스토리는 thin bootstrap; 실제 startup/main_loop는 4.2~4.4"를 명시. [Source: implementation-contract.md P3-01(58), architecture.md(447·538), architecture-contract.md(87-107), src/rider_crawl/__main__.py(패턴)]
  - [x] `tkinter`/`rider_crawl.ui`/`rider_crawl.app.run_once`를 import·호출하지 않는다(레거시 UI 진입은 `python -m rider_crawl` 소유; Agent는 별도 sync 진입). [Source: src/rider_crawl/__main__.py(ui.main), architecture.md(394·482-483·538-539)]
- [x] **Task 4 — 테스트 추가: `tests/agent/test_agent_package.py` (AC: 1~5)** — 외부 호출 없음(fake/monkeypatch/`tmp_path`), 가짜 값만:
  - [x] **(AC1 — 패키지/실행):** `import rider_agent`·`import rider_agent.reuse` 성공. `python -m rider_agent`가 exit 0으로 실행됨을 검증한다 — `runpy.run_module("rider_agent", run_name="__main__")`가 `SystemExit(0)`을 던지는지, 또는 `subprocess`로 `[sys.executable, "-m", "rider_agent"]`를 `PYTHONPATH=src`(또는 repo 레이아웃에 맞는 src 경로)로 돌려 `returncode == 0`인지 단언. tkinter/브라우저/네트워크 부작용이 없음을 보장(서브프로세스 무-GUI 정상 종료로 충분). [Source: implementation-contract.md P3-01(58), pyproject.toml(pythonpath), architecture.md(538)]
  - [x] **(AC1·재사용 identity):** `rider_agent.reuse`의 노출 심볼이 `rider_crawl`의 동일 객체임을 `is`로 단언 — 예: `rider_agent.reuse.render_current_screen_message is rider_crawl.message.render_current_screen_message`, `... .crawl_snapshot is rider_crawl.platforms.crawl_snapshot`, `... .send_kakao_text is rider_crawl.sender.send_kakao_text`, Gmail 2FA 두 함수도 동일. (재구현이 아니라 재사용임을 잠금.) [Source: architecture.md(105-108·505·507-508)]
  - [x] **(AC3·sync 가드 — AST, `rider_agent` 자기 모듈만):** `src/rider_agent/`의 각 `.py`를 `ast.parse`로 읽어 **`ast.AsyncFunctionDef`/`ast.Await`가 0개**이고, 자기 모듈이 `import asyncio`(또는 `from asyncio ...`)를 직접 갖지 않음을 단언. **주의:** `rider_crawl.crawler`가 asyncio를 쓰는 것은 검사 대상이 아니다(transitive 금지 아님). [Source: architecture.md(333-335·364·484), src/rider_crawl/crawler.py(3·99-101), memory/negative-guard-tests-use-ast]
  - [x] **(AC2·새 프레임워크 미도입 — AST):** `src/rider_agent/` 각 모듈의 import edge를 `ast`로 수집해, 허용 import root가 `{sys.stdlib_module_names 표준 라이브러리} ∪ {"rider_crawl"} ∪ {"rider_agent"(자기 패키지·상대 import)}`에 한정됨을 단언 — 즉 **유일한 third-party root는 `rider_crawl`** 이고 다른 외부 의존이 0임을 보장(자기 패키지 self-import는 허용). 추가로 `pyproject.toml`의 `[project].dependencies`에 `playwright==1.60.0`·`crawl4ai==0.8.7` 핀이 **그대로** 있고 deps 항목 수가 늘지 않았음을 읽어 단언(새 의존 0). [Source: epics.md AC(708-709), architecture.md(107-108·124), pyproject.toml(dependencies), memory/negative-guard-tests-use-ast]
  - [x] **(AC5·단방향 import — AST import-edge):** `src/rider_crawl/`의 모든 모듈을 `ast`로 스캔해 `rider_agent`를 import하는 edge가 **0개**임을 단언(docstring에 문자열로 `rider_agent`가 등장해도 import edge가 아니면 통과 — raw grep 아님). 또한 `src/rider_agent/`가 `rider_server`를 import하는 edge가 0개임을 단언. [Source: project-context.md(64), architecture.md(482-484), memory/negative-guard-tests-use-ast]
  - [x] **테스트 위치:** architecture가 지정한 `tests/agent/`(claim loop/profile/kakao queue 미러)를 신설하고 `test_agent_package.py`로 둔다. 기존 평면 컨벤션(`tests/server/`)을 따라 디렉터리 `__init__.py`는 추가하지 않으며 basename은 고유하게. [Source: architecture.md(461), pyproject.toml(testpaths), src/.../tests/server 선례]
  - [x] **(누출):** 모든 fixture는 가짜 값만. 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/`chat_id=<digits>`/한국 휴대폰/이메일·OTP 원문 금지. 실제 Telegram/Kakao/Gmail/브라우저 미호출. [Source: project-context.md(55·81), epic-3-retro-2026-06-13.md(109·118)]
- [x] **Task 5 — 회귀·범위·누출 검증 및 마무리 (AC: 1~5)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/test_*.py`·`tests/server/`) 신규 `tests/agent/` 케이스만큼만 증가가 정상(순수 additive). [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `src/rider_agent/{__init__,__main__,reuse}.py` + 신규 `tests/agent/test_agent_package.py`만** 보이고 **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md` 변경 0줄**임을 확인. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다(`git diff -w`로 확인). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 secret 0건, 그리고 `src/rider_crawl/`에 `rider_agent` import가 새로 생기지 않았음을 확인(AST 가드와 별개의 수동 교차 확인). [Source: project-context.md(64·81), epic-3-retro-2026-06-13.md(118)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(dev 노트에 잠정 수치 박지 말 것 — Epic 2/3에서 stale 수치가 MEDIUM 전수 재발). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `src/rider_agent/{__init__.py, __main__.py, reuse.py}` + 신규 `tests/agent/test_agent_package.py`. **`src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·`rider_crawl_onefile.spec`·`project-context.md`는 무변경.**
- **건드리지 않는다:** `rider_crawl` 전부(재사용만), 등록/token 저장(4.2), heartbeat(4.3), job claim/lease/HTTP(4.4), BrowserProfileManager(4.5), KakaoSenderWorker queue(4.6), autostart/실행조건(4.7), 배민 재인증(4.8), 쿠팡 Gmail 2FA 메일함 분리·lock(4.9), 서버 job/queue/Admin/async/DB(Epic 5). **빈 stub 파일도 만들지 않는다**(후속 스토리 소유 — 미리 만들면 충돌·스코프 크립). [Source: epics.md Story 4.2~4.9(715-903), architecture.md(446-457), implementation-contract.md(59-64)]

### 위치·의존성 방향·sync 경계 결정 — 왜 `src/rider_agent/` + 단방향 import + `rider_crawl` 무변경 (반드시 읽을 것)

- **위치(architecture 정본):** architecture.md(446-457)가 `src/rider_agent/`를 "[신규 — Windows Local Agent]"로 명시하고 `__main__.py # python -m rider_agent`, `workers/crawl_worker.py # rider_crawl 도메인 import`를 둔다. FR-12~16을 "rider_agent/ 전체"로 매핑(504)하고, 메신저/Kakao는 "rider_agent/workers/kakao_sender.py"(507-508), 인증은 "rider_crawl/auth/, rider_agent/auth/"(505)로 매핑한다. 본 스토리는 그 패키지의 **토대(`__init__`/`__main__`)와 재사용 seam만** 깐다(워커/auth/registration 등은 각 스토리). [Source: architecture.md(446-457·504-509)]
- **의존성 방향(절대 규칙):** `rider_agent` → `rider_crawl` import만 허용, **역방향 `rider_crawl` → `rider_agent` 금지**(project-context.md 64, architecture.md 482-484: "공유 도메인(rider_crawl): Cloud와 Agent가 함께 import"). `rider_agent → rider_server`도 0(두 신규 런타임은 HTTP(JSON)로만 통신, 코드 직접 호출 없음 — architecture 484). 옳은 방향: **신규 Agent가 `rider_crawl`(crawler/parser/renderer/Gmail/Kakao)을 import해 재사용**. [Source: project-context.md(64), architecture.md(482-484)]
- **`rider_crawl` 무변경 = 무회귀 안전 마진:** epic-3-retro(158)가 못 박았다 — "**`rider_crawl` 0줄**이 실행 흐름 재배선의 안전 마진이었다 … Epic 4 `rider_agent`도 동일 규약(rider_crawl만 import, sync 유지)으로 가야 한다." rider_server를 정의만 추가하고 미배선으로 둔 2.5/3.1 선례를 그대로 따른다(`rider_agent`도 이번엔 토대+seam만, 실제 claim 루프는 4.4부터). [Source: epic-3-retro-2026-06-13.md(101·158·108), src/rider_server/__init__.py]
- **sync 경계(과잉 가드 금지):** architecture(333-335·364·484)는 "Cloud(FastAPI/SQLAlchemy)=async, Agent의 Playwright/PC 자동화 경로=sync, 두 경계를 섞지 않는다(async 함수에서 blocking sync 직접 호출 금지)"이다. 핵심은 **Agent 자기 코드가 sync**라는 것이지, asyncio를 전혀 못 쓰게 하는 게 아니다. 실제로 `rider_crawl.crawler.crawl_current_screen`은 **동기 함수**지만 내부에서 `asyncio.run(_fetch_page_html_via_crawl4ai_cdp(...))`로 crawl4ai async를 감싸고(crawler.py 55·99-101), 쿠팡 경로는 `playwright.sync_api.sync_playwright`(crawler.py 580-581, coupang/crawler.py 338-340)를 쓴다. Agent는 이 **동기 표면을 그대로 호출**하면 된다. sync 가드 테스트는 반드시 **`rider_agent` 자기 모듈의 AST**만 보고 `async def`/`await`/직접 `import asyncio` 부재를 단언한다(transitive 금지로 짜면 crawler.py의 asyncio 때문에 오탐). [Source: architecture.md(333-335·364·484), src/rider_crawl/crawler.py(3·55·99-101·580-581), src/rider_crawl/platforms/coupang/crawler.py(338-340)]

### 재사용 seam — import-safety가 `python -m rider_agent` 성공의 관건 (핵심 설계 결정)

- AC1의 "`python -m rider_agent`가 실행된다"를 깨는 흔한 실수는 **seam이 무거운/플랫폼 종속 의존을 eager import**하게 만드는 것이다. 다행히 기존 `rider_crawl`은 이미 lazy 경계를 갖췄다:
  - `rider_crawl.platforms.__init__`은 `baemin`/`coupang` 클래스만 import하고, `BaeminDeliveryPlatform`은 `crawl_current_screen`을 **함수 내부에서 lazy import**한다(baemin.py 13-16) → `import rider_crawl.platforms`가 crawl4ai/playwright를 끌지 않는다.
  - `rider_crawl.sender`는 `pyautogui`/`pyperclip`(sender.py 330-333·783-784)·`pywinauto`(628-630)를 **함수 내부에서 lazy import** → `import rider_crawl.sender`가 Windows GUI 의존을 끌지 않는다(WSL/CI에서도 import-safe). `messengers.kakao`도 sender를 lazy import한다(kakao.py 11-15).
- 따라서 `reuse.py`는 위 모듈들을 **eager re-export해도 안전**하다. 단, seam이나 `__main__`에서 **함수를 실행(crawl/send/fetch)하지는 않는다** — import/re-export만(실행은 4.4+ 워커). `__init__.py`는 seam을 eager import하지 않게 가볍게 유지해 `import rider_agent` 자체의 비용을 낮춘다(seam은 `__main__`과 테스트가 import). [Source: src/rider_crawl/platforms/baemin.py(13-16), src/rider_crawl/sender.py(330-333·628-630·783-784), src/rider_crawl/messengers/kakao.py(11-15)]
- **`python -m` 실행 경로:** 프로젝트는 이미 `python -m rider_crawl`(architecture 538-539)·pytest `pythonpath=["src"]`(pyproject)로 `src`를 경로에 둔다. `python -m rider_agent`도 동일하게 **src가 sys.path에 있는 전제**로 실행/검증한다(서브프로세스 테스트는 `PYTHONPATH`에 src 추가 또는 `cwd=src`). wheel 패키징/PyInstaller exe entry(Agent 배포 산출물)는 **배포 관심사**로 4.1 범위 밖 — `rider_server`도 wheel `packages`에 없이 pythonpath로 동작하는 동일 선례. [Source: architecture.md(538-541·377), pyproject.toml(pythonpath·tool.hatch.build.targets.wheel), src/rider_server/(wheel 미등록 선례)]

### 재사용 대상 공개 표면 (seam이 노출할 심볼 — 재구현 금지)

| 도메인 | rider_crawl 공개 심볼 | 파일/행 | 후속 소유 |
|---|---|---|---|
| 수집(registry) | `platforms.crawl_snapshot(config, *, platform_name=None)` | platforms/__init__.py(30-34) | crawl_worker(4.5) |
| 수집(배민 legacy) | `crawler.crawl_current_screen`, `parser.*` | crawler.py(15), parser.py | 4.5 |
| 수집(쿠팡) | `platforms.coupang`(CoupangEatsPlatform) | platforms/coupang/ | 4.5 |
| 렌더 | `message.render_current_screen_message(snapshot, *, source_label="")` | message.py(35) | (Epic 5 렌더 서비스 이미 존재; Agent는 호출만) |
| Gmail 2FA | `auth.gmail.fetch_latest_verification_code`, `auth.coupang_email_2fa.recover_coupang_session_with_email_2fa` | gmail.py(33), coupang_email_2fa.py(76) | 4.9 |
| Kakao sender | `sender.send_kakao_text`(+`KakaoSendError`/`KakaoUnsafeSelectionError`), `messengers.KakaoMessenger`, `messengers.dispatch_text_message` | sender.py(312·24·40), messengers/__init__.py | 4.6 |

- 모두 **import/re-export만** — 시그니처 변경·래핑·재구현 금지(그건 각 후속 스토리). identity 테스트(`is`)로 재사용임을 잠근다. [Source: 위 파일/행, architecture.md(454·505·507-508)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl`·`rider_server`·`pyproject.toml` 무변경** — `git diff -w` = `src/rider_agent/` 신규 3파일 + 신규 테스트만. (b) **의존성 단방향** — `rider_agent → rider_crawl`만, 역방향 0, `rider_agent → rider_server` 0. (c) **sync 자기 코드** — `rider_agent` 모듈에 `async def`/`await`/직접 `import asyncio` 0. (d) **새 프레임워크 0** — third-party import root는 `rider_crawl`만, deps 핀(playwright 1.60.0·crawl4ai 0.8.7) 유지. (e) **부작용 없는 bootstrap** — `python -m rider_agent`는 GUI/네트워크/브라우저/Kakao 없이 exit 0. (f) **import-safety 유지** — seam은 lazy 경계를 깨지 않음(함수 미실행). (g) **누출 0** — 테스트는 실제 외부 미호출, 가짜 값만. [Source: project-context.md(35·55·64·81·82), architecture.md(333-335·482-484), epic-3-retro-2026-06-13.md(158)]

### 이전 스토리/회고 인텔리전스 (Epic 3 → 4.1 이월 교훈)

- **epic-3-retro가 4.1에 직접 지침(101):** "**4.1 `rider_agent` 패키지** → Epic 3가 검증한 **단방향 import 규약**(`rider_agent`도 `rider_crawl`만 import, 역방향 0)과 **sync 런타임 유지**(Cloud async와 분리) 원칙을 그대로 계승한다." 본 스토리는 이를 AC3/Dev Notes에 그대로 반영했다. [Source: epic-3-retro-2026-06-13.md(101)]
- **런타임 배선의 첫걸음(108):** "Epic 4는 Epic 3의 순수 정책/레코드를 처음 '런타임에 배선'하는 쪽으로 한 걸음 간다 … 서버 측 job 생성·queue·Admin은 Epic 5라 **서버 stub/mock에 대한 동작 검증**이 4.x의 테스트 형태." 단 4.1 자체는 아직 서버 호출이 없는 **토대 단계**라 stub/mock도 불필요(실행 가능 + 재사용 wiring 검증이 전부). 실제 claim 루프 mock은 4.4부터. [Source: epic-3-retro-2026-06-13.md(108)]
- **A1″(secret 스캔 차단 게이트)는 4.2 전에(118·168):** retro가 pre-commit secret 스캔을 "**Epic 4 4.2(Agent 토큰) 착수 전 실제 도입**"으로 격상했다. 4.1은 token/OTP를 다루지 않으므로 이 게이트가 본 스토리 AC의 차단 조건은 아니지만, dev는 신규 코드·테스트에 평문 secret 0건을 수동 grep로 계속 확인한다. (게이트 자체 도입은 TEA/도구 소유로 별건.) [Source: epic-3-retro-2026-06-13.md(109·118·168)]
- **A2″(테스트 수치/File List 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. Epic 2/3에서 dev 수치 stale로 MEDIUM이 전수 재발했다. [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]
- **부정 가드는 AST로(memory):** "module never calls/imports X" 류 테스트는 **AST import edge**로 검사해야 한다 — scope 경계 docstring이 금지 심볼(`rider_agent`/`rider_server`/`async`)을 문자열로 명시하므로 raw 소스 grep은 오탐한다. 본 스토리의 단방향·sync·no-new-framework 가드 전부 AST 기반으로 짠다. [Source: memory/negative-guard-tests-use-ast]
- **범위 규율(Epic 2/3 직교):** 각 스토리가 한 가지만 했다. 4.1도 "패키지 토대+재사용 seam"만 하고 등록(4.2)·heartbeat(4.3)·claim(4.4)·profile(4.5)·kakao(4.6)·autostart(4.7)·auth(4.8·4.9)를 끌어오지 않는다. [Source: epic-3-retro-2026-06-13.md(131), epics.md Story 4.2~4.9(715-903)]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest는 **WSL의 `python3`가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`** 로 돌린다(WSL python엔 pytest 미설치). pytest 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w`(-w: whitespace 무시)로 확인하고, 무관한 EOL flip 변경을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]

### Project Structure Notes

- 신규 패키지는 architecture.md(446-457) 디렉터리 트리와 정렬한다: `src/rider_agent/__main__.py`(= `python -m rider_agent`). 본 스토리는 트리 중 **`__init__.py`·`__main__.py`·재사용 seam(`reuse.py`)만** 생성하고, `registration.py`/`secure_store.py`/`heartbeat.py`/`job_loop.py`/`browser_profile.py`/`workers/`/`auth/`/`autostart.py`는 각 후속 스토리(4.2~4.9)가 만든다 — **이는 계획된 부분 구현이지 이탈이 아니다**(Epic 2/3 retro의 "13 vs 8 모델은 계획된 부분 구현" 동일 판정). [Source: architecture.md(446-457), epic-3-retro-2026-06-13.md(137)]
- 테스트는 `tests/agent/`(architecture 461) 신설. 기존 `tests/`(평면) + `tests/server/` 미러 컨벤션을 따른다(디렉터리 `__init__.py` 미추가). [Source: architecture.md(458-463), pyproject.toml(testpaths)]
- **변이/충돌:** `project-context.md`(20)는 "Agent/크롤러 코드는 `src/rider_crawl/`에"라고 적혀 있으나 이는 **Epic 2 시점 기준**이며, architecture.md/epics/epic-3-retro가 신규 Agent 런타임을 `src/rider_agent/`로 확정했다. project-context.md의 `rider_agent` 신설 반영은 **Epic 4 retro**에서 한다(rider_server를 Epic 2 retro에서 반영한 선례). 본 스토리에서 project-context.md는 수정하지 않는다. [Source: project-context.md(20·114), architecture.md(446·268), epic-3-retro-2026-06-13.md(97·101)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-4(694-696)] — Epic 4 목표·범위(P3-01~07, FR-12~20·25·28·32, 서버 stub/mock 검증).
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.1(698-713)] — Story 4.1 user story + AC(rider_agent 생성·재사용·`python -m rider_agent`·sync 런타임).
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.2~4.9(715-903)] — 후속 스토리 소유 경계(스코프 크립 방지).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P3-01(58)] — "Create `rider_agent` package that imports existing crawler/parser/renderer. `python -m rider_agent` runs."
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md#Local-Agent-Runtime(68-107)] — Agent 디렉터리/loop(후속 스토리 맥락), sync/PC 자동화 제약.
- [Source: _bmad-output/planning-artifacts/architecture.md#Project-Structure(446-457)] — `src/rider_agent/` 트리.
- [Source: _bmad-output/planning-artifacts/architecture.md#Boundaries(473-496)] — Cloud=async/Agent=sync, HTTP만 통신, 공유 도메인 import 방향.
- [Source: _bmad-output/planning-artifacts/architecture.md#Patterns(332-335·364·484)] — async/sync 경계 규칙(blocking sync 직접 await 금지).
- [Source: _bmad-output/planning-artifacts/architecture.md#Reused-Foundation(105-108·124·126-131)] — Playwright 1.60.0/crawl4ai 0.8.7 고정, 새 프레임워크 없음, rider_crawl 재사용.
- [Source: _bmad-output/project-context.md(20·35·53·55·64·75·81·82)] — 패키지 위치/sync·async/단방향 import/pytest 실행/누출 금지/범위 규율.
- [Source: _bmad-output/implementation-artifacts/epic-3-retro-2026-06-13.md(101·108·118·158·168)] — 4.1 직접 지침(단방향+sync 계승), rider_crawl 0줄 안전 마진, A1″/A2″.
- [Source: src/rider_crawl/platforms/__init__.py(30-34), platforms/baemin.py(13-16)] — `crawl_snapshot` 진입 + lazy crawler import.
- [Source: src/rider_crawl/crawler.py(3·55·99-101·580-581)] — 동기 표면 + 내부 `asyncio.run`/`sync_playwright`(sync 가드 과잉금지 근거).
- [Source: src/rider_crawl/message.py(35), sender.py(312·24·40·330-333·628-630), auth/gmail.py(33), auth/coupang_email_2fa.py(76), messengers/__init__.py·kakao.py(11-15)] — 재사용 seam 공개 표면 + lazy import 안전성.
- [Source: pyproject.toml] — `dependencies`(playwright==1.60.0·crawl4ai==0.8.7), `pythonpath=["src"]`, `testpaths=["tests"]`, wheel `packages`.
- [Source: memory/negative-guard-tests-use-ast, memory/stale-test-count-a2, memory/dev-env-quirks] — AST 부정 가드, 테스트 수치 단일 정본, venv pytest + `git diff -w`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (BMAD dev-story workflow)

### Debug Log References

- `.venv/Scripts/python.exe -m pytest tests/agent/test_agent_package.py -q` → 14 passed (신규 케이스 단독; qa-e2e Gap1~5 포함 — 리뷰 시점 재측정).
- `.venv/Scripts/python.exe -m pytest -q` → 전체 통과(무회귀, 아래 Completion Notes 수치).
- `git diff -w --stat -- src/rider_crawl src/rider_server pyproject.toml rider_crawl_onefile.spec _bmad-output/project-context.md` → 출력 없음(보호 경로 0줄 변경).
- 누출 grep(bot-token/chat_id/전화/이메일/OTP 패턴) → 신규 코드·테스트에 실제 secret 0건(유일 매치는 "원문 없음"을 설명하는 docstring의 단어 `OTP`, 값 아님).
- 의존성 방향 grep(`^\s*(from|import)\s+rider_agent` in `src/rider_crawl`) → 0건(역방향 import 없음).

### Completion Notes List

리뷰 시점 재측정(단일 정본): **전체 스위트 1008 passed, 0 failed**(운영 venv `.venv/Scripts/python.exe -m pytest -q`). 이 중 신규 `tests/agent/test_agent_package.py` **14건** 포함(dev 노트 작성 후 qa-generate-e2e-tests 가 Gap1~5 5건을 추가 → 9→14, 전체 1003→1008), 기존 테스트 회귀 0.

구현 요약 — 순수 additive, `rider_crawl`/`rider_server`/`pyproject.toml` 무변경:

- **Task 1·3 — 패키지 토대.** `src/rider_agent/__init__.py`는 docstring + `__version__ = "0.1.0"`(pyproject `version`과 일치)만 두어 가볍게 유지(seam eager import 없음 → `import rider_agent`가 crawl4ai/google을 끌지 않음). `src/rider_agent/__main__.py`는 동기 `def main() -> int` thin bootstrap: `reuse` seam import 가능을 확인하고 한 줄 sync 배너 출력 후 `0` 반환, 말미 `raise SystemExit(main())`. GUI/브라우저/네트워크/Kakao 부작용 없음. tkinter/`rider_crawl.ui`/`rider_crawl.app.run_once` 미import.
- **Task 2 — 재사용 seam(`src/rider_agent/reuse.py`).** `rider_crawl`의 검증된 빌딩블록을 재구현 없이 re-export하는 단일 chokepoint: 수집(`platforms.crawl_snapshot`+배민 `crawler`/`parser`+쿠팡 `coupang`), 렌더(`message.render_current_screen_message`), Gmail 2FA(`auth.gmail.fetch_latest_verification_code`, `auth.coupang_email_2fa.recover_coupang_session_with_email_2fa`), Kakao(`sender.send_kakao_text`+`KakaoSendError`/`KakaoUnsafeSelectionError`, `messengers.KakaoMessenger`/`dispatch_text_message`). 모두 import/re-export만 — 실행 없음. 대상 모듈은 모두 module-level 기준 import-safe임을 확인(crawler/parser는 stdlib+browser_launcher만, sender/auth는 무거운/플랫폼 의존을 함수 내부 lazy import). reuse.py 자기 코드는 순수 동기.
- **Task 4 — 테스트(`tests/agent/test_agent_package.py`, 14건 — dev 9건 + qa-e2e Gap1~5 5건).** AC1 패키지/seam import + `python -m rider_agent` exit 0(runpy in-process + subprocess `PYTHONPATH=src` 양쪽), AC1 재사용 identity(`is` 단언 12종), AC3 sync 가드(AST: `rider_agent` 자기 모듈에 `AsyncFunctionDef`/`Await`/`async for`/`async with` 0 + 직접 `import asyncio` 0; transitive 미검사), AC2 새 프레임워크 0(AST: third-party import root == `{rider_crawl}` + pyproject deps 9개·핀 playwright==1.60.0·crawl4ai==0.8.7 유지), AC5 단방향(AST import-edge: `src/rider_crawl` 어느 모듈도 `rider_agent` 미import, `src/rider_agent`는 `rider_server` 미import). `tests/agent/`는 `__init__.py` 없이 평면 컨벤션(`tests/server/` 미러).
- **Task 5 — 회귀·범위·누출.** 위 재측정 1008 passed, 보호 경로 0줄, 누출 0, 역방향 import 0 확인. CRLF/LF 노이즈·무관 파일 미수정(`git diff -w` 확인).

AC 충족: AC1 ✓(패키지 생성·재사용 import·`python -m rider_agent` exit 0·부작용 없음), AC2 ✓(새 프레임워크 0·핀 유지), AC3 ✓(자기 모듈 sync·단방향 import).

### File List

- `src/rider_agent/__init__.py` (신규)
- `src/rider_agent/__main__.py` (신규)
- `src/rider_agent/reuse.py` (신규)
- `tests/agent/test_agent_package.py` (신규)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정: 4-1 → in-progress → review)

## Change Log

- 2026-06-13 — Story 4.1 구현: `src/rider_agent/` 패키지 토대(`__init__.py`/`__main__.py`/`reuse.py`)와 `rider_crawl` 재사용 seam 추가, `python -m rider_agent` 동기 실행, `tests/agent/test_agent_package.py`(AC1~5 AST 가드 포함) 추가. `rider_crawl`/`rider_server`/`pyproject.toml` 무변경. Status → review.
- 2026-06-13 — Senior Developer Review (AI, Noah Lee): 결과 **Approve**. CRITICAL/HIGH 0. MEDIUM 1(테스트 수치 stale: 9→14·1003→1008·identity 11→12) 정정 완료. LOW 1(`__main__.py` `assert reuse is not None` → `python -O` 제거·중복) — `-O` 안전·linter-clean 한 명시적 seam 참조로 교체. 재측정 단일 정본: 전체 **1008 passed, 0 failed**(agent 14건). Status → done.

## Senior Developer Review (AI)

- **Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Mode:** 자동 수정(auto-fix, 비대화형)
- **Outcome:** ✅ **Approve** — CRITICAL 0건 → Status `done`.

### AC 검증 (전수)

| AC | 판정 | 근거 |
|---|---|---|
| AC1 (패키지 생성·재사용 import·`python -m rider_agent` exit 0·부작용 0) | ✅ IMPLEMENTED | `src/rider_agent/{__init__,__main__,reuse}.py` 존재; `reuse.py` 가 crawler/parser/coupang/`crawl_snapshot`/`render_current_screen_message`/gmail·coupang_email_2fa/sender·messengers 재사용; runpy+subprocess exit 0; import-safety(crawl4ai/playwright/GUI/google 미로드) 검증 통과 |
| AC1 (재사용 identity) | ✅ IMPLEMENTED | `test_reuse_seam_reexports_same_objects` — `is` 단언 12종, 모두 `rider_crawl` 동일 객체 |
| AC2 (새 프레임워크 0·핀 유지) | ✅ IMPLEMENTED | AST: third-party root == `{rider_crawl}`; `pyproject` deps 9개·`playwright==1.60.0`·`crawl4ai==0.8.7` 유지(0줄 변경 확인) |
| AC3 (자기 모듈 sync) | ✅ IMPLEMENTED | AST: `rider_agent` 모듈에 `AsyncFunctionDef`/`Await`/`AsyncFor`/`AsyncWith`/직접 `import asyncio` 0; transitive 미검사(과잉 가드 회피) |
| AC5 (단방향 import) | ✅ IMPLEMENTED | AST import-edge: `src/rider_crawl` → `rider_agent` 0, `src/rider_agent` → `rider_server` 0; `git diff -w` 보호 경로 0줄 |

### Task 감사 (모든 `[x]` 실증)

Task 1~5 전부 실제 구현 확인 — 거짓 완료(`[x]` 미구현) 0건. File List 5개 항목이 git 실재와 일치(소스 4 + sprint-status). `_bmad-output/` 산출물(test-summary·orchestration) 변경은 리뷰 대상 외.

### Findings

- 🟡 **MEDIUM-1 (수정 완료) — Dev Agent Record 테스트 수치 stale.** dev 노트가 agent **9건**/전체 **1003** 으로 기록했으나, 이후 qa-generate-e2e-tests 가 Gap1~5 5건을 추가해 실재는 agent **14건**/전체 **1008**(identity 단언도 "11종" → 실제 12종). 이는 story Task 5 + `memory/stale-test-count-a2` 가 경고한 재발 패턴. → Debug Log/Completion Notes/Change Log 를 재측정 단일 정본(14·1008·12)으로 정정.
- 🟢 **LOW-1 (수정 완료) — `__main__.py` `assert reuse is not None` 안티패턴.** (1) `python -O` 에서 제거됨, (2) 상단 `from rider_agent import ... reuse` 가 이미 seam 로드·검증을 끝내므로 중복. → assert 제거, 배너에 `len(reuse.__all__)` 를 실어 import 를 실제로 사용하는 `-O`-안전·linter-clean 형태로 교체(배너 substring `rider_agent`/`sync runtime`/버전 보존, 14건 통과 재확인).

### 검증 로그(리뷰 재측정)

- `.venv/Scripts/python.exe -m pytest -q` → **1008 passed, 0 failed**(회귀 0).
- `.venv/Scripts/python.exe -m pytest tests/agent/test_agent_package.py -q` → **14 passed**.
- `git diff -w --stat -- src/rider_crawl src/rider_server pyproject.toml rider_crawl_onefile.spec _bmad-output/project-context.md` → 출력 없음(보호 경로 0줄).
- 역방향 import grep(`rider_crawl` 내 `rider_agent`), 평문 secret 스캔 → 각 0건.
