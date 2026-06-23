# Queue Backlog Handling Policy

Date: 2026-06-23

## Purpose

When the server or Agent restarts, old queued jobs must not blindly resume if the
job is no longer safe or useful. This is especially important for browser-based
jobs such as Coupang authentication and Coupang crawling, because replaying stale
jobs can open browser windows repeatedly and trigger unintended login or 2FA
flows.

## Current Implemented Behavior

This section describes what the code does **now that this work order is
implemented** (2026-06-23). It is the source of truth for what to expect in
production today.

> **Superseded (crawl-coupang-auth-separation, 2026-06-23):** the earlier
> "one bounded **recovery crawl**" approach has been replaced by a dedicated
> auto-recovery auth job, `AUTH_COUPANG_2FA`. The scheduler now enqueues
> `AUTH_COUPANG_2FA` (not a `CRAWL_COUPANG` recovery crawl) for an `AUTH_REQUIRED`
> Coupang account with complete auto 2FA and no cooldown, and `CRAWL_COUPANG` no
> longer performs inline email 2FA. Bullets below that mention a "recovery crawl"
> read as "auto-recovery auth job." See
> `docs/goal/crawl-coupang-auth-separation-work-order-2026-06-23.md`.

- `OPEN_AUTH_BROWSER` payloads carry `requested_at` / `expires_at` (a 10–15 minute
  operator-intent TTL). Queue recovery closes an expired auth browser job as
  terminal `FAILED` with result reason `stale_auth_job_expired` instead of
  re-`PENDING`-ing it. It is not replayed after a server or Agent restart.
  `OPEN_AUTH_BROWSER` is now **manual only** — it opens the browser for a human and
  never runs automatic OTP/email 2FA.
- `AUTH_COUPANG_2FA` is the dedicated Coupang auto email-2FA recovery job. If its
  lease expires while claimed, queue recovery closes it as terminal `FAILED` with
  reason `stale_auth_recovery_abandoned` (never re-`PENDING`) so a stale auto
  recovery cannot trigger a duplicate OTP request. While running it is exposed as
  an active job so heartbeats keep extending its lease.
- `CRAWL_COUPANG` no longer performs inline login / email 2FA. A crawl that hits a
  login screen returns `AUTH_REQUIRED` and stops; auto recovery is a separate
  `AUTH_COUPANG_2FA` job.
- Scheduled `CRAWL_BAEMIN` / `CRAWL_COUPANG` payloads carry `job_origin="scheduler"`,
  `scheduled_at`, and `expires_at` (at most one interval after `scheduled_at`).
  Queue recovery closes an expired scheduled crawl (whether `PENDING`, `CLAIMED`,
  or `RUNNING`) as terminal `FAILED` with result reason `stale_crawl_skipped`.
- The scheduler reads `PlatformAccount.auth_state` and the per-account Coupang
  auto-recovery cooldown. It blocks scheduled crawl for `AUTH_REQUIRED` without
  complete auto email 2FA, `USER_ACTION_PENDING`, `BLOCKED_OR_CAPTCHA`, and
  `UNKNOWN`, and enqueues exactly one `AUTH_COUPANG_2FA` auth job for
  `AUTH_REQUIRED` Coupang with complete auto 2FA and no active cooldown (duplicate
  auth jobs are suppressed while one is active).
- A failed Coupang auto recovery sets `auto_recovery_failed_at` and
  `auto_recovery_cooldown_until` on the account, suppressing new recovery attempts
  during the cooldown. A successful recovery clears the cooldown.
- Before opening a browser/profile, the Agent calls a server preflight
  (`POST /v1/jobs/{id}/preflight`) and also defensively rechecks the payload
  `expires_at`. A denied or expired job completes with a safe reason
  (`payload_expired`, or `preflight_unavailable` when preflight is unreachable —
  fail-closed) and **does not open a browser**.

> Note: before this work order, old code re-`PENDING`-ed stale leased jobs after
> restart, so a stopped/restarted Agent could re-open browser windows for expired
> auth and crawl jobs. That legacy behavior is what these changes remove.

## Target Permanent Behavior

The long-term policy these changes move toward (some items extend beyond the
first implementation):

- Interactive/browser-opening jobs always expire quickly and never replay as a
  backlog.
- Scheduled crawls are coalesced to one useful job per target/platform; missed
  intervals are skipped, not replayed one-by-one.
- Coupang automatic recovery is attempted at most once per cooldown window, and a
  human-action state (`USER_ACTION_PENDING`, `BLOCKED_OR_CAPTCHA`) ends automatic
  retries until an operator acts.
- Operator-triggered auth (`OPEN_AUTH_BROWSER`) and scheduled crawl
  (`CRAWL_COUPANG`) stay separate concerns; the auth-start button never enqueues
  a crawl.
- Result, audit, and log records never contain passwords, verification codes,
  email app passwords, or secret-ref values — only machine-readable reason codes.

## Emergency Operator Action

If browser windows keep opening unexpectedly, **deactivate the target first, then
stop the Agent if already-queued work keeps opening windows**:

1. Open `/admin` from an allowed admin IP and select the affected customer.
2. Go to `관리` / entity management, find the Coupang 업체, and click `비활성화`
   so the monitoring target status becomes `INACTIVE`. The scheduler only selects
   `ACTIVE` targets, so this stops new scheduler-created crawl jobs for that target.
3. If windows are still opening after the target is inactive, stop the Windows
   Agent process — that means already-queued work is still being consumed. Inspect
   pending jobs before restarting.

## Verification Matrix

| Scenario | Expected behavior | Safe reason |
| --- | --- | --- |
| Server startup with expired `OPEN_AUTH_BROWSER` | Closed terminal `FAILED`, not re-`PENDING` | `stale_auth_job_expired` |
| Server startup with stale scheduled crawl (PENDING/CLAIMED/RUNNING) | Closed terminal `FAILED`, not replayed | `stale_crawl_skipped` |
| Agent startup with expired auth/crawl payload | Preflight denies / worker fails fast, no browser opens | `payload_expired` |
| Scheduler tick, `AUTH_REQUIRED` Coupang w/o auto 2FA | No crawl/auth job enqueued | `AUTH_REQUIRED_NO_AUTO_RECOVERY` |
| Scheduler tick, `AUTH_REQUIRED` Coupang w/ auto 2FA, no cooldown | One `AUTH_COUPANG_2FA` auth job enqueued (not a crawl) | `ENQUEUED_AUTH_COUPANG_2FA` |
| Scheduler tick, `AUTH_REQUIRED` Coupang w/ auto 2FA, auth job already active | No duplicate auth job | `AUTH_JOB_ALREADY_ACTIVE` |
| Manual `인증 시작`, complete auto 2FA refs | Enqueues `AUTH_COUPANG_2FA`, never `CRAWL_COUPANG` | — |
| Manual `인증 시작`, login only (no email 2FA) | Falls back to manual `OPEN_AUTH_BROWSER` | — |
| `AUTH_COUPANG_2FA` lease expires while claimed | Closed terminal `FAILED`, not re-`PENDING` (no duplicate OTP); account cooldown set | `stale_auth_recovery_abandoned` |
| `AUTH_COUPANG_2FA` PENDING past payload `expires_at` (5 min TTL) | Closed terminal `FAILED` on queue recovery / Agent preflight, browser never opens | `stale_auth_job_expired` |
| `AUTH_COUPANG_2FA` with incomplete secrets (e.g. app password missing) | Fail-closed before opening browser/IMAP | `secret_ref_unresolved` |
| `AUTH_COUPANG_2FA` IMAP login / mailbox setup failure | Stops as email-auth-required (operator must fix mailbox), not a transient retry | `EMAIL_AUTH_REQUIRED` |
| Two monitoring targets on the same Coupang account | At most one active auth job per `platform_account_id` (account-scoped dedup) | `AUTH_JOB_ALREADY_ACTIVE` |
| Coupang auto recovery success | Account `ACTIVE`, cooldown cleared, normal crawl scheduling resumes | — |
| Coupang auto recovery failure | Cooldown set, repeated auth attempts suppressed | `coupang_auto_recovery_cooldown` |

---

# Historical design notes (pre-implementation — NOT current behavior)

> **Authoritative behavior is the "Current Implemented Behavior" and "Verification
> Matrix" sections above.** Everything below this line is the original
> **pre-implementation** planning write-up. It describes the problem as it stood
> *before* the `crawl-coupang-auth-separation` work order and proposes options
> that were **not** the final design. In particular, the sections below still say
> things like "`CRAWL_COUPANG` performs inline email 2FA", "one bounded recovery
> **crawl**", and "`OPEN_AUTH_BROWSER` runs automatic OTP" — **these are obsolete.**
> In the shipped system: `CRAWL_COUPANG` never does inline 2FA, auto recovery is a
> dedicated `AUTH_COUPANG_2FA` job, and `OPEN_AUTH_BROWSER` is manual-only. Keep
> this section only as a record of the original problem statement and rationale;
> do not treat any rule here as operative.

## Current Problem (historical)

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
