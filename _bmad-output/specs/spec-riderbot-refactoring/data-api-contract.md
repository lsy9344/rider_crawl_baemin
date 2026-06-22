# Data And API Contract

## Core Domain Models

| Model | Contract |
| --- | --- |
| Tenant | Subscribed customer organization. |
| Subscription | Plan, status, billing period, quotas, and execution gate. |
| PlatformAccount | Baemin or Coupang login account; uses secret refs, not raw credentials. |
| MonitoringTarget | Actual collection unit: platform account plus center/store/url/interval/status. |
| BrowserProfile | Target-bound Chrome profile state assigned to an Agent. |
| MessengerChannel | Telegram chat/topic or Kakao room mapping. |
| DeliveryRule | Mapping from target snapshot/message to one or more channels. |
| Snapshot | Normalized crawl result with parser version and quality state. |
| Message | Rendered text derived from a Snapshot and template version. |
| DeliveryLog | Delivery status, dedup key, errors, and sent time. |
| AuthSession | Login/auth recovery state and reason. |
| Agent | Worker node identity, capacity, OS, version, heartbeat, status. |
| SecretRef | Reference to a secret value stored outside normal config/database fields. |

## Required Tables

| Table | Required fields |
| --- | --- |
| `tenants` | id, name, status, created_at |
| `subscriptions` | tenant_id, plan, status, current_period_end, quotas |
| `platform_accounts` | id, tenant_id, platform, label, username_ref, password_ref, auth_state |
| `monitoring_targets` | id, tenant_id, platform_account_id, name, external_id, url, interval_minutes, status |
| `browser_profiles` | id, agent_id, target_id, profile_path_ref, cdp_port, state |
| `messenger_channels` | id, tenant_id, messenger, telegram_chat_id, thread_id, kakao_room_name, state |
| `delivery_rules` | id, target_id, channel_id, template_id, enabled, send_only_on_change |
| `snapshots` | id, target_id, collected_at, normalized_json, parser_version, quality_state |
| `messages` | id, snapshot_id, template_version, text_hash, text_redacted_preview |
| `delivery_logs` | id, message_id, channel_id, status, dedup_key, error_code, sent_at |
| `agents` | id, name, machine_id, version, os, status, last_heartbeat_at, capacity_json |
| `jobs` | id, type, target_id, agent_id, status, run_after, attempts, error_code |
| `auth_sessions` | id, account_id, state, reason, requested_at, resolved_at |
| `audit_logs` | actor_id, action, target_type, target_id, diff_redacted, created_at |

## Agent API

### `POST /v1/agents/register`

Request:

```json
{
  "registration_code": "string",
  "machine_fingerprint": "string",
  "hostname": "string",
  "os": "string",
  "agent_version": "string"
}
```

Response:

```json
{
  "agent_id": "string",
  "agent_token": "string",
  "tenant_scope": "object",
  "config_version": "string"
}
```

### `POST /v1/agents/heartbeat`

Request contains `agent_id`, metrics, capabilities, active_jobs, kakao_status, and browser_profiles. Response contains `server_time`, `config_version`, and commands.

### `POST /v1/jobs/claim`

Request contains `agent_id`, capabilities, and `max_jobs`. Response returns claimed jobs.

### `POST /v1/jobs/{job_id}/events`

Request contains `event_type`, severity, `message_redacted`, and artifact refs.

### `POST /v1/jobs/{job_id}/complete`

Request contains status, result_json, error_code, `error_message_redacted`, and metrics.

## Admin API/UI

- Customer create/update/suspend/resume.
- Platform account create, auth state view, auth browser command.
- MonitoringTarget create, schedule setting, manual run.
- MessengerChannel register/verify/test message.
- DeliveryRule manage.
- Agent assignment, status, version, capacity.
- Recent errors, last success, queue lag, auth-required filters.
- Audit log view.

## State Machines

### Customer lifecycle

```text
LEAD
 -> SIGNED_UP
 -> PAYMENT_ACTIVE
 -> SETUP_PENDING
 -> PLATFORM_AUTH_PENDING
 -> MESSENGER_VERIFY_PENDING
 -> TEST_RUNNING
 -> ACTIVE
 -> DEGRADED
 -> AUTH_REQUIRED
 -> SUSPENDED
```

### Subscription execution gate

| State | System behavior |
| --- | --- |
| `PAYMENT_ACTIVE` | Normal scheduling and delivery. |
| `PAYMENT_FAILED_GRACE` | Continue collection/delivery but show Admin warning and allow customer alert. |
| `SUSPENDED` | Stop creating new CrawlJob/DispatchJob while preserving config and profiles. |
| `CANCELLED` | Revoke secrets and archive/delete profiles after policy-defined retention. |

### Baemin auth state

```text
UNKNOWN
ACTIVE
AUTH_REQUIRED
USER_ACTION_PENDING
AUTH_VERIFIED
CENTER_MISMATCH
BLOCKED_OR_CAPTCHA
```

Baemin automation scope is login-page opening, auth-needed detection, completion detection, center identity verification, alerting, and resume. It must not acquire or bypass phone authentication codes.

### Coupang Gmail 2FA

| Case | Required behavior |
| --- | --- |
| OAuth onboarding | Customer/operator approves Gmail OAuth once; token is stored by mailbox_id. |
| Token storage | MVP prefers Agent-local DPAPI or Windows Credential Manager; server stores refs only. |
| Mail search | Query only mail received after auth request time, with from/subject/query/customer filters. |
| Mailbox lock | Do not process two Coupang auth requests for the same mailbox_id at once. |
| CAPTCHA/abnormal login | Stop recovery and enter USER_ACTION_REQUIRED. |
| Refresh failure/revoked grant | Enter GMAIL_REAUTH_REQUIRED. |

## Dedup Key

Delivery deduplication must include enough fields to prevent duplicate sends while allowing different channels/templates to receive the same snapshot:

```text
monitoring_target_id
+ messenger_channel_id
+ snapshot_collected_at
+ template_version
+ message_hash
```
