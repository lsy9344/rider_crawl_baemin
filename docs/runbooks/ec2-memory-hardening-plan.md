# Runbook: EC2 메모리 안정화와 t4g.small 전환 계획

작성일: 2026-06-23 KST

## 1. 목적

프로덕션 EC2에서 발생한 메모리 부족(OOM) 재발 가능성을 낮춘다.

이번 계획의 핵심은 RDS 도입이 아니다. 현재 DB 크기와 연결 수는 작고, 문제는 `t4g.micro`
1GB 메모리에 앱, PostgreSQL, OS 서비스, GitHub Actions runner가 함께 떠 있는 구조다.

## 2. 현재 확인된 상태

| 항목 | 현재 상태 |
| --- | --- |
| EC2 instance | `i-0e6a710a505e6b3c4` |
| Region | `ap-northeast-2` |
| Instance type | `t4g.micro` |
| Public IP / EIP | `54.116.103.149` |
| Root disk | 20GB, 약 15GB 여유 |
| Swap | 없음 |
| PostgreSQL 위치 | EC2 내부 Docker container |
| 전체 DB 크기 | 약 9.7MB |
| Docker DB volume | 약 67MB |
| 현재 RDS | 없음 |
| GitHub Actions runner | EC2에서 systemd service로 실행 중 |

현재 runner service:

```text
actions.runner.lsy9344-rider_crawl_baemin.ip-10-50-1-8.service
```

주의: `.github/workflows/test.yml`의 `deploy-production` job은 아래 runner label을 사용한다.

```yaml
runs-on: [self-hosted, Linux, ARM64, rider-prod]
```

따라서 runner를 끄면 `main` push 후 자동 배포가 중단된다. runner 제거 전에는 배포 방식을
GitHub-hosted runner + SSH/SSM 방식으로 바꾸거나, 수동 배포 절차를 확정해야 한다.

## 3. 목표 상태

| 항목 | 목표 |
| --- | --- |
| Instance type | `t4g.small` |
| Swap | 2GB |
| GitHub Actions runner | 프로덕션 EC2에서 제거 또는 배포 방식 변경 후 중지 |
| DB pool | `RIDER_DB_POOL_SIZE=2`, `RIDER_DB_MAX_OVERFLOW=2`, `RIDER_UVICORN_WORKERS=1` |
| RDS | 이번 작업 범위에서 도입하지 않음 |
| 예상 비용 증가 | EC2 타입 변경으로 약 `+$7.6/month` |

## 4. 적용 순서

### Step 1. 작업 전 상태 기록

예상 시간: 10분

다운타임: 없음

```bash
free -h
swapon --show
df -h /
docker compose -p rider -f /opt/rider-server/repo/deploy/docker-compose.yml ps
docker stats --no-stream
```

DB 백업:

```bash
docker exec rider-db-1 pg_dump -U rider -d rider -Fc > ~/rider-pre-memory-hardening.dump
```

AWS 측에서는 EC2 root EBS snapshot을 하나 만든다. 인스턴스 타입 변경 전 수동 snapshot을 남기는
목적이다.

### Step 2. GitHub Actions runner 처리

예상 시간: 10-30분

다운타임: 앱에는 없음. 단, 자동 배포에는 영향 있음.

먼저 현재 배포 방식을 결정한다.

| 선택 | 의미 |
| --- | --- |
| 임시 중지 | EC2 메모리 사용량은 줄지만 `main` push 자동 배포가 중단됨 |
| 권장 | 배포를 GitHub-hosted runner + SSH/SSM 방식으로 바꾼 뒤 EC2 runner 제거 |

`systemctl disable` is blocked until manual deploy or GitHub-hosted + SSH/SSM deploy is verified.

비활성화 전 확인:

```bash
grep -n "runs-on: \[self-hosted, Linux, ARM64, rider-prod\]" /opt/rider-server/repo/.github/workflows/test.yml
```

결과 해석:

- 위 줄이 나오면 `.github/workflows/test.yml`의 `deploy-production` job이 아직 EC2 runner에 의존한다.
- 이 상태에서는 자동 배포보다 emergency 안정화가 더 중요하다는 운영자 판단이 없으면 runner를 끄지 않는다.
- Emergency mode: runner stop은 `main` 자동 배포가 일시 중단됨을 운영자가 수락하고, 아래 수동 배포 명령을 기록한 뒤에만 허용한다.
- Permanent mode: 배포를 GitHub-hosted runner + SSH/SSM으로 옮기고 성공을 확인한 뒤 EC2 runner를 disable한다.

Emergency mode에서 사용할 수동 배포 명령:

```bash
cd /opt/rider-server/repo
git fetch origin main
git checkout -B main -f FETCH_HEAD
docker compose --env-file /opt/rider-server/repo/.env -p rider -f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml up --build -d --remove-orphans
curl -fsS http://localhost:8000/health
```

위 조건을 만족했고 급한 안정화가 우선이면 runner를 중지한다.

```bash
sudo systemctl stop actions.runner.lsy9344-rider_crawl_baemin.ip-10-50-1-8.service
sudo systemctl disable actions.runner.lsy9344-rider_crawl_baemin.ip-10-50-1-8.service
```

검증:

```bash
ps aux | grep -Ei 'Runner.Listener|Runner.Worker|actions-runner' | grep -v grep
systemctl is-active actions.runner.lsy9344-rider_crawl_baemin.ip-10-50-1-8.service
```

### Step 3. 2GB swap 추가

예상 시간: 5분

다운타임: 없음

```bash
if ! swapon --show=NAME --noheadings | grep -qx '/swapfile'; then
  if ! test -f /swapfile; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  elif ! sudo file /swapfile | grep -q 'swap file'; then
    echo "/swapfile exists but is not formatted as swap; inspect it before removing or recreating" >&2
    exit 1
  fi
  sudo swapon /swapfile
fi

if ! grep -q '^/swapfile none swap sw 0 0$' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

printf 'vm.swappiness=10\nvm.vfs_cache_pressure=50\n' | sudo tee /etc/sysctl.d/99-rider-swap.conf
sudo sysctl --system
```

검증:

```bash
free -h
swapon --show
```

### Step 4. EC2에 불필요한 OS 서비스 중지

예상 시간: 5분

다운타임: 없음

필수 서비스는 중지하지 않는다: do not disable amazon-ssm-agent, do not disable docker,
do not disable containerd, do not disable ssh. `snapd`도 SSM/OS 관리 경로에 영향을 줄 수 있으므로
이 작업에서는 유지한다.

중지 대상:

```bash
sudo systemctl disable --now fwupd.service fwupd-refresh.timer ModemManager.service udisks2.service
```

검증:

```bash
systemctl is-active fwupd.service fwupd-refresh.timer ModemManager.service udisks2.service
```

`inactive` 또는 `failed`가 나오면 중지된 상태다.

### Step 5. DB pool 축소

예상 시간: 5-10분

다운타임: 앱 컨테이너 재생성 중 짧게 발생 가능

운영 compose 위치:

```text
/opt/rider-server/repo/deploy
```

운영 배포 변수의 source of truth는 `/opt/rider-server/repo/.env`다. `deploy/env/*.env` files are
compose service env files, not the root deploy variable file. 따라서 production DB pool 값은
`/opt/rider-server/repo/deploy/.env`가 아니라 root `.env`에 기록한다.

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

반영:

```bash
cd /opt/rider-server/repo
docker compose --env-file /opt/rider-server/repo/.env -p rider -f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml up -d --no-deps --force-recreate backend-api scheduler queue-recovery telegram-dispatch
```

검증:

```bash
docker compose -p rider -f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml ps
docker stats --no-stream
docker exec rider-db-1 psql -U rider -d rider -c "select state, count(*) from pg_stat_activity group by state;"
```

기대값:

- 평상시 DB 연결 수가 10-20개 수준에 머문다.
- `backend-api`, `scheduler`, `queue-recovery`, `telegram-dispatch`가 모두 running 상태다.

### Step 6. EC2를 t4g.small로 변경

예상 시간: 5-10분

다운타임: EC2 stop/start 동안 발생

비용 증가: 약 `+$7.6/month`

권장 방식은 Terraform이다. 운영 입력 파일(`deploy/terraform/example.tfvars`를 복사한
`terraform.tfvars`)에 `instance_type = "t4g.small"`을 유지해 다음 apply가 `t4g.micro`로 되돌리지
않게 한다. 먼저 plan에서 인스턴스 교체가 아니라 타입 변경인지 확인한다.

```bash
cd deploy/terraform
terraform plan -var='instance_type=t4g.small'
terraform apply -var='instance_type=t4g.small'
```

중단 조건:

- plan에 `-/+ aws_instance.app`가 보이면 즉시 중단한다.
- 현재 root volume local PostgreSQL data가 있고 `delete_on_termination = true`이므로 EC2 replacement는 DB data 손실 경로다.
- 이번 작업 범위는 instance type change이며 replacement나 destroy가 아니다.

직접 AWS CLI로 변경할 경우:

```bash
aws ec2 stop-instances --region ap-northeast-2 --instance-ids i-0e6a710a505e6b3c4
aws ec2 wait instance-stopped --region ap-northeast-2 --instance-ids i-0e6a710a505e6b3c4
aws ec2 modify-instance-attribute --region ap-northeast-2 --instance-id i-0e6a710a505e6b3c4 --instance-type '{"Value":"t4g.small"}'
aws ec2 start-instances --region ap-northeast-2 --instance-ids i-0e6a710a505e6b3c4
aws ec2 wait instance-running --region ap-northeast-2 --instance-ids i-0e6a710a505e6b3c4
```

부팅 후 검증:

```bash
free -h
swapon --show
docker ps
docker stats --no-stream
curl -fsS http://localhost:8000/health
```

## 5. 성공 기준

| 기준 | 목표 |
| --- | --- |
| Swap | `free -h`에서 2GB 표시 |
| Memory | 평상시 `available`이 수백 MB 이상 |
| Runner | `Runner.Listener` 프로세스 없음 |
| DB 연결 | 평상시 10-20개 이하 |
| Docker services | app 4개와 DB가 running |
| API health | `/health` 성공 |
| Host metrics | CloudWatch `HostMemAvailablePercent`, `HostSwapUsedPercent`가 보임 |
| Host alarms | low memory/high swap alarm이 생성됨 |
| Metrics service | `rider-metrics.service`가 active 또는 오류 원인과 조치가 기록됨 |
| OOM | no new OOM log |

OOM 재발 여부 확인:

```bash
journalctl -k --since "24 hours ago" | grep -Ei 'out of memory|oom|killed process'
```

Post-change smoke:

```bash
free -h
swapon --show
docker compose -p rider -f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml ps
docker stats --no-stream
curl -fsS http://localhost:8000/health
journalctl -k --since "24 hours ago" | grep -Ei 'out of memory|oom|killed process' || true
systemctl status rider-metrics.service --no-pager
```

CloudWatch 확인:

```bash
RUN_ONCE=1 /usr/local/bin/rider-push-metrics.sh
aws cloudwatch list-metrics --namespace RiderServer --region ap-northeast-2 | grep -E 'HostMemAvailable|HostSwapUsed'
```

## 6. 되돌리기

### EC2 타입 되돌리기

`t4g.micro`로 되돌리는 것은 `HostMemAvailablePercent`와 `HostSwapUsedPercent`가 충분한 headroom을
보여줄 때만 허용한다. 메모리 압력이 남아 있으면 되돌리지 않는다.

```bash
cd deploy/terraform
terraform apply -var='instance_type=t4g.micro'
```

또는 AWS CLI로 `t4g.micro`를 다시 지정한다.

### GitHub Actions runner 재시작

runner를 되살리면 memory pressure가 다시 올라갈 수 있다. 재시작 뒤 `free -h`, `docker stats`,
`HostMemAvailablePercent`, `HostSwapUsedPercent`를 다시 본다.

```bash
sudo systemctl enable --now actions.runner.lsy9344-rider_crawl_baemin.ip-10-50-1-8.service
```

### DB pool 원복

root deploy variable file인 `/opt/rider-server/repo/.env`를 수정한다.

```bash
cd /opt/rider-server/repo
sudo cp .env ".env.rollback.$(date -u +%Y%m%dT%H%M%SZ)"
sudo python3 - <<'PY'
from pathlib import Path

path = Path(".env")
if not path.is_file():
    raise SystemExit("missing /opt/rider-server/repo/.env")

updates = {
    "RIDER_DB_POOL_SIZE": "5",
    "RIDER_DB_MAX_OVERFLOW": "10",
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

그 다음 앱 컨테이너를 재생성한다.

```bash
cd /opt/rider-server/repo
docker compose --env-file /opt/rider-server/repo/.env -p rider -f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml up -d --no-deps --force-recreate backend-api scheduler queue-recovery telegram-dispatch
```

### Optional services rollback

Step 4에서 중지한 optional service만 되살린다. 필수 서비스(`amazon-ssm-agent`, `docker`,
`containerd`, `ssh`)는 중지 대상이 아니었다.

```bash
sudo systemctl enable --now fwupd.service fwupd-refresh.timer ModemManager.service udisks2.service
```

### Swap

swap은 보통 되돌리지 않는다. 메모리 부족 시 즉시 OOM으로 죽는 것을 막는 완충 장치이므로 유지한다.
정말 제거해야 한다면 아래 순서로 제거한다.

```bash
sudo swapoff /swapfile
sudo rm /swapfile
sudo sed -i '/\/swapfile none swap sw 0 0/d' /etc/fstab
sudo rm /etc/sysctl.d/99-rider-swap.conf
sudo sysctl --system
```

## 7. RDS 판단 기준

이번 계획에는 RDS 도입을 포함하지 않는다. 현재 데이터 크기와 DB 부하는 작다.

RDS는 성능 때문에 바로 필요한 것이 아니라, 아래 조건이 생길 때 검토한다.

- 유료 고객 운영으로 DB 장애 복구 시간이 중요해진다.
- 하루 단위 EBS snapshot만으로는 데이터 손실 허용 범위가 부족하다.
- `target` 수가 20-30개 이상으로 늘어난다.
- queue lag 또는 Kakao lag가 반복적으로 커진다.
- DB 백업, 복원, patch, 장애 대응을 EC2 내부 운영으로 감당하기 어렵다.

그 전까지는 `t4g.small` + 2GB swap + runner 분리 + DB pool 축소가 비용 대비 효과가 더 크다.
