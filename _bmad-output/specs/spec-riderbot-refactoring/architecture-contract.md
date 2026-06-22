# Architecture Contract

## Target Shape

최종 구조는 클라우드 중앙 서버와 Windows Local Agent pool이다. 클라우드는 고객/구독/설정/작업/상태/로그/Telegram을 관리하고, Windows Agent는 실제 Chrome profile, 배민/쿠팡 화면 수집, 쿠팡 Gmail 2FA, KakaoTalk PC 앱 전송을 담당한다.

```text
Admin / Operator
  -> Cloud Control Plane
     - Backend API / Admin API / Agent API
     - Scheduler / Job Queue
     - Customer, subscription, config, status, log DB
     - Telegram webhook / dispatcher
     - Secret references
  -> Windows Local Agent #1..N
     - BrowserProfileManager
     - Baemin/Coupang crawl workers
     - Coupang Gmail 2FA recovery
     - Kakao sender worker
     - Heartbeat/status/log reporting
  -> Telegram groups/topics and KakaoTalk rooms
```

## Responsibility Split

| Responsibility | Cloud | Local Agent |
| --- | --- | --- |
| Customer/subscription/config management | Owns | Pulls assigned config |
| Schedule calculation | Owns | Receives jobs |
| Baemin/Coupang Chrome execution | Does not run | Owns |
| Baemin phone authentication | Tracks state and alerts | Opens profile browser and detects completion |
| Coupang Gmail 2FA | Tracks policy/state | Uses mailbox token and performs recovery |
| Telegram delivery | Owns | Fallback only if explicitly added later |
| KakaoTalk delivery | Creates job and tracks state | Owns UI automation |
| Logs/artifacts | Stores sanitized data | Uploads sanitized events/artifacts |

## Cloud MVP

| Area | Contract |
| --- | --- |
| Compute | AWS EC2 Ubuntu LTS with Docker Compose for api/admin/scheduler containers; Dockerfile and env separation must allow later ECS/Fargate move. |
| Database | Amazon RDS PostgreSQL with at least 7-day retention before production; point-in-time recovery and manual snapshot policy required. |
| Object storage | S3 for sanitized screenshots, sanitized HTML fixtures, and exports; raw sensitive HTML is forbidden by default. |
| Secrets | AWS Secrets Manager for Telegram bot token, JWT signing key, DB password, external API keys; customer Gmail token stays agent-local for MVP unless security review changes this. |
| Network | HTTPS only; Admin uses 2FA or VPN/IP allowlist; Agent API uses token auth. |
| Monitoring | CloudWatch logs/alarms plus API error rate, agent offline count, queue lag, CPU/disk metrics. |
| Backup | RDS automated backup, S3 lifecycle, infra config backup, and restore rehearsal procedure. |

## Server Processes

| Process | Owns |
| --- | --- |
| `backend-api` | Admin API, Agent API, Telegram webhook, auth/session/status APIs |
| `scheduler` | MonitoringTarget interval calculation, jitter, CrawlJob creation, subscription gating |
| `telegram-dispatcher` | Message to Telegram sendMessage, retry/backoff, DeliveryLog recording |
| `admin-ui` | Customer/target/channel/agent/status screens, auth-required filters, test send, manual rerun |

## Scheduler Rules

- Query due targets by `monitoring_targets.next_run_at`.
- Assign deterministic jitter in the `0..interval` range when a target is created.
- Do not create new CrawlJob or DispatchJob when subscription is inactive or suspended.
- Do not create new jobs for a platform while that platform's global circuit breaker is open.
- Consider agent capacity and target affinity when assigning work.
- Never retry every 5 seconds forever; apply error-code-specific backoff.
- Use idempotent job creation so repeated scheduler ticks do not create duplicate due work.

## Local Agent Runtime

MVP installs Agent #1 on the current ordinary Windows PC. KakaoTalk UI automation needs an interactive desktop, so an Agent that only runs as a Windows service in Session 0 is not acceptable. Use a tray app or console app launched after user login, with Task Scheduler or Startup registration.

```text
C:\RiderBot\
  agent\
    rider_agent.exe
    version.json
    data\agent_config.json
    profiles\<tenant_id>\<target_id>\
    logs\agent.log
    logs\kakao_sender.log
    logs\browser_manager.log
    secrets\
```

`agent_config.json` must not contain raw secret values. If temporary beta storage is unavoidable, use DPAPI-encrypted local files.

## Agent Loop

```text
startup:
  load_local_agent_identity()
  validate_agent_token()
  start_heartbeat_thread()
  start_kakao_sender_worker_if_enabled()

main_loop:
  while running:
    config = pull_remote_config()
    job = claim_next_job(capabilities, capacity)
    if job is None:
      sleep(short_poll_interval)
      continue
    emit_job_started(job)
    result = execute_job(job)
    upload_sanitized_artifacts_if_any(result)
    complete_job(job, result)
```

## BrowserProfileManager

| Function | Contract |
| --- | --- |
| Profile creation | Create an independent User Data Directory per target_id; never reuse the default Chrome profile. |
| Port allocation | Allocate an available `127.0.0.1:<port>` and report `profile_id` and `cdp_port` state to the server. |
| Chrome launch | Launch with `--remote-debugging-port` and `--user-data-dir`; keep-alive or close policy is target config. |
| Health check | Report CDP endpoint response, login URL state, page count, and approximate memory state. |
| Duplicate prevention | Use lock file and process check so the same user_data_dir is not opened twice. |
| Recovery | Restart Chrome when CDP is unavailable; move to AUTH_REQUIRED when login is needed instead of retrying forever. |

## Agent Job Types

| Type | Location | Contract |
| --- | --- | --- |
| `CRAWL_BAEMIN` | Agent | Collect Baemin achievement data with the target Chrome profile and upload Snapshot. |
| `CRAWL_COUPANG` | Agent | Collect Coupang peak-dashboard data and try Gmail 2FA recovery when login expired. |
| `AUTH_CHECK` | Agent | Check login state only and report AUTH_REQUIRED or ACTIVE. |
| `OPEN_AUTH_BROWSER` | Agent | Open the target profile browser for human authentication and report state. |
| `KAKAO_SEND` | Agent | Send server-rendered message text to KakaoTalk room through serialized UI automation. |
| `CAPTURE_DIAGNOSTIC` | Agent | Upload sanitized screenshot or log for an error case. |
