# Test Automation Summary — Story 2.7 (기존 탭 설정의 안전한 마이그레이션 실행)

생성일: 2026-06-13 · 작성: QA 자동화 (bmad-qa-generate-e2e-tests) · 프레임워크: pytest

- **Feature:** `src/rider_server/migration/` 순수·결정적 마이그레이션 오케스트레이션 (ADD-16 1~5단계, FR-31, NFR-18·21·22)
- **Test file:** `tests/server/test_migration.py`
- **Run command:** `.venv/Scripts/python.exe -m pytest` (`pyproject.toml` `pythonpath=["src"]`, `testpaths=["tests"]`)

## 컨텍스트

Story 2.7은 **순수·결정적 마이그레이션 절차** 모듈이라 HTTP/UI 표면이 없다(FastAPI/SQLAlchemy/
async 의존 0) → 전통적 브라우저 E2E 대상이 아니다. 따라서 "API 테스트" = 모듈 공개 함수/값
객체 **계약 테스트**, "E2E" = `run_migration` 전체 흐름(백업→분류·발급→매핑→상태복사→seed)을
한 호출로 잠그는 **워크플로 테스트**로 실현했다. 기존 27개 테스트가 비운 분기를 **추가
(additive)** 로 채웠고, 구현·도메인·크롤러는 **무변경**(테스트 파일만 확장).

## Generated Tests

### API/Unit Tests (function-contract)
- [x] `tests/server/test_migration.py` — `run_migration`·상태 전이 5종·`back_up_settings`·`classify_and_issue`·`map_active_tab`·`copy_state_dir`·`seed_from_state`·값 객체(frozen) 계약 잠금

### E2E (full migration-orchestration workflow)
- [x] `test_run_migration_*` — 백업(원본 미삭제)→활성/비활성 분류→ID 3종 발급→`crawling{N}`→`targets/<id>` 복사→`last_message` hash→`MigrationSeed` 승계를 한 시나리오로 검증(off-by-one 가드 포함)

## 적용한 갭 (auto-applied, +10 cases, 27 → 37)

기존 27케이스가 happy-path와 헤드라인 불변식(백업 byte 충실, 분류, ID 멱등, 상태복사·seed 승계,
off-by-one, 승인 전 활성화 0, 직렬화 정본)을 잠갔다. 아래 **미커버 분기 10건**을 자동 보완했다.

| 갭 (이전 미커버) | AC / 불변식 근거 | 추가 테스트 |
|---|---|---|
| `pause`의 선행 상태(ACTIVE) 가드 (happy만 있었음) | AC3 / fail-closed ③ | `test_pause_on_non_active_raises` |
| `mark_dry_run_passed`의 선행 상태(MAPPED) 가드 | AC3 / fail-closed ③ | `test_mark_dry_run_passed_on_non_mapped_raises` |
| dry-run 건너뛴 MAPPED→APPROVED 직행 차단 | AC3 / fail-closed ③ | `test_approve_skipping_dry_run_raises` |
| 활성 0 경계(매핑 0이되 백업은 수행) | AC1 / NFR-18 | `test_run_migration_all_inactive_yields_no_targets` |
| 첫 발송 전 활성 탭 end-to-end(seed None·빈 대상 폴더) | AC2 fail-safe | `test_run_migration_active_tab_without_prior_state_has_no_seed` |
| 비관련 파일 무시 · hash 후행 공백 strip 분기 | AC2 / NFR-21 | `test_seed_from_state_ignores_unrelated_files_and_strips_hash` |
| `_copy_missing` 중첩 폴더 재귀(평면 파일만 있었음) | AC2 / NFR-18 | `test_copy_state_dir_copies_nested_subdirectories` |
| `MigrationResult`/`TargetMapping`/`MigrationSeed` frozen(이전엔 `TargetMigration`만) | Task 2 계약 | `test_value_objects_are_frozen` |
| 백업 충실도(NFR-18) vs 신규 산출물 평문 금지(ADD-15) 경계 — 문서화됐으나 미테스트 | NFR-18 / ADD-15 | `test_backup_preserves_plaintext_while_mapping_exposes_refs_only` |
| 두 번째 플랫폼(쿠팡) 중립 필드 매핑 정합(이전엔 배민만) | AC3 도메인 매핑 | `test_coupang_tab_maps_neutral_fields_consistently` |

> 설계 원칙: `(str, Enum)` 의 `str()`/f-string 출력은 Python 버전 민감 함정(Dev Notes 181)이라
> **단언하지 않았다** — 정본 직렬화 경로(`.value`/`==`/`json.dumps`)만 잠갔다(기존 케이스 유지).
> 모든 fixture는 가짜 ID·가짜 64-hex hash·명백한 가짜 평문(`fakeplainid`/`fakeplainpw`)·고정
> `datetime(2026,1,1)` 만 쓰고 실제 토큰/비밀번호/`chat_id`/휴대폰은 0건.

## Coverage

- **공개 API(`migration.__all__` 16심볼):** `run_migration`·`MigrationState`·`TargetMapping`·`MigrationSeed`·`TargetMigration`·`MigrationResult`·`back_up_settings`·`classify_and_issue`·`map_active_tab`·`copy_state_dir`·`seed_from_state`·`mark_dry_run_passed`·`approve`·`activate`·`pause`·`roll_back` — **16/16** 직접 또는 end-to-end 커버
- **AC1 (백업·분류·발급):** byte 충실 백업 + 원본 미삭제 + 활성-only 분류 + 패딩 제외 + ID 3종 멱등 + 활성 0 경계 + 백업 누락 `FileNotFoundError` — 완전
- **AC2 (상태복사·seed 승계):** `crawling{N}`→`targets/<id>` 복사(원본 미삭제·멱등·없는 파일만·중첩 재귀) + off-by-one 가드 + seed 추출(정상·None·공백 strip·비관련 파일) + 이전 상태 없음 fail-safe — 완전
- **AC3 (상태머신·승인 전 활성화 0):** 5개 전이 happy + 잘못된 선행 상태 `ValueError` 전수(pause·mark_dry_run·approve·activate) + `MAPPED` 정지 + frozen + 직렬화 정본 — 완전
- **도메인 매핑:** 배민·쿠팡 두 플랫폼 중립 필드 정합 + tenant_id 체인 + `SecretRef`(평문 0, ADD-15) + 미지 플랫폼 fail-closed — 완전
- **결정성/격리:** 전 케이스 `tmp_path`·고정 `datetime` 주입(실 `runtime/`·`logs/` 미접근, `now()`/`uuid4` 직접 호출 0)

## Results

- 신규 마이그레이션 테스트: **37 passed** (기존 27 + 신규 10)
- 전체 스위트: **786 passed** (기준선 776 + 10, **회귀 0**)
- 범위: `tests/server/test_migration.py` 만 확장 — `src/rider_crawl/`·`src/rider_server/domain/`·`pyproject.toml`·마이그레이션 구현 **무변경**(`git diff -w` 정합)
- 누출 검사: 봇 토큰(`\d{6,}:[\w-]{30,}`)/`chat_id=<digits>`/한국 휴대폰 평문 **0건**, 신규 매핑 산출물 평문 secret 0(ref만)

## Out of Scope (확인됨, 본 스토리 범위 아님)

- 실제 dry-run 렌더 비교(old vs new) → **Story 3.8**. 본 테스트는 `DRY_RUN_PASSED` 상태 전이만 검증.
- DeliveryRule 활성화·발송·DeliveryLog 영속·유니크 제약 → **Story 3.4/3.5 + Epic 5**. `MigrationSeed`는 seed 표현만 검증.
- scheduler 연동 → **Story 5.4**.

## Next Steps

- CI에서 동일 스위트 실행 (`bmad-testarch-ci` 미설치 — 수동 게이트 유지)
- Epic 3/5에서 `MigrationSeed`를 실제 `delivery_logs` 행으로 소비할 때, dedup key 나머지 필드(channel_id·collected_at·template_version) 합성 테스트 추가
</content>
