"""Story 5.8 / AC3 — Agent/외부 service token server-side revoke·rotate(always-run + 라우트).

(1) 순수 helper: ``revocation_aware_resolver``(revoked→None)·``looks_like_plaintext_secret``.
(2) service(무 DB, in-memory fake): revoke→is_revoked·audit, rotate→audit, 외부 token ref 회전
    (평문 fail-closed 거부·*_ref 보존), audit action/result/target/source/reason redaction.
(3) 라우트(``TestClient``): SECRET_ADMIN revoke 200·OPERATOR 거부 403(DENIED audit)·
    revoke 후 동일 bearer claim 401(resolve_agent_id 가 revoke 반영).

fake 값만(실제 토큰/전화/이메일/chat_id 형태 0). 평면 ``tests/server/`` 컨벤션.
``asyncio.run`` 으로 async service 구동(5.4~5.7 선례).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient as _TestClient

from rider_crawl.redaction import REDACTED
from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.admin_action_service import (
    AdminActionService,
    InMemoryAdminActionRepository,
)
from rider_server.services.agent_token_repository_postgres import (
    PostgresAgentTokenRepository,
)
from rider_server.services.agent_token_service import (
    AgentTokenService,
    InMemoryAgentTokenRepository,
    looks_like_plaintext_secret,
    revocation_aware_resolver,
)
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_SAME_ORIGIN_HEADERS = {"Origin": "http://testserver"}
_ACTOR = "11111111-1111-1111-1111-111111111111"
_SECRET_ADMIN = AdminPrincipal(actor_id=_ACTOR, role=AdminRole.SECRET_ADMIN, mfa_verified=True,
                               source="ADMIN_UI/secret-admin")
_OPERATOR = AdminPrincipal(actor_id=_ACTOR, role=AdminRole.OPERATOR, mfa_verified=True, source="x")


def TestClient(app, *args, **kwargs):  # noqa: N802 - test helper mirrors imported class name.
    headers = dict(_SAME_ORIGIN_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    return _TestClient(app, *args, headers=headers, **kwargs)


def _run(coro):
    return asyncio.run(coro)


def _svc() -> tuple[AgentTokenService, InMemoryAgentTokenRepository]:
    repo = InMemoryAgentTokenRepository()
    return AgentTokenService(repo), repo


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 helper
# ══════════════════════════════════════════════════════════════════════════

def test_revocation_aware_resolver_blocks_revoked() -> None:
    revoked: set[str] = set()
    resolve = revocation_aware_resolver(
        lambda token: "agent-1" if token else None, lambda aid: aid in revoked
    )
    assert resolve("tok") == "agent-1"  # 미revoke → 통과
    assert resolve("") is None  # 빈 token → None(401)
    revoked.add("agent-1")
    assert resolve("tok") is None  # revoke 반영 → None(401)


def test_looks_like_plaintext_secret() -> None:
    # *_ref 핸들은 secret 아님(False), 평문 token shape 는 True(fail-closed 차단 대상).
    assert looks_like_plaintext_secret("vault://telegram/bot") is False
    assert looks_like_plaintext_secret("secretref-abc123") is False
    assert looks_like_plaintext_secret("111:AAFAKEtokenbody") is True


# ══════════════════════════════════════════════════════════════════════════
# (2) service — revoke/rotate/외부 token(always-run, 무 DB)
# ══════════════════════════════════════════════════════════════════════════

def test_revoke_marks_revoked_and_audits() -> None:
    svc, repo = _svc()
    _run(svc.revoke("agent-1", at=_NOW, actor_id=_ACTOR, source="ADMIN_UI/secret-admin", reason="유출 의심"))

    assert _run(repo.is_revoked("agent-1")) is True
    entry = repo.audits[-1]
    assert entry.action == "AGENT_TOKEN_REVOKE"
    assert entry.target_type == "agent"
    assert entry.target_id == "agent-1"
    assert entry.result == "SUCCESS"
    assert entry.source == "ADMIN_UI/secret-admin"
    assert entry.reason == "유출 의심"
    assert entry.created_at == _NOW


def test_rotate_marks_rotated_and_audits() -> None:
    svc, repo = _svc()
    _run(svc.rotate("agent-1", at=_NOW, actor_id=_ACTOR, source="s", reason="정기 회전"))

    assert repo.rotated_at("agent-1") == _NOW
    assert repo.audits[-1].action == "AGENT_TOKEN_ROTATE"
    assert repo.audits[-1].result == "SUCCESS"


def test_external_token_rotate_accepts_ref_and_audits() -> None:
    svc, repo = _svc()
    ref = _run(
        svc.rotate_external_token(
            channel_id="ch-1", new_secret_ref="vault://telegram/bot2",
            at=_NOW, actor_id=_ACTOR, source="s", reason="bot token 회전",
        )
    )
    assert ref == "vault://telegram/bot2"
    entry = repo.audits[-1]
    assert entry.action == "EXTERNAL_TOKEN_ROTATE"
    assert entry.target_type == "messenger_channel"
    assert entry.diff_redacted["new_secret_ref"] == "vault://telegram/bot2"  # *_ref 보존(secret 아님)


def test_external_token_rotate_rejects_plaintext_failclosed() -> None:
    svc, repo = _svc()
    before = len(repo.audits)
    with pytest.raises(ValueError):
        _run(
            svc.rotate_external_token(
                channel_id="ch-1", new_secret_ref="111:AAFAKEplaintexttoken",
                at=_NOW, actor_id=_ACTOR, source="s", reason="x",
            )
        )
    assert len(repo.audits) == before  # 거부 → audit 0(평문을 audit 에도 안 남긴다)


def test_revoke_audit_redacts_secret_in_reason() -> None:
    svc, repo = _svc()
    _run(svc.revoke("agent-1", at=_NOW, actor_id=_ACTOR, source="s", reason="bot_token 111:AAFAKEsecret 유출"))
    assert REDACTED in repo.audits[-1].reason
    assert "111:AAFAKEsecret" not in repo.audits[-1].reason


# ══════════════════════════════════════════════════════════════════════════
# (3) 라우트 — SECRET_ADMIN 게이트 + revoke 후 claim 401
# ══════════════════════════════════════════════════════════════════════════

def _app(principal: AdminPrincipal):
    token_repo = InMemoryAgentTokenRepository()
    token_svc = AgentTokenService(token_repo)
    admin_repo = InMemoryAdminActionRepository()
    app = create_app(
        _FAKE_SETTINGS,
        admin_action_service=AdminActionService(admin_repo, InMemoryQueueBackend()),
        agent_token_service=token_svc,
    )
    app.state.resolve_admin_principal = lambda request: principal
    return app, token_repo, admin_repo


def test_route_secret_admin_can_revoke() -> None:
    app, token_repo, _ = _app(_SECRET_ADMIN)
    resp = TestClient(app).post("/admin/agents/agent-1/token/revoke?tenant=tn-1", data={"reason": "유출"})
    assert resp.status_code == HTTPStatus.OK
    assert "agent-1" in token_repo.revoked_ids


def test_route_operator_cannot_revoke_token() -> None:
    # SECRET_ADMIN↑ 게이트 — OPERATOR(rank 1)는 거부(403) + DENIED audit.
    app, token_repo, admin_repo = _app(_OPERATOR)
    resp = TestClient(app).post("/admin/agents/agent-1/token/revoke?tenant=tn-1")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert "agent-1" not in token_repo.revoked_ids  # 무효화 0
    assert admin_repo.audits[-1].result == "DENIED"


def test_route_channel_token_rotate_rejects_plaintext() -> None:
    app, _, _ = _app(_SECRET_ADMIN)
    resp = TestClient(app).post(
        "/admin/channels/ch-1/token/rotate?tenant=tn-1", data={"new_secret_ref": "111:AAFAKEplain"}
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST  # 평문 fail-closed


def test_revoke_then_claim_is_401() -> None:
    app, token_repo, _ = _app(_SECRET_ADMIN)
    # resolve_agent_id 를 revoke 반영 resolver 로 주입(known token→agent-1).
    app.state.resolve_agent_id = revocation_aware_resolver(
        lambda token: "agent-1" if token else None, lambda aid: aid in token_repo.revoked_ids
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer tok-agent-1"}

    # revoke 전: claim 통과(빈 큐 → jobs []).
    before = client.post("/v1/jobs/claim", json={"agent_id": "agent-1"}, headers=headers)
    assert before.status_code == HTTPStatus.OK

    # revoke.
    assert client.post("/admin/agents/agent-1/token/revoke?tenant=tn-1").status_code == HTTPStatus.OK

    # revoke 후: 같은 bearer claim 401(resolver→None).
    after = client.post("/v1/jobs/claim", json={"agent_id": "agent-1"}, headers=headers)
    assert after.status_code == HTTPStatus.UNAUTHORIZED


# ══════════════════════════════════════════════════════════════════════════
# (4-QA) 보강 — rotate 라우트·채널 token rotate happy·PG is_revoked fail-closed
# ══════════════════════════════════════════════════════════════════════════
# (qa-generate-e2e 보강: agent token rotate 라우트·채널 token rotate happy path 는 라우트
#  커버리지 빈틈이었고(기존엔 revoke 200·operator 403·plaintext-reject 만), PG
#  is_revoked 의 non-UUID fail-closed 분기는 PG-gated 파일에 가려져 있었다 — memory
#  pg-gated-files-hide-pure-helpers.)


def test_route_secret_admin_can_rotate_agent_token() -> None:
    app, token_repo, _ = _app(_SECRET_ADMIN)
    resp = TestClient(app).post(
        "/admin/agents/agent-1/token/rotate?tenant=tn-1", data={"reason": "정기"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert token_repo.rotated_at("agent-1") is not None  # rotate 시각 마킹
    assert token_repo.audits[-1].action == "AGENT_TOKEN_ROTATE"
    assert token_repo.audits[-1].result == "SUCCESS"


def test_route_operator_cannot_rotate_agent_token() -> None:
    # rotate 도 SECRET_ADMIN↑ 게이트 — OPERATOR 거부(403) + DENIED audit, rotate 미반영.
    app, token_repo, admin_repo = _app(_OPERATOR)
    resp = TestClient(app).post("/admin/agents/agent-1/token/rotate?tenant=tn-1")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert token_repo.rotated_at("agent-1") is None  # 무효화 0
    assert admin_repo.audits[-1].result == "DENIED"


def test_route_channel_token_rotate_accepts_ref() -> None:
    app, token_repo, _ = _app(_SECRET_ADMIN)
    resp = TestClient(app).post(
        "/admin/channels/ch-1/token/rotate?tenant=tn-1",
        data={"new_secret_ref": "vault://telegram/bot2", "reason": "회전"},
    )
    assert resp.status_code == HTTPStatus.OK
    entry = token_repo.audits[-1]
    assert entry.action == "EXTERNAL_TOKEN_ROTATE"
    assert entry.diff_redacted["new_secret_ref"] == "vault://telegram/bot2"  # *_ref 보존(secret 아님)


def test_pg_is_revoked_failclosed_on_non_uuid_agent_id() -> None:
    # PostgresAgentTokenRepository.is_revoked 의 non-UUID 분기는 DB 접근 전 fail-closed(True)
    # 반환(식별 불가 agent → revoked 취급). 세션 미사용이라 무-DB always-run 으로 잠근다.
    repo = PostgresAgentTokenRepository(None)  # type: ignore[arg-type]
    assert _run(repo.is_revoked("not-a-uuid")) is True
