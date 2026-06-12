# Test Automation Summary — Story 2.2

**Story:** 대상별 상태 경로 분리(`targets/<monitoring_target_id>`) + 설정 JSON atomic write + 로그 rotation·보존
**Workflow:** `bmad-qa-generate-e2e-tests` (QA 자동화 — 갭 보강)
**Date:** 2026-06-13
**Framework:** pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`)
**Test type:** 데스크톱(`tkinter`) 앱 — HTTP API 없음. "E2E" = 기능 동작(상태 경로 파생·atomic write·rotation) 통합/단위 테스트. 외부 브라우저/텔레그램/카카오/Gmail 미호출, 전부 `tmp_path`·`monkeypatch`.

## Result

- **전체 스위트: 618 passed** (기준선 613 + 신규 갭 5), 회귀 0.
- API 테스트: **N/A** — 이 프로젝트는 HTTP 엔드포인트가 없는 로컬 데스크톱 앱.

## Coverage analysis (AC → 테스트)

| AC clause | 기존 커버리지 | 갭 |
|---|---|---|
| AC1 #1 `targets/<id>` 경로 사용 | `test_state_subdir_uses_monitoring_target_id_when_present`, `test_app_configs_from_settings_uses_target_id_state_subdir_when_issued`, `test_last_message_path_follows_target_id_not_tab_order` | — |
| AC1 #2 빈 id 폴백·`targets/` 충돌 방지 | `test_state_subdir_falls_back_to_crawling_index_when_id_blank`, `test_state_subdir_blank_id_does_not_collapse_to_targets_slash` | — |
| AC1 #3 run_lock 무변경(브라우저 스코프) | `test_run_once_blocks_..._different_state_subdirs` (행위적) | **G4** — 경로 수준 직접 락 없음 |
| AC2 #4 강제 종료 무손상·temp 정리 | `test_save_all_atomic_preserves_original_on_replace_failure` (save_all/replace만) | **G1** save() 미커버, **G2** fsync 실패 지점 미커버 |
| AC2 #5 직렬화 형식 불변 | `test_save_all_atomic_preserves_serialization_format` + 라운드트립 | — |
| AC2 #6 persist-on-issue 자동 atomic | load_all 발급 테스트(통과 경로) | — (간접) |
| AC3 #7 rotation·보존 | `rotate_if_needed` 단위 5종 + writer 2종 | — |
| AC3 #8 반환 계약 + rotation 실패 best-effort | writer rotation 2종(반환 계약) | **G3** rotation 자체 예외 시 best-effort 미커버 |

## Generated Tests (auto-applied gaps — 신규 5)

### G1 — AC2 #4: 단일 객체 `save()` atomic 강제종료 무손상
- [x] `tests/test_ui_settings.py::test_save_single_object_atomic_preserves_original_on_replace_failure`
  - `save_all`만 검증되던 것을 `save()`(load persist 경로)까지 확장. `os.replace` 직전 강제 종료 → 원본 바이트 불변 + `.tmp` 잔여물 0. 평면 객체(`"crawlings"` 래핑 없음) 형식도 함께 락.

### G2 — AC2 #4: `os.fsync` 실패 지점 cleanup(replace 이전 실패)
- [x] `tests/test_ui_settings.py::test_save_all_atomic_cleans_temp_and_preserves_original_on_fsync_failure`
  - 실패 분기가 `os.replace`보다 이른 `os.fsync`(temp는 쓰였으나 미교체)에서도 원본 보존 + temp unlink 확인. Task 4가 명시한 "`os.replace`(또는 `os.fsync`)" 중 미커버였던 후자.

### G3 — AC3 #8: rotation 자체 예외 시 두 writer best-effort
- [x] `tests/test_log_rotation.py::test_write_run_error_log_stays_best_effort_when_rotation_raises`
- [x] `tests/test_log_rotation.py::test_write_kakao_diagnostics_stays_best_effort_when_rotation_raises`
  - `rotate_if_needed`를 `RuntimeError` 던지도록 monkeypatch → writer가 예외를 전파하지 않고 best-effort 값(None 또는 기록 경로) 반환. AC3 #8 "rotation 실패가 에러/진단 경로를 폭주시키거나 예외로 터뜨리면 안 된다"를 직접 락. 두 갈래 폴백(append 시도/조용히 무시)을 모두 허용하도록 over-specify 회피.

### G4 — AC1 #3: run_lock 경로가 state_subdir과 독립(브라우저 스코프)
- [x] `tests/test_app.py::test_run_lock_path_is_browser_scoped_independent_of_state_subdir`
  - 행위적 동시성 테스트를 보완하는 경로 수준 직접 락: 같은 브라우저 스코프면 `state_subdir`(`targets/id-a` vs `targets/id-b`)이 달라도 동일 `_run_lock_path`, 그리고 lock 경로에 `targets/<id>`가 포함되지 않음("run_lock을 `targets/<id>` 아래로 옮기면 안 된다").

## Coverage metrics

- AC sub-clause: **8/8 직접 커버**(갭 보강 후). 보강 전 4개 절(AC1 #3, AC2 #4 ×2, AC3 #8)은 부분/간접만 커버.
- 신규 테스트: 5 (G1·G2 설정 atomic, G3 로그 rotation best-effort ×2, G4 run_lock 경로 불변)
- 전체 스위트: 613 → **618 passed** (회귀 0).

## Quality gates

- secret 비노출(A1): 신규 테스트 값은 승인된 가짜값(`"token"`, `"-100123"`, `"-100999"`)·더미 텍스트만. 봇 토큰/`chat_id=`/휴대폰 평문 grep 0.
- 격리: 모든 신규 테스트 `tmp_path` + `monkeypatch`(자동 복원), 순서 의존성 없음, sleep/하드코딩 대기 없음.
- 범위: 본 워크플로는 **테스트 파일만** 추가(`test_ui_settings.py`·`test_log_rotation.py`·`test_app.py`). 제품 코드(`src/`) 무변경.

## Next Steps

- CI에서 전체 스위트 실행(`pytest -q`).
- 2.7(마이그레이션 러너) 구현 시 `targets/<id>` 폴더 복사·dedup seed 동작에 대한 통합 테스트를 별도 추가.
</content>
