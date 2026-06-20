from pathlib import Path
import logging

from rider_crawl.parser import (
    BAEMIN_DELIVERY_COLUMNS,
    MissingPerformanceDataError,
    baemin_delivery_history_to_snapshot,
    parse_baemin_delivery_history_html,
    parse_current_screen_html,
)


def test_parse_baemin_delivery_history_html_maps_rows_by_header_text():
    html = Path("tests/fixtures/baemin_delivery_history.html").read_text(encoding="utf-8")

    table = parse_baemin_delivery_history_html(html)

    assert table.headers == BAEMIN_DELIVERY_COLUMNS
    assert table.summary is not None
    assert table.summary["이름"] == "합계"
    assert table.summary["완료"] == "32"
    assert table.summary["배달취소(라이더귀책)"] == "2"
    assert [row["아이디"] for row in table.riders] == ["rider01", "rider02"]


def test_parse_baemin_delivery_history_html_keeps_mapping_when_columns_move():
    html = """
    <table>
      <thead><tr>
        <th>아이디</th><th>완료</th><th>이름</th><th>운행상태</th><th>거절</th>
      </tr></thead>
      <tbody>
        <tr><td>rider01</td><td>9</td><td>김배민</td><td>운행중</td><td>1</td></tr>
      </tbody>
    </table>
    """

    table = parse_baemin_delivery_history_html(html)

    assert table.summary is None
    assert table.riders[0]["이름"] == "김배민"
    assert table.riders[0]["완료"] == "9"
    assert table.riders[0]["아이디"] == "rider01"


def test_parse_baemin_delivery_history_html_skips_malformed_rows_with_debug_signal(caplog):
    html = """
    <table>
      <thead><tr>
        <th>아이디</th><th>완료</th><th>메모</th><th>이름</th><th>운행상태</th><th>거절</th>
      </tr></thead>
      <tbody>
        <tr><td>broken01</td><td>4</td><td>구조변형</td><td></td><td>운행중</td><td>1</td></tr>
        <tr><td>rider01</td><td>9</td><td></td><td>김배민</td><td>운행중</td><td>1</td><td>초과셀</td></tr>
        <tr><td>rider02</td><td>3</td><td></td><td>이배민</td><td>대기</td></tr>
      </tbody>
    </table>
    """
    caplog.set_level(logging.DEBUG, logger="rider_crawl.parser")

    table = parse_baemin_delivery_history_html(html)

    assert [row["이름"] for row in table.riders] == ["김배민", "이배민"]
    assert table.riders[0]["완료"] == "9"
    assert table.riders[1]["거절"] == ""
    assert "malformed Baemin delivery rows" in caplog.text


def test_parse_baemin_delivery_history_html_accepts_live_thead_summary_row():
    html = """
    <table>
      <thead>
        <tr>
          <th>이름</th><th>운행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
          <th>배차취소</th><th>배달취소(라이더귀책)</th>
          <th>아침점심피크</th><th>오후논피크</th><th>저녁피크</th><th>심야논피크</th>
          <th>6시</th><th>5시</th><th>아이디</th>
        </tr>
        <tr>
          <th>합계</th><th>-</th><th>-</th><th>9</th><th>2</th>
          <th>0</th><th>0</th><th>7</th><th>2</th><th>0</th><th>0</th>
          <th>0</th><th>0</th><th>-</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>강경우</td><td>운행 종료</td><td>010-0000-0000</td><td>0</td><td>0</td>
          <td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td>
          <td>0</td><td>0</td><td>kks1080</td>
        </tr>
      </tbody>
    </table>
    """

    table = parse_baemin_delivery_history_html(html)
    snapshot = baemin_delivery_history_to_snapshot(table)

    assert table.summary is not None
    assert table.summary["완료"] == "9"
    assert table.summary["아침점심피크"] == "7"
    assert snapshot.completed_count == 9
    assert snapshot.lunch_peak_count == 7
    assert snapshot.afternoon_non_peak_count == 2
    assert snapshot.dinner_non_peak_count == 0
    assert snapshot.non_peak_count == 2


def test_baemin_delivery_history_to_snapshot_uses_summary_row_for_existing_message_shape():
    html = Path("tests/fixtures/baemin_delivery_history.html").read_text(encoding="utf-8")
    table = parse_baemin_delivery_history_html(html)

    snapshot = baemin_delivery_history_to_snapshot(table)

    assert snapshot.center_name == "배민 배달현황"
    assert snapshot.shift_label == "배달현황"
    assert snapshot.available_current == 1
    assert snapshot.available_total == 2
    assert snapshot.online_riders == 1
    assert snapshot.completed_count == 32
    assert snapshot.rejected_ignored_count == 3
    assert snapshot.cancelled_count == 3
    assert snapshot.lunch_peak_count == 18
    assert snapshot.afternoon_non_peak_count == 12
    assert snapshot.dinner_peak_count == 4
    assert snapshot.dinner_non_peak_count == 2
    assert snapshot.non_peak_count == 14
    assert snapshot.reject_rate == 15.8


def test_parse_current_screen_html_accepts_baemin_delivery_history_page():
    html = Path("tests/fixtures/baemin_delivery_history.html").read_text(encoding="utf-8")

    snapshot = parse_current_screen_html(html)

    assert snapshot.completed_count == 32
    assert snapshot.active_riders == 1


def test_baemin_delivery_history_waiting_count_uses_waiting_status_not_offline_rows():
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>운행상태</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>아침점심피크</th><th>오후논피크</th><th>저녁피크</th><th>심야논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>운행자</td><td>운행중</td><td>1</td><td>0</td><td>0</td><td>0</td><td>1</td><td>0</td><td>0</td><td>0</td></tr>
        <tr><td>대기자</td><td>대기</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
        <tr><td>종료자</td><td>운행 종료</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """

    snapshot = parse_current_screen_html(html)

    assert snapshot.waiting_count == 1
    assert snapshot.available_current == 1
    assert snapshot.available_total == 3


def test_baemin_delivery_history_rejects_invalid_required_numeric_value():
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>운행상태</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
        <th>아침점심피크</th><th>오후논피크</th><th>저녁피크</th><th>심야논피크</th>
      </tr></thead>
      <tbody>
        <tr><td>합계</td><td>-</td><td>확인불가</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      </tbody>
    </table>
    """

    try:
        parse_current_screen_html(html)
    except MissingPerformanceDataError as exc:
        assert "완료" in str(exc)
    else:
        raise AssertionError("expected MissingPerformanceDataError")


_BAEMIN_HISTORY_COLSPAN_HTML = """
<table>
  <thead>
    <tr>
      <th rowspan="2">이름</th><th rowspan="2">운행상태</th><th rowspan="2">휴대폰번호</th>
      <th colspan="4">완료</th><th>거절</th>
      <th rowspan="2">배차취소</th><th rowspan="2">배달취소(라이더귀책)</th>
      <th rowspan="2">아침점심피크</th><th rowspan="2">오후논피크</th>
      <th rowspan="2">저녁피크</th><th rowspan="2">심야논피크</th><th rowspan="2">아이디</th>
    </tr>
    <tr><th>푸드</th><th>비마트</th><th>배민스토어</th><th>합계</th><th>푸드</th></tr>
    <tr>
      <th>합계</th><th>-</th><th>-</th>
      <th>49</th><th>0</th><th>0</th><th>49</th><th>1</th><th>1</th><th>0</th>
      <th>31</th><th>18</th><th>0</th><th>0</th><th>-</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>강민기</td><td>운행 종료</td><td>010-1111-2222</td>
      <td>29</td><td>0</td><td>0</td><td>29</td><td>1</td><td>1</td><td>0</td>
      <td>20</td><td>9</td><td>0</td><td>0</td><td>rider01</td>
    </tr>
  </tbody>
</table>
"""


def test_parse_baemin_delivery_history_html_aligns_columns_under_colspan_header():
    table = parse_baemin_delivery_history_html(_BAEMIN_HISTORY_COLSPAN_HTML)

    assert table.summary is not None
    assert table.summary["완료"] == "49"
    assert table.summary["거절"] == "1"
    assert table.summary["배차취소"] == "1"
    assert table.summary["배달취소(라이더귀책)"] == "0"
    assert len(table.riders) == 1
    rider = table.riders[0]
    assert rider["이름"] == "강민기"
    assert rider["완료"] == "29"
    assert rider["배달취소(라이더귀책)"] == "0"


def test_baemin_delivery_history_to_snapshot_computes_cancel_rate_under_colspan_header():
    table = parse_baemin_delivery_history_html(_BAEMIN_HISTORY_COLSPAN_HTML)

    snapshot = baemin_delivery_history_to_snapshot(table)

    assert snapshot.completed_count == 49
    assert snapshot.cancelled_count == 1
    assert snapshot.cancel_rate == 2.0
    assert snapshot.reject_rate == 3.9
