# Test Automation Summary — Story 1.2 (pytest 기준선 실행·분류·보관)

생성일: 2026-06-13 · 작성: QA 자동화 (bmad-qa-generate-e2e-tests) · 프레임워크: pytest

> Story 1.1 요약은 같은 폴더 `test-summary.md`에 있다. 이 파일은 Story 1.2 전용이라
> 1.1 요약을 덮어쓰지 않고 `test-summary-1.2.md`로 분리해 둔다.

## 컨텍스트

Story 1.2(P0-03)는 제품 코드 변경이 아니라 **리팩토링 시작 시점의 전체 pytest 결과를
1회 실행해 통과/실패/스킵으로 분류·보관하는 절차/산출물 스토리**다. 산출물은
`docs/qa/` 아래 ① 분류 리포트(md), ② JUnit XML raw 결과, ③ `-v` per-test 텍스트
로그 3종이다. UI·API가 없어 전통적 E2E 대상은 아니지만, **AC가 산출물 무결성·보안
속성**(secret 비노출, 메타데이터 완전성, 머신리더블 비교 키 보존, 리포트↔raw 집계
정합)이라 회귀 가드로 자동화할 가치가 높다. 스토리 Dev Notes도 회귀 가드 테스트 작성을
**bmad-qa 단계의 책임**으로 명시했다. 기존 수동 체크리스트를 durable pytest 회귀
테스트로 고정했다.

## Generated Tests

### Regression / Security Tests
- [x] `tests/test_pytest_baseline_artifacts.py` — pytest 기준선 리포트 회귀 가드 (**18 케이스**)

Story 1.1의 `tests/test_baseline_artifacts.py` 컨벤션(REPO_ROOT 상대경로, 봇 토큰
패턴 검사, 실제 secret 비하드코딩)을 그대로 따른다.

## 적용한 갭 (auto-applied)

Story 1.2 산출물에는 자동 회귀 가드가 **전무**했다(`grep -rl pytest-baseline tests/` → 없음).
수동으로만 검증되던 AC들을 자동화로 고정했다.

| 갭 | AC / 요구사항 | 자동 테스트 |
| --- | --- | --- |
| 산출물 3종(md/xml/txt) 존재 미보장 | AC1.1, AC1.3 | `test_baseline_artifact_exists` (3 파라미터화) |
| 산출물에 실제 secret(봇 토큰/이메일/전화번호) 누출 검사 없음 | NFR-5, ADD-15 | `test_no_secret_pattern_in_artifacts` (3 산출물 × 3 패턴 = 9) |
| JUnit XML 파싱 가능·testcase 수↔집계 정합 미검증 | AC1 | `test_junit_xml_is_wellformed_and_self_consistent` |
| 안정적 비교 키(classname+name = nodeid) 보존 미검증 | AC2, NFR-20 | `test_junit_testcases_preserve_stable_comparison_key` |
| 리포트(md) 집계가 raw(authoritative)와 일치하는지 미검증 | AC1 | `test_report_aggregate_matches_junit_xml` |
| 리포트 필수 메타데이터(일시·SHA·브랜치·버전·환경·명령·경로) 완전성 미검증 | AC1.2 | `test_report_contains_required_metadata` |
| 회귀 비교 절차·must-not-break 집합·skip 분류 문서화 미검증 | AC2.5, AC3 | `test_report_documents_regression_comparison_contract` |
| txt가 유효한 `-v` per-test 로그이고 passed 요약이 JUnit과 일치하는지 미검증 | AC1.3 | `test_txt_log_is_valid_verbose_per_test_log` |

설계 원칙: 프로젝트 규칙대로 **실제 secret 값을 테스트에 하드코딩하지 않는다.** 누출은
실제값이 아니라 secret '패턴'(텔레그램 봇 토큰 `\d{6,}:[\w-]{30,}`, 이메일, 한국
휴대폰/대표번호)으로 검사한다. **기대 수치(421 passed 등)는 베껴 쓰지 않고 JUnit
XML(authoritative)에서 파싱**해 md·txt와 교차검증하므로, 기준선이 재생성돼 수치가
바뀌어도 가드가 함께 따라간다. txt는 cp949 인코딩이라 인코딩-관대 reader로 읽는다.

## Coverage

| Acceptance Criterion | 커버 |
| --- | --- |
| AC1 — 분류·집계 리포트 + 메타데이터 + 머신리더블 raw (P0-03) | ✅ 존재 + 메타 완전성 + XML 정합 + 리포트↔XML 집계 일치 |
| AC2 — 회귀 비교 기준선 사용성 (NFR-20, FR-2) | ✅ 안정적 비교 키(nodeid) 보존 + 비교 절차/명령 문서화 |
| AC3 — 이미 실패/skip vs must-not-break 분류 | ✅ must-not-break·skip 분류 문서화 + all-green 명시 정합 |
| NFR-5 / ADD-15 — 산출물 secret 비노출 | ✅ md/xml/txt × 봇토큰·이메일·전화번호 패턴 0건 |

자동 검증 불가(의도적 제외): docs/qa 산출물의 git 추적 여부 — 현재 working tree에서
미추적(신규 파일) 상태라 추적 단언은 현 시점 green을 깬다. 커밋은 QA 테스트 생성
범위 밖이므로 추적 단언은 넣지 않고 디스크 존재만 가드한다.

## Validation Results

- 신규 모듈: **18 passed** (`pytest tests/test_pytest_baseline_artifacts.py`, 0.08s)
- 전체 스위트: **439 passed in 2.47s** (기존 421 + 신규 18, 회귀 0)
- 누출 정규식 teeth 확인: fake 봇토큰/이메일/휴대폰 탐지 ✅ / 타임스탬프·nodeid 오탐 0건 ✅
- 집계 교차검증: JUnit `tests=421/failures=0/errors=0/skipped=0` ↔ md 인용값 ↔ txt `421 passed` 일치 ✅
- 실행: Windows venv `.venv/Scripts/python.exe -m pytest` (Python 3.11.9, pytest 9.0.3)

## Next Steps

- CI 파이프라인에 포함(외부 입력·네트워크 의존 없음 — CI 친화적).
- `docs/qa/pytest-baseline-20260613.{md,xml,txt}`를 git 추적 대상으로 커밋(스토리
  Task 5 정책). 추적되면 본 가드는 fresh checkout/CI에서도 그대로 통과한다.
- 향후 기준선을 재생성(`pytest-baseline-<새 날짜>.*`)하면 본 모듈 상단 경로 상수
  3개를 새 날짜로 갱신한다(집계·메타 가드는 XML에서 파싱하므로 그대로 유효).
