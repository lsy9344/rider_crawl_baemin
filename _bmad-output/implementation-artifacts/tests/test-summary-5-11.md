# Test Automation Summary — Story 5.11 (Admin 엔티티 생성/편집 CRUD UI)

**Workflow:** bmad-qa-generate-e2e-tests · **Role:** QA 자동화 엔지니어 (테스트 생성만, 코드 리뷰/스토리 검증 제외)
**Date:** 2026-06-14 · **Framework:** pytest (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`)
**실행:** `.venv/Scripts/python.exe -m pytest` (WSL + Windows venv)

## 컨텍스트 — 검증 스토리

5.11 은 5.5~5.8 seam 위에 **5개 엔티티(고객·플랫폼 계정·모니터링 대상·메시지 채널·전송 규칙)
CRUD** 를 얹는다. dev 가 always-run 29 + PG-gated 3 + 가드 5 를 작성했다. QA gap-fill 은 **dev 가
빠뜨린 코드 경로**에 집중했다 — 특히 (a) ``update_*`` 오케스트레이션 메서드 4개(tenant/account/
channel/rule)가 happy path 미커버, (b) **secret 위생이 편집 경로에서도 강제되는지**(AC3) 미검증,
(c) 채널 비활성의 전이 비대칭(멱등 아님), (d) dev 가 1개 리소스만 테스트한 라우트 계약(create/
update/deactivate/list)을 나머지 리소스로 확장. 소스 코드 변경 0(QA 는 테스트만).

## Generated / Augmented Tests

기존 파일 ``tests/server/test_admin_entity_crud.py`` 말미에 **26건 append**(memory/stale-test-count-a2
— qa-e2e 가 dev 노트 이후 케이스를 append). import 1줄 추가(``InvalidChannelTransition``).

### (service) update 경로 happy — dev 는 monitoring_target update 만 커버

- [x] `test_update_tenant_records_before_after` — **G1.** ``update_tenant`` happy(dev 는 missing-entity 만 호출). 이름·상태 before/after + `TENANT_UPDATE` audit.
- [x] `test_update_platform_account_label_keeps_refs` — **G2.** 라벨 편집 + secret 핸들 보존(미입력 → 기존 `*_ref` 유지) + `PLATFORM_ACCOUNT_UPDATE` audit.
- [x] `test_update_platform_account_plaintext_secret_rejected_on_edit` — **G3 (AC3 핵심).** secret 위생이 **편집 경로에서도** fail-closed. 평문 token-shape update → `ValueError`, 기존 핸들 미오염, audit 0. dev 는 생성 경로 평문 거부만 커버했다.
- [x] `test_update_messenger_channel_routing_fields_no_transition` — **G4.** ``update_messenger_channel`` 전혀 미커버였다. 라우팅(chat_id/thread_id) 편집은 상태 전이가 아님(ACTIVE 불변) + `MESSENGER_CHANNEL_UPDATE` audit.
- [x] `test_update_delivery_rule_options_before_after` — **G5.** ``update_delivery_rule`` 전혀 미커버였다. template/send_only_on_change 편집 before/after + `DELIVERY_RULE_UPDATE` audit.

### (service) 비활성 경계 — 멱등성·전이 비대칭

- [x] `test_deactivate_delivery_rule_idempotent_no_extra_audit` — **G6.** 이미 `enabled=False` → 멱등 no-op(중복 audit 0). target 멱등 테스트와 동형 parity.
- [x] `test_deactivate_messenger_channel_already_inactive_is_rejected` — **G7 (행동 비대칭 잠금).** 채널 비활성은 **멱등이 아니다** — 전이표가 `INACTIVE→{PENDING}` 만 허용하므로 이미 INACTIVE 채널 재비활성은 `InvalidChannelTransition`(=ValueError→400). target/rule(멱등 no-op)과 다른 계약을 회귀로 고정.
- [x] `test_deactivate_messenger_channel_from_pending_allowed` — **G8.** 갓 생성한 PENDING 채널은 `PENDING→INACTIVE` 허용 전이로 비활성 가능.

### (service) 검증·scope 경계

- [x] `test_create_tenant_blank_name_rejected` — **G9.** 고객명 공백 → `ValueError`, 미반영.
- [x] `test_create_monitoring_target_blank_name_rejected` — **G10.** 대상 표시명 공백 → `ValueError`, 미반영.
- [x] `test_deactivate_delivery_rule_cross_tenant_blocked` — **G11.** DeliveryRule scope 는 `target_id`→target.tenant 도출 → cross-tenant deactivate `TenantScopeViolation`(dev 는 create cross-tenant 만 커버).

### (라우트) create/update/deactivate + 검증 400 — dev 미커버 리소스

- [x] `test_route_create_customer_persists` — **G12.** `POST /admin/customers` happy(미커버 리소스).
- [x] `test_route_create_customer_viewer_forbidden` — **G13.** customers 변경도 OPERATOR 게이트(리소스별 게이트 회귀 차단).
- [x] `test_route_update_customer_returns_fragment` — **G14.** `POST /admin/customers/{id}` 편집 happy.
- [x] `test_route_create_platform_account_happy` — **G15.** `POST /admin/platform-accounts` happy(dev 는 평문 400 만 커버).
- [x] `test_route_create_platform_account_unknown_platform_400` — **G16.** 알 수 없는 플랫폼 → 400(`_platform_or_400`).
- [x] `test_route_create_messenger_channel_pending` — **G17.** `POST /admin/messenger-channels` → PENDING 사전 생성.
- [x] `test_route_create_messenger_channel_unknown_messenger_400` — **G18.** 알 수 없는 메신저 → 400(`_messenger_or_400`).
- [x] `test_route_create_delivery_rule_fan_out_persists` — **G19.** `POST /admin/delivery-rules` 1:N fan-out(한 대상 → 2채널).
- [x] `test_route_create_delivery_rule_missing_fields_400` — **G20.** target_id/channel_id 누락 → 400(라우트 선검증).
- [x] `test_route_deactivate_target_soft_delete` — **G21.** `POST /admin/monitoring-targets/{id}/deactivate` → INACTIVE 영속.
- [x] `test_route_deactivate_delivery_rule_disabled` — **G22.** `POST /admin/delivery-rules/{id}/deactivate` → enabled=False 영속.

### (라우트) 목록 fragment — dev 는 monitoring-targets 목록만 커버

- [x] `test_route_list_customers_fragment` — **G23.** `GET /admin/customers`(VIEWER 가능).
- [x] `test_route_list_platform_accounts_fragment` — **G24.** `GET /admin/platform-accounts`.
- [x] `test_route_list_messenger_channels_fragment` — **G25.** `GET /admin/messenger-channels`.
- [x] `test_route_list_delivery_rules_fragment_by_target` — **G26.** `GET /admin/delivery-rules?target_id=`(target 기준 조회).

### 무변경 유지(재구현 0 — dev/정본 자산)

- `tests/server/test_admin_entity_crud.py` dev 작성 29건(순수 center/secret + service create/deactivate + 라우트 일부)
- `tests/negative/test_admin_entity_crud_pg.py` PG-gated 3건(실 INSERT+audit, soft-delete roundtrip, cross-tenant 미노출)
- `tests/server/test_admin_actions_guard.py` AST 가드(crud_routes 직접 write/전이 0·service 위임·import 단방향)

## Coverage

| AC | 영역 | 상태 |
| --- | --- | --- |
| AC1 | 5개 엔티티 create/조회/수정(폼·라우트·service) | ✅ 정본 + **QA: update 4메서드·라우트 create/update/list 확장** |
| AC1 | DeliveryRule 1:N fan-out(service + 라우트) | ✅ 정본 + **QA: 라우트 fan-out** |
| AC2 | soft delete(target=INACTIVE·channel=INACTIVE·rule=enabled=False) | ✅ 정본 + **QA: rule 멱등·채널 전이 비대칭·deactivate 라우트** |
| AC3 | secret `*_ref` 분리(생성/**편집** fail-closed) | ✅ 정본(생성) + **QA: 편집 경로 평문 거부(G3)** |
| AC3 | center_name 위험 경고(쿠팡 빈/배민 기본값) | ✅ 정본(순수+service+라우트) |
| AC3 | tenant 격리(cross-tenant 404 동급) | ✅ 정본 + **QA: rule deactivate cross-tenant(G11)** |
| AC4 | audit before/after + actor + result(write+audit 동일 tx) | ✅ 정본 + **QA: update/deactivate 4종 action 코드·diff 잠금** |
| AC4 | RBAC(VIEWER 읽기전용·OPERATOR↑ 변경·미인증 401) | ✅ 정본(targets) + **QA: customers 게이트(G13)** |

- 신규 third-party deps **0** · 신규 DB 컬럼/테이블/Alembic/enum 멤버 **0** · 소스 코드 변경 **0**(QA 는 테스트만)
- QA 변경 파일 1개: `tests/server/test_admin_entity_crud.py`(append 26 tests + import 1) — 전체 신규 파일(5.11 dev), CRLF/LF noise 없음

## 실측 Test Count (qa-e2e 시점 — `stale-test-count-a2` 패턴)

- **전체 회귀: `983 passed, 51 skipped, 0 failed` (6.75s)** — `pytest tests/server tests/negative`
- dev 시점 957 passed → QA gap-fill **+26** always-run. PG-gated 51 skip 불변(`TEST_DATABASE_URL` 미설정).
- 5.11 파일 단독: `29 → 55 passed`(`tests/server/test_admin_entity_crud.py`).

## Next Steps

- CI 에서 동일 회귀 실행(always-run +26). PG 환경(`TEST_DATABASE_URL`)에서는 `tests/negative/test_admin_entity_crud_pg.py` 3건 PG-gated 정본도 실행 권장.
- 향후 채널 **재활성화**(INACTIVE→PENDING) 운영 액션 도입 시: G7(비활성 멱등 아님) 전이 비대칭을 그대로 계약으로 유지하고 재활성 경로 테스트를 추가할 것.
- PG repo 의 `update_*`/`create_messenger_channel(registration_code)` 실 SQL 경로는 현재 in-memory fake 로만 always-run 커버 — PG 환경 확보 시 negative 파일에 update/registration_code roundtrip 추가 권장.
