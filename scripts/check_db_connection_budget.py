"""Check deployment database connection budget against Postgres max_connections."""

from __future__ import annotations

import argparse
import os

from rider_server.db.connection_budget import connection_budget, validate_connection_budget


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
