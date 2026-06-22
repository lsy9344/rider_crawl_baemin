"""Queue retry policy adapter."""

from __future__ import annotations

from datetime import datetime

from rider_server.domain import FailureCategory

from .backend import QueueRetryDecision

DEFAULT_MAX_JOB_ATTEMPTS = 3

_FAILURE_CATEGORY_ALIASES: dict[str, FailureCategory] = {
    "CRAWL_TIMEOUT": FailureCategory.CRAWL_FAILURE,
    "CDP_UNREACHABLE": FailureCategory.CRAWL_FAILURE,
    "PROFILE_UNAVAILABLE": FailureCategory.CRAWL_FAILURE,
    "PARSER_MISSING_DATA": FailureCategory.CRAWL_FAILURE,
    "USER_ACTION_PENDING": FailureCategory.AUTH_REQUIRED,
    "CENTER_MISMATCH": FailureCategory.TARGET_VALIDATION_FAILURE,
}


def default_retry_decider(
    error_code: str | None,
    attempt: int,
    now: datetime,
) -> QueueRetryDecision | None:
    """Return a retry decision for transient job failures."""

    if not error_code:
        return None
    category = _failure_category_for(error_code)
    if category is None:
        return None
    from rider_server.scheduler.policy import retry_run_after

    schedule = retry_run_after(
        now,
        error_code=category,
        attempt=attempt,
        max_attempts=DEFAULT_MAX_JOB_ATTEMPTS,
    )
    if not schedule.should_retry or schedule.run_after is None:
        return None
    return QueueRetryDecision(run_after=schedule.run_after)


def _failure_category_for(error_code: str) -> FailureCategory | None:
    normalized = str(error_code or "").strip().upper()
    if not normalized:
        return None
    alias = _FAILURE_CATEGORY_ALIASES.get(normalized)
    if alias is not None:
        return alias
    try:
        return FailureCategory(normalized)
    except ValueError:
        return None
