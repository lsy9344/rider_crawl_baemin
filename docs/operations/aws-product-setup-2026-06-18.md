# AWS product setup log - 2026-06-18

This document records what was checked or changed while preparing the Rider Server product environment.

## Current Product Environment

- AWS account: `654654307503`
- AWS region: `ap-northeast-2` (Seoul)
- IAM identity used by CLI: `arn:aws:iam::654654307503:user/noah_host`
- Terraform state backend: `s3://terraform-state-654654307503/rider-server/terraform.tfstate`
- Terraform workspace: `default`

## Local Tools Checked

The following tools are already installed and usable on this PC:

- AWS CLI: `aws-cli/2.25.13`
- Terraform: `v1.15.6`
- Docker: `28.0.4`
- Docker Compose: `v2.34.0-desktop.1`
- uv: `0.11.18`
- Python: `3.11.9`
- winget: `v1.28.240`

No new CLI package install was needed on this date.

## Local Settings Fixed

- Confirmed AWS CLI credentials can call STS successfully.
- Fixed the Terraform-generated EC2 private key ACL:
  - File: `deploy/terraform/.secrets/rider-server-keypair.pem`
  - Result: Windows OpenSSH now accepts the key.
- Fixed local SSH config ACL:
  - File: `C:\Users\KimYS\.ssh\config`
  - Result: normal `ssh` works without `-F NUL`.

## AWS Infrastructure Found Running

Terraform state already contains the Rider Server AWS resources.

- EC2 instance: `i-0e6a710a505e6b3c4`
- EC2 type: `t4g.micro`
- EC2 state: `running`
- Public IP: `54.116.103.149`
- VPC: `vpc-04055e3cd4a77e294`
- Artifact bucket: `rider-server-artifacts-654654307503`
- DB secret name: `rider-server/db-credentials`
- App secret name: `rider-server/app-secrets`
- CloudWatch metric namespace: `RiderServer`
- Alarm SNS topic: `arn:aws:sns:ap-northeast-2:654654307503:rider-server-alarms`

Current security group shape:

- SSH `22`: allowed only from the configured operator `/32` CIDR.
- Backend API `8000`: currently open from `0.0.0.0/0`.
- PostgreSQL `5432`: not opened externally.
- Outbound: open.

## Server Runtime Checked

SSH command:

```powershell
ssh -i deploy\terraform\.secrets\rider-server-keypair.pem ubuntu@54.116.103.149
```

Runtime state on EC2:

- Host path: `/opt/rider-server`
- Remote repo path: `/opt/rider-server/repo`
- Remote repo commit: `3739a81` (`feat: tenant별 텔레그램 설정 + AWS 인프라(IaC) + 평문 자격증명 전환`)
- Containers:
  - `rider-db-1`: running, healthy
  - `rider-backend-api-1`: running
  - `rider-scheduler-1`: running
- Backend health endpoint: `http://54.116.103.149:8000/health` returns `{"status":"ok"}`
- Admin endpoint after public access deployment: `http://54.116.103.149:8000/admin` returns `200 text/html`.
- Metrics endpoint on EC2: `http://127.0.0.1:8000/metrics/operational` returns operational metrics.
- `rider-metrics.service`: enabled and active.
- CloudWatch metric push: service logs show 13 metrics pushed every 60 seconds.
- Operating Docker Compose project name: `rider`.
- Operating DB volume: `rider_postgres-data`.
- A temporary accidental `deploy` compose project was created during redeploy, then removed. Its empty `deploy_postgres-data` volume was also removed.

Local source-of-truth note:

- Follow-up review decision: local code is the latest source of truth for the next deploy.
- Before the next EC2 redeploy, create a clean commit/tag from the local workspace and deploy that artifact. Do not treat the remote dirty working tree as the canonical release source.

## Product Code Changes Deployed

Deployed on 2026-06-18:

- Admin platform account form now accepts Coupang login ID, login password, verification email address, and email app password directly.
- Backend stores platform account `password` and `verification_email_app_password` values directly in PostgreSQL.
- Audit logs do not store the actual password values. They only record password change state such as `set`, `cleared`, or `unchanged`.
- Verified after deploy:
  - `http://54.116.103.149:8000/health` returned `{"status":"ok"}`.
  - `http://54.116.103.149:8000/admin` returned `200 text/html`.
  - Admin HTML contains the new `DB에 그대로 저장` guidance.

## Terraform Checks

Commands run from `deploy/terraform`:

```powershell
terraform fmt -check
terraform validate
terraform workspace show
terraform state list
terraform plan -detailed-exitcode -no-color
```

Results:

- Format check: passed.
- Validate: passed.
- Workspace: `default`.
- State backend: reachable.
- State contains the expected EC2, VPC, S3, Secrets Manager, CloudWatch, IAM, DLM, and key resources.
- Plan was checked only. It was not applied.

Important plan note:

- User decision on 2026-06-18: keep the current `8000` exposure.
- Local `deploy/terraform/terraform.tfvars` now sets `app_ingress_cidr = "0.0.0.0/0"` so future Terraform applies keep port `8000` open.
- Local `deploy/terraform/terraform.tfvars` now sets `alarm_email = "progression.two@gmail.com"`.
- If Terraform is applied with the alarm email, AWS will send a subscription confirmation email. Alarms are not delivered to that inbox until the email confirmation link is clicked.
- Terraform apply was run on 2026-06-18 after the user decision.
- Result: `6 added, 7 changed, 0 destroyed`.
- The SNS subscription for `progression.two@gmail.com` has been confirmed and now has a concrete subscription ARN.

## Secrets State

Secrets were checked without writing secret values into this document.

- `rider-server/db-credentials`: populated.
- `rider-server/app-secrets`:
  - `RIDER_TELEGRAM_BOT_TOKEN`: empty
  - `RIDER_TELEGRAM_WEBHOOK_SECRET`: empty
- User decision on 2026-06-18: do not use AWS Secrets Manager for Telegram app values. The user will enter Telegram values directly in the web app.
- User decision on 2026-06-18: do not use AWS Secrets Manager for Coupang platform account values. The user will enter Coupang login ID, login password, verification email address, and email app password directly in the Admin web app. These values are stored in PostgreSQL in the `platform_accounts` table.

## User Decisions Recorded

Recorded on 2026-06-18:

1. Backend exposure:
   - Keep current state.
   - Port `8000` stays open to the internet: `0.0.0.0/0`.
2. Telegram values:
   - The user will enter values directly in the web app.
   - Do not rely on AWS Secrets Manager for Telegram app values.
3. Coupang platform account values:
   - The user will enter Coupang ID/PW/email app password directly in the web app.
   - The backend stores those values directly in DB columns.
   - Plain meaning: the server can use them without a separate AWS secret setup, but DB backup/access must be treated as sensitive.
4. Admin access:
   - No IP allowlist.
   - No external identity provider or reverse proxy auth for now.
   - `/admin` public access mode is enabled through `RIDER_ADMIN_PUBLIC_ACCESS=1`.
   - Plain meaning: anyone who finds the public Admin URL can try to open it. "Only I know the address" is not a strong security control.
5. Alarm receiver:
   - `progression.two@gmail.com`
6. Agent rollout:
   - Agent name: `jena-5800h`
   - Agent ID: `b781de75-1386-4d91-be00-67381ecca828`

## Remaining User Actions

- Enter Telegram bot/channel values in the web app.
- Enter each 업체's Coupang login ID, login password, verification email address, and email app password in the Admin web app.
- Keep the Admin URL private if public access mode remains enabled.

## Current Alarm Note

- `rider-server-heartbeat-stale` is currently `ALARM`.
- Plain meaning: the server does not see an Agent heartbeat yet.
- This is expected until the first Agent (`jena-5800h`) is registered and running.

## Useful Commands

Check local AWS identity:

```powershell
aws sts get-caller-identity --output json
```

Check Terraform drift without applying:

```powershell
cd deploy\terraform
terraform plan -detailed-exitcode -no-color
```

Check backend health:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri http://54.116.103.149:8000/health
```

Check EC2 containers:

```powershell
ssh -i deploy\terraform\.secrets\rider-server-keypair.pem ubuntu@54.116.103.149 "docker ps"
```

Check CloudWatch alarms:

```powershell
aws cloudwatch describe-alarms --alarm-name-prefix rider-server --output table
```
