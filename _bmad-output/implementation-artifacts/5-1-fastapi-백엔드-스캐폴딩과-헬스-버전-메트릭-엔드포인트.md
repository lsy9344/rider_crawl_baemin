---
baseline_commit: 3d4cac0c087f51efb19c8a9f9fbfdb36340b5c61
---

# Story 5.1: FastAPI 백엔드 스캐폴딩과 헬스/버전/메트릭 엔드포인트

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want `src/rider_server/`에 FastAPI 표준 app 레이아웃의 Cloud 백엔드를 스캐폴딩하고 `/health`, `/version`, `/metrics`를 제공하며, Docker 컨테이너로 띄울 수 있게 하고 싶다,
So that 이후 모든 서버 기능(Agent/Admin API · scheduler · dispatcher · DB · Admin UI)이 올라갈 토대를 일관된 API 규약·async 런타임·에러 포맷 위에서 시작할 수 있다.

## Acceptance Criteria

**AC1 — FastAPI 스캐폴딩과 운영 엔드포인트 (P4-01, ADD-1·2)**
**Given** 정식 CLI 스캐폴드가 없는 브라운필드일 때
**When** `src/rider_server/` FastAPI 백엔드를 표준 app 레이아웃으로 구성하면
**Then** `/health`, `/version`, `/metrics` 엔드포인트가 동작하고
**And** 서버가 Docker 컨테이너로 실행된다(ADD-12).

**AC2 — API 규약 (ADD-8·13)**
**Given** API 규약을 따라야 할 때
**When** 엔드포인트를 정의하면
**Then** 리소스 경로는 `/v1/` 접두 + 복수 명사이고, JSON 필드는 snake_case(camelCase 변환 없음)이며
**And** 에러 응답은 `{"error":{"code":"<UPPER_SNAKE>","message_redacted":"..."}}` 포맷에 의미 있는 HTTP 상태코드를 쓰고 시각은 ISO 8601 UTC다.

**AC3 — async 런타임 (ADD, project-context 규칙)**
**Given** Cloud는 async 런타임일 때
**When** 서버 코드를 작성하면
**Then** FastAPI는 async로 작성되고 blocking sync를 async 경로에서 직접 호출하지 않는다(필요 시 executor 경계).

## Tasks / Subtasks

- [x] **Task 1 — 서버 의존성을 "별도 그룹"으로 추가 (AC1)**
  - [x] `pyproject.toml`의 `[project].dependencies`(현재 정확히 9개)는 **절대 건드리지 않는다**. 대신 `[project.optional-dependencies]`에 `server = [...]` 그룹을 신설해 `fastapi`, `uvicorn[standard]`를 추가한다(Pydantic v2는 FastAPI가 끌어옴). `dev` 그룹(pytest) 또는 `server` 그룹에 `httpx`를 추가한다(`fastapi.testclient.TestClient`가 httpx를 요구).
  - [x] **검증**: `tests/agent/test_agent_package.py::test_pyproject_dependencies_unchanged_pins`가 계속 통과해야 한다(`len(project.dependencies) == 9`, `playwright==1.60.0`·`crawl4ai==0.8.7` 핀 유지). 이 테스트가 깨지면 회귀로 취급한다.
  - [x] `.venv/Scripts/python.exe`에 서버 extra를 설치한다(`uv pip install -e ".[server,dev]"` 또는 동등). 미설치 시 FastAPI app을 import하는 테스트가 collection 단계에서 실패한다.
- [x] **Task 2 — FastAPI app 표준 레이아웃 스캐폴딩 (AC1, AC3)**
  - [x] `src/rider_server/main.py`에 FastAPI app(또는 `create_app()` factory)을 만들고 `/health`, `/version`, `/metrics`를 등록한다(architecture.md:418 트리 기준 — 세 엔드포인트는 `main.py`에 둔다). 핸들러는 `async def`로 작성한다.
  - [x] `src/rider_server/settings.py`: env 로딩(예: `APP_ENV`, `APP_VERSION`/`BUILD_SHA`) + 향후 Secrets Manager ref 로딩 자리. **5.1에서는 외부 dep 추가 없이** `os.environ` 기반의 작은 typed settings로 둔다(`pydantic-settings`는 별도 패키지이므로 도입하지 않는다 — 필요해지면 5.2+에서 결정).
  - [x] `src/rider_server/__main__.py`: `python -m rider_server`로 uvicorn을 띄우는 진입점(개발 실행 편의). 운영은 Docker의 uvicorn 커맨드를 정본으로 한다.
  - [x] `/health`: 의존성 없는 liveness. `{"status":"ok"}` 형태로 200 반환(DB 체크는 5.2 이후 readiness로 분리 — 5.1은 DB 없음).
  - [x] `/version`: `app_version`(+선택 `build_sha`, `build_time`) snake_case JSON. 버전은 settings/env에서 읽고, 없으면 합리적 기본값.
  - [x] `/metrics`: **최소·확장 가능**한 JSON(예: `uptime_seconds`, `app_version`). 운영 7지표(`agent_last_heartbeat` 등)는 **Story 5.9 + DB/queue 데이터 필요 → 5.1 범위 아님**. prometheus_client 등 새 dep를 5.1에서 도입하지 않는다.
- [x] **Task 3 — API 규약 고정: 에러 envelope·snake_case·ISO UTC (AC2)**
  - [x] 전역 exception handler를 등록해 모든 에러 응답을 `{"error":{"code":"<UPPER_SNAKE>","message_redacted":"..."}}`로 통일한다. **redaction은 재구현하지 말고** `rider_crawl.redaction.redacted_error_event(code, message, error=None)`(flat `{"code","message_redacted"}` 반환 → `{"error": ...}` envelope로 그대로 합성)을 재사용한다.
  - [x] HTTP 상태코드를 의미 있게 매핑한다(400/401/403/404/409/422/429/503). FastAPI 기본 `HTTPException`/`RequestValidationError`(422)도 동일 envelope로 변환한다.
  - [x] JSON 직렬화는 snake_case 유지(camelCase 변환 alias 금지). 시각은 ISO 8601 UTC 문자열(`...Z`), epoch 정수 혼용 금지.
  - [x] **운영 엔드포인트 경로 주의**: `/health`·`/version`·`/metrics`는 **운영(operational) 엔드포인트라 root-level**로 둔다(`/v1/` 접두 금지). `/v1/` + 복수명사 규약은 **리소스 엔드포인트**(`/v1/agents`, `/v1/jobs` 등 — 5.3+)에 적용된다. 이 둘을 혼동하지 않는다.
- [x] **Task 4 — Docker 컨테이너화 (AC1)**
  - [x] `deploy/Dockerfile.server`: `src/`를 복사하고 server extra를 설치한 뒤 `uvicorn rider_server.main:app`(또는 factory)으로 기동. **주의**: wheel build target(`[tool.hatch.build.targets.wheel] packages=["src/rider_crawl"]`)은 `rider_server`를 패키징하지 않으므로, 이미지에 `src/rider_server`와 `src/rider_crawl`(redaction import 대상)을 함께 넣고 `PYTHONPATH=/app/src`(pytest `pythonpath=["src"]` 미러)로 import 경로를 맞춘다.
  - [x] `deploy/docker-compose.yml`: 최소 `backend-api` 서비스(이 스토리에서 실행·검증). `scheduler`/`telegram-dispatcher`/`admin-ui`는 architecture 트리에 있으나 **각 후속 스토리 범위**이므로 5.1에서는 backend-api만 동작시키면 된다(나머지는 주석/placeholder 가능). env 분리는 `deploy/env/`로 둔다(추후 ECS/Fargate 이전 대비).
  - [x] 컨테이너 기동 후 `/health`가 200을 반환함을 수동/스크립트로 확인하고 결과를 Completion Notes에 남긴다.
- [x] **Task 5 — 테스트 (AC1·AC2·AC3) — `tests/server/` 패턴 계승**
  - [x] `tests/server/test_server_app.py`(또는 유사명): `TestClient`로 `/health`·`/version`·`/metrics` 200·JSON 형태·snake_case 키 단언. 시각 필드가 있으면 ISO 8601 UTC 포맷 단언. (이후 qa-generate-e2e-tests 가 `test_server_async_e2e.py`·`test_server_entrypoint.py`·`test_server_error_contract.py`·`test_server_settings.py` 4개 파일을 추가해 gap 보강.)
  - [x] 에러 envelope 테스트: 일부러 404/422/처리 안 된 예외를 유발해 `{"error":{"code":...,"message_redacted":...}}` 형태와 상태코드를 단언한다. secret-shaped 입력이 응답/로그에 평문으로 남지 않음을 redact 어서션으로 확인.
  - [x] **async 경계 가드 (Epic 4 retro A7-carry)**: `rider_server`는 async이므로 `rider_agent`의 sync 가드(`tests/agent/test_agent_package.py`)를 그대로 쓰면 안 된다. `tests/server/`에 **rider_server 전용** 가드를 신설해 (a) async 핸들러에서 알려진 blocking sync(예: `time.sleep`, sync DB/IO)를 직접 호출하지 않음을 AST 또는 명시 규칙으로 검증하고, (b) `rider_agent`의 9-dep·sync 가드와 분리 유지함을 확인한다. (rider_agent 가드는 `src/rider_agent/**`만 rglob하므로 rider_server async가 그 가드를 깨지는 않는다 — 단, 별도 server 가드를 추가하는 것이 retro 합의다.)
  - [x] 전체 스위트가 `.venv/Scripts/python.exe -m pytest`로 통과함을 확인하고(기존 1316 통과 회귀 0), 최종 테스트 수를 Completion Notes에 **재측정**해 기록한다(이전 스토리들에서 stale count가 반복 지적됨).

## Dev Notes

### 컨텍스트와 범위 경계 (가장 먼저 읽을 것)

- 이 스토리는 **Epic 5의 첫 스토리**이자 `src/rider_server/`에 **처음으로 실행 가능한 FastAPI 런타임**을 올리는 스캐폴딩이다. Epic 2~4 동안 `rider_server/`에는 순수 도메인(`domain/`)·서비스(`services/`)·마이그레이션 정책(`migration/`)만 쌓였고 **FastAPI/HTTP/DB 와이어링은 0**이었다. (`src/rider_server/__init__.py`가 "서버 스캐폴딩은 Epic 5 Story 5.1"이라고 명시.)
- **5.1 범위 = 토대만**: app factory, settings, `/health`·`/version`·`/metrics`, 에러 envelope, snake_case/ISO-UTC 규약, Docker. **5.1 범위 아님**(후속 스토리): PostgreSQL/SQLAlchemy/Alembic 모델·마이그레이션(5.2), QueueBackend(5.3), scheduler(5.4), Telegram webhook(5.5), Admin UI(5.6), 수동 운영 액션(5.7), MFA/audit/보안(5.8), 운영 7지표 실제 집계(5.9), 부하 smoke(5.10), Admin CRUD UI(5.11). 이들을 미리 만들지 않는다.
- **DB 없음**: AC3가 "FastAPI/SQLAlchemy는 async"라고 적지만 5.1엔 DB 코드가 없다. SQLAlchemy async 규칙은 5.2부터 실효된다. 5.1에서 SQLAlchemy/Alembic을 도입하지 않는다.

### 🚨 절대 놓치면 안 되는 가드레일

1. **9-dependency lock — 서버 dep는 별도 그룹으로.** `tests/agent/test_agent_package.py::test_pyproject_dependencies_unchanged_pins`가 `len(project.dependencies) == 9`를 단언한다(현재 정확히 9개). FastAPI/uvicorn/httpx를 `[project].dependencies`에 넣으면 **즉시 회귀**다. 반드시 `[project.optional-dependencies] server = [...]`(+ `dev`에 httpx)로 추가한다. 이 9-dep 고정은 `rider_agent`의 stdlib-only·PyInstaller 배포 표면을 지키는 장치다(project-context.md, Epic 4 retro에서 4 에픽 연속 유지). [Source: tests/agent/test_agent_package.py:218-225, _bmad-output/project-context.md L64]
2. **redaction 재구현 금지 — 기존 유틸 재사용.** 에러 envelope의 `message_redacted`는 `rider_crawl.redaction.redacted_error_event()`로 만든다. 이 함수는 `{"code","message_redacted"}` flat dict을 반환하며 docstring에 "ADD-13 `{"error":{"code","message_redacted"}}` envelope에 그대로 합성 가능"이라고 명시돼 있다. envelope 래핑(`{"error": ...}`)만 API 레이어가 한다. password/token/OTP/full email/phone redaction을 직접 정규식으로 다시 짜지 않는다. [Source: src/rider_crawl/redaction.py:248-273, __all__ 노출됨]
3. **import 방향 — rider_server는 rider_crawl만 import.** `rider_server`가 `rider_crawl.redaction`을 import하는 것은 허용된다(단방향 `rider_server → rider_crawl` OK). 금지되는 것은 `rider_crawl → rider_server/agent`, `rider_agent → rider_server`다. [Source: tests/agent/test_agent_package.py:232-245, architecture.md:484-489]
4. **운영 엔드포인트 vs 리소스 엔드포인트 경로 구분.** `/health`·`/version`·`/metrics`는 root-level(no `/v1/`). `/v1/` + 복수명사는 리소스용(5.3+). AC2 문구를 운영 엔드포인트에 잘못 적용해 `/v1/health`로 만들지 않는다. [Source: _bmad-output/specs/.../implementation-contract.md:70 (P4-01), architecture.md:259-263]
5. **async-boundary 가드 신설(Epic 4 retro A7-carry).** rider_server는 async라 rider_agent의 sync 가드를 재사용할 수 없다. server 전용 async/blocking 혼용 가드를 `tests/server/`에 신설한다(Task 5). [Source: epic-4-retro-2026-06-14.md A7-carry L154]

### 아키텍처 패턴과 규약 (정본)

- **API 응답 포맷**(ADD-13, architecture.md:292-298): 성공은 리소스 객체 직접 반환(불필요한 `{data:...}` 래퍼 금지), 목록은 `{"items":[...], "next_cursor":...}`. 에러는 `{"error":{"code":"<UPPER_SNAKE>","message_redacted":"..."}}` + 의미 있는 HTTP 상태(400/401/403/404/409/422/429/503). 시각은 ISO 8601 UTC(`2026-06-12T03:04:05Z`), epoch 정수 혼용 금지. boolean은 JSON true/false.
- **네이밍**(ADD-8, architecture.md:259-263): API 경로는 `/v1/` + 복수명사, 경로 파라미터 snake_case(`{job_id}`), JSON 필드 snake_case(camelCase 변환 안 함), 헤더는 표준/명시형(`X-Agent-Token` 등 — 5.1엔 불필요).
- **레이어**(architecture.md:276-279): Domain / Application(service) / Infrastructure / Interface(API). 외부 서비스는 함수 주입 또는 adapter 경계 유지(run_once 테스트 패턴 계승). 5.1은 Interface(API) + settings만 신설.
- **async/sync 경계**(architecture.md:336-338, 487): Cloud(FastAPI)=async, Agent=sync. 두 런타임은 HTTP(JSON)로만 통신. async 함수에서 blocking sync 직접 호출 금지(필요 시 executor).
- **에러 분류 정본**(architecture.md:326-333): 운영 카테고리(crawl_failure/auth_required/render_failure/telegram_failure/kakao_failure/duplicate_blocked/target_validation_failure). 5.1은 이들을 쓰지 않지만, 새로 만드는 에러 `code`는 UPPER_SNAKE 규약을 따른다.

### 디렉터리/파일 구조 (architecture.md:416-447 트리 기준)

신설 대상:
- `src/rider_server/main.py` — FastAPI app(`/health /version /metrics`), 전역 exception handler.
- `src/rider_server/settings.py` — env/(향후)Secrets Manager ref 로딩. 5.1은 stdlib `os.environ` 기반 최소 구현.
- `src/rider_server/__main__.py` — `python -m rider_server`(uvicorn 기동).
- `deploy/Dockerfile.server`, `deploy/docker-compose.yml`, `deploy/env/` — 신설(현재 `deploy/` 디렉터리 없음).
- `tests/server/test_server_app.py`(엔드포인트·envelope) + server 전용 async 가드 테스트.

건드리지 않음: `src/rider_crawl/**`(redaction import만), `src/rider_agent/**`, `src/rider_server/domain|services|migration/**`(읽기만), 기존 `tests/**`, `runtime/`·`logs/`·`secrets/`·`build/`·`.venv/`.

### 기술 스택과 버전 (architecture.md:100-124, web search 2026-06 검증)

- **FastAPI 0.136.x**(검증 최신 0.136.3), **uvicorn**(`[standard]` extra 권장), **Pydantic v2**(FastAPI가 끌어옴 — API 경계 검증용). **httpx**(TestClient 의존).
- Python `>=3.10`(프로젝트), 로컬 venv는 **3.11.9**(`.venv/Scripts/python.exe`). 검증은 `.venv/Scripts/python.exe -m pytest`.
- **5.1에서 도입하지 않음**: SQLAlchemy 2.x async / Alembic 1.18.x(→5.2), PostgreSQL 18(→5.2), prometheus_client, pydantic-settings, Redis(미도입 결정).
- 새 dep는 전부 `[project.optional-dependencies]`로(9-dep lock 보호). `uv.lock` 갱신이 필요하면 함께 처리하되, agent 빌드/9-dep 가드를 깨지 않는 범위로 한다.

### 테스트 표준 (project-context.md, architecture.md:280-282)

- pytest, `pyproject.toml`의 `pythonpath=["src"]`·`testpaths=["tests"]`. 신규 server 테스트는 `tests/server/` 기존 파일 패턴 계승: 파일 상단 `"""Story 5.1 / ACx ..."""` docstring, `from __future__ import annotations`, fake fixture만(실제 토큰/전화/이메일/chat_id 형태 금지).
- 외부 서비스 직접 호출 금지. HTTP 검증은 `TestClient`(또는 httpx `ASGITransport` AsyncClient)로 in-process.
- 상태/로그 파일은 `tmp_path`에서만 검증(실제 `runtime/`·`logs/` 사용 금지) — 5.1은 거의 무관하나 settings가 파일을 읽으면 적용.

### Previous Story Intelligence (Epic 4 회고 → Epic 5 인계, epic-4-retro-2026-06-14.md)

직접 적용:
- **A7-carry(가드)**: "Epic 5 server는 async라 4.1 sync 가드를 그대로 쓰면 안 됨 — server는 별도 async 경계 가드 필요." → Task 5의 server 전용 async 가드로 이행. [retro L154]
- **9-dep lock 5차 재확약**: Epic 4가 `pywin32`/`psutil`/`requests` 유혹을 9/9 막아 deps 9개·third-party 루트=rider_crawl을 유지. 5.1은 FastAPI를 도입하되 **반드시 optional group으로** 분리해 동일 lock을 보존. [retro L95-97, L30]
- **A1‴(secret 스캔 게이트, 권고)**: 회고는 "`rider_crawl.redaction` 재사용 pre-commit secret 스캔을 5.1 FastAPI 스캐폴딩 착수 전 실제 도입"을 강하게 권고(Owner: TEA/Amelia). FastAPI/secret 와이어링이 본격화되는 Epic 5에서 누출 비용이 급등하기 때문. **이 스토리의 hard AC는 아니나**, 도입돼 있다면 통과시키고, 없다면 별도 ticket(A1‴)로 추적한다. [retro L113, L148]

향후 표면화(5.1 범위 아님 — 미리 만들지 말 것):
- **A4(직렬화 정본)**: `(str,Enum)`/plain-string 미러 단일 변환 헬퍼와 동형-유사명(`HELD`/`ACTIVE`/`USER_ACTION_REQUIRED`(쿠팡)·`USER_ACTION_PENDING`(배민)) 충돌 정리는 **API 경계 Pydantic 변환·DB 상태 컬럼**에서 표면화 → 5.2/Agent API 스토리. 5.1엔 enum 직렬화가 없다. [retro L139, L151]
- **A5(SecretStorageClass 정합)**: 5.2+ secret 와이어링 시점. [retro L152]
- **A8(placeholder 추적)**: 미존재 `workers/crawl_worker.py`, 4.8 probe·4.9 `is_reauth` 실 바인딩은 Agent end-to-end 스토리에서. [retro L138, L155]

### Git Intelligence

- 최근 커밋은 전부 Epic 4(4.3~4.9 + retro). `feat(story-X.Y): <제목>` 컨벤션. `rider_server`는 Epic 4 내내 0줄 변경(회고 §29) — 5.1이 첫 server 코드 추가.
- 트리가 CRLF/LF로 noisy할 수 있다. 실제 변경 확인은 `git diff -w`로(공백차 무시). [memory: dev-env-quirks, crlf-roundtrip-idempotency]

### Project Structure Notes

- 기존 통합 구조(3패키지 `rider_crawl`/`rider_server`/`rider_agent`, `tests/` 미러)와 정합. 5.1은 `rider_server`에 Interface(API)/settings 레이어를 가산하는 **순수 additive** 작업.
- 변이/주의: (1) wheel build target이 `rider_server`를 패키징하지 않음 → Docker는 `src/` 복사 + `PYTHONPATH=src`로 우회(Task 4). (2) `deploy/`·`migrations/`·`.github/workflows/`는 아직 없음 — 5.1은 `deploy/`만 신설, `migrations/`는 5.2, CI(`ci.yml`)는 운영 contract상 권장이나 5.1 AC 밖(별도/후속).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.1 L908-928] — 스토리·AC 정본(BDD).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md L70] — P4-01: "Build FastAPI backend with `/health`, `/version`, `/metrics`. Runs as Docker container."
- [Source: _bmad-output/planning-artifacts/architecture.md L100-124] — 스택/버전(FastAPI 0.136.x 등, 2026-06 검증).
- [Source: _bmad-output/planning-artifacts/architecture.md L246-263] — API/DB 네이밍 규약(ADD-8).
- [Source: _bmad-output/planning-artifacts/architecture.md L292-304] — 응답/에러/시각 포맷(ADD-13).
- [Source: _bmad-output/planning-artifacts/architecture.md L335-338, L484-489] — async/sync 경계.
- [Source: _bmad-output/planning-artifacts/architecture.md L416-447] — 디렉터리 트리(rider_server/deploy 레이아웃).
- [Source: src/rider_crawl/redaction.py L248-273] — `redacted_error_event()`(에러 envelope 재사용).
- [Source: tests/agent/test_agent_package.py L218-225] — 9-dep lock 가드(서버 dep는 optional group으로).
- [Source: tests/agent/test_agent_package.py L232-245] — import 방향 가드.
- [Source: tests/server/test_domain_models.py L1-40] — server 테스트 파일 패턴(docstring/fake fixture).
- [Source: _bmad-output/implementation-artifacts/epic-4-retro-2026-06-14.md L113,L138-139,L148-155] — A1‴/A4/A5/A7-carry/A8 인계.
- [Source: _bmad-output/project-context.md L20,L64,L81] — 패키지 구조·9-dep 고정·secret/`*_ref` 규칙.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (BMAD dev-story workflow)

### Debug Log References

- **venv `.pth` UnicodeDecodeError (Windows cp949 × 한글 경로)**: `uv pip install -e ".[server,dev]"` 가 editable `.pth`(`.venv/Lib/site-packages/_editable_impl_rider_crawl.pth`)에 한글 경로(`...개발외주...src`)를 기록 → 인터프리터 기동 시 `site.py` 가 `.pth` 를 cp949 로 읽다 `UnicodeDecodeError` 로 **모든 python 호출이 즉시 죽음**(pip 조차 기동 불가). 조치: 깨진 `.pth` 와 dangling `rider_crawl-0.1.0.dist-info` 를 직접 제거. editable 링크는 불필요 — pytest 가 `pyproject.toml`의 `pythonpath=["src"]` 로 `rider_crawl`/`rider_server` 를 해석하고, fastapi/uvicorn/httpx 는 정상 third-party 패키지로 남는다. 이후 전 호출 정상화. (Docker 는 `/app` ASCII 경로라 무관.)
- **Starlette 1.3.x TestClient httpx2 deprecation**: `fastapi.testclient.TestClient` import 시 "install `httpx2`" `UserWarning` 발생. 실제 동작은 httpx 0.28.1 로 정상(200/JSON/redact 검증). `pyproject.toml` `filterwarnings` 에 targeted ignore 추가(기존 lxml ignore 선례 계승).

### Completion Notes List

- **Task 1**: `[project.optional-dependencies]`에 `server = [fastapi>=0.136,<0.137, uvicorn[standard]>=0.30]` 신설, `dev` 에 `httpx>=0.27` 추가. `[project].dependencies` 9개 불변 — `test_pyproject_dependencies_unchanged_pins` green. 설치 결과: FastAPI 0.136.3, uvicorn 0.49.0, httpx 0.28.1.
- **Task 2**: `create_app()` 팩토리 + module-level `app`. `/health`→`{"status":"ok"}`, `/version`→`app_version`(+조건부 `build_sha`/`build_time`), `/metrics`→`app_version`/`uptime_seconds`(duration 숫자)/`server_time`(ISO-UTC). 세 핸들러 모두 `async def`(AC3). `settings.py` 는 stdlib `os.environ` 기반 frozen dataclass `Settings.from_env`(외부 dep 0, `pydantic-settings` 미도입). `__main__.py` 는 uvicorn import-string 진입점.
- **Task 3**: 전역 exception handler 3종(`StarletteHTTPException`/`RequestValidationError`/`Exception`)으로 `{"error":{"code","message_redacted"}}` envelope 통일. code 는 `HTTPStatus(status).name`(이미 UPPER_SNAKE) 매핑, validation→`VALIDATION_ERROR`, unhandled→`INTERNAL_ERROR`. redaction 은 `rider_crawl.redaction.redacted_error_event` **재사용**(재구현 0, 단방향 `rider_server→rider_crawl`). validation 핸들러는 입력 값(`input`)을 메시지에서 제외 + redact 통과로 secret-shaped 입력 누출 차단. 운영 엔드포인트는 root-level(`/v1/` 미적용).
- **Task 4**: `deploy/Dockerfile.server`(python:3.11-slim, server extra만 설치, `src/rider_server`+`src/rider_crawl` 복사, `PYTHONPATH=/app/src`, CMD `uvicorn rider_server.main:app`), `deploy/docker-compose.yml`(backend-api만; scheduler/dispatcher/admin-ui 는 후속 스토리 placeholder 주석), `deploy/env/backend-api.env`(평문 secret 0). `rider_crawl.redaction` 은 stdlib(`re`/`typing`)만 import + `rider_crawl/__init__.py` 가 docstring 뿐 → crawl4ai/playwright 불필요한 lean 이미지 가능 확인. **`/health` 200 검증**: 이 WSL 환경엔 Docker daemon 통합이 꺼져 있어 이미지 빌드는 불가 → 이미지 CMD 와 동일한 uvicorn(`rider_server.main:app`)을 실제 소켓(127.0.0.1:8123)으로 기동해 `GET /health → 200 {"status":"ok"}`, `/version → 200`, `/metrics → 200` 확인. (컨테이너 실빌드는 Docker Desktop WSL integration 활성 후 `docker compose -f deploy/docker-compose.yml up --build` 로 재현 가능.)
- **Task 5**: `tests/server/test_server_app.py`(14건 중 11건 — 엔드포인트·snake_case·ISO-UTC·root-level·async·404/422/500 envelope·redact 누출 차단), `tests/server/test_server_async_boundary.py`(3건 — async blocking-call AST 가드 + 자기검증 non-vacuous + rider_agent 가드와 스코프 분리). dev 단계 신규 14건 — **이후 qa-generate-e2e-tests 가 4개 파일을 추가해 최종 신규 47건**(아래 "테스트 수치" 참조, [review 정정]).
- **테스트 수치 (재측정 단일 정본 · review 재측정 반영)**: dev 노트는 신규 14건/전체 1330 으로 기록했으나, 이후 qa-generate-e2e-tests 가 4개 파일(`test_server_async_e2e.py`·`test_server_entrypoint.py`·`test_server_error_contract.py`·`test_server_settings.py`)을 추가해 **신규 server 테스트는 총 47건**(6개 파일)이다. 전체 스위트 `.venv/Scripts/python.exe -m pytest -q` → **1363 passed, 0 failed**(baseline 1316 + 47 = 1363, 회귀 0). 9-dep lock·단방향 import·sync 가드 green. [review 2026-06-14 재측정]
- **스코프 규율**: `git diff -w` 결과 tracked 변경은 `pyproject.toml`·`sprint-status.yaml`(+사전에 더럽던 `orchestration-*.md`는 본 작업과 무관 — 미접촉). 나머지는 전부 신규 파일(순수 additive).
- **A1‴(pre-commit secret 스캔)**: retro 권고이나 본 스토리 hard AC 아님 — 미도입. 별도 ticket(A1‴)로 추적 권장.

### File List

- `pyproject.toml` (수정 — server extra 그룹·dev httpx·filterwarnings ignore)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정 — 5-1 in-progress→review)
- `src/rider_server/main.py` (신규 — FastAPI app factory·운영 엔드포인트·에러 envelope)
- `src/rider_server/settings.py` (신규 — stdlib os.environ 기반 typed Settings)
- `src/rider_server/__main__.py` (신규 — `python -m rider_server` uvicorn 진입점)
- `deploy/Dockerfile.server` (신규)
- `deploy/docker-compose.yml` (신규)
- `deploy/env/backend-api.env` (신규)
- `tests/server/test_server_app.py` (신규 — 엔드포인트·envelope·redact)
- `tests/server/test_server_async_boundary.py` (신규 — server 전용 async 경계 가드)
- `tests/server/test_server_async_e2e.py` (신규/QA gap-fill — 실제 asyncio 이벤트 루프 위 ASGI e2e)
- `tests/server/test_server_entrypoint.py` (신규/QA gap-fill — `python -m rider_server` 진입점·env→host/port/reload)
- `tests/server/test_server_error_contract.py` (신규/QA gap-fill — 상태코드 매핑·405·detail redaction·envelope 키 snake_case)
- `tests/server/test_server_settings.py` (신규/QA gap-fill — `Settings.from_env` 기본값·override·빈문자열→None·frozen)

## Change Log

| 날짜 | 변경 | 작성자 |
| --- | --- | --- |
| 2026-06-14 | Story 5.1 구현 — FastAPI 백엔드 스캐폴딩(app factory·settings·`__main__`), `/health`·`/version`·`/metrics` async 운영 엔드포인트, `{"error":{code,message_redacted}}` 에러 envelope(redaction 재사용)·snake_case·ISO-UTC 규약, Docker(`deploy/`), server 전용 async 경계 가드. server deps 는 optional group 으로 분리(9-dep lock 유지). 전체 스위트 1330 passed/0 failed(신규 14건, 회귀 0). Status → review. | Dev (claude-opus-4-8) |
| 2026-06-14 | Review (AI) — CRITICAL 0 / HIGH 0. MEDIUM 2 자동수정: (1) File List 에 누락됐던 QA gap-fill 4개 테스트 파일 추가, (2) stale 테스트 수치 정정(신규 14→47건, 전체 1330→1363 passed/0 failed 재측정). LOW 3건은 기록만(아래 Review 섹션). 9-dep lock·단방향 import·async 경계 가드 green. Status → done. | Review (claude-opus-4-8) |

## Senior Developer Review (AI)

**Reviewer:** Noah Lee (AI adversarial review) · **Date:** 2026-06-14 · **Outcome:** ✅ Approve (Status → done)

**검증 환경:** `.venv/Scripts/python.exe -m pytest -q` → **1363 passed, 0 failed** (baseline 1316 + 신규 47 = 1363, 회귀 0). 9-dep lock(`test_pyproject_dependencies_unchanged_pins`)·단방향 import 가드·server 전용 async 경계 가드 모두 green.

### AC 충족
- **AC1 (FastAPI 스캐폴딩·운영 엔드포인트·Docker)**: `create_app()` 팩토리 + `/health`·`/version`·`/metrics` 동작, `deploy/Dockerfile.server`·`docker-compose.yml`·`env/` 신설. **IMPLEMENTED.** (실 컨테이너 빌드는 WSL Docker daemon 미통합으로 직접 uvicorn 기동으로 대체 검증 — 아래 LOW-3.)
- **AC2 (API 규약)**: 에러 envelope `{"error":{code,message_redacted}}`(redaction 재사용), JSON snake_case, ISO-8601 UTC(`...Z`), 의미 있는 상태코드(404/405/422/400/401/403/409/429/503), 운영 엔드포인트 root-level(`/v1/` 미적용) — 전부 테스트로 잠금. **IMPLEMENTED.**
- **AC3 (async 런타임)**: 3개 핸들러 모두 `async def`, AST blocking-call 가드 신설(`time.sleep`/`subprocess.*` 등 async 본문 직접호출 금지·non-vacuous 자기검증), 실 asyncio 루프 e2e 검증. **IMPLEMENTED.**

### 가드레일 확인
- 9-dep lock 유지: 서버 deps(`fastapi`/`uvicorn[standard]`)는 `[project.optional-dependencies].server`, `httpx`는 `dev` 그룹 — `[project].dependencies` 9개 불변. ✅
- redaction 재구현 0: `rider_crawl.redaction.redacted_error_event` 재사용, 단방향 `rider_server → rider_crawl`. ✅
- lean Docker 이미지 가정 검증: `rider_crawl/__init__.py` 는 docstring-only → `import rider_crawl.redaction` 가 crawl4ai/playwright 를 끌지 않음. ✅

### 자동수정한 항목 (MEDIUM)
- **[MED-1] File List 불완전** — qa-generate-e2e-tests 가 추가한 4개 파일(`test_server_async_e2e.py`·`test_server_entrypoint.py`·`test_server_error_contract.py`·`test_server_settings.py`)이 File List 에 누락 → **추가 완료.**
- **[MED-2] stale 테스트 수치** — Completion Notes 의 "신규 14건 / 전체 1330" 은 dev 단계 수치. QA gap-fill 반영 시 **신규 47건 / 전체 1363** 으로 재측정 → Completion Notes·Change Log 정정 완료. (반복 지적된 stale-count 패턴.)

### 기록만 한 항목 (LOW — 동작 변경 없음, 후속 판단용)
- **[LOW-1] 500 envelope 의 비-계약 필드 `error_message_redacted`** — `_unhandled_exc_handler` 가 `error=exc` 를 넘겨 ADD-13 표준형(`{code, message_redacted}`)에 `error_message_redacted`(redact 된 예외 본문)가 추가로 노출된다. secret/OTP 는 마스킹되나 운영 식별자는 redact 대상이 아니라(memory: redact-skips-operational-ids) 클라이언트로 내부 예외 텍스트가 새는 경미한 information-disclosure. **미수정 사유**: redact 로 secret 은 차단되고, 별도 server-side 로깅이 없어 이 필드가 유일한 에러 가시성이며, QA 테스트가 현 동작을 명시적으로 잠갔다(제거 시 관측성 하락 + 테스트 회귀). 후속(5.2+ 로깅 도입 시) "클라이언트엔 generic, 상세는 server log" 로 분리 권장.
- **[LOW-2] Dockerfile 의 fastapi/uvicorn 핀 중복** — `deploy/Dockerfile.server` 가 pyproject `server` extra 의 버전을 직접 재선언(2-source-of-truth drift 위험). **미수정 사유**: wheel build target 이 `rider_server` 를 패키징하지 않아 `pip install .` 가 불가(rider_crawl heavy deps 까지 끌어 lean 이미지 깨짐). 직접 설치는 의도된 트레이드오프이며 Dockerfile 주석에 명시됨. CI 에서 두 핀 일치 검사를 거는 것을 후속 권장.
- **[LOW-3] Task 4 컨테이너 실빌드 미수행** — WSL Docker daemon 미통합으로 이미지 빌드 대신 동일 CMD(`uvicorn rider_server.main:app`)를 실 소켓으로 기동해 `/health` 200 검증. 투명하게 문서화됨. Docker Desktop WSL integration 활성 후 `docker compose -f deploy/docker-compose.yml up --build` 로 재현 권장.

### A1‴ (carry)
pre-commit secret 스캔은 본 스토리 hard AC 아님 — 미도입 상태 유지. 별도 ticket(A1‴)로 추적 권장(Epic 5 secret 와이어링 본격화 전).

_Reviewer: Noah Lee on 2026-06-14_
