# Test Automation Summary — Story 5.10 (100 fake target 부하 smoke와 negative safety test)

**Workflow:** bmad-qa-generate-e2e-tests · **Role:** QA 자동화 엔지니어 (테스트 생성만, 코드 리뷰/스토리 검증 제외)
**Date:** 2026-06-14 · **Framework:** pytest (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`)
**실행:** `.venv/Scripts/python.exe -m pytest -q` (WSL + Windows venv)

## 컨텍스트 — 검증 스토리

5.10은 새 기능이 아니라 5.1~5.9 안전장치의 **출시 전 통합·입증** 스토리다. AC1 smoke·AC2 7
시나리오·AC3 검증 항목은 거의 전부 통과 테스트로 이미 존재(추적성 매트릭스로 잠금). 유일한
신규 기능 코드는 **전역 dispatch kill switch 배선**(`test_send` service+route)이다. 따라서 QA
gap-fill 은 그 신규 코드의 **불변식 경계**에 집중했다(재구현 0, 기존 정본 테스트 무변경).

## Generated / Augmented Tests

### Gap-fill (이번 QA 세션 신규 — `tests/server/test_kill_switch_5_10.py` 말미)

- [x] `test_kill_switch_block_does_not_consume_reserve_key_so_later_send_succeeds`
  — **G1 / 게이트레일 #3.** kill switch 가 `deliver_once` 진입 **전** short-circuit 하므로 차단 시
  `reserve`(dedup key)를 소비하지 않음(`reserve_calls == []`). 재활성화 후 같은 key 로 정상 발송
  성공 → "차단이 dedup key 를 오염시켜 영구 `DUPLICATE_BLOCKED`" 회귀를 잠금.
- [x] `test_route_test_send_fail_closed_when_sending_enabled_attr_unset`
  — **G2 / 게이트레일 #4.** `getattr(app.state, "sending_enabled", False)` 의 기본 차단 분기를
  강제(app.state 에 플래그 미설정) → seam 미호출·DENIED audit 정확히 1건. 기존 테스트는 플래그를
  항상 명시 설정해 이 defense-in-depth 기본값이 미커버였다.
- [x] `test_blocked_test_send_audit_diff_records_reason_without_leak`
  — **G3 / 게이트레일 #5(secret 위생)+관측성.** 차단 DENIED audit `diff_redacted` 가 redaction
  통과 dict 로 차단 사유(`sending_enabled=False`)·미발송 상태(`HELD`)·불투명 channel_id 만 기록.

### 무변경 유지(재구현 0 — dev/정본 자산)

- `tests/server/test_kill_switch_5_10.py` 의 dev 작성 7건(service/route happy+block, AND 진리표, retry enqueue-only 경계)
- `tests/server/test_scheduler_tick.py` AC1 smoke(`test_5_10_*` 2건 + 5.4 smoke 2건)
- `tests/negative/test_safety_matrix.py` AC2/AC3 추적성 매트릭스(AST 정본 존재 확인)

## Coverage

| AC | 영역 | 상태 |
| --- | --- | --- |
| AC1 | 100-target single-tick smoke (enqueued==100, all PENDING, ≥85 distinct sec, 2주기) | ✅ 정본 + 5_10 신규(dev) |
| AC2 | 7개 negative safety 시나리오 추적성(매트릭스 AST 잠금) | ✅ 정본 참조 |
| AC3 | 마이그레이션·운영 안전 검증 + pause + **kill switch** | ✅ 정본 + kill switch 배선 |
| AC3 | kill switch 불변식 경계 (reserve 미소비 / unset fail-closed / audit 위생) | ✅ **QA gap-fill 신규 3건** |

- 신규 third-party deps **0** · 신규 DB 컬럼/테이블/Alembic/enum 멤버 **0** · 소스 코드 변경 **0**(QA 는 테스트만)
- `git diff -w`: QA 변경은 `tests/server/test_kill_switch_5_10.py` 1파일(append)뿐 (CRLF/LF noise 없음)

## 실측 Test Count (qa-e2e 시점 — `stale-test-count-a2` 패턴)

- **전체 회귀: `1915 passed, 48 skipped, 0 failed` (11.02s)**
- dev 시점 1912 passed → QA gap-fill **+3** always-run. PG-gated 48 skip 불변(`TEST_DATABASE_URL` 미설정).
- 스토리 5.10 관련 파일 단독: `73 → 76 passed`(kill switch 10 + safety matrix + scheduler tick).

## Next Steps

- CI 에서 동일 회귀 실행(always-run +3). PG 환경에서는 매트릭스 PG-gated 정본도 실행 권장.
- 향후 중앙 dispatch 런타임 루프 도입 시: 그 실 `send` 호출부에 동일 `effective_send_enabled`
  게이트 compose 필수(현재 service/route 주석으로 명시). 그 시점에 본 G1/G2/G3 패턴을 새 chokepoint 로 확장.
