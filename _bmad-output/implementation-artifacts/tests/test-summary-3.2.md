# Test Automation Summary — Story 3.2 (Snapshot 정규화 + fail-closed)

생성일: 2026-06-13 · 작성: QA 자동화 워크플로(bmad-qa-generate-e2e-tests) · 대상: Noah Lee · 프레임워크: pytest

> 기본 출력 파일 `test-summary.md` 는 Story 1.1 기록이 점유 중이라, 그 기록을 보존하기 위해 본 스토리는 `test-summary-3.2.md` 로 분리 저장했다.

## 테스트 프레임워크

- **pytest** (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`).
- 실행: `.venv/Scripts/python.exe -m pytest -q` (WSL `python3` 미설치 — venv 인터프리터 사용).
- 본 스토리는 순수 동기 도메인/서비스라 **API/서비스 테스트만** 해당. UI(tkinter) 경로 없음 → **E2E(브라우저) 테스트 비해당**.

## 검증한 기능

`SnapshotNormalizer.normalize()` — `CrawlSnapshotResult`(배민 `CurrentScreenSnapshot` /
쿠팡 `PerformanceSnapshot`)를 도메인 `Snapshot` 레코드로 wrapping + 필수데이터 누락 시
`MissingSnapshotDataError` fail-closed 게이트.

- 대상 코드: `src/rider_server/services/snapshot_normalizer.py`, `src/rider_server/domain/snapshot.py`, `src/rider_server/domain/states.py` (`SnapshotQualityState`)
- 테스트 파일: `tests/server/test_snapshot_normalize.py`

## 생성/보강한 테스트

기존 17 케이스가 happy path·핵심 fail-closed·보존을 이미 커버하고 있었고, QA 워크플로는
**구현 분기/계약 커버리지 구멍 8건**을 추가했다(전부 통과).

### API/서비스 테스트 (gap fill)

- [x] `test_fail_closed_cases_raise_and_inherit_base_exceptions` (parametrize ×5) — 5개 fail-closed 입력(raw None / 배민 center_name 빈값·None / 쿠팡 `peak_dashboard` None / 예상 외 타입)이 모두 `MissingSnapshotDataError` 이자 base(`MissingPerformanceDataError`)·`ValueError` 로 잡힘(AC2 계승)
- [x] `test_normalized_json_excludes_quality_meta` — `normalized_json` 이 `quality_state`/`parser_version`(품질 메타)을 포함하지 않고 parser 출력 키와 정확히 일치(AC1/AC3)
- [x] `test_snapshot_quality_state_str_enum_contract` — `SnapshotQualityState` `(str,Enum)`·이름==값·`OK`/`MISSING_REQUIRED` 멤버 계약(AC1/Task1)
- [x] `test_normalize_coupang_preserves_tracking_and_is_deterministic` — 쿠팡 경로의 추적필드·`target_id`·`collected_at` 보존 + 결정성(배민에만 있던 대칭 갭)

### 메운 커버리지 갭 (근거)

| # | 갭 | AC/근거 | 심각도 |
|---|-----|---------|--------|
| G1 | 쿠팡 `peak_dashboard is None` fail-closed 분기 테스트 0건 (`_require_present` PerformanceSnapshot 분기) | AC2 | High |
| G2 | 비-`None` 실패 케이스의 예외 계승(`MissingPerformanceDataError`/`ValueError`) 미검증 — Task5 "두 케이스 모두 계승 확인" | AC2 | High |
| G3 | `center_name is None` 분기 미검증(기존 parametrize는 `""`/공백만) | AC2 | Med |
| G4 | `normalized_json` 이 품질 메타를 포함하지 않음 미검증 — architecture.md(301)와 갈린 Dev Notes(130) 결정 잠금 | AC1/AC3 | Med |
| G5 | `SnapshotQualityState` `(str,Enum)`·값 계약 + `MISSING_REQUIRED` 멤버 미검증 | AC1/Task1 | Med |
| G6 | 쿠팡 추적/`target_id`/`collected_at` 보존·결정성 대칭 부재 | AC1/AC2 | Low |

## 커버리지

- Story 3.2 테스트 파일: **17 → 25 케이스** (+8, 전부 통과)
- AC 매핑: AC1(정규화 필드·추적성·`normalized_json` 보존)·AC2(fail-closed 4입력+계승+Message 미진입)·AC3(fixture 동등성·품질 메타 분리)·Task1(enum 계약) — 모두 자동 테스트로 커버
- 전체 스위트: **825 → 833 passed, 0 failed** (`.venv/Scripts/python.exe -m pytest -q`)

## 범위·안전 확인

- `src/rider_crawl/`·`pyproject.toml` **0줄 변경**(`git diff -w --numstat` 빈 출력) — 순수 additive, 테스트 1파일만 보강
- 신규 테스트 평문 secret 0건(봇 토큰 패턴 grep no match), 가짜 값/고정 `datetime`만 사용
- 외부 호출 없음(브라우저/텔레그램/카카오/Gmail 미호출) — fake/in-memory only
- 하드코딩 wait/sleep 없음, 테스트 독립(순서 의존 없음), 시맨틱 식별(타입/필드/예외 기반)

## 다음 단계

- CI에서 스위트 실행(현 환경은 `.venv/Scripts/python.exe`; 외부 입력·네트워크 의존 없어 CI 친화적).
- Story 3.3(Message·`snapshot_id`/`text_hash`)·3.5(dedup의 `snapshot_collected_at`)·Epic 5(snapshots 테이블 영속·런타임 wiring) 진행 시 본 `Snapshot` 계약 위에 additive로 테스트 확장.
