"""복구/신규 환경 non-sending 게이트 — Story 5.8 / AC3 (NFR-9·25).

복구·신규 환경은 **명시적 활성화 전까지 non-sending 모드로 시작** 한다(모호하면 보내지 않는다 —
fail-closed). 신규 차단 경로를 만들지 않고 **기존 dispatch ``send_enabled``/kill switch 와 compose**
한다 — 실전송은 ``send_enabled``(채널/대상별 게이트)와 ``sending_enabled``(환경 전역 복구 플래그)가
**둘 다 True** 일 때만 일어난다. ``sending_enabled`` 기본값은 OFF(``Settings.sending_enabled`` /
``RIDER_SENDING_ENABLED``)라 운영자가 명시적으로 켜기 전에는 실전송 0.

순수 함수라 always-run 단위로 잠근다(신규 deps 0). 내부 ``now()``/I/O 미호출(결정적).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

_KST = timezone(timedelta(hours=9), "Asia/Seoul")


def effective_send_enabled(*, send_enabled: bool, sending_enabled: bool) -> bool:
    """실전송 허용 여부 — ``send_enabled`` 와 복구 ``sending_enabled`` 를 AND 로 compose.

    ``sending_enabled`` 가 False(복구/신규 환경 기본 OFF)면 ``send_enabled`` 가 True 라도 실전송을
    차단한다(NFR-9·25). 둘 다 True 일 때만 True — 운영자 명시적 활성화 전 fail-closed.
    """

    return bool(send_enabled and sending_enabled)


def _parse_hhmm(value: str) -> time | None:
    parts = (value or "").split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def send_window_allows_dispatch(
    now: datetime,
    *,
    schedule_enabled: bool,
    start_time: str,
    stop_time: str,
) -> bool:
    """현재 시각이 대상의 전송 허용 시간대 안인지 판정한다."""

    if not schedule_enabled:
        return True
    start = _parse_hhmm(start_time)
    stop = _parse_hhmm(stop_time)
    if start is None or stop is None or start == stop:
        return False
    aware_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    current = aware_now.astimezone(_KST).time().replace(second=0, microsecond=0)
    if start < stop:
        return start <= current < stop
    return current >= start or current < stop
