"""Queue stale lease recovery service contract."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rider_server.queue import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    InMemoryQueueBackend,
)
from rider_server.queue import __main__ as queue_main
from rider_server.queue.recovery import recover_once
from rider_server.queue.states import (
    JOB_TYPE_AUTH_COUPANG_2FA,
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
    JOB_TYPE_OPEN_AUTH_BROWSER,
    RESULT_REASON_STALE_AUTH_JOB_EXPIRED,
    RESULT_REASON_STALE_AUTH_RECOVERY_ABANDONED,
    RESULT_REASON_STALE_CRAWL_SKIPPED,
)

_T0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_recover_once_recovers_expired_leases_without_claim_route():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )

        result = await recover_once(backend, now=_T0 + timedelta(seconds=31))

        assert result.recovered_count == 1
        assert result.ran_at == _T0 + timedelta(seconds=31)
        assert backend.job_status(job_id) == JOB_STATUS_PENDING

    asyncio.run(_run())


def test_recovery_expires_open_auth_browser_instead_of_repending() -> None:
    """Expired auth browser jobs are terminal, not replayed after restart."""

    async def _run():
        backend = InMemoryQueueBackend()
        # OPEN_AUTH_BROWSER 는 짧은 TTL — payload expires_at 가 이미 지났다.
        expires_at = _T0 + timedelta(minutes=12)
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_OPEN_AUTH_BROWSER,
            payload_json={
                "job_type": JOB_TYPE_OPEN_AUTH_BROWSER,
                "requested_at": _iso(_T0),
                "expires_at": _iso(expires_at),
            },
            now=_T0,
        )
        await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_OPEN_AUTH_BROWSER],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        # lease 도 만료, payload expires_at 도 지난 시점에 recovery.
        now = expires_at + timedelta(minutes=1)
        result = await recover_once(backend, now=now)

        assert result.recovered_count == 1
        snap = backend.job_snapshot(job_id)
        assert snap is not None
        assert snap.status == JOB_STATUS_FAILED
        assert snap.error_code  # 기존 safe failure category 보존(CRAWL_TIMEOUT).
        assert snap.result_json["reason"] == RESULT_REASON_STALE_AUTH_JOB_EXPIRED
        assert snap.result_json["reason"] == "stale_auth_job_expired"

        # 이후 claim 으로 다시 잡히지 않는다(다시 PENDING 안 됨 → 브라우저 안 열림).
        claimed = await backend.claim(
            agent_id="agent-2",
            capabilities=[JOB_TYPE_OPEN_AUTH_BROWSER],
            max_jobs=5,
            lease_seconds=120,
            now=now,
        )
        assert claimed == []

    asyncio.run(_run())


def test_stale_auth_coupang_2fa_is_not_retried_by_recovery_loop() -> None:
    """Expired auth job does not trigger repeated OTP request.

    crawl-coupang-auth-separation Task 8: AUTH_COUPANG_2FA 의 lease 가 만료되면(claim 된 상태)
    payload TTL 과 무관하게 terminal FAILED 로 닫는다 — PENDING 재진입 0(중복 OTP 요청 차단).
    """

    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_AUTH_COUPANG_2FA,
            payload_json={
                "job_type": JOB_TYPE_AUTH_COUPANG_2FA,
                "platform": "coupang",
                "recovery_mode": "coupang_auto_email_2fa",
            },
            now=_T0,
        )
        await backend.claim(
            agent_id="agent-auth",
            capabilities=[JOB_TYPE_AUTH_COUPANG_2FA],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        # lease 만료 시점에 recovery — payload TTL 은 없지만 lease 만료만으로 terminal 종료.
        now = _T0 + timedelta(seconds=121)
        result = await recover_once(backend, now=now)

        assert result.recovered_count == 1
        snap = backend.job_snapshot(job_id)
        assert snap is not None
        # PENDING 재진입이 아니라 terminal FAILED.
        assert snap.status == JOB_STATUS_FAILED
        assert snap.result_json["reason"] == RESULT_REASON_STALE_AUTH_RECOVERY_ABANDONED

        # 다시 claim 되지 않는다(중복 OTP 요청 차단).
        claimed = await backend.claim(
            agent_id="agent-auth-2",
            capabilities=[JOB_TYPE_AUTH_COUPANG_2FA],
            max_jobs=5,
            lease_seconds=120,
            now=now,
        )
        assert claimed == []

    asyncio.run(_run())


def test_healthy_pending_auth_coupang_2fa_is_not_closed_by_recovery() -> None:
    """A not-yet-claimed AUTH_COUPANG_2FA job is preserved (only claimed+expired is terminal)."""

    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_AUTH_COUPANG_2FA,
            payload_json={
                "job_type": JOB_TYPE_AUTH_COUPANG_2FA,
                "platform": "coupang",
                "recovery_mode": "coupang_auto_email_2fa",
            },
            now=_T0,
        )
        # claim 하지 않은 채 recovery 를 돌려도 PENDING 보존.
        result = await recover_once(backend, now=_T0 + timedelta(hours=1))

        assert result.recovered_count == 0
        assert backend.job_status(job_id) == JOB_STATUS_PENDING

    asyncio.run(_run())


def test_stale_pending_auth_coupang_2fa_past_payload_ttl_is_closed() -> None:
    """PENDING AUTH_COUPANG_2FA past its payload ``expires_at`` is closed terminal (검토 High).

    downtime 뒤 누적된 오래된 자동 2FA 인증 job 이 나중에 claim·실행돼 중복 OTP 를 요청하는 것을
    막는다. lease 만료가 아니라 payload TTL 로 닫으므로 reason 은 stale_auth_job_expired 다.
    """

    async def _run():
        backend = InMemoryQueueBackend()
        expires_at = _T0 + timedelta(minutes=5)
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_AUTH_COUPANG_2FA,
            payload_json={
                "job_type": JOB_TYPE_AUTH_COUPANG_2FA,
                "platform": "coupang",
                "recovery_mode": "coupang_auto_email_2fa",
                "scheduled_at": _iso(_T0),
                "expires_at": _iso(expires_at),
            },
            now=_T0,
        )
        # claim 하지 않은 채(PENDING) payload TTL 이 지난 시점에 recovery.
        now = expires_at + timedelta(minutes=1)
        result = await recover_once(backend, now=now)

        assert result.recovered_count == 1
        snap = backend.job_snapshot(job_id)
        assert snap is not None
        assert snap.status == JOB_STATUS_FAILED
        assert snap.result_json["reason"] == RESULT_REASON_STALE_AUTH_JOB_EXPIRED

        # 다시 claim 되지 않는다(브라우저/OTP 미접근).
        claimed = await backend.claim(
            agent_id="agent-late",
            capabilities=[JOB_TYPE_AUTH_COUPANG_2FA],
            max_jobs=5,
            lease_seconds=120,
            now=now,
        )
        assert claimed == []

    asyncio.run(_run())


def test_recovery_skips_expired_scheduled_crawl_instead_of_backlog_replay() -> None:
    """Expired scheduled crawls are closed with a safe reason."""

    async def _run():
        backend = InMemoryQueueBackend()
        expires_at = _T0 + timedelta(minutes=10)
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_CRAWL_COUPANG,
            payload_json={
                "job_type": JOB_TYPE_CRAWL_COUPANG,
                "job_origin": "scheduler",
                "scheduled_at": _iso(_T0),
                "expires_at": _iso(expires_at),
            },
            now=_T0,
        )
        await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_COUPANG],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        now = expires_at + timedelta(minutes=1)
        result = await recover_once(backend, now=now)

        assert result.recovered_count == 1
        snap = backend.job_snapshot(job_id)
        assert snap is not None
        # 다시 PENDING 으로 돌아가지 않는다.
        assert snap.status == JOB_STATUS_FAILED
        assert snap.status != JOB_STATUS_PENDING
        assert snap.result_json["reason"] == RESULT_REASON_STALE_CRAWL_SKIPPED
        # last_failed_at 또는 completed_at 이 now 로 기록된다.
        assert snap.last_failed_at == now or snap.completed_at == now

    asyncio.run(_run())


def test_queue_recovery_closes_expired_pending_scheduled_crawls() -> None:
    """Pending scheduled jobs with expired payload are not later claimed."""

    async def _run():
        backend = InMemoryQueueBackend()
        expires_at = _T0 + timedelta(minutes=10)
        # claim 하지 않은 채 PENDING 상태로 남아 있는 stale scheduled crawl(서버 downtime backlog).
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_CRAWL_COUPANG,
            payload_json={
                "job_type": JOB_TYPE_CRAWL_COUPANG,
                "job_origin": "scheduler",
                "scheduled_at": _iso(_T0),
                "expires_at": _iso(expires_at),
            },
            now=_T0,
        )

        now = expires_at + timedelta(minutes=1)
        result = await recover_once(backend, now=now)

        assert result.recovered_count == 1
        snap = backend.job_snapshot(job_id)
        assert snap is not None
        assert snap.status == JOB_STATUS_FAILED
        assert snap.result_json["reason"] == RESULT_REASON_STALE_CRAWL_SKIPPED

        # recovery 뒤에는 claim 으로 잡히지 않는다.
        claimed = await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_COUPANG],
            max_jobs=5,
            lease_seconds=120,
            now=now,
        )
        assert claimed == []

    asyncio.run(_run())


def test_queue_recovery_leaves_active_pending_scheduled_crawl_claimable() -> None:
    """A non-expired PENDING scheduled crawl is left untouched and still claimable."""

    async def _run():
        backend = InMemoryQueueBackend()
        expires_at = _T0 + timedelta(minutes=30)
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_CRAWL_COUPANG,
            payload_json={
                "job_type": JOB_TYPE_CRAWL_COUPANG,
                "job_origin": "scheduler",
                "scheduled_at": _iso(_T0),
                "expires_at": _iso(expires_at),
            },
            now=_T0,
        )

        # 아직 payload 유효 → recovery 가 건드리지 않는다.
        result = await recover_once(backend, now=_T0 + timedelta(minutes=1))
        assert result.recovered_count == 0
        assert backend.job_status(job_id) == JOB_STATUS_PENDING

        claimed = await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_COUPANG],
            max_jobs=1,
            lease_seconds=120,
            now=_T0 + timedelta(minutes=1),
        )
        assert len(claimed) == 1

    asyncio.run(_run())


def test_recovery_still_repends_non_expired_retryable_crawl() -> None:
    """Non-expired retryable crawl jobs keep the existing retry behavior."""

    async def _run():
        backend = InMemoryQueueBackend()
        # expires_at 이 아직 안 지난 scheduled crawl — 기존 retry(PENDING 재진입) 유지.
        expires_at = _T0 + timedelta(minutes=30)
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_CRAWL_COUPANG,
            payload_json={
                "job_type": JOB_TYPE_CRAWL_COUPANG,
                "job_origin": "scheduler",
                "scheduled_at": _iso(_T0),
                "expires_at": _iso(expires_at),
            },
            now=_T0,
        )
        await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_COUPANG],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )

        # lease 만 만료(payload 는 아직 유효) → 기존 retry 정책으로 PENDING 재진입.
        result = await recover_once(backend, now=_T0 + timedelta(seconds=31))

        assert result.recovered_count == 1
        assert backend.job_status(job_id) == JOB_STATUS_PENDING

    asyncio.run(_run())


def test_queue_recovery_run_loop_logs_failure_and_keeps_running(capsys):
    sleeps: list[float] = []

    class _FlakyBackend(InMemoryQueueBackend):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def recover_stale(self, *, now: datetime, batch_size: int | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("db temporarily unavailable")
            return 2

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    backend = _FlakyBackend()
    asyncio.run(
        queue_main.run_loop(
            interval_seconds=0.01,
            queue_backend=backend,
            sleep=_sleep,
            max_ticks=2,
        )
    )

    assert backend.calls == 2
    assert sleeps == [0.01]
    out = capsys.readouterr().out
    assert "queue recovery failed" in out
    assert "db temporarily unavailable" in out
    assert '"recovered_count": 2' in out


def test_queue_recovery_run_loop_writes_health_file_via_thread(monkeypatch, tmp_path):
    calls: list[tuple[object, tuple, dict]] = []

    async def _to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(queue_main.asyncio, "to_thread", _to_thread)
    health_file = tmp_path / "queue-recovery.health"

    asyncio.run(
        queue_main.run_loop(
            interval_seconds=0.01,
            queue_backend=InMemoryQueueBackend(),
            max_ticks=1,
            health_file=str(health_file),
        )
    )

    assert calls
    assert calls[0][0] is queue_main._write_health_file
    assert health_file.exists()


def test_compose_defines_queue_recovery_service():
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert "\n  queue-recovery:\n" in compose
    assert "python -m rider_server.queue" in compose
    assert "QUEUE_RECOVERY_HEALTH_FILE" in compose


def test_recover_once_leaves_active_leases_claimed():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        result = await recover_once(backend, now=_T0 + timedelta(seconds=31))

        assert result.recovered_count == 0
        assert backend.job_status(job_id) == JOB_STATUS_CLAIMED

    asyncio.run(_run())


def test_recover_stale_iso_lexical_filter_matches_temporal_staleness() -> None:
    """PostgresQueueBackend 의 ``expires_at <= now`` SQL 텍스트 필터가 시각 비교와 일치한다.

    PENDING 후보를 **실제로 만료된** 행으로만 좁히는 SQL 필터는 ``_iso_utc_z`` 정규형의 사전식
    비교에 의존한다(검토 High — 미래 expires_at 정상 PENDING 이 batch 를 먹지 않게). 이 가정이
    경계에서 깨지면 만료 row 를 놓치거나 정상 row 를 stale 로 오판하므로, 사전식 결과가
    ``stale_recovery_reason``(``_parse_iso_utc`` 기반)과 같은지 잠근다.
    """
    from rider_server.queue.postgres_queue import _iso_utc_z
    from rider_server.queue.states import stale_recovery_reason

    now = datetime(2026, 6, 23, 12, 0, 0, 123456, tzinfo=timezone.utc)
    now_iso = _iso_utc_z(now)
    deltas = [
        timedelta(minutes=-10),
        timedelta(seconds=-1),
        timedelta(0),  # 같은 초 → 만료로 본다(inclusive)
        timedelta(seconds=1),
        timedelta(minutes=10),
    ]
    for d in deltas:
        expires = now + d
        expires_iso = _iso_utc_z(expires)
        lexical_stale = expires_iso <= now_iso
        # scheduled crawl payload 로 Python 권위 판정(같은 _iso_utc 형식).
        reason = stale_recovery_reason(
            job_type=JOB_TYPE_CRAWL_COUPANG,
            payload_json={"job_origin": "scheduler", "expires_at": expires_iso},
            now=now,
            job_status=JOB_STATUS_PENDING,
        )
        temporal_stale = reason is not None
        assert lexical_stale == temporal_stale, (
            f"delta={d}: lexical({lexical_stale}) != temporal({temporal_stale})"
        )


def test_queue_recovery_loop_uses_configured_batch_size() -> None:
    class _RecordingBackend(InMemoryQueueBackend):
        def __init__(self) -> None:
            super().__init__()
            self.batch_sizes: list[int | None] = []

        async def recover_stale(self, *, now: datetime, batch_size: int | None = None) -> int:
            self.batch_sizes.append(batch_size)
            return await super().recover_stale(now=now, batch_size=batch_size)

    backend = _RecordingBackend()

    asyncio.run(
        queue_main.run_loop(
            interval_seconds=0.01,
            queue_backend=backend,
            max_ticks=1,
            settings=queue_main.Settings(
                app_env="test",
                app_version="9.9.9",
                build_sha=None,
                build_time=None,
                job_recovery_batch_size=37,
            ),
        )
    )

    assert backend.batch_sizes == [37]
