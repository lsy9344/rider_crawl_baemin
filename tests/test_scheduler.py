import asyncio

import pytest

from rider_crawl.scheduler import BotScheduler


class FakeStopEvent:
    def __init__(self, stops: list[bool]) -> None:
        self.stops = stops
        self.waited: list[float] = []

    def is_set(self) -> bool:
        return self.stops.pop(0) if self.stops else True

    def wait(self, seconds: float) -> bool:
        self.waited.append(seconds)
        return self.is_set()


def test_scheduler_runs_immediately_then_waits_interval():
    calls: list[int] = []
    stop_event = FakeStopEvent([False, False, True])
    scheduler = BotScheduler(interval_minutes=35, run_job=lambda: calls.append(len(calls) + 1))

    scheduler.run_loop(stop_event=stop_event)

    assert calls == [1, 2]
    assert stop_event.waited == [2100, 2100]


def test_scheduler_can_run_with_second_interval_for_baemin_refresh_loop():
    calls: list[int] = []
    stop_event = FakeStopEvent([False, False, True])
    scheduler = BotScheduler(interval_seconds=20, run_job=lambda: calls.append(len(calls) + 1))

    scheduler.run_loop(stop_event=stop_event)

    assert calls == [1, 2]
    assert stop_event.waited == [20, 20]


def test_scheduler_retries_soon_when_job_reports_skipped_run():
    calls: list[int] = []
    stop_event = FakeStopEvent([False, False, True])
    scheduler = BotScheduler(interval_minutes=35, retry_seconds=5, run_job=lambda: calls.append(len(calls) + 1) or False)

    scheduler.run_loop(stop_event=stop_event)

    assert calls == [1, 2]
    assert stop_event.waited == [5, 5]


def test_scheduler_does_not_run_when_stopped_before_start():
    calls: list[int] = []
    stop_event = FakeStopEvent([True])
    scheduler = BotScheduler(interval_minutes=35, run_job=lambda: calls.append(1))

    scheduler.run_loop(stop_event=stop_event)

    assert calls == []
    assert stop_event.waited == []


def test_scheduler_rejects_non_positive_interval():
    try:
        BotScheduler(interval_minutes=0, run_job=lambda: None)
    except ValueError as exc:
        assert "interval_minutes" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_scheduler_survives_base_exception_from_job():
    # run_job이 Exception이 아닌 예외(렌더러 크래시 직후의 CancelledError 등)를 던져도
    # 워커 루프가 죽지 않고 빠른 재시도(retry_seconds) 후 다음 회차를 돈다.
    calls: list[int] = []

    def job() -> object:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise asyncio.CancelledError()
        return True

    stop_event = FakeStopEvent([False, False, True])
    scheduler = BotScheduler(interval_minutes=35, retry_seconds=5, run_job=job)

    scheduler.run_loop(stop_event=stop_event)  # 예외가 새어나오지 않아야 한다

    assert calls == [1, 2]
    assert stop_event.waited == [5, 2100]


def test_scheduler_calls_on_job_error_hook():
    errors: list[BaseException] = []
    raised = {"done": False}

    def job() -> object:
        if not raised["done"]:
            raised["done"] = True
            raise RuntimeError("boom")
        return True

    stop_event = FakeStopEvent([False, False, True])
    scheduler = BotScheduler(
        interval_minutes=35, retry_seconds=5, run_job=job, on_job_error=errors.append
    )

    scheduler.run_loop(stop_event=stop_event)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


def test_scheduler_reraises_keyboard_interrupt():
    errors: list[BaseException] = []
    stop_event = FakeStopEvent([False])
    scheduler = BotScheduler(
        interval_minutes=35,
        run_job=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
        on_job_error=errors.append,
    )

    with pytest.raises(KeyboardInterrupt):
        scheduler.run_loop(stop_event=stop_event)

    assert errors == []  # 정상 종료 신호는 훅을 거치지 않는다


def test_scheduler_reraises_system_exit():
    errors: list[BaseException] = []
    stop_event = FakeStopEvent([False])
    scheduler = BotScheduler(
        interval_minutes=35,
        run_job=lambda: (_ for _ in ()).throw(SystemExit()),
        on_job_error=errors.append,
    )

    with pytest.raises(SystemExit):
        scheduler.run_loop(stop_event=stop_event)

    assert errors == []


def test_scheduler_swallows_hook_failure():
    # on_job_error 콜백이 실패해도 루프는 멈추지 않는다.
    calls: list[int] = []

    def job() -> object:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return True

    def bad_hook(_exc: BaseException) -> None:
        raise ValueError("hook failed")

    stop_event = FakeStopEvent([False, False, True])
    scheduler = BotScheduler(
        interval_minutes=35, retry_seconds=5, run_job=job, on_job_error=bad_hook
    )

    scheduler.run_loop(stop_event=stop_event)

    assert calls == [1, 2]
    assert stop_event.waited == [5, 2100]
