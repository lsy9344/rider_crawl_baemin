# Test Automation Summary — Story 4.7 (Agent 실행 조건 + 재부팅 후 자동 시작)

**Workflow:** bmad-qa-generate-e2e-tests · **Role:** QA 자동화 엔지니어 (테스트 생성만)
**Date:** 2026-06-14 · **Framework:** pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`)

## Feature Under Test

Story 4.7 산출물 — 모두 순수 동기 · 주입 가능 · Windows-gated lazy (실 OS 미호출):

- `src/rider_agent/autostart.py` — launch-command 합성 · 노드 역할 resolver · interactive-session probe/게이트 · autostart 등록/해제/조회 primitive
- `src/rider_agent/__main__.py` — 얇은 `autostart` 서브커맨드 (additive)
- `src/rider_agent/job_loop.py` — `run_agent` interactive-session 게이트 (additive, `session_probe=None`이면 무회귀)

> API/UI 테스트는 **해당 없음**: 본 기능은 서버를 호출하지 않는 로컬 OS 통합 primitive + CLI 서브커맨드다(엔드포인트·UI 없음). "E2E" 등가물은 `run_agent` 세션 게이트 통합 테스트로 커버한다. 전부 주입 fake(`writer`/`runner`/`session_probe`/`remover`) + `tmp_path`/fake environ 로 결정적 검증 — 비-Windows CI 에서도 통과(import-safety).

## Generated Tests

### tests/agent/test_autostart.py (단일 파일, 평면)

- **기존(dev-story):** 28건
- **QA 갭 보완 추가:** 13건 → **총 41건**

#### 추가된 갭 보완 테스트 (13건)

| # | 테스트 | 메운 갭 | AC |
|---|---|---|---|
| 1 | `test_startup_register_rewrites_on_changed_command` | 멱등의 **반대 분기** — 커맨드 변경 시 재쓰기(기존엔 "같으면 0"만) | AC2 |
| 2 | `test_startup_register_default_writer_and_remover_roundtrip` | 주입 미지정 시 **기본** `_default_startup_writer`/`_default_startup_remover` 경로(전부 fake만 썼음) | AC2 |
| 3 | `test_startup_cmd_quotes_paths_with_spaces_and_stays_idempotent` | 공백 경로 `list2cmdline` 인용(Dev Notes 열린질문 #3) + **CRLF round-trip 멱등 회귀 가드**(Debug Log) | AC2 |
| 4 | `test_register_autostart_unknown_method_raises` | `register_autostart` 미지원 method → `ValueError` 분기 | AC2 |
| 5 | `test_unregister_autostart_unknown_method_raises` | `unregister_autostart` 미지원 method → `ValueError` 분기 | AC2 |
| 6 | `test_is_autostart_registered_unknown_method_raises` | `is_autostart_registered` 미지원 method → `ValueError` 분기 | AC2 |
| 7 | `test_default_session_probe_non_windows_returns_true` | 무주입 시 기본 probe 경로(비-Windows → `True`, 실 `ctypes` 미호출) — 기존엔 주입 probe만 | AC1 |
| 8 | `test_default_runner_non_windows_raises` | 기본 `schtasks` runner 의 **Windows-gating** 불변식(비-Windows → `RuntimeError`, import-safety) | AC1 |
| 9 | `test_run_autostart_status_reports_not_registered` | 서브커맨드 `--status` **미등록** 분기("not-registered") | AC2 |
| 10 | `test_run_autostart_unregister_reports_not_registered_when_absent` | 서브커맨드 `--unregister` **부재** 분기("not-registered") | AC2 |
| 11 | `test_run_autostart_register_forwards_task_scheduler_method` | `--method task_scheduler` 가 primitive 로 전달됨(기존엔 기본 startup만) | AC2 |
| 12 | `test_autostart_requires_an_action_flag` | 상호배타 required action 그룹 → 미지정 시 `SystemExit` | AC2 |
| 13 | `test_run_agent_session_gate_irrelevant_for_crawler_only` | **AC3 통합** — crawler-only 노드는 Session 0 여도 게이트 무관(워커는 역할 기준 미기동, fail-closed 사유 surfacing 안 함) | AC3 |

## Coverage

- **autostart.py 공개 표면:** launch-command(dev/frozen/server_url/secret-free), 노드 역할 resolver 3종, 세션 probe/게이트(주입 + 기본 Windows-gated 양쪽), 등록/해제/조회 × {startup, task_scheduler} × {happy, 멱등 동일/변경, 미존재, 미지원 method} — **전 분기 커버**
- **`__main__` autostart 서브커맨드:** 라우팅 + register/status/unregister × {등록/미등록} + method 전달 + required-action — **커버**
- **`run_agent` 세션 게이트(통합):** Session 0 차단(fail-closed) · 무주입 무회귀 · token 누출 0 · **crawler-only 무관(AC3)** — **커버**
- **회귀 가드:** 4.1 패키지 가드(third-party root, sync, 단방향, deps 9핀, `__main__` 배너/tkinter 0), `test_job_loop`·`test_kakao_sender`·`test_heartbeat` 전부 green

## Results

- `tests/agent/test_autostart.py`: **41 passed**
- 4.1 가드 + 인접 스위트(`test_agent_package`·`test_job_loop`·`test_kakao_sender`·`test_heartbeat`·`test_autostart`): **171 passed**
- **전체 스위트: 1249 passed** (baseline 1236 + 신규 13, 회귀 0)

## Leak / Scope Guards

- 신규 테스트는 실 `schtasks`·실 세션 API(`ctypes`)·실 `%APPDATA%` Startup 폴더를 **호출하지 않는다**(주입 fake + `tmp_path` + `monkeypatch sys.platform`)
- 가짜 식별자만(`py-fake`/`agent-fake.exe`/`https://srv-fake.example`/`agtok-fake-...`) — 실 token/chat_id/휴대폰/이메일/OTP 0
- 등록 산출물·로그·서브커맨드 출력에 token/`--code`/raw 경로 0건 단언
- **본 QA 실행은 테스트 파일만 추가**(`tests/agent/test_autostart.py`) — `src/` 및 다른 산출물은 무변경

## Next Steps

- CI 에서 테스트 실행(비-Windows 에서도 import-safe — 통과 확인됨)
- 후속 4.8/4.9 가 노드 역할/세션 사유를 늘릴 때 평문 상수 유지(enum/"정확히 N" lock 금지)
