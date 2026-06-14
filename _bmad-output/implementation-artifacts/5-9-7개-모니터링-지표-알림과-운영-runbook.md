---
baseline_commit: c17a3c4af4967c50b888290acba29b8461dd0145
---

# Story 5.9: 7개 모니터링 지표·알림과 운영 runbook

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 핵심 7개 지표를 노출하고 임계 초과 시 알림을 받으며 표준 runbook으로 대응하고 싶다,
so that 인증 만료·queue 지연·전송 오류·Agent offline을 사고로 번지기 전에 감지·대응할 수 있다.

## Acceptance Criteria

**AC1 — 7개 모니터링 지표 노출 (NFR-14, ADD-12, architecture 214-216)**

**Given** 운영 관측성이 필요할 때
**When** 모니터링 지표를 노출하면
**Then** 다음 7개 지표가 운영 스크레이프 가능한 형태(JSON 엔드포인트)로 노출된다:
1. `agent_last_heartbeat` — heartbeat가 **2분 초과** 누락이면 offline로 표시.
2. `target_last_success_at` — 마지막 수집 성공 경과가 interval **×2 → warning / ×4 → critical**.
3. `auth_required_count` — **≥1**이면 alert 대상.
4. `kakao_queue_lag_seconds` — **120초 초과**(반복/지속) 시 문제.
5. `crawl_error_rate_by_platform` — **최근 15분 실패율 30% 초과**(플랫폼별 BAEMIN/COUPANG).
6. `telegram_send_error_rate` — **최근 10분** 전송 오류 급증.
7. `gmail_reauth_required_count` — **≥1**이면 alert 대상.
**And** 지표 payload는 **집계 수치(count/rate/gauge)만** 담는다 — tenant_id·고객명·센터/상점명·target 식별 텍스트를 노출하지 않는다(unauthenticated scrape 경로에서 tenant 격리·redaction 정책 유지).

**AC2 — 최소 알림 (FR-34, architecture 79-80)**

**Given** 최소 알림이 필요할 때
**When** 임계가 초과되면
**Then** 최소 다음 4개 알림이 발생한다: `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required`.
**And** 알림 발화 판정은 **순수 함수**(시각·임계 주입)로 결정적이며, 임계는 AC1의 정본 임계값(2분/120초/30%/≥1)을 그대로 쓴다.

**AC3 — 운영 runbook과 장애 분류 (NFR-17, NFR-15, architecture 326-328)**

**Given** 장애 대응 절차가 필요할 때
**When** runbook을 작성하면
**Then** 최소 7종 runbook이 `docs/runbooks/`에 **파일로 존재**한다: `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required`, `profile_mismatch`, `kakao_ambiguous_room`, `duplicate_blocked`.
**And** 각 runbook은 증상→원인→조치→에스컬레이션을 담고, 장애 원인이 정본 `FailureCategory` 7종 — `CRAWL_FAILURE`/`AUTH_REQUIRED`/`RENDER_FAILURE`/`TELEGRAM_FAILURE`/`KAKAO_FAILURE`/`DUPLICATE_BLOCKED`/`TARGET_VALIDATION_FAILURE` — 으로 조치 가능하게 분류된다(NFR-15).

## Tasks / Subtasks

- [x] **Task 1: 순수 지표·알림 정책 모듈** `src/rider_server/metrics/policy.py` (AC1, AC2)
  - [x] 1.1 `MetricsSnapshot` frozen dataclass 정의 — 7개 지표의 **집계 facts**만(중립 타입): `agents_total`/`agents_offline`/`oldest_heartbeat_age_seconds`, `targets_warning`/`targets_critical`, `auth_required_count`, `kakao_queue_lag_seconds`(max), `crawl_error_rate_by_platform`(dict[str,float] BAEMIN/COUPANG + 표본수), `telegram_error_count`(10분 윈도), `gmail_reauth_required_count`. 고객명·센터명·target_id 등 식별 텍스트 **금지**.
  - [x] 1.2 임계 상수는 **기존 정본을 재사용**(재정의 금지·drift 방지): `AGENT_OFFLINE_AFTER`(=`rider_server.admin.severity.AGENT_OFFLINE_AFTER`, 2분), crawl 임계는 scheduler 정본(`DEFAULT_BREAKER_THRESHOLD=0.30`, `DEFAULT_BREAKER_MIN_SAMPLES`, `DEFAULT_BREAKER_WINDOW=15분`), `QUEUE_LAG_ALERT_SECONDS=120`(NFR-14), `TELEGRAM_ERROR_WINDOW=10분`, `AUTH_REQUIRED_ALERT_MIN=1`, `GMAIL_REAUTH_ALERT_MIN=1`. private 상수(`_TELEGRAM_ERROR_WINDOW`)는 import 불가하므로 동일값 재선언 + **identity/동등 테스트**로 잠근다.
  - [x] 1.3 `evaluate_alerts(snapshot, *, now=None) -> tuple[Alert, ...]` 순수 함수 — 4개 최소 알림 발화 판정. `agent_offline`(agents_offline≥1), `queue_lag`(kakao_queue_lag_seconds>120), `api_error_rate`(어느 플랫폼이든 crawl_error_rate>0.30 AND 표본≥min_samples, **또는** telegram_error 급증), `auth_required`(auth_required_count≥1 OR gmail_reauth_required_count≥1). `Alert`는 plain-string 코드 + 심각도(severity.py의 plain-string 상수 재사용, **enum 멤버 추가 금지**).
  - [x] 1.4 알림 코드는 plain-string 상수(`ALERT_AGENT_OFFLINE` 등) — 새 Enum 만들지 않는다(`test_domain_states` count-lock 무관 유지, 5.4/5.6 선례).

- [x] **Task 2: 지표 repository 포트 + 조립 서비스 + in-memory fake** `src/rider_server/metrics/service.py` (AC1)
  - [x] 2.1 `MetricsRepository(abc.ABC)` 포트 — **읽기 전용**(write 메서드 없음, 5.6 `DashboardRepository` 동형). 메서드는 중립 facts만 반환(ORM/SQL/AsyncSession 누출 0). DB I/O만 async.
  - [x] 2.2 `MetricsService` — repository facts를 `MetricsSnapshot`으로 조립. snapshot 조립은 sync 순수(시각 `now` 주입 → always-run 결정성).
  - [x] 2.3 `InMemoryMetricsRepository` — 무-DB 기본값 + `seed_*` 테스트 헬퍼(5.6 `InMemoryDashboardRepository` 선례). 런타임 read 경로는 read 메서드만.

- [x] **Task 3: PostgreSQL repository — 기존 집계 compose(신규 쿼리 최소화)** `src/rider_server/metrics/repository_postgres.py` (AC1)
  - [x] 3.1 `crawl_error_rate_by_platform`: scheduler `SchedulerRepository.platform_failure_window(platform, since, now)`를 **재사용**해 (total, failures) → rate 계산. 신규 쿼리 작성 금지 — 동일 윈도/임계(15분/30%/min_samples)로 `evaluate_breaker` 정본과 일치.
  - [x] 3.2 `kakao_queue_lag_seconds`·`telegram_error_count`: 5.6 `dashboard_repository_postgres`의 channel_health 집계 패턴 재사용(`MIN(Job.run_after)` KAKAO_SEND PENDING; `DeliveryLog.error_code==TELEGRAM_FAILURE` 10분 윈도). **fleet 집계**(전 tenant 합/최댓값)로 올린다 — 대시보드는 tenant scope지만 지표 엔드포인트는 비식별 fleet 수치.
  - [x] 3.3 `agent_last_heartbeat`: `agents.last_heartbeat_at` → `severity.is_agent_online`로 offline 카운트·최고령 heartbeat age. agents는 fleet 자원(tenant scope 아님).
  - [x] 3.4 `target_last_success_at`: 5.6 파생 집계(`MAX(snapshots.collected_at WHERE quality_state='OK')`) + `severity.classify_freshness`로 **warning/critical 대상 수**만 집계(개별 timestamp/이름 노출 금지).
  - [x] 3.5 `auth_required_count`: 5.6 `auth_required()` 집계 재사용(platform_accounts.auth_state·auth_sessions pending). `gmail_reauth_required_count`: `auth_sessions`(미해결 `resolved_at IS NULL`) ⨝ `platform_accounts.platform==COUPANG` 카운트. **주의**: rider_server에 Gmail 전용 enum이 없다(Platform=BAEMIN/COUPANG뿐). 서버가 별도 gmail-reauth 상태를 아직 기록하지 않으면 Coupang 미해결 auth_session pending 수로 근사하고 한계를 runbook에 명시(**0/근사값을 임의 enum 신설로 위조하지 않는다**).
  - [x] 3.6 신규 DB 컬럼/테이블 **추가 금지**(14표 lock). 모든 지표는 기존 테이블 파생 집계.

- [x] **Task 4: 운영 지표 엔드포인트 배선** `src/rider_server/main.py` (AC1, AC2)
  - [x] 4.1 `GET /metrics/operational` 라우트 추가(root-level, no `/v1/`) — `MetricsSnapshot` + 발화 알림(`evaluate_alerts` 결과)을 JSON으로 반환. snake_case·ISO8601 UTC·집계 수치만.
  - [x] 4.2 `app.state.metrics_repository` seam 추가(기본 `_default_metrics_repository(settings)` → PG, 테스트 주입 가능). 5.6 `dashboard_repository` seam 패턴 동형.
  - [x] 4.3 라우트는 실 `datetime.now(timezone.utc)` 사용(주입 아님 — 5.6/5.7 라우트 선례). 시간 의존 단정은 순수 policy/service 레이어에서만, 라우트 테스트는 shape/키/알림 존재만.
  - [x] 4.4 기존 `/metrics`(5.1: app_version/uptime/server_time) **무변경 유지** — `test_server_app.py::test_metrics_minimal_extensible_shape` 회귀 없음(별도 엔드포인트로 분리해 dependency-free liveness/scrape 계약 보존; `/health` DB 비의존 유지).

- [x] **Task 5: 7종 운영 runbook 작성** `docs/runbooks/` (AC3)
  - [x] 5.1 `agent_offline.md` — 증상(heartbeat 2분 초과), 원인(PC 종료/네트워크/프로세스 죽음), 조치(autostart·재부팅 heartbeat 복구 확인, Story 4.7 연계), 에스컬레이션.
  - [x] 5.2 `queue_lag.md` — kakao_queue_lag>120초. 원인(Kakao FIFO 단일 세션 적체·창 검증 실패), 조치(Agent Kakao 세션 상태, 모호한 방명 검증 확인).
  - [x] 5.3 `api_error_rate.md` — crawl 15분 30% 초과 / telegram 10분 급증. circuit breaker open 의미, 사이트 구조 변경·로그인 만료를 정상 운영 위험으로 분류.
  - [x] 5.4 `auth_required.md` — auth_required_count≥1 / gmail_reauth≥1. 배민 사람 개입 재인증(4.8), 쿠팡 Gmail 2FA(4.9). **무한 재인증 요청 금지** 정책 재확인.
  - [x] 5.5 `profile_mismatch.md` — 기대 센터/상점명 불일치(`TARGET_VALIDATION_FAILURE`/`CENTER_MISMATCH`). 다른 계정 실적 오발송 위험 → fail-closed 미발송.
  - [x] 5.6 `kakao_ambiguous_room.md` — 동명/모호 방 → 미발송. 임의 창 전송 금지(KAKAO_FAILURE 분류).
  - [x] 5.7 `duplicate_blocked.md` — dedup 차단(`DUPLICATE_BLOCKED`). 정상 idempotency 동작 vs 의심 케이스 구분, crash-after-send 안전.
  - [x] 5.8 각 runbook이 정본 `FailureCategory` 7종 코드를 명시 참조하도록 작성(NFR-15 분류 계약). 기존 `docs/runbooks/backup-restore.md` 헤더·스타일 일치.

- [x] **Task 6: 테스트** (AC1, AC2, AC3)
  - [x] 6.1 `tests/server/test_metrics_policy.py`(always-run, DB 없음) — `evaluate_alerts` 4개 최소 알림 경계: 2분 정확/초과, 120초 정확/초과, 30%+min_samples(1/1=100% false-positive 차단), auth≥1, gmail≥1. 임계 상수 재사용 identity/동등(scheduler/severity 정본과 동일값) 잠금.
  - [x] 6.2 `tests/server/test_metrics_service.py`(always-run, in-memory) — seed→`MetricsSnapshot` 조립 정확성, snapshot에 식별 텍스트 부재(비식별 보장), `/metrics/operational` 라우트 shape/키/알림 배열(`TestClient`).
  - [x] 6.3 `tests/negative/test_metrics_repository_pg.py`(PG-gated, `skipif not TEST_DATABASE_URL`) — 실 집계: crawl rate window, kakao lag, telegram 10분, auth_required, gmail reauth fleet 카운트. cross-tenant 합산이 식별정보 누출 없이 fleet 수치로만 나오는지.
  - [x] 6.4 `tests/server/test_runbooks_present.py`(always-run) — 7종 runbook 파일 존재 + 각 파일이 해당 `FailureCategory` 코드 문자열 참조(AC3 완료 위조 차단 — "lying about completion" 방지).
  - [x] 6.5 (선택) `tests/server/test_metrics_boundary.py` — import 가드: `metrics/`가 `rider_agent` import 0, write/상태전이 호출 0(5.4/5.6 boundary 선례 AST 가드).

## Dev Notes

### 이 스토리의 본질: 데이터는 이미 있다 — 얇은 지표/알림 레이어 + runbook 문서

5.1~5.8이 7개 지표의 **원천 데이터와 집계 로직을 이미 만들어 두었다.** 이 스토리는 새 계측을 발명하는 게 아니라 **흩어진 기존 집계를 비식별 fleet 지표로 조립·노출**하고, **순수 알림 판정**을 추가하고, **운영 runbook 문서**를 쓰는 일이다. 핵심 위험은 (a) 이미 있는 집계를 재구현해 임계가 drift하는 것, (b) unauthenticated scrape 엔드포인트에 고객 식별정보를 흘리는 것, (c) DB 컬럼/enum을 늘려 14표·count-lock을 깨는 것이다. 셋 다 아래 가드레일로 차단한다.

### 핵심 정본(검증 완료) — 절대 틀리면 안 되는 이름

- **심각도 어휘(plain-string, enum 아님)**: `SEVERITY_NORMAL/WARNING/CRITICAL/STOPPED` (`src/rider_server/admin/severity.py:30-33`). 새 심각도/알림은 **plain-string 상수**로 둔다 — `domain/states.py` enum에 멤버 추가 금지(5.4 `BREAKER_OPEN`/5.6 severity 선례).
- **장애 분류 정본 `FailureCategory` 7종**(`domain/states.py:165-186`): `CRAWL_FAILURE`, `AUTH_REQUIRED`, `RENDER_FAILURE`, `TELEGRAM_FAILURE`, `KAKAO_FAILURE`, `DUPLICATE_BLOCKED`, `TARGET_VALIDATION_FAILURE`. **정확히 7 멤버**이며 `test_domain_states`가 count-lock한다 — 멤버 추가/이름 변경 금지. runbook은 이 7 문자열을 그대로 참조한다(NFR-15).
- **Agent offline 임계**: `AGENT_OFFLINE_AFTER = timedelta(minutes=2)` (`severity.py:47`), 판정 `is_agent_online(last_heartbeat_at, now)` — 정확히 2분 경과는 online("more than 2 minutes"=초과 `>`만 offline).
- **freshness 임계**: `classify_freshness(last_success_at, interval_minutes, now)` — `>interval×4`→CRITICAL, `>interval×2`→WARNING, None→WARNING, interval≤0→NORMAL. 경계는 "초과(>)".
- **crawl 임계 정본(scheduler)**: `DEFAULT_BREAKER_THRESHOLD=0.30`, `DEFAULT_BREAKER_MIN_SAMPLES`, `DEFAULT_BREAKER_WINDOW=15분` (`scheduler/service.py:40` + `scheduler/policy.py:164 evaluate_breaker`). min_samples가 1/1=100% false-positive를 막는다 — 반드시 함께 적용.
- **telegram 오류 윈도**: 10분(`dashboard_repository_postgres` `_TELEGRAM_ERROR_WINDOW`, private — 동일값 재선언 후 동등 테스트로 잠금). kakao queue lag와 telegram error는 **별도 필드**(혼합 금지, 5.6 `ChannelHealthRow` 계약).
- **queue lag 임계**: 120초(NFR-14 "120s 반복 초과").
- **DB 컬럼/시각 정본**: `agents.last_heartbeat_at`, `snapshots.collected_at`(+`quality_state='OK'`), `jobs.run_after`(KAKAO_SEND PENDING), `delivery_logs.error_code`/`sent_at`, `auth_sessions.state`/`reason`/`resolved_at`/`account_id`, `platform_accounts.auth_state`/`platform`.
- **엔드포인트**: 운영 엔드포인트는 root-level(no `/v1/`). 기존 `/health`(DB 비의존 liveness)·`/version`·`/metrics`(app_version/uptime/server_time)는 무변경.

### 🚨 가드레일(위반 시 CI 실패·리뷰 반려) — 우선순위 순

1. **14표·count-lock 불변**: 신규 DB 컬럼/테이블/Alembic 마이그레이션 **0**. 모든 지표는 기존 테이블 파생 집계. `domain/states.py` enum 멤버 수 불변(`test_domain_states`). `FailureCategory` 7·`AdminRole` 4·`AuditResult` 등 기존 count-lock 무회귀.
2. **tenant 격리·redaction(unauthenticated scrape)**: `/metrics/operational` payload에 tenant_id·고객명·**센터/상점명**·target_id·방명 등 **식별 텍스트 금지** — 집계 수치(count/rate/gauge)만. ⚠️ `redact()`는 room/center/store **이름을 마스킹하지 않으므로**(운영 ID 비마스킹), redaction에 의존하지 말고 **애초에 이름/ID를 payload에 담지 않는다**. secret(token/OTP/password) 평문 0.
3. **임계 drift 0(재사용 강제)**: crawl 임계·윈도는 scheduler 정본 재사용, agent offline은 `severity` 정본 재사용. 같은 의미의 임계를 두 곳에서 다른 값으로 두지 않는다 — identity/동등 테스트로 잠금.
4. **읽기 전용**: `metrics/` 모듈은 상태를 바꾸지 않는다 — repository 포트에 write/save/enqueue/상태전이 없음. service/enqueue 호출 0(boundary 가드).
5. **async/sync 경계**: DB I/O만 async, 집계·알림 판정은 sync 순수(`now` 주입). async 라우트에서 blocking sync 직접 호출 금지.
6. **import 단방향**: `rider_server`는 `rider_agent`를 import하지 않는다(`metrics/`도 동일, boundary 가드).
7. **위조 완료 차단**: AC3는 "7 runbook 파일 존재 + FailureCategory 참조"를 `test_runbooks_present.py`로 강제 — 파일 없이 done 처리 불가.

### 재사용 자산(재구현 금지 — compose/import만)

| 필요 | 재사용 대상(정본) | 위치 |
| --- | --- | --- |
| agent offline 판정·2분 임계 | `is_agent_online`, `AGENT_OFFLINE_AFTER` | `admin/severity.py:47,183` |
| target freshness ×2/×4 | `classify_freshness` | `admin/severity.py:54` |
| fail-closed 신호(auth/center/kakao) | `failclosed_signals_from` | `admin/severity.py:107` |
| crawl 실패율 15분 윈도 | `SchedulerRepository.platform_failure_window` | `scheduler/postgres_repository.py:128`, `scheduler/service.py:102` |
| crawl 30%+min_samples 판정 | `evaluate_breaker`, `DEFAULT_BREAKER_*` | `scheduler/policy.py:164`, `scheduler/service.py:40` |
| kakao lag·telegram error 집계 | dashboard channel_health 패턴 | `admin/dashboard_repository_postgres.py` |
| auth_required 집계 | `DashboardRepository.auth_required` | `admin/dashboard_service.py:147` + PG impl |
| repository 포트 + in-memory fake 패턴 | `DashboardRepository`/`InMemoryDashboardRepository` | `admin/dashboard_service.py:120,231` |
| 엔드포인트 seam 패턴 | `dashboard_repository` seam, `_iso_utc_now` | `main.py:108,136,197` |
| runbook 스타일/헤더 | `backup-restore.md` | `docs/runbooks/backup-restore.md` |
| redaction(보조) | `redact`/`redacted_error_event` | `rider_crawl.redaction` |

**경계 주의(같은 이름·다른 레이어)**: `FailureCategory.AUTH_REQUIRED`(delivery error_code) ≠ `BaeminAuthState.AUTH_REQUIRED`(계정 인증) ≠ `CustomerLifecycleState.AUTH_REQUIRED`(고객 lifecycle) — **문자열 값**으로만 비교(`severity.failclosed_signals_from` 선례). Gmail reauth는 **쿠팡 전용·agent-side** 개념이며 spec plain-string(`GMAIL_REAUTH_REQUIRED`/`USER_ACTION_REQUIRED`)으로, rider_server enum이 아니다 — 서버는 `auth_sessions`(Coupang account, 미해결)로만 근사한다.

### Project Structure Notes

- 신규 subpackage `src/rider_server/metrics/`(`__init__.py`, `policy.py`, `service.py`, `repository_postgres.py`) — 5.4 `scheduler/`·5.6 `admin/` 구조 동형(정책 분리: 순수 policy / async service+port / PG impl).
- 테스트: `tests/server/test_metrics_policy.py`·`test_metrics_service.py`·`test_runbooks_present.py`(always-run), `tests/negative/test_metrics_repository_pg.py`(PG-gated). 기존 `tests/server/`·`tests/negative/` 미러 구조 준수.
- runbook: `docs/runbooks/{agent_offline,queue_lag,api_error_rate,auth_required,profile_mismatch,kakao_ambiguous_room,duplicate_blocked}.md`.
- `pyproject.toml`: 신규 third-party 의존성 **불필요**(stdlib + 기존 server extra로 충분). prometheus_client 등 도입 금지 — JSON 집계 노출로 MVP 충족.

### 열린 질문(구현 시 결정 — 막지 말고 가장 안전한 선택)

- **엔드포인트 분리 vs `/metrics` 확장**: `/metrics/operational` **신규 엔드포인트**를 권장(채택). 이유: `/metrics`(5.1)·`/health`는 DB 비의존 liveness/scrape 계약이라 DB 집계를 섞으면 DB 장애 시 liveness가 깨진다. operational 지표는 DB 의존이므로 분리. (대안으로 `/metrics`에 additive 키만 더하는 것도 가능하나, dependency-free 계약 보존 위해 분리 채택.)
- **operational 엔드포인트 인증**: root-level **unauthenticated scrape**(CloudWatch agent 모델) 유지 — 단 payload는 비식별 fleet 집계만이라 노출 위험 없음. 의심되면 더 제한적으로(내부 네트워크/스크레이프 토큰) 가되, **식별정보를 담지 않는 것이 1차 방어선**.
- **실 알림 채널(CloudWatch alarm) 배선**: 본 스토리의 테스트 가능한 산출물은 **순수 `evaluate_alerts` + 엔드포인트 알림 배열**이다. 실제 CloudWatch alarm 생성은 인프라(`deploy/`)·운영 설정 영역 → runbook/배포 문서에 "이 임계로 알람을 건다"를 기록하고, 테스트에서 외부 알람을 위조하지 않는다.
- **gmail_reauth 근사**: 서버가 쿠팡 Gmail reauth를 별도 상태로 아직 기록하지 않으면 `auth_sessions`(COUPANG account, `resolved_at IS NULL`) 카운트로 근사하고 한계를 `auth_required.md`에 명시. 임의 enum/컬럼 신설 금지.

### 개발 환경·프로세스(매 스토리 반복되는 함정)

- pytest 실행: WSL에서 `.venv/Scripts/python.exe -m pytest`(Windows venv). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`).
- PG-gated 테스트는 `TEST_DATABASE_URL` 없으면 skip — **always-run 테스트가 순수 의미(임계·비식별·알림 경계)를 잠그도록** 설계(PG-skip CI에서도 의미 검증). PG-gated 파일에 순수 헬퍼를 숨기지 말 것.
- 라우트는 실 `now()` 사용 → 라우트 테스트로 시간 의존(warning/critical 시점)을 단정하지 말고 순수 policy/service에서 `now` 주입으로 단정.
- git tree가 CRLF/LF noisy — 변경 검증은 `git diff -w`. 관련 없는 변경 되돌리지 않기.
- 완료 전 전체 회귀 실행 후 **실측 test count**를 Completion Notes에 기록(dev 시점과 qa-e2e 시점이 다를 수 있으니 리뷰에서 재측정).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.9] — AC 정본(7지표 임계, 4 최소 알림, 7 runbook + NFR-15 분류).
- [Source: _bmad-output/planning-artifacts/epics.md:102] NFR-14(7지표·임계), :105 NFR-17(runbook 최소 세트), :116 NFR-15(원인 분류).
- [Source: _bmad-output/planning-artifacts/architecture.md:214-218] CloudWatch + 7지표 정본·측정 트리거.
- [Source: _bmad-output/planning-artifacts/architecture.md:79-80,326-332] 관측성 cross-cutting + 에러 분류 정본(7 카테고리)·fail-closed.
- [Source: src/rider_server/admin/severity.py:30-47,54-83,107-145,183-197] 심각도/임계/offline/fail-closed 정본(재사용).
- [Source: src/rider_server/scheduler/policy.py:164-182] `evaluate_breaker`(30%+min_samples). [Source: src/rider_server/scheduler/service.py:40,102-165] 15분 윈도·`platform_failure_window`(재사용).
- [Source: src/rider_server/admin/dashboard_service.py:94-148] channel_health(kakao lag/telegram error 별도)·auth_required 포트(재사용). [Source: src/rider_server/admin/dashboard_repository_postgres.py] PG 집계 패턴.
- [Source: src/rider_server/domain/states.py:165-186] `FailureCategory` 7종 count-lock 정본(runbook 참조).
- [Source: src/rider_server/db/models/account.py:53-61] `AuthSession.account_id/state/reason/resolved_at`(gmail reauth 근사 출처).
- [Source: src/rider_server/main.py:217-242] 기존 `/health`·`/version`·`/metrics`(무변경), seam 패턴(`dashboard_repository`, `_iso_utc_now`).
- [Source: docs/runbooks/backup-restore.md] runbook 헤더·스타일 선례(Story 5.8).
- [Source: _bmad-output/project-context.md] 프로젝트 56규칙(redaction·secret·import 단방향·sync/async 경계·기존 동작 보존).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 전체 회귀(dev 시점): `.venv/Scripts/python.exe -m pytest -q` → **1854 passed, 48 skipped, 0 failed**
  (5.8 done 시점 1815 → +39 always-run 신규. PG-gated 7건은 `TEST_DATABASE_URL` 미설정으로 skip).
- **전체 회귀(리뷰 시점 재측정, 2026-06-14): `1862 passed, 48 skipped, 0 failed`** — dev 기록
  1854 대비 +8. qa-e2e 보강(`test_metrics_policy.py`·`test_metrics_service.py` 의 "qa-e2e 보강"
  섹션)이 dev 메모 작성 후 always-run 8건을 추가했다(memory/stale-test-count-a2 패턴).
- 신규 지표 테스트(리뷰 재측정): `test_metrics_policy.py`(23) + `test_metrics_service.py`(13) +
  `test_runbooks_present.py`(4) + `test_metrics_boundary.py`(7) = **47 always-run passed**,
  `test_metrics_repository_pg.py`(7) PG-gated skipped → 합 **54 신규**(dev 기록 46 → +8).
- boundary 가드 초안에서 `"enqueue" not in source` raw 매칭이 service.py docstring(금지 심볼을
  문자열로 명시)을 오탐 → 호출 형태 `.enqueue(` 로 좁혀 해결(negative-guard-tests-use-ast 선례).

### Completion Notes List

**구현 요약(얇은 지표/알림 레이어 + runbook — 데이터·집계는 5.1~5.8이 이미 보유):**

- **Task 1 (policy.py, 순수):** `MetricsSnapshot`(frozen, 7지표 비식별 집계 facts만 — 식별
  텍스트 필드 0), `evaluate_alerts`(4 최소 알림 `agent_offline`/`queue_lag`/`api_error_rate`/
  `auth_required`, plain-string 코드). 임계는 **정본 재사용**: `AGENT_OFFLINE_AFTER` 는
  `severity` 와 **동일 객체**, crawl 임계는 `scheduler.policy.DEFAULT_BREAKER_*` 동일 객체,
  window(15분/10분)는 동일값 재선언 후 동등 테스트로 잠금. `crawl_alert_open` 은
  `evaluate_breaker` 와 전수 동등(0..8 표본 grid) 잠금 — 임계 drift 0.
- **Task 2 (service.py):** `MetricsRepository`(abc, **읽기 전용** — write 메서드 없음),
  `MetricsService.assemble`(sync 순수, `now` 주입) + `.snapshot`(async DB I/O),
  `InMemoryMetricsRepository`(seed_* 헬퍼). repo facts 도 이름/target_id 미포함(비식별 1차 방어선).
- **Task 3 (repository_postgres.py):** crawl 은 `PostgresSchedulerRepository.platform_failure_window`
  **재사용**(신규 쿼리 0), kakao lag·telegram error 는 5.6 패턴을 **fleet 집계**(전 tenant
  합/최댓값, tenant scope 제거)로 올림, freshness 는 `MAX(snapshots WHERE OK)` 파생(이름/
  target_id 미SELECT), gmail_reauth 는 쿠팡 미해결 `auth_session` 근사. **신규 DB 컬럼/테이블 0**.
- **Task 4 (main.py):** `GET /metrics/operational` 신규 엔드포인트(root-level, 실 `now`),
  `app.state.metrics_repository` seam(`create_app(metrics_repository=...)` 주입). 기존 `/metrics`
  (5.1)·`/health`·`/version` **무변경**(dependency-free liveness 계약 보존 — DB 의존 분리).
- **Task 5 (docs/runbooks/):** 7종 작성, 각 파일이 해당 정본 `FailureCategory` 코드 명시 참조
  (7 카테고리 전체 커버). `auth_required.md` 에 gmail_reauth 근사의 한계 정직 명시.
- **Task 6 (tests):** policy(23)/service(13)/runbooks(4)/boundary(7) always-run **47** + PG-gated(7)
  = **54 신규**(리뷰 재측정값; dev 메모 39+7 은 qa-e2e 보강 8건 추가 전 수치 — 위 Debug Log 참조).

**AC 충족:** AC1(7지표 JSON 노출·집계 수치만·식별 텍스트 0), AC2(4 최소 알림·순수 결정적·정본
임계), AC3(7 runbook 파일 + FailureCategory 7종 분류, `test_runbooks_present` 가 완료 위조 차단).

**가드레일 준수:** 14표·count-lock 불변(신규 컬럼/테이블/enum 멤버 0), tenant 격리·비식별
(payload 집계 수치만), 임계 drift 0(재사용+동등 잠금), 읽기 전용(boundary 가드), import 단방향
(`metrics` → `rider_agent` import 0), `/metrics` 회귀 0.

**리뷰어 주의:** PG-gated 7건은 `TEST_DATABASE_URL` 부재로 dev에서 skip — always-run 단위가
임계·조립·비식별·알림 경계를 잠그도록 설계했으나 실 SQL 파생 집계(fleet 합/최댓값)는 PG 환경에서
재측정 권장. `evaluate_alerts(now=)` 는 시그니처 대칭용(현재 미사용 — facts가 조립 단계에서 시각
해석 완료).

### File List

**신규:**
- `src/rider_server/metrics/__init__.py`
- `src/rider_server/metrics/policy.py`
- `src/rider_server/metrics/service.py`
- `src/rider_server/metrics/repository_postgres.py`
- `docs/runbooks/agent_offline.md`
- `docs/runbooks/queue_lag.md`
- `docs/runbooks/api_error_rate.md`
- `docs/runbooks/auth_required.md`
- `docs/runbooks/profile_mismatch.md`
- `docs/runbooks/kakao_ambiguous_room.md`
- `docs/runbooks/duplicate_blocked.md`
- `tests/server/test_metrics_policy.py`
- `tests/server/test_metrics_service.py`
- `tests/server/test_runbooks_present.py`
- `tests/server/test_metrics_boundary.py`
- `tests/negative/test_metrics_repository_pg.py`

**수정:**
- `src/rider_server/main.py` — `/metrics/operational` 라우트 + `_default_metrics_repository`
  seam + `create_app(metrics_repository=...)` 파라미터(기존 `/metrics`·`/health`·`/version` 무변경).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 5.9 backlog → in-progress → review.
- `_bmad-output/implementation-artifacts/5-9-...md`(본 파일) — frontmatter `baseline_commit`,
  checkbox, Dev Agent Record, Status.

### Change Log

| 날짜 | 변경 | 비고 |
| --- | --- | --- |
| 2026-06-14 | Story 5.9 dev-story 구현 완료 | metrics 패키지(policy/service/PG repo) + `/metrics/operational` 엔드포인트 + 7 runbook + 46 테스트(39 always-run·7 PG-gated). 전체 회귀 1854 passed/48 skipped/0 failed. 신규 DB 변경·enum 멤버 0(14표·count-lock 불변), 임계 정본 재사용(drift 0), 비식별 fleet 집계, 읽기 전용. Status → review. |
| 2026-06-14 | Senior Developer Review (AI) — **승인(Approve)** | 어드버서리얼 리뷰: AC1·AC2·AC3 전수 코드 대조 통과, 6개 Task `[x]` 전부 실구현 확인(위조 0). 정본 재사용(severity/scheduler) identity·동등 잠금 검증, 비식별 payload(식별 텍스트 0) 검증, 14표·count-lock·import 단방향·읽기 전용 가드 통과. 전체 회귀 **재측정 1862 passed/48 skipped/0 failed**. CRITICAL 0·HIGH 0. MEDIUM 1(테스트 카운트 stale — 본 리뷰에서 정정) fix 완료. LOW 2(설계 의도로 유지). Status → done. |

## Senior Developer Review (AI)

**리뷰어:** Noah Lee · **일자:** 2026-06-14 · **결과:** ✅ 승인(Approve) · **Status:** review → done

### 요약

데이터·집계는 5.1~5.8 자산을 **재사용**하고, 이 스토리는 비식별 fleet 지표 조립 + 순수 알림
판정 + 운영 runbook 문서를 더하는 얇은 레이어다. 6개 Task 의 `[x]` 표기를 실제 코드와 1:1
대조한 결과 **전부 실구현**되었고(완료 위조 0), AC1·AC2·AC3 의 핵심 불변식(비식별·임계 drift
0·14표·읽기 전용·import 단방향)이 코드와 테스트로 잠겨 있다. CRITICAL/HIGH 없음.

### AC 검증(코드 근거)

- **AC1(7지표 노출·집계 수치만):** `MetricsSnapshot.to_payload()`(`metrics/policy.py:117`)가 7
  지표를 집계 수치/None 만으로 노출. `GET /metrics/operational`(`main.py`)이 snake_case·ISO8601.
  식별 텍스트 부재는 `test_metrics_service.py::test_payload_holds_only_aggregate_numbers_no_identifiers`
  와 `::test_snapshot_dataclass_has_no_identifying_fields` 로 잠금. ✅
- **AC2(4 최소 알림·순수 결정적):** `evaluate_alerts`(`metrics/policy.py:166`)가
  `agent_offline`/`queue_lag`/`api_error_rate`/`auth_required` 발화. 임계는 `severity`·`scheduler`
  정본 **재사용**(identity/동등 테스트), `crawl_alert_open` 은 `evaluate_breaker` 와 0..8 표본
  grid 전수 동등. 경계(2분·120초·30%+min_samples·≥1) 전수 테스트. ✅
- **AC3(7 runbook + FailureCategory):** `docs/runbooks/` 7파일 존재·정본 7 카테고리 전수 커버,
  `test_runbooks_present.py` 가 파일 존재 + 코드 참조를 강제(완료 위조 차단). ✅

### 정본 재사용·가드 검증

- agent offline 2분: `policy.AGENT_OFFLINE_AFTER is severity.AGENT_OFFLINE_AFTER`(동일 객체). ✅
- crawl 30%+min_samples(5)·15분, telegram 10분: scheduler/dashboard 정본과 동등 잠금. ✅
- crawl 윈도 쿼리: `PostgresSchedulerRepository.platform_failure_window` 재사용(신규 쿼리 0). ✅
- 14표·count-lock: 신규 DB 컬럼/테이블/Alembic·enum 멤버 0(`test_domain_states` 무회귀). ✅
- 읽기 전용·import 단방향: `test_metrics_boundary.py`(AST edge) — write SQL 동사·`rider_agent`
  import·`.commit(`/`.enqueue(` 0. ✅
- 기존 `/metrics`(5.1)·`/health`·`/version` 무변경(`test_metrics_service.py` 회귀). ✅

### 발견 사항

- **[MEDIUM][정정 완료]** Dev Agent Record(Debug Log/Completion Notes/Change Log)의 테스트
  카운트가 stale: dev 기록 `1854 passed`·`always-run 39`·`신규 46`. 리뷰 재측정 `1862 passed,
  48 skipped, 0 failed`·`always-run 47`·`신규 54`. 원인은 qa-e2e 보강 8건이 dev 메모 후 추가됨
  (memory/stale-test-count-a2). → 본 리뷰에서 Debug Log·Completion Notes·Change Log 정정.
- **[LOW][유지]** AC1 #6 정본 명칭은 `telegram_send_error_rate`(rate)이나 구현/노출은
  `telegram_error_count`(count). Task 1.1 이 의도적으로 count 로 명세했고, 기능 의도("최근 10분
  급증 감지")는 count 임계(≥1, fail-loud)로 충족된다. 진짜 rate 는 분모(총 전송수) 쿼리가 필요
  해 MVP 범위 밖 — 계약상 count 유지가 타당. 코드 변경 없음(명칭·의미 차이만 명시).
- **[LOW][유지]** `evaluate_alerts(snapshot, *, now=None)` 의 `now` 는 `del now` 로 즉시 폐기되는
  forward-compat 시그니처 시임(테스트 `test_evaluate_alerts_accepts_now_kwarg` 가 호출 안전성만
  단언). facts 가 조립 단계에서 시각 해석을 끝내므로 현재 분기 미사용 — 의도된 대칭 시임, 유지.
- **(범위 외)** git 에 `_bmad-output/.../tests/test-summary.md`·`story-automator/orchestration-*.md`
  변경이 File List 에 미기재되나, 둘 다 `_bmad-output/`(자동화 산출물)이라 리뷰 범위 제외 — 실
  소스 File List 는 git 현실과 일치.

### 결론

AC 3/3 충족, Task 6/6 실구현, 가드레일 전수 통과, 회귀 0. CRITICAL 0 → **Status: done**.
