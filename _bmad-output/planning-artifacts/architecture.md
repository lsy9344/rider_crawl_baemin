---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-06-12'
inputDocuments:
  - '_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/prd.md'
  - '_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/addendum.md'
  - '_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/reconcile-research.md'
  - '_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/review-operational-risk.md'
  - '_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/review-operational-risk-delta.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/SPEC.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/architecture-contract.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/.decision-log.md'
  - 'docs/module-architecture.md'
  - 'docs/project-current-state-and-structure.md'
  - '_bmad-output/project-context.md'
workflowType: 'architecture'
project_name: 'rider_result_mornitoring'
user_name: 'Noah Lee'
date: '2026-06-12'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:** PRD에 34개 FR이 8개 카테고리로 구성됨 — (1) 기준선/회귀
방지 FR-1~3, (2) ID 기반 운영 모델 FR-4~6, (3) 수집-렌더링-전송 분리 FR-7~11,
(4) Local Agent/작업 노드 FR-12~16, (5) 플랫폼 인증/계정 안전 FR-17~20, (6) 중앙
서버/Admin FR-21~23, (7) 메신저 채널/전송 정책 FR-24~26, (8) 마이그레이션/배포/운영
안전 FR-27~34. 이는 그린필드가 아니라 동작 보존이 최우선인 브라운필드 리팩토링이며,
기존 배민/쿠팡 parser·renderer·sender·Gmail 2FA를 wrapping 방식으로 재사용한다.

**Non-Functional Requirements (실제 설계 드라이버):**
- Fail-closed: 오발송보다 미발송을 선택 → at-least-once + idempotency 강제.
- Multi-tenant 격리: API/DB/queue/log/Admin/Agent 배정 전부 tenant scope.
- Secret 보안: Telegram/Coupang/Gmail/Agent token, OTP의 평문 저장·로깅 금지.
- Outbound-only Agent: 클라우드가 로컬 Chrome CDP에 직접 접속 금지.
- 로컬 제약: 배민 사람 개입 인증, KakaoTalk 직렬 단일 세션, Chrome profile 격리.

**Scale & Complexity:**
- Primary domain: Full-stack 분산 시스템 (Cloud control plane + Windows Local
  Agent + Admin UI).
- Complexity level: High.
- Estimated architectural components: Cloud(backend-api, scheduler,
  telegram-dispatcher, admin-ui) + Agent(crawl worker, kakao sender,
  browser-profile-manager, job loop) + 데이터(PostgreSQL 13 테이블) + 인프라
  (AWS Seoul EC2/RDS/S3/Secrets Manager).
- Multi-tenancy: 필수(MVP 고객≈테넌트 1:1). Real-time: 부분(heartbeat/폴링).

### Technical Constraints & Dependencies

- 고정 의존성: crawl4ai==0.8.7, playwright==1.60.0 (임의 업그레이드 금지).
- 비공식 웹 스크래핑 방식(공식 API 아님): 사이트 구조 변경·로그인 만료를 정상
  운영 위험으로 취급.
- 기존 자산 보존: run_once 경계, platforms/messengers registry, 배민 legacy
  crawler.py/parser.py/sender.py, 쿠팡 platforms/coupang/, auth/gmail.py.
- 1차 인프라: 현재 Windows PC를 Agent #1로 사용. 고성능 서버 선구매 금지.
- spec 계약이 이미 13 도메인 모델/13 테이블/Agent API 5종/3 상태머신/dedup key를
  정의 → 본 아키텍처는 이를 검증·확정·통합한다.

### Cross-Cutting Concerns Identified

1. 분산 job 안전성 — crash-after-send, 중복 claim, stale token, replay 방지를
   위한 DB 레벨 idempotency 유니크 제약 + job lease 의미론.
2. Tenant isolation — 모든 계층에서 기본 강제 + cross-tenant negative test.
3. Secret custody & redaction — 저장 위치 분류(중앙 secret store / DPAPI / 비저장)
   + 로그/artifact 마스킹.
4. 마이그레이션 안전 — 상태머신, 전역 kill switch, old/new 동시전송 방지, canary,
   reconciliation 체크.
5. 관측성 + 인시던트 대응 — 7개 지표(heartbeat/success lag/auth/kakao lag/error
   rate 등) + 최소 runbook 세트.

### Architecture Decisions This Workflow Must Close

PRD §13.1과 delta 리뷰가 "represented but not yet decided"로 남긴 항목 —
- 7 ADR: Agent 인증/job claim, Secret 저장/rotation, Queue/job state·idempotency·
  crash-after-send, Tenant isolation, Migration cutover/rollback/kill switch,
  Admin access/MFA/RBAC, KakaoTalk 제품 정책.
- 3 Open Question: queue backend(PostgreSQL vs Redis), Admin UI 기술(React/Next
  vs 서버 렌더링), Gmail token 저장 위치 확정.

## Starter Template Evaluation

### Primary Technology Domain

브라운필드 리팩토링. 단일 스타터 스캐폴드 모델이 아니라 기존 Python 패키지를
foundation으로 두고 3개 빌드 타깃(Cloud / Agent / Admin UI)을 구성한다.

### Foundation Targets

**① Cloud Control Plane (신규)**
- FastAPI 0.136.x + SQLAlchemy 2.x(async) + Alembic 1.18.x + PostgreSQL 18
- Docker Compose: backend-api, scheduler, telegram-dispatcher
- 정식 CLI 스캐폴드 없음 → 표준 app/ 레이아웃 직접 구성, 첫 구현 스토리로 둔다.

**② Windows Local Agent (신규, 기존 코드 재사용)**
- `rider_agent` 패키지가 기존 src/rider_crawl/ (crawler/parser/renderer/
  Gmail 2FA/Kakao sender)를 import. 새 프레임워크 도입 없음.
- Playwright 1.60.0 고정(기존과 동일). 배포는 기존 PyInstaller spec 재활용.

**③ Admin UI (신규 프론트엔드, SPEC Open Question)**
- 옵션: (A) React-Admin/Refine SPA, (B) FastAPI+Jinja2+HTMX 서버 렌더링,
  (C) Next.js SSR.
- 운영자 전용 내부 도구이며 SEO 불필요 → C(Next.js full SSR)는 과함.
- 다음 단계 ADR에서 정식 확정. (초기 경향: 단순성 우선이면 B, 데이터 집약
  운영 대시보드 확장성 우선이면 A.)

### Verified Current Versions (web search, 2026-06)

| 기술 | 최신 안정 | 채택 |
| --- | --- | --- |
| FastAPI | 0.136.3 | 최신 |
| PostgreSQL | 18.4 (19 beta) | 18 (RDS 안정) |
| Alembic | 1.18.4 (async) | 채택 |
| Playwright(Python) | 1.60.0 | 1.60.0 고정 |

### Reused Existing Foundation (변경 금지 경계)

- src/rider_crawl/ 패키지, platforms/messengers registry, run_once 경계
- 배민 legacy crawler.py/parser.py/sender.py, 쿠팡 platforms/coupang/
- auth/gmail.py, auth/coupang_email_2fa.py
- pytest 구조, PyInstaller onefile spec

**Note:** Cloud backend와 Admin UI의 초기 프로젝트 스캐폴딩은 첫 구현 스토리로 둔다.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
- Queue backend: PostgreSQL job table (FOR UPDATE SKIP LOCKED), QueueBackend
  인터페이스로 추상화해 추후 Redis/SQS 교체 보장.
- Job 의미론: at-least-once + DB 레벨 idempotency 유니크 제약. exactly-once를
  가정하지 않는다 (crash-after-send 안전).
- Access model: 전 Admin 계정 MFA 필수, 역할 4종(viewer/operator/secret-admin/
  break-glass), Agent token은 tenant+job-type scope.
- Secret 저장: 중앙 Secrets Manager(Telegram/Coupang/JWT ref) + Agent-local
  DPAPI(Gmail/Agent token). DB/로그/스크린샷/config 평문 금지.
- Tenant isolation: 모든 고객 소유 엔티티에 tenant_id, 모든 API/queue/Admin
  쿼리가 기본 tenant scope 필터.

**Important Decisions (Shape Architecture):**
- Admin UI: FastAPI + Jinja2 + HTMX 서버 렌더링(단일 Python 스택).
- 마이그레이션 cutover: 상태머신(discovered→mapped→dry-run passed→approved→
  active→paused→rolled back) + 전역 kill switch + old/new 동시전송 방지 + canary.
- KakaoTalk: 제한/best-effort. sender FIFO queue, 단일 Windows session 직렬,
  고유 방명 검증. 강한 SLA 약속 금지.

**Deferred Decisions (Post-MVP):**
- 결제 PG 연동, 고객 설치형 Agent, 다중 리전, Redis/SQS 전환은 지표 기반 후속.
- 실제 도메인, 요금제 quota 숫자, KakaoTalk 공식 API 채택 여부.

### Data Architecture

- DB: PostgreSQL 18 (AWS RDS), PITR + ≥7일 보존 + 수동 스냅샷.
- ORM/마이그레이션: SQLAlchemy 2.x(async) + Alembic 1.18.x. 빈 DB → 13 테이블
  마이그레이션이 P4-02 수용 기준.
- 모델: data-api-contract.md의 13 도메인 모델/13 테이블 채택(tenants,
  subscriptions, platform_accounts, monitoring_targets, browser_profiles,
  messenger_channels, delivery_rules, snapshots, messages, delivery_logs,
  agents, jobs, auth_sessions, audit_logs).
- 검증: Pydantic v2(API 경계). dataclass는 Agent 내부 도메인 객체에 유지.
- Dedup key: target_id + channel_id + collected_at + template_version +
  message_hash. delivery_logs 성공 레코드에 DB 유니크 제약.
- 캐싱: MVP에선 별도 캐시 레이어 없음. queue=PostgreSQL이라 Redis 미도입.

### Authentication & Security

- Admin: MFA 필수(전 계정) + 역할 4종 + VPN/IP allowlist 추가 가능.
- Agent: registration code → agent_id+token 발급. token은 tenant+job-type
  scope, server-side revoke/rotate 가능. replay 방지(서명된 claim/lease).
- Secret 저장 분류: 중앙 Secrets Manager(ref만 DB) vs Agent-local DPAPI vs
  비저장. Gmail/Agent/Chrome profile은 Agent-local. encryption at rest.
- Redaction: password/token/refresh/authorization code/OTP/full phone/full
  email 로깅 금지. error 이벤트는 message_redacted/error_message_redacted.
- Raw HTML 저장 기본 금지(sanitized만). Kakao 스크린샷 업로드는 마스킹/승인.

### API & Communication Patterns

- API 스타일: REST (FastAPI), OpenAPI 자동 문서.
- Agent API: register/heartbeat/jobs.claim/jobs.events/jobs.complete (5종).
- 통신: Agent→Server outbound HTTPS only. Server가 로컬 Chrome CDP 직접 접속 금지.
- Telegram: 중앙 webhook + secret header 검증. /register <code>. Agent별
  getUpdates polling 제거(token 큐 경합 방지).
- 에러 분류: 조치 가능 카테고리(수집/인증/렌더/Telegram/Kakao/중복/대상검증
  실패) + error_code별 backoff. 5초 무한 재시도 금지(circuit breaker).

### Frontend Architecture

- Admin UI: FastAPI + Jinja2 템플릿 + HTMX(부분 갱신). 별도 JS 빌드 파이프라인
  없음. 백엔드와 동일 인증/세션.
- 화면: 고객/target/agent/channel/최근 오류/마지막 성공/queue lag/auth-required
  필터/감사 로그. 상태 심각도(정상/주의/위험/중지) 표시.
- 상태 계산: target_last_success_at > interval×2 → warning, ×4 → critical.

### Infrastructure & Deployment

- 호스팅: AWS Seoul. EC2 Ubuntu LTS + Docker Compose(backend-api, scheduler,
  telegram-dispatcher, admin-ui). 추후 ECS/Fargate 이전 가능하게 Dockerfile/
  env 분리.
- Object storage: S3(sanitized 스크린샷/HTML fixture/export). raw 민감 HTML 금지.
- CI/CD: lint + test + build. 이미지 태깅. DB 마이그레이션은 백업 확인 후 실행.
- Agent 배포: 기존 PyInstaller onefile 재활용. 버전 manifest, job 없을 때
  업데이트, rollback 바이너리 유지. Windows Startup/Task Scheduler 자동 시작.
- 모니터링: CloudWatch + 7 지표(agent_last_heartbeat, target_last_success_at,
  auth_required_count, kakao_queue_lag_seconds, crawl_error_rate_by_platform,
  telegram_send_error_rate, gmail_reauth_required_count).
- 스케일링: 현재 PC=Agent #1. 측정 트리거(CPU>70%/RAM>80%/target>20-30/
  kakao lag>120s) 도달 시 전용 worker PC + target sharding.

### Decision Impact Analysis

**Implementation Sequence (P0~P4 MVP):**
1. P0 기준선/redaction/회귀 시나리오 고정.
2. P1 도메인/설정 리팩토링(customer_id, atomic write, secret ref 분리).
3. P2 수집-렌더-전송 분리(Snapshot/Message/DeliveryRule/DeliveryLog/idempotency).
4. P3 Local Agent(registration, heartbeat, claim loop, BrowserProfileManager,
   KakaoSenderWorker, autostart).
5. P4 중앙 서버(FastAPI/PostgreSQL/Alembic/scheduler/Telegram webhook/Admin/audit).

**Cross-Component Dependencies:**
- Queue=PostgreSQL 결정 → Redis 미도입 → 캐싱/큐 단순화 → idempotency가 DB
  트랜잭션과 한 곳에 → crash-after-send 안전성 단순화.
- Admin UI=서버 렌더링 → 백엔드와 동일 배포/인증 → 프론트 빌드 파이프라인 제거.
- Gmail token=Agent-local → Agent 분실/교체 시 재인증 흐름 필요(운영 runbook).
- MFA+4역할+scoped token → audit_logs 필드/감사 cadence가 Admin·Agent 양쪽에 영향.

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

**Critical Conflict Points Identified:** 6개 영역(case 경계, async/sync 경계,
DB 네이밍, 에러/분류, 기존 JSON 호환, 직렬화 위치)에서 에이전트가 다른 선택을
할 수 있어 명시적으로 고정한다. 기존 project-context.md 규칙 56개를 상위 권위로
유지하고, 신규 Cloud/Agent 코드의 공백만 보강한다.

### Naming Patterns

**Database Naming Conventions (PostgreSQL):**
- 테이블: 복수 snake_case (`monitoring_targets`, `delivery_logs`). data-api-
  contract.md 테이블명을 정본으로 한다.
- 컬럼: snake_case (`tenant_id`, `last_heartbeat_at`, `collected_at`).
- PK: `id` (UUID). FK: `<entity>_id` (`platform_account_id`, `target_id`).
- 시각 컬럼: `_at` 접미사 + timezone-aware (`created_at`, `sent_at`).
- 상태 컬럼: `status`/`state`, 값은 대문자 enum 문자열(`ACTIVE`, `AUTH_REQUIRED`).
- 인덱스: `ix_<table>_<col>`. 유니크: `uq_<table>_<cols>`
  (예: dedup `uq_delivery_logs_dedup_key`).
- secret은 컬럼에 평문 금지. `*_ref` 컬럼만(`password_ref`, `username_ref`).

**API Naming Conventions (FastAPI REST):**
- 경로: 복수 명사 + `/v1/` 접두. `/v1/agents`, `/v1/jobs/{job_id}/complete`.
- 경로 파라미터: `{job_id}` (snake_case).
- JSON 필드: **snake_case** (Python 도메인과 일치, camelCase 변환 안 함).
- 헤더: `X-Agent-Token`, `X-Telegram-Bot-Api-Secret-Token` 등 표준/명시형.

**Code Naming Conventions (Python):**
- 함수/변수: snake_case. 클래스/dataclass/Pydantic 모델: PascalCase.
  (project-context.md 규칙 유지)
- 모듈/패키지: snake_case. 신규 패키지: Cloud=`rider_server`, Agent=`rider_agent`.
- 공개 경계 이름 호환 우선: `coupang_eats_url`, `baemin_center_name` 등 기존
  필드명은 넓은 변경 없이 이름만 바꾸지 않는다(legacy alias로 매핑).

### Structure Patterns

**Project Organization:**
- 제품 코드는 `src/` 아래. 기존 `src/rider_crawl/`(공유 도메인: parser/renderer/
  Gmail 2FA/kakao) 유지. 신규 `src/rider_server/`(Cloud), `src/rider_agent/`(Agent).
- 레이어: Domain / Application(service) / Infrastructure(DB·외부) / Interface
  (API·UI). 외부 서비스/브라우저/메신저는 함수 주입 또는 adapter 경계 유지
  (run_once 테스트 패턴 계승).
- 테스트: `tests/` 디렉터리 미러 구조(co-located 아님). pytest
  `pythonpath=["src"]`, `testpaths=["tests"]` 유지. 신규 모듈은 옆 기존 테스트
  파일 패턴을 따른다(test_app.py, test_config.py, test_architecture.py 등).

**File Structure Patterns:**
- 런타임 상태=`runtime/`, 로그=`logs/`, 비밀=`secrets/google/`,
  Agent 상태=토큰별 고정 루트(`app_state_root()`). 이 경로 정책을 깨지 않는다.
- 설정 저장 JSON: `ensure_ascii=False, indent=2`. atomic write(temp→fsync→rename).
- Alembic 마이그레이션: `migrations/versions/`. 빈 DB→전체 테이블 재현 가능.

### Format Patterns

**API Response Formats:**
- 성공: 리소스 객체 직접 반환(불필요한 `{data:...}` 래퍼 없음). 목록은
  `{"items": [...], "next_cursor": ...}`.
- 에러: `{"error": {"code": "<UPPER_SNAKE>", "message_redacted": "..."}}`.
  HTTP 상태코드를 의미 있게 사용(400/401/403/404/409/422/429/503).
- 시각: ISO 8601 UTC 문자열(`2026-06-12T03:04:05Z`). epoch 정수 혼용 금지.
- boolean: JSON true/false. null은 "값 없음"에만, 기본값 덮어쓰기 금지.

**Data Exchange Formats:**
- Snapshot.normalized_json: 안정적 키 + parser_version 포함. 필수 데이터
  누락 시 기본값으로 채우지 않고 명확한 예외(MissingPerformanceDataError 계승).
- 직렬화 경계: Agent 내부 도메인=dataclass, API 경계=Pydantic v2.
  교차 시 명시적 변환 함수(`to_api_model`/`from_api_model`)를 둔다.

### Communication Patterns

**Event/Job Patterns:**
- job type: UPPER_SNAKE(`CRAWL`, `RENDER`, `DISPATCH_TELEGRAM`, `KAKAO_SEND`,
  `BAEMIN_AUTH_OPEN`). 상태 전이는 정의된 set만 허용.
- job 이벤트(`/jobs/{id}/events`): `event_type`, `severity`, `message_redacted`,
  artifact ref. 본문에 secret/OTP 금지.
- 멱등성: dispatch는 중복 실행/crash-after-send에 안전해야 한다. 성공 전송 전
  dedup key 유니크 제약을 먼저 확보(insert-then-send 또는 동등 패턴).
- lease: claim 시 lease 만료시각 부여. heartbeat로 연장, 만료 시 stale 회수.

**State Management Patterns:**
- 상태값은 enum 문자열 단일 정본(Python Enum ↔ DB 문자열 일치).
- 불변 업데이트 선호. 상태 전이는 service 레이어에서만(직접 DB 컬럼 임의 변경 금지).

### Process Patterns

**Error Handling Patterns:**
- 분류 정본(운영 카테고리): crawl_failure, auth_required, render_failure,
  telegram_failure, kakao_failure, duplicate_blocked, target_validation_failure.
- 인증 필요는 무한 재시도 금지 → AUTH_REQUIRED 계열 상태로 전이.
- 일시 오류는 error_code별 backoff(고정 5초 무한 재시도 금지). platform-wide
  실패율 급증 시 circuit breaker.
- fail-closed: 잘못된 tenant/profile/Kakao room이면 전송하지 않고 실패 기록.
- 모든 로그/예외 메시지는 redaction 통과(password/token/OTP/full phone/email).

**Loading/Async State Patterns:**
- Cloud(FastAPI/SQLAlchemy)=async. Agent의 Playwright/PC 자동화 경로=sync.
  두 경계를 섞지 않는다(async 함수에서 blocking sync 직접 호출 금지,
  필요 시 executor 경계).
- Admin UI(HTMX)=서버 렌더 부분 갱신. 클라이언트 상태 저장 최소화.

### Enforcement Guidelines

**All AI Agents MUST:**
- project-context.md 56개 규칙을 최우선 제약으로 먼저 읽고 따른다.
- DB/API 이름은 data-api-contract.md 정본과 일치시킨다.
- secret을 컬럼/로그/스크린샷/config에 평문으로 두지 않는다(`*_ref`만).
- 기존 공개 동작(렌더링 결과, 저장 JSON 호환, 탭 9개 로딩, 쿠팡 추론)을
  의도 없이 바꾸지 않는다 → 바뀌면 실패로 취급.
- 분산 작업은 at-least-once를 가정하고 dedup 유니크 제약 + lease로 안전화.

**Pattern Enforcement:**
- 검증: pytest(`.venv\Scripts\python.exe -m pytest`) + 마이그레이션 호환 테스트
  + cross-tenant/stale-token/crash-after-send negative test.
- 위반 발견 시 architecture.md ADR 또는 project-context.md에 예외를 기록.
- 패턴 변경은 이 문서와 project-context.md를 함께 갱신.

### Pattern Examples

**Good:**
- DB: `delivery_logs.dedup_key` UNIQUE → 같은 key 재시도는 INSERT 충돌로 차단.
- API: `GET /v1/monitoring_targets` → `{"items":[{"id":"...","status":"ACTIVE"}]}`.
- Error: `409 {"error":{"code":"DUPLICATE_BLOCKED","message_redacted":"..."}}`.

**Anti-Patterns:**
- ❌ API 응답을 camelCase로 변환(`targetId`).
- ❌ snapshot 필수값 누락을 0/기본값으로 채워 메시지 생성.
- ❌ async service에서 Playwright sync 호출 직접 await 없이 블로킹.
- ❌ `state_subdir`를 다시 `crawlingN` 순번 기반으로 식별.
- ❌ secret을 `password` 컬럼/`error_message`에 평문 저장.

## Project Structure & Boundaries

### Complete Project Directory Structure

```text
rider_result_mornitoring/
├── pyproject.toml                  # pytest pythonpath=["src"], 의존성
├── uv.lock
├── README.md
├── rider_crawl_onefile.spec        # Agent PyInstaller (기존 재활용)
├── rider_crawl_exe_entry.py
├── .env.example
├── config.json                     # 키워드 자동응답(기존, exe 옆 배포)
├── .github/
│   └── workflows/
│       ├── ci.yml                  # lint + pytest + build
│       └── agent-release.yml       # Agent 버전 manifest 빌드
├── deploy/
│   ├── docker-compose.yml          # api/scheduler/telegram-dispatcher/admin
│   ├── Dockerfile.server
│   └── env/                        # 환경별 분리(추후 ECS/Fargate 이전 대비)
├── migrations/                     # Alembic (rider_server DB)
│   ├── env.py                      # async 템플릿
│   └── versions/
├── src/
│   ├── rider_crawl/                # [공유 도메인 — 기존, 보존]
│   │   ├── __main__.py             # 기존 tkinter UI 진입(레거시 호환 유지)
│   │   ├── app.py                  # run_once 경계(호환 경로로 유지)
│   │   ├── models.py               # CurrentScreenSnapshot/PerformanceSnapshot 등
│   │   ├── message.py              # renderer (template_version 추가)
│   │   ├── crawler.py / parser.py  # 배민 legacy (보존)
│   │   ├── sender.py               # Telegram API + Kakao UI legacy
│   │   ├── platforms/
│   │   │   ├── base.py / __init__.py
│   │   │   ├── baemin.py
│   │   │   └── coupang/{crawler,parser}.py
│   │   ├── messengers/{base,telegram,kakao}.py
│   │   ├── auth/{gmail,coupang_email_2fa}.py
│   │   ├── browser_launcher.py / lock.py / scheduler.py
│   │   ├── ui.py / ui_settings.py / config.py
│   │   ├── keyword_responder.py / telegram_commands.py
│   │   └── redaction.py            # [신규] 공용 redaction 유틸(P0-04)
│   │
│   ├── rider_server/               # [신규 — Cloud Control Plane]
│   │   ├── __main__.py
│   │   ├── main.py                 # FastAPI app (/health /version /metrics)
│   │   ├── settings.py             # env, Secrets Manager ref 로딩
│   │   ├── domain/                 # 13 도메인 모델(dataclass/Enum) + 상태머신
│   │   │   ├── tenant.py / subscription.py / monitoring_target.py
│   │   │   ├── job.py / snapshot.py / message.py / delivery.py
│   │   │   ├── agent.py / auth_session.py / secret_ref.py
│   │   │   └── states.py           # Customer/Subscription/Baemin auth enum
│   │   ├── db/
│   │   │   ├── base.py             # async engine/session
│   │   │   └── models/             # SQLAlchemy ORM (13 테이블)
│   │   ├── schemas/                # Pydantic v2 (API 경계)
│   │   ├── services/               # CrawlService/MessageRenderService/
│   │   │   │                       #   DispatchService/SubscriptionGate
│   │   │   └── idempotency.py      # dedup key + insert-then-send
│   │   ├── queue/
│   │   │   ├── backend.py          # QueueBackend 인터페이스
│   │   │   └── postgres_queue.py   # FOR UPDATE SKIP LOCKED 구현
│   │   ├── scheduler/              # interval+jitter, circuit breaker
│   │   ├── dispatch/
│   │   │   └── telegram_dispatcher.py  # 중앙 webhook/sendMessage
│   │   ├── api/
│   │   │   ├── agents.py           # register/heartbeat
│   │   │   ├── jobs.py             # claim/events/complete
│   │   │   ├── telegram_webhook.py # secret header 검증 + /register
│   │   │   └── admin_api.py
│   │   ├── admin/                  # FastAPI+Jinja2+HTMX
│   │   │   ├── routes.py
│   │   │   └── templates/          # 대시보드/필터/감사 로그
│   │   ├── security/               # MFA, 4역할, agent token scope, audit
│   │   └── migration/              # cutover 상태머신, kill switch, canary
│   │
│   └── rider_agent/                # [신규 — Windows Local Agent]
│       ├── __main__.py             # python -m rider_agent
│       ├── registration.py         # registration code → agent_id/token
│       ├── secure_store.py         # DPAPI/Credential Manager (token/Gmail)
│       ├── heartbeat.py            # 30~60s
│       ├── job_loop.py             # outbound HTTPS poll/claim/complete
│       ├── browser_profile.py      # BrowserProfileManager(port/profile 격리)
│       ├── workers/
│       │   ├── crawl_worker.py     # rider_crawl 도메인 import
│       │   └── kakao_sender.py     # FIFO queue, 단일 세션 직렬
│       ├── auth/                   # 배민 auth open, Gmail mailbox lock
│       └── autostart.py            # Windows Startup/Task Scheduler
├── tests/
│   ├── (기존 test_*.py 보존)
│   ├── server/                     # API/job lifecycle/tenant isolation
│   ├── agent/                      # claim loop/profile/kakao queue
│   ├── regression/                 # 기존 2탭 dry-run 비교(P0-05)
│   └── negative/                   # cross-tenant/stale-token/crash-after-send
├── docs/
│   ├── qa/                         # P0 pytest 결과, baseline
│   ├── runbooks/                   # agent_offline/queue_lag/auth_required 등
│   └── (기존 docs 보존)
├── runtime/                        # 로컬 런타임 상태(기존 정책)
├── logs/                           # 로테이션 적용
└── secrets/google/                 # Gmail OAuth(Git 제외)
```

### Architectural Boundaries

**API Boundaries:**
- Agent API(`/v1/agents/*`, `/v1/jobs/*`): token-auth, outbound-only.
- Admin API/UI: 세션+MFA, 역할 기반. Agent API와 인증 경계 분리.
- Telegram webhook: secret header 검증. 외부 인바운드 단일 진입.
- 모든 customer-owned 쿼리는 tenant scope 필터 통과(격리 경계).

**Component Boundaries:**
- 공유 도메인(rider_crawl): Cloud와 Agent가 함께 import. UI(tkinter)는 레거시
  호환 경로로만 남고 신규 운영은 Agent/Server 경유.
- Cloud=async, Agent=sync. 두 런타임은 HTTP(JSON)로만 통신. 코드 직접 호출 없음.
- Service 레이어만 상태 전이/DB 변경. API/UI는 service 호출.

**Service Boundaries:**
- CrawlService→Snapshot, MessageRenderService→Message, DispatchService→
  DispatchJob/DeliveryLog. 각 단계 실패는 독립 상태로 기록(결합 금지).
- SubscriptionGate가 scheduler 앞단에서 비활성 고객 job 생성 차단.

**Data Boundaries:**
- 단일 PostgreSQL(13 테이블). queue도 같은 DB(jobs 테이블) → 트랜잭션 일관성.
- secret 값은 DB 밖(Secrets Manager / Agent DPAPI). DB엔 `*_ref`만.
- S3=sanitized artifact만. raw 민감 HTML 저장 금지.
- runtime/·logs·secrets/google는 Agent 로컬 디스크 경계(서버로 평문 업로드 금지).

### Requirements to Structure Mapping

- ① 기준선/회귀(FR-1~3) → tests/regression/, docs/qa/, redaction.py.
- ② ID 모델(FR-4~6) → rider_server/domain/, db/models/, migrations/.
- ③ 수집-렌더-전송(FR-7~11) → rider_crawl 도메인 + rider_server/services/ +
  queue/idempotency.py.
- ④ Local Agent(FR-12~16) → rider_agent/ 전체.
- ⑤ 플랫폼 인증(FR-17~20) → rider_crawl/auth/, platforms/, rider_agent/auth/.
- ⑥ 중앙 서버/Admin(FR-21~23) → rider_server/api/, admin/, security/.
- ⑦ 메신저/전송 정책(FR-24~26) → rider_server/dispatch/(Telegram),
  rider_agent/workers/kakao_sender.py.
- ⑧ 마이그레이션/배포/안전(FR-27~34) → rider_server/migration/, deploy/,
  .github/workflows/, docs/runbooks/.

### Integration Points

**Internal Communication:**
- Agent ↔ Server: outbound HTTPS JSON(register/heartbeat/claim/events/complete).
- Server 내부: api → service → db/queue. scheduler → queue. dispatcher → service.
- Admin(HTMX) → admin_api → service(서버 렌더 부분 갱신).

**External Integrations:**
- Telegram Bot API(중앙), KakaoTalk PC 앱(Agent UI 자동화), Gmail API(Agent),
  배민/쿠팡 웹(Agent Playwright/CDP), AWS(RDS/S3/Secrets Manager/CloudWatch).

**Data Flow:**
- scheduler가 due target→CrawlJob 생성 → Agent claim → snapshot 업로드 →
  MessageRenderService → DeliveryRule fan-out → DispatchJob(Telegram=중앙,
  Kakao=Agent queue) → DeliveryLog(dedup) → Admin 가시화.

### File Organization Patterns

- Configuration: 루트 pyproject.toml/.env.example, deploy/env/(서버), Agent는
  registration 후 서버 config 수신.
- Source: src/ 3 패키지(rider_crawl 공유 / rider_server / rider_agent).
- Test: tests/ 미러 구조 + regression/negative 분리.
- Asset: S3(sanitized), Admin 정적 자산은 admin/templates 인접.

### Development Workflow Integration

- Dev 실행: 서버=`uvicorn`(Docker Compose), Agent=`python -m rider_agent`,
  레거시 UI=`python -m rider_crawl`(호환 확인용).
- Build: 서버=Docker 이미지 태깅, Agent=PyInstaller onefile + 버전 manifest.
- Deploy: DB 마이그레이션은 백업 확인 후. Agent 업데이트는 job 없을 때 +
  rollback 바이너리 유지. staging smoke(fake/fixture target) 후 prod.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:**
- 기술 스택 호환: FastAPI 0.136 + SQLAlchemy 2(async) + Alembic 1.18 +
  PostgreSQL 18은 검증된 조합. Playwright 1.60.0은 기존과 동일 핀(충돌 없음).
- Queue=PostgreSQL 결정이 "Redis 미도입 → 캐싱 레이어 없음 → 멱등성이 DB
  트랜잭션과 한 곳"으로 자기 일관적. 모순 결정 없음.
- async(Cloud)/sync(Agent) 경계가 HTTP(JSON)로만 통신해 런타임 충돌 차단.

**Pattern Consistency:**
- snake_case가 Python 도메인·DB 컬럼·API JSON 전반에 일관(camelCase 변환 없음).
- 네이밍 정본(data-api-contract.md 테이블/API)과 패턴 규칙이 일치.
- fail-closed/redaction/멱등성 패턴이 보안·신뢰성 NFR과 정렬.

**Structure Alignment:**
- 3 패키지(rider_crawl 공유 / rider_server / rider_agent)가 책임 분리표와 정렬.
- 경계(Agent outbound-only, service-only 상태 전이, secret DB 밖)가 구조에 반영.
- tests/ regression·negative 분리가 회귀·negative safety 요구를 지원.

### Requirements Coverage Validation ✅

**Functional Requirements Coverage:**
- FR-1~34 전부 "Requirements to Structure Mapping"의 8 카테고리에 매핑됨.
- 핵심 흐름 CrawlJob→Snapshot→Message→DispatchJob→DeliveryLog가 services/queue/
  dispatch 구조로 구현 가능.

**Non-Functional Requirements Coverage:**
- 신뢰성(fail-closed, 필수데이터 누락 시 미발송, idempotency): 패턴+DB 유니크.
- 보안(secret 분류, redaction, MFA/4역할/scoped token, encryption at rest): 결정+
  security/ 구조.
- 관측성(7 지표, 심각도, runbook): 인프라 결정 + docs/runbooks/.
- 호환/마이그레이션(원본 보존, atomic write, last_message seed, 상태머신,
  kill switch): migration/ + 패턴.
- 성능/확장(100 fake target smoke, 측정 기반 증설, target sharding): 결정 반영.

### Implementation Readiness Validation ✅

**Decision Completeness:**
- 7 ADR 주제 모두 결정됨(Agent 인증/job claim, secret 저장/rotation, queue/job
  state·idempotency·crash-after-send, tenant isolation, migration cutover/
  rollback/kill switch, Admin MFA/RBAC, KakaoTalk runtime 제약).
- 3 Open Question 닫힘(queue=PostgreSQL, Admin UI=Jinja2+HTMX, Gmail token=
  Agent-local DPAPI). 버전 web search로 검증됨.

**Structure Completeness:**
- 디렉터리 트리가 실제 기존 src/rider_crawl/ 레이아웃 위에 구체적으로 정의됨.
- 컴포넌트 경계·통합 지점·데이터 흐름 명시됨.

**Pattern Completeness:**
- 6 충돌점(case/async-sync/DB naming/error/JSON 호환/직렬화) 모두 규칙화.
- good/anti-pattern 예시 제공.

### Gap Analysis Results

**Critical Gaps:** 없음(아키텍처 차원). 구현을 막는 미결 아키텍처 결정 없음.

**Important Gaps (아키텍처 외부 — 사업 결정, PRD가 launch blocker로 명시):**
- 법무/약관/계정 위임/고객 동의/인증 대행/KakaoTalk 자동화 정책 검토 미완
  (Owner: 제품/사업 책임자). 아키텍처는 kill switch·fail-closed·best-effort
  표기로 안전장치를 제공하나 결정 자체는 차단 항목.
- KakaoTalk 상품 정책(기본/제한/프리미엄)·공식 API 채택 여부 미결.
- 실제 도메인, 요금제 quota 숫자, 결제 provider 미정.

**Nice-to-Have Gaps:**
- ADR을 개별 파일(docs/adr/)로 분리하면 추적성 향상.
- warning/critical·kakao lag 임계값은 운영 실측 후 조정(현재 초기값 사용).

### Validation Issues Addressed

- "Conditional for execution"(delta 리뷰) 해소: 7 ADR을 본 문서에서 실제 결정.
- review가 지적한 "one-of 2FA/VPN/IP는 약함" → MFA 필수+4역할+scoped token으로 강화.
- 분산 job exactly-once 오가정 위험 → at-least-once+DB 유니크+lease로 명시.

### Architecture Completeness Checklist

**Requirements Analysis**
- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed
- [x] Technical constraints identified
- [x] Cross-cutting concerns mapped

**Architectural Decisions**
- [x] Critical decisions documented with versions
- [x] Technology stack fully specified
- [x] Integration patterns defined
- [x] Performance considerations addressed

**Implementation Patterns**
- [x] Naming conventions established
- [x] Structure patterns defined
- [x] Communication patterns specified
- [x] Process patterns documented

**Project Structure**
- [x] Complete directory structure defined
- [x] Component boundaries established
- [x] Integration points mapped
- [x] Requirements to structure mapping complete

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION (16개 체크리스트 전부 [x],
아키텍처 차원 Critical Gap 없음). 단, **상용 출시(commercial launch)는
법무/약관 검토와 KakaoTalk 상품 정책이라는 사업 launch blocker가 닫혀야 가능** —
이는 아키텍처 준비도와 별개의 사업 게이트다.

**Confidence Level:** high — spec 계약이 이미 검증·교차점검(preservation pass)을
거쳤고, 두 차례 운영 리스크 리뷰가 모든 blocker/high를 gate로 반영함을 확인함.

**Key Strengths:**
- 동작 보존 우선의 브라운필드 전환(기존 parser/renderer/Gmail 2FA 재사용).
- fail-closed 원칙과 DB 레벨 멱등성으로 오발송/중복 차단.
- outbound-only Agent + secret 로컬 보관으로 blast radius 최소화.
- 단계적 P0~P4 cutover + canary + kill switch로 마이그레이션 안전.

**Areas for Future Enhancement:**
- 지표 기반 worker/sender pool 증설(P6), Redis/SQS 전환, 고객 설치형 Agent.
- ADR 개별 파일화, 운영 임계값 실측 튜닝, 결제 자동화.

### Implementation Handoff

**AI Agent Guidelines:**
- 모든 아키텍처 결정을 문서대로 정확히 따른다.
- project-context.md 56 규칙을 최우선 제약으로 둔다.
- 구조·경계를 준수하고, secret을 평문으로 두지 않는다.
- 모든 아키텍처 질문은 이 문서를 참조한다.

**First Implementation Priority:**
- P0 기준선 고정: branch/tag(baseline-local-ui-YYYYMMDD), settings 백업,
  pytest 결과 저장(docs/qa/), redaction 유틸 검증, 기존 2탭 회귀 시나리오 문서화.
- 이후 Cloud backend(FastAPI app 스캐폴드)와 Alembic 초기 마이그레이션을 첫
  구현 스토리로 생성한다.
