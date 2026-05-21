from __future__ import annotations

from .models import CurrentScreenSnapshot


def render_current_screen_message(snapshot: CurrentScreenSnapshot) -> str:
    return "\n".join(
        [
            "[실시간 실적봇]",
            f"⏰ {snapshot.updated_at} 기준",
            "",
            f"{snapshot.shift_label} : {snapshot.available_current}명/{snapshot.available_total}명",
            f"대기 : {snapshot.waiting_count}명",
            "",
            f"완료 : {_format_count(snapshot.completed_count)}건",
            f"거절/무시 : {_format_count(snapshot.rejected_ignored_count)}건",
            f"취소 : {_format_count(snapshot.cancelled_count)}건",
            f"점심피크 : {_format_count(snapshot.lunch_peak_count)}건",
            f"저녁피크 : {_format_count(snapshot.dinner_peak_count)}건",
            f"논피크 : {_format_count(snapshot.non_peak_count)}건",
            f"수행중인인원 : {snapshot.active_riders}명",
        ]
    )


def _format_count(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
