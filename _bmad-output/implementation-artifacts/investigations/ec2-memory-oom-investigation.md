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

## Follow-up: 2026-06-24

### New Evidence

- Confirmed: GitHub Actions deploy job `28085428323` started the self-hosted runner production deploy at `2026-06-24 08:27:00 UTC`. EC2 journal shows `runsvc.sh ... Running job: Deploy production` at the same timestamp.
- Confirmed: Immediately after deploy started, Docker/BuildKit activity began. Journal shows several `var-lib-docker-tmp-buildkit...mount` entries at `08:27:09 UTC`.
- Confirmed: Docker reported healthcheck failures during the deploy window:
  - `08:27:10 UTC`: `transport: Error while dialing: only one connection allowed`
  - `08:27:15 UTC`: `healthcheck failed fatally`
  - `08:28:04 UTC`: `context deadline exceeded`
- Confirmed: App metrics started failing after deploy began. `rider-push-metrics.sh` reported `curl failed` at `08:28:15`, `08:36:03`, and `08:38:15 UTC`.
- Confirmed: sysstat shows severe host pressure after deploy started:
  - `08:20:02 UTC`: `kbavail=167820`, load `0.09`, blocked `0`, iowait `1.32%`
  - `08:33:48 UTC`: `kbavail=17268`, load `32.32`, blocked `27`, iowait `36.23%`
  - swap was absent: `kbswpfree=0`, `kbswpused=0`
  - disk reads jumped from about `1.9MB/s` to about `125MB/s`, and root NVMe utilization rose to about `45%`.
- Confirmed: AWS EC2 `StatusCheckFailed` stayed `0` during `08:10-08:45 UTC`, so AWS saw the instance as healthy even while services inside it were unresponsive.
- Confirmed: No kernel OOM kill line was found in the `2026-06-24 08:15-08:39 UTC` kernel journal window.

### Additional Findings

### Finding 7: The June 24 deploy incident was a resource-starvation hang, not a confirmed kernel OOM

**Evidence:** EC2 journal and sysstat around `2026-06-24 08:27-08:38 UTC`.

**Detail:** The runner started a deploy, Docker/BuildKit began work, memory availability collapsed to about 17 MiB, load average rose to 32, and 27 processes were blocked. The app stopped responding to local curl checks, but the kernel did not record an OOM kill.

### Finding 8: Production deploy builds on the same low-memory production host

**Evidence:** `.github/workflows/test.yml` deploy-production job runs on `[self-hosted, Linux, ARM64, rider-prod]` and executes `docker compose ... up --build`.

**Detail:** The same EC2 host runs PostgreSQL, four application containers, Docker/containerd, host services, and the GitHub Actions runner. During deploy it also performs image build work, which adds memory and disk pressure.

### Updated Hypotheses

### Hypothesis 2: Docker build/read pressure caused host-wide service starvation during deploy

**Status:** Confirmed

**Theory:** On a `t4g.micro` with no swap, `docker compose up --build` started BuildKit work while the app stack and runner were already resident. The host did not immediately kill a process, but memory and I/O pressure grew enough that Docker healthchecks, app curls, and SSH/runner responsiveness degraded or stopped.

**Supporting indicators:** BuildKit mounts at `08:27:09 UTC`, Docker healthcheck failures by `08:27:10-08:28:04 UTC`, app metric curl failures from `08:28:15 UTC`, sysstat at `08:33:48 UTC` showing `kbavail=17268`, load `32.32`, blocked `27`, iowait `36.23%`, and no EC2 status-check failure.

**Would confirm:** Already confirmed by matching timeline and sysstat pressure data.

**Would refute:** A kernel panic, OOM kill, AWS host status failure, or manual shutdown at the same time. None was found in the available logs.

**Resolution:** Confirmed as the best root cause for the June 24 CI/deploy hang. The direct mechanism is host resource starvation during Docker deploy build, not a code-level application crash.

### Backlog Changes

- Done: Add 2 GiB swap on the EC2 host. Verified with `swapon --show`.
- Done: Harden CI deploy script to wait for Docker daemon readiness before running compose.
- Done: Move production deploy off the production EC2 runner path. The replacement design builds the ARM64 image on GitHub-hosted Actions, pushes it to ECR, and deploys through SSM with `docker compose ... up -d --no-build`.
- Done: Keep host memory/swap CloudWatch metrics visible. The EC2 pusher was updated to publish `HostMemAvailablePercent` and `HostSwapUsedPercent`, and Terraform now manages the matching low-memory/high-swap alarms.

### Updated Conclusion

**Confidence:** High for the June 24 deploy incident.

The server did not show evidence of an AWS host failure or a kernel OOM kill on June 24. It became internally unresponsive because the production EC2 was too small for its combined workload: app containers, local PostgreSQL, Docker, host services, and a GitHub Actions runner all shared a `t4g.micro` with no swap, and the deploy job added Docker build pressure. The host entered severe memory/I/O starvation, causing Docker healthchecks and app curl checks to fail while AWS still considered the instance running.

## Remediation Applied: 2026-06-25

- EC2 was changed from `t4g.micro` to `t4g.small`.
- A pre-change PostgreSQL dump was written on the host, and root EBS snapshot `snap-03d303d51f094afad` completed before the resize.
- Production `.env` was updated to `RIDER_UVICORN_WORKERS=1`, `RIDER_DB_POOL_SIZE=2`, and `RIDER_DB_MAX_OVERFLOW=2`.
- Optional host services `fwupd`, `fwupd-refresh.timer`, `ModemManager`, and `udisks2` were disabled and masked.
- The installed `rider-push-metrics.sh` was updated to the host memory/swap-aware version and pushed 18 metrics to CloudWatch.
- SSM was enabled through the EC2 instance profile and verified with a successful `AWS-RunShellScript` command.
- Terraform now manages the ECR repository, GitHub OIDC deploy role, EC2 ECR pull permission, SSM core policy attachment, and host memory/swap alarms.
