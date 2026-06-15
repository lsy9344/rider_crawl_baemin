"""Seed a pending Agent registration row.

Usage:
    python -m rider_server.agent_registration_seed --agent-id <uuid> --name office-pc-1
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from rider_server.db.base import create_engine, create_session_factory
from rider_server.services.agent_registration_admin import seed_pending_agent_registration
from rider_server.settings import Settings


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m rider_server.agent_registration_seed")
    parser.add_argument("--agent-id", required=True, help="pending Agent UUID")
    parser.add_argument("--name", default="pending-agent", help="operator-facing Agent name")
    parser.add_argument(
        "--registration-code",
        default=None,
        help="optional one-time code; omitted means generate one",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env(environ)
    if not settings.database_url:
        print("agent registration seed failed: DATABASE_URL is required", flush=True)
        return 2

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    code = asyncio.run(
        seed_pending_agent_registration(
            session_factory=session_factory,
            agent_id=args.agent_id,
            name=args.name,
            registration_code=args.registration_code,
            now=datetime.now(timezone.utc),
        )
    )
    print(f"agent registration seeded: agent_id={args.agent_id} registration_code={code}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
