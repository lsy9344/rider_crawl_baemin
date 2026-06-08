from __future__ import annotations

from .models import CurrentScreenSnapshot


def render_current_screen_message(snapshot: CurrentScreenSnapshot, *, source_label: str = "") -> str:
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


def _format_count(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rate_line(label: str, value: float | None) -> list[str]:
    if value is None:
        return []
    return [f"{label} : {_format_count(value)}%"]
