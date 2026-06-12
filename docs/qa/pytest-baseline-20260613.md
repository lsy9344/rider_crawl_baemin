# pytest 기준선 리포트 — 2026-06-13 (P0-03)

리팩토링 시작 시점의 **전체 pytest 결과를 1회 실행해 통과/실패/스킵으로 분류·집계한 회귀 비교 기준선** 문서다.
이 기준선의 **통과 집합은 "리팩토링이 깨면 안 되는 must-not-break 집합"**이며, 이후 단계에서 동일 명령으로 재실행한 결과와 대조해 새로 실패한 테스트(회귀 후보)를 식별한다.
이 문서와 raw 산출물에는 **실제 secret(토큰/비밀번호/OTP/chat_id/전화번호)이나 운영 식별자 원문을 적지 않는다.** 수치·메타데이터·test nodeid만 기록한다.
(요구사항: FR-1 기준선 저장(pytest 결과), FR-2 기존 테스트 유지, NFR-20 각 단계 기존 테스트 실행 가능, NFR-5 secret 비노출, NFR-18 원본 보존, ADD-15 secret 평문 저장 금지)

---

## 1. 실행 메타데이터

| 항목 | 값 |
| --- | --- |
| 실행 일시 (KST) | 2026-06-13 03:05:26 KST (JUnit XML `timestamp` 기준) |
| 기준선 commit SHA (full) | `d4211c3074690e740a66e02f1e546b4cdb1d1e89` |
| 기준선 브랜치 | `refactoring` (리팩토링 분기 tip) |
| Python 버전 | 3.11.9 |
| pytest 버전 | 9.0.3 (pluggy 1.6.0, 플러그인: anyio 4.13.0) |
| 실행 환경 (OS/런타임) | Windows (`platform win32`), 운영 venv `.venv/Scripts/python.exe` |
| 기준선 측정 작업 환경 | WSL2(Linux 6.6.87.2-microsoft-standard-WSL2)에서 Windows venv exe를 호출 |
| 실행 명령 | `.venv/Scripts/python.exe -m pytest -v --junit-xml=docs/qa/pytest-baseline-20260613.xml` (저장소 루트, 추가 경로 인자 없음) |
| 수집 결정 | `pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`, `configfile: pyproject.toml` |
| 소요 시간 | 3.48s (전체) / 3.386s (testsuite 집계) |
| raw 산출물 (머신리더블) | `docs/qa/pytest-baseline-20260613.xml` (JUnit XML, 권장 비교 키), `docs/qa/pytest-baseline-20260613.txt` (`-v` per-test 텍스트 로그) |

### 실행 환경 선택 근거 (운영 런타임 venv)

- 기준선은 **실제 운영 실행 환경(Windows `.venv`, Python 3.11.9)**에서 측정했다. WSL 시스템 `python3`에는 pytest가 설치돼 있지 않고 Python 버전(3.12.x)도 운영과 다르므로 기준선 측정에 쓰지 않는다. [Source: memory/dev-env-quirks, Story 1.1 Dev Agent Record]
- 별도 plugin 설치 없이 pytest 기본 옵션(`-v`, `--junit-xml`)만 사용했다(의존성 추가 금지 규칙 준수).

## 2. 기준선 ref와 tag(`baseline-local-ui-20260613`)의 관계

이 프로젝트에는 목적이 다른 **두 개의 기준선**이 있다. 혼동을 막기 위해 명시한다.

| 기준선 | 대상 | 목적 |
| --- | --- | --- |
| 운영 known-good 기준선 **tag** | `baseline-local-ui-20260613` → annotated tag → `a34d0d0` (== `origin/main`) | 리팩토링 전 known-good 운영본 고정(Story 1.1, P0-01) |
| **pytest 회귀 기준선** (이 문서) | `refactoring` HEAD `d4211c3` | 리팩토링이 깨면 안 되는 테스트 통과 집합 측정(P0-03) |

### pytest 기준선을 tag(`a34d0d0`)가 아니라 `refactoring` HEAD에서 캡처한 이유

1. **tag 대상에는 Story 1.1 산출물이 없다.** Story 1.1이 추가한 `tests/test_baseline_artifacts.py`(24 케이스)는 tag 커밋 `a34d0d0`에 **존재하지 않고**, `refactoring` HEAD `d4211c3`에만 존재한다(검증: `git ls-tree --name-only a34d0d0 -- tests/test_baseline_artifacts.py` → 빈 결과 / `... HEAD ...` → 파일 존재). tag에서 실행하면 안전망 에픽(Epic 1)의 산출물이 빠진 불완전한 스위트가 된다.
2. **실제 제품 리팩토링(Epic 2 이후)은 `refactoring` tip에서 분기**한다. 회귀 기준선은 "리팩토링이 분기하는 바로 그 트리"여야 비교가 정확하다.
3. Story 1.1이 정한 **"체크아웃/리셋/머지 없이 작업 트리를 흔들지 않는다"** 원칙을 유지했다. `a34d0d0` 체크아웃은 working tree를 건드려 이 원칙과 충돌한다.

> 재현성 보장: working tree에는 CRLF/LF EOL 노이즈와 무관 파일(`_bmad-output/...`) 변경이 섞일 수 있으나, **제품 코드/테스트 기준 실질 변경은 0건**이다(`git diff -w -- src tests` 빈 상태 — 아래 §5 검증). 기준선 재현성은 commit SHA(`d4211c3`) + "제품 코드 무변경" 명시로 확보한다. 이 노이즈는 project-context 워크플로 규칙에 따라 되돌리지 않는다.

## 3. 결과 집계 (AC1)

`--junit-xml`의 testsuite 집계와 testcase 카운트가 일치한다(authoritative).

| outcome | 건수 | 회귀 관점 분류 |
| --- | --- | --- |
| **passed** | **421** | **must-not-break 집합** (리팩토링이 깨면 회귀) |
| failed | 0 | — |
| error | 0 | — |
| skipped | 0 | — |
| xfailed / xpassed | 0 / 0 | — |
| **총 수집(collected)** | **421** | 22개 테스트 파일 |

**→ 기준선 all-green (실패 0 / 에러 0 / 스킵 0).** 따라서 AC3의 "이미 실패/환경 skip 집합"은 **이 실행 환경에서 비어 있고**, must-not-break 통과 집합 = 전체 421 케이스다.

> 참고 기대값(Story 1.1 QA `test-summary.md`): 전체 스위트 421 passed. **실측도 421 passed로 일치**한다. (기대값을 베껴 쓴 것이 아니라 본 실행에서 실제 측정한 값이다 — raw 산출물 `pytest-baseline-20260613.xml`의 `tests="421" failures="0" errors="0" skipped="0"`로 재검증 가능.)

### 파일별 통과 분포 (must-not-break 집합 요약)

전체 nodeid 목록은 raw 산출물(xml/txt)에 보존되어 있으므로, 여기서는 파일 단위 요약만 둔다.

| 테스트 파일 | passed | 테스트 파일 | passed |
| --- | ---: | --- | ---: |
| `tests/test_app.py` | 10 | `tests/test_keyword_responder.py` | 19 |
| `tests/test_architecture.py` | 10 | `tests/test_lock.py` | 5 |
| `tests/test_baemin_parser.py` | 7 | `tests/test_message.py` | 3 |
| `tests/test_baseline_artifacts.py` | 24 | `tests/test_parser.py` | 10 |
| `tests/test_browser_launcher.py` | 14 | `tests/test_scheduler.py` | 5 |
| `tests/test_config.py` | 12 | `tests/test_sender.py` | 37 |
| `tests/test_coupang_crawler.py` | 59 | `tests/test_telegram_commands.py` | 36 |
| `tests/test_coupang_email_2fa.py` | 11 | `tests/test_telegram_sender.py` | 12 |
| `tests/test_coupang_message.py` | 4 | `tests/test_ui_helpers.py` | 66 |
| `tests/test_coupang_parser.py` | 10 | `tests/test_ui_settings.py` | 18 |
| `tests/test_crawler.py` | 32 | **합계** | **421** |
| `tests/test_gmail_2fa.py` | 17 | (22개 파일) | |

## 4. 분류 (AC3) — 이미 실패/skip 집합 vs must-not-break 통과 집합

| 분류 | 정의 (회귀 판정) | 이번 기준선 결과 |
| --- | --- | --- |
| **(a) 이미 실패/환경 skip 집합** | 기준선에서 이미 failed/error/skip이던 nodeid. 리팩토링이 고칠 의무 없음, 회귀 비교에서 **제외**. | **비어 있음** (failed 0, skip 0) |
| **(b) must-not-break 통과 집합** | 기준선 passed 전체. 리팩토링이 깨면 **회귀**. | **421 케이스 전체** (§3 파일별 분포 + raw 산출물의 전체 nodeid) |

### 환경 의존 skip에 대한 주의 (실패로 오분류 금지)

- 이번 **로컬 운영 환경 실행에서는 skip이 0건**이다. 단, `tests/test_baseline_artifacts.py`의 일부 케이스는 **로컬 전용 산출물(git tag `baseline-local-ui-20260613`, 백업 zip)이 존재할 때만 검증하고, fresh checkout/CI에서는 가짜 실패를 막기 위해 설계상 `skip`된다.** [Source: Story 1.1 `test-summary.md`(36–37행)]
- 이번 실행 환경에는 해당 tag와 zip이 **존재**하므로 이 케이스들이 모두 **passed**로 잡혔다(예: `test_baseline_tag_is_annotated_and_matches_record`, `test_backup_zip_is_git_ignored_if_present`).
- **따라서 다른 환경(CI/fresh checkout)에서 이 케이스들이 `skip`으로 나오는 것은 "기준선 정상"이며 회귀가 아니다.** skip을 failure로 묶지 않는다. 회귀 판정은 아래 §5 규칙(기준선 passed → 이후 failed)만 적용한다.

## 5. 향후 회귀 비교 방법 (AC2)

### 재현·비교 절차

1. **동일 환경·동일 명령 재실행:** 저장소 루트에서
   ```
   .venv/Scripts/python.exe -m pytest -v --junit-xml=docs/qa/pytest-rerun-<YYYYMMDD>.xml
   ```
   (운영 venv·Windows·동일 `pyproject.toml` 수집 설정 유지.)
2. **대조:** 새 결과를 이 기준선 raw 산출물과 비교한다.
   - **회귀 후보 = "기준선에서 passed였는데 새 실행에서 failed/error로 바뀐 nodeid".**
   - 기준선의 "(a) 이미 실패/환경 skip 집합"(이번엔 비어 있음)에 있던 항목은 회귀에서 **제외**한다.
   - 환경 의존 skip(예: `test_baseline_artifacts.py`의 로컬 전용 케이스)이 CI에서 skip되는 것은 회귀가 아니다.
3. **새 실패 분류:** 새로 생긴 failed nodeid를 회귀 후보로 보고 원인(리팩토링 변경 vs 환경 변화)을 분석한다.

### 안정적 비교 키 (test nodeid)

- 비교 단위는 **test nodeid**(`파일::테스트함수`)다. JUnit XML의 `<testcase classname="tests.test_xxx" name="test_yyy">` 노드가 안정적 비교 키이며, 리네임·삭제 없이 보존된다(NFR-20: 통과 테스트는 계속 수집 가능·이름 보존).
- 텍스트 대조가 필요하면 `-v` 로그(`pytest-baseline-20260613.txt`)의 per-test 라인을 정렬해 diff한다.
- 권장 비교 명령 예:
  ```bash
  # JUnit XML에서 passed nodeid 목록 추출(정렬) 후 재실행 결과와 대조
  # 기준선 passed - 재실행 passed = 회귀 후보(새로 깨진 테스트)
  ```
  구조적 비교는 XML의 `testcase` 노드에 `<failure>`/`<error>` 자식이 새로 생겼는지로 판정한다.

## 6. Secret 비노출·원본 보존 검증 (NFR-5, NFR-18, ADD-15)

| 검증 항목 | 방법 | 결과 |
| --- | --- | --- |
| 산출물 secret 비노출 | `docs/qa/*.txt`·`*.xml`에 Telegram bot-token 형식(`\d{8,10}:[A-Za-z0-9_-]{30,}`), 한국 전화번호, 실제 이메일 패턴 grep | **매치 0건** (테스트 nodeid에 `token`/`chat_id`/`thread_id` 같은 *단어*는 있으나 실제 secret *값*은 없음) |
| traceback secret 혼입 | all-green이라 traceback 자체가 없음(txt에 `Traceback`/`assert` 라인 0건) | **혼입 여지 없음** |
| 원본 상태 보존 | `git status -- runtime logs config.json runtime/state/ui_settings.json` | **변경 0건** (`runtime/`·`logs/`·`ui_settings.json`·`crawlingN` 무변경) |
| 제품 코드·테스트 무변경 | `git diff -w -- src tests` | **빈 상태** (실질 변경 0건, CRLF/LF 노이즈만 가능) |

- 단위 테스트는 fake/monkeypatch/`tmp_path`만 사용하도록 설계돼(project-context 테스트 규칙) 외부 브라우저·텔레그램·카카오·Gmail을 직접 호출하지 않는다. 따라서 네트워크 없이 재현 가능하며 secret 원문이 출력에 남지 않는다.
- 이 실행에서 flaky 현상은 관찰되지 않았다(전체 421 passed, 3.48s).

## 7. 산출물 목록 (git 추적 대상)

`docs/qa/` 산출물은 sanitized 문서이므로 `docs/qa/baseline-record-20260613.md`와 동일 정책으로 git 추적 대상에 둔다.

| 경로 | 종류 |
| --- | --- |
| `docs/qa/pytest-baseline-20260613.md` | 분류 리포트(이 문서) |
| `docs/qa/pytest-baseline-20260613.xml` | JUnit XML raw 결과(머신리더블 비교 키) |
| `docs/qa/pytest-baseline-20260613.txt` | `-v` per-test 텍스트 로그 |

## 8. 후속 호환

- 본 리포트는 Story 1.1의 `docs/qa/baseline-record-YYYYMMDD.md` 컨벤션(폴더·명명·메타데이터 표 스타일)을 따른다.
- 혼동 주의: `_bmad-output/implementation-artifacts/tests/test-summary.md`는 Story 1.1의 **QA 자동화 요약**(가드 테스트 설명)이지 pytest 기준선 리포트가 아니다. pytest 기준선은 이 `docs/qa/` 문서다.
- 이후 회귀 가드 테스트 자동화(bmad-qa 단계)는 본 기준선의 must-not-break 집합을 입력으로 삼을 수 있다.
