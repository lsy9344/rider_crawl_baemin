---
baseline_commit: d4211c3074690e740a66e02f1e546b4cdb1d1e89
---

# Story 1.2: pytest 기준선 실행과 결과 분류·보관

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want 리팩토링 시작 시점의 전체 pytest 결과를 실행하고 통과/실패/스킵으로 분류해 `docs/qa/`에 보관하고,
so that 이후 단계에서 어떤 테스트가 새로 깨졌는지(회귀)를 이 기준선과 비교해 판단할 수 있다.

> 본 스토리는 Epic 1(기준선 안전망, P0)의 두 번째 스토리이며, spec 단계 **P0-03**을 구현한다. Story 1.1처럼 **코드 기능 변경이 아니라 운영 기준선(pytest 결과)을 고정하는 절차/산출물 스토리**다. 산출물은 ① `docs/qa/` 아래 분류된 pytest 기준선 리포트(markdown), ② 재현·비교용 머신리더블 raw 결과(JUnit XML 또는 `-v` 텍스트 로그), ③ "이미 실패하던 테스트" vs "리팩토링이 깨면 안 되는 통과 테스트" 분류 기록이다. **`src/rider_crawl/` 제품 코드와 `tests/` 테스트는 추가·수정하지 않는다 — 기존 스위트를 실행해 결과를 기록만 한다.**

## Acceptance Criteria

**AC1 — 전체 pytest 실행과 분류 리포트 생성 (P0-03)**
1. **Given** 기존 `tests/` 구조와 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`)이 있을 때 **When** 저장소 루트에서 전체 pytest를 1회 실행하면 **Then** 통과(passed)/실패(failed)/스킵(skipped)이(필요 시 xfailed/xpassed/error 포함) 분류·집계된 테스트 리포트가 `docs/qa/` 아래에 저장된다.
2. **And** 리포트에 **실행 일시(KST), Python 버전, pytest 버전, 실행 환경(OS/런타임), 실행 명령, 기준선 commit SHA(전체)·브랜치**가 기록된다.
3. **And** 재현·향후 비교를 위한 머신리더블 raw 결과(JUnit XML 또는 per-test outcome가 보이는 `-v` 텍스트 로그)가 `docs/qa/` 아래에 함께 저장된다.

**AC2 — 회귀 비교 기준선으로서의 사용성 (NFR-20, FR-2)**
4. **Given** 기존 테스트가 기준선의 일부로 취급될 때 **When** 리팩토링 이후 동일 명령으로 같은 테스트를 다시 실행하면 **Then** 기준선에서 통과하던 테스트는 계속 실행 가능해야 하며(수집 가능·이름 보존), 기준선 리포트의 통과 집합은 "리팩토링이 깨면 안 되는 must-not-break 집합"으로 명시된다.
5. **And** 리포트에는 향후 비교 방법(재실행 명령 + 기준선 결과와의 diff/대조 방법 + 안정적으로 비교 가능한 test nodeid 목록 형식)이 기록되어, 새로 실패한 테스트를 회귀 후보로 식별할 수 있다.

**AC3 — 이미 실패하던 테스트와 통과 테스트 구분 (분류)**
6. **Given** 일부 기존 테스트가 이미 실패/스킵 상태일 수 있을 때 **When** 리포트를 분류하면 **Then** "기준선에서 **이미 실패(또는 환경상 skip)하던** 테스트"와 "리팩토링이 깨면 안 되는 **통과** 테스트"가 명확히 구분되어 기록된다.
7. **And** 실패가 0건이면 "기준선 all-green"으로, skip이 있으면 각 skip의 사유(환경 의존 등)가 함께 기록되어, 향후 새 실패와 기준선 상태를 혼동하지 않는다.

## Tasks / Subtasks

- [x] **Task 1 — 기준선 실행 컨텍스트 확정·기록 (AC: 1, 2)**
  - [x] 기준선 실행 ref를 확정한다: **현재 작업 브랜치 `refactoring`의 HEAD 작업 트리**를 기준선으로 한다(아래 Dev Notes "기준선 ref 결정" 참조). 기준선 commit SHA는 `git rev-parse HEAD`로 기록한다(현재 `d4211c3074690e740a66e02f1e546b4cdb1d1e89`, 브랜치 `refactoring`). **체크아웃/리셋 없이** 현재 작업 트리 그대로 실행한다(Story 1.1과 동일하게 작업 트리를 흔들지 않는다).
  - [x] 제품 코드·테스트가 깨끗한 상태에서 실행함을 확인한다: `git diff -w --stat -- src tests`가 비어 있음(현재 비어 있음 — CRLF/LF EOL 노이즈만 있고 실질 변경 0건). 더러운 working-tree 노이즈는 되돌리지 않는다(project-context 워크플로 규칙).
  - [x] 실행 환경을 확정·기록한다: **운영 런타임인 Windows venv `.venv/Scripts/python.exe`**로 실행하고, 그 Python 버전(예: 3.11.9)과 pytest 버전(예: 9.0.3)을 `--version`으로 확인해 적는다. **WSL 시스템 python3로 실행하지 않는다**(pytest 미설치, 환경 불일치). [Source: memory/dev-env-quirks]
  - [x] `baseline-local-ui-20260613`(annotated tag → `a34d0d0`, origin/main) tag와의 관계를 기록한다: pytest 기준선은 tag 대상(`a34d0d0`)이 아니라 `refactoring` HEAD에서 캡처한다는 점과 그 사유를 명시한다(Dev Notes 참조).
- [x] **Task 2 — 전체 pytest 실행 + 머신리더블 산출물 캡처 (AC: 1, 3)**
  - [x] 저장소 루트에서 전체 스위트를 **1회** 실행한다(추가 경로 인자 없이 `pyproject.toml` 설정에 맡긴다): `.venv/Scripts/python.exe -m pytest -v`.
  - [x] 동시에 머신리더블 산출물을 생성한다 — 권장: JUnit XML `--junit-xml=docs/qa/pytest-baseline-20260613.xml`. JUnit이 불가/불편하면 `-v` 텍스트 출력 전체를 `docs/qa/pytest-baseline-20260613.txt`로 보존한다(per-test outcome가 남아야 함).
  - [x] 집계 수치를 정확히 읽는다: passed/failed/skipped/xfailed/xpassed/error 카운트와 소요 시간. (참고 기대값: Story 1.1 QA 시점 전체 스위트 **421 passed**, `def test_` 함수 352개 / 22개 파일. 실제 실행 결과가 다르면 **실제값을 기록**하고 차이를 분석한다 — 기대값을 베껴 쓰지 않는다.)
  - [x] 실패/에러가 있으면 각 실패 test nodeid와 짧은 사유(예외 타입·요지)를 수집한다. **재현 로그·리포트에 실제 secret(토큰/비밀번호/OTP/chat_id/전화번호)이나 운영 식별자 원문이 들어가지 않는지** 확인한다(NFR-5). 단위 테스트는 fake/monkeypatch를 쓰므로 통상 secret이 없지만, traceback에 혼입되지 않았는지 점검한다.
- [x] **Task 3 — `docs/qa/` 분류 리포트 작성 (AC: 1, 2, 3)**
  - [x] `docs/qa/pytest-baseline-20260613.md`를 작성한다. Story 1.1의 `docs/qa/baseline-record-20260613.md` 컨벤션(폴더·명명·메타데이터 표 스타일)을 따른다.
  - [x] 포함 메타데이터: 실행 일시(KST), 기준선 commit SHA(full)·브랜치, Python 버전, pytest 버전, OS/런타임, 실행 명령, raw 산출물 경로(xml/txt), `baseline-local-ui-20260613` tag와의 관계.
  - [x] **분류 표**를 작성한다(AC3): (a) **이미 실패/환경 skip 집합** — 기준선에서 이미 실패하거나 환경상 skip된 test nodeid + 사유. 리팩토링이 고칠 의무가 없는 항목. (b) **must-not-break 통과 집합** — 통과한 테스트 전체(파일 단위 요약 + 전체 nodeid 목록은 raw 산출물로 대체 가능). 리팩토링이 깨면 회귀로 본다. 실패 0건이면 "기준선 all-green" 명시.
  - [x] skip이 있으면 각 skip 사유를 기록한다(예: `test_baseline_artifacts.py`의 로컬 전용 tag/zip 검증은 CI/fresh checkout에서 skip — Story 1.1 QA test-summary 참조). skip을 "실패"로 오분류하지 않는다.
  - [x] JSON/표 스타일·인코딩은 Story 1.1 문서 톤과 일치시키고, 실제 secret·운영 식별자 원문을 적지 않는다.
- [x] **Task 4 — 회귀 비교 방법 문서화 (AC: 2)**
  - [x] 리포트에 "향후 회귀 비교 절차"를 적는다: ① 동일 환경에서 동일 명령 재실행, ② 새 결과를 기준선 raw 산출물(xml/txt)과 대조해 **기준선에서 통과였는데 새로 실패한 nodeid = 회귀 후보**로 식별, ③ 기준선의 "이미 실패/skip 집합"에 있던 항목은 회귀에서 제외.
  - [x] 비교 가능성을 보장하기 위해 안정적인 비교 단위(test nodeid, 정렬된 목록 또는 JUnit XML의 testcase 노드)를 비교 키로 명시한다.
- [x] **Task 5 — 누출 방지·원본 보존·git 정합 검증 및 마무리 (AC: 1, 2)**
  - [x] `docs/qa/` 산출물(md/xml/txt)에 실제 토큰·`chat_id`·비밀번호·OTP·보험사 전화번호·실제 센터명/카카오 방명 같은 민감 문자열이 없는지 grep으로 점검한다(NFR-5, ADD-15).
  - [x] 원본 보존 확인: 실행이 `runtime/`·`logs/`·`runtime/state/ui_settings.json`·`crawlingN`을 변형하지 않았음을 확인한다(`git status`에 의도치 않은 변경 없음; 테스트는 `tmp_path`만 써야 함 — project-context 테스트 규칙). `src/`·`tests/` 제품 코드 실질 변경 0건(`git diff -w -- src tests` 빈 상태) 유지.
  - [x] 생성한 모든 산출물 경로를 File List에 기록한다. `docs/qa/` 리포트와 raw 산출물은 sanitized 문서이므로 git 추적 대상으로 둔다(`docs/qa/baseline-record-20260613.md`와 동일 정책).

## Dev Notes

### 이 스토리의 성격과 경계

- **코드 기능 변경이 아니다.** Story 1.1과 같은 절차/산출물 스토리다. `src/rider_crawl/` 제품 코드도, `tests/` 테스트 파일도 **추가·수정하지 않는다.** 기존 스위트를 그대로 실행해 결과를 분류·보관할 뿐이다.
- **새 테스트를 작성하지 않는다.** `test_baseline_artifacts.py`는 Story 1.1에서 이미 추가됐다(현재 HEAD에 tracked). 본 스토리에서 새 회귀 가드 테스트를 만들지 않는다 — 그건 QA 자동화(bmad-qa) 단계의 책임이고, 본 스토리는 "기준선 측정"이다.
- **산출물은 `docs/qa/` 문서뿐이다:** 분류 리포트(md) + 머신리더블 raw(xml/txt). sprint-status·스토리 파일 갱신 외에는 추적 변경이 없어야 한다.

### 기준선 ref 결정 (중요 — 모호함 제거)

- **pytest 기준선은 현재 `refactoring` HEAD 작업 트리에서 캡처한다.** commit SHA `d4211c3074690e740a66e02f1e546b4cdb1d1e89`, 브랜치 `refactoring`. 이유:
  1. Story 1.1이 만든 `baseline-local-ui-20260613` annotated tag는 운영 known-good 본인 `origin/main`(`a34d0d0`)을 가리키지만, **그 commit에는 Story 1.1에서 추가된 `test_baseline_artifacts.py`가 없다.** tag 대상에서 실행하면 안전망 에픽(Epic 1) 자체의 산출물이 빠진 불완전한 스위트가 된다.
  2. 실제 **제품 리팩토링(Epic 2 이후)은 `refactoring` 브랜치 tip에서 분기**한다. 회귀의 기준선은 "리팩토링이 분기하는 바로 그 트리"여야 비교가 정확하다.
  3. Story 1.1이 정한 **"체크아웃/리셋/머지 없이 작업 트리를 흔들지 않는다"** 원칙을 그대로 따른다. `a34d0d0`를 체크아웃하면 working tree를 건드리게 되어 1.1 원칙과 충돌한다.
- 다만 **tag(`a34d0d0`)와의 관계를 리포트에 명시**해 혼동을 막는다: "운영 known-good 기준선 tag = origin/main(a34d0d0); pytest 회귀 기준선 = refactoring HEAD(d4211c3, Story 1.1 산출물 포함)". 두 기준선의 목적이 다르다는 점을 적는다.
- working tree는 CRLF/LF EOL 노이즈와 무관 파일(`_bmad-output/story-automator/...`) 수정이 섞여 있을 수 있다. **제품 코드/테스트 기준 실질 변경은 0건**(`git diff -w -- src tests` 빈 상태)이며, 이 노이즈는 되돌리지 않는다(project-context 워크플로 규칙). 기준선 재현성은 commit SHA + "제품 코드 무변경" 명시로 확보한다.

### 실행 방법과 환경 (project-context 워크플로 규칙 + 메모리)

- **실행 명령:** 저장소 루트에서 `.venv/Scripts/python.exe -m pytest -v` (추가 경로 인자 없이 — `pyproject.toml`의 `pythonpath=["src"]`, `testpaths=["tests"]`가 수집을 결정한다).
- **운영 런타임 venv로 실행한다.** WSL 시스템 `python3`에는 pytest가 없고 Python 버전도 다르다(Story 1.1에서 동일 확인). 기준선은 실제 운영 실행 환경(Windows `.venv`, Python `>=3.10`, 실측 3.11.9)에서 측정해야 의미가 있다. [Source: memory/dev-env-quirks, 1-1 Dev Agent Record]
- **머신리더블 산출물 권장 옵션(pytest 9.x):** `--junit-xml=<path>`로 testcase별 outcome(passed/failure/skipped/error)을 구조적으로 남기면 향후 diff 비교가 쉽다. 대안으로 `-rA`(전체 outcome 요약)·`-v`(per-test 라인) 텍스트 로그를 보존한다. 별도 plugin 설치 없이 가능한 기본 옵션만 쓴다(의존성 추가 금지).
- **결정성:** 이 스위트는 외부 브라우저·텔레그램·카카오·Gmail을 직접 호출하지 않고 fake/monkeypatch/`tmp_path`를 쓰도록 설계돼 있어(project-context 테스트 규칙) 네트워크 없이 재현 가능하다. 따라서 기준선은 CI 친화적이며 반복 실행해도 안정적이어야 한다 — 만약 flaky가 보이면 그 사실 자체를 리포트에 기록한다.

### 기대 기준선 상태 (참고값 — 실제 실행으로 갱신)

- Story 1.1 QA(test-summary.md) 시점: **전체 스위트 421 passed in ~2.94s** (기존 397 + Story 1.1 신규 24 케이스). `def test_` 함수 352개, 22개 테스트 파일.
- 즉 **현 시점 기대 기준선은 all-green(실패 0)** 일 가능성이 높다. 그렇다면 AC3의 "이미 실패하던 테스트" 집합은 비어 있고, 리포트에 "기준선 all-green, 회귀 후보로 쓸 must-not-break 통과 집합 = 전체"로 명시한다.
- **단, 기대값을 그대로 옮겨 쓰지 말 것.** 환경(venv pytest 버전, 플랫폼 의존 skip 등)에 따라 skip이 생길 수 있다. 반드시 실제 1회 실행 결과를 캡처해 수치·skip 사유를 기록한다. 기대값과 다르면 차이를 분석해 적는다.

### 분류 정책 (AC3 핵심)

- **3분류로 본다:** (1) passed = must-not-break(리팩토링이 깨면 회귀), (2) failed/error = 기준선에서 이미 깨진 것(리팩토링이 고칠 의무 없음, 단 새로 늘면 회귀), (3) skipped/xfail = 환경·의도적 비실행(사유 기록, 실패와 구분).
- skip을 failure로 묶지 않는다. 예: 로컬 전용 산출물(git tag, 백업 zip)을 검증하는 `test_baseline_artifacts.py` 케이스는 fresh checkout/CI에서 skip되도록 설계됐다(Story 1.1 QA). 이런 skip은 "기준선 정상"이다.
- 회귀 판정 규칙을 리포트에 명문화: **"기준선 passed → 이후 failed"만 회귀 후보**. 기준선에서 이미 failed/skip이던 항목은 회귀 비교에서 제외.

### Secret/원본 보존 (NFR-5, NFR-18, ADD-15 — 위반 금지)

- pytest 출력·JUnit XML·txt 로그에 실제 secret이 남지 않도록 한다. 단위 테스트는 fake 값을 쓰지만 traceback·assert 메시지에 혼입 여지가 없는지 grep으로 확인한다(마스킹 대상: token, password, OTP, full phone/email, `telegram_bot_token`, `telegram_chat_id`, `telegram_message_thread_id`, 실제 센터명/카카오 방명/보험사 번호).
- 테스트는 `runtime/`·`logs/` 원본을 건드리지 않고 `tmp_path`만 써야 한다. 실행 후 `git status`로 `runtime/`·`logs/`·`ui_settings.json`·`crawlingN`에 의도치 않은 변경이 없음을 확인한다(NFR-18, FR-2).

### Project Structure Notes

- 산출물 위치는 architecture가 정의한 `docs/qa/`(P0 pytest 결과·baseline 보관소)와 정렬된다. [Source: architecture.md#Source-Tree(462-465), #Decision-Log(672-673)]
- `docs/qa/`는 Story 1.1이 이미 생성했고 `baseline-record-20260613.md`가 들어 있다. 본 스토리 산출물은 같은 폴더·명명 규칙(`pytest-baseline-YYYYMMDD.*`)을 공유한다. [Source: docs/qa/baseline-record-20260613.md#5-후속-호환]
- 혼동 주의: `_bmad-output/implementation-artifacts/tests/test-summary.md`는 Story 1.1의 **QA 자동화 요약**(생성한 가드 테스트 설명)이지 pytest 기준선 리포트가 아니다. 본 스토리 리포트는 `docs/qa/`에 둔다.
- 제품 코드(`src/rider_crawl/`)·테스트(`tests/`) 변경 없음. `.agents/`, `.claude/`, `_bmad/` 도구 파일도 대상 아님. [Source: project-context.md#코드품질규칙]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.2(265-285)] — 본 스토리 user story·AC 원문.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#Phase-P0(28)] — P0-03(전체 pytest 실행·실패 분류, 리포트 `docs/qa/` 저장) 계약·수용 조건.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Tests(44-51)] — Unit 테스트 "All pass in CI", 테스트 범위 정의.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Log-And-Artifact-Redaction(13-19)] — 산출물 secret 비노출.
- [Source: _bmad-output/planning-artifacts/architecture.md#Source-Tree(456-468)] — `tests/`·`docs/qa/`·`tests/regression/` 구조, P0 pytest 결과 보관 위치.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision-Log(672-673)] — P0 기준선 고정 정의(pytest 결과 저장 포함).
- [Source: _bmad-output/implementation-artifacts/1-1-기준선-branch-tag와-설정-백업-생성.md] — 직전 스토리: 절차/산출물 스토리 패턴, `docs/qa/` 컨벤션, venv 실행 회귀 검증(397→421 passed), 작업 트리 무변경 원칙.
- [Source: _bmad-output/implementation-artifacts/tests/test-summary.md] — Story 1.1 QA 자동화: 전체 스위트 421 passed, 로컬 전용 케이스 skip 설계.
- [Source: docs/qa/baseline-record-20260613.md] — 기준선 tag(`baseline-local-ui-20260613`→`a34d0d0`)·실행 환경 메타데이터 컨벤션.
- [Source: pyproject.toml] — pytest 설정(`pythonpath=["src"]`, `testpaths=["tests"]`, `filterwarnings`), dev 의존성 `pytest>=8.3.0`.
- [Source: project-context.md#테스트규칙·워크플로규칙(52-82)] — pytest 실행 명령, 외부 의존 차단, `tmp_path` 사용, CRLF/LF 노이즈 비복원.
- 요구사항 추적: FR-1(기준선 저장 — pytest 결과), FR-2(자산 재사용·기존 테스트 유지), NFR-20(각 단계 기존 테스트 실행 가능), NFR-5(secret 비노출), NFR-18(원본 보존), ADD-15(secret 평문 저장 금지). [Source: epics.md#FR-Coverage-Map(154-189), #NonFunctional-Requirements]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (BMAD dev-story 워크플로)

### Debug Log References

- 실행 환경 확인: `.venv/Scripts/python.exe --version` → Python 3.11.9 / `-m pytest --version` → pytest 9.0.3 (운영 venv, WSL 시스템 python3 미사용).
- 기준선 실행: `.venv/Scripts/python.exe -m pytest -v --junit-xml=docs/qa/pytest-baseline-20260613.xml` (저장소 루트, exit 0) → **421 passed in 3.48s**.
- 집계 검증(authoritative): JUnit XML `<testsuite tests="421" failures="0" errors="0" skipped="0">`; testcase 파싱 결과 passed=421, 22개 파일.
- tag 관계 검증: `git ls-tree --name-only a34d0d0 -- tests/test_baseline_artifacts.py` → 빈 결과(tag에 없음) / `... HEAD ...` → 파일 존재 → pytest 기준선은 `refactoring` HEAD(`d4211c3`)에서 캡처.
- Secret 스캔: bot-token 형식/전화번호/실제 이메일 패턴 grep → md·xml·txt 매치 0건. all-green이라 traceback/assert 라인도 0건.
- 원본 보존: `git status -- runtime logs config.json runtime/state/ui_settings.json` 변경 0건, `git diff -w -- src tests` 빈 상태.

### Completion Notes List

- **성격:** 코드/테스트 무변경의 절차·산출물 스토리. `src/rider_crawl/`·`tests/` 추가·수정 없음. 산출물은 `docs/qa/` 문서 3종뿐.
- **기준선 결과: all-green** — 421 passed / 0 failed / 0 error / 0 skipped (22개 파일). 참고 기대값(Story 1.1 QA: 421 passed)과 실측 일치. must-not-break 집합 = 421 케이스 전체, "이미 실패/skip 집합"은 이 환경에서 공집합.
- **기준선 ref:** tag(`a34d0d0`)가 아니라 `refactoring` HEAD(`d4211c3`)에서 캡처. 이유는 tag 커밋에 Story 1.1의 `test_baseline_artifacts.py`가 없어서이며, 두 기준선의 목적 차이를 리포트 §2에 명시.
- **환경 의존 skip 주의:** `test_baseline_artifacts.py`의 로컬 전용 케이스는 tag/zip이 있는 이 환경에선 passed지만, CI/fresh checkout에선 설계상 skip된다. 이 skip은 "기준선 정상"이며 회귀가 아님을 리포트 §4에 명문화.
- **회귀 판정 규칙:** "기준선 passed → 이후 failed"만 회귀 후보. 비교 키는 test nodeid(JUnit `<testcase>` 노드). 리포트 §5에 재현·대조 절차 기록.
- **누출/보존:** 산출물 secret 0건, runtime/logs/ui_settings/제품 코드 무변경 확인.

### File List

- `docs/qa/pytest-baseline-20260613.md` (신규) — pytest 기준선 분류 리포트(메타데이터·집계·분류·회귀 비교 방법).
- `docs/qa/pytest-baseline-20260613.xml` (신규) — JUnit XML raw 결과(머신리더블 비교 키).
- `docs/qa/pytest-baseline-20260613.txt` (신규) — `-v` per-test 텍스트 로그.
- `tests/test_pytest_baseline_artifacts.py` (신규, **QA 자동화 산출물**) — 기준선 산출물 회귀 가드 18 케이스. **dev-story 단계가 아니라 후속 QA 자동화(bmad-qa-generate-e2e-tests) 단계가 추가**했다. dev-story의 "`tests/` 무변경" 경계는 유지되며(이 파일은 측정 결과가 아니라 측정 결과를 지키는 가드), 산출물(md/xml/txt)과 **함께 커밋**되어야 fresh checkout/CI에서도 통과한다.
- `_bmad-output/implementation-artifacts/tests/test-summary-1.2.md` (신규, QA 자동화 요약) — 위 가드 테스트의 갭·커버리지·검증 결과 요약.
- `_bmad-output/implementation-artifacts/1-2-pytest-기준선-실행과-결과-분류-보관.md` (수정) — frontmatter `baseline_commit`, 태스크 체크박스, Dev Agent Record, Senior Developer Review (AI), Status.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정) — 스토리 상태 ready-for-dev → in-progress → review → done.

## Change Log

| 일시 (KST) | 변경 | 비고 |
| --- | --- | --- |
| 2026-06-13 | pytest 기준선 1회 실행(421 passed, all-green) 및 JUnit XML/`-v` 로그 캡처 | `docs/qa/pytest-baseline-20260613.{xml,txt}` |
| 2026-06-13 | `docs/qa/pytest-baseline-20260613.md` 분류 리포트 작성(메타데이터·집계·AC3 분류·회귀 비교 절차) | Story 1.1 `docs/qa/` 컨벤션 준수 |
| 2026-06-13 | Secret 비노출·원본 보존·git 정합 검증 통과, Status → review | NFR-5/NFR-18/ADD-15 |
| 2026-06-13 | QA 자동화(bmad-qa): `tests/test_pytest_baseline_artifacts.py`(18 케이스) + `test-summary-1.2.md` 추가 — 산출물 무결성·secret 비노출 회귀 가드 | 전체 스위트 421→439 passed, 회귀 0 |
| 2026-06-13 | 자동 코드 리뷰(story-automator-review): AC1–3·태스크·NFR-5/18 전수 검증, 리포트 per-file 분포 XML 대조 일치, full suite 439 passed 재검증 | MEDIUM 1건(File List 누락) auto-fix, CRITICAL 0건 |
| 2026-06-13 | 리뷰 auto-fix: File List에 QA 산출물 2종 추가 + 가드↔산출물 커밋 결합 명시, Status → done | Senior Developer Review (AI) 참조 |

## Senior Developer Review (AI)

- **Reviewer:** Noah Lee (자동 코드 리뷰 — bmad-story-automator-review, adversarial)
- **Date:** 2026-06-13 (KST)
- **Outcome:** ✅ **Approve** — CRITICAL/HIGH 0건. MEDIUM 1건·LOW 2건은 auto-fix 완료.

### 검증 요약 (claim ↔ reality)

| 항목 | 검증 방법 | 결과 |
| --- | --- | --- |
| AC1 (분류·집계 리포트 + 메타 + raw) | md/xml/txt 존재·상호정합 확인, XML `tests="421" failures="0" errors="0" skipped="0"` | ✅ IMPLEMENTED |
| AC2 (회귀 비교 사용성) | 재실행 명령·nodeid 비교 키·대조 절차 문서화 확인 (§5) | ✅ IMPLEMENTED |
| AC3 (이미 실패/skip vs must-not-break 분류) | §4 분류표·all-green 명시·환경 skip 주의 확인 | ✅ IMPLEMENTED |
| Task 1–5 [x] | 각 태스크 산출물·git 상태로 실제 완료 검증 | ✅ 전부 실제 완료 |
| NFR-5 (secret 비노출) | bot-token/한국전화/이메일 패턴 독립 grep (md/xml/txt) | ✅ 매치 0건 |
| NFR-18 (원본 보존) | `git status -- runtime logs`, `git diff -w -- src tests` | ✅ 변경 0건 |
| 리포트 per-file 분포 정확성 | XML `classname` 카운트 ↔ §3 표 22개 파일 전수 대조 | ✅ 전부 일치, 합계 421 |
| 현 시점 그린 재검증 | `.venv/Scripts/python.exe -m pytest -q` (full) | ✅ **439 passed** (421 baseline + 18 QA guard), 회귀 0 |

### 발견 사항 및 조치

1. **[MEDIUM][auto-fixed] File List ↔ git 불일치 (문서 완전성).** QA 자동화가 추가한 source test `tests/test_pytest_baseline_artifacts.py`(18 케이스)와 `test-summary-1.2.md`가 git에는 있으나 File List에 없었다 → File List에 provenance(QA 단계 산출물) 주석과 함께 추가.
2. **[LOW][auto-fixed] 가드 테스트 ↔ 산출물 커밋 결합 미문서화.** `test_baseline_artifact_exists`가 `docs/qa/pytest-baseline-20260613.{md,xml,txt}` 디스크 존재를 단언 → 산출물이 가드와 **함께 커밋**되어야 fresh checkout/CI에서 통과한다는 점을 File List에 명시.
3. **[LOW][auto-fixed] Change Log 누락.** QA 자동화 추가·리뷰 결과 항목을 Change Log에 보강.

### 참고 (회귀가 아님 / 후속 주의)

- 기준선 XML/txt/md는 **frozen 산출물**이다. 향후 `--junit-xml=docs/qa/pytest-baseline-20260613.xml`로 재실행하면 baseline이 덮어써져 가드가 새 수치를 따라간다 — 재실행 시 **새 날짜 파일명**(`pytest-rerun-<날짜>.xml`)을 쓰라는 §5 절차를 준수할 것.
- dev-story의 "`tests/` 무변경" 경계는 유지됨: 위 가드 테스트는 측정 결과가 아니라 측정 결과를 지키는 QA 단계 산출물이다.
