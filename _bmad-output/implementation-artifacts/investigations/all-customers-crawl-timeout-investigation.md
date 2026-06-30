# Investigation: 전체 고객 CRAWL_TIMEOUT 표시

## Hand-off Brief

1. **What happened.** Confirmed: 2026-06-29 22:04-22:14 KST에 여러 고객의 scheduled crawl job 4건이 `CRAWL_TIMEOUT`으로 닫혔지만, 실제 Agent 실행 timeout이 아니라 queue recovery가 만료된 scheduler job을 `stale_crawl_skipped`로 안전 폐기한 기록이었다.
2. **Where the case stands.** Concluded for the shared symptom; "모든 고객이 현재 timeout"이라는 전제는 DB 기준으로 refuted이며, 22:17 이후 일부 후속 수집은 성공으로 회복됐다.
3. **What's needed next.** 운영 설정에서 1-2분 주기 target이 단일 Agent(`max_in_flight=1`)에 몰린 상태를 완화하거나, scheduler job TTL/표시 문구를 분리해 `stale_crawl_skipped`가 실제 crawl timeout처럼 보이지 않게 한다.

## Case Info

| Field | Value |
| ----- | ----- |
| Ticket | N/A |
| Date opened | 2026-06-29 |
| Status | Concluded |
| System | 운영 EC2 `i-0e6a710a505e6b3c4`, PostgreSQL `rider-db-1`, Agent `jena-5800h` |
| Evidence sources | Production DB read-only queries through SSM, source code, prior investigation/work-order docs |

## Problem Statement

사용자 보고: "어떠한 이유에서 해운대이로움, 표준경기남양주..., H&J, 팀100 등 모든 고객의 상태가 '수집 작업이 제한...' 에러가 발생했어요. 원인을 찾으세요."

## Evidence Inventory

| Source | Status | Notes |
| ------ | ------ | ----- |
| Production DB `jobs` | Available | Recent timeout rows, payload TTL, result reason 확인. |
| Production DB `monitoring_targets`/`tenants`/`subscriptions` | Available | Active targets, interval, next_run_at, gate 상태 확인. |
| Production DB `agents` | Available | Agent online, `max_in_flight=1`, capabilities 확인. |
| Source code | Available | `recover_stale()` closes expired scheduled crawl as `FAILED/CRAWL_TIMEOUT` with `reason=stale_crawl_skipped`. |
| Local DB tunnel | Missing | `127.0.0.1:55434` refused; SSM path used instead. |

## Timeline of Events

| Time | Event | Source | Confidence |
| ---- | ----- | ------ | ---------- |
| 2026-06-29 19:15 KST | Production deploy recreated backend/scheduler/queue/telegram containers. | Existing `ec2-disconnect-investigation.md` | Confirmed |
| 2026-06-29 21:56-22:03 KST | Several tight-interval crawls succeeded on Agent `jena-5800h`. | DB `jobs` query | Confirmed |
| 2026-06-29 22:04:50 KST | `표준경기남양주C팀100퍼센트` scheduled crawl was closed `CRAWL_TIMEOUT` / `stale_crawl_skipped`. | DB `jobs` query | Confirmed |
| 2026-06-29 22:07:50 KST | `H&J` and `팀100 남양주동부` scheduled crawls were closed `CRAWL_TIMEOUT` / `stale_crawl_skipped`. | DB `jobs` query | Confirmed |
| 2026-06-29 22:14:51 KST | `표준서울마포B이츠앤홀딩스3` scheduled crawl was closed `CRAWL_TIMEOUT` / `stale_crawl_skipped`. | DB `jobs` query | Confirmed |
| 2026-06-29 22:15-22:17 KST | Follow-up/manual crawls for several affected targets succeeded. | DB `jobs`, audit log | Confirmed |
| 2026-06-29 22:18 KST | `해운대이로움 남구중앙` still failed with `PARSER_MISSING_DATA`, a separate existing Coupang page/parsing/login issue. | DB `jobs`, prior investigation | Confirmed |

## Confirmed Findings

### Finding 1: The recent shared `CRAWL_TIMEOUT` rows are stale queue cleanup, not browser execution timeout

**Evidence:** Production DB rows for jobs `7c97bc4c...`, `e66b835d...`, `ae14c8a8...`, `0bf0a08e...`.

**Detail:** These rows have no `claimed_at`, no `agent_id`, no `duration_ms`, `payload_origin=scheduler`, and `result_json.reason=stale_crawl_skipped`. That means the Agent never opened Chrome for those rows.

### Finding 2: The expired scheduler payloads had very short TTLs

**Evidence:** Production DB `payload_json` query.

**Detail:** `표준경기남양주C팀100퍼센트` and `표준서울마포B...` had `timeout_seconds=60`; `H&J` and `팀100 남양주동부` had `timeout_seconds=180`. The jobs were closed shortly after `expires_at`.

### Finding 3: The fleet is over-compressed for the configured intervals

**Evidence:** Production DB `agents` and active target interval query.

**Detail:** Only one online Agent is present, `jena-5800h`, with `max_in_flight=1`. Active targets include three 1-minute targets and two 2-minute targets, plus 60-minute Coupang targets. This can create more due jobs than one Agent can safely finish before short scheduler TTLs expire.

### Finding 4: Not all customers currently remain in `CRAWL_TIMEOUT`

**Evidence:** Current active target failure query at DB time 2026-06-29 22:17 KST.

**Detail:** Current failures after latest success were `H&J` with `AGENT_JOB_EXECUTION_ERROR` and `해운대이로움 남구중앙` with `PARSER_MISSING_DATA`; the shared `CRAWL_TIMEOUT` rows had already been followed by successful crawls for some affected targets.

## Deduced Conclusions

### Deduction 1: The common cause is scheduler backlog/TTL expiry under single-Agent capacity

**Based on:** Findings 1-3.

**Reasoning:** Scheduler-created jobs expire if not claimed before `payload_json.expires_at`. With one Agent processing one job at a time, 1-2 minute intervals across several targets can leave some due jobs unclaimed long enough for queue recovery to close them. Code maps that cleanup to `error_code=CRAWL_TIMEOUT`, so the UI text says "수집 작업이 제한 시간 안에 완료되지 않음" even though this specific row was never run by the Agent.

**Conclusion:** The shared `CRAWL_TIMEOUT` display was caused by capacity/backlog plus stale scheduled job cleanup, not simultaneous Coupang/Baemin login failure across all customers.

### Deduction 2: `해운대이로움 남구중앙` is a separate issue

**Based on:** Finding 4 and prior `haeundae-eroom-namgu-central-investigation.md`.

**Reasoning:** Its latest failures are `PARSER_MISSING_DATA` with "required crawl data missing", matching the earlier Coupang login/non-dashboard-page diagnosis. It is not part of the shared stale timeout mechanism.

**Conclusion:** Treat `해운대이로움 남구중앙` separately from the recent stale `CRAWL_TIMEOUT` rows.

## Hypothesized Paths

### Hypothesis 1: Recent admin changes increased schedule pressure

**Status:** Supported, not the root cause alone.

**Theory:** Recent creates/updates and test crawls around 21:57-22:16 KST added new active targets and manual work, increasing pressure on the one-Agent queue.

**Supporting indicators:** Audit log shows `표준서울마포B...` tenant/subscription/target creation and multiple `TEST_CRAWL` actions in the same window.

**Would confirm:** A full before/after queue load comparison showing due volume increased past one-Agent capacity at that time.

**Would refute:** Evidence that the same intervals and target count had been stable without stale cleanup before the window.

**Resolution:** Open as contributing context; core mechanism is already confirmed by job payloads and recovery result.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | ------ | ------------- |
| Agent local log around 22:04-22:14 KST | Would show exactly which job occupied the only worker slot while other jobs expired. | Inspect Windows Agent logs on `jena-5800h`. |
| Admin UI screenshot at report time | Would confirm whether the user saw target cards, job queue rows, or recent failure text. | Capture `/admin` at incident time or reproduce from DB. |

## Source Code Trace

| Element | Detail |
| ------- | ------ |
| Error origin | `src/rider_server/queue/postgres_queue.py:675` `recover_stale()` |
| Trigger | Scheduler-created crawl jobs pass `payload_json.expires_at` while still `PENDING`. |
| Condition | One Agent with `max_in_flight=1` cannot claim all due 1-2 minute target jobs before short TTL expiry. |
| Related files | `src/rider_server/queue/states.py`, `src/rider_server/scheduler/service.py`, `src/rider_server/admin/routes.py`, `docs/operations/queue-backlog-handling-policy.md` |

## Conclusion

**Confidence:** High

The shared "수집 작업이 제한 시간 안에 완료되지 않음" rows were stale scheduler jobs closed by server queue recovery. They were not all real browser timeouts, because the rows have no claim/agent/duration and explicitly carry `result_json.reason=stale_crawl_skipped`. The deeper operational cause is that several active targets are configured at 1-2 minute intervals while only one Agent can process one job at a time.

## Recommended Next Steps

### Fix direction

1. Raise the shortest production intervals or reduce active 1-2 minute targets unless additional Agent capacity is added.
2. Consider separating UI wording for `stale_crawl_skipped` from real `CRAWL_TIMEOUT`, so stale cleanup does not look like an Agent/Chrome timeout.
3. For `해운대이로움 남구중앙`, continue the separate parser/login-page investigation; the latest evidence is still `PARSER_MISSING_DATA`.

### Diagnostic

Run a queue-capacity check whenever adding 1-minute targets: expected average crawl duration times due frequency must fit under available `max_in_flight`.

## Reproduction Plan

1. Configure several active targets with 1-2 minute intervals on a single Agent with `max_in_flight=1`.
2. Let scheduler enqueue multiple crawl jobs close together.
3. Hold the Agent busy long enough that one pending job's `payload_json.expires_at` passes.
4. Queue recovery closes that pending job as `FAILED`, `error_code=CRAWL_TIMEOUT`, `result_json.reason=stale_crawl_skipped`.

## Side Findings

- Local DB tunnel `127.0.0.1:55434` was down during investigation; production DB reads were done through AWS SSM.
- `H&J` also had one real Agent-side failure at 22:14 KST: `AGENT_JOB_EXECUTION_ERROR` with a Windows temp `stderr.log` path error. A later H&J crawl at 22:17 KST succeeded.
