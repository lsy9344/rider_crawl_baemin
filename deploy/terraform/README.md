# rider_server AWS 인프라 (Terraform)

Epic 5 클라우드 control plane 을 AWS Seoul(`ap-northeast-2`)에 IaC 로 구축한다.

## 사양서 대비 결정(비용 최소화)

architecture.md 는 EC2 + **RDS** + S3 + Secrets Manager + CloudWatch 를 명시하지만,
운영 비용 최소화 요구(월 $40 미만, 크롤링 부하는 로컬 PC=Agent 가 담당)에 따라 다음과 같이 결정했다:

- **DB: RDS 대신 EC2 1대에 Docker Compose 로 PostgreSQL 동거.** RDS 비용(~$12+/월) 제거.
  사양서의 RDS PITR≥7일 백업 요구는 **DLM 일일 EBS 스냅샷(7일 보존)** 으로 대체한다.
- **NAT Gateway 없음.** 퍼블릭 서브넷 + IGW 만 사용(NAT 시간당 과금 ~$35/월 회피). EC2 가
  공인 IP(EIP)로 직접 outbound.
- **EC2 t4g.micro (ARM).** control plane(FastAPI+scheduler)은 가벼워 충분. 월 ~$6.

예상 월 비용: **약 $8~12** (t4g.micro + gp3 20GB + S3/Secrets/전송).

추후 규모가 커지면(트리거: target>20~30, kakao lag>120s) RDS 분리/전용 worker PC 로 확장한다.

## 리소스

- VPC(10.50.0.0/16) + 퍼블릭 서브넷 + IGW + 라우트
- 보안그룹: SSH(22, 운영자 IP 한정), 8000(앱). 5432 는 외부 미개방(호스트 내부만)
- EC2 t4g.micro + gp3 20GB(암호화) + EIP + Docker 설치 user-data
- S3 아티팩트 버킷(비공개/암호화/버저닝)
- Secrets Manager: `rider-server/db-credentials`(자동 생성 비번), `rider-server/app-secrets`(운영자 입력)
- IAM 인스턴스 역할: secret read + S3 R/W + CloudWatch Logs
- DLM: 매일 EBS 스냅샷, 7일 보존

state 는 `s3://terraform-state-654654307503/rider-server/terraform.tfstate` 에 저장.

## 사용

```bash
cd deploy/terraform
cp example.tfvars terraform.tfvars   # ssh_ingress_cidr 를 운영자 IP 로
terraform init
terraform plan
terraform apply
```

private key 는 apply 후 `.secrets/rider-server-keypair.pem` 에 생성된다(git 무시). 접속:

```bash
terraform output -raw ssh_command
```

## 다음 단계(앱+DB 배포)

1. EC2 에 `deploy/docker-compose.yml` + `deploy/env/` 업로드(또는 git clone).
2. Secrets Manager 에서 DB `database_url` 을 받아 `DATABASE_URL` 로 주입.
3. `docker compose up --build -d` → migrate one-shot → backend-api + scheduler 기동.
4. `app-secrets` 에 텔레그램 webhook/봇 토큰 값 입력 후 send 게이트 활성화.
