"""Tests for the Agent RIDER_LOOKUP worker (Phase 4).

Browser/config prep is injected as ``fetch_rider_rows`` so these never open a
browser: they lock command execution, platform/expiry guards, failure
classification, the result shape the server consumes, and job-type routing.
"""

from datetime import datetime, timezone

from rider_agent.job_loop import (
    ERROR_UNSUPPORTED_JOB_TYPE,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCESS,
    ClaimedJob,
)
from rider_agent.reuse import BrowserActionRequiredError, CdpUnavailableError
from rider_agent.workers.rider_lookup import (
    ERROR_AUTH_REQUIRED,
    ERROR_LOOKUP_FAILURE,
    ERROR_LOOKUP_TIMEOUT,
    ERROR_PARSER_MISSING_DATA,
    ERROR_PAYLOAD_EXPIRED,
    ERROR_UNSUPPORTED_PLATFORM,
    RESULT_TYPE,
    RESULT_TYPE_FAILED,
    RiderLookupWorker,
    build_execute_job,
    make_baemin_rider_rows_fetcher,
    rider_lookup_payload_from_job,
)


class MissingPerformanceDataError(Exception):
    """Name-compatible stand-in (worker classifies by class name)."""


def _rows_with_match():
    return [
        {
            "이름": "강민기",
            "휴대폰번호": "010-9999-1234",
            "완료": "48",
            "거절": "0",
            "배차취소": "1",
            "배달취소(라이더귀책)": "1",
        },
        {"이름": "다른사람", "휴대폰번호": "010-1111-5678", "완료": "10"},
    ]


def _payload(**overrides):
    payload = {
        "tenant_id": "t1",
        "target_id": "tg1",
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


def _job(payload=None, *, type="RIDER_LOOKUP"):
    return ClaimedJob(job_id="j1", type=type, target_id="tg1", payload=payload or _payload())


def _worker(fetch):
    return RiderLookupWorker(
        fetch_rider_rows=fetch,
        now=lambda: datetime(2026, 7, 1, 0, 30, tzinfo=timezone.utc),
    )


def test_success_builds_rider_lookup_result():
    calls = []

    def fetch(job, payload):
        calls.append(payload)
        return _rows_with_match()

    result = _worker(fetch).execute(_job())

    assert result.status == JOB_STATUS_SUCCESS
    assert calls and calls[0].name == "강민기"
    rj = result.result_json
    assert rj["result_type"] == RESULT_TYPE
    assert rj["reply_text"] == "강민기1234\n취소율 4%, 취소 2개\n위험합니다."
    assert rj["reply_channel_id"] == "ch1"
    assert rj["reply_kakao_room_name"] == "운영방"
    assert rj["origin_event_key"] == "sha256:abc"
    assert rj["target_id"] == "tg1"
    assert rj["tenant_id"] == "t1"
    assert rj["auth_state"] == "ACTIVE"


def test_no_match_still_succeeds_with_no_match_reply():
    result = _worker(lambda job, payload: [{"이름": "아무개", "휴대폰번호": "010-0000-0000"}]).execute(_job())
    assert result.status == JOB_STATUS_SUCCESS
    assert result.result_json["reply_text"] == "강민기1234\n해당 라이더를 찾지 못했습니다."


def test_unsupported_platform_fails_closed_without_fetch():
    calls = []

    def fetch(job, payload):
        calls.append(payload)
        return []

    result = _worker(fetch).execute(_job(_payload(platform="coupang")))

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_UNSUPPORTED_PLATFORM
    assert calls == []  # never fetched
    assert result.result_json["result_type"] == RESULT_TYPE_FAILED
    assert result.result_json["reply_channel_id"] == "ch1"
    assert "reply_text" not in result.result_json


def test_expired_payload_fails_before_fetch():
    calls = []

    def fetch(job, payload):
        calls.append(payload)
        return []

    result = _worker(fetch).execute(_job(_payload(expires_at="2026-07-01T00:00:00Z")))

    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_PAYLOAD_EXPIRED
    assert result.result_json["reason"] == "payload_expired"
    assert calls == []


def test_auth_required_is_classified():
    def fetch(job, payload):
        raise BrowserActionRequiredError("login")

    result = _worker(fetch).execute(_job())
    assert result.error_code == ERROR_AUTH_REQUIRED
    assert result.result_json["auth_state"] == "AUTH_REQUIRED"


def test_cdp_unreachable_is_classified():
    def fetch(job, payload):
        raise CdpUnavailableError("no cdp")

    result = _worker(fetch).execute(_job())
    assert result.error_code == "CDP_UNREACHABLE"


def test_timeout_is_classified():
    def fetch(job, payload):
        raise TimeoutError()

    assert _worker(fetch).execute(_job()).error_code == ERROR_LOOKUP_TIMEOUT


def test_parser_missing_data_is_classified():
    def fetch(job, payload):
        raise MissingPerformanceDataError()

    assert _worker(fetch).execute(_job()).error_code == ERROR_PARSER_MISSING_DATA


def test_generic_failure_is_classified_and_keeps_reply_scope():
    def fetch(job, payload):
        raise ValueError("boom")

    result = _worker(fetch).execute(_job())
    assert result.error_code == ERROR_LOOKUP_FAILURE
    assert result.result_json["origin_event_key"] == "sha256:abc"
    assert "reply_text" not in result.result_json


def test_wrong_job_type_is_unsupported_without_fetch():
    calls = []

    def fetch(job, payload):
        calls.append(payload)
        return []

    result = _worker(fetch).execute(_job(type="CRAWL_BAEMIN"))
    assert result.status == JOB_STATUS_FAILED
    assert result.error_code == ERROR_UNSUPPORTED_JOB_TYPE
    assert calls == []


def test_build_execute_job_routes_by_type():
    from rider_agent.job_loop import make_failure_result

    worker = _worker(lambda job, payload: _rows_with_match())
    fallback_calls = []

    def fallback(job):
        fallback_calls.append(job.type)
        return make_failure_result("OTHER", "other")

    execute = build_execute_job(rider_lookup_worker=worker, fallback=fallback)

    lookup_result = execute(_job())
    assert lookup_result.status == JOB_STATUS_SUCCESS
    assert fallback_calls == []

    other_result = execute(_job(type="KAKAO_SEND"))
    assert other_result.error_code == "OTHER"
    assert fallback_calls == ["KAKAO_SEND"]


# --- production fetcher (profile prep + shared fetch) ----------------------

def test_fetcher_composes_profile_prep_and_shared_fetch():
    import types
    from pathlib import Path

    captured = {}

    def fake_fetch(config):
        captured["config"] = config
        return _rows_with_match()

    class _FakeProfileManager:
        def __init__(self):
            self.calls = []

        def ensure_profile(self, tenant_id, target_id, *, build_config):
            self.calls.append((tenant_id, target_id))
            return types.SimpleNamespace(cdp_url="http://127.0.0.1:9333", profile_dir=Path("prof-dir"))

    pm = _FakeProfileManager()
    fetcher = make_baemin_rider_rows_fetcher(profile_manager=pm, fetch_rows=fake_fetch)

    job = _job()
    rows = fetcher(job, rider_lookup_payload_from_job(job))

    assert rows == _rows_with_match()
    assert pm.calls == [("t1", "tg1")]  # same profile identity as crawl jobs
    config = captured["config"]
    assert config.platform_name == "baemin"
    assert config.coupang_eats_url.endswith("/delivery/history")
    assert config.baemin_center_name == "남구센터"
    assert str(config.cdp_url) == "http://127.0.0.1:9333"
