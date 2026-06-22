# Operational Risk Delta Review

Reviewed documents:

- `_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/prd.md`
- `_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/addendum.md`

Scope: check whether the previous blocker/high findings from `review-operational-risk.md` are now represented as explicit PRD requirements or readiness gates.

## Verdict

**Pass for representation; conditional for execution.**

The updated PRD/addendum now represent the previous blocker/high findings as explicit launch blockers, architecture readiness gates, implementation readiness evidence, commercial launch gates, or ADR topics. This does not mean the risks are resolved; it means the PRD now blocks architecture, implementation, or launch until the required decisions/evidence exist.

## Delta Against Prior Blocker/High Findings

### 1. Legal, account delegation, and platform-policy risk

**Status: represented.**

The prior blocker is now covered as a launch blocker and commercial launch readiness gate.

Evidence:

- `prd.md` §12.2 Launch Blockers requires platform terms, account delegation, customer consent, assisted authentication, Gmail access, and KakaoTalk automation policy review, with owner and artifacts.
- `prd.md` §13.3 Commercial Launch Readiness requires legal/policy review and customer-facing terms covering access, operator actions, credentials/OTP handling, messaging, and revocation.

No remaining blocker/high finding for representation.

### 2. Secret custody and privacy controls

**Status: represented.**

The prior blocker is now covered as security/privacy requirements plus architecture readiness.

Evidence:

- `prd.md` §6.2 requires a data inventory for secrets, credentials, customer identifiers, message content, logs, browser profiles, backups, and diagnostic artifacts.
- `prd.md` §6.2 requires secret storage classification, encryption at rest where applicable, and retention/scrubbing for screenshots, HTML dumps, exception traces, queue payloads, and failed message bodies.
- `prd.md` §12.1 keeps Gmail token storage as an architecture blocker.
- `prd.md` §13.1 requires ADR coverage for secret storage, token rotation, token revocation, and diagnostic artifacts.
- `addendum.md` ADR topics repeat secret custody and token rotation/revocation.

No remaining blocker/high finding for representation.

### 3. Admin and Agent trust boundaries

**Status: represented.**

The prior high finding is now covered by Admin security requirements and architecture readiness gates.

Evidence:

- `prd.md` FR-34 now requires MFA for all Admin accounts and MVP role separation.
- `prd.md` FR-34 requires token revoke/rotate.
- `prd.md` §13.1 requires ADR coverage for Agent authentication, job claim/complete protocol, Admin access control, MFA, roles, and audit log shape.
- `prd.md` §13.2 requires Admin/Agent audit log fields.
- `addendum.md` requires ADR topics for Agent authentication/scoped job claim protocol and Admin access control.

No remaining blocker/high finding for representation.

### 4. Migration cutover, rollback, and activation safety

**Status: represented.**

The prior high finding is now covered by migration requirements and readiness gates.

Evidence:

- `prd.md` §6.4 requires migration states: discovered, mapped, dry-run passed, approved, active, paused, rolled back.
- `prd.md` §6.4 requires global dispatch kill switch and tenant/channel pause.
- `prd.md` §6.4 requires old/new path simultaneous-send prevention and rollback behavior.
- `prd.md` §13.1 requires ADR coverage for migration cutover, rollback, kill switch, and simultaneous-send prevention.
- `prd.md` §13.2 requires baseline capture and canary migration plans.

No remaining blocker/high finding for representation.

### 5. KakaoTalk product and operational limits

**Status: represented.**

The prior high finding is now covered by launch blockers, commercial launch readiness, and ADR topics.

Evidence:

- `prd.md` §12.2 sets KakaoTalk policy as a launch blocker and states MVP default as limited/best-effort pending customer guidance, quota, and incident policy.
- `prd.md` §12.2 says strong SLA must not be promised without an official alternative.
- `prd.md` §13.1 requires ADR coverage for KakaoTalk product policy and sender runtime constraints.
- `prd.md` §13.3 requires KakaoTalk to be described as limited/best-effort unless an official high-reliability channel is adopted.
- `addendum.md` requires ADR coverage for KakaoTalk policy, quotas, SLA language, and sender runtime constraints.

No remaining blocker/high finding for representation.

### 6. Local Agent hardening

**Status: represented.**

The prior high finding is now covered by implementation readiness evidence.

Evidence:

- `prd.md` §13.2 requires a Local Agent #1 hardening checklist with disk encryption or documented exception, Windows account isolation, profile folder permissions, screen lock policy, remote access MFA, and lost/decommissioned node handling.
- `prd.md` §2.3 clarifies MVP Local Agent is operator-owned, while customer-installed Agent is post-MVP.
- `addendum.md` retains Windows Agent hardening notes.

No remaining blocker/high finding for representation.

### 7. Job execution semantics

**Status: represented.**

The prior high finding is now covered by architecture readiness and implementation readiness evidence.

Evidence:

- `prd.md` §13.1 requires ADR coverage for queue/job state model, lease, retry, idempotency, and crash-after-send behavior.
- `prd.md` §13.2 requires negative safety tests for stale/revoked Agent token, duplicate dispatch, double Agent claim, and crash-after-send.
- `prd.md` §6.5 requires negative safety tests including double Agent claim and crash-after-send.
- `addendum.md` requires ADR coverage for queue state, lease semantics, retry policy, idempotency, and crash-after-send behavior.

No remaining blocker/high finding for representation.

## Remaining Notes

- The PRD now gates unresolved decisions appropriately, but those gates still need to be closed before architecture handoff, story breakdown, or commercial launch.
- The most important remaining work is producing the ADRs and launch artifacts named in §12 and §13, not adding more PRD prose.
