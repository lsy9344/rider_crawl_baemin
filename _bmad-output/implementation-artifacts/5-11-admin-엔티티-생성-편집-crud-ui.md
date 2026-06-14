---
baseline_commit: aae579fecd1e554a6d2b7f24e14fe13e1f51b57a
---

# Story 5.11: Admin 엔티티 생성/편집 CRUD UI

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want Admin UI에서 고객·플랫폼 계정·모니터링 대상·메시지 채널·전송 규칙을 직접 생성·조회·수정·비활성화하고 싶다,
so that DB나 마이그레이션 스크립트를 직접 건드리지 않고도 운영 중 신규 고객/계정/대상/채널/규칙을 안전하게(테넌트 격리·secret 분리·감사 로그) 추가·변경할 수 있다.

## Acceptance Criteria

**AC1 — 엔티티 생성/조회/수정 폼(FR-4, ADD-10)**
**Given** 운영자가 신규 엔티티를 추가해야 할 때
**When** Admin UI(Jinja2+HTMX)에서 고객·플랫폼 계정·모니터링 대상·메시지 채널·전송 규칙 생성/편집 폼을 제공하면
**Then** 각 엔티티를 ID 기반으로 생성·조회·수정할 수 있고
**And** 모니터링 대상은 플랫폼·계정(`platform_account_id`)·기대 센터/상점명(`center_name`)·URL/식별자(`url`/`external_id`)·연결된 브라우저 프로필을 입력받아 도메인 모델(Story 2.5) 계약과 일치하며
**And** 전송 규칙은 하나의 대상에서 하나 이상의 채널로 연결(1:N)되도록 생성할 수 있다(같은 `target_id` + 다른 `channel_id` 인스턴스 — FR-9 토대).

**AC2 — 삭제 대신 비활성화(soft delete, FR-4 / FR-31)**
**Given** 삭제 대신 비활성화를 지원해야 할 때
**When** 운영자가 엔티티를 비활성화하면
**Then** 물리 삭제가 아니라 soft delete/inactive 상태로 전환되어 운영 이력이 보존되고(대상=`status=INACTIVE`, 채널=`state=INACTIVE`, 규칙=`enabled=False`)
**And** 비활성 대상은 자동으로 재활성화되지 않는다(FR-31 연계 — 재활성화는 명시적 운영자 액션만).

**AC3 — 입력 검증·secret 분리·테넌트 격리(NFR-8, ADD-15, FR-20)**
**Given** 입력값에 secret이나 잘못된 값이 들어올 수 있을 때
**When** 폼 입력을 검증·저장하면
**Then** 토큰/비밀번호 같은 secret은 폼·DB 컬럼에 평문으로 저장되지 않고 `*_ref` 핸들로만 처리되며(평문 입력이면 fail-closed 400)
**And** 쿠팡(`Platform.COUPANG`) 기대 센터/상점명(`center_name`)이 비었거나 배민 기본값이면 저장 시 위험 상태로 표시·경고된다(FR-20 연계 — 차단이 아닌 경고)
**And** 모든 customer-owned 엔티티 생성/조회/수정은 tenant scope 필터를 통과한다(tenant isolation — cross-tenant는 404 동급).

**AC4 — audit + 역할 기반 권한(FR-22, FR-34)**
**Given** 운영 변경을 추적해야 할 때
**When** 운영자가 엔티티를 생성·수정·비활성화하면
**Then** 변경이 audit log에 actor, source, before/after value(`diff_redacted`), target IDs, reason, timestamp, result로 기록되고(액션 write와 audit는 같은 트랜잭션)
**And** 생성/편집/비활성화 권한은 역할로 구분된다(VIEWER는 읽기 전용, OPERATOR 이상만 변경 — `require_role(AdminRole.OPERATOR)`).

## Tasks / Subtasks

- [x] **Task 1 — CRUD 영속 포트 + 구현(생성/수정/비활성화, AC1·AC2)**
  - [x] `AdminEntityRepository` Protocol 신설(services/) — 기존 `AdminActionRepository`(`transition_*`만 보유)와 분리. 5개 엔티티별 `create_*`/`update_*`/`deactivate_*`(또는 `save_*`) + `get_*` 메서드 정의. **모든 write는 엔티티 write + audit INSERT를 같은 트랜잭션으로 묶는다**(5.7 선례: `transition_target(entity, audit)`).
  - [x] `InMemoryAdminEntityRepository` 구현(무-DB 기본값 + always-run 테스트 fake — `InMemoryAdminActionRepository`/`InMemoryChannelRepository` 선례). dict 갱신 + `audits.append(audit)`로 "같은 트랜잭션" 모사.
  - [x] `PostgresAdminEntityRepository` 구현(PG-gated) — **CREATE는 신규 INSERT 경로**(기존 어디에도 INSERT 경로 없음: `PostgresChannelRepository.save()`는 UPDATE-only). `insert(Row).values(...)` + `insert(AuditLogRow).values(**_audit_values(audit))` 동일 세션 `commit()`. ORM↔domain 변환은 `_to_domain` 패턴(domain은 SQLAlchemy import 0). ID는 `uuid_pk` client-side `uuid.uuid4` default 또는 service 주입.
- [x] **Task 2 — CRUD 오케스트레이션 service(AC1·AC2·AC3)**
  - [x] `AdminEntityService` 신설(services/admin_entity_service.py) — write 단일 소유처. `create_*`/`update_*`/`deactivate_*` 메서드. 내부에서 `datetime.now()` 호출 금지(시각 `at`는 호출부 주입 — 5.7 결정성 규약).
  - [x] tenant scope 검증: customer-owned 엔티티(Tenant 제외한 자식 + Tenant 자신은 생성 시 새 tenant_id 발급)는 load 시 `entity.tenant_id == tenant_id` 검사, 불일치면 `TenantScopeViolation`(404 매핑). DeliveryRule은 직접 `tenant_id` 없음 → `target_id`→MonitoringTarget의 tenant로 scope 도출.
  - [x] secret 분리(AC3): `PlatformAccount.username_ref`/`password_ref`는 `SecretRef` 핸들만 — 폼이 평문 자격증명을 주면 `ValueError`로 거부(라우트가 400 매핑, `rotate_external_token` 선례 "평문 secret 금지 — *_ref 핸들만"). 평문을 응답/로그/audit에 싣지 않는다.
  - [x] center_name 위험 경고(AC3): `Platform.COUPANG` 계정에 연결된 대상에서 `center_name`이 비었거나 배민 기본값이면 저장은 허용하되 결과 fragment에 위험 표시(차단 아님). 판정은 순수 helper로 분리(always-run 단위 테스트).
  - [x] 비활성화 경로(AC2): MonitoringTarget `→INACTIVE`(5.11이 신규 소유 — `set_target_status`가 INACTIVE를 명시적으로 5.11에 위임), DeliveryRule `enabled=False`. **MessengerChannel `deactivate→INACTIVE`는 5.5 `ChannelRegistrationService.deactivate`를 재사용**(register/verify/activate 상태머신 재구현 금지).
  - [x] audit(AC4): 각 create/update/deactivate에서 `_audit(...)` 패턴 재사용 — before/after를 `diff`에 담고 `build_diff_redacted`(mask_operational_ids=True) 통과. action 코드는 UPPER_SNAKE 신규 상수(예 `TENANT_CREATE`/`MONITORING_TARGET_UPDATE`/`DELIVERY_RULE_DEACTIVATE`), target_type는 기존 상수 재사용 + 신규(`tenant`/`platform_account`/`delivery_rule`). `AuditEntry`/`AuditResult`(SUCCESS/FAILURE/DENIED) 재사용.
- [x] **Task 3 — CRUD 라우트(POST 생성/수정/비활성화 + GET 목록/폼 fragment, AC1·AC4)**
  - [x] 신규 모듈 `admin/crud_routes.py`(별도 `APIRouter(prefix="/admin")`) — read-only 대시보드(`routes.py`)와 물리 분리. write는 `app.state.admin_entity_service`만 호출(직접 ORM write 0, `actions_routes.py` 선례).
  - [x] 라우트 시그니처: 변경 라우트는 `_principal=Depends(require_role(AdminRole.OPERATOR))`, 폼 파싱은 `_form(request)`(stdlib `parse_qs`, python-multipart 미사용 — 9-dep lock), tenant는 `_tenant_id(request)`, actor/source는 `_resolve_actor`/`_resolve_source`, 시각은 `_now()`, 결과는 `_fragment(...)`(`_action_result.html`), 예외는 `_raise_for`(NotFound/TenantScopeViolation→404, ValueError→400). 이 헬퍼들은 `actions_routes.py`에서 재사용 또는 동형 복제.
  - [x] **URL 충돌 회피**: read-only 대시보드가 이미 `GET /admin/targets`·`/admin/channels`·`/admin/agents`·`/admin/auth-required`를 점유한다. CRUD 목록/폼/생성 라우트는 구별되는 경로 사용(예 복수 명사 리소스 `POST /admin/monitoring-targets`, `POST /admin/customers`, `POST /admin/messenger-channels`, `POST /admin/delivery-rules`, `POST /admin/platform-accounts`; 편집 `POST /admin/<resource>/{id}`; 비활성화 `POST /admin/<resource>/{id}/deactivate`).
  - [x] `create_app`에 router 등록(`app.include_router(admin_crud_router)`) + `app.state.admin_entity_service` 기본값 배선(`_default_admin_entity_repository(settings)` — DATABASE_URL 있으면 PG, 없으면 in-memory; 테스트는 `create_app(admin_entity_service=...)` 주입). `admin/__init__.py`에 router 재노출.
- [x] **Task 4 — 템플릿(Jinja2+HTMX, AC1·AC2)**
  - [x] 엔티티별 생성/편집 폼 partial(`_entity_form.html` 류) + 목록 partial(`_entities.html` 류). HTMX `htmx.ajax('POST', url, {target:'#action-result', swap:'innerHTML', values})` 패턴(`_actions.html` 선례, 신규 npm/빌드 0 — HTMX 2 CDN). autoescape 유지.
  - [x] `dashboard.html`에 "엔티티 관리" 섹션 추가(기존 5개 섹션 패턴 — 30초 polling은 목록에만, 폼은 정적). secret 입력 필드는 `*_ref` 핸들 입력임을 라벨로 명시(평문 금지).
- [x] **Task 5 — 테스트(always-run 우선 + 라우트 + PG-gated + 가드)**
  - [x] always-run service/순수(무 DB, in-memory fake repo + 주입 시각/actor): 5개 엔티티 create/update/deactivate happy path, tenant scope 차단(cross-tenant→`TenantScopeViolation`), secret 평문 거부(ValueError), center_name 위험 판정(쿠팡 빈/배민기본값), DeliveryRule 1:N fan-out 생성, soft-delete 상태값 단언, audit before/after+result 기록.
  - [x] 라우트(`TestClient` + 주입 `_OPERATOR` principal): POST 200/HTMX fragment, VIEWER→403, 미인증→401, tenant 불일치→404, 평문 secret→400. (시각 의존 단정은 라우트에서 하지 않는다 — 라우트는 실 `now()` 사용; freshness/severity는 순수/service 레이어에서 — memory/admin-routes-wallclock-severity.)
  - [x] PG-gated(`tests/negative/`): 실 INSERT + audit 동일 트랜잭션, soft-delete 후 재조회, cross-tenant 미노출. PG 미설정 CI에서 skip되므로 순수 helper(secret/center_name/scope)는 always-run으로 별도 추출(memory/pg-gated-files-hide-pure-helpers).
  - [x] 가드: 신규 `crud_routes.py`가 직접 ORM write/transition 호출 0임을 AST로 강제 — `test_admin_actions_guard.py`에 `crud_routes.py` 스캔 추가(또는 신규 가드 파일). read-only 화이트리스트(`routes.py`/`dashboard_service.py`/`dashboard_repository_postgres.py`/`severity.py`/`__init__.py`)는 그대로 유지(신규 모듈은 화이트리스트에 넣지 않는다). 단방향 import: `crud_routes.py`·`admin_entity_service.py`는 `rider_agent` import 0, 라우트는 `sqlalchemy` import 0.
- [x] **Task 6 — 회귀·lock 검증**
  - [x] 신규 테이블·컬럼·enum 멤버 추가 0 확인: `test_db_schema.py`(테이블 정확히 14), `test_domain_states.py`(MonitoringTargetStatus=3·MessengerChannelState=4·AuditResult=3), `test_domain_models.py`(도메인 필드 lock) green 유지.
  - [x] `.venv/Scripts/python.exe -m pytest tests/server tests/negative` 전체 green. CRLF/LF 잡음은 `git diff -w`로 확인(memory/dev-env-quirks).

## Dev Notes

### 핵심 재사용(이미 존재 — 재구현 금지)

이 스토리는 **새 정책을 만들기보다 기존 5.5~5.8 seam을 wiring**하는 것이 대부분이다. 단, **CREATE(INSERT) 경로만은 코드베이스 어디에도 없어 신규 작성**한다(아래 "CREATE는 신규" 참고).

- **RBAC 게이트(5.8):** `from rider_server.security import AdminRole, require_role`. 변경 라우트는 `Depends(require_role(AdminRole.OPERATOR))`. 게이트가 principal 해석(None→401)·IP allowlist(403)·MFA(privileged 미검증→403)·역할 rank(부족→403)를 fail-closed로 강제하고, 인증된 주체의 거부는 `result=DENIED` audit한다(미인증은 audit 안 함 — anti-flooding). [Source: src/rider_server/security/access.py#require_role]
- **라우트 헬퍼(5.7, `actions_routes.py` 재사용/동형):** `_form`(stdlib parse_qs, 마지막 값 취함), `_tenant_id`(`?tenant=<id>` seam), `_resolve_actor`(principal.actor_id 또는 `UNAUTHENTICATED_ACTOR`), `_resolve_source`(principal.source), `_now`(실 UTC now), `_fragment`(`_action_result.html` 렌더, `ok` 플래그), `_raise_for`(`AdminActionNotFound`/`TenantScopeViolation`→404, `ValueError`→400 — 순서 주의). [Source: src/rider_server/admin/actions_routes.py:69-138]
- **audit(5.7/5.8):** `AdminActionService._audit(...)`·`build_diff_redacted(payload)`·`AuditEntry`·`AuditResult` 패턴을 그대로 따른다. `diff`에 `{"from_x":..., "to_x":..., "reason":...}` before/after를 담고 `build_diff_redacted`(`redact_mapping(..., mask_operational_ids=True)`)로 통과. `source`/`reason`은 자유 텍스트 `redact()` 통과. write+audit는 repository가 같은 트랜잭션으로 persist. [Source: src/rider_server/services/admin_action_service.py:217-266, :368-382]
- **tenant scope(5.7):** `_scoped_target`/`_scoped_subscription` 패턴 — load 후 `tenant_id` 불일치면 `TenantScopeViolation`(= `AdminActionNotFound` 하위 → 404, 존재 누설 방지). [Source: src/rider_server/services/admin_action_service.py:325-341, :149-167]
- **채널 lifecycle(5.5):** `ChannelRegistrationService`(register/verify/activate/deactivate)는 채널 상태머신 단일 소유처다. 5.11의 채널 "비활성화"는 `ChannelRegistrationService.deactivate(channel_id)`(→INACTIVE soft delete)를 **재사용**한다. 5.11이 추가하는 것은 (a) 신규 PENDING 채널 **생성**(pre-provision, 선택적 `registration_code`)과 (b) 라우팅 필드(`telegram_chat_id`/`thread_id`/`kakao_room_name`) 편집뿐. register/verify/activate 전이표·고유성 강제(`assert_unique_telegram_topics`/`assert_unique_kakao_rooms`)는 건드리지 않는다. [Source: src/rider_server/services/channel_registration.py:278-383]
- **에러 envelope(5.1):** 전역 핸들러가 `HTTPException`을 `{"error":{"code","message_redacted"}}`로 통일. 라우트는 `_raise_for`로 매핑만. [Source: src/rider_server/main.py:291-324]

### CREATE는 신규 — 기존에 INSERT 경로 없음

- `AdminActionRepository`는 `transition_*`(UPDATE)만, `PostgresChannelRepository.save()`도 UPDATE-only, `InMemoryChannelRepository.seed()`는 테스트 전용이다. **5개 엔티티 모두 신규 INSERT 경로를 새로 작성**해야 한다(PG: `insert(Row)` + 같은 세션 audit INSERT + commit). [Source: src/rider_server/services/channel_repository_postgres.py:63-76]
- ID: ORM `uuid_pk()`가 client-side `uuid.uuid4` default를 부여한다(`gen_random_uuid()` 비의존). service가 명시적 `id`를 만들지, DB default에 맡길지 한 가지로 정한다. [Source: src/rider_server/db/models/_columns.py:15-17]
- `Tenant.created_at`은 **자동 now() 기본값이 없다**(순수·결정적 — 호출부 주입). 고객 생성 시 `at`(=`_now()`)을 `created_at`으로 주입해야 한다. [Source: src/rider_server/domain/tenant.py:11-16]

### 엔티티별 도메인 계약(Story 2.5 — 필드 추가/이름 변경 금지)

frozen dataclass, `(str,Enum)` 상태값(이름==값). 비활성화는 상태값 전이(물리 삭제·`is_deleted`·`deleted_at` 금지).

| 엔티티 | domain | 핵심 필드 | soft delete | tenant scope |
|---|---|---|---|---|
| 고객 Tenant | domain/tenant.py | `id, name, status(CustomerLifecycleState), created_at` | (lifecycle 상태로 표현 — 본 스토리는 생성/편집 중심) | 루트(생성 시 새 tenant_id) |
| 플랫폼 계정 PlatformAccount | domain/platform_account.py | `id, tenant_id, platform(Platform), label, username_ref(SecretRef), password_ref(SecretRef), auth_state(BaeminAuthState)` | (auth_state — 본 스토리는 생성/편집) | `tenant_id` |
| 모니터링 대상 MonitoringTarget | domain/monitoring_target.py | `id, tenant_id, platform_account_id, name, center_name, external_id, url, interval_minutes, status(MonitoringTargetStatus)` | `status=INACTIVE` (5.11 신규 소유) | `tenant_id` |
| 메시지 채널 MessengerChannel | domain/messenger_channel.py | `id, tenant_id, messenger(Messenger), telegram_chat_id?, thread_id?, kakao_room_name?, state(MessengerChannelState)` | `state=INACTIVE` (5.5 재사용) | `tenant_id` |
| 전송 규칙 DeliveryRule | domain/delivery_rule.py | `id, target_id, channel_id, template_id, enabled, send_only_on_change` | `enabled=False` | `target_id`→target.tenant_id |

- `telegram_chat_id`/`thread_id`/`kakao_room_name`은 **라우팅 식별자라 secret 아님**(ref화 금지). [Source: src/rider_server/domain/messenger_channel.py:1-7]
- `MonitoringTarget.center_name` = 쿠팡 기대 센터/상점명 검증 정본(FR-20). 비었거나 배민 기본값이면 다른 계정 실적 오발송 위험 → AC3 위험 경고. [Source: _bmad-output/project-context.md "쿠팡은 기대 센터/상점명 검증이 필수"]
- DeliveryRule 1:N fan-out = 같은 `target_id`에 `channel_id`가 다른 여러 인스턴스. [Source: src/rider_server/domain/delivery_rule.py:1-6]

### Secret 위생(AC3 — 절대 규칙)

- `PlatformAccount.username_ref`/`password_ref`는 `SecretRef`(불투명 핸들)만. DB 컬럼명도 `*_ref`(평문 `password`/`token` 컬럼 0). 폼이 평문을 주면 fail-closed 400. [Source: src/rider_server/domain/secret_ref.py, src/rider_server/db/models/account.py:27-28, src/rider_server/admin/actions_routes.py:478-503]
- 자유 텍스트 `reason`/`source`에 **센터/상점명·Kakao 방명 등 운영 식별자를 기대-마스킹으로 넣지 말 것** — `redact()`는 운영 식별자를 마스킹하지 않는다(token/OTP/password/chat_id만). `diff` 안에 들어간 운영 식별자는 `build_diff_redacted`(mask_operational_ids=True)가 가리지만, `reason`/`source` 평문 경로는 가리지 않는다. [Source: memory/redact-skips-operational-ids; src/rider_server/services/channel_registration.py:18-21]

### 변경 금지 lock(위반 = 회귀)

- **테이블 정확히 14개** — 신규 테이블/컬럼 0. 5개 엔티티는 모두 기존 14테이블에 존재. [Source: tests/server/test_db_schema.py:100-108]
- **enum 멤버 수 고정** — `MonitoringTargetStatus`=3(ACTIVE/PAUSED/INACTIVE), `MessengerChannelState`=4, `AuditResult`=3. 새 멤버 추가 금지(audit action 코드는 plain-string 상수라 자유 추가 가능). [Source: tests/server/test_domain_states.py:134-169; memory/enum-member-count-locks]
- **read-only 가드 화이트리스트 유지** — `routes.py`/`dashboard_service.py`/`dashboard_repository_postgres.py`/`severity.py`/`__init__.py`에 write-call 0. 신규 `crud_routes.py`는 화이트리스트에 넣지 말고, `test_admin_actions_guard.py`류 AST 가드로 "service 위임만" 강제. [Source: tests/server/test_admin_readonly_guard.py:29-64, tests/server/test_admin_actions_guard.py:21-31]
- **9-dep lock / 신규 third-party 금지** — server extras 고정. 폼 파싱은 stdlib `parse_qs`(python-multipart 미도입), HTMX는 CDN. [Source: src/rider_server/admin/actions_routes.py:102-118; memory/server-deps-go-in-optional-group]

### Project Structure Notes

- 레이어: Interface(`admin/crud_routes.py` + `templates/`) → Application(`services/admin_entity_service.py`) → Infrastructure(`services/admin_entity_repository_postgres.py` + in-memory). domain은 SQLAlchemy import 0. async(Cloud) — blocking sync 직접 호출 금지. [Source: _bmad-output/planning-artifacts/architecture.md#Structure-Patterns]
- 단방향 import: `rider_server → rider_crawl`만, `rider_agent` import 0. [Source: _bmad-output/project-context.md]
- 명명: DB snake_case 복수 테이블·`*_ref` secret 컬럼·UPPER_SNAKE 상태값/audit action; 라우트 함수 snake_case; 템플릿 partial `_<name>.html`. [Source: architecture.md#Naming-Patterns]
- 테스트: 평면 `tests/server/`(always-run) + `tests/negative/`(PG-gated). `pytest-asyncio` 미도입 → `asyncio.run`으로 async 구동(5.4~5.7 선례). 실행은 `.venv/Scripts/python.exe -m pytest`(WSL). [Source: tests/server/test_admin_actions.py:11-65; memory/dev-env-quirks]
- 잠재 충돌: read-only 대시보드가 점유한 `GET /admin/targets`·`/admin/channels`·`/admin/agents`·`/admin/auth-required`와 CRUD 경로가 겹치지 않게 구별되는 리소스 경로 사용. [Source: src/rider_server/admin/routes.py:147-191]

### 최신 기술 메모

- 기술 스택은 전부 고정(PostgreSQL/SQLAlchemy 2.x async + Alembic, FastAPI, Jinja2, HTMX 2 CDN, Pydantic v2 API 경계). **신규 의존성 추가 금지** — 외부 버전 조사 불필요, 코드베이스의 기존 패턴/버전을 정본으로 따른다. API 경계 검증이 필요하면 Pydantic v2, 내부 도메인은 dataclass 유지. [Source: _bmad-output/project-context.md, architecture.md#Data-Architecture]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.11] — 스토리 정의·AC 원본
- [Source: src/rider_server/admin/actions_routes.py] — 라우트 헬퍼/RBAC/HTMX fragment 패턴(5.7)
- [Source: src/rider_server/services/admin_action_service.py] — service write-owner·_audit·_scoped_*·INACTIVE 위임 주석(5.7)
- [Source: src/rider_server/security/access.py] — require_role 게이트(5.8)
- [Source: src/rider_server/services/channel_registration.py] — 채널 lifecycle 재사용 대상(5.5)
- [Source: src/rider_server/services/channel_repository_postgres.py] — repository ORM↔domain·save UPDATE-only(INSERT 신규 필요)
- [Source: src/rider_server/domain/*.py] — Tenant/Subscription/PlatformAccount/MonitoringTarget/MessengerChannel/DeliveryRule/SecretRef/states 계약(2.5)
- [Source: src/rider_server/db/models/{tenancy,account,messaging,audit,_columns}.py] — ORM 컬럼/PK/FK(5.2)
- [Source: src/rider_server/admin/templates/{dashboard,_actions,_action_result}.html] — Jinja2+HTMX 템플릿 패턴
- [Source: src/rider_server/main.py] — create_app seam 배선·라우터 등록·에러 envelope
- [Source: tests/server/{test_admin_actions,test_admin_actions_guard,test_admin_readonly_guard,test_db_schema,test_domain_states}.py] — 테스트 패턴·가드·lock
- [Source: _bmad-output/project-context.md] — 프로젝트 고유 규칙(secret/tenant/단방향 import/Kakao 방명)
- memory: [[redact-skips-operational-ids]], [[enum-member-count-locks]], [[db-tables-13-vs-14]], [[admin-routes-wallclock-severity]], [[story-5-8-audit-on-deny-anti-flooding]], [[pg-gated-files-hide-pure-helpers]], [[server-deps-go-in-optional-group]], [[dev-env-quirks]]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8) — bmad-dev-story workflow

### Debug Log References

- `.venv/Scripts/python.exe -m pytest tests/server/test_admin_entity_crud.py` → 29 passed
- `.venv/Scripts/python.exe -m pytest tests/server tests/negative -q` → 957 passed, 51 skipped (PG-gated, TEST_DATABASE_URL 미설정)
- lock 회귀: `test_db_schema.py`(14표)·`test_domain_states.py`(enum count)·`test_domain_models.py`(필드 lock)·`test_admin_readonly_guard.py`·`test_admin_actions_guard.py` 모두 green
- `git diff -w` 으로 CRLF/LF 잡음 없음 확인(추적 파일 80 insertions/8 deletions — 의도된 변경만)

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
- **Task 1 (영속 포트 + 구현):** `AdminEntityRepository` Protocol(services/) 신설 — 5개 엔티티별 `get_*`/`list_*`/`create_*`/`save_*`. 모든 write 는 `(entity, audit)` 쌍을 받아 동일 트랜잭션으로 영속(5.7 `transition_*(entity, audit)` 선례). `InMemoryAdminEntityRepository`(dict + `audits.append`) + `PostgresAdminEntityRepository`(신규 `insert(Row).values(...)` + `insert(AuditLogRow).values(**_audit_values(audit))` 동일 세션 commit — 코드베이스 최초 INSERT 경로). `_audit_values`/`_target_to_domain` 은 5.7 PG repo 에서 재사용. id 는 호출부(라우트 `uuid4`/테스트 고정) 주입.
- **Task 2 (오케스트레이션 service):** `AdminEntityService` — write 단일 소유처, 내부 `datetime.now()` 0(시각 `at` 주입). tenant scope 는 5.7 `_scoped_*` 패턴(불일치→`TenantScopeViolation`=404). secret 은 `_secret_ref_or_reject`(5.8 `looks_like_plaintext_secret` 재사용 — 평문/빈 핸들 fail-closed `ValueError`). center_name 위험은 순수 `is_center_name_risky`(쿠팡 + 빈/`DEFAULT_BAEMIN_CENTER_NAME` → True, 차단 아님). audit 는 5.7 `build_diff_redacted`(mask_operational_ids=True) + 신규 UPPER_SNAKE action 상수 + `AuditEntry`/`AuditResult` 재사용.
- **채널 비활성화 설계 결정:** 5.5 `ChannelRegistrationService.deactivate` 는 audit 가 없어 AC4(write+audit 동일 트랜잭션)를 만족하지 못한다. 그래서 5.5 상태머신을 **`assert_channel_transition`(전이 허용표) 재사용**으로 가져오고(register/verify/activate 재구현 0), `→INACTIVE` 전이 결과를 entity repo `save_messenger_channel(updated, audit)` 로 영속해 audit 를 같은 트랜잭션에 묶었다.
- **Task 3 (라우트):** `admin/crud_routes.py`(별도 `APIRouter(prefix="/admin")`) — read-only(`routes.py`)·액션(`actions_routes.py`)과 물리 분리. 변경=`require_role(OPERATOR)`, 조회 fragment=`require_role(VIEWER)`. 폼은 stdlib `parse_qs`(python-multipart 미사용). 복수 명사 리소스 경로(`/admin/customers`·`/platform-accounts`·`/monitoring-targets`·`/messenger-channels`·`/delivery-rules`·`/entities`)로 기존 GET(`/admin/targets` 등)과 충돌 회피. `create_app` 에 router 등록 + `app.state.admin_entity_service` 기본 배선(`_default_admin_entity_repository` — DATABASE_URL 있으면 PG, 없으면 in-memory) + `admin/__init__.py` 재노출.
- **Task 4 (템플릿):** `_entity_admin.html`(생성 폼=HTMX `hx-post` 직렬화, 편집/비활성화=id-in-path JS `crudUpdate`, 목록=`hx-trigger="load, every 30s"`) + `_entities.html`(목록 partial, autoescape). secret 입력은 `*_ref` 핸들 전용 라벨(평문 금지). `dashboard.html` 에 "엔티티 관리" 섹션 추가(폼은 정적, 목록만 polling).
- **Task 5 (테스트):** always-run 29건(순수 center/secret + service create/update/deactivate happy/scope/평문거부/fan-out/soft-delete/audit + 라우트 200/403/401/404/400/위험경고/목록/대시보드). PG-gated 3건(`tests/negative/test_admin_entity_crud_pg.py` — 실 INSERT+audit, soft-delete roundtrip, cross-tenant 미노출). 가드는 `test_admin_actions_guard.py` 에 crud_routes 직접 write/전이 0·service 위임·sqlalchemy/rider_agent import 0 추가(읽기 전용 화이트리스트 무변경 — crud_routes 미포함).
- **Task 6 (회귀·lock):** 신규 테이블/컬럼/enum 멤버 0(14표·MonitoringTargetStatus=3·MessengerChannelState=4·AuditResult=3 lock green). audit action 코드는 plain-string 상수라 enum count-lock 무관. 9-dep lock 유지(신규 third-party 0, HTMX CDN). 단방향 import(rider_agent 0) 유지.

### File List

- src/rider_server/services/admin_entity_service.py (신규 — 포트 + in-memory repo + service + 순수 helper)
- src/rider_server/services/admin_entity_repository_postgres.py (신규 — PG repo, 신규 INSERT/UPDATE + audit 동일 트랜잭션)
- src/rider_server/admin/crud_routes.py (신규 — CRUD 라우트, OPERATOR/VIEWER 게이트, service 위임)
- src/rider_server/admin/templates/_entity_admin.html (신규 — 엔티티 관리 폼 섹션)
- src/rider_server/admin/templates/_entities.html (신규 — 목록 partial)
- src/rider_server/admin/__init__.py (수정 — admin_crud_router 재노출)
- src/rider_server/admin/templates/dashboard.html (수정 — "엔티티 관리" 섹션 추가)
- src/rider_server/main.py (수정 — _default_admin_entity_repository + admin_entity_service seam + router 등록)
- tests/server/test_admin_entity_crud.py (신규 — always-run 순수/service/라우트)
- tests/negative/test_admin_entity_crud_pg.py (신규 — PG-gated 실 INSERT/soft-delete/cross-tenant)
- tests/server/test_admin_actions_guard.py (수정 — crud_routes/entity service 가드 추가)
- _bmad-output/implementation-artifacts/sprint-status.yaml (수정 — 5.11 in-progress→review)

## Change Log

| Date | Version | Description |
|---|---|---|
| 2026-06-14 | 0.1 | Story 5.11 dev-story 구현 완료 — Admin 엔티티 CRUD(고객/플랫폼 계정/모니터링 대상/메시지 채널/전송 규칙) 생성·편집·비활성화 UI. 신규 INSERT 경로 + write/audit 동일 트랜잭션, tenant 격리, secret `*_ref` 분리, center_name 위험 경고, OPERATOR/VIEWER RBAC. 신규 테이블/컬럼/enum 멤버 0. always-run 29 + PG-gated 3 + 가드 5 테스트 추가, 전체 회귀 green(957 passed/51 skipped). |
