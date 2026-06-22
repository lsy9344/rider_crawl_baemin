from __future__ import annotations

from pathlib import Path

import pytest


DOC_TESTS = {
    "tests/server/test_runbooks_present.py",
    "tests/test_manual_regression_runbook.py",
    "tests/test_pytest_baseline_artifacts.py",
    "tests/test_project_current_state_doc.py",
    "tests/test_reuse_boundaries_doc.py",
}

LOCAL_ARTIFACT_TESTS = {
    "tests/test_baseline_artifacts.py",
}

ARCHITECTURE_TESTS = {
    "tests/agent/test_agent_package.py",
    "tests/server/test_admin_actions_guard.py",
    "tests/server/test_admin_dev_tools.py",
    "tests/server/test_deployment_config.py",
    "tests/server/test_metrics_boundary.py",
    "tests/server/test_postgres_runtime_guards.py",
    "tests/server/test_scheduler_boundary.py",
    "tests/server/test_server_async_boundary.py",
    "tests/test_architecture.py",
}

SLOW_TESTS = {
    "tests/agent/test_agent_package.py",
    "tests/agent/test_coupang_gmail_2fa.py",
}

CONCURRENCY_TESTS = {
    "tests/agent/test_coupang_gmail_2fa.py",
    "tests/negative/test_queue_concurrency.py",
}

POSTGRES_TESTS = {
    "tests/negative/test_admin_actions_pg.py",
    "tests/negative/test_admin_entity_crud_pg.py",
    "tests/negative/test_dashboard_repository_pg.py",
    "tests/negative/test_messenger_channel_unique.py",
    "tests/negative/test_metrics_repository_pg.py",
    "tests/negative/test_queue_concurrency.py",
    "tests/negative/test_scheduler_idempotency.py",
    "tests/negative/test_security_pg.py",
}

MARKER_DESCRIPTIONS = (
    "postgres: tests that require TEST_DATABASE_URL or real PostgreSQL semantics",
    "slow: tests that use subprocesses, real sleeps, threads, or broad smoke loops",
    "docs: documentation, runbook, and baseline artifact checks",
    "architecture: AST, source, import, and package-boundary guard checks",
    "e2e: end-to-end or multi-layer workflow checks",
    "concurrency: thread, race, and parallel safety checks",
    "local_artifact: checks that depend on local git tags or generated local artifacts",
)


def pytest_configure(config: pytest.Config) -> None:
    for marker in MARKER_DESCRIPTIONS:
        config.addinivalue_line("markers", marker)


def _test_path(item: pytest.Item) -> str:
    try:
        return item.path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return item.path.as_posix()


def _has_postgres_skip(item: pytest.Item) -> bool:
    for mark in item.iter_markers(name="skipif"):
        reason = str(mark.kwargs.get("reason", ""))
        if "TEST_DATABASE_URL" in reason or "Postgres" in reason or "PostgreSQL" in reason:
            return True
    return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        path = _test_path(item)
        markers = []

        if path in DOC_TESTS:
            markers.append(pytest.mark.docs)
        if path in LOCAL_ARTIFACT_TESTS:
            markers.extend([pytest.mark.docs, pytest.mark.local_artifact])
        if path in ARCHITECTURE_TESTS:
            markers.append(pytest.mark.architecture)
        if path in SLOW_TESTS:
            markers.append(pytest.mark.slow)
        if path in CONCURRENCY_TESTS:
            markers.append(pytest.mark.concurrency)
        if path.endswith("_e2e.py") or "_e2e" in Path(path).stem:
            markers.append(pytest.mark.e2e)
        if path in POSTGRES_TESTS or _has_postgres_skip(item):
            markers.append(pytest.mark.postgres)

        for marker in markers:
            item.add_marker(marker)
