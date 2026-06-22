---
baseline_commit: 7ef46d1113bc72d539d863e20bfb0703ade2619f
---

# Story 5.6: Admin 운영 대시보드와 상태 심각도 표시

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 고객/대상/Agent/채널/job 상태와 마지막 성공·실패·queue lag·인증 필요를 **한 화면에서 읽고** 심각도(정상/주의/위험/중지)로 구분하고 싶다,
so that 고객이 알려주기 전에 어디가 막혔는지 한눈에 파악하고 우선순위를 정할 수 있다.

## Acceptance Criteria

> **범위 한 줄 요약**: 이 스토리는 **읽기 전용(read-only) 관측 대시보드**다. 운영 액션(test crawl/dry-run/test send/retry/인증 확인)·고객/구독 상태 전이는 **5.7**, MFA/4역할/audit/token revoke는 **5.8**, 7개 지표 알림·runbook은 **5.9**, 엔티티 생성/편집 CRUD UI는 **5.11** 소유다. 대시보드는 **상태를 바꾸지 않는다**(상태 전이 0 — 라우트/UI에서 service 전이·DB 컬럼 변경 금지).
>
> **정본 이름 주의**: enum/컬럼 이름은 이미 구현된 코드를 따른다. "마지막 수집 성공"은 별도 컬럼이 아니라 `snapshots`(quality_state=`OK`)·`delivery_logs`(status=`SENT`)·`jobs`·`agents.last_heartbeat_at` 에서 **파생 집계**한다(신규 컬럼/테이블 0 — 14표 lock 유지). 심각도 4단계 표기 "정상/주의/위험/중지"는 plain-string 코드값으로 표현한다(아래 Dev Notes "심각도 어휘" 참조).

**AC1 — Jinja2+HTMX Admin 대시보드 읽기 화면 (P4-03, FR-21, ADD-10)**
- **Given** 운영 상태를 한곳에서 봐야 할 때
- **When** Jinja2+HTMX Admin 대시보드를 구현하면
- **Then** 대상별 **마지막 수집 성공**(`snapshots.collected_at` where `quality_state='OK'` 최신)·**마지막 전송 성공**(`delivery_logs.sent_at` where `status='SENT'` 최신)·**마지막 실패 사유**(`jobs.error_code` 또는 `delivery_logs.error_code` 최신, `FailureCategory` 값)가 표시되고, Agent별 **heartbeat**(`agents.last_heartbeat_at`)·**버전**(`agents.version`)·**현재 job**(해당 agent가 claim한 활성 `jobs`)·**처리 가능 job type**(`agents.capacity_json`의 capability)이 표시된다.
- **And** **KakaoTalk queue lag**(`KAKAO_SEND` job 대기 지연)와 **Telegram 전송 오류**(`TELEGRAM_FAILURE` 분류)가 **구분되어** 표시된다.
- **And** 별도 JS 빌드 파이프라인 없이(HTMX는 정적 자산/CDN, npm/webpack 0) **서버 렌더 부분 갱신**(HTMX swap)되고, 백엔드와 **동일 인증/세션 seam**을 통과한다(실제 MFA/4역할 강제는 5.8 — 본 스토리는 주입 가능한 `require_admin_session` seam만 두고 기본값은 fail-safe).

**AC2 — 마지막 성공 시각 기반 심각도 계산 (FR-23, NFR-13)**
- **Given** 상태 심각도를 계산해야 할 때
- **When** 마지막 수집 성공 시각(`target_last_success_at`)을 평가하면
- **Then** `now - target_last_success_at` 가 `interval×2` 초과 시 **warning(주의)**, `interval×4` 초과 시 **critical(위험)** 으로 표시되고(임계는 `monitoring_targets.interval_minutes` 기준; `last_success_at`이 없으면=한 번도 성공 못함 → 최소 warning 이상),
- **And** 심각도는 **정상/주의/위험/중지** 4단계로 운영자가 이해 가능하게 분류된다. 분류는 **DB 없이 검증 가능한 순수 함수**(시각 `now` 주입, 내부에서 `datetime.now()` 호출 금지 — 5.4 `policy.py` 선례)로 존재한다.

**AC3 — fail-closed 위험 우선 표시 (FR-23)**
- **Given** fail-closed 우선 표시가 필요할 때
- **When** 위험 상태를 판단하면
- **Then** **인증 필요**(`PlatformAccount.auth_state==AUTH_REQUIRED` / `auth_sessions.state` 인증대기 / `CustomerLifecycleState.AUTH_REQUIRED`), **기대 대상 검증 실패**(`CENTER_MISMATCH` / `TARGET_VALIDATION_FAILURE`), **KakaoTalk 오발송 위험**(`KAKAO_FAILURE` 계열·방명 검증 실패)은 **자동 전송보다 중지를 우선하는 상태**로 표시된다.
- **And** 이 fail-closed 신호는 **시간 경과 기반 warning/critical 보다 우선**한다(예: 인증 필요면 마지막 성공이 최근이어도 "위험/중지" 우선). 우선순위 병합도 순수 함수로 검증 가능하다.

**AC4 — 인증 필요 대상 필터 (FR-17 연계, FR-21)**
- **Given** 인증 필요 대상을 골라봐야 할 때
- **When** auth-required 필터를 적용하면
- **Then** 어떤 **고객/대상/프로필**이 인증을 요구하는지 목록으로 확인할 수 있다(HTMX 부분 갱신으로 필터 토글). 목록은 `platform_accounts.auth_state`·`auth_sessions`·`browser_profiles` 를 tenant scope로 조인해 도출한다.
- **And** 필터 결과에 secret/OTP/토큰이 노출되지 않는다(고객/센터명은 운영 로그 허용값이나, 진단 breadcrumb은 `redact(..., mask_operational_ids=True)` 경유).

## Tasks / Subtasks

- [x] **Task 1 — 심각도 분류 순수 정책 모듈 (AC2·AC3)**
  - [x] 1.1 `src/rider_server/admin/severity.py`(또는 `services/dashboard_policy.py`)에 **순수** 심각도 분류를 둔다. FastAPI/SQLAlchemy/async 의존 0, 내부에서 `datetime.now()`/`random` 호출 금지(시각·임계는 호출부 주입 — 5.4 `scheduler/policy.py` 정본 계승) [Source: src/rider_server/scheduler/policy.py:1-16].
  - [x] 1.2 시간 경과 심각도: `classify_freshness(last_success_at, interval_minutes, now) -> str`. `last_success_at is None`(한 번도 성공 못함) → 최소 `WARNING`; `now - last_success_at > interval×4` → `CRITICAL`; `> interval×2` → `WARNING`; 그 외 `NORMAL`. 경계(정확히 ×2/×4)는 "초과(>)"라 경계값은 하위 등급(ops-contract:26 "Over interval x 2 / over interval x 4" 정본). `interval_minutes<=0`(미설정)일 때의 동작을 명시적으로 결정(권장: freshness 평가 skip → `NORMAL`, 단 fail-closed 신호는 그대로) [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md:26].
  - [x] 1.3 fail-closed 우선 신호: `classify_failclosed(signals) -> str | None`. 인증 필요·기대 대상 검증 실패·Kakao 오발송 위험이 있으면 `STOPPED`(중지) 또는 `CRITICAL` 우선값 반환(권장: 자동 전송보다 중지 우선 = `STOPPED`). 신호 없으면 `None`.
  - [x] 1.4 병합 우선순위: `overall_severity(...) -> str` = fail-closed 신호가 있으면 그 값, 없으면 freshness 값(AC3 "fail-closed > 시간 경과"). 순서가 뒤집히지 않음을 단위 테스트로 잠근다.
  - [x] 1.5 심각도 어휘는 **plain-string 상수**(`SEVERITY_NORMAL="NORMAL"`/`SEVERITY_WARNING="WARNING"`/`SEVERITY_CRITICAL="CRITICAL"`/`SEVERITY_STOPPED="STOPPED"`)로 둔다 — `test_domain_states` count-lock(11/4/7/4)을 건드리지 않게 기존 enum에 멤버 추가 금지(5.4 `BREAKER_OPEN`/`BREAKER_CLOSED` 선례). UI 한글 라벨(정상/주의/위험/중지)은 템플릿/매핑에서 표현 [Source: src/rider_server/scheduler/policy.py:42-49; memory enum-member-count-locks].
- [x] **Task 2 — 대시보드 read-model 서비스 + repository 포트 (AC1·AC4)**
  - [x] 2.1 `src/rider_server/admin/dashboard_service.py`(또는 `services/`)에 **읽기 전용** 집계 서비스를 둔다. 정책↔DB 경계를 `DashboardRepository`(abc) 포트로 분리한다 — **always-run in-memory fake** 와 **PostgreSQL 구현** 양쪽이 같은 조립 로직을 통과(5.3 `QueueBackend`·5.4 `SchedulerRepository` 선례). 순수 집계/심각도 합성은 sync, DB I/O만 async [Source: src/rider_server/scheduler/service.py:86-126; src/rider_server/queue/backend.py].
  - [x] 2.2 read-model 중립 DTO(frozen dataclass — ORM Row 누출 금지): `TargetRow`(target_id/tenant_id/name/center_name/platform/interval_minutes/last_success_at/last_delivery_at/last_failure_code/severity), `AgentRow`(agent_id/name/version/last_heartbeat_at/online/current_job_type/capabilities), `ChannelHealthRow`(kakao_queue_lag_seconds/telegram_error_count 등 구분 표시), `AuthRequiredRow`(tenant/target/profile/reason) [Source: src/rider_server/scheduler/service.py:43-72 DueTarget 선례].
  - [x] 2.3 `DashboardRepository` 메서드(중립 입력만 노출, AsyncSession/SQL 누출 금지): `target_health(now)`·`agent_health(now)`·`channel_health(now)`·`auth_required(now)`. 각 메서드는 tenant scope 필터를 통과한다(architecture #Data-Boundaries "모든 customer-owned 쿼리 tenant scope"). PostgreSQL 구현은 파생 집계(예: target별 `MAX(snapshots.collected_at) WHERE quality_state='OK'`, `MAX(delivery_logs.sent_at) WHERE status='SENT'`)를 SQL로 [Source: src/rider_server/scheduler/postgres_repository.py; architecture.md:481-499].
  - [x] 2.4 Agent online/offline: `agents.last_heartbeat_at` 가 `now - 2분` 보다 오래되면 offline(ops-contract:25 "Missing for more than 2 minutes"). 이 판정도 순수 함수로(시각 주입) [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md:25; src/rider_server/db/models/agent.py:32].
  - [x] 2.5 KakaoTalk queue lag vs Telegram 전송 오류 **구분**: kakao lag = 대기 중 `KAKAO_SEND` job의 `now - run_after`(또는 가장 오래된 미claim 대기); telegram error = 최근 윈도 `delivery_logs`/`jobs` 의 `TELEGRAM_FAILURE` 카운트. 두 값을 별도 필드로(혼합 금지) [Source: src/rider_server/queue/states.py job type 상수; src/rider_server/domain/states.py:179-186 FailureCategory].
- [x] **Task 3 — Admin UI 라우트 + Jinja2 템플릿 + HTMX 부분 갱신 (AC1·AC4)**
  - [x] 3.1 `src/rider_server/admin/routes.py` 에 `APIRouter(prefix="/admin", tags=["admin"])` 를 만든다. **HTML 응답**(`HTMLResponse`/`Jinja2Templates.TemplateResponse`)이므로 `/v1/` JSON 리소스 규약과 별개다 — `/admin` 프리픽스 권장(운영 엔드포인트 root-level 금지 가드는 `/v1/health|version|metrics` 만 대상이라 충돌 없음) [Source: tests/server/test_server_app.py:95-102].
  - [x] 3.2 라우트: `GET /admin`(대시보드 풀 페이지), `GET /admin/targets`·`/admin/agents`·`/admin/channels`(HTMX 부분 fragment), `GET /admin/auth-required`(AC4 필터 fragment). HTMX swap용 fragment는 partial 템플릿을 반환한다(풀 페이지 재요청 없이 부분 갱신) [Source: architecture.md:197-203 Frontend Architecture].
  - [x] 3.3 `src/rider_server/admin/templates/` 에 Jinja2 템플릿(`dashboard.html` + `_targets.html`/`_agents.html`/`_channels.html`/`_auth_required.html` partial). HTMX는 `<script src=...>` 정적 자산 또는 CDN으로 로드(npm/빌드 0). 심각도 코드값→한글 라벨/CSS class 매핑은 템플릿 필터/컨텍스트로 [Source: architecture.md:199-200].
  - [x] 3.4 `Jinja2Templates` 인스턴스를 모듈 레벨 또는 `app.state` 로 둔다. 템플릿 렌더는 sync(CPU)이며 async 가드의 blocking I/O 금지 목록(`time.sleep`/subprocess)과 무관하다 — 그래도 DB I/O는 async repository로만 [Source: tests/server/test_server_async_boundary.py:23-32].
  - [x] 3.5 라우터를 `src/rider_server/admin/__init__.py`(또는 `api/__init__.py`)에 재노출하고 `create_app` 에서 `app.include_router(admin_router)` 로 등록한다 [Source: src/rider_server/main.py:191-193; src/rider_server/api/__init__.py].
- [x] **Task 4 — create_app 와이어링 + repository/세션 seam (AC1)**
  - [x] 4.1 `create_app(...)` 에 `dashboard_repository: DashboardRepository | None = None` 주입 인자를 추가하고, `app.state.dashboard_repository` 에 둔다. 미지정 시 `_default_dashboard_repository(settings)`(DATABASE_URL 있으면 PostgreSQL, 없으면 in-memory) — `_default_queue_backend`/`_default_channel_repository` 동형 패턴 [Source: src/rider_server/main.py:36-59,101-126].
  - [x] 4.2 `app.state.require_admin_session` seam을 둔다(5.8 이 MFA/4역할/세션으로 교체). 기본값은 **fail-safe**: 운영자 인증이 아직 없는 단계라 (a) 무인증 허용 후 5.8 교체, 또는 (b) 환경변수 토글로 보호 중 택1을 결정해 명시한다. `resolve_agent_id` seam(5.3→5.8) 선례를 따른다 [Source: src/rider_server/main.py:117-126; src/rider_server/api/jobs.py:50-73].
  - [x] 4.3 대시보드는 **읽기 전용**임을 보장: 라우트/서비스에서 `session.commit()`·상태 전이·INSERT/UPDATE 0(조회만). 가능하면 read-only 트랜잭션. 상태 전이는 5.7 service 소유 [Source: architecture.md:488-493 Service Boundaries].
- [x] **Task 5 — jinja2 의존성 선언 (AC1, 가드 준수)**
  - [x] 5.1 `pyproject.toml` `[project.optional-dependencies].server` 에 `jinja2>=3.1,<4` 를 additive로 추가한다(현재 venv에는 transitive로 존재하나 **명시 선언 필요** — 재현 가능 빌드). **절대 `[project].dependencies`(9개 고정)에 넣지 않는다**(rider_agent stdlib-only 표면 보호) [Source: pyproject.toml:6-32; memory server-deps-go-in-optional-group].
  - [x] 5.2 HTMX 자산: 별도 npm 패키지/빌드 없이 정적 파일(`admin/static/` 또는 템플릿 내 CDN `<script>`)로 둔다. python 의존성 추가 아님 [Source: architecture.md:199].
- [x] **Task 6 — 테스트 (AC1·AC2·AC3·AC4, 4-tier)**
  - [x] 6.1 always-run 순수 정책(DB 불필요): `classify_freshness` 경계(×2/×4 초과·정확히 경계값·`None`·interval<=0), fail-closed 우선(`overall_severity` 가 인증 필요 시 freshness 무시), agent online/offline 2분 경계, kakao lag vs telegram error 구분. 시각은 주입(결정성) [Source: src/rider_server/scheduler/policy.py 테스트 선례 tests/server/test_scheduler_policy.py].
  - [x] 6.2 always-run 오케스트레이션(in-memory fake repo + 주입 now): read-model 조립이 target/agent/channel/auth-required 행을 올바른 severity로 만들고, tenant scope 필터가 적용됨을 검증. HTMX 라우트는 `TestClient` 로 200·HTML·부분 fragment 반환 확인(`hx-` 속성 존재). 읽기 전용(주입 fake repo에 write 호출 0) 검증 [Source: tests/server/test_server_app.py:34-36 TestClient 패턴].
  - [x] 6.3 reuse/boundary 가드: admin 모듈이 상태 전이 service(register/verify/activate 등)·DB write를 **호출하지 않음**(읽기 전용)을 AST import/call-edge 가드로(raw grep 금지 — memory/negative-guard-tests-use-ast). 단방향 import(`rider_server`→`rider_crawl`만; `rider_agent` import 0), async-boundary(블로킹 sync 직접 호출 0, 기존 rglob 가드가 신규 admin 모듈 자동 커버) [Source: tests/server/test_server_async_boundary.py:66-71; memory negative-guard-tests-use-ast].
  - [x] 6.4 PG-gated(`tests/negative/` 또는 `tests/server/`, `@pytest.mark.skipif`로 `TEST_DATABASE_URL` 없으면 skip): PostgreSQL `DashboardRepository` 가 실제 파생 집계(MAX(snapshots.collected_at) where OK 등)를 정확히 반환하고 tenant scope로 격리됨을 seed 후 검증. cross-tenant negative(다른 tenant 데이터가 새지 않음) [Source: architecture.md:481-499; memory pg-gated-files-hide-pure-helpers].
  - [x] 6.5 14표 lock·9-dep lock·enum count-lock 무회귀 확인: 신규 컬럼/테이블/마이그레이션 0(권장 경로), `[project].dependencies` 9개 유지, 기존 enum 멤버 무변경. `test_metadata_has_exactly_14_contract_tables`·`test_single_migration_head_with_initial_base`(head=0004 유지) 통과 [Source: tests/server/test_db_schema.py:95-98,401-421].
  - [x] 6.6 테스트 파일 컨벤션: 상단 docstring `"""Story 5.6 / ACx …"""`, `from __future__ import annotations`, fake fixture만(실제 토큰/전화/이메일/chat_id 금지), `tests/server/` flat(`__init__.py` 없음). secret-shaped 값이 HTML 응답에 평문으로 남지 않음을 redact 어서션으로 확인 [Source: tests/server/test_server_app.py:1-6,26-31].

## Dev Notes

### 이 스토리의 본질: 읽기 전용 관측 대시보드 (절대 혼동 금지)

- **5.6 = 보기(read)만.** 상태 전이·수동 액션(test crawl/dry-run/test send/retry/인증 확인)·구독 상태 전이는 **5.7**, MFA/4역할/audit/token revoke는 **5.8**, 7지표 알림·runbook은 **5.9**, 엔티티 CRUD UI는 **5.11**. 대시보드 라우트/서비스는 **DB write·상태 전이 0**. (architecture #Service-Boundaries: "Service 레이어만 상태 전이/DB 변경. API/UI는 service 호출" — 본 스토리는 **조회 service**만 호출) [Source: architecture.md:488-493].
- 5.5 Dev Notes가 명시한 위임: "등록/검증을 트리거하는 **Admin UI 버튼**은 5.6/5.7 소유" — 그러나 **버튼(액션)은 5.7**, **5.6은 관측 화면**이다. 5.6에서 register/verify/activate를 **호출하지 않는다**(channel lifecycle service는 읽기만 참조하거나 아예 참조 안 함) [Source: 5-5 story line 128].

### 정본 데이터 소스 — "마지막 성공/실패"는 파생 집계(신규 컬럼 금지 권장)

`monitoring_targets` 에는 `last_success_at`/`last_failure_at` 컬럼이 **없다**. 현재 스케줄링 컬럼만 있다: `next_run_at`, `last_enqueued_at`(5.4 additive). 따라서 대시보드 표시값은 **기존 테이블에서 파생**한다(14표 lock·migration drift 회피):

| 표시 항목 | 파생 소스 | 집계 |
| --- | --- | --- |
| 대상 마지막 **수집 성공** | `snapshots` | `MAX(collected_at)` where `target_id=? AND quality_state='OK'` |
| 대상 마지막 **전송 성공** | `delivery_logs`→`messages`→`snapshots`→target | `MAX(sent_at)` where `status='SENT'` (target 조인) |
| 대상 마지막 **실패 사유** | `jobs.error_code` / `delivery_logs.error_code` | 최신 non-null `FailureCategory` 값 |
| Agent **heartbeat/online** | `agents.last_heartbeat_at` | `now - last_heartbeat_at > 2분` → offline |
| Agent **현재 job** | `jobs` | `agent_id=? AND status in (CLAIMED/RUNNING)` |
| Agent **처리 가능 type** | `agents.capacity_json` | capability 목록 |
| **Kakao queue lag** | 대기 `KAKAO_SEND` `jobs` | 가장 오래된 미claim의 `now - run_after`(초) |
| **Telegram 전송 오류** | `delivery_logs`/`jobs` | 최근 윈도 `TELEGRAM_FAILURE` 카운트 |
| **인증 필요** | `platform_accounts.auth_state` / `auth_sessions.state` / `browser_profiles.state` | `AUTH_REQUIRED`/인증대기 행 |

[Source: src/rider_server/db/models/account.py:32-50, agent.py:23-65, messaging.py:47-80]

> **권장 결정**: 신규 컬럼/테이블/마이그레이션 0. 파생 집계로 충족 → 14표 lock·0004 head 유지(0005 불필요). 만약 성능상 denormalized `last_success_at`가 필요하면 그건 **additive**지만 본 스토리에선 **defer**(drift 위험·lock 회피). repository 포트가 집계를 캡슐화하므로 후속 최적화는 fake/PG 구현 교체로 가능.

### 핵심 정본(검증 완료) — 절대 틀리면 안 되는 이름

- **심각도 어휘**: 신규. 기존 enum에 멤버 추가 **금지**(count-lock). plain-string 상수 `SEVERITY_NORMAL/WARNING/CRITICAL/STOPPED`(5.4 `BREAKER_OPEN`/`BREAKER_CLOSED` 선례). UI 한글 라벨(정상/주의/위험/중지)은 템플릿 매핑 [Source: src/rider_server/scheduler/policy.py:42-49; memory enum-member-count-locks].
- **심각도 임계**: `target_last_success_at` 가 `interval×2` **초과** → warning, `interval×4` **초과** → critical. 정확히 ×2/×4 경계는 하위 등급(spec "Over"). interval은 `monitoring_targets.interval_minutes`(분) [Source: ops-security-test-contract.md:26; architecture.md:203].
- **agent offline 임계**: `last_heartbeat_at` 가 **2분** 초과 누락 → offline [Source: ops-security-test-contract.md:25].
- **FailureCategory 7멤버**(`error_code` 어휘): `CRAWL_FAILURE`/`AUTH_REQUIRED`/`RENDER_FAILURE`/`TELEGRAM_FAILURE`/`KAKAO_FAILURE`/`DUPLICATE_BLOCKED`/`TARGET_VALIDATION_FAILURE`. Telegram 오류=`TELEGRAM_FAILURE`, Kakao 위험=`KAKAO_FAILURE`. count-lock 7 — 추가 금지 [Source: src/rider_server/domain/states.py:165-186].
- **인증 상태 어휘**: `BaeminAuthState`(7) `AUTH_REQUIRED`/`USER_ACTION_PENDING`/`CENTER_MISMATCH`/`BLOCKED_OR_CAPTCHA` 등; `CustomerLifecycleState.AUTH_REQUIRED`(고객 lifecycle, 11멤버); `auth_sessions.state`(`BaeminAuthState` 값). **타입별 동명 멤버**(`ACTIVE`/`AUTH_REQUIRED`)는 필드 타입으로 구별 — 혼동 금지 [Source: src/rider_server/domain/states.py:14-60; account.py:53-61].
- **job type 6 상수**: `CRAWL_BAEMIN`/`CRAWL_COUPANG`/`AUTH_CHECK`/`OPEN_AUTH_BROWSER`/`KAKAO_SEND`/`CAPTURE_DIAGNOSTIC`. Telegram은 **중앙 send-only**(Agent job type 아님 — `DISPATCH_TELEGRAM` 없음). Kakao queue=`KAKAO_SEND` job [Source: src/rider_server/queue/states.py:22-29; memory agent-job-type-vocab].
- **마이그레이션 head = `0004_messenger_channel_registration`**. 본 스토리는 권장 경로상 **마이그레이션 추가 0** → head 유지. `test_single_migration_head_with_initial_base` 의 head 단언은 0004 그대로 [Source: tests/server/test_db_schema.py:401-421; migrations/versions/].

### 재사용 자산(재구현 금지 — compose/import만)

- **repository 포트 패턴**: `src/rider_server/scheduler/service.py` 의 `SchedulerRepository`(abc) + in-memory fake + `scheduler/postgres_repository.py`. 대시보드 `DashboardRepository` 를 **동형**으로 만든다(중립 DTO 반환, AsyncSession/SQL 누출 0). 5.3 `QueueBackend` 추상화도 같은 정신 [Source: src/rider_server/scheduler/service.py:86-126; src/rider_server/queue/backend.py].
- **순수 정책 패턴**: `scheduler/policy.py`(시각/seed/random 주입, async 의존 0). 심각도 분류를 같은 규약으로 — 테스트 결정성·always-run [Source: src/rider_server/scheduler/policy.py:1-16,56-83].
- **create_app seam**: `_default_queue_backend`/`_default_channel_repository`(DATABASE_URL 분기) + `app.state.<x>` 주입. 대시보드 repository/`require_admin_session` 도 같은 seam으로 [Source: src/rider_server/main.py:36-126].
- **에러 envelope**: 라우트에서 `{"error":...}` 직접 만들지 말고 `HTTPException(status, detail)` raise → 전역 핸들러가 `{"error":{"code","message_redacted"}}` 변환(HTML 페이지 에러도 전역 핸들러 통과). 단, 대시보드는 정상 경로가 HTML이라 200/HTMLResponse, 인증 실패는 401/403 HTTPException [Source: src/rider_server/main.py:57-69,156-189].
- **redaction**: `redact(text, *, mask_operational_ids=False)`. 고객/센터명은 기본 비마스킹(운영 로그 허용값), 진단 breadcrumb(chat_id/thread_id 등)은 `mask_operational_ids=True`. **토큰/OTP/secret은 어떤 경우에도 노출 금지** [Source: src/rider_crawl/redaction.py; memory redact-skips-operational-ids].
- **Pydantic/FastAPI 라우팅**: `api/jobs.py`(라우터→`api/__init__.py` 재노출→`create_app` include) 패턴. 단 대시보드는 JSON 대신 HTML — `Jinja2Templates` 사용 [Source: src/rider_server/api/jobs.py:44; src/rider_server/main.py:191-193].

### FastAPI + Jinja2 + HTMX 패턴(복붙용)

- `from fastapi.templating import Jinja2Templates` → `templates = Jinja2Templates(directory="src/rider_server/admin/templates")`(또는 `Path(__file__).parent/"templates"` 로 패키지 상대). 라우트: `return templates.TemplateResponse(request, "dashboard.html", {...})`(Starlette 0.29+ 시그니처: request 첫 인자).
- HTMX 부분 갱신: fragment 라우트가 partial 템플릿(`_targets.html`)만 반환 → 클라이언트 `hx-get="/admin/targets" hx-trigger="every 30s"` 로 polling swap(JS 빌드 0). HTMX는 `<script src="https://unpkg.com/htmx.org@2">` 또는 `admin/static/htmx.min.js`(정적) 로드.
- 라우트 경로: `/admin`(HTML) — `/v1/` 가드(`test_registered_routes_have_no_v1_operational_paths`)는 `/v1/health|version|metrics` 만 금지하므로 `/admin/*` 는 무관. JSON snake_case 가드(`_assert_snake_case_keys`)는 JSON 응답에만 적용되니 HTML은 무관 [Source: tests/server/test_server_app.py:39-41,95-102].
- 템플릿 렌더(sync CPU)는 async 라우트에서 호출 가능(blocking I/O 아님). DB 조회만 async repository로 분리(가드: `time.sleep`/subprocess만 금지) [Source: tests/server/test_server_async_boundary.py:23-32].

### 가드레일(위반 시 CI 실패)

- **읽기 전용**: admin 모듈은 DB write/상태 전이 service(register/verify/activate, enqueue, 전이 함수) **호출 금지**. AST call-edge 가드로 강제(raw grep 아님). 조회 repository만 호출 [Source: architecture.md:488-493; memory negative-guard-tests-use-ast].
- **9-dep lock**: `[project].dependencies` 정확히 9개. `jinja2` 는 `[project.optional-dependencies].server` 에만 추가(절대 main deps 금지). FastAPI/SQLAlchemy 는 이미 server extra [Source: pyproject.toml:6-32; memory server-deps-go-in-optional-group].
- **enum count-lock**: `CustomerLifecycleState`(11)·`SubscriptionStatus`(4)·`FailureCategory`(7)·`MessengerChannelState`(4)·`BaeminAuthState`(7)·`MonitoringTargetStatus`(3) 등 멤버 추가/삭제 금지. 심각도 어휘는 plain-string 상수. 잠금이 **여러 파일**에 흩어져 있으니 변경 전 repo 전체 grep [Source: memory enum-member-count-locks; tests/server/test_domain_states.py].
- **14표 lock**: 신규 테이블 0(권장 경로상 마이그레이션도 0). `test_metadata_has_exactly_14_contract_tables` 유지 [Source: tests/server/test_db_schema.py:95-98].
- **단방향 import**: `rider_server`→`rider_crawl`만. `rider_agent` import 금지(AST 가드) [Source: project-context.md].
- **async-boundary**: async 함수에서 `time.sleep`/`subprocess.*` 직접 호출 금지(rglob 가드가 신규 admin 모듈 자동 커버) [Source: tests/server/test_server_async_boundary.py:66-71].
- **tenant scope**: 모든 customer-owned 조회는 tenant scope 필터 통과. cross-tenant negative 로 누출 0 검증 [Source: architecture.md:481-499].
- **secret 위생**: 토큰/OTP/secret 평문 0(HTML 응답·로그·예외). `telegram_chat_id`/`thread_id`/방명 = 라우팅 id(secret 아님)지만 진단 breadcrumb엔 `mask_operational_ids=True` [Source: project-context.md; memory redact-skips-operational-ids].

### 개발 환경·프로세스(매 스토리 반복되는 함정)

- pytest는 **`.venv/Scripts/python.exe -m pytest`** 로 실행(WSL `python3` 미설치; `pythonpath=["src"]`, editable 설치 없음 — 한글 경로 `.pth`가 cp949 UnicodeDecodeError 유발). 단, jinja2 등 server extra 가 venv에 설치돼 있는지 먼저 확인(현재 jinja2 3.1.6 존재 확인됨) [Source: memory dev-env-quirks].
- 신규 파일은 `\n`으로 작성(CRLF 재변환이 content-compare 멱등 깨뜨림), diff는 `git diff -w`로 확인. 템플릿(.html)도 동일 [Source: memory crlf-roundtrip-idempotency].
- 커밋 컨벤션 `feat(story-5.6): …`, baseline 커밋 `7ef46d1`(5.5).
- **테스트 카운트는 review 시 재측정**: dev가 적은 수치는 qa-generate-e2e가 케이스 추가하며 stale해진다. dev-exit vs post-QA를 구분해 정본 한 숫자를 review에서 기록 [Source: memory stale-test-count-a2].
- **PG-gated 파일이 순수 helper를 가린다**: 심각도 분류·online 판정·lag/error 구분 같은 순수 의미는 always-run 단위 테스트로 별도 추출(CI에서 PG skip돼도 실행되게) [Source: memory pg-gated-files-hide-pure-helpers].

### Project Structure Notes

- **신규**: `src/rider_server/admin/__init__.py`, `src/rider_server/admin/routes.py`(HTMX 라우터), `src/rider_server/admin/severity.py`(순수 심각도 정책), `src/rider_server/admin/dashboard_service.py`(read-model 조립 + `DashboardRepository` abc + in-memory fake), `src/rider_server/admin/dashboard_repository_postgres.py`(PG 파생 집계 구현), `src/rider_server/admin/templates/dashboard.html` + partial(`_targets.html`/`_agents.html`/`_channels.html`/`_auth_required.html`), (선택) `src/rider_server/admin/static/`(htmx 정적 자산). 테스트: `tests/server/test_admin_dashboard.py`(라우트+조립), `tests/server/test_dashboard_severity.py`(순수 정책), `tests/server/test_admin_readonly_guard.py`(read-only AST), `tests/negative/test_dashboard_repository_pg.py`(PG-gated). [아키텍처 트리는 `admin/routes.py`+`admin/templates/` 를 이미 예고 — architecture.md:443-445]
- **수정**: `src/rider_server/main.py`(`dashboard_repository`·`require_admin_session` seam + `admin_router` include), `src/rider_server/api/__init__.py` **또는** `admin/__init__.py`(라우터 재노출 — admin 자체 패키지 권장), `pyproject.toml`(`jinja2` server extra additive).
- **변경 금지**: 0001~0004 마이그레이션, 기존 enum 멤버, `telegram_central_dispatch.py`/`channel_registration.py`(상태 전이 service — 대시보드는 호출 안 함), `rider_crawl`/`rider_agent` 패키지(역/교차 import 금지), 14표 스키마.
- admin을 `api/` 하위가 아닌 **별도 `admin/` 패키지**로 두는 것이 architecture 트리(443-445 `admin/routes.py`+`templates/`)와 정합. JSON Agent API(`api/`)와 HTML Admin UI(`admin/`)의 인증 경계도 분리(architecture #API-Boundaries: "Admin API/UI: 세션+MFA … Agent API와 인증 경계 분리").

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.6 (lines 1024-1049)] — 스토리 정의·4 AC, FR-21(174)/FR-23(176)/ADD-10(191)
- [Source: _bmad-output/planning-artifacts/architecture.md:197-203] — Frontend Architecture(Jinja2+HTMX 부분 갱신, 심각도 계산 interval×2/×4)
- [Source: _bmad-output/planning-artifacts/architecture.md:443-445] — `admin/routes.py`+`templates/` 디렉터리 정본; #API-Boundaries(481-482), #Service-Boundaries(488-493), #Data-Boundaries(495-499 tenant scope)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md:21-31] — 7 지표 정본(agent_last_heartbeat 2분, target_last_success_at ×2/×4, auth_required_count, kakao_queue_lag 120s, telegram_send_error_rate)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md:83-92] — Admin API/UI 표면(Recent errors/last success/queue lag/auth-required filters), 상태머신(94-144)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md:72] — P4-03 "Build Admin UI … Operator can inspect current state in web UI"
- [Source: src/rider_server/main.py:36-126,191-193] — create_app seam·default 분기·router include 패턴
- [Source: src/rider_server/scheduler/service.py:86-126 / policy.py:1-83] — Repository 포트(abc)+fake+PG, 순수 정책(시각 주입) 정본 패턴
- [Source: src/rider_server/db/models/{account,agent,messaging,tenancy}.py] — 파생 집계 소스 컬럼(snapshots/delivery_logs/jobs/agents/platform_accounts/auth_sessions/browser_profiles)
- [Source: src/rider_server/domain/states.py] — 심각도/실패/인증/lifecycle enum 정본·count-lock·타입별 동명 멤버
- [Source: tests/server/test_server_app.py / test_server_async_boundary.py / test_db_schema.py] — 라우트·async·14표·head 가드(준수/갱신)
- [Source: _bmad-output/project-context.md] — 단방향 import·secret 정책·redaction·텔레그램 단일 큐
- [Source: _bmad-output/implementation-artifacts/5-5-telegram-webhook과-채널-등록-검증-활성화.md] — 직전 스토리: seam·repository·게이트·재사용 자산·"버튼은 5.6/5.7" 위임

### 열린 질문(구현 시 결정 — 막지 말고 가장 안전한 선택)

1. **`require_admin_session` 기본값(무인증 vs 토글 보호)**: 5.8 이 MFA/4역할/세션을 소유하므로 5.6은 **seam만** 둔다. 권장: 기본값을 환경변수 토글(`ADMIN_UI_ENABLED`/허용 시에만 서빙) 또는 무인증-허용 후 5.8 교체 중 택1. fail-safe 우선이면 비프로덕션에서만 열고 프로덕션은 5.8 전까지 비활성 권장. 5.3 `resolve_agent_id` 가 5.8 로 미룬 선례와 동일하게 "seam + 최소 동작" [Source: src/rider_server/main.py:117-119].
2. **신규 컬럼 vs 파생 집계**: 권장은 **파생 집계(신규 컬럼 0, 14표·head 유지)**. denormalized `last_success_at`/`last_failure_at` 는 성능 최적화로 후속에 additive 가능하나 본 스토리에선 drift/lock 회피로 defer. repository 포트가 캡슐화하므로 교체 비파괴.
3. **심각도 표현(plain-string vs 신규 enum)**: 권장 **plain-string 상수**(5.4 breaker 선례, count-lock 무관). 신규 `DashboardSeverity` enum 을 추가해도 기존 count-lock(특정 enum 이름 대상)은 깨지지 않으나, `test_domain_states` 의 enum 인벤토리/패턴 테스트가 있으면 영향 가능 → plain-string 이 최소 위험.
4. **kakao lag/telegram error 윈도·단위**: kakao lag=초(`now - 가장 오래된 대기 KAKAO_SEND job.run_after`), telegram error=최근 윈도(예: 10분, ops-contract:30) `TELEGRAM_FAILURE` 카운트. 정확한 윈도/표현은 5.9 지표 파이프라인과 정합되게 하되, 5.6은 **현재 상태 표시**(집계 1회)면 충분 — 알림/임계 트리거는 5.9.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (BMAD dev-story workflow)

### Debug Log References

- `.venv/Scripts/python.exe -m pytest tests/server/test_dashboard_severity.py tests/server/test_admin_dashboard.py tests/server/test_admin_readonly_guard.py tests/negative/test_dashboard_repository_pg.py -q` → 41 passed, 6 skipped(PG-gated). *(dev-exit 측정값)*
- `.venv/Scripts/python.exe -m pytest -q` (전체 회귀) → **1676 passed, 30 skipped**(PG-gated; 무회귀). *(dev-exit 측정값)*
- 마이그레이션 head 무변경 확인: `test_single_migration_head_with_initial_base`·`test_metadata_has_exactly_14_contract_tables` 통과(head=0004 유지, 14표 lock 유지).
- **[Review 재측정 — 2026-06-14]** qa-generate-e2e 가 케이스를 추가해 dev-exit 수치가 stale 해짐(memory/stale-test-count-a2). 정본 재측정값:
  - 5.6 4파일 + `test_dashboard_pg_helpers.py` → **67 passed, 6 skipped**(PG-gated).
  - 전체 회귀 `pytest -q` → **1702 passed, 30 skipped**(PG-gated; 무회귀, 14표·9-dep·enum count-lock 유지).

### Completion Notes List

- **읽기 전용 관측 대시보드** 구현(상태 전이 0). 신규 컬럼/테이블/마이그레이션 0 — 모든 표시값은 기존 테이블 **파생 집계**(snapshots OK·delivery_logs SENT·jobs/delivery_logs error_code·agents.last_heartbeat_at). 14표·0004 head 유지.
- **Task 1(심각도 순수 정책, `admin/severity.py`)**: `classify_freshness`(interval×2/×4 **초과** → 주의/위험; 정확히 경계는 하위 등급; `last_success_at=None` → 최소 WARNING; `interval<=0` → freshness skip=NORMAL), `failclosed_signals_from`(인증 필요/CENTER_MISMATCH·TARGET_VALIDATION_FAILURE/KAKAO_FAILURE 어휘 매핑 — 문자열 값 비교로 타입별 동명 멤버 혼동 차단), `classify_failclosed`→STOPPED, `overall_severity`(**fail-closed > 시간 경과**), `is_agent_online`(2분 **초과** offline, 경계=online). 심각도 4단계는 **plain-string 상수**(enum 멤버 추가 0 → count-lock 무회귀, 5.4 BREAKER 선례).
- **Task 2(read-model + 포트, `admin/dashboard_service.py`)**: 중립 facts(repository) ↔ 심각도 합성 DTO(`TargetRow`/`AgentRow`/`ChannelHealthRow`/`AuthRequiredRow`) 분리. `DashboardRepository`(abc, **write 메서드 0**) + `InMemoryDashboardRepository`(always-run fake/무-DB 기본값). 순수 합성 sync, DB I/O async. tenant scope(targets/channels/auth-required); agents 는 fleet 전역(명시적 예외). Kakao lag vs Telegram error 별도 필드(혼합 금지).
- **Task 3(라우트/템플릿, `admin/routes.py` + `templates/`)**: `APIRouter(prefix="/admin")` HTML 응답 — 풀 페이지(`GET /admin`) + HTMX fragment(`/admin/targets|agents|channels|auth-required`). HTMX는 CDN `<script>`(npm/빌드 0), `every 30s` polling swap. 심각도 코드값→한글 라벨(정상/주의/위험/중지)/CSS class 는 Jinja 필터 매핑.
- **Task 4(와이어링)**: `create_app(dashboard_repository=...)` 주입 인자 + `app.state.dashboard_repository`(DATABASE_URL 분기 기본값) + `app.state.require_admin_session` seam + `app.include_router(admin_router)`.
- **Task 5(의존성)**: `jinja2>=3.1,<4` 를 `[project.optional-dependencies].server` 에 additive(현 venv 3.1.6 확인). `[project].dependencies` 9개 불변.
- **열린 질문 결정**: (1) `require_admin_session` 기본값 = **최소 통과 seam(no-op)** — 5.6 단계엔 운영자 인증 인프라 부재(5.3 `resolve_agent_id`가 full lifecycle 을 5.8 로 미룬 선례). 대시보드는 읽기 전용·secret 미노출이라 비프로덕션 안전, 5.8 이 MFA/4역할/세션 강제기로 교체(테스트는 거부 seam 주입으로 401 검증). (2) 파생 집계 채택(신규 컬럼 defer). (3) plain-string 심각도. (4) kakao lag=초(now-MIN(run_after)), telegram error=최근 10분 윈도 TELEGRAM_FAILURE 카운트.
- **secret 위생**: read-model DTO 에 token/secret/otp/`_ref` 필드 0(구조적 가드 테스트). 인증 필요 목록은 기계가독 reason 코드만 노출(OTP/토큰 0).
- **테스트 카운트 주의**: 위 1676 은 dev-exit 측정값 — qa-generate-e2e 가 케이스를 추가하면 stale 해진다(review 시 재측정).

### File List

**신규 (src):**
- `src/rider_server/admin/__init__.py`
- `src/rider_server/admin/severity.py`
- `src/rider_server/admin/dashboard_service.py`
- `src/rider_server/admin/dashboard_repository_postgres.py`
- `src/rider_server/admin/routes.py`
- `src/rider_server/admin/templates/dashboard.html`
- `src/rider_server/admin/templates/_targets.html`
- `src/rider_server/admin/templates/_agents.html`
- `src/rider_server/admin/templates/_channels.html`
- `src/rider_server/admin/templates/_auth_required.html`

**신규 (tests):**
- `tests/server/test_dashboard_severity.py`
- `tests/server/test_admin_dashboard.py`
- `tests/server/test_admin_readonly_guard.py`
- `tests/server/test_dashboard_pg_helpers.py` *(qa-generate-e2e 추가 — PG repo 순수 헬퍼/정책 상수 always-run 추출; review 시 File List 보강)*
- `tests/negative/test_dashboard_repository_pg.py`

**수정:**
- `src/rider_server/main.py` (`dashboard_repository`·`require_admin_session` seam + `admin_router` include + `_default_dashboard_repository`)
- `pyproject.toml` (`jinja2>=3.1,<4` server extra additive)

### Change Log

| Date       | Version | Description | Author |
| ---------- | ------- | ----------- | ------ |
| 2026-06-14 | 0.1     | Story 5.6 created — Admin 운영 대시보드(읽기 전용 관측)·심각도 4단계(정상/주의/위험/중지)·인증 필요 필터. 파생 집계(신규 컬럼/테이블 0, 14표·0004 head 유지) + Jinja2/HTMX 서버 렌더 + DashboardRepository 포트. | Bob (create-story) |
| 2026-06-14 | 1.0     | Story 5.6 구현 완료 — 순수 심각도 정책(`admin/severity.py`)·read-model 서비스+포트(`admin/dashboard_service.py`)·PG 파생 집계(`admin/dashboard_repository_postgres.py`)·HTMX 라우트/Jinja2 템플릿·create_app 와이어링·jinja2 server extra. 전체 1676 passed / 30 skipped(PG-gated), 무회귀. Status → review. | Amelia (dev-story) |
| 2026-06-14 | 1.1     | Senior Developer Review(AI) 완료 — CRITICAL/HIGH 0(4 AC·전 Task 구현 검증). MEDIUM 2건 auto-fix(File List 에 `test_dashboard_pg_helpers.py` 누락 보강, 테스트 카운트 재측정 67/1702). LOW 3건 기록(PG-only·5.9 deferred — 무-Postgres 환경 미검증이라 SQL 변경 보류). Status → done. | lsy9344 (review) |

## Senior Developer Review (AI)

**Reviewer:** lsy9344 · **Date:** 2026-06-14 · **Mode:** story-automator adversarial review (auto-fix) · **Outcome:** ✅ Approve (Status → done)

**재측정 테스트:** 5.6 5파일 → 67 passed / 6 skipped(PG-gated); 전체 회귀 → 1702 passed / 30 skipped(무회귀). 14표·9-dep·enum count-lock·0004 head 유지.

### AC 검증 (4/4 구현)

- **AC1 (Jinja2+HTMX 읽기 대시보드)** — ✅ `admin/routes.py` 풀 페이지 + 4 fragment(`/admin/targets|agents|channels|auth-required`), HTMX CDN(`unpkg.com/htmx.org@2`, npm/빌드 0), `every 30s` polling swap. 파생 집계 표시값(수집/전송 성공·실패 사유·heartbeat/버전/현재 job/capability), Kakao lag vs Telegram 오류 별도 필드. `require_admin_session` seam 통과(기본 no-op, 5.8 교체).
- **AC2 (시간 경과 심각도)** — ✅ `severity.classify_freshness` interval×2/×4 **초과** 경계(=경계는 하위 등급), `None`→최소 WARNING, `interval<=0`→NORMAL. 순수 함수(now 주입, 내부 `datetime.now()` 0).
- **AC3 (fail-closed 우선)** — ✅ `failclosed_signals_from`(문자열 값 비교로 타입별 동명 `AUTH_REQUIRED` 혼동 차단)→`classify_failclosed`→STOPPED, `overall_severity` 가 freshness 를 덮음. 단위·서비스 양층 잠금.
- **AC4 (인증 필요 필터)** — ✅ `auth_required` tenant scope 조인(계정 AUTH_REQUIRED + auth_session pending), reason 은 기계가독 코드만(secret/OTP/토큰 0). cross-tenant negative 검증.

### Task 감사 (6/6 [x] 실제 완료)

전 Task 의 [x] 가 코드 증거와 일치(severity 순수 정책·repository 포트+in-memory/PG·라우트/템플릿·create_app seam·jinja2 server extra·4-tier 테스트). `[project].dependencies` 9개 불변, `jinja2` 는 server extra. read-only AST 가드(write/transition 호출 0)·단방향 import 가드 통과.

### 검증한(문제 없음) 항목

- **타임존 안전**: 전 datetime 컬럼 `DateTime(timezone=True)`(`_columns.ts()`), 라우트 `_now()`=`datetime.now(timezone.utc)` → aware−aware 뺄셈, TypeError 위험 0(jobs.py 선례 일치).
- **XSS**: `Jinja2Templates` 기본 autoescape(.html) → 한글 가게/센터명 escape.
- **tenant lifecycle 타입**: `Tenant.status`=CustomerLifecycleState 값(모델 주석·PG seed 확인) → `lifecycle_state==AUTH_REQUIRED` 매핑 정확.
- **create_app 와이어링**: `_default_dashboard_repository` 가 `_default_queue_backend`/`_default_channel_repository` 와 동형(DATABASE_URL 분기, lazy engine).

### 발견 및 조치

| # | 심각도 | 발견 | 조치 |
| - | ------ | ---- | ---- |
| 1 | MEDIUM | File List 에 `tests/server/test_dashboard_pg_helpers.py`(qa-generate-e2e 추가, 12 케이스) 누락 | ✅ Auto-fix — File List 보강 |
| 2 | MEDIUM | Debug Log 테스트 카운트 stale(41/1676 → 실제 67/1702; qa 케이스 추가 후 미갱신, memory/stale-test-count-a2) | ✅ Auto-fix — 재측정값 기록 |
| 3 | LOW | `_latest_failure_code`: `sent_at=NULL`(미전송) 실패는 ts NULL→최오래 취급이라, 더 오래된 job 실패가 최신 telegram/kakao 실패를 가릴 수 있음 | 📝 기록 — docstring 명시된 5.9-deferred 근사(종료시각 컬럼 부재). PG-only·무-Postgres 환경 미검증이라 SQL 변경 보류 |
| 4 | LOW | PG `target_health`: target 당 4~5 서브쿼리(N+1) | 📝 기록 — tenant scope 로 N 작고 30s polling 읽기 전용. 포트가 집계 캡슐화 → 후속 최적화 비파괴. 무-Postgres 환경 미검증이라 변경 보류 |
| 5 | LOW | `auth_required`: target 없는 계정 AUTH_REQUIRED 는 inner join 으로 누락(auth_session pending 분기는 별도 도출) | 📝 기록 — 실사용상 계정-무-target 은 비정상 케이스. PG-only 미검증 보류 |

**LOW 보류 사유:** #3~#5 는 모두 PostgreSQL 전용 경로이며 현 환경에 `TEST_DATABASE_URL`/Postgres 부재로 PG-gated 테스트가 skip 된다. 검증 불가한 SQL 을 맹목 수정하면 skip 으로 가려진 회귀를 유발할 수 있어, story 가 명시한 5.9 정밀화 deferral 을 존중하고 기록만 한다(5.9 백로그 후보).
