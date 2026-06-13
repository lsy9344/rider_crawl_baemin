# Test Automation Summary — Story 5.1 (FastAPI 백엔드 스캐폴딩 · 운영 엔드포인트)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest

## 컨텍스트

스토리 5.1은 `src/rider_server/` 에 처음 올라가는 **실행 가능한 FastAPI 백엔드 스캐폴딩**이다 —
app factory(`main.py`) · stdlib typed settings(`settings.py`) · `python -m rider_server`
진입점(`__main__.py`) · 운영 엔드포인트 `/health`·`/version`·`/metrics`(root-level) · 전역 에러
envelope(`{"error":{"code","message_redacted"}}`, redaction 재사용) · snake_case/ISO-UTC 규약 · Docker.
UI 없음(HTTP/JSON 백엔드). 검증은 in-process(`TestClient` / `httpx.AsyncClient`+`ASGITransport`)로
외부 소켓·서비스 미호출, fake 값만(secret-shaped 문자열은 redaction 검증용).

dev-story가 `tests/server/`에 14건(`test_server_app.py` 11 + `test_server_async_boundary.py` 3)을
만든 상태에서, AC 대비 **테스트가 비어 있던 격차**를 찾아 자동 보강(auto-apply)했다.
**프로덕션 소스 0줄 변경**(QA 워크플로는 테스트만 생성, 새 의존성 0).

## Generated / 보강 테스트 (신규 33건, 4개 파일)

### 1) `tests/server/test_server_error_contract.py` (22건) — AC2 에러 규약 심화
- 405 Method Not Allowed envelope — `/health`·`/version`·`/metrics`(GET 전용) POST → `METHOD_NOT_ALLOWED` (3 params)
- HTTP 상태코드 → UPPER_SNAKE code 매핑을 **실제 exception handler 경로**로 검증 — 400/401/403/404/409/422/429/503 (8 params) + 상태코드 보존
- `HTTPException.detail` 의 secret-shaped 문자열 redact(응답 평문 미노출)
- 500 envelope = 일반 메시지(`internal server error`) + redact 된 `error_message_redacted` 분리
- 에러 envelope 키 전부 snake_case
- `HTTPStatus.name` ↔ 기대 매핑 자기검증(non-vacuous, 8 params)

### 2) `tests/server/test_server_settings.py` (5건) — Task 2 settings
- `from_env` 기본값(빈 mapping) / 전체 env 읽기 / 빈 문자열→None 정규화 / 부분 build 메타 독립 정규화 / `Settings` frozen 불변성

### 3) `tests/server/test_server_entrypoint.py` (3건) — Task 2 진입점
- `main()` → `uvicorn.run("rider_server.main:app", ...)` import-string·host/port 기본값
- HOST/PORT env override
- reload 분기: development → True, production → False
- (`uvicorn.run` monkeypatch — 실 서버 미기동 / `__main__` 임포트는 함수 내부로 미뤄 runpy 경고 회피)

### 4) `tests/server/test_server_async_e2e.py` (3건) — AC3 async 런타임 e2e
- 실제 asyncio 이벤트 루프(`asyncio.run` + `httpx.AsyncClient`/`ASGITransport`)에서 `/health` 200 · `/version` 200 · 404 envelope 유지
- (정적 `iscoroutinefunction` 단언을 넘어 async 런타임을 end-to-end 실증; `pytest-asyncio` 미도입이라 `asyncio.run` 사용)

## 발견한 커버리지 격차 (모두 auto-apply)

| # | 격차 | 보강 | AC |
|---|------|------|----|
| G1 | HTTP 상태 매핑이 404 1건 + 순수 `HTTPStatus.name`만(실 handler 미경유) | 400~503 8건 실 핸들러 검증 | AC2 |
| G2 | 405 Method Not Allowed 경로 미검증 | 운영 3엔드포인트 POST → 405 envelope | AC2 |
| G3 | `HTTPException.detail` redaction 경로 미검증(기존엔 500/validation만) | detail secret redact 단언 | AC2 |
| G4 | `settings.py` 직접 테스트 0건(엔드포인트 경유 간접만) | from_env 5건(기본/override/빈값정규화/frozen) | Task2 |
| G5 | `__main__.main()` 진입점·reload 분기 미검증 | uvicorn 와이어링 3건 | Task2 |
| G6 | AC3 async 가 정적 단언만(실 이벤트 루프 미경유) | ASGI async e2e 3건 | AC3 |

기존 14건(유지): `/health`·`/version`(full/unset)·`/metrics` shape, root-level(no `/v1/`),
async 핸들러 정적 단언, 404/422(validation)/500 envelope·redact, async-boundary AST 가드 3.

## Coverage

| Acceptance Criterion | 커버 |
|---|---|
| AC1 — `/health`·`/version`·`/metrics` 동작·Docker | ✅ 기존 5 + 405·async e2e (Docker 실빌드는 WSL 통합 비활성 → dev 가 실소켓 `/health` 200 확인, 자동화 밖) |
| AC2 — 에러 envelope·UPPER_SNAKE·snake_case·ISO-UTC·root-level·의미있는 status | ✅ 기존 6 + error_contract 22(405·8상태·detail redact·500·snake_case 키) |
| AC3 — FastAPI async·blocking sync 직접호출 금지 | ✅ 기존 3 정적/AST + async_e2e 3 실 이벤트 루프 |

모듈 커버: `main.py`(3 핸들러 + 3 exception handler) · `settings.py`(from_env 전 분기) · `__main__.py`(reload 분기 포함).

## Validation Results (단일 정본)

운영 venv `.venv/Scripts/python.exe -m pytest`:

- 신규 4파일 `-v` → **33 passed**
- 전체 스위트 `-q` → **1363 passed, 0 failed** (보강 전 baseline 1330 + 신규 33, 순수 additive·회귀 0)
- 가드 green: 9-dep lock(`test_pyproject_dependencies_unchanged_pins`)·단방향 import·rider_agent sync 가드·rider_server async 경계 가드.

## 범위/누출 검증

- 이번 QA 라운드 변경은 `tests/server/` 신규 4파일뿐. 프로덕션 코드(`src/rider_server/**`·`src/rider_crawl/**`)·`pyproject.toml` **0줄 변경**(`git status`: 신규 테스트는 `??`, src 트리 `M` 없음 / `pyproject.toml` 의 server extra 는 dev-story 선행 변경이며 본 라운드 미접촉).
- 새 의존성 0: `TestClient`/`httpx` 는 기존 `dev` extra, async 는 stdlib `asyncio` — 9-dep lock 불변.
- 누출 가드: secret-shaped 입력(`token=supersecret999`/`password=hunter2`)이 응답·envelope 에 평문 0건임을 단언.

## 체크리스트 결과(`checklist.md`)

- [x] API 테스트 생성(운영 엔드포인트·에러 규약·settings·진입점) / E2E(UI 없음 — HTTP/JSON 백엔드, async ASGI e2e 로 대체)
- [x] 표준 프레임워크 API(pytest, `TestClient`, `httpx.AsyncClient`/`ASGITransport`, `monkeypatch`, `pytest.raises`)
- [x] happy path(200·snake_case·ISO-UTC) + 임계 케이스(404/405/422/500·8 상태매핑·redact·reload 분기)
- [x] 전 테스트 통과(33/33, 전체 1363) / 의미 있는 단언 / 명확한 docstring / 순서 독립(각 케이스 자체 app·settings·monkeypatch)
- [x] no hardcoded waits(async 는 `asyncio.run`, sleep 없음) / 요약 작성 · 적정 위치(`tests/server/`) 저장 · 커버리지·수치 명시

## Next Steps

- CI(`ci.yml`)에서 server extra(`uv pip install -e ".[server,dev]"`) 설치 후 본 스위트 실행(5.1 AC 밖 권고, retro A1‴ secret 스캔 게이트와 함께).
- 리소스 엔드포인트(`/v1/agents` 등, 5.3+) 도입 시 `/v1/` 규약·페이지네이션(`{items,next_cursor}`) 계약 테스트 추가.
- Docker 컨테이너 실빌드(`docker compose -f deploy/docker-compose.yml up --build`) `/health` 200 은 WSL Docker 통합 활성 환경에서 통합 테스트로 승격 고려.
