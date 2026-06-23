# EC2 Memory Hardening Runbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task, or `superpowers:executing-plans` if one worker executes it inline. Keep checkbox state in this document as work lands.

작성일: 2026-06-23
상태: 구현 완료
대상 저장소: `rider_result_mornitoring`
근거 문서: `docs/runbooks/ec2-memory-hardening-plan.md`
검토 근거: 2026-06-23 문서 리뷰, `_bmad-output/implementation-artifacts/investigations/ec2-memory-oom-investigation.md`, 병렬 서브에이전트 검토 결과

**Goal:** 최근 EC2 OOM 재발 가능성을 낮추고, 운영자가 같은 runbook을 두 번 실행해도 안전하게 동작하도록 배포/모니터링/rollback 지시를 보강한다.

**Architecture:** 운영 구조는 단일 EC2 + Docker Compose + 로컬 PostgreSQL을 유지하되, `t4g.small`, 2GB swap, GitHub runner 분리, DB pool 축소, host memory/swap metric을 묶어 적용한다. Terraform은 인스턴스 교체가 아니라 type change만 허용하고, root volume에 DB가 있으므로 destroy/replacement는 명시적으로 막는다. 배포 env source는 repo root `.env`를 기준으로 통일한다.

**Tech Stack:** AWS EC2 t4g, Ubuntu 24.04 ARM64, Docker Compose, PostgreSQL 16, Terraform, GitHub Actions, systemd, CloudWatch custom metrics, pytest.

---

## 작업 원칙

- 운영 runbook 명령은 재실행 가능해야 한다.
- root volume에 로컬 PostgreSQL data가 있으므로 Terraform replacement 또는 destroy는 중단 조건이다.
- GitHub Actions runner를 끄기 전 `main` 배포 대체 경로를 먼저 확보한다.
- memory hardening은 용량 증설만이 아니라 감시와 알람까지 포함한다.
- 비밀값 예시는 실제 운영값처럼 보이지 않게 더미 값만 사용한다.
- 기존 unrelated dirty worktree는 되돌리지 않는다.

## 완료 기준

- `docs/runbooks/ec2-memory-hardening-plan.md`가 root `.env`와 deploy env 파일의 역할을 명확히 구분한다.
- runner 중지 단계는 배포 대체 경로 확인 없이는 실행하지 않게 되어 있다.
- swap 추가 명령은 `/swapfile`과 `/etc/fstab` 중복을 만들지 않는다.
- Terraform 지시에는 persistent `t4g.small` 설정과 EC2 replacement abort 조건이 있다.
- host memory/swap metric이 CloudWatch에 올라가고 low memory/high swap alarm이 있다.
- DB pool 축소는 compose, root env, CI budget check 중 하나의 source of truth로 검증된다.
- runbook 성공 기준에 memory metric/alarm과 OOM 로그 확인이 포함된다.

---

## Task 0: 기준선 확인

**Intent:** 현재 배포 설정과 OOM 조사 근거를 변경 전에 고정한다.

**Files:** 없음

- [x] **Step 1: 작업 전 변경 상태 확인**

Run:

```powershell
git status --short
```

Expected:

- 이 작업과 무관한 변경은 기록만 하고 되돌리지 않는다.

- [x] **Step 2: 현재 문서와 배포 config 테스트 실행**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py tests/server/test_deployment_config.py tests/server/test_scale_readiness.py -q
```

Expected:

- 현재 실패가 있으면 test name과 원인을 이 문서의 구현 기록에 남긴다.

---

## Task 1: 운영 env source of truth 정리

**Intent:** 운영자가 DB pool 값을 잘못된 `.env` 파일에 넣어 자동 배포 때 사라지는 일을 막는다.

**Files:**

- Modify: `docs/runbooks/ec2-memory-hardening-plan.md`
- Modify: `.github/workflows/test.yml`
- Modify: `docs/runbooks/crawl-scale-runbook.md`
- Test: `tests/server/test_runbooks_present.py`
- Test: `tests/server/test_deployment_config.py`

- [x] **Step 1: env 위치 문서 guard 실패 테스트 추가**

Add test in `tests/server/test_runbooks_present.py`:

```python
def test_ec2_memory_runbook_uses_repo_root_env_for_production_deploy() -> None:
    """Production deploy sources /opt/rider-server/repo/.env, not deploy/.env."""
```

Required assertions:

- The runbook mentions `/opt/rider-server/repo/.env`.
- The runbook does not instruct operators to edit `/opt/rider-server/repo/deploy/.env` as the production source of truth.
- The runbook explains `deploy/env/*.env` files are compose service env files, not the root deploy variable file.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_ec2_memory_runbook_uses_repo_root_env_for_production_deploy -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: runbook DB pool command 수정**

Required runbook content (idempotent Python edit block — `grep ... || echo ... | sudo tee -a .env`
append 방식은 **금지**한다. 그 방식은 중복 키를 만들고 마지막 값만 유효해 비결정적이며,
`tests/server/test_runbooks_present.py` 가 명시적으로 차단한다. 실제 runbook
(`docs/runbooks/ec2-memory-hardening-plan.md`)도 아래 Python 블록을 정본으로 쓴다):

```bash
cd /opt/rider-server/repo
sudo cp .env ".env.backup.$(date -u +%Y%m%dT%H%M%SZ)"
sudo python3 - <<'PY'
from pathlib import Path

path = Path(".env")
if not path.is_file():
    raise SystemExit("missing /opt/rider-server/repo/.env")

updates = {
    "RIDER_DB_POOL_SIZE": "2",
    "RIDER_DB_MAX_OVERFLOW": "2",
    "RIDER_UVICORN_WORKERS": "1",
}
seen = set()
out = []
for line in path.read_text(encoding="utf-8").splitlines():
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        if key not in seen:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
```

Verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_ec2_memory_runbook_uses_repo_root_env_for_production_deploy -q
```

---

## Task 2: GitHub runner 중지 전 배포 대체 경로를 막는 precheck 추가

**Intent:** EC2 메모리를 줄이려다 `main` 자동 배포가 조용히 멈추는 것을 막는다.

**Files:**

- Modify: `docs/runbooks/ec2-memory-hardening-plan.md`
- Modify: `.github/workflows/test.yml` if deploy path is migrated
- Test: `tests/server/test_runbooks_present.py`

- [x] **Step 1: runner dependency 문서 guard 실패 테스트 추가**

Add test in `tests/server/test_runbooks_present.py`:

```python
def test_ec2_memory_runbook_blocks_runner_shutdown_without_deploy_replacement() -> None:
    """Runner shutdown requires confirmed production deploy replacement."""
```

Required assertions:

- The runbook mentions `.github/workflows/test.yml` `deploy-production`.
- The runbook mentions `[self-hosted, Linux, ARM64, rider-prod]`.
- The runbook says `systemctl disable` is blocked until manual deploy or GitHub-hosted + SSH/SSM deploy is verified.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_ec2_memory_runbook_blocks_runner_shutdown_without_deploy_replacement -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: runner 처리 정책을 두 단계로 고정**

Required runbook content:

- Emergency mode: runner stop is allowed only after operator accepts that `main` auto deploy is paused and a manual deploy command is recorded.
- Permanent mode: migrate deploy to GitHub-hosted runner + SSH/SSM, then disable EC2 runner.
- Verification before disable:

```bash
grep -n "runs-on: \\[self-hosted, Linux, ARM64, rider-prod\\]" /opt/rider-server/repo/.github/workflows/test.yml
```

Expected:

- If the line exists, automatic deploy still depends on the EC2 runner.
- The runbook must say not to disable the runner unless emergency stability is more important than automatic deploy.

---

## Task 3: idempotent swap와 서비스 중지 명령으로 변경

**Intent:** runbook을 두 번 실행해도 `/etc/fstab` 중복, swap 재포맷 실패, 필수 서비스 중단이 생기지 않게 한다.

**Files:**

- Modify: `docs/runbooks/ec2-memory-hardening-plan.md`
- Test: `tests/server/test_runbooks_present.py`

- [x] **Step 1: swap idempotency 문서 guard 실패 테스트 추가**

Add test:

```python
def test_ec2_memory_runbook_has_idempotent_swap_commands() -> None:
    """Swap setup checks existing swapfile and fstab before writing."""
```

Required assertions:

- The runbook includes `test -f /swapfile`.
- The runbook includes `grep -q '^/swapfile none swap sw 0 0' /etc/fstab`.
- The runbook does not use plain `tee -a /etc/fstab` without a `grep` guard in the swap step.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_ec2_memory_runbook_has_idempotent_swap_commands -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: guarded swap 명령으로 교체**

Required runbook command:

```bash
if ! swapon --show=NAME --noheadings | grep -qx '/swapfile'; then
  if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  fi
  sudo swapon /swapfile
fi

if ! grep -q '^/swapfile none swap sw 0 0$' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

printf 'vm.swappiness=10\nvm.vfs_cache_pressure=50\n' | sudo tee /etc/sysctl.d/99-rider-swap.conf
sudo sysctl --system
```

- [x] **Step 3: service disable safety 문서 guard 추가**

Add test:

```python
def test_ec2_memory_runbook_keeps_required_services_enabled() -> None:
    """Runbook names services that must not be disabled."""
```

Required assertions:

- The runbook says not to disable `amazon-ssm-agent`.
- It says not to disable `docker`, `containerd`, or `ssh`.
- It has rollback commands for disabled optional services.

Verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_ec2_memory_runbook_has_idempotent_swap_commands tests/server/test_runbooks_present.py::test_ec2_memory_runbook_keeps_required_services_enabled -q
```

---

## Task 4: Terraform t4g.small persistence와 replacement guard 추가

**Intent:** 다음 Terraform apply가 다시 `t4g.micro`로 되돌리거나 root volume을 포함한 인스턴스 교체를 만들지 않게 한다.

**Files:**

- Modify: `deploy/terraform/variables.tf`
- Modify: `deploy/terraform/example.tfvars`
- Modify: `deploy/terraform/README.md`
- Modify: `deploy/terraform/compute.tf`
- Modify: `docs/runbooks/ec2-memory-hardening-plan.md`
- Test: `tests/server/test_deployment_config.py`
- Test: `tests/server/test_runbooks_present.py`

- [x] **Step 1: production tfvars 정책 고정**

Decision:

- Production guidance must persist `instance_type = "t4g.small"` in a committed Terraform input path.
- If `variables.tf` default remains `t4g.micro` for cost-minimal examples, `deploy/terraform/example.tfvars` and README must show the production override and the runbook must require it.

- [x] **Step 2: replacement abort 문서 guard 실패 테스트 추가**

Add test:

```python
def test_ec2_memory_runbook_aborts_on_terraform_instance_replacement() -> None:
    """Local PostgreSQL on root volume makes EC2 replacement a stop condition."""
```

Required assertions:

- The runbook mentions aborting if plan shows `-/+ aws_instance.app`.
- The runbook mentions root volume local PostgreSQL data.
- The runbook mentions `delete_on_termination = true`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_ec2_memory_runbook_aborts_on_terraform_instance_replacement -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 3: Terraform guard 추가 검토**

Required behavior:

- Add a lifecycle guard or explicit README warning so an accidental destroy is not treated as routine memory hardening.
- If adding `prevent_destroy = true` to `aws_instance.app` is too disruptive for current Terraform workflows, document the replacement abort condition in `deploy/terraform/README.md` and the runbook.

Verification:

```powershell
terraform -chdir=deploy/terraform fmt -check
terraform -chdir=deploy/terraform validate
```

Expected:

- `fmt -check` passes.
- `validate` passes with configured provider/backend environment.

---

## Task 5: DB pool budget guard와 production values 검증

**Intent:** DB pool 축소가 문서에만 있고 실제 compose/CI에서는 검증되지 않는 상태를 없앤다.

**Files:**

- Modify: `docs/runbooks/ec2-memory-hardening-plan.md`
- Modify: `docs/runbooks/crawl-scale-runbook.md`
- Modify: `.github/workflows/test.yml`
- Modify: `tests/server/test_scale_readiness.py`

- [x] **Step 1: production budget check 실패 테스트 추가**

Add test in `tests/server/test_scale_readiness.py`:

```python
def test_memory_hardening_db_pool_values_fit_single_host_budget() -> None:
    """Recommended production pool values keep local PostgreSQL connection count low."""
```

Required assertions:

- Recommended values are `RIDER_DB_POOL_SIZE=2`, `RIDER_DB_MAX_OVERFLOW=2`, `RIDER_UVICORN_WORKERS=1`.
- The calculated expected app-side connection budget stays under the documented limit.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_scale_readiness.py::test_memory_hardening_db_pool_values_fit_single_host_budget -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: CI deployment config gate에 budget command 추가**

Required command in `.github/workflows/test.yml` deployment/config job:

```bash
RIDER_UVICORN_WORKERS=1 RIDER_DB_POOL_SIZE=2 RIDER_DB_MAX_OVERFLOW=2 python scripts/check_db_connection_budget.py --postgres-max-connections 100
```

Completion criteria:

- CI fails if future defaults or recommended production values exceed the budget.
- `docs/runbooks/crawl-scale-runbook.md` and memory runbook use the same formula.

Verification:

```powershell
$env:RIDER_UVICORN_WORKERS="1"
$env:RIDER_DB_POOL_SIZE="2"
$env:RIDER_DB_MAX_OVERFLOW="2"
.venv\Scripts\python.exe scripts\check_db_connection_budget.py --postgres-max-connections 100
.venv\Scripts\python.exe -m pytest tests/server/test_scale_readiness.py -q
```

---

## Task 6: host memory/swap CloudWatch metrics와 alarms 추가

**Intent:** 이번 OOM의 핵심 누락인 host memory/swap 관측 공백을 닫는다.

**Files:**

- Modify: `deploy/cloudwatch/push_metrics.sh`
- Modify: `deploy/cloudwatch/rider-metrics.service`
- Modify: `deploy/terraform/cloudwatch.tf`
- Modify: `deploy/terraform/outputs.tf`
- Modify: `docs/runbooks/ec2-memory-hardening-plan.md`
- Test: `tests/server/test_metrics_policy.py`
- Test: `tests/server/test_deployment_config.py`

- [x] **Step 1: host metric parser 테스트 추가**

Add test in `tests/server/test_metrics_policy.py`:

```python
def test_cloudwatch_pusher_documents_host_memory_and_swap_metrics() -> None:
    """Metric pusher publishes host memory pressure signals."""
```

Required assertions:

- `push_metrics.sh` emits or documents `MemAvailableBytes`.
- It emits or documents `MemAvailablePercent`.
- It emits or documents `SwapUsedBytes`.
- It emits or documents `SwapUsedPercent`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_metrics_policy.py::test_cloudwatch_pusher_documents_host_memory_and_swap_metrics -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: `/proc/meminfo` 기반 metric 추가**

Required implementation in `deploy/cloudwatch/push_metrics.sh`:

- Read `/proc/meminfo`.
- Publish app metrics and host metrics in the same `RiderServer` namespace.
- Keep `Environment=production` dimension.
- Do not add process command lines or values that may contain secrets.

Required metric names:

- `HostMemAvailableBytes`
- `HostMemAvailablePercent`
- `HostSwapUsedBytes`
- `HostSwapUsedPercent`

- [x] **Step 3: Terraform alarms 추가**

Required alarms:

- Low memory: `HostMemAvailablePercent < 15` for a sustained period.
- High swap: `HostSwapUsedPercent > 40` for a sustained period.
- Metric pusher failure can remain a later task if no heartbeat metric exists, but the runbook must mention checking `systemctl status rider-metrics.service`.

Verification:

```powershell
terraform -chdir=deploy/terraform fmt -check
terraform -chdir=deploy/terraform validate
.venv\Scripts\python.exe -m pytest tests/server/test_metrics_policy.py tests/server/test_deployment_config.py -q
```

Manual EC2 check:

```bash
RUN_ONCE=1 /usr/local/bin/rider-push-metrics.sh
aws cloudwatch list-metrics --namespace RiderServer --region ap-northeast-2 | grep -E 'HostMemAvailable|HostSwapUsed'
```

---

## Task 7: runbook 성공 기준, rollback, smoke를 현재 장애 원인에 맞게 보강

**Intent:** 문서가 “설치 방법”만이 아니라 재발 방지 여부를 확인하게 한다.

**Files:**

- Modify: `docs/runbooks/ec2-memory-hardening-plan.md`
- Modify: `deploy/terraform/README.md`
- Test: `tests/server/test_runbooks_present.py`

- [x] **Step 1: 성공 기준 문서 guard 추가**

Add test:

```python
def test_ec2_memory_runbook_success_criteria_include_metrics_and_oom_checks() -> None:
    """Runbook success includes host metrics, alarms, and OOM log checks."""
```

Required assertions:

- The runbook mentions `journalctl -k --since`.
- It mentions `HostMemAvailablePercent`.
- It mentions `HostSwapUsedPercent`.
- It mentions no new OOM log.
- It mentions `rider-metrics.service`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py::test_ec2_memory_runbook_success_criteria_include_metrics_and_oom_checks -q
```

Expected before implementation:

```text
FAILED
```

- [x] **Step 2: rollback 범위를 현실화**

Required runbook content:

- EC2 type rollback to `t4g.micro` is allowed only after memory metrics show enough headroom.
- Swap rollback is normally not recommended.
- Runner restart rollback command is present, but it also warns memory pressure may return.
- DB pool rollback command uses root `.env`, not `deploy/.env`.
- Optional services rollback command lists exactly the services disabled earlier.

- [x] **Step 3: post-change smoke checklist 추가**

Required smoke checks:

```bash
free -h
swapon --show
docker compose -p rider -f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml ps
docker stats --no-stream
curl -fsS http://localhost:8000/health
journalctl -k --since "24 hours ago" | grep -Ei 'out of memory|oom|killed process' || true
systemctl status rider-metrics.service --no-pager
```

Expected:

- API health succeeds.
- Docker services are running.
- Swap is visible.
- No new OOM log is present.
- Metrics service is active or its error is documented with remediation.

---

## 전체 검증 명령

Docs and config:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_runbooks_present.py tests/server/test_deployment_config.py tests/server/test_scale_readiness.py tests/server/test_metrics_policy.py -q
```

Docker Compose:

```powershell
$env:RIDER_POSTGRES_PASSWORD="rider"
$env:RIDER_DB_MIGRATION_BACKUP_CONFIRMED="1"
$env:RIDER_TELEGRAM_WEBHOOK_SECRET="ci_dummy_webhook_secret"
$env:RIDER_TELEGRAM_BOT_TOKEN="ci_dummy_bot_token"
docker compose -f deploy/docker-compose.yml config
```

Terraform:

```powershell
terraform -chdir=deploy/terraform fmt -check
terraform -chdir=deploy/terraform validate
terraform -chdir=deploy/terraform plan -var='instance_type=t4g.small'
```

Budget:

```powershell
$env:RIDER_UVICORN_WORKERS="1"
$env:RIDER_DB_POOL_SIZE="2"
$env:RIDER_DB_MAX_OVERFLOW="2"
.venv\Scripts\python.exe scripts\check_db_connection_budget.py --postgres-max-connections 100
```

## 수동 운영 확인

- AWS console 또는 CLI에서 root EBS snapshot이 생성되었는지 확인한다.
- Terraform plan에서 `-/+ aws_instance.app`가 보이면 작업을 중단한다.
- `main` push 자동 배포가 runner 중지 뒤에도 가능한지, 또는 수동 배포 절차가 승인되었는지 확인한다.
- EC2에서 `RUN_ONCE=1 /usr/local/bin/rider-push-metrics.sh` 실행 후 CloudWatch에 host memory/swap metric이 보이는지 확인한다.

## 구현 기록

- 기준선: `tests/server/test_runbooks_present.py tests/server/test_deployment_config.py tests/server/test_scale_readiness.py -q` → 56 passed.
- RED: 새 guard 11개가 구현 전 모두 실패함을 확인했다.
- GREEN: 새 guard 11개 → 11 passed.
- 관련 전체 검증: `tests/server/test_runbooks_present.py tests/server/test_deployment_config.py tests/server/test_scale_readiness.py tests/server/test_metrics_policy.py -q` → 90 passed.
- 전체 pytest: `pytest -q` → 2503 passed, 69 skipped.
- Budget: `RIDER_UVICORN_WORKERS=1 RIDER_DB_POOL_SIZE=2 RIDER_DB_MAX_OVERFLOW=2 RIDER_DB_RESERVED_CONNECTIONS=10 python -S scripts/check_db_connection_budget.py --postgres-max-connections 100` → requested=26, max_connections=100, ok.
- Shell: `bash -n deploy/cloudwatch/push_metrics.sh` → passed.
- Docker Compose: `docker compose -f deploy/docker-compose.yml config` with CI dummy secrets → passed.
- Terraform: `terraform -chdir=deploy/terraform fmt -check` and `terraform -chdir=deploy/terraform validate` → passed after `terraform init -backend=false -input=false`.
- Terraform plan: `terraform -chdir=deploy/terraform plan -refresh=false -var='instance_type=t4g.small' -input=false` could not run without normal S3 backend initialization. No backend/state mutation was performed.

## 리스크와 대응

- **Runner 중지로 배포 중단:** emergency mode가 아니면 runner 중지 전 deploy 대체 경로를 먼저 merge한다.
- **Terraform replacement로 DB 손실:** root volume local PostgreSQL data가 있으므로 replacement plan은 중단한다. snapshot을 만든 뒤에도 replacement는 memory hardening 범위가 아니다.
- **Swap 명령 재실행 실패:** guarded command만 문서에 남기고 plain `tee -a /etc/fstab` 사용을 금지한다.
- **Metric 비용 증가:** host metric 4개와 alarm 2개를 먼저 추가하고, process-level metric은 필요할 때 별도 작업으로 분리한다.
- **DB pool 과소 설정:** connection timeout이나 queue lag가 늘면 pool rollback보다 먼저 `pg_stat_activity`, app error logs, queue lag를 확인한다.
