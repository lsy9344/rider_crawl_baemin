"""Agent crawl worker for CRAWL_BAEMIN / CRAWL_COUPANG.

External browser/network calls are injected fakes. The worker must route real
crawl job types away from ``default_execute_job`` and return the snapshot-shaped
``result_json`` the server ingest workstream expects.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading

from rider_agent.heartbeat import CAPABILITY_CRAWL_BAEMIN, CAPABILITY_CRAWL_COUPANG
from rider_agent.job_loop import ClaimedJob, JOB_STATUS_FAILED, JOB_STATUS_SUCCESS, run_agent
from rider_agent.secure_store import AgentIdentity, save_agent_identity
from rider_agent.workers.crawl_worker import (
    AUTH_STATE_ACTIVE,
    AUTH_STATE_AUTH_REQUIRED,
    AUTH_STATE_CENTER_MISMATCH,
    ERROR_AUTH_REQUIRED,
    ERROR_CENTER_MISMATCH,
    ERROR_TARGET_VALIDATION_FAILURE,
    ERROR_PROFILE_UNAVAILABLE,
    CrawlWorker,
    build_execute_job,
)
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)

_IDENTITY = AgentIdentity(
    agent_id="agent-fake-crawl",
    agent_token="agtok-fake-crawl-secret",
    tenant_scope={"tenant": "t-fake"},
    config_version="cfg-fake-1",
)


@dataclass(frozen=True)
class UnsafeSnapshot:
    center_name: str
    completed_count: int
    raw_html: str
    cookie_token: str
    nested: dict


def _baemin_snapshot(center_name: str = "배민센터A") -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name=center_name,
        date_label="6월 15일",
        shift_label="오후논피크",
        shift_time_range="13:00~17:00",
        shift_status="진행중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2,
        cancelled_count=0,
        completed_count=102,
        sequence_violation_count=0,
        lunch_peak_count=60,
        dinner_peak_count=0,
        non_peak_count=42,
        active_riders=5,
    )


def _coupang_snapshot() -> PerformanceSnapshot:
    return PerformanceSnapshot(
        current_screen=_baemin_snapshot("쿠팡상점A"),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=9, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )


def _crawl_job(job_type=CAPABILITY_CRAWL_BAEMIN, *, platform="baemin", expected="배민센터A"):
    return ClaimedJob(
        job_id="job-crawl-1",
        type=job_type,
        target_id="target-1",
        lease_expires_at=5_000_000_000.0,
        payload={
            "target_id": "target-1",
            "tenant_id": "tenant-1",
            "platform": platform,
            "platform_account_id": "account-1",
            "primary_url": "https://example.invalid/performance",
            "expected_display_name": expected,
            "browser_profile_id": "profile-1",
            "browser_profile_ref": "local-profile-ref",
            "timeout_seconds": 60,
            "parser_version": f"{platform}-v1",
        },
    )


def test_baemin_success_returns_snapshot_payload() -> None:
    calls: list[str] = []

    def fake_crawl(config, *, platform_name=None):
        calls.append(platform_name)
        assert config.send_enabled is False
        return _baemin_snapshot()

    worker = CrawlWorker(crawl_snapshot=fake_crawl)

    result = worker.execute(_crawl_job())

    assert result.status == JOB_STATUS_SUCCESS
    assert calls == ["baemin"]
    payload = result.result_json
    assert payload["schema_version"] == 1
    assert payload["result_type"] == "snapshot"
    assert payload["target_id"] == "target-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["platform"] == "baemin"
    assert payload["parser_version"] == "baemin-v1"
    assert payload["quality_state"] == "OK"
    assert payload["normalized_json"]["center_name"] == "배민센터A"
    assert payload["artifact_refs"] == []


def test_coupang_success_returns_snapshot_payload() -> None:
    worker = CrawlWorker(crawl_snapshot=lambda config, *, platform_name=None: _coupang_snapshot())

    result = worker.execute(
        _crawl_job(CAPABILITY_CRAWL_COUPANG, platform="coupang", expected="쿠팡상점A")
    )

    assert result.status == JOB_STATUS_SUCCESS
    payload = result.result_json
    assert payload["platform"] == "coupang"
    assert payload["normalized_json"]["peak_dashboard"]["processed_count"] == 67
    assert payload["normalized_json"]["current_screen"]["center_name"] == "쿠팡상점A"


def test_coupang_job_refs_are_resolved_into_email_2fa_config() -> None:
    captured = {}
    secrets = {
        "vault://coupang/login-id": "coupang-login-id",
        "vault://coupang/login-password": "coupang-login-password",
        "vault://mail/address": "mailbox@example.invalid",
        "vault://mail/app-password": "mail-app-password",
    }

    def fake_crawl(config, *, platform_name=None):
        captured["platform_name"] = platform_name
        captured["config"] = config
        return _coupang_snapshot()

    base_job = _crawl_job(
        CAPABILITY_CRAWL_COUPANG, platform="coupang", expected="쿠팡상점A"
    )
    job = ClaimedJob(
        job_id=base_job.job_id,
        type=base_job.type,
        target_id=base_job.target_id,
        lease_expires_at=base_job.lease_expires_at,
        payload={
            **base_job.payload,
            "username": "vault://coupang/login-id",
            "password": "vault://coupang/login-password",
            "verification_email_address": "vault://mail/address",
            "verification_email_app_password": "vault://mail/app-password",
            "verification_email_subject_keyword": "보안코드",
            "verification_email_sender_keyword": "wing",
            "coupang_auto_email_2fa_enabled": True,
        },
    )
    worker = CrawlWorker(crawl_snapshot=fake_crawl, secret_resolver=secrets.get)

    result = worker.execute(job)

    assert result.status == JOB_STATUS_SUCCESS
    config = captured["config"]
    assert captured["platform_name"] == "coupang"
    assert config.coupang_auto_email_2fa_enabled is True
    assert config.coupang_login_id == "coupang-login-id"
    assert config.coupang_login_password == "coupang-login-password"
    assert config.verification_email_address == "mailbox@example.invalid"
    assert config.verification_email_app_password == "mail-app-password"
    assert config.verification_email_subject_keyword == "보안코드"
    assert config.verification_email_sender_keyword == "wing"
    assert config.verification_email_mailbox_lock_id == "vault://mail/address"


def test_coupang_job_plaintext_secret_fields_now_accepted() -> None:
    calls: list[str | None] = []
    secrets = {
        "vault://coupang/login-id": "coupang-login-id",
        "vault://coupang/login-password": "coupang-login-password",
        "vault://mail/address": "mailbox@example.invalid",
        "vault://mail/app-password": "mail-app-password",
    }

    def fake_crawl(config, *, platform_name=None):
        calls.append(platform_name)
        return _coupang_snapshot()

    base_job = _crawl_job(
        CAPABILITY_CRAWL_COUPANG, platform="coupang", expected="쿠팡상점A"
    )
    job = ClaimedJob(
        job_id=base_job.job_id,
        type=base_job.type,
        target_id=base_job.target_id,
        lease_expires_at=base_job.lease_expires_at,
        payload={
            **base_job.payload,
            "username": "vault://coupang/login-id",
            "password": "vault://coupang/login-password",
            "verification_email_address": "myemail@gmail.com",
            "verification_email_app_password": "myapppassword",
            "coupang_auto_email_2fa_enabled": True,
        },
    )
    worker = CrawlWorker(crawl_snapshot=fake_crawl, secret_resolver=secrets.get)

    result = worker.execute(job)

    assert result.status == JOB_STATUS_SUCCESS
    assert calls == ["coupang"]


def test_auth_required_does_not_call_crawler() -> None:
    calls: list[str] = []
    worker = CrawlWorker(
        crawl_snapshot=lambda config, *, platform_name=None: calls.append(platform_name),
        auth_probe=lambda job, config: AUTH_STATE_AUTH_REQUIRED,
    )

    result = worker.execute(_crawl_job())

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert calls == []


def test_center_mismatch_is_fail_closed() -> None:
    worker = CrawlWorker(crawl_snapshot=lambda config, *, platform_name=None: _baemin_snapshot("다른센터"))

    result = worker.execute(_crawl_job(expected="배민센터A"))

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_TARGET_VALIDATION_FAILURE
    assert result.result_json["auth_state"] == AUTH_STATE_CENTER_MISMATCH
    assert result.result_json["mismatch"] == ERROR_CENTER_MISMATCH


def test_profile_failure_is_fail_closed_before_crawl() -> None:
    class BrokenProfiles:
        def ensure_profile(self, *args, **kwargs):
            raise RuntimeError("profile path C:/secret/raw should not leak")

    calls: list[str] = []
    worker = CrawlWorker(
        profile_manager=BrokenProfiles(),
        crawl_snapshot=lambda config, *, platform_name=None: calls.append(platform_name),
    )

    result = worker.execute(_crawl_job())

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_PROFILE_UNAVAILABLE
    assert calls == []
    assert "C:/secret/raw" not in (result.error_message_redacted or "")


def test_result_payload_excludes_secrets_raw_html_and_local_paths() -> None:
    worker = CrawlWorker(crawl_snapshot=lambda config, *, platform_name=None: _baemin_snapshot())
    job = _crawl_job()
    job = ClaimedJob(
        job_id=job.job_id,
        type=job.type,
        target_id=job.target_id,
        lease_expires_at=job.lease_expires_at,
        payload={
            **job.payload,
            "password": "plain-password-value",
            "otp": "123456",
            "raw_html": "<html>plain-password-value</html>",
            "local_path": "C:/Users/Someone/Profile",
        },
    )

    result = worker.execute(job)

    blob = json.dumps(result.result_json, ensure_ascii=False)
    assert "plain-password-value" not in blob
    assert "123456" not in blob
    assert "<html>" not in blob
    assert "C:/Users/Someone/Profile" not in blob


def test_snapshot_normalized_json_strips_sensitive_crawler_fields() -> None:
    worker = CrawlWorker(
        crawl_snapshot=lambda config, *, platform_name=None: UnsafeSnapshot(
            center_name="배민센터A",
            completed_count=102,
            raw_html="<html>secret-page</html>",
            cookie_token="cookie-token-raw",
            nested={
                "safe_count": 7,
                "local_path": "C:/Users/Someone/Profile",
                "otp": "123456",
            },
        )
    )

    result = worker.execute(_crawl_job())

    normalized = result.result_json["normalized_json"]
    assert normalized == {
        "center_name": "배민센터A",
        "completed_count": 102,
        "nested": {"safe_count": 7},
    }
    blob = json.dumps(normalized, ensure_ascii=False)
    assert "secret-page" not in blob
    assert "cookie-token-raw" not in blob
    assert "C:/Users/Someone/Profile" not in blob
    assert "123456" not in blob


def test_build_execute_job_routes_crawl_types_and_keeps_fallback() -> None:
    worker = CrawlWorker(crawl_snapshot=lambda config, *, platform_name=None: _baemin_snapshot())
    fallback_jobs: list[ClaimedJob] = []

    execute = build_execute_job(
        crawl_worker=worker,
        fallback=lambda job: fallback_jobs.append(job) or worker.make_unsupported(job),
    )

    crawl_result = execute(_crawl_job())
    other_job = ClaimedJob(job_id="j-other", type="CAPTURE_DIAGNOSTIC")
    other_result = execute(other_job)

    assert crawl_result.status == JOB_STATUS_SUCCESS
    assert fallback_jobs == [other_job]
    assert other_result.status == JOB_STATUS_FAILED


class _FakeStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def put(self, value, *, ref="") -> str:
        self._data[ref] = value
        return ref

    def resolve(self, ref) -> str | None:
        return self._data.get(ref)


class _StoppingSleep:
    def __init__(self, stop: threading.Event) -> None:
        self._stop = stop
        self.calls = 0

    def __call__(self, _seconds: float) -> None:
        self.calls += 1
        self._stop.set()


class _FakeTransport:
    def __init__(self, claim_response: dict) -> None:
        self._claim_response = claim_response
        self.calls: list[tuple[str, dict, dict | None]] = []

    def post_json(self, url, body, *, headers=None) -> dict:
        self.calls.append((url, body, headers))
        if url.endswith("/v1/jobs/claim"):
            return self._claim_response
        return {}

    def bodies_for(self, suffix: str) -> list[dict]:
        return [body for url, body, _headers in self.calls if url.endswith(suffix)]


def test_run_agent_composes_crawl_worker_for_crawl_job(tmp_path) -> None:
    store = _FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    transport = _FakeTransport(
        {
            "jobs": [
                {
                    "job_id": "job-crawl-1",
                    "type": CAPABILITY_CRAWL_BAEMIN,
                    "target_id": "target-1",
                    "lease_expires_at": 5_000_000_000.0,
                    **_crawl_job().payload,
                }
            ]
        }
    )

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=_StoppingSleep(stop),
        now=lambda: 1.0,
        stop_event=stop,
        start_heartbeat=False,
        start_crawl_worker=True,
        crawl_snapshot=lambda config, *, platform_name=None: _baemin_snapshot(),
    )

    complete_bodies = transport.bodies_for("/complete")
    assert summary.started is True
    assert summary.crawl_worker is not None
    assert complete_bodies
    assert complete_bodies[0]["status"] == JOB_STATUS_SUCCESS
    assert complete_bodies[0]["result_json"]["result_type"] == "snapshot"
    assert complete_bodies[0]["error_code"] is None


def test_run_agent_passes_store_resolver_to_crawl_worker(tmp_path) -> None:
    store = _FakeStore()
    store.put("coupang-login-id", ref="vault://coupang/login-id")
    store.put("coupang-login-password", ref="vault://coupang/login-password")
    store.put("mailbox@example.invalid", ref="vault://mail/address")
    store.put("mail-app-password", ref="vault://mail/app-password")
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    stop = threading.Event()
    base_job = _crawl_job(
        CAPABILITY_CRAWL_COUPANG, platform="coupang", expected="쿠팡상점A"
    )
    transport = _FakeTransport(
        {
            "jobs": [
                {
                    "job_id": "job-crawl-1",
                    "type": CAPABILITY_CRAWL_COUPANG,
                    "target_id": "target-1",
                    "lease_expires_at": 5_000_000_000.0,
                    **base_job.payload,
                    "username": "vault://coupang/login-id",
                    "password": "vault://coupang/login-password",
                    "verification_email_address": "vault://mail/address",
                    "verification_email_app_password": "vault://mail/app-password",
                    "coupang_auto_email_2fa_enabled": True,
                }
            ]
        }
    )
    captured = {}

    def fake_crawl(config, *, platform_name=None):
        captured["config"] = config
        stop.set()
        return _coupang_snapshot()

    summary = run_agent(
        transport=transport,
        store=store,
        identity_path=identity_path,
        sleep=lambda _seconds: stop.set(),
        now=lambda: 1.0,
        stop_event=stop,
        start_heartbeat=False,
        start_crawl_worker=True,
        crawl_snapshot=fake_crawl,
    )

    config = captured["config"]
    assert summary.started is True
    assert config.coupang_auto_email_2fa_enabled is True
    assert config.coupang_login_id == "coupang-login-id"
    assert config.coupang_login_password == "coupang-login-password"
    assert config.verification_email_address == "mailbox@example.invalid"
    assert config.verification_email_app_password == "mail-app-password"
