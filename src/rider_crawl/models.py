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


@dataclass(frozen=True)
class PeakPeriodSnapshot:
    done: float | int
    total: float | int


@dataclass(frozen=True)
class PeakDashboardSnapshot:
    updated_at: str
    assigned_count: float | int
    processed_count: float | int
    reject_rate: float | int
    morning: PeakPeriodSnapshot
    lunch_peak: PeakPeriodSnapshot
    lunch_non_peak: PeakPeriodSnapshot
    dinner_peak: PeakPeriodSnapshot
    dinner_non_peak: PeakPeriodSnapshot


@dataclass(frozen=True)
class PerformanceSnapshot:
    current_screen: CurrentScreenSnapshot
    peak_dashboard: PeakDashboardSnapshot


# A platform's crawl result is either the Baemin current-screen snapshot or the
# Coupang two-page performance snapshot.
CrawlSnapshotResult = CurrentScreenSnapshot | PerformanceSnapshot
