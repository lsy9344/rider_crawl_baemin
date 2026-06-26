"""Server deployment configuration guardrails.

These tests lock the minimum server/DB setup needed for the refactored product
to run as a durable control plane. They avoid starting Docker or connecting to
Postgres; the checks are static or use injected seams.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

from rider_server.main import create_app
from rider_server.settings import Settings


def test_production_app_requires_database_url() -> None:
    settings = Settings(
        app_env="production",
        app_version="9.9.9",
        build_sha=None,
        build_time=None,
        database_url=None,
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(settings)


def test_server_dockerfile_installs_full_server_runtime_dependencies() -> None:
    dockerfile = Path("deploy/Dockerfile.server").read_text(encoding="utf-8")

    for dependency in (
        "sqlalchemy[asyncio]",
        "alembic",
        "asyncpg",
        "jinja2",
    ):
        assert dependency in dockerfile
    assert "COPY migrations /app/migrations" in dockerfile


def _requirement_key(spec: str) -> str:
    """버전/extra 를 떼고 정규화한 패키지 이름(예: ``uvicorn[standard]>=0.30`` → ``uvicorn``)."""

    # 버전 연산자 앞까지가 name(+optional [extra]); extra 와 버전 제약은 비교에서 제외한다.
    name = re.split(r"[<>=!~ ]", spec.strip(), maxsplit=1)[0]
    name = name.split("[", maxsplit=1)[0]
    return name.strip().lower()


def test_server_dockerfile_dependency_list_matches_pyproject_server_extra() -> None:
    """Task 8-C: Dockerfile 의 pip install 목록이 pyproject ``server`` extra 와 정확히
    일치(정본화). 한쪽에만 추가/누락된 server 의존성을 정적으로 차단한다."""

    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    pyproject_server = {
        _requirement_key(dep)
        for dep in data["project"]["optional-dependencies"]["server"]
    }

    dockerfile = Path("deploy/Dockerfile.server").read_text(encoding="utf-8")
    # ``RUN pip install ... \`` 블록의 따옴표로 감싼 각 의존성 문자열을 추출한다.
    install_block = dockerfile[dockerfile.index("pip install") :]
    install_block = install_block[: install_block.index("\n\n")]
    dockerfile_deps = {
        _requirement_key(match) for match in re.findall(r'"([^"]+)"', install_block)
    }

    assert dockerfile_deps == pyproject_server, (
        "Dockerfile.server 의 pip install 목록과 pyproject server extra 가 어긋났습니다. "
        f"Dockerfile-only={dockerfile_deps - pyproject_server}, "
        f"pyproject-only={pyproject_server - dockerfile_deps}"
    )


def test_compose_defines_durable_db_and_migration_service() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert "\n  db:\n" in compose
    assert "postgres:" in compose
    assert "postgres-data:" in compose
    assert "\n  migrate:\n" in compose
    assert "python -m rider_server.db.migrate upgrade head" in compose
    assert "DATABASE_URL" in compose
    assert "RIDER_DB_MIGRATION_BACKUP_CONFIRMED" in compose
    assert "condition: service_healthy" in compose
    assert "condition: service_completed_successfully" in compose


def test_ci_validates_deployment_compose_and_server_image() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "deployment-config:" in workflow
    assert "docker compose -f deploy/docker-compose.yml config" in workflow
    assert "Pre-pull BuildKit image" in workflow
    assert "docker pull moby/buildkit:buildx-stable-1" in workflow
    assert "docker/build-push-action" in workflow
    assert "cache-from: type=gha" in workflow
    assert "cache-to: type=gha,mode=max" in workflow
    assert "RIDER_POSTGRES_PASSWORD" in workflow
    assert "RIDER_DB_MIGRATION_BACKUP_CONFIRMED" in workflow
    assert "DEPLOYMENT_RESULT" in workflow
    assert "Deployment config" in workflow


def test_ci_validates_memory_hardening_db_connection_budget() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert 'RIDER_DB_RESERVED_CONNECTIONS: "10"' in workflow
    assert "RIDER_UVICORN_WORKERS=1 RIDER_DB_POOL_SIZE=2 RIDER_DB_MAX_OVERFLOW=2 python scripts/check_db_connection_budget.py --postgres-max-connections 100" in workflow


def test_ci_postgres_gate_uses_linux_service_and_pr_path_filter() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "changes:" in workflow
    assert "dorny/paths-filter@v3" in workflow
    assert "postgres-tests:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "postgres:16" in workflow
    assert "needs: changes" in workflow
    assert "needs.changes.outputs.postgres == 'true'" in workflow
    assert "ikalnytskyi/action-setup-postgres" not in workflow


def test_quick_stage_excludes_architecture_e2e_and_concurrency() -> None:
    script = Path("scripts/test.ps1").read_text(encoding="utf-8")

    assert "not architecture" in script
    assert "not e2e" in script
    assert "not concurrency" in script


def test_ci_runs_backend_health_smoke_off_pr_path() -> None:
    """Task 8-C: push/schedule 에서 backend /health smoke 가 돌고, fast PR 경로는 건너뛴다."""

    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "Backend /health smoke" in workflow
    assert "if: github.event_name != 'pull_request'" in workflow
    assert "http://127.0.0.1:8000/health" in workflow
    assert "docker run -d --name rider-health-smoke" in workflow


def test_ci_deploys_main_to_ec2_via_github_hosted_ssm_after_quality_gates() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "deploy-production:" in workflow
    assert "needs: [local-tests, postgres-tests, deployment-config]" in workflow
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "permissions:" in workflow
    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials" in workflow
    assert "docker/build-push-action" in workflow
    assert "push: true" in workflow
    assert "linux/arm64" in workflow
    assert "aws ssm send-command" in workflow
    assert "AWS-RunShellScript" in workflow
    assert "aws ssm wait command-executed" in workflow
    assert "aws ssm get-command-invocation" in workflow
    assert "Deploy production through SSM" in workflow
    assert "ssh-keyscan" not in workflow
    assert "DEPLOY_EC2_INSTANCE_ID" in workflow
    assert "DEPLOY_AWS_ROLE_ARN" in workflow
    assert "ECR_REPOSITORY" in workflow
    assert "RIDER_SERVER_IMAGE=" in workflow
    assert "git fetch origin main" in workflow
    assert "git checkout -B main -f FETCH_HEAD" in workflow
    compose_files = "-f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml"
    assert "set -a" not in workflow
    assert ". ./.env" not in workflow
    assert (
        f"docker compose --env-file .env -p rider {compose_files} "
        "up -d --no-build --remove-orphans"
    ) in workflow
    assert f"docker compose --env-file .env -p rider {compose_files} ps" in workflow
    deploy_job = workflow[workflow.index("deploy-production:") :]
    deploy_job = deploy_job[: deploy_job.index("\n  ci-summary:")]
    assert "self-hosted" not in deploy_job
    assert "up --build" not in deploy_job
    assert "production health ok after ${i}s" in workflow
    assert "logs --tail=80 backend-api" in workflow
    assert "curl -fsS http://127.0.0.1:8000/health" in workflow
    assert "Production deploy" in workflow


def test_ci_deploy_waits_for_docker_daemon_before_remote_compose() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    deploy_script = workflow[workflow.index("Deploy production through SSM") :]
    deploy_script = deploy_script[: deploy_script.index("\n  ci-summary:")]

    assert "bash <<'BASH'" in deploy_script
    assert "\n          BASH" in deploy_script
    assert "export HOME=/root" in deploy_script
    assert "git config --global --add safe.directory /opt/rider-server/repo" in deploy_script
    assert "docker info >/dev/null 2>&1" in deploy_script
    assert "docker daemon did not become ready" in deploy_script
    assert deploy_script.index("docker info >/dev/null 2>&1") < deploy_script.index(
        "docker compose --env-file .env -p rider"
    )


def test_ci_serializes_main_production_deploys_without_cancelling_them() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}" in workflow

    deploy_job = workflow[workflow.index("deploy-production:") :]
    deploy_job = deploy_job[: deploy_job.index("\n  ci-summary:")]

    assert "concurrency:" in deploy_job
    assert "group: production-deploy-main" in deploy_job
    assert "cancel-in-progress: false" in deploy_job


def test_ci_validates_terraform_without_touching_live_aws_state() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "Validate Terraform config" in workflow
    assert "hashicorp/setup-terraform" in workflow
    assert "terraform -chdir=deploy/terraform init -backend=false" in workflow
    assert "terraform -chdir=deploy/terraform fmt -check" in workflow
    assert "terraform -chdir=deploy/terraform validate" in workflow
    assert "terraform apply" not in workflow


def test_terraform_default_instance_type_matches_memory_hardened_production() -> None:
    variables = Path("deploy/terraform/variables.tf").read_text(encoding="utf-8")
    readme = Path("deploy/terraform/README.md").read_text(encoding="utf-8")

    block = variables[variables.index('variable "instance_type"') :]
    block = block[: block.index('variable "root_volume_gb"')]

    assert 'default     = "t4g.small"' in block
    assert "EC2 t4g.micro (ARM) 기본값" not in readme
    assert "운영 기본값은 t4g.small" in readme


def test_manual_deploy_docs_do_not_reintroduce_ec2_docker_builds() -> None:
    active_docs = {
        "README.md": Path("README.md").read_text(encoding="utf-8"),
        "deploy/terraform/README.md": Path("deploy/terraform/README.md").read_text(
            encoding="utf-8"
        ),
    }

    for path, text in active_docs.items():
        assert "up --build" not in text, path
        assert "docker compose up --build" not in text, path

    assert "--no-build" in active_docs["deploy/terraform/README.md"]


def test_ci_deploy_runs_host_preflight_before_remote_compose() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    deploy_script = workflow[workflow.index("Deploy production through SSM") :]
    deploy_script = deploy_script[: deploy_script.index("\n  ci-summary:")]

    assert "bash scripts/production_deploy_preflight.sh" in deploy_script
    assert deploy_script.index("bash scripts/production_deploy_preflight.sh") < deploy_script.index(
        "docker compose --env-file .env -p rider"
    )


def test_production_health_workflow_checks_ec2_ssm_health_and_cloudwatch() -> None:
    workflow = Path(".github/workflows/production-health.yml").read_text(encoding="utf-8")

    assert "name: Production Health" in workflow
    assert "schedule:" in workflow
    assert "concurrency:" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "aws-actions/configure-aws-credentials" in workflow
    assert "aws ssm send-command" in workflow
    assert "AWS-RunShellScript" in workflow
    assert "bash scripts/production_health_check.sh" in workflow
    assert "aws cloudwatch describe-alarms" in workflow
    assert "rider-server-host-mem-available-low" in workflow
    assert "rider-server-host-swap-used-high" in workflow


def test_production_operation_scripts_guard_memory_runner_disk_and_oom() -> None:
    preflight = Path("scripts/production_deploy_preflight.sh").read_text(encoding="utf-8")
    health = Path("scripts/production_health_check.sh").read_text(encoding="utf-8")

    assert "MemAvailable" in preflight
    assert "df -Pk" in preflight
    assert "Runner.Listener|Runner.Worker|actions-runner" in preflight
    assert "docker info" in preflight

    assert "curl -fsS http://127.0.0.1:8000/health" in health
    assert "docker compose --env-file .env -p rider" in health
    assert "journalctl -k --since" in health
    assert "out of memory|oom|killed process" in health
    assert "MemAvailable" in health


def test_production_operation_scripts_use_unique_runner_tempfiles() -> None:
    for script_path in (
        "scripts/production_deploy_preflight.sh",
        "scripts/production_health_check.sh",
    ):
        script = Path(script_path).read_text(encoding="utf-8")

        assert "mktemp /tmp/rider-runner-processes.XXXXXX" in script
        assert "trap cleanup EXIT" in script
        assert ">/tmp/rider-runner-processes.txt" not in script


def test_git_attributes_keep_linux_runner_files_lf_normalized() -> None:
    attributes = Path(".gitattributes").read_text(encoding="utf-8")

    assert "*.sh text eol=lf" in attributes
    assert "*.yml text eol=lf" in attributes
    assert "*.yaml text eol=lf" in attributes


def test_ci_slack_notification_skips_cancelled_workflow_runs() -> None:
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    notify_step = workflow[workflow.index("      - name: Notify Slack") :]

    assert "if: ${{ always() && !cancelled() }}" in notify_step


def test_compose_server_services_can_use_prebuilt_image_without_production_build() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    for service in ("migrate", "backend-api", "scheduler", "queue-recovery", "telegram-dispatch"):
        block = compose[compose.index(f"\n  {service}:\n") :]
        next_service = re.search(r"\n  [a-z0-9-]+:\n", block[len(f"\n  {service}:\n") :])
        if next_service:
            block = block[: len(f"\n  {service}:\n") + next_service.start()]
        assert "image: ${RIDER_SERVER_IMAGE:-rider-server:dev}" in block


def test_compose_backend_host_port_is_configurable() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert "${RIDER_BACKEND_PORT:-8000}:8000" in compose


def test_compose_defines_backend_and_scheduler_healthchecks() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    backend = compose[compose.index("\n  backend-api:\n") : compose.index("\n  scheduler:\n")]
    scheduler = compose[compose.index("\n  scheduler:\n") :]
    assert "healthcheck:" in backend
    assert "http://127.0.0.1:8000/health" in backend
    assert "healthcheck:" in scheduler
    assert "SCHEDULER_HEALTH_FILE" in scheduler


def test_terraform_defaults_do_not_publicly_expose_app_port() -> None:
    variables = Path("deploy/terraform/variables.tf").read_text(encoding="utf-8")
    security = Path("deploy/terraform/security.tf").read_text(encoding="utf-8")
    example = Path("deploy/terraform/example.tfvars").read_text(encoding="utf-8")

    app_var = variables[variables.index('variable "app_ingress_cidr"') :]
    app_var = app_var[: app_var.index('variable "db_name"')]
    assert 'default     = ""' in app_var
    assert "count             = var.app_ingress_cidr == \"\" ? 0 : 1" in security
    assert "기본은 0.0.0.0/0" not in example
    assert "빈 값이면 앱 포트 ingress 규칙이 생성되지 않는다" in example


def test_terraform_cloudwatch_covers_runbook_alert_signals() -> None:
    cloudwatch = Path("deploy/terraform/cloudwatch.tf").read_text(encoding="utf-8")

    for metric_name in (
        "HostMemAvailablePercent",
        "HostSwapUsedPercent",
        "kakao_queue_lag_seconds",
        "auth_required_count",
        "gmail_reauth_required_count",
        "crawl_error_rate_by_platform_BAEMIN",
        "crawl_samples_by_platform_BAEMIN",
        "crawl_error_rate_by_platform_COUPANG",
        "crawl_samples_by_platform_COUPANG",
        "telegram_error_count",
    ):
        assert metric_name in cloudwatch

    for alert_name in (
        "host-mem-available-low",
        "host-swap-used-high",
        "queue-lag",
        "auth-required",
        "gmail-reauth-required",
        "api-error-rate-baemin",
        "api-error-rate-coupang",
        "telegram-errors",
    ):
        assert alert_name in cloudwatch

    assert "crawl_error_min_samples" in cloudwatch
    assert "crawl_error_rate_alarm_threshold" in cloudwatch


def test_terraform_prevents_accidental_ec2_destroy_with_local_postgres() -> None:
    compute = Path("deploy/terraform/compute.tf").read_text(encoding="utf-8")
    readme = Path("deploy/terraform/README.md").read_text(encoding="utf-8")

    app_resource = compute[compute.index('resource "aws_instance" "app"') :]
    app_resource = app_resource[: app_resource.index('\nresource "aws_eip" "app"')]
    assert "prevent_destroy = true" in app_resource
    assert "delete_on_termination = true" in app_resource
    assert "root volume local PostgreSQL data" in readme
    assert "-/+ aws_instance.app" in readme


def test_terraform_defines_github_oidc_ecr_and_ssm_deploy_role() -> None:
    deploy = Path("deploy/terraform/github_deploy.tf").read_text(encoding="utf-8")
    variables = Path("deploy/terraform/variables.tf").read_text(encoding="utf-8")
    outputs = Path("deploy/terraform/outputs.tf").read_text(encoding="utf-8")

    assert "aws_iam_openid_connect_provider" in deploy
    assert "token.actions.githubusercontent.com" in deploy
    assert 'default     = "lsy9344/rider_crawl_baemin"' in variables
    assert 'default     = "main"' in variables
    assert 'default     = "rider-server"' in variables
    assert "repo:${var.github_repository}:ref:refs/heads/${var.github_deploy_branch}" in deploy
    assert "aws_ecr_repository" in deploy
    assert "ssm:SendCommand" in deploy
    assert "ssm:GetCommandInvocation" in deploy
    assert "AWS-RunShellScript" in deploy
    assert "cloudwatch:DescribeAlarms" in deploy
    assert "ecr:GetAuthorizationToken" in deploy
    assert "ecr:PutImage" in deploy
    assert "ecr:BatchGetImage" in deploy
    assert "github_deploy_role_arn" in outputs
    assert "server_ecr_repository_url" in outputs


def test_terraform_readme_documents_public_https_boundary() -> None:
    readme = Path("deploy/terraform/README.md").read_text(encoding="utf-8")

    assert "Terraform은 8000 포트 보안그룹까지만 만든다" in readme
    assert "Telegram webhook 공개 URL에는 HTTPS/TLS 종료 계층이 별도로 필요하다" in readme


def test_terraform_secrets_keep_recovery_window() -> None:
    secrets = Path("deploy/terraform/storage_secrets.tf").read_text(encoding="utf-8")

    assert "recovery_window_in_days = 0" not in secrets
    assert secrets.count("recovery_window_in_days = 7") >= 2


def test_cloudwatch_metric_absence_is_alarm_state() -> None:
    cloudwatch = Path("deploy/terraform/cloudwatch.tf").read_text(encoding="utf-8")

    assert 'treat_missing_data = "notBreaching"' not in cloudwatch
    assert cloudwatch.count('"breaching"') >= 4


def test_backend_api_env_documents_admin_allowed_origins() -> None:
    env = Path("deploy/env/backend-api.env").read_text(encoding="utf-8")

    assert "RIDER_ADMIN_ALLOWED_ORIGINS" in env


def test_backend_api_env_does_not_enable_public_admin_by_default() -> None:
    env = Path("deploy/env/backend-api.env").read_text(encoding="utf-8")

    assert "RIDER_ADMIN_PUBLIC_ACCESS=1" not in env
    assert "RIDER_ADMIN_PUBLIC_ACCESS=true" not in env.lower()


def test_public_admin_override_limits_access_to_owner_ip() -> None:
    env = Path("deploy/env/backend-api.dev-public-admin.env").read_text(encoding="utf-8")

    assert "RIDER_ADMIN_PUBLIC_ACCESS=1" in env
    assert "RIDER_ADMIN_IP_ALLOWLIST=175.196.151.158/32" in env
    assert "APP_ENV=development" not in env


def test_compose_passes_telegram_env_ref_values_to_backend() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")
    telegram_env = Path("deploy/env/telegram-webhook.env").read_text(encoding="utf-8")

    backend = compose[compose.index("\n  backend-api:\n") : compose.index("\n  scheduler:\n")]
    assert "- ./env/telegram-webhook.env" in backend
    assert "TELEGRAM_WEBHOOK_SECRET_REF=env:RIDER_TELEGRAM_WEBHOOK_SECRET" in telegram_env
    assert "TELEGRAM_BOT_TOKEN_REF=env:RIDER_TELEGRAM_BOT_TOKEN" in telegram_env
    assert (
        "RIDER_TELEGRAM_WEBHOOK_SECRET: ${RIDER_TELEGRAM_WEBHOOK_SECRET:?set RIDER_TELEGRAM_WEBHOOK_SECRET}"
        in backend
    )
    assert (
        "RIDER_TELEGRAM_BOT_TOKEN: ${RIDER_TELEGRAM_BOT_TOKEN:?set RIDER_TELEGRAM_BOT_TOKEN}"
        in backend
    )


def test_telegram_env_documents_default_supported_env_secret_refs() -> None:
    env = Path("deploy/env/telegram-webhook.env").read_text(encoding="utf-8")

    assert "\nTELEGRAM_WEBHOOK_SECRET_REF=env:RIDER_TELEGRAM_WEBHOOK_SECRET" in env
    assert "\nTELEGRAM_BOT_TOKEN_REF=env:RIDER_TELEGRAM_BOT_TOKEN" in env
    assert "\n# TELEGRAM_WEBHOOK_SECRET_REF=env:RIDER_TELEGRAM_WEBHOOK_SECRET" not in env
    assert "\n# TELEGRAM_BOT_TOKEN_REF=env:RIDER_TELEGRAM_BOT_TOKEN" not in env
    assert "vault://telegram" not in env


def test_terraform_readme_does_not_claim_app_secrets_autoload() -> None:
    readme = Path("deploy/terraform/README.md").read_text(encoding="utf-8")

    assert "app-secrets 에 텔레그램 webhook/봇 토큰 값 입력 후 send 게이트 활성화" not in readme
    assert "Secrets Manager 값은 앱이 자동으로 읽지 않는다" in readme
    assert "RIDER_TELEGRAM_WEBHOOK_SECRET" in readme
    assert "RIDER_TELEGRAM_BOT_TOKEN" in readme


def test_refactoring_docs_record_intentional_tenant_telegram_plaintext_exception() -> None:
    work_order = Path("docs/refactoring/detailed_work_order.md").read_text(
        encoding="utf-8"
    )
    direction = Path("docs/refactoring/refactoring_improvement_direction.md").read_text(
        encoding="utf-8"
    )

    for doc in (work_order, direction):
        assert "Tenant Telegram token/webhook secret DB 평문 컬럼 저장은 의도된 예외" in doc
        assert "audit log에는 값이 아니라 변경 여부만 기록" in doc


def test_refactoring_docs_record_intentional_crawl_payload_credential_handoff() -> None:
    direction = Path("docs/refactoring/refactoring_improvement_direction.md").read_text(
        encoding="utf-8"
    )

    assert "Crawl job payload에는 비밀번호류 필드가 들어갈 수 있다" in direction
    assert "password, verification_email_app_password" in direction
    assert "claim 응답/DB payload_json 경계는 secret-bearing surface" in direction


def test_refactoring_review_report_is_marked_as_historical() -> None:
    report = Path("docs/refactoring/refactoring_review_report.md").read_text(
        encoding="utf-8"
    )

    assert "역사적 검토 기록" in report
    assert "2026-06-15 당시" in report
    assert "최신 상태 판단은 현재 코드/테스트/deploy 검증을 기준" in report


def test_backup_runbook_documents_current_migration_head() -> None:
    runbook = Path("docs/runbooks/backup-restore.md").read_text(encoding="utf-8")
    latest = Path("migrations/versions/0020_fleet_claim_scale_hardening.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0020_fleet_claim_scale"' in latest
    assert "0020_fleet_claim_scale" in runbook
    assert "0005_audit_fields_and_agent_token_revoke" not in runbook


def test_test_strategy_matches_test_script_no_pth_rewrite() -> None:
    doc = Path("docs/qa/test-execution-strategy.md").read_text(encoding="utf-8")
    script = Path("scripts/test.ps1").read_text(encoding="utf-8")

    assert ".pth` 파일을 UTF-8로 다시 쓴다" not in doc
    assert "`.pth` 파일을 수정하지 않는다" in doc
    assert "Set-Content" not in script


def test_readme_documents_coupang_peak_dashboard_as_single_primary_url() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert (
        "쿠팡이츠는 `https://partner.coupangeats.com/page/peak-dashboard`"
        in readme
    )
    assert (
        "| `COUPANG_EATS_URL` | 쿠팡 주 URL(기본 "
        "`https://partner.coupangeats.com/page/peak-dashboard`)"
        in readme
    )
    assert "rider-performance는 크롤러 내부에서 보조 조회될 수 있습니다" in readme
    assert (
        "보조 URL(쿠팡 피크 대시보드): 쿠팡이츠 탭에서만 사용하며 활성 쿠팡이츠 탭은 반드시 입력해야 합니다"
        not in readme
    )
    assert "| `PEAK_DASHBOARD_URL` | 쿠팡 피크 대시보드 보조 URL" not in readme


def test_readme_local_server_commands_install_server_extra() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert 'pip install -e ".[dev,server]"' in readme
    server_section = readme[readme.index("## 서버/DB 실행") :]
    assert "server extra" in server_section


def test_readme_documents_migration_backup_confirmation() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    server_section = readme[readme.index("## 서버/DB 실행") :]
    assert "RIDER_DB_MIGRATION_BACKUP_CONFIRMED" in server_section
    assert "백업 확인" in server_section


def test_wheel_includes_crawl_server_and_agent_without_server_deps_in_base() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    packages = set(data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])
    assert {"src/rider_crawl", "src/rider_server", "src/rider_agent"} <= packages

    base_dependencies = {dependency.replace(" ", "") for dependency in data["project"]["dependencies"]}
    assert "IMAPClient>=3.0.1" in base_dependencies
    for server_only_dependency in ("fastapi", "uvicorn", "sqlalchemy", "alembic", "asyncpg", "jinja2"):
        assert not any(
            dependency.lower().startswith(server_only_dependency)
            for dependency in base_dependencies
        )


def test_server_dockerfile_comment_matches_current_packaging() -> None:
    dockerfile = Path("deploy/Dockerfile.server").read_text(encoding="utf-8")

    assert "패키징하지 않으므로" not in dockerfile
    assert 'packages=["src/rider_crawl"]' not in dockerfile
    assert "src/" in dockerfile


def test_wheel_contains_migrations_for_installed_migration_cli() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    force_include = data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include["migrations"] == "migrations"


def test_test_runner_does_not_rewrite_virtualenv_site_packages() -> None:
    script = Path("scripts/test.ps1").read_text(encoding="utf-8")

    assert "_editable_impl_rider_crawl.pth" not in script
    assert "Set-Content" not in script


def test_create_app_reuses_single_database_engine_for_default_components(
    monkeypatch,
) -> None:
    from rider_server import main as server_main

    class _Engine:
        pass

    engine = _Engine()
    session_factory = object()
    created_calls: list[tuple[str, dict[str, object]]] = []

    def _fake_create_engine(database_url: str, **kwargs: object):
        created_calls.append((database_url, kwargs))
        return engine

    def _fake_create_session_factory(engine_arg):
        assert engine_arg is engine
        return session_factory

    monkeypatch.setattr(server_main, "create_engine", _fake_create_engine)
    monkeypatch.setattr(server_main, "create_session_factory", _fake_create_session_factory)

    settings = Settings(
        app_env="production",
        app_version="9.9.9",
        build_sha=None,
        build_time=None,
        database_url="postgresql+asyncpg://user:pass@db:5432/rider",
        db_pool_size=7,
        db_max_overflow=3,
    )

    app = server_main.create_app(settings)

    assert created_calls == [
        (
            settings.database_url,
            {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow},
        )
    ]
    assert app.state.db_engine is engine
    assert app.state.db_session_factory is session_factory


def test_pyinstaller_spec_includes_imap_2fa_runtime_modules() -> None:
    spec = Path("rider_crawl_onefile.spec").read_text(encoding="utf-8")
    tree = ast.parse(spec, filename="rider_crawl_onefile.spec")

    hiddenimports = _module_literal(tree, "hiddenimports")

    assert "imapclient" in hiddenimports
    assert "rider_crawl.auth.imap_2fa" in hiddenimports
    assert "rider_crawl.auth.coupang_email_2fa" in hiddenimports


def test_migration_entrypoint_uses_programmatic_utf8_safe_config(monkeypatch) -> None:
    from rider_server.db import migrate

    captured: dict[str, object] = {}

    def _fake_upgrade(config, revision, *, sql=False):
        captured["revision"] = revision
        captured["sql"] = sql
        captured["script_location"] = config.get_main_option("script_location")
        captured["url"] = config.get_main_option("sqlalchemy.url")

    monkeypatch.setattr(migrate.command, "upgrade", _fake_upgrade)

    rc = migrate.main(
        ["upgrade", "head"],
        environ={
            "DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/rider",
            "RIDER_DB_MIGRATION_BACKUP_CONFIRMED": "1",
        },
    )

    assert rc == 0
    assert captured["revision"] == "head"
    assert captured["sql"] is False
    assert str(captured["script_location"]).endswith("migrations")
    assert captured["url"] == "postgresql+asyncpg://user:pass@db:5432/rider"


def test_migration_entrypoint_requires_backup_confirmation_for_online_upgrade(
    monkeypatch,
    capsys,
) -> None:
    from rider_server.db import migrate

    called = False

    def _fake_upgrade(config, revision, *, sql=False):
        nonlocal called
        called = True

    monkeypatch.setattr(migrate.command, "upgrade", _fake_upgrade)

    rc = migrate.main(
        ["upgrade", "head"],
        environ={"DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/rider"},
    )

    assert rc == 2
    assert called is False
    assert "RIDER_DB_MIGRATION_BACKUP_CONFIRMED" in capsys.readouterr().out


def test_migration_sql_render_does_not_require_backup_confirmation(monkeypatch) -> None:
    from rider_server.db import migrate

    captured: dict[str, object] = {}

    def _fake_upgrade(config, revision, *, sql=False):
        captured["revision"] = revision
        captured["sql"] = sql

    monkeypatch.setattr(migrate.command, "upgrade", _fake_upgrade)

    rc = migrate.main(
        ["upgrade", "head", "--sql"],
        environ={"DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/rider"},
    )

    assert rc == 0
    assert captured == {"revision": "head", "sql": True}


def test_migration_revision_ids_fit_alembic_default_version_column() -> None:
    too_long: list[str] = []

    for path in sorted(Path("migrations/versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in ("revision", "down_revision"):
            value = _module_literal(tree, name)
            if isinstance(value, str) and len(value) > 32:
                too_long.append(f"{path.name}:{name}={value!r}")

    assert too_long == []


def _module_literal(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"missing module literal: {name}")
