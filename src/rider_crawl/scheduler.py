from __future__ import annotations

import threading
from typing import Callable, Protocol


class StopEvent(Protocol):
    def is_set(self) -> bool:
        ...

    def wait(self, seconds: float) -> bool:
        ...


class BotScheduler:
    def __init__(
        self,
        *,
        run_job: Callable[[], object],
        interval_minutes: int | None = None,
        interval_seconds: int | None = None,
        retry_seconds: int = 5,
        on_job_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if interval_seconds is None:
            if interval_minutes is None:
                raise ValueError("interval_minutes or interval_seconds is required")
            if interval_minutes <= 0:
                raise ValueError("interval_minutes must be greater than zero")
            interval_seconds = interval_minutes * 60
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        if retry_seconds <= 0:
            raise ValueError("retry_seconds must be greater than zero")
        self._interval_seconds = interval_seconds
        self._retry_seconds = retry_seconds
        self.run_job = run_job
        self._on_job_error = on_job_error

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    def run_loop(self, *, stop_event: StopEvent | None = None) -> None:
        event = stop_event or threading.Event()
        if event.is_set():
            return

        while True:
            try:
                result = self.run_job()
            except (KeyboardInterrupt, SystemExit):
                # 정상 종료 신호(앱/프로세스 종료)는 그대로 올려보낸다.
                raise
            except BaseException as exc:  # noqa: BLE001 - 워커 스레드 사망 방지가 목적
                # run_job에서 빠져나온 어떤 예외도(예: 렌더러 크래시 직후의
                # asyncio.CancelledError) 이 데몬 워커 스레드를 죽이지 못하게 한다.
                # 흔적만 남기고(on_job_error) '실패 회차'로 보고 빠른 재시도를 태운다.
                if self._on_job_error is not None:
                    try:
                        self._on_job_error(exc)
                    except Exception:
                        # 알림 콜백이 실패해도 루프는 멈추지 않는다.
                        pass
                result = False
            wait_seconds = self._retry_seconds if result is False else self.interval_seconds
            if event.wait(wait_seconds):
                break
