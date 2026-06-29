# Investigation: Coupang Haeundaeplus Suyeongjungang Auth

## Hand-off Brief

1. **What happened.** 사용자는 쿠팡 고객 "해운대플러스 수영중앙"만 대시보드에서 "메일 인증 필요" 상태로 남는다고 보고했다.
2. **Where the case stands.** 코드상 "메일 인증 필요"는 `AUTH_COUPANG_2FA`가 IMAP 로그인/메일 설정 실패를 감지했을 때 표면화되는 세부 상태로 확인됐다.
3. **What's needed next.** 운영 DB/Agent 로그에서 해당 고객의 최신 job `result_json.auth_recovery_state`, `reason`, 등록 메일 ref 존재 여부를 확인하면 고객별 원인을 확정할 수 있다.

## Case Info

| Field            | Value |
| ---------------- | ----- |
| Ticket           | N/A |
| Date opened      | 2026-06-29 |
| Status           | Active |
| System           | Windows, project `rider_result_mornitoring` |
| Evidence sources | User screenshot text, local source, runtime/log files, possible local DB/API response files |

## Problem Statement

고객 "해운대플러스 수영중앙"은 쿠팡 플랫폼에서 수집/전송 기록이 없고 현재 인증 단계가 "메일 인증 필요"로 표시된다. 다른 등록 고객들은 정상 인증되었다.

## Evidence Inventory

| Source | Status | Notes |
| ------ | ------ | ----- |
| User report | Available | "메일 인증 필요", 플랫폼 쿠팡, 센터/상점 "해운대플러스 수영중앙", 수집/전송 기록 없음 |
| Source code | Available | `EMAIL_AUTH_REQUIRED` state and AUTH_COUPANG_2FA flow traced |
| Runtime/log files | Partial | Local `logs/` are old; local `runtime/remote__admin_*` snapshot is a different customer |
| Local Admin/DB | Partial | Local Admin on 8001 has only `local-auth-target`; local DB password from `.env` did not match running DB |
| Browser/production Admin | Missing | Chrome extension bootstrap failed; CDP 9222 was closed |

## Investigation Backlog

| # | Path to Explore | Priority | Status | Notes |
| - | --------------- | -------- | ------ | ----- |
| 1 | Find target/account rows for "해운대플러스 수영중앙" | High | Blocked | Needs production DB/Admin access; local data does not contain this customer |
| 2 | Find AUTH_COUPANG_2FA job result for the account | High | Blocked | Needs production DB/Agent logs |
| 3 | Compare with normal Coupang customer auth settings | Medium | Open | Identify customer-specific difference |
| 4 | Trace source code mapping from job result to dashboard label | Medium | Done | Dashboard label maps from `auth_recovery_state`/`reason` |

## Timeline of Events

| Time | Event | Source | Confidence |
| ---- | ----- | ------ | ---------- |
| 2026-06-29 | User reported customer stuck at "메일 인증 필요" | User message | Confirmed |
| 2026-06-29 | Local Admin 8001 checked; only `local-auth-target` auth-required row exists | `http://127.0.0.1:8001/admin/auth-required?tenant=all` | Confirmed |

## Confirmed Findings

### Finding 1: "메일 인증 필요" maps to `EMAIL_AUTH_REQUIRED`

**Evidence:** `src/rider_server/admin/severity.py:249`

**Detail:** Dashboard detail labels map `EMAIL_AUTH_REQUIRED` to "메일 인증 필요" and reason `email_auth_required` to the same label.

### Finding 2: `EMAIL_AUTH_REQUIRED` is produced by the Agent 2FA classification boundary

**Evidence:** `src/rider_agent/auth/coupang_gmail_2fa.py:143`

**Detail:** `classify_coupang_2fa_outcome()` returns `STATE_EMAIL_AUTH_REQUIRED` only when `is_email_auth_required` is true.

### Finding 3: The default email-auth predicate is specifically IMAP/mail-setting oriented

**Evidence:** `src/rider_agent/auth/coupang_gmail_2fa.py:173`

**Detail:** `default_is_email_auth_required()` treats `ImapAuthError` or `Coupang2faError(email_auth_required=True)` as `EMAIL_AUTH_REQUIRED`.

### Finding 4: IMAP login failure is classified as an operator-action mail auth error

**Evidence:** `src/rider_crawl/auth/imap_2fa.py:226`

**Detail:** `_imap_connect()` removes whitespace from the app password, then raises `ImapAuthError` when IMAP login fails.

### Finding 5: Coupang 2FA email-code fetch wraps IMAP auth failures as `Coupang2faError(email_auth_required=True)`

**Evidence:** `src/rider_crawl/auth/coupang_email_2fa.py:167`

**Detail:** `_fetch_code()` catches `Imap2faError`; if it is an `ImapAuthError`, it raises `Coupang2faError(..., email_auth_required=True)`.

## Deduced Conclusions

### Deduction 1: The current customer is most likely blocked at the registered verification mailbox, not at center validation or sending

**Based on:** Finding 1-5 and the user-reported flow "대상 검증 차단 신호 없음" -> "메일 인증 필요".

**Reasoning:** The dashboard label is not a generic crawl failure label. It is wired to the `AUTH_COUPANG_2FA` detail state that is emitted when the email-auth predicate sees an IMAP/mail-setting failure. The user's status flow also says target validation has no blocking signal and no collection ever succeeded.

**Conclusion:** The highest-probability cause is a customer-specific verification email setup problem: wrong verification mailbox, wrong/expired app password, IMAP disabled, unsupported email domain, or the Coupang account's real 2FA destination not matching the mailbox registered in this system.

## Hypothesized Paths

### Hypothesis 1: Customer-specific 2FA setup or mailbox mismatch

**Status:** Open

**Theory:** 해당 쿠팡 계정의 등록 메일함/앱 비밀번호/쿠팡 로그인 계정 조합이 실제 쿠팡 인증 메일 수신 계정과 맞지 않아 자동복구가 완료되지 못했을 수 있다.

**Supporting indicators:** 다른 고객은 정상 인증되며, 이 고객만 `EMAIL_AUTH_REQUIRED`로 표시된다.

**Would confirm:** 해당 account/job 기록에서 `AUTH_COUPANG_2FA`가 실행됐지만 결과가 `EMAIL_AUTH_REQUIRED`/`email_auth_required`이고, 설정 ref는 존재하나 실제 OTP 완료 증거가 없다.

**Would refute:** job 미실행, secret 누락, agent capability 부재, timeout 등 다른 terminal reason이 확인된다.

**Resolution:** Open.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | ------ | ------------- |
| 실제 target/account/job 행 | 원인을 추정에서 확정으로 올린다 | 운영 DB에서 target/customer/job 조회 |
| Agent 실행 로그 | 이메일 인증 화면에서 어느 단계까지 갔는지 확인한다 | Agent PC 로그 또는 server job result |
| 등록 메일 설정 검증 | 메일함/앱 비밀번호/IMAP 문제를 확정한다 | secret 값 노출 없이 IMAP 로그인 성공 여부만 확인 |

## Source Code Trace

| Element | Detail |
| ------- | ------ |
| Error origin | `src/rider_crawl/auth/imap_2fa.py:226` / `src/rider_crawl/auth/coupang_email_2fa.py:167` |
| Trigger | `AUTH_COUPANG_2FA` job calls email 2FA recovery |
| Condition | IMAP login/email setup failure becomes `EMAIL_AUTH_REQUIRED` |
| Related files | `src/rider_agent/auth/coupang_gmail_2fa.py`, `src/rider_server/admin/severity.py`, `src/rider_server/queue/postgres_queue.py` |

## Conclusion

**Confidence:** Medium

코드상 "메일 인증 필요"의 의미는 확인됐다. 이 상태는 쿠팡 대상 검증 실패나 일반 수집 실패가 아니라, 자동 2FA가 등록된 인증 메일함에 IMAP으로 접근하지 못했거나 메일 설정이 맞지 않을 때 표면화되는 상태다. 다만 로컬 환경에는 해당 고객 최신 운영 행이 없어, 고객별 원인은 운영 DB/Agent 로그 확인 전까지는 "가장 유력한 원인"으로 둔다.

## Recommended Next Steps

### Fix direction

Open.

### Diagnostic

운영 DB/API에서 고객명, target_id, account_id, latest `AUTH_COUPANG_2FA` job의 `result_json.auth_recovery_state`, `result_json.reason`을 확인한다. secret 값은 보지 말고 ref 존재 여부와 IMAP 로그인 성공 여부만 확인한다.

## Reproduction Plan

Open.

## Side Findings
