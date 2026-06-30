# Runbook: CRAWL_TIMEOUT 조사 (reactivation no-catchup 이후)

> 장애 분류(`FailureCategory`): `CRAWL_TIMEOUT`.
> 근거 작업지시서: `docs/goal/done_reactivation-no-catchup-timeout-work-order-2026-06-29.md`.

## CRAWL_TIMEOUT 은 무엇인가 (로그인 실패가 아니다)

`CRAWL_TIMEOUT` 은 **수집 작업이 제한 시간(`timeout_seconds`) 안에 완료되지 않았다**는 뜻이다.
"로그인 실패"나 "활성화 실패"가 아니다. timeout 은 멈춘 브라우저 작업을 끊는 **안전장치**다 —
없으면 한 고객 작업이 Agent 전체를 붙잡는다. 그래서 timeout 은 제거하거나 무작정 늘리지 않는다.

판단 기준:

- **Chrome 창이 떴다고 수집 성공이 아니다.** 성공은 snapshot 저장 또는 job complete success 로만
  판단한다.
- timeout 이 한 번 떠도, 이후 성공 수집이 들어오면 대시보드는 묵은 실패를 숨기고 정상으로 돌아온다.
- 반복 발생할 때만 아래 조사 순서를 탄다. 단발 timeout 은 Chrome/페이지 로딩 지연일 수 있다.

## reactivation 직후 timeout 이 잠깐 떴다면

`reactivation no-catchup` 수정(2026-06-29) 전에는 고객/대상을 inactive→active 로 되돌리면
밀린 `next_run_at` 이 즉시 due 가 돼 **따라잡기 수집**이 바로 돌았고, 이 즉시 수집이 Chrome/쿠팡
로딩 지연과 만나 `CRAWL_TIMEOUT` 을 잠깐 노출했다.

수정 후에는 재활성화 시 `monitoring_targets.next_run_at` 이 **다음 주기(`now + interval`)**
로 밀려, 활성화 직후 즉시 수집이 생기지 않는다. 즉시 확인이 필요하면 `지금 수집`(test-crawl)
버튼을 쓴다(이 수동 액션은 정책과 무관하게 즉시 enqueue 된다).

no-catchup reset 이 적용되는 재활성화 경로(모두 scheduler 와 같은 게이트 기준):

- 고객 lifecycle 비활성 계열 → `ACTIVE`/`PAYMENT_ACTIVE`(고객 편집).
- 구독 차단(`SUSPENDED`/`CANCELLED`) → 허용(`PAYMENT_ACTIVE`/`PAYMENT_FAILED_GRACE`)(구독 복구/편집).
- 대상 `PAUSED`→`ACTIVE`(운영 토글), `INACTIVE`→`ACTIVE`(설정 CRUD 복구).

이미 schedulable 인 상태에서의 no-op 저장(예: `ACTIVE` 고객 설정만 저장, 이미 허용된 구독 편집)은
`next_run_at` 을 밀지 않는다. `interval_minutes=0` 대상도 재활성화 직후 즉시 due 가 되지 않게 최소
하한(60초)으로 미래로 민다.

재활성화 후 확인:

```sql
SELECT id, tenant_id, name, status, interval_minutes, next_run_at, last_enqueued_at
FROM monitoring_targets
WHERE tenant_id = '<tenant_id>'
ORDER BY name;
```

기대: ACTIVE 대상의 `next_run_at` 이 미래(`now + interval`)다. 즉시 새 `CRAWL_*` job 이
생기지 않는다.

## 일반 scheduled crawl 에서 timeout 이 반복될 때 (단계별 증거)

selector 추측 수정이나 timeout 값 변경으로 시작하지 않는다. 단계별 증거로 원인을 먼저 가린다.

### 1. job timeline 확인

```sql
SELECT
  j.id,
  j.type,
  j.status,
  j.error_code,
  j.attempts,
  j.claimed_at,
  j.lease_expires_at,
  j.completed_at,
  j.duration_ms,
  j.result_json
FROM jobs j
WHERE j.target_id = '<target_id>'
ORDER BY COALESCE(j.completed_at, j.claimed_at, j.run_after) DESC
LIMIT 20;
```

### 2. Agent process timeout vs server stale lease recovery 구분

- `completed_at` 이 있고 `error_code='CRAWL_TIMEOUT'`: **Agent child process timeout** 가능성이
  높다(아래 3. Agent 로그로 단계별 시간 확인).
- `lease_expires_at <= now` 뒤 recovery 가 실패 처리: **lease/heartbeat/stale recovery** 경로
  가능성이 높다. Agent heartbeat 가 끊겼으면 Chrome 문제가 아니라 **Agent offline/lease** 문제부터
  본다(`docs/runbooks/agent_offline.md`).

### 3. Agent PC 로그에서 단계별 시간 확인

- profile prepare
- Chrome launch / CDP connect
- login / auth probe
- page goto / load
- table / selector wait
- parser
- complete report

어느 단계가 timeout 을 먹는지 본 뒤에야 조치를 정한다.

### 4. 금지

- 단계별 증거 없이 `timeout_seconds` 를 늘리거나 selector 를 바꾸지 않는다.
- timeout 경계 자체를 제거하지 않는다.

## stale queued crawl 은 이미 안전하게 닫힌다 (replay 없음)

inactive 기간 동안 큐에 남아 있던 scheduled crawl 은 active 복귀 시 **다시 실행되지 않는다.**
두 경계가 fail-closed 로 막는다:

- **Agent preflight**: 브라우저를 열기 전 `POST /v1/jobs/{id}/preflight` 가 payload `expires_at < now`
  면 `payload_expired` 로 거부한다(`preflight_decision` → `stale_recovery_reason`).
- **Server queue recovery**: stale lease 회수 시 만료된 scheduled crawl 을 `STALE_CRAWL_SKIPPED`
  로 terminal 종료한다.

scheduled crawl payload 의 `expires_at` 는 `scheduled_at + max(interval, timeout)` 라, 10시간
비활성 뒤엔 이미 만료돼 있다. 증거 테스트:

- `tests/server/test_queue_recovery.py::test_recovery_skips_expired_scheduled_crawl_instead_of_backlog_replay`
- `tests/server/test_queue_recovery.py::test_queue_recovery_closes_expired_pending_scheduled_crawls`
- `tests/server/test_jobs_api.py::test_job_preflight_denies_expired_open_auth_browser`

## 에스컬레이션

- 다수 대상이 동시에 `CRAWL_TIMEOUT` → 플랫폼 페이지 변경/대규모 로딩 지연 의심, 개발/운영 합동 점검.
- 단일 대상만 반복 → 해당 대상 URL/센터/프로필 단위 조사(`docs/runbooks/profile_mismatch.md`).
