# Operations, Security, And Test Contract

## Secret Storage

| Secret | Storage contract |
| --- | --- |
| Telegram bot token | AWS Secrets Manager; DB stores only `secret_ref`; rotation/revoke procedure required. |
| Coupang password | Prefer AWS Secrets Manager or Agent DPAPI; Admin must not display plaintext after save. |
| Gmail OAuth token | MVP stores Agent-local in DPAPI/Windows Credential Manager; customer/mailbox isolation required. |
| Agent token | Agent-local secure store; server-side revoke must work. |
| Chrome profile | Agent-local disk; BitLocker recommended; server stores profile id/ref, not raw sensitive path as primary identity. |

## Log And Artifact Redaction

- Never log password, token, refresh token, authorization code, OTP, full phone number, or full email.
- Customer and center names may exist in operator logs when needed, but external diagnostic artifacts need masking options.
- Raw HTML storage is forbidden by default; only sanitized HTML may be stored for parser failure analysis.
- Kakao error screenshots may expose chat information, so upload requires masking or operator approval.
- Error events must use `message_redacted` and `error_message_redacted`.

## Monitoring Metrics

| Metric | Warning rule | Required action |
| --- | --- | --- |
| `agent_last_heartbeat` | Missing for more than 2 minutes | Agent offline alert. |
| `target_last_success_at` | Over `interval x 2`; critical over `interval x 4` | Show target warning/critical. |
| `auth_required_count` | At least 1 | Alert operator and show auth-required list. |
| `kakao_queue_lag_seconds` | Repeatedly over 120 seconds | Add sender capacity or spread delivery interval. |
| `crawl_error_rate_by_platform` | Over 30% in recent 15 minutes | Consider platform circuit breaker. |
| `telegram_send_error_rate` | Sudden spike in recent 10 minutes | Check bot token and chat permissions. |
| `gmail_reauth_required_count` | At least 1 | Request Gmail reauthorization. |

## Deployment

- CI must run lint, tests, and build.
- Backend/Admin Docker images must be tagged.
- DB migration must run only after backup confirmation.
- Agent updates use a version manifest.
- Agent update must wait until no job is running and keep rollback binary.
- Production deploy needs staging smoke test using fake or fixture-based Baemin/Coupang targets.

## Tests

| Test | Scope | Acceptance |
| --- | --- | --- |
| Unit | domain, parser, renderer, dedup, redaction | All pass in CI. |
| Integration | Agent API, job lifecycle, Telegram sender mock, Gmail mock | Failure, retry, and dedup behavior verified. |
| E2E dry-run | One existing active Baemin target and one existing active Coupang target | Collection/render/store succeeds with actual sending disabled. |
| Messenger test | Telegram test chat and Kakao test room | One test message succeeds and creates DeliveryLog. |
| Auth test | Baemin AUTH_REQUIRED detection and Coupang Gmail 2FA fixture | State transitions are correct. |
| Load smoke | 100 fake targets scheduled | Jitter and queue work without job storm. |

## Equipment And Scaling

Do not buy a high-performance server before the architecture split is done. Use current ordinary Windows PC for P0-P4 and make it Agent #1. Move to dedicated hardware only when measured triggers are hit.

| Trigger | Rule | Action |
| --- | --- | --- |
| Chrome load | CPU over 70% or RAM over 80% for 3 peak-time days | Buy or split to dedicated worker PC. |
| Target count | Active monitoring targets exceed 20-30 | Dedicate current PC and consider 64GB RAM worker. |
| Kakao lag | Kakao queue lag repeatedly over 120 seconds | Add Kakao sender PC/account or spread sends. |
| Operational risk | Current PC is also used for work, reboot, sleep, or focus-heavy tasks | Move to dedicated Windows worker. |
| Operating hours | Peak-time no-downtime service is required | Use dedicated hardware with UPS, wired network, and remote management. |

| Scale | Contract |
| --- | --- |
| Up to 30 targets | Current PC or 32GB RAM Windows PC may work if Kakao volume is low. |
| 30-100 targets | 8+ cores, 64GB RAM, NVMe, Windows 11 Pro, wired internet, UPS. |
| 100+ targets | Prefer several worker PCs and target sharding over one large machine. |
| Large Kakao volume | Separate Windows PC/session/account and sender pool; same session parallel sending remains forbidden. |

## Key Risks

| Risk | Response |
| --- | --- |
| Baemin phone authentication | Human-in-the-loop auth UX; no bypass automation. |
| KakaoTalk UI automation | Sender pool, FIFO queue, unique room name, test verification. |
| Baemin/Coupang page changes | Parser canary, circuit breaker, sanitized fixture capture. |
| Chrome session growth | BrowserProfileManager, target sharding, capacity reporting. |
| Gmail token confusion | Mailbox-level token isolation, lock, query filtering. |
| Plaintext secret leakage | Secret refs, OS credential store, redaction tests. |
| Single worker failure | Cloud/Agent split and backup worker path. |
| Duplicate messages | DeliveryLog and idempotency key. |
| Failed-payment customers still running | Subscription state gates scheduler. |
| Terms, delegation, and consent risk | Review terms, consent, and security policy before paid launch. |

## Forbidden Behaviors

- Increase tabs from 9 to 100 as a scaling strategy.
- Build unattended Baemin phone authentication or bypass.
- Run two Kakao sends in parallel in the same Windows session.
- Share Gmail token between customers.
- Store token/password/OTP in logs, DB text fields, screenshots, config files, or error messages.
- Let cloud connect directly to local Chrome CDP ports.
- Retry parser failures rapidly without backoff or circuit breaker.
