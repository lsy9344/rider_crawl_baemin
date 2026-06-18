"""Server deployment configuration guardrails.

These tests lock the minimum server/DB setup needed for the refactored product
to run as a durable control plane. They avoid starting Docker or connecting to
Postgres; the checks are static or use injected seams.
"""

from __future__ import annotations

import ast
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

    app_var = variables[variables.index('variable "app_ingress_cidr"') :]
    app_var = app_var[: app_var.index('variable "db_name"')]
    assert 'default     = ""' in app_var
    assert "count             = var.app_ingress_cidr == \"\" ? 0 : 1" in security


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


def test_telegram_env_documents_default_supported_env_secret_refs() -> None:
    env = Path("deploy/env/telegram-webhook.env").read_text(encoding="utf-8")

    assert "TELEGRAM_WEBHOOK_SECRET_REF=env:RIDER_TELEGRAM_WEBHOOK_SECRET" in env
    assert "TELEGRAM_BOT_TOKEN_REF=env:RIDER_TELEGRAM_BOT_TOKEN" in env
    assert "vault://telegram" not in env


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
    created_urls: list[str] = []

    def _fake_create_engine(database_url: str):
        created_urls.append(database_url)
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
    )

    app = server_main.create_app(settings)

    assert created_urls == [settings.database_url]
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
