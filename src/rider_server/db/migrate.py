"""Alembic migration CLI for server deployments.

This wrapper builds Alembic ``Config`` programmatically so migration execution
does not depend on the local console encoding used to read ``alembic.ini``.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config

from rider_crawl.redaction import redact
from rider_server.settings import Settings

MIGRATION_BACKUP_CONFIRM_ENV = "RIDER_DB_MIGRATION_BACKUP_CONFIRMED"
MIGRATIONS_DIR_ENV = "RIDER_MIGRATIONS_DIR"
_TRUE_VALUES = {"1", "true", "yes", "on", "confirmed"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _env(environ: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _migration_backup_confirmed(environ: Mapping[str, str] | None = None) -> bool:
    value = _env(environ).get(MIGRATION_BACKUP_CONFIRM_ENV, "")
    return value.strip().lower() in _TRUE_VALUES


def resolve_migrations_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve migration scripts in source checkout or installed wheel layouts."""

    env = _env(environ)
    configured = env.get(MIGRATIONS_DIR_ENV)
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            _repo_root() / "migrations",
            Path(__file__).resolve().parents[2] / "migrations",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def build_config(database_url: str, *, migrations_dir: Path | None = None) -> Config:
    """Build an Alembic config without reading ``alembic.ini``."""

    cfg = Config()
    cfg.set_main_option(
        "script_location",
        str(migrations_dir or resolve_migrations_dir()),
    )
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m rider_server.db.migrate")
    parser.add_argument("command", choices=("upgrade", "downgrade"))
    parser.add_argument("revision", nargs="?", default="head")
    parser.add_argument("--sql", action="store_true", help="render SQL without connecting")
    args = parser.parse_args(argv)

    env = _env(environ)
    settings = Settings.from_env(env)
    if not settings.database_url:
        print("migration failed: DATABASE_URL is required", flush=True)
        return 2
    if not args.sql and not _migration_backup_confirmed(env):
        print(
            "migration failed: set "
            f"{MIGRATION_BACKUP_CONFIRM_ENV}=1 after verifying the current DB backup",
            flush=True,
        )
        return 2

    cfg = build_config(settings.database_url)
    try:
        if args.command == "upgrade":
            command.upgrade(cfg, args.revision, sql=args.sql)
        else:
            command.downgrade(cfg, args.revision, sql=args.sql)
    except Exception as exc:  # noqa: BLE001 - CLI boundary; never print unredacted secrets.
        print(redact(f"migration failed: {exc.__class__.__name__}: {exc}"), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
