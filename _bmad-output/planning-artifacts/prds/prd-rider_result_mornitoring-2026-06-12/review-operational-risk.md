# Operational Risk Review

Reviewed documents:

- `_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/prd.md`
- `_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/addendum.md`

Review focus: operational safety, security/privacy, migration risk, legal/account-delegation risk, and architecture readiness.

## Verdict

**Conditional no-go for build handoff and commercial launch planning.**

The PRD is directionally strong: it correctly identifies the core danger areas, especially cross-customer misdelivery, browser profile isolation, KakaoTalk UI automation, duplicate delivery, Local Agent constraints, and human-assisted authentication. However, several high-risk areas are still left as assumptions, open questions, or architecture follow-ups. For a brownfield app moving from one local operator machine to central server plus Windows Local Agent operations, those items are not optional polish. They are launch gates and architecture inputs.

The next step should not be broad implementation. It should be a short risk-closure pass that turns the items below into explicit product gates, architecture decisions, test evidence, and operational runbooks.

## Top Findings

### 1. Blocker: Legal, account delegation, and platform-policy risk is acknowledged but not gated

The PRD names platform terms, customer consent, account delegation, operator-assisted authentication, and KakaoTalk automation as risks and open questions. That is not enough for a sellable multi-customer operation.

Relevant references:

- `prd.md` section 4.2 lists policy and delegation uncertainty as a major concern.
- `prd.md` FR-18 assumes the operator may assist with Baemin reauthentication.
- `prd.md` section 10 leaves terms/account-delegation review as a launch-time item.
- `prd.md` Open Question 11 asks who owns this review.
- `addendum.md` Open Addendum Items asks to assign ownership for platform terms, customer consent, account delegation, and authentication-assistance policy review.

Risk:

If operators log in, trigger phone verification, read Gmail 2FA, or automate KakaoTalk on behalf of customers without a clear consent and policy model, the product can create contract, privacy, platform-ban, and customer-dispute exposure. The PRD currently lets engineering start while the legal basis for the operating model is unresolved.

Required changes before handoff:

- Make legal/policy review a **P0 pre-launch gate**, not an open question.
- Define who performs the review, what artifacts prove completion, and what product features are blocked until completion.
- Require customer authorization language for platform account access, message sending, OTP/2FA assistance, remote support, retention, and revocation.
- Define forbidden operations: credential sharing methods, OTP storage, unattended account takeover, sending through unverified KakaoTalk rooms, and any platform-policy bypass.
- Require a customer-facing description of KakaoTalk as best-effort or limited if the official API alternative is not used.

### 2. Blocker: Secret custody and privacy controls are under-specified for central-server plus local-agent operations

The PRD requires redaction and mentions token storage decisions, but it does not yet define a full secret and privacy model.

Relevant references:

- `prd.md` FR-19 requires Gmail token/customer separation and no token/password/code logging.
- `prd.md` FR-31 adds log rotation/retention.
- `prd.md` FR-34 requires token revoke/rotate, backup, retention, and restore rehearsal.
- `prd.md` section 6.2 leaves Gmail token storage undecided.
- `addendum.md` Windows Agent Hardening Notes mentions DPAPI, BitLocker, Windows Credential Manager, profile permissions, remote access 2FA, and token revoke as architecture decisions.

Risk:

The system handles Telegram bot tokens, Gmail OAuth tokens, Coupang credentials, authentication codes, chat IDs, topic IDs, customer identity, browser profiles, and possibly screenshots or diagnostic output. Redaction alone does not protect secrets at rest, secrets in backups, local profile folders, remote support sessions, crash dumps, queue payloads, or Admin UI displays.

Required changes before handoff:

- Add a data inventory: secret, credential, customer identifier, message content, operational log, browser profile, and backup categories.
- Define where each secret may live: central DB, managed secret store, Windows DPAPI/Credential Manager, environment variable, or never persisted.
- Require encryption at rest for DB/backups and Windows profile/secret storage where applicable.
- Require secret access audit logs and least-privilege scoping by tenant, Agent, job type, and channel.
- Define diagnostic artifact retention and scrubbing rules, including screenshots, HTML dumps, exception traces, queue payloads, and failed message bodies.
- Make restore testing include secret safety: restored data must not accidentally activate dispatch or leak live credentials into non-production.

### 3. High: Admin and Agent trust boundaries are too weak for the blast radius

The PRD requires authenticated HTTPS, Agent tokens, heartbeat, revoke/rotate, and at least one Admin protection option. That still leaves the central control plane and local agents too open-ended.

Relevant references:

- `prd.md` FR-12 to FR-16 define Agent registration, polling, claim/complete, profile isolation, Kakao queue, and outbound-only communication.
- `prd.md` FR-22 says MVP favors audit logs over detailed RBAC.
- `prd.md` FR-34 says Admin access must use one of 2FA, VPN, IP allowlist, or an equivalent control.

Risk:

A compromised Admin account or Agent token can cause cross-customer data exposure, malicious dispatch jobs, secret access, or silent job manipulation. "One of 2FA, VPN, IP allowlist" is too weak for an operator console that can send customer messages, trigger browser sessions, and retry jobs.

Required changes before handoff:

- Require MFA for all Admin users; do not treat IP allowlist alone as enough.
- Add role separation even for MVP: viewer, operator, secret/admin, and break-glass admin can be simple but must exist.
- Scope Agent tokens to tenant/job type/profile/channel where possible; one stolen Agent token should not claim arbitrary jobs.
- Define token issuance, rotation, revocation, expiry, and lost-agent handling.
- Add replay protection or equivalent signed job/lease semantics for Agent claim and completion.
- Make risky Admin actions require audit records with actor, before/after values, target IDs, reason, and source IP/device.

### 4. High: Migration plan lacks hard cutover, rollback, and activation safety gates

The PRD strongly emphasizes baseline preservation, dry-run comparison, and non-deletion of existing settings. That is good, but the actual cutover safety model is not complete.

Relevant references:

- `prd.md` FR-1 to FR-3 define baseline capture and dry-run comparison.
- `prd.md` FR-27 defines phased transition P0-P4.
- `prd.md` FR-31 requires atomic settings write, `last_message` seed migration, inactive-tab preservation, and log retention.
- `prd.md` section 11 lists launch and migration requirements.

Risk:

The highest-risk migration failures are not just file deletion. They are duplicate sending, stale `last_message` state, reactivating inactive tabs, wrong DeliveryRule activation, old and new paths both sending, partial migration after a crash, and inability to roll back when the new server path misbehaves.

Required changes before handoff:

- Add an explicit migration state machine: discovered, mapped, dry-run passed, approved, active, paused, rolled back.
- Require a global dispatch kill switch and per-tenant/per-channel pause.
- Define whether old and new paths can run in parallel and which path is allowed to send.
- Require canary migration for one Baemin and one Coupang target before batch migration.
- Define rollback: how to disable new DeliveryRules, restore old runtime behavior, and preserve new logs without sending duplicates.
- Add migration reconciliation checks: active count, inactive count, profile mapping count, channel mapping count, idempotency seed count, and unmapped item report.

### 5. High: KakaoTalk remains a high-risk channel without enough product and operational limits

The PRD correctly treats KakaoTalk as constrained, serialized, and best-effort. But it still includes KakaoTalk in MVP and leaves official API evaluation and product packaging open.

Relevant references:

- `prd.md` FR-15 requires serialized KakaoTalk delivery and chatroom validation.
- `prd.md` FR-25 says KakaoTalk should not be treated as unlimited or strong-SLA.
- `prd.md` Open Questions 2 and 3 leave KakaoTalk product policy and official alternatives undecided.
- `addendum.md` notes public Kakao safety/policy concerns and recommends waiting before making high-reliability claims.

Risk:

KakaoTalk PC UI automation has technical failure modes that are hard to fully control: focus changes, duplicate room names, UI updates, clipboard issues, account restrictions, rate patterns, remote desktop/session behavior, and screen lock behavior. It also carries policy risk. A central product can amplify these issues across many customers.

Required changes before handoff:

- Define KakaoTalk MVP policy: default disabled, limited beta, quota-limited add-on, or premium/manual approval.
- Add customer-facing SLA language: no strong delivery guarantee unless official API/channel path is used.
- Require room identity verification beyond visible name where feasible, or explicitly block ambiguous cases.
- Define rate limits, daily caps, queue lag thresholds, and customer notification behavior when lag exceeds policy.
- Add operator runbooks for UI update, login loss, account restriction, focus failure, duplicate room names, and stuck queue.

### 6. High: Local Agent hardening is listed as architecture work but should be product acceptance criteria

The addendum mentions key Windows hardening topics, but the PRD does not make them acceptance criteria.

Relevant references:

- `prd.md` FR-32 covers interactive user session and reboot recovery.
- `addendum.md` Windows Agent Hardening Notes mention BitLocker, DPAPI, profile permissions, remote access 2FA, and Agent token revoke.

Risk:

The Local Agent is not a dumb worker. It has browser profiles, session cookies, possibly Gmail tokens, platform credentials, KakaoTalk access, and customer data. If the PC is shared, unlocked, remotely accessed without controls, or backed up poorly, it becomes the easiest place to steal credentials or send unauthorized messages.

Required changes before handoff:

- Add a minimum Windows node hardening checklist to MVP acceptance.
- Require disk encryption or a documented exception.
- Require Windows account isolation, profile folder permissions, screen lock policy, and remote access MFA.
- Define whether agents auto-update, how updates are signed, and how version rollback works.
- Define what happens when a node is lost, stolen, decommissioned, or reassigned.
- Separate crawler-only nodes and Kakao sender nodes in both permissions and operational requirements.

### 7. High: Job execution semantics need architecture decisions before implementation

The PRD names polling, claim, complete, timeout, idempotency, retry, and circuit breaker behavior. It does not yet define the state machine or database/queue invariants that prevent unsafe behavior.

Relevant references:

- `prd.md` FR-10 covers duplicate delivery prevention.
- `prd.md` FR-11 covers retry and failure state.
- `prd.md` FR-13 covers claim/complete.
- `prd.md` FR-33 covers jitter, circuit breaker, assignment, and backoff.
- `addendum.md` calls out queue-based execution and idempotency.

Risk:

Distributed jobs are normally at-least-once. If the architecture accidentally assumes exactly-once execution, retries and timeouts can create duplicate sends or false success. This is especially dangerous for KakaoTalk because there may be no reliable delivery receipt from the UI.

Required changes before handoff:

- Define job states and allowed transitions for CrawlJob, MessageRenderJob, DispatchJob, and AuthSession.
- Require database-level uniqueness for idempotency keys and DeliveryLog success records.
- Define job lease duration, heartbeat extension, timeout behavior, and stale-claim recovery.
- Require dispatch to be safe under duplicate execution, process crash after send, network failure after completion report, and Agent restart.
- Define which operations are retryable, non-retryable, and human-action-required.
- Add tests for replayed complete calls, stale claim completion, duplicate dispatch creation, and crash-after-send.

### 8. Medium: Tenant isolation is implied but not testable enough

The PRD defines Customer and Tenant and assumes near 1:1 mapping in MVP. It does not yet require strong tenant scoping for every operation.

Relevant references:

- `prd.md` section 3 defines Tenant.
- `prd.md` FR-4 defines ID-based management.
- `addendum.md` recommends attaching `tenant_id` to events/logs.

Risk:

The central server changes the failure mode from "one local operator machine is messy" to "one bug can cross customers." Tenant isolation has to be enforced in APIs, DB queries, queues, logs, Admin screens, backups, and Agent job assignment.

Required changes before handoff:

- Require every customer-owned entity to carry `tenant_id` or a clear tenant scope.
- Require API, queue, and Admin queries to filter by tenant scope by default.
- Add negative tests for cross-tenant reads, updates, retries, dispatches, and Agent claims.
- Require logs and metrics to include tenant-scoped IDs without exposing secrets or raw personal data.
- Define whether an Agent can serve one tenant, many tenants, or only specific profiles.

### 9. Medium: Observability is good on status, weaker on incident response

The PRD requires status screens, severity, queue lag, heartbeat, failure categories, and alerts. It does not yet define runbooks, alert ownership, or incident response.

Relevant references:

- `prd.md` FR-21 to FR-23 cover Admin observability and severity.
- `prd.md` FR-34 requires minimum alerts.
- `prd.md` section 6.3 defines operational observability.

Risk:

Showing a red status is not the same as operating safely. Operators need to know who is paged, what to pause, what to retry, when to notify customers, and how to avoid making an incident worse.

Required changes before handoff:

- Add minimum runbooks for `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required`, `profile_mismatch`, `kakao_ambiguous_room`, and `duplicate_blocked`.
- Define alert recipients, hours of coverage, escalation, and customer communication thresholds.
- Add per-channel pause controls and incident notes in Admin.
- Define audit review cadence for risky actions and failed dispatches.

### 10. Medium: Backup/restore is mentioned, but disaster recovery behavior is not product-defined

The PRD requires backup, retention, and restore rehearsal, but it does not define what "restored" means for a live automation system.

Relevant references:

- `prd.md` FR-34 requires backup, retention, restore rehearsal.
- `prd.md` section 6.2 mentions backup/restore procedures.

Risk:

Restoring a DB that contains old job queues, DeliveryRules, idempotency keys, and tokens can cause duplicate dispatch, stale jobs, or accidental live sending from a restored environment.

Required changes before handoff:

- Define RPO/RTO targets for MVP.
- Require restore rehearsal into a non-sending mode by default.
- Ensure restored environments cannot send Telegram/Kakao messages until explicitly activated.
- Require idempotency and DeliveryLog history to restore consistently.
- Define backup encryption, retention, deletion, and customer offboarding behavior.

### 11. Medium: Architecture readiness is not complete because several decisions are deferred

The addendum intentionally keeps technology choices and deployment posture as architecture inputs, not PRD commitments. That is reasonable for product scope, but implementation cannot start safely without a short architecture decision pass.

Relevant references:

- `addendum.md` suggests FastAPI, PostgreSQL, Redis-backed queue, Docker, AWS Seoul, Playwright workers, and Python Windows Agent.
- `prd.md` leaves Gmail token storage, Admin role depth, KakaoTalk policy, Local Agent ownership model, and legal review ownership open.

Risk:

Teams can implement incompatible pieces: Agent token model, queue semantics, secret storage, Admin authorization, data retention, and migration phases. Those decisions affect schema, API, tests, deployment, and operational controls.

Required changes before handoff:

- Produce lightweight ADRs before implementation for:
  - Agent authentication and job claim protocol.
  - Secret storage and token rotation.
  - Queue/job state model and idempotency.
  - Tenant isolation model.
  - KakaoTalk product policy.
  - Migration/cutover/rollback model.
  - Admin access control.
- Add an architecture-readiness checklist that blocks implementation stories lacking these decisions.

### 12. Medium: Success metrics do not include enough negative safety proof

The success metrics include duplicate/misdelivery prevention and operational safety checks, but they should be stricter for a system that can message real customers.

Relevant references:

- `prd.md` SM-2 covers duplicate/misdelivery prevention.
- `prd.md` SM-7 covers operational safety.
- `addendum.md` Verification Details lists useful unit, integration, E2E, and load smoke checks.

Risk:

The current metrics can be satisfied by a small happy-path or narrow simulated set. They do not require enough adversarial verification: wrong tenant, wrong profile, wrong Kakao room, stale Agent token, restored DB, double Agent claim, and crash-after-send.

Required changes before handoff:

- Add explicit negative test scenarios to MVP acceptance.
- Include cross-tenant denial tests.
- Include stale/rotated/revoked Agent token tests.
- Include old-path and new-path simultaneous-send prevention tests.
- Include restored-environment non-sending tests.
- Include Kakao ambiguous-room and focus-loss tests.

## Recommended Launch Gates

Before commercial launch:

1. Legal/policy review completed and documented for platform terms, customer consent, account delegation, assisted authentication, Gmail access, and KakaoTalk automation.
2. Secret/privacy model approved, including storage, encryption, redaction, retention, backup, restore, and diagnostic artifacts.
3. Admin and Agent access model implemented with MFA, scoped tokens, rotation/revocation, audit logs, and least privilege.
4. Migration runbook tested with canary, rollback, kill switch, and duplicate-send prevention.
5. KakaoTalk product policy decided and reflected in customer terms, quotas, UI, alerts, and support process.
6. Local Agent hardening checklist passed for Agent #1.
7. Job state/idempotency architecture proven with failure-mode tests.

Before implementation story breakdown:

1. ADRs exist for Agent protocol, queue semantics, tenant isolation, secret storage, Admin access, migration, and KakaoTalk policy.
2. PRD or architecture document converts all blocker/high-risk assumptions into explicit decisions.
3. Test plan includes negative safety scenarios, not only successful scheduling and dry-run cases.

## Positive Notes

- The PRD correctly rejects a naive "move everything to a powerful server" approach.
- The split into CrawlJob, Snapshot, Message, DispatchJob, and DeliveryLog is the right shape.
- The document correctly treats KakaoTalk PC automation as a constrained local operation.
- The migration-first posture is appropriate for a working brownfield desktop app.
- The emphasis on fail-closed behavior is the right product principle.

These strengths make the direction credible. The gap is that the most dangerous topics still need to become binding gates and architecture decisions before the team writes production code.
