"""Tests for the Agent Kakao inbound watcher + event client (Phase 2).

No real KakaoTalk DB, browser, or network. A fake reader feeds messages and a
fake client/transport captures submissions. These lock the watcher's safety
contract: disabled-by-default, configured-rooms-only, startup priming,
dedup by scope+log_id, submit-only-after-server-verdict, and no raw text/secret
leakage into logs.
"""

import json
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
    REASON_EMPTY_WATCHLIST,
    REASON_DB_UNAVAILABLE,
    REASON_FEATURE_DISABLED,
    REASON_LATEST_WINDOW_1,
    REASON_NON_INTERACTIVE,
    REASON_OK,
    REASON_PREREQUISITES_MISSING,
    REASON_ROOM_NOT_FOUND,
    resolve_kakao_inbound_enabled,
    resolve_kakao_inbound_rooms,
    InboundEventResult,
    KakaoInboundClient,
    KakaoInboundConfig,
    KakaoInboundSubmitError,
    KakaoInboundWatcher,
    RefreshingKakaoInboundWatcher,
    KakaoRoomConfig,
    KAKAO_DB_KEY_REF,
    KAKAO_USER_HASH_REF,
    KakaoWatchlist,
    KakaoWatchlistClient,
    LocalKakaoInboundSettings,
    ScanReport,
    _parse_watchlist,
    build_kakao_inbound_watcher,
    build_kakao_inbound_watcher_from_sources,
    load_local_kakao_inbound_settings,
    make_kakao_reader_factory,
    static_kakao_inbound_health,
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

    def get_json(self, url, *, headers=None):
        self.calls.append({"url": url, "body": None, "headers": headers})
        if self._errors:
            err = self._errors.pop(0)
            if err is not None:
                raise err
        if self._responses:
            return self._responses.pop(0)
        return {"kakao_inbound": {"enabled": False, "config_version": "", "rooms": []}}


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


def test_server_rejection_logs_safe_verdict_without_command_text(tmp_path):
    logs: list[str] = []
    rejecting = FakeClient(InboundEventResult(accepted=False, reason="unknown_room"))
    by_id = {"111": [_msg(log_id="1001")]}
    watcher = _watcher(tmp_path, by_id=by_id, client=rejecting, log=logs.append)

    watcher.scan_once()  # prime
    by_id["111"] = [_msg(log_id="1002", text="!!강민기1234")]
    report = watcher.scan_once()

    joined = " ".join(logs)
    assert report.rejected == 1
    assert "AGENT_KAKAO_INBOUND_VERDICT" in joined
    assert "unknown_room" in joined
    assert "1002" in joined
    assert "!!강민기1234" not in joined
    assert "강민기1234" not in joined


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


def test_static_kakao_inbound_health_uses_fixed_safe_keys():
    source = static_kakao_inbound_health(
        HEALTH_DISABLED,
        REASON_DB_UNAVAILABLE,
        latest_window_size=20,
        configured_missing_count=1,
        room_name="raw-room",
        message="!!raw1234",
        db_path="C:/Users/raw/chatListInfo.edb",
        db_key="secret",
        user_hash="rawhash",
        phone_last4="1234",
    )

    health = source.health()

    assert health == {
        "state": HEALTH_DISABLED,
        "reason": REASON_DB_UNAVAILABLE,
        "latest_window_size": 20,
        "configured_missing_count": 1,
    }


# --- build_kakao_inbound_watcher: assembly --------------------------------

def test_build_kakao_inbound_watcher_assembles_and_submits_via_transport(tmp_path):
    transport = FakeTransport()
    msgs = {"111": [_msg(log_id="1001")]}
    config = KakaoInboundConfig(
        enabled=True,
        rooms=(KakaoRoomConfig(room_name="운영방", chat_id="111"),),
        user_hash_digest="sha256:abc",
    )
    watcher = build_kakao_inbound_watcher(
        identity=_identity(),
        transport=transport,
        base_url="https://srv",
        config=config,
        reader_factory=lambda: FakeReader([_room()], msgs, window=20),
        state_path=tmp_path / "kakao_inbound_state.json",
        now=lambda: FIXED_NOW,
    )

    assert isinstance(watcher, KakaoInboundWatcher)
    # first scan primes the high-water without processing
    assert watcher.scan_once().primed == 1
    # a newer message flows through the assembled client's injected transport
    msgs["111"] = [_msg(log_id="1001"), _msg(log_id="2000")]
    report = watcher.scan_once()

    assert report.submitted == 1
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://srv/v1/kakao/inbound-events"
    assert call["headers"]["Authorization"] == "Bearer secret-token"


def test_build_kakao_inbound_watcher_respects_disabled_config(tmp_path):
    transport = FakeTransport()
    config = KakaoInboundConfig(  # enabled defaults False
        rooms=(KakaoRoomConfig(room_name="운영방", chat_id="111"),),
        user_hash_digest="sha256:abc",
    )
    watcher = build_kakao_inbound_watcher(
        identity=_identity(),
        transport=transport,
        config=config,
        reader_factory=lambda: FakeReader([_room()], {"111": [_msg()]}, window=20),
        state_path=tmp_path / "state.json",
        now=lambda: FIXED_NOW,
    )
    report = watcher.scan_once()

    assert report.health == HEALTH_DISABLED
    assert transport.calls == []  # disabled → no server call


# --- KakaoWatchlistClient: server watchlist fetch -------------------------

def test_watchlist_client_fetches_and_parses_non_secret_rooms():
    transport = FakeTransport(responses=[{
        "kakao_inbound": {
            "enabled": True,
            "config_version": "sha256:xy",
            "rooms": [
                {"room_name": "운영방", "chat_id": "111"},
                {"room_name": "상담방", "chat_id": ""},
            ],
        }
    }])
    client = KakaoWatchlistClient(_identity(), transport=transport, base_url="https://srv")

    watchlist = client.fetch()

    assert watchlist.enabled is True
    assert watchlist.config_version == "sha256:xy"
    assert watchlist.rooms == (
        KakaoRoomConfig(room_name="운영방", chat_id="111"),
        KakaoRoomConfig(room_name="상담방", chat_id=""),
    )
    call = transport.calls[0]
    assert call["url"] == "https://srv/v1/agents/kakao-inbound-config"
    assert call["headers"]["Authorization"] == "Bearer secret-token"


def test_watchlist_client_returns_none_on_auth_error():
    transport = FakeTransport(errors=[TransportError("nope", status_code=401)])
    logs: list[str] = []
    client = KakaoWatchlistClient(
        _identity(), transport=transport, base_url="https://srv", log=logs.append
    )

    assert client.fetch() is None
    assert len(transport.calls) == 1  # auth failure is not retried


def test_watchlist_client_retries_then_returns_none():
    transport = FakeTransport(errors=[
        TransportError("x", status_code=500),
        TransportError("x", status_code=500),
        TransportError("x", status_code=500),
    ])
    slept: list[float] = []
    client = KakaoWatchlistClient(
        _identity(), transport=transport, base_url="https://srv", sleep=slept.append
    )

    assert client.fetch() is None
    assert len(transport.calls) == 3  # exhausted bounded retries
    assert slept  # backed off between attempts


def test_parse_watchlist_tolerates_missing_and_bad_shapes():
    assert _parse_watchlist({}) == KakaoWatchlist(enabled=False, config_version="", rooms=())
    assert _parse_watchlist({"kakao_inbound": {"rooms": "bad"}}).rooms == ()
    # rooms without a room_name are dropped
    watchlist = _parse_watchlist(
        {"kakao_inbound": {"enabled": True, "rooms": [{"chat_id": "9"}, {"room_name": "방"}]}}
    )
    assert watchlist.rooms == (KakaoRoomConfig(room_name="방", chat_id=""),)


# --- Hybrid effective-enabled gate + rooms merge --------------------------

def _gate(**overrides):
    kwargs = dict(
        local_enabled=True,
        prerequisites_ok=True,
        session_interactive=True,
        watchlist_enabled=True,
        watchlist_has_rooms=True,
    )
    kwargs.update(overrides)
    return resolve_kakao_inbound_enabled(**kwargs)


def test_gate_enabled_when_all_conditions_met():
    assert _gate() == (True, REASON_OK)


def test_gate_local_kill_switch_wins():
    assert _gate(local_enabled=False) == (False, REASON_FEATURE_DISABLED)


def test_gate_requires_interactive_session():
    assert _gate(session_interactive=False) == (False, REASON_NON_INTERACTIVE)


def test_gate_requires_local_prerequisites():
    assert _gate(prerequisites_ok=False) == (False, REASON_PREREQUISITES_MISSING)


def test_gate_requires_non_empty_server_watchlist():
    assert _gate(watchlist_enabled=False) == (False, REASON_EMPTY_WATCHLIST)
    assert _gate(watchlist_has_rooms=False) == (False, REASON_EMPTY_WATCHLIST)


def test_rooms_prefer_server_watchlist_over_local_fallback():
    watchlist = KakaoWatchlist(
        enabled=True, config_version="v",
        rooms=(KakaoRoomConfig(room_name="서버방", chat_id="1"),),
    )
    rooms = resolve_kakao_inbound_rooms(
        watchlist=watchlist, fallback_rooms=(KakaoRoomConfig(room_name="로컬방"),)
    )
    assert rooms == (KakaoRoomConfig(room_name="서버방", chat_id="1"),)


def test_rooms_fall_back_to_local_when_watchlist_unavailable():
    local = (KakaoRoomConfig(room_name="로컬방"),)
    assert resolve_kakao_inbound_rooms(watchlist=None, fallback_rooms=local) == local


def test_rooms_do_not_fall_back_when_server_watchlist_is_empty():
    local = (KakaoRoomConfig(room_name="local-room"),)
    empty = KakaoWatchlist(enabled=True, config_version="v", rooms=())
    assert resolve_kakao_inbound_rooms(watchlist=empty, fallback_rooms=local) == ()


# --- local settings loader + reader factory -------------------------------

def test_load_local_settings_missing_file_is_disabled(tmp_path):
    settings = load_local_kakao_inbound_settings(tmp_path / "nope.json")
    assert settings == LocalKakaoInboundSettings()
    assert settings.enabled is False


def test_load_local_settings_parses_json(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({
        "enabled": True,
        "chat_list_db_path": "C:/k/chatListInfo.edb",
        "chat_logs_dir": "C:/k",
        "use_chat_logs": True,
        "latest_messages_limit": 20,
        "accepted_chat_types": ["DirectChat", "MultiChat", "OM"],
        "rooms": [{"room_name": "운영방", "chat_id": "111"}, {"chat_id": "no-name"}],
    }), encoding="utf-8")

    settings = load_local_kakao_inbound_settings(path)

    assert settings.enabled is True
    assert settings.chat_list_db_path == "C:/k/chatListInfo.edb"
    assert settings.use_chat_logs is True
    assert settings.accepted_chat_types == ("DirectChat", "MultiChat", "OM")
    # rooms without a room_name are dropped
    assert settings.fallback_rooms == (KakaoRoomConfig(room_name="운영방", chat_id="111"),)


def test_load_local_settings_bad_json_is_disabled(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_local_kakao_inbound_settings(path).enabled is False


def test_make_reader_factory_selects_chatlogs_or_latest_one():
    chatlogs = make_kakao_reader_factory(
        chat_list_db_path="a.edb", chat_list_db_key="KEY", chat_logs_dir="d", use_chat_logs=True
    )()
    assert chatlogs.latest_window_size == 20  # ChatLogsReader (latest-N)

    latest_one = make_kakao_reader_factory(
        chat_list_db_path="a.edb", chat_list_db_key="KEY", use_chat_logs=False
    )()
    assert latest_one.latest_window_size == 1  # ChatRoomListReader (latest-one)


# --- gate + build integration (build_kakao_inbound_watcher_from_sources) ---

def _from_sources(**overrides):
    secrets = {KAKAO_DB_KEY_REF: "KEY", KAKAO_USER_HASH_REF: "rawhash"}
    kwargs = dict(
        identity=_identity(),
        transport=FakeTransport(),
        base_url="https://s",
        settings=LocalKakaoInboundSettings(enabled=True, chat_list_db_path="a.edb"),
        secret_resolver=lambda ref: secrets.get(ref),
        session_interactive=True,
        state_path="s.json",
        watchlist=KakaoWatchlist(
            enabled=True, config_version="v", rooms=(KakaoRoomConfig("서버방", "1"),)
        ),
    )
    kwargs.update(overrides)
    return build_kakao_inbound_watcher_from_sources(**kwargs)


def test_from_sources_disabled_by_local_kill_switch():
    watcher, reason = _from_sources(
        settings=LocalKakaoInboundSettings(enabled=False, chat_list_db_path="a.edb")
    )
    assert watcher is None
    assert reason == REASON_FEATURE_DISABLED


def test_from_sources_disabled_when_secrets_missing():
    watcher, reason = _from_sources(secret_resolver=lambda ref: None)
    assert watcher is None
    assert reason == REASON_PREREQUISITES_MISSING


def test_from_sources_disabled_when_non_interactive():
    watcher, reason = _from_sources(session_interactive=False)
    assert watcher is None
    assert reason == REASON_NON_INTERACTIVE


def test_from_sources_builds_watcher_with_digest_and_server_rooms():
    watcher, reason = _from_sources()
    assert reason == REASON_OK
    assert isinstance(watcher, KakaoInboundWatcher)
    # only the digest of the raw user hash reaches config
    assert watcher._config.user_hash_digest == user_hash_digest("rawhash")
    assert watcher._config.rooms == (KakaoRoomConfig("서버방", "1"),)


def test_from_sources_passes_local_accepted_chat_types_to_watcher():
    watcher, reason = _from_sources(
        settings=LocalKakaoInboundSettings(
            enabled=True,
            chat_list_db_path="a.edb",
            accepted_chat_types=("DirectChat", "MultiChat", "OM"),
        )
    )

    assert reason == REASON_OK
    assert watcher._config.accepted_chat_types == ("DirectChat", "MultiChat", "OM")


def test_from_sources_uses_local_fallback_when_no_server_watchlist():
    watcher, reason = _from_sources(
        settings=LocalKakaoInboundSettings(
            enabled=True, chat_list_db_path="a.edb",
            fallback_rooms=(KakaoRoomConfig("카나리방", "9"),),
        ),
        watchlist=None,  # server unreachable -> canary fallback rooms
    )
    assert reason == REASON_OK
    assert watcher._config.rooms == (KakaoRoomConfig("카나리방", "9"),)


def test_from_sources_disabled_when_server_watchlist_empty_even_with_local_fallback():
    watcher, reason = _from_sources(
        settings=LocalKakaoInboundSettings(
            enabled=True,
            chat_list_db_path="a.edb",
            fallback_rooms=(KakaoRoomConfig("local-room", "9"),),
        ),
        watchlist=KakaoWatchlist(enabled=True, config_version="v", rooms=()),
    )
    assert watcher is None
    assert reason == REASON_EMPTY_WATCHLIST


def test_from_sources_disabled_when_no_rooms_anywhere():
    watcher, reason = _from_sources(watchlist=None)  # no server rooms, no fallback
    assert watcher is None
    assert reason == REASON_EMPTY_WATCHLIST


class _FakeWatchlistClient:
    def __init__(self, values):
        self._values = list(values)
        self.fetches = 0

    def fetch(self):
        self.fetches += 1
        if self._values:
            return self._values.pop(0)
        return None


class _ScanOnlyWatcher:
    def __init__(self):
        self.scans = 0

    def scan_once(self):
        self.scans += 1
        return ScanReport(health=HEALTH_ACTIVE, reason=REASON_OK, rooms_scanned=1)

    def health(self):
        return {"state": HEALTH_ACTIVE, "reason": REASON_OK}


def _refreshing_watcher(client, builder):
    return RefreshingKakaoInboundWatcher(
        identity=_identity(),
        transport=FakeTransport(),
        base_url="https://s",
        settings=LocalKakaoInboundSettings(enabled=True, chat_list_db_path="a.edb"),
        secret_resolver=lambda ref: {
            KAKAO_DB_KEY_REF: "KEY",
            KAKAO_USER_HASH_REF: "rawhash",
        }.get(ref),
        session_probe=lambda: True,
        state_path="s.json",
        watchlist_client=client,
        builder=builder,
        refresh_interval_seconds=0,
    )


def test_refreshing_watcher_rebuilds_when_config_version_changes():
    active = _ScanOnlyWatcher()
    seen_versions = []

    def builder(**kwargs):
        watchlist = kwargs["watchlist"]
        seen_versions.append(watchlist.config_version if watchlist is not None else None)
        if watchlist is not None and watchlist.rooms:
            return active, REASON_OK
        return None, REASON_EMPTY_WATCHLIST

    client = _FakeWatchlistClient([
        KakaoWatchlist(enabled=True, config_version="v1", rooms=()),
        KakaoWatchlist(
            enabled=True,
            config_version="v2",
            rooms=(KakaoRoomConfig("server-room", "1"),),
        ),
    ])
    watcher = _refreshing_watcher(client, builder)

    assert watcher.scan_once() == ScanReport(
        health=HEALTH_DISABLED, reason=REASON_EMPTY_WATCHLIST
    )
    assert watcher.scan_once().rooms_scanned == 1
    assert seen_versions == ["v1", "v2"]
    assert active.scans == 1


def test_refreshing_watcher_reuses_inner_when_config_version_unchanged():
    active = _ScanOnlyWatcher()
    builds = 0

    def builder(**kwargs):
        nonlocal builds
        builds += 1
        return active, REASON_OK

    watchlist = KakaoWatchlist(
        enabled=True,
        config_version="same",
        rooms=(KakaoRoomConfig("server-room", "1"),),
    )
    watcher = _refreshing_watcher(
        _FakeWatchlistClient([watchlist, watchlist]),
        builder,
    )

    assert watcher.scan_once().rooms_scanned == 1
    assert watcher.scan_once().rooms_scanned == 1
    assert builds == 1
    assert active.scans == 2


def test_from_sources_never_logs_secrets():
    logs: list[str] = []
    _from_sources(
        secret_resolver=lambda ref: {
            KAKAO_DB_KEY_REF: "SUPERSECRETKEY", KAKAO_USER_HASH_REF: "rawuserhash"
        }.get(ref),
        log=logs.append,
    )
    blob = " ".join(logs)
    assert "SUPERSECRETKEY" not in blob
    assert "rawuserhash" not in blob
