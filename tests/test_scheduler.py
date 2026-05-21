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
