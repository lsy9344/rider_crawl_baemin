# Investigation: EC2 memory OOM

## Hand-off Brief

1. **What happened.** Confirmed: the EC2 instance `i-0e6a710a505e6b3c4` hit a global Linux OOM event on 2026-06-22 15:57:04 UTC, and the kernel killed `fwupd`, not an app container.
2. **Where the case stands.** Confirmed: the server is a `t4g.micro` with about 906 MiB usable RAM, no swap, local PostgreSQL, four Python app services, Docker/containerd, CloudWatch pusher, SSM/snap services, and a GitHub Actions runner sharing one host.
3. **What's needed next.** Apply memory hardening: add swap or move to a larger instance, remove nonessential host services, move/disable the GitHub Actions runner on production, and add memory metrics/alarms.

## Case Info

| Field | Value |
| --- | --- |
| Ticket | N/A |
| Date opened | 2026-06-23 |
| Status | Active |
| System | AWS EC2 `i-0e6a710a505e6b3c4`, `t4g.micro`, Ubuntu 24.04 ARM64, Docker Compose |
| Evidence sources | SSH read-only diagnostics, AWS CLI, CloudTrail, local deploy code and docs |

## Problem Statement

User reported that the AWS EC2 instance recently died from memory shortage and asked why so many programs were running.

## Evidence Inventory

| Source | Status | Notes |
| --- | --- | --- |
| EC2 kernel journal | Available | OOM event found at 2026-06-22 15:57:04 UTC. |
| AWS CloudTrail | Available | `RebootInstances` by `noah_host` at 2026-06-23 09:43:23 KST. |
| Docker state | Available | Current app containers are not marked `OOMKilled`. |
| Current process list | Available | GitHub Actions runner and several default Ubuntu services are running with the app stack. |
| Local deploy config | Available | Compose defines PostgreSQL plus backend, scheduler, queue recovery, telegram dispatch. |
| Memory CloudWatch metric | Missing | The custom pusher exports app metrics, not host memory usage. |

## Timeline of Events

| Time | Event | Source | Confidence |
| --- | --- | --- | --- |
| 2026-06-22 15:57:04 UTC | Kernel global OOM occurred. `python` was the allocating process, and `fwupd` was killed. | `journalctl -k -b -1` | Confirmed |
| 2026-06-22 15:57:04 UTC | Swap was absent: `Free swap = 0kB`, `Total swap = 0kB`. | `journalctl -k -b -1` | Confirmed |
| 2026-06-23 00:43:23 UTC | EC2 reboot requested through AWS API. | CloudTrail `RebootInstances` by `noah_host` | Confirmed |
| 2026-06-23 00:44:03 UTC | Instance booted again. | `journalctl --list-boots`, `last -x` | Confirmed |

## Confirmed Findings

### Finding 1: The host is undersized for the number of resident processes

**Evidence:** `deploy/terraform/variables.tf:13-16`, `README.md:108`, `deploy/docker-compose.yml:8`, `deploy/docker-compose.yml:44`, `deploy/docker-compose.yml:81`, `deploy/docker-compose.yml:112`, `deploy/docker-compose.yml:143`

**Detail:** Terraform defaults to `t4g.micro`, and project docs say the current operating model is single EC2 plus local PostgreSQL plus `backend-api`, `scheduler`, `queue-recovery`, and `telegram-dispatch`. That is a valid low-cost PoC shape, but it gives only about 906 MiB usable RAM on this instance.

### Finding 2: The OOM event was real, global, and had no swap safety net

**Evidence:** SSH command `sudo journalctl -k -b -1 --since '2026-06-22 15:56:30 UTC' --until '2026-06-22 15:58:00 UTC' --no-pager`

**Detail:** Kernel log shows `python invoked oom-killer`, `Out of memory: Killed process 427101 (fwupd)`, `Free swap = 0kB`, and `Total swap = 0kB`. At the same moment, free RAM was below kernel watermarks.

### Finding 3: App containers were not the direct OOM victim

**Evidence:** SSH `docker inspect` on current containers.

**Detail:** `rider-telegram-dispatch-1`, `rider-scheduler-1`, `rider-queue-recovery-1`, `rider-backend-api-1`, and `rider-db-1` all report `OOMKilled=false`. The killed process in the OOM event was the host service `fwupd`.

### Finding 4: A GitHub Actions runner is running on the production EC2

**Evidence:** SSH `systemctl list-units --type=service --state=running --no-pager`; SSH `ps -eo ... --sort=-rss`.

**Detail:** The service `actions.runner.lsy9344-rider_crawl_baemin.ip-10-50-1-8.service` is active. Current RSS for `Runner.Listener` was about 69 MiB. At the OOM event, `Runner.Listener` had RSS 22,283 pages, about 87 MiB, and a `node` child was also present.

### Finding 5: DB pool settings allow more resident PostgreSQL processes than the micro instance comfortably needs

**Evidence:** `deploy/docker-compose.yml:64-65`, `deploy/docker-compose.yml:87-88`, `deploy/docker-compose.yml:118-119`, `deploy/docker-compose.yml:150-151`, `docs/runbooks/crawl-scale-runbook.md:18-29`

**Detail:** Each Python process defaults to `RIDER_DB_POOL_SIZE=5` and `RIDER_DB_MAX_OVERFLOW=10`. The runbook explicitly calculates this across API worker, scheduler, queue recovery, and telegram dispatch. Current PostgreSQL had 14 connections, with many idle backend processes.

### Finding 6: Monitoring did not cover the failure mode

**Evidence:** `deploy/cloudwatch/push_metrics.sh`; SSH `systemctl status rider-metrics.service`; previous boot journal.

**Detail:** The pusher scrapes `/metrics/operational` app metrics. It does not emit host memory/swap pressure. Before reboot it repeatedly failed with `NoCredentials`, creating a monitoring gap. After reboot it was pushing metrics again.

## Deduced Conclusions

### Deduction 1: The root cause is capacity/operations, not a tight app memory leak

**Based on:** Findings 1, 2, 3, 4, and current `docker stats`.

**Reasoning:** Current app containers are modest individually, and no app container was marked OOM-killed. The OOM event happened because total host memory pressure crossed the threshold on a 1 GiB instance with no swap. Host services and the GitHub runner were part of the resident set.

**Conclusion:** The immediate root cause is insufficient memory headroom on a small single-host deployment, amplified by unnecessary host services and no swap.

### Deduction 2: The June 23 reboot was user/API initiated, not directly caused by the kernel OOM

**Based on:** CloudTrail `RebootInstances` at 2026-06-23 09:43:23 KST and the OOM at 2026-06-23 00:57:04 KST.

**Reasoning:** The OOM event happened several hours before the reboot. Logs show the OOM killer recovered by killing `fwupd`; later CloudTrail records an explicit EC2 reboot call.

**Conclusion:** The server had a real memory incident, but the later restart was an AWS API reboot, not a kernel crash.

## Hypothesized Paths

### Hypothesis 1: Scheduled host maintenance jobs triggered the memory spike

**Status:** Open

**Theory:** `fwupd-refresh`, `snapd`, update timers, or GitHub runner activity overlapped with the app stack and pushed the host over the edge.

**Supporting indicators:** The OOM victim was `fwupd`; timers and snap services are present; CPU spiked around the OOM window.

**Would confirm:** Full process accounting or sysstat memory history around 2026-06-22 15:57 UTC.

**Would refute:** Memory history showing an app container grew abnormally and host services were incidental.

**Resolution:** Not resolved because host memory time-series was not collected.

## Missing Evidence

| Gap | Impact | How to Obtain |
| --- | --- | --- |
| Host memory time-series before OOM | Would identify the top memory grower before kill | Add CloudWatch agent or extend custom pusher to emit `MemAvailable`, swap, and process/container RSS |
| Per-process accounting history | Would separate runner/update spike from app growth | Enable sysstat/process accounting or periodic `ps`/`docker stats` snapshot |
| GitHub runner job history | Would show whether a CI job ran during the OOM | Check runner logs under `/opt/actions-runner/_diag` |

## Source Code Trace

| Element | Detail |
| --- | --- |
| Error origin | Host kernel OOM, not application exception |
| Trigger | Allocation by a Python process when host free memory was below safe watermark |
| Condition | `t4g.micro`, no swap, local DB, four app services, Docker stack, default Ubuntu services, GitHub Actions runner |
| Related files | `deploy/docker-compose.yml`, `deploy/terraform/variables.tf`, `deploy/cloudwatch/push_metrics.sh`, `docs/runbooks/crawl-scale-runbook.md` |

## Conclusion

**Confidence:** Medium

The confirmed root issue is lack of memory headroom: the instance is a `t4g.micro` with no swap and too many resident services for a production host. The direct OOM victim was `fwupd`, not a rider app container. The app architecture intentionally runs several services, but some host processes are unnecessary for this EC2 role, especially the GitHub Actions runner and firmware/update-related services. The exact pre-OOM top grower cannot be proven because host memory metrics were not collected.

## Recommended Next Steps

### Fix direction

1. Immediate: add 1-2 GiB swap and set conservative swappiness.
2. Immediate: stop or move the GitHub Actions runner off this production EC2.
3. Immediate: disable or mask `fwupd-refresh.timer`; consider disabling `fwupd`, `ModemManager`, and `udisks2` if not needed.
4. Near term: reduce DB pool defaults for micro deployment, for example `RIDER_DB_POOL_SIZE=2`, `RIDER_DB_MAX_OVERFLOW=2`.
5. Near term: add memory metrics/alarms for `MemAvailable`, swap used, and optionally top processes/containers.
6. Best production fix: move from `t4g.micro` to `t4g.small` or larger, or move PostgreSQL to RDS.

### Diagnostic

Run after changes:

```bash
free -h
swapon --show
docker stats --no-stream
ps -eo pid,user,rss,comm,args --sort=-rss | head -n 30
journalctl -k --since '24 hours ago' | grep -Ei 'oom|out of memory|killed process'
```

## Reproduction Plan

Do not reproduce OOM on production. In staging, run the same compose stack on a micro-sized host with swap disabled, then start an extra 200-300 MiB process while update services and the runner are active. Expected result is global OOM pressure and a kernel-selected victim.

## Side Findings

- Confirmed: `rider-metrics.service` has `MemoryMax=64M`, so the pusher is not likely the main memory cause.
- Confirmed: the custom metric pusher had repeated `NoCredentials` failures before reboot, so monitoring was impaired during part of the incident window.
