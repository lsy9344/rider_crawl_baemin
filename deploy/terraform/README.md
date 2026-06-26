# rider_server AWS 인프라 (Terraform)

Epic 5 클라우드 control plane 을 AWS Seoul(`ap-northeast-2`)에 IaC 로 구축한다.

## 사양서 대비 결정(비용 최소화)

architecture.md 는 EC2 + **RDS** + S3 + Secrets Manager + CloudWatch 를 명시하지만,
운영 비용 최소화 요구(월 $40 미만, 크롤링 부하는 로컬 PC=Agent 가 담당)에 따라 다음과 같이 결정했다:

- **DB: RDS 대신 EC2 1대에 Docker Compose 로 PostgreSQL 동거.** RDS 비용(~$12+/월) 제거.
  사양서의 RDS PITR≥7일 백업 요구는 **DLM 일일 EBS 스냅샷(7일 보존)** 으로 대체한다.
  현재 구성에는 WAL 기반 PITR이 없으므로 최악 RPO는 약 24시간이다.
- **NAT Gateway 없음.** 퍼블릭 서브넷 + IGW 만 사용(NAT 시간당 과금 ~$35/월 회피). EC2 가
  공인 IP(EIP)로 직접 outbound.
- **EC2 t4g.small (ARM) 기본값.** 운영 memory hardening 이후 작은 프로덕션 서버의 기준값은
  `t4g.small`이다.

예상 월 비용: **약 $15~20** (t4g.small + gp3 20GB + S3/Secrets/전송).

추후 규모가 커지면(트리거: target>20~30, kakao lag>120s) RDS Multi-AZ/PITR,
backend 수평확장, 전용 worker/queue, ALB 또는 reverse proxy+TLS로 확장한다.

## 리소스

- VPC(10.50.0.0/16) + 퍼블릭 서브넷 + IGW + 라우트
- 보안그룹: SSH(22, 운영자 IP 한정), 8000(앱, 기본 미공개 `app_ingress_cidr=""`). 5432 는 외부 미개방(호스트 내부만)
- EC2 t4g.small + gp3 20GB(암호화) + EIP + Docker/AWS CLI/jq 설치 user-data
- S3 아티팩트 버킷(비공개/암호화/버저닝)
- Secrets Manager: `rider-server/db-credentials`(자동 생성 비번), `rider-server/app-secrets`(운영자 입력), 삭제 복구 7일
- IAM 인스턴스 역할: secret read + S3 R/W + CloudWatch Logs + `RiderServer` custom metrics put
- DLM: 매일 EBS 스냅샷, 7일 보존

Terraform은 8000 포트 보안그룹까지만 만든다. Telegram webhook 공개 URL에는 HTTPS/TLS 종료 계층이 별도로 필요하다(ALB, reverse proxy, Cloudflare Tunnel 등). 운영에서 외부 공개가 필요하면 `app_ingress_cidr`를 최소 CIDR로 열고, TLS 종료와 Admin 인증을 먼저 구성한다.

state 는 `s3://terraform-state-654654307503/rider-server/terraform.tfstate` 에 저장.

## 사용

```bash
cd deploy/terraform
cp example.tfvars terraform.tfvars   # ssh_ingress_cidr 와 app_ingress_cidr 를 운영자/Agent 출처 CIDR 로
terraform init
terraform plan
terraform apply
```

운영 기본값은 t4g.small 이며, production `terraform.tfvars`에도 아래 값을 명시해 둔다.

```hcl
instance_type = "t4g.small"
```

## EC2 replacement/destroy 중단 조건

이 Terraform 구성은 root volume local PostgreSQL data를 사용한다. `aws_instance.app`의 root block
device는 `delete_on_termination = true`이므로 EC2 replacement 또는 destroy가 local DB data 손실로
이어질 수 있다.

Memory hardening 중 `terraform plan`에 `-/+ aws_instance.app`가 보이면 즉시 중단한다. 이 작업에서
허용되는 변경은 instance type change이며 replacement가 아니다. `aws_instance.app`에는
`prevent_destroy = true`를 둬서 accidental destroy를 막는다.

의도적으로 EC2를 교체해야 하는 별도 작업이라면 최신 EBS snapshot과 DB dump를 확인하고, 복원 절차를
따로 승인받은 뒤에만 guard를 임시 해제한다. 교체가 끝나면 `prevent_destroy = true`를 즉시 복구한다.

private key 는 apply 후 `.secrets/rider-server-keypair.pem` 에 생성된다(git 무시). 접속:

```bash
terraform output -raw ssh_command
```

## 다음 단계(앱+DB 배포)

1. EC2 에 `deploy/docker-compose.yml` + `deploy/env/` 업로드(또는 git clone).
2. Secrets Manager 에서 DB `username`/`password`/`dbname`을 받아 `RIDER_POSTGRES_*` 환경변수로 주입하거나, compose의 `DATABASE_URL`을 외부 DB 값으로 override.
3. 외부 공개가 필요하면 SG `app_ingress_cidr`와 `RIDER_BACKEND_BIND=0.0.0.0`을 함께 명시하고, TLS/reverse proxy/Admin 인증을 먼저 구성.
4. 최신 DB 백업 또는 EC2/EBS 스냅샷을 확인한 뒤 `RIDER_DB_MIGRATION_BACKUP_CONFIRMED=1`을 주입한다. 운영 EC2에서는 GitHub Actions/ECR에서 만든 image를 `RIDER_SERVER_IMAGE`로 지정하고 `docker compose pull` 후 `docker compose up -d --no-build --remove-orphans`로 재시작한다.
5. `deploy/cloudwatch/push_metrics.sh`와 `deploy/cloudwatch/rider-metrics.service`를 설치·enable해 custom metric을 적재.
6. Secrets Manager 값은 앱이 자동으로 읽지 않는다. `app-secrets`에 텔레그램 webhook/봇 토큰 값을 보관한다면 배포 스크립트나 host 환경에서 `RIDER_TELEGRAM_WEBHOOK_SECRET`·`RIDER_TELEGRAM_BOT_TOKEN`으로 주입하고, `deploy/env/telegram-webhook.env`의 `env:` ref가 그 값을 가리키게 한다. 또는 Admin 웹앱의 고객별 텔레그램 설정에 값을 입력한 뒤 send gate를 활성화한다.
