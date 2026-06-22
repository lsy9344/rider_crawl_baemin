---
stepsCompleted: [1, 2, 3]
inputDocuments:
  - '_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/prd.md'
  - '_bmad-output/planning-artifacts/architecture.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md'
  - '_bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md'
---

# rider_result_mornitoring - Epic Breakdown

## Overview

이 문서는 `rider_result_mornitoring` 리팩토링의 완전한 에픽/스토리 분해를 제공한다. PRD(FR-1~34, NFR), Architecture(P0~P4 구현 시퀀스, 3 패키지, 13 테이블), 그리고 spec 계약(13 도메인 모델/Agent API 5종/상태머신/dedup key)의 요구사항을 구현 가능한 스토리로 분해한다. 핵심 원칙은 **동작 보존 우선 브라운필드 리팩토링** — 기존 배민/쿠팡 parser·renderer·sender·Gmail 2FA를 wrapping 방식으로 재사용하며, 오발송보다 미발송(fail-closed)을 택한다.

## Requirements Inventory

### Functional Requirements

**① 기준선 고정과 회귀 방지 (FR-1~3) — Realizes UJ-1**

- **FR-1: 기존 동작 기준선 저장** — 운영자는 리팩토링 시작 전 기존 활성 배민/쿠팡 대상, Telegram/Kakao 전송 테스트, 설정 파일, 상태 폴더, pytest 결과를 기준선으로 저장할 수 있어야 한다. (원본 `ui_settings.json`/`crawlingN` 미삭제, 대표 대상 dry-run 결과 보존, 테스트 전송 절차 문서화)
- **FR-2: 기존 자산 재사용 보장** — 배민 parser/crawler, 쿠팡 parser, renderer, Telegram/Kakao sender, 쿠팡 Gmail 2FA, 기존 테스트를 재사용 대상으로 취급. (기존 테스트 계속 실행 가능, 의도치 않은 렌더링 변경은 실패, 기존 공개 동작 호환)
- **FR-3: 신규 경로 dry-run 비교** — 새 수집/렌더링/전송 경로를 실제 발송 없이 dry-run으로 실행하고 기존/신규 메시지 차이를 승인 전 확인. (dry-run은 실발송 없음, 차이 발생 시 자동 활성화 안 함, 승인된 대상만 전송)

**② ID 기반 운영 모델 (FR-4~6) — Realizes UJ-1, UJ-4**

- **FR-4: 고객/대상/채널 ID 관리** — 고객/구독/플랫폼계정/모니터링대상/메시지채널/전송규칙을 ID 기반 CRUD(비활성화 포함). (대상은 플랫폼·계정·기대 센터/상점명·URL/식별자·브라우저 프로필 보유, 전송 규칙 1:N 채널, soft delete/inactive)
- **FR-5: legacy alias 유지** — 마이그레이션 대상은 기존 `크롤링N`/`crawlingN`을 `legacy alias`로 보존. (탭명은 표시명/보조 식별자로만, 추가/삭제/순서 변경이 상태와 안 섞임, 기존 이슈 추적 연결)
- **FR-6: 구독 상태에 따른 작업 제어** — 고객 구독 상태로 작업 실행 가능 여부 판단, 중지 고객의 신규 수집/전송 차단. (`ACTIVE` 아니면 신규 CrawlJob 미예약, `SUSPENDED`의 미전송 Dispatch는 `HELD`, 성공 기록은 재전송 안 됨, 중지 사유·시각 표시)

**③ 수집-렌더링-전송 분리 (FR-7~11) — Realizes UJ-1, UJ-3**

- **FR-7: Crawl Job과 Snapshot 생성** — 대상별 CrawlJob 실행, 결과를 Snapshot으로 저장. (Crawl 실패는 Message/Dispatch로 이어지지 않음, 필수 데이터 누락 시 기본값 메시지 금지·실패 기록, Snapshot은 고객/계정/대상/시각/Agent 추적 가능)
- **FR-8: Message 렌더링 분리** — Snapshot→Message 생성을 수집과 분리. (동일 Snapshot 재수집 없이 재렌더링, 기존/신규 렌더링 비교 가능, 포맷 변경을 수집 수정 없이 검증)
- **FR-9: Dispatch Job fan-out** — 하나의 Message를 여러 DeliveryRule에 따라 여러 Dispatch Job으로 fan-out. (한 Snapshot에서 Telegram·Kakao 각각 생성, 채널 실패가 다른 채널 성공 무효화 안 함, 채널별 상태 별도 기록)
- **FR-10: 중복 발송 방지** — 동일 고객·대상·Snapshot·채널·토픽/방 조합 중복 발송 차단. (동일 idempotency key 성공 전송 미재발송, 다른 고객/대상/채널 전송 오차단 금지, 중복 차단은 DeliveryLog에 별도 결과)
- **FR-11: 재시도와 실패 상태 관리** — 수집/렌더링/전송 실패를 상태로 기록, 재시도 가능 vs 사람 개입 필요 구분. (인증 필요는 무한 재시도 금지·`AUTH_REQUIRED` 계열, 일시 오류는 제한 재시도+backoff, 반복 실패 parser는 경고 상태)

**④ Local Agent와 작업 노드 (FR-12~16) — Realizes UJ-2, UJ-3, UJ-4**

- **FR-12: Agent 등록과 heartbeat** — Local Agent가 등록되고 상태/버전/job type/마지막 heartbeat/현재 작업 보고. (heartbeat 미수신 시 offline/degraded 표시, 버전 불일치 식별, 처리 가능 job type 표시)
- **FR-13: Agent job polling/claim/complete** — 작업 polling, claim한 작업만 실행, 완료/실패/보류 보고. (두 Agent가 같은 job 동시 성공 금지, Agent 사망 시 timeout 후 재할당/실패, 결과에 실행 Agent·시각·실패 사유 포함)
- **FR-14: Browser Profile 격리** — 계정/대상별 Browser Profile·CDP 연결 격리. (서로 다른 고객/계정이 같은 프로필 공유 금지, 포트/프로필 중복 시 작업 미시작, 기대 센터/상점명 검증 실패 시 메시지 미생성·미발송)
- **FR-15: KakaoTalk 직렬 전송** — Kakao 전송은 Local Agent 직렬 queue, 정확한 채팅방 검증 전 미발송. (동시 병렬 입력 금지, 방명 중복/창 확인 실패/포커스 실패/결과 확인 실패는 실패 기록·임의 전송 금지, queue lag 표시)
- **FR-16: outbound-only Agent 통신** — Agent는 outbound HTTPS polling/reporting, 서버의 inbound 접속 불요. (방화벽 inbound 개방 없이 동작, 서버는 마지막 통신 시각·실패 표시, 토큰 없거나 만료 시 job 미수신)

**⑤ 플랫폼 인증과 계정 안전 (FR-17~20) — Realizes UJ-2**

- **FR-17: 배민 인증 필요 감지** — 배민 수집 중 휴대폰 인증/로그인 만료 감지, 작업을 인증 필요 상태로 전환. (인증 필요 시 메시지 미생성·미발송, 어떤 고객/대상/프로필이 인증 요구하는지 확인, 인증 후 명시적 재시도/자동 재개)
- **FR-18: 사람 개입형 배민 재인증** — 운영자/담당자가 브라우저 프로필을 열어 배민 인증 완료. (휴대폰 인증 우회/자동 통과 시도 금지, 인증 확인 전 정상 미표시, 인증 실패/timeout은 상태에 남음)
- **FR-19: 쿠팡 Gmail 2FA 분리** — Gmail 2FA를 고객/메일함/token 단위 분리, 동시 같은 메일함 읽기 충돌 방지. (token은 고객/계정 단위 분리, 같은 mailbox lock, 인증번호·OAuth token·쿠팡 비밀번호 미로깅)
- **FR-20: 플랫폼 대상 검증** — 수집 화면이 기대 고객/센터/상점/대상과 일치하는지 검증. (쿠팡 기대 센터/상점명 비었거나 기본값이면 위험 상태, 다른 화면이면 전송 중단, 검증 실패는 조치 가능 오류 표시)

**⑥ 중앙 서버와 Admin 운영 화면 (FR-21~23) — Realizes UJ-1, UJ-2, UJ-4**

- **FR-21: 운영 대시보드** — 고객/대상/마지막 수집 성공/마지막 전송 성공/인증 상태/Agent 상태/queue 상태/오류를 한 화면(또는 연결 화면)에서 확인. (대상별 마지막 성공 시각·실패 사유, Agent별 heartbeat·버전·현재 job·job type, Kakao queue lag와 Telegram 오류 구분 표시)
- **FR-22: 수동 운영 액션** — 대상 활성/비활성, Agent 배정, test crawl, dry-run render, test send, job retry, 인증 필요 확인. (test send는 지정 테스트 채널로만, retry는 중복 방지 미우회, 위험 액션은 실행자·시각 기록)
- **FR-23: 상태 심각도 표시** — 수집/전송/Agent/인증 상태를 정상/주의/위험/중지 심각도로 표시. (마지막 성공이 주기 2배 초과 시 warning, 4배 초과 시 critical, 인증 필요·검증 실패·Kakao 오발송 위험은 자동 전송보다 중지 우선)

**⑦ 메시지 채널과 전송 정책 (FR-24~26) — Realizes UJ-3**

- **FR-24: Telegram 중앙 전송** — Telegram 채널 등록/topic ID 관리/test message/sendMessage/결과 기록을 중앙 서버 중심으로. (동일 bot token 다중 프로세스 polling 금지, chat ID+topic ID 조합은 scope에 포함, 전송 실패는 채널별 DeliveryLog 기록)
- **FR-25: KakaoTalk 제한 운영** — Kakao를 무제한/강 SLA 채널로 취급 안 함, queue 지연·계정 제한 가능성·UI 변경·오발송 위험을 정책 반영(제한/best-effort). (전송량·queue lag 표시, 실패를 다른 방으로 자동 복구 금지, lag 기준 초과 시 증설/제한 판단, 공식 채널/API 대안은 후속)
- **FR-26: 채널별 전송 이력** — DeliveryRule/Dispatch Job 전송 이력을 채널별 추적. (같은 Snapshot의 Telegram/Kakao 성공 여부 별도 확인, 실패 채널만 재시도, 이력은 중복 방지 판단에 사용)

**⑧ 마이그레이션과 배포 운영, 운영 안전 보강 (FR-27~34)**

- **FR-27: 단계별 전환** — P0 기준선→P1 ID 모델→P2 수집/전송 분리→P3 Local Agent→P4 중앙 서버 순서로 전환. (P2 이후 기존 UI 1회 실행 결과 동일, P3 이후 Agent가 대표 대상 polling/claim/complete, P4 이후 Admin에서 대상·Agent 상태 확인)
- **FR-28: 현재 PC를 Agent #1로 사용** — 현재 일반 Windows PC를 첫 Local Agent로 사용, 고성능 서버 구매는 지표 기반 증설 이후. (기존 Chrome/Kakao 환경 활용, 증설 판단에 대상 수·평균 수집 시간·Kakao lag·PC 안정성·운영 시간 반영, 단일 서버 몰아넣기 금지)
- **FR-29: 채널 등록/검증/활성화** — Telegram/Kakao 채널을 등록코드·테스트 메시지·확인 절차로 활성화. (Telegram은 chat ID+topic ID 확인 후 대상, Kakao는 고유 방명/동등 정책 통과 후 대상, 테스트 확인 전 DeliveryRule은 운영 전송 미사용)
- **FR-30: 운영자 주도 고객/구독 상태 흐름** — 결제 자동화 없이 setup/인증 대기/채널 검증 대기/테스트 실행/활성/성능 저하/인증 필요/중지 상태 구분. (`ACTIVE`/`AUTH_REQUIRED`/`DEGRADED`/`SUSPENDED` 구분, 중지 시 신규 수집 중단·secret/profile 참조 보존, 복구 시 `HELD` Dispatch는 운영자 확인 후 폐기/재개, 폐기 정책은 상태 모델로 표현 가능)
- **FR-31: 마이그레이션 안전 제약** — 기존 설정·중복 방지 상태 이전 시 데이터 손상·중복 발송·비활성 대상 자동 활성화 방지. (atomic write, 기존 `last_message` → 신규 DeliveryLog/idempotency seed 승계, 비활성 탭 보존·자동 활성화 금지, 로그 rotation/보존)
- **FR-32: Local Agent 실제 실행 조건** — Agent가 실제 Windows 조건에서 수집·Kakao 전송 수행. (Kakao 작업 Agent는 interactive session 필요·Session 0 service-only 금지, 재부팅 후 사용자 로그인 시 자동 시작·heartbeat 복구, crawler-only와 kakao sender Agent 실행 조건·job type 구분)
- **FR-33: Scheduler와 queue 안전장치** — 대상 증가에도 job 폭주·잘못된 Agent 배정·플랫폼 장애 확산 방지. (schedule jitter 검증, platform-wide 장애/parser 실패율 급증 시 circuit breaker, job assignment는 capacity+target/profile affinity 고려, error code별 backoff로 폭주 방지)
- **FR-34: Admin 보안과 복구성** — 관리자 접근·token 폐기·백업/복구 안전 처리. (전 관리자 MFA 기본+VPN/IP allowlist 추가 가능, 최소 역할 viewer/operator/secret-admin/break-glass, Agent token·외부 token revoke/rotate, 운영 DB·진단 산출물 backup/retention/restore rehearsal, 최소 알림 `agent_offline`/`queue_lag`/`api_error_rate`/`auth_required`)

### NonFunctional Requirements

**신뢰성과 안전성 (§6.1)**

- NFR-1: 잘못된 고객/대상/채팅방 발송보다 작업 실패를 선택(fail-closed).
- NFR-2: 필수 실적 데이터 누락 시 메시지를 만들지 않음(`MissingPerformanceDataError` 계승).
- NFR-3: 재시도는 idempotency key + DeliveryLog로 중복 발송 차단(at-least-once 가정, exactly-once 가정 금지).
- NFR-4: 인증 필요 상태는 무한 재시도 금지, 사람 개입 상태로 전환.

**보안과 개인정보 (§6.2)**

- NFR-5: Telegram token, Gmail OAuth token, 쿠팡 비밀번호, 인증번호(OTP), chat ID, topic ID, 고객 식별 정보를 로그·예외 메시지에서 redaction.
- NFR-6: Agent↔서버 통신은 인증된 HTTPS 경로 사용.
- NFR-7: Agent token 유출/만료 시 해당 Agent는 job 미수신(server-side revoke 동작).
- NFR-8: Secret 저장 위치 분류 — 중앙 Secrets Manager(Telegram/Coupang/JWT ref) / Agent-local DPAPI·Credential Manager(Gmail/Agent token/Chrome profile) / 비저장. DB는 `*_ref`만, 평문 금지.
- NFR-9: DB/backup/Windows profile·secret 저장소는 적용 가능 범위 encryption at rest(BitLocker 권장).
- NFR-10: 진단 산출물(screenshot/HTML dump/exception trace/queue payload/실패 메시지)은 retention+scrubbing. Raw HTML 저장 기본 금지(sanitized만), Kakao 스크린샷 업로드는 마스킹/승인.
- NFR-11: 아키텍처는 secret/credential/고객 식별자/메시지 본문/운영 로그/브라우저 프로필/backup/진단 산출물 data inventory 작성.

**운영 관측성 (§6.3)**

- NFR-12: 고객/대상/Agent/채널/job 단위 상태 확인 가능.
- NFR-13: warning/critical 기준은 스케줄 주기·마지막 성공 시각 기준 계산 가능(2배 warning, 4배 critical).
- NFR-14: 7개 모니터링 지표 노출 — `agent_last_heartbeat`(>2분 offline), `target_last_success_at`(×2 warning/×4 critical), `auth_required_count`(≥1 alert), `kakao_queue_lag_seconds`(>120s 반복), `crawl_error_rate_by_platform`(최근 15분 >30%), `telegram_send_error_rate`(최근 10분 급증), `gmail_reauth_required_count`(≥1).
- NFR-15: 장애 원인을 조치 가능 카테고리로 분류 — crawl_failure / auth_required / render_failure / telegram_failure / kakao_failure / duplicate_blocked / target_validation_failure.
- NFR-16: 인증 실패 사유를 조치 가능 유형으로 분류 — token 만료/revoke, 비밀번호 오류, 인증메일 지연, CAPTCHA/이상 로그인, mailbox lock 충돌, 최신 메일 오인식, 반복 인증 요청 루프.
- NFR-17: MVP runbook 최소 포함 — `agent_offline`/`queue_lag`/`api_error_rate`/`auth_required`/`profile_mismatch`/`kakao_ambiguous_room`/`duplicate_blocked`.

**호환성과 마이그레이션 (§6.4)**

- NFR-18: 기존 `runtime/`/`logs/`/`runtime/state/ui_settings.json`/`crawlingN` 상태는 마이그레이션 중 원본 보존.
- NFR-19: 기존 CLI/env 경로와 UI 저장 경로 설정 정책을 의도 없이 섞지 않음.
- NFR-20: 기존 테스트·수동 회귀 시나리오는 각 단계에서 계속 실행 가능.
- NFR-21: 기존 중복 방지 상태(`last_message`)는 신규 idempotency/DeliveryLog 판단으로 승계.
- NFR-22: 마이그레이션 상태 표현 — discovered/mapped/dry-run passed/approved/active/paused/rolled back.
- NFR-23: 전역 dispatch kill switch + tenant/channel 단위 pause 사용 가능.
- NFR-24: old path와 new path 동시 실제 전송 방지(cutover 규칙).
- NFR-25: rollback은 신규 DeliveryRule 비활성화+기존 런타임 경로 복구, 신규 로그는 중복 방지 기록으로 보존.

**성능과 확장 (§6.5)**

- NFR-26: MVP는 최소 100개 fake target scheduling smoke로 job scheduling/queue/상태 추적 동작 입증.
- NFR-27: 실제 수용량은 Chrome 메모리·평균 수집 시간·Kakao 평균 전송 시간·로그인 만료 빈도·Agent 안정성으로 결정.
- NFR-28: 운영 규모는 고객 수보다 모니터링 대상 수 기준 판단(측정 트리거 — CPU>70%/RAM>80%/target>20-30/kakao lag>120s).
- NFR-29: negative safety test 포함 — wrong tenant / wrong profile / wrong Kakao room / stale Agent token / restored DB / double Agent claim / crash-after-send.

### Additional Requirements

**Architecture 결정에서 파생된 기술 요구사항 (스토리 작성에 직접 영향)**

- ADD-1: **신규 프로젝트 스캐폴딩은 첫 구현 스토리** — Cloud backend(FastAPI 표준 `app/` 레이아웃)와 Admin UI는 정식 CLI 스캐폴드가 없으므로 직접 구성을 첫 스토리로 둔다. 빈 DB → 13 테이블 Alembic 마이그레이션이 P4-02 수용 기준.
- ADD-2: **3 패키지 구조** — `src/rider_crawl/`(공유 도메인, 보존), `src/rider_server/`(신규 Cloud), `src/rider_agent/`(신규 Windows Agent). Cloud=async(FastAPI/SQLAlchemy), Agent=sync(Playwright/PC 자동화), 두 런타임은 HTTP(JSON)로만 통신.
- ADD-3: **고정 기술 스택** — FastAPI 0.136.x, SQLAlchemy 2.x(async), Alembic 1.18.x, PostgreSQL 18(AWS RDS), Pydantic v2(API 경계), Playwright 1.60.0 고정, crawl4ai 0.8.7 고정. 임의 업그레이드 금지.
- ADD-4: **Queue backend = PostgreSQL** — `jobs` 테이블 + `FOR UPDATE SKIP LOCKED`, `QueueBackend` 인터페이스로 추상화(추후 Redis/SQS 교체 보장). Redis 미도입, 별도 캐시 레이어 없음. idempotency가 DB 트랜잭션과 한 곳.
- ADD-5: **Job 의미론** — at-least-once + DB 레벨 idempotency 유니크 제약(`uq_delivery_logs_dedup_key`). claim 시 lease 만료시각 부여, heartbeat로 연장, 만료 시 stale 회수. insert-then-send(성공 전송 전 dedup key 유니크 확보).
- ADD-6: **Agent API 5종** — `POST /v1/agents/register`, `POST /v1/agents/heartbeat`, `POST /v1/jobs/claim`, `POST /v1/jobs/{job_id}/events`, `POST /v1/jobs/{job_id}/complete`. token-auth, outbound-only. job 이벤트/complete 본문에 secret/OTP 금지.
- ADD-7: **13 테이블 정본** — tenants, subscriptions, platform_accounts, monitoring_targets, browser_profiles, messenger_channels, delivery_rules, snapshots, messages, delivery_logs, agents, jobs, auth_sessions, audit_logs. (data-api-contract.md의 required fields와 네이밍 정본 준수)
- ADD-8: **DB/API 네이밍 정본** — 테이블 복수 snake_case, 컬럼 snake_case, PK `id`(UUID), FK `<entity>_id`, 시각 `_at`(timezone-aware), 상태 대문자 enum 문자열, 인덱스 `ix_*`/유니크 `uq_*`, secret은 `*_ref`만. API 경로 `/v1/` 복수 명사, JSON 필드 snake_case(camelCase 변환 금지).
- ADD-9: **3 상태머신 정본** — (a) Customer lifecycle(LEAD→…→ACTIVE/DEGRADED/AUTH_REQUIRED/SUSPENDED), (b) Subscription execution gate(PAYMENT_ACTIVE/PAYMENT_FAILED_GRACE/SUSPENDED/CANCELLED), (c) Baemin auth state(UNKNOWN/ACTIVE/AUTH_REQUIRED/USER_ACTION_PENDING/AUTH_VERIFIED/CENTER_MISMATCH/BLOCKED_OR_CAPTCHA). 상태 전이는 service 레이어에서만.
- ADD-10: **Admin UI = FastAPI + Jinja2 + HTMX** 서버 렌더링(별도 JS 빌드 파이프라인 없음, 백엔드와 동일 인증/세션). 상태 심각도 계산은 서버.
- ADD-11: **Telegram = 중앙 webhook + secret header 검증** + `/register <code>`. Agent별 getUpdates polling 제거(token 큐 경합 방지). chat_id + optional message_thread_id 자동 저장.
- ADD-12: **인프라/배포** — AWS Seoul, EC2 Ubuntu LTS + Docker Compose(backend-api/scheduler/telegram-dispatcher/admin-ui), S3(sanitized artifact만), CloudWatch + 7 지표. Agent는 기존 PyInstaller onefile 재활용 + 버전 manifest + rollback 바이너리. CI: lint+test+build, DB 마이그레이션은 백업 확인 후.
- ADD-13: **에러 응답 포맷** — `{"error": {"code": "<UPPER_SNAKE>", "message_redacted": "..."}}`, 의미 있는 HTTP 상태(400/401/403/404/409/422/429/503). 목록은 `{"items":[...], "next_cursor":...}`. 시각 ISO 8601 UTC.
- ADD-14: **직렬화 경계** — Agent 내부 도메인=dataclass, API 경계=Pydantic v2, 교차 시 명시적 변환 함수(`to_api_model`/`from_api_model`). Snapshot.normalized_json은 안정적 키 + parser_version 포함.
- ADD-15: **금지 행위(forbidden)** — 탭 9→100 확장으로 스케일링 금지, 배민 휴대폰 인증 자동/우회 금지, 같은 Windows session에서 Kakao 2건 병렬 전송 금지, 고객 간 Gmail token 공유 금지, secret을 로그/DB text/스크린샷/config/에러 메시지에 저장 금지, 클라우드가 로컬 Chrome CDP 직접 접속 금지, parser 실패 backoff/circuit breaker 없이 빠른 재시도 금지.
- ADD-16: **마이그레이션 계약** — 기존 `ui_settings.json` 백업 → 활성 탭만 target 후보 분류 → tenant_id/platform_account_id/monitoring_target_id 발급 → `runtime/state/crawlingN` → `targets/<monitoring_target_id>` 복사(원본 미삭제) → `last_message` hash로 DeliveryLog dedup seed → 실발송 끈 dry-run → 기존/신규 메시지 비교 → 운영자 승인 후 DeliveryRule 활성화.

### UX Design Requirements

해당 없음 — 이 프로젝트는 별도 UX Design Specification이 없다. Admin UI는 운영자 전용 내부 도구로, Architecture 결정상 FastAPI + Jinja2 + HTMX 서버 렌더링(SEO 불필요, JS 빌드 파이프라인 없음)이다. UI 관련 요구사항은 FR-21~23(대시보드/수동 액션/심각도 표시)과 ADD-10(서버 렌더링 결정)에 포함되어 있다.

### FR Coverage Map

각 FR이 어느 에픽에서 다뤄지는지 매핑(누락 방지). NFR/ADD는 교차 관심사로 여러 에픽에 걸쳐 적용되며, 1차 책임 에픽을 괄호로 표기한다.

- **FR-1**: Epic 1 — 기존 동작 기준선(branch/tag·settings 백업·pytest 결과) 저장
- **FR-2**: Epic 1 — 기존 parser/renderer/sender/Gmail 2FA/테스트 재사용 보장
- **FR-3**: Epic 3 — 신규 경로 dry-run 비교(실발송 없음, 승인 전 차이 확인) ※ Epic 1에서 절차 문서화, Epic 3에서 실제 dry-run 경로 구현
- **FR-4**: Epic 2 — 고객/구독/플랫폼계정/대상/채널/규칙 ID 기반 CRUD(도메인 모델·soft delete) ※ Admin 엔티티 생성/편집 CRUD UI는 Epic 5 Story 5.11
- **FR-5**: Epic 2 — legacy alias 보존
- **FR-6**: Epic 2 — 구독 상태에 따른 작업 제어(SubscriptionGate) ※ scheduler 연동은 Epic 5
- **FR-7**: Epic 3 — CrawlJob → Snapshot 생성, 필수 데이터 누락 시 실패
- **FR-8**: Epic 3 — Message 렌더링 분리(template_version, text_hash)
- **FR-9**: Epic 3 — DeliveryRule fan-out(1 대상 → N 채널)
- **FR-10**: Epic 3 — 중복 발송 방지(DeliveryLog + idempotency key)
- **FR-11**: Epic 3 — 재시도/실패 상태 관리(재시도 가능 vs 사람 개입) ※ backoff/circuit breaker는 Epic 5
- **FR-12**: Epic 4 — Agent 등록 + heartbeat
- **FR-13**: Epic 4 — Agent job polling/claim/complete(lease, 중복 claim 방지)
- **FR-14**: Epic 4 — Browser Profile/CDP 격리, 기대 센터/상점명 검증
- **FR-15**: Epic 4 — KakaoTalk 직렬 queue + 정확한 채팅방 검증
- **FR-16**: Epic 4 — outbound-only Agent 통신
- **FR-17**: Epic 4 — 배민 인증 필요 감지(AUTH_REQUIRED 전환)
- **FR-18**: Epic 4 — 사람 개입형 배민 재인증(우회 금지)
- **FR-19**: Epic 4 — 쿠팡 Gmail 2FA 고객/메일함/token 분리 + mailbox lock
- **FR-20**: Epic 4 — 플랫폼 대상 검증(기대 대상 불일치 시 전송 중단)
- **FR-21**: Epic 5 — 운영 대시보드(상태/마지막 성공/queue lag/오류)
- **FR-22**: Epic 5 — 수동 운영 액션(test crawl/dry-run/test send/retry/인증 확인)
- **FR-23**: Epic 5 — 상태 심각도 표시(정상/주의/위험/중지, 2배/4배 기준)
- **FR-24**: Epic 3 — Telegram 중앙 전송(webhook/sendMessage/채널별 로그) ※ 채널 등록 UI/`/register`는 Epic 5
- **FR-25**: Epic 4 — KakaoTalk 제한/best-effort 운영(queue lag, 자동 복구 금지)
- **FR-26**: Epic 3 — 채널별 전송 이력(실패 채널만 재시도)
- **FR-27**: Epic 1~5 전반 — 단계별 전환(P0→P4) ※ 본 에픽 구조 자체가 FR-27 실현, cutover 규칙은 Epic 3/5
- **FR-28**: Epic 4 — 현재 PC를 Agent #1로 사용, 지표 기반 증설
- **FR-29**: Epic 5 — 채널 등록/검증/활성화(테스트 확인 전 운영 전송 금지) ※ Telegram `/register`·Kakao 방명 검증
- **FR-30**: Epic 2 + Epic 5 — 운영자 주도 고객/구독 상태 흐름(상태 모델=Epic 2, Admin 상태 전이 UI=Epic 5)
- **FR-31**: Epic 2 — 마이그레이션 안전 제약(atomic write, last_message seed 승계, 비활성 자동 활성화 금지, 로그 rotation)
- **FR-32**: Epic 4 — Local Agent 실제 실행 조건(interactive session, autostart, crawler/kakao 구분)
- **FR-33**: Epic 5 — Scheduler/queue 안전장치(jitter, circuit breaker, capacity/affinity, backoff)
- **FR-34**: Epic 5 — Admin 보안/복구성(MFA, 4역할, token revoke/rotate, backup/restore, 최소 알림)

**NFR 1차 책임 매핑(교차 적용):** NFR-1~4 신뢰성(Epic 3) · NFR-5~11 보안/secret(Epic 1 redaction 기반 + Epic 2 secret_ref + Epic 5 Admin/저장) · NFR-12~17 관측성(Epic 5) · NFR-18~25 호환/마이그레이션(Epic 1 baseline + Epic 2 migration + Epic 3 cutover) · NFR-26~29 성능/negative test(Epic 5 부하 smoke + 각 에픽 negative test).

**ADD 1차 책임 매핑:** ADD-1 스캐폴딩(Epic 5 server, Epic 4 agent) · ADD-2~3 패키지/스택(Epic 4·5) · ADD-4~6 queue/job/Agent API(Epic 4 claim + Epic 5 server queue) · ADD-7~9 테이블/네이밍/상태머신(Epic 2 도메인 + Epic 5 DB) · ADD-10~13 Admin/Telegram/인프라/에러포맷(Epic 5) · ADD-14 직렬화(Epic 3·4) · ADD-15 금지행위(전 에픽 가드레일) · ADD-16 마이그레이션 계약(Epic 2).

## Epic List

이 제품은 **동작 보존 우선 브라운필드 리팩토링**이며, Architecture/spec이 P0→P4 마이그레이션 단계를 risk boundary로 확정했다. 따라서 에픽은 단계 정렬을 따르되, 각 에픽은 운영자 관점의 **독립적 운영 가치**를 전달하고 다음 에픽을 가능케 한다(within-epic 미래 의존성 없음). 5개 MVP 에픽 + 2개 후속(post-MVP) 에픽으로 구성한다.

### Epic 1: 기준선 안전망 — 리팩토링해도 기존 운영이 깨지지 않는다 (P0)

운영자는 리팩토링을 시작하기 전에 현재 잘 동작하는 배민/쿠팡 운영 상태를 안전하게 고정하고, 이후 어떤 단계에서든 회귀를 감지·복구할 수 있다. branch/tag 기준선, settings 백업, pytest 결과 보관, 공용 redaction 유틸 검증, 기존 2탭 회귀 시나리오 문서화를 통해 "되돌릴 수 있고, 무엇이 바뀌었는지 알 수 있는" 안전망을 만든다. 이 에픽은 기존 자산을 보존·재사용 대상으로 못박아 이후 모든 에픽의 토대가 된다.
**FRs covered:** FR-1, FR-2 (+ FR-3 절차 문서화, FR-27 P0 단계). **NFR/ADD:** NFR-5(redaction 기반), NFR-18·20(원본 보존·기존 테스트 유지), ADD-15(금지행위 가드레일 명시).

### Epic 2: ID 기반 운영 모델과 안전한 마이그레이션 — 탭 번호가 아니라 고객/대상 ID로 본다 (P1)

운영자는 `크롤링N` 탭 번호 대신 안정적인 ID(고객/구독/플랫폼계정/모니터링대상/채널/전송규칙)로 운영 대상을 식별·관리하고, 기존 활성 탭을 원본 손실·중복 발송·비활성 대상 자동 활성화 없이 새 ID 모델로 옮긴다. 도메인 모델·상태(enum)·legacy alias·atomic write·secret_ref 분리·`last_message` seed 승계를 갖춰, 고객이 늘어도 상태가 꼬이지 않는 운영 식별 체계를 완성한다.
**FRs covered:** FR-4, FR-5, FR-6, FR-31 (+ FR-30 상태 모델 부분). **NFR/ADD:** NFR-8(secret_ref), NFR-19·21·22(설정 정책 분리·seed 승계·마이그레이션 상태), ADD-7~9(13 도메인 모델·네이밍·3 상태머신), ADD-16(마이그레이션 계약).

### Epic 3: 한 번 수집 → 안전하게 여러 채널로 — 수집/렌더/전송 분리와 중복 방지 (P2)

운영자는 한 번 수집한 실적 Snapshot에서 메시지를 렌더링하고, 연결된 여러 채널(Telegram/Kakao)로 fan-out하되 중복 발송 없이 채널별 성공/실패를 따로 추적할 수 있다. `run_once`를 CrawlService/MessageRenderService/DispatchService로 분리하고, DeliveryLog + idempotency 유니크 제약으로 crash-after-send에도 중복을 막으며, 실발송 없는 dry-run으로 기존/신규 메시지를 비교한다. Telegram 중앙 전송도 이 단계에서 도입한다.
**FRs covered:** FR-7, FR-8, FR-9, FR-10, FR-11, FR-24, FR-26, FR-3 (실제 dry-run 경로). **NFR/ADD:** NFR-1~4(fail-closed·idempotency), NFR-24·25(cutover·rollback), ADD-5(at-least-once+lease seed), ADD-11(Telegram webhook), ADD-14(직렬화 경계).

### Epic 4: Windows Local Agent — Chrome·KakaoTalk·인증을 로컬에서 안전하게 처리한다 (P3)

작업 노드 관리자는 현재 Windows PC를 Agent #1로 등록해, 중앙 서버에 outbound-only로 job을 polling/claim/complete하면서 실제 배민/쿠팡 수집과 KakaoTalk 직렬 전송을 수행한다. Browser Profile/CDP 격리, 기대 센터/상점명 검증, 배민 사람 개입형 재인증, 쿠팡 Gmail 2FA 메일함 분리·lock, KakaoTalk FIFO 직렬 전송·방명 검증, 재부팅 후 autostart까지 — 로컬 제약을 우회하지 않고 안전하게 다루는 Agent를 완성한다.
**FRs covered:** FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, FR-18, FR-19, FR-20, FR-25, FR-28, FR-32. **NFR/ADD:** NFR-2·4(미발송·인증 무한재시도 금지), NFR-6·7·9(HTTPS·token revoke·encryption), NFR-16(인증 실패 분류), ADD-4·6(PostgreSQL queue claim·Agent API), ADD-15(금지행위).

### Epic 5: 중앙 서버와 운영자 Admin — 한곳에서 보고, 제어하고, 안전하게 운영한다 (P4 + 운영 안전 보강)

운영자는 FastAPI 중앙 서버와 Jinja2+HTMX Admin UI에서 고객/대상/Agent/채널/job 상태와 마지막 성공·실패·queue lag·인증 필요를 한곳에서 보고, 대상 활성화·test send·retry·채널 등록/검증·인증 확인 같은 운영 액션을 수행한다. PostgreSQL 13 테이블·Alembic 마이그레이션, jitter·circuit breaker·capacity 기반 scheduler, Telegram `/register` webhook, 7개 모니터링 지표, MFA·4역할·audit log·token revoke·backup/restore까지 — 관측성·제어·보안·복구성을 갖춘 운영 통제면을 완성한다. 100 fake target scheduling smoke로 확장성을 입증한다.
**FRs covered:** FR-21, FR-22, FR-23, FR-29, FR-33, FR-34 (+ FR-4 Admin 엔티티 생성/편집 CRUD UI, FR-30 Admin 상태 전이 UI, FR-3/FR-24 채널/dry-run 운영 UI, FR-6 scheduler 게이트 연동, FR-27 P4 단계). **NFR/ADD:** NFR-10·11(진단 산출물·data inventory), NFR-12~17(관측성·지표·runbook), NFR-23(kill switch·pause), NFR-26~29(100 target smoke·negative test), ADD-1·10·12·13(스캐폴딩·Admin·인프라·에러포맷).

---

### Post-MVP 에픽 (참고 — 본 MVP 범위 밖, PRD §5.8/spec P5~P6)

> 아래 두 에픽은 PRD가 MVP(P0~P4) 이후 후속 범위로 명시한 항목이다. MVP 에픽에 끌어올린 안전장치(FR-29~34)와 구분하기 위해 별도로 기록하되, **스토리 생성 대상은 Epic 1~5(MVP)에 한정**한다.

### Epic 6 (Post-MVP): 온보딩과 인증 고도화 (P5)

운영자/고객이 tenant 생성·setup code·plan quota 설정, 플랫폼 계정 다중 대상, Telegram `/register <code>` 자동 등록, Kakao 방명 검증, 배민/쿠팡 인증 흐름, ACTIVE 전 test crawl·전채널 test message 강제까지 갖춘 고도화된 온보딩 경험.
**참고 FR/계약:** PRD §5.9 일부, spec P5(Onboarding And Authentication).

### Epic 7 (Post-MVP): 대량 운영 안정화 (P6)

schedule jitter·exponential backoff·platform circuit breaker·parser canary·worker/sender sharding·queue lag alert·version rollout·customer impact report로 100+ 대상 대량 운영에서 job storm/중복 발송 없이 서비스 degrade.
**참고 FR/계약:** PRD §5.8 후속, spec P6(Operations Hardening). ※ MVP Epic 5에 최소 jitter/circuit breaker(FR-33)는 이미 포함, 본 에픽은 sharding·canary·rollout 고도화.

## Epic 1: 기준선 안전망 — 리팩토링해도 기존 운영이 깨지지 않는다 (P0)

운영자는 리팩토링을 시작하기 전에 현재 잘 동작하는 배민/쿠팡 운영 상태를 안전하게 고정하고, 이후 어떤 단계에서든 회귀를 감지·복구할 수 있다. branch/tag 기준선, settings 백업, pytest 결과 보관, 공용 redaction 유틸 검증, 기존 2탭 회귀 시나리오 문서화를 통해 "되돌릴 수 있고, 무엇이 바뀌었는지 알 수 있는" 안전망을 만든다. (spec P0-01~05, FR-1·2·3 절차, FR-27 P0)

### Story 1.1: 기준선 branch/tag와 설정 백업 생성

As a 운영자,
I want 리팩토링을 시작하기 전에 현재 동작하는 코드와 설정을 branch/tag와 백업으로 고정하고 싶다,
So that 이후 어떤 단계에서도 깨끗하게 되돌릴 수 있는 기준점이 남는다.

**Acceptance Criteria:**

**Given** 현재 정상 동작하는 main(또는 배포) 브랜치와 로컬 `runtime/state/ui_settings.json`, `config.json`, `.env.example`이 존재할 때
**When** 운영자가 기준선 고정 절차(P0-01)를 수행하면
**Then** `baseline-local-ui-YYYYMMDD` 형식의 git tag가 생성되고
**And** 현재 설정/상태 폴더의 백업 zip이 존재하며
**And** tag와 백업 zip의 위치가 `docs/qa/`(또는 동등 위치)에 기록된다.

**Given** 민감값이 포함된 실제 설정 파일을 문서화해야 할 때
**When** `ui_settings.json`, `config.json`, `.env.example`의 sanitized 샘플을 작성하면(P0-02)
**Then** 토큰·비밀번호·chat_id·OTP 등 민감값이 placeholder로 대체된 sanitized config 샘플이 `docs/`에 존재하고
**And** 어떤 실제 secret 값도 문서·git에 커밋되지 않는다(NFR-5, ADD-15).

**Given** 기존 원본 상태를 보존해야 할 때
**When** 백업을 생성하면
**Then** 기존 `runtime/`, `logs/`, `runtime/state/ui_settings.json`, `crawlingN` 상태 폴더 원본이 삭제·변형되지 않고 그대로 유지된다(NFR-18, FR-1).

### Story 1.2: pytest 기준선 실행과 결과 분류·보관

As a 개발자,
I want 리팩토링 시작 시점의 전체 pytest 결과를 실행하고 분류해 보관하고 싶다,
So that 이후 단계에서 어떤 테스트가 새로 깨졌는지(회귀)를 기준선과 비교해 판단할 수 있다.

**Acceptance Criteria:**

**Given** 기존 `tests/` 구조와 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`)가 있을 때
**When** 전체 pytest를 실행하면(P0-03)
**Then** 통과/실패/스킵이 분류된 테스트 리포트가 `docs/qa/` 아래에 저장되고
**And** 리포트에 실행 일시, Python 버전, 실행 환경이 기록된다.

**Given** 기존 테스트가 기준선의 일부로 취급될 때
**When** 리팩토링 이후 같은 테스트를 다시 실행하면
**Then** 기준선에서 통과하던 테스트는 계속 실행 가능해야 하며(NFR-20, FR-2)
**And** 기준선 대비 새로 실패한 테스트는 회귀 후보로 식별된다.

**Given** 일부 기존 테스트가 이미 실패 상태일 수 있을 때
**When** 리포트를 분류하면
**Then** "기준선에서 이미 실패하던 테스트"와 "리팩토링이 깨면 안 되는 통과 테스트"가 구분되어 기록된다.

### Story 1.3: 공용 redaction 유틸 추가 및 검증

As a 개발자,
I want 토큰·비밀번호·OTP 등 민감값을 로그·예외 메시지에서 가리는 공용 redaction 유틸을 갖추고 싶다,
So that 이후 모든 신규 Cloud/Agent 코드가 일관된 마스킹 정책을 재사용해 민감값 노출을 막을 수 있다.

**Acceptance Criteria:**

**Given** 신규 redaction 유틸이 필요할 때
**When** `src/rider_crawl/redaction.py`에 공용 redaction 함수를 추가하거나 기존 유틸을 검증하면(P0-04)
**Then** password, token, refresh token, authorization code, OTP, full phone number, full email이 출력에서 마스킹되고
**And** 해당 동작을 검증하는 redaction 단위 테스트가 통과한다(NFR-5).

**Given** redaction 유틸이 로그/예외 경계에 쓰일 때
**When** 민감값을 포함한 문자열을 redaction에 통과시키면
**Then** 마스킹된 결과에는 어떤 원본 secret 부분 문자열도 남지 않고
**And** 고객명·센터명 같은 운영 식별자는 정책에 따라 보존되거나 마스킹 옵션으로 처리된다(operations-security-test-contract Log And Artifact Redaction).

**Given** 에러 이벤트 포맷이 필요할 때
**When** 에러를 기록하면
**Then** `message_redacted`/`error_message_redacted` 형태로 남길 수 있는 헬퍼가 제공된다(ADD-6, ADD-13).

### Story 1.4: 기존 2탭 수동 회귀 시나리오 문서화

As a 운영자,
I want 현재 활성 배민 1탭·쿠팡 1탭에 대한 수동 회귀·dry-run 비교 절차를 문서로 갖추고 싶다,
So that 리팩토링 각 단계 후 동일 절차를 반복해 기존 동작 보존 여부를 검증할 수 있다.

**Acceptance Criteria:**

**Given** 현재 활성 배민 대상 1개와 쿠팡 대상 1개가 있을 때
**When** 수동 회귀 시나리오를 문서화하면(P0-05, FR-1)
**Then** 배민 run, 쿠팡 run, Telegram 테스트 전송, Kakao 테스트 전송 절차가 단계별로 `docs/qa/`(또는 runbook)에 기록되고
**And** 각 절차의 기대 결과(수집 성공·렌더링된 메시지 형태)가 함께 명시된다.

**Given** 기준선 수집/렌더링 결과를 남겨야 할 때
**When** 대표 배민/쿠팡 대상의 수집·렌더링 dry-run을 1회 실행하면
**Then** 렌더링된 메시지 결과(또는 그 hash)가 기준선으로 저장되고
**And** 이 dry-run은 실제 Telegram/Kakao 발송을 하지 않는다(FR-3).

**Given** 향후 단계에서 메시지 차이를 비교해야 할 때
**When** 문서화된 절차를 다시 수행하면
**Then** 기준선 메시지와 신규 메시지를 비교할 수 있는 형태(저장 위치·비교 방법)가 절차에 포함된다(FR-3, NFR-24).

### Story 1.5: 기존 자산 재사용 경계와 금지 행위 명문화

As a 개발자,
I want 어떤 기존 코드가 보존·재사용 대상인지와 절대 하면 안 되는 행위를 한 문서로 못박고 싶다,
So that 이후 모든 에픽의 구현 에이전트가 동작 보존 경계를 흔들지 않고 일관되게 작업할 수 있다.

**Acceptance Criteria:**

**Given** 보존해야 할 기존 자산이 있을 때
**When** 재사용 경계를 문서화하면(FR-2, implementation-contract Reuse And Replace)
**Then** 배민 parser/crawler, 쿠팡 parser, message renderer, Telegram/Kakao sender, 쿠팡 Gmail 2FA, `run_once` 경계, platforms/messengers registry가 "보존·wrapping 재사용 대상"으로 명시되고
**And** 기존 공개 동작(렌더링 결과, 저장 JSON 호환, 탭 9개 로딩, 쿠팡 추론)을 의도 없이 바꾸는 변경은 실패로 취급한다고 기록된다.

**Given** 금지 행위를 명시해야 할 때
**When** 금지 목록을 문서화하면(ADD-15, operations-security-test-contract Forbidden Behaviors)
**Then** 탭 9→100 확장 스케일링, 배민 휴대폰 인증 자동/우회, 같은 Windows session Kakao 병렬 전송, 고객 간 Gmail token 공유, secret 평문 저장(로그/DB text/스크린샷/config/에러), 클라우드의 로컬 CDP 직접 접속, backoff/circuit breaker 없는 빠른 parser 재시도가 금지 행위로 나열된다.

**Given** 이 문서가 권위 기준으로 쓰여야 할 때
**When** 구현 에이전트가 작업을 시작하면
**Then** 문서는 `project-context.md` 56개 규칙을 최우선 상위 권위로 참조하도록 안내하고
**And** 위반 발견 시 architecture.md ADR 또는 project-context.md에 예외를 기록하도록 지시한다.

## Epic 2: ID 기반 운영 모델과 안전한 마이그레이션 — 탭 번호가 아니라 고객/대상 ID로 본다 (P1)

운영자는 `크롤링N` 탭 번호 대신 안정적인 ID로 운영 대상을 식별·관리하고, 기존 활성 탭을 원본 손실·중복 발송·비활성 대상 자동 활성화 없이 새 ID 모델로 옮긴다. 도메인 모델·상태(enum)·legacy alias·atomic write·secret_ref 분리·`last_message` seed 승계를 갖춘다. (spec P1-01~06, 마이그레이션 계약, FR-4·5·6·31, FR-30 상태 모델, ADD-7~9·16). 참고: 이 에픽은 도메인 모델(dataclass/Enum)과 `src/rider_crawl` 설정 진화를 다루며, PostgreSQL 13 테이블·Alembic 생성은 Epic 5(P4-02)에서 한다.

### Story 2.1: UiSettings에 고객/대상 ID 부여와 legacy alias 보존

As a 운영자,
I want 기존 탭별 설정에 고객 ID·플랫폼 계정 ID·모니터링 대상 ID를 붙이고 기존 탭명은 legacy alias로 남기고 싶다,
So that 이후 운영을 탭 번호가 아니라 안정적인 ID로 추적하면서 기존 탭과의 연결도 잃지 않는다.

**Acceptance Criteria:**

**Given** 기존 `runtime/state/ui_settings.json`(최대 9탭)이 있을 때
**When** UiSettings에 `customer_id`, `customer_name`, `platform_account_id`, `monitoring_target_id`를 추가하고 기존 탭명을 `legacy_alias`로만 보존하면(P1-01, FR-5)
**Then** 기존 `ui_settings.json`이 자동으로 마이그레이션되어 로드되고
**And** 기존 탭명은 표시명/보조 식별자로만 쓰이며 내부 주 식별자로 쓰이지 않는다
**And** 저장 JSON은 `ensure_ascii=False, indent=2` 스타일을 유지한다.

**Given** 기존 9탭 로딩 호환을 깨면 안 될 때
**When** `UiSettingsStore.load_all(max_tabs=9)`로 로드하면
**Then** 9탭 로딩과 기존 legacy 카카오 설정·쿠팡 플랫폼 추론이 깨지지 않고
**And** 기존 저장 JSON 호환 테스트가 통과한다(NFR-19, project-context 테스트 규칙).

**Given** ID가 없던 기존 설정을 처음 로드할 때
**When** 마이그레이션이 ID를 발급하면
**Then** 같은 탭은 재로드 시 동일한 ID를 안정적으로 유지한다.

### Story 2.2: 대상별 상태 경로 분리와 atomic write·로그 rotation

As a 운영자,
I want 런타임 상태 경로를 `crawlingN`에서 `targets/<monitoring_target_id>`로 바꾸고 설정 저장을 atomic하게, 로그를 rotation하게 만들고 싶다,
So that 탭 순서를 바꾸거나 앱이 강제 종료돼도 다른 대상의 상태나 중복 방지 기록이 섞이거나 손상되지 않는다.

**Acceptance Criteria:**

**Given** 기존 `runtime/state/crawlingN` 순번 기반 상태 폴더가 있을 때
**When** `state_subdir`를 `targets/<monitoring_target_id>`로 변경하면(P1-02)
**Then** 탭 표시 순서를 바꿔도 `last_message`나 `run_lock`이 다른 대상과 섞이지 않고
**And** 상태 식별이 더 이상 `crawlingN` 순번에 의존하지 않는다(ADD anti-pattern 회피).

**Given** 설정 저장 중 강제 종료가 발생할 수 있을 때
**When** 설정을 atomic write(temp 파일 → fsync → rename)로 저장하면(P1-03, FR-31)
**Then** 강제 종료 테스트에서도 JSON이 손상되지 않고 직전 유효 상태가 보존된다.

**Given** `run_errors.log`, `kakao_diagnostics.log`가 계속 커질 수 있을 때
**When** 로그 rotation을 추가하면(P1-04, NFR-10)
**Then** 로그가 크기 또는 날짜 기준으로 rotation되고 보존 기준이 적용된다.

### Story 2.3: 플랫폼 중립 Target 필드 통일

As a 개발자,
I want 배민과 쿠팡이 같은 모니터링 대상 모델을 쓰도록 플랫폼 중립 필드를 통일하고 싶다,
So that 플랫폼별로 갈라진 식별 필드 없이 하나의 Target 모델로 대상을 다룰 수 있다.

**Acceptance Criteria:**

**Given** 현재 배민/쿠팡이 서로 다른 필드(예: `baemin_center_name`, `coupang_eats_url`)를 쓸 때
**When** 플랫폼 중립 필드 `center_name`, `display_name`, `target_external_id`, `primary_url`을 도입하면(P1-05)
**Then** 배민과 쿠팡이 동일한 Target 모델을 사용하고
**And** 쿠팡의 기대 센터/상점명 검증이 `center_name`을 통해 유지된다(FR-20 연계).

**Given** 기존 공개 경계 이름 호환을 우선해야 할 때
**When** 신규 중립 필드를 도입하면
**Then** 기존 `coupang_eats_url`·`baemin_center_name` 등은 넓은 변경 없이 legacy alias로 매핑되고 이름만 임의로 바뀌지 않는다(ADD-8, project-context 규칙).

**Given** 쿠팡 기대 센터/상점명이 비었거나 배민 기본값일 때
**When** Target을 검증하면
**Then** 해당 대상은 위험 상태로 분류될 수 있는 필드 상태를 가진다(FR-20 토대, 실제 차단은 Epic 4).

### Story 2.4: secret 값 분리 — 설정 파일은 ref만 보관

As a 보안 담당 개발자,
I want 토큰·비밀번호 같은 secret을 일반 설정에서 분리하고 UI/설정 JSON에는 참조(ref)만 남기고 싶다,
So that 설정 파일이나 백업에 평문 secret이 남지 않아 유출 위험을 줄인다.

**Acceptance Criteria:**

**Given** 기존 설정에 토큰/비밀번호가 섞여 저장될 수 있을 때
**When** secret 값을 일반 설정에서 분리하고 설정 JSON에는 ref만 남기면(P1-06, NFR-8)
**Then** 신규 설정 파일에는 원본 token/password가 들어가지 않고 `*_ref` 형태만 존재하며
**And** 기존 설정 마이그레이션 시에도 평문 secret이 신규 파일로 복사되지 않는다(ADD-15 금지행위).

**Given** secret 저장 위치 분류가 필요할 때
**When** secret 저장 정책을 적용하면
**Then** 저장 위치가 중앙 secret store / Agent-local DPAPI·Credential Manager / 비저장 중 하나로 분류되고(NFR-8)
**And** DB/로그/스크린샷/config에 평문 secret을 두지 않는다는 규칙이 적용된다.

### Story 2.5: 핵심 도메인 모델과 상태 enum 정의

As a 개발자,
I want 고객·구독·플랫폼계정·대상·채널·전송규칙 등 핵심 도메인 모델과 상태 enum을 정의하고 싶다,
So that 이후 수집/전송/Agent/서버 에픽이 동일한 ID 기반 도메인 모델과 상태값 정본을 공유한다.

**Acceptance Criteria:**

**Given** ID 기반 운영 모델이 필요할 때
**When** 도메인 모델을 dataclass/Enum로 정의하면(ADD-7, FR-4)
**Then** Tenant, Subscription, PlatformAccount, MonitoringTarget, BrowserProfile, MessengerChannel, DeliveryRule, SecretRef가 data-api-contract의 계약(필드·관계)에 맞게 정의되고
**And** 모니터링 대상은 플랫폼·계정·기대 센터/상점명·URL/식별자·연결된 브라우저 프로필을 가지며
**And** 전송 규칙은 하나의 대상에서 하나 이상의 채널로 연결될 수 있다(FR-9 토대).

**Given** 상태값이 코드/DB/API에서 일관돼야 할 때
**When** 상태 enum을 정의하면(ADD-9, FR-30)
**Then** Customer lifecycle(LEAD→…→ACTIVE/DEGRADED/AUTH_REQUIRED/SUSPENDED)과 Baemin auth state(UNKNOWN/ACTIVE/AUTH_REQUIRED/USER_ACTION_PENDING/AUTH_VERIFIED/CENTER_MISMATCH/BLOCKED_OR_CAPTCHA)가 대문자 enum 문자열로 정의되고
**And** `ACTIVE`/`AUTH_REQUIRED`/`DEGRADED`/`SUSPENDED`가 MVP에서 구분된다.

**Given** 삭제 대신 비활성화를 지원해야 할 때
**When** 대상/채널/규칙을 비활성화하면
**Then** soft delete 또는 inactive 상태로 운영 이력이 보존되고 물리 삭제되지 않는다(FR-4).

### Story 2.6: 구독 실행 게이트 — 중지 고객의 작업 차단

As a 운영자,
I want 고객 구독 상태가 작업 실행 가능 상태인지 확인하고 중지된 고객의 신규 작업을 막고 싶다,
So that 결제 실패·수동 중지 고객에게 실수로 실적이 계속 수집·발송되지 않는다.

**Acceptance Criteria:**

**Given** 고객 구독 상태가 실행 게이트로 쓰일 때
**When** SubscriptionGate가 구독 상태를 평가하면(FR-6, ADD-9 Subscription execution gate)
**Then** `ACTIVE`/`PAYMENT_ACTIVE`가 아닌 고객은 신규 CrawlJob이 예약되지 않고
**And** `PAYMENT_FAILED_GRACE`는 수집/전송을 계속하되 Admin 경고를 표시할 수 있으며
**And** `SUSPENDED`는 신규 CrawlJob/DispatchJob 생성을 멈추되 설정·secret/profile 참조를 보존한다.

**Given** 중지 고객의 미전송 작업이 있을 때
**When** 고객이 `SUSPENDED`로 전환되면
**Then** 미전송 Dispatch Job은 기본적으로 `HELD` 상태로 전환되어 자동 발송되지 않고
**And** 이미 성공 기록된 Dispatch Job은 구독 상태 변경 후에도 재전송되지 않는다(FR-6).

**Given** 중지에서 복구할 때
**When** 고객이 `SUSPENDED`에서 `ACTIVE`로 복구되면
**Then** `HELD` Dispatch Job은 운영자 확인 후 폐기 또는 재개 중 하나로 처리될 수 있고
**And** 중지 사유와 마지막 상태 변경 시각이 상태 모델에 기록된다(FR-30).

### Story 2.7: 기존 탭 설정의 안전한 마이그레이션 실행

As a 운영자,
I want 기존 활성 탭을 백업·분류·ID 발급·상태 복사·중복방지 seed 승계 절차로 새 ID 모델로 옮기고 싶다,
So that 마이그레이션 과정에서 원본 손실·중복 발송·비활성 대상 자동 활성화 없이 안전하게 전환된다.

**Acceptance Criteria:**

**Given** 기존 `ui_settings.json`의 `crawlings` 배열이 있을 때
**When** 마이그레이션을 실행하면(ADD-16, FR-31)
**Then** 기존 설정이 먼저 백업되고
**And** 활성 탭만 target 후보로 분류되며 비활성 탭은 보존하되 자동 활성화하지 않고
**And** 각 활성 탭에 `tenant_id`, `platform_account_id`, `monitoring_target_id`가 발급된다.

**Given** 기존 상태 폴더와 중복방지 기록을 승계해야 할 때
**When** 마이그레이션이 상태를 복사하면
**Then** `runtime/state/crawlingN` 폴더가 `targets/<monitoring_target_id>`로 복사되고 원본은 삭제되지 않으며(NFR-18)
**And** 기존 `last_message` hash가 신규 DeliveryLog/idempotency seed로 승계된다(NFR-21).

**Given** 마이그레이션 진행 상태를 추적해야 할 때
**When** 각 대상이 단계를 거치면
**Then** discovered, mapped, dry-run passed, approved, active, paused, rolled back 같은 상태를 표현할 수 있고(NFR-22)
**And** 실제 DeliveryRule 활성화는 운영자 승인 전에는 일어나지 않는다(FR-3 연계, 실제 dry-run 비교는 Epic 3).

## Epic 3: 한 번 수집 → 안전하게 여러 채널로 — 수집/렌더/전송 분리와 중복 방지 (P2)

운영자는 한 번 수집한 실적 Snapshot에서 메시지를 렌더링하고, 연결된 여러 채널(Telegram/Kakao)로 fan-out하되 중복 발송 없이 채널별 성공/실패를 따로 추적할 수 있다. `run_once`를 CrawlService/MessageRenderService/DispatchService로 분리하고, DeliveryLog + idempotency 유니크 제약으로 crash-after-send에도 중복을 막으며, 실발송 없는 dry-run으로 기존/신규 메시지를 비교한다. (spec P2-01~06, FR-3·7·8·9·10·11·24·26, NFR-1~4·24·25, ADD-5·11·14). 참고: Kakao 실제 전송은 Local Agent(Epic 4)가 담당하므로 이 에픽에서는 DispatchJob 생성·DeliveryLog·기존 경로 호환 전송까지 다룬다.

### Story 3.1: run_once를 수집/렌더/전송 서비스로 분리

As a 개발자,
I want 강하게 묶인 `run_once` 흐름을 CrawlService, MessageRenderService, DispatchService로 분리하고 싶다,
So that 수집·렌더링·전송을 따로 호출·테스트·재시도할 수 있으면서도 기존 1회 실행 결과는 그대로 보존된다.

**Acceptance Criteria:**

**Given** 기존 `app.run_once(config)` 경계가 수집·메시지·전송을 묶고 있을 때
**When** `CrawlService`, `MessageRenderService`, `DispatchService`로 분리하면(P2-01, FR-7·8)
**Then** 각 서비스는 독립적으로 호출 가능하고 주입 가능한(adapter 경계) crawler/sender를 받아 테스트에서 fake로 대체할 수 있으며
**And** 기존 UI 1회 실행 결과가 분리 전과 동일하게 유지된다(NFR-20, FR-2).

**Given** 마이그레이션 중 기존 호환 경로가 필요할 때
**When** 신규 분리 구조를 도입하면
**Then** 기존 `run_once` 호환 경로가 유지되어 레거시 UI 실행이 계속 동작한다(addendum 호환 경로).

**Given** 각 단계 실패가 독립적이어야 할 때
**When** CrawlService가 실패하면
**Then** Message 생성이나 Dispatch Job 생성으로 이어지지 않는다(FR-7).

### Story 3.2: Snapshot 정의와 필수 데이터 누락 시 fail-closed

As a 운영자,
I want 수집 결과를 정규화된 Snapshot으로 저장하되 필수 실적 데이터가 없으면 잘못된 메시지를 만들지 않게 하고 싶다,
So that 누락된 데이터로 만들어진 틀린 실적 메시지가 고객에게 발송되는 일을 막는다.

**Acceptance Criteria:**

**Given** CrawlService가 대상 화면을 수집할 때
**When** Snapshot을 생성하면(P2-02, FR-7)
**Then** Snapshot은 platform, target_id, collected_at, normalized_json(또는 normalized_data), parser_version, quality_state를 가지며
**And** 어떤 고객·플랫폼계정·대상·실행 시각·Agent에서 만들어졌는지 추적 가능하다.

**Given** 필수 실적 데이터가 누락됐을 때
**When** Snapshot 정규화를 시도하면
**Then** 기본값(0 등)으로 채우지 않고 명확한 예외(`MissingPerformanceDataError` 계승)를 내며 실패로 기록하고(NFR-2, FR-7)
**And** 해당 실행은 Message 생성으로 이어지지 않는다.

**Given** 기존 배민/쿠팡 parser 동작을 보존해야 할 때
**When** parser 출력을 Snapshot으로 wrapping하면
**Then** 배민/쿠팡 snapshot fixture 테스트가 통과하고 기존 parser 동작이 의도 없이 바뀌지 않는다(implementation-contract Reuse).

### Story 3.3: Message 정의와 안정적 렌더링 분리

As a 개발자,
I want Snapshot에서 Message를 렌더링하는 단계를 수집과 분리하고 template_version과 안정적 hash를 갖게 하고 싶다,
So that 같은 Snapshot을 재수집 없이 다시 렌더링하고 기존/신규 렌더링 결과를 비교할 수 있다.

**Acceptance Criteria:**

**Given** Snapshot이 있을 때
**When** MessageRenderService가 Message를 생성하면(P2-03, FR-8)
**Then** Message는 snapshot_id, template_version, text(또는 text_redacted_preview), text_hash를 가지며
**And** 동일 Snapshot + 동일 template_version은 동일한 text_hash를 만든다.

**Given** 동일 Snapshot을 재렌더링할 때
**When** 재수집 없이 다시 렌더링하면
**Then** 같은 결과가 나오고 수집 로직 수정 없이 포맷 변경을 검증할 수 있다(FR-8).

**Given** 기존 renderer 결과 호환이 필요할 때
**When** 신규 렌더링 결과를 기존 결과와 비교하면
**Then** 의도 없이 렌더링 결과가 바뀐 경우 실패로 식별된다(FR-2, FR-3 토대).

### Story 3.4: DeliveryRule fan-out — 한 대상에서 여러 채널로

As a 고객사 담당자,
I want 하나의 모니터링 대상 메시지가 연결된 여러 채널로 fan-out되게 하고 싶다,
So that 같은 실적 내용을 Telegram 그룹과 KakaoTalk 방에서 모두 받을 수 있다.

**Acceptance Criteria:**

**Given** 하나의 모니터링 대상에 여러 DeliveryRule(Telegram 채널, Kakao 방)이 연결돼 있을 때
**When** 하나의 Message가 fan-out되면(P2-04, FR-9)
**Then** 연결된 채널마다 별도의 Dispatch Job이 생성되고
**And** 한 번의 수집에서 최소 두 개 채널로 fan-out되는 시나리오가 테스트로 검증된다.

**Given** 한 채널 전송이 실패할 때
**When** 다른 채널이 정상 전송되면
**Then** 특정 채널 실패가 다른 채널 전송 성공을 무효화하지 않는다(FR-9).

**Given** DeliveryRule이 변경 시에만 보낼 수 있을 때
**When** `send_only_on_change`가 설정되면
**Then** 마지막 메시지 해시가 플랫폼·URL·센터·전송 대상 scope에 묶여 판단되고 scope key가 축소되지 않는다(project-context 규칙).

### Story 3.5: DeliveryLog와 idempotency — crash-after-send에도 중복 차단

As a 운영자,
I want 동일 대상·Snapshot·채널·토픽/방 조합에 대한 중복 발송을 idempotency key와 DeliveryLog로 막고 싶다,
So that 재시도나 전송 직후 크래시가 나도 같은 메시지가 두 번 발송되지 않는다.

**Acceptance Criteria:**

**Given** Dispatch Job이 전송될 때
**When** DeliveryLog와 idempotency key를 적용하면(P2-05, FR-10, ADD-5)
**Then** dedup key는 `monitoring_target_id + messenger_channel_id + snapshot_collected_at + template_version + message_hash`로 구성되고
**And** 같은 Dispatch Job이 재시도돼도 동일 idempotency key의 성공 전송은 다시 보내지 않는다.

**Given** crash-after-send를 가정해야 할 때
**When** 전송을 처리하면
**Then** at-least-once 의미론을 가정하고 성공 전송 전에 dedup key 유니크 제약을 먼저 확보하는 insert-then-send(또는 동등) 패턴을 사용하며(ADD-5)
**And** exactly-once를 가정하지 않는다.

**Given** 중복 방지 키가 다른 전송을 오차단하면 안 될 때
**When** 다른 고객·다른 대상·다른 채널의 전송이 들어오면
**Then** 그 전송들은 잘못 차단되지 않고
**And** 중복으로 막힌 전송은 DeliveryLog에 별도 결과(duplicate_blocked)로 기록된다(FR-10, NFR-15).

### Story 3.6: 수집 실패와 전송 실패 분리, 재시도·실패 상태 관리

As a 운영자,
I want 수집/렌더링/전송 실패를 서로 다른 상태로 기록하고 재시도 가능 실패와 사람 개입 필요 실패를 구분하고 싶다,
So that 어느 단계가 왜 실패했는지 보고 적절히 재시도하거나 사람이 개입할 수 있다.

**Acceptance Criteria:**

**Given** 한 실행에서 수집은 성공하고 전송이 실패할 때
**When** 결과를 기록하면(P2-06, FR-11·26)
**Then** 수집 성공과 전송(예: Kakao) 실패가 서로 다른 상태로 따로 보이고
**And** 채널별 성공·실패·재시도·보류 상태가 각각 기록된다(FR-26).

**Given** 인증 필요 실패와 일시적 실패를 구분해야 할 때
**When** 실패를 분류하면(FR-11, NFR-15)
**Then** 인증 필요 상태는 무한 재시도하지 않고 `AUTH_REQUIRED` 계열 상태로 남고
**And** 일시적 네트워크/서버 오류는 error_code별 제한된 재시도와 backoff를 따르며 고정 5초 무한 재시도를 만들지 않는다.

**Given** parser가 반복 실패할 때
**When** 같은 parser 작업이 계속 실패하면
**Then** 운영자가 확인할 수 있는 경고 상태로 전환된다(FR-11).

**Given** 같은 Snapshot에서 일부 채널만 실패했을 때
**When** 재시도하면
**Then** 실패한 채널만 재시도할 수 있고 이미 성공한 채널은 중복 발송되지 않는다(FR-26).

### Story 3.7: Telegram 중앙 전송 도입

As a 운영자,
I want Telegram 전송을 중앙 webhook/sendMessage 흐름으로 옮기고 Agent별 getUpdates polling을 제거하고 싶다,
So that 같은 bot token을 여러 프로세스가 동시에 polling해 큐가 경합하는 문제를 없애고 전송을 중앙에서 관리한다.

**Acceptance Criteria:**

**Given** 기존 Telegram sender가 동작할 때
**When** 중앙 webhook/sendMessage 흐름으로 전환하면(FR-24, ADD-11, implementation-contract Reuse)
**Then** Telegram 전송이 중앙 경로로 처리되고 Agent별 getUpdates polling이 제거되며
**And** 같은 bot token을 여러 프로세스에서 동시에 polling하는 구조가 만들어지지 않는다(NFR, project-context 규칙).

**Given** 전송 대상 scope를 식별해야 할 때
**When** Telegram 전송을 기록하면
**Then** chat_id와 topic_id(message_thread_id) 조합이 전송 대상 scope에 포함되고
**And** Telegram 전송 실패는 DeliveryLog에 채널별로 기록된다(FR-24, FR-26).

**Given** 활성 텔레그램 대상끼리 충돌하면 안 될 때
**When** 채널을 운영하면
**Then** 같은 `chat_id + topic_id` 조합을 여러 활성 대상이 공유하지 않는다(project-context 규칙).

### Story 3.8: 신규 경로 dry-run 비교와 승인 후 활성화

As a 운영자,
I want 새 수집/렌더/전송 경로를 실제 발송 없이 dry-run으로 돌리고 기존 메시지와 차이를 승인 전 확인하고 싶다,
So that 기존 고객에게 잘못된 메시지나 중복 발송을 만들지 않고 검증된 경로만 실서비스로 활성화한다.

**Acceptance Criteria:**

**Given** 신규 분리 경로(Crawl/Render/Dispatch)가 있을 때
**When** dry-run을 실행하면(FR-3)
**Then** 실제 Telegram/Kakao 발송이 일어나지 않고 수집·렌더·저장만 수행되며
**And** 대표 배민 대상과 쿠팡 대상에 대해 dry-run이 성공한다(operations-security-test E2E dry-run).

**Given** 기존/신규 메시지 차이를 검토해야 할 때
**When** dry-run 결과를 기준선 메시지와 비교하면
**Then** 메시지 차이가 발생하면 DeliveryRule 활성화가 자동으로 진행되지 않고
**And** 운영자가 차이를 확인·승인한 대상만 신규 DeliveryRule로 실제 전송된다(FR-3).

**Given** old path와 new path가 동시에 전송하면 안 될 때
**When** cutover를 진행하면
**Then** old/new 동시 실제 전송을 막는 cutover 규칙이 적용되고(NFR-24)
**And** rollback 시 신규 DeliveryRule을 비활성화하고 기존 런타임 경로를 복구하되 신규 로그는 중복 방지 기록으로 보존한다(NFR-25).

## Epic 4: Windows Local Agent — Chrome·KakaoTalk·인증을 로컬에서 안전하게 처리한다 (P3)

작업 노드 관리자는 현재 Windows PC를 Agent #1로 등록해, 중앙 서버에 outbound-only로 job을 polling/claim/complete하면서 실제 배민/쿠팡 수집과 KakaoTalk 직렬 전송을 수행한다. Browser Profile/CDP 격리, 기대 센터/상점명 검증, 배민 사람 개입형 재인증, 쿠팡 Gmail 2FA 메일함 분리·lock, KakaoTalk FIFO 직렬 전송·방명 검증, 재부팅 후 autostart까지 — 로컬 제약을 우회하지 않고 안전하게 다룬다. (spec P3-01~07, 인증/Gmail 2FA 계약, FR-12~20·25·28·32, NFR-2·4·6·7·9·16, ADD-4·6·15). 참고: 서버 측 job 생성·queue·Admin은 Epic 5에서 완성되며, 이 에픽은 Agent 측 claim 루프와 로컬 작업 실행을 다룬다(서버 stub/mock에 대해 동작 검증).

### Story 4.1: rider_agent 패키지 생성과 기존 도메인 재사용

As a 개발자,
I want 기존 crawler/parser/renderer/Gmail 2FA/Kakao sender를 import하는 `rider_agent` 패키지를 만들고 싶다,
So that 새 프레임워크 도입 없이 검증된 기존 코드를 그대로 재사용하는 Local Agent 토대를 갖춘다.

**Acceptance Criteria:**

**Given** 기존 `src/rider_crawl/` 공유 도메인이 있을 때
**When** `src/rider_agent/` 패키지를 만들고 기존 crawler/parser/renderer/Gmail 2FA/Kakao sender를 import하면(P3-01, ADD-2)
**Then** `python -m rider_agent`가 실행되고
**And** Playwright 1.60.0·crawl4ai 0.8.7 고정 버전을 그대로 사용하며 새 프레임워크를 도입하지 않는다(ADD-3).

**Given** Agent는 sync 런타임이어야 할 때
**When** Agent 코드를 작성하면
**Then** Playwright/PC 자동화 경로는 sync로 유지되고 Cloud의 async 경계와 섞지 않는다(ADD, project-context 규칙).

### Story 4.2: 등록 코드 입력과 Agent 토큰 보안 저장

As a 작업 노드 관리자,
I want 일회용 등록 코드로 Agent를 서버에 등록하고 발급된 agent_id/token을 OS 보안 저장소에 보관하고 싶다,
So that Agent가 안전하게 자신을 식별하고 평문 token이 디스크에 노출되지 않는다.

**Acceptance Criteria:**

**Given** 운영자가 발급한 일회용 registration code가 있을 때
**When** Agent가 `POST /v1/agents/register`로 등록하면(P3-02, FR-12, ADD-6)
**Then** 서버가 agent_id, agent_token, tenant_scope, config_version을 발급하고
**And** 같은 등록 코드로 Agent가 서버에 1회 등록된다.

**Given** 발급된 token을 저장해야 할 때
**When** Agent가 token을 보관하면(NFR-8)
**Then** agent_id/token은 Agent-local DPAPI/Windows Credential Manager에 저장되고
**And** token이 평문으로 로그·config·디스크 텍스트에 남지 않는다(ADD-15).

**Given** token이 유출·만료될 수 있을 때
**When** 서버가 Agent token을 revoke하면(NFR-7)
**Then** 해당 Agent는 더 이상 job을 받을 수 없고
**And** token 없거나 만료 시 job 수신이 거부된다(FR-16).

### Story 4.3: Agent heartbeat 보고

As a 작업 노드 관리자,
I want Agent가 주기적으로 상태·버전·처리 가능 job type·현재 작업을 heartbeat로 보고하게 하고 싶다,
So that 운영자가 Agent가 살아있는지, 어떤 버전인지, 무엇을 처리 중인지 알 수 있다.

**Acceptance Criteria:**

**Given** 등록된 Agent가 동작할 때
**When** Agent가 30~60초마다 `POST /v1/agents/heartbeat`로 보고하면(P3-03, FR-12)
**Then** heartbeat에 metrics, capabilities, active_jobs, kakao_status, browser_profiles가 포함되고
**And** 운영 화면(Epic 5)이 online/offline 상태를 표시할 수 있는 데이터가 서버에 기록된다.

**Given** Agent가 일정 시간 heartbeat를 보내지 않을 때
**When** 마지막 heartbeat가 2분 이상 없으면(NFR-14)
**Then** 해당 Agent는 offline 또는 degraded로 판정될 수 있는 상태가 되고
**And** Agent 버전이 서버 기대 버전과 다르면 식별 가능하다(FR-12).

**Given** Agent별 처리 능력을 알아야 할 때
**When** heartbeat가 capabilities를 보고하면
**Then** Agent별 처리 가능 job type이 표시된다(FR-12).

### Story 4.4: outbound HTTPS job polling/claim/complete와 lease

As a 작업 노드 관리자,
I want Agent가 방화벽 inbound 개방 없이 outbound HTTPS로만 job을 claim하고 결과를 보고하게 하고 싶다,
So that 운영자 PC에 inbound 포트를 열지 않고도 안전하게 작업을 처리하며 같은 job이 중복 실행되지 않는다.

**Acceptance Criteria:**

**Given** 서버에 처리할 job이 있을 때
**When** Agent가 `POST /v1/jobs/claim`으로 claim하고 `POST /v1/jobs/{job_id}/complete`로 완료를 보고하면(P3-04, FR-13·16, ADD-6)
**Then** Agent는 inbound 포트 개방 없이 outbound HTTPS만으로 job 수신·결과 보고가 가능하고
**And** claim한 job만 실행한다.

**Given** 두 Agent가 같은 job을 동시에 가져가면 안 될 때
**When** job을 claim하면(ADD-5)
**Then** claim 시 lease 만료시각이 부여되고 heartbeat로 연장되며 만료 시 stale 회수되어
**And** 두 Agent가 같은 job을 동시에 성공 처리하지 않는다(FR-13).

**Given** Agent가 작업 중 죽을 수 있을 때
**When** lease가 만료되면
**Then** job은 timeout 후 재할당되거나 실패 상태가 되고
**And** job 결과에는 실행 Agent, 시작/종료 시각, 실패 사유(error_code, error_message_redacted)가 포함된다(FR-13, ADD-6).

**Given** job 이벤트를 보고할 때
**When** Agent가 `POST /v1/jobs/{job_id}/events`를 호출하면
**Then** event_type, severity, message_redacted, artifact ref가 전달되고 본문에 secret/OTP가 포함되지 않는다(ADD-6, NFR-5).

### Story 4.5: BrowserProfileManager — Chrome 프로필/CDP 격리와 대상 검증

As a 작업 노드 관리자,
I want 계정·대상별 Chrome 프로필과 CDP 포트를 격리하고 기대 센터/상점명을 검증하게 하고 싶다,
So that 서로 다른 고객·계정이 같은 프로필을 공유하거나 기대와 다른 화면을 수집해 다른 계정 실적을 오발송하는 일을 막는다.

**Acceptance Criteria:**

**Given** 여러 대상이 각자 Chrome 프로필/CDP 포트를 쓸 때
**When** BrowserProfileManager가 프로필을 관리하면(P3-05, FR-14)
**Then** 서로 다른 고객/계정이 같은 Browser Profile을 잘못 공유하지 않고
**And** CDP 포트나 프로필 중복이 감지되면 작업을 시작하지 않는다.

**Given** 수집한 화면이 기대 대상과 일치해야 할 때
**When** 대상 검증을 수행하면(FR-20)
**Then** 쿠팡 기대 센터/상점명이 비어 있거나 배민 기본값이면 작업을 위험 상태로 보고하고
**And** 기대 대상과 다른 화면(CENTER_MISMATCH)이면 메시지를 만들거나 보내지 않으며
**And** 검증 실패는 운영자가 조치할 수 있는 오류(target_validation_failure)로 표시된다(NFR-15).

**Given** CDP 포트와 프로필이 계정 격리 장치일 때
**When** 대상을 추가하거나 자동 실행하면
**Then** 포트/프로필 중복 검증이 약화되지 않는다(project-context 규칙).

### Story 4.6: KakaoSenderWorker — FIFO 직렬 전송과 정확한 채팅방 검증

As a 운영자,
I want KakaoTalk 전송을 단일 Windows 세션의 FIFO 직렬 queue로 처리하고 정확한 채팅방 검증 전에는 보내지 않게 하고 싶다,
So that 같은 세션에서 병렬 입력이나 애매한 방으로의 오발송 없이 Kakao 메시지를 안전하게 보낸다.

**Acceptance Criteria:**

**Given** 여러 Kakao 전송 작업이 쌓일 때
**When** KakaoSenderWorker가 처리하면(P3-06, FR-15·25)
**Then** 전송이 같은 Windows 세션에서 FIFO로 직렬 처리되고
**And** 한 Agent의 Kakao 전송이 동시에 여러 방에 병렬 입력하지 않는다(ADD-15 금지행위).

**Given** 채팅방을 정확히 검증해야 할 때
**When** 전송 전 방을 확인하면(FR-15)
**Then** 채팅방명 중복, 창 확인 실패, 포커스 실패, 전송 결과 확인 실패는 실패로 기록되고 임의 전송하지 않으며(kakao_failure / kakao_ambiguous_room)
**And** 고유 방명 또는 동등한 식별 정책을 통과해야 전송 대상이 된다.

**Given** Kakao를 제한/best-effort 채널로 운영할 때
**When** 전송량·지연을 관리하면(FR-25)
**Then** KakaoTalk 전송량과 queue lag가 보고되어 운영 화면(Epic 5)에 표시 가능하고
**And** Kakao 전송 실패를 자동으로 다른 방에 보내는 방식으로 복구하지 않는다.

### Story 4.7: Agent 실행 조건과 재부팅 후 자동 시작

As a 작업 노드 관리자,
I want Kakao 작업 Agent가 interactive Windows 세션에서 실행되고 재부팅 후 자동 시작되며 crawler-only와 Kakao sender Agent의 실행 조건이 구분되게 하고 싶다,
So that PC 재부팅이나 잠금 상태에서도 Agent가 안정적으로 복구되고 작업 유형별로 올바른 실행 환경에서 동작한다.

**Acceptance Criteria:**

**Given** Kakao 작업이 PC 앱 UI 자동화일 때
**When** Kakao sender Agent를 실행하면(FR-32)
**Then** interactive user session에서 실행되고 Session 0 service-only 방식에 의존하지 않는다(ADD-15, addendum hardening).

**Given** PC가 재부팅될 수 있을 때
**When** Windows Startup 또는 Task Scheduler로 Agent 자동 시작을 구성하면(P3-07)
**Then** 재부팅 후 사용자 로그인 시 Agent가 자동 시작되고 heartbeat가 복구된다.

**Given** crawler-only 노드와 Kakao sender 노드의 요구가 다를 때
**When** Agent를 구성하면(FR-32)
**Then** 순수 crawler Agent와 Kakao sender Agent의 실행 조건과 처리 가능 job type이 구분되고
**And** 현재 일반 Windows PC를 Agent #1로 사용해 기존 Chrome/Kakao 환경을 활용한다(FR-28).

### Story 4.8: 배민 인증 필요 감지와 사람 개입형 재인증

As a 운영자,
I want 배민 수집 중 휴대폰 인증·로그인 만료를 감지해 작업을 인증 필요 상태로 두고 사람이 인증을 완료한 뒤에만 재개되게 하고 싶다,
So that 인증을 우회하지 않으면서 인증으로 막힌 대상에 잘못된 메시지를 보내지 않고 사람 확인 후 안전하게 재개한다.

**Acceptance Criteria:**

**Given** 배민 수집 중 휴대폰 인증 또는 로그인 만료가 필요할 때
**When** Agent가 인증 필요 상태를 감지하면(FR-17)
**Then** 작업이 `AUTH_REQUIRED` 상태로 전환되고 실적 메시지를 생성·전송하지 않으며(NFR-2)
**And** 어떤 고객/대상/브라우저 프로필이 인증을 요구하는지 서버에 보고된다.

**Given** 사람이 인증을 완료해야 할 때
**When** 운영자/담당자가 해당 브라우저 프로필을 인증 모드로 열고 휴대폰 인증을 완료하면(FR-18)
**Then** 시스템은 휴대폰 인증 코드를 취득·우회·자동 통과하려 시도하지 않고(ADD-15)
**And** Agent는 사람이 완료한 인증 상태(AUTH_VERIFIED)만 감지해 작업을 재개한다.

**Given** 인증이 제때 완료되지 않을 때
**When** 정해진 시간이 지나면(FR-17·18, NFR-4)
**Then** 대상 상태는 `AUTH_REQUIRED`(또는 auth timeout)로 유지되고 전송은 진행하지 않으며 무한 재시도하지 않고
**And** 인증 실패/timeout이 운영 상태에 남는다.

### Story 4.9: 쿠팡 Gmail 2FA 메일함 분리와 lock

As a 보안 담당 운영자,
I want 쿠팡 Gmail 2FA를 고객/메일함/token 단위로 분리하고 같은 메일함 동시 읽기를 lock으로 막고 싶다,
So that 다른 고객의 인증번호를 잘못 읽거나 민감값을 노출하지 않고 안전하게 2FA를 복구한다.

**Acceptance Criteria:**

**Given** 여러 고객의 쿠팡 Gmail 2FA가 있을 때
**When** Gmail OAuth token을 저장하면(FR-19, Gmail 2FA 계약)
**Then** token은 고객/mailbox_id 단위로 분리 저장되고(Agent-local DPAPI/Credential Manager) 서버는 ref만 저장하며
**And** 고객 간 Gmail token을 공유하지 않는다(ADD-15).

**Given** 같은 메일함을 동시에 읽으면 안 될 때
**When** 두 쿠팡 인증 요청이 같은 mailbox_id에 들어오면(FR-19)
**Then** mailbox lock으로 동시 처리를 막고
**And** 메일 검색은 인증 요청 시각 이후 수신 메일만 from/subject/query/customer 필터로 조회해 최신 메일 오인식을 피한다.

**Given** 민감값을 보호해야 할 때
**When** 2FA를 처리하면(NFR-5)
**Then** 인증번호(OTP), OAuth token, refresh token, 쿠팡 비밀번호가 로그·예외 메시지에 남지 않고
**And** CAPTCHA/이상 로그인은 복구를 멈추고 USER_ACTION_REQUIRED로, refresh 실패/grant 취소는 GMAIL_REAUTH_REQUIRED로 분류된다(NFR-16).

**Given** 자동복구가 실패할 때
**When** 인증이 반복 실패하면
**Then** 반복 인증 요청을 계속 보내지 않고 탭/작업을 중지하는 기존 정책을 유지한다(project-context 규칙, NFR-4).

## Epic 5: 중앙 서버와 운영자 Admin — 한곳에서 보고, 제어하고, 안전하게 운영한다 (P4 + 운영 안전 보강)

운영자는 FastAPI 중앙 서버와 Jinja2+HTMX Admin UI에서 고객/대상/Agent/채널/job 상태와 마지막 성공·실패·queue lag·인증 필요를 한곳에서 보고, 대상 활성화·test send·retry·채널 등록/검증·인증 확인 같은 운영 액션을 수행한다. PostgreSQL 13 테이블·Alembic, jitter·circuit breaker·capacity 기반 scheduler, Telegram `/register` webhook, 7개 모니터링 지표, MFA·4역할·audit log·token revoke·backup/restore까지 관측성·제어·보안·복구성을 갖춘다. 100 fake target scheduling smoke로 확장성을 입증한다. (spec P4-01~07, FR-3·4·6·21·22·23·24·27·29·30·33·34, NFR-10·11·12~17·23·26~29, ADD-1·10·12·13). 참고: FR-4의 Admin 엔티티 생성/편집 CRUD UI는 Story 5.11에서 다룬다(도메인 모델은 Epic 2 Story 2.5).

### Story 5.1: FastAPI 백엔드 스캐폴딩과 헬스/버전/메트릭 엔드포인트

As a 개발자,
I want FastAPI 표준 레이아웃의 Cloud 백엔드를 스캐폴딩하고 `/health`, `/version`, `/metrics`를 제공하고 싶다,
So that 이후 모든 서버 기능(API·scheduler·dispatcher·Admin)이 올라갈 토대를 Docker 컨테이너로 띄울 수 있다.

**Acceptance Criteria:**

**Given** 정식 CLI 스캐폴드가 없는 브라운필드일 때
**When** `src/rider_server/` FastAPI 백엔드를 표준 app 레이아웃으로 구성하면(P4-01, ADD-1·2)
**Then** `/health`, `/version`, `/metrics` 엔드포인트가 동작하고
**And** 서버가 Docker 컨테이너로 실행된다(ADD-12).

**Given** API 규약을 따라야 할 때
**When** 엔드포인트를 정의하면(ADD-8·13)
**Then** 경로는 `/v1/` 복수 명사, JSON 필드는 snake_case(camelCase 변환 없음)이고
**And** 에러 응답은 `{"error":{"code":"<UPPER_SNAKE>","message_redacted":"..."}}` 포맷에 의미 있는 HTTP 상태코드를 쓰며 시각은 ISO 8601 UTC다.

**Given** Cloud는 async 런타임일 때
**When** 서버 코드를 작성하면
**Then** FastAPI/SQLAlchemy는 async로 작성되고 blocking sync를 async에서 직접 호출하지 않는다(ADD, project-context 규칙).

### Story 5.2: PostgreSQL 13 테이블 스키마와 Alembic 마이그레이션

As a 개발자,
I want 13개 도메인 테이블의 PostgreSQL 스키마를 정의하고 Alembic 마이그레이션으로 빈 DB에서 재현하고 싶다,
So that 운영 데이터가 ID 기반 모델로 영속화되고 어떤 환경에서도 동일한 스키마를 재생성할 수 있다.

**Acceptance Criteria:**

**Given** 빈 PostgreSQL 18 DB가 있을 때
**When** Alembic 마이그레이션을 실행하면(P4-02, ADD-7)
**Then** tenants, subscriptions, platform_accounts, monitoring_targets, browser_profiles, messenger_channels, delivery_rules, snapshots, messages, delivery_logs, agents, jobs, auth_sessions, audit_logs 13개 테이블이 생성되고
**And** 각 테이블이 data-api-contract의 required fields를 가진다.

**Given** DB 네이밍 정본을 따라야 할 때
**When** 스키마를 정의하면(ADD-8)
**Then** 테이블은 복수 snake_case, 컬럼 snake_case, PK는 UUID `id`, FK는 `<entity>_id`, 시각은 `_at`(timezone-aware), 상태는 대문자 enum 문자열이고
**And** secret은 컬럼에 평문 없이 `*_ref`만 둔다(NFR-8).

**Given** 중복 방지를 DB에서 강제해야 할 때
**When** delivery_logs를 정의하면(ADD-5, FR-10)
**Then** dedup key에 대한 유니크 제약 `uq_delivery_logs_dedup_key`가 존재해 같은 key 재시도는 INSERT 충돌로 차단된다.

### Story 5.3: QueueBackend 추상화와 PostgreSQL job queue

As a 개발자,
I want job queue를 `QueueBackend` 인터페이스로 추상화하고 PostgreSQL `jobs` 테이블 구현을 제공하고 싶다,
So that Redis 미도입으로 idempotency를 DB 트랜잭션과 한 곳에 두면서도 추후 Redis/SQS로 교체할 길을 연다.

**Acceptance Criteria:**

**Given** queue가 필요할 때
**When** `QueueBackend` 인터페이스와 PostgreSQL 구현을 만들면(P4-05, ADD-4)
**Then** PostgreSQL `jobs` 테이블 + `FOR UPDATE SKIP LOCKED`로 job claim이 구현되고
**And** `QueueBackend` 인터페이스 테스트가 통과해 구현을 Redis/SQS로 옮길 수 있음이 보장된다.

**Given** at-least-once 의미론을 가정할 때
**When** job을 claim/lease하면(ADD-5)
**Then** claim 시 lease 만료시각이 부여되고 만료 시 stale 회수되며
**And** 두 Agent가 같은 job을 동시에 성공 처리하지 않는다(FR-13 서버 측 보장).

**Given** job type이 정의돼야 할 때
**When** job을 생성하면
**Then** job type은 UPPER_SNAKE(`CRAWL`, `RENDER`, `DISPATCH_TELEGRAM`, `KAKAO_SEND`, `BAEMIN_AUTH_OPEN`)이고 상태 전이는 정의된 set만 허용한다(ADD).

**Given** Epic 4 Agent claim 루프가 Epic 3·4에서 서버 stub/mock에 대해 검증됐을 때
**When** 실제 PostgreSQL `jobs` 테이블 + `FOR UPDATE SKIP LOCKED` 구현이 준비되면(Epic 4↔5 통합 검증)
**Then** 실제 Agent(`rider_agent`)가 mock이 아닌 실 서버 큐에서 `POST /v1/jobs/claim`으로 claim하고 `complete`까지 수행하는 end-to-end 경로가 통합 테스트로 검증되고
**And** 두 Agent(또는 두 claim 요청)가 같은 job을 동시에 가져갈 때 정확히 하나만 성공하고 나머지는 빈 응답/충돌을 받는다(`double Agent claim` negative test, Story 5.10 연계, FR-13)
**And** lease 만료 후 stale 회수와 재할당이 실 DB에서 동작함을 확인한다(mock 경계가 아닌 실제 동작 검증).

### Story 5.4: Scheduler — interval·jitter·circuit breaker와 구독 게이트

As a 운영자,
I want 대상별 interval에 jitter를 더해 job을 예약하되 구독 게이트로 중지 고객을 거르고 플랫폼 장애 시 circuit breaker로 보호하고 싶다,
So that 대상이 늘어도 같은 시각 job 폭주나 중지 고객 작업, 플랫폼 전체 장애 확산이 일어나지 않는다.

**Acceptance Criteria:**

**Given** 여러 대상이 스케줄될 때
**When** scheduler가 due 대상에 CrawlJob을 생성하면(P4-04, FR-33)
**Then** interval에 jitter가 적용되어 모든 대상이 같은 초에 몰리지 않음이 검증 가능하고
**And** job assignment는 Agent capacity와 target/profile affinity를 고려한다.

**Given** 구독 게이트가 scheduler 앞단에 있을 때
**When** SubscriptionGate가 평가하면(FR-6)
**Then** `ACTIVE`/`PAYMENT_ACTIVE`가 아닌 고객은 신규 CrawlJob이 예약되지 않는다.

**Given** 플랫폼 전체 장애나 parser 실패율 급증이 발생할 때
**When** circuit breaker가 동작하면(FR-33, NFR-14)
**Then** platform-wide 장애 또는 parser 실패율 급증(최근 15분 30% 초과) 시 신규 CrawlJob 생성을 제한하고
**And** error_code별 backoff가 고정 5초 무한 재시도 같은 폭주 패턴을 만들지 않는다(ADD-15).

### Story 5.5: Telegram webhook과 채널 등록·검증·활성화

As a 운영자,
I want Telegram을 secret header 검증 webhook + `/register <code>`로 등록하고 채널을 테스트 메시지 확인 후에만 활성화하고 싶다,
So that getUpdates polling 경합 없이 채널을 등록하고 검증되지 않은 채널로 실서비스 전송이 나가지 않게 한다.

**Acceptance Criteria:**

**Given** Telegram 채널을 등록해야 할 때
**When** secret header 검증 webhook과 `/register <code>`를 구현하면(P4-06, FR-29, ADD-11)
**Then** `chat_id`와 optional `message_thread_id`가 자동 저장되고
**And** getUpdates polling 없이 등록이 동작하며 secret header가 검증되지 않은 요청은 거부된다.

**Given** 채널을 활성화하기 전일 때
**When** 채널 등록/검증 절차를 거치면(FR-29)
**Then** Telegram 채널은 chat_id와 topic_id가 확인된 뒤, Kakao 채팅방은 고유 방명/동등 정책을 통과한 뒤에만 전송 대상이 되고
**And** 테스트 메시지 확인 전 DeliveryRule은 실제 운영 전송에 쓰이지 않는다.

**Given** 등록·검증 결과를 추적해야 할 때
**When** 채널을 검증하면
**Then** 채널 상태(state)가 등록/검증/활성으로 구분되어 기록된다(ADD-7 messenger_channels).

### Story 5.6: Admin 운영 대시보드와 상태 심각도 표시

As a 운영자,
I want 고객/대상/Agent/채널/job 상태와 마지막 성공·실패·queue lag·인증 필요를 한 화면에서 보고 심각도로 구분하고 싶다,
So that 고객이 알려주기 전에 어디가 막혔는지 한눈에 파악하고 우선순위를 정할 수 있다.

**Acceptance Criteria:**

**Given** 운영 상태를 한곳에서 봐야 할 때
**When** Jinja2+HTMX Admin 대시보드를 구현하면(P4-03, FR-21, ADD-10)
**Then** 대상별 마지막 수집 성공·마지막 전송 성공·마지막 실패 사유, Agent별 heartbeat·버전·현재 job·처리 가능 job type이 표시되고
**And** KakaoTalk queue lag와 Telegram 전송 오류가 구분되어 표시되며
**And** 별도 JS 빌드 파이프라인 없이 백엔드와 동일 인증/세션으로 서버 렌더 부분 갱신된다.

**Given** 상태 심각도를 계산해야 할 때
**When** 마지막 성공 시각을 평가하면(FR-23, NFR-13)
**Then** `target_last_success_at`이 interval×2 초과 시 warning, ×4 초과 시 critical로 표시되고
**And** 상태는 정상/주의/위험/중지로 운영자가 이해 가능하게 분류된다.

**Given** fail-closed 우선 표시가 필요할 때
**When** 위험 상태를 판단하면
**Then** 인증 필요, 기대 대상 검증 실패, KakaoTalk 오발송 위험은 자동 전송보다 중지를 우선하는 상태로 표시된다(FR-23).

**Given** 인증 필요 대상을 골라봐야 할 때
**When** auth-required 필터를 적용하면
**Then** 어떤 고객/대상/프로필이 인증을 요구하는지 목록으로 확인할 수 있다(FR-17 연계, FR-21).

### Story 5.7: Admin 수동 운영 액션과 고객/구독 상태 전이

As a 운영자,
I want Admin UI에서 대상 활성/비활성·Agent 배정·test crawl·dry-run render·test send·job retry·인증 확인·구독 상태 전이를 수행하고 싶다,
So that 코드 변경 없이 운영 중 필요한 조치를 안전하게(중복 방지 우회 없이) 실행할 수 있다.

**Acceptance Criteria:**

**Given** 운영자가 수동 액션을 수행할 때
**When** Admin이 대상 활성/비활성, Agent 배정, test crawl, dry-run render, test send, job retry, 인증 필요 확인을 제공하면(FR-22)
**Then** test send는 운영자가 지정한 테스트 채널로만 전송되고
**And** retry는 중복 발송 방지 정책(idempotency)을 우회하지 않으며
**And** dry-run render는 실제 발송 없이 결과만 보여준다(FR-3 연계).

**Given** 고객/구독 상태를 전이해야 할 때
**When** 운영자가 상태를 변경하면(FR-30)
**Then** setup/인증 대기/채널 검증 대기/테스트 실행/활성/성능 저하/인증 필요/중지 상태가 구분되고
**And** `SUSPENDED`→`ACTIVE` 복구 시 `HELD` Dispatch Job을 운영자 확인 후 폐기/재개로 처리한다(FR-6 연계).

**Given** 위험한 수동 액션을 추적해야 할 때
**When** 액션을 실행하면(FR-22)
**Then** 실행자와 실행 시각이 audit log에 기록된다.

### Story 5.8: Audit log와 Admin 접근 보안

As a 보안 담당 운영자,
I want 모든 Admin 변경을 audit log로 남기고 관리자 접근에 MFA·역할·token revoke를 적용하고 싶다,
So that 누가 무엇을 바꿨는지 추적하고 권한 없는 접근이나 유출된 token으로 인한 사고를 막는다.

**Acceptance Criteria:**

**Given** Admin이 설정을 변경할 때
**When** audit log를 기록하면(P4-07, FR-34)
**Then** 고객/secret/채널 설정 변경이 추적 가능하고
**And** audit log는 actor, source, before/after value(diff_redacted), target IDs, reason, timestamp, result를 포함한다(implementation readiness gate).

**Given** Admin 접근을 보호해야 할 때
**When** 접근 제어를 적용하면(FR-34)
**Then** 모든 관리자 계정은 MFA가 기본이고 VPN 또는 IP allowlist 같은 추가 제한을 둘 수 있으며
**And** 최소 역할 viewer/operator/secret-admin/break-glass가 구분된다.

**Given** token을 폐기·교체해야 할 때
**When** Agent token이나 주요 외부 service token을 관리하면(NFR-7)
**Then** server-side revoke 또는 rotate가 가능하고
**And** 운영 DB와 진단 산출물은 backup/retention/restore rehearsal 정책을 가지며 복구 환경은 명시적 활성화 전까지 non-sending 모드로 시작한다(NFR-9·25).

### Story 5.9: 7개 모니터링 지표·알림과 운영 runbook

As a 운영자,
I want 핵심 7개 지표를 노출하고 임계 초과 시 알림을 받으며 표준 runbook으로 대응하고 싶다,
So that 인증 만료·queue 지연·전송 오류·Agent offline을 사고로 번지기 전에 감지·대응할 수 있다.

**Acceptance Criteria:**

**Given** 운영 관측성이 필요할 때
**When** 모니터링 지표를 노출하면(NFR-14, ADD-12)
**Then** `agent_last_heartbeat`(2분 초과 offline), `target_last_success_at`(×2 warning/×4 critical), `auth_required_count`(≥1 alert), `kakao_queue_lag_seconds`(120s 반복 초과), `crawl_error_rate_by_platform`(최근 15분 30% 초과), `telegram_send_error_rate`(최근 10분 급증), `gmail_reauth_required_count`(≥1) 7개 지표가 CloudWatch/대시보드에 노출된다.

**Given** 최소 알림이 필요할 때
**When** 임계가 초과되면(FR-34)
**Then** 최소 `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required` 알림이 발생한다.

**Given** 장애 대응 절차가 필요할 때
**When** runbook을 작성하면(NFR-17)
**Then** `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required`, `profile_mismatch`, `kakao_ambiguous_room`, `duplicate_blocked` 최소 세트가 `docs/runbooks/`에 존재하고
**And** 장애 원인이 crawl_failure/auth_required/render_failure/telegram_failure/kakao_failure/duplicate_blocked/target_validation_failure로 조치 가능하게 분류된다(NFR-15).

### Story 5.10: 100 fake target 부하 smoke와 negative safety test

As a 운영자,
I want 100개 가짜 대상 scheduling smoke와 핵심 negative safety test로 확장성과 안전성을 입증하고 싶다,
So that 대상이 늘어도 job 폭주 없이 동작하고 잘못된 대상/방/token/중복 시나리오에서 사고가 안 나는 것을 출시 전 확인한다.

**Acceptance Criteria:**

**Given** 확장성을 입증해야 할 때
**When** 100개 fake target scheduling smoke를 실행하면(NFR-26, P4 smoke)
**Then** job 생성·상태 전환·queue 기록이 실패 없이 완료되고 jitter로 job storm이 발생하지 않는다.

**Given** 안전성을 입증해야 할 때
**When** negative safety test를 실행하면(NFR-29)
**Then** wrong tenant, wrong profile, wrong Kakao room, stale Agent token, restored DB, double Agent claim, crash-after-send 시나리오가 모두 fail-closed로 차단되고
**And** 잘못된 tenant/profile/Kakao room이면 전송하지 않고 실패로 기록한다(NFR-1).

**Given** 마이그레이션 안전장치를 검증해야 할 때
**When** 운영 안전 시나리오를 테스트하면(NFR-23, SM-7)
**Then** 채널 검증 전 활성화 차단, atomic settings write, `last_message` seed 승계, Agent autostart heartbeat 복구, scheduler jitter/circuit breaker가 검증되고
**And** 전역 dispatch kill switch와 tenant/channel 단위 pause가 동작한다.

### Story 5.11: Admin 엔티티 생성/편집 CRUD UI

As a 운영자,
I want Admin UI에서 고객·모니터링 대상·메시지 채널·전송 규칙을 직접 생성·조회·수정·비활성화하고 싶다,
So that DB나 마이그레이션 스크립트를 직접 건드리지 않고도 운영 중 신규 고객/대상/채널/규칙을 안전하게(테넌트 격리·secret 분리·감사 로그) 추가·변경할 수 있다.

**Acceptance Criteria:**

**Given** 운영자가 신규 엔티티를 추가해야 할 때
**When** Admin UI(Jinja2+HTMX)에서 고객·플랫폼 계정·모니터링 대상·메시지 채널·전송 규칙 생성/편집 폼을 제공하면(FR-4, ADD-10)
**Then** 각 엔티티를 ID 기반으로 생성·조회·수정할 수 있고
**And** 모니터링 대상은 플랫폼·계정·기대 센터/상점명·URL/식별자·연결된 브라우저 프로필을 입력받아 도메인 모델(Story 2.5) 계약과 일치하며
**And** 전송 규칙은 하나의 대상에서 하나 이상의 채널로 연결(1:N)되도록 생성할 수 있다(FR-9 토대).

**Given** 삭제 대신 비활성화를 지원해야 할 때
**When** 운영자가 엔티티를 비활성화하면(FR-4)
**Then** 물리 삭제가 아니라 soft delete/inactive 상태로 전환되어 운영 이력이 보존되고
**And** 비활성 대상은 자동으로 재활성화되지 않는다(FR-31 연계).

**Given** 입력값에 secret이나 잘못된 값이 들어올 수 있을 때
**When** 폼 입력을 검증·저장하면(NFR-8, ADD-15)
**Then** 토큰/비밀번호 같은 secret은 폼·DB 컬럼에 평문으로 저장되지 않고 `*_ref`로만 처리되며
**And** 쿠팡 기대 센터/상점명이 비었거나 배민 기본값이면 저장 시 위험 상태로 표시·경고된다(FR-20 연계)
**And** 모든 customer-owned 엔티티 생성/조회/수정은 tenant scope 필터를 통과한다(tenant isolation).

**Given** 운영 변경을 추적해야 할 때
**When** 운영자가 엔티티를 생성·수정·비활성화하면(FR-22, FR-34)
**Then** 변경이 audit log에 actor, source, before/after value(diff_redacted), target IDs, reason, timestamp, result로 기록되고
**And** 생성/편집/비활성화 권한은 역할(viewer는 읽기 전용, operator 이상만 변경)로 구분된다.
