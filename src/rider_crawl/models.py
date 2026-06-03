from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentScreenSnapshot:
    center_name: str
    date_label: str
    shift_label: str
    shift_time_range: str
    shift_status: str
    updated_at: str
    available_current: int
    available_total: int
    waiting_count: int
    online_riders: int
    rejected_ignored_count: float
    cancelled_count: float
    completed_count: float
    sequence_violation_count: float
    lunch_peak_count: float
    dinner_peak_count: float
    non_peak_count: float
    active_riders: int
    reject_rate: float | None = None
    cancel_rate: float | None = None
    afternoon_non_peak_count: float = 0
    dinner_non_peak_count: float = 0
