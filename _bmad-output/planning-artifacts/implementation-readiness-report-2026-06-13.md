---
stepsCompleted: ['step-01-document-discovery', 'step-02-prd-analysis', 'step-03-epic-coverage-validation', 'step-04-ux-alignment', 'step-05-epic-quality-review', 'step-06-final-assessment']
status: 'complete'
overallReadiness: 'READY (2 Major items resolved post-assessment via epics.md edits; only 3 Minor + 1 business launch gate remain)'
assessor: 'Implementation Readiness workflow (PM role) — facilitated for Noah Lee'
completedAt: '2026-06-13'
documentsIncluded:
  prd: '_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/prd.md'
  architecture: '_bmad-output/planning-artifacts/architecture.md'
  epics: '_bmad-output/planning-artifacts/epics.md'
  ux: null
supportingDocuments:
  - 'prds/.../addendum.md'
  - 'prds/.../reconcile-research.md'
  - 'prds/.../reconcile-detailed-work-order.md'
  - 'prds/.../review-operational-risk.md'
  - 'prds/.../review-operational-risk-delta.md'
  - 'prds/.../review-rubric.md'
  - 'prds/.../review-rubric-delta.md'
  - 'prds/.../.decision-log.md'
---

# Implementation Readiness Assessment Report

**Date:** 2026-06-13
**Project:** rider_result_mornitoring

---

## Step 1: Document Discovery — Inventory

### PRD Documents

**Whole Documents:**
- `prds/prd-rider_result_mornitoring-2026-06-12/prd.md` (50,296 bytes, modified 2026-06-12 23:28)

**Sharded Documents:** None found.

**Supporting PRD-folder documents (not the primary PRD, but related planning artifacts):**
- `addendum.md` (8,240 bytes, 2026-06-12 23:23)
- `reconcile-research.md` (12,299 bytes, 2026-06-12 23:15)
- `reconcile-detailed-work-order.md` (14,076 bytes, 2026-06-12 23:15)
- `review-operational-risk.md` (20,084 bytes, 2026-06-12 23:21)
- `review-operational-risk-delta.md` (6,284 bytes, 2026-06-12 23:25)
- `review-rubric.md` (9,194 bytes, 2026-06-12 23:21)
- `review-rubric-delta.md` (2,930 bytes, 2026-06-12 23:25)
- `.decision-log.md` (3,296 bytes, 2026-06-12 23:28)

### Architecture Documents

**Whole Documents:**
- `architecture.md` (36,441 bytes, modified 2026-06-12 23:56)

**Sharded Documents:** None found.

### Epics & Stories Documents

**Whole Documents:**
- `epics.md` (89,683 bytes, modified 2026-06-13 00:17)

**Sharded Documents:** None found.

### UX Design Documents

⚠️ **WARNING: No UX document found** (`*ux*.md`). To be confirmed with user whether UX is in scope for this project.

---

### Critical Issues

- **Duplicates:** None. No document exists in both whole and sharded form.
- **Missing:** UX document not found. **Resolved by user (C):** UX is treated as out-of-scope for this refactoring project (tkinter desktop app; Admin UI requirements live inside the PRD §5.6/§5.7, not a separate UX spec).

---

## Step 2: PRD Analysis

**Source:** `prds/prd-rider_result_mornitoring-2026-06-12/prd.md` (read in full, 675 lines). This is a **refactoring PRD** — the goal is to turn an already-working Python/tkinter desktop automation app into a sellable multi-customer operating structure (central server + Windows Local Agents) while preserving existing collect/parse/render/send/Gmail-2FA behavior.

### Functional Requirements

The PRD numbers FRs explicitly (FR-1 … FR-34), grouped into 9 capability areas. Each FR carries testable "Consequences."

**§5.1 Baseline & regression prevention** (Realizes UJ-1)
- **FR-1: 기존 동작 기준선 저장** — Capture baseline of active Baemin/Coupang targets, Telegram/Kakao test sends, config files, state folders, and pytest results before refactoring. Originals (`ui_settings.json`, `crawlingN`) must not be deleted.
- **FR-2: 기존 자산 재사용 보장** — Existing Baemin parser/crawler, Coupang parser, message renderer, Telegram/Kakao senders, Coupang Gmail 2FA, and tests must be reused; unintended render-output changes count as failures.
- **FR-3: 신규 경로 dry-run 비교** — Run new collect/render/dispatch path as dry-run (no real send), diff old vs new messages, require approval before activating a DeliveryRule.

**§5.2 ID-based operating model** (Realizes UJ-1, UJ-4)
- **FR-4: 고객/대상/채널 ID 관리** — CRUD + deactivate (soft delete) of Customer, Subscription, Platform Account, Monitoring Target, Message Channel, DeliveryRule by stable ID.
- **FR-5: legacy alias 유지** — Migrated targets preserve old `크롤링N`/`crawlingN` identifier as a `legacy alias` (display/secondary only, never primary key).
- **FR-6: 구독 상태에 따른 작업 제어** — Subscription status gates job execution; non-ACTIVE customers don't get new Crawl Jobs; SUSPENDED customers' undispatched jobs go HELD; already-succeeded jobs aren't re-sent.

**§5.3 Collect-render-dispatch separation** (Realizes UJ-1, UJ-3)
- **FR-7: Crawl Job과 Snapshot 생성** — Run per-target Crawl Jobs, store result as Snapshot; crawl failure doesn't cascade to message/dispatch; missing required perf data → recorded failure, not default message.
- **FR-8: Message 렌더링 분리** — Render Message from Snapshot as a step separated from collection; same Snapshot can be re-rendered without re-collecting.
- **FR-9: Dispatch Job fan-out** — One Message fans out to multiple Dispatch Jobs per connected DeliveryRules; per-channel success/failure/retry/hold tracked separately.
- **FR-10: 중복 발송 방지** — Idempotency over (customer, target, snapshot, channel, topic/room); retries don't re-send a succeeded idempotency key; key must not wrongly block other customers/targets/channels.
- **FR-11: 재시도와 실패 상태 관리** — Record collect/render/dispatch failures as states; distinguish retryable vs human-intervention failures; AUTH_REQUIRED not infinitely retried; transient errors use bounded retry + backoff.

**§5.4 Local Agent & work nodes** (Realizes UJ-2, UJ-3, UJ-4)
- **FR-12: Agent 등록과 heartbeat** — Local Agent registers; reports status, version, handleable job types, last heartbeat, current job; offline/degraded shown when stale.
- **FR-13: Agent job polling/claim/complete** — Agent polls, claims (no double-success), completes/fails/holds; dead agent → job timeout reassign/fail; result records agent, start/end times, failure reason.
- **FR-14: Browser Profile 격리** — Isolate Browser Profile + CDP per platform account/target; detect CDP port/profile duplication → don't start; expected center/store-name validation failure → don't render/send.
- **FR-15: KakaoTalk 직렬 전송** — Kakao sends via Agent serial queue; no parallel room input; ambiguous room / window / focus / confirm failures → recorded failure, no arbitrary send; queue lag surfaced.
- **FR-16: outbound-only Agent 통신** — Agent does outbound HTTPS polling/reporting only; no server→PC inbound; no firewall inbound port; missing/expired agent token → no jobs.

**§5.5 Platform auth & account safety** (Realizes UJ-2)
- **FR-17: 배민 인증 필요 감지** — Detect Baemin phone-auth / login-expiry during collection, transition to auth-required; no message generated/sent while auth-required.
- **FR-18: 사람 개입형 배민 재인증** — Operator/agent opens the browser profile and completes Baemin auth manually; system never bypasses phone auth; target not shown normal until auth confirmed.
- **FR-19: 쿠팡 Gmail 2FA 분리** — Keep Coupang Gmail 2FA but separate per customer/mailbox/token; lock concurrent same-mailbox reads; OTP/OAuth token/Coupang password never logged.
- **FR-20: 플랫폼 대상 검증** — Validate the collected screen matches expected customer/center/store/target; empty/default expected name → risk state; mismatch → send halted.

**§5.6 Central server & Admin UI** (Realizes UJ-1, UJ-2, UJ-4)
- **FR-21: 운영 대시보드** — Operator sees customers, targets, last collect success, last dispatch success, auth status, agent status, queue status, error status in one/linked views.
- **FR-22: 수동 운영 액션** — Operator can activate/deactivate target, assign agent, test crawl, dry-run render, test send, job retry, check auth-required; test send only to designated test channel; retry doesn't bypass dedup; risky actions audit-logged (actor + time).
- **FR-23: 상태 심각도 표시** — Severity display (normal/warning/critical/stopped); >2× schedule period → warning candidate; >4× → critical candidate; auth-required / target-mismatch / Kakao misfire-risk → prefer stop over auto-send.

**§5.7 Message channels & dispatch policy** (Realizes UJ-3)
- **FR-24: Telegram 중앙 전송** — Central Telegram channel registration, topic ID mgmt, test message, sendMessage, result logging; never poll same bot token from multiple processes; (chat ID, topic ID) is part of dispatch scope.
- **FR-25: KakaoTalk 제한 운영** — Treat Kakao as limited/best-effort (not unlimited / strong-SLA); surface volume + queue lag; failures don't auto-reroute to another room; official Kakao channel/API stays a follow-up item.
- **FR-26: 채널별 전송 이력** — Track per-channel dispatch history per DeliveryRule/Dispatch Job; retry only the failed channel; history feeds dedup decisions.

**§5.8 Migration & deployment** 
- **FR-27: 단계별 전환** — Phased: P0 baseline → P1 ID model → P2 collect/dispatch separation → P3 Local Agent → P4 central server. (ASSUMPTION: MVP = P0–P4; P5/P6 are follow-up.) Post-P2 the existing UI single-run result must equal the old result.
- **FR-28: 현재 PC를 Agent #1로 사용** — Use the current ordinary Windows PC as the first Local Agent; defer high-perf server purchase to metric-based scaling.

**§5.9 Onboarding, scheduling, operational-safety reinforcement**
- **FR-29: 채널 등록/검증/활성화** — Activate Telegram/Kakao channels via registration code + test message + customer/operator confirmation; unverified DeliveryRule not used for real dispatch.
- **FR-30: 운영자 주도 고객/구독 상태 흐름** — Without full billing automation, distinguish setup / auth-wait / channel-verify-wait / test / ACTIVE / DEGRADED / AUTH_REQUIRED / SUSPENDED; SUSPENDED preserves config/secret/profile refs; SUSPENDED→ACTIVE forces operator decision on HELD jobs (discard or resume).
- **FR-31: 마이그레이션 안전 제약** — Prevent data corruption, duplicate send, auto-activation of inactive targets; atomic settings write; legacy `last_message` carried into new DeliveryLog/idempotency seed; inactive tabs preserved but not auto-activated; logs have rotation/retention.
- **FR-32: Local Agent 실제 실행 조건** — Agent runs real collection + Kakao send under real Windows conditions; Kakao needs interactive user session (not Session 0 service-only); autostart + heartbeat recovery after reboot/login; crawler-only vs Kakao-sender agents distinguished by job type.
- **FR-33: Scheduler와 queue 안전장치** — Prevent job storms / bad agent assignment / platform-wide fault spread; schedule jitter; circuit breaker on platform-wide fault / parser failure spike; capacity + target/profile affinity in assignment; per-error-code backoff (no 5s-infinite-retry).
- **FR-34: Admin 보안과 복구성** — Admin MFA + VPN/IP allowlist; minimum roles viewer/operator/secret-admin/break-glass; agent + external service token revoke/rotate; DB + diagnostics backup/retention/restore-rehearsal; minimum alerts agent_offline, queue_lag, api_error_rate, auth_required.

**Total FRs: 34** (FR-1 … FR-34)

### Non-Functional Requirements

The PRD groups cross-cutting NFRs in §6 (not numbered as NFR-n; numbered here for traceability).

**§6.1 Reliability & safety**
- NFR-R1: Prefer failing a job over sending to wrong customer/target/room.
- NFR-R2: Missing required perf data → no message produced.
- NFR-R3: Retries use idempotency key + DeliveryLog to block duplicates.
- NFR-R4: Auth-required → human-intervention state, not infinite retry.

**§6.2 Security & privacy**
- NFR-S1: Redact Telegram token, Gmail OAuth token, Coupang password, OTP, chat ID, topic ID, customer PII from logs/exceptions.
- NFR-S2: Agent↔server over authenticated HTTPS.
- NFR-S3: Leaked/expired agent token → agent can't receive jobs.
- NFR-S4: Gmail token storage location deferred to a security decision (ASSUMPTION: agent-local first).
- NFR-S5: Admin access has minimum protections; token revoke/rotation + backup/restore detailed in architecture.
- NFR-S6: Architecture must produce a data inventory (secret, credential, customer IDs, message body, op logs, browser profiles, backup, diagnostics).
- NFR-S7: Secret storage classified as central DB / managed secret store / Windows DPAPI-Credential Manager / env var / non-stored.
- NFR-S8: DB, backup, Windows profile/secret stores → encryption at rest where applicable.
- NFR-S9: Diagnostic artifacts (screenshots, HTML dumps, exception traces, queue payloads, failed message bodies) → retention + scrubbing policy.

**§6.3 Operational observability**
- NFR-O1: Operator can view status per customer/target/agent/channel/job.
- NFR-O2: Warning/critical thresholds computable from schedule period + last success.
- NFR-O3: Kakao queue lag, agent heartbeat, job failure rate, auth-required exposed in UI.
- NFR-O4: Failure causes recorded as actionable categories (collect-fail / auth-required / render-fail / telegram-fail / kakao-fail / dedup-blocked / target-validation-fail).
- NFR-O5: Auth failure reasons classified (token expiry / wrong password / mail delay / CAPTCHA / mailbox conflict / stale-mail misread).
- NFR-O6: MVP runbook covers agent_offline, queue_lag, api_error_rate, auth_required, profile_mismatch, kakao_ambiguous_room, duplicate_blocked.

**§6.4 Compatibility & migration**
- NFR-C1: Preserve originals of `runtime/`, `logs/`, `ui_settings.json`, `crawlingN` state during migration.
- NFR-C2: Don't unintentionally mix CLI/env config policy with UI-stored config policy.
- NFR-C3: Existing tests + manual regression scenarios runnable at each refactor stage.
- NFR-C4: Existing dedup state carried into new idempotency/DeliveryLog decisions.
- NFR-C5: Migration represents states discovered/mapped/dry-run-passed/approved/active/paused/rolled-back.
- NFR-C6: Operator can use global dispatch kill switch + tenant/channel-level pause.
- NFR-C7: Old path & new path must not both really-send (cutover rule).
- NFR-C8: Rollback deactivates new DeliveryRule, restores old runtime path, keeps new logs as dedup record.

**§6.5 Performance & scale**
- NFR-P1: MVP must pass a ≥100 fake-target scheduling smoke (job scheduling, queue, state tracking work).
- NFR-P2: Real capacity decided by Chrome memory, avg collect time, avg Kakao send time, login-expiry frequency, agent stability.
- NFR-P3: Kakao scaling/limit decided via queue-lag threshold.
- NFR-P4: Scale judged by monitoring-target count, not customer count.
- NFR-P5: Negative safety tests include wrong tenant, wrong profile, wrong Kakao room, stale agent token, restored DB, double agent claim, crash-after-send.

**Total NFR clauses: ~32** (across §6.1–§6.5).

### Additional Requirements / Constraints

- **Explicit Non-Goals (§7):** no Baemin phone-auth bypass/full auto-login; no official Baemin/Coupang API product; no needless rewrite of working parser/renderer/Gmail-2FA/senders; no deleting old config/state during migration; no Kakao unlimited mass-send as a default product; no Kubernetes / microservices / high-perf server purchase as MVP prerequisite; no full customer self-onboarding / billing automation in MVP.
- **MVP scope (§8):** In-scope and out-of-scope lists are explicit and align with FR groups + Non-Goals.
- **Success Metrics (§9):** SM-1…SM-7 (primary SM-1/2/3, secondary SM-4/5/6/7) each cite the FRs they validate; counter-metrics SM-C1/C2/C3 guard against gaming. **This gives an explicit FR→metric traceability seed for Step 3.**
- **Readiness Gates (§13):** Architecture Readiness (7 ADR topics), Implementation Readiness (baseline/canary plan, negative-safety test plan, Agent #1 hardening checklist, audit-log fields), Commercial Launch Readiness.
- **Open Questions (§12):** 3 architecture blockers, 3 launch blockers, 4 post-MVP decisions — these are deliberately unresolved and must be checked against architecture.md in Step 4.
- **Assumptions Index (§14):** 11 explicit `[ASSUMPTION]` entries, all sourced to a section.
- **Companion docs:** Tech-stack / DB / API / agent-loop / server-spec detail is deliberately pushed to `addendum.md`; PRD body stays requirements-level.

### PRD Completeness Assessment (initial)

**Strengths:**
- FRs are fully numbered (FR-1…FR-34), each with testable Consequences — excellent for traceability.
- FR→UJ mapping ("Realizes UJ-n") and FR→metric mapping (SM-n "Validates FR-…") already exist in the PRD itself, which makes coverage validation in Steps 3–5 far more reliable.
- Non-goals, assumptions, and open questions are explicit, reducing ambiguity.
- Safety/anti-misfire requirements (the highest operational risk per project-context.md) are pervasive and concrete.

**Watch-items to verify in later steps (not failures yet):**
1. NFRs are not individually numbered in the PRD (only FRs are). Epics/stories must still cover §6 security/observability/migration clauses — risk that NFRs get under-represented in epics. **→ Verify in Step 3 epic coverage.**
2. Several FRs depend on **architecture decisions still listed as Open Questions** (§12.1 Gmail token location, job-claim protocol, idempotency model, tenant isolation, Admin access). **→ Verify in Step 4 that architecture.md actually resolves these, since PRD §13.1 makes them gating ADRs.**
3. The PRD is large and operationally dense (34 FRs + ~32 NFR clauses + phased P0–P4). Epic decomposition must respect the **phase ordering (FR-27)** and the **behavior-preservation baseline (FR-1/2/3)** as prerequisites. **→ Verify epic sequencing in Step 3/5.**

---

## Epic Coverage Validation

**Source:** `epics.md` (read in full, 1133 lines). The document declares 3 input contracts (`data-api-contract.md`, `implementation-contract.md`, `operations-security-test-contract.md` under `_bmad-output/specs/spec-riderbot-refactoring/`) in addition to PRD + architecture. It contains an explicit **FR Coverage Map** (a self-declared traceability table), 5 MVP epics (Epic 1–5, 35 stories total), and 2 post-MVP epics (Epic 6–7) explicitly excluded from story generation.

The epics doc also re-inventories the PRD's NFRs into a **numbered NFR-1…NFR-29** scheme and adds **ADD-1…ADD-16** (architecture-derived technical requirements). This resolves PRD watch-item #1 (un-numbered NFRs) — the epics author numbered them.

### Coverage Matrix (PRD FR → Epic/Story, verified against actual Acceptance Criteria)

I did not trust the self-declared coverage map alone — I traced each FR to concrete Given/When/Then ACs in the stories.

| FR | PRD requirement (short) | Story w/ concrete AC | Status |
| --- | --- | --- | --- |
| FR-1 | Baseline capture of existing targets/config/tests | Story 1.1 (tag+backup), 1.2 (pytest), 1.4 (manual regression) | ✓ Covered |
| FR-2 | Reuse existing parser/renderer/sender/2FA/tests | Story 1.5 (reuse boundary doc), 1.2, 3.1–3.3 (wrapping) | ✓ Covered |
| FR-3 | New-path dry-run diff before activation | Story 1.4 (procedure), 3.8 (real dry-run+cutover), 5.7 (Admin dry-run) | ✓ Covered |
| FR-4 | ID-based CRUD of customer/target/channel | Story 2.5 (domain models), 2.1 (IDs); Admin CRUD UI 5.x | ✓ Covered |
| FR-5 | legacy alias preservation | Story 2.1, 2.3 (field-name legacy mapping) | ✓ Covered |
| FR-6 | Subscription-gated job execution | Story 2.6 (SubscriptionGate), 5.4 (scheduler gate) | ✓ Covered |
| FR-7 | Crawl Job → Snapshot; fail on missing data | Story 3.1 (split), 3.2 (Snapshot + fail-closed) | ✓ Covered |
| FR-8 | Render separated from collection | Story 3.3 (MessageRenderService, stable hash) | ✓ Covered |
| FR-9 | Dispatch fan-out 1→N channels | Story 3.4 (DeliveryRule fan-out) | ✓ Covered |
| FR-10 | Duplicate-send prevention | Story 3.5 (DeliveryLog+idempotency), 5.2 (uq constraint) | ✓ Covered |
| FR-11 | Retry & failure-state management | Story 3.6 (retryable vs human), 5.4 (backoff) | ✓ Covered |
| FR-12 | Agent register + heartbeat | Story 4.2 (register), 4.3 (heartbeat) | ✓ Covered |
| FR-13 | Agent poll/claim/complete, no double-claim | Story 4.4 (claim+lease), 5.3 (server-side SKIP LOCKED) | ✓ Covered |
| FR-14 | Browser Profile/CDP isolation + target validation | Story 4.5 (BrowserProfileManager) | ✓ Covered |
| FR-15 | KakaoTalk serial send + room validation | Story 4.6 (KakaoSenderWorker FIFO) | ✓ Covered |
| FR-16 | outbound-only Agent comms | Story 4.4 (outbound HTTPS), 4.2 (token) | ✓ Covered |
| FR-17 | Baemin auth-required detection | Story 4.8 (AUTH_REQUIRED transition) | ✓ Covered |
| FR-18 | Human-in-the-loop Baemin re-auth | Story 4.8 (no-bypass, AUTH_VERIFIED) | ✓ Covered |
| FR-19 | Coupang Gmail 2FA per-mailbox separation + lock | Story 4.9 (mailbox lock, token separation) | ✓ Covered |
| FR-20 | Platform target validation | Story 2.3 (field state), 4.5 (CENTER_MISMATCH block) | ✓ Covered |
| FR-21 | Operations dashboard | Story 5.6 (Admin dashboard) | ✓ Covered |
| FR-22 | Manual operations actions | Story 5.7 (test crawl/send/retry/auth) | ✓ Covered |
| FR-23 | Status severity display (2×/4×) | Story 5.6 (severity calc) | ✓ Covered |
| FR-24 | Telegram central send | Story 3.7 (central webhook), 5.5 (register) | ✓ Covered |
| FR-25 | KakaoTalk limited/best-effort policy | Story 4.6 (queue lag, no auto-reroute) | ✓ Covered |
| FR-26 | Per-channel dispatch history | Story 3.6 (per-channel state), 3.5 | ✓ Covered |
| FR-27 | Phased P0→P4 transition | Epic 1–5 structure = P0–P4; cutover 3.8 | ✓ Covered (structural) |
| FR-28 | Current PC as Agent #1 | Story 4.1, 4.7 (Agent #1 reuse) | ✓ Covered |
| FR-29 | Channel registration/verify/activate | Story 5.5 (Telegram register+verify) | ✓ Covered |
| FR-30 | Operator-driven customer/subscription states | Story 2.5/2.6 (state model), 5.7 (Admin transitions) | ✓ Covered |
| FR-31 | Migration safety constraints | Story 2.2 (atomic write+rotation), 2.7 (migration exec) | ✓ Covered |
| FR-32 | Agent real-runtime conditions (interactive session, autostart) | Story 4.7 | ✓ Covered |
| FR-33 | Scheduler/queue safety (jitter, circuit breaker, affinity) | Story 5.4 | ✓ Covered |
| FR-34 | Admin security & recoverability (MFA, roles, revoke, backup) | Story 5.8 (audit+MFA), 5.9 (alerts) | ✓ Covered |

**FRs in epics but NOT in PRD:** None. All stories trace back to a PRD FR, NFR, or an architecture-derived ADD requirement (which themselves derive from PRD §6/§13). No scope creep detected.

### NFR Coverage (spot-check)

NFRs are cross-cutting; epics map a primary-responsible epic. Spot-checked the highest-risk ones (per project-context.md):
- **NFR-5 redaction** → Story 1.3 (dedicated redaction util + tests) — strong, early.
- **NFR-8 secret_ref / storage classification** → Story 2.4, 4.2, 4.9, 5.2 — consistent `*_ref` enforcement.
- **NFR-29 negative safety tests** (wrong tenant/profile/room, stale token, restored DB, double claim, crash-after-send) → Story 5.10 — all 7 PRD §6.5 scenarios enumerated.
- **NFR-14 7 monitoring metrics** → Story 5.9 — all 7 with thresholds.
- **NFR-24/25 cutover/rollback** → Story 3.8.
All §6.1–§6.5 NFR clauses have at least one home. No orphaned NFR found.

### Missing Requirements

**Critical Missing FRs:** None. All 34 FRs trace to ≥1 story with concrete acceptance criteria.

**Observations (not gaps, flagged for Step 4/5):**
1. **FR-4 Admin CRUD UI is implied but thinly storied.** Story 2.5 builds the domain models and Story 5.6/5.7 build dashboard + manual actions, but there is no explicit story for *create/edit Customer/Target/Channel/DeliveryRule via Admin UI* forms. The coverage map says "Admin CRUD UI는 Epic 5" yet Epic 5 stories (5.1–5.10) cover dashboard, actions, channel registration, audit/security, metrics — not generic entity CRUD screens. **→ Verify in Step 5 whether FR-4's "운영자는 …생성/조회/수정/비활성화" is fully satisfiable from Story 5.7's action set, or if a CRUD-screen story is missing.**
2. **FR-27 is "structural" coverage**, satisfied by epic ordering rather than a dedicated story. This is legitimate but means the P0→P4 cutover discipline lives in Story 3.8 + epic sequencing, not one owner. **→ Confirm in Step 5 cohesion check.**
3. Several stories depend on architecture decisions the PRD listed as **Open Questions** (Gmail token location → resolved as Agent-local DPAPI in NFR-8/ADD; job-claim/idempotency → ADD-4/5; tenant isolation; Admin access → Story 5.8). The epics *assert* these resolutions (via ADD-1…16). **→ Step 4 must confirm architecture.md actually contains these ADRs**, since epics treat them as settled.

### Coverage Statistics

- **Total PRD FRs:** 34
- **FRs covered in epics (with concrete AC):** 34
- **Coverage percentage: 100%**
- **NFR clauses (§6):** ~29 numbered in epics, all mapped (no orphans found in spot-check)
- **Extra (non-PRD) requirements:** 16 ADD items, all architecture-derived (no scope creep)
- **MVP stories:** 35 (Epic 1: 5, Epic 2: 7, Epic 3: 8, Epic 4: 9, Epic 5: 10)

---

## UX Alignment Assessment

### UX Document Status

**Not Found** — no `*ux*.md` artifact exists. **Assessed as: UX implied (an Admin UI exists) but intentionally folded into PRD + Architecture rather than a standalone UX spec.** This is acceptable for this project type, and the epics document explicitly states the same ("이 프로젝트는 별도 UX Design Specification이 없다", epics §UX Design Requirements).

**Why a standalone UX spec is not required here:**
- The only UI is an **operator-only internal Admin tool** (not a customer-facing product surface). No marketing site, no consumer mobile/web app, no SEO concern.
- UI requirements are fully captured as functional requirements: **FR-21** (operations dashboard), **FR-22** (manual actions), **FR-23** (status severity display), plus **ADD-10** (server-rendered FastAPI+Jinja2+HTMX decision).
- Message *content* (the actual user-facing artifact for end recipients) is governed by the existing renderer + `template_version` (FR-8), not by UI design.

### UX ↔ PRD ↔ Architecture Alignment (verified)

Since the "UX" here is the Admin UI, I validated that the **architecture actually supports the UI requirements** and — critically — that **architecture.md resolves the PRD's gating ADRs** that the epics assert as settled (Step 3 observation #3).

**Architecture supports the Admin UI requirements:**
- FR-21 dashboard → architecture.md "Frontend Architecture" specifies the exact screens (customer/target/agent/channel/recent-error/last-success/queue-lag/auth-required filter/audit log) and server-side severity calculation. ✓
- FR-23 severity (2×/4×) → architecture explicitly defines `target_last_success_at > interval×2 → warning, ×4 → critical`, matching PRD §5.6 and epics Story 5.6 exactly. ✓ **No drift across PRD → architecture → epics on this threshold.**
- ADD-10 server-rendered, same auth/session as backend → architecture "Component Boundaries" confirms Admin API/UI share session+MFA, no separate JS build pipeline. ✓

**Architecture resolves the PRD's gating ADRs (PRD §13.1) — confirmed:**

| PRD §13.1 / §12.1 open item | architecture.md resolution | Status |
| --- | --- | --- |
| Agent authentication + job claim/complete protocol | registration code→agent_id+token (tenant+job-type scope), signed claim/lease, 5 Agent APIs | ✓ Decided |
| Secret storage, rotation, revocation, diagnostics handling | central Secrets Manager (refs) vs Agent-local DPAPI vs non-stored; revoke/rotate; sanitized-only S3 | ✓ Decided |
| Queue/job state, lease, retry, idempotency, crash-after-send | PostgreSQL queue (`FOR UPDATE SKIP LOCKED`), at-least-once + DB unique dedup, insert-then-send, lease | ✓ Decided |
| Tenant isolation (DB/API/queue/log/Admin/Agent) | tenant_id on all owned entities; default tenant-scope filter on all queries; cross-tenant negative tests | ✓ Decided |
| Migration cutover, rollback, kill switch, simultaneous-send prevention | cutover state machine + global kill switch + old/new dual-send prevention + canary | ✓ Decided |
| Admin access control, MFA, roles, audit log shape | MFA mandatory (all accounts) + 4 roles + audit fields (actor/source/before-after/target IDs/reason/timestamp/result) | ✓ Decided |
| KakaoTalk product policy + sender runtime constraints | limited/best-effort, FIFO single-session serial, unique-room validation, no strong SLA | ✓ Decided |
| **Open Q:** queue backend (PostgreSQL vs Redis) | PostgreSQL (Redis not adopted) | ✓ Closed |
| **Open Q:** Admin UI tech (React/Next vs server-render) | FastAPI+Jinja2+HTMX | ✓ Closed |
| **Open Q:** Gmail token storage location | Agent-local DPAPI | ✓ Closed |

**Result:** All 7 gating ADRs are decided and all 3 Open Questions are closed in architecture.md. **Step 3 observation #3 is RESOLVED** — the epics' ADD-1…16 assertions are genuinely backed by architecture decisions, not assumed. The architecture's own frontmatter shows `status: complete`, `stepsCompleted [1..8]`, and a self-assessment of "READY FOR IMPLEMENTATION".

### Alignment Issues

- **None blocking.** PRD ↔ Architecture ↔ Epics are mutually consistent on the UI surface, the severity thresholds, the data model (13 tables), the Agent API (5 endpoints), and the dedup key formula. The same `template_version`, `uq_delivery_logs_dedup_key`, and 7-metric set appear identically in all three documents.

### Warnings

1. **Business launch blockers remain open (not an architecture/UX gap).** Architecture explicitly flags that **commercial launch** is gated by legal/terms/account-delegation/consent/KakaoTalk-policy review (PRD §12.2/§13.3). These do **not** block implementation of the MVP epics, but they block go-live. Operators of this readiness check should not interpret "implementation ready" as "launch ready."
2. **Operational thresholds are initial values** (warning/critical multipliers, kakao lag 120s, error-rate 30%/15min). Architecture notes these need real-measurement tuning post-deploy. Stories encode them as concrete numbers, which is fine for MVP but should be treated as tunable, not contractual.
3. **ADRs are embedded in architecture.md, not split into `docs/adr/` files.** Architecture lists this as a nice-to-have. Not a readiness blocker; minor traceability improvement.

---

## Epic Quality Review

Reviewed all 5 MVP epics and 35 stories against the create-epics-and-stories standards: user-value focus, epic independence, forward-dependency prohibition, story sizing, AC quality (Given/When/Then, testable, error paths), and DB-table creation timing. Brownfield-specific checks applied (migration/compatibility stories, reuse boundaries).

### 1. Epic Structure — User Value Focus

| Epic | Title framing | Verdict |
| --- | --- | --- |
| Epic 1 | "리팩토링해도 기존 운영이 깨지지 않는다" — operator revert/regression safety | ✓ User value (operator) |
| Epic 2 | "탭 번호가 아니라 고객/대상 ID로 본다" — operator identification capability | ✓ User value |
| Epic 3 | "한 번 수집 → 안전하게 여러 채널로" — operator/customer fan-out without duplicates | ✓ User value |
| Epic 4 | "Chrome·KakaoTalk·인증을 로컬에서 안전하게 처리한다" — work-node-manager runs real local jobs | ✓ User value (operator-facing capability, not a bare "infra setup") |
| Epic 5 | "한곳에서 보고, 제어하고, 안전하게 운영한다" — operator observability + control | ✓ User value |

**No technical-milestone epics.** Every epic is framed by an operator/customer outcome, not "set up DB" / "build API." Notably, the project deliberately did **not** create a standalone "scaffolding" epic — scaffolding is embedded as the first story of the epic that needs it (Story 5.1 for server, Story 4.1 for agent), which is the correct brownfield pattern.

### 2. Epic Independence

The epics follow the **P0→P4 phase ordering** (architecture's risk boundary). Each epic builds only on **earlier** epics, never a later one:
- Epic 1 (baseline) stands alone. ✓
- Epic 2 (ID model/migration) uses only Epic 1's preserved assets. ✓
- Epic 3 (collect/render/dispatch split) uses Epic 1+2 domain models. ✓
- Epic 4 (Local Agent) uses Epic 1–3 services; runs against server stub/mock (doc explicitly says "서버 stub/mock에 대해 동작 검증"). ✓
- Epic 5 (central server/Admin) completes the server side Epic 4 talked to via mock. ✓

**Backward-deferral pattern (legitimate, not a violation):** Several earlier stories build a *partial* capability and explicitly defer the *fuller* capability to a later epic — e.g. Story 2.3 sets a target's risk-state field but "실제 차단은 Epic 4"; Story 2.5/2.6 build the state model, Admin transition UI is Epic 5. **This is the correct direction** (early epic delivers a usable slice; later epic extends it). It is **not** a forward dependency because the early story is independently completable and testable without the later one. ✓

**One genuine sequencing tension (see §5 Major):** Epic 4 is sequenced *before* Epic 5, but Epic 4 stories exercise the Agent API (claim/heartbeat/complete) and the PostgreSQL `jobs` table + `SKIP LOCKED` claim semantics — which are **implemented** in Epic 5 (Story 5.2, 5.3). Epic 4 says it validates "against server stub/mock." This is workable but means Epic 4's claim-loop stories cannot be *end-to-end* verified until Epic 5 lands. Flagged as Major (sequencing/integration-timing), not Critical (no story is logically un-completable; the mock boundary is explicit).

### 3. Story Sizing & Independence

- 35 stories, each scoped to a single coherent capability with a clear actor (`As a 운영자/개발자/작업 노드 관리자/고객사 담당자/보안 담당...`). None are epic-sized ("build everything") and none are trivial sub-tasks.
- No story references a *later* story as a prerequisite. Within-epic order is additive (e.g. Epic 4: 4.1 package → 4.2 register → 4.3 heartbeat → 4.4 claim loop → 4.5 profile → 4.6 kakao → 4.7 autostart → 4.8 baemin auth → 4.9 gmail 2FA). ✓
- Brownfield reuse is explicit: Story 1.5 (reuse boundary + forbidden behaviors), Story 3.1–3.3 (wrap `run_once`), Story 4.1 (import existing crawler/parser). ✓

### 4. Acceptance Criteria Quality

- **Format:** Every story uses proper **Given/When/Then** BDD multi-scenario ACs. ✓
- **Error/negative paths present:** ACs consistently include failure scenarios, not just happy path — e.g. Story 3.2 (missing data → `MissingPerformanceDataError`, no message), Story 4.6 (ambiguous room → record failure, no send), Story 3.5 (crash-after-send → insert-then-send, no duplicate), Story 5.10 (7 negative-safety scenarios). This is notably strong and aligns with the project's fail-closed principle. ✓
- **Testable & specific:** ACs cite concrete artifacts/values (`uq_delivery_logs_dedup_key`, `baseline-local-ui-YYYYMMDD`, interval×2/×4, the dedup key formula, the 7 metrics with thresholds). Very few vague criteria. ✓
- **Traceability:** Nearly every AC cites its FR/NFR/ADD source inline (e.g. "(P2-05, FR-10, ADD-5)"). Excellent traceability hygiene. ✓

### 5. DB / Entity Creation Timing

- **Correct pattern.** Tables are not all created in Epic 1. Domain *models* (dataclass/Enum) appear in Epic 2 (Story 2.5) where first needed; the **physical PostgreSQL 13-table Alembic migration is in Epic 5 Story 5.2** (P4-02), where the server is scaffolded. The empty-DB → 13-table migration is an explicit acceptance criterion there. ✓
- One nuance: Epic 2 builds domain/state models that won't be *persisted to Postgres* until Epic 5. For Epic 2 these live in `src/rider_crawl`/config-level structures (the doc says so explicitly). Acceptable — Epic 2 is the P1 "domain/config refactor" phase, not the DB phase.

### Findings by Severity

#### 🔴 Critical Violations
**None.** No technical-milestone epics, no forward dependencies that make a story un-completable, no epic-sized stories.

#### 🟠 Major Issues
1. **Epic 4 → Epic 5 integration-timing dependency.** Epic 4's Agent claim/heartbeat/complete stories (4.2–4.4) depend on server-side queue semantics (`jobs` table, `FOR UPDATE SKIP LOCKED`, lease) that are only *implemented* in Epic 5 (Stories 5.2, 5.3). Epic 4 mitigates with an explicit "server stub/mock" boundary, so stories are completable, but **true end-to-end claim verification slips to Epic 5.** *Remediation:* either (a) accept the mock boundary and add an explicit "Epic 4↔5 integration" verification story in Epic 5 (Story 5.3 partially does this), or (b) pull a minimal server-side claim endpoint forward as a thin Epic 4 dependency. Recommend (a) — it's already mostly there; just make the cross-epic integration test an explicit AC.
2. **FR-4 generic Admin CRUD screens have no dedicated story.** (Carried from Step 3.) FR-4 requires operators to "생성/조회/수정/비활성화" customers/targets/channels/rules. Story 2.5 builds the models; Story 5.6 is the dashboard (read); Story 5.7 is action-oriented (test crawl/send/retry/auth/subscription-transition). **No story explicitly delivers create/edit forms for the core entities.** This may be an intentional MVP scope choice (operator edits via DB/config initially), but it is not stated. *Remediation:* either add a small "Admin entity CRUD" story to Epic 5, or add an explicit note that MVP entity creation is handled via migration + Story 5.7 actions and full CRUD UI is deferred — so the gap is a decision, not an oversight.

#### 🟡 Minor Concerns
1. **FR-27 has no single owner story** — phased cutover discipline is distributed across epic ordering + Story 3.8 (cutover/rollback). Acceptable, but a one-line "cutover runbook" reference would make ownership explicit.
2. **Story 2.3 ↔ Story 4.5 split on target validation** (field-state set in 2.3, enforcement/blocking in 4.5). Logically sound, but a reviewer must hold both to see the full FR-20 story. Minor cognitive coupling.
3. **Story numbering vs spec P-codes** occasionally interleave non-monotonically (e.g. Epic 5 cites P4-01…P4-07 but story order is 5.1…5.10). Not a defect; just note the story order, not the P-code order, is the build order.

### Best-Practices Compliance Checklist (per epic, aggregate)

| Check | Result |
| --- | --- |
| Epic delivers user value | ✅ all 5 |
| Epic can function independently (on earlier epics only) | ✅ all 5 (Epic 4 e2e-gated on Epic 5 — Major #1) |
| Stories appropriately sized | ✅ 35/35 |
| No forward dependencies (un-completable) | ✅ none found |
| DB tables created when needed | ✅ (Epic 5 Story 5.2, not upfront) |
| Clear, testable Given/When/Then ACs with error paths | ✅ strong |
| Traceability to FRs maintained | ✅ inline FR/NFR/ADD citations |

---

## Summary and Recommendations

### Overall Readiness Status

## ✅ READY for implementation — with 2 Major items to address-or-decide (neither blocks starting Epic 1).

This is one of the more rigorously prepared planning sets I've reviewed. The PRD, Architecture, and Epics are mutually consistent and traceable: **100% of 34 FRs trace to concrete stories with testable acceptance criteria**, all **7 gating ADRs are decided** and all **3 architecture Open Questions are closed**, NFRs are numbered and mapped with no orphans, and there are **zero Critical structural defects** in the epic/story decomposition. The work can begin at Epic 1 (P0 baseline) immediately.

### Issue Tally

- 🔴 **Critical: 0**
- 🟠 **Major: 2** (neither blocks Epic 1; both are best resolved before/within Epic 5)
- 🟡 **Minor: 3**
- ⚠️ **Non-readiness business gate: 1** (commercial-launch legal/policy review — out of scope for *implementation* readiness, but must close before go-live)

### Critical Issues Requiring Immediate Action

**None.** No issue prevents starting implementation.

### Major Issues to Address-or-Decide (before/within Epic 5)

> **UPDATE 2026-06-13:** Both Major items below were **RESOLVED** by editing `epics.md` after this assessment (user-approved). See "Post-Assessment Remediation" at the end of this report.

1. **FR-4 generic Admin CRUD screens have no dedicated story.** Decide explicitly: either add a small "Admin entity create/edit" story to Epic 5, **or** record that MVP entity creation is handled via migration + Story 5.7 actions and full CRUD UI is deferred post-MVP. Make it a decision, not a silent gap. — **RESOLVED:** new **Story 5.11 (Admin 엔티티 생성/편집 CRUD UI)** added to Epic 5; FR-4 coverage map + Epic 5 FR list updated. MVP stories 35 → 36.
2. **Epic 4 ↔ Epic 5 end-to-end claim verification timing.** Epic 4's Agent claim/heartbeat/complete stories run against a server stub/mock; real `jobs`-table `SKIP LOCKED` semantics land in Epic 5 (5.2/5.3). Make the cross-epic integration test an explicit acceptance criterion in Epic 5 Story 5.3 so the mock→real seam is verified, not assumed. — **RESOLVED:** explicit Epic 4↔5 integration-verification AC added to **Story 5.3** (real-server claim/complete e2e, double-claim negative, lease-expiry stale recovery against real DB).

### Recommended Next Steps

1. **Proceed to story creation / implementation starting at Epic 1, Story 1.1** (P0 baseline tag + backup). The architecture's "First Implementation Priority" already names this exact starting point.
2. **Make two small edits to `epics.md`** to convert the 2 Major items into explicit decisions: (a) a note or story for FR-4 Admin CRUD scope; (b) an explicit Epic-4↔5 integration-verification AC in Story 5.3.
3. **(Optional, Minor)** Add a one-line FR-27 cutover-owner reference and consider splitting ADRs into `docs/adr/` for traceability — both are nice-to-haves, not blockers.
4. **Track the commercial-launch gate separately.** Implementation can run in full while legal/terms/account-delegation/consent/KakaoTalk-policy review proceeds in parallel. Do **not** flip any DeliveryRule to live production sending for a paying customer until that gate closes — the architecture's kill-switch + fail-closed + dry-run discipline supports running right up to (but not through) that line.

### Final Note

This assessment reviewed 3 primary artifacts (PRD, Architecture, Epics) plus 8 supporting documents, and validated 34 FRs, ~29 NFR clauses, 16 architecture-derived requirements, 5 MVP epics, and 35 stories across 6 workflow steps. It identified **5 issues across 2 severity bands (2 Major, 3 Minor) and 1 separate business-launch gate** — and **0 Critical defects**. None of the implementation-side findings block starting work. Address the 2 Major items as explicit decisions before they become relevant (Epic 5), and keep the commercial-launch legal gate on a parallel track. **Recommendation: proceed to implementation.**

*Assessed 2026-06-13 by the BMad Implementation Readiness workflow (Product Manager role), facilitated for Noah Lee. Report: `_bmad-output/planning-artifacts/implementation-readiness-report-2026-06-13.md`.*

---

## Post-Assessment Remediation (2026-06-13)

After the assessment, the user approved editing `epics.md` to convert both Major items into explicit, storied decisions. Changes applied:

1. **New Story 5.11 — Admin 엔티티 생성/편집 CRUD UI** (Epic 5). Operator can create/read/update/deactivate Customer, Platform Account, Monitoring Target, Message Channel, DeliveryRule via the Admin UI. ACs cover: ID-based CRUD aligned to the Story 2.5 domain contract; 1:N DeliveryRule creation; soft-delete (no physical delete, no auto-reactivation); secret kept as `*_ref` only (no plaintext in form/DB); Coupang expected-center/store-name empty/default → risk-flag on save; tenant-scope filter on all owned-entity ops; audit-log on every create/edit/deactivate; role-gated writes (viewer read-only). → **FR-4 "생성/수정" fully satisfied.**
2. **Story 5.3 — added Epic 4↔5 integration-verification AC.** Real `rider_agent` claims from the real PostgreSQL `jobs` queue (`FOR UPDATE SKIP LOCKED`) end-to-end through `complete` (not mock); concurrent double-claim → exactly one success (ties to Story 5.10 negative test, FR-13); lease-expiry stale recovery + reassignment verified against the real DB. → **mock→real seam now explicitly verified, not assumed.**

**Traceability updates:** FR-4 coverage-map line now points to Story 5.11; Epic 5 list entry and detailed-section header now include FR-4. MVP story count **35 → 36** (Epic 5: 10 → 11).

**Net effect on readiness:** 🟠 Major **2 → 0**. Remaining open items are 3 Minor (nice-to-have) + the 1 separate commercial-launch business gate. Overall status unchanged: **✅ READY for implementation.**
