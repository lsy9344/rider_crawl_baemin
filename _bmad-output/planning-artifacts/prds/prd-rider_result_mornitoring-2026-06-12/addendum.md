# PRD Addendum

This addendum preserves implementation-level details and source-derived depth that should inform architecture and implementation planning without overloading the main PRD.

## Source Inputs

- `docs/refactoring/research.md`
- `docs/refactoring/detailed_work_order.md`

## Technical Direction To Preserve

- The current app is a working Python/tkinter desktop automation app. The refactor should preserve current behavior while separating responsibilities.
- The main operational bottlenecks are not raw CPU alone. They are login session isolation, Chrome profiles, CDP ports, Baemin phone verification, Coupang Gmail 2FA, KakaoTalk PC UI automation, duplicate delivery prevention, and operational visibility.
- The recommended direction is a central cloud server plus Windows local agent/work node model. The local agent keeps browser and KakaoTalk desktop constraints near the machine that can satisfy them.
- The current `크롤링1~9` tab model should become an ID-based model around customer, tenant, monitoring target, platform account, browser profile, messenger channel, delivery rule, auth session, job run, delivery log, agent, and secret reference.
- The current `run_once()` boundary should split into crawl, render, and dispatch responsibilities while preserving a compatibility path for current one-shot behavior during migration.
- Telegram should move toward central webhook/registration/send management. KakaoTalk should be treated as a serialized sender pool because it depends on a real Windows desktop session, foreground/focus behavior, and exact chatroom validation.

## Implementation Details Better Suited For Architecture

- Suggested service boundaries include Domain, Application, Infrastructure, Worker, and Interface layers.
- Suggested workers/services include `CrawlService`, `MessageRenderService`, `DispatchService`, local agent job polling/claim/complete, browser profile management, Telegram dispatcher, and Kakao sender.
- Suggested data flow is `CrawlJob -> Snapshot -> Message -> DispatchJob`, with fan-out from one collected snapshot to multiple destination channels.
- Suggested reliability mechanisms include jittered scheduling, retry/backoff, idempotency keys, delivery logs, circuit breaker behavior, and queue lag monitoring.
- Suggested MVP technology stack from source docs includes FastAPI, PostgreSQL, Redis-backed queue, Docker-based cloud deployment, Playwright workers, and a Python Windows agent. These are architecture inputs, not PRD-level product commitments unless later confirmed.
- Suggested deployment posture is AWS Seoul region, HTTPS, Docker, PostgreSQL, managed secret storage, and outbound-only local agent communication.
- Suggested local-agent constraint: KakaoTalk automation needs an interactive Windows desktop session and should not be treated as a Session 0 Windows service-only workload.

## External Pattern Notes

- Local agent products commonly use a control-plane pattern where the agent polls or receives configuration safely, then reports local state back. This supports the PRD's outbound-only Local Agent requirement. Public references reviewed: Datadog Remote Configuration, Fleet agent configuration, Tailscale control/data plane.
- Browser automation should treat customer/account Chrome profile isolation as a product requirement, not just an implementation detail. Public Playwright documentation notes CDP connection is lower fidelity than Playwright protocol, and Chrome remote debugging behavior increasingly requires separate user data directories.
- KakaoTalk PC app automation should be treated as a constrained or best-effort channel. Public Kakao safety/policy material warns about abnormal usage and environment/pattern restrictions, so strong commercial SLA claims should wait for official channel/API evaluation.
- Queue-based execution should assume retries and duplicate execution can happen. Public Celery and Temporal documentation both make idempotency important for retryable work.
- Minimal observability should attach identifiers such as `tenant_id`, `agent_id`, `job_id`, `browser_profile`, `messenger_target`, and `result_hash` to events/logs so operations can trace failures without exposing secrets.

## Verification Details From Source Inputs

- P0 should freeze a baseline through branch/tag, settings backup, pytest result, redaction tests, and manual regression scenarios.
- Baseline behavior should include at least one active Baemin target and one active Coupang target, plus Telegram and Kakao test flows.
- Unit coverage should include domain models, parser behavior, renderer behavior, deduplication, and log redaction.
- Integration coverage should include agent API, job lifecycle, Telegram/Gmail mocks, retries, and duplicate prevention.
- E2E dry-run should collect, render, and persist results without real outbound delivery for representative Baemin and Coupang targets.
- Load smoke should include scheduling around 100 fake targets, plus separate smoke checks for messenger delivery and auth state transitions.

## Operational Thresholds To Revisit

- Last successful crawl older than 2x schedule interval may become warning; older than 4x may become critical.
- Kakao queue lag over about 120 seconds may trigger sender scaling or customer-facing expectation changes.
- Worker capacity should be measured from Chrome memory, average crawl duration, Kakao average send duration, login expiry frequency, and machine stability.

## Scale Bands From Source Inputs

- 10 monitoring targets or fewer: current Windows PC plus backup/remote access/auto restart may be enough while structure is being separated.
- 30-100 monitoring targets: central server plus one or more Windows work nodes becomes important; tab UI should no longer be the operating model.
- 100-500 monitoring targets: worker pool, sender pool, capacity-based assignment, queue lag alarms, and platform-level circuit breakers become operational requirements.
- 1000+ monitoring targets: sharding, stronger queue discipline, multiple work-node groups, and formal capacity planning are outside MVP but should shape architecture extension points.

## Auth Failure Taxonomy To Preserve

- Baemin: active, auth required, user action pending, auth verified, active again, auth timeout.
- Coupang/Gmail: token expired or revoked, wrong password, verification email delayed, CAPTCHA or unexpected challenge, mailbox lock conflict, newest-email misclassification, repeated auth request loop.

## Windows Agent Hardening Notes

- Kakao sender nodes need an interactive user session. A Session 0 service-only implementation is not enough for KakaoTalk PC automation.
- PC reboot recovery should rely on user-login autostart or Task Scheduler behavior that restores Agent heartbeat.
- Architecture should decide BitLocker, Windows Credential Manager/DPAPI, profile folder permissions, remote access 2FA, and Agent token revoke procedure.
- Crawler-only nodes and Kakao sender nodes may have different hardening and runtime requirements.

## Open Addendum Items

- Decide whether Gmail tokens stay local to the agent through Windows DPAPI or move into centralized secret storage after security review.
- Decide whether KakaoTalk delivery is included in the base product, sold with quota limits, or sold as a premium/sender-pool option.
- Evaluate an official Kakao channel/API alternative before promising a high-reliability KakaoTalk delivery SLA.
- Decide whether the initial release includes onboarding and subscription controls or stops at P0-P4 operational MVP.
- Assign ownership for platform terms, customer consent, account delegation, and authentication-assistance policy review.

## ADR Topics Required Before Implementation

- Agent authentication and scoped job claim protocol.
- Secret custody model and token rotation/revocation.
- Queue state model, lease semantics, retry policy, idempotency, and crash-after-send behavior.
- Tenant isolation across DB, API, Admin, logs, queues, backups, and Agent assignment.
- Migration cutover, rollback, kill switch, and simultaneous-send prevention.
- Admin access control, MFA, role model, and audit log fields.
- KakaoTalk product policy, quotas, SLA language, and sender runtime constraints.
