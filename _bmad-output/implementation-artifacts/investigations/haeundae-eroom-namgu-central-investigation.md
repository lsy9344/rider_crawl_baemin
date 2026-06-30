# Investigation: 해운대이로움 남구중앙 수집데이터누락

## Hand-off Brief

1. **What happened.** 운영 서버에서 `해운대이로움 남구중앙` target `f8762713-91ea-4d19-b47d-a06c69f6c53b`의 마지막 정상 수집은 2026-06-29 17:47 KST이고, 이후 19:02, 20:12, 21:24 KST `CRAWL_COUPANG` job이 모두 `PARSER_MISSING_DATA`로 실패했다.
2. **Where the case stands.** Status: Concluded. DB와 Admin heartbeat는 해당 profile이 `UNKNOWN / PARSER_MISSING_DATA` 상태임을 보이며, 사용자가 관찰한 쿠팡 로그인 화면과 합치된다.
3. **What's needed next.** Agent PC에서 해당 쿠팡 Chrome profile을 재로그인 또는 인증 복구한 뒤 즉시 수집을 실행하고, 이후 성공 수집이 들어오는지 확인한다.

## Case Info

| Field            | Value |
| ---------------- | ----- |
| Ticket           | N/A |
| Date opened      | 2026-06-29 |
| Status           | Concluded |
| System           | 운영 EC2 `54.116.103.149`, PostgreSQL `rider-db-1`, Windows Agent `jena-5800h` |
| Evidence sources | Production Admin fragments, production PostgreSQL read-only queries, local `logs/agent.log`, process/CDP checks |

## Problem Statement

사용자 설명: "최근 '해운대이로움 남구중앙' 고객의 상태가 '수집데이터누락'으로 변했어요. 3시간 전이라고 뜨는데 원인이 무엇인가요? 에이전트 피씨의 상태또한 쿠팡이츠 로그인 화면에 멈춰있습니다. 로그 기록 찾아서 원인 파악하세요"

## Evidence Inventory

| Source | Status | Notes |
| ------ | ------ | ----- |
| Production Admin target fragment | Available | `해운대이로움 남구중앙` target row is `WARNING`, failure `PARSER_MISSING_DATA`, last success `3시간 전`, target `f8762713-91ea-4d19-b47d-a06c69f6c53b`. |
| Production Admin agent fragment | Available | Agent `jena-5800h` online; target `f8762713...` browser profile shows `READY`, `auth_state=UNKNOWN`, `last_error_code=PARSER_MISSING_DATA`, `cdp_port=59630`. |
| Production DB `jobs` | Available | Recent target jobs queried read-only via EC2 SSH and `psql`. |
| Production DB `snapshots` | Available | Latest OK snapshot for target is 2026-06-29 17:47 KST. |
| Production DB `auth_sessions` | Available | No unresolved auth session rows for this target/account. |
| Local `logs/agent.log` | Partial | Only contains 2026-06-29T07:25:56Z "agent not started: valid identity/token required"; not sufficient for live agent trace. |
| Direct local CDP `127.0.0.1:59630` | Missing | Connection refused from this workspace, so the current Chrome tab could not be directly inspected here. |

## Investigation Backlog

| # | Path to Explore | Priority | Status | Notes |
| - | --------------- | -------- | ------ | ----- |
| 1 | Confirm target row and current status on production Admin | High | Done | Status and target id confirmed. |
| 2 | Reconstruct target job timeline from `jobs` | High | Done | 17:47 last success, then three parser-missing failures. |
| 3 | Check account/auth state | High | Done | Account remains `ACTIVE`; no auth session rows. |
| 4 | Inspect live Chrome tab via CDP | Medium | Blocked | Local connection to `127.0.0.1:59630` refused. |

## Timeline of Events

| Time | Event | Source | Confidence |
| ---- | ----- | ------ | ---------- |
| 2026-06-29 12:50:31 KST | `CRAWL_COUPANG` failed with `AUTH_REQUIRED`. | DB `jobs` query | Confirmed |
| 2026-06-29 12:50:58 KST | `AUTH_COUPANG_2FA` succeeded and account became `ACTIVE`. | DB `jobs`, `platform_accounts` query | Confirmed |
| 2026-06-29 12:51-17:47 KST | Multiple `CRAWL_COUPANG` jobs succeeded with `auth_state=ACTIVE`. | DB `jobs`, `snapshots` query | Confirmed |
| 2026-06-29 17:47:21 KST | Latest OK snapshot stored for `해운대이로움 남구중앙`. | DB `snapshots` query | Confirmed |
| 2026-06-29 19:02:18 KST | First later `CRAWL_COUPANG` failure with `PARSER_MISSING_DATA`. | DB `jobs` query | Confirmed |
| 2026-06-29 20:12:20 KST | Second `PARSER_MISSING_DATA` failure. | DB `jobs` query | Confirmed |
| 2026-06-29 21:24:22 KST | Third `PARSER_MISSING_DATA` failure. | DB `jobs` query | Confirmed |
| 2026-06-29 21:39:31 KST | Agent heartbeat reports target profile `UNKNOWN / PARSER_MISSING_DATA`. | DB `agents.capacity_json` query | Confirmed |

## Confirmed Findings

### Finding 1: The displayed warning is backed by recent failed crawl jobs

**Evidence:** Production DB `jobs` query for target `f8762713-91ea-4d19-b47d-a06c69f6c53b`.

**Detail:** After the 17:47 KST successful crawl, the next three `CRAWL_COUPANG` jobs failed at 19:02, 20:12, and 21:24 KST with `error_code=PARSER_MISSING_DATA`. The failure diagnostics contain `error_message_redacted="required crawl data missing"`.

### Finding 2: Historical performance data still exists; the issue is current page parsing

**Evidence:** Production DB `snapshots` query.

**Detail:** The latest stored OK snapshot at 17:47 KST contains normal Coupang peak-dashboard fields for `해운대이로움 남구중앙`. This is not a DB deletion or old snapshot loss; new crawl attempts are failing before a new OK snapshot is created.

### Finding 3: The server does not currently consider this target auth-required

**Evidence:** Production Admin `/admin/auth-required` fragment and DB `auth_sessions` query.

**Detail:** Auth-required list is empty for the tenant, and `auth_sessions` returned zero rows for this target/account. The platform account is `ACTIVE` with `auto_recovery_attempted_at=06-29 12:50:58 KST`.

### Finding 4: Agent heartbeat matches the failed target profile

**Evidence:** Production DB `agents.capacity_json` query.

**Detail:** Agent `jena-5800h` heartbeat at 21:39 KST reports target `f8762713...` profile `READY`, `auth_state=UNKNOWN`, `last_error_code=PARSER_MISSING_DATA`, `cdp_port=59630`, `last_probe_at=2026-06-29T12:24:20Z`.

## Deduced Conclusions

### Deduction 1: The most likely immediate cause is that the crawler is reading a non-dashboard page, consistent with a Coupang login screen

**Based on:** Findings 1-4 and the user's live observation.

**Reasoning:** `PARSER_MISSING_DATA` with `required crawl data missing` means the crawler did not find required peak-dashboard performance fields. A Coupang login screen would lack those fields. Because the failure result did not include `auth_state=AUTH_REQUIRED`, the server kept the account as `ACTIVE` and displayed the safer generic warning "수집 데이터 누락".

**Conclusion:** The target changed to warning because the Coupang crawl no longer reaches or reads the peak-dashboard data after 17:47 KST. The observed login screen is the likely page-level cause, but direct CDP inspection from this workspace was unavailable.

## Hypothesized Paths

### Hypothesis 1: Coupang session expired after 17:47 KST, but the crawl path surfaced it as parser-missing instead of auth-required

**Status:** Confirmed as the best supported explanation, with direct Chrome inspection missing.

**Theory:** The profile reached a login page or another non-dashboard page. The parser could not find required fields and raised missing-data. The auth classifier did not produce `AUTH_REQUIRED` for this run, so no auth session or 2FA recovery job was created.

**Supporting indicators:** User saw a Coupang login screen; DB failures are parser missing; account/auth session remains ACTIVE/empty.

**Would confirm:** Direct CDP or headed PC inspection showing target `f8762713...` profile on the Coupang login page at the time of failure.

**Would refute:** Evidence that the profile was on the peak-dashboard page with all required text present during a failed crawl.

**Resolution:** Supported by DB and user observation; direct CDP inspection from this workspace was blocked by connection refusal.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | ------ | ------------- |
| Direct agent Chrome tab URL/screenshot for CDP 59630 | Would prove whether the page was exactly the Coupang login page during failure. | Inspect on the Agent PC or run a diagnostic capture job while the profile is open. |
| Local agent step log for the failed crawl | Would show whether login detection failed before parser invocation. | Add/read redacted Agent local logs or capture diagnostic artifacts. |

## Source Code Trace

| Element | Detail |
| ------- | ------ |
| Error origin | `src/rider_agent/workers/crawl_worker.py` maps `MissingPerformanceDataError` to `PARSER_MISSING_DATA`; `src/rider_crawl/platforms/coupang/parser.py` raises missing-data when required peak dashboard fields are absent. |
| Trigger | Scheduled `CRAWL_COUPANG` jobs for target `f8762713...` after 17:47 KST. |
| Condition | Coupang page lacked required dashboard data, consistent with a login or non-dashboard page. |
| Related files | `src/rider_agent/workers/crawl_worker.py`, `src/rider_crawl/platforms/coupang/parser.py`, `src/rider_server/admin/routes.py`, `src/rider_server/admin/dashboard_repository_postgres.py`. |

## Conclusion

**Confidence:** Medium-High

The state changed because `CRAWL_COUPANG` for `해운대이로움 남구중앙` started failing with `PARSER_MISSING_DATA` after the last successful snapshot at 17:47 KST. The database shows the account is still marked `ACTIVE`, so the server did not open an auth-required state; it only saw that the required dashboard data was missing. Given the user's observation that the Agent PC is stuck on the Coupang Eats login screen, the practical cause is a login/session problem that is currently surfacing as parser-missing rather than auth-required.

## Recommended Next Steps

### Fix direction

1. On the Agent PC, complete Coupang login for the `해운대이로움 남구중앙` profile and then run "지금 수집" for target `f8762713...`.
2. If the next crawl succeeds, no DB repair is needed; the target will return to normal after a new OK snapshot.
3. If it fails again while still on login, inspect the protected Coupang auth classifier path because login-page detection is not turning this run into `AUTH_REQUIRED`.

### Diagnostic

Capture the current tab URL/title/screenshot for target `f8762713...` profile, and collect a redacted Agent crawl diagnostic for the next failed run.

## Reproduction Plan

1. Put the target's Coupang profile on a login page instead of peak-dashboard.
2. Run `CRAWL_COUPANG` for `f8762713...`.
3. Expected current behavior: job fails with `PARSER_MISSING_DATA`, no OK snapshot is created, target card shows "수집 데이터 누락".
4. Expected desired auth behavior: login page should be classified as auth-required and trigger the protected recovery path or user action state.

## Side Findings

- Local `logs/agent.log` is not useful for the live production failure; it only says the local agent was not started due to missing identity/token at 2026-06-29T07:25:56Z.
- Direct local access to `127.0.0.1:59630` failed with connection refused, so this workspace could not verify the Chrome tab directly.
