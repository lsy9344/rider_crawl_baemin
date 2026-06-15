"""Alembic migration CLI for server deployments.

This wrapper builds Alembic ``Config`` programmatically so migration execution
does not depend on the local console encoding used to read ``alembic.ini``.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config

from rider_crawl.redaction import redact
from rider_server.settings import Settings


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_config(database_url: str, *, migrations_dir: Path | None = None) -> Config:
    """Build an Alembic config without reading ``alembic.ini``."""

    cfg = Config()
    cfg.set_main_option(
        "script_location",
        str(migrations_dir or (_repo_root() / "migrations")),
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

    settings = Settings.from_env(environ)
    if not settings.database_url:
        print("migration failed: DATABASE_URL is required", flush=True)
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
