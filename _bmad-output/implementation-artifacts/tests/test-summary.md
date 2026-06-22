# Test Automation Summary — Story 5.9 (7개 모니터링 지표·알림과 운영 runbook)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`) · 모드: 발견 갭 자동 적용

> 본 파일은 QA 런 정본이며 5.9 스냅샷이다. **제품 코드 무변경 — 테스트만 추가.**

## 결과 요약

| 구분 | dev-exit | post-QA(현재) | 증감 |
| --- | --- | --- | --- |
| 전체 스위트 passed | 1854 | **1862** | **+8** |
| 전체 스위트 skipped | 48 | 48 | 0 |
| 5.9 지표 스위트 always-run | 39 | **47** | **+8** |
| 5.9 PG-gated(skip) | 7 | 7 | 0 |

전체 회귀: `1862 passed, 48 skipped` — 신규 갭 테스트 8개, **회귀 0**. 이 수치가 review 정본(dev-exit 1854 는 QA 추가로 stale — memory/stale-test-count-a2). 모든 신규 테스트는 **always-run**(무 DB)이라 CI PG skip 환경에서도 실행된다.

## 발견·채운 갭

기존 always-run 은 임계 재사용(drift 0)·비식별·4 최소 알림 발화·runbook 존재를 잘 잠갔으나, **(a) 4개 알림 중 `api_error_rate` 심각도가 단정되지 않음, (b) `auth_required` 의 OR 조건(auth/gmail 동시) 중복 발화 미검증, (c) AC2 "결정적"의 알림 순서·중복 0 미검증, (d) AC1 "≥1" 임계 상수·`Alert`/`MetricsSnapshot` frozen 불변식 미검증, (e) 조립 레이어의 정확 경계(offline 2분·freshness ×2/×4)와 빈 fleet 분기가 seeded 데이터로만 우회되어 미검증**에 갭이 있었다.

### `tests/server/test_metrics_policy.py` (+5) — AC2 알림 정책

- `test_api_error_rate_severity_is_critical` — 4개 최소 알림 중 **api_error_rate 심각도**(crawl breaker·telegram 급증 양 경로 CRITICAL). 기존 `test_alert_severity` 는 agent_offline/queue_lag/auth_required 3종만 단정.
- `test_auth_required_fires_exactly_once_when_both_signals_set` — `auth_required_count≥1` **AND** `gmail_reauth≥1` 이어도 `auth_required` 알림은 **정확히 한 번**(OR 중복 발화 차단).
- `test_all_four_alerts_fire_in_canonical_order_without_duplicates` — AC2 "결정적": 전 조건 발화 시 코드 순서가 `MINIMUM_ALERT_CODES` 정본 순서와 일치하고 **중복 코드 0**.
- `test_alert_threshold_minimums_lock_ac1_ge_one` — AC1 "≥1" 임계(`AUTH_REQUIRED_ALERT_MIN`/`GMAIL_REAUTH_ALERT_MIN`/`TELEGRAM_ERROR_ALERT_MIN` == 1) 잠금.
- `test_snapshot_and_alert_are_frozen_immutable` — `Alert`/`MetricsSnapshot` frozen 불변식(우발적 변이 차단).

### `tests/server/test_metrics_service.py` (+3) — AC1 조립 경계

- `test_assemble_agent_offline_uses_strict_two_minute_boundary` — AC1 #1 "2분 **초과**"만 offline: 정확히 2분 경과는 online(severity 정본 `is_agent_online` 재사용을 조립 레이어에서 잠금). 기존 seeded 테스트는 None/30s/5min 만 써 경계 미검증.
- `test_assemble_freshness_uses_strict_x2_x4_boundaries` — AC1 #2 "×2→warning / ×4→critical" 경계: 정확히 ×2 → NORMAL(미경보), 정확히 ×4 → WARNING(critical 아님), ×4 초과 → CRITICAL. 기존 seeded 테스트는 25/50min 만 써 정확 경계 미검증.
- `test_assemble_empty_fleet_is_all_zero_with_none_oldest_age` — 빈 fleet 조립: `oldest_heartbeat_age_seconds is None`(ages 빈 분기) + 전 카운트 0 + 알림 0.

## 설계 관찰(결함 아님 — 코드 리뷰 메모)

- **라우트 실 `now()`**: `/metrics/operational` 은 주입 불가한 실시간 `now()` 라 라우트 테스트로 시간 의존(warning/critical 시점)을 단정하지 않고, 순수 policy/service 가 `now` 주입으로 잠근다(memory/admin-routes-wallclock-severity, jobs-complete-route-wallclock-now 선례).
- **`evaluate_alerts(now=)` 미사용**: 시그니처 대칭용 — facts 가 조립 단계에서 시각 해석을 마쳐 알림은 임계 비교만 한다(`del now`). 기존 `test_evaluate_alerts_accepts_now_kwarg` 유지.
- **gmail_reauth 근사**: 서버에 Gmail 전용 상태가 없어 쿠팡 미해결 `auth_session` 으로 근사하며 한계는 `auth_required.md` 에 명시(임의 enum/컬럼 신설 0).

## 커버리지(5.9 AC별)

- **AC1 7지표 노출·비식별**: 7지표 JSON 노출·집계 수치만·식별 텍스트 0·payload 키(기존) + 조립 경계(offline 2분·freshness ×2/×4·빈 fleet) 보강.
- **AC2 4개 최소 알림**: 발화 경계·임계 재사용 identity/동등(기존) + 심각도(api_error_rate)·dedup·결정적 순서·임계 ≥1·불변식 보강.
- **AC3 7종 runbook + 분류**: `test_runbooks_present.py` 가 7파일 존재 + FailureCategory 7종 참조로 완료 위조 차단(변경 없음).
- **경계 가드**: `test_metrics_boundary.py` 읽기 전용·단방향 import·third-party 허용집합 AST 가드(변경 없음).

## lock 무회귀

14표 lock·신규 컬럼/테이블/Alembic 마이그레이션 0·`domain/states.py` enum count-lock(`FailureCategory`7 등) 유지·단방향 import(`metrics/` → `rider_agent` 0)·임계 drift 0(severity/scheduler 정본 재사용)·기존 `/metrics`·`/health`·`/version` 회귀 0 — 신규 enum/deps 0.

## 검증

- 2개 보강 파일: `pytest tests/server/test_metrics_policy.py test_metrics_service.py` → **36 passed**
- 5.9 지표 스위트(5파일): **47 passed / 7 skipped**
- 전체 스위트: **1862 passed / 48 skipped** ✅ · 회귀 0

## Next Steps (PG 환경 필요 — 현 WSL/venv `TEST_DATABASE_URL` 부재로 미추가)

현 환경에서 검증 불가한 PG-only 코드 가지라 신규 테스트를 추가하지 않았다(체크리스트 "모든 테스트 통과" 보존). 실 PostgreSQL CI 잡에서 별도 seed 로 보강 권장:

- `PostgresMetricsRepository.kakao_queue_lag_seconds` — 대기 KAKAO_SEND 부재 시 `0` 반환 분기(`oldest_run_after is None`) 및 미래 `run_after` 음수 clamp(`max(0, …)`).
- `PostgresMetricsRepository.telegram_error_count` — `sent_at IS NULL`(전송 자체 실패) 포함 OR 가지(현 seed 는 `sent_at` 값만 사용해 미실행).
- 정본 테스트 카운트(1862)는 review 단계에서 재측정해 Dev Agent Record 와 일치시킨다.
