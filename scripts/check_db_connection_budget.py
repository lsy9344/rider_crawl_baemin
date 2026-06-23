"""Check deployment database connection budget against Postgres max_connections."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def _load_connection_budget_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rider_server"
        / "db"
        / "connection_budget.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_rider_connection_budget",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load connection budget module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_connection_budget_module = _load_connection_budget_module()
connection_budget = _connection_budget_module.connection_budget
validate_connection_budget = _connection_budget_module.validate_connection_budget


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--postgres-max-connections",
        type=int,
        default=_env_int("POSTGRES_MAX_CONNECTIONS", 100),
    )
    parser.add_argument(
        "--reserved-connections",
        type=int,
        default=_env_int("RIDER_DB_RESERVED_CONNECTIONS", 0),
    )
    args = parser.parse_args()

    budget = connection_budget(
        uvicorn_workers=_env_int("RIDER_UVICORN_WORKERS", 1),
        db_pool_size=_env_int("RIDER_DB_POOL_SIZE", 5),
        db_max_overflow=_env_int("RIDER_DB_MAX_OVERFLOW", 10),
        scheduler_processes=_env_int("SCHEDULER_PROCESSES", 1),
        queue_recovery_processes=_env_int("QUEUE_RECOVERY_PROCESSES", 1),
        dispatch_processes=_env_int("TELEGRAM_DISPATCH_PROCESSES", 1),
        reserved_connections=args.reserved_connections,
    )
    result = validate_connection_budget(
        budget,
        postgres_max_connections=args.postgres_max_connections,
    )
    print(result.message)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
