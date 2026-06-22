# Investigation: PC Agent Server Log Communication

## Hand-off Brief

1. **What happened.** PC Agent는 heartbeat/job 결과를 서버에 보내지만, 운영용 원격 로그/이벤트 기록은 충분히 영속되지 않는다.
2. **Where the case stands.** Status: Concluded. 서버는 생존 여부, 현재 job, 완료 결과의 `result_json`/`error_code`는 알지만, job event, complete metrics, 실패 상세 메시지, 로컬 runner 오류는 대부분 DB에 남기지 않는다.
3. **What's needed next.** 운영 전 `agent_events` 또는 `audit_logs` 기반 이벤트 영속화, complete 진단 필드 저장, CLI 로컬 파일 로그, Kakao status allow-list 정합성 보강을 우선 적용한다.

## Case Info

| Field            | Value |
| ---------------- | ----- |
| Ticket           | N/A |
| Date opened      | 2026-06-18 |
| Status           | Concluded |
| System           | Windows local workspace, Python project |
| Evidence sources | Source code, tests, project context, version control if needed |

## Problem Statement

사용자 설명: "에이전트 (pc) <> 서버와의 로그 통신?이 잘 구성되어있는지 검토하세요. 이 상태로 가도 괜찮을지 개선이 필요한지 검토해 보세요. 에이전트 pc 에서 일어나는 일을 서버에서 많이 모르는건 아닐지, 잘 알아야 문제해결 및 개선이 효율적으로 될텐데., 이 부분을 추론 , 리서치하여 검토해보세요."

## Evidence Inventory

| Source | Status | Notes |
| ------ | ------ | ----- |
| Project context | Available | `_bmad-output/project-context.md` loaded. |
| Agent source | Available | `src/rider_agent/heartbeat.py`, `src/rider_agent/job_loop.py`, `src/rider_agent/__main__.py`, worker modules traced. |
| Server source | Available | `src/rider_server/api/agents.py`, `api/jobs.py`, `services/agent_registry*.py`, `queue/postgres_queue.py`, admin/metrics read models traced. |
| Tests | Available | Agent/server contract tests read for guarantees and gaps. |
| Runtime logs/fixtures | Missing | Production runtime logs were not available in workspace. |

## Investigation Backlog

| # | Path to Explore | Priority | Status | Notes |
| - | --------------- | -------- | ------ | ----- |
| 1 | Agent-to-server event/log sender | High | Done | Heartbeat, claim, events, complete paths traced. |
| 2 | Server receive/store APIs and DB models | High | Done | Server stores heartbeat capacity and job result/status, but not PG events. |
| 3 | Tests around telemetry/log/status behavior | High | Done | Tests prove protocol/redaction, not full operational persistence. |
| 4 | Missing evidence and operational gaps | Medium | Done | Production logs and retention policy remain missing. |

## Timeline of Events

| Time | Event | Source | Confidence |
| ---- | ----- | ------ | ---------- |
| 2026-06-18 | Investigation opened from user request. | User request | Confirmed |
| 2026-06-18 | Agent/server source and tests inspected; two subagents independently reviewed agent and server sides. | Source code/subagent reports | Confirmed |

## Confirmed Findings

### Finding 1: Heartbeat gives the server only a current status snapshot

**Evidence:** `src/rider_agent/heartbeat.py:157`, `src/rider_agent/heartbeat.py:160`, `src/rider_agent/heartbeat.py:164`; `src/rider_server/services/agent_registry_postgres.py:115`; `src/rider_server/services/agent_registry_postgres.py:118`

**Detail:** Agent heartbeat sends `agent_id`, version, metrics, capabilities, active jobs, Kakao status, and browser profile status. Server stores this as `agents.last_heartbeat_at` and `agents.capacity_json`. Default metrics are only platform and Python version (`src/rider_agent/heartbeat.py:109`).

### Finding 2: Job events are accepted by API but not stored in PostgreSQL

**Evidence:** `src/rider_agent/job_loop.py:540`, `src/rider_agent/job_loop.py:546`; `src/rider_server/api/jobs.py:356`, `src/rider_server/api/jobs.py:367`; `src/rider_server/queue/postgres_queue.py:381`, `src/rider_server/queue/postgres_queue.py:390`

**Detail:** Agent sends a best-effort `JOB_STARTED` event after claim. Server API calls `backend.emit_event`, but the PostgreSQL backend is explicitly a no-op because the 14-table contract has no events table.

### Finding 3: Complete payload includes diagnostics, but server persists only part of it

**Evidence:** `src/rider_agent/job_loop.py:317`; `src/rider_server/api/jobs.py:93`; `src/rider_server/api/jobs.py:313`; `src/rider_server/api/jobs.py:318`; `src/rider_server/queue/postgres_queue.py:216`; `src/rider_server/queue/postgres_queue.py:218`

**Detail:** Agent sends `error_message_redacted`, `metrics`, `started_at`, and `finished_at`, but server complete calls store only `result_json`, `error_code`, and final status in the queue path. The test at `tests/server/test_jobs_api.py:449` proves the body is accepted, not that these diagnostic fields are stored.

### Finding 4: Actual CLI run does not wire a log callback

**Evidence:** `src/rider_agent/__main__.py:145`, `src/rider_agent/__main__.py:153`; `src/rider_agent/job_loop.py:586`, `src/rider_agent/job_loop.py:592`; `src/rider_agent/heartbeat.py:357`, `src/rider_agent/heartbeat.py:363`

**Detail:** `JobRunner` and `HeartbeatReporter` can record redacted local error events through a `log` callback, but the real CLI call to `run_agent` does not pass `log` or `on_status`. Some errors remain only in in-memory `last_error_event` until process exit.

### Finding 5: Kakao status producer and server allow-list are not fully aligned

**Evidence:** `src/rider_agent/workers/kakao_sender.py:253`; `src/rider_server/services/agent_registry.py:132`

**Detail:** Kakao worker reports `enabled`, `queue_depth`, `queue_lag_seconds`, `sent`, `failed`, and `last_error_code`. Server allow-list keeps keys such as `queue_depth` and `last_error_code`, but not `enabled`, `queue_lag_seconds`, `sent`, or `failed`; it instead allows unused names like `worker_enabled`.

### Finding 6: Admin and metrics surfaces are useful status boards, not forensic logs

**Evidence:** `src/rider_server/admin/templates/_agents.html:1`, `src/rider_server/admin/templates/_agents.html:6`; `src/rider_server/admin/dashboard_repository_postgres.py:168`; `src/rider_server/main.py:576`; `src/rider_server/metrics/service.py:116`

**Detail:** Admin shows Agent name/version/online/current job/capabilities and target last failure code. `/metrics/operational` exposes fleet aggregates. Neither surface reconstructs a PC-local step-by-step timeline.

## Deduced Conclusions

### Deduction 1: Proceeding as-is is acceptable only for a narrow pilot, not for efficient remote operations

**Based on:** Findings 1-6

**Reasoning:** Heartbeat and job completion are enough to know that an Agent is alive and whether jobs eventually succeeded or failed. But event persistence, local runner error logging, complete diagnostics, and detailed timeline data are missing. If a PC-side issue happens, the server may see stale heartbeat, lease timeout, or a generic `error_code` but not enough context to explain why.

**Conclusion:** The current design is safe-leaning and redaction-conscious, but operational visibility is too thin for confident scale-out.

## Hypothesized Paths

### Hypothesis 1: Server visibility may be insufficient for efficient remote troubleshooting

**Status:** Confirmed

**Theory:** The PC agent may report lifecycle/status data but not enough structured, correlated diagnostic events to reconstruct local failures from the server.

**Supporting indicators:** User concern plus desktop-agent architecture where browser/UI automation failures often happen locally.

**Would confirm:** Code shows sparse heartbeats/status only, limited log upload, weak correlation IDs, or no durable local retry queue.

**Would refute:** Code shows structured event/log upload with severity, run IDs, task IDs, retry/backfill, redaction, and server-side query surfaces.

**Resolution:** Confirmed by event no-op in PostgreSQL, ignored complete diagnostics, and missing CLI log callback.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | ------ | ------------- |
| Production runtime logs | Would show actual message volume, dropped events, payload shape, and operational usage. | Inspect deployed logs or sample agent/server log files if available. |
| Deployment configuration | Would show whether log endpoints are enabled and retained in production. | Inspect deployment env and server settings. |
| Retention policy | Would determine how long operational evidence remains available. | Add/read retention config or runbook. |

## Source Code Trace

| Element | Detail |
| ------- | ------ |
| Error origin | `src/rider_server/queue/postgres_queue.py:381` no-op event persistence; `src/rider_server/api/jobs.py:313` partial complete persistence; `src/rider_agent/__main__.py:145` missing log callback. |
| Trigger | Normal Agent run: heartbeat, claim, event, complete. |
| Condition | PC-side errors, complete transport failures, or event logs require later diagnosis. |
| Related files | `src/rider_agent/heartbeat.py`, `src/rider_agent/job_loop.py`, `src/rider_agent/workers/kakao_sender.py`, `src/rider_server/api/agents.py`, `src/rider_server/api/jobs.py`, `src/rider_server/services/agent_registry.py`, `src/rider_server/queue/postgres_queue.py`, `src/rider_server/admin/dashboard_repository_postgres.py`. |

## Conclusion

**Confidence:** High

현재 상태는 "서버가 Agent를 제어하고 기본 상태를 보는 구조"로는 괜찮지만, "PC에서 무슨 일이 있었는지 서버만 보고 빠르게 원인 분석하는 구조"로는 부족하다. 보안상 raw HTML/screenshot/secret을 안 올리는 판단은 맞지만, redacted 구조 이벤트와 진단 메타데이터까지 사라지는 것은 운영 리스크다.

## Recommended Next Steps

### Fix direction

1. PostgreSQL에서 `/v1/jobs/{id}/events`를 영속화한다. 새 테이블이 어렵다면 `audit_logs`에 `source=AGENT`, `target_type=JOB`, `diff_redacted` 형태로 저장하는 방식이 현재 14-table 제약에 맞다.
2. `jobs.result_json` 또는 additive columns에 `error_message_redacted`, `metrics`, `started_at`, `finished_at`를 보존한다. 특히 `metrics.kakao_outcome`, `auth_reason`, `duration_ms`는 운영 분석 가치가 크다.
3. `python -m rider_agent run`에서 `log` callback을 파일 writer로 연결한다. 최소 `runtime/agent/agent.log` 또는 `logs/agent.log`에 redacted line JSON을 남긴다.
4. Heartbeat `kakao_status` allow-list를 실제 worker 출력과 맞춘다. `enabled`, `queue_lag_seconds`, `sent`, `failed`를 보존하거나 agent 출력명을 server allow-list에 맞춘다.
5. 장기적으로 `diagnostic_bundle` 명령 또는 `CAPTURE_DIAGNOSTIC` job을 구현하되, raw artifact는 기본 업로드하지 말고 명시 요청 + redaction + size cap + retention으로 제한한다.

### Diagnostic

Targeted tests:

- PostgreSQL backend 또는 repository test: `/events` 후 evidence가 DB/audit에 남는지 확인.
- Complete API test: `metrics`, `error_message_redacted`, `started_at`, `finished_at`가 저장/조회되는지 확인.
- CLI test: `_run_agent_loop`가 log callback을 연결하고 token/code가 파일에 남지 않는지 확인.
- Heartbeat server test: 실제 `KakaoSenderWorker.kakao_status()` payload가 server `capacity_json.kakao_status`에 원하는 키로 남는지 확인.

## Reproduction Plan

1. Fake transport로 heartbeat success/failure를 각각 발생시킨다.
2. Fake job을 claim하게 하고 event success, event failure, complete success, complete 500을 각각 발생시킨다.
3. 서버 DB/Admin/metrics에서 무엇이 남는지 확인한다.
4. 로컬 agent log 파일에 redacted event가 남는지 확인한다.

## Side Findings

- `python3` Windows Store alias failed; BMAD customization script succeeded with `py -3`.
- The workspace already had unrelated modified files before this review; this investigation only added this case file.

## Follow-up: 2026-06-18

### New Evidence

- `audit_logs` already has the right shape for small redacted operational evidence: actor/source/action/target/reason/timestamp/result and `diff_redacted` JSON (`src/rider_server/db/models/audit.py`).
- PostgreSQL job event persistence is intentionally deferred to a future audit_logs linkage, not absent by accident (`src/rider_server/queue/postgres_queue.py:381`).
- Existing audit tests lock the idea that important operational/security events should be redacted and machine-readable, including denied actions (`tests/server/test_audit_log_schema.py`).

### Additional Findings

- The proposed direction is sound only if it stays structured and bounded. Persisting lifecycle events such as `JOB_CLAIMED`, `JOB_STARTED`, `JOB_FAILED`, `JOB_SUCCEEDED`, `COMPLETE_FAILED`, `KAKAO_QUEUE_BACKLOG`, and `AUTH_REQUIRED` gives high diagnostic value with small payloads.
- It becomes inefficient if the team treats server logging as a raw PC log sink. Raw browser HTML, screenshots, clipboard text, chat contents, full exception dumps, and frequent debug lines should stay local by default and be uploaded only through explicit diagnostic capture with redaction, size caps, and retention.
- Reusing `audit_logs` is a good short-term fit under the 14-table constraint, because it avoids schema sprawl and already carries redaction/result semantics. It is not a good long-term place for high-volume DEBUG telemetry; if event volume grows, a separate telemetry store or time-series/log sink should be considered later.
- Correlation IDs are a better efficiency lever than more logs. Every event/result should carry `agent_id`, `job_id`, `event_type`, timestamp, severity, and ideally `run_id`/`attempt` so operators can reconstruct one timeline without scanning unrelated records.

### Updated Hypotheses

- Hypothesis 2: "Adding more server-side agent evidence will improve operations without becoming wasteful" is partially confirmed. It is true for a small event vocabulary and completion diagnostics, but false for unbounded raw log upload.

### Backlog Changes

- Add an implementation story that defines an allow-list of agent event types, maps them to `audit_logs`, and explicitly rejects oversized or unknown event payloads.
- Add a retention decision before broad rollout: keep structured job/audit events longer, keep local raw logs short, and make diagnostic bundles request-scoped.

### Updated Conclusion

The better direction is not "more logs"; it is "small structured events always, heavy diagnostic evidence only on request." This improves remote troubleshooting while avoiding the future trap of noisy, expensive, privacy-sensitive server log storage.

## Follow-up: 2026-06-18 #2

### Re-review Question

서버 <> Agent 간 상태 파악 목적에서, 현재 개선 방향이 전체적으로 효율적인가를 다시 검토했다. 기준은 (1) 상시 수집 비용, (2) 장애 원인 재구성 가치, (3) 개인정보/운영 리스크, (4) 운영자 조회 효율이다.

### Efficiency Assessment

| Area | Current Shape | Efficiency Judgment | Evidence |
| ---- | ------------- | ------------------- | -------- |
| Heartbeat snapshot | Agent 생존, capability, active job, Kakao/browser 요약을 주기 전송 | 상시 상태판으로는 효율적이다. 작은 snapshot 이고 lease 연장에도 쓰인다. | `src/rider_agent/heartbeat.py:122`, `src/rider_server/api/agents.py:100`, `src/rider_server/services/agent_registry.py:148` |
| Job event API | Agent 가 `JOB_STARTED` 이벤트를 보내지만 PG 저장은 no-op | 가장 비효율적이다. 호출 비용은 쓰지만 사건 증거가 남지 않는다. | `src/rider_agent/job_loop.py:540`, `src/rider_server/api/jobs.py:355`, `src/rider_server/queue/postgres_queue.py:381` |
| Complete diagnostics | Agent 는 metrics/timing/error detail 을 보내지만 서버 complete 는 일부만 저장 | 이미 보내는 유용한 데이터를 버리는 구조라 진단 효율이 낮다. 새 로그 홍수보다 이 보존이 먼저다. | `src/rider_server/api/jobs.py:93`, `src/rider_server/api/jobs.py:313`, `src/rider_server/queue/postgres_queue.py:192` |
| Kakao status | Agent 는 집계 수치를 만들지만 서버 allow-list 와 일부 키가 맞지 않음 | 데이터 양은 적절하지만 키 불일치 때문에 운영 가치가 줄어든다. | `src/rider_agent/workers/kakao_sender.py:242`, `src/rider_server/services/agent_registry.py:132` |
| Local Agent errors | runner/reporter 는 redacted log callback 을 지원하지만 CLI run 에서 연결하지 않음 | 서버 장애 추적 이전에 PC 현장 증거도 약하다. 파일 로그는 저비용 고효율이다. | `src/rider_agent/job_loop.py:621`, `src/rider_agent/job_loop.py:586`, `src/rider_agent/__main__.py:145` |
| Admin/metrics | fleet health, current job, aggregate alerts 제공 | 관제에는 효율적이지만 PC 내부 timeline 재구성에는 부족하다. | `src/rider_server/admin/templates/_agents.html:1`, `src/rider_server/main.py:577`, `src/rider_server/metrics/policy.py:88` |

### Updated Findings

- 기존 결론은 유지한다. "더 많은 로그"가 아니라 "상시 작은 구조 이벤트 + 완료 진단 보존 + 요청형 무거운 진단"이 가장 효율적이다.
- 현재 상태는 비용을 너무 아끼는 설계라기보다, 이미 발생한 통신의 진단 가치를 충분히 저장하지 못하는 설계다. 특히 `/events` no-op 과 complete 진단 필드 미보존은 운영 효율을 직접 낮춘다.
- `audit_logs` 재사용은 단기적으로 효율적이다. 이미 `source`, `action`, `target`, `result`, `diff_redacted`가 있어 14-table 제약 안에서 작은 Agent 이벤트를 담기 쉽다. 단, DEBUG/고빈도 telemetry 저장소로 쓰면 비효율이 된다.
- Heartbeat 는 현재처럼 snapshot 중심으로 유지하는 편이 맞다. 여기에 raw log 를 섞기보다, `kakao_status` 키 정합성과 active job/run correlation 을 맞추는 것이 더 효율적이다.

### Recommended Efficient Target

1. Always-on server evidence 는 작은 allow-list 이벤트로 제한한다: `JOB_CLAIMED`, `JOB_STARTED`, `JOB_SUCCEEDED`, `JOB_FAILED`, `COMPLETE_FAILED`, `AUTH_REQUIRED`, `KAKAO_QUEUE_BACKLOG`.
2. 각 이벤트 payload 는 `agent_id`, `job_id`, `event_type`, `severity`, `created_at`, `run_id` 또는 `attempt`, `message_redacted`, 작은 `context`만 허용한다.
3. Complete payload 의 `error_message_redacted`, `metrics`, `started_at`, `finished_at`는 `jobs.result_json`의 diagnostic block 또는 additive columns 로 보존한다.
4. Agent CLI 는 redacted line JSON 로컬 파일 로그를 연결한다. 서버 업로드가 실패한 상황에서도 PC 쪽 증거가 남아야 한다.
5. Raw HTML, screenshot, clipboard, chat content, full traceback 은 상시 업로드 금지로 둔다. 필요할 때만 `CAPTURE_DIAGNOSTIC` 같은 명시 명령으로 size cap, retention, redaction 을 걸어 수집한다.
6. Admin 에는 job 단위 timeline 조회를 추가한다. 운영자는 heartbeat snapshot, job result, event/audit row 를 한 곳에서 이어봐야 한다.

### Priority Adjustment

P0: `/v1/jobs/{id}/events`를 `audit_logs` 또는 동등한 영속 저장소에 작고 제한된 payload 로 저장한다.

P0: complete 진단 필드를 버리지 않게 저장 계약과 테스트를 추가한다.

P1: `kakao_status` producer/server allow-list 를 맞춘다.

P1: `python -m rider_agent run`에 redacted local file log callback 을 연결한다.

P2: 요청형 diagnostic bundle 을 설계한다. 상시 서버 로그 확대보다 뒤에 둔다.

### Updated Conclusion

효율성 관점의 최종 판단은 "방향은 맞지만 현재 구현은 아직 효율적 상태가 아니다"이다. 적게 보내는 점은 좋지만, 이미 보내는 이벤트와 진단 값을 저장하지 않아 서버만 보고 원인을 찾는 시간이 길어진다. 가장 효율적인 다음 단계는 raw 로그 확대가 아니라, 작은 구조 이벤트와 complete 진단 필드를 먼저 영속화하고, 무거운 PC 증거는 요청형으로 남기는 것이다.

## Follow-up: 2026-06-18 #3

### Server Readiness Review

사용자 요청: "이 파일 관련하여 서버쪽 코드는 준비되어있는지 검토하세요."  
검토 범위는 이 조사 파일의 권고사항 대비 서버 수신, 저장, 조회 코드가 준비되어 있는지이다.

### Readiness Matrix

| Concern | Readiness | Evidence | Notes |
| ------- | --------- | -------- | ----- |
| Heartbeat 수신 | Prepared | `src/rider_server/api/agents.py:36`, `src/rider_server/api/agents.py:100` | 서버는 `metrics`, `capabilities`, `active_jobs`, `kakao_status`, `browser_profiles`를 받는다. |
| Active job lease 연장 | Prepared | `src/rider_server/api/agents.py:125` | heartbeat 의 `active_jobs`를 보고 best-effort lease 연장을 수행한다. |
| Job claim/complete 기본 상태 저장 | Prepared | `src/rider_server/api/jobs.py:209`, `src/rider_server/queue/postgres_queue.py:192` | job status, `result_json`, `error_code` 저장은 가능하다. |
| Agent event API | Partially prepared | `src/rider_server/api/jobs.py:355`, `tests/server/test_jobs_api.py:383` | HTTP 라우트와 in-memory test visibility 는 있다. |
| Agent event PostgreSQL 영속화 | Not prepared | `src/rider_server/queue/postgres_queue.py:381` | PG 구현은 명시적으로 no-op 이라 운영 DB에는 `JOB_STARTED` 같은 이벤트가 남지 않는다. |
| Complete diagnostic field 보존 | Not prepared | `src/rider_server/api/jobs.py:93`, `src/rider_server/api/jobs.py:313`, `src/rider_server/queue/postgres_queue.py:192` | API 는 `error_message_redacted`, `metrics`, `started_at`, `finished_at`를 받지만 저장 경로에는 넘기지 않는다. |
| `audit_logs` 재사용 기반 | Partially prepared | `src/rider_server/db/models/audit.py:25`, `src/rider_server/services/admin_action_repository_postgres.py:210` | 테이블과 record helper 는 있으나 Agent event 라우트에 연결되어 있지 않다. |
| Kakao status 운영 수치 보존 | Partially prepared | `src/rider_server/services/agent_registry.py:132`, `tests/server/test_agents_api.py:315` | allow-list 는 있으나 `enabled`, `queue_lag_seconds`, `sent`, `failed` 등 실제 Agent 집계 키 일부가 빠진다. |
| Admin status board | Prepared for status board only | `src/rider_server/admin/templates/_agents.html:1`, `src/rider_server/admin/dashboard_repository_postgres.py:211` | Agent online/offline, heartbeat, current job 은 보이나 job timeline 은 없다. |
| Operational metrics | Prepared for aggregate only | `src/rider_server/main.py:576`, `src/rider_server/metrics/repository_postgres.py:58` | fleet 집계에는 적합하지만 개별 PC/job 사건 재구성에는 부족하다. |

### Confirmed Findings

1. **서버 API 표면은 일부 준비되어 있다.** Heartbeat, job claim, complete, event endpoint 는 존재한다. 따라서 Agent 가 서버로 기본 상태와 결과를 보낼 통로는 있다.
2. **운영 증거 영속화는 아직 준비 부족이다.** `/v1/jobs/{id}/events`는 202 를 반환할 수 있지만 PostgreSQL 에서는 저장하지 않는다. 이는 "서버가 PC에서 무슨 일이 있었는지 안다"는 목표와 맞지 않는다.
3. **Complete payload 는 수신보다 저장이 약하다.** Agent 가 이미 보내는 `metrics`, timing, redacted error detail 을 서버가 받아도 최종 저장 경로에서 빠진다.
4. **`audit_logs`는 좋은 후보지만 아직 배선되지 않았다.** Admin 액션용 audit 기반은 성숙해 있으나 Agent event persistence 로 재사용하는 별도 service/repository 경계가 없다.
5. **Admin/metrics는 관제용이지 forensic timeline 용이 아니다.** 현재 화면과 지표는 상태 파악에는 쓸 수 있지만, job 단위 사건 흐름을 재구성하기에는 부족하다.

### Deduced Conclusion

서버 쪽 코드는 "Agent 제어와 현재 상태판" 수준은 준비되어 있지만, 이 조사 파일이 요구한 "문제 해결을 빠르게 하는 서버 측 Agent 관측성"은 아직 준비되지 않았다. 특히 event persistence 와 complete diagnostics preservation 이 빠져 있어, PC 장애가 발생하면 서버 DB만으로 원인 추적이 어렵다.

### Implementation Backlog Update

P0: `QueueBackend.emit_event` 또는 별도 Agent telemetry repository 를 통해 `/v1/jobs/{id}/events`를 PostgreSQL에 영속화한다. 14-table 제약을 유지하려면 `audit_logs`에 `source=AGENT`, `action=<event_type>`, `target_type=JOB`, `target_id=<job_id>`, `diff_redacted=<bounded context>`, `result=SUCCESS` 형태로 저장하는 방향이 가장 가깝다.

P0: `CompleteRequest`의 `error_message_redacted`, `metrics`, `started_at`, `finished_at`를 저장 계약에 포함한다. 새 컬럼이 부담이면 `jobs.result_json.diagnostics` 같은 bounded diagnostic block 으로 보존하는 방안을 검토한다.

P1: `heartbeat_capacity()`의 `kakao_status` allow-list 를 실제 Agent producer 와 맞춘다. 최소 `enabled`, `queue_lag_seconds`, `sent`, `failed`를 보존하거나 Agent 출력명을 서버 allow-list 에 맞춘다.

P1: Admin 에 job detail/timeline 조회를 추가한다. heartbeat snapshot, job row, audit/event row 를 job_id 기준으로 이어보는 화면이 필요하다.

P2: `/metrics/operational`은 지금처럼 비식별 fleet 집계를 유지한다. 개별 PC 진단 데이터는 metrics payload 에 섞지 않는다.

### Updated Conclusion

서버는 "받을 입구"는 대부분 갖췄지만 "운영 증거로 남기는 저장/조회"는 아직 부족하다. 따라서 다음 구현은 새 raw log 업로드보다 서버의 기존 API와 `audit_logs`/`jobs.result_json` 저장 경로를 연결하는 작업이 우선이다.
