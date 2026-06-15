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
    assert "condition: service_healthy" in compose
    assert "condition: service_completed_successfully" in compose


def test_backend_api_env_documents_admin_allowed_origins() -> None:
    env = Path("deploy/env/backend-api.env").read_text(encoding="utf-8")

    assert "RIDER_ADMIN_ALLOWED_ORIGINS" in env


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
        environ={"DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/rider"},
    )

    assert rc == 0
    assert captured["revision"] == "head"
    assert captured["sql"] is False
    assert str(captured["script_location"]).endswith("migrations")
    assert captured["url"] == "postgresql+asyncpg://user:pass@db:5432/rider"


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
