from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re

from .models import CurrentScreenSnapshot, PeakPeriodSnapshot, PerformanceSnapshot

# 각 피크 구간에 함께 표기할 시간대. 주중(평일)과 주말은 운영 시간이 달라
# 별도 표를 사용한다. 키는 메시지 라벨, 값은 "시작~끝" 문자열.
WEEKDAY_PEAK_TIMES = {
    "morning": "06:00~10:54",
    "lunch_peak": "10:55~12:59",
    "lunch_non_peak": "13:00~16:54",
    "dinner_peak": "16:55~19:59",
    "dinner_non_peak": "20:00~03:59",
}

WEEKEND_PEAK_TIMES = {
    "morning": "06:00~10:54",
    # 주말 점심 피크는 영업일 기준 다음날 새벽 01:59까지 이어지는 운영 규칙이다.
    "lunch_peak": "10:55~01:59",
    "lunch_non_peak": "02:00~04:54",
    "dinner_peak": "04:55~07:59",
    "dinner_non_peak": "20:00~03:59",
}

GAUGE_CELLS = 10


def _progress_gauge(ratio: float) -> str:
    clamped = min(max(ratio, 0.0), 1.0)
    filled = int(clamped * GAUGE_CELLS + 0.5)
    return "█" * filled + "░" * (GAUGE_CELLS - filled)


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
    timestamp = _format_baemin_timestamp(snapshot.date_label, snapshot.updated_at)
    lines = [
        "[실시간 실적봇]",
    ]
    if source_label.strip():
        lines.append(f"[{source_label.strip()}]")
    lines.append(f"{timestamp} 기준")
    lines.append("")
    _append_baemin_period(
        lines, "오전오후피크", snapshot.lunch_peak_count, snapshot.lunch_peak_goal, snapshot.lunch_peak_rate
    )
    _append_baemin_period(
        lines,
        "오후논피크",
        snapshot.afternoon_non_peak_count,
        snapshot.afternoon_non_peak_goal,
        snapshot.afternoon_non_peak_rate,
    )
    _append_baemin_period(
        lines, "저녁피크", snapshot.dinner_peak_count, snapshot.dinner_peak_goal, snapshot.dinner_peak_rate
    )
    _append_baemin_period(
        lines,
        "저녁논피크",
        snapshot.dinner_non_peak_count,
        snapshot.dinner_non_peak_goal,
        snapshot.dinner_non_peak_rate,
    )
    tail_lines = list(_cancel_rate_lines(snapshot.cancel_rate))
    if snapshot.cancel_rate is not None:
        # '수행중인원'은 배달현황 표의 운행상태가 '운행중'인 라이더 수다(쿠팡의 '수행중인원'과
        # 같은 개념). 배달현황을 함께 읽었을 때만 채워지고, 그때 취소율도 같은 표에서 함께
        # 들어오므로 cancel_rate 유무로 표기 여부를 결정한다(달성현황만 읽은 경우엔 생략).
        tail_lines.append(f"수행중인원 : {_format_count(snapshot.active_riders)}명")
    if tail_lines:
        lines.extend(["", *tail_lines])
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
            _period_gauge(dashboard.morning),
            f"점심 피크 : {_format_period(dashboard.lunch_peak, times['lunch_peak'])}",
            _period_gauge(dashboard.lunch_peak),
            f"점심 논피크 : {_format_period(dashboard.lunch_non_peak, times['lunch_non_peak'])}",
            _period_gauge(dashboard.lunch_non_peak),
            f"저녁 피크 : {_format_period(dashboard.dinner_peak, times['dinner_peak'])}",
            _period_gauge(dashboard.dinner_peak),
            f"저녁 논피크 : {_format_period(dashboard.dinner_non_peak, times['dinner_non_peak'])}",
            _period_gauge(dashboard.dinner_non_peak),
            "",
            f"배정 {_format_count(dashboard.assigned_count)}건 / 처리 {_format_count(dashboard.processed_count)}건",
            f"거절률: {_format_adjusted_reject_rate(dashboard.reject_rate)}%",
        ]
    )
    # 수행중 인원은 rider-performance의 온라인 인원이다. 해당 보조 화면을 못 읽으면 생략한다.
    if snapshot.current_screen is not None:
        lines.append(f"수행중인원: {snapshot.current_screen.active_riders}명")
    return "\n".join(lines)


def _format_baemin_timestamp(date_label: str, updated_at: str) -> str:
    date_prefix = _format_baemin_date_prefix(date_label)
    if date_prefix:
        return f"⏰{date_prefix} {updated_at}"
    return f"⏰ {updated_at}"


def _format_baemin_date_prefix(date_label: str) -> str:
    label = date_label.strip()
    if not label:
        return ""

    numeric_match = re.fullmatch(r"\d{2}-(?P<month>\d{1,2})-(?P<day>\d{1,2})", label)
    if numeric_match:
        return f"{{{int(numeric_match.group('month'))}월{int(numeric_match.group('day'))}일}}"

    korean_match = re.search(r"(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일", label)
    if korean_match:
        return f"{{{int(korean_match.group('month'))}월{int(korean_match.group('day'))}일}}"

    return f"{{{label}}}"


def _period_gauge(period: PeakPeriodSnapshot) -> str:
    if period.total and period.total > 0:
        ratio = period.done / period.total
    else:
        ratio = 1.0 if period.done >= period.total else 0.0
    return _progress_gauge(ratio)


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


def _format_adjusted_reject_rate(value: float | int) -> str:
    adjusted = min(Decimal("100"), Decimal(str(value)) + Decimal("1"))
    if adjusted == adjusted.to_integral_value():
        return str(int(adjusted))
    return format(adjusted.normalize(), "f")


def _format_baemin_period(done: float | int, goal: float | int, rate: float | int | None) -> str:
    if goal or rate is not None:
        shown_rate = 0 if rate is None else rate
        return f"{_format_count(done)}건/{_format_count(goal)}건[{_format_count(shown_rate)}%]"
    return f"{_format_count(done)}건"


def _append_baemin_period(
    lines: list[str], label: str, done: float | int, goal: float | int, rate: float | int | None
) -> None:
    lines.append(f"{label} : {_format_baemin_period(done, goal, rate)}")
    gauge = _baemin_period_gauge(done, goal, rate)
    if gauge is not None:
        lines.append(gauge)


def _baemin_period_gauge(done: float | int, goal: float | int, rate: float | int | None) -> str | None:
    if goal:
        ratio = done / goal
    elif rate is not None:
        ratio = rate / 100
    else:
        return None
    return _progress_gauge(ratio)


def _cancel_rate_lines(cancel_rate: float | None) -> list[str]:
    if cancel_rate is None:
        return []
    return [f"취소율 : {_format_plain_rate(cancel_rate)}%"]


def _format_plain_rate(value: float | int) -> str:
    decimal_value = Decimal(str(value))
    if decimal_value == decimal_value.to_integral_value():
        return str(int(decimal_value))
    return format(decimal_value.normalize(), "f")
