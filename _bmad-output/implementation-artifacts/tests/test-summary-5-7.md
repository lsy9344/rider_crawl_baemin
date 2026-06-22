# Test Automation Summary — Story 5.7 (Admin 수동 운영 액션과 고객/구독 상태 전이)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`) · 모드: 발견 갭 자동 적용

> 본 파일은 5.7 스냅샷이며, 동일 내용이 `test-summary.md`(QA 런 정본)에도 반영됐다. **제품 코드 무변경 — 테스트만 추가.**

## 결과 요약

| 구분 | dev-exit | post-QA(현재) | 증감 |
| --- | --- | --- | --- |
| 전체 스위트 passed | 1739 | **1757** | **+18** |
| 전체 스위트 skipped | 36 | 36 | 0 |
| Story 5.7 always-run | 35 | **53** | **+18** |
| Story 5.7 PG-gated(skip) | 6 | 6 | 0 |

전체 회귀: `1757 passed, 36 skipped` — 신규 갭 테스트 18개, **회귀 0**. 이 수치가 review 정본(dev-exit 35 는 QA 추가로 stale — memory/stale-test-count-a2).

## 발견·채운 갭

기존 35 always-run + 6 PG-gated 는 핵심 불변식(suspend/resume·HELD dispose·dedup 우회 0·fan-out 0·SUCCEEDED 거부)을 잘 잠갔으나, **(a) `assign_agent`·`auth_check` 가 PG-gated 에만 있어 always-run 부재, (b) tenant scope 가 구독/target 만 검증되고 retry/dispose 미검증, (c) 라우트 11개 중 5개만 커버(activate·test-crawl·auth-check·dry-run·assign·retry-happy·dispose-happy 미검증), (d) record형 audit(AGENT_ASSIGN/TEST_CRAWL/AUTH_CHECK/HELD dispose) 기록 미검증**에 갭이 있었다.

### `tests/server/test_admin_actions.py` (+15)

service(always-run, 무 DB · fake repo · 주입 시각/actor):
- `test_assign_agent_persists_affinity_and_audit` — Agent 배정 affinity 영속 + AGENT_ASSIGN audit(agent_id 불투명 id 보존). **이전 always-run 부재**(memory/pg-gated-files-hide-pure-helpers).
- `test_assign_agent_cross_tenant_blocked` — 배정 cross-tenant 차단(`agent_for` 변경/누출 0).
- `test_auth_check_enqueues_auth_check_job_and_audit` — AUTH_CHECK job 1건 PENDING enqueue + audit. **이전 always-run·PG 모두 부재**.
- `test_retry_cross_tenant_blocked` — job retry 가 job.tenant≠요청 tenant 면 차단(전이 0).
- `test_dispose_cross_tenant_blocked` — HELD dispose cross-tenant 차단(전이/누출 0).

라우트(`TestClient`, POST·HTMX fragment):
- `test_route_activate_returns_fragment_and_persists` — 활성화 라우트(기존엔 pause 만, activate 미검증 — TARGET_ACTIVATE 별 액션코드).
- `test_route_test_crawl_enqueues_baemin` — test crawl(BAEMIN) enqueue 200.
- `test_route_test_crawl_coupang_platform_branch` — platform=COUPANG **분기 커버**.
- `test_route_auth_check_triggers` — 인증 확인(AUTH_CHECK) 트리거 라우트.
- `test_route_dry_run_returns_preview_without_send` — dry-run 미발송(FR-3) 기본 seam.
- `test_route_assign_agent_happy_path` — Agent 배정 라우트 happy(`agent_for` 반영).
- `test_route_assign_agent_missing_fields_is_400` — target_id/agent_id 누락 → 400.
- `test_route_retry_failed_job_to_pending` — retry happy(FAILED→PENDING) — 기존엔 SUCCEEDED→400 만.
- `test_route_dispose_discard_happy_path` — HELD dispose(DISCARD)→DISCARDED happy — 기존엔 NUKE→400 만.
- `test_route_resume_invalid_to_status_is_400` — 잘못된 복구 to_status → 400 envelope.

### `tests/server/test_admin_action_audit.py` (+3)

- `test_assign_agent_records_audit_with_agent_id` — AGENT_ASSIGN audit(actor/target/시각 + agent_id 보존).
- `test_test_crawl_and_auth_check_each_record_an_audit_row` — record형 액션도 audit row(TEST_CRAWL·AUTH_CHECK 순서).
- `test_dispose_held_records_audit_with_disposition` — HELD_DISPATCH_DISCARD audit + disposition 보존.

## 설계 관찰(결함 아님)

- **HELD Dispatch 영속(열린 질문 #1)**: PG `get_held_dispatch→None`(보수적 미노출)이라 PG 경로의 dispose happy-path 는 의도적으로 미검증 — 순수 게이트 의미(DISCARD/RESUME/비-HELD 거부·복구 자동발송 0)는 always-run 으로 잠금. Epic 3/5 reconcile 표시 유지.
- **라우트 실 `now()`**: 라우트는 주입 불가한 실시간 `now()` 라 시각 기반 단언 없이 액션 성공/거부/HTML 만 검증(memory/admin-routes-wallclock-severity 선례).
- **redaction 키 규칙**: `agent_id`·`disposition`·`*_status` 는 secret 어간 아님 → 보존, `chat_id`/token/otp 류는 통째 마스킹 — audit `diff_redacted` 단언이 이 규칙에 정합.

## 커버리지(액션 표면 기준)

- AC1 액션 service: 8/8 always-run(이전 6 → 보강 후 8 — assign·auth-check 추가)
- AC1/AC2 라우트: 11/11 엔드포인트(이전 5 → 보강 후 11)
- AC3 audit: 전이형 4 + record형 4 = 위험 액션 전수 기록 커버

## lock 무회귀

14표·0004 head·enum count-lock(11/4/3/7)·9-dep 전부 유지 — 신규 컬럼/테이블/마이그레이션/enum/deps 0.

## Next Steps

- **PG-gated 6건**은 실 PostgreSQL(`TEST_DATABASE_URL`) 환경에서만 실행 — CI 에 PG 서비스가 붙으면 영속·tenant 격리·audit INSERT 가 추가 검증된다(현 WSL/venv skip).
- 정본 테스트 카운트는 review 단계에서 재측정해 Dev Agent Record(현재 41 신규)와 일치시킨다.
