# Implementation Contract

## Reuse And Replace

| Keep | Required change |
| --- | --- |
| Baemin parser/crawler | Wrap output in normalized Snapshot and keep fixture tests. |
| Coupang peak-dashboard parser | Add store/center validation and parser version recording. |
| Message renderer | Add `template_version`, tenant-level templates, and stored rendered results. |
| Telegram sender | Move to central webhook/sendMessage flow; remove per-Agent getUpdates polling. |
| Coupang Gmail 2FA logic | Add customer/mailbox token isolation, mailbox lock, and restricted-scope handling. |
| Existing tests | Merge parser/renderer regression tests with new domain/service tests. |

| Replace or shrink | Replacement |
| --- | --- |
| Crawling1..9 tab identity | Tenant, Customer, PlatformAccount, MonitoringTarget |
| Tab scheduler threads | Central scheduler, job queue, worker claim loop |
| Coupled `run_once` | CrawlJob -> Snapshot -> Message -> DispatchJob -> DeliveryLog |
| Plain JSON secrets | Secret Manager, Windows Credential Manager, or DPAPI-backed refs |
| Immediate Kakao send | KakaoSendJob queue and sender lock |

## Phase P0 - Baseline And Guardrails

| ID | Contract | Acceptance |
| --- | --- | --- |
| P0-01 | Tag current main or deployment branch, for example `baseline-local-ui-20260612`. | Tag and backup zip exist. |
| P0-02 | Document sanitized `runtime/state/ui_settings.json`, `config.json`, `.env.example`. | Sanitized config sample exists in docs. |
| P0-03 | Run full pytest and classify failures. | Test report is saved under `docs/qa/`. |
| P0-04 | Add or verify redaction utility for token/password/OTP logs. | Redaction unit test passes. |
| P0-05 | Create manual regression scenario for current 2 active tabs. | Baemin run, Coupang run, Telegram/Kakao test procedures are documented. |

## Phase P1 - Domain And Settings Refactor

| ID | Contract | Acceptance |
| --- | --- | --- |
| P1-01 | Add `customer_id`, `customer_name`, `platform_account_id`, `monitoring_target_id` to UiSettings; keep old tab name only as `legacy_alias`. | Existing `ui_settings.json` migrates automatically. |
| P1-02 | Change `state_subdir` from `crawlingN` to `targets/<monitoring_target_id>`. | Reordering tabs does not mix `last_message` or `run_lock`. |
| P1-03 | Save settings with atomic write: temp file, fsync, rename. | Forced shutdown test does not corrupt JSON. |
| P1-04 | Add log rotation for `run_errors.log` and `kakao_diagnostics.log`. | Logs rotate by size or date. |
| P1-05 | Use platform-neutral fields: `center_name`, `display_name`, `target_external_id`, `primary_url`. | Baemin and Coupang use the same Target model. |
| P1-06 | Separate secret values from normal settings; UI JSON keeps only refs. | New settings files contain no raw token/password. |

## Phase P2 - Collection/Dispatch Split

| ID | Contract | Acceptance |
| --- | --- | --- |
| P2-01 | Split `run_once` into `CrawlService`, `MessageRenderService`, and `DispatchService`. | Existing UI one-run result remains equivalent. |
| P2-02 | Define Snapshot with platform, target_id, collected_at, normalized_data, parser_version, quality_state. | Baemin/Coupang snapshot fixture tests pass. |
| P2-03 | Define Message with snapshot_id, template_version, text, text_hash. | Same snapshot creates same hash. |
| P2-04 | Define DeliveryRule that maps one target to multiple messenger channels. | One crawl fans out to at least two channels in test. |
| P2-05 | Implement DeliveryLog and idempotency key. | Re-running the same message does not send a duplicate. |
| P2-06 | Separate delivery failure from crawl failure. | Crawl success and Kakao failure are visible as separate states. |

## Phase P3 - Local Agent

| ID | Contract | Acceptance |
| --- | --- | --- |
| P3-01 | Create `rider_agent` package that imports existing crawler/parser/renderer. | `python -m rider_agent` runs. |
| P3-02 | Implement registration code entry and secure agent_id/token storage. | One-time code registers Agent in server. |
| P3-03 | Report heartbeat every 30-60 seconds. | Admin shows online/offline state. |
| P3-04 | Implement HTTPS outbound job polling, claim, and complete loop. | Agent works behind firewall without inbound port. |
| P3-05 | Implement BrowserProfileManager. | Profile/port duplicate use is prevented. |
| P3-06 | Split KakaoSenderWorker into a queue worker. | Kakao send is serialized in the same Windows session. |
| P3-07 | Add Windows Startup or Task Scheduler launch. | Agent starts after reboot and user login. |

## Phase P4 - Central Server

| ID | Contract | Acceptance |
| --- | --- | --- |
| P4-01 | Build FastAPI backend with `/health`, `/version`, `/metrics`. | Runs as Docker container. |
| P4-02 | Add PostgreSQL schema and Alembic migrations. | Empty DB migrates to all required tables. |
| P4-03 | Build Admin UI for customers, targets, agents, recent errors, auth-required filter. | Operator can inspect current state in web UI. |
| P4-04 | Build scheduler with interval and jitter. | Customers do not all run at the same second. |
| P4-05 | Build queue abstraction. | `QueueBackend` interface tests pass and implementation can move to Redis/SQS. |
| P4-06 | Build Telegram webhook with secret header validation. | `/register` works without getUpdates polling. |
| P4-07 | Add audit log. | Admin changes to customer/secret/channel settings are traceable. |

## Phase P5 - Onboarding And Authentication

| Contract | Acceptance |
| --- | --- |
| Admin creates tenant and setup code, then selects plan quotas for targets and messenger channels. | Tenant has quota and setup state. |
| Platform accounts are separated by platform and can own multiple targets. | Same customer can have multiple Baemin/Coupang targets. |
| Telegram registration uses `/register <code>`. | `chat_id` and optional `message_thread_id` are stored automatically. |
| Kakao registration requires unique room name and test send before activation. | Duplicate room name blocks activation. |
| Baemin auth opens target browser for a person to complete phone verification. | AUTH_REQUIRED can move back to ACTIVE after detection. |
| Coupang auth uses Gmail OAuth and email 2FA recovery. | Gmail reauth and CAPTCHA are separate states. |
| Test crawl and all-channel test message are required before ACTIVE. | Customer cannot activate without successful tests. |

## Phase P6 - Operations Hardening

| Contract | Acceptance |
| --- | --- |
| Add schedule jitter, exponential backoff, platform circuit breaker, parser canary, worker sharding, queue lag alerts, version rollout, and customer impact reports. | Parser failures or queue spikes degrade service without job storms or duplicate sends. |

## Migration Contract

- Back up existing `runtime/state/ui_settings.json`.
- Read the old `crawlings` array and classify only active tabs as target candidates.
- Issue `tenant_id`, `platform_account_id`, and `monitoring_target_id` for each active tab.
- Copy old `runtime/state/crawlingN` folders to `targets/<monitoring_target_id>` and do not delete originals.
- Seed DeliveryLog dedup from old `last_message` hash.
- Run one dry-run with actual sending disabled.
- Compare old and new rendered messages.
- Activate new DeliveryRules only after operator approval.
