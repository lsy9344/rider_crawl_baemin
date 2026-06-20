# 구현 검토 후속 작업 지시서

완료 상태: 작업지시서의 후속 작업을 끝냈따.

작성일: 2026-06-19
대상: `docs/goal/done_*` 검토결과/작업지시서 반영 후 현재 작업트리
목적: 완료 표시된 작업 중 실제 구현이 부족하거나 수락 기준을 만족하지 못한 항목을 보완한다.

## 검토 요약

아래 핵심 테스트 묶음은 통과했다.

```powershell
uv run pytest -q tests/server/test_admin_dashboard.py tests/server/test_scheduler_tick.py tests/server/test_queue_backend.py tests/server/test_jobs_api.py tests/server/test_agents_api.py tests/agent/test_crawl_worker.py tests/agent/test_browser_profile.py tests/agent/test_job_loop.py tests/server/test_db_schema.py tests/server/test_migration.py
```

결과:

```text
365 passed, 22 skipped
```

다만 테스트가 통과해도 다음 수락 기준은 아직 충족하지 못했다.

- Telegram dispatch outbox가 기본 운영 경로에서 실제 발송으로 이어지지 않는다.
- legacy 설정 migration이 평문 계정 값을 `PlatformAccount`에 다시 넣을 수 있다.
- dashboard pagination이 DB/read-model 조회 뒤에만 적용된다.
- Agent crawl timeout이 실제 hard kill/process boundary가 아니라 daemon thread timeout이다.
- UI 설정 편의성 작업의 핵심인 DB 실패 안내, 새 고객 세팅 흐름, 실발송 전 테스트 게이트가 미완성이다.

## 작업 원칙

- 기존 사용자 변경을 되돌리지 않는다.
- 먼저 실패 테스트를 추가하고, 그 테스트가 실제로 실패하는 것을 확인한 뒤 구현한다.
- secret 평문을 새 fixture, 로그, 문서 예시에 넣지 않는다. 필요하면 명백한 가짜값만 쓰고 redaction을 검증한다.
- 운영 기능을 숨은 FastAPI background task로 붙이지 않는다. 별도 worker, 명시 CLI, compose service 중 하나로 실행 위치를 분명히 한다.
- UI 개선은 기존 숙련자용 CRUD 화면을 한 번에 삭제하지 않는다. 새 흐름을 추가하고 기존 화면은 고급/기존 방식으로 유지한다.

## 완료 기준

- 기본 DB 경로에서 snapshot ingest 후 Telegram delivery가 worker claim 대상이 되고 실제 전송 worker가 실행 가능하다.
- dispatch worker는 full message body를 보내거나, 전송 시점에 안전하게 full body를 재생성한다. redacted preview를 실전송 본문으로 쓰지 않는다.
- ambiguous Telegram 실패는 기존 중복 전송 방지 정책을 유지한다.
- delivery lock은 worker crash 후 만료되어 재claim 가능하다.
- legacy migration 결과도 secret ref만 `PlatformAccount`에 저장한다.
- dashboard target fragment는 DB/read-model 단계에서 limit/cursor를 적용한다.
- crawl timeout은 stuck browser work를 실제로 종료할 수 있는 process boundary 또는 명시적으로 검증된 kill 경계를 가진다.
- `/admin` DB 연결 실패는 내부 trace/JSON 500 대신 운영자용 HTML 안내를 반환한다.
- 관리 탭에는 새 고객 세팅 시작 흐름, 테스트 완료 상태, 실발송 전 gate가 보인다.
- 후속 테스트와 `docs`/`architecture` 검증이 통과한다.

---

## Task 1: P0 Telegram dispatch outbox 기본 경로 복구

**문제:**

- `src/rider_server/main.py`의 기본 ingest service는 `PostgresSnapshotIngestRepository(factory)`를 sender 없이 만든다.
- `src/rider_server/services/snapshot_repository_postgres.py`는 sender가 없으면 Telegram delivery log를 `HELD`로 저장한다.
- `src/rider_server/services/dispatch_worker.py`는 `RETRYING`만 claim한다.
- 결과적으로 snapshot ingest와 dispatch를 분리한 뒤 기본 운영 경로에서 Telegram 발송이 멈출 수 있다.

**Files:**

- Modify: `src/rider_server/services/snapshot_repository_postgres.py`
- Modify/Create: `src/rider_server/services/dispatch_worker.py`
- Modify/Create: `src/rider_server/dispatch/__main__.py` 또는 동등한 명시 CLI
- Modify: `src/rider_server/main.py`
- Modify: `src/rider_server/runtime.py`
- Modify: `deploy/docker-compose.yml`
- Test: `tests/server/test_snapshot_telegram_runtime.py`
- Test: `tests/server/test_telegram_central_dispatch.py`
- Test: `tests/server/test_postgres_runtime_guards.py`

- [ ] **Step 1: 기본 ingest가 worker-claim 가능한 delivery log를 만드는 실패 테스트 추가**

Required assertions:

- sender 없는 기본 `PostgresSnapshotIngestRepository`가 Telegram delivery log를 `RETRYING` 또는 새 `PENDING` 등 worker claim 대상 상태로 만든다.
- `HELD`는 "설정 부족/사람 개입 필요"일 때만 사용한다.
- `TelegramDispatchWorker.claim_pending()`이 해당 row를 claim한다.

- [ ] **Step 2: dispatch worker 실행 위치 배선**

Choose one:

- separate compose service, for example `telegram-dispatch`
- explicit CLI, for example `python -m rider_server.dispatch --interval-seconds 5`
- scheduler-maintenance command with documented call site

Required:

- Docker compose 또는 runbook에서 실행 방법이 보인다.
- `RuntimeDeps`에 dispatch worker 또는 factory 의존성이 드러난다.
- 숨은 FastAPI request-process background task로 만들지 않는다.

- [ ] **Step 3: redacted preview 전송 금지**

현재 worker는 `Message.text_redacted_preview`를 `message_text`로 사용한다. 다음 중 하나로 고친다.

- DB에 full message body를 저장하되 secret/PII 정책을 재검토하고 redaction 테스트를 추가한다.
- 전송 시점에 snapshot + template_version으로 full body를 재생성한다.

Required assertions:

- Telegram sender가 받는 본문은 preview가 아니라 실제 전송 본문이다.
- Admin preview와 전송 본문의 역할이 테스트에서 분리된다.

- [ ] **Step 4: ambiguous failure idempotency 유지**

Required assertions:

- 기존 `CentralTelegramSender`의 ambiguous failure 정책을 worker 경로에서도 유지한다.
- 성공 여부가 애매한 예외는 중복 전송을 만들지 않는다.
- `reserve`/`release`를 no-op로 두지 말고 기존 idempotency service 또는 DB dedup 상태와 연결한다.

- [ ] **Step 5: delivery lock expiry 추가**

Required behavior:

- `locked_at IS NULL OR locked_at <= now - lock_timeout` row를 claim할 수 있다.
- worker crash 후 다음 worker가 오래된 lock을 회수한다.
- lock timeout은 설정 가능하거나 합리적 기본값을 가진다.

검증:

```powershell
uv run pytest -q tests/server/test_snapshot_telegram_runtime.py tests/server/test_telegram_central_dispatch.py tests/server/test_postgres_runtime_guards.py
```

---

## Task 2: P0 legacy migration의 secret ref 보장

**문제:**

`src/rider_server/migration/runner.py`는 legacy `UiSettings`의 `coupang_login_id`와 `coupang_login_password`를 ref보다 먼저 사용해 `PlatformAccount.username/password`에 넣는다. `tests/server/test_migration.py::test_backup_preserves_plaintext_while_mapping_exposes_refs_only`도 평문 저장을 기대한다.

**Files:**

- Modify: `src/rider_server/migration/runner.py`
- Modify: `tests/server/test_migration.py`
- Possibly modify: `src/rider_crawl/ui_settings.py` only if ref creation helper must be reused

- [ ] **Step 1: migration red test 추가**

Required assertions:

- legacy settings에 평문 `coupang_login_id`/`coupang_login_password`가 있어도 `TargetMapping.platform_account.username/password`는 ref다.
- backup file은 rollback 충실도를 위해 원본을 보존할 수 있지만, 새 domain mapping에는 평문이 없다.
- app password와 verification email도 같은 기준을 따른다.

- [ ] **Step 2: migration mapping을 ref 우선으로 고정**

Required behavior:

- ref가 있으면 ref를 사용한다.
- 평문만 있으면 migration 중 secret store에 저장하고 생성된 ref를 사용한다.
- secret store를 사용할 수 없으면 fail-closed 또는 명시적인 migration error로 중단한다. 조용히 평문 DB 저장하지 않는다.

검증:

```powershell
uv run pytest -q tests/server/test_migration.py tests/server/test_db_schema.py
```

---

## Task 3: P1 dashboard pagination을 DB/read-model 단계로 내리기

**문제:**

`src/rider_server/admin/routes.py`의 `/admin/targets`는 `_target_rows_for_display()`로 전체 target read-model을 만든 뒤 Python slice를 한다. `src/rider_server/admin/dashboard_repository_postgres.py`도 base query 전체 결과를 읽은 뒤 bulk aggregation을 수행한다. N+1은 줄었지만 "수백 target에서도 fragment limit" 수락 기준은 충족하지 못한다.

**Files:**

- Modify: `src/rider_server/admin/dashboard_repository_postgres.py`
- Modify: `src/rider_server/admin/dashboard_service.py`
- Modify: `src/rider_server/admin/routes.py`
- Modify: `src/rider_server/admin/templates/_targets.html`
- Test: `tests/server/test_admin_dashboard.py`
- Test: `tests/negative/test_dashboard_repository_pg.py`

- [ ] **Step 1: repository contract에 limit/cursor 추가**

Required behavior:

- target health 조회가 `limit`과 `offset` 또는 cursor를 받는다.
- `has_more` 판단을 위해 `limit + 1` 조회 또는 count 전략을 명시한다.
- initial dashboard도 기본 limit만 렌더한다.

- [ ] **Step 2: Postgres query에 limit/order 적용**

Required query behavior:

- stable order by severity/name/id 또는 문서화된 기본 정렬
- tenant filter 먼저 적용
- `LIMIT`/cursor가 base target query에 직접 적용
- aggregation helper는 제한된 target id에만 실행

- [ ] **Step 3: query-count/large target 테스트 추가**

Required assertions:

- 300개 target fixture에서도 `/admin/targets?limit=100`이 repository 전체 target을 만들지 않는다.
- 가능하면 PostgreSQL-gated query-count 테스트로 base query limit을 확인한다.

검증:

```powershell
uv run pytest -q tests/server/test_admin_dashboard.py
```

PostgreSQL 가능 시:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
uv run pytest -q tests/negative/test_dashboard_repository_pg.py
```

---

## Task 4: P1 Agent crawl hard timeout/process boundary

**문제:**

`src/rider_agent/workers/crawl_worker.py`의 timeout은 daemon thread와 `Event.wait()`로 구현되어 있다. timeout 결과는 빨리 반환하지만 stuck crawl thread, Playwright/CDP, browser profile 자원은 계속 살아 있을 수 있다.

**Files:**

- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify: `src/rider_agent/job_loop.py`
- Possibly create: `src/rider_agent/workers/crawl_process.py`
- Test: `tests/agent/test_crawl_worker.py`
- Test: `tests/agent/test_job_loop.py`
- Test: `tests/agent/test_browser_profile.py`

- [ ] **Step 1: 현재 daemon thread 방식의 한계를 드러내는 테스트 추가**

Required assertions:

- timeout 후 stuck work가 계속 running 상태로 남지 않는다.
- timeout 후 profile/port/browser lifecycle이 release된다.

- [ ] **Step 2: process boundary 또는 실제 kill 가능한 boundary 도입**

Preferred behavior:

- crawl job을 child process에서 실행한다.
- parent가 timeout 시 child process를 terminate/kill한다.
- child input/output contract에는 plaintext secret이 없다.
- result는 작은 JSON-safe contract로만 전달한다.

If process boundary is deferred:

- 코드 주석과 runbook에 "hard timeout 미완"을 명확히 적고, `max_jobs > 1` 안내를 더 보수적으로 낮춘다.

검증:

```powershell
uv run pytest -q tests/agent/test_crawl_worker.py tests/agent/test_job_loop.py tests/agent/test_browser_profile.py
```

---

## Task 5: P2 retry/status observability 보강

**문제:**

`src/rider_server/queue/postgres_queue.py`의 retry 재진입 경로는 failed attempt에도 `completed_at`/`duration_ms`를 기록하지 않는다. dashboard latest failure는 `coalesce(claimed_at, run_after)`를 사용해 retry backoff의 미래 시각이 실패 발생 시각처럼 보일 수 있다.

**Files:**

- Modify: `src/rider_server/queue/postgres_queue.py`
- Modify: `src/rider_server/queue/memory_queue.py`
- Modify: `src/rider_server/admin/dashboard_repository_postgres.py`
- Test: `tests/server/test_queue_backend.py`
- Test: `tests/server/test_admin_dashboard.py`

- [ ] **Step 1: retry attempt metadata 테스트 추가**

Required assertions:

- retry로 `PENDING` 재진입해도 failed attempt의 발생 시각을 조회할 수 있다.
- dashboard latest failure timestamp가 `run_after`가 아니라 failure completion/attempt timestamp를 기준으로 한다.

- [ ] **Step 2: 모델 결정**

Choose one:

- job row에 `last_failed_at` 추가
- retry 전 `completed_at`을 attempt timestamp로 기록하되 최종 완료 의미와 충돌하지 않게 rename/문서화
- append-only job event/history로 retry attempt를 기록

검증:

```powershell
uv run pytest -q tests/server/test_queue_backend.py tests/server/test_admin_dashboard.py
```

---

## Task 6: P1 `/admin` DB 실패 안내와 설정 UX 최소 수락 기준

**문제:**

`done_ui-ux-settings-work-order.md`의 핵심 요구가 많이 남아 있다.

- `/admin` DB 연결 실패 HTML 안내 없음
- 관리 탭은 여전히 `엔티티 관리`와 `① 새 업체 추가`로 시작
- 고객 생성/계정/채널은 접힌 보조 영역에 있음
- 실발송 ON을 테스트 완료 전에도 바로 선택 가능
- `loadDeliveryRules()`가 응답 전 `조회 완료` 표시
- 내부 용어와 작은 글자, mono 본문, ARIA tab/panel, row keyboard, password style 미완

**Files:**

- Modify: `src/rider_server/admin/routes.py`
- Modify: `src/rider_server/admin/templates/dashboard.html`
- Modify: `src/rider_server/admin/templates/_entity_admin.html`
- Modify: `src/rider_server/admin/templates/_targets.html`
- Possibly create partials under `src/rider_server/admin/templates/`
- Test: `tests/server/test_admin_dashboard.py`

- [ ] **Step 1: `/admin` DB 실패 안내 테스트와 구현**

Required assertions:

- dashboard repository가 DB 연결 예외를 내도 `/admin`은 사용자용 HTML을 반환한다.
- HTML에는 `DB 연결 실패`, `DATABASE_URL`, `DB 실행 상태`, `재시도` 안내가 있다.
- secret이 포함된 DB URL은 출력하지 않는다.

- [ ] **Step 2: 새 고객 세팅 시작 흐름 추가**

Minimum acceptable first pass:

- 관리 탭 상단에 `새 고객 세팅 시작` 진입점이 있다.
- 고객 -> 플랫폼 -> 계정 -> 업체/센터 -> 채널 -> 테스트 -> 실발송 순서 체크리스트가 보인다.
- 기존 CRUD 폼 위치로 이동하는 링크라도 제공한다.
- 고객 생성이 접힌 보조 영역 안에만 갇혀 있지 않다.

- [ ] **Step 3: 테스트 전 실발송 gate**

Required behavior:

- 수집 테스트와 전송 테스트 상태가 실발송 ON 근처에 표시된다.
- 테스트 미완료 상태에서 실발송 ON은 disabled 또는 강한 확인을 거친다.
- `sending_enabled` 같은 내부 이름 대신 `실제 메시지 보내기` 문구를 쓴다.

- [ ] **Step 4: 기본/고급 분리와 용어 정리**

Move to advanced/default-hidden area:

- `external_id`
- raw ID fields
- `webhook secret`
- `registration_code`
- 내부 enum raw value

Replace visible labels:

- `Tenant` -> `고객`
- `external_id` -> `외부 관리 코드`
- `webhook secret` -> `텔레그램 webhook 보안키`
- `sending_enabled` -> `실제 메시지 보내기`
- `SETUP_PENDING` -> `세팅 중`
- `PAYMENT_ACTIVE` -> `결제 활성`
- `soft delete` -> `비활성화`
- `fail-closed` -> `기본 차단`

- [ ] **Step 5: HTMX 상태와 접근성**

Required behavior:

- `loadDeliveryRules()`는 요청 중에는 `조회 중`을 표시한다.
- 성공 응답 후에만 `조회 완료`를 표시한다.
- 실패 시 같은 status 영역에 실패와 재시도 안내를 표시한다.
- tab button에 `aria-controls`가 있고 panel에 `role="tabpanel"`이 있다.
- target row는 `tabindex="0"` + Enter/Space 또는 명확한 detail button 중심으로 keyboard 접근 가능하다.
- password input도 text input과 같은 style/focus/min-width를 가진다.

- [ ] **Step 6: typography 최소 기준**

Required behavior:

- 본문 sans는 system sans/Pretendard 계열을 우선한다. Geist Mono는 숫자/ID/code에만 쓴다.
- 일반 label/button/body 텍스트는 14px 이상으로 맞춘다.
- 모바일 body를 13px로 낮추지 않는다.
- 390px 폭에서 버튼과 inline status가 옆으로 밀리지 않는다.

검증:

```powershell
uv run pytest -q tests/server/test_admin_dashboard.py
npx impeccable --json src/rider_server/admin/templates src/rider_crawl/ui.py
```

---

## Task 7: 전체 검증과 문서 정합성

**Files:**

- Modify: `docs/runbooks/crawl-scale-runbook.md`
- Modify: `docs/operations/aws-product-setup-2026-06-18.md` if deploy/run commands change
- Modify: `docs/goal/done_*` only if they need a clear correction note

- [ ] **Step 1: 후속 작업 완료 후 core 검증**

```powershell
uv run pytest -q tests/server/test_admin_dashboard.py tests/server/test_scheduler_tick.py tests/server/test_queue_backend.py tests/server/test_jobs_api.py tests/server/test_agents_api.py tests/agent/test_crawl_worker.py tests/agent/test_browser_profile.py tests/agent/test_job_loop.py tests/server/test_db_schema.py tests/server/test_migration.py
```

- [ ] **Step 2: architecture/docs 검증**

```powershell
.\scripts\test.ps1 architecture
.\scripts\test.ps1 docs
```

- [ ] **Step 3: PostgreSQL-gated 검증**

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB"
uv run pytest -q tests/negative/test_dashboard_repository_pg.py tests/negative/test_scheduler_idempotency.py tests/negative/test_queue_concurrency.py tests/negative/test_admin_entity_crud_pg.py
```

- [ ] **Step 4: manual smoke**

```powershell
docker compose -f deploy/docker-compose.yml config
curl http://SERVER/health
curl http://SERVER/admin
```

Manual checks:

- `/admin` DB failure 안내 화면
- 새 고객 세팅 시작 흐름
- 테스트 전 실발송 gate
- Telegram dispatch worker가 pending delivery를 claim하고 처리하는 흐름
- Agent timeout이 stuck crawl/process를 남기지 않는지
