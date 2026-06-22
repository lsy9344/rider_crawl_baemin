"""Story 5.10 / AC3 — 전역 dispatch kill switch가 **실제로 동작함**을 잠그는 always-run 테스트.

5.8이 ``Settings.sending_enabled``/``app.state.sending_enabled`` 플래그를 추가했지만 **어떤
실발송 경로도 이 게이트를 호출하지 않았다**(consumer 0). 5.10이 현존 유일 실발송 chokepoint인
operator ``test_send``(``deliver_once(send=...)`` 호출)에 ``recovery.effective_send_enabled`` 를
compose해 kill switch를 배선한다 — ``sending_enabled=False``(복구/신규 환경 기본 OFF)면 주입
``send`` 를 **0회 호출**하고 fail-closed(미발송) 결과 + DENIED audit를 남긴다.

배선은 두 레이어로 잠근다(fail-closed 우회 차단):
  (1) service ``AdminActionService.test_send(..., sending_enabled=...)`` — 실 ``send`` 호출부에서
      게이트(직접 호출자/미래 seam도 잠긴다). 기존 ``effective_send_enabled`` 재사용(재구현 0),
      ``deliver_once`` 본문·시그니처·reserve→send 순서 무변경.
  (2) route ``POST /admin/targets/{id}/test-send`` — seam 호출 **전** pre-gate(seam이 게이트를
      잊고 우회하지 못하게). 차단 시 seam 미호출 + DENIED audit.

**경계(Task 1.3):** enqueue-only 액션(``retry_job``·``test_crawl``·``auth_check``)과 구조적
미발송 ``dry_run_render`` 은 실 ``send`` 를 호출하지 않으므로 게이트 대상이 아니다 — 본 파일은
``retry_job`` 이 enqueue-only(실발송 0)임을 트레이서로 잠근다. 중앙 dispatch 루프(미존재)
도입 시 동일 게이트(``effective_send_enabled``) compose가 필수임을 service/route 주석으로 남긴다.

fake 값만(실제 토큰/전화/이메일/chat_id 형태 금지). 평면 ``tests/server/`` 컨벤션.
``pytest-asyncio`` 미도입 → ``asyncio.run`` 으로 async service 구동(5.7 선례).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http import HTTPStatus

from fastapi.testclient import TestClient as _TestClient

from rider_server.domain import DeliveryStatus, Messenger
from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.queue.states import JOB_STATUS_FAILED, JOB_STATUS_PENDING
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.admin_action_service import (
    AdminActionService,
    InMemoryAdminActionRepository,
    JobRef,
)
from rider_server.services.dispatch_fanout_service import DispatchJob
from rider_server.services.recovery import effective_send_enabled
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_SENT_AT = datetime(2026, 6, 14, 12, 0, 5, tzinfo=timezone.utc)
_TENANT = "tn-1"
_ACTOR = "11111111-1111-1111-1111-111111111111"
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_SAME_ORIGIN_HEADERS = {"Origin": "http://testserver"}
_OPERATOR = AdminPrincipal(
    actor_id=_ACTOR, role=AdminRole.OPERATOR, mfa_verified=True, source="ADMIN_UI/operator"
)
_CONFIRM = {"confirm_action": "confirmed"}


def TestClient(app, *args, **kwargs):  # noqa: N802 - test helper mirrors imported class name.
    headers = dict(_SAME_ORIGIN_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    return _TestClient(app, *args, headers=headers, **kwargs)


def _run(coro):
    return asyncio.run(coro)


def _confirmed(data: dict | None = None) -> dict:
    return {**(data or {}), **_CONFIRM}


def _service(repo, queue=None) -> AdminActionService:
    return AdminActionService(repo, queue or InMemoryQueueBackend())


def _dispatch_job(channel_id="ch-test") -> DispatchJob:
    return DispatchJob(
        id="dj-1",
        target_id="mt-1",
        channel_id=channel_id,
        message_id="msg-1",
        messenger=Messenger.TELEGRAM,
        template_version="v1",
        message_hash="abc123",
    )


def _send_collector():
    sends: list[str] = []
    reserved: set[str] = set()

    def reserve(key: str) -> bool:
        if key in reserved:
            return False
        reserved.add(key)
        return True

    def send(job: DispatchJob) -> None:
        sends.append(job.channel_id)

    return sends, reserve, send


# ══════════════════════════════════════════════════════════════════════════
# (1) service 게이트 — sending_enabled=False 면 실 send 0회 + 미발송 + DENIED audit
# ══════════════════════════════════════════════════════════════════════════

def test_test_send_blocked_when_sending_disabled_calls_send_zero_times() -> None:
    repo = InMemoryAdminActionRepository()
    svc = _service(repo)
    sends, reserve, send = _send_collector()

    result = _run(
        svc.test_send(
            _dispatch_job("ch-test"),
            collected_at=_NOW,
            reserve=reserve,
            send=send,
            log_id_for=lambda j: "log-1",
            sent_at=_SENT_AT,
            tenant_id=_TENANT,
            actor_id=_ACTOR,
            at=_NOW,
            sending_enabled=False,  # 전역 kill switch OFF(복구/신규 환경 기본).
        )
    )

    # 실 send 0회(주입 send 미호출) — fail-closed 미발송.
    assert sends == []
    assert result.status is DeliveryStatus.HELD  # 미발송(사람 개입=운영자 활성화 보류)
    assert result.sent_at is None
    # 차단 시도도 audit(보안/관측) — TEST_SEND 액션 result=DENIED.
    assert repo.audits[-1].action == "TEST_SEND"
    assert repo.audits[-1].result == "DENIED"


def test_test_send_sends_when_sending_enabled_true() -> None:
    repo = InMemoryAdminActionRepository()
    svc = _service(repo)
    sends, reserve, send = _send_collector()

    result = _run(
        svc.test_send(
            _dispatch_job("ch-test"),
            collected_at=_NOW,
            reserve=reserve,
            send=send,
            log_id_for=lambda j: "log-1",
            sent_at=_SENT_AT,
            tenant_id=_TENANT,
            actor_id=_ACTOR,
            at=_NOW,
            sending_enabled=True,  # 명시적 활성화 → 실전송.
        )
    )

    assert sends == ["ch-test"]  # 정확히 1회 전송(단일 채널).
    assert result.status is DeliveryStatus.SENT
    assert repo.audits[-1].result == "SUCCESS"


def test_test_send_default_sending_enabled_preserves_send() -> None:
    # 기본값(sending_enabled 미지정)은 기존 동작 보존(True) — 5.7 service 테스트 무회귀.
    repo = InMemoryAdminActionRepository()
    svc = _service(repo)
    sends, reserve, send = _send_collector()

    result = _run(
        svc.test_send(
            _dispatch_job("ch-test"),
            collected_at=_NOW,
            reserve=reserve,
            send=send,
            log_id_for=lambda j: "log-1",
            sent_at=_SENT_AT,
            tenant_id=_TENANT,
            actor_id=_ACTOR,
            at=_NOW,
        )
    )
    assert result.status is DeliveryStatus.SENT
    assert sends == ["ch-test"]


# ══════════════════════════════════════════════════════════════════════════
# (2) effective_send_enabled 재사용 — 실전송 = send_enabled AND sending_enabled
# ══════════════════════════════════════════════════════════════════════════

def test_kill_switch_uses_effective_send_enabled_and_semantics() -> None:
    # send_enabled(채널/대상 게이트)와 sending_enabled(전역) 둘 다 True 일 때만 전송.
    # (순수 AND 진리표 자체는 test_recovery_non_sending.py 가 잠금 — 여기선 재사용 확인.)
    assert effective_send_enabled(send_enabled=True, sending_enabled=True) is True
    assert effective_send_enabled(send_enabled=True, sending_enabled=False) is False
    assert effective_send_enabled(send_enabled=False, sending_enabled=True) is False


# ══════════════════════════════════════════════════════════════════════════
# (3) route pre-gate — sending_enabled=False(기본) 면 seam 호출 전 차단(미발송)
# ══════════════════════════════════════════════════════════════════════════

def _app_with(repo, *, sending_enabled: bool):
    svc = AdminActionService(repo, InMemoryQueueBackend())
    app = create_app(_FAKE_SETTINGS, admin_action_service=svc)
    app.state.resolve_admin_principal = lambda request: _OPERATOR
    app.state.sending_enabled = sending_enabled
    return app


def _wire_seam(app, sends: list[str]):
    reserved: set[str] = set()

    async def _seam(service, *, target_id, channel_id, tenant_id, actor_id, at, source=None,
                    sending_enabled=True):
        def reserve(key: str) -> bool:
            if key in reserved:
                return False
            reserved.add(key)
            return True

        return await service.test_send(
            _dispatch_job(channel_id),
            collected_at=_NOW,
            reserve=reserve,
            send=lambda j: sends.append(j.channel_id),
            log_id_for=lambda j: "log-1",
            sent_at=_SENT_AT,
            tenant_id=tenant_id,
            actor_id=actor_id,
            at=at,
            source=source,
            sending_enabled=sending_enabled,
        )

    app.state.admin_test_send = _seam


def test_route_test_send_blocked_when_sending_disabled_does_not_call_seam() -> None:
    repo = InMemoryAdminActionRepository()
    app = _app_with(repo, sending_enabled=False)  # 전역 kill switch OFF(기본).
    sends: list[str] = []
    _wire_seam(app, sends)
    client = TestClient(app)

    resp = client.post(
        "/admin/targets/mt-1/test-send?tenant=tn-1",
        data=_confirmed({"channel_id": "ch-test"}),
    )

    assert resp.status_code == HTTPStatus.OK  # HTMX fragment(차단 안내) — 미발송.
    assert "SENT" not in resp.text
    assert sends == []  # seam(실 send) 미호출.
    # 차단 시도 audit(result=DENIED, TEST_SEND).
    assert repo.audits[-1].action == "TEST_SEND"
    assert repo.audits[-1].result == "DENIED"


def test_route_test_send_sends_when_sending_enabled() -> None:
    repo = InMemoryAdminActionRepository()
    app = _app_with(repo, sending_enabled=True)  # 명시적 활성화.
    sends: list[str] = []
    _wire_seam(app, sends)
    client = TestClient(app)

    resp = client.post(
        "/admin/targets/mt-1/test-send?tenant=tn-1",
        data=_confirmed({"channel_id": "ch-test"}),
    )

    assert resp.status_code == HTTPStatus.OK
    assert "SENT" in resp.text
    assert sends == ["ch-test"]


# ══════════════════════════════════════════════════════════════════════════
# (4) 경계(Task 1.3) — retry_job 은 enqueue-only(실발송 0): 게이트 대상 아님
# ══════════════════════════════════════════════════════════════════════════

def test_retry_job_is_enqueue_only_no_real_send() -> None:
    """retry_job 은 FAILED/RETRY→PENDING 재진입만 한다 — ``deliver_once``/``send`` seam이 없다.

    (스토리 매트릭스가 retry 를 chokepoint 로 적었으나, 코드상 retry_job 은 실 send 를 호출하지
    않는 enqueue-only 액션이다 — test_crawl/auth_check 와 동일 범주. 실발송 게이트는 실제 send
    가 일어나는 test_send/중앙 dispatch 루프에 둔다.)
    """

    repo = InMemoryAdminActionRepository()
    repo.seed_job(
        JobRef(job_id="job-1", type="CRAWL_BAEMIN", target_id="mt-1",
               status=JOB_STATUS_FAILED, tenant_id=_TENANT)
    )
    svc = _service(repo)

    status = _run(svc.retry_job("job-1", tenant_id=_TENANT, actor_id=_ACTOR, reason="재시도", at=_NOW))

    assert status == JOB_STATUS_PENDING
    assert _run(repo.get_job("job-1")).status == JOB_STATUS_PENDING
    # 전송 결과(DeliveryLog)·send 부작용 없음 — 순수 enqueue 재진입.
    assert repo.audits[-1].action == "JOB_RETRY"
    assert repo.audits[-1].result == "SUCCESS"


# ══════════════════════════════════════════════════════════════════════════
# QA gap-fill (qa-generate-e2e-tests, Story 5.10) — kill switch 불변식 빈틈 보강
# ══════════════════════════════════════════════════════════════════════════
# dev 가 happy-path + 차단을 service/route 양쪽에 잠갔으나, kill switch 가 idempotency seam·
# fail-closed 기본값·secret 위생과 맞물리는 **불변식 3건** 이 미커버였다. 재구현이 아니라
# 게이트 동작의 경계 의미를 추가로 잠근다(test_scheduler_tick.py:412·test_admin_actions.py:525
# QA 보강 선례 동일 컨벤션).


def test_kill_switch_block_does_not_consume_reserve_key_so_later_send_succeeds() -> None:
    """게이트레일 #3: kill switch 는 ``deliver_once`` **전** 에 short-circuit 하므로 차단 시
    ``reserve`` seam(dedup key)을 **소비하지 않는다** — 같은 key 로 나중에 정상 발송이 가능해야 한다.

    (만약 reserve-후-차단으로 구현됐다면 dedup key 가 선점되어 재활성화 후에도 영구
    ``DUPLICATE_BLOCKED`` 가 된다 — 운영 사고. 이 테스트가 그 회귀를 잠근다.)
    """

    repo = InMemoryAdminActionRepository()
    svc = _service(repo)
    reserve_calls: list[str] = []
    reserved: set[str] = set()
    sends: list[str] = []

    def reserve(key: str) -> bool:
        reserve_calls.append(key)
        if key in reserved:
            return False
        reserved.add(key)
        return True

    def send(job: DispatchJob) -> None:
        sends.append(job.channel_id)

    common = dict(
        collected_at=_NOW,
        reserve=reserve,
        send=send,
        log_id_for=lambda j: "log-1",
        sent_at=_SENT_AT,
        tenant_id=_TENANT,
        actor_id=_ACTOR,
        at=_NOW,
    )

    # (1) 전역 발송 OFF → 차단: send 0회 + reserve seam 자체가 호출되지 않음(dedup key 미소비).
    blocked = _run(svc.test_send(_dispatch_job("ch-test"), sending_enabled=False, **common))
    assert blocked.status is DeliveryStatus.HELD
    assert sends == []
    assert reserve_calls == []  # 핵심: reserve(dedup key) 미소비 — deliver_once 진입 전 차단.

    # (2) 운영자가 전역 발송을 켜면 같은 key 로 실제 발송이 성공한다(차단이 key 를 오염시키지 않음).
    sent = _run(svc.test_send(_dispatch_job("ch-test"), sending_enabled=True, **common))
    assert sent.status is DeliveryStatus.SENT
    assert sends == ["ch-test"]
    assert len(reserve_calls) == 1  # 이제서야 deliver_once 가 reserve 를 1회 호출.


def test_route_test_send_fail_closed_when_sending_enabled_attr_unset() -> None:
    """게이트레일 #4: 라우트는 ``getattr(app.state, "sending_enabled", False)`` 로 읽는다 —
    app.state 에 플래그가 **설정조차 안 된** 상황(미래 refactor 가 wiring 을 빠뜨림)에서도
    기본값이 차단(False)이라 fail-closed 다. 기존 테스트는 항상 플래그를 명시 설정해 이
    defense-in-depth 기본 분기가 미커버였다.
    """

    repo = InMemoryAdminActionRepository()
    svc = AdminActionService(repo, InMemoryQueueBackend())
    app = create_app(_FAKE_SETTINGS, admin_action_service=svc)
    app.state.resolve_admin_principal = lambda request: _OPERATOR
    # 의도적으로 sending_enabled 를 제거 — getattr 기본값(False) 분기를 강제한다.
    if hasattr(app.state, "sending_enabled"):
        del app.state.sending_enabled
    sends: list[str] = []
    _wire_seam(app, sends)
    client = TestClient(app)

    resp = client.post(
        "/admin/targets/mt-1/test-send?tenant=tn-1",
        data=_confirmed({"channel_id": "ch-test"}),
    )

    assert resp.status_code == HTTPStatus.OK  # 차단 안내 fragment(미발송).
    assert "SENT" not in resp.text
    assert sends == []  # seam(실 send) 미호출 — 플래그 미설정이어도 fail-closed.
    denied = [a for a in repo.audits if a.result == "DENIED"]
    assert len(denied) == 1  # 정확히 1건의 DENIED audit(중복 audit 0).
    assert denied[0].action == "TEST_SEND"


def test_blocked_test_send_audit_diff_records_reason_without_leak() -> None:
    """게이트레일 #5(secret 위생) + 관측성: 차단된 test send 의 DENIED audit ``diff_redacted`` 는
    redaction 통과 dict 로, kill switch 차단 사유(``sending_enabled=False``)와 미발송 상태(``HELD``)를
    남기되 평문 secret 은 싣지 않는다(불투명 channel_id 키만).
    """

    repo = InMemoryAdminActionRepository()
    svc = _service(repo)
    sends, reserve, send = _send_collector()

    _run(
        svc.test_send(
            _dispatch_job("ch-test"),
            collected_at=_NOW,
            reserve=reserve,
            send=send,
            log_id_for=lambda j: "log-1",
            sent_at=_SENT_AT,
            tenant_id=_TENANT,
            actor_id=_ACTOR,
            at=_NOW,
            sending_enabled=False,
        )
    )

    audit = repo.audits[-1]
    assert audit.action == "TEST_SEND"
    assert audit.result == "DENIED"
    diff = audit.diff_redacted
    assert isinstance(diff, dict)  # redaction(build_diff_redacted) 통과 산출물.
    assert diff["sending_enabled"] is False  # 차단 사유가 기계가독으로 남는다.
    assert diff["status"] == DeliveryStatus.HELD.value  # 미발송 상태 관측.
    assert "channel_id" in diff  # 불투명 핸들만(평문 secret 아님).
