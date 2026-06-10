from __future__ import annotations

from datetime import datetime

from .models import CurrentScreenSnapshot, PeakPeriodSnapshot, PerformanceSnapshot

# 각 피크 구간에 함께 표기할 시간대. 주중(평일)과 주말은 운영 시간이 달라
# 별도 표를 사용한다. 키는 메시지 라벨, 값은 "시작~끝" 문자열.
WEEKDAY_PEAK_TIMES = {
    "morning": "06:00~10:54",
    "lunch_peak": "10:54~12:59",
    "lunch_non_peak": "13:00~16:54",
    "dinner_peak": "16:55~19:59",
    "dinner_non_peak": "20:00~03:59",
}

WEEKEND_PEAK_TIMES = {
    "morning": "06:00~10:54",
    "lunch_peak": "10:54~01:59",
    "lunch_non_peak": "02:00~04:54",
    "dinner_peak": "04:55~07:59",
    "dinner_non_peak": "20:00~03:59",
}


def _peak_times(*, now: datetime | None = None) -> dict[str, str]:
    # weekday(): 월=0 ... 금=4, 토=5, 일=6. 토·일이면 주말 표를 쓴다.
    current = now or datetime.now()
    if current.weekday() >= 5:
        return WEEKEND_PEAK_TIMES
    return WEEKDAY_PEAK_TIMES


def render_current_screen_message(
    snapshot: CurrentScreenSnapshot | PerformanceSnapshot,
    *,
    source_label: str = "",
    now: datetime | None = None,
) -> str:
    if isinstance(snapshot, PerformanceSnapshot):
        return _render_performance_message(snapshot, source_label=source_label, now=now)
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


def _render_performance_message(
    snapshot: PerformanceSnapshot, *, source_label: str = "", now: datetime | None = None
) -> str:
    dashboard = snapshot.peak_dashboard
    times = _peak_times(now=now)
    lines = [
        "[실시간 실적봇]",
    ]
    if source_label.strip():
        lines.append(f"[{source_label.strip()}]")
    lines.extend(
        [
            f"⏰ {dashboard.updated_at} 기준",
            "",
            f"아침 : {_format_period(dashboard.morning, times['morning'])}",
            f"점심 피크 : {_format_period(dashboard.lunch_peak, times['lunch_peak'])}",
            f"점심 논피크 : {_format_period(dashboard.lunch_non_peak, times['lunch_non_peak'])}",
            f"저녁 피크 : {_format_period(dashboard.dinner_peak, times['dinner_peak'])}",
            f"저녁 논피크 : {_format_period(dashboard.dinner_non_peak, times['dinner_non_peak'])}",
            "",
            f"배정 {_format_count(dashboard.assigned_count)}건 / 처리 {_format_count(dashboard.processed_count)}건",
            f"🚨거절률: {_format_count(dashboard.reject_rate)}%🚨",
            f"🌇수행중인인원 : {snapshot.current_screen.active_riders}명",
        ]
    )
    return "\n".join(lines)


def _format_period(period: PeakPeriodSnapshot, time_range: str = "") -> str:
    if period.done >= period.total:
        status = "완료"
    else:
        status = f"{_format_count(period.done)}건/{_format_count(period.total)}건"
    if time_range:
        return f"{status} ({time_range})"
    return status


def _format_count(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rate_line(label: str, value: float | None) -> list[str]:
    if value is None:
        return []
    return [f"{label} : {_format_count(value)}%"]
