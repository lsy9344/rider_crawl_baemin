"""Tests for the public Baemin delivery-history row-level accessor (Phase 4).

These lock the row-level contract the RIDER_LOOKUP worker relies on: flatten
paged tables into rider rows and feed them straight into the shared matcher,
without opening a browser or touching aggregate snapshot JSON.
"""

from rider_crawl.crawler import (
    fetch_baemin_delivery_history_rows,
    flatten_baemin_history_rows,
)
from rider_crawl.parser import BaeminDeliveryHistoryTable
from rider_crawl.rider_lookup import (
    COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
    RiderLookupCommand,
    find_rider_cancel_matches,
    render_lookup_reply,
)


def _table(riders):
    return BaeminDeliveryHistoryTable(headers=["이름"], summary=None, riders=riders)


def test_flatten_empty_is_empty():
    assert flatten_baemin_history_rows([]) == []
    assert flatten_baemin_history_rows([_table([])]) == []


def test_flatten_preserves_page_and_row_order():
    page1 = _table([{"이름": "가"}, {"이름": "나"}])
    page2 = _table([{"이름": "다"}])
    rows = flatten_baemin_history_rows([page1, page2])
    assert [r["이름"] for r in rows] == ["가", "나", "다"]


def test_flatten_copies_rows():
    original = {"이름": "가"}
    rows = flatten_baemin_history_rows([_table([original])])
    rows[0]["이름"] = "변경"
    assert original["이름"] == "가"  # source row untouched


def test_fetch_rows_uses_injected_fetch_tables():
    tables = [_table([{"이름": "강민기", "휴대폰번호": "010-0000-1234"}])]
    rows = fetch_baemin_delivery_history_rows(object(), fetch_tables=lambda _config: tables)
    assert rows == [{"이름": "강민기", "휴대폰번호": "010-0000-1234"}]


def test_rows_feed_shared_matcher_end_to_end():
    tables = [
        _table(
            [
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
        )
    ]
    rows = fetch_baemin_delivery_history_rows(object(), fetch_tables=lambda _config: tables)
    command = RiderLookupCommand(
        type=COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP, name="강민기", phone_last4="1234"
    )
    matches = find_rider_cancel_matches(rows, command=command, source_label="배민 남구센터")

    assert len(matches) == 1
    stats = matches[0].stats
    assert stats.total_cancel_count == 2
    assert stats.cancel_rate == 4.0  # (0 + 2) / (48 + 0 + 2) * 100
    reply = render_lookup_reply(command, matches)
    assert reply == "강민기1234님\n거절:0개/취소:2개\n거절/취소율:4%"
