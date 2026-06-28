# Investigation: KakaoTalk Status Webapp

## Hand-off Brief

1. **What happened.** User reports H&J has KakaoTalk sending enabled on the agent PC, but KakaoTalk is logged out and no chat rooms are visible, while a web monitoring surface appears "정상".
2. **Where the case stands.** Current code can send agent-level KakaoTalk session status, but target-level monitoring severity only reflects Kakao problems after a `KAKAO_FAILURE` is recorded for the target.
3. **What's needed next.** Verify the deployed agent/server revision and inspect the live H&J agent `capacity_json.kakao_status`; then decide whether Kakao session failure should also drive target/customer monitoring severity before a send job fails.

## Case Info

| Field            | Value                                                                 |
| ---------------- | --------------------------------------------------------------------- |
| Ticket           | N/A                                                                   |
| Date opened      | 2026-06-28                                                            |
| Status           | Active                                                                |
| System           | Windows, project workspace `rider_result_mornitoring`                 |
| Evidence sources | User report, local logs, source code, version control                 |

## Problem Statement

User-reported description: "최근 로그 분석하세요. 에이전트 피씨의 고객은 카카오톡 전송이 활성화되어있지만 카카오톡 자체가 로그인이 안되어 채팅방 자체를 볼 수 없는 상황입니다. 하지만, 웹앱의 모니터링 상태정보는 '정상'에 머물러 있습니다. (H&J) 카카오톡 상태 정보를 웹앱으로 보내야하는데 이 부분이 없는것 같습니다."

## Evidence Inventory

| Source | Status | Notes |
| ------ | ------ | ----- |
| `src/rider_server/api/agents.py` | Available | `HeartbeatRequest` includes `kakao_status`, and `/v1/agents/heartbeat` passes it to `HeartbeatInput`. |
| `src/rider_agent/workers/kakao_sender.py` | Available | `KakaoSenderWorker.kakao_status()` includes `interactive_session_available` when the KakaoTalk login probe returns a boolean. |
| `src/rider_crawl/sender.py` | Available | `kakao_login_available()` classifies main contact window as logged in, login window as logged out, and unknown cases as omitted telemetry. |
| `src/rider_server/admin/templates/_agents.html` | Available | Agent table renders `enabled · 로그인 필요` only when an online agent reports `interactive_session_available is false`. |
| `src/rider_server/admin/severity.py` | Available | Target severity maps Kakao risk only from latest failure code `KAKAO_FAILURE`, not from agent `kakao_status`. |
| Git history | Available | Recent commits include `582e105` at 2026-06-28T21:59:35+09:00 and `1eda481` at 2026-06-28T22:52:36+09:00. |
| Local logs | Partial | Logs under `logs/` are mostly dev/local and do not include live H&J heartbeat bodies. |
| Local HTML snapshot | Partial | 2026-06-28 01:11 KST H&J target snapshot shows `AUTH_REQUIRED`, while the agent Kakao status shows `enabled` without session telemetry. |
| Running production agent PC state | Missing | The current KakaoTalk GUI login state on the remote agent PC is not directly available in this workspace. |

## Investigation Backlog

| # | Path to Explore | Priority | Status | Notes |
| - | --------------- | -------- | ------ | ----- |
| 1 | Search recent logs for H&J/Kakao status payloads and dashboard output | High | Done | Local logs lack live H&J heartbeat bodies; local HTML snapshot provides partial UI evidence. |
| 2 | Trace agent `kakao_status_provider` wiring and login detection | High | Done | Current code wires Kakao worker status into heartbeat when `KAKAO_SEND` worker starts. |
| 3 | Trace server registry persistence and dashboard severity mapping | High | Done | Server persists `interactive_session_available`, but target severity ignores it. |
| 4 | Check tests around KakaoTalk status reporting | Medium | Done | 194 targeted tests passed; coverage is agent-fragment focused, not target/customer severity from Kakao session. |
| 5 | Verify live deployed versions and live DB payload | High | Open | Needed to distinguish stale deploy from missing live telemetry. |

## Timeline of Events

| Time | Event | Source | Confidence |
| ---- | ----- | ------ | ---------- |
| 2026-06-28 | Commit `582e105` added KakaoTalk login state detection/dashboard flagging. | `git log --oneline` | Confirmed |
| 2026-06-28 | Commit `1eda481` hardened KakaoTalk session status reporting. | `git log --oneline` | Confirmed |
| 2026-06-28 01:11 KST | Local H&J HTML snapshot target row shows `data-severity="AUTH_REQUIRED"` and reason `캡차/이상 로그인`; agent Kakao cell shows `enabled` without session status. | `runtime/remote__admin_targets_tenant_864fc127_1138_40d2_b115_a24decf8a2b8.html:34`, `runtime/remote__admin_tenant_864fc127_1138_40d2_b115_a24decf8a2b8.html:707` | Confirmed |
| 2026-06-28 21:59:35 KST | Commit `582e105` introduced KakaoTalk login-state detection and Agent dashboard flagging. | `git log --format` | Confirmed |
| 2026-06-28 22:52:36 KST | Commit `1eda481` hardened async session probing and dashboard warning behavior. | `git log --format` | Confirmed |

## Confirmed Findings

### Finding 1: Heartbeat payload has a KakaoTalk status slot

**Evidence:** `src/rider_server/api/agents.py:67`

**Detail:** `HeartbeatRequest` defines `kakao_status`, validates it, and passes it into `HeartbeatInput` during heartbeat handling.

### Finding 2: Current agent code can report KakaoTalk login/session status

**Evidence:** `src/rider_agent/workers/kakao_sender.py:256`, `src/rider_agent/workers/kakao_sender.py:497`, `src/rider_crawl/sender.py:915`

**Detail:** The worker status includes `interactive_session_available` when the login probe returns `True` or `False`. Production worker startup defaults that probe to `kakao_login_available()`.

### Finding 3: Server persistence preserves KakaoTalk session status

**Evidence:** `src/rider_server/services/agent_registry.py:156`, `src/rider_server/services/agent_registry.py:176`, `src/rider_server/admin/dashboard_repository_postgres.py:539`

**Detail:** The server allowlist includes `interactive_session_available`; `heartbeat_capacity()` stores it under `capacity_json.kakao_status`; the admin repository reads it back into `AgentHealthFacts`.

### Finding 4: Web target monitoring severity does not consume agent KakaoTalk session status

**Evidence:** `src/rider_server/admin/dashboard_service.py:303`, `src/rider_server/admin/severity.py:137`, `src/rider_server/admin/templates/_targets.html:84`

**Detail:** Target rows are driven by freshness plus fail-closed signals from account state, tenant lifecycle, auth session, and latest failure code. Kakao contributes only when `latest_failure_code == KAKAO_FAILURE`; live `capacity_json.kakao_status.interactive_session_available` is not part of `TargetHealthFacts` or target severity.

### Finding 5: Agent table shows Kakao login warnings, but only in the Agent fleet surface

**Evidence:** `src/rider_server/admin/dashboard_service.py:338`, `src/rider_server/admin/templates/_agents.html:45`

**Detail:** `DashboardService.agent_row()` maps `interactive_session_available`, and `_agents.html` renders `enabled · 로그인 필요` for online agents when that value is false.

### Finding 6: Local logs are not sufficient to prove the live H&J heartbeat payload

**Evidence:** `logs/dev-admin-ui.out.log`, `logs/kakao_diagnostics.log`

**Detail:** The available logs show heartbeat access lines and older Kakao focus failures, but not live H&J request bodies or current production agent state. The local 2026-06-28 HTML snapshot shows H&J in `AUTH_REQUIRED`, not `NORMAL`.

## Deduced Conclusions

### Deduction 1: "No KakaoTalk status path exists" is refuted for current code

**Based on:** Findings 1, 2, and 3.

**Reasoning:** The current agent worker can produce `interactive_session_available`; heartbeat includes `kakao_status`; server storage allowlists and persists the field.

**Conclusion:** The status path exists in current code, but it is agent-level and recent.

### Deduction 2: A customer/target card can remain normal while the agent KakaoTalk session is bad

**Based on:** Findings 4 and 5.

**Reasoning:** Target severity does not look at agent `kakao_status`. It only changes for Kakao after a target-linked failure such as `KAKAO_FAILURE` appears in jobs/delivery logs.

**Conclusion:** If H&J has recent successful crawl data and no fresh `KAKAO_FAILURE`, the target monitoring status can remain `NORMAL` even when the agent KakaoTalk app is logged out.

### Deduction 3: A stale deployment before 2026-06-28 21:59 KST would also explain the report

**Based on:** Git history and local HTML snapshot.

**Reasoning:** KakaoTalk login state detection and Agent dashboard warnings were added late on 2026-06-28. A server/agent deployed before those commits would not show the new signal.

**Conclusion:** Live version check is required before treating current-source behavior as production behavior.

## Hypothesized Paths

### Hypothesis 1: The agent does not send a failing KakaoTalk status when no send job is active

**Status:** Refuted for current code; Open for live deployment

**Theory:** If KakaoTalk status is only updated by the sender worker during send attempts, a logged-out KakaoTalk app can remain invisible to heartbeat while no active send job touches the chat room.

**Supporting indicators:** User reports KakaoTalk is logged out, but the dashboard remains "정상"; this is consistent with missing source telemetry.

**Would confirm:** Agent heartbeat payload has empty/healthy `kakao_status` despite a logged-out KakaoTalk GUI.

**Would refute:** Logs show heartbeat sends `kakao_status.status != ok` for H&J and the server/dashboard still renders "정상".

**Resolution:** Current code probes from heartbeat status independent of send attempts, but live deployment/payload evidence is missing.

### Hypothesis 2: The server receives KakaoTalk failure status but dashboard severity ignores it

**Status:** Confirmed for target/customer monitoring severity; Refuted for Agent table display

**Theory:** The server may persist `kakao_status` in capacity JSON, but the web monitoring status may only check agent online, target enabled, crawl/job state, or browser profile status.

**Supporting indicators:** User sees web status "정상", not necessarily raw heartbeat data.

**Would confirm:** Stored `capacity_json.kakao_status` contains a failure while admin severity logic still returns normal.

**Would refute:** Dashboard severity logic includes KakaoTalk failure and would render non-normal when present.

**Resolution:** Target severity ignores `kakao_status`; Agent table renders it.

### Hypothesis 3: The live H&J UI was running an older revision

**Status:** Open

**Theory:** The user's observation happened before commits `582e105`/`1eda481` were deployed to server and agent PC.

**Supporting indicators:** Local HTML at 2026-06-28 01:11 lacks session telemetry in the Agent Kakao cell; session detection commits landed at 21:59 and 22:52 KST.

**Would confirm:** Live server/agent version is older than `582e105` or agent heartbeat payload lacks `interactive_session_available`.

**Would refute:** Live server and agent are at/after `1eda481`, and the DB has `interactive_session_available=false`.

**Resolution:** Open.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | ------ | ------------- |
| Exact H&J heartbeat payload from production | Distinguishes absent source telemetry from ignored downstream telemetry | Inspect agent/server logs or DB row for H&J agent capacity JSON |
| Live KakaoTalk GUI state on agent PC | Confirms the operational condition at the time of heartbeat | Remote headed verification or agent-side diagnostic screenshot/log |
| Live deployed server and agent commit/version | Distinguishes stale deploy from current-code logic gap | Check deployed image/revision and agent executable/source version |

## Source Code Trace

| Element | Detail |
| ------- | ------ |
| Error origin | Target/customer severity composition does not include agent `kakao_status`; agent warning is separate. |
| Trigger | Agent heartbeat and dashboard monitoring render |
| Condition | KakaoTalk enabled but desktop app not logged in |
| Related files | `src/rider_agent/heartbeat.py`, `src/rider_agent/workers/kakao_sender.py`, `src/rider_crawl/sender.py`, `src/rider_server/api/agents.py`, `src/rider_server/services/agent_registry.py`, `src/rider_server/admin/dashboard_service.py`, `src/rider_server/admin/severity.py`, `src/rider_server/admin/templates/_agents.html`, `src/rider_server/admin/templates/_targets.html` |

## Conclusion

**Confidence:** Medium

Current code already has an agent-level KakaoTalk status path: worker probe → heartbeat `kakao_status` → server `capacity_json` → Agent table warning. The target/customer monitoring status can still remain "정상" because it does not consume that agent-level Kakao session signal; it only reflects Kakao after target-linked `KAKAO_FAILURE` is recorded. A stale deploy before the 2026-06-28 evening commits is also plausible and requires live version/payload evidence.

## Recommended Next Steps

### Fix direction

If product intent is "KakaoTalk logged out should make affected customers non-normal before the next send failure", add a target/customer severity signal derived from active Kakao channels plus online agent `kakao_status.interactive_session_available=false`. Keep the raw status secret-free and avoid exposing room names in heartbeat.

### Diagnostic

1. Check live H&J agent `capacity_json.kakao_status` for `enabled=true` and `interactive_session_available=false`.
2. Confirm the live server and agent include commits at/after `1eda481`.
3. If payload is missing, inspect KakaoTalk window titles/classes on the agent PC and extend `kakao_login_available()` only with observed evidence.
4. If payload is present, add a target/dashboard regression test that a Kakao-enabled customer is not shown as `NORMAL` when its assigned agent reports no interactive Kakao session.

## Reproduction Plan

For current code: create an agent with `capacity_json.kakao_status={"enabled": true, "interactive_session_available": false}` and a target with fresh successful crawl and active Kakao channel. Current expected behavior: Agent table warns, target row remains `NORMAL` unless a fresh `KAKAO_FAILURE` exists. Desired behavior, if confirmed by product: target row becomes an action-required Kakao state.

## Side Findings

- The current branch already contains KakaoTalk status-related commits from 2026-06-28; the issue may be a gap in target-level aggregation or a stale deployed version rather than total absence.
- Targeted verification passed locally: `.\.venv\Scripts\python.exe -m pytest tests\agent\test_kakao_sender.py tests\server\test_admin_dashboard.py tests\test_sender.py -q` → 194 passed.
