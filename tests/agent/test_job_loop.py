"""Story 4.4 — outbound job 폴링/claim/complete 루프 + lease 인지 + startup 배선 검증.

외부 호출 없음: transport 는 fake(URL 라우팅 + canned/에러), 루프는 **주입 fake sleep + stop
event + 호출 카운터 + 주입 now/executor** 로 실 네트워크·실 thread 장기 대기·실 시계 없이
결정적 검증한다. 값은 명백한 가짜값만(``agtok-fake-…``/``agent-fake-…``/``job-fake-…``). 실제
봇 토큰/chat_id/전화/이메일/OTP 원문 없음(누출 가드).
"""

from __future__ import annotations

import json
import threading

import pytest

from rider_agent.heartbeat import HeartbeatReporter, build_heartbeat_payload
from rider_agent.job_loop import (
    CLAIM_PATH,
    DEFAULT_SHORT_POLL_INTERVAL_SECONDS,
    EVENT_TYPE_JOB_STARTED,
    ERROR_UNSUPPORTED_JOB_TYPE,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCESS,
    AgentRunSummary,
    ClaimedJob,
    JobResult,
    build_agent_components,
    claim_jobs,
    complete_job,
    default_execute_job,
    emit_job_event,
    make_failure_result,
    make_job_event,
    make_success_result,
    run_agent,
    start_heartbeat_thread,
    start_kakao_inbound_thread,
    _run_kakao_inbound_loop,
)
from rider_agent.registration import (
    DEFAULT_SERVER_BASE_URL,
    SERVER_URL_ENV,
    TransportError,
)
from rider_agent.secure_store import (
    TOKEN_STATUS_MISSING,
    TOKEN_STATUS_REVOKED,
    TOKEN_STATUS_VALID,
    AgentIdentity,
    save_agent_identity,
)
from rider_crawl.redaction import REDACTED

# 매 주기 claim/complete/events 헤더에 실리는 반복 노출 표면이라 누출 단언의 핵심 대상.
FAKE_TOKEN = "agtok-fake-job-loop-secret"

_IDENTITY = AgentIdentity(
    agent_id="agent-fake-1",
    agent_token=FAKE_TOKEN,
    tenant_scope={"tenant": "t-fake"},
    config_version="cfg-fake-1",
)

# 먼 미래 lease(서버 부여값).
FUTURE_LEASE = 5_000_000_000.0

_JOB_DICT = {
    "job_id": "job-fake-1",
    "type": "CRAWL_BAEMIN",
    "target_id": "target-fake-1",
    "lease_expires_at": FUTURE_LEASE,
    "payload": {"some": "data"},
}


def _job(job_id="job-fake-1", *, lease_expires_at=FUTURE_LEASE, type="CRAWL_BAEMIN"):
    return ClaimedJob(
        job_id=job_id, type=type, target_id="target-fake-1", lease_expires_at=lease_expires_at
    )


def _identity() -> AgentIdentity:
    return _IDENTITY


class FakeTransport:
    """주입 fake transport: URL(claim/events/complete) 라우팅 + (url, body, headers) 캡처.

    ``claim_script`` 는 claim 호출에 순서대로 적용하는 응답/예외 리스트다(소진 후 빈 jobs).
    ``claim_error`` 가 주어지면 매 claim 호출마다 그 예외를 던진다(영속 401/5xx 재현).
    """

    def __init__(
        self,
        *,
        claim_script=None,
        claim_error=None,
        complete_error=None,
        events_error=None,
    ) -> None:
        self.claim_script = list(claim_script) if claim_script is not None else []
        self._claim_idx = 0
        self.claim_error = claim_error
        self.complete_error = complete_error
        self.events_error = events_error
        self.calls: list[tuple[str, dict, dict | None]] = []

    def post_json(self, url, body, *, headers=None) -> dict:
        self.calls.append((url, body, headers))
        if url.endswith(CLAIM_PATH):
            return self._next_claim()
        if url.endswith("/events"):
            if self.events_error is not None:
                raise self.events_error
            return {}
        if url.endswith("/complete"):
            if self.complete_error is not None:
                raise self.complete_error
            return {}
        return {}  # heartbeat 등 기타 — 안전 빈 응답.

    def _next_claim(self) -> dict:
        if self.claim_error is not None:
            raise self.claim_error
        if self._claim_idx < len(self.claim_script):
            item = self.claim_script[self._claim_idx]
            self._claim_idx += 1
        else:
            item = {"jobs": []}
        if isinstance(item, BaseException):
            raise item
        return item

    def calls_for(self, suffix: str) -> list[tuple[str, dict, dict | None]]:
        return [c for c in self.calls if c[0].endswith(suffix)]


class StoppingSleep:
    """주입 fake sleep: 총 N 회 호출 후 stop_event 를 set(결정적, 실 대기 0). thread-safe."""

    def __init__(self, stop_event: threading.Event, *, stop_after: int) -> None:
        self._stop_event = stop_event
        self._stop_after = stop_after
        self._lock = threading.Lock()
        self.intervals: list[float] = []

    def __call__(self, seconds: float) -> None:
        with self._lock:
            self.intervals.append(seconds)
            if len(self.intervals) >= self._stop_after:
                self._stop_event.set()


class SequenceClock:
    """주입 fake now: 값 리스트를 순서대로 돌려준다(소진 후 마지막 값 유지)."""

    def __init__(self, values) -> None:
        self._values = list(values)
        self._i = 0

    def __call__(self) -> float:
        idx = min(self._i, len(self._values) - 1)
        self._i += 1
        return self._values[idx]


class FakeStore:
    """최소 SecretStore — token 1개 보관/조회(비-Windows 에서도 동작, codec 불요)."""

    def __init__(self, *, token: str | None = None) -> None:
        self._token = token
        self._data: dict[str, str] = {}

    def put(self, value, *, ref="") -> str:
        self._data[ref] = value
        return ref

    def resolve(self, ref) -> str | None:
        if ref in self._data:
            return self._data[ref]
        return self._token


def test_compose_execute_job_keeps_fallback_for_unknown_job_type():
    from rider_agent.worker_composition import compose_execute_job

    calls = []

    def fallback(job):
        calls.append(job.type)
        return make_failure_result("UNKNOWN", "unknown")

    composition = compose_execute_job(
        identity=_identity(),
        capabilities=[],
        fallback=fallback,
        log=None,
        now=lambda: 1.0,
        sleep=lambda seconds: None,
    )

    result = composition.execute_job(ClaimedJob(job_id="job-1", type="NEW_TYPE"))

    assert result.status == JOB_STATUS_FAILED
    assert calls == ["NEW_TYPE"]
    assert composition.close_callbacks == ()


def test_compose_auth_worker_uses_crawl_profile_assignment_for_auth_jobs(tmp_path):
    from types import SimpleNamespace

    from rider_agent.heartbeat import CAPABILITY_CRAWL_COUPANG, CAPABILITY_OPEN_AUTH_BROWSER
    from rider_agent.worker_composition import compose_execute_job

    class Profiles:
        def __init__(self) -> None:
            self.calls = []
            self.configs = []

        def ensure_profile(self, tenant_id, target_id, *, build_config):
            self.calls.append((tenant_id, target_id))
            profile_dir = tmp_path / "profiles" / tenant_id / target_id
            config = build_config(
                tenant_id=tenant_id,
                target_id=target_id,
                cdp_url="http://127.0.0.1:9450",
                user_data_dir=profile_dir,
            )
            self.configs.append(config)
            return SimpleNamespace(
                cdp_url=config.cdp_url,
                profile_dir=config.browser_user_data_dir,
            )

        def browser_profiles(self):
            return []

    profiles = Profiles()
    opened_payloads = []

    def open_auth_browser(job):
        opened_payloads.append(dict(job.payload))
        return True

    composition = compose_execute_job(
        identity=_identity(),
        capabilities=[CAPABILITY_OPEN_AUTH_BROWSER, CAPABILITY_CRAWL_COUPANG],
        fallback=default_execute_job,
        log=None,
        now=lambda: 0.0,
        sleep=lambda _seconds: None,
        start_auth_worker=True,
        auth_open_auth_browser=open_auth_browser,
        start_crawl_worker=True,
        crawl_profile_manager=profiles,
        crawl_snapshot=lambda config, *, platform_name=None: None,
    )

    result = composition.execute_job(
        ClaimedJob(
            job_id="job-auth-1",
            type=CAPABILITY_OPEN_AUTH_BROWSER,
            target_id="target-1",
            lease_expires_at=FUTURE_LEASE,
            payload={
                "target_id": "target-1",
                "tenant_id": "tenant-1",
                "platform": "coupang",
                "platform_account_id": "account-1",
                "primary_url": "https://partner.coupangeats.com/page/peak-dashboard",
                "expected_display_name": "쿠팡상점A",
                "browser_profile_ref": "profile:target-1",
            },
        )
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert profiles.calls == [("tenant-1", "target-1")]
    assert opened_payloads[0]["cdp_url"] == "http://127.0.0.1:9450"
    assert opened_payloads[0]["browser_user_data_dir"] == str(
        tmp_path / "profiles" / "tenant-1" / "target-1"
    )


def test_compose_routes_auth_coupang_2fa_to_dedicated_worker(tmp_path):
    # crawl-coupang-auth-separation Task 3: AUTH_COUPANG_2FA capability 가 있으면 전용 worker 로
    # 라우팅되고(자동 email 2FA), AUTH_CHECK/OPEN_AUTH_BROWSER 는 baemin auth 라우터로 흐른다.
    from types import SimpleNamespace

    from rider_agent.heartbeat import (
        CAPABILITY_AUTH_COUPANG_2FA,
        CAPABILITY_CRAWL_COUPANG,
        CAPABILITY_OPEN_AUTH_BROWSER,
    )
    from rider_agent.worker_composition import compose_execute_job

    class Profiles:
        def ensure_profile(self, tenant_id, target_id, *, build_config):
            profile_dir = tmp_path / "profiles" / tenant_id / target_id
            config = build_config(
                tenant_id=tenant_id,
                target_id=target_id,
                cdp_url="http://127.0.0.1:9450",
                user_data_dir=profile_dir,
            )
            return SimpleNamespace(cdp_url=config.cdp_url, profile_dir=config.browser_user_data_dir)

        def browser_profiles(self):
            return []

    recover_calls = []

    composition = compose_execute_job(
        identity=_identity(),
        capabilities=[
            CAPABILITY_OPEN_AUTH_BROWSER,
            CAPABILITY_AUTH_COUPANG_2FA,
            CAPABILITY_CRAWL_COUPANG,
        ],
        fallback=default_execute_job,
        log=None,
        now=lambda: 0.0,
        sleep=lambda _seconds: None,
        start_auth_worker=True,
        start_crawl_worker=True,
        crawl_profile_manager=Profiles(),
        crawl_snapshot=lambda config, *, platform_name=None: None,
        secret_resolver={
            "mailbox-ref": "operator@example.com",
            "mail-app-password-ref": "fake app password",
        }.get,
    )

    # AUTH_COUPANG_2FA 는 전용 worker 로 — recover 가 실제로 호출되는지 monkeypatch 로 가로채기는
    # 어렵지만, result_json 형태가 전용 worker 의 것(auth_recovery_state 포함)임을 확인한다.
    import rider_agent.auth.coupang_gmail_2fa as cg2fa

    original = cg2fa.execute_auth_coupang_2fa_job

    def spy(job, **kwargs):
        recover_calls.append(job.type)
        kwargs["recover"] = lambda: True
        return original(job, **kwargs)

    cg2fa.execute_auth_coupang_2fa_job = spy
    try:
        result = composition.execute_job(
            ClaimedJob(
                job_id="job-auth-2fa-1",
                type=CAPABILITY_AUTH_COUPANG_2FA,
                target_id="target-1",
                lease_expires_at=FUTURE_LEASE,
                payload={
                    "target_id": "target-1",
                    "tenant_id": "tenant-1",
                    "platform": "coupang",
                    "platform_account_id": "account-1",
                    "primary_url": "https://partner.coupangeats.com/page/peak-dashboard",
                    "expected_display_name": "쿠팡상점A",
                    "browser_profile_ref": "profile:target-1",
                    "verification_email_address_ref": "mailbox-ref",
                    "verification_email_app_password_ref": "mail-app-password-ref",
                },
            )
        )
    finally:
        cg2fa.execute_auth_coupang_2fa_job = original

    assert recover_calls == [CAPABILITY_AUTH_COUPANG_2FA]
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_recovery_state"] == "ACTIVE"
    assert result.result_json["auth_state"] == "ACTIVE"


def _rider_lookup_composition(tmp_path, monkeypatch, *, fetched):
    from types import SimpleNamespace

    from rider_agent.heartbeat import CAPABILITY_CRAWL_BAEMIN, CAPABILITY_RIDER_LOOKUP
    from rider_agent.worker_composition import compose_execute_job
    import rider_agent.reuse as reuse

    def fake_fetch(config):
        fetched.append(config)
        return [
            {
                "이름": "강민기",
                "휴대폰번호": "010-9999-1234",
                "완료": "48",
                "거절": "0",
                "배차취소": "1",
                "배달취소(라이더귀책)": "1",
            }
        ]

    # Block the real browser fetch; the production fetcher imports this lazily at
    # compose time, so patching the reuse symbol substitutes the fake.
    monkeypatch.setattr(reuse, "fetch_baemin_delivery_history_rows", fake_fetch)

    class Profiles:
        def __init__(self):
            self.calls = []

        def ensure_profile(self, tenant_id, target_id, *, build_config):
            self.calls.append((tenant_id, target_id))
            profile_dir = tmp_path / "profiles" / tenant_id / target_id
            config = build_config(
                tenant_id=tenant_id,
                target_id=target_id,
                cdp_url="http://127.0.0.1:9450",
                user_data_dir=profile_dir,
            )
            return SimpleNamespace(cdp_url=config.cdp_url, profile_dir=config.browser_user_data_dir)

        def browser_profiles(self):
            return []

    profiles = Profiles()
    composition = compose_execute_job(
        identity=_identity(),
        capabilities=[CAPABILITY_RIDER_LOOKUP, CAPABILITY_CRAWL_BAEMIN],
        fallback=default_execute_job,
        log=None,
        now=lambda: 0.0,
        sleep=lambda _seconds: None,
        start_crawl_worker=True,
        crawl_profile_manager=profiles,
        crawl_snapshot=lambda config, *, platform_name=None: None,
    )
    return composition, profiles


def _rider_lookup_payload(**overrides):
    payload = {
        "target_id": "tg1",
        "tenant_id": "t1",
        "platform": "baemin",
        "platform_account_id": "acc1",
        "primary_url": "https://deliverycenter.baemin.com/delivery/history",
        "expected_display_name": "남구센터",
        "reply_channel_id": "ch1",
        "reply_kakao_room_name": "운영방",
        "origin_event_key": "sha256:abc",
        "command": {"type": "RIDER_CANCEL_RATE_LOOKUP", "name": "강민기", "phone_last4": "1234"},
        "timeout_seconds": 60,
    }
    payload.update(overrides)
    return payload


def test_compose_routes_rider_lookup_to_worker(tmp_path, monkeypatch):
    from rider_agent.heartbeat import CAPABILITY_RIDER_LOOKUP

    fetched = []
    composition, profiles = _rider_lookup_composition(tmp_path, monkeypatch, fetched=fetched)

    result = composition.execute_job(
        ClaimedJob(
            job_id="rl-1",
            type=CAPABILITY_RIDER_LOOKUP,
            target_id="tg1",
            lease_expires_at=FUTURE_LEASE,
            payload=_rider_lookup_payload(),
        )
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["result_type"] == "rider_lookup"
    assert result.result_json["reply_text"].startswith("강민기1234")
    assert result.result_json["reply_channel_id"] == "ch1"
    assert profiles.calls == [("t1", "tg1")]  # same profile identity as crawl
    assert len(fetched) == 1


def test_rider_lookup_routing_preserves_crawl_baemin(tmp_path, monkeypatch):
    from rider_agent.heartbeat import CAPABILITY_CRAWL_BAEMIN

    fetched = []
    composition, _ = _rider_lookup_composition(tmp_path, monkeypatch, fetched=fetched)

    # Adding RIDER_LOOKUP must not change CRAWL_BAEMIN routing: a crawl job still
    # goes to the crawl worker (snapshot result), never the lookup worker.
    result = composition.execute_job(
        ClaimedJob(
            job_id="cb-1",
            type=CAPABILITY_CRAWL_BAEMIN,
            target_id="tg1",
            lease_expires_at=FUTURE_LEASE,
            payload=_rider_lookup_payload(),
        )
    )
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["result_type"] == "snapshot"
    assert fetched == []  # lookup fetch never runs for a crawl job


class _DiagnosticProfiles:
    """compose-level fake — auth wrapper 가 record_profile_diagnostic 을 호출하는지 캡처."""

    def __init__(self, tmp_path) -> None:
        self._tmp_path = tmp_path
        self.diagnostics: list[dict] = []

    def ensure_profile(self, tenant_id, target_id, *, build_config):
        from types import SimpleNamespace

        profile_dir = self._tmp_path / "profiles" / tenant_id / target_id
        config = build_config(
            tenant_id=tenant_id,
            target_id=target_id,
            cdp_url="http://127.0.0.1:9450",
            user_data_dir=profile_dir,
        )
        return SimpleNamespace(cdp_url=config.cdp_url, profile_dir=config.browser_user_data_dir)

    def record_profile_diagnostic(
        self, tenant_id, target_id, *, auth_state=None, last_error_code=None, last_probe_at=None
    ):
        self.diagnostics.append(
            {
                "tenant_id": tenant_id,
                "target_id": target_id,
                "auth_state": auth_state,
                "last_error_code": last_error_code,
                "last_probe_at": last_probe_at,
            }
        )

    def browser_profiles(self):
        return []


def _coupang_auth_payload() -> dict:
    return {
        "target_id": "target-1",
        "tenant_id": "tenant-1",
        "platform": "coupang",
        "platform_account_id": "account-1",
        "primary_url": "https://partner.coupangeats.com/page/peak-dashboard",
        "expected_display_name": "쿠팡상점A",
        "browser_profile_ref": "profile:target-1",
    }


def test_compose_auth_open_browser_records_profile_diagnostic(tmp_path):
    # compose 레벨에서 OPEN_AUTH_BROWSER 성공 결과가 profile 진단으로 기록되는지 확인한다
    # (개별 auth 함수 단위 테스트만으로는 wrapper 배선 누락을 못 잡는다).
    from rider_agent.heartbeat import CAPABILITY_CRAWL_COUPANG, CAPABILITY_OPEN_AUTH_BROWSER
    from rider_agent.worker_composition import compose_execute_job

    profiles = _DiagnosticProfiles(tmp_path)
    composition = compose_execute_job(
        identity=_identity(),
        capabilities=[CAPABILITY_OPEN_AUTH_BROWSER, CAPABILITY_CRAWL_COUPANG],
        fallback=default_execute_job,
        log=None,
        now=lambda: 0.0,
        sleep=lambda _seconds: None,
        start_auth_worker=True,
        auth_open_auth_browser=lambda job: True,
        auth_detect_completion=lambda job: True,
        start_crawl_worker=True,
        crawl_profile_manager=profiles,
        crawl_snapshot=lambda config, *, platform_name=None: None,
    )

    result = composition.execute_job(
        ClaimedJob(
            job_id="job-auth-open-1",
            type=CAPABILITY_OPEN_AUTH_BROWSER,
            target_id="target-1",
            lease_expires_at=FUTURE_LEASE,
            payload=_coupang_auth_payload(),
        )
    )

    assert result.status == JOB_STATUS_SUCCESS
    assert len(profiles.diagnostics) == 1
    record = profiles.diagnostics[0]
    assert record["tenant_id"] == "tenant-1"
    assert record["target_id"] == "target-1"
    # OPEN_AUTH_BROWSER 성공은 result_json.auth_state 를 남긴다(인증 검증됨).
    assert record["auth_state"] == result.result_json["auth_state"]
    # epoch 0.0 → ISO-8601 UTC 문자열로 기록.
    assert record["last_probe_at"] == "1970-01-01T00:00:00Z"


def test_compose_auth_coupang_2fa_records_profile_diagnostic(tmp_path):
    # AUTH_COUPANG_2FA 도 compose 레벨에서 result_json.auth_state 정규화 뒤 기록되는지 확인한다.
    from rider_agent.heartbeat import (
        CAPABILITY_AUTH_COUPANG_2FA,
        CAPABILITY_CRAWL_COUPANG,
        CAPABILITY_OPEN_AUTH_BROWSER,
    )
    from rider_agent.worker_composition import compose_execute_job

    profiles = _DiagnosticProfiles(tmp_path)
    composition = compose_execute_job(
        identity=_identity(),
        capabilities=[
            CAPABILITY_OPEN_AUTH_BROWSER,
            CAPABILITY_AUTH_COUPANG_2FA,
            CAPABILITY_CRAWL_COUPANG,
        ],
        fallback=default_execute_job,
        log=None,
        now=lambda: 0.0,
        sleep=lambda _seconds: None,
        start_auth_worker=True,
        start_crawl_worker=True,
        crawl_profile_manager=profiles,
        crawl_snapshot=lambda config, *, platform_name=None: None,
        secret_resolver={
            "mailbox-ref": "operator@example.com",
            "mail-app-password-ref": "fake app password",
        }.get,
    )

    import rider_agent.auth.coupang_gmail_2fa as cg2fa

    original = cg2fa.execute_auth_coupang_2fa_job

    def spy(job, **kwargs):
        kwargs["recover"] = lambda: True
        return original(job, **kwargs)

    cg2fa.execute_auth_coupang_2fa_job = spy
    try:
        result = composition.execute_job(
            ClaimedJob(
                job_id="job-auth-2fa-diag-1",
                type=CAPABILITY_AUTH_COUPANG_2FA,
                target_id="target-1",
                lease_expires_at=FUTURE_LEASE,
                payload={
                    **_coupang_auth_payload(),
                    "verification_email_address_ref": "mailbox-ref",
                    "verification_email_app_password_ref": "mail-app-password-ref",
                },
            )
        )
    finally:
        cg2fa.execute_auth_coupang_2fa_job = original

    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["auth_state"] == "ACTIVE"
    assert len(profiles.diagnostics) == 1
    record = profiles.diagnostics[0]
    assert record["target_id"] == "target-1"
    assert record["auth_state"] == "ACTIVE"


def _runner(transport, **kwargs):
    """JobRunner 생성 헬퍼 — stop/sleep 기본 배선 + 주입 override."""

    from rider_agent.job_loop import JobRunner

    stop = kwargs.pop("stop_event", None) or threading.Event()
    sleep = kwargs.pop("sleep", None) or StoppingSleep(stop, stop_after=2)
    kwargs.setdefault("now", lambda: 0.0)
    return JobRunner(_IDENTITY, transport=transport, stop_event=stop, sleep=sleep, **kwargs)


# ══════════════════════════════════════════════════════════════════════════
# AC1 — claim/complete/events client (URL·본문·파싱)
# ══════════════════════════════════════════════════════════════════════════


def test_claim_jobs_posts_to_claim_path_with_body_and_parses_jobs():
    transport = FakeTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])

    jobs = claim_jobs(_IDENTITY, transport=transport, base_url="https://srv.test", max_jobs=3)

    url, body, _headers = transport.calls[0]
    assert url == "https://srv.test" + CLAIM_PATH
    assert body == {
        "agent_id": "agent-fake-1",
        "capabilities": list(body["capabilities"]),
        "max_jobs": 3,
    }
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_id == "job-fake-1"
    assert job.type == "CRAWL_BAEMIN"
    assert job.target_id == "target-fake-1"
    assert job.lease_expires_at == FUTURE_LEASE
    assert job.payload["payload"] == {"some": "data"}


@pytest.mark.parametrize(
    "response",
    [
        {},  # jobs 누락
        {"jobs": "not-a-list"},  # 비-list
        {"jobs": [None, 42, {"no_job_id": 1}]},  # 비-dict/누락 항목
        "not-a-dict",  # 비-dict 응답
    ],
)
def test_claim_jobs_fail_closed_on_malformed_response(response):
    transport = FakeTransport(claim_script=[response])

    assert claim_jobs(_IDENTITY, transport=transport) == []


def test_complete_job_posts_to_complete_path_with_result_fields():
    transport = FakeTransport()
    result = JobResult(
        status=JOB_STATUS_SUCCESS,
        result_json={"ok": True},
        error_code=None,
        error_message_redacted=None,
        metrics={"n": 1},
        agent_id="agent-fake-1",
        started_at=100.0,
        finished_at=200.0,
    )

    complete_job(_IDENTITY, "job-fake-1", result, transport=transport, base_url="https://srv.test")

    url, body, _headers = transport.calls[0]
    assert url == "https://srv.test/v1/jobs/job-fake-1/complete"
    # AC1 핵심 필드 + AC3 필드 모두 포함.
    assert set(body) >= {
        "status",
        "result_json",
        "error_code",
        "error_message_redacted",
        "metrics",
        "agent_id",
        "started_at",
        "finished_at",
    }
    assert body["status"] == JOB_STATUS_SUCCESS
    assert body["result_json"] == {"ok": True}
    assert body["metrics"] == {"n": 1}


def test_claim_url_falls_back_to_env_then_default(monkeypatch):
    monkeypatch.setenv(SERVER_URL_ENV, "https://env.test")
    transport = FakeTransport(claim_script=[{"jobs": []}])
    claim_jobs(_IDENTITY, transport=transport)
    assert transport.calls[0][0] == "https://env.test" + CLAIM_PATH

    monkeypatch.delenv(SERVER_URL_ENV, raising=False)
    transport2 = FakeTransport(claim_script=[{"jobs": []}])
    claim_jobs(_IDENTITY, transport=transport2)
    assert transport2.calls[0][0] == DEFAULT_SERVER_BASE_URL + CLAIM_PATH


# ══════════════════════════════════════════════════════════════════════════
# AC4 — job events: redact 된 진행 이벤트(secret/OTP/raw error/HTML 비포함)
# ══════════════════════════════════════════════════════════════════════════


def test_make_job_event_redacts_message():
    event = make_job_event(
        "DIAGNOSTIC",
        "warn",
        "token=agtok-fake-leak-77 email=foo@bar.com phone 010-1234-5678",
        artifact_refs=["artifact:ref-1"],
    )

    assert "agtok-fake-leak-77" not in event.message_redacted
    assert "foo@bar.com" not in event.message_redacted
    assert "010-1234-5678" not in event.message_redacted
    assert event.artifact_refs == ("artifact:ref-1",)


def test_emit_job_event_posts_to_events_path_with_redacted_body():
    transport = FakeTransport()
    event = make_job_event(
        "PROGRESS",
        "info",
        "diagnostic token=agtok-fake-leak-88 user foo@bar.com",
        artifact_refs=["artifact:sanitized-1"],
    )

    emit_job_event(_IDENTITY, "job-fake-1", event, transport=transport, base_url="https://srv.test")

    url, body, _headers = transport.calls[0]
    assert url == "https://srv.test/v1/jobs/job-fake-1/events"
    assert set(body) == {"event_type", "severity", "message_redacted", "artifact_refs"}
    assert body["event_type"] == "PROGRESS"
    assert body["severity"] == "info"
    # raw secret/email 이 본문에 없음.
    serialized = json.dumps(body)
    assert "agtok-fake-leak-88" not in serialized
    assert "foo@bar.com" not in serialized
    assert body["artifact_refs"] == ["artifact:sanitized-1"]


def test_event_type_severity_not_enum_locked():
    # 후속 워커가 임의 event_type/severity 를 늘려도 무탈(평문 문자열, "정확히 N" lock 없음).
    event = make_job_event("FUTURE_CUSTOM_EVENT", "critical", "ok")
    assert event.event_type == "FUTURE_CUSTOM_EVENT"
    assert event.severity == "critical"


# ══════════════════════════════════════════════════════════════════════════
# AC3 / 기본 executor — 결과 헬퍼 + UNSUPPORTED_JOB_TYPE
# ══════════════════════════════════════════════════════════════════════════


def test_default_execute_job_returns_unsupported_failure():
    result = default_execute_job(_job(type="WEIRD_FUTURE_TYPE"))

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_UNSUPPORTED_JOB_TYPE
    assert "WEIRD_FUTURE_TYPE" in (result.error_message_redacted or "")


def test_make_failure_result_redacts_error_message():
    result = make_failure_result(
        "AGENT_JOB_EXECUTION_ERROR",
        "boom token=agtok-fake-leak-99",
        error=RuntimeError("secret email leak@bar.com"),
    )

    assert result.status == JOB_STATUS_FAILED
    assert "agtok-fake-leak-99" not in (result.error_message_redacted or "")
    assert "leak@bar.com" not in (result.error_message_redacted or "")


# ══════════════════════════════════════════════════════════════════════════
# AC1 — JobRunner: claim 한 job 만 실행 + short-poll + token 게이트
# ══════════════════════════════════════════════════════════════════════════


def test_runner_executes_only_claimed_job_then_completes():
    transport = FakeTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
    executed: list[ClaimedJob] = []

    def execute(job):
        executed.append(job)
        return make_success_result(result_json={"ok": True})

    runner = _runner(transport, execute_job=execute)
    runner.run()

    # claim 한 그 job 만 1회 실행.
    assert len(executed) == 1
    assert executed[0].job_id == "job-fake-1"
    # 성공 보고됨(complete 1회).
    assert len(transport.calls_for("/complete")) == 1
    # claim 직후 started 이벤트 1회.
    started = transport.calls_for("/events")
    assert len(started) == 1
    assert started[0][1]["event_type"] == EVENT_TYPE_JOB_STARTED


def test_runner_does_not_call_worker_when_preflight_denies_job():
    """Preflight denial completes safely without opening browser/profile."""

    class _PreflightDenyTransport(FakeTransport):
        def post_json(self, url, body, *, headers=None) -> dict:
            if url.endswith("/preflight"):
                self.calls.append((url, body, headers))
                return {"allowed": False, "reason": "payload_expired", "server_time": "2026-06-14T12:00:00Z"}
            return super().post_json(url, body, headers=headers)

    transport = _PreflightDenyTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
    executed: list[ClaimedJob] = []

    def execute(job):
        executed.append(job)
        return make_success_result(result_json={"ok": True})

    runner = _runner(transport, execute_job=execute)
    runner.run()

    # 워커(execute_job)는 호출되지 않는다(브라우저/profile 미오픈).
    assert executed == []
    # preflight 는 1회 호출됐다.
    assert len(transport.calls_for("/preflight")) == 1
    # complete 는 실패 status + result_json["reason"] 로 안전히 보고된다.
    complete_calls = transport.calls_for("/complete")
    assert len(complete_calls) == 1
    complete_body = complete_calls[0][1]
    assert complete_body["status"] == "failed"
    assert complete_body["result_json"]["reason"] == "payload_expired"


def test_runner_preflight_unavailable_is_fail_closed():
    """Preflight transport failure stops the worker (fail-closed)."""

    class _PreflightErrorTransport(FakeTransport):
        def post_json(self, url, body, *, headers=None) -> dict:
            if url.endswith("/preflight"):
                self.calls.append((url, body, headers))
                raise TransportError("preflight HTTP error", status_code=503)
            return super().post_json(url, body, headers=headers)

    transport = _PreflightErrorTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
    executed: list[ClaimedJob] = []

    runner = _runner(transport, execute_job=lambda j: executed.append(j))
    runner.run()

    assert executed == []
    complete_calls = transport.calls_for("/complete")
    assert len(complete_calls) == 1
    assert complete_calls[0][1]["status"] == "failed"
    assert complete_calls[0][1]["result_json"]["reason"] == "preflight_unavailable"


def test_auth_coupang_2fa_is_a_preflight_job_type():
    """AUTH_COUPANG_2FA 도 브라우저/CDP 를 열고 자동 OTP 를 요청하므로 preflight 대상이다(검토 High)."""
    from rider_agent.job_loop import _PREFLIGHT_JOB_TYPES

    assert "AUTH_COUPANG_2FA" in _PREFLIGHT_JOB_TYPES
    # crawl/open-auth 도 여전히 포함, 비-브라우저 type 은 제외(불필요한 왕복 0).
    assert {"CRAWL_BAEMIN", "CRAWL_COUPANG", "OPEN_AUTH_BROWSER"} <= _PREFLIGHT_JOB_TYPES
    assert "KAKAO_SEND" not in _PREFLIGHT_JOB_TYPES
    assert "AUTH_CHECK" not in _PREFLIGHT_JOB_TYPES


def test_runner_preflight_denies_auth_coupang_2fa_before_opening_browser():
    """만료된 AUTH_COUPANG_2FA 는 preflight 에서 거부돼 브라우저/OTP 요청 전에 안전히 닫힌다."""

    class _PreflightDenyTransport(FakeTransport):
        def post_json(self, url, body, *, headers=None) -> dict:
            if url.endswith("/preflight"):
                self.calls.append((url, body, headers))
                return {"allowed": False, "reason": "payload_expired", "server_time": "2026-06-14T12:00:00Z"}
            return super().post_json(url, body, headers=headers)

    auth_job = dict(_JOB_DICT, type="AUTH_COUPANG_2FA")
    transport = _PreflightDenyTransport(claim_script=[{"jobs": [auth_job]}])
    executed: list[ClaimedJob] = []

    runner = _runner(transport, execute_job=lambda j: executed.append(j))
    runner.run()

    assert executed == []  # 자동 OTP 워커 미실행
    assert len(transport.calls_for("/preflight")) == 1
    complete_calls = transport.calls_for("/complete")
    assert len(complete_calls) == 1
    assert complete_calls[0][1]["status"] == "failed"
    assert complete_calls[0][1]["result_json"]["reason"] == "payload_expired"


def test_runner_empty_claim_skips_execute_and_sleeps():
    transport = FakeTransport(claim_script=[{"jobs": []}])
    executed: list[ClaimedJob] = []

    runner = _runner(transport, execute_job=lambda j: executed.append(j))
    runner.run()

    assert executed == []
    assert transport.calls_for("/complete") == []
    # job 없을 때 short-poll 만큼 sleep.
    assert runner._short_poll_interval == DEFAULT_SHORT_POLL_INTERVAL_SECONDS


def test_claim_failures_backoff_with_jitter():
    # 연속 claim 실패(5xx)면 다음 폴링 대기가 지수 backoff 로 늘고, per-Agent stable jitter 가
    # 더해진다(서버 복구 직후 thundering herd 완화). 성공하면 backoff 가 리셋된다.
    from rider_agent.job_loop import (
        CLAIM_BACKOFF_MULTIPLIER,
        DEFAULT_CLAIM_BACKOFF_BASE_SECONDS,
        DEFAULT_POLL_JITTER_RATIO,
        DEFAULT_SHORT_POLL_INTERVAL_SECONDS,
    )
    from rider_agent.heartbeat import stable_jitter_ratio

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=3)
    # 2회 연속 5xx 실패 후 3회차 빈 큐 성공.
    transport = FakeTransport(
        claim_script=[
            TransportError("agent jobs HTTP error", status_code=503),
            TransportError("agent jobs HTTP error", status_code=503),
            {"jobs": []},
        ]
    )
    runner = _runner(transport, stop_event=stop, sleep=sleep)
    runner.run()

    seed_ratio = stable_jitter_ratio(_IDENTITY.agent_id)
    base = DEFAULT_CLAIM_BACKOFF_BASE_SECONDS

    def _expected(backoff_base: float) -> float:
        return backoff_base + backoff_base * DEFAULT_POLL_JITTER_RATIO * seed_ratio

    waits = sleep.intervals
    assert len(waits) == 3
    # 1차 실패 후: backoff base(=5s) + jitter.
    assert waits[0] == pytest.approx(_expected(base))
    # 2차 실패 후: backoff base*multiplier(=10s) + jitter — 지수 증가.
    assert waits[1] == pytest.approx(_expected(base * CLAIM_BACKOFF_MULTIPLIER))
    assert waits[1] > waits[0]
    # 3차 성공 후: backoff 리셋 → short_poll + jitter(실패 대기보다 짧다).
    short = DEFAULT_SHORT_POLL_INTERVAL_SECONDS
    assert waits[2] == pytest.approx(
        short + short * DEFAULT_POLL_JITTER_RATIO * seed_ratio
    )
    assert waits[2] < waits[1]


def test_claim_401_is_not_backed_off():
    # 401/revoke 는 일시 장애가 아니라 재등록 필요 — backoff 로 숨기지 않고 short_poll 간격을 유지.
    from rider_agent.job_loop import (
        DEFAULT_POLL_JITTER_RATIO,
        DEFAULT_SHORT_POLL_INTERVAL_SECONDS,
    )
    from rider_agent.heartbeat import stable_jitter_ratio

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    transport = FakeTransport(
        claim_error=TransportError("agent jobs HTTP error", status_code=401)
    )
    runner = _runner(transport, stop_event=stop, sleep=sleep)
    runner.run()

    assert runner.needs_registration is True
    seed_ratio = stable_jitter_ratio(_IDENTITY.agent_id)
    short = DEFAULT_SHORT_POLL_INTERVAL_SECONDS
    expected = short + short * DEFAULT_POLL_JITTER_RATIO * seed_ratio
    # 401 은 backoff 카운터를 올리지 않으므로 매 대기가 short_poll(+jitter) 그대로다.
    assert all(w == pytest.approx(expected) for w in sleep.intervals)


def test_runner_surfaces_identity_mismatch_on_403_claim_without_backoff_spin():
    # 403 = token 은 유효하나 다른 agent identity 로 해석됨. transient backoff 에 묻지 않고
    # 재등록 필요 상태로 surfacing 하며, 전용 이벤트 코드를 남긴다.
    from rider_agent.job_loop import (
        DEFAULT_POLL_JITTER_RATIO,
        DEFAULT_SHORT_POLL_INTERVAL_SECONDS,
    )
    from rider_agent.heartbeat import stable_jitter_ratio

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    transport = FakeTransport(
        claim_error=TransportError("agent jobs HTTP error", status_code=403)
    )
    runner = _runner(transport, stop_event=stop, sleep=sleep)
    runner.run()

    assert runner.needs_registration is True
    assert runner.token_status == TOKEN_STATUS_REVOKED
    assert runner.last_error_event is not None
    assert runner.last_error_event["code"] == "AGENT_JOB_IDENTITY_REJECTED"
    # 403 도 401 처럼 backoff 카운터를 올리지 않으므로 매 대기가 short_poll(+jitter) 그대로다.
    seed_ratio = stable_jitter_ratio(_IDENTITY.agent_id)
    short = DEFAULT_SHORT_POLL_INTERVAL_SECONDS
    expected = short + short * DEFAULT_POLL_JITTER_RATIO * seed_ratio
    assert all(w == pytest.approx(expected) for w in sleep.intervals)


def test_runner_token_gate_blocks_claim_when_revoked():
    transport = FakeTransport()
    statuses: list[str] = []

    runner = _runner(
        transport,
        token_check=lambda identity: False,  # 서버 검사 실패 → revoked
        on_status=statuses.append,
    )
    runner.run()

    # claim 미전송(=job 미수신, FR-16).
    assert transport.calls == []
    assert runner.needs_registration is True
    assert runner.token_status == TOKEN_STATUS_REVOKED
    assert statuses == [TOKEN_STATUS_REVOKED]


# ══════════════════════════════════════════════════════════════════════════
# AC1.3 — best-effort 복원력(단발 실패가 thread 를 죽이지 않음, 매 주기 sleep)
# ══════════════════════════════════════════════════════════════════════════


def test_runner_survives_claim_transport_error_and_continues():
    transport = FakeTransport(
        claim_script=[TransportError("agent jobs HTTP error", status_code=503), {"jobs": []}]
    )
    logs: list[str] = []

    runner = _runner(transport, log=logs.append)
    runner.run()

    # 첫 주기 5xx 에도 루프가 죽지 않고 다음 주기로 진행(>=2회 claim).
    assert len(transport.calls_for(CLAIM_PATH)) == 2
    assert runner.last_error_event is not None
    # 5xx 는 revoke 가 아니다 — 2차 성공으로 valid 유지.
    assert runner.token_status == TOKEN_STATUS_VALID
    assert runner.needs_registration is False
    assert FAKE_TOKEN not in " ".join(logs)


def test_runner_surfaces_revoked_on_401_claim_without_crash_or_spin():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    statuses: list[str] = []
    logs: list[str] = []
    transport = FakeTransport(
        claim_error=TransportError("agent jobs HTTP error", status_code=401)
    )

    runner = _runner(
        transport, stop_event=stop, sleep=sleep, on_status=statuses.append, log=logs.append
    )
    runner.run()

    # 매 주기 401 이어도 crash 없이 루프 진행 → 주입 sleep 으로만 정지(무한 즉시 스핀 없음).
    assert len(transport.calls_for(CLAIM_PATH)) == 2
    assert sleep.intervals  # 매 주기 끝에 sleep.
    assert runner.needs_registration is True
    assert runner.token_status == TOKEN_STATUS_REVOKED
    assert statuses == [TOKEN_STATUS_REVOKED]  # 상태 변화 시에만 1회.
    assert FAKE_TOKEN not in " ".join(logs)


def test_runner_survives_executor_exception_and_reports_failure():
    transport = FakeTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
    logs: list[str] = []

    def execute(job):
        raise RuntimeError("worker exploded token=agtok-fake-leak-1")

    runner = _runner(transport, execute_job=execute, log=logs.append)
    runner.run()

    # executor 예외에도 루프가 죽지 않고 complete 로 실패 보고.
    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    body = completes[0][1]
    assert body["status"] == JOB_STATUS_FAILED
    assert body["error_code"] == "AGENT_JOB_EXECUTION_ERROR"
    # 다음 주기 claim 도 호출됨(루프 생존).
    assert len(transport.calls_for(CLAIM_PATH)) == 2
    # 에러 본문/로그에 raw secret 비노출.
    assert "agtok-fake-leak-1" not in json.dumps(body)
    assert FAKE_TOKEN not in " ".join(logs)


# ══════════════════════════════════════════════════════════════════════════
# AC2 — lease: 기록 + active_jobs 노출 + 서버 거부 흡수
# ══════════════════════════════════════════════════════════════════════════


def test_runner_records_lease_and_exposes_active_jobs_in_flight():
    transport = FakeTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
    captured: dict = {}
    holder: dict = {}

    def execute(job):
        # 실행 중(in-flight)에 active_jobs 스냅샷 캡처.
        captured["active"] = holder["runner"].active_jobs()
        return make_success_result()

    runner = _runner(transport, execute_job=execute)
    holder["runner"] = runner
    runner.run()

    # in-flight 동안 lease_expires_at 가 active_jobs 로 노출됨(heartbeat 연장 입력).
    assert captured["active"] == [
        {"job_id": "job-fake-1", "lease_expires_at": FUTURE_LEASE}
    ]
    # complete 후 in-flight 에서 제거.
    assert runner.active_jobs() == []


def test_auth_coupang_2fa_job_is_exposed_as_active_job_for_heartbeat():
    """Long auth jobs are lease-extended while running.

    crawl-coupang-auth-separation Task 8: AUTH_COUPANG_2FA 도 다른 job 과 동일하게 실행 중
    in-flight 로 추적돼 heartbeat active_jobs 에 노출된다(서버가 lease 를 계속 연장 — 장시간
    메일 대기 중 stale 회수로 중복 OTP 가 요청되지 않게).
    """

    auth_job = dict(
        _JOB_DICT,
        job_id="job-auth-2fa-9",
        type="AUTH_COUPANG_2FA",
        payload={"platform": "coupang", "recovery_mode": "coupang_auto_email_2fa"},
    )
    transport = FakeTransport(claim_script=[{"jobs": [auth_job]}])
    captured: dict = {}
    holder: dict = {}

    def execute(job):
        captured["active"] = holder["runner"].active_jobs()
        return make_success_result()

    runner = _runner(transport, execute_job=execute)
    holder["runner"] = runner
    runner.run()

    # 실행 중 lease_expires_at 가 active_jobs 로 노출됨(heartbeat 연장 입력).
    assert captured["active"] == [
        {"job_id": "job-auth-2fa-9", "lease_expires_at": FUTURE_LEASE}
    ]
    assert runner.active_jobs() == []


def test_runner_reports_success_even_when_original_claim_lease_looks_expired():
    transport = FakeTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
    # started=100, finished=200. 서버 heartbeat가 lease를 연장했을 수 있으므로
    # client는 원래 claim lease만 보고 success를 버리지 않는다.
    clock = SequenceClock([100.0, 200.0, 5000.0])
    job_expired = dict(_JOB_DICT, lease_expires_at=1000.0)
    transport.claim_script = [{"jobs": [job_expired]}]

    runner = _runner(transport, now=clock, execute_job=lambda j: make_success_result())
    runner.run()

    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    assert completes[0][1]["status"] == JOB_STATUS_SUCCESS
    assert runner.active_jobs() == []


def test_runner_reports_success_when_claim_lease_missing_and_lets_server_decide():
    job_no_lease = {"job_id": "job-fake-1", "type": "CRAWL_BAEMIN"}
    transport = FakeTransport(claim_script=[{"jobs": [job_no_lease]}])

    runner = _runner(transport, execute_job=lambda j: make_success_result())
    runner.run()

    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    assert completes[0][1]["status"] == JOB_STATUS_SUCCESS


@pytest.mark.parametrize("status_code", [409, 410])
def test_runner_absorbs_complete_rejection_without_crash(status_code):
    transport = FakeTransport(
        claim_script=[{"jobs": [dict(_JOB_DICT)]}],
        complete_error=TransportError("agent jobs HTTP error", status_code=status_code),
    )
    logs: list[str] = []

    runner = _runner(transport, execute_job=lambda j: make_success_result(), log=logs.append)
    runner.run()

    # 서버 거부(lease lost/이미 재할당)를 crash 없이 흡수·기록·in-flight 제거.
    assert len(transport.calls_for("/complete")) == 1
    assert runner.last_error_event is not None
    assert runner.active_jobs() == []
    assert FAKE_TOKEN not in " ".join(logs)


def test_runner_result_carries_agent_id_and_injected_timestamps():
    transport = FakeTransport(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
    clock = SequenceClock([100.0, 200.0, 300.0])  # started, finished, lease-check

    runner = _runner(
        transport, now=clock, execute_job=lambda j: make_success_result(metrics={"n": 1})
    )
    runner.run()

    body = transport.calls_for("/complete")[0][1]
    assert body["agent_id"] == "agent-fake-1"
    assert body["started_at"] == 100.0
    assert body["finished_at"] == 200.0
    assert body["status"] == JOB_STATUS_SUCCESS
    assert body["metrics"] == {"n": 1}


# ══════════════════════════════════════════════════════════════════════════
# AC2 — heartbeat active_jobs 배선 + start_heartbeat_thread
# ══════════════════════════════════════════════════════════════════════════


def test_build_components_wires_runner_active_jobs_into_reporter():
    runner, reporter = build_agent_components(_IDENTITY, transport=FakeTransport())
    runner._track(_job())

    # reporter 의 active_jobs provider 가 runner in-flight 를 반영(="heartbeat 로 연장" 배선).
    assert reporter._active_jobs_provider() == runner.active_jobs()
    assert reporter._active_jobs_provider() == [
        {"job_id": "job-fake-1", "lease_expires_at": FUTURE_LEASE}
    ]
    # heartbeat payload 의 active_jobs 가 실제로 채워진다.
    payload = build_heartbeat_payload(
        _IDENTITY, active_jobs_provider=reporter._active_jobs_provider
    )
    assert payload["active_jobs"] == [
        {"job_id": "job-fake-1", "lease_expires_at": FUTURE_LEASE}
    ]


def test_build_components_reports_max_jobs_capacity_in_heartbeat_metrics():
    _runner_obj, reporter = build_agent_components(
        _IDENTITY,
        transport=FakeTransport(),
        max_jobs=4,
    )

    payload = build_heartbeat_payload(
        _IDENTITY,
        metrics_provider=reporter._metrics_provider,
    )

    assert payload["metrics"]["max_in_flight"] == 4


def test_start_heartbeat_thread_runs_then_stops():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    transport = FakeTransport()
    reporter = HeartbeatReporter(_IDENTITY, transport=transport, sleep=sleep, stop_event=stop)

    thread = start_heartbeat_thread(reporter)
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert thread.daemon is True
    # heartbeat 가 적어도 1회 전송됨(/v1/agents/heartbeat).
    assert any(url.endswith("/v1/agents/heartbeat") for url, _b, _h in transport.calls)


# ══════════════════════════════════════════════════════════════════════════
# Task 4 — run_agent 오케스트레이션(startup 배선)
# ══════════════════════════════════════════════════════════════════════════


def test_run_agent_does_not_start_loop_without_identity(tmp_path):
    statuses: list[str] = []
    transport = FakeTransport()

    summary = run_agent(
        transport=transport,
        store=FakeStore(token=None),
        identity_path=tmp_path / "agent_config.json",  # 없는 파일 → identity 없음
        on_status=statuses.append,
    )

    assert isinstance(summary, AgentRunSummary)
    assert summary.started is False
    assert summary.token_status == TOKEN_STATUS_MISSING
    # 루프 미진입 → claim/heartbeat 미전송.
    assert transport.calls == []
    assert statuses == [TOKEN_STATUS_MISSING]


def test_run_agent_starts_loop_and_stops_cleanly(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    transport = FakeTransport(claim_script=[{"jobs": []}])

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,  # 단일 thread 로 결정적 검증(heartbeat thread 는 별도 테스트).
    )

    assert summary.started is True
    assert summary.runner is not None
    assert summary.reporter is not None
    # active_jobs 배선 확인(reporter provider == runner.active_jobs 동작).
    assert summary.reporter._active_jobs_provider() == summary.runner.active_jobs()


def test_run_agent_spawns_and_joins_heartbeat_thread(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=3)
    transport = FakeTransport(claim_script=[{"jobs": []}])

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=True,
    )

    assert summary.started is True
    assert summary.heartbeat_thread is not None
    # run_agent 의 finally 가 reporter.stop()+join 으로 정리 → thread 종료.
    assert not summary.heartbeat_thread.is_alive()


# ══════════════════════════════════════════════════════════════════════════
# Kakao inbound watcher thread (Phase 2/5 activation wiring)
# ══════════════════════════════════════════════════════════════════════════


def test_kakao_inbound_loop_scans_until_stop():
    stop = threading.Event()

    class _Watcher:
        def __init__(self) -> None:
            self.calls = 0

        def scan_once(self):
            self.calls += 1
            stop.set()  # stop right after the first scan

    watcher = _Watcher()
    _run_kakao_inbound_loop(watcher, stop_event=stop, interval=0.0, log=None)
    assert watcher.calls == 1


def test_kakao_inbound_loop_survives_scan_error():
    stop = threading.Event()
    logs: list[str] = []

    class _Watcher:
        def __init__(self) -> None:
            self.calls = 0

        def scan_once(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            stop.set()

    watcher = _Watcher()
    _run_kakao_inbound_loop(watcher, stop_event=stop, interval=0.0, log=logs.append)
    assert watcher.calls == 2  # survived the first error and scanned again
    assert logs  # error surfaced (redacted) to the log


def test_kakao_inbound_loop_logs_non_empty_scan_report():
    from rider_agent.kakao_inbound import ScanReport

    stop = threading.Event()
    logs: list[str] = []

    class _Watcher:
        def scan_once(self):
            stop.set()
            return ScanReport(
                health="warning",
                reason="configured_room_not_found",
                missing_rooms=1,
            )

    _run_kakao_inbound_loop(_Watcher(), stop_event=stop, interval=0.0, log=logs.append)

    joined = " ".join(logs)
    assert "kakao inbound scan" in joined
    assert "configured_room_not_found" in joined
    assert "missing_rooms" not in joined
    assert "configured_missing_count" in joined


def test_run_agent_spawns_and_joins_kakao_inbound_thread(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=3)
    transport = FakeTransport(claim_script=[{"jobs": []}])

    class _Watcher:
        def scan_once(self):
            return None

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        kakao_inbound_watcher=_Watcher(),
        kakao_inbound_interval_seconds=0.0,
    )

    assert summary.started is True
    assert summary.kakao_inbound_thread is not None
    # run_agent 의 finally 가 stop_event.set()+join 으로 정리 → thread 종료.
    assert not summary.kakao_inbound_thread.is_alive()


def test_run_agent_heartbeat_includes_kakao_inbound_health(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)

    class _Watcher:
        def scan_once(self):
            stop.set()

        def health(self):
            return {
                "state": "active",
                "reason": "ok",
                "latest_window_size": 20,
            }

    summary = run_agent(
        transport=FakeTransport(claim_script=[{"jobs": []}]),
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        kakao_inbound_watcher=_Watcher(),
        kakao_inbound_interval_seconds=0.0,
    )

    status = summary.reporter._kakao_status_provider()
    assert status["inbound"] == {
        "state": "active",
        "reason": "ok",
        "latest_window_size": 20,
    }


def test_run_agent_without_watcher_has_no_inbound_thread(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    transport = FakeTransport(claim_script=[{"jobs": []}])

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
    )

    assert summary.kakao_inbound_thread is None


# ══════════════════════════════════════════════════════════════════════════
# token-auth 헤더 + 평문 비노출(핵심 가드)
# ══════════════════════════════════════════════════════════════════════════


def test_claim_complete_events_carry_bearer_header_without_plaintext_in_body():
    transport = FakeTransport(claim_script=[{"jobs": []}])
    claim_jobs(_IDENTITY, transport=transport)
    complete_job(_IDENTITY, "job-fake-1", make_success_result(), transport=transport)
    emit_job_event(
        _IDENTITY, "job-fake-1", make_job_event("PROGRESS", "info", "ok"), transport=transport
    )

    for _url, body, headers in transport.calls:
        assert headers == {"Authorization": f"Bearer {FAKE_TOKEN}"}
        # token 은 헤더에만 — 본문 어디에도 평문 없음.
        assert FAKE_TOKEN not in json.dumps(body)


def test_no_plaintext_token_in_logs_or_error_event_on_failure():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=1)
    logs: list[str] = []
    transport = FakeTransport(
        claim_error=TransportError("agent jobs HTTP error", status_code=500)
    )

    runner = _runner(transport, stop_event=stop, sleep=sleep, log=logs.append)
    runner.run()

    joined = " ".join(logs) + json.dumps(runner.last_error_event or {})
    assert FAKE_TOKEN not in joined


# ══════════════════════════════════════════════════════════════════════════
# Task 5 — __main__ run 서브커맨드(thin wiring, 무회귀, redaction)
# ══════════════════════════════════════════════════════════════════════════
# NOTE: rider_agent.__main__ 은 함수 내부에서 lazy import 한다. 모듈 상단에서 import 하면
# pytest collection 시점에 __main__ 이 sys.modules 에 올라가 4.1 runpy 테스트가 RuntimeWarning
# 을 낸다(무회귀 유지) — memory/agent-main-runpy-warning.


def test_run_agent_loop_cli_started_prints_redacted(capsys):
    from rider_agent import __main__ as agent_main

    captured: dict = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return AgentRunSummary(started=True, token_status=TOKEN_STATUS_VALID)

    rc = agent_main._run_agent_loop(
        ["--server-url", "https://srv.test"],
        transport=object(),
        store=object(),
        identity_path="cfg",
        runner=fake_run_agent,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped" in out
    assert captured["base_url"] == "https://srv.test"
    assert captured["start_auth_worker"] is True
    assert captured["start_crawl_worker"] is True
    assert captured["start_kakao_sender"] is True
    # inbound watcher assembly is fail-safe: bad store / no config -> None wired through.
    assert captured["kakao_inbound_watcher"] is None
    # token 평문 미출력.
    assert FAKE_TOKEN not in out


def test_run_agent_loop_cli_wires_refreshing_kakao_inbound_watcher(
    tmp_path, monkeypatch, capsys
):
    from rider_agent import __main__ as agent_main
    from rider_agent.kakao_inbound import RefreshingKakaoInboundWatcher
    import rider_crawl.config as crawl_config

    captured: dict = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return AgentRunSummary(started=True, token_status=TOKEN_STATUS_VALID)

    state_root = tmp_path / "state-root"
    config_dir = state_root / "runtime" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "kakao-inbound.json").write_text(
        json.dumps({"enabled": True, "chat_list_db_path": "a.edb"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(crawl_config, "app_state_root", lambda: state_root)
    identity_path = tmp_path / "agent_config.json"
    store = FakeStore()
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    rc = agent_main._run_agent_loop(
        ["--server-url", "https://srv.test"],
        transport=object(),
        store=store,
        identity_path=identity_path,
        runner=fake_run_agent,
    )

    assert rc == 0
    assert isinstance(captured["kakao_inbound_watcher"], RefreshingKakaoInboundWatcher)
    assert FAKE_TOKEN not in capsys.readouterr().out


def test_run_agent_loop_cli_wires_local_log_and_session_probe(tmp_path, monkeypatch, capsys):
    from rider_agent import __main__ as agent_main
    import rider_crawl.config as crawl_config

    captured: dict = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        kwargs["log"]("agent failed token=raw-secret-123456")
        return AgentRunSummary(started=True, token_status=TOKEN_STATUS_VALID)

    state_root = tmp_path / "state-root"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(crawl_config, "app_state_root", lambda: state_root)
    rc = agent_main._run_agent_loop(
        [],
        transport=object(),
        store=object(),
        identity_path="cfg",
        runner=fake_run_agent,
    )

    assert rc == 0
    assert callable(captured["log"])
    assert callable(captured["session_probe"])
    log_text = (state_root / "logs" / "agent.log").read_text(encoding="utf-8")
    assert REDACTED in log_text
    assert "raw-secret-123456" not in log_text
    assert FAKE_TOKEN not in capsys.readouterr().out


def test_run_agent_loop_second_instance_returns_1_without_starting_runner(
    tmp_path, capsys
):
    from rider_agent import __main__ as agent_main

    first_entered = threading.Event()
    release_first = threading.Event()
    runner_calls = 0

    def fake_run_agent(**kwargs):
        nonlocal runner_calls
        runner_calls += 1
        if runner_calls == 1:
            first_entered.set()
            release_first.wait(timeout=2.0)
        return AgentRunSummary(started=True, token_status=TOKEN_STATUS_VALID)

    first_rc: list[int] = []
    identity_path = tmp_path / "agent_config.json"
    first_thread = threading.Thread(
        target=lambda: first_rc.append(
            agent_main._run_agent_loop(
                [],
                transport=object(),
                store=object(),
                identity_path=identity_path,
                runner=fake_run_agent,
            )
        )
    )

    first_thread.start()
    assert first_entered.wait(timeout=1.0)

    second_rc = agent_main._run_agent_loop(
        [],
        transport=object(),
        store=object(),
        identity_path=identity_path,
        runner=fake_run_agent,
    )

    release_first.set()
    first_thread.join(timeout=2.0)

    assert second_rc == 1
    assert runner_calls == 1
    assert "already running" in capsys.readouterr().out
    assert first_rc == [0]


def test_run_agent_loop_cli_passes_capacity_and_profile_knobs(tmp_path, monkeypatch):
    from rider_agent import __main__ as agent_main

    captured: dict = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return AgentRunSummary(started=True, token_status=TOKEN_STATUS_VALID)

    monkeypatch.chdir(tmp_path)
    rc = agent_main._run_agent_loop(
        [
            "--max-jobs",
            "2",
            "--profile-idle-ttl-seconds",
            "30",
            "--max-profiles",
            "4",
        ],
        transport=object(),
        store=object(),
        identity_path="cfg",
        runner=fake_run_agent,
    )

    assert rc == 0
    assert captured["max_jobs"] == 2
    assert captured["profile_idle_ttl_seconds"] == 30
    assert captured["max_profiles"] == 4


def test_run_agent_builds_default_crawl_profile_manager(tmp_path, monkeypatch):
    from rider_agent import worker_composition

    monkeypatch.setattr(worker_composition, "app_state_root", lambda: tmp_path, raising=False)
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)
    stop = threading.Event()
    stop.set()

    summary = run_agent(
        transport=FakeTransport(claim_script=[{"jobs": []}]),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_crawl_worker=True,
        start_kakao_sender=False,
    )

    assert summary.crawl_worker is not None
    assert summary.crawl_worker._profile_manager is not None
    profiles_root = summary.crawl_worker._profile_manager._profiles_root
    assert profiles_root.is_absolute()
    assert profiles_root == tmp_path / "runtime" / "agent-browser-profiles"
    assert summary.reporter._browser_profiles_provider is not None


def test_run_agent_closes_crawl_profile_manager_on_shutdown(tmp_path):
    class Profiles:
        def __init__(self) -> None:
            self.closed = 0

        def browser_profiles(self):
            return []

        def close_all(self) -> None:
            self.closed += 1

    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)
    stop = threading.Event()
    stop.set()
    profiles = Profiles()

    summary = run_agent(
        transport=FakeTransport(claim_script=[{"jobs": []}]),
        store=store,
        identity_path=identity_path,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        start_crawl_worker=True,
        start_kakao_sender=False,
        capabilities=["CRAWL_BAEMIN"],
        crawl_profile_manager=profiles,
        crawl_snapshot=lambda config, *, platform_name=None: None,
    )

    assert summary.started is True
    assert profiles.closed == 1


def test_run_agent_rejects_profile_cap_below_max_jobs(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)
    stop = threading.Event()
    stop.set()

    with pytest.raises(ValueError, match="max_profiles.*max_jobs"):
        run_agent(
            transport=FakeTransport(claim_script=[{"jobs": []}]),
            store=store,
            identity_path=identity_path,
            sleep=lambda _s: None,
            now=lambda: 0.0,
            stop_event=stop,
            start_heartbeat=False,
            start_crawl_worker=True,
            start_kakao_sender=False,
            max_jobs=4,
            max_profiles=2,
        )


def test_run_agent_loop_cli_not_started_returns_1_without_leak(capsys):
    from rider_agent import __main__ as agent_main

    def fake_run_agent(**kwargs):
        return AgentRunSummary(started=False, token_status=TOKEN_STATUS_MISSING)

    rc = agent_main._run_agent_loop(
        [],
        transport=object(),
        store=object(),
        identity_path="cfg",
        runner=fake_run_agent,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "not started" in out
    assert "register" in out
    assert FAKE_TOKEN not in out


def test_main_routes_run_subcommand(monkeypatch):
    from rider_agent import __main__ as agent_main

    captured: dict[str, list[str]] = {}

    def fake_loop(run_argv, **_kwargs):
        captured["argv"] = run_argv
        return 5

    monkeypatch.setattr(agent_main, "_run_agent_loop", fake_loop)
    assert agent_main.main(["run", "--server-url", "https://x"]) == 5
    assert captured["argv"] == ["--server-url", "https://x"]


def test_main_without_subcommand_still_prints_banner(capsys):
    # 무회귀: run 추가 후에도 인자 없는 호출은 배너(4.1 계약).
    from rider_agent import __main__ as agent_main

    assert agent_main.main([]) == 0
    assert "sync runtime" in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════════════
# QA E2E gap coverage (qa-generate-e2e-tests) — AC 분기 보강
# 위 35 케이스가 비운 동작 분기를 채운다(외부 호출 0, 가짜 값만, 주입 sleep/now/stop).
# ══════════════════════════════════════════════════════════════════════════


# ── AC1.2 — capabilities 기본 DEFAULT_CAPABILITIES 이되 주입 가능 ──────────────


def test_claim_jobs_uses_injected_capabilities():
    # 기본값(DEFAULT_CAPABILITIES) 대신 주입한 capabilities 가 claim 본문에 그대로 실린다.
    transport = FakeTransport(claim_script=[{"jobs": []}])

    claim_jobs(
        _IDENTITY,
        transport=transport,
        capabilities=["CRAWL_BAEMIN", "KAKAO_SEND"],
    )

    _url, body, _headers = transport.calls[0]
    assert body["capabilities"] == ["CRAWL_BAEMIN", "KAKAO_SEND"]


def test_runner_passes_injected_capabilities_through_to_claim():
    transport = FakeTransport(claim_script=[{"jobs": []}])

    runner = _runner(transport, capabilities=("KAKAO_SEND",))
    runner.run()

    claim_body = transport.calls_for(CLAIM_PATH)[0][1]
    assert claim_body["capabilities"] == ["KAKAO_SEND"]


# ── AC1.3 / AC2.5 — complete 거부 분기(401 revoked / 일반 5xx) ─────────────────


def test_runner_surfaces_revoked_on_complete_401():
    # complete 가 401 이면 lease-lost(409/410)와 달리 재등록 필요로 surfacing 한다.
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=1)  # 1주기만 — 다음 claim 성공이 상태를 되돌리지 않게.
    statuses: list[str] = []
    logs: list[str] = []
    transport = FakeTransport(
        claim_script=[{"jobs": [dict(_JOB_DICT)]}],
        complete_error=TransportError("agent jobs HTTP error", status_code=401),
    )

    runner = _runner(
        transport,
        stop_event=stop,
        sleep=sleep,
        execute_job=lambda j: make_success_result(),
        on_status=statuses.append,
        log=logs.append,
    )
    runner.run()

    assert len(transport.calls_for("/complete")) == 1
    assert runner.needs_registration is True
    assert runner.token_status == TOKEN_STATUS_REVOKED
    assert statuses == [TOKEN_STATUS_REVOKED]
    assert runner.active_jobs() == []  # in-flight 정리.
    assert FAKE_TOKEN not in " ".join(logs)


def test_runner_surfaces_identity_mismatch_on_complete_403():
    # complete 가 403 이면 401(revoked) 와 같은 재등록 필요 계열로 닫되, identity mismatch
    # 전용 이벤트 코드를 남긴다. lease-lost(409/410) 와 다르다.
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=1)  # 1주기만 — 다음 claim 성공이 상태를 되돌리지 않게.
    statuses: list[str] = []
    logs: list[str] = []
    transport = FakeTransport(
        claim_script=[{"jobs": [dict(_JOB_DICT)]}],
        complete_error=TransportError("agent jobs HTTP error", status_code=403),
    )

    runner = _runner(
        transport,
        stop_event=stop,
        sleep=sleep,
        execute_job=lambda j: make_success_result(),
        on_status=statuses.append,
        log=logs.append,
    )
    runner.run()

    assert len(transport.calls_for("/complete")) == 1
    assert runner.needs_registration is True
    assert runner.token_status == TOKEN_STATUS_REVOKED
    assert statuses == [TOKEN_STATUS_REVOKED]
    assert runner.last_error_event is not None
    assert runner.last_error_event["code"] == "AGENT_JOB_IDENTITY_REJECTED"
    assert runner.active_jobs() == []  # in-flight 정리.
    assert FAKE_TOKEN not in " ".join(logs)


def test_runner_records_error_on_generic_complete_failure_and_survives():
    # 409/410/401 이 아닌 일반 실패(5xx)도 crash 없이 흡수·기록·in-flight 제거하고 루프 생존.
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=1)
    transport = FakeTransport(
        claim_script=[{"jobs": [dict(_JOB_DICT)]}],
        complete_error=TransportError("agent jobs HTTP error", status_code=500),
    )

    runner = _runner(
        transport,
        stop_event=stop,
        sleep=sleep,
        execute_job=lambda j: make_success_result(),
    )
    runner.run()

    assert len(transport.calls_for("/complete")) == 1
    assert runner.last_error_event is not None
    # 5xx 는 revoke 가 아니다 — 상태 유지.
    assert runner.token_status == TOKEN_STATUS_VALID
    assert runner.needs_registration is False
    assert runner.active_jobs() == []


def test_complete_failure_keeps_result_in_local_outbox(tmp_path):
    outbox_path = tmp_path / "complete-outbox.json"
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=1)
    transport = FakeTransport(
        claim_script=[{"jobs": [dict(_JOB_DICT)]}],
        complete_error=TransportError("agent jobs HTTP error", status_code=500),
    )

    runner = _runner(
        transport,
        stop_event=stop,
        sleep=sleep,
        execute_job=lambda j: make_success_result(result_json={"ok": True, "token": "raw-secret"}),
        complete_outbox_path=outbox_path,
    )
    runner.run()

    payload = json.loads(outbox_path.read_text(encoding="utf-8"))
    assert len(payload["pending"]) == 1
    record = payload["pending"][0]
    assert record["job_id"] == "job-fake-1"
    assert record["body"]["status"] == JOB_STATUS_SUCCESS
    assert record["body"]["result_json"]["ok"] is True
    assert "raw-secret" not in json.dumps(payload)


def test_outbox_replays_before_claiming_new_jobs(tmp_path):
    outbox_path = tmp_path / "complete-outbox.json"
    first_transport = FakeTransport(
        claim_script=[{"jobs": [dict(_JOB_DICT)]}],
        complete_error=TransportError("agent jobs HTTP error", status_code=500),
    )
    first_runner = _runner(
        first_transport,
        stop_event=threading.Event(),
        sleep=lambda _seconds: None,
        execute_job=lambda j: make_success_result(result_json={"ok": True}),
        complete_outbox_path=outbox_path,
    )
    first_runner.run_once()

    second_transport = FakeTransport(claim_script=[{"jobs": []}])
    second_runner = _runner(
        second_transport,
        stop_event=threading.Event(),
        sleep=lambda _seconds: None,
        complete_outbox_path=outbox_path,
    )
    second_runner.run_once()

    complete_index = next(
        index for index, (url, _body, _headers) in enumerate(second_transport.calls)
        if url.endswith("/complete")
    )
    claim_index = next(
        index for index, (url, _body, _headers) in enumerate(second_transport.calls)
        if url.endswith(CLAIM_PATH)
    )
    assert complete_index < claim_index
    assert json.loads(outbox_path.read_text(encoding="utf-8"))["pending"] == []


def test_runner_retries_transient_complete_failure_before_untracking():
    class FlakyCompleteTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__(claim_script=[{"jobs": [dict(_JOB_DICT)]}])
            self.failures_left = 1

        def post_json(self, url, body, *, headers=None) -> dict:
            if url.endswith("/complete") and self.failures_left > 0:
                self.calls.append((url, body, headers))
                self.failures_left -= 1
                raise TransportError("agent jobs HTTP error", status_code=500)
            return super().post_json(url, body, headers=headers)

    transport = FlakyCompleteTransport()
    runner = _runner(
        transport,
        sleep=lambda _seconds: None,
        execute_job=lambda j: make_success_result(),
    )

    runner.run_once()

    assert len(transport.calls_for("/complete")) == 2
    assert runner.last_error_event is None
    assert runner.active_jobs() == []


# ── AC1.3 / AC4 — started 이벤트 보고 실패가 루프/job 을 죽이지 않는다(best-effort) ──


def test_runner_survives_started_event_failure_and_still_completes():
    # /events(started) 가 실패해도 job 은 정상 실행·complete 된다(이벤트는 진행에 영향 없음).
    transport = FakeTransport(
        claim_script=[{"jobs": [dict(_JOB_DICT)]}],
        events_error=TransportError("agent jobs HTTP error", status_code=500),
    )
    executed: list[ClaimedJob] = []
    logs: list[str] = []

    def execute(job):
        executed.append(job)
        return make_success_result()

    runner = _runner(transport, execute_job=execute, log=logs.append)
    runner.run()

    assert len(executed) == 1  # 이벤트 실패에도 job 실행됨.
    assert len(transport.calls_for("/complete")) == 1  # 그리고 complete 됨.
    assert runner.last_error_event is not None  # 이벤트 실패가 redact 되어 기록됨.
    assert FAKE_TOKEN not in " ".join(logs)


# ── AC2.5 — complete lease 판정은 서버에 맡긴다 ────────────────────────────────


def test_runner_reports_failure_result_even_when_lease_expired():
    # 실패 결과도 client local lease 판단 없이 서버에 보고한다.
    job_expired = dict(_JOB_DICT, lease_expires_at=1000.0)
    transport = FakeTransport(claim_script=[{"jobs": [job_expired]}])

    def execute(job):
        return make_failure_result("AGENT_JOB_EXECUTION_ERROR", "boom")

    # now=5000 > lease=1000 이지만 client는 local lease 판단 없이 서버에 보고한다.
    runner = _runner(transport, now=lambda: 5000.0, execute_job=execute)
    runner.run()

    completes = transport.calls_for("/complete")
    assert len(completes) == 1
    assert completes[0][1]["status"] == JOB_STATUS_FAILED


# ── AC1 — claim 한 job 이 여러 개면 모두 실행·complete 한다 ──────────────────────


def test_runner_processes_multiple_claimed_jobs():
    job1 = dict(_JOB_DICT)
    job2 = dict(_JOB_DICT, job_id="job-fake-2")
    transport = FakeTransport(claim_script=[{"jobs": [job1, job2]}])
    executed: list[str] = []

    def execute(job):
        executed.append(job.job_id)
        return make_success_result()

    runner = _runner(transport, execute_job=execute)
    runner.run()

    assert executed == ["job-fake-1", "job-fake-2"]
    assert len(transport.calls_for("/complete")) == 2
    assert runner.active_jobs() == []  # 둘 다 in-flight 에서 제거.


def test_runner_processes_claimed_jobs_concurrently_up_to_max_jobs():
    job1 = dict(_JOB_DICT)
    job2 = dict(_JOB_DICT, job_id="job-fake-2")
    transport = FakeTransport(claim_script=[{"jobs": [job1, job2]}])
    lock = threading.Lock()
    running = 0
    running_counts: list[int] = []
    both_started = threading.Event()

    def execute(job):
        nonlocal running
        with lock:
            running += 1
            running_counts.append(running)
            if running == 2:
                both_started.set()
        both_started.wait(timeout=0.2)
        with lock:
            running -= 1
        return make_success_result()

    runner = _runner(
        transport,
        max_jobs=2,
        execute_job=execute,
        sleep=lambda _seconds: None,
    )

    runner.run_once()

    assert max(running_counts) == 2
    assert len(transport.calls_for("/complete")) == 2
    assert runner.active_jobs() == []


def test_runner_tracks_all_claimed_jobs_before_serial_execution_for_heartbeat():
    job1 = dict(_JOB_DICT)
    job2 = dict(_JOB_DICT, job_id="job-fake-2")
    transport = FakeTransport(claim_script=[{"jobs": [job1, job2]}])
    captured: list[dict] = []
    holder: dict = {}

    def execute(job):
        if job.job_id == "job-fake-1":
            captured.extend(holder["runner"].active_jobs())
        return make_success_result()

    runner = _runner(transport, max_jobs=2, execute_job=execute)
    holder["runner"] = runner
    runner.run_once()

    assert captured == [
        {"job_id": "job-fake-1", "lease_expires_at": FUTURE_LEASE},
        {"job_id": "job-fake-2", "lease_expires_at": FUTURE_LEASE},
    ]
    assert runner.active_jobs() == []


# ── AC1.2 — run_agent startup 게이트: 토큰 revoke 면 루프 미진입 ─────────────────


def test_run_agent_does_not_start_loop_when_token_revoked(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)
    statuses: list[str] = []
    transport = FakeTransport()

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        token_check=lambda identity: False,  # 서버 검사 실패 → revoked
        on_status=statuses.append,
    )

    assert summary.started is False
    assert summary.token_status == TOKEN_STATUS_REVOKED
    assert transport.calls == []  # claim/heartbeat 미전송(루프 미진입).
    assert statuses == [TOKEN_STATUS_REVOKED]
