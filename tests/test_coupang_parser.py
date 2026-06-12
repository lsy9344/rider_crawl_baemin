from pathlib import Path

import pytest

from rider_crawl.platforms.coupang.parser import (
    MissingPerformanceDataError,
    parse_count,
    parse_current_screen_html,
    parse_current_screen_text,
    parse_peak_dashboard_text,
    parse_pair,
)


def test_parse_coupang_current_screen_html_extracts_summary_fields():
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")

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
    assert snapshot.active_riders == 7


def test_parse_coupang_current_screen_reads_center_and_shift_from_heading_not_hardcoded():
    # A different region/shift than the fixture: guards against regressing to a
    # hardcoded "의정부남부" center or fixed shift label.
    text = "\n".join(
        [
            "에이비씨로지스 강남센터 오전피크(09:00~12:30) 할당량 진행 중 라이더 현황",
            "6월 10일(오늘)",
            "14:02 업데이트",
            "3 / 12 명",
            "대기: 1",
            "온라인: 5",
            "거절/무시: 2",
            "취소: 0",
            "완료: 80",
            "순서 미준수: 0",
            "점심피크: 30",
            "저녁피크: 0",
            "논피크: 50",
        ]
    )

    snapshot = parse_current_screen_text(text)

    assert snapshot.center_name == "에이비씨로지스 강남센터"
    assert snapshot.shift_label == "오전피크"
    assert snapshot.shift_time_range == "09:00~12:30"
    assert snapshot.shift_status == "할당량 진행 중"
    assert snapshot.available_current == 3
    assert snapshot.available_total == 12
    assert snapshot.online_riders == 5


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
def test_parse_coupang_count_normalizes_korean_count_text(raw, expected):
    assert parse_count(raw) == expected


def test_parse_coupang_pair_extracts_done_and_total_counts():
    assert parse_pair("10건/19건") == (10, 19)
    assert parse_pair("7 / 25 명") == (7, 25)


def test_parse_coupang_current_screen_html_raises_when_required_data_is_missing():
    with pytest.raises(MissingPerformanceDataError):
        parse_current_screen_html("<html><body>로그인이 필요합니다</body></html>")


def test_parse_coupang_peak_dashboard_text_extracts_format_metrics():
    snapshot = parse_peak_dashboard_text(
        "\n".join(
            [
                "제이앤에이치플러스 의정부남부",
                "저녁피크(16:55~20:00)",
                "19:27 업데이트",
                "실시간 오늘의 실적",
                "배정 물량",
                "309건",
                "처리 물량",
                "245.6건",
                "총 거절 수",
                "12.6건",
                "거절률",
                "4.6%",
                "피크타임별 현황",
                "아침",
                "344.4%",
                "잔여",
                "+22",
                "목표/완료",
                "9/31",
                "점심 피크",
                "134.7%",
                "잔여",
                "+15.6",
                "목표/완료",
                "45/60.6",
                "점심 논피크",
                "130.5%",
                "잔여",
                "+17.4",
                "목표/완료",
                "57/74.4",
                "저녁 피크",
                "66.3%",
                "잔여",
                "-40.4",
                "목표/완료",
                "120/79.6",
                "저녁 논피크",
                "0%",
                "잔여",
                "78",
                "목표/완료",
                "78/0",
                "시간대별 기록",
            ]
        )
    )

    assert snapshot.updated_at == "19:27"
    assert snapshot.assigned_count == 309
    assert snapshot.processed_count == 245.6
    assert snapshot.reject_rate == 4.6
    assert snapshot.morning.done == 31
    assert snapshot.morning.total == 9
    assert snapshot.lunch_peak.done == 60.6
    assert snapshot.lunch_peak.total == 45
    assert snapshot.lunch_non_peak.done == 74.4
    assert snapshot.lunch_non_peak.total == 57
    assert snapshot.dinner_peak.done == 79.6
    assert snapshot.dinner_peak.total == 120
    assert snapshot.dinner_non_peak.done == 0
    assert snapshot.dinner_non_peak.total == 78
