---
baseline_commit: e9772aeddcaefb6faa6699b3788dbcbf590151dc (Story 5.7 — feat(story-5.7): Admin 수동 운영 액션과 고객/구독 상태 전이)
---

# Story 5.8: Audit log와 Admin 접근 보안

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 보안 담당 운영자,
I want 모든 Admin 변경을 완전한 audit log(actor·source·diff·target·reason·timestamp·result)로 남기고, 관리자 접근에 MFA·4역할·IP allowlist 를 강제하고, Agent/외부 service token 을 server-side 로 revoke/rotate 하며, 복구 환경을 non-sending 모드로 시작하고 싶다,
so that 누가 무엇을 왜 바꿨고 성공했는지 추적하고, 권한 없는 접근이나 유출된 token 으로 인한 사고를 fail-closed 로 막는다.

## Acceptance Criteria

> **범위 한 줄 요약**: 이 스토리는 **Admin 보안 경계 완성**이다 — 5.6/5.7 이 일부러 **seam**(`require_admin_session`·`resolve_admin_actor`·`resolve_agent_id`)으로 비워 둔 자리를 **MFA/4역할/IP allowlist 강제 + 완전한 audit 스키마 + token revoke/rotate + 복구 non-sending**로 채운다. **순수 정책·게이트·dispatch 파이프라인은 재구현하지 않는다**(5.7 의 `AdminActionService`·`SubscriptionGate`·`IdempotentDeliveryService`·기존 dispatch `send_enabled` 그대로 재사용). 신규 운영 UI(엔티티 CRUD)는 5.11, 모니터링 지표/알림 runbook 은 5.9 소유 — 본 스토리는 **보안·감사·복구성**만.
>
> **정본 이름 주의**: 상태값/enum/audit 컬럼/seam 이름은 **이미 구현된 코드를 따른다**(아래 Dev Notes "핵심 정본"). 임의의 새 테이블을 만들지 않는다(14표 lock). 새 컬럼/마이그레이션은 AC1·AC3 가 명시적으로 요구하는 것만 **additive**(0005)로 추가하고, **head lock 테스트를 0005 로 전진**시킨다(5.5→0004 선례).

**AC1 — 완전한 audit log 스키마와 모든 Admin 변경 추적 (P4-07, FR-34, implementation readiness gate)**
- **Given** Admin 이 설정을 변경할 때(고객/구독·secret/token·채널·대상·job 등)
- **When** audit log 를 기록하면
- **Then** **고객/secret/채널 설정 변경이 추적 가능**하고(위험 변경 누락 0),
- **And** audit log 레코드는 **actor, source, before/after value(`diff_redacted`), target IDs, reason, timestamp, result** 를 포함한다(implementation readiness gate — readiness-report:319). 현재 `audit_logs` 컬럼은 `actor_id`/`action`/`target_type`/`target_id`/`diff_redacted`/`created_at` 6개라 **`source`·`reason`·`result` 3개가 누락** → **additive 컬럼 3개 추가**(0005 마이그레이션)하고 `AuditEntry`/`record_audit`/5.7 액션 audit 경로를 이 3필드까지 채우도록 확장한다(5.7 은 `reason` 을 `diff_redacted` 에 임시 저장했음 — 5.8 이 first-class 컬럼으로 승격).
- **And** `result` 는 액션 결과(성공/실패/거부)를 기계가독 값으로 기록한다(권한 거부·fail-closed 거부도 audit 됨 — 보안 audit 의 핵심), `source` 는 변경 출처(예: `ADMIN_UI`/actor 역할/source IP), `reason` 은 운영자 자유 텍스트(**redaction 통과** — token/OTP/password/chat_id 평문 0).
- **And** audit 기록은 액션과 **동일 트랜잭션**(액션 성공·audit 누락 불가 — 5.7 불변식 유지)이고, secret 위생(`redact`/`redact_mapping`)을 어떤 컬럼에서도 깨지 않는다(NFR-8).

**AC2 — Admin 접근 제어: MFA·4역할·IP allowlist (FR-34)**
- **Given** Admin 접근을 보호해야 할 때
- **When** 접근 제어를 적용하면
- **Then** **모든 관리자 계정은 MFA 가 기본**이고(미검증 principal 의 privileged 액션은 403),
- **And** **VPN 또는 IP allowlist 같은 추가 제한**을 둘 수 있으며(허용되지 않은 source 는 거부),
- **And** **최소 역할 viewer/operator/secret-admin/break-glass** 가 구분된다(`AdminRole` 4멤버, count-lock 4). 역할별 게이트: **viewer**=읽기 전용 대시보드(GET)만, **operator**=5.7 운영 액션(activate/pause/retry/test-send/구독 전이 등), **secret-admin**=secret/token revoke·rotate(AC3), **break-glass**=긴급 override(전 권한, **강하게 audit** — 모든 break-glass 사용이 `result`/`source` 와 함께 기록).
- **And** 5.6/5.7 의 `require_admin_session`·`resolve_admin_actor` seam 을 **실 principal 해석기**(`AdminPrincipal`: actor_id·role·mfa_verified·source)로 교체한다. **기본 seam 은 fail-closed deny**(principal 미해결 → 401; MFA 미검증 → 403; 역할 부족 → 403; IP 불허 → 403). 운영/테스트는 `app.state` seam 으로 실제 강제기/주입 principal 을 넣어 검증한다(자격 저장·MFA 챌린지 인프라는 외부 auth front/IdP/config registry — **신규 DB 테이블 0**, 아래 열린 질문 #1).

**AC3 — token revoke/rotate 와 복구성(backup/restore·non-sending) (NFR-7, NFR-9, NFR-25)**
- **Given** token 을 폐기·교체해야 할 때
- **When** Agent token 이나 주요 외부 service token(Telegram bot token 등)을 관리하면
- **Then** **server-side revoke 또는 rotate 가 가능**하고 — Agent token: revoke 시 `resolve_agent_id` 가 해당 token→agent 를 더 이상 인증하지 않는다(revoked → None → 401 on claim/heartbeat/complete). rotate 는 기존을 무효화하고 새 발급 경로를 연다. 외부 service token(Telegram): secret 은 Secrets Manager 에 있고 DB 는 `*_ref` 만 두므로 **ref rotation 절차 + 무효화**로 처리(평문 DB 저장 0). revoke/rotate 자체가 AC1 audit 에 기록된다(secret-admin 역할).
- **And** **운영 DB 와 진단 산출물은 backup/retention/restore rehearsal 정책**을 가지며(문서 + "마이그레이션은 backup 확인 후 실행" 게이트), **복구 환경은 명시적 활성화 전까지 non-sending 모드로 시작**한다(NFR-9·25) — 복구/신규 환경은 기본 전송 차단(기존 dispatch `send_enabled`/kill switch 재사용)이고 운영자가 명시적으로 활성화해야 실전송이 나간다. 모호하면 보내지 않는다(fail-closed).

## Tasks / Subtasks

- [x] **Task 1 — audit_logs 스키마 완성: source·reason·result 컬럼 + 마이그레이션 0005 (AC1)**
  - [x] 1.1 `src/rider_server/db/models/audit.py` 에 **additive 컬럼 3개** 추가: `source: Mapped[str | None]`(String, nullable — 출처/역할/IP), `reason: Mapped[str | None]`(String, nullable — redaction 통과 자유텍스트), `result: Mapped[str]`(String, nullable=False — `AuditResult` 값). 기존 6컬럼은 보존(`actor_id`/`action`/`target_type`/`target_id`/`diff_redacted`/`created_at`). **컬럼명에 `token`/`secret`/`password` 단독 사용 금지**(forbidden-column 정확매치 가드) [Source: src/rider_server/db/models/audit.py; tests/server/test_db_schema.py:88,142-148].
  - [x] 1.2 `migrations/versions/0005_*.py` 신규(additive `op.add_column` × audit 3 + Task 4 의 agents token 컬럼) — `down_revision = "0004_messenger_channel_registration"`, round-trip `downgrade` 정확. **테이블 수 14 유지**(신규 테이블 0, 컬럼만). 0001~0004 수정 금지 [Source: migrations/versions/0004_messenger_channel_registration.py(패턴); migrations/versions/0002_jobs_lease_columns.py(op.add_column)].
  - [x] 1.3 **head lock 전진(필수)**: `tests/server/test_db_schema.py::test_single_migration_head_with_initial_base` 가 head=="0004…" 와 전체 체인을 하드코딩 → **0005 를 head 로, down_revision=0004 로** 갱신(5.5 가 0003→0004 로 전진시킨 선례와 동일). 단일 head·선형 체인 유지 [Source: tests/server/test_db_schema.py:401-421].
  - [x] 1.4 `REQUIRED_FIELDS["audit_logs"]` 에 `source`/`reason`/`result` 추가(게이트를 테스트로 잠금 — readiness-report:319 의 7필드를 스키마 가드가 강제). `test_each_table_has_required_fields`(subset 검사)·`test_migration_renders_every_model_column`(신규 컬럼이 0005 SQL 에 렌더) green 확인 [Source: tests/server/test_db_schema.py:55-93,390-398].
  - [x] 1.5 `result` 어휘: 신규 도메인 enum `AuditResult`(권장 `SUCCESS`/`FAILURE`/`DENIED`) 또는 plain-string 상수 — **새 enum 은 자체 count-lock 테스트 추가**(기존 enum 멤버 수정 0). String 컬럼(native PG ENUM 0 — `test_no_native_pg_enum_columns`) [Source: src/rider_server/domain/states.py; tests/server/test_db_schema.py:131-139; tests/server/test_domain_states.py].

- [x] **Task 2 — AuditEntry/record_audit 확장 + 5.7 액션 경로 retrofit (AC1)**
  - [x] 2.1 `src/rider_server/services/admin_action_service.py` 의 `AuditEntry`(현 6필드)에 `source`/`reason`/`result` 추가하고 `_audit()` 헬퍼·`record_audit` 포트(in-memory + PG repository)가 3필드를 채워 INSERT 하도록 확장. 5.7 이 `diff_redacted["reason"]` 에 넣던 reason 을 **first-class `reason` 컬럼**으로 옮긴다(diff 에도 남길지는 구현 재량 — 단 평문 secret 0) [Source: src/rider_server/services/admin_action_service.py:109-126,222-240].
  - [x] 2.2 5.7 의 모든 위험 액션 audit 호출이 새 3필드를 전달하도록 retrofit: `result`=액션 성공/실패/거부, `source`=principal 출처(AC2 의 `AdminPrincipal` 에서 도출), `reason`=운영자 입력. **권한 거부/fail-closed 거부도 `result=DENIED` 로 audit**(보안 audit 핵심 — 시도 자체를 남긴다 — `record_denied`/`record_break_glass`) [Source: src/rider_server/services/admin_action_service.py:259-291; src/rider_server/admin/actions_routes.py].
  - [x] 2.3 `diff_redacted`/`reason`/`source` 전부 `redact`/`redact_mapping(mask_operational_ids=True)` 통과 — token/OTP/password/chat_id 원문 평문 0. `redact` 가 room/center/store 이름은 안 가리므로 진단 breadcrumb 은 `mask_operational_ids=True` 사용 [Source: src/rider_crawl/redaction.py:130,231; memory redact-skips-operational-ids].

- [x] **Task 3 — Admin 접근 제어: AdminPrincipal·MFA·4역할·IP allowlist (AC2)**
  - [x] 3.1 신규 보안 모듈 `src/rider_server/security/`(architecture 정본 위치) — `AdminRole`(VIEWER/OPERATOR/SECRET_ADMIN/BREAK_GLASS, str Enum, count-lock 4), `AdminPrincipal`(dataclass: `actor_id`/`role`/`mfa_verified`/`source`), `resolve_admin_principal` seam(request→principal|None), `require_role(min_role)` 의존성, IP allowlist 검사(`source_ip` ∈ 설정 allowlist) [Source: architecture.md:144-149,176-180,446,478-482].
  - [x] 3.2 **기존 seam 교체(재구현 아님 — 강제기 주입)**: `admin/routes.py::require_admin_session` 과 `admin/actions_routes.py::resolve_admin_actor` 가 새 `resolve_admin_principal` 위에서 동작하도록 wiring. **기본 seam = fail-closed deny**(principal None→401; `mfa_verified=False`+privileged→403; 역할 부족→403; IP 불허→403). 5.6 기본 no-op deny 로 바뀌므로, 5.6/5.7 테스트는 **테스트용 principal 주입**으로 통과시킨다(아래 4.5) [Source: src/rider_server/admin/routes.py:71-95; src/rider_server/admin/actions_routes.py:48-62].
  - [x] 3.3 **역할 게이트 매핑**: GET 대시보드/fragment(`admin/routes.py`)=VIEWER↑, 운영 액션(`admin/actions_routes.py` POST)=OPERATOR↑, secret/token revoke·rotate(Task 4)=SECRET_ADMIN↑, break-glass=전 권한+강제 audit. 게이트는 **의존성/security 레이어**에서 — 읽기 전용 `routes.py` 에 직접 ORM write 추가 금지(아래 가드레일 #1: routes.py 는 read-only 화이트리스트, audit-on-deny 는 service/security 경유) [Source: tests/server/test_admin_readonly_guard.py:29-34].
  - [x] 3.4 `actor_id` 는 FK 없는 UUID 컬럼 유지(users 테이블 신설 0 — 14표 lock). principal 의 식별자가 UUID 면 컬럼에, 아니면 `diff_redacted`/`source` 에 보존(5.7 sentinel 패턴 계승). MFA 검증·자격 저장은 외부 auth front/config registry seam(열린 질문 #1) [Source: src/rider_server/db/models/audit.py; tests/server/test_db_schema.py:262(audit_logs no-FK lock)].

- [x] **Task 4 — token revoke/rotate + 복구 non-sending (AC3)**
  - [x] 4.1 **Agent token server-side revoke**: `agents` 테이블에 additive `token_revoked_at: Mapped[datetime | None]`(+ 선택 `token_rotated_at`) 컬럼 추가(Task 1.2 의 0005 에 포함, 테이블 수 14 유지, **`token` 단독 컬럼명 금지**). `api/jobs.py::resolve_agent_id` 경로가 revoked agent 의 token 을 거부(→None→401, `revocation_aware_resolver`). 권장 `AgentTokenService.revoke(agent_id, at, actor)`/`rotate(...)` — write 는 service+repository 동일 tx, AC1 audit(SECRET_ADMIN) [Source: src/rider_server/db/models/agent.py; src/rider_server/api/jobs.py:50-72; tests/server/test_db_schema.py:88].
  - [x] 4.2 **외부 service token rotate/revoke**: Telegram bot token 등은 Secrets Manager + DB `*_ref` 만이므로 **ref rotation/무효화 절차**로 처리(평문 DB 0). secret-admin 액션이 `messenger_channels`/secret_ref 바인딩을 갱신·무효화하고 audit(`rotate_external_token`, 평문 fail-closed 거부). 평문 token 을 응답/로그/HTML 에 노출 0 [Source: operations-security-test-contract.md:7-11; src/rider_server/domain/secret_ref.py].
  - [x] 4.3 **복구 non-sending 모드**: 신규/복구 환경은 **기본 전송 차단**(env 플래그 `RIDER_SENDING_ENABLED` 기본 OFF) — 기존 dispatch `send_enabled`/kill switch 와 compose(`effective_send_enabled`, 신규 차단 경로 신설 금지). 운영자 **명시적 활성화** 전까지 실전송 0. 모호 시 차단(fail-closed) [Source: src/rider_server/services/dispatch_service.py; src/rider_server/migration/cutover.py(kill switch)].
  - [x] 4.4 **backup/retention/restore rehearsal 정책 문서**: `docs/runbooks/backup-restore.md` — DB PITR/≥7일 보존/수동 스냅샷, "마이그레이션은 backup 확인 후 실행" 게이트, restore rehearsal 절차, 복구 후 non-sending→명시적 활성화 흐름. (모니터링 지표 runbook 7종은 5.9 소유 — 본 스토리는 backup/restore + non-sending 만) [Source: architecture.md:164,205-213; operations-security-test-contract.md:33-40].

- [x] **Task 5 — create_app 와이어링 + seam (AC1·AC2·AC3)**
  - [x] 5.1 `create_app` 에 `resolve_admin_principal` seam + IP allowlist 설정 + non-sending 플래그를 주입(기존 `require_admin_session`/`resolve_admin_actor`/`resolve_agent_id` seam 과 동형). 기본값 fail-closed, 테스트는 in-memory principal/강제기 주입 [Source: src/rider_server/main.py:140-180].
  - [x] 5.2 `AgentTokenService`(또는 revoke seam) + 확장된 audit repository 를 `create_app` seam 으로 주입(`_default_admin_action_repository` 동형 PG/in-memory 분기). 라우터 include 정합 [Source: src/rider_server/main.py:47-180].
  - [x] 5.3 Settings 에 보안 관련 env(allowlist, non-sending, MFA 강제 토글) 추가 — **신규 third-party deps 0**(9-dep/server extra lock). stdlib + 기존 FastAPI/SQLAlchemy 만 [Source: src/rider_server/settings.py; memory server-deps-go-in-optional-group].

- [x] **Task 6 — 테스트 (AC1·AC2·AC3, 4-tier)**
  - [x] 6.1 **always-run 순수/service(무 DB, fake 주입)**: (AC2) `require_role` 게이트 — viewer 가 액션 시도 403, operator 가 secret 액션 403, secret-admin 통과, break-glass override+audit; MFA 미검증 privileged 403; IP 불허 403; principal None 401. (AC1) `AuditEntry` 3신규필드 채움·`result=DENIED` 가 거부 시 기록·redaction 통과. (AC3) revoked agent token 거부·rotate 후 구 token 무효·non-sending 시 dispatch send 0. 시각/actor/principal 주입(결정성) [Source: memory pg-gated-files-hide-pure-helpers].
  - [x] 6.2 **audit 완전성 검증**: 위험 액션(구독 전이·retry·test-send·token revoke/rotate·break-glass)이 `audit_logs` 에 actor+source+action+target+reason+timestamp+**result** INSERT(동일 tx). `diff_redacted`/`reason`/`source` 에 token/OTP/password/chat_id 평문 0(redact 어서션). 거부된 시도도 `result=DENIED` 로 남음 [Source: src/rider_server/db/models/audit.py; memory redact-skips-operational-ids].
  - [x] 6.3 **라우트(`TestClient`)**: 주입 principal 별 — viewer GET 200 / POST 액션 403; operator 액션 200·HTMX fragment; secret-admin token revoke 200; 무 principal 401; MFA 미검증 403; IP 불허 403. 4xx/401/403 은 `{"error":{"code","message_redacted"}}` envelope. revoke 후 동일 token claim 401 [Source: src/rider_server/admin/routes.py; actions_routes.py; api/jobs.py].
  - [x] 6.4 **가드 무회귀(AST, raw grep 금지)**: 5.6 read-only 화이트리스트(routes/dashboard_service/dashboard_repository_postgres/severity/__init__)가 **여전히 통과·vacuous 아님**(보안 게이트 추가가 write 호출을 routes.py 에 들이지 않음); 신규 `security/` 모듈은 단방향 import(`rider_agent` 0)·service 위임. 신규 audit write 는 service 경유 [Source: tests/server/test_admin_readonly_guard.py; src/rider_server/admin/actions_routes 가드; memory negative-guard-tests-use-ast].
  - [x] 6.5 **PG-gated**(`@pytest.mark.skipif` no `TEST_DATABASE_URL`): 실 PostgreSQL 에서 audit 7필드 영속·`agents.token_revoked_at` UPDATE·revoke 후 claim 차단·0005 upgrade/downgrade round-trip·cross-tenant audit 누출 0. 순수 의미(게이트·revoke·non-sending)는 6.1 로 always-run 추출(CI PG skip 시에도 실행) [Source: tests/server/test_db_schema.py; memory pg-gated-files-hide-pure-helpers].
  - [x] 6.6 **lock 무회귀**: **14표 유지**(`test_metadata_has_exactly_14_contract_tables` — 신규 테이블 0, 컬럼만 additive)·**head=0005**(1.3 갱신 후 green)·9-dep lock·기존 enum count-lock(`CustomerLifecycleState`11/`SubscriptionStatus`4/`MonitoringTargetStatus`3/`FailureCategory`7) 전부 유지(기존 enum 멤버 수정 0). 신규 enum(`AdminRole`4/`AuditResult`)만 자체 count-lock 추가 [Source: tests/server/test_db_schema.py:95-98; tests/server/test_domain_states.py:74,85,129; memory enum-member-count-locks, db-tables-13-vs-14, server-deps-go-in-optional-group].
  - [x] 6.7 테스트 컨벤션: 상단 docstring `"""Story 5.8 / ACx …"""`, `from __future__ import annotations`, fake fixture만(실제 토큰/전화/이메일/chat_id 금지), `tests/server/` flat(`__init__.py` 없음). async-boundary 가드(신규 async 모듈에 `time.sleep`/`subprocess` 0)는 rglob 가드가 자동 커버 [Source: tests/server/test_server_app.py:1-6; tests/server/test_server_async_boundary.py].

### Review Follow-ups (AI)

비차단(non-blocking) — 본 스토리 범위는 충족(0 CRITICAL). 후속 스토리/배포 배선에서 처리.

- [ ] [AI-Review][Low] AC3 `resolve_agent_id` revoke 반영을 **배포 시 명시 배선** 필요 — `create_app` 은 5.3 stub(`default_resolve_agent_id`, 모든 token→"agent")을 그대로 두므로 `revocation_aware_resolver` 합성이 production 기본에는 없다(seam 패턴상 의도된 deferral, 순수 helper로 검증됨). 실 token→agent_id 매핑 도입 시 `revocation_aware_resolver` 로 감싸 revoke→401 을 활성화한다. PG `is_revoked` 는 async 라 sync `resolve_agent_id` seam 과 합성하려면 sync 조회 경로가 필요 [src/rider_server/main.py:188; src/rider_server/services/agent_token_service.py:40].
- [ ] [AI-Review][Low] 복구 non-sending 게이트(`effective_send_enabled`/`app.state.sending_enabled`)는 현재 **server-side 소비처가 없다**(실 dispatch 는 agent-side `AppConfig.send_enabled`). 본 스토리는 플래그·순수 helper·runbook 까지만(신규 차단 경로 신설 금지). server-side dispatch 배선 스토리가 `effective_send_enabled` 를 호출하도록 연결할 것 [src/rider_server/services/recovery.py:15; memory story-5-8-audit-on-deny-anti-flooding].
- [ ] [AI-Review][Low] IP allowlist 의 `source_ip` 는 클라이언트 `X-Forwarded-For` 를 무조건 선두 신뢰한다 — **신뢰 reverse-proxy 가 XFF 를 강제로 set/strip 하는 배포** 를 전제(docstring 명시). 직접 노출 시 XFF spoofing 으로 allowlist 우회 가능. 배포에서 trusted-proxy 경계를 보장하거나 향후 trusted-hop 설정을 추가 검토 [src/rider_server/security/access.py:43].

## Dev Notes

### 이 스토리의 본질: 5.6/5.7 이 비워 둔 보안 seam 을 fail-closed 로 채운다

- 5.6=보기(read), 5.7=하기(act), **5.8=지키기(secure)**. 5.6/5.7 의 코드·docstring 이 **명시적으로** "MFA/4역할/세션·token lifecycle 은 5.8 소유"라고 seam 을 비워 뒀다 — 5.8 은 그 seam 위에 **강제기**를 얹는다(정책·게이트·dispatch 재구현 0).
- **재사용**: 액션 오케스트레이션·audit-write = 5.7 `AdminActionService`(확장만), 구독/HELD = `SubscriptionGate`, dedup = `IdempotentDeliveryService`, dispatch non-sending = 기존 `send_enabled`/kill switch, redaction = `redact`/`redact_mapping`. **새 보안 정책 코어만 신설**(AdminPrincipal·역할 게이트·token revoke·non-sending 게이트).
- **신규 테이블 0**: MFA/역할/operator 자격은 **DB 테이블이 아니라** 역할 enum + 외부 auth front/config registry seam 으로 모델링(14표 lock 유지). audit 완성·agent revoke 는 **기존 테이블에 additive 컬럼만**.

### 핵심 정본(검증 완료) — 절대 틀리면 안 되는 이름

- **`audit_logs` 현재 컬럼(6)**: `id`(uuid pk)·`actor_id`(uuid|None, FK 없음)·`action`(str)·`target_type`(str|None)·`target_id`(uuid|None, 다형 FK 없음)·`diff_redacted`(JSON)·`created_at`(tz-aware). **5.8 추가(3)**: `source`(str|None)·`reason`(str|None)·`result`(str) → readiness gate 7필드(actor/source/diff/target/reason/timestamp/result) 충족 [Source: src/rider_server/db/models/audit.py; implementation-readiness-report:319].
- **마이그레이션 head 현재 = `0004_messenger_channel_registration`**(체인 0001→0002→0003→0004). 5.8 이 **0005 additive** 추가 → head=0005, down_revision=0004. `test_single_migration_head_with_initial_base` **갱신 필수** [Source: tests/server/test_db_schema.py:401-421; migrations/versions/].
- **`AuditEntry`(현 6필드)**: `services/admin_action_service.py:109-126` — 3필드 확장 대상. `UNAUTHENTICATED_ACTOR="UNAUTHENTICATED_ADMIN"` sentinel(5.7) [Source: src/rider_server/services/admin_action_service.py:56-126].
- **seam 3종(5.8 교체 대상)**: `require_admin_session`(admin/routes.py:85, 기본 no-op→**fail-closed deny**), `resolve_admin_actor`(admin/actions_routes.py:60, 기본 sentinel→**실 principal actor**), `resolve_agent_id`(api/jobs.py:69, 기본 통과→**revoke 반영**) [Source: src/rider_server/admin/routes.py:71-95; actions_routes.py:48-62; api/jobs.py:50-72].
- **`AdminRole`(신규, 4멤버 count-lock)**: `VIEWER`/`OPERATOR`/`SECRET_ADMIN`/`BREAK_GLASS`(architecture 정본 순서). **4역할 = architecture 결정**(line 144-145) — 임의 5번째 추가 금지 [Source: architecture.md:144-145,178].
- **`agents` 테이블**: token 컬럼 없음(token 은 Agent-local DPAPI). server-side revoke = additive `token_revoked_at`(권장). **`token` 단독 컬럼명 금지**(forbidden-column 정확매치 — `token_revoked_at` 는 안전) [Source: src/rider_server/db/models/agent.py; tests/server/test_db_schema.py:88].
- **dispatch 전송 게이트**: 기존 `DispatchService` `send_enabled`(3.x)·migration kill switch(cutover.py) — non-sending 은 이 위에 **default-OFF** 추가(신규 차단 경로 금지) [Source: src/rider_server/services/dispatch_service.py; src/rider_server/migration/cutover.py].

### 🚨 가드레일(위반 시 CI 실패) — 우선순위 순

1. **[#1 트랩] 5.6 read-only 가드 + audit-on-deny**: `test_admin_readonly_guard.py` 가 read-only 화이트리스트(`routes.py`/`dashboard_service.py`/`dashboard_repository_postgres.py`/`severity.py`/`__init__.py`)에서 write 호출을 금지한다. 보안 게이트(역할/MFA/IP 검사)는 **read 라 OK** 이지만, **거부 시 audit(`result=DENIED`) 기록은 write** → 반드시 **service/security 레이어 경유**(routes.py 에 직접 INSERT 금지). 화이트리스트 자기검증(vacuous 아님) 유지 [Source: tests/server/test_admin_readonly_guard.py:29-34,134-144].
2. **14표 lock(신규 테이블 0)**: MFA/역할/operator 자격을 위한 **users/admin_accounts 등 신규 테이블 신설 금지**. 역할=enum, 자격/MFA=외부 seam, audit/agent revoke=**기존 테이블 additive 컬럼만** [Source: tests/server/test_db_schema.py:95-98; memory db-tables-13-vs-14].
3. **head lock 전진(0004→0005)**: 0005 additive 추가 시 `test_single_migration_head_with_initial_base` 의 하드코딩 head/down_revision 갱신(미갱신 시 즉시 실패). 0001~0004 수정 금지·단일 head·선형 체인 [Source: tests/server/test_db_schema.py:401-421].
4. **fail-closed 기본값**: 모든 보안 seam 기본값은 **deny**(principal None→401, MFA 미검증→403, 역할 부족→403, IP 불허→403, non-sending 기본 ON). 5.6/5.7 의 permissive no-op 기본을 5.8 이 deny 로 바꾸므로 기존 5.6/5.7 테스트는 **principal 주입**으로 통과시킨다(테스트 회귀 = 의도된 보안 강화, 주입으로 해소) [Source: architecture.md:561 (fail-closed 패턴)].
5. **secret 위생(보안 audit 의 핵심)**: token/OTP/password/chat_id 원문 평문 0 — `diff_redacted`·`reason`·`source`·HTML·로그·예외 전부. revoke/rotate 응답에 token 평문 0. `redact`/`redact_mapping(mask_operational_ids=True)` 경유 [Source: src/rider_crawl/redaction.py; operations-security-test-contract.md:13-19,93; memory redact-skips-operational-ids].
6. **enum count-lock**: 기존 `CustomerLifecycleState`11·`SubscriptionStatus`4·`MonitoringTargetStatus`3·`FailureCategory`7 **수정 0**. 신규 `AdminRole`(4)·`AuditResult` 는 **자체** count-lock 테스트 추가(잠금이 여러 파일에 흩어지니 변경 전 repo 전체 grep) [Source: tests/server/test_domain_states.py:74,85,129; memory enum-member-count-locks].
7. **9-dep / server extra lock**: MFA/TOTP/JWT 라이브러리 등 **신규 third-party 추가 금지**. stdlib(`hmac`/`hashlib`/`secrets`/`ipaddress`) + 기존 FastAPI/SQLAlchemy 로 구현. MFA 검증은 외부 front/IdP 신뢰 또는 stdlib TOTP 수준 — 신규 deps 0 [Source: memory server-deps-go-in-optional-group].
8. **단방향 import**: `rider_server`→`rider_crawl` 만. `security/` 모듈도 `rider_agent` import 0(AST 가드) [Source: project-context.md].
9. **tenant scope**: customer-owned audit/액션은 tenant scope 유지(cross-tenant audit 누출 0). agent revoke 는 전역(agent fleet 은 tenant 무관, 5.6 선례) [Source: architecture.md:482].
10. **native PG ENUM 0 / no-FK audit / async-boundary**: 신규 컬럼은 String(`AuditResult` native enum 아님)·`audit_logs`/`agents` 는 FK 없음 유지·async 모듈에 `time.sleep`/`subprocess` 0 [Source: tests/server/test_db_schema.py:131-139,262; test_server_async_boundary.py].

### 재사용 자산(재구현 금지 — compose/import만)

- **`AdminActionService`/`AuditEntry`/`record_audit`/`build_diff_redacted`**(5.7): audit-write 단일 소유처 — **확장**(3필드)만, 재작성 금지 [Source: src/rider_server/services/admin_action_service.py].
- **`redact`/`redact_mapping`/`redacted_error_event`**: secret 위생 — audit 모든 텍스트 컬럼에 적용 [Source: src/rider_crawl/redaction.py:130,231,248].
- **seam 패턴**: `create_app` 의 `app.state.<seam>` + `_default_*`(DATABASE_URL/settings 분기) — 신규 보안 seam 도 동형 [Source: src/rider_server/main.py:47-180].
- **에러 envelope**: `HTTPException`(401/403/4xx) raise → 전역 핸들러 `{"error":{"code","message_redacted"}}`. 인증/인가 실패도 이 포맷 [Source: src/rider_server/main.py:57-69].
- **dispatch send 게이트**: `DispatchService(send_enabled=...)` + kill switch — non-sending 은 이 위에 default-OFF [Source: src/rider_server/services/dispatch_service.py; migration/cutover.py].
- **`resolve_agent_id`**: bearer→agent_id seam(api/jobs.py) — revoke 를 이 resolver 가 반영(revoked→None→401) [Source: src/rider_server/api/jobs.py:50-72].

### Project Structure Notes

- **신규(권장)**: `src/rider_server/security/__init__.py`·`principal.py`(`AdminPrincipal`/`AdminRole`/`resolve_admin_principal`)·`access.py`(`require_role`/IP allowlist/MFA 게이트), `src/rider_server/services/agent_token_service.py`(revoke/rotate + audit), `docs/runbooks/backup-restore.md`. 도메인 enum 은 `domain/states.py` 에 `AdminRole`/`AuditResult` 추가(또는 `security/` 내). 테스트: `tests/server/test_admin_security.py`(역할/MFA/IP 게이트), `tests/server/test_audit_log_schema.py`(7필드·redaction), `tests/server/test_agent_token_revoke.py`(revoke/rotate), `tests/server/test_recovery_non_sending.py`, `tests/negative/test_security_pg.py`(PG-gated 영속·cross-tenant).
- **수정**: `src/rider_server/db/models/audit.py`(+3컬럼)·`db/models/agent.py`(+token_revoked_at), `migrations/versions/0005_*.py`(신규 additive), `services/admin_action_service.py`(`AuditEntry`+3·record_audit), `admin/routes.py`+`admin/actions_routes.py`(principal/역할 게이트로 seam 교체), `api/jobs.py`(resolve_agent_id revoke 반영), `services/dispatch_service.py` 호출부 또는 `main.py`(non-sending 게이트), `main.py`+`settings.py`(보안 seam/env), `tests/server/test_db_schema.py`(head 0005·REQUIRED_FIELDS audit 7).
- **변경 금지**: 0001~0004 마이그레이션, 기존 enum 멤버, `SubscriptionGate`/`IdempotentDeliveryService`/`queue.states`(정책 코어 — 호출만), 5.6 read-only 대시보드의 read-only 성질, `rider_crawl`/`rider_agent` 패키지, 14표 스키마(테이블 수).
- 보안 모듈을 `security/`(architecture 정본 위치, line 446)에 두고 admin/ 와 분리하면 read-only 가드 scope(admin/ 화이트리스트)와 충돌 없이 게이트를 얹을 수 있다.

### 열린 질문(구현 시 결정 — 막지 말고 가장 안전한 선택)

1. **MFA/operator 자격 저장(가장 트리키, 14표 lock)**: users/admin_accounts 테이블 신설은 14표 lock 위반. **권장 결정**: (a) 역할=`AdminRole` enum, (b) operator 식별·MFA 검증은 **외부 auth front/IdP/reverse-proxy 신뢰 헤더 + config(env/secret store) operator registry** seam(신규 DB 테이블 0), (c) 서버는 principal 의 `mfa_verified`·`role`·`source` 를 **강제·audit** 만. 테스트는 principal 주입으로 e2e 검증. 실 IdP 통합은 배포 인프라 결정 — 코드는 fail-closed seam 으로 충분. native MFA(TOTP) 가 필요하면 stdlib(`hmac`/`hashlib`)로 신규 deps 0 구현 가능하나 비밀 저장 위치(외부)는 동일.
2. **`result`/`source` 어휘**: `result`=`SUCCESS`/`FAILURE`/`DENIED`(권장 enum), `source`=출처 문자열(`ADMIN_UI`/역할/source IP 조합). 자유 텍스트면 redaction 통과. 거부(`DENIED`)도 반드시 기록(보안 audit).
3. **Agent token revoke 영속 위치**: 권장 `agents.token_revoked_at`(additive, server-side revoke 가 `resolve_agent_id` 에서 반영). 대안: 별도 revocation 목록(테이블 신설=lock 위반이라 비권장). rotate 는 token 자체가 Agent-local 이므로 server 는 무효화+재발급 경로만(실 재발급은 4.2 registration 흐름 재사용).
4. **외부 service token rotate 범위**: Telegram bot token 은 Secrets Manager+`*_ref` 라 코드는 ref 무효화/회전 절차 + audit 만(평문 0). 실제 Secrets Manager 호출은 배포 인프라 — MVP 는 secret_ref 바인딩 갱신 + 절차 문서로 충분(fail-closed: ref 미해결 시 전송 거부 — 5.5 webhook secret 선례).
5. **non-sending 활성화 UX**: 복구/신규 환경 기본 OFF, 운영자 명시적 활성화(env 토글 또는 admin 액션). break-glass/secret-admin 만 활성화 가능 + audit. 모호 시 OFF 유지(fail-closed). 전역 kill switch(5.10 dispatch kill switch)와 정합 — 본 스토리는 "복구 시 기본 차단"만, 전역 pause/kill 의 전체 매트릭스는 5.10.

### 개발 환경·프로세스(매 스토리 반복되는 함정)

- pytest 는 **`.venv/Scripts/python.exe -m pytest`** 로 실행(WSL `python3` 미설치; `pythonpath=["src"]`, editable 설치 없음 — 한글 경로 `.pth` 가 cp949 UnicodeDecodeError). `git diff -w` 로 CRLF/LF noise 무시 [Source: memory dev-env-quirks].
- 신규 파일은 `\n` 으로 작성(CRLF 재변환이 content-compare 멱등 깨뜨림). 템플릿·마이그레이션·docs 동일 [Source: memory crlf-roundtrip-idempotency].
- 커밋 컨벤션 `feat(story-5.8): …`, baseline 커밋 `e9772ae`(5.7).
- **테스트 카운트는 review 시 재측정**(dev-exit 수치는 qa-generate-e2e 추가로 stale). dev-exit vs post-QA 구분해 정본 한 숫자를 review 에서 기록 [Source: memory stale-test-count-a2].
- **PG-gated 파일이 순수 helper 를 가린다**: 역할 게이트 판정·revoke 의미·non-sending 판정 같은 순수 의미는 always-run 단위로 별도 추출(CI PG skip 시에도 실행) [Source: memory pg-gated-files-hide-pure-helpers].
- **라우트는 실 `now()` 사용**(주입 불가): 시각 기반 단언은 service/순수 레이어(시각 주입), 라우트는 인가 성공/거부/HTML 만 검증 [Source: memory admin-routes-wallclock-severity].

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.8 (lines 1074-1095)] — 스토리 정의·3 AC(audit/접근제어/token·복구), P4-07, FR-34, NFR-7·9·25
- [Source: _bmad-output/planning-artifacts/epics.md:906] — Epic 5 개요(MFA·4역할·audit log·token revoke·backup/restore = Epic 5 보안·복구성)
- [Source: _bmad-output/planning-artifacts/architecture.md:144-150] — Access model(MFA 필수·4역할·token tenant+job-type scope), tenant isolation; :176-185 Authentication & Security(MFA/4역할/VPN·IP allowlist·revoke/rotate·redaction); :197-203 Frontend(audit log view·동일 인증/세션); :446 `security/`(MFA·4역할·token scope·audit) 정본 위치; :478-482 API Boundaries(Admin=세션+MFA·역할, Agent=token-auth, 인증 경계 분리); :488 Service-only writes
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md:38] — `audit_logs` 필드(actor_id/action/target_type/target_id/diff_redacted/created_at); :92 Audit log view
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md:7-11] — Secret Storage(Telegram rotate/revoke, Agent token server-side revoke); :13-19 Redaction; :33-40 Deployment(migration after backup confirmation)
- [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-06-13.md:319] — audit fields gate(actor/source/before-after/target IDs/reason/timestamp/result); :134 FR-34; :256 FR-34 → Story 5.8(audit+MFA)
- [Source: src/rider_server/db/models/audit.py] — `AuditLog` ORM(현 6컬럼·FK 없는 다형 참조·JSON diff) — AC1 확장 대상
- [Source: src/rider_server/services/admin_action_service.py:56-240] — `AuditEntry`/`_audit`/`record_audit`/`build_diff_redacted`/`UNAUTHENTICATED_ACTOR`(5.7) — 확장 대상
- [Source: src/rider_server/admin/routes.py:71-95] — `require_admin_session` seam(5.8 교체); [Source: src/rider_server/admin/actions_routes.py:48-62] — `resolve_admin_actor` seam(5.8 교체)
- [Source: src/rider_server/api/jobs.py:50-72] — `resolve_agent_id`/`resolve_agent` bearer seam(revoke 반영 대상)
- [Source: src/rider_server/db/models/agent.py] — `agents` 테이블(token 컬럼 없음 — server-side revoke=additive `token_revoked_at`)
- [Source: src/rider_server/main.py:47-180] — create_app seam/default 분기/router include 패턴
- [Source: src/rider_crawl/redaction.py:130,231,248] — `redact`/`redact_mapping`/`redacted_error_event`(secret 위생)
- [Source: tests/server/test_db_schema.py:36-98,131-148,262,390-421] — 14표 lock·REQUIRED_FIELDS·forbidden-column·native-enum 0·no-FK audit·migration-renders·head lock(0004→0005 갱신 대상)
- [Source: tests/server/test_domain_states.py:74,85,129] — enum count-lock(11/4/7) — 기존 수정 0, 신규 enum 자체 lock
- [Source: tests/server/test_admin_readonly_guard.py:29-34,134-144] — 5.6 read-only 화이트리스트(audit-on-deny 는 service 경유)
- [Source: migrations/versions/0004_messenger_channel_registration.py; 0002_jobs_lease_columns.py] — additive `op.add_column` + 14표 유지 패턴(0005 모델)
- [Source: _bmad-output/implementation-artifacts/5-7-admin-수동-운영-액션과-고객-구독-상태-전이.md] — 직전 스토리: audit-write·seam·sentinel·read-only 가드 narrowing·"MFA/4역할/audit 스키마는 5.8" 위임
- [Source: _bmad-output/project-context.md] — 단방향 import·secret 정책·redaction·service 경계·9-dep lock

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (BMAD create-story workflow)

### Debug Log References

- 기본 seam fail-closed 전환(가드레일 #4)으로 5.6/5.7 라우트 테스트 24건이 401 회귀 → 의도된 보안 강화. `test_admin_dashboard.py`(VIEWER principal 주입)·`test_admin_actions.py`(OPERATOR principal 주입 + `require_admin_session` 거부 테스트를 principal None→401 로 재작성)로 해소.
- 도메인 `__all__` 정본 lock(`test_domain_models.py::test_package_all_reexports_eight_models_and_all_enums`)이 `AuditResult` 추가로 실패 → 기대 집합에 `AuditResult` 추가(흩어진 enum lock — [[enum-member-count-locks]] 패턴 재확인).
- pytest 실행은 `.venv/Scripts/python.exe -m pytest`(WSL `python3`/`PYTHONPATH=src` 직접 실행은 한글 경로로 모듈 미해석 — pyproject `pythonpath=["src"]` 의존).

### Completion Notes List

- **AC1 (audit 완성):** `audit_logs` 에 `source`/`reason`/`result` additive 3컬럼(0005) → readiness gate 7필드(actor/source/diff/target/reason/timestamp/result) 충족. `AuditEntry`/`_audit`/PG `_audit_values` 확장, 5.7 위험 액션 전부 `source`/`result` retrofit(reason 은 first-class 컬럼으로 승격, diff 에도 보존). `source`/`reason` 은 `redact` 통과(평문 secret 0). 신규 `AuditResult`(SUCCESS/FAILURE/DENIED) enum + 자체 count-lock 3.
- **AC2 (접근 제어):** 신규 `security/` 패키지 — `AdminRole`(VIEWER/OPERATOR/SECRET_ADMIN/BREAK_GLASS, count-lock 4, 단조 rank), `AdminPrincipal`, `resolve_admin_principal` seam(기본 fail-closed None→401), `require_role(min_role)` 의존성(IP allowlist→MFA→역할 rank, 거부 시 `result=DENIED` audit via service — read-only 가드 정합), `enforce_session`(VIEWER, write-free). 5.6 `require_admin_session`·5.7 `resolve_admin_actor` 를 principal 위에서 동작하도록 교체. 게이트: GET=VIEWER↑, POST 운영=OPERATOR↑, token=SECRET_ADMIN↑, break-glass 사용은 강제 audit.
- **AC3 (token·복구성):** `AgentTokenService` revoke/rotate(`agents.token_revoked_at`/`token_rotated_at` additive 0005, server-side revoke 동일 tx + SECRET_ADMIN audit). `revocation_aware_resolver` 로 revoked→None→401(claim/heartbeat/complete). 외부 service token 은 `rotate_external_token`(`*_ref` 핸들만, 평문 fail-closed 거부). 복구 non-sending: `Settings.sending_enabled` 기본 OFF + `effective_send_enabled(send_enabled, sending_enabled)` 로 기존 dispatch `send_enabled` 와 AND compose(신규 차단 경로 0). `docs/runbooks/backup-restore.md`(PITR·≥7일·backup 후 마이그레이션 게이트·restore rehearsal·non-sending→명시 활성화).
- **불변식 유지:** 14표 lock(신규 테이블 0, 컬럼만 additive)·head 0004→0005 전진(round-trip)·9-dep lock(신규 third-party 0 — stdlib `ipaddress` 만)·기존 enum 수정 0·`audit_logs`/`agents` no-FK·native PG ENUM 0(String)·단방향 import(`security/` `rider_agent` 0)·async-boundary(`time.sleep`/subprocess 0).
- **테스트:** 전체 1815 passed / 41 skipped(PG-gated). 신규 always-run 5파일 + PG-gated 1파일. 라우트는 실 `now()` 라 시각 단언 회피(인가 성공/거부/audit result 만 검증).

### File List

**신규(제품 코드):**
- `src/rider_server/security/__init__.py`
- `src/rider_server/security/principal.py` (`AdminRole`/`AdminPrincipal`/`role_satisfies`/`is_privileged`)
- `src/rider_server/security/access.py` (`require_role`/`enforce_session`/`ip_allowed`/`source_ip`/audit-on-deny)
- `src/rider_server/services/agent_token_service.py` (`AgentTokenService`/in-memory repo/`revocation_aware_resolver`/`looks_like_plaintext_secret`)
- `src/rider_server/services/agent_token_repository_postgres.py` (PG `AgentTokenRepository`)
- `src/rider_server/services/recovery.py` (`effective_send_enabled`)
- `migrations/versions/0005_audit_fields_and_agent_token_revoke.py`
- `docs/runbooks/backup-restore.md`

**수정(제품 코드):**
- `src/rider_server/db/models/audit.py` (+`source`/`reason`/`result`)
- `src/rider_server/db/models/agent.py` (+`token_revoked_at`/`token_rotated_at`)
- `src/rider_server/domain/states.py` (+`AuditResult`)
- `src/rider_server/domain/__init__.py` (`AuditResult` 재노출)
- `src/rider_server/services/admin_action_service.py` (`AuditEntry`+3·`_audit`·`record_denied`/`record_break_glass`·source retrofit·신규 action/target_type 상수)
- `src/rider_server/services/admin_action_repository_postgres.py` (`_audit_values`+3컬럼)
- `src/rider_server/admin/routes.py` (`require_admin_session` 기본 seam → `enforce_session` fail-closed VIEWER)
- `src/rider_server/admin/actions_routes.py` (role 게이트 `require_operator`/`require_secret_admin`·actor/source principal 도출·token revoke/rotate 라우트)
- `src/rider_server/main.py` (`resolve_admin_principal`/IP allowlist/MFA/non-sending/`agent_token_service` seam 와이어링 + `_default_agent_token_repository`)
- `src/rider_server/settings.py` (+`sending_enabled`/`admin_ip_allowlist`/`admin_mfa_required` + `_env_bool`/`_env_tuple`)

**신규(테스트):**
- `tests/server/test_admin_security.py`
- `tests/server/test_audit_log_schema.py`
- `tests/server/test_agent_token_revoke.py`
- `tests/server/test_recovery_non_sending.py`
- `tests/negative/test_security_pg.py`

**수정(테스트):**
- `tests/server/test_db_schema.py` (head 0005·`REQUIRED_FIELDS[audit_logs]`+3)
- `tests/server/test_domain_states.py` (+`AuditResult` count-lock 3)
- `tests/server/test_domain_models.py` (`__all__` 정본에 `AuditResult` 추가)
- `tests/server/test_admin_dashboard.py` (VIEWER principal 주입)
- `tests/server/test_admin_actions.py` (OPERATOR principal 주입·401/403/MFA 거부 테스트)

**메타:**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (5-8 in-progress→review)

### Change Log

| Date       | Version | Description | Author |
| ---------- | ------- | ----------- | ------ |
| 2026-06-14 | 0.1     | Story 5.8 created — Audit log 완성(`source`/`reason`/`result` additive 컬럼 + 0005 마이그레이션, readiness gate 7필드)·Admin 접근 제어(MFA·4역할 `AdminRole`·IP allowlist, fail-closed seam 교체)·token revoke/rotate(Agent `token_revoked_at` server-side revoke + 외부 service token ref rotation)·복구 non-sending + backup/restore runbook. 신규 테이블 0(14표 lock 유지), head 0004→0005 전진, 기존 enum 수정 0·신규 enum 자체 count-lock, 신규 deps 0. 5.6/5.7 seam(`require_admin_session`/`resolve_admin_actor`/`resolve_agent_id`) 강제기로 교체. | Bob (create-story) |
| 2026-06-14 | 1.0     | Story 5.8 구현 완료 — Task 1~6 전부. audit 7필드 + 0005(head 전진·round-trip)·`security/`(AdminPrincipal·4역할·MFA·IP allowlist·fail-closed `require_role`·audit-on-deny)·`AgentTokenService` revoke/rotate(revoke→401 resolver)·외부 token ref 회전(평문 fail-closed)·복구 non-sending(`effective_send_enabled`)·backup/restore runbook. 5.6/5.7 seam 교체(테스트 principal 주입으로 해소). 전체 1815 passed / 41 skipped(PG-gated). 14표·head 0005·9-dep·enum count-lock·단방향 import·async-boundary 무회귀. | Amelia (dev-story) |
| 2026-06-14 | 1.1     | Adversarial code review(story-automator-review) — 3 AC 전부 구현 검증, 가드(14표·head 0005·enum count-lock·read-only AST·forbidden-column·native-enum-0·no-FK·single-head) 전부 green·non-vacuous, File List ↔ git 일치. 수정: dev-exit 테스트 카운트 stale(1804→1815 정정, [[stale-test-count-a2]]). 0 CRITICAL/HIGH → Status done. 3 LOW follow-up(resolve_agent_id 배포 배선·non-sending 소비처·XFF 신뢰) 비차단 기록. | Review (story-automator) |

## Senior Developer Review (AI)

**Reviewer:** lsy9344 · **Date:** 2026-06-14 · **Outcome:** ✅ Approve (0 CRITICAL / 0 HIGH; 3 LOW follow-ups, non-blocking)

### Scope & method
스토리 File List 의 신규 8 + 수정 10 제품 파일과 신규 5 + 수정 5 테스트를 git 변경(`git status`/`diff`)과 교차 검증하고, 3 AC·Task [x] 주장·가드레일 10종을 코드 근거로 대조. 전체 스위트 재측정: **1815 passed / 41 skipped(PG-gated)**.

### AC 검증
- **AC1 (완전한 audit 스키마):** `audit_logs` 에 `source`/`reason`/`result` additive 3컬럼(0005, `result` server_default→제거 패턴)으로 readiness gate 7필드 충족. `AuditEntry`/`_audit`/PG `_audit_values` 가 3필드 채워 INSERT, 5.7 위험 액션 전부 retrofit, `record_denied`(DENIED)/`record_break_glass` 추가. `source`/`reason` 은 `redact`, `diff_redacted` 는 `redact_mapping(mask_operational_ids=True)` 통과 — 평문 secret 0 확인. **IMPLEMENTED.**
- **AC2 (MFA·4역할·IP allowlist):** `AdminRole`(4, count-lock·단조 rank), `AdminPrincipal`, `resolve_admin_principal`(기본 fail-closed None→401), `require_role`(IP→MFA→rank 순 fail-closed + 인증 주체 거부만 DENIED audit = anti-flooding), `enforce_session`(VIEWER, write-free). 5.6/5.7 seam 을 principal 위에서 교체. 게이트 매핑 GET=VIEWER↑/POST=OPERATOR↑/token=SECRET_ADMIN↑/break-glass 강제 audit. **IMPLEMENTED.**
- **AC3 (token revoke/rotate·복구성):** `AgentTokenService.revoke/rotate`(`agents.token_revoked_at`/`token_rotated_at` additive, 동일 tx + SECRET_ADMIN audit), `revocation_aware_resolver`(revoked→None→401, 순수 helper 검증), `rotate_external_token`(`*_ref` 만, 평문 fail-closed `looks_like_plaintext_secret`), 복구 non-sending(`Settings.sending_enabled` 기본 OFF + `effective_send_enabled` AND-compose), `docs/runbooks/backup-restore.md`. **IMPLEMENTED**(소비처 배선은 LOW-2 참조).

### 가드레일 무회귀(전부 green·non-vacuous 확인)
14표 lock(`test_metadata_has_exactly_14_contract_tables`) · head=0005 + 선형 체인(`test_single_migration_head_with_initial_base`) · `REQUIRED_FIELDS[audit_logs]` 7필드 · forbidden-column exact-match(`token_revoked_at`/`token_rotated_at` 안전) · native PG ENUM 0 · no-FK(`audit_logs`/`agents`) · 신규 enum 자체 count-lock(`AdminRole`4/`AuditResult`3) + 기존 enum 수정 0 · 도메인 `__all__` lock(`AuditResult` 추가) · 5.6 read-only AST 가드(audit-on-deny 는 service 경유라 routes.py write 0, 화이트리스트 non-vacuous) · `security/` 단방향 import(`rider_agent` 0) · 9-dep lock(stdlib `ipaddress` 만).

### Findings
- **[MEDIUM·FIXED] Stale test count** — Completion Notes/Change Log 가 1804 로 기재, 실측 1815. dev-exit 후 qa-e2e 케이스 추가로 어긋남([[stale-test-count-a2]]). 본 리뷰에서 1815 로 정정.
- **[LOW·follow-up] AC3 `resolve_agent_id` revoke 합성이 seam-only** — `create_app` 이 5.3 stub 을 그대로 둬 production 기본엔 `revocation_aware_resolver` 합성 없음(코드베이스 전반 seam 패턴과 일관, 순수 helper+라우트 테스트로 검증). 실 token→agent 매핑 도입 시 배선 필요. PG `is_revoked` async vs sync seam 미스매치 주의.
- **[LOW·follow-up] 복구 non-sending 소비처 없음** — `effective_send_enabled`/`app.state.sending_enabled` 가 server-side dispatch 부재로 미소비(agent-side `AppConfig.send_enabled` 가 실 게이트). 스토리 범위(플래그+helper+runbook)는 충족, 배선 스토리가 연결.
- **[LOW·hardening] `source_ip` XFF 무조건 신뢰** — IP allowlist 가 신뢰 reverse-proxy 전제(docstring 명시). 직접 노출 시 spoofing 우회 가능 — 배포 경계로 보장.

상세 후속 항목은 위 **Review Follow-ups (AI)** 참조(전부 비차단).
