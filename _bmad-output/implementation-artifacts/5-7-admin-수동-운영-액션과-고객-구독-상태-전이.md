---
baseline_commit: 455b36278d7c31831966052edf90247f10734108
---

# Story 5.7: Admin 수동 운영 액션과 고객/구독 상태 전이

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want Admin UI에서 **대상 활성/비활성·Agent 배정·test crawl·dry-run render·test send·job retry·인증 확인**과 **고객/구독 상태 전이**(특히 `SUSPENDED`↔복구 시 `HELD` Dispatch 처리)를 수행하고 싶다,
so that 코드 변경 없이 운영 중 필요한 조치를 **안전하게(중복 방지 우회 없이, fail-closed로)** 실행하고 누가 무엇을 했는지 추적할 수 있다.

## Acceptance Criteria

> **범위 한 줄 요약**: 이 스토리는 **쓰기(상태 전이/액션) 대시보드**다 — 5.6의 읽기 전용 관측 대시보드 위에 **수동 운영 액션과 상태 전이**를 얹는다. 핵심 정책 로직은 이미 존재하는 **순수 service**(`SubscriptionGate`(2.6)·`QueueBackend` 전이표(5.3)·`IdempotentDeliveryService`(3.5)·`DispatchService`/fanout/render(3.1~3.7))를 **재구현하지 말고 wiring·persist·UI 노출만** 한다. MFA/4역할/audit **스키마·강제**는 5.8 소유 — 본 스토리는 **AC3가 요구하는 actor+시각 audit 기록**(이미 존재하는 `audit_logs` 테이블 사용)과 `require_admin_session`/tenant seam(5.6에서 도입) **재사용**만 한다. 엔티티 생성/편집 CRUD UI는 5.11.
>
> **신규 컬럼/테이블/마이그레이션 0 (권장 경로)**: `audit_logs`(5.2)·`subscriptions.status`(2.5)·`monitoring_targets.status`(2.5)·`jobs`(5.3) 테이블이 이미 존재한다. 상태 전이는 **기존 컬럼 UPDATE + 기존 enum 값**으로 수행 → 14표 lock·0004 head·enum count-lock 유지.
>
> **정본 이름 주의**: 상태값/enum/job status 는 **이미 구현된 코드를 따른다**(아래 Dev Notes "핵심 정본"). 임의의 새 상태/job type/severity 멤버를 추가하지 않는다.

**AC1 — Admin 수동 운영 액션 (P4-03/data-api-contract Admin UI, FR-22, FR-3 연계)**
- **Given** 운영자가 수동 액션을 수행할 때
- **When** Admin이 **대상 활성/비활성**(`MonitoringTargetStatus` ACTIVE↔PAUSED)·**Agent 배정**(target↔agent affinity)·**test crawl**(CRAWL job 1회 enqueue)·**dry-run render**(실발송 없이 렌더 결과만)·**test send**(운영자 지정 테스트 채널로만)·**job retry**(FAILED/RETRY→PENDING 재진입)·**인증 필요 확인**(AUTH_CHECK 트리거/auth_session 조회)을 제공하면
- **Then** **test send 는 운영자가 지정한 테스트 채널로만** 전송되고(실 고객 채널 fan-out 금지),
- **And** **retry 는 중복 발송 방지 정책(idempotency dedup key)을 우회하지 않으며**(같은 dedup key 재시도는 `DUPLICATE_BLOCKED` 로 차단 — `IdempotentDeliveryService.deliver_once` 의 `reserve` seam 재사용, 우회 경로 신설 금지),
- **And** **dry-run render 는 실제 발송 없이 결과만** 보여준다(`DispatchService`/`send_enabled=False` 또는 render-only 경로 — FR-3 dry-run 정신 계승, `DeliveryLog`/실전송 0).
- **And** 액션은 **service 레이어를 통해서만** 상태/DB를 바꾸고(라우트/템플릿에서 직접 ORM write·상태 전이 금지 — architecture #Service-Boundaries), job status 변경은 **`queue.states.assert_transition`(허용 전이표)**를 통과한다(미정의 전이 거부).

**AC2 — 고객/구독 상태 전이와 `HELD` Dispatch 처리 (FR-30, FR-6 연계)**
- **Given** 고객/구독 상태를 전이해야 할 때
- **When** 운영자가 상태를 변경하면
- **Then** **setup/인증 대기/채널 검증 대기/테스트 실행/활성/성능 저하/인증 필요/중지** 상태가 구분되고(고객 lifecycle = `CustomerLifecycleState`: SETUP_PENDING/PLATFORM_AUTH_PENDING/MESSENGER_VERIFY_PENDING/TEST_RUNNING/ACTIVE/DEGRADED/AUTH_REQUIRED/SUSPENDED; 구독 게이트 = `SubscriptionStatus`: PAYMENT_ACTIVE/PAYMENT_FAILED_GRACE/SUSPENDED/CANCELLED), **새 멤버 추가 없이 기존 enum 값으로** 전이한다,
- **And** 중지(`suspend`)·복구(`resume`) 전이는 **`SubscriptionGate.suspend()`/`resume()`** 가 반환하는 새 `Subscription` 상태 + `SubscriptionStateChange` 기록을 **persist** 한다(가공 로직 재구현 금지),
- **And** `SUSPENDED`→`ACTIVE`(복구) 시 **`HELD` Dispatch 는 운영자 확인 후** `SubscriptionGate.dispose_held(status, HeldDisposition.DISCARD|RESUME)` 로 **폐기(DISCARDED)/재개(PENDING)** 처리한다 — **복구가 `HELD` 를 자동 발송하지 않는다**(fail-closed 불변식 ②: 복구는 구독 상태만 바꾸고, `HELD`→발송 가능은 오직 운영자 `RESUME` 입력 시). `SUCCEEDED` 분은 어떤 경로로도 발송 가능으로 되돌아가지 않는다(불변식 ①).

**AC3 — 위험한 수동 액션 audit 기록 (FR-22)**
- **Given** 위험한 수동 액션을 추적해야 할 때
- **When** 액션(상태 전이·retry·test send·활성/비활성·Agent 배정·`HELD` 폐기/재개 등)을 실행하면
- **Then** **실행자(actor)와 실행 시각(timestamp)이 audit log 에 기록된다** — 기존 `audit_logs` 테이블(`actor_id`/`action`/`target_type`/`target_id`/`diff_redacted`/`created_at`)에 INSERT. `action` 은 UPPER_SNAKE 기계가독 코드, `diff_redacted` 는 **redaction 통과**(token/OTP/password/chat_id 원문 평문 금지 — `redact(...)`/`mask_operational_ids` 경유).
- **And** actor 출처는 **`require_admin_session` seam**에서 도출한다(5.8 이 MFA/4역할/실 사용자로 교체 — 5.7은 seam이 제공하는 actor 식별자만 기록; 미인증/익명이면 명시적 sentinel). audit 기록 실패가 운영 액션을 가리지 않도록 **액션과 같은 트랜잭션**에서 기록한다(액션 성공·audit 누락 불가).

## Tasks / Subtasks

- [x] **Task 1 — Admin 액션 service 레이어 (AC1·AC2·AC3, 쓰기 경계)**
  - [x] 1.1 상태 전이/액션 오케스트레이션을 **service 레이어**에 둔다 — 권장 신규 `src/rider_server/services/admin_action_service.py`(또는 `admin_actions.py`). 라우트/템플릿이 아니라 **여기서만** DB write·상태 전이가 일어난다(architecture #Service-Boundaries "Service 레이어만 상태 전이/DB 변경") [Source: architecture.md:488-493].
  - [x] 1.2 **구독 전이**: `SubscriptionGate.suspend(subscription, reason=, at=)`/`resume(subscription, reason=, at=, to_status=)` 를 호출해 받은 `(new_subscription, SubscriptionStateChange)` 를 persist(=`subscriptions.status` UPDATE). 가공 로직 **재구현 금지** — 게이트는 순수·결정적이고 `at`(시각)·`reason` 은 호출부(service)가 주입한다 [Source: src/rider_server/services/subscription_gate.py:126-166].
  - [x] 1.3 **`HELD` Dispatch 처리**: 복구 시 운영자 `HeldDisposition`(DISCARD/RESUME) 으로 `SubscriptionGate.dispose_held(status, disposition)` 호출 → DISCARDED/PENDING. `HELD` 가 아닌 입력은 게이트가 `ValueError`(불변식 ①) → service 가 4xx 로 변환. **복구가 자동 발송하지 않음**(불변식 ②)을 보장 — resume 와 dispose 를 분리된 명시적 운영자 액션으로 [Source: src/rider_server/services/subscription_gate.py:168-194]. ⚠️ `HELD` Dispatch 영속 표현은 **열린 질문 #1**(Dev Notes) — 권장 결정 따를 것.
  - [x] 1.4 **대상 활성/비활성**: `monitoring_targets.status` 를 `MonitoringTargetStatus.ACTIVE`↔`PAUSED` 로 UPDATE(soft delete=`INACTIVE` 는 5.11 CRUD 소유, 여기선 운영 토글만). tenant scope 검증 [Source: src/rider_server/domain/states.py:97-102; db/models/account.py].
  - [x] 1.5 **job retry**: 대상 job 의 현재 status 가 `FAILED`/`RETRY` 일 때만 `assert_transition(current, "PENDING")` 통과 후 PENDING 재진입(재enqueue). 새 job 강제 생성·SUCCEEDED 되돌림 금지. attempts/backoff 는 기존 service 소유 [Source: src/rider_server/queue/states.py:62-110].
  - [x] 1.6 **test crawl / dry-run render / test send**: test crawl=CRAWL job 1회 enqueue(`QueueBackend.enqueue`); dry-run render=`MessageRenderService`/`DispatchService(send_enabled=False)` 로 렌더 결과만(실전송·DeliveryLog 0); **test send=운영자 지정 테스트 채널 1개로만** `IdempotentDeliveryService.deliver_once` 경유(dedup `reserve` seam 우회 금지, fan-out 금지) [Source: src/rider_server/services/idempotency.py; dispatch_service.py; message_render_service.py; crawl_service.py].
  - [x] 1.7 **audit 기록(AC3)**: 모든 위험 액션은 `audit_logs` INSERT(actor_id/action/target_type/target_id/diff_redacted/created_at). 권장 **재사용 audit-write 헬퍼**(예: `record_audit(session, *, actor_id, action, target_type, target_id, diff_redacted, at)`) 를 두되 **MFA/역할/조회 UI 는 5.8**. `diff_redacted` 는 `redact(...)` 통과·secret 0. 액션과 **동일 트랜잭션** [Source: src/rider_server/db/models/audit.py; src/rider_crawl/redaction.py].

- [x] **Task 2 — Admin 액션 라우트 + HTMX 폼/버튼 (AC1·AC2)**
  - [x] 2.1 **읽기 전용 가드 충돌 해소(필수·아래 가드레일 §1)**: 5.6 `test_admin_readonly_guard.py` 는 `src/rider_server/admin/` **전체**에서 write/transition 호출을 금지한다. 5.7 액션 라우트는 이 가드를 깬다 → **권장: 액션 라우트를 읽기 전용 대시보드 파일과 분리**하고 가드 scope 를 읽기 전용 파일(`severity.py`/`dashboard_service.py`/`dashboard_repository_postgres.py`/`routes.py`)로 **좁힌다**. 액션 모듈엔 **별도 가드**(라우트는 직접 ORM write 금지, 오직 `admin_action_service` 호출; service 만 write)를 추가한다. **열린 질문 #2** 결정 따를 것 [Source: tests/server/test_admin_readonly_guard.py:1-60].
  - [x] 2.2 액션 라우트(POST, HTML/HTMX fragment 응답): `POST /admin/targets/{target_id}/activate|pause`, `POST /admin/targets/{target_id}/test-crawl|dry-run|test-send`, `POST /admin/jobs/{job_id}/retry`, `POST /admin/targets/{target_id}/auth-check`, `POST /admin/agents/assign`, `POST /admin/subscriptions/{subscription_id}/suspend|resume`, `POST /admin/dispatch/{...}/dispose`(HELD DISCARD/RESUME). 경로는 `/admin` 프리픽스(HTML — `/v1/` JSON 규약과 분리, 5.6 선례) [Source: src/rider_server/admin/routes.py:37,116-187].
  - [x] 2.3 각 라우트는 `Depends(require_admin_session)` 통과 + tenant scope(현 `?tenant=` seam, 5.8 세션 바인딩 교체) + service 호출 → 갱신된 fragment 반환(HTMX swap). 위험 액션(suspend/resume/dispose/test-send)은 **명시적 확인 UX**(폼 + reason 입력) — reason 은 audit `diff_redacted` 에 [Source: src/rider_server/admin/routes.py:85-111].
  - [x] 2.4 **에러 처리**: 게이트 `ValueError`(잘못된 dispose)·`InvalidJobTransition`(잘못된 retry)·tenant 불일치·미인증은 `HTTPException`(4xx) raise → 전역 핸들러가 `{"error":{"code","message_redacted"}}` 변환(HTML 경로도 통과). fail-closed: 모호하면 실행 거부 [Source: src/rider_server/main.py:57-69].
  - [x] 2.5 5.6 템플릿(`dashboard.html` + partial)에 액션 버튼/폼 추가(HTMX `hx-post`, `hx-confirm` 또는 확인 모달). 신규 npm/빌드 0(HTMX CDN/정적). XSS: Jinja autoescape 유지(고객/센터/방명 escape) [Source: src/rider_server/admin/templates/].

- [x] **Task 3 — create_app 와이어링 + seam (AC1·AC3)**
  - [x] 3.1 액션 service 가 필요로 하는 쓰기 의존(async session/`QueueBackend`/dispatch 콜백)을 `create_app` seam 으로 주입한다 — `_default_queue_backend`/`_default_dashboard_repository` 동형 패턴. 테스트는 in-memory fake 주입 [Source: src/rider_server/main.py:36-126].
  - [x] 3.2 `require_admin_session` seam 이 **actor 식별자**를 제공하도록 확장(또는 별도 `resolve_admin_actor` seam) — 5.8 이 MFA/실 사용자/역할로 교체. 5.7 기본값: actor 미해결 시 명시적 sentinel(예: `"UNAUTHENTICATED_ADMIN"`), 운영/테스트는 실제 강제기 주입 [Source: src/rider_server/admin/routes.py:73-95].
  - [x] 3.3 액션 라우터를 `create_app` 에 include(읽기 전용 `admin_router` 와 별도 또는 동일 prefix 하위 — 가드 분리 결정과 정합) [Source: src/rider_server/main.py:191-221].

- [x] **Task 4 — 테스트 (AC1·AC2·AC3, 4-tier)**
  - [x] 4.1 **always-run 순수/service(무 DB, fake 주입)**: 구독 suspend→`SUSPENDED`+`SubscriptionStateChange` persist 호출, resume→복구 상태, dispose_held(HELD,DISCARD)→DISCARDED·(HELD,RESUME)→PENDING·(SUCCEEDED,…)→거부; retry 가 FAILED/RETRY→PENDING 만 허용(`assert_transition`)·SUCCEEDED retry 거부; **test send 가 단일 테스트 채널로만**(fan-out 호출 0)·**retry 가 dedup 우회 0**(같은 key reserve 충돌 시 DUPLICATE_BLOCKED). 시각/actor 주입(결정성) [Source: src/rider_server/services/subscription_gate.py; idempotency.py].
  - [x] 4.2 **audit 기록 검증**: 각 위험 액션이 `audit_logs` 에 actor+action+target+timestamp INSERT(같은 트랜잭션). `diff_redacted` 에 token/OTP/password/chat_id 원문 평문 0(redact 어서션). 미인증 actor sentinel 기록 확인 [Source: src/rider_server/db/models/audit.py; memory redact-skips-operational-ids].
  - [x] 4.3 **라우트(`TestClient`)**: POST 액션이 200/HTMX fragment 반환·service 호출·갱신 상태 반영; 잘못된 dispose/retry→4xx envelope; tenant 불일치→차단; `require_admin_session` 거부 seam 주입 시 401/403. read-only 대시보드 GET 무회귀 [Source: tests/server/test_admin_dashboard.py; test_server_app.py].
  - [x] 4.4 **가드 재정렬 검증(필수)**: 5.6 read-only 가드가 읽기 전용 파일에 한정돼 **여전히 통과**(vacuous 아님), 신규 액션 모듈은 **라우트 직접 ORM write 0**(service 위임)·단방향 import(`rider_agent` 0) 가드 통과. AST call-edge(raw grep 금지) [Source: tests/server/test_admin_readonly_guard.py; memory negative-guard-tests-use-ast].
  - [x] 4.5 **PG-gated**(`@pytest.mark.skipif` no `TEST_DATABASE_URL` → skip): 실제 PostgreSQL 에서 subscription/target status UPDATE·audit INSERT·job retry 전이·tenant 격리가 정확히 영속됨을 seed 후 검증. cross-tenant negative(다른 tenant 대상/구독 전이·audit 누출 0) [Source: architecture.md:481-499; memory pg-gated-files-hide-pure-helpers].
  - [x] 4.6 **lock 무회귀**: 14표(`test_metadata_has_exactly_14_contract_tables`)·0004 head(`test_single_migration_head_with_initial_base`)·9-dep·enum count-lock(`CustomerLifecycleState`11/`SubscriptionStatus`4/`MonitoringTargetStatus`3/`FailureCategory`7 등) 전부 유지. 신규 컬럼/테이블/마이그레이션/enum 멤버/deps 0 [Source: tests/server/test_db_schema.py:95-98,401-421; test_domain_states.py; memory enum-member-count-locks, db-tables-13-vs-14].
  - [x] 4.7 테스트 컨벤션: 상단 docstring `"""Story 5.7 / ACx …"""`, `from __future__ import annotations`, fake fixture만(실제 토큰/전화/이메일/chat_id 금지), `tests/server/` flat(`__init__.py` 없음). secret 평문이 HTML/audit 에 안 남음을 redact 어서션으로 [Source: tests/server/test_server_app.py:1-6].

## Dev Notes

### 이 스토리의 본질: 5.6 위에 "쓰기(액션·전이)" 추가 — 단, 정책은 재구현 금지

- **5.6 = 보기(read), 5.7 = 하기(act).** 5.7은 **이미 존재하는 순수 service 정책**을 wiring·persist·UI 노출만 한다. 새 상태머신/dedup/전이 규칙을 **만들지 않는다**. 핵심 재사용:
  - 구독 중지/복구/HELD 처리 = **`SubscriptionGate`**(2.6, 순수·결정적, `suspend`/`resume`/`hold_undelivered`/`dispose_held`). 이미 fail-closed 3 불변식을 코드로 보장한다 [Source: subscription_gate.py:1-22,168-194].
  - job retry 전이 = **`queue.states.assert_transition` + `ALLOWED_TRANSITIONS`**(5.3). FAILED/RETRY→PENDING 만 허용 [Source: queue/states.py:62-110].
  - test send/retry 중복 차단 = **`IdempotentDeliveryService.deliver_once`**(3.5, insert-then-send, `reserve` seam). **우회 경로 신설이 곧 버그** [Source: idempotency.py:1-40].
  - dry-run/test crawl/render = **`DispatchService`/`dispatch_fanout`/`MessageRenderService`/`crawl_service`**(3.1~3.7). `send_enabled=False` 로 dry-run, 단일 채널로 test send [Source: dispatch_service.py:60-90].
- **5.5 Dev Notes 위임 수령**: "등록/검증을 트리거하는 Admin UI **버튼은 5.6/5.7 소유**" — 5.6은 관측만, **버튼/액션이 5.7**. 채널 register/verify/activate 를 운영 액션으로 노출할 수 있으나(`channel_registration` service 재사용), 본 스토리 AC 핵심은 target/job/구독 액션 — 채널 액션은 동일 service 위임 패턴으로 추가 가능(재구현 금지) [Source: 5-5 story; src/rider_server/services/channel_registration.py].

### 핵심 정본(검증 완료) — 절대 틀리면 안 되는 이름

- **`SubscriptionStatus`(4멤버)**: `PAYMENT_ACTIVE`/`PAYMENT_FAILED_GRACE`/`SUSPENDED`/`CANCELLED`. 구독 게이트가 평가하는 상태. resume 기본 `to_status=PAYMENT_ACTIVE` [Source: domain/states.py:34-42; subscription_gate.py:83-100].
- **`CustomerLifecycleState`(11멤버)**: `LEAD/SIGNED_UP/PAYMENT_ACTIVE/SETUP_PENDING/PLATFORM_AUTH_PENDING/MESSENGER_VERIFY_PENDING/TEST_RUNNING/ACTIVE/DEGRADED/AUTH_REQUIRED/SUSPENDED`. 고객 lifecycle(Tenant.status). AC2 "setup/인증대기/채널검증대기/테스트실행/활성/성능저하/인증필요/중지"가 이 멤버에 1:1 매핑 — **새 멤버 추가 금지**(count-lock 11) [Source: domain/states.py:14-32; data-api-contract.md:95-110].
- **`MonitoringTargetStatus`(3멤버)**: `ACTIVE`/`PAUSED`/`INACTIVE`(soft delete). 운영 활성/비활성 토글 = ACTIVE↔PAUSED. INACTIVE(삭제)는 5.11 [Source: domain/states.py:97-102].
- **job status 전이표**: PENDING→CLAIMED→RUNNING→(SUCCEEDED|FAILED|RETRY); FAILED/RETRY→PENDING(재시도); SUCCEEDED=터미널. retry 는 **`assert_transition(current,"PENDING")`** 로만 [Source: queue/states.py:84-110].
- **`DispatchJobStatus`(게이트 최소 부분집합)**: `PENDING`/`HELD`/`SUCCEEDED`/`DISCARDED`. **`HeldDisposition`**: `DISCARD`/`RESUME`. `dispose_held(HELD,DISCARD)→DISCARDED`, `(HELD,RESUME)→PENDING`, 비-HELD→`ValueError` [Source: subscription_gate.py:46-64,180-194].
- **`audit_logs` 컬럼**: `id`(uuid pk)·`actor_id`(uuid, FK 없음 — users 부재)·`action`(str)·`target_type`(str|None)·`target_id`(uuid|None, 다형 FK 없음)·`diff_redacted`(JSON)·`created_at`(tz-aware). 이미 존재(5.2) — 신규 컬럼 0 [Source: db/models/audit.py].
- **dedup key 5필드**: `target_id|channel_id|collected_at.isoformat()|template_version|message_hash`. retry/test send 모두 이 key 의 `reserve` 통과 — 우회 금지 [Source: idempotency.py:46-78].
- **마이그레이션 head = `0004_messenger_channel_registration`**. 권장 경로상 추가 0 → head 유지 [Source: tests/server/test_db_schema.py:401-421].

### 🚨 가드레일(위반 시 CI 실패) — 우선순위 순

1. **[#1 트랩] 5.6 read-only 가드 충돌**: `tests/server/test_admin_readonly_guard.py` 가 `src/rider_server/admin/` **전체**를 스캔해 `commit/add/flush/insert/update/delete/enqueue/save/register/verify/activate/deactivate/claim/complete/assert_transition` 등 호출을 **금지**한다. 5.7 액션은 이를 **반드시** 깬다. **해소 필수**(열린 질문 #2): (a) 액션 라우트/모듈을 읽기 전용 파일과 분리하고 가드 scope 를 읽기 전용 파일 화이트리스트로 좁힘 + 액션 모듈엔 "라우트는 직접 write 0, service 위임만" 가드 신설(권장), **또는** (b) 액션 service 를 `services/` 에 두고(admin/ 밖) admin/ 는 그 service 만 호출 — 단 호출하는 함수명이 금지 목록(`activate`/`deactivate` 등)과 겹치면 여전히 가드에 걸리므로 가드 갱신 불가피. **그냥 admin/ 에 write 추가하면 5.6 가드가 즉시 실패** [Source: tests/server/test_admin_readonly_guard.py:1-60; memory negative-guard-tests-use-ast].
2. **service-only 상태 전이**: 라우트/템플릿에서 직접 ORM write·상태 전이 금지. 전이는 service 레이어에서만, job status 는 `assert_transition` 통과 [Source: architecture.md:488-493; queue/states.py].
3. **idempotency 우회 금지**: retry/test send 는 `deliver_once` 의 `reserve` seam 경유. 새 "강제 발송"/"dedup skip" 경로 신설 = 버그(crash-after-send 중복 위험) [Source: idempotency.py:1-22].
4. **fail-closed 불변식**: ① SUCCEEDED 는 발송 가능으로 안 돌아감, ② resume 가 HELD 자동 발송 안 함(별도 운영자 dispose 필요), ③ 미매핑 상태/모호 입력은 차단. 게이트가 이미 보장 — service 가 게이트를 우회하지 말 것 [Source: subscription_gate.py:16-21].
5. **enum count-lock**: `CustomerLifecycleState`11·`SubscriptionStatus`4·`MonitoringTargetStatus`3·`FailureCategory`7 등 멤버 추가/삭제 금지. 잠금이 **여러 파일**에 흩어짐 → 변경 전 repo 전체 grep [Source: memory enum-member-count-locks; tests/server/test_domain_states.py].
6. **14표 lock / 0004 head / 9-dep lock**: 신규 테이블/마이그레이션/deps 0(권장 경로). `audit_logs` 는 이미 존재 [Source: tests/server/test_db_schema.py:95-98; memory db-tables-13-vs-14, server-deps-go-in-optional-group].
7. **단방향 import**: `rider_server`→`rider_crawl` 만. `rider_agent` import 0(AST 가드) [Source: project-context.md].
8. **tenant scope**: 모든 customer-owned 액션(target/구독/job/audit)은 tenant scope 검증. cross-tenant negative 로 누출 0 [Source: architecture.md:481-499].
9. **secret 위생**: token/OTP/password/chat_id 원문 평문 0(HTML·audit `diff_redacted`·로그·예외). 고객/센터/방명=운영 허용값이나 진단 breadcrumb 은 `mask_operational_ids=True` [Source: project-context.md; memory redact-skips-operational-ids].
10. **async-boundary**: async 라우트/service 에서 `time.sleep`/`subprocess.*` 직접 호출 금지(rglob 가드가 신규 모듈 자동 커버) [Source: tests/server/test_server_async_boundary.py:66-71].

### 재사용 자산(재구현 금지 — compose/import만)

- **`SubscriptionGate`**(`services/subscription_gate.py`): suspend/resume/hold_undelivered/dispose_held — FR-6·FR-30 정책 코어. service 는 시각(`at`)·reason·actor 만 주입 [Source: subscription_gate.py:108-194].
- **`QueueBackend`**(`queue/backend.py`, `postgres_queue.py`): enqueue(test crawl)·전이. retry 는 `assert_transition` + 재enqueue [Source: queue/backend.py:62-151].
- **`IdempotentDeliveryService`**(`services/idempotency.py`): build_dedup_key + deliver_once(reserve→send). test send/retry 가 통과 [Source: idempotency.py:48-142].
- **dispatch/render pipeline**: `DispatchService`(3.1)·`dispatch_fanout_service`(3.4)·`telegram_central_dispatch`(3.7)·`message_render_service`·`crawl_service`. dry-run=`send_enabled=False` [Source: services/].
- **create_app seam**: `_default_queue_backend`/`_default_dashboard_repository`/`_default_require_admin_session`(DATABASE_URL 분기 + `app.state.<x>`). 액션 service·actor seam 도 동형으로 [Source: main.py:36-126].
- **에러 envelope**: 라우트에서 `HTTPException` raise → 전역 핸들러가 `{"error":{"code","message_redacted"}}` 변환 [Source: main.py:57-69].
- **redaction**: `redact(text, *, mask_operational_ids=False)` — audit `diff_redacted`·HTML 에 적용. 토큰/OTP/secret 어떤 경우에도 노출 0 [Source: src/rider_crawl/redaction.py; memory redact-skips-operational-ids].
- **5.6 admin 모듈**: `require_admin_session`/`_tenant_id` seam, `Jinja2Templates` + severity 필터, dashboard 템플릿(액션 버튼 부착 대상) [Source: admin/routes.py:73-111].

### Project Structure Notes

- **신규(권장)**: `src/rider_server/services/admin_action_service.py`(상태 전이/액션 오케스트레이션 + audit-write 헬퍼 — DB write 가 여기 집중), `src/rider_server/admin/actions_routes.py`(POST 액션 라우트 + HTMX fragment; **읽기 전용 대시보드와 분리**), (선택) `src/rider_server/admin/templates/_actions.html`(폼/버튼 partial). 테스트: `tests/server/test_admin_actions.py`(라우트+service), `tests/server/test_admin_action_audit.py`(audit 기록), `tests/server/test_admin_actions_guard.py`(액션 모듈 write-via-service 가드), `tests/negative/test_admin_actions_pg.py`(PG-gated 영속·cross-tenant).
- **수정**: `tests/server/test_admin_readonly_guard.py`(scope 를 읽기 전용 파일 화이트리스트로 좁힘 — **5.7 핵심 변경**), `src/rider_server/main.py`(액션 service seam·actor seam·액션 라우터 include), `src/rider_server/admin/routes.py` **또는** templates(대시보드에 액션 버튼/HTMX 부착), (필요 시) `require_admin_session` seam actor 확장.
- **변경 금지**: 0001~0004 마이그레이션, 기존 enum 멤버, `SubscriptionGate`/`IdempotentDeliveryService`/`queue.states`(정책 코어 — 호출만), `rider_crawl`/`rider_agent` 패키지, 14표 스키마, 5.6 읽기 전용 대시보드의 read-only 성질(액션은 별 모듈).
- 액션 모듈을 읽기 전용 대시보드와 **물리적으로 분리**하면 5.6 의 "대시보드=읽기 전용" 불변식을 유지하면서 5.7 쓰기를 추가할 수 있다(architecture #API-Boundaries: Admin UI 세션+MFA 경계는 5.8 — 5.7은 seam 재사용).

### 개발 환경·프로세스(매 스토리 반복되는 함정)

- pytest 는 **`.venv/Scripts/python.exe -m pytest`** 로 실행(WSL `python3` 미설치; `pythonpath=["src"]`, editable 설치 없음 — 한글 경로 `.pth` 가 cp949 UnicodeDecodeError). jinja2 3.1.6 venv 설치 확인됨(5.6) [Source: memory dev-env-quirks].
- 신규 파일은 `\n` 으로 작성(CRLF 재변환이 content-compare 멱등 깨뜨림), diff 는 `git diff -w` 로 확인. 템플릿(.html)도 동일 [Source: memory crlf-roundtrip-idempotency].
- 커밋 컨벤션 `feat(story-5.7): …`, baseline 커밋 `455b362`(5.6).
- **테스트 카운트는 review 시 재측정**: dev 가 적은 수치는 qa-generate-e2e 가 케이스 추가하며 stale 해진다. dev-exit vs post-QA 구분해 정본 한 숫자를 review 에서 기록 [Source: memory stale-test-count-a2].
- **PG-gated 파일이 순수 helper 를 가린다**: 전이 허용 판정·dispose 의미·dedup 우회 차단 같은 순수 의미는 always-run 단위 테스트로 별도 추출(CI 에서 PG skip 돼도 실행) [Source: memory pg-gated-files-hide-pure-helpers; admin-routes-wallclock-severity].
- **라우트는 실 `now()` 사용**(주입 불가): 시각 기반 단언은 service/순수 레이어에서(시각 주입), 라우트는 액션 성공/거부/HTML 만 검증(5.6 `admin-routes-wallclock-severity` 선례) [Source: memory admin-routes-wallclock-severity].

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.7 (lines 1051-1072)] — 스토리 정의·3 AC, FR-22(175)/FR-30(183)/FR-6(159)/FR-3(156)
- [Source: _bmad-output/planning-artifacts/architecture.md:188-203] — API/Frontend(REST/Jinja2+HTMX), :237-339 패턴(상태 전이 service-only, 멱등성, fail-closed), :476-499 경계(API/Service/Data, tenant scope)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md:83-92] — Admin API/UI 표면(Customer suspend/resume·target manual run·channel test message·manual rerun), :94-144 State Machines(Customer lifecycle/Subscription gate)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md:56] — admin-ui: "test send, manual rerun", :65 "Never retry every 5 seconds forever"
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md:103] — "Run one dry-run with actual sending disabled"(FR-3 dry-run)
- [Source: src/rider_server/services/subscription_gate.py] — `SubscriptionGate`(suspend/resume/hold_undelivered/dispose_held)·`GateDecision`/`SubscriptionStateChange`/`DispatchJobStatus`/`HeldDisposition`·fail-closed 3 불변식
- [Source: src/rider_server/queue/states.py:42-110] — job status 전이표·`assert_transition`·`InvalidJobTransition`(retry 정본)
- [Source: src/rider_server/services/idempotency.py] — dedup key 5필드·`deliver_once` insert-then-send(test send/retry 우회 금지)
- [Source: src/rider_server/services/{dispatch_service,dispatch_fanout_service,message_render_service,crawl_service,telegram_central_dispatch}.py] — dry-run/test crawl/test send pipeline
- [Source: src/rider_server/db/models/audit.py] — `audit_logs` 컬럼(actor/action/target/diff_redacted/created_at) — AC3 기록 대상(이미 존재)
- [Source: src/rider_server/admin/routes.py] — 5.6 admin 라우트·`require_admin_session`/`_tenant_id` seam·Jinja2/HTMX(액션 부착 대상)
- [Source: tests/server/test_admin_readonly_guard.py] — 🚨 5.6 read-only 가드(5.7 이 scope 좁혀야 함)
- [Source: src/rider_server/main.py:36-221] — create_app seam·default 분기·router include 패턴
- [Source: src/rider_server/domain/states.py:14-102] — Customer/Subscription/MonitoringTarget enum 정본·count-lock
- [Source: _bmad-output/implementation-artifacts/5-6-admin-운영-대시보드와-상태-심각도-표시.md] — 직전 스토리: read-only 대시보드·seam·심각도·"버튼은 5.7" 위임
- [Source: _bmad-output/project-context.md] — 단방향 import·secret 정책·redaction·service 경계

### 열린 질문(구현 시 결정 — 막지 말고 가장 안전한 선택)

1. **`HELD` Dispatch 의 영속 표현(가장 트리키)**: `jobs` 테이블 status 에는 `HELD` 가 없다(PENDING/CLAIMED/RUNNING/SUCCEEDED/FAILED/RETRY). `DeliveryStatus`(states.py:138-162)에는 `HELD` 멤버가 있고, `SubscriptionGate.DispatchJobStatus` 도 `HELD` 를 "Epic 3/4/5 와 reconcile" 한다고 명시. **권장 결정**: (a) 전용 `dispatch_jobs` 테이블을 신설하지 않는다(14표 lock). (b) 중지 시 "미전송 dispatch 작업"을 표현하는 **기존 레코드**(미claim/대기 `KAKAO_SEND` job + 미전송 `delivery_logs`)에 게이트의 순수 `hold_undelivered`/`dispose_held` 의미를 적용하되, **실제 컬럼 매핑은 dispatch 영속이 실제로 어디 있는지에 맞춰 최소·fail-closed 로** 한다. (c) 핵심 불변식(resume 가 HELD 자동 발송 0, 운영자 dispose 필수, SUCCEEDED 불가역)을 **순수 service 테스트로 먼저 잠그고**, 영속은 가능한 표현에 보수적으로 매핑. 영속 표현이 현재 불명확하면 **AC2 의 게이트 의미(폐기/재개 운영자 결정)를 충족하는 최소 구현 + 명확한 docstring 으로 Epic 3/5 reconcile 표시**(5.9/5.10 dispatch 안전 시나리오와 정합). 모호함을 발송으로 해석하지 말 것(fail-closed).
2. **read-only 가드 분리 방식**: 권장 = 액션을 별 모듈(`admin/actions_routes.py` + `services/admin_action_service.py`)로 두고 `test_admin_readonly_guard.py` 의 `ADMIN_DIR` 스캔을 **읽기 전용 파일 화이트리스트**(severity/dashboard_service/dashboard_repository_postgres/routes)로 좁힌 뒤, 액션 모듈엔 "라우트 직접 ORM write 0·service 위임만, write 는 service 에서만" 가드를 신설. 가드가 vacuous 하지 않음을 자기검증 유지.
3. **actor 출처(5.7 vs 5.8)**: 5.8 이 MFA/4역할/실 사용자 소유. 5.7 은 `require_admin_session` seam 이 주는 actor 식별자만 audit 에 기록(미해결 시 sentinel). 실제 사용자 매핑·역할 강제는 5.8 — 5.7 은 seam 재사용으로 충분.
4. **test send 테스트 채널 지정 방식**: 운영자가 액션 시 채널 id 를 명시(폼 입력) 또는 tenant 의 지정 테스트 채널(기존 `messenger_channels` 중 PENDING/VERIFIED). fan-out 금지 — 단일 채널 1건만. 정확한 선택 UX 는 구현 재량이나 "실 고객 fan-out 0" 불변식 유지.
5. **채널 액션(register/verify/activate) 노출 여부**: 5.5 가 5.6/5.7 로 위임. AC 핵심은 target/job/구독이므로 채널 lifecycle 버튼은 동일 service(`channel_registration`) 위임 패턴으로 **추가 가능하나 필수 아님** — 범위 보수적으로 두고 5.11 CRUD 와 중복 피함.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (BMAD create-story workflow)

### Debug Log References

- `.venv/Scripts/python.exe -m pytest -q` → **1739 passed, 36 skipped**(dev-exit, PG-gated 부재) — 0 regression. *(review 재측정: qa-generate-e2e 추가분 포함 **1757 passed, 36 skipped** — memory/stale-test-count-a2.)*
- 함정 해소: Starlette 1.3.1 `request.form()` 은 urlencoded 에도 `python-multipart` 를 요구한다. 신규 deps 0(9-dep/server extra lock) 제약상 `urllib.parse.parse_qs`(stdlib)로 폼 본문을 직접 파싱하도록 전환(`actions_routes._form`).

### Completion Notes List

**구현 요약(쓰기 경계 = service+repository, 정책 재구현 0):**
- **service 레이어 단일 소유처** `services/admin_action_service.py` — 상태 전이/액션 오케스트레이션 + audit-write. 순수 게이트를 compose 만 함: 구독 `SubscriptionGate.suspend/resume`, HELD `dispose_held`, job retry `queue.states.assert_transition`, test send `IdempotentDeliveryService.deliver_once`(reserve seam 우회 0·단일 채널), test crawl/auth-check `QueueBackend.enqueue`. 시각 `at`·actor 는 호출부 주입(결정성).
- **fail-closed 불변식 보존(게이트가 보장, service 가 우회하지 않음):** ① SUCCEEDED dispose 거부(ValueError), ② resume 가 HELD 자동 발송 0(복구는 구독 상태만; HELD 처리는 별도 `dispose_held_dispatch` 액션), ③ 미허용 전이/모호 입력 차단.
- **AC3 audit:** 위험 액션마다 `audit_logs`(actor/action/target/diff_redacted/created_at) INSERT. PG 는 전이 UPDATE + audit INSERT 를 **같은 트랜잭션**(한 session·한 commit). `diff_redacted` 는 `redact_mapping(mask_operational_ids=True)` 통과 — token/OTP/password/chat_id 평문 0(reason 자유텍스트도 redact). 미인증 actor 는 sentinel(`UNAUTHENTICATED_ADMIN`) 기록(PG 는 UUID 아니면 컬럼 NULL + `diff.actor` 보존).
- **read-only 가드 충돌 해소(가드레일 #1):** 액션을 `admin/actions_routes.py`(POST)로 **물리 분리**하고 `test_admin_readonly_guard` write-call 스캔을 읽기 전용 파일 화이트리스트(routes/dashboard_service/dashboard_repository_postgres/severity/__init__)로 좁힘. 액션 모듈엔 `test_admin_actions_guard`(라우트 직접 write 0·service 위임만·sqlalchemy/rider_agent import 0) 신설. 5.6 read-only `.py`(routes.py/dashboard_service.py) **무수정**.
- **신규 컬럼/테이블/마이그레이션/enum 멤버/deps 0**(권장 경로): 14표·0004 head·enum count-lock(11/4/3/7)·9-dep 전부 유지. 기존 컬럼 UPDATE + 기존 enum 값만.
- **wiring:** `create_app` 에 `admin_action_service` seam(PG/in-memory 분기 `_default_admin_action_repository`) + `resolve_admin_actor` seam + 액션 라우터 include. 테스트는 in-memory fake 주입.

**열린 질문 결정(가장 안전·fail-closed):**
- **#1 HELD Dispatch 영속:** `jobs.status`/`delivery_logs.status` 어휘에 게이트 4값(`DISCARDED`/`PENDING`)을 신규 멤버 없이 매핑 불가(lock) → **순수 게이트 의미는 service + in-memory + always-run 테스트로 잠그고**, PG 는 보수적으로 `get_held_dispatch→None`(미노출)·`transition_dispatch→audit 만`(자동 발송 0). docstring 에 Epic 3/5 reconcile 명시.
- **#2 가드 분리:** 권장 경로(별 모듈 + 화이트리스트 narrowing + 신규 위임 가드) 채택.
- **#3 actor:** seam 이 주는 식별자만 기록, 미해결 sentinel. 실 사용자/MFA/역할은 5.8.
- **#4 test send:** 단일 채널 `deliver_once`(fan-out 0); 라우트는 `admin_test_send` seam(미설정 fail-closed 400) → service.test_send 호출.
- **#5 채널 액션:** 범위 보수적으로 제외(target/job/구독이 AC 핵심; 채널 lifecycle 은 5.5 service·5.11 CRUD).

**테스트(dev-exit 측정):** 신규 always-run 35(service 23 + audit 5 + 액션 가드 6 + read-only 가드 재정렬 +1) + PG-gated 6 = **41 신규**. 전체 1739 passed / 36 skipped(전부 PG-gated).

**테스트(review 재측정 — 정본):** qa-generate-e2e 가 always-run 18건을 보강(`test_admin_actions.py` 40 / `test_admin_action_audit.py` 8 / `test_admin_actions_guard.py` 6 = 54 always-run + `test_admin_actions_pg.py` 6 PG-gated). 전체 스위트 **1757 passed / 36 skipped**(전부 PG-gated), **0 regression**. 14표·0004 head·enum count-lock(11/4/3/7)·9-dep lock 전부 유지(`test_db_schema`/`test_domain_states` green). memory/stale-test-count-a2 — dev-exit 41 수치는 stale, 이 1757/36 이 정본.

### File List

**신규(소스):**
- `src/rider_server/services/admin_action_service.py` — 액션 service + 포트 + in-memory repo + audit 값 객체/diff 헬퍼
- `src/rider_server/services/admin_action_repository_postgres.py` — PG repository(전이 UPDATE + audit INSERT 동일 tx)
- `src/rider_server/admin/actions_routes.py` — 액션 POST 라우트 + actor seam + HTMX fragment
- `src/rider_server/admin/templates/_action_result.html` — 액션 결과 fragment
- `src/rider_server/admin/templates/_actions.html` — 구독/Dispatch/Agent/job 액션 패널(HTMX JS API)

**신규(테스트):**
- `tests/server/test_admin_actions.py` — always-run service + 라우트(TestClient)
- `tests/server/test_admin_action_audit.py` — audit 기록 + redaction
- `tests/server/test_admin_actions_guard.py` — 액션 라우트 write-via-service 가드(AST)
- `tests/negative/test_admin_actions_pg.py` — PG-gated 영속·tenant 격리

**수정:**
- `src/rider_server/main.py` — `_default_admin_action_repository` + `create_app` seam/라우터 include
- `src/rider_server/admin/__init__.py` — `admin_actions_router` 재노출
- `src/rider_server/services/__init__.py` — 5.7 심볼 재노출
- `src/rider_server/admin/templates/dashboard.html` — 액션 섹션 + 스타일
- `src/rider_server/admin/templates/_targets.html` — 대상별 액션 버튼 컬럼
- `tests/server/test_admin_readonly_guard.py` — write-call 스캔 scope 를 읽기 전용 화이트리스트로 좁힘
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 5.7 in-progress → review

### Change Log

| Date       | Version | Description | Author |
| ---------- | ------- | ----------- | ------ |
| 2026-06-14 | 0.1     | Story 5.7 created — Admin 수동 운영 액션(target 활성/비활성·Agent 배정·test crawl·dry-run·test send·job retry·인증 확인)과 고객/구독 상태 전이(`SUSPENDED`↔복구 + `HELD` dispose/resume). 기존 순수 service(`SubscriptionGate`/`queue.states`/`IdempotentDeliveryService`) wiring·persist·UI 노출 + audit 기록. 신규 컬럼/테이블/마이그레이션/enum/deps 0(권장 경로, 14표·0004 head·count-lock 유지). 🚨 5.6 read-only 가드 scope 재정렬 필수. | Bob (create-story) |
| 2026-06-14 | 1.0     | dev-story 구현 완료 — `admin_action_service`(쓰기 경계, 순수 게이트 compose)·`admin_action_repository_postgres`(전이+audit 동일 tx)·`actions_routes`(POST/HTMX)·템플릿·`create_app` seam 와이어링. read-only 가드 화이트리스트 narrowing + 액션 위임 가드 신설. 신규 컬럼/테이블/마이그레이션/enum/deps 0. 41 신규 테스트, 전체 1739 passed/36 skipped(PG-gated), 0 regression. Status → review. | Amelia (dev-story) |
| 2026-06-14 | 1.1     | Senior Developer Review (AI) — CRITICAL 0. AC1/AC2/AC3 구현·재사용 정합 검증(SubscriptionGate/queue.states/IdempotentDeliveryService/QueueBackend 시그니처 일치, 정책 재구현 0). File List = git 일치. MEDIUM 1 auto-fix(stale 테스트 카운트 → review 재측정 1757/36 정본화). HELD PG 영속·dry-run/test-send 기본 seam 미배선은 열린 질문 #1/#4 가 승인한 fail-closed 보수 deferral(게이트 의미는 always-run 으로 잠금)로 결함 아님. Status → done. | Review (AI) |
