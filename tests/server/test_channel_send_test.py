"""채널 전송 테스트(0023) — 실발송 게이트 해제 조건 검증(service + 라우트).

(1) ChannelTestService(무 DB, fake seam): 텔레그램 동기 전송→PASSED+게이트 스탬프, 카카오
    enqueue→PENDING→에이전트 SUCCEEDED→PASSED, 실패 경로(빈 chat_id·cross-tenant·전송 예외),
    게이트가 send_test_passed_at 으로 OFF→ON 을 허용/차단.
(2) 라우트(TestClient + 주입 _OPERATOR): POST /admin/channel-test 가 in-memory 큐로 wiring 된
    기본 channel_test_service 를 통해 텔레그램은 PASSED, 카카오는 PENDING 을 fragment 로 돌려준다.
    권한(VIEWER→403), 채널 미선택→안내.

fake 값만(실 토큰/chat_id 형태 금지). 평면 ``tests/server/`` 컨벤션. pytest-asyncio 미도입 →
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http import HTTPStatus

from fastapi.testclient import TestClient as _TestClient

from rider_server.domain import (
    CustomerLifecycleState,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
    Tenant,
)
from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.queue.states import JOB_STATUS_SUCCEEDED, JOB_TYPE_KAKAO_SEND
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.admin_entity_service import (
    AdminEntityService,
    InMemoryAdminEntityRepository,
)
from rider_server.services.channel_test_service import (
    ChannelTestService,
    TEST_RESULT_FAILED,
    TEST_RESULT_PASSED,
    TEST_RESULT_PENDING,
)
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "tn-1"
_ACTOR = "11111111-1111-1111-1111-111111111111"
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_SAME_ORIGIN_HEADERS = {"Origin": "http://testserver"}
_OPERATOR = AdminPrincipal(
    actor_id=_ACTOR, role=AdminRole.OPERATOR, mfa_verified=True, source="ADMIN_UI/operator"
)
_VIEWER = AdminPrincipal(
    actor_id=_ACTOR, role=AdminRole.VIEWER, mfa_verified=True, source="ADMIN_UI/viewer"
)


def TestClient(app, **kwargs):  # noqa: N802 - mirrors imported class name + same-origin default.
    headers = dict(_SAME_ORIGIN_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    return _TestClient(app, headers=headers, **kwargs)


def _run(coro):
    return asyncio.run(coro)


def _tenant(tenant_id: str = _TENANT) -> Tenant:
    return Tenant(
        id=tenant_id, name="고객", status=CustomerLifecycleState.ACTIVE, created_at=_NOW
    )


def _tg_channel(channel_id="ch-tg", *, tenant=_TENANT, chat_id="-100777") -> MessengerChannel:
    return MessengerChannel(
        id=channel_id,
        tenant_id=tenant,
        messenger=Messenger.TELEGRAM,
        telegram_chat_id=chat_id,
        state=MessengerChannelState.ACTIVE,
    )


def _kakao_channel(channel_id="ch-ka", *, tenant=_TENANT, room="테스트방") -> MessengerChannel:
    return MessengerChannel(
        id=channel_id,
        tenant_id=tenant,
        messenger=Messenger.KAKAO,
        kakao_room_name=room,
        state=MessengerChannelState.ACTIVE,
    )


def _service_with(repo: InMemoryAdminEntityRepository):
    """ChannelTestService + 그 seam(텔레그램 fake 전송·카카오 enqueue·잡 상태) 묶음을 만든다."""

    svc = AdminEntityService(repo)
    queue = InMemoryQueueBackend()
    sent: list[tuple[str, str]] = []

    def telegram_test_send(channel: MessengerChannel, text: str) -> None:
        sent.append((channel.id, text))

    async def kakao_enqueue(*, kakao_room_name, message, tenant_id, channel_id):
        return await queue.enqueue(
            job_type=JOB_TYPE_KAKAO_SEND,
            payload_json={"kakao_room_name": kakao_room_name, "message": message},
            now=_NOW,
        )

    async def job_status(job_id: str):
        return await queue.get_job_status(job_id)

    cts = ChannelTestService(
        get_channel=svc.get_messenger_channel,
        get_tenant=svc.get_tenant,
        mark_send_test_passed=svc.update_tenant,
        telegram_test_send=telegram_test_send,
        kakao_enqueue=kakao_enqueue,
        job_status=job_status,
    )
    return svc, cts, queue, sent


# ══════════════════════════════════════════════════════════════════════════
# (service) 무 DB — 전송 테스트 결과/게이트 스탬프
# ══════════════════════════════════════════════════════════════════════════

def test_telegram_test_sends_and_stamps_gate() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_tg_channel())
    svc, cts, _queue, sent = _service_with(repo)

    out = _run(cts.run_test("ch-tg", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert out.result == TEST_RESULT_PASSED
    assert sent == [("ch-tg", _default_test_message())]
    assert _run(svc.get_tenant(_TENANT)).send_test_passed_at == _NOW


def test_telegram_test_pass_unlocks_sending_gate() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_tg_channel())
    svc, cts, _queue, _sent = _service_with(repo)

    _run(cts.run_test("ch-tg", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))
    updated = _run(
        svc.update_tenant(_TENANT, sending_enabled=True, at=_NOW, actor_id=_ACTOR)
    )

    assert updated.sending_enabled is True


def test_kakao_test_enqueues_pending_then_passes_on_agent_success() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_kakao_channel())
    svc, cts, queue, _sent = _service_with(repo)

    out = _run(cts.run_test("ch-ka", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))
    assert out.result == TEST_RESULT_PENDING and out.job_id

    # 에이전트 보고 전: 아직 PENDING, 게이트 미해제.
    pending = _run(
        cts.check_kakao_test(out.job_id, tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR)
    )
    assert pending.result == TEST_RESULT_PENDING
    assert _run(svc.get_tenant(_TENANT)).send_test_passed_at is None

    # 에이전트 claim+success 시뮬레이션 → 잡 SUCCEEDED.
    claimed = _run(
        queue.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_KAKAO_SEND],
            max_jobs=1,
            lease_seconds=60,
            now=_NOW,
        )
    )
    _run(
        queue.complete(
            job_id=claimed[0].job_id,
            agent_id="agent-1",
            status=JOB_STATUS_SUCCEEDED,
            now=_NOW,
        )
    )

    passed = _run(
        cts.check_kakao_test(out.job_id, tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR)
    )
    assert passed.result == TEST_RESULT_PASSED
    assert _run(svc.get_tenant(_TENANT)).send_test_passed_at == _NOW


def test_gate_blocks_on_to_off_until_test_passes() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_kakao_channel())
    svc, cts, _queue, _sent = _service_with(repo)

    # 테스트 통과 전: OFF→ON 차단.
    try:
        _run(svc.update_tenant(_TENANT, sending_enabled=True, at=_NOW, actor_id=_ACTOR))
        raised = False
    except ValueError:
        raised = True
    assert raised is True
    assert _run(svc.get_tenant(_TENANT)).sending_enabled is False


def test_telegram_empty_chat_id_fails_closed() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_tg_channel(chat_id=""))
    svc, cts, _queue, sent = _service_with(repo)

    out = _run(cts.run_test("ch-tg", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert out.result == TEST_RESULT_FAILED
    assert sent == []
    assert _run(svc.get_tenant(_TENANT)).send_test_passed_at is None


def test_cross_tenant_channel_fails_closed() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_tg_channel())
    _svc, cts, _queue, _sent = _service_with(repo)

    out = _run(cts.run_test("ch-tg", tenant_id="other", at=_NOW, actor_id=_ACTOR))

    assert out.result == TEST_RESULT_FAILED


def test_telegram_send_exception_maps_to_failed() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_tg_channel())
    svc = AdminEntityService(repo)

    def boom(channel, text):
        raise RuntimeError("transport down")

    cts = ChannelTestService(
        get_channel=svc.get_messenger_channel,
        get_tenant=svc.get_tenant,
        mark_send_test_passed=svc.update_tenant,
        telegram_test_send=boom,
        kakao_enqueue=None,
        job_status=None,
    )

    out = _run(cts.run_test("ch-tg", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert out.result == TEST_RESULT_FAILED
    assert _run(svc.get_tenant(_TENANT)).send_test_passed_at is None


def _default_test_message() -> str:
    from rider_server.services.channel_test_service import DEFAULT_TEST_MESSAGE

    return DEFAULT_TEST_MESSAGE


# ══════════════════════════════════════════════════════════════════════════
# (라우트) TestClient — POST /admin/channel-test (기본 wiring + in-memory 큐)
# ══════════════════════════════════════════════════════════════════════════

def _app_with(repo: InMemoryAdminEntityRepository, *, principal=_OPERATOR):
    # channel_test_service 는 create_app 이 기본 wiring(텔레그램 직접 전송 + in-memory 큐 enqueue)
    # 한다. 텔레그램 토큰 미설정이라 텔레그램 실 전송은 RuntimeError → 라우트는 FAILED fragment.
    app = create_app(_FAKE_SETTINGS, admin_entity_service=AdminEntityService(repo))
    app.state.resolve_admin_principal = lambda request: principal
    return app


def test_route_channel_test_kakao_returns_pending_fragment() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_kakao_channel())
    client = TestClient(_app_with(repo))

    resp = client.post(
        f"/admin/channel-test?tenant={_TENANT}", data={"channel_id": "ch-ka"}
    )

    assert resp.status_code == HTTPStatus.OK
    assert "warn" in resp.text  # PENDING → warn 상태 클래스
    assert "카카오" in resp.text


def test_route_channel_test_requires_channel_selection() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(f"/admin/channel-test?tenant={_TENANT}", data={})

    assert resp.status_code == HTTPStatus.OK
    assert "채널을 선택" in resp.text


def test_route_channel_test_viewer_forbidden() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_kakao_channel())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.post(
        f"/admin/channel-test?tenant={_TENANT}", data={"channel_id": "ch-ka"}
    )

    assert resp.status_code == HTTPStatus.FORBIDDEN
