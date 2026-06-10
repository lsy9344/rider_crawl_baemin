from __future__ import annotations

from .models import CurrentScreenSnapshot, PeakPeriodSnapshot, PerformanceSnapshot


def render_current_screen_message(
    snapshot: CurrentScreenSnapshot | PerformanceSnapshot,
    *,
    source_label: str = "",
) -> str:
    if isinstance(snapshot, PerformanceSnapshot):
        return _render_performance_message(snapshot, source_label=source_label)
    return _render_baemin_current_screen_message(snapshot, source_label=source_label)


def _render_baemin_current_screen_message(snapshot: CurrentScreenSnapshot, *, source_label: str = "") -> str:
    lines = [
        "[실시간 실적봇]",
    ]
    if source_label.strip():
        lines.append(f"[{source_label.strip()}]")
    lines.extend(
        [
            f"⏰ {snapshot.updated_at} 기준",
            "",
            f"오전오후피크 : {_format_count(snapshot.lunch_peak_count)}건",
            f"오후논피크 : {_format_count(snapshot.afternoon_non_peak_count)}건",
            f"저녁피크 : {_format_count(snapshot.dinner_peak_count)}건",
            f"저녁논피크 : {_format_count(snapshot.dinner_non_peak_count)}건",
        ]
    )
    rate_lines = _rate_line("거절율", snapshot.reject_rate)
    if rate_lines:
        lines.extend(["", *rate_lines])
    return "\n".join(lines)


def _render_performance_message(snapshot: PerformanceSnapshot, *, source_label: str = "") -> str:
    dashboard = snapshot.peak_dashboard
    lines = [
        "[실시간 실적봇]",
    ]
    if source_label.strip():
        lines.append(f"[{source_label.strip()}]")
    lines.extend(
        [
            f"⏰ {dashboard.updated_at} 기준",
            "",
            f"아침 : {_format_period(dashboard.morning)}",
            f"점심 피크 : {_format_period(dashboard.lunch_peak)}",
            f"점심 논피크 : {_format_period(dashboard.lunch_non_peak)}",
            f"저녁 피크 : {_format_period(dashboard.dinner_peak)}",
            f"저녁 논피크 : {_format_period(dashboard.dinner_non_peak)}",
            "",
            f"배정 {_format_count(dashboard.assigned_count)}건 / 처리 {_format_count(dashboard.processed_count)}건",
            f"🚨거절률: {_format_count(dashboard.reject_rate)}%🚨",
            f"🌇수행중인인원 : {snapshot.current_screen.active_riders}명",
        ]
    )
    return "\n".join(lines)


def _format_period(period: PeakPeriodSnapshot) -> str:
    if period.done >= period.total:
        return "완료"
    return f"{_format_count(period.done)}건/{_format_count(period.total)}건"


def _format_count(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rate_line(label: str, value: float | None) -> list[str]:
    if value is None:
        return []
    return [f"{label} : {_format_count(value)}%"]
