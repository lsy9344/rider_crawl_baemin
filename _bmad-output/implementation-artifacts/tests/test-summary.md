# Test Automation Summary — Story 4.1 (rider_agent 패키지 토대 + 재사용 seam)

작성: 2026-06-13 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest

## 컨텍스트

스토리 4.1은 UI/HTTP 엔드포인트가 없는 **패키지 토대 + 단방향 import seam**이라 API/E2E(브라우저) 테스트 대상이 아니다. 프로젝트의 기존 pytest 프레임워크(`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`)로 **AST 가드 + 서브프로세스 import-safety + 단위 계약** 형태의 테스트를 생성했다. 외부(브라우저/네트워크/Kakao/Gmail) 미호출, 가짜 값만 사용.

dev-story가 `tests/agent/test_agent_package.py`에 9건을 만든 상태에서, AC/Task/Dev-Notes가 **명시적으로 요구하지만 테스트가 비어 있던 격차**를 찾아 자동 보강(auto-apply)했다.

## Generated / 보강 테스트

`tests/agent/test_agent_package.py` (+5건, 기존 9 → **14건**). 신규 헬퍼 `_abs_import_modules`(서브모듈 단위 import-edge), `_run_python`(깨끗한 서브프로세스).

| # | 테스트 | 커버한 격차 | 근거 |
|---|--------|-------------|------|
| Gap1 | `test_main_does_not_import_tkinter_or_legacy_ui` | `__main__`이 `tkinter`/`rider_crawl.ui`/`rider_crawl.app`을 import하지 않음(부작용 0의 정적 근거). 기존엔 exit 0만 보고 레거시 UI 미배선을 단언 안 함 | AC1, Task 3 |
| Gap2 | `test_reuse_seam_is_import_safe_no_heavy_deps` | reuse seam을 eager import해도 `crawl4ai`/`playwright`/`pyautogui`/`pywinauto`/`pyperclip`/`googleapiclient`가 `sys.modules`에 안 올라옴(깨끗한 서브프로세스). Dev Notes가 "`python -m rider_agent` 성공의 관건"으로 지목한 import-safety가 미검증이었음 | AC1, Dev Notes(재사용 seam 설계) |
| Gap3 | `test_import_rider_agent_does_not_eager_load_reuse_seam` | `import rider_agent`가 `rider_agent.reuse`를 eager-load하지 않음(가벼운 `__init__`) | Task 1 |
| Gap4 | `test_main_returns_zero_and_prints_sync_banner` | `main()` 직접 호출이 `0` 반환 + sync 배너(버전·"sync runtime") 출력. runpy/subprocess만 있던 것을 단위 계약으로 보강(실패 위치 좁힘) | AC1 |
| Gap5 | `test_reuse_all_names_are_resolvable` | `reuse.__all__`의 모든 이름이 실제 attribute로 해석됨(re-export drift 가드) | AC1·재사용 완전성 |

기존 9건(유지): 패키지/seam import, `python -m rider_agent` exit 0(runpy+subprocess), 재사용 identity `is` 단언, sync AST 가드, third-party root==`{rider_crawl}`, pyproject 핀 유지, 단방향 import(crawl→agent 0 / agent→server 0).

## Coverage

| Acceptance Criterion | 커버 |
|---|---|
| AC1 — 패키지·재사용 import·`python -m` 실행·부작용 0 | ✅ 기존 4건 + Gap1(레거시 UI 미배선)/Gap2(import-safety)/Gap3(가벼운 `__init__`)/Gap4(`main()` 단위)/Gap5(re-export 완전성) |
| AC2 — 새 프레임워크 0·핀 유지 | ✅ 기존 2건(third-party root==`{rider_crawl}`, playwright/crawl4ai 핀) |
| AC3 — sync 자기 코드·단방향 import | ✅ 기존 3건(AST sync 가드, crawl→agent 0, agent→server 0) |

import-safety가 의존하는 lazy 경계를 소스로 직접 확인 후 단언값 확정: `rider_crawl`의 `crawler/parser/platforms/coupang/message/auth/messengers`는 module-level에서 heavy dep 미import이고, `pyautogui`/`pywinauto`/`pyperclip`/`googleapiclient`/`crawl4ai`는 함수 내부 lazy import.

## Validation Results (단일 정본)

운영 venv `.venv/Scripts/python.exe -m pytest`:

- `tests/agent/test_agent_package.py -q` → **14 passed**
- 전체 스위트 `-q` → **1008 passed, 0 failed** (기존 1003 + 신규 5, 순수 additive·회귀 0)

## 범위/누출 검증

- 보호 경로 `git diff -w` 0줄: `src/rider_crawl`·`src/rider_server`·`src/rider_agent`·`pyproject.toml`·`rider_crawl_onefile.spec`·`_bmad-output/project-context.md` 무변경. 변경은 `tests/agent/test_agent_package.py`에 테스트 추가뿐.
- 누출 grep(봇토큰 `\d{6,}:[\w-]{30,}`/`chat_id=`/휴대폰/이메일/OTP) → 신규 테스트에 0건.
- 역방향 의존(`rider_crawl`이 `rider_agent` import) → 0건.

## 체크리스트 결과(`checklist.md`)

- [x] API 테스트(해당 없음 — 엔드포인트 없는 토대 스토리) / E2E 테스트(해당 없음 — UI 없음); 대신 패키지·실행·import-edge 테스트
- [x] 표준 프레임워크 API(pytest, `ast`, `subprocess`, `capsys`)
- [x] happy path(`python -m rider_agent` exit 0, `main()` 0 반환) + 임계 케이스(import-safety 위반·레거시 UI 배선·re-export drift를 실패로 잡음)
- [x] 전 테스트 통과 / 의미 있는 단언(AST·`sys.modules`) / 명확한 설명 / 하드코딩 sleep 없음 / 순서 독립
- [x] 요약 작성 · 적정 위치 저장 · 커버리지 명시

## Next Steps

- 4.2~4.4(등록/heartbeat/job 루프)에서 `reuse` seam을 실제 호출하는 워커가 생기면 **서버 stub/mock 동작 검증** 형태로 테스트 확장(epic-3-retro 108).
- import-safety 가드의 heavy-dep 목록은 후속 워커가 새 외부 의존을 lazy로 추가하면 함께 갱신.
