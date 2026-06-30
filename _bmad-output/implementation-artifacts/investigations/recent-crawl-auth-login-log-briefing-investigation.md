# Investigation: Recent Crawl Auth Login Log Briefing

## Hand-off Brief

1. **What happened.** 최근 크롤/인증/로그인 증상은 단일 장애가 아니라 2FA 메일 인증 실패, 로그인 화면이 parser 누락으로 보인 문제, stale scheduler job이 `CRAWL_TIMEOUT`처럼 보인 문제가 섞여 있었다.
2. **Where the case stands.** 최신 원격 Admin 스냅샷은 인증 필요 0건, 큐 0건이지만, 직전 로그와 조사 기록에는 사용자 혼선을 만들 수 있는 실패 표시가 남아 있다.
3. **What's needed next.** UI 문구와 진단 로그를 실제 원인 단위로 더 분리하고, Agent PC 로컬 step 로그/진단 artifact 확보를 보강한다.

## Case Info

| Field | Value |
| --- | --- |
| Date opened | 2026-06-30 |
| Status | Concluded |
| System | Windows local workspace, remote Admin snapshots, production investigation notes |
| Evidence sources | `logs/agent.log`, `runtime/remote_*`, `.playwright-cli/*.log`, recent investigation files, git history |

## Problem Statement

최근 발생한 크롤/인증/로그인 관련 로그를 조회하고, 사용자가 오해할 수 있는 상태 표시나 처리 과정 부족분을 검토한다.

## Evidence Inventory

| Source | Status | Notes |
| --- | --- | --- |
| Local app log | Partial | `logs/agent.log` has only local agent registration failure. |
| Remote Admin snapshots | Available | 2026-06-28 auth/job failure fragment and 2026-06-29 clear fragment exist. |
| Playwright console logs | Available | Admin password-field DOM warnings only; crawl/auth cause evidence 없음. |
| Recent investigations | Available | Auth, parser-missing, stale timeout, EC2 deploy, health reports reviewed. |
| Docker local logs | Missing | Docker Desktop engine not running locally. |

## Confirmed Findings

### Finding 1: 2026-06-28 snapshot had one auth-required target and two failed Coupang 2FA jobs

**Evidence:** `runtime/remote__admin_auth_required_tenant_864fc127_1138_40d2_b115_a24decf8a2b8.html:4`, `runtime/remote__admin_jobs_tenant_864fc127_1138_40d2_b115_a24decf8a2b8.html:24`

**Detail:** H&J was shown as login expired/auth check needed; two `쿠팡 2차인증` rows were failed while Agent was online.

### Finding 2: 2026-06-29 latest remote snapshot shows no auth-required rows and an empty queue

**Evidence:** `runtime/remote_ce2d__admin_auth-required_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html:4`, `runtime/remote_ce2d__admin_jobs_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html:23`

**Detail:** Latest captured tenant shows `인증 필요 대상 0건` and `처리 중인 작업이 없습니다`.

### Finding 3: Local Agent log is not useful for production crawl/auth diagnosis

**Evidence:** `logs/agent.log:1`

**Detail:** Local log only says valid identity/token is required before Agent start.

### Finding 4: A recent shared CRAWL_TIMEOUT display was stale queue cleanup, not real browser timeout

**Evidence:** `_bmad-output/implementation-artifacts/investigations/all-customers-crawl-timeout-investigation.md`

**Detail:** Stale scheduler rows had no claimed agent/duration and carried `reason=stale_crawl_skipped`.

### Finding 5: One Coupang login-screen case surfaced as `PARSER_MISSING_DATA`

**Evidence:** `_bmad-output/implementation-artifacts/investigations/haeundae-eroom-namgu-central-investigation.md`

**Detail:** The account stayed ACTIVE while crawl results failed on missing dashboard data, matching a login/non-dashboard page.

## Deduced Conclusions

### Deduction 1: The user-facing statuses are technically defensible but not always human-clear

**Based on:** Findings 1-5.

**Reasoning:** The system safely avoids claiming auth-required when parser/CDP evidence is uncertain, and stale queue cleanup safely closes expired work. However, both surfaces can look like a normal crawl/browser failure to an operator.

**Conclusion:** Main improvement is not more retry logic first; it is clearer state wording and better local Agent evidence.

## Conclusion

**Confidence:** Medium-High

The latest snapshot is clean for the captured tenant, but recent history shows several confusing states: failed 2FA while Agent looked online, login-page symptoms labeled as data missing, and queue expiry labeled like crawl timeout. No secret leakage was observed in the reviewed snippets.

## Recommended Next Steps

### Fix direction

1. Split `stale_crawl_skipped` UI wording from real Agent/browser `CRAWL_TIMEOUT`.
2. When `PARSER_MISSING_DATA` occurs on Coupang, show a secondary hint: possible login/non-dashboard page, not generic data loss.
3. Add/read redacted Agent step logs for protected auth flow boundaries: primary login, email method selected, code sent, IMAP fetch started/succeeded/failed, submit, target reopened.
4. Keep latest failure history visible even after auth-required count returns to zero.

### Diagnostic

Collect Agent PC local logs around the next failed `AUTH_COUPANG_2FA` or `CRAWL_COUPANG` run, with OTP/password/app-password redacted.

