# Test Automation Summary — Story 3.4 (DeliveryRule fan-out)

생성일: 2026-06-13 · 작성: QA automation (bmad-qa-generate-e2e-tests) · 언어: Korean

> 기존 `test-summary.md`(Story 1.1)를 보존하기 위해 본 스토리 요약은 story-scoped 파일로 둔다.

## 대상 기능

- **`DispatchFanoutService`** (`src/rider_server/services/dispatch_fanout_service.py`, P2-04 / FR-9)
  - `plan(message, rules, *, channels, job_id_for)` — 한 `Message` → 활성 `DeliveryRule` 마다 `DispatchJob` fan-out
  - `dispatch_all(message, jobs, *, send)` — 채널 격리 전송(한 채널 실패가 다른 채널 성공을 무효화하지 않음)
  - 값 객체 `DispatchJob` / `FanoutOutcome` (frozen), `UnknownChannelError(KeyError)` (fail-closed)

## 테스트 프레임워크

- **pytest 9.0.3** (Python 3.11.9, 운영 venv `.venv/Scripts/python.exe`). 프로젝트 기존 프레임워크 그대로 사용.
- UI 없음(순수 서버 서비스) → **E2E/브라우저 테스트 비해당**. API/서비스 단위 테스트로 커버.
- 위치: 평면 `tests/server/test_dispatch_fanout.py` (기존 컨벤션, `__init__.py` 미추가). 외부 호출 0 — fake `send`/in-memory 값.

## 생성된 테스트

### 서비스/계약 테스트 — `tests/server/test_dispatch_fanout.py`

기존 dev-story 10케이스 + QA gap 보강 **10케이스 = 총 20케이스** (전부 통과).

#### 기존 커버(10) — happy path·주요 AC
- [x] `test_plan_fans_out_one_message_to_at_least_two_channels` — AC1: ≥2채널 fan-out·필드 계약
- [x] `test_plan_excludes_disabled_rules` — AC1.2: `enabled=False` 제외
- [x] `test_plan_preserves_channel_dimension_for_scope_non_reduction` — AC3: `(target_id, channel_id)` 보존
- [x] `test_dispatch_all_isolates_channel_failure` — AC2: 첫 채널 실패 격리
- [x] `test_dispatch_all_all_success_when_no_failure` — AC2: 전체 성공
- [x] `test_dispatch_all_error_is_redacted_and_unclassified` — AC2/AC5: redaction·미분류·누출 방지
- [x] `test_plan_raises_on_unknown_channel` — fail-closed: dangling FK surface
- [x] `test_dispatch_job_and_outcome_are_frozen` — 불변성
- [x] `test_plan_is_deterministic` — 결정성(내부 `uuid4()`/`now()` 미호출)
- [x] `test_reexported_from_services_package` — 재노출/`__all__`

#### QA gap 보강(10, 신규) — 경계값·격리 강화·계약 잠금
- [x] `test_plan_returns_empty_when_no_rules` — **경계값**: 빈 `rules` → `[]`
- [x] `test_plan_returns_empty_when_all_rules_disabled` — **경계값**: 전부 비활성 → `[]`
- [x] `test_plan_skips_disabled_rule_before_channel_lookup` — **순서 계약**: 비활성 스킵이 채널 해석보다 선행(없는 채널이어도 비활성이면 raise 안 함)
- [x] `test_plan_preserves_order_with_interspersed_disabled` — [활성, 비활성, 활성] 순서 보존·스킵
- [x] `test_plan_fans_out_to_two_channels_of_same_messenger` — **AC3 강화**: 같은 messenger 라도 `channel_id` 로 distinct(scope 차원=channel_id)
- [x] `test_plan_unknown_channel_error_carries_channel_id_and_chains` — **fail-closed 강화**: 원인 `channel_id` surface + `KeyError` 체이닝(`__cause__`)
- [x] `test_dispatch_all_returns_empty_when_no_jobs` — **경계값**: 빈 `jobs` → `[]`, sender 미호출
- [x] `test_dispatch_all_isolates_middle_channel_failure` — **AC2 강화**: 3채널 중 가운데 실패가 앞·뒤 성공을 무효화하지 않음
- [x] `test_dispatch_all_contains_every_failure_independently` — **AC2 극단**: 전 채널 실패해도 각 1회 시도·독립 기록
- [x] `test_dispatch_all_does_not_swallow_base_exception` — **격리 경계 잠금**: `except Exception` 은 `KeyboardInterrupt`(BaseException) 전파(제어흐름 예외 미삼킴)

## 발견·보강한 커버리지 갭(auto-applied)

| 영역 | 갭(보강 전 미커버) | 보강 테스트 |
|---|---|---|
| `plan` 경계값 | 빈 rules / 전부 disabled → `[]` | 2케이스 |
| `plan` 순서·스킵 계약 | 비활성-스킵이 채널 해석보다 선행 / interspersed 순서 | 2케이스 |
| AC3 scope 비축소 | 동일 messenger·다른 channel_id 도 distinct | 1케이스 |
| fail-closed 상세 | 예외가 원인 channel_id·cause 체인을 보존 | 1케이스 |
| `dispatch_all` 경계값 | 빈 jobs → `[]` | 1케이스 |
| AC2 격리 강화 | 가운데 실패 / 전체 실패 격리 | 2케이스 |
| 격리 경계 | BaseException 비삼킴(`except Exception` 잠금) | 1케이스 |

> 모든 갭은 **순수 additive**(신규 케이스만 추가, 기존 동작·소스 무변경)로 적용. dedup key 조립·실패 분류·도메인 변경 등 **스코프 밖 영역은 의도적으로 미선점**(3.5/3.6/Epic 5 위임 경계 준수).

## 커버리지

- **`DispatchFanoutService.plan`**: 활성/비활성/혼합·순서·빈입력·동일 messenger·unknown channel(체이닝)·결정성 — 분기 전부 커버.
- **`DispatchFanoutService.dispatch_all`**: 전체 성공·첫/가운데/전체 실패 격리·빈입력·redaction·`Exception` vs `BaseException` 경계 — 분기 전부 커버.
- **값 객체**: `DispatchJob`/`FanoutOutcome` frozen·필드 계약·재노출 커버.
- AC1·AC1.2·AC2·AC3·AC4(순수·결정·단방향·비노출)·AC5(미분류·redaction) 매핑 완료.

## 검증 결과

| 항목 | 결과 |
|---|---|
| 신규 파일 단독 | **20 passed** (`tests/server/test_dispatch_fanout.py`) |
| 전체 스위트 | **866 passed** (운영 venv `.venv/Scripts/python.exe -m pytest -q`) |
| 기준선 재확인(파일 제외) | **846 passed** (`--ignore=tests/server/test_dispatch_fanout.py`) → 순수 additive **+20** |
| 회귀 | 0 (특히 `test_run_once_split.py`·`test_domain_models.py`·`test_message_render.py`·`test_snapshot_normalize.py`·`test_app.py` 전부 통과) |
| 스코프(`git diff -w`) | 보호 소스(`rider_crawl/`·`domain/`·3.1~3.3 services·`pyproject.toml`) **0줄 변경** |
| 의존성 방향 | 단방향(`rider_server → rider_crawl`)만, ast 가드 통과 |
| 누출 grep | 신규 테스트에 신규 평문 secret **0건** (기존 redaction-증명 fake fixture만 존재) |
| 하드코딩 wait/sleep | 없음 |
| 테스트 독립성 | 순서 의존 0(자급자족 fixture) |

## 체크리스트(checklist.md) 검증

- [x] API/서비스 테스트 생성(해당) · [x] E2E 비해당(UI 없음, 사유 명시)
- [x] 표준 pytest API 사용 · [x] happy path 커버 · [x] 임계 에러 케이스 커버(채널 실패·unknown channel·BaseException)
- [x] 전체 테스트 통과(20/20, 전체 866) · [x] 의미 있는 로케이터(서비스 단위 — 직접 호출/필드 단언)
- [x] 명확한 설명(테스트명·주석) · [x] 하드코딩 wait/sleep 없음 · [x] 테스트 독립성 보장
- [x] 요약 작성 · [x] 적절한 디렉터리(`tests/server/`) 저장 · [x] 커버리지 지표 포함

## Next Steps

- CI에서 `tests/server/` 포함 실행(이미 `testpaths=["tests"]` 커버).
- 다운스트림 스토리에서 fan-out 위에 빌드 시 회귀 그물로 재사용: dedup key/insert-then-send=3.5, 채널별 실패 분류·재시도=3.6, 중앙 Telegram=3.7, jobs/delivery_logs 영속·async wiring=Epic 5, Kakao 실전송=Epic 4.
