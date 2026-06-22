"""Server-side Agent registration provisioning utilities."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from rider_server.services.agent_registry import hash_registration_code


class _DuplicateCommitSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        class _Result:
            def scalar_one_or_none(self):
                return None

        return _Result()

    async def commit(self):
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("insert", {}, Exception("uq_agents_registration_code_hash"))


class _DuplicateCommitSessionFactory:
    def __call__(self):
        return _DuplicateCommitSession()


def test_pending_agent_values_store_only_registration_code_hash() -> None:
    from rider_server.services.agent_registration_admin import pending_agent_values

    agent_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    values = pending_agent_values(
        agent_id=agent_id,
        name="office-pc-1",
        registration_code="JOIN-CODE-1",
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert values["id"] == agent_id
    assert values["name"] == "office-pc-1"
    assert values["status"] == "PENDING_REGISTRATION"
    assert values["registration_code_hash"] == hash_registration_code("JOIN-CODE-1")
    assert "JOIN-CODE-1" not in values.values()
    assert values["registration_code_used_at"] is None
    assert values["token_hash"] is None


def test_seed_pending_agent_registration_duplicate_hash_is_operator_message() -> None:
    import asyncio

    from rider_server.services.agent_registration_admin import (
        DuplicateAgentRegistrationError,
        seed_pending_agent_registration,
    )

    async def _run():
        return await seed_pending_agent_registration(
            _DuplicateCommitSessionFactory(),
            agent_id="00000000-0000-0000-0000-000000000001",
            name="office-pc-1",
            now=datetime(2026, 6, 15, tzinfo=timezone.utc),
            registration_code="JOIN-CODE-1",
        )

    with pytest.raises(DuplicateAgentRegistrationError, match="중복"):
        asyncio.run(_run())


def test_agent_registration_seed_cli_requires_database_url(capsys) -> None:
    from rider_server import agent_registration_seed

    rc = agent_registration_seed.main(
        ["--agent-id", "00000000-0000-0000-0000-000000000001"],
        environ={},
    )

    assert rc == 2
    assert "DATABASE_URL is required" in capsys.readouterr().out


def test_agent_registration_seed_cli_prints_generated_code(monkeypatch, capsys) -> None:
    from rider_server import agent_registration_seed

    captured: dict[str, object] = {}

    async def _fake_seed(**kwargs):
        captured.update(kwargs)
        return "generated-code"

    monkeypatch.setattr(agent_registration_seed, "seed_pending_agent_registration", _fake_seed)
    monkeypatch.setattr(agent_registration_seed, "create_engine", lambda _url: object())
    monkeypatch.setattr(agent_registration_seed, "create_session_factory", lambda _engine: object())

    rc = agent_registration_seed.main(
        ["--agent-id", "00000000-0000-0000-0000-000000000001", "--name", "office-pc-1"],
        environ={"DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/rider"},
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "agent_id=00000000-0000-0000-0000-000000000001" in out
    assert "registration_code=generated-code" in out
    assert captured["name"] == "office-pc-1"
