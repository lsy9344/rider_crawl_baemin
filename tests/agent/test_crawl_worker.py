"""Agent crawl worker for CRAWL_BAEMIN / CRAWL_COUPANG.

External browser/network calls are injected fakes. The worker must route real
crawl job types away from ``default_execute_job`` and return the snapshot-shaped
``result_json`` the server ingest workstream expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from rider_agent.heartbeat import CAPABILITY_CRAWL_BAEMIN, CAPABILITY_CRAWL_COUPANG
from rider_agent.job_loop import (
    ClaimedJob,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCESS,
    make_failure_result,
    make_success_result,
    run_agent,
)
from rider_agent.reuse import BrowserActionRequiredError
from rider_agent.secure_store import AgentIdentity, save_agent_identity
from rider_agent.workers.crawl_worker import (
    AUTH_STATE_ACTIVE,
    AUTH_STATE_AUTH_REQUIRED,
    AUTH_STATE_CENTER_MISMATCH,
    ERROR_AUTH_REQUIRED,
    ERROR_CENTER_MISMATCH,
    ERROR_CRAWL_FAILURE,
    ERROR_CRAWL_TIMEOUT,
    ERROR_PAYLOAD_EXPIRED,
    ERROR_TARGET_VALIDATION_FAILURE,
    ERROR_PROFILE_UNAVAILABLE,
    CrawlWorker,
    build_execute_job,
)
from rider_crawl.redaction import REDACTED
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
    assert payload["auth_state"] == AUTH_STATE_ACTIVE
    assert payload["parser_version"] == "baemin-v1"
    assert payload["quality_state"] == "OK"
    assert payload["normalized_json"]["center_name"] == "배민센터A"
    assert payload["artifact_refs"] == []


def test_crawl_worker_rejects_expired_payload_before_profile_prepare() -> None:
    """crawl_worker checks expires_at before ensure_profile."""

    now = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
    expired_at = now - timedelta(minutes=5)

    base = _crawl_job()
    job = ClaimedJob(
        job_id=base.job_id,
        type=base.type,
        target_id=base.target_id,
        lease_expires_at=base.lease_expires_at,
        payload={
            **base.payload,
            "job_origin": "scheduler",
            "expires_at": expired_at.isoformat().replace("+00:00", "Z"),
        },
    )

    ensure_calls: list[tuple] = []
    crawl_calls: list[str] = []

    class _RecordingProfileManager:
        def ensure_profile(self, tenant_id, target_id, *, build_config):
            ensure_calls.append((tenant_id, target_id))
            raise AssertionError("ensure_profile must not be called for expired payload")

    def fake_crawl(config, *, platform_name=None):
        crawl_calls.append(platform_name)
        return _baemin_snapshot()

    worker = CrawlWorker(
        profile_manager=_RecordingProfileManager(),
        crawl_snapshot=fake_crawl,
        now=lambda: now,
    )

    result = worker.execute(job)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_PAYLOAD_EXPIRED
    assert result.result_json["reason"] == "payload_expired"
    # profile/browser/crawl 은 전혀 호출되지 않았다.
    assert ensure_calls == []
    assert crawl_calls == []


def test_crawl_worker_runs_when_payload_not_expired() -> None:
    """Non-expired payload still runs the crawl normally."""

    now = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
    base = _crawl_job()
    job = ClaimedJob(
        job_id=base.job_id,
        type=base.type,
        target_id=base.target_id,
        lease_expires_at=base.lease_expires_at,
        payload={
            **base.payload,
            "expires_at": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        },
    )
    worker = CrawlWorker(
        crawl_snapshot=lambda config, *, platform_name=None: _baemin_snapshot(),
        now=lambda: now,
    )

    result = worker.execute(job)

    assert result.status == JOB_STATUS_SUCCESS


def test_coupang_success_returns_snapshot_payload() -> None:
    worker = CrawlWorker(crawl_snapshot=lambda config, *, platform_name=None: _coupang_snapshot())

    result = worker.execute(
        _crawl_job(CAPABILITY_CRAWL_COUPANG, platform="coupang", expected="쿠팡상점A")
    )

    assert result.status == JOB_STATUS_SUCCESS
    payload = result.result_json
    assert payload["platform"] == "coupang"
    assert payload["auth_state"] == AUTH_STATE_ACTIVE
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
            "coupang_login_id_ref": "vault://coupang/login-id",
            "coupang_login_password_ref": "vault://coupang/login-password",
            "verification_email_address_ref": "vault://mail/address",
            "verification_email_app_password_ref": "vault://mail/app-password",
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
    # crawl-coupang-auth-separation Task 4: 크롤 job 은 inline 2FA 를 하지 않는다. 자격 ref 는
    # 여전히 해소돼 config 에 채워지지만(로컬 호환·센터 검증), 자동 email 2FA 는 강제로 꺼진다.
    assert config.coupang_auto_email_2fa_enabled is False
    assert config.coupang_login_id == "coupang-login-id"
    assert config.coupang_login_password == "coupang-login-password"
    assert config.verification_email_address == "mailbox@example.invalid"
    assert config.verification_email_app_password == "mail-app-password"
    assert config.verification_email_subject_keyword == "보안코드"
    assert config.verification_email_sender_keyword == "wing"
    assert config.verification_email_mailbox_lock_id == "vault://mail/address"


def test_coupang_job_local_secret_refs_are_resolved_into_email_2fa_config() -> None:
    captured = {}
    secrets = {
        "local:target-1/coupang_login_id": "coupang-login-id",
        "local:target-1/coupang_login_password": "coupang-login-password",
        "local:target-1/verification_email_address": "mailbox@example.invalid",
        "local:target-1/verification_email_app_password": "mail-app-password",
    }

    def fake_crawl(config, *, platform_name=None):
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
            "coupang_login_id_ref": "local:target-1/coupang_login_id",
            "coupang_login_password_ref": "local:target-1/coupang_login_password",
            "verification_email_address_ref": "local:target-1/verification_email_address",
            "verification_email_app_password_ref": "local:target-1/verification_email_app_password",
            "coupang_auto_email_2fa_enabled": True,
        },
    )
    worker = CrawlWorker(crawl_snapshot=fake_crawl, secret_resolver=secrets.get)

    result = worker.execute(job)

    assert result.status == JOB_STATUS_SUCCESS
    config = captured["config"]
    assert config.coupang_login_id == "coupang-login-id"
    assert config.coupang_login_password == "coupang-login-password"
    assert config.verification_email_address == "mailbox@example.invalid"
    assert config.verification_email_app_password == "mail-app-password"
    # Task 4: 크롤 job 의 자동 email 2FA 는 강제 비활성(자격 ref 해소는 유지).
    assert config.coupang_auto_email_2fa_enabled is False


def test_coupang_job_plaintext_credentials_flow_into_email_2fa_config() -> None:
    # 옵션 B: 웹앱에 입력한 평문 쿠팡 ID/PW/이메일이 _ref 페이로드 키에 평문으로 실려 와도
    # 핸들 resolve 없이 그대로 config 에 채워져 자동 로그인 + IMAP 2FA 에 쓰인다.
    captured = {}

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
            "coupang_login_id_ref": "plain-coupang-login-id",
            "coupang_login_password_ref": "plain-coupang-login-password",
            "verification_email_address_ref": "myemail@gmail.com",
            "verification_email_app_password_ref": "myapppassword",
            "coupang_auto_email_2fa_enabled": True,
        },
    )
    worker = CrawlWorker(crawl_snapshot=fake_crawl)

    result = worker.execute(job)

    assert result.status == JOB_STATUS_SUCCESS
    config = captured["config"]
    # Task 4: 평문 자격은 그대로 config 에 흘러들지만 자동 email 2FA 는 강제 비활성.
    assert config.coupang_auto_email_2fa_enabled is False
    assert config.coupang_login_id == "plain-coupang-login-id"
    assert config.coupang_login_password == "plain-coupang-login-password"
    assert config.verification_email_address == "myemail@gmail.com"
    assert config.verification_email_app_password == "myapppassword"


def test_coupang_job_defaults_email_2fa_disabled_when_flag_omitted() -> None:
    captured = {}

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
            "coupang_login_id_ref": "plain-coupang-login-id",
            "coupang_login_password_ref": "plain-coupang-login-password",
            "verification_email_address_ref": "myemail@gmail.com",
            "verification_email_app_password_ref": "myapppassword",
        },
    )
    worker = CrawlWorker(crawl_snapshot=fake_crawl)

    result = worker.execute(job)

    assert result.status == JOB_STATUS_SUCCESS
    config = captured["config"]
    assert captured["platform_name"] == "coupang"
    assert config.coupang_auto_email_2fa_enabled is False


def test_crawl_coupang_job_does_not_enable_email_2fa_from_payload(monkeypatch) -> None:
    """Crawl jobs never run automatic Coupang email recovery.

    crawl-coupang-auth-separation Task 4: payload 에 ``coupang_auto_email_2fa_enabled=True`` 가
    있어도 ``AppConfig.coupang_auto_email_2fa_enabled`` 는 False 이고,
    ``recover_coupang_session_with_email_2fa`` 는 호출되지 않는다.
    """
    captured = {}
    recover_calls = []
    monkeypatch.setattr(
        "rider_agent.reuse.recover_coupang_session_with_email_2fa",
        lambda *a, **k: recover_calls.append((a, k)) or True,
    )

    def fake_crawl(config, *, platform_name=None):
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
            "coupang_login_id_ref": "plain-coupang-login-id",
            "coupang_login_password_ref": "plain-coupang-login-password",
            "verification_email_address_ref": "myemail@gmail.com",
            "verification_email_app_password_ref": "myapppassword",
            "coupang_auto_email_2fa_enabled": True,
        },
    )

    result = CrawlWorker(crawl_snapshot=fake_crawl).execute(job)

    assert result.status == JOB_STATUS_SUCCESS
    assert captured["config"].coupang_auto_email_2fa_enabled is False
    assert recover_calls == []  # 크롤 경로는 자동 email 2FA 복구를 호출하지 않는다


def test_crawl_coupang_login_screen_returns_auth_required_without_recovery(monkeypatch) -> None:
    """Login screen in crawl path is surfaced to server as AUTH_REQUIRED (no auto recovery)."""
    recover_calls = []
    monkeypatch.setattr(
        "rider_agent.reuse.recover_coupang_session_with_email_2fa",
        lambda *a, **k: recover_calls.append((a, k)) or True,
    )

    def fake_crawl(config, *, platform_name=None):
        raise BrowserActionRequiredError("coupang login required")

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
            "coupang_login_id_ref": "plain-coupang-login-id",
            "coupang_login_password_ref": "plain-coupang-login-password",
            "verification_email_address_ref": "myemail@gmail.com",
            "verification_email_app_password_ref": "myapppassword",
            "coupang_auto_email_2fa_enabled": True,
        },
    )

    result = CrawlWorker(crawl_snapshot=fake_crawl).execute(job)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == AUTH_STATE_AUTH_REQUIRED
    assert recover_calls == []  # 로그인 화면을 만나도 자동 2FA 복구 시도 0


def test_default_process_boundary_passes_plaintext_credentials(monkeypatch) -> None:
    # 옵션 B: 평문 자격증명도 process boundary 를 정상 통과해 subprocess crawl 로 넘어간다.
    crossed = {}

    def fake_subprocess(child_job, **kwargs):
        crossed["payload"] = dict(child_job.payload)
        return make_success_result(result_json={"target_id": child_job.target_id})

    monkeypatch.setattr(
        "rider_agent.workers.crawl_process.run_crawl_in_subprocess",
        fake_subprocess,
    )

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
            "coupang_login_password_ref": "plain-login-password",
        },
    )

    result = CrawlWorker().execute(job)

    assert result.status == JOB_STATUS_SUCCESS
    assert crossed["payload"]["coupang_login_password_ref"] == "plain-login-password"


def test_coupang_unresolved_secret_ref_fails_before_crawl() -> None:
    calls: list[str | None] = []

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
            "coupang_login_id_ref": "vault://coupang/login-id",
            "coupang_login_password_ref": "vault://coupang/missing-password",
            "verification_email_address_ref": "vault://mail/address",
            "verification_email_app_password_ref": "vault://mail/app-password",
            "coupang_auto_email_2fa_enabled": True,
        },
    )
    secrets = {
        "vault://coupang/login-id": "coupang-login-id",
        "vault://mail/address": "mailbox@example.invalid",
        "vault://mail/app-password": "mail-app-password",
    }
    worker = CrawlWorker(crawl_snapshot=fake_crawl, secret_resolver=secrets.get)

    result = worker.execute(job)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == "SECRET_REF_UNRESOLVED"
    assert calls == []


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


def test_unclassified_crawl_failure_persists_redacted_cause() -> None:
    class RendererCrash(RuntimeError):
        pass

    def broken_crawl(config, *, platform_name=None):
        raise RendererCrash("renderer crashed: token=raw-secret-123456")

    worker = CrawlWorker(crawl_snapshot=broken_crawl)

    result = worker.execute(_crawl_job())

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_CRAWL_FAILURE
    assert "RendererCrash" in (result.error_message_redacted or "")
    assert REDACTED in (result.error_message_redacted or "")
    assert "raw-secret" not in (result.error_message_redacted or "")
    diagnostics = result.result_json["diagnostics"]["agent_error"]
    assert diagnostics["type"] == "RendererCrash"
    assert REDACTED in diagnostics["message_redacted"]
    assert "raw-secret" not in diagnostics["message_redacted"]


def test_coupang_center_validation_exception_is_target_validation_failure() -> None:
    class CoupangCenterValidationError(RuntimeError):
        pass

    def broken_crawl(config, *, platform_name=None):
        raise CoupangCenterValidationError(
            "쿠팡 센터 검증 실패: 피크 대시보드 화면에서 센터명을 확인하지 못했습니다.\n"
            "설정 센터명: 쿠팡상점A"
        )

    worker = CrawlWorker(crawl_snapshot=broken_crawl)

    result = worker.execute(
        _crawl_job(CAPABILITY_CRAWL_COUPANG, platform="coupang", expected="쿠팡상점A")
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_TARGET_VALIDATION_FAILURE
    assert "CoupangCenterValidationError" in (result.error_message_redacted or "")
    assert result.result_json["auth_state"] == AUTH_STATE_CENTER_MISMATCH
    assert result.result_json["mismatch"] == ERROR_CENTER_MISMATCH
    diagnostics = result.result_json["diagnostics"]["agent_error"]
    assert diagnostics["type"] == "CoupangCenterValidationError"
    assert "피크 대시보드" in diagnostics["message_redacted"]


def test_crawl_timeout_returns_failure_without_hanging() -> None:
    started = threading.Event()
    release = threading.Event()

    def stuck_crawl(config, *, platform_name=None):
        started.set()
        release.wait()
        return _baemin_snapshot()

    job = _crawl_job()
    job = ClaimedJob(
        job_id=job.job_id,
        type=job.type,
        target_id=job.target_id,
        lease_expires_at=job.lease_expires_at,
        payload={**job.payload, "timeout_seconds": 0.01},
    )
    worker = CrawlWorker(crawl_snapshot=stuck_crawl)

    result = worker.execute(job)
    release.set()

    assert started.is_set()
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_CRAWL_TIMEOUT
    assert result.result_json["target_id"] == "target-1"


def test_crawl_timeout_releases_target_profile() -> None:
    started = threading.Event()
    release = threading.Event()

    class Profiles:
        def __init__(self) -> None:
            self.released: list[tuple[str, str]] = []
            self.cleaned: list[float] = []

        def ensure_profile(self, tenant_id, target_id, *, build_config):
            return SimpleNamespace(
                cdp_url="http://127.0.0.1:9222",
                profile_dir=Path("runtime") / "profiles" / str(target_id),
            )

        def release(self, tenant_id, target_id):
            self.released.append((tenant_id, target_id))

        def cleanup_idle_profiles(self, *, max_idle_seconds):
            self.cleaned.append(max_idle_seconds)

    def stuck_crawl(config, *, platform_name=None):
        started.set()
        release.wait()
        return _baemin_snapshot()

    job = _crawl_job()
    job = ClaimedJob(
        job_id=job.job_id,
        type=job.type,
        target_id=job.target_id,
        lease_expires_at=job.lease_expires_at,
        payload={**job.payload, "timeout_seconds": 0.01},
    )
    profiles = Profiles()
    worker = CrawlWorker(
        profile_manager=profiles,
        crawl_snapshot=stuck_crawl,
        profile_idle_ttl_seconds=30,
    )

    try:
        result = worker.execute(job)

        assert started.is_set()
        assert result.status == JOB_STATUS_FAILED
        assert result.error_code == ERROR_CRAWL_TIMEOUT
        assert profiles.released == [("tenant-1", "target-1")]
        assert profiles.cleaned == [30]
    finally:
        release.set()


def test_default_crawl_timeout_uses_process_boundary(monkeypatch) -> None:
    calls: list[tuple[str, float, str, str]] = []

    def fake_process(job, *, timeout_seconds, target_id, platform, cleanup=None):
        calls.append((job.job_id, timeout_seconds, target_id, platform))
        return make_failure_result(
            ERROR_CRAWL_TIMEOUT,
            "crawl timed out",
            result_json={"target_id": target_id, "platform": platform},
        )

    monkeypatch.setattr(
        "rider_agent.workers.crawl_process.run_crawl_in_subprocess",
        fake_process,
    )

    job = _crawl_job()
    worker = CrawlWorker()

    result = worker.execute(job)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_CRAWL_TIMEOUT
    assert calls == [(job.job_id, 60.0, "target-1", "baemin")]


def test_stateful_crawl_timeout_uses_process_boundary_and_cleanup(monkeypatch) -> None:
    calls: list[tuple[str, float, str, str, dict]] = []

    class Profiles:
        def __init__(self) -> None:
            self.released: list[tuple[str, str]] = []
            self.cleaned: list[float] = []

        def ensure_profile(self, tenant_id, target_id, *, build_config):
            config = build_config(
                tenant_id=tenant_id,
                target_id=target_id,
                cdp_url="http://127.0.0.1:9301",
                user_data_dir=Path("runtime") / "profiles" / str(target_id),
            )
            return SimpleNamespace(
                cdp_url=config.cdp_url,
                profile_dir=config.browser_user_data_dir,
            )

        def release(self, tenant_id, target_id):
            self.released.append((tenant_id, target_id))

        def cleanup_idle_profiles(self, *, max_idle_seconds):
            self.cleaned.append(max_idle_seconds)

    def fail_thread_timeout(*args, **kwargs):
        raise AssertionError("browser crawl must not use thread timeout path")

    def fake_process(job, *, timeout_seconds, target_id, platform, cleanup=None):
        calls.append((job.job_id, timeout_seconds, target_id, platform, dict(job.payload)))
        if cleanup is not None:
            cleanup()
        return make_failure_result(
            ERROR_CRAWL_TIMEOUT,
            "crawl timed out",
            result_json={"target_id": target_id, "platform": platform},
        )

    monkeypatch.setattr("rider_agent.workers.crawl_worker._run_with_timeout", fail_thread_timeout)
    monkeypatch.setattr(
        "rider_agent.workers.crawl_process.run_crawl_in_subprocess",
        fake_process,
    )

    profiles = Profiles()
    job = _crawl_job()
    worker = CrawlWorker(
        profile_manager=profiles,
        secret_resolver=lambda ref: "secret",
        profile_idle_ttl_seconds=30,
    )

    result = worker.execute(job)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_CRAWL_TIMEOUT
    assert len(calls) == 1
    assert calls[0][:4] == (job.job_id, 60.0, "target-1", "baemin")
    assert calls[0][4]["cdp_url"] == "http://127.0.0.1:9301"
    assert calls[0][4]["browser_user_data_dir"].endswith("target-1")
    assert profiles.released == [("tenant-1", "target-1")]
    assert profiles.cleaned == [30]


def test_default_process_boundary_enabled_with_stateful_context() -> None:
    assert CrawlWorker(profile_manager=object())._should_use_process_boundary() is True
    assert CrawlWorker(secret_resolver=lambda ref: "secret")._should_use_process_boundary() is True


def test_crawl_worker_runs_profile_cleanup_after_job() -> None:
    class Profiles:
        def __init__(self) -> None:
            self.cleanup_calls: list[float] = []

        def ensure_profile(self, *args, **kwargs):
            return SimpleNamespace(
                cdp_url="http://127.0.0.1:9301",
                profile_dir=Path("runtime") / "profiles" / "target-1",
            )

        def cleanup_idle_profiles(self, *, max_idle_seconds):
            self.cleanup_calls.append(max_idle_seconds)
            return []

    profiles = Profiles()
    worker = CrawlWorker(
        profile_manager=profiles,
        profile_idle_ttl_seconds=30,
        crawl_snapshot=lambda config, *, platform_name=None: _baemin_snapshot(),
    )

    result = worker.execute(_crawl_job())

    assert result.status == JOB_STATUS_SUCCESS
    assert profiles.cleanup_calls == [30]


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
                    "coupang_login_id_ref": "vault://coupang/login-id",
                    "coupang_login_password_ref": "vault://coupang/login-password",
                    "verification_email_address_ref": "vault://mail/address",
                    "verification_email_app_password_ref": "vault://mail/app-password",
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
    # Task 4: store resolver 는 여전히 자격 ref 를 config 에 채우지만, 크롤 job 의 자동 email
    # 2FA 는 강제 비활성(자동복구는 별도 AUTH_COUPANG_2FA job).
    assert config.coupang_auto_email_2fa_enabled is False
    assert config.coupang_login_id == "coupang-login-id"
    assert config.coupang_login_password == "coupang-login-password"
    assert config.verification_email_address == "mailbox@example.invalid"
    assert config.verification_email_app_password == "mail-app-password"


# ══════════════════════════════════════════════════════════════════════════
# crawl_process: timeout 시 child Python 만이 아니라 Chrome 트리까지 종료(고아 방지)
# ══════════════════════════════════════════════════════════════════════════


def test_subprocess_timeout_kills_process_tree(monkeypatch) -> None:
    import subprocess as _sp

    import rider_agent.workers.crawl_process as cp

    captured: dict = {}
    tree_killed: list[int] = []

    class _FakeProc:
        def __init__(self) -> None:
            self.pid = 4321
            self._alive = True

        def wait(self, timeout=None):
            # 첫 wait(타임아웃 대기)는 TimeoutExpired 로 timeout 분기 유발.
            if self._alive:
                self._alive = False
                raise _sp.TimeoutExpired(cmd="crawl", timeout=timeout or 0)
            return 0

        def poll(self):
            return None if self._alive else 0

    def _fake_popen(argv, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    def _fake_terminate(proc):
        tree_killed.append(proc.pid)

    monkeypatch.setattr(cp.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(cp, "_terminate_process_tree", _fake_terminate)

    job = ClaimedJob(
        job_id="job-1", type="CRAWL_BAEMIN", target_id="target-1",
        lease_expires_at=None, payload={"timeout_seconds": 0},
    )
    cleanup_called: list[bool] = []
    result = cp.run_crawl_in_subprocess(
        job, timeout_seconds=0.01, target_id="target-1", platform="baemin",
        cleanup=lambda: cleanup_called.append(True),
    )

    # child 는 자체 프로세스 그룹/세션으로 띄워져 트리 kill 이 가능해야 한다.
    kwargs = captured["kwargs"]
    assert ("creationflags" in kwargs) or kwargs.get("start_new_session") is True
    # timeout 시 트리 종료가 호출되고 cleanup 도 돈다.
    assert tree_killed == [4321]
    assert cleanup_called == [True]
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_CRAWL_TIMEOUT


def test_subprocess_failure_reports_redacted_stderr(monkeypatch) -> None:
    import rider_agent.workers.crawl_process as cp

    class _FailedProc:
        pid = 4322
        returncode = 1

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    def _fake_popen(argv, **kwargs):
        kwargs["stderr"].write(b"Traceback token=raw-secret-123456\nRenderer crashed\n")
        kwargs["stderr"].flush()
        return _FailedProc()

    monkeypatch.setattr(cp.subprocess, "Popen", _fake_popen)

    job = ClaimedJob(
        job_id="job-1", type="CRAWL_BAEMIN", target_id="target-1",
        lease_expires_at=None, payload={"timeout_seconds": 0},
    )
    result = cp.run_crawl_in_subprocess(
        job, timeout_seconds=1, target_id="target-1", platform="baemin",
    )

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_CRAWL_FAILURE
    diagnostics = result.result_json["diagnostics"]["subprocess"]
    assert diagnostics["returncode"] == 1
    assert REDACTED in diagnostics["stderr_tail"]
    assert "raw-secret" not in diagnostics["stderr_tail"]


def test_redacted_tail_redacts_before_truncating(tmp_path) -> None:
    """tail 잘림이 마스킹 문맥을 끊지 않는다 — redact 먼저, truncate 나중(검토 High).

    민감 키(``password=``)가 마지막 N 자(tail) 경계 **앞**에 있고 그 값은 tail 안에 남는 상황을
    만든다. truncate 를 먼저 하면 키가 잘려 값만 남아 누출되지만, redact 를 먼저 하면 값이 마스킹된
    뒤 tail 을 취하므로 secret 이 남지 않는다.
    """
    import rider_agent.workers.crawl_process as cp
    from rider_crawl.redaction import REDACTED

    secret = "supersecretvalue123456"
    # password= 가 4000자 경계 앞쪽에 오도록 앞에 padding 을 채우고, 값은 거의 끝(tail 안)에 둔다.
    head_pad = "A" * (cp._MAX_STREAM_TAIL_CHARS - 5)
    tail_pad = "B" * 200
    text = f"{head_pad} password={secret} {tail_pad}"
    log_path = tmp_path / "stderr.log"
    log_path.write_text(text, encoding="utf-8")

    out = cp._redacted_tail(log_path)

    assert len(out) <= cp._MAX_STREAM_TAIL_CHARS
    assert secret not in out  # 값이 tail 안에 남았어도 마스킹됨
    assert REDACTED in out


def test_terminate_process_tree_is_best_effort_when_already_gone() -> None:
    import rider_agent.workers.crawl_process as cp

    class _DeadProc:
        pid = 999

        def poll(self):
            return 0  # 이미 종료됨.

    # 이미 죽은 proc 이면 아무 것도 하지 않고 조용히 반환(예외 없음).
    cp._terminate_process_tree(_DeadProc())
