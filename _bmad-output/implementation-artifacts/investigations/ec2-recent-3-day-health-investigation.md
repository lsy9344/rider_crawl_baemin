# Investigation: EC2 recent 3-day health

## Hand-off Brief

1. EC2 host health is good for the audited 72-hour window: instance is running, AWS status checks stayed 0, no kernel OOM was found, and memory/swap/disk headroom is healthy.
2. Application operations are not fully clean: `targets_critical` had repeated ALARM periods, and the current `/metrics/operational` snapshot still reports `targets_critical=1`.
3. The current risk is not host capacity; it is that the COUPANG target `6b8fd18e` is not being automatically scheduled because its account auth state is `UNKNOWN`.

## Case Info

| Field | Value |
| --- | --- |
| Date opened | 2026-06-29 KST |
| Window | 2026-06-26 00:06 KST to 2026-06-29 00:06 KST |
| System | EC2 `i-0e6a710a505e6b3c4`, `ap-northeast-2`, `t4g.small` |
| Status | Concluded |

## Confirmed Evidence

| Area | Evidence |
| --- | --- |
| EC2 status | `running`; system status `ok`; instance status `ok`; no scheduled events. |
| SSM | Online, last ping 2026-06-29 00:03 KST. |
| AWS status checks | `StatusCheckFailed`, `StatusCheckFailed_Instance`, `StatusCheckFailed_System` all max `0.0` across 864/864 five-minute points. |
| Host uptime | Up 3 days 11:34 at 2026-06-29 00:06 KST; no reboot inside the audited 72-hour window. |
| Kernel OOM | No `out of memory`, `oom-killer`, or `killed process` lines in kernel journal for the window. |
| Current memory | 1836 MiB total, 1095 MiB available; 2047 MiB swap total, 25 MiB used. |
| CloudWatch memory | `HostMemAvailablePercent` min 53.24%, average 58.95%, latest 59.39%. |
| CloudWatch swap | `HostSwapUsedPercent` max 1.25%, latest 1.25%. |
| Disk | Root volume 19 GiB, 11 GiB used, 8.0 GiB available, 57% used. |
| CPU | Average 4.97%, max five-minute point 25.12%. |
| Docker | app containers healthy; all running containers `OOMKilled=false`. |
| App health | `GET /health` returned `{"status":"ok"}`. |
| Metrics pusher | `rider-metrics.service` active since 2026-06-25; 8 one-minute `curl failed` events in 72h, then recovery. |
| Alarms currently | All `rider-server-*` CloudWatch alarms are currently `OK`. |
| Alarm history | `api-error-rate-coupang`, `auth-required`, and `targets-critical` had ALARM transitions during the window. |
| Current operational metrics | `/metrics/operational` at 2026-06-29 00:11 KST: `agents_offline=0`, `oldest_heartbeat_age_seconds=12`, `targets_total=1`, `targets_critical=1`, `auth_required_count=0`, `queue_lag=0`, crawl error rates 0, telegram errors 0. |
| Current target state | Target `6b8fd18e` / account `3e703327` is `COUPANG`, active, 2-minute interval, account `auth_state=UNKNOWN`, last OK snapshot 2026-06-28 23:52:47 KST. |
| Scheduler | Recent scheduler logs repeatedly show `enqueued=false`, `reason=AUTH_STATE_UNKNOWN`; no pending/claimed/running/retry jobs currently exist. |

## Alarm Durations

| Alarm | ALARM intervals | Total ALARM time | Longest interval |
| --- | ---: | ---: | ---: |
| `rider-server-api-error-rate-coupang` | 13 | 35 min | 9 min |
| `rider-server-auth-required` | 11 | 472 min | 354 min |
| `rider-server-targets-critical` | 6 | 2730 min | 2363 min |

## Conclusion

Confidence: High.

The EC2 host did not show capacity or availability trouble in the last 72 hours. The previous memory-risk remediation appears effective: memory headroom is above 50%, swap use is low, CPU is low, root disk has 8 GiB free, status checks stayed healthy, and there were no OOM events.

The remaining operational risk is application freshness. One active COUPANG target is currently critical because normal collection is stale, and the scheduler is intentionally blocking new scheduled crawl jobs while the account auth state is `UNKNOWN`. This can stop fresh result delivery even though the server itself is healthy.
