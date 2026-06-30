# Investigation: 남구인라이더스 남구중앙 로그인 인증 복구

## Hand-off Brief

1. **What happened.** Confirmed: `남구인라이더스 남구중앙` target `24e238e6-5d73-40e5-acab-c06be8cfcbd6`는 2026-06-30 09:13 KST `CRAWL_COUPANG`에서 `AUTH_REQUIRED`로 실패했고, 09:21 KST Admin `AUTH_START`가 만든 `AUTH_COUPANG_2FA` job이 09:22 KST 성공했다.
2. **Where the case stands.** Status: Concluded. DB상 계정은 `ACTIVE`, recovery cooldown은 없음, 최신 OK snapshot은 2026-06-30 11:30 KST까지 들어왔다.
3. **What's needed next.** 자동복구를 실패 직후 즉시 원하면 scheduler가 이미 전진시킨 `next_run_at`만 기다리는 현재 정책을 바꿔야 한다.

## Case Info

| Field | Value |
| ----- | ----- |
| Ticket | N/A |
| Date opened | 2026-06-30 |
| Status | Concluded |
| System | 운영 EC2 `i-0e6a710a505e6b3c4`, PostgreSQL `rider-db-1`, Agent `jena-5800h` |
| Evidence sources | Production DB read-only SSM/psql queries, source code scheduler/queue/admin action trace |

## Problem Statement

사용자 질문: "`남구인라이더스 남구중앙` 고객의 로그인+인증 및 복구 과정에 대해 로그를 조사하세요. 주기는 20분인데, 왜 실패했고, 실패했는데 왜 복구를 못했나요?"

## Evidence Inventory

| Source | Status | Notes |
| ------ | ------ | ----- |
| Production DB `monitoring_targets`/`platform_accounts` | Available | target is `ACTIVE`, interval is 20, account auth_state is `ACTIVE`, all login/email 2FA refs exist, cooldown is null. |
| Production DB `jobs` | Available | Recent auth-required failures and recovery jobs reconstructed. |
| Production DB `audit_logs` | Available | 2026-06-30 09:21 `AUTH_START` created job `95a23837...`; 09:34/09:35/09:57 `TEST_CRAWL` actions followed. |
| Production DB `auth_sessions` | Available | No unresolved auth session rows for this account. |
| Source code | Available | Scheduler uses `interval + deterministic jitter`; auth recovery is considered during due-target scheduler flow. |

## Timeline of Events

| Time | Event | Source | Confidence |
| ---- | ----- | ------ | ---------- |
| 2026-06-29 14:26:33 KST | Manual `TEST_CRAWL` `CRAWL_COUPANG` failed `AUTH_REQUIRED`. | DB `jobs`, `audit_logs` | Confirmed |
| 2026-06-29 14:27:01 KST | Scheduler `AUTH_COUPANG_2FA` succeeded; `auth_recovery_state=ACTIVE`. | DB `jobs` | Confirmed |
| 2026-06-29 20:35:11 KST | Scheduled `CRAWL_COUPANG` failed `AUTH_REQUIRED`. | DB `jobs` | Confirmed |
| 2026-06-29 20:41:24 KST | Scheduler `AUTH_COUPANG_2FA` succeeded; following crawl succeeded at 20:42. | DB `jobs` | Confirmed |
| 2026-06-30 09:13:22 KST | Scheduled `CRAWL_COUPANG` failed `AUTH_REQUIRED`; result auth_state was `AUTH_REQUIRED`. | DB `jobs` | Confirmed |
| 2026-06-30 09:21:51 KST | Admin `AUTH_START` created `AUTH_COUPANG_2FA` job `95a23837...`. | DB `audit_logs` | Confirmed |
| 2026-06-30 09:22:24 KST | `AUTH_COUPANG_2FA` succeeded; account returned `ACTIVE`. | DB `jobs`, `platform_accounts` | Confirmed |
| 2026-06-30 09:35-09:39 KST | Manual test crawls and the next scheduled crawl succeeded. | DB `jobs`, `snapshots`, `audit_logs` | Confirmed |
| 2026-06-30 11:30 KST | Latest checked OK snapshot exists. | DB `snapshots` | Confirmed |

## Confirmed Findings

### Finding 1: The actual crawl failure was auth-required, not parser/data failure

**Evidence:** Production DB `jobs`: job `4fd76a28-f766-44be-99c6-44696ffae401`, `CRAWL_COUPANG`, completed 2026-06-30 09:13:22 KST, `status=FAILED`, `error_code=AUTH_REQUIRED`, `result_json.auth_state=AUTH_REQUIRED`.

**Detail:** The crawler reached a login/auth-needed state. The subsequent successful 2FA job shows the stored login/email 2FA refs were usable.

### Finding 2: Recovery did happen, but the 2026-06-30 recovery was manually triggered before scheduler auto-recovery would naturally run

**Evidence:** Production DB `audit_logs`: 2026-06-30 09:21:51 KST `AUTH_START` created job `95a23837-0a44-4190-9020-7ee9c1121b7f`; DB `jobs`: that job succeeded at 09:22:24 KST with `auth_state=ACTIVE`, `auth_recovery_state=ACTIVE`.

**Detail:** That job has no scheduler `job_origin`/`scheduled_at`, matching Admin action creation rather than scheduler-created recovery. So "not recovered" is not true by DB state; "not auto-immediately recovered" is the accurate phrasing.

### Finding 3: The 20-minute setting becomes about 27m37s between scheduled runs for this target

**Evidence:** Source `src/rider_server/scheduler/policy.py:58` computes deterministic jitter from target id, and `src/rider_server/scheduler/policy.py:76` sets next run to `now + interval + jitter`. For target `24e238e6...`, local calculation gives jitter `457s` = `7m37s`; total cadence is `27m37s`.

**Detail:** This explains why normal scheduled rows appear around 08:43, 09:12, 09:39, 10:07, 10:35, 11:03, 11:30 instead of exactly every 20 minutes.

## Deduced Conclusions

### Deduction 1: Failure cause

**Based on:** Finding 1 and successful later auth recovery.

**Reasoning:** `AUTH_REQUIRED` means the Coupang page/session required login/auth. Since `AUTH_COUPANG_2FA` succeeded minutes later, this was not a bad password/email-app-password case.

**Conclusion:** The immediate failure was a Coupang session/auth requirement, likely session expiration or re-auth prompt.

### Deduction 2: Why it looked like recovery did not work

**Based on:** Findings 2-3 and scheduler code.

**Reasoning:** A scheduled crawl advances `next_run_at` when the job is created. The 09:13 failure came from a scheduled crawl whose next normal due time was around 09:39 because of 20m + 7m37s jitter. Scheduler auto-recovery is checked when the target is due and auth_state is `AUTH_REQUIRED`; before that due time, Admin `AUTH_START` manually created the auth job at 09:21.

**Conclusion:** Auto-recovery did not immediately fire after the 09:13 failure because the scheduler had already moved the next due window forward. Manual auth recovery ran first and succeeded.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | ------ | ------------- |
| Agent local browser screenshot exactly at 09:13 | Would show the exact Coupang screen. | Agent PC diagnostic capture. |

## Source Code Trace

| Element | Detail |
| ------- | ------ |
| Scheduled interval behavior | `src/rider_server/scheduler/policy.py:58`, `src/rider_server/scheduler/policy.py:76` |
| Auth gate behavior | `src/rider_server/scheduler/policy.py:224`, `src/rider_server/scheduler/service.py:491` |
| Admin auth start path | `src/rider_server/services/admin_action_service.py:871` |
| Recovery cooldown/result persistence | `src/rider_server/queue/postgres_queue.py:269` |

## Conclusion

**Confidence:** High

The 09:13 failure was a real Coupang authentication-required event. It was recovered successfully at 09:22 through Admin `AUTH_START` / `AUTH_COUPANG_2FA`, and later crawls succeeded. The reason it did not auto-recover immediately is scheduler timing: the target's configured 20-minute interval is actually scheduled as 20 minutes plus deterministic jitter, and the failed scheduled crawl had already pushed the next due time forward.

## Recommended Next Steps

### Fix direction

If immediate automatic recovery is desired, change the scheduler/job completion flow so an `AUTH_REQUIRED` scheduled crawl can enqueue `AUTH_COUPANG_2FA` immediately, or reset the target to due on auth-required completion. This touches the protected Coupang auth contract and needs focused regression tests.

### Diagnostic

For future incidents, compare three DB facts first: failed `CRAWL_COUPANG.error_code`, next `AUTH_COUPANG_2FA` job origin, and target `next_run_at`.

## Reproduction Plan

1. Let a scheduled Coupang crawl enqueue and advance `next_run_at`.
2. Make the job complete with `AUTH_REQUIRED`.
3. Observe account auth_state becomes `AUTH_REQUIRED`, but scheduler auto-recovery waits until the target is due again unless Admin `AUTH_START` is used first.

