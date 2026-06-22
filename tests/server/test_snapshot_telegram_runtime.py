from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from rider_server.domain import (
    DeliveryStatus,
    Message,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
)
from rider_server.main import _default_job_result_ingest_service
from rider_server.services.job_result_ingest_service import JobResultIngestError, SnapshotIngestRecord
from rider_server.services.dispatch_fanout_service import DispatchJob
from rider_server.services.snapshot_repository_postgres import (
    PostgresSnapshotIngestRepository,
    _attempt_telegram_delivery,
    _record_scoped_to_locked_job,
    _send_window_allows_dispatch,
)
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _settings(**overrides) -> Settings:
    data = {
        "app_env": "test",
        "app_version": "0.0.0",
        "build_sha": None,
        "build_time": None,
        "database_url": "postgresql+asyncpg://user:pass@db:5432/rider",
        "telegram_webhook_secret_ref": None,
        "telegram_bot_token_ref": "env:RIDER_TEST_TELEGRAM_TOKEN",
        "sending_enabled": True,
        "admin_ip_allowlist": (),
        "admin_mfa_required": True,
        "admin_allowed_origins": (),
    }
    data.update(overrides)
    return Settings(**data)


def _channel() -> MessengerChannel:
    return MessengerChannel(
        id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        messenger=Messenger.TELEGRAM,
        telegram_chat_id="-100fake",
        thread_id="7",
        state=MessengerChannelState.ACTIVE,
    )


def _job() -> DispatchJob:
    return DispatchJob(
        id="33333333-3333-3333-3333-333333333333",
        target_id="44444444-4444-4444-4444-444444444444",
        channel_id="11111111-1111-1111-1111-111111111111",
        message_id="55555555-5555-5555-5555-555555555555",
        messenger=Messenger.TELEGRAM,
        template_version="baemin.realtime.v1",
        message_hash="a" * 64,
    )


def _message() -> Message:
    return Message(
        id="55555555-5555-5555-5555-555555555555",
        snapshot_id="66666666-6666-6666-6666-666666666666",
        template_version="baemin.realtime.v1",
        text="[test] live result",
        text_hash="a" * 64,
        text_redacted_preview="[test] live result",
    )


def _snapshot_record() -> SnapshotIngestRecord:
    return SnapshotIngestRecord(
        job_id="33333333-3333-3333-3333-333333333333",
        agent_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        target_id="44444444-4444-4444-4444-444444444444",
        tenant_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        platform="baemin",
        platform_account_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        collected_at=_NOW,
        parser_version="baemin-v1",
        quality_state="OK",
        normalized_json={
            "center_name": "센터",
            "completed": 1,
            "assigned": 1,
            "canceled": 0,
            "rejected": 0,
        },
        artifact_refs=[],
        completed_at=_NOW,
    )


def test_snapshot_record_scope_uses_locked_job_payload_over_agent_result() -> None:
    job = SimpleNamespace(
        target_id="55555555-5555-5555-5555-555555555555",
        payload_json={
            "tenant_id": "22222222-2222-2222-2222-222222222222",
            "platform": "COUPANG",
            "platform_account_id": "99999999-9999-9999-9999-999999999999",
        },
    )

    scoped = _record_scoped_to_locked_job(_snapshot_record(), job)

    assert scoped.target_id == "55555555-5555-5555-5555-555555555555"
    assert scoped.tenant_id == "22222222-2222-2222-2222-222222222222"
    assert scoped.platform == "coupang"
    assert scoped.platform_account_id == "99999999-9999-9999-9999-999999999999"


def test_snapshot_record_scope_requires_server_owned_job_payload() -> None:
    job = SimpleNamespace(
        target_id="55555555-5555-5555-5555-555555555555",
        payload_json={"platform": "baemin", "platform_account_id": "account"},
    )

    with pytest.raises(JobResultIngestError, match="job payload tenant_id is required"):
        _record_scoped_to_locked_job(_snapshot_record(), job)


def test_send_window_allows_dispatch_when_disabled() -> None:
    assert _send_window_allows_dispatch(
        _NOW,
        schedule_enabled=False,
        start_time="",
        stop_time="",
    ) is True


def test_send_window_allows_dispatch_inside_same_day_kst_window() -> None:
    assert _send_window_allows_dispatch(
        datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc),
        schedule_enabled=True,
        start_time="09:00",
        stop_time="22:00",
    ) is True


def test_send_window_blocks_dispatch_outside_same_day_kst_window() -> None:
    assert _send_window_allows_dispatch(
        datetime(2026, 6, 16, 13, 30, tzinfo=timezone.utc),
        schedule_enabled=True,
        start_time="09:00",
        stop_time="22:00",
    ) is False


def test_send_window_allows_overnight_kst_window() -> None:
    assert _send_window_allows_dispatch(
        datetime(2026, 6, 16, 13, 30, tzinfo=timezone.utc),
        schedule_enabled=True,
        start_time="22:00",
        stop_time="06:00",
    ) is True
    assert _send_window_allows_dispatch(
        datetime(2026, 6, 16, 22, 0, tzinfo=timezone.utc),
        schedule_enabled=True,
        start_time="22:00",
        stop_time="06:00",
    ) is False


def test_snapshot_telegram_attempt_success_marks_sent_and_calls_sender() -> None:
    calls: list[tuple[str, str, str]] = []

    def send(channel: MessengerChannel, job: DispatchJob, text: str) -> None:
        calls.append((channel.id, job.id, text))

    log = asyncio.run(
        _attempt_telegram_delivery(
            channel=_channel(),
            job=_job(),
            message=_message(),
            log_id="77777777-7777-7777-7777-777777777777",
            collected_at=_NOW,
            now=_NOW,
            send=send,
        )
    )

    assert calls == [
        (
            "11111111-1111-1111-1111-111111111111",
            "33333333-3333-3333-3333-333333333333",
            "[test] live result",
        )
    ]
    assert log.status is DeliveryStatus.SENT
    assert log.error_code is None
    assert log.sent_at == _NOW


def test_snapshot_telegram_attempt_failure_uses_telegram_failure_policy() -> None:
    def send(_channel: MessengerChannel, _job: DispatchJob, _text: str) -> None:
        raise RuntimeError("bot api unavailable")

    log = asyncio.run(
        _attempt_telegram_delivery(
            channel=_channel(),
            job=_job(),
            message=_message(),
            log_id="77777777-7777-7777-7777-777777777777",
            collected_at=_NOW,
            now=_NOW,
            send=send,
        )
    )

    assert log.status is DeliveryStatus.FAILED
    assert log.error_code == "TELEGRAM_FAILURE"
    assert log.sent_at is None


def test_dispatch_worker_retryable_failure_updates_backoff_state() -> None:
    from rider_server.services.dispatch_worker import (
        ClaimedTelegramDelivery,
        TelegramDispatchWorker,
    )

    def send(_channel: MessengerChannel, _job: DispatchJob, _text: str) -> None:
        raise RuntimeError("bot api unavailable")

    worker = TelegramDispatchWorker(telegram_sender=send, max_attempts=3)
    delivery = ClaimedTelegramDelivery(
        log_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        channel=_channel(),
        job=_job(),
        message_text=_message().text,
        collected_at=_NOW,
        attempt_count=0,
    )

    update = asyncio.run(worker.attempt_delivery(delivery, now=_NOW))

    assert update.status == DeliveryStatus.RETRYING.value
    assert update.error_code == "TELEGRAM_FAILURE"
    assert update.sent_at is None
    assert update.available_at == _NOW + timedelta(seconds=30)
    assert update.attempt_count == 1
    assert update.locked_at is None
    assert update.locked_by is None


def test_dispatch_worker_non_retryable_final_failure_clears_claim() -> None:
    from rider_server.services.dispatch_worker import (
        ClaimedTelegramDelivery,
        TelegramDispatchWorker,
    )

    def send(_channel: MessengerChannel, _job: DispatchJob, _text: str) -> None:
        raise RuntimeError("bot api unavailable")

    worker = TelegramDispatchWorker(telegram_sender=send, max_attempts=1)
    delivery = ClaimedTelegramDelivery(
        log_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        channel=_channel(),
        job=_job(),
        message_text=_message().text,
        collected_at=_NOW,
        attempt_count=0,
    )

    update = asyncio.run(worker.attempt_delivery(delivery, now=_NOW))

    assert update.status == DeliveryStatus.FAILED.value
    assert update.error_code == "TELEGRAM_FAILURE"
    assert update.sent_at is None
    assert update.available_at is None
    assert update.attempt_count == 1
    assert update.locked_at is None
    assert update.locked_by is None


def test_dispatch_worker_ambiguous_telegram_failure_is_not_retried() -> None:
    from rider_crawl.sender import TelegramSendError
    from rider_server.services.dispatch_worker import (
        ClaimedTelegramDelivery,
        TelegramDispatchWorker,
    )

    calls = 0

    def send(_channel: MessengerChannel, _job: DispatchJob, _text: str) -> None:
        nonlocal calls
        calls += 1
        raise TelegramSendError("response lost after request", ambiguous=True)

    worker = TelegramDispatchWorker(telegram_sender=send, max_attempts=3)
    delivery = ClaimedTelegramDelivery(
        log_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        channel=_channel(),
        job=_job(),
        message_text=_message().text,
        collected_at=_NOW,
        attempt_count=0,
    )

    update = asyncio.run(worker.attempt_delivery(delivery, now=_NOW))

    assert calls == 1
    assert update.status == DeliveryStatus.HELD.value
    assert update.error_code == "TELEGRAM_FAILURE"
    assert update.available_at is None
    assert update.locked_at is None
    assert update.locked_by is None


def test_dispatch_worker_runs_sync_sender_outside_event_loop() -> None:
    from rider_server.services.dispatch_worker import (
        ClaimedTelegramDelivery,
        TelegramDispatchWorker,
    )

    def send(_channel: MessengerChannel, _job: DispatchJob, _text: str) -> None:
        # The production dispatcher's sync sender resolves tenant config with
        # asyncio.run(); this must not execute inside the worker event loop.
        asyncio.run(asyncio.sleep(0))

    worker = TelegramDispatchWorker(telegram_sender=send, max_attempts=1)
    delivery = ClaimedTelegramDelivery(
        log_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        channel=_channel(),
        job=_job(),
        message_text=_message().text,
        collected_at=_NOW,
        attempt_count=0,
    )

    update = asyncio.run(worker.attempt_delivery(delivery, now=_NOW))

    assert update.status == DeliveryStatus.SENT.value
    assert update.error_code is None
    assert update.sent_at == _NOW


def test_dispatch_worker_marks_send_started_before_external_send(monkeypatch) -> None:
    from rider_server.services.dispatch_worker import (
        ClaimedTelegramDelivery,
        DeliveryLogUpdate,
        TelegramDispatchWorker,
    )

    worker = TelegramDispatchWorker(telegram_sender=lambda *_args: None)
    delivery = ClaimedTelegramDelivery(
        log_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        channel=_channel(),
        job=_job(),
        message_text=_message().text,
        collected_at=_NOW,
        attempt_count=0,
    )
    calls: list[str] = []

    async def claim_pending(*, now: datetime):
        calls.append("claim")
        return [delivery]

    async def mark_send_started(log_id: uuid.UUID, *, now: datetime):
        assert log_id == delivery.log_id
        calls.append("send-started")

    async def attempt_delivery(claimed, *, now: datetime):
        assert claimed == delivery
        calls.append("send")
        return DeliveryLogUpdate(
            status=DeliveryStatus.SENT.value,
            error_code=None,
            sent_at=now,
            available_at=None,
            attempt_count=1,
            locked_at=None,
            locked_by=None,
        )

    async def apply_update(log_id: uuid.UUID, update: DeliveryLogUpdate):
        assert log_id == delivery.log_id
        calls.append("update")

    monkeypatch.setattr(worker, "claim_pending", claim_pending)
    monkeypatch.setattr(worker, "mark_send_started", mark_send_started)
    monkeypatch.setattr(worker, "attempt_delivery", attempt_delivery)
    monkeypatch.setattr(worker, "apply_update", apply_update)

    count = asyncio.run(worker.run_once(now=_NOW))

    assert count == 1
    assert calls == ["claim", "send-started", "send", "update"]


def test_apply_update_runs_real_sqlalchemy_update_without_shadowing() -> None:
    """회귀: ``apply_update`` 파라미터가 모듈 ``from sqlalchemy import update`` 를 섀도잉하면
    ``update(DeliveryLogRow)`` 가 dataclass 호출(TypeError)이 된다. monkeypatch 없이 실제
    본문을 실행해 SQLAlchemy Update 구문이 생성·execute 되는지 확인한다."""
    from sqlalchemy.sql.dml import Update

    from rider_server.services.dispatch_worker import (
        DeliveryLogUpdate,
        TelegramDispatchWorker,
    )

    executed: list = []

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, statement):
            executed.append(statement)
            return _FakeResult()

        async def commit(self):
            executed.append("commit")

    def _session_factory():
        return _FakeSession()

    worker = TelegramDispatchWorker(
        telegram_sender=lambda *_args: None,
        session_factory=_session_factory,
    )
    update_values = DeliveryLogUpdate(
        status=DeliveryStatus.SENT.value,
        error_code=None,
        sent_at=_NOW,
        available_at=None,
        attempt_count=1,
        locked_at=None,
        locked_by=None,
    )

    # 버그가 있으면 여기서 TypeError('DeliveryLogUpdate' object is not callable)가 난다.
    asyncio.run(
        worker.apply_update(
            uuid.UUID("77777777-7777-7777-7777-777777777777"), update_values
        )
    )

    # execute 에 실제 SQLAlchemy Update 구문(상태머신 함수)이 전달됐는지 확인.
    assert any(isinstance(stmt, Update) for stmt in executed)
    assert "commit" in executed


def test_stale_sending_delivery_is_held_not_auto_retried(monkeypatch) -> None:
    from rider_server.services.dispatch_worker import TelegramDispatchWorker

    worker = TelegramDispatchWorker(telegram_sender=lambda *_args: None)
    calls: list[str] = []

    async def hold_stale_sending(*, now: datetime):
        calls.append("hold-stale-sending")
        return 1

    async def claim_pending(*, now: datetime):
        calls.append("claim-pending")
        return []

    monkeypatch.setattr(worker, "hold_stale_sending", hold_stale_sending)
    monkeypatch.setattr(worker, "claim_pending", claim_pending)

    count = asyncio.run(worker.run_once(now=_NOW))

    assert count == 0
    assert calls == ["hold-stale-sending", "claim-pending"]


def test_dispatch_worker_claimed_delivery_uses_full_message_text_not_preview() -> None:
    from rider_server.services.dispatch_worker import TelegramDispatchWorker

    log = SimpleNamespace(
        id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        attempt_count=0,
    )
    message = SimpleNamespace(
        id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
        template_version="baemin.realtime.v1",
        text_hash="a" * 64,
        text="[실시간 실적봇]\n원문 전송 본문",
        text_redacted_preview="[미리보기] 원문 아님",
    )
    snapshot = SimpleNamespace(
        target_id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        collected_at=_NOW,
    )
    channel = SimpleNamespace(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        tenant_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        messenger=Messenger.TELEGRAM.value,
        telegram_chat_id="-100fake",
        thread_id="7",
        kakao_room_name=None,
        state=MessengerChannelState.ACTIVE.value,
    )

    delivery = TelegramDispatchWorker._delivery_from_rows(log, message, snapshot, channel)

    assert delivery.message_text == "[실시간 실적봇]\n원문 전송 본문"


def test_snapshot_repository_no_longer_owns_post_commit_telegram_delivery() -> None:
    assert not hasattr(PostgresSnapshotIngestRepository, "_deliver_telegram_after_commit")


def test_default_snapshot_ingest_no_longer_wires_telegram_sender(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_repo(session_factory, *, telegram_sender=None):
        captured["session_factory"] = session_factory
        captured["telegram_sender"] = telegram_sender
        return object()

    monkeypatch.setattr("rider_server.main.create_engine", lambda _url, **_kwargs: object())
    monkeypatch.setattr("rider_server.main.create_session_factory", lambda _engine: "sessions")
    monkeypatch.setattr("rider_server.main.PostgresSnapshotIngestRepository", fake_repo)

    _default_job_result_ingest_service(_settings())

    assert captured["session_factory"] == "sessions"
    assert captured["telegram_sender"] is None


def test_default_snapshot_ingest_keeps_sender_out_when_global_gate_off(monkeypatch) -> None:
    """Snapshot ingest stores outbox work; a dispatch worker owns Telegram send."""
    captured: dict[str, object] = {}

    def fake_repo(session_factory, *, telegram_sender=None):
        captured["session_factory"] = session_factory
        captured["telegram_sender"] = telegram_sender
        return object()

    monkeypatch.setattr("rider_server.main.create_engine", lambda _url, **_kwargs: object())
    monkeypatch.setattr("rider_server.main.create_session_factory", lambda _engine: "sessions")
    monkeypatch.setattr("rider_server.main.PostgresSnapshotIngestRepository", fake_repo)

    _default_job_result_ingest_service(_settings(sending_enabled=False))

    assert captured["session_factory"] == "sessions"
    assert captured["telegram_sender"] is None
