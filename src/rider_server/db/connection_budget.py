"""Database connection budget guardrails for multi-process deployment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectionBudget:
    total_requested: int
    per_process_capacity: int
    process_count: int
    reserved_connections: int = 0


@dataclass(frozen=True)
class ConnectionBudgetValidation:
    ok: bool
    message: str


def connection_budget(
    *,
    uvicorn_workers: int,
    db_pool_size: int,
    db_max_overflow: int,
    scheduler_processes: int = 1,
    queue_recovery_processes: int = 1,
    dispatch_processes: int = 1,
    reserved_connections: int = 0,
) -> ConnectionBudget:
    per_process_capacity = max(0, int(db_pool_size)) + max(0, int(db_max_overflow))
    process_count = (
        max(0, int(uvicorn_workers))
        + max(0, int(scheduler_processes))
        + max(0, int(queue_recovery_processes))
        + max(0, int(dispatch_processes))
    )
    total_requested = process_count * per_process_capacity + max(0, int(reserved_connections))
    return ConnectionBudget(
        total_requested=total_requested,
        per_process_capacity=per_process_capacity,
        process_count=process_count,
        reserved_connections=max(0, int(reserved_connections)),
    )


def validate_connection_budget(
    budget: ConnectionBudget,
    *,
    postgres_max_connections: int,
) -> ConnectionBudgetValidation:
    max_connections = max(0, int(postgres_max_connections))
    if budget.total_requested <= max_connections:
        return ConnectionBudgetValidation(
            ok=True,
            message=(
                f"database connection budget ok: requested={budget.total_requested}, "
                f"max_connections={max_connections}"
            ),
        )
    return ConnectionBudgetValidation(
        ok=False,
        message=(
            f"database connection budget exceeds max_connections: "
            f"requested={budget.total_requested}, max_connections={max_connections}"
        ),
    )
