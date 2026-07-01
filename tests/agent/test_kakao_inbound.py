"""Tests for the Agent Kakao inbound watcher + event client (Phase 2).

No real KakaoTalk DB, browser, or network. A fake reader feeds messages and a
fake client/transport captures submissions. These lock the watcher's safety
contract: disabled-by-default, configured-rooms-only, startup priming,
dedup by scope+log_id, submit-only-after-server-verdict, and no raw text/secret
leakage into logs.
"""

from datetime import datetime, timezone

import pytest

from rider_crawl.kakao_db import KakaoMessageRef, KakaoRoomRef
from rider_agent.registration import TransportError
from rider_agent.secure_store import AgentIdentity
from rider_agent.kakao_inbound import (
    HEALTH_ACTIVE,
    HEALTH_DEGRADED,
    HEALTH_DISABLED,
    HEALTH_WARNING,
    REASON_FEATURE_DISABLED,
    REASON_LATEST_WINDOW_1,
    REASON_ROOM_NOT_FOUND,
    InboundEventResult,
    KakaoInboundClient,
    KakaoInboundConfig,
    KakaoInboundSubmitError,
    KakaoInboundWatcher,
    KakaoRoomConfig,
    user_hash_digest,
)

FIXED_NOW = datetime(2026, 7, 1, 10, 12, 32, tzinfo=timezone.utc)


# --- fakes ----------------------------------------------------------------

class FakeReader:
    def __init__(self, rooms, messages_by_id, *, window=1):
        self.latest_window_size = window
        self._rooms = rooms
        self._by_id = messages_by_id
        self.closed = False

    def list_rooms(self):
        return list(self._rooms)

    def latest_messages(self, room, limit):
        cap = min(limit, self.latest_window_size)
        return list(self._by_id.get(room.chat_id, []))[:cap]

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, result=None, error=None):
        self.events = []
        self._result = result if result is not None else InboundEventResult(accepted=True)
        self._error = error

    def submit(self, event):
        self.events.append(event)
        if self._error is not None:
            raise self._error
        return self._result


class FakeTransport:
    def __init__(self, responses=None, errors=None):
        self.calls = []
        self._responses = list(responses or [])
        self._errors = list(errors or [])

    def post_json(self, url, body, *, headers=None):
        self.calls.append({"url": url, "body": body, "headers": headers})
        if self._errors:
            err = self._errors.pop(0)
            if err is not None:
                raise err
        if self._responses:
            return self._responses.pop(0)
        return {"accepted": True}


def _identity():
    return AgentIdentity(agent_id="agent-1", agent_token="secret-token", tenant_scope={}, config_version="1")


def _room(chat_id="111", name="운영방", chat_type="MultiChat"):
    return KakaoRoomRef(chat_id=chat_id, room_name=name, chat_type=chat_type)


def _msg(chat_id="111", name="운영방", log_id="1001", timestamp=50, text="!!강민기1234"):
    return KakaoMessageRef(chat_id=chat_id, room_name=name, log_id=log_id, timestamp=timestamp, text=text)


def _watcher(tmp_path, *, by_id, rooms=None, client=None, config=None, window=1, log=None):
    rooms = rooms if rooms is not None else [_room()]
    config = config or KakaoInboundConfig(
        enabled=True,
        rooms=(KakaoRoomConfig(room_name="운영방"),),
        user_hash_digest="sha256:abc",
    )
    return KakaoInboundWatcher(
        config=config,
        reader_factory=lambda: FakeReader(rooms, by_id, window=window),
        client=client or FakeClient(),
        state_path=tmp_path / "kakao_inbound_state.json",
        now=lambda: FIXED_NOW,
        log=log,
    )


# --- watcher: gating & priming -------------------------------------------

def test_disabled_by_default_does_not_scan(tmp_path):
    client = FakeClient()
    config = KakaoInboundConfig(rooms=(KakaoRoomConfig(room_name="운영방"),))  # enabled defaults False
    watcher = _watcher(tmp_path, by_id={"111": [_msg()]}, client=client, config=config)

    report = watcher.scan_once()

    assert report.health == HEALTH_DISABLED
    assert report.reason == REASON_FEATURE_DISABLED
    assert client.events == []


def test_first_scan_primes_high_water_without_processing(tmp_path):
    client = FakeClient()
    watcher = _watcher(tmp_path, by_id={"111": [_msg(log_id="1001")]}, client=client)

    report = watcher.scan_once()

    assert report.primed == 1
    assert report.submitted == 0
    assert client.events == []
    # state persisted so a restart does not replay it
    assert (tmp_path / "kakao_inbound_state.json").exists()


def test_first_latest_n_scan_primes_to_newest_without_processing(tmp_path):
    client = FakeClient()
    watcher = _watcher(
        tmp_path,
        by_id={
            "111": [
                _msg(log_id="1001", text="!!강민기1234"),
                _msg(log_id="1002", text="!!이순신5678"),
            ]
        },
        client=client,
        window=20,
    )

    report = watcher.scan_once()

    assert report.primed == 1
    assert report.submitted == 0
    assert client.events == []
    assert watcher._high_water["111"] == 1002


def test_processes_new_command_after_prime(tmp_path):
    client = FakeClient(InboundEventResult(accepted=True, job_id="job-1"))
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=client)

    watcher.scan_once()  # prime at 1001
    by_id["111"] = [_msg(log_id="1002", text="확인 !!강민기1234")]
    report = watcher.scan_once()

    assert report.submitted == 1
    assert len(client.events) == 1
    event = client.events[0]
    assert event["source"] == "pc_kakao_db"
    assert event["kakao_user_hash_digest"] == "sha256:abc"
    assert event["chat_id"] == "111"
    assert event["room_name"] == "운영방"
    assert event["last_log_id"] == "1002"
    assert event["detected_at"] == "2026-07-01T10:12:32+00:00"
    assert event["command"] == {
        "type": "RIDER_CANCEL_RATE_LOOKUP",
        "name": "강민기",
        "phone_last4": "1234",
    }


def test_latest_n_processes_new_messages_oldest_to_newest(tmp_path):
    client = FakeClient(InboundEventResult(accepted=True))
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=client, window=20)

    watcher.scan_once()  # prime at 1001
    by_id["111"] = [
        _msg(log_id="1002", text="!!강민기1234"),
        _msg(log_id="1003", text="!!이순신5678"),
    ]
    report = watcher.scan_once()

    assert report.submitted == 2
    assert [event["last_log_id"] for event in client.events] == ["1002", "1003"]
    assert watcher._high_water["111"] == 1003


def test_latest_n_gap_primes_to_newest_without_flooding(tmp_path):
    client = FakeClient(InboundEventResult(accepted=True))
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=client, window=20)

    watcher.scan_once()  # prime at 1001
    by_id["111"] = [
        _msg(log_id=str(log_id), text="!!강민기1234")
        for log_id in range(1025, 1045)
    ]
    report = watcher.scan_once()

    assert report.gap_possible == 1
    assert report.submitted == 0
    assert client.events == []
    assert watcher._high_water["111"] == 1044


def test_dedupes_same_log_id(tmp_path):
    client = FakeClient()
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=client)

    watcher.scan_once()  # prime 1001
    by_id["111"] = [_msg(log_id="1002")]
    watcher.scan_once()  # process 1002
    report = watcher.scan_once()  # same 1002 still latest -> skip

    assert report.submitted == 0
    assert len(client.events) == 1


def test_only_scans_configured_rooms(tmp_path):
    client = FakeClient()
    rooms = [_room("111", "운영방"), _room("222", "다른방")]
    by_id = {
        "111": [_msg("111", "운영방", log_id="1001")],
        "222": [_msg("222", "다른방", log_id="2001", text="!!이순신5678")],
    }
    watcher = _watcher(tmp_path, by_id=by_id, rooms=rooms, client=client)

    watcher.scan_once()  # prime configured room only
    # advance both rooms; only the configured room should be processed
    by_id["111"] = [_msg("111", "운영방", log_id="1002")]
    by_id["222"] = [_msg("222", "다른방", log_id="2002", text="!!이순신5678")]
    report = watcher.scan_once()

    assert report.submitted == 1
    assert [e["chat_id"] for e in client.events] == ["111"]


def test_parser_miss_advances_without_submitting(tmp_path):
    client = FakeClient()
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=client)

    watcher.scan_once()  # prime
    # "!!" prefilter matched but token is invalid
    by_id["111"] = [_msg(log_id="1002", text="메모 !! 확인")]
    report = watcher.scan_once()

    assert report.parser_misses == 1
    assert report.submitted == 0
    assert client.events == []


# --- watcher: server verdict handling ------------------------------------

def test_submit_error_does_not_advance_and_retries_next_scan(tmp_path):
    failing = FakeClient(error=KakaoInboundSubmitError("boom"))
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=failing)

    watcher.scan_once()  # prime
    by_id["111"] = [_msg(log_id="1002")]
    report = watcher.scan_once()

    assert report.submit_errors == 1
    assert report.submitted == 0
    # high-water NOT advanced: a fresh working client gets the same message
    working = FakeClient(InboundEventResult(accepted=True))
    watcher._client = working
    report2 = watcher.scan_once()
    assert report2.submitted == 1
    assert len(working.events) == 1


def test_server_rejection_is_terminal_and_advances(tmp_path):
    rejecting = FakeClient(InboundEventResult(accepted=False, reason="unknown_room"))
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=rejecting)

    watcher.scan_once()  # prime
    by_id["111"] = [_msg(log_id="1002")]
    report = watcher.scan_once()

    assert report.rejected == 1
    assert report.submitted == 0
    # rejected message is not re-submitted on the next scan (terminal verdict)
    report2 = watcher.scan_once()
    assert report2.rejected == 0
    assert len(rejecting.events) == 1


# --- watcher: privacy & health -------------------------------------------

def test_does_not_log_raw_text_name_or_phone(tmp_path):
    logs = []
    failing = FakeClient(error=KakaoInboundSubmitError("boom"))
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=failing, log=logs.append)

    watcher.scan_once()  # prime
    by_id["111"] = [_msg(log_id="1002", text="!!강민기1234")]
    watcher.scan_once()  # submit error -> logs an error event

    joined = "\n".join(logs)
    assert "강민기" not in joined
    assert "1234" not in joined
    assert "!!강민기1234" not in joined


def test_health_is_degraded_for_latest_window_one(tmp_path):
    watcher = _watcher(tmp_path, by_id={"111": [_msg()]}, window=1)

    report = watcher.scan_once()

    assert report.health == HEALTH_DEGRADED
    assert report.reason == REASON_LATEST_WINDOW_1
    assert watcher.health()["latest_window_size"] == 1


def test_missing_configured_room_warns_when_not_in_fallback(tmp_path):
    # With a latest-N reader (window>1) a missing room surfaces as a warning;
    # the fallback (window==1) would otherwise mask it as degraded.
    rooms = [_room("999", "다른방")]
    watcher = _watcher(tmp_path, by_id={}, rooms=rooms, window=20)

    report = watcher.scan_once()

    assert report.missing_rooms == 1
    assert report.health == HEALTH_WARNING
    assert report.reason == REASON_ROOM_NOT_FOUND


def test_state_persists_across_watcher_instances(tmp_path):
    by_id = {"111": [_msg(log_id="1001")]}
    first = _watcher(tmp_path, by_id=by_id, client=FakeClient())
    first.scan_once()  # prime 1001 and persist

    client = FakeClient()
    second = _watcher(tmp_path, by_id=by_id, client=client)
    report = second.scan_once()  # same 1001 -> already primed, no submit

    assert report.submitted == 0
    assert report.primed == 0
    assert client.events == []


# --- client ---------------------------------------------------------------

def test_client_submits_with_bearer_and_parses_result():
    transport = FakeTransport(responses=[{"accepted": True, "duplicate": False, "job_id": "job-9"}])
    client = KakaoInboundClient(_identity(), transport=transport, base_url="https://srv")

    result = client.submit({"source": "pc_kakao_db"})

    assert result.accepted is True
    assert result.job_id == "job-9"
    call = transport.calls[0]
    assert call["url"] == "https://srv/v1/kakao/inbound-events"
    assert call["headers"]["Authorization"] == "Bearer secret-token"


def test_client_retries_transient_then_succeeds():
    sleeps = []
    transport = FakeTransport(
        responses=[{"accepted": True}],
        errors=[TransportError("net"), TransportError("net"), None],
    )
    client = KakaoInboundClient(
        _identity(), transport=transport, base_url="https://srv",
        max_attempts=3, sleep=sleeps.append,
    )

    result = client.submit({"x": 1})

    assert result.accepted is True
    assert len(transport.calls) == 3
    assert len(sleeps) == 2


def test_client_does_not_retry_auth_error():
    transport = FakeTransport(errors=[TransportError("auth", status_code=401)])
    client = KakaoInboundClient(_identity(), transport=transport, base_url="https://srv")

    with pytest.raises(KakaoInboundSubmitError):
        client.submit({"x": 1})

    assert len(transport.calls) == 1


def test_client_business_rejection_is_not_an_error():
    transport = FakeTransport(responses=[{"accepted": False, "reason": "unknown_room"}])
    client = KakaoInboundClient(_identity(), transport=transport, base_url="https://srv")

    result = client.submit({"x": 1})

    assert result.accepted is False
    assert result.reason == "unknown_room"


def test_user_hash_digest_is_prefixed_sha256():
    digest = user_hash_digest("local-user-hash")

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    assert "local-user-hash" not in digest
