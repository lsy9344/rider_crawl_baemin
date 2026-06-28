# Project Claude Instructions

## Coupang Login / Email 2FA Protected Contract

The Coupang login and email 2FA flow is a protected production contract. It has
broken several times because small selector, timeout, routing, or documentation
changes altered a working flow. Do not casually refactor it.

Protected runtime files:

- `src/rider_crawl/auth/coupang_email_2fa.py`
- `src/rider_agent/auth/coupang_gmail_2fa.py`
- `src/rider_agent/worker_composition.py`
- `src/rider_crawl/platforms/coupang/crawler.py`
- `src/rider_server/services/admin_action_service.py`
- `src/rider_server/scheduler/service.py`
- `src/rider_server/queue/postgres_queue.py`

Protected tests:

- `tests/test_coupang_email_2fa.py`
- `tests/agent/test_coupang_gmail_2fa.py`
- `tests/agent/test_job_loop.py`
- `tests/test_coupang_crawler.py`
- `tests/server/test_admin_actions.py`
- `tests/server/test_scheduler_tick.py`
- `tests/server/test_queue_backend.py`
- `tests/server/test_queue_recovery.py`

Current behavior to preserve:

- `AUTH_COUPANG_2FA` and Coupang crawl session recovery both use
  `recover_coupang_session_with_email_2fa()`.
- The flow is: primary login if needed, select email authentication, click the
  send-code button, read the OTP by IMAP, fill the code, submit, then reopen the
  target page.
- The send-code button may be visible before it is actionable. Keep the
  interaction timeout long enough for that case.
- A 2FA screen can contain visible "아이디" / login text. Do not classify that
  screen as the primary login screen when 2FA signals are present.
- Hidden duplicate buttons can exist. Prefer visible role/text targets.
- Never log, persist, or return OTPs, Coupang passwords, email app passwords, or
  plaintext secret values.

Before changing any protected runtime file:

1. Trace every caller and payload path listed above.
2. Check whether docs disagree with code. Code and passing contract tests are the
   source of truth for this flow.
3. Add or update a focused regression test before changing behavior.
4. Run the protected test set, at minimum:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
   ```

5. For selector, wait, login, 2FA, CDP, or agent-routing changes, also verify a
   real headed browser flow against the agent PC or a local Chrome session before
   claiming the fix is complete.

If a future task only touches unrelated code, leave these files alone. If a task
seems to require changing this flow, explain the reason and the verification plan
before editing.
