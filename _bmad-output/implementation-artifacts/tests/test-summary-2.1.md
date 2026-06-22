# Test Automation Summary — Story 2.1 (UiSettings 고객/대상 ID 부여 + legacy_alias 보존)

생성일: 2026-06-13 · 작성: QA 자동화 (bmad-qa-generate-e2e-tests) · 프레임워크: pytest

## 컨텍스트

- **Story:** `_bmad-output/implementation-artifacts/2-1-uisettings에-고객-대상-id-부여와-legacy-alias-보존.md`
- **대상 코드:** `src/rider_crawl/ui_settings.py` (`UiSettings` 5개 ID/alias 필드, `_issue_missing_ids`, `load`/`load_all` persist-on-first-issue)
- **테스트 파일:** `tests/test_ui_settings.py`
- **실행:** `.venv/Scripts/python.exe -m pytest -q` (WSL `python3` 아님 — pytest 미설치)
- API/Playwright E2E 없음: `tkinter` 데스크톱 앱 + 순수 파일 I/O 마이그레이션 로직이라 HTTP/브라우저 표면이 없다. 프로젝트 컨벤션(`tmp_path`, 외부 호출 없음)에 따라 단위 수준 테스트로 검증.

## 생성된 테스트 (`tests/test_ui_settings.py`)

### 기존 Story-2.1 테스트 (dev-story 단계, 8개)
- [x] `test_ui_settings_round_trip_preserves_id_and_alias_fields` — AC1 save/load 라운드트립
- [x] `test_ui_settings_save_all_load_all_preserves_id_and_alias_fields` — AC1 + JSON 스타일(`ensure_ascii=False`, `"crawlings"`)
- [x] `test_load_all_issues_stable_monitoring_target_id_across_reloads` — AC3 #6 persist-on-issue 안정성
- [x] `test_load_all_preserves_existing_ids_without_reissue` — AC3 #7 멱등(모든 ID 사전 보유)
- [x] `test_load_all_issues_ids_only_for_active_tabs` — AC3 #8 활성 탭만 발급
- [x] `test_load_all_does_not_create_file_when_missing` — AC3 no-file 가드
- [x] `test_load_all_seeds_legacy_alias_from_tab_index_and_preserves_existing` — AC2 #4 legacy_alias seed/보존
- [x] `test_load_single_issues_stable_id_for_single_object_file` — AC3 단일 객체 load 안정성

### 이번 QA 런에서 보강한 갭 테스트 (6개)
- [x] `test_load_all_issues_three_distinct_independent_ids` — AC3/Dev-Notes: 3개 ID 독립 발급, 서로 다른 값(같은 값 재사용 금지)
- [x] `test_load_all_does_not_auto_issue_customer_name` — Dev-Notes: `customer_name`은 자동 발급 안 함(빈 문자열 유지)
- [x] `test_load_all_fills_only_missing_ids_and_preserves_existing` — AC3 #7: **필드 단위** 멱등(일부 ID만 사전 보유 시 누락분만 발급)
- [x] `test_load_all_treats_whitespace_only_url_as_inactive` — AC3 #8: `performance_url.strip()` 의미(`"   "` → 비활성, 발급/쓰기 없음)
- [x] `test_load_all_does_not_rewrite_file_when_all_ids_present` — AC3 #7: 모든 ID 있는 파일 → **재기록 안 함**(persist-on-*first*-issue 가드, 바이트 불변)
- [x] `test_to_app_config_does_not_expose_id_fields` — AC1 #3 범위 가드: ID 필드를 `AppConfig`에 연결하지 않음

## 갭 분석 → 보강 근거

각 갭은 AC 또는 Dev-Notes에 명시됐으나 단언하는 테스트가 없던 계약이다:

| # | 갭 | 계약 근거 |
| --- | --- | --- |
| 1 | 3개 ID가 서로 다른 값(독립 발급) | AC3 / Dev-Notes "각각 독립 발급, 같은 값 재사용 금지" |
| 2 | `customer_name` 자동 발급 안 함 | Dev-Notes 명시 |
| 3 | 필드 단위 멱등(일부 ID만 있어도 누락분만 발급) | AC3 #7 (`_issue_missing_ids` per-field 분기) |
| 4 | 공백뿐인 `performance_url`은 비활성 | AC3 #8 `.strip()` 의미 |
| 5 | 완전 발급된 파일 재로드 시 write 없음 | AC3 #7 + persist-on-first-issue 가드 |
| 6 | ID 필드가 `AppConfig`에 노출 안 됨 | AC1 #3 범위 경계 |

## 커버리지

| Acceptance Criterion | 커버 테스트 |
| --- | --- |
| AC1 #1–2 (필드 라운드트립 + JSON 스타일) | round_trip / save_all_load_all preserve |
| AC1 #3 (defaulted, AppConfig 미연결) | `test_to_app_config_does_not_expose_id_fields` (신규) |
| AC2 #4 (legacy_alias seed/보존, 표시 전용) | seeds_legacy_alias |
| AC2 #5 (9탭/kakao/coupang/refresh-seconds 비파괴) | 기존 `test_ui_settings.py` 회귀(무수정, 전부 통과) |
| AC3 #6 (발급 ID 영속·재로드 안정) | stable_monitoring_target_id / single-object 안정성 |
| AC3 #7 (멱등 — 보존·재발급 금지) | preserves_existing + **fills_only_missing** + **does_not_rewrite_file** (신규) |
| AC3 #8 (활성 탭만, `.strip()` 의미, filler 무발급) | issues_ids_only_for_active_tabs + **whitespace_only_url** (신규) |

- `tests/test_ui_settings.py`: **32 테스트** (기존 회귀 18 + dev 8 + QA 갭 6)
- 전체 스위트: **598 passed** (기준선 592 + 신규 6), 회귀 0

## 품질 게이트

- secret/식별자 누출 grep(`[0-9]{6,}:[A-Za-z0-9_-]{30,}` / `chat_id=<digits>` / KR 휴대폰) → **NO LEAKS** (가짜값만: `mt-keep`, `cust-fixed`, `https://example.test/…`).
- 범위: 이번 QA 런은 `tests/test_ui_settings.py`에만 테스트 코드 추가 — **제품 코드 무변경**.
- hardcoded wait/sleep 없음; 각 테스트 독립(고유 `tmp_path` 파일 생성); 의미 기반 단언.

## 다음 단계

- CI에서 전체 pytest 스위트와 함께 실행.
- Story는 `status: review` 유지 — QA 테스트 갭 종결, 코드 리뷰 진행 가능.
