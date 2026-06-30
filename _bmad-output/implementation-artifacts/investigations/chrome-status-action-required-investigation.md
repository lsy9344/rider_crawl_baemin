# Investigation: Chrome 상태 확인 필요 발생 원인

## Hand-off Brief

1. **What happened.** Admin 웹앱의 Chrome 상태 목록에 `PARSER_MISSING_DATA`와 `AUTH_REQUIRED`가 붙은 `확인 필요` 포트가 표시됐지만, 해당 target들은 현재 정상 수집 상태다.
2. **Where the case stands.** Concluded; 원인은 성공 수집 후 Agent browser profile의 `last_error_code`가 지워지지 않는 stale diagnostic 표시 버그다.
3. **What's needed next.** 성공 결과를 기록할 때 `last_error_code`를 명시적으로 clear하도록 Agent 진단 갱신 API를 수정하고 회귀 테스트를 추가한다.

## Case Info

| Field            | Value |
| ---------------- | ----- |
| Ticket           | N/A |
| Date opened      | 2026-06-30 |
| Status           | Concluded |
| System           | Windows / PowerShell / rider_result_mornitoring |
| Evidence sources | User report, source code, local HTML snapshots, runtime/DB if available |

## Problem Statement

사용자는 웹앱 `Chrome 상태`에 `정상` 포트들과 함께 `확인 필요 포트 58020 PARSER_MISSING_DATA`, `확인 필요 포트 53362 AUTH_REQUIRED`가 표시된 이유와 처리 방법을 정확히 조사해 달라고 요청했다.

## Evidence Inventory

| Source | Status | Notes |
| ------ | ------ | ----- |
| User report | Available | Chrome 상태의 포트/코드/확인 시각 7개가 제공됐다. |
| Admin template | Available | `last_error_code`가 있으면 `확인 필요`로 표시한다. |
| Agent heartbeat capacity | Available | 운영 DB `agents.capacity_json`에서 포트별 target/account/current diagnostic 확인. |
| Job history | Available | 운영 DB `jobs`에서 두 target의 최신 job들이 성공했음을 확인. |
| Admin target fragment | Available | 원격 `/admin/targets?tenant=all`에서 두 target 모두 `NORMAL` 확인. |

## Investigation Backlog

| # | Path to Explore | Priority | Status | Notes |
| - | --------------- | -------- | ------ | ----- |
| 1 | Chrome 상태 표시 규칙 확인 | High | Done | 템플릿에서 확인. |
| 2 | 포트별 target 매핑 확인 | High | Done | 운영 DB에서 확인. |
| 3 | target별 최신 job 실패 원인 확인 | High | Done | 두 target 모두 이후 성공 job 확인. |
| 4 | 성공 후 `last_error_code`가 지워지는지 확인 | Medium | Done | 코드상 `None`이 clear가 아니라 no-op라서 stale 확정. |

## Timeline of Events

| Time | Event | Source | Confidence |
| ---- | ----- | ------ | ---------- |
| 2026-06-30T02:06:16Z | Port 58020 reported `PARSER_MISSING_DATA` | User report | Confirmed |
| 2026-06-30T02:13:25Z | Port 53362 reported `AUTH_REQUIRED` | User report | Confirmed |
| 2026-06-30T02:13:30Z | Port 53362 target `팀100 남양주동부` had a successful `CRAWL_COUPANG` after prior `AUTH_REQUIRED` failure | Production DB query | Confirmed |
| 2026-06-30T02:23:21Z | Port 58020 target `표준서울마포B이츠앤홀딩스3` had a successful `CRAWL_BAEMIN` | Production DB query | Confirmed |
| 2026-06-30T02:31Z | Remote Admin target fragment showed both affected targets as `NORMAL` | `http://54.116.103.149:8000/admin/targets?tenant=all` | Confirmed |

## Confirmed Findings

### Finding 1: `확인 필요` is rendered when `last_error_code` exists

**Evidence:** `src/rider_server/admin/templates/_agents.html:32`

**Detail:** The Admin Agent table shows `확인 필요` before checking `AUTH_REQUIRED` or `READY` whenever `p.last_error_code` is present.

### Finding 2: Current Agent heartbeat maps the affected ports to active, ready profiles with stale error codes

**Evidence:** Production DB query at 2026-06-30T02:29Z.

**Detail:** `58020` maps to `표준서울마포B이츠앤홀딩스3` (`BAEMIN`, account `ACTIVE`, profile `READY/ACTIVE`, `last_error_code=PARSER_MISSING_DATA`). `53362` maps to `팀100 남양주동부` (`COUPANG`, account `ACTIVE`, profile `READY/ACTIVE`, `last_error_code=AUTH_REQUIRED`).

### Finding 3: The affected targets are currently normal in the target list

**Evidence:** Remote Admin `/admin/targets?tenant=all` at 2026-06-30T02:31Z.

**Detail:** `팀100 남양주동부` is `data-severity="NORMAL"`, `data-failcode=""`, with recent collection and delivery. `표준서울마포B이츠앤홀딩스3` is also `data-severity="NORMAL"`, `data-failcode=""`, with recent collection and delivery.

### Finding 4: The latest jobs for both affected targets succeeded after the stale errors

**Evidence:** Production DB query at 2026-06-30T02:29Z.

**Detail:** `팀100 남양주동부` had `AUTH_COUPANG_2FA` success at 02:12:52Z, `CRAWL_COUPANG` success at 02:13:30Z, and `KAKAO_SEND` success at 02:13:35Z after a prior `CRAWL_COUPANG` `AUTH_REQUIRED` failure at 01:44:59Z. `표준서울마포B이츠앤홀딩스3` had `CRAWL_BAEMIN` success at 02:23:21Z and `KAKAO_SEND` success at 02:23:27Z.

### Finding 5: Success path passes `last_error_code=None`, but the profile manager treats `None` as "do not overwrite"

**Evidence:** `src/rider_agent/workers/crawl_worker.py:236`, `src/rider_agent/browser_profile.py:593`, `src/rider_agent/browser_profile.py:611`

**Detail:** `CrawlWorker._record_crawl_diagnostic_from_result()` computes `last_error_code = None` for successful results. `BrowserProfileManager.record_profile_diagnostic()` documents and implements `None` as a partial-update no-op, so the previous `assignment.last_error_code` is preserved.

## Deduced Conclusions

### Deduction 1: The current `확인 필요` rows are stale display state, not active collection failures

**Based on:** Findings 1-5

**Reasoning:** The target rows and latest jobs are successful/normal, but the Agent profile still carries `last_error_code`. The code path intended to record success cannot clear that field because `None` means no update.

**Conclusion:** Operationally, no manual login/parser intervention is currently required for these two rows. The durable fix is code-level clearing of stale `last_error_code` on successful crawl diagnostics.

## Hypothesized Paths

### Hypothesis 1: The two rows are not Chrome process failures; they are last crawl/auth diagnostic failures attached to browser profiles

**Status:** Confirmed

**Theory:** Agent heartbeat projects profile diagnostics (`auth_state`, `last_error_code`, `last_probe_at`) into Admin Chrome status. Ports 58020 and 53362 are live profile ports whose latest diagnostic failed.

**Supporting indicators:** User report shows specific `last_error_code` values next to ports, matching the template display contract.

**Would confirm:** Agent heartbeat capacity shows browser profiles with those `cdp_port` values and matching `last_error_code`.

**Would refute:** The values come from a separate Chrome health probe unrelated to profile diagnostics.

**Resolution:** Confirmed by production `agents.capacity_json` and code trace.

### Hypothesis 2: Port 58020's original `PARSER_MISSING_DATA` came from a server-final failed job

**Status:** Refuted

**Theory:** The `PARSER_MISSING_DATA` shown in Chrome status should appear as the latest failed job for the target.

**Supporting indicators:** `crawl_worker.py` can map parser failures to `PARSER_MISSING_DATA`.

**Would confirm:** A `jobs.error_code='PARSER_MISSING_DATA'` row for target `20fa4aaa-da8a-4118-b9ca-a832eb8d49c5`.

**Would refute:** No such final job row while heartbeat still carries the diagnostic.

**Resolution:** Refuted by production jobs query: no `PARSER_MISSING_DATA` final job row was present for that target; current server-final jobs are successful.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | ------ | ------------- |
| Agent-local log for port 58020's original parser diagnostic | Would identify the exact transient parser exception that first set the stale heartbeat field. It is not needed to diagnose the stale display bug. | Collect logs from Agent PC `jena-5800h`. |

## Source Code Trace

| Element | Detail |
| ------- | ------ |
| Error origin | `src/rider_agent/browser_profile.py:611` preserves old `last_error_code` when the caller passes `None`. |
| Trigger | A successful crawl calls `CrawlWorker._record_crawl_diagnostic_from_result()` with `last_error_code=None`. |
| Condition | A prior non-null `last_error_code` already exists on the profile assignment. |
| Related files | `src/rider_server/admin/templates/_agents.html`, `src/rider_agent/workers/crawl_worker.py`, `src/rider_agent/browser_profile.py`, `src/rider_server/services/agent_registry.py` |

## Conclusion

**Confidence:** High

The current `확인 필요` rows are stale Agent heartbeat diagnostics. The affected targets are normal and have successful recent jobs; the UI still shows warning because successful diagnostics cannot clear a previously stored `last_error_code`.

## Follow-up Fix

### Implemented

- Added an explicit `clear_last_error_code` option to `BrowserProfileManager.record_profile_diagnostic()`.
- Updated `CrawlWorker._record_crawl_diagnostic_from_result()` to clear stale `last_error_code` on successful crawl results.
- Kept the existing partial-update contract: `last_error_code=None` alone still preserves the previous value.

### Verification

- Added a profile-manager regression test that records an error and then clears it.
- Added a crawl-worker regression expectation that successful crawl diagnostics request the clear.
- Verified with:
  - `.\.venv\Scripts\python.exe -m pytest tests\agent\test_browser_profile.py tests\agent\test_crawl_worker.py tests\server\test_admin_dashboard.py -q`

## Reproduction Plan

1. Seed a browser profile assignment.
2. Record a failed diagnostic (`PARSER_MISSING_DATA` or `AUTH_REQUIRED`) and confirm Admin would show warning.
3. Record a successful crawl diagnostic for the same target.
4. Confirm heartbeat omits `last_error_code` and Admin renders `정상`.

## Side Findings

- None yet.
