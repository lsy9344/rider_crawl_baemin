from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
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
    _PendingTelegramDelivery,
    _attempt_telegram_delivery,
    _record_scoped_to_locked_job,
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


def test_post_commit_telegram_log_update_failure_does_not_escape_complete() -> None:
    calls: list[tuple[str, str, str]] = []

    def send(channel: MessengerChannel, job: DispatchJob, text: str) -> None:
        calls.append((channel.id, job.id, text))

    class FailingSessionFactory:
        def __call__(self):
            raise RuntimeError("delivery log database unavailable")

    repo = PostgresSnapshotIngestRepository(
        FailingSessionFactory(),
        telegram_sender=send,
    )
    delivery = _PendingTelegramDelivery(
        channel=_channel(),
        job=_job(),
        message=_message(),
        log_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        collected_at=_NOW,
    )

    asyncio.run(repo._deliver_telegram_after_commit(delivery, now=_NOW))

    assert calls == [
        (
            "11111111-1111-1111-1111-111111111111",
            "33333333-3333-3333-3333-333333333333",
            "[test] live result",
        )
    ]


def test_default_snapshot_ingest_wires_telegram_sender_when_sending_enabled(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_repo(session_factory, *, telegram_sender=None):
        captured["session_factory"] = session_factory
        captured["telegram_sender"] = telegram_sender
        return object()

    monkeypatch.setattr("rider_server.main.create_engine", lambda _url: object())
    monkeypatch.setattr("rider_server.main.create_session_factory", lambda _engine: "sessions")
    monkeypatch.setattr("rider_server.main.PostgresSnapshotIngestRepository", fake_repo)

    _default_job_result_ingest_service(_settings())

    assert captured["session_factory"] == "sessions"
    assert captured["telegram_sender"] is not None


def test_default_snapshot_ingest_wires_sender_with_db_even_when_global_gate_off(monkeypatch) -> None:
    """0012: DB(tenant provider) 있으면 전역 env send 게이트가 꺼져 있어도 sender 를 구성한다.

    실발송 차단은 더 이상 "sender 미구성"이 아니라 **발송 직전 tenant 게이트**(fail-closed)로
    수행한다 — 전역 OFF 라도 특정 tenant 가 sending_enabled=True 면 그 tenant 만 발송 가능하다.
    실제 게이트 동작은 test_tenant_telegram_gate 에서 단위 검증한다.
    """
    captured: dict[str, object] = {}

    def fake_repo(session_factory, *, telegram_sender=None):
        captured["session_factory"] = session_factory
        captured["telegram_sender"] = telegram_sender
        return object()

    monkeypatch.setattr("rider_server.main.create_engine", lambda _url: object())
    monkeypatch.setattr("rider_server.main.create_session_factory", lambda _engine: "sessions")
    monkeypatch.setattr("rider_server.main.PostgresSnapshotIngestRepository", fake_repo)

    _default_job_result_ingest_service(_settings(sending_enabled=False))

    assert captured["session_factory"] == "sessions"
    # DB provider 가 존재하므로 sender 는 구성된다(게이트는 per-tenant 로 이동).
    assert captured["telegram_sender"] is not None
