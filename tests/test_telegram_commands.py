import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from rider_crawl.config import AppConfig, app_state_root
from rider_crawl.keyword_responder import KeywordResponder
from rider_crawl.parser import parse_baemin_delivery_history_html
from rider_crawl.telegram_commands import (
    TelegramUpdatePoller,
    TelegramCommandProcessor,
    _default_offset_store_path,
    calculate_cancel_rate,
    find_rider_cancel_stats,
    parse_rider_lookup_command,
    render_rider_cancel_reply,
)


def test_parse_rider_lookup_command_extracts_name_and_phone_last4():
    command = parse_rider_lookup_command("!홍길동1234")

    assert command is not None
    assert command.name == "홍길동"
    assert command.phone_last4 == "1234"


def test_parse_rider_lookup_command_ignores_non_lookup_text():
    assert parse_rider_lookup_command("홍길동1234") is None
    assert parse_rider_lookup_command("!1234") is None
    assert parse_rider_lookup_command("!홍길동12") is None


def test_find_rider_cancel_stats_matches_name_and_phone_last4():
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>오전피크</th><th>오후논피크</th><th>저녁피크</th><th>야간논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>100</td><td>5</td><td>3</td><td>2</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-9999</td><td>100</td><td>5</td><td>9</td><td>9</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """
    table = parse_baemin_delivery_history_html(html)

    matches = find_rider_cancel_stats(table.riders, name="홍길동", phone_last4="1234")

    assert len(matches) == 1
    assert matches[0].name == "홍길동"
    assert matches[0].phone_last4 == "1234"
    assert matches[0].completed_count == 100
    assert matches[0].rejected_count == 5
    assert matches[0].dispatch_cancel_count == 3
    assert matches[0].rider_fault_cancel_count == 2
    assert matches[0].total_cancel_count == 5


def test_calculate_cancel_rate_uses_completed_rejected_and_cancels():
    assert calculate_cancel_rate(completed=100, rejected=20, total_cancelled=5) == 4.0
    assert calculate_cancel_rate(completed=0, rejected=0, total_cancelled=0) == 0


def test_render_rider_cancel_reply_marks_risky_rate():
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>오전피크</th><th>오후논피크</th><th>저녁피크</th><th>야간논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>100</td><td>20</td><td>3</td><td>2</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """
    stats = find_rider_cancel_stats(parse_baemin_delivery_history_html(html).riders, name="홍길동", phone_last4="1234")[0]

    assert render_rider_cancel_reply(stats) == "홍길동1234\n취소율 4%, 취소 5개\n위험합니다."


def test_telegram_command_processor_routes_lookup_to_matching_chat_config(tmp_path):
    first_html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>오전피크</th><th>오후논피크</th><th>저녁피크</th><th>야간논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>100</td><td>20</td><td>3</td><td>2</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """
    second_html = first_html.replace("<td>100</td><td>20</td><td>3</td><td>2</td>", "<td>50</td><td>0</td><td>1</td><td>1</td>")
    configs = [
        _config(tmp_path, crawl_name="크롤링1", chat_id="-100111"),
        _config(tmp_path, crawl_name="크롤링2", chat_id="-100222"),
    ]
    html_by_name = {"크롤링1": first_html, "크롤링2": second_html}
    fetched: list[str] = []
    sent: list[tuple[str, str, int | None]] = []
    processor = TelegramCommandProcessor(
        configs,
        fetch_html=lambda config: fetched.append(config.crawl_name) or html_by_name[config.crawl_name],
        send_text=lambda config, message, *, message_thread_id=None: sent.append(
            (config.telegram_chat_id, message, message_thread_id)
        ),
    )

    handled = processor.handle_text("-100222", "!홍길동1234")

    assert handled is True
    assert fetched == ["크롤링2"]
    assert sent[0] == ("-100222", "조회 중입니다.", None)
    assert sent[1] == ("-100222", "홍길동1234\n취소율 3.8%, 취소 2개\n정상 범위입니다.", None)


def test_telegram_command_processor_routes_lookup_to_matching_chat_thread(tmp_path):
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>오전피크</th><th>오후논피크</th><th>저녁피크</th><th>야간논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>50</td><td>0</td><td>1</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """
    configs = [
        _config(tmp_path, crawl_name="크롤링1", chat_id="-100123", message_thread_id="77"),
        _config(tmp_path, crawl_name="크롤링2", chat_id="-100123", message_thread_id="88"),
    ]
    fetched: list[str] = []
    sent: list[tuple[str, str, int | None]] = []
    processor = TelegramCommandProcessor(
        configs,
        fetch_html=lambda config: fetched.append(config.crawl_name) or html,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(
            (config.crawl_name, message, message_thread_id)
        ),
    )

    handled = processor.handle_text("-100123", "!홍길동1234", message_thread_id=88)

    assert handled is True
    assert fetched == ["크롤링2"]
    assert sent[0] == ("크롤링2", "조회 중입니다.", 88)
    assert sent[1] == ("크롤링2", "홍길동1234\n취소율 3.8%, 취소 2개\n정상 범위입니다.", 88)


def test_telegram_command_processor_normalizes_configured_thread_id(tmp_path):
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>오전피크</th><th>오후논피크</th><th>저녁피크</th><th>야간논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>50</td><td>0</td><td>1</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """
    sent: list[tuple[str, int | None]] = []
    processor = TelegramCommandProcessor(
        [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123", message_thread_id="077")],
        fetch_html=lambda _config: html,
        send_text=lambda _config, message, *, message_thread_id=None: sent.append((message, message_thread_id)),
    )

    handled = processor.handle_text("-100123", "!홍길동1234", message_thread_id=77)

    assert handled is True
    assert sent[0] == ("조회 중입니다.", 77)


def test_telegram_command_processor_logs_unmatched_chat_without_replying(tmp_path):
    logs: list[str] = []
    sent: list[str] = []
    processor = TelegramCommandProcessor(
        [_config(tmp_path, crawl_name="크롤링1", chat_id="-100111")],
        fetch_html=lambda _config: "",
        send_text=lambda _config, message: sent.append(message),
        log_event=logs.append,
    )

    handled = processor.handle_text("-100999", "!홍길동1234")

    assert handled is False
    assert sent == []
    assert logs == ["텔레그램 대상 미매칭: -100999"]


def test_telegram_command_processor_logs_unmatched_thread_without_replying(tmp_path):
    logs: list[str] = []
    sent: list[str] = []
    processor = TelegramCommandProcessor(
        [_config(tmp_path, crawl_name="크롤링1", chat_id="-100111", message_thread_id="77")],
        fetch_html=lambda _config: "",
        send_text=lambda _config, message, **_kwargs: sent.append(message),
        log_event=logs.append,
    )

    handled = processor.handle_text("-100111", "!홍길동1234", message_thread_id=88)

    assert handled is False
    assert sent == []
    assert logs == ["텔레그램 대상 미매칭: -100111/88"]


def test_telegram_command_processor_rejects_duplicate_targets(tmp_path):
    configs = [
        _config(tmp_path, crawl_name="크롤링1", chat_id="-100123", message_thread_id="077"),
        _config(tmp_path, crawl_name="크롤링2", chat_id="-100123", message_thread_id="77"),
    ]

    with pytest.raises(ValueError, match="텔레그램 대상이 중복"):
        TelegramCommandProcessor(configs)


def test_telegram_update_poller_routes_matching_chat_text_and_advances_offset(tmp_path):
    handled: list[tuple[str, str, int | None]] = []
    requested_offsets: list[int | None] = []
    config = _config(tmp_path, crawl_name="크롤링1")

    def fake_get_updates(received_config, *, offset, timeout_seconds):
        assert received_config is config
        requested_offsets.append(offset)
        return [
            {
                "update_id": 10,
                "message": {"chat": {"id": "-100123"}, "message_thread_id": 77, "text": "!홍길동1234"},
            },
            {"update_id": 11, "message": {"chat": {"id": "-100999"}, "text": "!무시0000"}},
        ]

    poller = TelegramUpdatePoller(
        config,
        handle_text=lambda chat_id, text, message_thread_id=None: handled.append((chat_id, text, message_thread_id)),
        get_updates=fake_get_updates,
        offset_store_path=tmp_path / "telegram.offset",
    )

    poller.poll_once()

    assert handled == [("-100123", "!홍길동1234", 77), ("-100999", "!무시0000", None)]
    assert requested_offsets == [None]
    assert poller.next_update_id == 12


def test_telegram_update_poller_does_not_advance_offset_when_handler_fails(tmp_path):
    config = _config(tmp_path, crawl_name="크롤링1")
    calls = 0

    def failing_handle_text(chat_id, text, message_thread_id=None):
        nonlocal calls
        calls += 1
        raise RuntimeError("temporary send failure")

    poller = TelegramUpdatePoller(
        config,
        handle_text=failing_handle_text,
        get_updates=lambda *_args, **_kwargs: [
            {"update_id": 10, "message": {"chat": {"id": "-100123"}, "text": "!홍길동1234"}}
        ],
        offset_store_path=tmp_path / "telegram.offset",
    )

    try:
        poller.poll_once()
    except RuntimeError:
        pass

    assert calls == 1
    assert poller.next_update_id is None


def test_telegram_update_poller_skips_successful_higher_update_after_lower_update_fails(tmp_path):
    config = _config(tmp_path, crawl_name="크롤링1")
    requested_offsets: list[int | None] = []
    handled_chat_ids: list[str] = []
    handle_lock = threading.Lock()
    lower_update_failed = False

    def fake_get_updates(received_config, *, offset, timeout_seconds):
        assert received_config is config
        requested_offsets.append(offset)
        return [
            {"update_id": 10, "message": {"chat": {"id": "-100111"}, "text": "!홍길동1234"}},
            {"update_id": 11, "message": {"chat": {"id": "-100222"}, "text": "!김철수5678"}},
        ]

    def handle_text(chat_id, text, message_thread_id=None):
        nonlocal lower_update_failed
        with handle_lock:
            handled_chat_ids.append(chat_id)
            should_fail = chat_id == "-100111" and not lower_update_failed
            if should_fail:
                lower_update_failed = True
        if should_fail:
            raise RuntimeError("lower update failed")

    poller = TelegramUpdatePoller(
        config,
        handle_text=handle_text,
        get_updates=fake_get_updates,
        offset_store_path=tmp_path / "telegram.offset",
    )

    with pytest.raises(RuntimeError, match="lower update failed"):
        poller.poll_once()

    assert handled_chat_ids.count("-100111") == 1
    assert handled_chat_ids.count("-100222") == 1
    assert poller.next_update_id != 12

    poller.poll_once()

    assert requested_offsets[1] != 12
    assert handled_chat_ids.count("-100111") == 2
    assert handled_chat_ids.count("-100222") == 1
    assert poller.next_update_id == 12


def test_telegram_update_poller_persists_completed_higher_update_when_lower_update_fails(tmp_path):
    config = _config(tmp_path, crawl_name="크롤링1")
    offset_path = tmp_path / "telegram.offset"
    handled_chat_ids: list[str] = []

    def get_updates(*_args, **_kwargs):
        return [
            {"update_id": 10, "message": {"chat": {"id": "-100111"}, "text": "!홍길동1234"}},
            {"update_id": 11, "message": {"chat": {"id": "-100222"}, "text": "!김철수5678"}},
        ]

    def handle_text(chat_id, text, message_thread_id=None):
        handled_chat_ids.append(chat_id)
        if chat_id == "-100111":
            raise RuntimeError("lower update failed")

    poller = TelegramUpdatePoller(
        config,
        handle_text=handle_text,
        get_updates=get_updates,
        offset_store_path=offset_path,
    )

    with pytest.raises(RuntimeError, match="lower update failed"):
        poller.poll_once()

    restarted = TelegramUpdatePoller(
        config,
        handle_text=handle_text,
        get_updates=get_updates,
        offset_store_path=offset_path,
    )
    with pytest.raises(RuntimeError, match="lower update failed"):
        restarted.poll_once()

    assert handled_chat_ids.count("-100111") == 2
    assert handled_chat_ids.count("-100222") == 1


def test_telegram_update_poller_persists_offset_after_successful_handler(tmp_path):
    handled: list[tuple[str, str, int | None]] = []
    offset_path = tmp_path / "telegram.offset"
    config = _config(tmp_path, crawl_name="크롤링1")

    def fake_get_updates(received_config, *, offset, timeout_seconds):
        assert received_config is config
        assert offset is None
        return [{"update_id": 10, "message": {"chat": {"id": "-100123"}, "text": "!홍길동1234"}}]

    poller = TelegramUpdatePoller(
        config,
        handle_text=lambda chat_id, text, message_thread_id=None: handled.append((chat_id, text, message_thread_id)),
        get_updates=fake_get_updates,
        offset_store_path=offset_path,
    )

    poller.poll_once()
    restarted = TelegramUpdatePoller(
        config,
        handle_text=lambda *_args, **_kwargs: None,
        get_updates=lambda *_args, **_kwargs: [],
        offset_store_path=offset_path,
    )

    assert handled == [("-100123", "!홍길동1234", None)]
    assert offset_path.read_text(encoding="utf-8") == "11"
    assert restarted.next_update_id == 11


def test_telegram_update_poller_reloads_offset_after_lock_is_acquired(tmp_path):
    offset_path = tmp_path / "telegram.offset"
    config = _config(tmp_path, crawl_name="크롤링1")
    requested_offsets: list[int | None] = []

    poller = TelegramUpdatePoller(
        config,
        handle_text=lambda *_args, **_kwargs: None,
        get_updates=lambda _config, *, offset, timeout_seconds: requested_offsets.append(offset) or [],
        offset_store_path=offset_path,
    )
    offset_path.write_text("21", encoding="utf-8")

    poller.poll_once()

    assert requested_offsets == [21]
    assert poller.next_update_id == 21


def test_telegram_update_poller_completes_unhandled_lookup_command(tmp_path):
    offset_path = tmp_path / "telegram.offset"
    config = _config(tmp_path, crawl_name="크롤링1")
    handled: list[tuple[str, str]] = []
    poller = TelegramUpdatePoller(
        config,
        handle_text=lambda chat_id, text, message_thread_id=None: handled.append((chat_id, text)) or False,
        get_updates=lambda *_args, **_kwargs: [
            {"update_id": 10, "message": {"chat": {"id": "-100999"}, "text": "!홍길동1234"}}
        ],
        offset_store_path=offset_path,
    )

    poller.poll_once()

    assert handled == [("-100999", "!홍길동1234")]
    assert poller.next_update_id == 11
    assert offset_path.read_text(encoding="utf-8") == "11"


def test_telegram_update_poller_recovers_offset_when_completed_sidecar_exists(tmp_path):
    offset_path = tmp_path / "telegram.offset"
    completed_path = Path(f"{offset_path}.completed.json")
    completed_path.write_text("[10]", encoding="utf-8")
    config = _config(tmp_path, crawl_name="크롤링1")
    handled: list[str] = []

    poller = TelegramUpdatePoller(
        config,
        handle_text=lambda chat_id, text, message_thread_id=None: handled.append(chat_id),
        get_updates=lambda *_args, **_kwargs: [
            {"update_id": 10, "message": {"chat": {"id": "-100123"}, "text": "!홍길동1234"}}
        ],
        offset_store_path=offset_path,
    )

    poller.poll_once()

    assert handled == []
    assert poller.next_update_id == 11
    assert offset_path.read_text(encoding="utf-8") == "11"


def test_telegram_update_poller_does_not_advance_offset_when_final_reply_fails(tmp_path):
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>오전피크</th><th>오후논피크</th><th>저녁피크</th><th>야간논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>50</td><td>0</td><td>1</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """
    logs: list[str] = []
    sent: list[str] = []
    offset_path = tmp_path / "telegram.offset"

    def fake_send_text(_config, message, *, message_thread_id=None):
        sent.append(message)
        if message != "조회 중입니다.":
            raise RuntimeError("temporary send failure")

    processor = TelegramCommandProcessor(
        [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")],
        fetch_html=lambda _config: html,
        send_text=fake_send_text,
        log_event=logs.append,
    )
    poller = TelegramUpdatePoller(
        _config(tmp_path, crawl_name="크롤링1", chat_id="-100123"),
        handle_text=processor.handle_text,
        get_updates=lambda *_args, **_kwargs: [
            {"update_id": 10, "message": {"chat": {"id": "-100123"}, "text": "!홍길동1234"}}
        ],
        offset_store_path=offset_path,
    )

    with pytest.raises(RuntimeError, match="temporary send failure"):
        poller.poll_once()

    assert sent[0] == "조회 중입니다."
    assert poller.next_update_id is None
    assert not offset_path.exists()
    assert any("최종 답장 전송 오류" in message for message in logs)


def test_telegram_update_poller_does_not_repeat_progress_reply_after_final_reply_fails(tmp_path):
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
      </tr></thead>
      <tbody>
        <tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>50</td><td>0</td><td>1</td><td>1</td></tr>
      </tbody>
    </table>
    """
    sent: list[str] = []
    offset_path = tmp_path / "telegram.offset"

    def fake_send_text(_config, message, *, message_thread_id=None):
        sent.append(message)
        if message != "조회 중입니다.":
            raise RuntimeError("temporary send failure")

    processor = TelegramCommandProcessor(
        [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")],
        fetch_html=lambda _config: html,
        send_text=fake_send_text,
    )
    poller = TelegramUpdatePoller(
        _config(tmp_path, crawl_name="크롤링1", chat_id="-100123"),
        handle_text=processor.handle_text,
        get_updates=lambda *_args, **_kwargs: [
            {"update_id": 10, "message": {"chat": {"id": "-100123"}, "text": "!홍길동1234"}}
        ],
        offset_store_path=offset_path,
    )

    with pytest.raises(RuntimeError, match="temporary send failure"):
        poller.poll_once()
    with pytest.raises(RuntimeError, match="temporary send failure"):
        poller.poll_once()

    assert sent.count("조회 중입니다.") == 1
    assert poller.next_update_id is None


def test_telegram_update_poller_handles_updates_in_same_batch_concurrently(tmp_path):
    barrier = threading.Barrier(2, timeout=1)
    handled: list[str] = []

    def handle_text(chat_id: str, text: str, message_thread_id=None):
        barrier.wait()
        handled.append(chat_id)

    poller = TelegramUpdatePoller(
        _config(tmp_path, crawl_name="크롤링1"),
        handle_text=handle_text,
        get_updates=lambda *_args, **_kwargs: [
            {"update_id": 10, "message": {"chat": {"id": "-100111"}, "text": "!홍길동1234"}},
            {"update_id": 11, "message": {"chat": {"id": "-100222"}, "text": "!김철수5678"}},
        ],
        offset_store_path=tmp_path / "telegram.offset",
    )

    poller.poll_once()

    assert sorted(handled) == ["-100111", "-100222"]
    assert poller.next_update_id == 12


def test_telegram_update_poller_uses_offset_file_lock(tmp_path):
    offset_path = tmp_path / "telegram.offset"
    lock_path = tmp_path / "telegram.offset.lock"
    lock_path.write_text("not-a-timestamp", encoding="utf-8")
    calls = []

    poller = TelegramUpdatePoller(
        _config(tmp_path, crawl_name="크롤링1"),
        handle_text=lambda *_args, **_kwargs: None,
        get_updates=lambda *_args, **_kwargs: calls.append("called") or [],
        offset_store_path=offset_path,
    )

    with pytest.raises(RuntimeError, match="run lock is already held"):
        poller.poll_once()

    assert calls == []


def test_default_offset_store_path_depends_on_token_not_first_tab_log_dir(tmp_path):
    first = replace(_config(tmp_path, crawl_name="크롤링1"), log_dir=tmp_path / "first" / "logs")
    second = replace(_config(tmp_path, crawl_name="크롤링2"), log_dir=tmp_path / "second" / "logs")

    assert _default_offset_store_path(first) == _default_offset_store_path(second)


def test_default_offset_store_path_is_absolute_and_cwd_stable(tmp_path, monkeypatch):
    config = _config(tmp_path, crawl_name="크롤링1")

    sub_a = tmp_path / "a"
    sub_b = tmp_path / "b"
    sub_a.mkdir()
    sub_b.mkdir()

    monkeypatch.chdir(sub_a)
    path_from_a = _default_offset_store_path(config)
    monkeypatch.chdir(sub_b)
    path_from_b = _default_offset_store_path(config)

    assert path_from_a.is_absolute()
    # 작업 디렉터리가 달라도 같은 토큰이면 같은 파일을 가리켜야 한다.
    assert path_from_a == path_from_b


def test_default_offset_store_path_honors_state_root_override(tmp_path, monkeypatch):
    config = _config(tmp_path, crawl_name="크롤링1")
    monkeypatch.setenv("RIDER_CRAWL_STATE_ROOT", str(tmp_path / "state-root"))

    path = _default_offset_store_path(config)

    expected_root = (tmp_path / "state-root").resolve()
    assert path == expected_root / "runtime" / "state" / "telegram_offsets" / path.name
    assert path.parent == app_state_root() / "runtime" / "state" / "telegram_offsets"


def test_telegram_command_processor_replies_that_lookup_is_baemin_only_for_coupang(tmp_path):
    sent: list[str] = []
    config = AppConfig(
        **{
            **_config(tmp_path, crawl_name="크롤링1", chat_id="-100123").__dict__,
            "platform_name": "coupang",
            "coupang_eats_url": "https://partner.coupangeats.com/page/rider-performance",
            "peak_dashboard_url": "https://partner.coupangeats.com/page/peak-dashboard",
        }
    )
    processor = TelegramCommandProcessor(
        [config],
        fetch_html=lambda _config: (_ for _ in ()).throw(AssertionError("must not fetch Coupang as Baemin")),
        send_text=lambda _config, message, **_kwargs: sent.append(message),
    )

    handled = processor.handle_text("-100123", "!홍길동1234")

    assert handled is True
    assert sent == ["라이더 조회 명령은 배민 탭에서만 지원합니다."]


def _config(
    tmp_path: Path,
    *,
    crawl_name: str,
    chat_id: str = "-100123",
    message_thread_id: str = "",
) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / crawl_name,
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=True,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        telegram_bot_token="token",
        telegram_chat_id=chat_id,
        telegram_message_thread_id=message_thread_id,
        crawl_name=crawl_name,
    )


def _keyword_responder(
    tmp_path: Path,
    *,
    keywords=("사고", "병원"),
    auto_message="자동응답",
    cooldown_seconds=30,
) -> KeywordResponder:
    path = tmp_path / "keyword_config.json"
    path.write_text(
        json.dumps(
            {
                "keywords": list(keywords),
                "auto_message": auto_message,
                "cooldown_seconds": cooldown_seconds,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return KeywordResponder(config_path=path)


def test_keyword_auto_reply_sends_on_keyword(tmp_path):
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    sent: list[tuple[str, str, int | None]] = []
    processor = TelegramCommandProcessor(
        configs,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(
            (config.telegram_chat_id, message, message_thread_id)
        ),
        keyword_responder=_keyword_responder(tmp_path, auto_message="안내드립니다"),
    )

    handled = processor.handle_text("-100123", "사고가 났어요")

    assert handled is True
    assert sent == [("-100123", "안내드립니다", None)]


def test_keyword_auto_reply_ignores_unconfigured_chat(tmp_path):
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    sent: list = []
    processor = TelegramCommandProcessor(
        configs,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(message),
        keyword_responder=_keyword_responder(tmp_path),
    )

    # 설정된 대상(-100123)이 아닌 다른 그룹방 메시지는 키워드가 있어도 무시한다.
    handled = processor.handle_text("-999999", "사고")

    assert handled is False
    assert sent == []


def test_keyword_auto_reply_is_topic_aware(tmp_path):
    configs = [
        _config(tmp_path, crawl_name="크롤링1", chat_id="-100123", message_thread_id="5"),
    ]
    sent: list[tuple[str, int | None]] = []
    processor = TelegramCommandProcessor(
        configs,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(
            (message, message_thread_id)
        ),
        keyword_responder=_keyword_responder(tmp_path, auto_message="A"),
    )

    # 설정된 토픽(5)에서 온 메시지에만 같은 토픽으로 응답한다.
    assert processor.handle_text("-100123", "사고", message_thread_id=5) is True
    # 다른 토픽(9)은 설정 대상이 아니므로 무시한다.
    assert processor.handle_text("-100123", "사고", message_thread_id=9) is False
    assert sent == [("A", 5)]


def test_keyword_auto_reply_respects_cooldown(tmp_path):
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    sent: list = []
    processor = TelegramCommandProcessor(
        configs,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(message),
        keyword_responder=_keyword_responder(tmp_path, cooldown_seconds=30),
    )

    assert processor.handle_text("-100123", "사고") is True
    # 쿨다운 이내 반복 키워드는 응답하지 않는다.
    assert processor.handle_text("-100123", "병원") is False
    assert len(sent) == 1


def test_keyword_auto_reply_does_not_run_without_responder(tmp_path):
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    sent: list = []
    processor = TelegramCommandProcessor(
        configs,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(message),
    )

    # keyword_responder가 없으면 일반 메시지는 그대로 무시된다(기존 동작 유지).
    assert processor.handle_text("-100123", "사고") is False
    assert sent == []


def test_keyword_auto_reply_excludes_slash_commands(tmp_path):
    # P2: /start, /help 등 명령어 메시지는 키워드 자동응답 대상에서 제외한다.
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    sent: list = []
    processor = TelegramCommandProcessor(
        configs,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(message),
        keyword_responder=_keyword_responder(tmp_path, auto_message="AUTO"),
    )

    # 키워드가 포함돼 있어도 슬래시 명령어이면 무시한다.
    assert processor.handle_text("-100123", "/help 사고") is False
    assert processor.handle_text("-100123", "/start") is False
    assert processor.handle_text("-100123", "  /help 병원") is False
    assert sent == []


def test_keyword_auto_reply_retries_after_send_failure(tmp_path):
    # P1: 전송이 실패하면 쿨다운을 기록하지 않아 다음 메시지에서 다시 응답해야 한다.
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    attempts = {"n": 0}

    def flaky_send(config, message, *, message_thread_id=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("네트워크 오류")

    processor = TelegramCommandProcessor(
        configs,
        send_text=flaky_send,
        keyword_responder=_keyword_responder(tmp_path, cooldown_seconds=30),
    )

    # 첫 전송은 예외를 그대로 올린다(poller가 재시도하도록).
    with pytest.raises(RuntimeError):
        processor.handle_text("-100123", "사고")
    # 두 번째 메시지: 쿨다운에 막히지 않고 다시 응답해 전송에 성공한다.
    assert processor.handle_text("-100123", "사고") is True
    assert attempts["n"] == 2


def test_keyword_auto_reply_does_not_double_send_on_concurrent_batch(tmp_path):
    # P1(race): 같은 batch의 업데이트는 폴러가 병렬 처리한다. 같은 대상에 키워드
    # 메시지가 동시에 들어와도 대상별 락으로 한 번만 응답해야 한다.
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    sent: list = []
    sent_lock = threading.Lock()

    def slow_send(config, message, *, message_thread_id=None):
        # 전송이 느리다고 가정해 두 스레드가 동시에 쿨다운을 통과할 창을 넓힌다.
        time.sleep(0.05)
        with sent_lock:
            sent.append(message)

    processor = TelegramCommandProcessor(
        configs,
        send_text=slow_send,
        keyword_responder=_keyword_responder(tmp_path, cooldown_seconds=30),
    )

    results: list = []
    results_lock = threading.Lock()

    def worker():
        handled = processor.handle_text("-100123", "사고")
        with results_lock:
            results.append(handled)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 정확히 하나만 응답하고, 다른 하나는 쿨다운으로 막혀야 한다.
    assert sorted(results, reverse=True) == [True, False]
    assert len(sent) == 1


def test_lookup_command_still_takes_precedence_over_keyword(tmp_path):
    html = (
        "<table><thead><tr>"
        "<th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>"
        "<th>배차취소</th><th>배달취소(라이더귀책)</th>"
        "<th>오전피크</th><th>오후논피크</th><th>저녁피크</th><th>야간논피크</th>"
        "</tr></thead><tbody>"
        "<tr><td>홍길동</td><td>수행중</td><td>010-1111-1234</td><td>50</td><td>0</td>"
        "<td>1</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>"
        "</tbody></table>"
    )
    configs = [_config(tmp_path, crawl_name="크롤링1", chat_id="-100123")]
    sent: list = []
    # 키워드를 '홍길동'으로 설정해, 키워드 경로로 처리되면 자동 메시지("KW!")가 나간다.
    processor = TelegramCommandProcessor(
        configs,
        fetch_html=lambda config: html,
        send_text=lambda config, message, *, message_thread_id=None: sent.append(message),
        keyword_responder=_keyword_responder(tmp_path, keywords=("홍길동",), auto_message="KW!"),
    )

    # '!조회' 명령은 키워드 자동응답보다 우선한다(라이더 조회로 처리).
    handled = processor.handle_text("-100123", "!홍길동1234")

    assert handled is True
    # 라이더 조회 경로를 탔으므로 키워드 자동 메시지는 나가지 않는다.
    assert "KW!" not in sent
    assert any("홍길동1234" in message for message in sent)
