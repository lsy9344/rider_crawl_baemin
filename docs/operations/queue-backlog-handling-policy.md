# Queue Backlog Handling Policy

Date: 2026-06-23

## Purpose

When the server or Agent restarts, old queued jobs must not blindly resume if the
job is no longer safe or useful. This is especially important for browser-based
jobs such as Coupang authentication and Coupang crawling, because replaying stale
jobs can open browser windows repeatedly and trigger unintended login or 2FA
flows.

## Current Problem

The current system can create and run `CRAWL_COUPANG` when all of these are true:

- A Coupang monitoring target is `ACTIVE`.
- The target is due for collection.
- A Windows Agent is online and advertises the `CRAWL_COUPANG` capability.
- Scheduler tenant/subscription gates allow work.

The scheduler currently does not block scheduled crawling only because the linked
platform account is in `AUTH_REQUIRED`. That means an account that needs login
can still cause repeated `CRAWL_COUPANG` attempts while the monitoring target
stays active.

Important nuance: `CRAWL_COUPANG` is not a pure data-read job today. The current
crawler can attempt Coupang login recovery and email 2FA inside the crawl flow
when `coupang_auto_email_2fa_enabled` is true. So the policy must not be
"always block `CRAWL_COUPANG` when auth is required." The safer policy is:

- allow one scheduled crawl to perform automatic recovery when recovery inputs
  are complete;
- block or delay repeated crawls after recovery fails;
- keep operator-triggered auth jobs separate from scheduled crawl jobs.

There is also a second class of risk: stale jobs that were queued before a
server or Agent outage can run after recovery even though their original user
intent has expired.

## Target Behavior

Queued work should be replayed only when it is still valid. Interactive or
browser-opening jobs should expire quickly. Scheduled crawl jobs should be
coalesced or skipped when stale, not replayed as a backlog.

## Job Handling Rules

### `OPEN_AUTH_BROWSER`

`OPEN_AUTH_BROWSER` is an interactive authentication job. It is tied to the
operator's current action.

Required behavior:

- Expire stale pending jobs after a short TTL.
- Allow at most one pending/running auth browser job per target.
- Do not replay old auth jobs after server or Agent restart.
- If a newer auth job exists for the same target, cancel or ignore older ones.
- Record a safe result reason such as `stale_auth_job_expired`.

Recommended TTL:

- 10 to 15 minutes for manual auth.
- 3 to 5 minutes for automated email 2FA if no browser progress is observed.

### `CRAWL_COUPANG`

`CRAWL_COUPANG` is real Coupang site crawling, but the current code also has a
built-in automatic login/email-2FA recovery path. That recovery path is useful
for the normal 6-hour Coupang session expiry case, because one scheduled crawl
can restore the session and continue collection without operator work.

This does not mean `CRAWL_COUPANG` should be used as a general auth button or
allowed to retry forever. It should be a scheduled crawl with at most one
automatic recovery attempt.

Required behavior:

- The auth-start action must enqueue only `OPEN_AUTH_BROWSER`, not
  `CRAWL_COUPANG`.
- Scheduler-created `CRAWL_COUPANG` may run when automatic Coupang email 2FA is
  fully configured, even if the previous account state is `AUTH_REQUIRED`.
- Scheduler-created `CRAWL_COUPANG` must be blocked when automatic email 2FA is
  not configured, the last recovery failed recently, the account is
  `USER_ACTION_PENDING`, or the account is blocked by captcha.
- A stale scheduled crawl should be skipped or replaced by one fresh crawl, not
  replayed once per missed interval.
- Before opening a browser, the Agent should fail fast if the job payload is too
  old or if server state says recovery is not allowed.
- If automatic recovery fails, record a safe result reason such as
  `coupang_auto_2fa_failed` and suppress more crawl attempts until the next
  allowed recovery window or operator action.

Recommended stale policy:

- If the job was scheduled more than one interval ago, skip it and advance
  `next_run_at`.
- If several crawl jobs exist for the same target, keep only the newest useful
  one.

Recommended recovery policy:

- When the account is `ACTIVE`, run `CRAWL_COUPANG` normally.
- When the account is `AUTH_REQUIRED` and auto email 2FA is complete, allow one
  `CRAWL_COUPANG` recovery attempt.
- When recovery succeeds, continue the same crawl or enqueue one immediate fresh
  crawl.
- When recovery fails, mark the account as `AUTH_REQUIRED` or
  `USER_ACTION_PENDING`, do not keep opening browser windows, and show the
  operator that auth needs attention.
- When captcha or unsupported auth appears, do not retry automatically.

Code ownership note:

- The low-level Coupang login/email-2FA browser steps should live in one shared
  auth module.
- `OPEN_AUTH_BROWSER` should call that module for operator-triggered recovery.
- `CRAWL_COUPANG` should call that same module only as a bounded pre-crawl
  recovery step.
- The crawl parser should stay focused on collecting and parsing performance
  data after the session is ready.

### `CRAWL_BAEMIN`

Use the same stale scheduled crawl policy as Coupang, but without Coupang-specific
auth and 2FA checks.

Required behavior:

- Do not replay every missed interval after downtime.
- Keep at most one useful crawl job per target.
- Skip stale jobs with a safe result reason such as `stale_crawl_skipped`.

### Delivery Jobs

Delivery jobs are more sensitive because they can send external messages.

Required behavior:

- Preserve idempotency keys.
- Never duplicate sends after restart.
- If the message content is stale, skip instead of sending late.
- Keep a clear audit trail for skipped delivery.

## Queue Recovery Rules

On server startup or recovery:

1. Reclaim jobs that were `RUNNING` or `CLAIMED` on dead Agents.
2. Expire stale interactive jobs.
3. Mark stale crawl jobs as skipped instead of running a backlog.
4. Keep only one pending crawl per target and platform.
5. Leave delivery jobs to idempotent delivery recovery rules.

On Agent startup:

1. Do not immediately execute stale browser-opening jobs.
2. Ask the server for the latest job state before opening a browser.
3. If the linked account is auth-required and the job is a crawl, execute it
   only when bounded automatic recovery is allowed for that target.
4. If the job is older than its TTL, report expired.

## Scheduler Rules

The scheduler should include platform account auth state in its due-target facts.

For Coupang:

- `ACTIVE` account: scheduled crawl may be created.
- `AUTH_REQUIRED` with complete auto email 2FA settings: create at most one
  bounded recovery crawl.
- `AUTH_REQUIRED` without complete auto email 2FA settings: do not create
  `CRAWL_COUPANG`; surface auth-required state.
- Recent automatic recovery failure: do not create another `CRAWL_COUPANG`
  until a cooldown expires or an operator restarts auth.
- `USER_ACTION_PENDING`: do not create `CRAWL_COUPANG`.
- `BLOCKED_OR_CAPTCHA`: do not create `CRAWL_COUPANG`; require operator action.
- `UNKNOWN`: conservative default is skip and require auth check.

This avoids the current failure mode where an active monitoring target keeps
creating `CRAWL_COUPANG` even though the account cannot crawl successfully, while
still allowing normal 6-hour Coupang session expiry to be recovered
automatically.

## Admin Operation

If browser windows keep opening unexpectedly, stop the source of work first:

1. Open `/admin` from an allowed admin IP.
2. Select the affected customer.
3. Go to `관리` / entity management.
4. In `등록된 업체: 편집 / 비활성화`, select the Coupang 업체.
5. Click `비활성화`.
6. Confirm that the monitoring target status becomes `INACTIVE`.

This stops scheduler-created crawl jobs for that target because the scheduler
only selects `ACTIVE` monitoring targets.

If windows are still opening after the target is inactive, stop the Windows Agent
and inspect pending jobs. That means already-queued work is still being consumed.

## Verification Criteria

The change is complete only when these checks pass:

- Pressing `인증 시작` creates `OPEN_AUTH_BROWSER` only.
- Pressing `인증 시작` never creates `CRAWL_COUPANG`.
- `AUTH_REQUIRED` Coupang accounts with complete auto email 2FA receive at most
  one bounded recovery crawl.
- `AUTH_REQUIRED` Coupang accounts without auto email 2FA do not receive
  scheduled `CRAWL_COUPANG`.
- A failed automatic recovery suppresses repeated browser-opening crawl attempts.
- Restarting the server does not replay stale `OPEN_AUTH_BROWSER` jobs.
- Restarting the Agent does not open browsers for expired auth jobs.
- Missed scheduled crawls are coalesced or skipped, not replayed one by one.
- Skipped jobs include safe result reasons without credentials or verification
  codes.

## Operational Notes

Temporary mitigation:

- Deactivate the affected monitoring target.
- Or stop the Agent process.

Permanent fix:

- Add scheduler auth-state and auto-recovery gating.
- Add queue recovery expiry for interactive jobs.
- Add stale scheduled crawl coalescing.
- Add Agent-side fail-fast checks before opening a browser.
