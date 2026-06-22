# Test Automation Summary — Story 1.4 (qa-generate-e2e-tests)

대상: Story 1.4 — 기존 2탭 수동 회귀 시나리오 문서화 (P0-05)
프레임워크: **pytest** (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`)
실행: `.venv/Scripts/python.exe -m pytest -q` (WSL `python3` 미사용 — pytest 미설치)
일시(KST): 2026-06-13

## 컨텍스트

Story 1.4는 **문서·산출물 스토리**다(제품 코드 무변경). 검증 대상 "기능"은 ① 수동 회귀
런북, ② dry-run 기준선 기록, ③ 그 산출물을 지키는 회귀 가드 테스트다. 외부 브라우저/
Telegram/Kakao/Gmail/네트워크를 호출하지 않는 **순수 파일 읽기 테스트**(project-context §55)이므로
별도 API/E2E(브라우저) 스위트는 적용 대상이 아니다 — 산출물 회귀 가드가 이 스토리의 E2E 안전망이다.

## 발견한 커버리지 갭 → 자동 적용한 테스트

기존 가드(`tests/test_manual_regression_runbook.py`, 20케이스)는 4개 절차의 **존재**와 secret
비노출만 단언했고, 아래 AC 항목이 회귀 가드에서 비어 있었다. 모두 **자동 적용**(테스트 추가)했다.

| AC | 갭(기존 미커버) | 추가한 테스트 | 케이스 |
| --- | --- | --- | --- |
| AC1 #2 | 각 절차의 **기대 결과(렌더 메시지 형태)** | `test_runbook_documents_message_header_and_collection_success`, `test_runbook_documents_baemin_four_peak_labels`(4), `test_runbook_documents_baemin_optional_reject_rate`, `test_runbook_documents_coupang_dashboard_form` | 7 |
| AC2 #4·#5 | **캡처 메타데이터**(플랫폼/탭 라벨/캡처 일시 KST/실행 방식) + **실발송 없음** 표시 | `test_dry_run_baseline_records_capture_metadata`(4), `test_dry_run_baseline_capture_time_is_kst`, `test_dry_run_baseline_marks_no_real_send` | 6 |
| AC3 #7 | 비교 방법 **세부**(동일 입력 sha256 일치 + 숫자 제외 형태/라벨 비교) | `test_comparison_method_documents_sha256_equality`(2), `test_comparison_method_documents_shape_compare_excluding_numbers`(2) | 4 |
| AC3 #9 / NFR-24 | **cutover 규칙**(운영자 승인 후에만 활성화, 자동 활성화 금지) | `test_cutover_rule_requires_operator_approval`(2) | 2 |

추가 합계: **19 케이스** (가드 20 → **39**).

## 생성/수정한 테스트

### E2E(산출물 회귀 가드) 테스트
- [x] `tests/test_manual_regression_runbook.py` — 산출물 회귀 가드(보강). **20 → 39 케이스** (166 → 274줄)

### API 테스트
- 해당 없음 (이 스토리에 HTTP API/엔드포인트 없음 — 문서·산출물 스토리)

## 커버리지 (AC 추적)

- AC1 (4개 절차 + **기대 결과 형태**): 절차 존재 ✅ + 기대 형태 ✅ (신규)
- AC2 (dry-run 기준선: 스켈레톤/sha256 + **메타데이터/비발송**): ✅ + 메타데이터 ✅ (신규)
- AC3 (비교 방법: 섹션 + **세부 방법 + cutover**): ✅ + 세부 ✅ (신규)
- AC4 (산출물 존재 + secret 패턴 비노출): ✅ (기존)
- AC 커버리지: 4/4 AC, 모든 하위 항목 회귀 가드에 반영

## 검증 결과

- 가드 테스트: `pytest tests/test_manual_regression_runbook.py -q` → **39 passed** (0.08s)
- 전체 스위트: `.venv/Scripts/python.exe -m pytest -q` → **538 passed** (519 + 신규 19, **회귀 0**, NFR-20 충족)
- 범위: `src/rider_crawl/` 제품 코드 무변경(`git diff -w --stat -- src/rider_crawl` 빈 상태). 가드 테스트 1파일만 변경.
- secret 비노출: 추가분에 실제 토큰/`chat_id` 평문 없음(정규식 소스/placeholder만). 가드 테스트 스스로 두 산출물의 secret 패턴 부재를 강제.

## Next Steps

- (운영자) 로그인된 Chrome(CDP) 환경에서 런북 §2 UI 경로로 배민/쿠팡 dry-run 1회 실측 → 기준선 표의
  `<운영자 캡처 필요>` 칸(sha256·캡처 일시)을 채운다. 가드 테스트는 실측값 유무로 깨지지 않게 설계됨.
- 실제 dry-run 경로(FR-3)와 자동 비교 하네스(`tests/regression/`)는 Epic 3/5 책임(이 스토리 범위 밖).
