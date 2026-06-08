from pathlib import Path

from rider_crawl.config import AppConfig
from rider_crawl.parser import parse_baemin_delivery_history_html
from rider_crawl.telegram_commands import (
    TelegramUpdatePoller,
    TelegramCommandProcessor,
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
    )

    try:
        poller.poll_once()
    except RuntimeError:
        pass

    assert calls == 1
    assert poller.next_update_id is None


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
