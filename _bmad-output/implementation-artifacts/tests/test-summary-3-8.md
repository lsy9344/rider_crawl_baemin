# Test Automation Summary — Story 3.8 (신규 경로 dry-run 비교와 승인 후 활성화)

생성일: 2026-06-13 · 작성: QA 자동화 (bmad-qa-generate-e2e-tests) · 프레임워크: pytest

- **Feature under test:** `src/rider_server/migration/cutover.py` (FR-3, NFR-22·24·25, ADD-16 6~8단계)
- **Test command:** `.venv/Scripts/python.exe -m pytest -q` (`pyproject.toml` `pythonpath=["src"]`)
- **Role:** QA 자동화 — 테스트 생성 전용(코드 리뷰/스토리 검증 아님)

## 컨텍스트

Story 3.8은 순수 함수/frozen 값 객체로 된 마이그레이션 cutover 레이어다(API 엔드포인트·UI 없음).
따라서 "E2E"는 **dry-run → 기준선 비교 → 승인 게이트 → cutover → rollback** 상태머신 플로우를
fake `crawl_snapshot`·in-memory 값 객체로 검증하는 함수 레벨 E2E다(외부 브라우저/Telegram 0).
Dev가 37건을 산출(전 AC 1차 커버)했고, QA는 미커버 경계 8건을 자동 보완했다.

## Generated Tests

### `tests/server/test_cutover.py` (additive — Dev 37 + QA 갭 8 = 45건)

기존 37건은 Dev 산출(무변경). QA 자동 보완 8건:

- [x] `test_cutover_errors_are_valueerror_subclasses` — `CutoverApprovalError`/`DualSendError`가 `ValueError` 하위(호출부 `except ValueError` 일관 포착) + 구별 타입 (AC2·AC3)
- [x] `test_run_dry_run_injects_message_and_snapshot_ids` — 주입 `message_id`/`snapshot_id`가 `Message`로 흐름(내부 uuid4() 금지) (AC4)
- [x] `test_run_dry_run_with_now_none_still_no_send` — `now=None` 분기(렌더러 기존 now() 보존)에서도 no-send·렌더 정상 (AC4)
- [x] `test_run_dry_run_coupang_reuses_renderer_not_reimplements` — 복잡한 쿠팡 `PerformanceSnapshot` 경로도 3.3 렌더러 바이트 동등(재구현 0) (AC4)
- [x] `test_compare_uses_result_target_id_not_seed` — 비교 `target_id`는 dry-run 결과에서 도출(seed 식별자가 아님) (AC2)
- [x] `test_activate_cutover_with_no_rules_succeeds` — 빈 rules cutover → ACTIVE·빈 `enabled_rules` (AC3)
- [x] `test_activate_cutover_guard_breadcrumb_uses_legacy_alias_without_rules` — `_target_id_for` fallback(legacy_alias) breadcrumb 도출 (AC3)
- [x] `test_roll_back_cutover_with_no_rules_preserves_logs` — 빈 rules rollback → 빈 `disabled_rules`·dedup 로그 보존·ROLLED_BACK (AC3)

## 적용한 갭 (auto-applied)

| 갭 | AC / 요구사항 | 자동 테스트 |
| --- | --- | --- |
| 승인/동시전송 예외가 `ValueError` 하위라는 계약 미검증(호출부 일관 포착 전제) | AC2·AC3 | `test_cutover_errors_are_valueerror_subclasses` |
| 주입 id(`message_id`/`snapshot_id`)가 `Message`로 흐르는지 미검증(uuid4 금지 결정성) | AC4 | `test_run_dry_run_injects_message_and_snapshot_ids` |
| `now=None` 분기(렌더러 기존 now() 보존) 미커버 — 전 케이스가 고정 now 주입 | AC4 | `test_run_dry_run_with_now_none_still_no_send` |
| 쿠팡 `PerformanceSnapshot` 렌더 재사용 바이트 동등 미검증(배민만 커버) | AC4 | `test_run_dry_run_coupang_reuses_renderer_not_reimplements` |
| 비교 `target_id` 출처(결과 vs seed) 미고정 | AC2 | `test_compare_uses_result_target_id_not_seed` |
| 빈 rules cutover 경계 + `_target_id_for` fallback(legacy_alias/mapping) 미커버 | AC3 | `test_activate_cutover_with_no_rules_succeeds`, `test_activate_cutover_guard_breadcrumb_uses_legacy_alias_without_rules` |
| 빈 rules rollback 경계 미커버 | AC3 | `test_roll_back_cutover_with_no_rules_preserves_logs` |

설계 원칙: 프로젝트 규칙대로 **실제 secret을 테스트에 하드코딩하지 않는다** — 고정
`datetime(2026,1,5,14,2)`·가짜 64-hex hash·가짜 id·legacy 별칭(`크롤링1`)만 사용.

## Coverage

| AC | 영역 | Dev | QA 추가 |
| --- | --- | --- | --- |
| AC1 | dry-run no-send·배민/쿠팡·실패 전파 | ✅ 7 | — |
| AC2 | 기준선 hash 비교·승인 게이트·상태머신 | ✅ 10 | +2 (예외 계층·target_id 출처) |
| AC3 | 동시전송 가드·cutover·rollback dedup 보존 | ✅ 11 | +4 (예외 계층·빈 rules×2·breadcrumb fallback) |
| AC4/AC5 | 재사용·결정성·비노출·frozen·재노출 | ✅ 9 | +3 (id 주입·now=None·쿠팡 재사용) |

cutover 레이어 공개 심볼 12개 전부 + 모든 AC 분기 커버. 신규 갭으로 예외 타입 계약·주입 id
흐름·`now=None` 분기·쿠팡 렌더 재사용·`_target_id_for` fallback·빈 rules 경계를 추가 잠금.

## Validation Results

- `pytest tests/server/test_cutover.py -q` → **45 passed** (37 → 45, +8)
- `pytest -q` (전체) → **994 passed** (기준선 986 → 994, 회귀 0; HEAD `73bb897` 재측정)
- `git diff -w` 범위: `rider_crawl/`·`services/`·`runner.py`·`domain/`·`pyproject.toml` **0줄 변경**
  (QA 변경분은 `tests/server/test_cutover.py` 추가뿐 — 순수 additive)
- 평문 secret grep(신규 테스트) = **0건**; `rider_crawl → rider_server` 역import = **0건**;
  `test_rider_crawl_never_imports_rider_server` = **passed**
- 실행: Windows venv `.venv/Scripts/python.exe -m pytest`

## Next Steps

- CI에 포함(외부 입력·네트워크 의존 0 — CI 친화적).
- Epic 5 운영 cutover(DB 영속·async·kill switch·canary·Admin dry-run render UI·legacy 폴러
  물리 종료) 도입 시 실런타임 E2E를 별도 추가 — 본 스위트 범위 밖(스토리 범위 경계 준수).
