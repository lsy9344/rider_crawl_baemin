"""Story 5.3 / AC4 — Agent API 라우트(claim/complete/events) + 실 Agent↔실 서버 큐 e2e.

(1) HTTP 계약: claim/complete/events 라우트가 Agent 본문 모양·상태 매핑·lease 소유 검증·에러
    envelope 를 정확히 낸다(bearer 401, 재할당 409, 미존재 404, 알 수 없는 status 422).
(2) end-to-end(in-memory, 항상 실행): ``rider_agent.job_loop`` 의 **실제** ``JobRunner``/
    ``claim_jobs``/``complete_job`` 을 ``TestClient``(ASGI in-process) 로 ``create_app``(in-memory
    backend 주입)에 연결해 enqueue→Agent claim→execute→complete 까지 mock 없이 한 바퀴 돈다.

``pytest-asyncio`` 미도입 — TestClient(동기, 내부 이벤트 루프)로 async 앱을 in-process 구동한다.
Agent transport(``registration.Transport``)는 동기 ``post_json`` seam 이라 TestClient 를 그 seam 에
어댑트하는 얇은 어댑터를 둔다(httpx ASGITransport 직접 대신 5.1 검증된 TestClient 패턴 재사용).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from rider_agent.heartbeat import DEFAULT_CAPABILITIES
from rider_agent.job_loop import JobRunner, make_success_result
from rider_agent.registration import TransportError
from rider_agent.secure_store import AgentIdentity
from rider_crawl.redaction import REDACTED
from rider_server.main import create_app
from rider_server.queue import (
    COMPLETE_ACCEPTED,
    COMPLETE_LEASE_LOST,
    COMPLETE_NOT_FOUND,
    ClaimedJobRecord,
    CompleteOutcome,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    InMemoryQueueBackend,
)
from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN
from rider_server.services.job_result_ingest_service import (
    JobResultIngestService,
    SnapshotIngestRecord,
)
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_BEARER = {"Authorization": "Bearer agent-1"}
_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)


def _headers_for(agent_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {agent_id}"}


def _app_with_backend(
    backend: InMemoryQueueBackend,
    *,
    job_result_ingest_service=None,
    resolved_agent_id: str | None = "agent-1",
):
    app = create_app(
        _SETTINGS,
        queue_backend=backend,
        job_result_ingest_service=job_result_ingest_service,
    )
    if resolved_agent_id is not None:
        app.state.resolve_agent_id = lambda _token: resolved_agent_id
    return app


class _CompleteRaceBackend:
    """complete 직전 lease 상실/미존재 race 를 재현하는 route 테스트용 backend."""

    def __init__(self, *, target_id: str, complete_result: str) -> None:
        self._target_id = target_id
        self._complete_result = complete_result
        self.complete_calls: list[dict[str, object]] = []

    async def in_flight_job(self, *, job_id: str, agent_id: str, now: datetime):
        return ClaimedJobRecord(
            job_id=job_id,
            type=JOB_TYPE_CRAWL_BAEMIN,
            target_id=self._target_id,
            lease_expires_at=now,
        )

    async def complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json=None,
        error_code=None,
        now: datetime,
    ):
        self.complete_calls.append(
            {
                "job_id": job_id,
                "agent_id": agent_id,
                "status": status,
                "result_json": result_json,
                "error_code": error_code,
            }
        )
        return CompleteOutcome(self._complete_result, job_id)

    async def claim(self, **_kwargs):
        return []

    async def enqueue(self, **_kwargs):
        return "unused"

    async def extend_lease(self, **_kwargs):
        return False

    async def recover_stale(self, **_kwargs):
        return 0

    async def emit_event(self, **_kwargs):
        return None


# ══════════════════════════════════════════════════════════════════════════
# (1) HTTP 라우트 계약
# ══════════════════════════════════════════════════════════════════════════


def test_claim_requires_bearer_returns_401_envelope():
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    # bearer 없음 → 401 + 전역 에러 envelope
    r = client.post("/v1/jobs/claim", json={"agent_id": "agent-1", "capabilities": [], "max_jobs": 1})
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_claim_empty_queue_returns_empty_jobs():
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        "/v1/jobs/claim",
        json={"agent_id": "agent-1", "capabilities": [JOB_TYPE_CRAWL_BAEMIN], "max_jobs": 1},
        headers=_BEARER,
    )
    assert r.status_code == 200
    assert r.json() == {"jobs": []}


def test_claim_returns_job_with_iso_utc_lease():
    import asyncio

    backend = InMemoryQueueBackend()
    job_id = asyncio.run(
        backend.enqueue(
            job_type=JOB_TYPE_CRAWL_BAEMIN,
            payload_json={"target_id": "target-1", "platform": "baemin"},
            now=_NOW,
        )
    )
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        "/v1/jobs/claim",
        json={"agent_id": "agent-1", "capabilities": [JOB_TYPE_CRAWL_BAEMIN], "max_jobs": 5},
        headers=_BEARER,
    )
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id
    assert jobs[0]["type"] == JOB_TYPE_CRAWL_BAEMIN
    assert jobs[0]["payload"] == {"target_id": "target-1", "platform": "baemin"}
    # ISO 8601 UTC(...Z) 로 통일(epoch 혼용 금지)
    assert jobs[0]["lease_expires_at"].endswith("Z")


def test_complete_reassigned_or_mismatch_owner_returns_409():
    import asyncio

    backend = InMemoryQueueBackend()
    job_id = asyncio.run(backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_NOW))
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_NOW,
        )
    )
    client = TestClient(_app_with_backend(backend, resolved_agent_id="agent-2"))
    # 다른 agent_id 가 complete → 소유 불일치 → 409(Agent 가 lease_lost 흡수)
    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={"status": "success", "agent_id": "agent-2", "result_json": {}},
        headers=_headers_for("agent-2"),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_claim_rejects_bearer_agent_body_agent_mismatch() -> None:
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend, resolved_agent_id="agent-1"))

    r = client.post(
        "/v1/jobs/claim",
        json={"agent_id": "agent-2", "capabilities": [JOB_TYPE_CRAWL_BAEMIN], "max_jobs": 1},
        headers=_BEARER,
    )

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_claim_rejects_async_resolver_that_returns_none() -> None:
    backend = InMemoryQueueBackend()
    app = _app_with_backend(backend, resolved_agent_id=None)

    async def _resolve_none(_token: str):
        return None

    app.state.resolve_agent_id = _resolve_none
    client = TestClient(app)

    r = client.post(
        "/v1/jobs/claim",
        json={"agent_id": "agent-1", "capabilities": [JOB_TYPE_CRAWL_BAEMIN], "max_jobs": 1},
        headers=_BEARER,
    )

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_complete_unknown_job_returns_404():
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        "/v1/jobs/does-not-exist/complete",
        json={"status": "success", "agent_id": "agent-1"},
        headers=_BEARER,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_complete_unknown_status_returns_422():
    import asyncio

    backend = InMemoryQueueBackend()
    job_id = asyncio.run(backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_NOW))
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_NOW,
        )
    )
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={"status": "weird-status", "agent_id": "agent-1"},
        headers=_BEARER,
    )
    assert r.status_code == 422


def test_events_accepted_202():
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        "/v1/jobs/job-1/events",
        json={
            "event_type": "JOB_STARTED",
            "severity": "info",
            "message_redacted": "job started: CRAWL_BAEMIN",
            "artifact_refs": [],
        },
        headers=_BEARER,
    )
    assert r.status_code == 202
    assert backend.events and backend.events[0]["event_type"] == "JOB_STARTED"


# ── (QA gap A) events 본문은 서버가 한 번 더 redact 통과시켜 secret 평문이 안 남는다 ──


def test_events_redacts_secret_in_message():
    # guardrail #8 (secret 평문 0): message 에 token/phone 형 문자열이 섞여 와도 서버가
    # redact() 를 한 번 더 통과시켜 평문이 events 기록/로그에 남지 않는다.
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    leak = "auth failed token=987654321:ABCdefGHIjkl phone=010-1234-5678"
    r = client.post(
        "/v1/jobs/job-x/events",
        json={
            "event_type": "JOB_FAILED",
            "severity": "error",
            "message_redacted": leak,
            "artifact_refs": [],
        },
        headers=_BEARER,
    )
    assert r.status_code == 202
    stored = backend.events[-1]["message_redacted"]
    assert REDACTED in stored
    assert "987654321:ABCdefGHIjkl" not in stored
    assert "010-1234-5678" not in stored


# ── (QA gap G) complete 해피패스 HTTP 200 + 소문자 status → 상태머신값 매핑 ─────────


def test_complete_success_returns_200_with_succeeded():
    import asyncio

    backend = InMemoryQueueBackend()
    # 라우트는 실 wall-clock now 로 lease 만료를 검증하므로, 실 현재 시각 기준으로 claim 한다
    # (lease 120s 안에 complete 가 일어나 만료되지 않음 — e2e 테스트와 동일 패턴).
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(
        backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, target_id="target-1", now=now)
    )
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )
    client = TestClient(_app_with_backend(backend))
    # Agent 가 실제로 보내는 full 본문(started_at/finished_at/metrics 포함)을 그대로 수용해야 한다.
    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={
            "status": "success",
            "agent_id": "agent-1",
            "result_json": {"ok": True},
            "error_code": None,
            "error_message_redacted": None,
            "metrics": {"duration_ms": 12},
            "started_at": now.timestamp(),
            "finished_at": now.timestamp() + 1,
        },
        headers=_BEARER,
    )
    assert r.status_code == 200
    assert r.json() == {"job_id": job_id, "status": JOB_STATUS_SUCCEEDED}
    assert backend.job_status(job_id) == JOB_STATUS_SUCCEEDED


def test_complete_redacts_result_json_before_queue_storage():
    import asyncio

    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(
        backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, target_id="target-1", now=now)
    )
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )
    client = TestClient(_app_with_backend(backend))

    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={
            "status": "success",
            "agent_id": "agent-1",
            "result_json": {
                "ok": True,
                "otp": "123456",
                "verification_email_app_password": "mail-app-password",
                "nested": {"email": "mailbox@example.invalid"},
            },
        },
        headers=_BEARER,
    )

    assert r.status_code == 200
    stored = backend.job_snapshot(job_id).result_json
    blob = json.dumps(stored, ensure_ascii=False)
    assert REDACTED in blob
    assert "123456" not in blob
    assert "mail-app-password" not in blob
    assert "mailbox@example.invalid" not in blob
    assert stored["ok"] is True


def test_complete_success_commits_snapshot_ingest_when_service_configured():
    import asyncio

    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(
        backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, target_id="target-1", now=now)
    )
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )
    records: list[SnapshotIngestRecord] = []
    service = JobResultIngestService(save_snapshot=records.append)
    client = TestClient(_app_with_backend(backend, job_result_ingest_service=service))

    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={
            "status": "success",
            "agent_id": "agent-1",
            "result_json": {
                "schema_version": 1,
                "result_type": "snapshot",
                "target_id": "target-1",
                "tenant_id": "tenant-1",
                "platform_account_id": "account-1",
                "platform": "baemin",
                "collected_at": "2026-06-15T00:00:00Z",
                "parser_version": "baemin-v1",
                "quality_state": "OK",
                "normalized_json": {
                    "center_name": "배민센터A",
                    "completed_count": 102,
                    "raw_html": "<html>secret</html>",
                },
                "artifact_refs": [],
            },
        },
        headers=_BEARER,
    )

    assert r.status_code == 200
    assert backend.job_status(job_id) == JOB_STATUS_SUCCEEDED
    assert len(records) == 1
    record = records[0]
    assert record.job_id == job_id
    assert record.agent_id == "agent-1"
    assert record.target_id == "target-1"
    assert record.tenant_id == "tenant-1"
    assert record.platform == "baemin"
    assert record.normalized_json == {"center_name": "배민센터A", "completed_count": 102}


def test_complete_success_uses_atomic_snapshot_complete_when_service_provides_it():
    backend = _CompleteRaceBackend(target_id="target-1", complete_result=COMPLETE_LEASE_LOST)

    class _AtomicIngestService(JobResultIngestService):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[SnapshotIngestRecord] = []

        async def complete_snapshot_job(
            self,
            record: SnapshotIngestRecord,
            *,
            agent_id: str,
            status: str,
            result_json: dict | None,
            error_code: str | None,
            now: datetime,
        ) -> CompleteOutcome:
            self.records.append(record)
            return CompleteOutcome(COMPLETE_ACCEPTED, record.job_id, final_status=status)

    service = _AtomicIngestService()
    client = TestClient(
        _app_with_backend(backend, job_result_ingest_service=service),
        raise_server_exceptions=False,
    )

    r = client.post(
        "/v1/jobs/job-atomic-1/complete",
        json={
            "status": "success",
            "agent_id": "agent-1",
            "result_json": {
                "schema_version": 1,
                "result_type": "snapshot",
                "target_id": "target-1",
                "tenant_id": "tenant-1",
                "platform_account_id": "account-1",
                "platform": "baemin",
                "collected_at": "2026-06-15T00:00:00Z",
                "parser_version": "baemin-v1",
                "quality_state": "OK",
                "normalized_json": {
                    "center_name": "배민센터A",
                    "completed_count": 102,
                },
                "artifact_refs": [],
            },
        },
        headers=_BEARER,
    )

    assert r.status_code == 200
    assert r.json()["status"] == JOB_STATUS_SUCCEEDED
    assert [record.job_id for record in service.records] == ["job-atomic-1"]
    assert backend.complete_calls == []


def test_database_app_uses_atomic_snapshot_ingest_service() -> None:
    app = create_app(
        Settings(
            app_env="test",
            app_version="9.9.9",
            build_sha=None,
            build_time=None,
            database_url="postgresql+asyncpg://user:pass@localhost/db",
        )
    )

    service = app.state.job_result_ingest_service

    assert service is not None
    assert callable(getattr(service, "complete_snapshot_job", None))


def test_complete_snapshot_rejects_target_mismatch_before_ingest() -> None:
    import asyncio

    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(
        backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, target_id="target-1", now=now)
    )
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )
    records: list[SnapshotIngestRecord] = []
    service = JobResultIngestService(save_snapshot=records.append)
    client = TestClient(_app_with_backend(backend, job_result_ingest_service=service))

    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={
            "status": "success",
            "agent_id": "agent-1",
            "result_json": {
                "schema_version": 1,
                "result_type": "snapshot",
                "target_id": "target-2",
                "platform": "baemin",
                "collected_at": "2026-06-15T00:00:00Z",
                "parser_version": "baemin-v1",
                "quality_state": "OK",
                "normalized_json": {"center_name": "배민센터A"},
            },
        },
        headers=_BEARER,
    )

    assert r.status_code == 422
    assert backend.job_status(job_id) == JOB_STATUS_CLAIMED
    assert records == []


def test_complete_snapshot_persistence_failure_keeps_job_claimed() -> None:
    import asyncio

    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(
        backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, target_id="target-1", now=now)
    )
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )

    def _raise(_record: SnapshotIngestRecord) -> None:
        raise RuntimeError("snapshot insert failed")

    service = JobResultIngestService(save_snapshot=_raise)
    client = TestClient(
        _app_with_backend(backend, job_result_ingest_service=service),
        raise_server_exceptions=False,
    )

    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={
            "status": "success",
            "agent_id": "agent-1",
            "result_json": {
                "schema_version": 1,
                "result_type": "snapshot",
                "target_id": "target-1",
                "platform": "baemin",
                "collected_at": "2026-06-15T00:00:00Z",
                "parser_version": "baemin-v1",
                "quality_state": "OK",
                "normalized_json": {"center_name": "배민센터A"},
            },
        },
        headers=_BEARER,
    )

    assert r.status_code == 500
    assert backend.job_status(job_id) == JOB_STATUS_CLAIMED


@pytest.mark.parametrize(
    ("complete_result", "expected_status"),
    [(COMPLETE_LEASE_LOST, 409), (COMPLETE_NOT_FOUND, 404)],
)
def test_complete_snapshot_does_not_commit_if_backend_complete_loses_race(
    complete_result: str, expected_status: int
) -> None:
    backend = _CompleteRaceBackend(target_id="target-1", complete_result=complete_result)
    records: list[SnapshotIngestRecord] = []
    service = JobResultIngestService(save_snapshot=records.append)
    client = TestClient(
        _app_with_backend(backend, job_result_ingest_service=service),
        raise_server_exceptions=False,
    )

    r = client.post(
        "/v1/jobs/job-race-1/complete",
        json={
            "status": "success",
            "agent_id": "agent-1",
            "result_json": {
                "schema_version": 1,
                "result_type": "snapshot",
                "target_id": "target-1",
                "platform": "baemin",
                "collected_at": "2026-06-15T00:00:00Z",
                "parser_version": "baemin-v1",
                "quality_state": "OK",
                "normalized_json": {"center_name": "배민센터A"},
            },
        },
        headers=_BEARER,
    )

    assert r.status_code == expected_status
    assert records == []
    assert backend.complete_calls


def test_complete_failed_returns_200_with_failed():
    import asyncio

    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=now))
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={
            "status": "failed",
            "agent_id": "agent-1",
            "error_code": "UNSUPPORTED_JOB_TYPE",
            "error_message_redacted": "job failed",
        },
        headers=_BEARER,
    )
    assert r.status_code == 200
    assert r.json()["status"] == JOB_STATUS_FAILED
    assert backend.job_status(job_id) == JOB_STATUS_FAILED


def test_complete_failed_does_not_commit_snapshot_ingest_when_service_configured():
    import asyncio

    backend = InMemoryQueueBackend()
    now = datetime.now(timezone.utc)
    job_id = asyncio.run(backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=now))
    asyncio.run(
        backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=now,
        )
    )
    records: list[SnapshotIngestRecord] = []
    service = JobResultIngestService(save_snapshot=records.append)
    client = TestClient(_app_with_backend(backend, job_result_ingest_service=service))

    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={
            "status": "failed",
            "agent_id": "agent-1",
            "error_code": "AUTH_REQUIRED",
            "result_json": {
                "schema_version": 1,
                "result_type": "snapshot",
                "target_id": "target-1",
                "platform": "baemin",
                "collected_at": "2026-06-15T00:00:00Z",
                "parser_version": "baemin-v1",
                "quality_state": "OK",
                "normalized_json": {"center_name": "배민센터A"},
            },
        },
        headers=_BEARER,
    )

    assert r.status_code == 200
    assert backend.job_status(job_id) == JOB_STATUS_FAILED
    assert records == []


# ══════════════════════════════════════════════════════════════════════════
# (2) 실 Agent JobRunner ↔ 실 서버 큐 end-to-end (in-memory, 항상 실행)
# ══════════════════════════════════════════════════════════════════════════


class _TestClientTransport:
    """rider_agent ``Transport`` seam 을 ``TestClient`` 로 어댑트(동기 post_json)."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def post_json(self, url, body, *, headers=None):
        path = urlsplit(url).path  # https://localhost/v1/jobs/claim → /v1/jobs/claim
        resp = self._client.post(path, json=body, headers=headers or {})
        if resp.status_code // 100 != 2:
            raise TransportError("jobs HTTP error", status_code=resp.status_code)
        return resp.json()


def _identity() -> AgentIdentity:
    # fake identity — token 은 헤더로만(평문 비노출). validate_agent_token 은 token 존재만으로 valid.
    return AgentIdentity(
        agent_id="agent-e2e",
        agent_token="fake-token-abc",
        tenant_scope="tenant-1",
        config_version="v1",
    )


def test_e2e_real_runner_claims_executes_completes_success():
    import asyncio

    backend = InMemoryQueueBackend()
    job_id = asyncio.run(backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=datetime.now(timezone.utc)))
    client = TestClient(_app_with_backend(backend, resolved_agent_id="agent-e2e"))
    transport = _TestClientTransport(client)

    runner = JobRunner(
        _identity(),
        transport=transport,
        execute_job=lambda job: make_success_result(result_json={"done": True}),
        sleep=lambda _s: None,
        now=time.time,
        capabilities=DEFAULT_CAPABILITIES,
        max_jobs=1,
    )
    # 실제 claim→execute→complete 한 바퀴(mock 없음).
    runner.run_once()

    assert backend.job_status(job_id) == JOB_STATUS_SUCCEEDED
    # claim 직후 JOB_STARTED 이벤트가 실제로 서버에 전달됨
    assert any(e["job_id"] == job_id and e["event_type"] == "JOB_STARTED" for e in backend.events)
    # best-effort 루프에 에러가 기록되지 않았다(claim/complete/events 전부 2xx)
    assert runner.last_error_event is None


def test_e2e_real_runner_default_executor_marks_failed():
    import asyncio

    backend = InMemoryQueueBackend()
    job_id = asyncio.run(backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=datetime.now(timezone.utc)))
    client = TestClient(_app_with_backend(backend, resolved_agent_id="agent-e2e"))
    transport = _TestClientTransport(client)

    # default_execute_job → UNSUPPORTED_JOB_TYPE 실패 결과 → 서버가 FAILED 로 기록
    runner = JobRunner(
        _identity(),
        transport=transport,
        sleep=lambda _s: None,
        now=time.time,
        capabilities=DEFAULT_CAPABILITIES,
        max_jobs=1,
    )
    runner.run_once()

    assert backend.job_status(job_id) == JOB_STATUS_FAILED
