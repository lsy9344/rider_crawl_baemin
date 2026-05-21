from pathlib import Path

import pytest

from rider_crawl.parser import (
    MissingPerformanceDataError,
    parse_count,
    parse_current_screen_html,
    parse_pair,
)


def test_parse_current_screen_html_extracts_summary_fields():
    html = Path("tests/fixtures/current_screen.html").read_text(encoding="utf-8")

    snapshot = parse_current_screen_html(html)

    assert snapshot.center_name == "제이앤에이치플러스 의정부남부"
    assert snapshot.date_label == "5월 21일(오늘)"
    assert snapshot.shift_label == "오후논피크"
    assert snapshot.shift_time_range == "13:00~16:55"
    assert snapshot.shift_status == "할당량 소진 중"
    assert snapshot.updated_at == "14:02"
    assert snapshot.available_current == 7
    assert snapshot.available_total == 25
    assert snapshot.waiting_count == 0
    assert snapshot.online_riders == 7
    assert snapshot.rejected_ignored_count == 2.4
    assert snapshot.cancelled_count == 0
    assert snapshot.completed_count == 102.4
    assert snapshot.sequence_violation_count == 0
    assert snapshot.lunch_peak_count == 60.6
    assert snapshot.dinner_peak_count == 0
    assert snapshot.non_peak_count == 41.8
    assert snapshot.active_riders == 5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10건", 10),
        ("102.4건", 102.4),
        ("거절율: 6.2%", 6.2),
        ("1,203 건", 1203),
        ("-", 0),
    ],
)
def test_parse_count_normalizes_korean_count_text(raw, expected):
    assert parse_count(raw) == expected


def test_parse_pair_extracts_done_and_total_counts():
    assert parse_pair("10건/19건") == (10, 19)
    assert parse_pair("7 / 25 명") == (7, 25)


def test_parse_current_screen_html_raises_when_required_data_is_missing():
    with pytest.raises(MissingPerformanceDataError):
        parse_current_screen_html("<html><body>로그인이 필요합니다</body></html>")
