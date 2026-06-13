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

import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from rider_agent.heartbeat import DEFAULT_CAPABILITIES
from rider_agent.job_loop import JobRunner, make_success_result
from rider_agent.registration import TransportError
from rider_agent.secure_store import AgentIdentity
from rider_crawl.redaction import REDACTED
from rider_server.main import create_app
from rider_server.queue import (
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    InMemoryQueueBackend,
)
from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_BEARER = {"Authorization": "Bearer fake-token-abc"}


def _app_with_backend(backend: InMemoryQueueBackend):
    return create_app(queue_backend=backend)


# ══════════════════════════════════════════════════════════════════════════
# (1) HTTP 라우트 계약
# ══════════════════════════════════════════════════════════════════════════


def test_claim_requires_bearer_returns_401_envelope():
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    # bearer 없음 → 401 + 전역 에러 envelope
    r = client.post("/v1/jobs/claim", json={"agent_id": "a", "capabilities": [], "max_jobs": 1})
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_claim_empty_queue_returns_empty_jobs():
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        "/v1/jobs/claim",
        json={"agent_id": "a", "capabilities": [JOB_TYPE_CRAWL_BAEMIN], "max_jobs": 1},
        headers=_BEARER,
    )
    assert r.status_code == 200
    assert r.json() == {"jobs": []}


def test_claim_returns_job_with_iso_utc_lease():
    import asyncio

    backend = InMemoryQueueBackend()
    job_id = asyncio.run(backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_NOW))
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        "/v1/jobs/claim",
        json={"agent_id": "a", "capabilities": [JOB_TYPE_CRAWL_BAEMIN], "max_jobs": 5},
        headers=_BEARER,
    )
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id
    assert jobs[0]["type"] == JOB_TYPE_CRAWL_BAEMIN
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
    client = TestClient(_app_with_backend(backend))
    # 다른 agent_id 가 complete → 소유 불일치 → 409(Agent 가 lease_lost 흡수)
    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={"status": "success", "agent_id": "agent-2", "result_json": {}},
        headers=_BEARER,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_complete_unknown_job_returns_404():
    backend = InMemoryQueueBackend()
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        "/v1/jobs/does-not-exist/complete",
        json={"status": "success", "agent_id": "a"},
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
            agent_id="a",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_NOW,
        )
    )
    client = TestClient(_app_with_backend(backend))
    r = client.post(
        f"/v1/jobs/{job_id}/complete",
        json={"status": "weird-status", "agent_id": "a"},
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
    client = TestClient(_app_with_backend(backend))
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
    client = TestClient(_app_with_backend(backend))
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
