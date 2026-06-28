# Center Mismatch Recovery and Dispatch Kill Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for implementation slices, or `superpowers:executing-plans` if one worker executes the whole plan. Keep checkbox state in this document as work lands.

작성일: 2026-06-24  
상태: 작업 전  
대상 저장소: `rider_result_mornitoring`  
근거 문서: `docs/diagnosis/center-mismatch-stuck-and-dispatch-killswitch-2026-06-24.md`  
근거 요약: 라이브 EC2 DB와 배포 컨테이너 코드 확인 결과

**Goal:** 전송 OFF 상태에서 snapshot ingest가 KAKAO_SEND/Telegram delivery work를 만들지 못하게 막고, Coupang crawl timeout 루프와 `CENTER_MISMATCH` 고착을 운영 증거 기반으로 해소한다.

**Architecture:** snapshot ingest는 snapshot/message 저장까지는 계속 수행하되, dispatch fan-out을 만들기 전에 기존 `effective_send_enabled(send_enabled=True, sending_enabled=settings.sending_enabled)` 게이트를 통과해야 한다. `CENTER_MISMATCH` 카드는 성공 crawl이 오면 배포된 서버가 자동 해제하므로, 코드 수정 대상이 아니라 crawl timeout 원인 조사와 선택적 1회 DB 보정으로 다룬다.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, PostgreSQL, pytest, Windows Agent runtime, Playwright/CDP, EC2 Docker Compose.

---

## 현재 확정 사실

- target `6b8fd18e`가 쓰는 Coupang account `3e703327`의 `auth_state`는 `CENTER_MISMATCH`로 남아 있다.
- Agent는 이미 신버전이다. 2026-06-23 10:36~11:00 성공 crawl 4건이 `result_json.auth_state=ACTIVE`를 보냈다.
- 위 4건은 신버전 서버 배포 시각인 2026-06-23 11:58 이전이라, 당시 구버전 서버가 `auth_state`를 읽지 않아 계정을 `ACTIVE`로 덮지 못했다.
- 2026-06-23 11:58 이후에는 `CRAWL_TIMEOUT` 등으로 성공 crawl이 0건이다. 그래서 자동 해제 트리거가 오지 않았다.
- 현재 배포된 서버의 `_platform_account_auth_update`에는 `result_json.auth_state`를 읽는 코드가 존재한다. 성공 crawl 1건이 오면 카드가 자동으로 풀린다.
- 크롬 반복 인증 주기는 job retry backoff 30초 -> 60초와 맞는다. 여기에 10분 스케줄과 수동 재검증이 겹쳐 루프처럼 보인다.
- `CENTER_MISMATCH`는 "로그인은 됨, 센터만 불일치" 상태로 취급되어 scheduled crawl을 계속 허용한다. 이 자체는 설계 의도다.
- `snapshot_repository_postgres.py::_enqueue_dispatch_records`는 전역 `sending_enabled` kill switch를 보지 않고 `delivery_rules.enabled`, 채널 `ACTIVE`, 시간 윈도만 본다. 이 부분은 명백한 서버 버그다.

## 작업 원칙

- 코드 수정 1순위는 dispatch enqueue kill switch다. 이 수정은 오발송 방지와 직접 연결된다.
- `CENTER_MISMATCH` 자동 해제 로직은 이미 배포되어 있으므로 새 해제 로직을 만들지 않는다.
- crawl timeout은 Agent 로그 없이는 원인을 단정하지 않는다. 로그 수집 -> 재현 -> 원인 분류 순서로 간다.
- 전송 OFF로 차단할 때는 `DeliveryLogRow`도 만들지 않는다. 차단 시 dedup key를 소비하면 나중에 전송 ON 후 정상 발송이 막힐 수 있다.
- 기존 dirty worktree는 되돌리지 않는다.
- 실 DB 수동 UPDATE는 검증 쿼리와 운영자 승인 뒤 1회만 수행한다.

## 파일 구조

- Modify: `src/rider_server/services/snapshot_repository_postgres.py`
  - `PostgresSnapshotIngestRepository`가 `sending_enabled`를 생성자에서 받고 `_enqueue_dispatch_records` 전에 기존 kill switch 함수를 호출한다.
- Modify: `src/rider_server/main.py`
  - `_default_job_result_ingest_service()`가 `settings.sending_enabled`를 repository로 넘긴다.
- Modify: `tests/server/test_postgres_runtime_guards.py`
  - DB 없이 source-level guard로 kill switch가 `pg_insert(DeliveryLogRow)` 전에 호출되는지 잠근다.
- Modify: `tests/server/test_snapshot_telegram_runtime.py`
  - 기본 wiring이 `Settings.sending_enabled` 값을 repository에 넘기는지 잠근다.
- Modify: `tests/negative/test_atomic_snapshot_idempotency_pg.py` or create `tests/negative/test_snapshot_dispatch_killswitch_pg.py`
  - PostgreSQL에서 `sending_enabled=False`일 때 snapshot/message는 저장되지만 delivery log와 KAKAO_SEND job은 생기지 않는지 검증한다.
- Modify: `docs/runbooks/backup-restore.md`
  - 전역 non-sending 모드가 snapshot fan-out enqueue도 막는다고 문서화한다.
- Optional Modify: `docs/runbooks/auth_required.md`
  - `CENTER_MISMATCH` 고착은 성공 crawl 또는 승인된 1회 DB 보정으로 해제한다는 운영 절차를 추가한다.

---

## Task 0: 기준선과 문서 근거 확인

**Intent:** 구현자가 이번 작업의 전제와 현재 dirty worktree를 명확히 기록한다.

**Files:** 없음

- [ ] **Step 1: 작업 전 변경 상태 확인**

Run:

```powershell
git status --short
```

Expected:

- 이 문서 작성 시점에는 이미 unrelated 변경이 있을 수 있다.
- 구현자는 본인 작업과 무관한 파일을 되돌리지 않는다.
- `docs/diagnosis/`가 untracked이면 이 작업의 근거 문서로 취급하고 삭제하지 않는다.

- [ ] **Step 2: 근거 문서 확인**

Run:

```powershell
Get-Content -Raw docs\diagnosis\center-mismatch-stuck-and-dispatch-killswitch-2026-06-24.md
```

Expected:

- "agent 미배포"가 이번 건의 원인이 아니라는 갱신 내용이 확인된다.
- 전송 OFF enqueue 버그의 대상 함수가 `snapshot_repository_postgres.py::_enqueue_dispatch_records`임을 확인한다.

---

## Task 1: snapshot dispatch enqueue에 전역 kill switch 테스트 추가

**Intent:** `sending_enabled=False`일 때 delivery log와 dispatch job을 만들 수 없게, 구현 전에 실패하는 guard를 만든다.

**Files:**

- Modify: `tests/server/test_postgres_runtime_guards.py`

- [ ] **Step 1: source-level 실패 테스트 추가**

Add test:

```python
def test_snapshot_enqueue_checks_global_sending_enabled_before_delivery_log_insert() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")
    enqueue_body = source[
        source.index("async def _enqueue_dispatch_records") : source.index(
            "def _record_scoped_to_locked_job"
        )
    ]

    assert "effective_send_enabled" in source
    assert "self._sending_enabled" in source
    assert "effective_send_enabled(" in enqueue_body
    assert enqueue_body.index("effective_send_enabled(") < enqueue_body.index(
        "pg_insert(DeliveryLogRow)"
    )
```

- [ ] **Step 2: 실패 확인**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_postgres_runtime_guards.py::test_snapshot_enqueue_checks_global_sending_enabled_before_delivery_log_insert -q
```

Expected before implementation:

```text
FAILED
```

---

## Task 2: `PostgresSnapshotIngestRepository`에 `sending_enabled` 주입

**Intent:** snapshot 저장과 dispatch enqueue를 분리하고, dispatch enqueue만 fail-closed로 막는다.

**Files:**

- Modify: `src/rider_server/services/snapshot_repository_postgres.py`
- Test: `tests/server/test_postgres_runtime_guards.py`

- [ ] **Step 1: 기존 kill switch 함수 import**

In `src/rider_server/services/snapshot_repository_postgres.py`, add:

```python
from rider_server.services.recovery import effective_send_enabled
```

- [ ] **Step 2: repository 생성자에 fail-closed 기본값 추가**

Change constructor shape:

```python
class PostgresSnapshotIngestRepository(JobResultIngestService):
    """Persist prepared Agent snapshot ingest records to ``snapshots``."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        telegram_sender: TelegramSender | None = None,
        sending_enabled: bool = False,
    ) -> None:
        super().__init__(save_snapshot=self.save_snapshot)
        self._session_factory = session_factory
        self._telegram_sender = telegram_sender
        self._sending_enabled = bool(sending_enabled)
```

Rules:

- Default is `False`, not `True`. Direct construction must fail closed unless a caller explicitly opts in.
- Keep `telegram_sender` parameter for existing test compatibility, but do not reintroduce inline Telegram send.

- [ ] **Step 3: dispatch fan-out 전에 gate 추가**

In `_enqueue_dispatch_records()`, after the send-window check and before selecting delivery rules, add:

```python
        if not effective_send_enabled(
            send_enabled=True,
            sending_enabled=self._sending_enabled,
        ):
            return
```

Required ordering:

- The gate must run before `pg_insert(DeliveryLogRow)`.
- The gate must run before `insert(JobRow)` for `JOB_TYPE_KAKAO_SEND`.
- The function must still return quietly, because snapshot ingest should not fail just because sending is OFF.

- [ ] **Step 4: source guard 통과 확인**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_postgres_runtime_guards.py::test_snapshot_enqueue_checks_global_sending_enabled_before_delivery_log_insert -q
```

Expected after implementation:

```text
1 passed
```

---

## Task 3: app wiring이 `Settings.sending_enabled`를 repository에 넘기게 변경

**Intent:** 운영 환경의 `RIDER_SENDING_ENABLED` 기본 OFF가 snapshot enqueue 경로까지 실제로 닿게 한다.

**Files:**

- Modify: `src/rider_server/main.py`
- Modify: `tests/server/test_snapshot_telegram_runtime.py`

- [ ] **Step 1: wiring 실패 테스트 추가**

In `tests/server/test_snapshot_telegram_runtime.py`, update the fake repository tests to capture `sending_enabled`.

Add:

```python
def test_default_snapshot_ingest_wires_global_sending_enabled(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_repo(session_factory, *, telegram_sender=None, sending_enabled=False):
        captured["session_factory"] = session_factory
        captured["telegram_sender"] = telegram_sender
        captured["sending_enabled"] = sending_enabled
        return object()

    monkeypatch.setattr("rider_server.main.create_engine", lambda _url, **_kwargs: object())
    monkeypatch.setattr("rider_server.main.create_session_factory", lambda _engine: "sessions")
    monkeypatch.setattr("rider_server.main.PostgresSnapshotIngestRepository", fake_repo)

    _default_job_result_ingest_service(_settings(sending_enabled=False))

    assert captured["session_factory"] == "sessions"
    assert captured["telegram_sender"] is None
    assert captured["sending_enabled"] is False
```

Add:

```python
def test_default_snapshot_ingest_wires_global_sending_enabled_on(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_repo(session_factory, *, telegram_sender=None, sending_enabled=False):
        captured["session_factory"] = session_factory
        captured["telegram_sender"] = telegram_sender
        captured["sending_enabled"] = sending_enabled
        return object()

    monkeypatch.setattr("rider_server.main.create_engine", lambda _url, **_kwargs: object())
    monkeypatch.setattr("rider_server.main.create_session_factory", lambda _engine: "sessions")
    monkeypatch.setattr("rider_server.main.PostgresSnapshotIngestRepository", fake_repo)

    _default_job_result_ingest_service(_settings(sending_enabled=True))

    assert captured["session_factory"] == "sessions"
    assert captured["telegram_sender"] is None
    assert captured["sending_enabled"] is True
```

- [ ] **Step 2: 실패 확인**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_snapshot_telegram_runtime.py::test_default_snapshot_ingest_wires_global_sending_enabled tests/server/test_snapshot_telegram_runtime.py::test_default_snapshot_ingest_wires_global_sending_enabled_on -q
```

Expected before implementation:

```text
FAILED
```

- [ ] **Step 3: `_default_job_result_ingest_service()` 배선 변경**

Change:

```python
return PostgresSnapshotIngestRepository(factory)
```

To:

```python
return PostgresSnapshotIngestRepository(
    factory,
    sending_enabled=settings.sending_enabled,
)
```

- [ ] **Step 4: wiring 테스트 통과 확인**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_snapshot_telegram_runtime.py::test_default_snapshot_ingest_wires_global_sending_enabled tests/server/test_snapshot_telegram_runtime.py::test_default_snapshot_ingest_wires_global_sending_enabled_on -q
```

Expected after implementation:

```text
2 passed
```

---

## Task 4: PostgreSQL 통합 회귀 테스트로 enqueue 차단 확인

**Intent:** 실제 DB 경로에서 `sending_enabled=False`가 delivery log와 KAKAO_SEND job 생성을 막는지 확인한다.

**Files:**

- Modify: `tests/negative/test_atomic_snapshot_idempotency_pg.py` or create `tests/negative/test_snapshot_dispatch_killswitch_pg.py`

- [ ] **Step 1: PG-gated 실패 테스트 추가**

Add test name:

```python
async def test_snapshot_ingest_sending_disabled_does_not_create_delivery_log_or_kakao_job(pg_session_factory) -> None:
    """Global sending OFF stores snapshot/message but does not reserve dispatch work."""
```

Required setup:

- Tenant row exists.
- Monitoring target row exists and points to active Coupang account.
- Delivery rule row exists with `enabled=True`.
- Messenger channel row exists with `messenger="KAKAO"` and `state="ACTIVE"`.
- Claimable crawl job row exists and is completed through `PostgresSnapshotIngestRepository(..., sending_enabled=False).complete_snapshot_job(...)`.

Required assertions:

```python
assert snapshot_count == 1
assert message_count == 1
assert delivery_log_count == 0
assert kakao_send_job_count == 0
```

- [ ] **Step 2: ON 상태 양성 테스트 추가**

Add test name:

```python
async def test_snapshot_ingest_sending_enabled_creates_kakao_job(pg_session_factory) -> None:
    """Global sending ON keeps the existing Kakao fan-out behavior."""
```

Required assertions:

```python
assert snapshot_count == 1
assert message_count == 1
assert delivery_log_count == 1
assert kakao_send_job_count == 1
```

- [ ] **Step 3: PG 테스트 실행**

Run:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://rider:rider@localhost:5432/rider_test"
.venv\Scripts\python.exe -m pytest tests/negative/test_snapshot_dispatch_killswitch_pg.py -q
```

Expected after implementation:

```text
2 passed
```

If the existing PG fixture lives only in `test_atomic_snapshot_idempotency_pg.py`, place both tests there and run that file instead.

---

## Task 5: 운영 문서에 snapshot fan-out kill switch 반영

**Intent:** 운영자가 "전송 OFF"의 의미를 실전송 차단뿐 아니라 enqueue 차단까지 이해하게 한다.

**Files:**

- Modify: `docs/runbooks/backup-restore.md`
- Modify: `tests/server/test_runbooks_present.py`

- [ ] **Step 1: runbook guard 테스트 추가**

Add test:

```python
def test_backup_restore_runbook_mentions_snapshot_dispatch_enqueue_kill_switch() -> None:
    text = Path("docs/runbooks/backup-restore.md").read_text(encoding="utf-8")

    assert "snapshot" in text.lower()
    assert "enqueue" in text.lower()
    assert "RIDER_SENDING_ENABLED" in text
    assert "delivery log" in text.lower() or "delivery_log" in text.lower()
```

- [ ] **Step 2: 문서 갱신**

Add a short section to `docs/runbooks/backup-restore.md`:

```markdown
### Snapshot fan-out enqueue

`RIDER_SENDING_ENABLED`가 꺼져 있으면 snapshot과 message 저장은 계속 수행하지만 dispatch fan-out은 만들지 않는다.
즉 `delivery_logs` 예약행과 `KAKAO_SEND` job을 생성하지 않는다. 차단 시 dedup key를 소비하지 않아야 하므로,
운영자가 전송을 다시 켠 뒤 같은 snapshot 흐름이 정상적으로 fan-out될 수 있다.
```

- [ ] **Step 3: 문서 테스트 실행**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_backup_restore_runbook_mentions_snapshot_dispatch_enqueue_kill_switch -q
```

Expected after implementation:

```text
1 passed
```

---

## Task 6: Coupang crawl timeout 원인 로그 수집

**Intent:** 크롬 30초/60초 반복 인증의 실제 원인을 Agent 쪽 증거로 확정한다.

**Files:** 없음

- [ ] **Step 1: EC2에서 실패 job 목록 확보**

Run on EC2:

```bash
sudo docker exec -i rider-db-1 psql -U rider -d rider -c "
WITH affected_targets AS (
  SELECT mt.id
    FROM monitoring_targets mt
   WHERE mt.platform_account_id='3e703327-84ea-42ce-bf4a-2282848f6bfa'
)
SELECT id, type, status, error_code, attempts, claimed_at, completed_at, result_json
  FROM jobs
 WHERE target_id IN (SELECT id FROM affected_targets)
    OR payload_json->>'platform_account_id'='3e703327-84ea-42ce-bf4a-2282848f6bfa'
 ORDER BY COALESCE(completed_at, claimed_at, run_after) DESC
 LIMIT 30;"
```

Expected:

- Recent failures show `CRAWL_TIMEOUT`, `CDP_UNREACHABLE`, or `PROFILE_UNAVAILABLE`.
- At least one job id is selected for matching against Agent logs.

- [ ] **Step 2: Agent PC 로그 확보**

Collect from the Agent PC:

```powershell
Get-ChildItem logs -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 20 FullName,LastWriteTime,Length
```

Then search:

```powershell
rg -n "CRAWL_TIMEOUT|CDP_UNREACHABLE|PROFILE_UNAVAILABLE|job_id|6b8fd18e|3e703327|coupang|timeout|auth" logs runtime
```

Expected:

- The selected job id appears in Agent logs.
- Logs show the last successful browser/CDP step before timeout.

- [ ] **Step 3: 원인 분류**

Classify into exactly one primary bucket:

- `profile_unavailable`: Chrome profile lock, missing profile path, or CDP endpoint unavailable before page navigation.
- `login_session_expired`: login page is reached and crawl correctly returns auth-required or waits incorrectly.
- `page_load_timeout`: Coupang dashboard/navigation never reaches expected ready state.
- `selector_wait_timeout`: page loaded but expected selector/text does not appear.
- `cdp_disconnected`: Chrome started but CDP connection drops mid-run.

Record evidence in a new note:

```text
docs/diagnosis/coupang-crawl-timeout-agent-log-2026-06-24.md
```

Required fields:

- job id
- target id
- account id
- Agent version
- Chrome/profile path redacted if it contains a username
- first failure timestamp
- last browser action before failure
- primary bucket
- next code or operations action

---

## Task 7: 원인별 crawl timeout 조치

**Intent:** 로그로 확정된 원인만 수정한다. 추정으로 Playwright timeout을 늘리지 않는다.

**Files:** depends on Task 6 bucket

- [ ] **Step 1: `profile_unavailable`이면 profile/CDP 경로만 수정**

Likely files:

- Modify: `src/rider_agent/browser_profile.py`
- Modify: `src/rider_agent/workers/crawl_process.py`
- Test: `tests/agent/test_browser_profile.py`
- Test: `tests/agent/test_crawl_worker.py`

Required behavior:

- Profile lock or CDP failure is detected before retrying browser-heavy work.
- Result uses a stable error code already understood by retry policy.
- No password, email app password, cookie, or local username is logged.

- [ ] **Step 2: `login_session_expired`이면 auth-required fast path만 수정**

Likely files:

- Modify: `src/rider_agent/workers/crawl_worker.py`
- Modify: `src/rider_crawl/platforms/coupang/crawler.py`
- Test: `tests/agent/test_crawl_worker.py`
- Test: `tests/test_coupang_crawler.py`

Required behavior:

- Login screen is detected quickly.
- `CRAWL_COUPANG` does not perform email 2FA.
- Result is `AUTH_REQUIRED` or the existing auth state mapping, not `CRAWL_TIMEOUT`.

- [ ] **Step 3: `page_load_timeout` 또는 `selector_wait_timeout`이면 readiness wait만 수정**

Likely files:

- Modify: `src/rider_crawl/platforms/coupang/crawler.py`
- Test: `tests/test_coupang_crawler.py`

Required behavior:

- The crawler distinguishes "login page", "dashboard loading", and "dashboard loaded but missing data".
- The previous slow-dashboard wait does not spend two full page timeouts on a login page.
- Timeout result includes a redacted, fixed reason string.

- [ ] **Step 4: `cdp_disconnected`이면 worker recovery만 수정**

Likely files:

- Modify: `src/rider_agent/workers/crawl_process.py`
- Modify: `src/rider_agent/workers/crawl_worker.py`
- Test: `tests/agent/test_crawl_worker.py`

Required behavior:

- CDP disconnect is surfaced as a stable failure.
- Agent does not leave orphan browser windows in a retry loop.
- Retry remains bounded by server attempts and schedule.

---

## Task 8: `CENTER_MISMATCH` 고착 해제 운영 절차

**Intent:** crawl timeout이 먼저 해결되면 자동 해제를 기다리고, 즉시 카드 해제가 필요할 때만 안전하게 1회 보정한다.

**Files:**

- Optional Modify: `docs/runbooks/auth_required.md`

- [ ] **Step 1: 자동 해제 우선 확인**

After Task 6/7 fixes or operational recovery, run one successful crawl.

DB check:

```bash
sudo docker exec -i rider-db-1 psql -U rider -d rider -c "
SELECT pa.id, pa.auth_state, j.id AS latest_job_id, j.status, j.result_json->>'auth_state' AS result_auth_state
  FROM platform_accounts pa
  LEFT JOIN jobs j ON j.payload_json->>'platform_account_id' = pa.id::text
 WHERE pa.id='3e703327-84ea-42ce-bf4a-2282848f6bfa'
 ORDER BY COALESCE(j.completed_at, j.claimed_at, j.run_after) DESC
 LIMIT 5;"
```

Expected:

- A new successful crawl has `result_json.auth_state=ACTIVE`.
- `platform_accounts.auth_state` becomes `ACTIVE`.

- [ ] **Step 2: 수동 UPDATE 전 검증 쿼리**

Only run when the operator accepts immediate manual correction.

```sql
SELECT mt.id AS target_id, mt.name AS target_name, pa.id AS account_id, pa.auth_state
  FROM monitoring_targets mt
  JOIN platform_accounts pa
    ON pa.id = mt.platform_account_id
   AND pa.tenant_id = mt.tenant_id
 WHERE pa.id='3e703327-84ea-42ce-bf4a-2282848f6bfa';
```

Expected:

- Exactly the intended target/account pair is returned.
- Operator confirms the target is the correct Coupang center.

- [ ] **Step 3: 승인된 1회 UPDATE**

```sql
UPDATE platform_accounts
   SET auth_state='ACTIVE'
 WHERE id='3e703327-84ea-42ce-bf4a-2282848f6bfa'
   AND auth_state='CENTER_MISMATCH';
```

Expected:

- `UPDATE 1`.
- If `UPDATE 0`, stop and re-check the account state. Do not broaden the WHERE clause.

---

## Task 9: 전체 검증

**Intent:** 오발송 방지, 기존 snapshot 저장, crawl/auth 운영 흐름을 함께 확인한다.

- [ ] **Step 1: focused tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_postgres_runtime_guards.py tests/server/test_snapshot_telegram_runtime.py tests/server/test_recovery_non_sending.py tests/server/test_kill_switch_5_10.py -q
```

Expected:

```text
passed
```

- [ ] **Step 2: affected server tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_jobs_api.py tests/server/test_queue_backend.py tests/server/test_dispatch_fanout.py tests/server/test_snapshot_telegram_runtime.py -q
```

Expected:

```text
passed
```

- [ ] **Step 3: PG-gated tests**

Run:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://rider:rider@localhost:5432/rider_test"
.venv\Scripts\python.exe -m pytest tests/negative/test_snapshot_dispatch_killswitch_pg.py tests/negative/test_atomic_snapshot_idempotency_pg.py -q
```

Expected:

```text
passed
```

- [ ] **Step 4: 운영 smoke**

On staging or controlled production window:

```bash
sudo docker compose exec backend-api env | grep RIDER_SENDING_ENABLED
sudo docker exec -i rider-db-1 psql -U rider -d rider -c "
SELECT type, status, count(*)
  FROM jobs
 WHERE type='KAKAO_SEND'
 GROUP BY 1,2
 ORDER BY 1,2;"
```

Expected when `RIDER_SENDING_ENABLED` is unset or false:

- New successful crawl stores snapshot/message.
- No new `KAKAO_SEND` job appears.
- No new `delivery_logs` row is reserved for the blocked fan-out.

---

## Out of Scope

- Do not redesign the full dispatch queue.
- Do not change the meaning of `CENTER_MISMATCH`.
- Do not add a new account auth state enum for this fix.
- Do not increase generic Playwright timeouts without Agent log evidence.
- Do not enable `RIDER_SENDING_ENABLED` as part of this work.

## 리스크와 대응

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| `sending_enabled=False`에서 delivery log를 먼저 만들면 dedup key가 오염됨 | 전송 ON 뒤에도 같은 fan-out이 막힐 수 있음 | kill switch를 `pg_insert(DeliveryLogRow)` 전에 둔다 |
| direct repository construction이 기본 ON이면 새 호출자가 gate를 우회할 수 있음 | 운영 차단이 일부 경로에서 무력화됨 | constructor 기본값은 `False`, production wiring만 Settings 값을 넘긴다 |
| snapshot ingest 자체를 실패시키면 crawl 성공 기록이 사라짐 | 운영자가 상태를 더 알기 어려워짐 | snapshot/message 저장은 유지하고 dispatch fan-out만 return |
| crawl timeout 원인을 추정으로 고치면 반복 인증이 계속됨 | 새 timeout 또는 다른 retry loop 발생 | Agent 로그로 bucket 확정 후 해당 경로만 수정 |
| 수동 `auth_state=ACTIVE` 업데이트가 잘못된 계정에 적용됨 | 실제 센터 불일치인데 카드가 풀릴 수 있음 | target/account join 검증 + 정확한 account id + `auth_state='CENTER_MISMATCH'` WHERE 조건 |

## 구현 순서 요약

1. source-level guard로 `_enqueue_dispatch_records` kill switch 위치를 잠근다.
2. `PostgresSnapshotIngestRepository(..., sending_enabled=False)` 기본 fail-closed를 추가한다.
3. `_default_job_result_ingest_service()`가 `settings.sending_enabled`를 넘기게 한다.
4. PG 통합 테스트로 OFF/ON fan-out 차이를 확인한다.
5. runbook에 snapshot fan-out enqueue 차단 의미를 적는다.
6. Agent 로그로 Coupang crawl timeout 원인을 확정하고 해당 bucket만 수정한다.
7. 성공 crawl로 `CENTER_MISMATCH` 자동 해제를 확인한다.
8. 즉시 해제가 필요할 때만 승인된 1회 SQL UPDATE를 실행한다.
