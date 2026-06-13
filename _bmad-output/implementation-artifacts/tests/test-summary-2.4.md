# Test Automation Summary — Story 2.4 (secret 값 분리)

워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화 엔지니어(테스트 생성 전용, 코드 리뷰 아님)
대상 스토리: `_bmad-output/implementation-artifacts/2-4-secret-값-분리-설정-파일은-ref만-보관.md`
일자: 2026-06-13 · baseline_commit: `f9937e8`

## Test Framework

기존 **pytest** 사용(`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`).
실행기: `.venv/Scripts/python.exe -m pytest -q` (WSL `python3`는 pytest 미설치 — 사용 금지).
이 스토리는 데스크톱 `tkinter` 앱이라 브라우저 E2E가 아니라 **seam 레벨 end-to-end 행위 테스트**
(load→migrate→save→resolve→to_app_config 라운드트립)로 커버한다(외부 호출 0, `tmp_path` 주입).

## AC ↔ 테스트 커버리지 매트릭스

| AC | 보장 | 기존(dev) 테스트 | QA 갭 보강(신규) |
|---|---|---|---|
| AC1.1 | save 시 평문 0·`*_ref`만 | `test_save_strips_plaintext_secret_and_writes_only_ref`, `test_save_all_strips_plaintext_secret_and_preserves_format` | — |
| AC1.2 | 마이그레이션 평문 미복사 | `test_load_all_migrates_legacy_plaintext_secret_to_ref_only`, `test_load_single_migrates_legacy_plaintext_to_ref_only` | — |
| AC1.3 | to_app_config 바이트 동일 resolve | `test_ref_only_file_resolves_to_byte_identical_plaintext_in_app_config` | **`test_secret_persistence_survives_fresh_store_instances_restart`** (새 인스턴스/디스크 정본 = 프로세스 재시작) |
| AC2.4 | secret 3분류 매핑 | `test_classification_maps_five_secret_kinds_to_three_buckets`, `test_classification_values_are_exactly_three_distinct_strings` | **`test_otp_is_not_stored_and_excluded_from_store_handled_fields`** (OTP 비저장 구조 잠금) |
| AC2.5b | redaction `*_ref` 보존·어간 마스킹 | `test_story_2_4_secret_ref_keys_preserved_and_plaintext_keys_masked` | — |
| AC2.5c | Gmail OAuth = 경로(ref)만 | `test_gmail_oauth_token_is_path_ref_only_in_settings_json` | — |
| AC3.6 | 주입 가능 seam·별도 파일 | `test_put_resolve_round_trip`, `test_store_file_is_separate_from_settings_file` | **`test_default_store_wiring_writes_separate_secrets_file`** (ui.py 기본 wiring), **`test_save_all_keeps_per_tab_secrets_isolated_with_distinct_refs`** (다중 탭 격리) |
| AC3.7 | fail-closed·MVP 한계 | `test_resolve_missing_ref_returns_none_fail_closed`, `test_resolve_missing_store_value_is_fail_closed_empty` | **`test_save_strips_secret_with_content_ref_when_no_target_id`** (ID 없는 fail-safe), **`test_save_without_secrets_issues_no_ref_and_creates_no_store_file`** |
| AC6 | put/resolve 회전 | `test_put_idempotent_does_not_rewrite_when_value_unchanged` | **`test_put_with_reused_ref_updates_stored_value`** (같은 ref·값 변경 갱신) |
| 무회귀 | dedup scope key 불변 | `test_message_scope_key_unchanged_by_secret_store_roundtrip` | — |

## 발견·자동 적용한 갭 (7건)

dev 테스트가 이미 AC 행복경로를 잘 덮어, 남은 갭은 **경계·격리·지속성·운영 기본 wiring**에 집중됐다.

1. **다중 탭 secret 격리** — 두 활성 탭의 서로 다른 token이 ref 충돌 없이 각자 값으로 resolve(탭 간 누출 0). `test_save_all_keeps_per_tab_secrets_isolated_with_distinct_refs`
2. **프로세스 재시작 지속성** — 기존 테스트는 같은 store 인스턴스를 재사용. 신규 `UiSettingsStore`+`LocalFileSecretStore`가 같은 디스크 경로를 다시 읽어 바이트 동일 resolve. `test_secret_persistence_survives_fresh_store_instances_restart`
3. **운영 기본 store wiring** — backend 미주입(=ui.py 경로)이 설정 파일 옆 **별도** `secrets.local.json`을 만들고 `ui_settings.json`엔 평문 0. `test_default_store_wiring_writes_separate_secrets_file`
4. **ID 없는 fail-safe** — `monitoring_target_id`가 없는 설정도 내용 기반 fallback ref로 평문이 직렬화되지 않음. `test_save_strips_secret_with_content_ref_when_no_target_id`
5. **무-secret 가드** — secret이 없으면 ref 미발급·store 파일 미생성(불필요한 secret 산출물 0). `test_save_without_secrets_issues_no_ref_and_creates_no_store_file`
6. **store 레이어 회전** — 같은 ref에 다른 값 put 시 갱신(반쪽 마이그레이션 재이관 정합). `test_put_with_reused_ref_updates_stored_value`
7. **OTP 비저장 구조 잠금** — `otp`가 store 영속 필드(`_SECRET_FIELDS`)에 없음을 단언(store는 token/password/login-id만). `test_otp_is_not_stored_and_excluded_from_store_handled_fields`

## Generated Tests

### tests/test_ui_settings.py (수정 — 5건 추가)
- [x] `test_save_all_keeps_per_tab_secrets_isolated_with_distinct_refs`
- [x] `test_secret_persistence_survives_fresh_store_instances_restart`
- [x] `test_default_store_wiring_writes_separate_secrets_file`
- [x] `test_save_strips_secret_with_content_ref_when_no_target_id`
- [x] `test_save_without_secrets_issues_no_ref_and_creates_no_store_file`

### tests/test_secret_store.py (수정 — 2건 추가)
- [x] `test_put_with_reused_ref_updates_stored_value`
- [x] `test_otp_is_not_stored_and_excluded_from_store_handled_fields`

## Coverage

- AC: AC1·AC2·AC3·AC6·AC7 + 무회귀 보장 모두 테스트로 잠금(행복경로 = dev, 경계/격리/지속성 = QA 보강).
- 테스트 수: `test_ui_settings.py` 52→57, `test_secret_store.py` 9→11.
- **전체 스위트: 663 → 670 passed**(신규 7건만 증가, 기존 통과 회귀 0 — NFR-20).

## 검증 결과

- 실행: `.venv/Scripts/python.exe -m pytest -q` → **670 passed in ~4s**.
- 범위: 이번 세션은 **테스트만** 변경(제품 코드 무수정). `src/rider_crawl/*`의 diff는 dev 단계의
  기존 미커밋 작업이며 본 QA 세션이 추가한 것 아님.
- secret 비노출(A1): 신규 테스트 값은 가짜값(`tok-fake`/`pw-fake`/`id-fake`/`tok-a`/`tok-b`/`pw-old`/`pw-new`)만.
  실 봇 토큰·`chat_id=<digits>`·KR 휴대폰 패턴 grep 0건.
- 격리: 모든 신규 테스트는 `tmp_path`(+주입/기본 store) 사용 — 실 `runtime/`·`ui_settings.json`·`secrets.local.json` 미변형, 순서 의존 없음, sleep/대기 없음.

## Next Steps

- CI에서 동일 스위트 실행(현재 CI 파이프라인 부재 — 필요 시 `bmad-testarch-ci`).
- 정식 `SecretRef`/`PlatformAccount`(Story 2.5), 마이그레이션 러너(Story 2.7), 실제 DPAPI(Epic 4)·
  AWS Secrets Manager(Epic 5) 구현 시 이 seam 테스트를 백엔드 계약 테스트로 확장.
