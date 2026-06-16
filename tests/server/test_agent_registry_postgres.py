"""DB-less guards for the PostgreSQL agent registry implementation."""

from __future__ import annotations

import ast
from pathlib import Path

_REGISTRY_SOURCE = Path("src/rider_server/services/agent_registry_postgres.py")


def _register_source() -> str:
    source = _REGISTRY_SOURCE.read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in ast.walk(module):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "register":
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                return segment
    raise AssertionError("register method not found")


def test_postgres_register_locks_registration_row_before_consuming_code() -> None:
    tree = ast.parse(_register_source())

    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "with_for_update"
        for node in ast.walk(tree)
    )


def test_postgres_register_consumes_code_with_conditional_update() -> None:
    source = _register_source()

    assert "registration_code_used_at.is_(None)" in source
    assert "RegistrationCodeAlreadyUsed" in source
    assert "rowcount" in source
