# Test Automation Summary — Story 5.6 (Admin 운영 대시보드와 상태 심각도 표시)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`) · 모드: 발견 갭 자동 적용

> 본 파일은 5.6 스냅샷이며, 동일 내용이 `test-summary.md`(QA 런 정본)에도 반영됐다. **제품 코드 무변경 — 테스트만 추가.**

## 결과 요약

| 구분 | dev-exit | post-QA(현재) | 증감 |
| --- | --- | --- | --- |
| 전체 스위트 passed | 1676 | **1702** | **+26** |
| 전체 스위트 skipped | 30 | 30 | 0 |
| Story 5.6 always-run | 41 | **67** | **+26** |
| Story 5.6 PG-gated(skip) | 6 | 6 | 0 |

전체 회귀: `1702 passed, 30 skipped` — 신규 갭 테스트 26개, **회귀 0**. 이 수치가 review 정본(dev-exit 41 은 QA 추가로 stale — memory/stale-test-count-a2).

## 발견·채운 갭

기존 41 always-run + 6 PG-gated 는 happy-path·핵심 거부·심각도 경계를 잘 잠갔으나, **(a) PG-gated 파일에 숨은 순수 헬퍼·정책 상수, (b) HTMX fragment 의 인증 보호, (c) async 인증 seam 분기, (d) 빈 상태 템플릿 분기, (e) 심각도 4단계 라벨/CSS 매핑 전수, (f) fail-closed 가 CRITICAL freshness 도 덮는지·포트 읽기 전용 표면**에 갭이 있었다.

### `tests/server/test_dashboard_pg_helpers.py` (+11 · AC1, **신규 always-run 파일**)

> memory/`pg-gated-files-hide-pure-helpers` 준수: `dashboard_repository_postgres.py` 는 `TEST_DATABASE_URL` 없으면 통째로 skip 되어 그 안의 **SQL 무관 순수 결정 로직**이 CI 에서 한 번도 실행되지 않았다. 이를 always-run 으로 추출.

- `_pick_latest_code` 선택 규칙 **8 분기 전수**(jobs vs delivery_logs 중 "더 최신 ts 의 error_code"): 둘 다 None·한쪽만·delivery 최신·job 최신·delivery ts=None 은 가장 오래된 취급→job·동률 ts→job 우선·둘 다 ts=None→job. `SimpleNamespace` row 로 ORM/DB 불필요.
- 정책 상수 3종(드리프트 차단): `_AUTH_SESSION_PENDING_STATES == (AUTH_REQUIRED, USER_ACTION_PENDING)`(둘 다여야 인증 필요 누출 0), `_ACTIVE_JOB_STATUSES == (CLAIMED, RUNNING)`, `_TELEGRAM_ERROR_WINDOW == 10분`.

### `tests/server/test_dashboard_severity.py` (+5 · AC2·AC3)

- `test_classify_failclosed_stopped_for_target_validation_signal` — **기존 누락**: `classify_failclosed` 가 auth/kakao 신호만 검증했고 `target_validation_failed` 단독→STOPPED 미검증.
- `test_overall_passes_through_freshness_when_no_failclosed` — 병합이 WARNING/NORMAL 도 passthrough(기존엔 CRITICAL 만).
- `test_overall_failclosed_overrides_even_critical_freshness` — fail-closed 가 **이미 CRITICAL 인 freshness 도** 덮음(기존엔 최근=NORMAL 만 덮는 케이스).
- `test_severity_rank_unknown_value_is_zero` — 미지 severity 의 방어적 분기(`.get(.,0)`) — 표시 정렬이 깨지지 않음.
- `test_is_agent_online_respects_injected_offline_threshold` — `offline_after` 주입 가능성(임계 조정).

### `tests/server/test_admin_dashboard.py` (+10 · AC1·AC3·AC4)

- `test_target_row_failclosed_overrides_even_critical_freshness` — service 조립 레벨에서 41분 오래됨(CRITICAL)+인증 필요 → STOPPED(시간 경과 덮음).
- `test_all_fragments_also_require_admin_session` — **보안 갭**: 풀 페이지만 401 검증됐고 HTMX fragment(`/admin/targets|agents|channels|auth-required`) 의 seam 보호 미검증. 거부 seam 에서 4개 fragment 모두 401.
- `test_require_admin_session_supports_async_seam` — `inspect.isawaitable` **분기 미커버**: async seam 통과(200)·거부(401) 양쪽.
- `test_admin_seam_can_return_403_forbidden` — 401 외 403(권한 부족, 5.8 4역할 대비)도 전역 envelope `FORBIDDEN` 매핑.
- `test_empty_repo_renders_empty_state_messages` — 빈 tenant → 각 fragment 의 `{% else %}` 안내문 렌더(크래시 0)·채널 기본값 0초/0건.
- `test_severity_label_and_class_filters_map_all_four_levels` — `_severity_label`/`_severity_class` 4단계 전수 + 미지값 안전 기본값.
- `test_targets_partial_renders_label_and_class_for_each_severity` — 템플릿이 필터로 정상/주의/위험/중지 + `sev-*` class 를 모두 렌더(주입 행으로 결정적; **라우트는 실시간 now 라 시간 경과 심각도 비결정적** → 템플릿 직접 렌더로 검증).
- `test_dashboard_full_page_without_tenant_param_renders` — `?tenant` 미지정(빈 seam)이어도 200·대상 빈 안내문·agent fleet(전역) 표시.
- `test_full_page_invokes_only_read_methods` — recording repo 로 **읽기 전용 런타임 행위** 확인: 풀 페이지가 4개 read 포트만 호출(write/전이 0).
- `test_dashboard_repository_port_exposes_only_read_methods` — 포트 표면에 write/전이 메서드 부재(타입 보장 — AST 가드와 상보).

## 설계 관찰(결함 아님)

- **라우트 `_now()` 는 실시간 wall-clock** 이며 주입 불가다(jobs.py 선례 — memory/`jobs-complete-route-wallclock-now`). 따라서 HTTP 라우트로는 **시간 경과 심각도(주의/위험)를 결정적으로 단언할 수 없다**(인증 기반 STOPPED·구조만 가능). 순수(`severity.py`)·service(`now` 주입)·템플릿(주입 행) 계층은 결정적이라 시간 경과 의미를 그쪽에서 잠갔다. 운영 표시가 "라이브"인 것은 올바른 설계.

## Coverage

| 표면 | 상태 |
| --- | --- |
| 심각도 순수 정책(freshness ×2/×4·None·interval≤0·fail-closed 병합·online 2분) | ✅ 경계·전수 |
| fail-closed 신호 3종(auth/target-validation/kakao) → STOPPED | ✅ 전부(target-validation 보강) |
| read-model 조립(severity·online·tenant scope·채널 구분·정렬) | ✅ |
| HTMX 라우트(풀 페이지·4 fragment·`hx-*`·CDN) | ✅ |
| 인증 seam(기본 통과·sync 거부 401·**async 통과/거부**·**403**·**fragment 보호**) | ✅ |
| 템플릿(심각도 4단계 라벨/CSS·**빈 상태 분기**) | ✅ always-run |
| 읽기 전용(AST 가드·**런타임 read-only**·**포트 표면 lock**) | ✅ |
| PG 파생 집계·tenant 격리·채널 lag/error | ⏳ PG-gated(실 Postgres 시) |
| **PG 파일 내 순수 헬퍼·정책 상수** | ✅ always-run 추출 |
| 무회귀 lock(9-dep·14표·0004 head·enum count·jinja2 server extra) | ✅ |

- **AC1**(대시보드 읽기 화면): 라우트 200·HTML·`hx-*`·CDN·심각도/online/채널 구분·**fragment 인증 보호**·**읽기 전용 런타임** — 커버 완료.
- **AC2**(시간 경과 심각도): freshness ×2/×4 초과·정확 경계 하위·None·interval≤0·라벨/CSS 매핑 — 커버 완료.
- **AC3**(fail-closed 우선): 신호 3종→STOPPED·병합이 **NORMAL/CRITICAL freshness 모두** 덮음·순서 불변·정렬 — 커버 완료.
- **AC4**(인증 필요 필터): tenant scope 목록·cross-tenant 누출 0·reason 코드만 노출(secret/OTP 0)·빈 상태 — 커버 완료.

## 검증 체크리스트

- [x] API 테스트 생성(HTMX 라우트·서비스 오케스트레이션·인증 seam)
- [x] UI E2E — 서버 렌더 HTML(`TestClient` 로 풀 페이지/fragment·`hx-*` 속성·한글 라벨) + 템플릿 직접 렌더
- [x] 표준 프레임워크(pytest) · happy path · 핵심 에러 케이스(401/403 거부·빈 상태·미지 코드)
- [x] 전 테스트 통과 — `1702 passed, 30 skipped`
- [x] 시맨틱 단언(역할/텍스트/`hx-*` 속성·CSS class) · 하드코딩 sleep/대기 없음(`asyncio.run`·주입 `now`) · 테스트 독립성(각자 repo/app)
- [x] fake fixture 만(`tn-1`/`vault://`/`-100fake` 등 — 실제 토큰/전화/이메일/chat_id 없음) · read-model DTO secret 형 필드 0
- [x] 요약·커버리지 메트릭 포함

실행:

```bash
.venv/Scripts/python.exe -m pytest tests/server/test_dashboard_severity.py \
  tests/server/test_admin_dashboard.py tests/server/test_admin_readonly_guard.py \
  tests/server/test_dashboard_pg_helpers.py tests/negative/test_dashboard_repository_pg.py -q
# → 67 passed, 6 skipped  (PG-gated 는 TEST_DATABASE_URL 설정 시 실행)
```

## Next Steps

- CI always-run 회귀. 실 Postgres(`TEST_DATABASE_URL`) 연동 시 PG-gated 6건(파생 집계·tenant 격리·채널 구분·인증 필요·agent fleet) 자동 검증.
- review 단계에서 정본 카운트(**1702 passed / 30 skipped**)로 story Dev Agent Record dev-exit 수치(1676)·Story 5.6 always-run(41) 갱신(memory/stale-test-count-a2).
- 라우트 시각 주입(테스트용 `now` seam)을 5.9 지표 파이프라인에서 고려하면 시간 경과 심각도의 라우트-레벨 E2E 단언이 가능해진다(현재는 순수/service/템플릿 계층으로 충분).
