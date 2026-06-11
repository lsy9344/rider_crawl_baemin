from pathlib import Path
from datetime import datetime

import pytest

from rider_crawl.parser import (
    MissingPerformanceDataError,
    parse_achievement_report_text,
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
    assert snapshot.afternoon_non_peak_count == 41.8
    assert snapshot.dinner_peak_count == 0
    assert snapshot.dinner_non_peak_count == 0
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


def test_parse_achievement_report_text_extracts_latest_completed_row_for_center():
    text = "\n".join(
        [
            "달성현황",
            "협력사 아이디",
            "날짜",
            "요일",
            "아침점심",
            "오후논피",
            "저녁피크",
            "심야논피",
            "수락률",
            "표준서울마포B - DP2605181318",
            "26-06-10",
            "수",
            "323/231 (100%)",
            "296/220 (100%)",
            "433/330 (100%)",
            "374/319 (100%)",
            "88.18%",
            "표준서울마포B - DP2605181318",
            "26-06-11",
            "목",
            "0/231 (0%)",
            "0/220 (0%)",
            "0/330 (0%)",
            "0/319 (0%)",
            "0.00%",
            "오늘 배달현황",
            "주간 배달 현황",
        ]
    )

    snapshot = parse_achievement_report_text(
        text,
        center_id="DP2605181318",
        center_name="표준서울마포B이츠앤홀딩스3",
        now=datetime(2026, 6, 11, 0, 37),
    )

    assert snapshot.center_name == "표준서울마포B이츠앤홀딩스3"
    assert snapshot.date_label == "26-06-10"
    assert snapshot.updated_at == "00:37"
    assert snapshot.lunch_peak_count == 323
    assert snapshot.lunch_peak_goal == 231
    assert snapshot.lunch_peak_rate == 100
    assert snapshot.afternoon_non_peak_count == 296
    assert snapshot.afternoon_non_peak_goal == 220
    assert snapshot.afternoon_non_peak_rate == 100
    assert snapshot.dinner_peak_count == 433
    assert snapshot.dinner_peak_goal == 330
    assert snapshot.dinner_peak_rate == 100
    assert snapshot.dinner_non_peak_count == 374
    assert snapshot.dinner_non_peak_goal == 319
    assert snapshot.dinner_non_peak_rate == 100
    assert snapshot.reject_rate == 12


def test_parse_achievement_report_text_prefers_today_when_today_has_counts():
    text = "\n".join(
        [
            "주간 배달 현황",
            "표준서울마포B - DP2605181318",
            "26-06-10",
            "수",
            "323/231 (100%)",
            "296/220 (100%)",
            "433/330 (100%)",
            "374/319 (100%)",
            "88.18%",
            "표준서울마포B - DP2605181318",
            "26-06-11",
            "목",
            "98/231 (42%)",
            "1/220 (1%)",
            "2/330 (1%)",
            "3/319 (1%)",
            "90.00%",
        ]
    )

    snapshot = parse_achievement_report_text(
        text,
        center_id="DP2605181318",
        center_name="표준서울마포B이츠앤홀딩스3",
        now=datetime(2026, 6, 11, 11, 28),
    )

    assert snapshot.date_label == "26-06-11"
    assert snapshot.lunch_peak_count == 98
    assert snapshot.reject_rate == 10
