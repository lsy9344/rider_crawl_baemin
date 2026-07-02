from pathlib import Path

import pytest

from rider_crawl.platforms.coupang.parser import (
    MissingPerformanceDataError,
    parse_count,
    parse_current_screen_html,
    parse_current_screen_text,
    parse_coupang_rider_performance_rows,
    parse_peak_dashboard_text,
    parse_pair,
)
from rider_crawl.rider_lookup import (
    COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
    RiderLookupCommand,
    find_rider_cancel_matches,
    render_lookup_reply,
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


def test_parse_coupang_current_screen_uses_online_count_for_active_riders():
    text = "\n".join(
        [
            "제이앤에이치플러스 의정부남부 밤논피크(20:00~06:00) 할당량 소진 중 라이더 현황",
            "6.14 오늘)",
            "01:05 업데이트",
            "밤논피크 참여 가능",
            "0 / 15 명",
            "대기",
            "0명",
            "활성 라이더",
            "이름 / 연락처",
            "총 4명",
            "상태",
            "온라인 0명",
            "거절/무시: 5.8건",
            "취소: 1건",
            "완료: 78.8건",
            "순서 미준수: 0건",
            "점심피크: 21.6건",
            "저녁피크: 15.8건",
            "논피크: 41.4건",
            "비활성 라이더",
            "이름 / 연락처",
            "총 0명",
        ]
    )

    snapshot = parse_current_screen_text(text)

    assert snapshot.online_riders == 0
    assert snapshot.active_riders == 0


def test_parse_coupang_current_screen_accepts_scrapling_split_available_pair():
    text = "\n".join(
        [
            "제이앤에이치플러스 의정부남부",
            "밤논피크(20:00~06:00)",
            "할당량 소진 중",
            "라이더 현황",
            "01:05 업데이트",
            "밤논피크 참여 가능",
            "0/15",
            "대기",
            "0",
            "활성 라이더",
            "이름 / 연락처",
            "총 4명",
            "상태",
            "온라인 0명",
            "거절/무시",
            "5.8건",
            "취소",
            "1건",
            "완료",
            "78.8건",
            "순서 미준수",
            "0건",
            "점심피크",
            "21.6건",
            "저녁피크",
            "15.8건",
            "논피크",
            "41.4건",
        ]
    )

    snapshot = parse_current_screen_text(text)

    assert snapshot.available_current == 0
    assert snapshot.available_total == 15
    assert snapshot.active_riders == 0


def test_parse_coupang_current_screen_falls_back_to_record_table_online_count():
    text = "\n".join(
        [
            "해운대이로움 남구중앙",
            "6.13",
            "6:00",
            "~",
            "6.14",
            "5:59",
            "해운대이로움 남구중앙",
            "6월 13일(토)",
            "라이더 현황",
            "신규 라이더 등록",
            "10:51 업데이트",
            "이름 / 연락처",
            "총 60명",
            "상태",
            "온라인 18명",
            "거절/무시",
            "161.4건",
            "취소",
            "28건",
            "완료",
            "1772건",
            "순서 미준수",
            "0건",
            "점심피크",
            "416.4건",
            "저녁피크",
            "458건",
            "논피크",
            "897.6건",
        ]
    )

    snapshot = parse_current_screen_text(text)

    assert snapshot.center_name == "해운대이로움 남구중앙"
    assert snapshot.updated_at == "10:51"
    assert snapshot.available_current == 0
    assert snapshot.available_total == 0
    assert snapshot.online_riders == 18
    assert snapshot.active_riders == 18
    assert snapshot.completed_count == 1772
    assert snapshot.lunch_peak_count == 416.4


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


def test_coupang_parser_accepts_goal_done_label_variants():
    snapshot = parse_peak_dashboard_text(
        "\n".join(
            [
                "20:38 업데이트",
                "배정 물량",
                "1건",
                "처리 물량",
                "1건",
                "거절률",
                "0%",
                "피크타임별 현황",
                "아침",
                "목표 / 완료",
                "9 / 1",
                "점심 피크",
                "목표 / 완료",
                "45 / 2",
                "점심 논피크",
                "목표 / 완료",
                "57 / 3",
                "저녁 피크",
                "목표 / 완료",
                "120 / 4",
                "저녁 논피크",
                "목표 / 완료",
                "78 / 5",
                "시간대별 기록",
            ]
        )
    )

    assert snapshot.morning.total == 9
    assert snapshot.morning.done == 1
    assert snapshot.dinner_non_peak.done == 5


def test_coupang_parser_accepts_comma_and_unit_numbers_in_peak_pairs():
    snapshot = parse_peak_dashboard_text(
        "\n".join(
            [
                "20:38 업데이트",
                "배정 물량",
                "1,234건",
                "처리 물량",
                "1,111건",
                "거절률",
                "0%",
                "피크타임별 현황",
                "아침",
                "목표/완료",
                "1,000건 / 999건",
                "점심 피크",
                "목표/완료",
                "2,000건 / 1,999건",
                "점심 논피크",
                "목표/완료",
                "3,000건 / 2,999건",
                "저녁 피크",
                "목표/완료",
                "4,000건 / 3,999건",
                "저녁 논피크",
                "목표/완료",
                "5,000건 / 4,999건",
                "시간대별 기록",
            ]
        )
    )

    assert snapshot.assigned_count == 1234
    assert snapshot.morning.total == 1000
    assert snapshot.morning.done == 999
    assert snapshot.dinner_non_peak.total == 5000
    assert snapshot.dinner_non_peak.done == 4999


def test_parse_coupang_rider_performance_rows_maps_live_table_to_lookup_rows():
    rows = parse_coupang_rider_performance_rows(_COUPANG_RIDER_PERFORMANCE_HTML)

    assert rows == [
        {
            "이름": "홍길동",
            "휴대폰번호": "010-1111-1234",
            "상태": "배달중",
            "거절": "1건",
            "배차취소": "2건",
            "배달취소(라이더귀책)": "0",
            "완료": "50건",
        },
        {
            "이름": "이순신",
            "휴대폰번호": "010-2222-5678",
            "상태": "오프라인",
            "거절": "-",
            "배차취소": "-",
            "배달취소(라이더귀책)": "0",
            "완료": "3건",
        },
    ]


def test_coupang_rider_performance_rows_feed_shared_cancel_rate_lookup():
    command = RiderLookupCommand(
        type=COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
        name="홍길동",
        phone_last4="1234",
    )

    matches = find_rider_cancel_matches(
        parse_coupang_rider_performance_rows(_COUPANG_RIDER_PERFORMANCE_HTML),
        command=command,
        source_label="해운대플러스 수영중앙",
    )

    stats = matches[0].stats
    assert stats.rejected_count == 1
    assert stats.total_cancel_count == 2
    assert stats.cancel_rate == 5.7
    assert render_lookup_reply(command, matches) == "홍길동1234님\n거절:1개/취소:2개\n거절/취소율:5.7%"


def test_parse_coupang_rider_performance_rows_raises_when_required_headers_are_missing():
    html = "<table><thead><tr><th>이름 / 연락처</th><th>완료</th></tr></thead></table>"

    with pytest.raises(MissingPerformanceDataError):
        parse_coupang_rider_performance_rows(html)


_COUPANG_RIDER_PERFORMANCE_HTML = """
<table>
  <thead>
    <tr>
      <th>우선순위</th>
      <th>이름 / 연락처 총 2명</th>
      <th>우선순위변경</th>
      <th>상태 온라인 1명</th>
      <th>거절/무시 3건</th>
      <th>취소 2건</th>
      <th>완료 50건</th>
      <th>순서 미준수 0건</th>
      <th>점심피크 10건</th>
      <th>저녁피크 20건</th>
      <th>논피크 20건</th>
      <th>활성상태</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1</td>
      <td>홍길동<br>010-1111-1234</td>
      <td>교체</td>
      <td>배달중</td>
      <td>1건</td>
      <td>2건</td>
      <td>50건</td>
      <td>-</td>
      <td>10건</td>
      <td>20건</td>
      <td>20건</td>
      <td>활성</td>
    </tr>
    <tr>
      <td>-</td>
      <td>비어있음</td>
      <td>교체</td>
      <td>온라인 시 자동 추가</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td></td>
    </tr>
    <tr>
      <td>-</td>
      <td>이순신<br>010-2222-5678</td>
      <td></td>
      <td>오프라인</td>
      <td>-</td>
      <td>-</td>
      <td>3건</td>
      <td>-</td>
      <td>0건</td>
      <td>0건</td>
      <td>3건</td>
      <td></td>
    </tr>
  </tbody>
</table>
"""
