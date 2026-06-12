# Test Automation Summary — Story 1.5 (재사용 경계·금지 행위 거버넌스)

**워크플로:** bmad-qa-generate-e2e-tests · **날짜:** 2026-06-13 · **담당:** QA 자동화 (Noah Lee)

스토리 1.5는 **문서·산출물 스토리**다(제품 코드 무변경). 따라서 API/UI E2E 테스트가 아니라
거버넌스 문서를 자동 회귀로 고정하는 **순수 파일 읽기 가드 테스트**가 해당 산출물이다
(`test_baseline_artifacts.py`·`test_manual_regression_runbook.py`와 동일 패턴, project-context §55).

기존 30개 가드 케이스가 AC 하위 절 일부를 비워 둔 것을 발견해, 1.4와 동일하게
`tests/test_reuse_boundaries_doc.py`에 **QA 추적 커버리지 16케이스를 보강**했다(문서 무변경 — 보강
테스트가 단언하는 문자열이 모두 기존 거버넌스 문서에 이미 존재함을 사전 확인).

## Generated / Augmented Tests

### Artifact Regression-Guard Tests (E2E 대체 — 순수 파일 읽기)
- [x] `tests/test_reuse_boundaries_doc.py` — 거버넌스 문서 산출물 회귀 가드. **30 → 46 케이스**.

### API Tests
- N/A — 이 스토리에 HTTP/서비스 엔드포인트 없음(거버넌스 문서 산출물).

## 보강한 AC 커버리지 갭 (16 신규 케이스)

| AC | 갭 (기존 미단언) | 신규 테스트 | 케이스 |
| --- | --- | --- | --- |
| AC1 #1 | 보존 자산 표의 '허용되는 변경'·'금지되는 변경' 열 | `test_doc_preserved_asset_table_has_allowed_and_forbidden_change_columns` | 1 |
| AC1 #3 | 핵심 required change 목표(정규화 Snapshot/webhook/getUpdates/template_version) | `test_doc_records_required_change_targets` (parametrize) | 4 |
| AC1 #2 | 쿠팡 렌더 골든 연결 + 저장 JSON `indent=2` 완전형 | `test_doc_links_coupang_golden_and_json_indent` | 1 |
| AC2 #4 | 금지 행위 표의 '사유(코드/운영 근거)' 열 | `test_doc_forbidden_table_has_rationale_column` | 1 |
| AC2 #5 | 구체 대안(FIFO queue/sender lock/backoff/`*_ref`) | `test_doc_records_specific_forbidden_alternatives` (parametrize) | 4 |
| AC3 #6 | 권위 계층 하위 3·4단(architecture.md·spec 계약) + '코드 구현 전'·'일반 관례보다 우선' | `test_doc_authority_hierarchy_names_lower_tiers` (parametrize) + `test_doc_authority_principle_read_before_coding` | 4 |
| AC3 #7 | '임의 변경 금지' 명문화 | `test_doc_forbids_arbitrary_change` | 1 |

## Coverage

- **AC 추적:** AC1(#1·#2·#3) / AC2(#4·#5) / AC3(#6·#7) / AC4(#8·#9) — 4개 AC 전 하위 절을 회귀 가드로 고정.
- **가드 케이스:** 7개 보존 자산 + 4개 공개 동작 + 7개 금지 행위 + 권위 계층 4단 + 예외/위반 절차 + secret 4종 패턴 부재.
- **신규/전체:** 16 신규 → 파일 46 케이스 / 전체 스위트 **584 passed**.

## Validation (run via `.venv/Scripts/python.exe -m pytest`)

| 항목 | 결과 |
| --- | --- |
| `tests/test_reuse_boundaries_doc.py` | **46 passed** (기존 30 + 신규 16) |
| 전체 스위트 `pytest -q` | **584 passed** (기준선 568 + 신규 16, **회귀 0**) |
| 범위 `git diff -w --stat -- src/rider_crawl` | 빈 상태 — 제품 코드 0줄 변경 |
| secret 누출 grep(봇 토큰/`chat_id=<digits>`/한국 휴대폰) | 실제값 매치 0건 (placeholder/가짜값만) |

## 테스트 품질 노트

- 모든 신규 케이스는 외부 브라우저/Telegram/Kakao/Gmail/네트워크 **미호출**(순수 파일 읽기).
- 하드코딩 대기/sleep 없음. 각 케이스는 문서를 새로 읽어 **순서 독립**.
- secret 검사는 `test_manual_regression_runbook.py`의 정규식(`TELEGRAM_BOT_TOKEN_RE` 등)을 import
  재사용하고, **실제 secret '패턴'의 부재**로만 단언한다(파일 전체 `redact()` 비교 금지 — 1.4 AC4 교훈).

## Next Steps

- CI에서 전체 스위트 실행 시 본 가드가 거버넌스 문서의 필수 섹션 누락·secret 유입을 차단.
- Epic 2~5에서 required change(정규화 Snapshot wrapping·중앙 webhook·token 격리·circuit breaker)를
  **구현**할 때, 경계가 바뀌면 §5 예외 절차(ADR/architecture.md 기록)를 거치고 본 가드를 함께 갱신.
</content>
