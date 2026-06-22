import asyncio
import dataclasses
import sys
import types

import pytest

from rider_crawl.config import AppConfig
from rider_crawl import crawler
from rider_crawl.crawler import crawl_current_screen
from rider_crawl.models import CurrentScreenSnapshot


_MINIMAL_SNAPSHOT = CurrentScreenSnapshot(
    center_name="배민 배달현황",
    date_label="",
    shift_label="배달현황",
    shift_time_range="",
    shift_status="",
    updated_at="14:02",
    available_current=0,
    available_total=0,
    waiting_count=0,
    online_riders=0,
    rejected_ignored_count=0,
    cancelled_count=0,
    completed_count=0,
    sequence_violation_count=0,
    lunch_peak_count=0,
    dinner_peak_count=0,
    non_peak_count=0,
    active_riders=0,
)


def test_crawl_current_screen_parses_html_from_injected_fetcher(tmp_path):
    html = (tmp_path / "fixture.html")
    html.write_text(
        """
        <html><body>
          <h1>제이앤에이치플러스 의정부남부</h1>
          <h2>제이앤에이치플러스 의정부남부 오후논피크(13:00~16:55) 할당량 소진 중 라이더 현황</h2>
          <p>14:02 업데이트</p>
          <p>오후논피크 참여 가능</p>
          <p>7 / 25 명</p>
          <p>대기</p><p>0명</p>
          <table><thead><tr>
            <th>상태 온라인 7명</th>
            <th>거절/무시 2.4건</th>
            <th>취소 0건</th>
            <th>완료 102.4건</th>
            <th>순서 미준수 0건</th>
            <th>점심피크 60.6건</th>
            <th>저녁피크 0건</th>
            <th>논피크 41.8건</th>
          </tr></thead><tbody>
            <tr><td>배달중</td></tr>
            <tr><td>배달중</td></tr>
            <tr><td>배달중</td></tr>
            <tr><td>배달중</td></tr>
            <tr><td>배달중</td></tr>
          </tbody></table>
        </body></html>
        """,
        encoding="utf-8",
    )
    config = AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )

    snapshot = crawl_current_screen(config, fetch_html=lambda _config: html.read_text(encoding="utf-8"))

    assert snapshot.updated_at == "14:02"
    assert snapshot.completed_count == 102.4
    assert snapshot.active_riders == 5


def test_crawl_current_screen_parses_achievement_report_text_from_injected_fetcher(tmp_path):
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
        ]
    )
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="표준서울마포B이츠앤홀딩스3",
        baemin_center_id="DP2605181318",
    )

    snapshot = crawl_current_screen(config, fetch_html=lambda _config: text)

    assert snapshot.lunch_peak_count == 323
    assert snapshot.lunch_peak_goal == 231
    assert snapshot.dinner_non_peak_count == 374
    assert snapshot.reject_rate == 11.82


def test_crawl_current_screen_merges_cancel_rate_from_history(tmp_path):
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
        ]
    )
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="표준서울마포B이츠앤홀딩스3",
        baemin_center_id="DP2605181318",
    )
    history = dataclasses.replace(_MINIMAL_SNAPSHOT, reject_rate=4.7, cancel_rate=1.2, cancelled_count=2)

    snapshot = crawl_current_screen(
        config,
        fetch_html=lambda _config: text,
        fetch_cancel_summary=lambda _config: history,
    )

    assert snapshot.lunch_peak_count == 323
    assert snapshot.cancel_rate == 4.7


def test_crawl_current_screen_keeps_report_when_cancel_summary_unavailable(tmp_path):
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
        ]
    )
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="표준서울마포B이츠앤홀딩스3",
        baemin_center_id="DP2605181318",
    )

    snapshot = crawl_current_screen(
        config,
        fetch_html=lambda _config: text,
        fetch_cancel_summary=lambda _config: None,
    )

    assert snapshot.lunch_peak_count == 323
    assert snapshot.cancel_rate is None


def test_fetch_page_html_uses_cdp_mode_by_default(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="cdp")

    monkeypatch.setattr(crawler, "fetch_page_html_via_cdp", lambda _config: "cdp-html")
    monkeypatch.setattr(crawler, "fetch_page_html_via_persistent_context", lambda _config: "persistent-html")

    assert crawler.fetch_page_html(config) == "cdp-html"


def test_fetch_page_html_via_cdp_uses_crawl4ai_cdp_fetcher(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="cdp")

    monkeypatch.setattr(crawler, "fetch_page_html_via_crawl4ai_cdp", lambda _config: "baemin-html")

    assert crawler.fetch_page_html_via_cdp(config) == "baemin-html"


def test_fetch_page_html_keeps_persistent_context_as_fallback(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="persistent")

    monkeypatch.setattr(crawler, "fetch_page_html_via_cdp", lambda _config: "cdp-html")
    monkeypatch.setattr(crawler, "fetch_page_html_via_persistent_context", lambda _config: "persistent-html")

    assert crawler.fetch_page_html(config) == "persistent-html"


def test_fetch_page_html_rejects_mismatched_selected_baemin_center(tmp_path, monkeypatch):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected value="DP999">송파센터</option></select>'
    )
    monkeypatch.setattr(crawler, "fetch_page_html_via_cdp", lambda _config: html)

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        crawler.fetch_page_html(config)


def test_fetch_page_html_rejects_missing_baemin_center_evidence_in_cdp_mode(tmp_path, monkeypatch):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html()
    monkeypatch.setattr(crawler, "fetch_page_html_via_cdp", lambda _config: html)

    with pytest.raises(RuntimeError, match="센터 정보를 확인하지 못했습니다"):
        crawler.fetch_page_html(config)


def test_crawl_baemin_cancel_summary_parses_history_for_matching_center(tmp_path, monkeypatch):
    # 배달현황 HTML이 설정 센터(DP123)와 일치하면 정상적으로 취소율 스냅샷을 만든다.
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected value="DP123">강남센터</option></select>'
    )
    monkeypatch.setattr(
        crawler,
        "_fetch_baemin_delivery_history_tables",
        lambda _config: [crawler.parse_baemin_delivery_history_html(html)],
    )

    snapshot = crawler.crawl_baemin_cancel_summary(config)

    assert snapshot is not None
    assert snapshot.completed_count == 1


_HISTORY_METRIC_COLUMNS = [
    "완료",
    "거절",
    "배차취소",
    "배달취소(라이더귀책)",
    "아침점심피크",
    "오후논피크",
    "저녁피크",
    "심야논피크",
]


def _history_table(*riders, summary=None):
    return crawler.BaeminDeliveryHistoryTable(
        headers=["이름", "운행상태", *_HISTORY_METRIC_COLUMNS],
        summary=summary,
        riders=list(riders),
    )


def _online_rider(name: str):
    row = {"이름": name, "운행상태": "운행중"}
    for column in _HISTORY_METRIC_COLUMNS:
        row[column] = "1" if column == "완료" else "0"
    return row


def _history_summary(**overrides):
    summary = {column: "0" for column in _HISTORY_METRIC_COLUMNS}
    summary.update(overrides)
    return summary


def test_combine_history_page_tables_keeps_page0_summary_and_concats_riders():
    # summary는 모든 페이지에 같은 전체 총계로 반복되므로 첫 페이지 것만 써서 취소율 분모가
    # 부풀려지지 않게 하고, '운행중' 집계용 라이더 행만 전 페이지에서 이어 붙인다.
    page0 = _history_table(_online_rider("라이더1"), summary=_history_summary(완료="10", 거절="1"))
    page1 = _history_table(_online_rider("라이더2"), summary=_history_summary(완료="10", 거절="1"))

    combined = crawler._combine_history_page_tables([page0, page1])

    assert combined.summary == page0.summary
    assert [r["이름"] for r in combined.riders] == ["라이더1", "라이더2"]


def test_crawl_baemin_cancel_summary_sums_active_riders_across_pages(tmp_path, monkeypatch):
    # 100명을 꽉 채운 1페이지 + 일부만 있는 2페이지를 합쳐 '수행중인원'이 페이지를 가로질러
    # 집계되는지 확인한다(단일 페이지였으면 앞 100명만 잡혀 누락됐다).
    config = _config(tmp_path, browser_mode="cdp")
    page0 = _history_table(
        *[_online_rider(f"P0-{i}") for i in range(crawler._BAEMIN_HISTORY_PAGE_SIZE)],
        summary=_history_summary(완료="200"),
    )
    page1 = _history_table(
        *[_online_rider(f"P1-{i}") for i in range(9)],
        summary=_history_summary(완료="200"),
    )
    monkeypatch.setattr(
        crawler, "_fetch_baemin_delivery_history_tables", lambda _config: [page0, page1]
    )

    snapshot = crawler.crawl_baemin_cancel_summary(config)

    assert snapshot is not None
    assert snapshot.active_riders == crawler._BAEMIN_HISTORY_PAGE_SIZE + 9


def test_collect_baemin_history_tables_reads_all_pages_by_total_count(tmp_path):
    # 페이지 수는 '총 N건'으로 결정한다. 총 109건/size 100 → 2페이지. 100명 페이지 +
    # 9명 페이지를 읽고 멈춘다(3번째 페이지는 읽지 않는다). size가 실제 적용됐는지에
    # 의존하지 않는다.
    config = _config(tmp_path, browser_mode="cdp")
    full = _baemin_delivery_history_html_rows(crawler._BAEMIN_HISTORY_PAGE_SIZE)
    partial = _baemin_delivery_history_html_rows(9)
    page = _FakePaginatingHistoryPage([full, partial, full], total_count=109)

    tables = asyncio.run(crawler._collect_baemin_history_tables(page, config))

    assert len(tables) == 2
    assert len(tables[0].riders) == crawler._BAEMIN_HISTORY_PAGE_SIZE
    assert len(tables[1].riders) == 9
    assert page.goto_urls[0] == crawler._baemin_history_page_url(config, 0)
    assert page.goto_urls[1] == crawler._baemin_history_page_url(config, 1)


def test_collect_baemin_history_tables_uses_actual_served_size_when_server_caps(tmp_path):
    # 서버가 size=100 요청을 무시하고 20개씩만 내려주는 경우. 첫 페이지가 실제로 준 행수(20)를
    # 분모로 써야 총 109건 → 6페이지를 끝까지 읽는다(요청 size=100 기준이면 2페이지에서 멈춰
    # 89명 누락). 마지막 페이지는 9명.
    config = _config(tmp_path, browser_mode="cdp")
    served = 20
    full = _baemin_delivery_history_html_rows(served)
    last = _baemin_delivery_history_html_rows(9)
    # 총 109건/응답 20 → 6페이지(앞 5장 20명 + 마지막 9명).
    htmls = [full] * 5 + [last]
    page = _FakePaginatingHistoryPage(htmls, total_count=109)

    tables = asyncio.run(crawler._collect_baemin_history_tables(page, config))

    assert len(tables) == 6
    assert sum(len(t.riders) for t in tables) == served * 5 + 9
    assert page.goto_urls[-1] == crawler._baemin_history_page_url(config, 5)


def test_collect_baemin_history_tables_discards_when_center_unverifiable(tmp_path):
    # 센터가 설정돼 있는데 배달현황 HTML에 센터 단서가 전혀 없으면 '검증 불가'도 폐기한다
    # (require_evidence=True). 다른 센터 데이터를 정상값처럼 섞지 않기 위해서다.
    config = _config(tmp_path, browser_mode="cdp", baemin_center_id="DP123")
    no_evidence = _baemin_delivery_history_html_rows(1)  # 센터 select/단서 없음
    page = _FakePaginatingHistoryPage([no_evidence], total_count=1)

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        asyncio.run(crawler._collect_baemin_history_tables(page, config))


def test_collect_baemin_history_tables_rejects_mismatched_center_on_later_page(tmp_path):
    # 첫 페이지는 설정 센터(DP123)와 일치하지만, 둘째 페이지가 다른 센터(DP999) HTML이면
    # 후속 페이지도 검증을 타므로 폐기한다(예외 전파).
    config = _config(tmp_path, browser_mode="cdp", baemin_center_id="DP123")
    served = 20
    page0 = _baemin_delivery_history_html_rows(
        served, '<select name="center"><option selected value="DP123">강남센터</option></select>'
    )
    page1_other = _baemin_delivery_history_html_rows(
        served, '<select name="center"><option selected value="DP999">송파센터</option></select>'
    )
    page = _FakePaginatingHistoryPage([page0, page1_other], total_count=40)

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        asyncio.run(crawler._collect_baemin_history_tables(page, config))


def test_collect_baemin_history_tables_discards_when_later_page_parse_fails(tmp_path):
    # 첫 페이지는 성공했지만 둘째 페이지가 파싱 실패(로그인 만료/HTML 변경 등)면, 일부만
    # 정상값처럼 보내지 않도록 예외를 그대로 올려 보조 수집 전체를 폐기한다.
    config = _config(tmp_path, browser_mode="cdp")
    full = _baemin_delivery_history_html_rows(crawler._BAEMIN_HISTORY_PAGE_SIZE)
    broken = "<html><body>로그인이 필요합니다</body></html>"
    page = _FakePaginatingHistoryPage([full, broken], total_count=109)

    with pytest.raises(crawler.MissingPerformanceDataError):
        asyncio.run(crawler._collect_baemin_history_tables(page, config))

    # crawl_baemin_cancel_summary는 이 예외를 None으로 흡수해 보조 수집을 통째로 버린다.
    monkeypatched = _FakePaginatingHistoryPage([full, broken], total_count=109)

    import rider_crawl.crawler as crawler_module

    original = crawler_module._fetch_baemin_delivery_history_tables

    def _raise(_config):
        return asyncio.run(crawler._collect_baemin_history_tables(monkeypatched, config))

    crawler_module._fetch_baemin_delivery_history_tables = _raise
    try:
        assert crawler.crawl_baemin_cancel_summary(config) is None
    finally:
        crawler_module._fetch_baemin_delivery_history_tables = original


def test_collect_baemin_history_tables_selects_center_on_change_redirect(tmp_path):
    # 첫 goto가 센터 변경 화면으로 튕기면 설정 센터를 골라준 뒤 같은 URL로 다시 이동한다.
    config = _config(tmp_path, browser_mode="cdp", baemin_center_id="DP123")
    partial = _baemin_delivery_history_html_rows(
        1, '<select name="center"><option selected value="DP123">강남센터</option></select>'
    )
    page = _FakePaginatingHistoryPage(
        [partial], total_count=1, redirect_to=crawler._BAEMIN_CENTER_CHANGE_URL
    )
    selected = {"called": False}

    async def _fake_select(_page, _config):
        selected["called"] = True
        _page.url = crawler._baemin_history_page_url(config, 0)

    import rider_crawl.crawler as crawler_module

    original = crawler_module._select_baemin_center
    crawler_module._select_baemin_center = _fake_select
    try:
        tables = asyncio.run(crawler._collect_baemin_history_tables(page, config))
    finally:
        crawler_module._select_baemin_center = original

    assert selected["called"] is True
    assert len(tables) == 1


def test_collect_baemin_history_tables_rejects_mismatched_center(tmp_path):
    # 달성현황은 DP123인데 배달현황 HTML이 다른 센터(DP999)면, 취소율/수행중인원이 섞이지
    # 않도록 센터 검증에서 막아 예외를 올린다(보조 수집 폐기).
    config = _config(tmp_path, browser_mode="cdp", baemin_center_id="DP123", baemin_center_name="강남센터")
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected value="DP999">송파센터</option></select>'
    )
    page = _FakePaginatingHistoryPage([html], total_count=1)

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        asyncio.run(crawler._collect_baemin_history_tables(page, config))


def test_baemin_history_page_count_does_not_clamp_silently():
    # 헬퍼는 필요한 페이지 수를 그대로 돌려준다(상한에서 조용히 자르지 않는다).
    assert crawler._baemin_history_page_count(401, 20) == 21
    assert crawler._baemin_history_page_count(109, 20) == 6
    assert crawler._baemin_history_page_count(0, 20) == 1


def test_parse_delivery_history_total_count_accepts_comma_groups():
    assert crawler._parse_delivery_history_total_count("총 1,234건") == 1234
    assert crawler._parse_delivery_history_total_count("총 12,345 건") == 12345


def test_collect_baemin_history_tables_discards_when_exceeding_page_cap(tmp_path):
    # 상한(_BAEMIN_HISTORY_MAX_PAGES)을 넘는 초대형 센터는 일부만 읽고 정상값처럼 보내지
    # 않도록 예외를 올려 보조 수집 전체를 폐기한다.
    config = _config(tmp_path, browser_mode="cdp")
    served = 20
    over_cap_total = (crawler._BAEMIN_HISTORY_MAX_PAGES + 1) * served  # 21페이지 필요
    full = _baemin_delivery_history_html_rows(served)
    page = _FakePaginatingHistoryPage([full], total_count=over_cap_total)

    with pytest.raises(crawler.MissingPerformanceDataError, match="상한"):
        asyncio.run(crawler._collect_baemin_history_tables(page, config))


def test_collect_baemin_history_tables_discards_when_rows_have_no_total_count(tmp_path):
    # 총건수 단서가 없으면 첫 페이지 행만으로 뒤 페이지 존재 여부를 알 수 없다.
    # 일부 active_riders만 정상값처럼 보내지 않도록 보조 수집 전체를 폐기한다.
    config = _config(tmp_path, browser_mode="cdp")
    page = _FakePaginatingHistoryPage([_baemin_delivery_history_html_rows(9)], total_text="목록")

    with pytest.raises(crawler.MissingPerformanceDataError, match="총건수"):
        asyncio.run(crawler._collect_baemin_history_tables(page, config))


def test_persistent_history_reads_all_pages_by_actual_served_size(tmp_path, monkeypatch):
    # persistent(sync) 경로도 첫 페이지 실제 행수(20)를 분모로 총 109건 → 6페이지 읽는다.
    config = _config(tmp_path, browser_mode="persistent")
    served = 20
    full = _baemin_delivery_history_html_rows(served)
    last = _baemin_delivery_history_html_rows(9)
    page = _SyncPaginatingHistoryPage([full] * 5 + [last], total_count=109)
    _patch_sync_playwright(monkeypatch, page)

    tables = crawler._fetch_baemin_history_tables_via_persistent_context(config)

    assert len(tables) == 6
    assert sum(len(t.riders) for t in tables) == served * 5 + 9


def test_persistent_history_discards_when_later_page_parse_fails(tmp_path, monkeypatch):
    # persistent 경로도 둘째 페이지 파싱 실패면 예외를 올려 폐기한다(async와 동일 정책).
    config = _config(tmp_path, browser_mode="persistent")
    full = _baemin_delivery_history_html_rows(crawler._BAEMIN_HISTORY_PAGE_SIZE)
    broken = "<html><body>로그인이 필요합니다</body></html>"
    page = _SyncPaginatingHistoryPage([full, broken], total_count=109)
    _patch_sync_playwright(monkeypatch, page)

    with pytest.raises(crawler.MissingPerformanceDataError):
        crawler._fetch_baemin_history_tables_via_persistent_context(config)


def test_persistent_history_rejects_mismatched_center_on_later_page(tmp_path, monkeypatch):
    # persistent 경로도 후속 페이지 센터 불일치(DP999)면 검증에서 막아 폐기한다.
    config = _config(tmp_path, browser_mode="persistent", baemin_center_id="DP123")
    served = 20
    page0 = _baemin_delivery_history_html_rows(
        served, '<select name="center"><option selected value="DP123">강남센터</option></select>'
    )
    page1_other = _baemin_delivery_history_html_rows(
        served, '<select name="center"><option selected value="DP999">송파센터</option></select>'
    )
    page = _SyncPaginatingHistoryPage([page0, page1_other], total_count=40)
    _patch_sync_playwright(monkeypatch, page)

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        crawler._fetch_baemin_history_tables_via_persistent_context(config)


def test_baemin_history_page_url_honors_configured_history_url(tmp_path):
    # 운영 환경이 커스텀 배달현황 URL(쿼리 포함)을 쓰면 그 host/path/쿼리를 보존하면서
    # page/size만 덮어쓴다. 배달현황 경로가 아니면 기본 URL로 폴백한다.
    custom = dataclasses.replace(
        _config(tmp_path, browser_mode="cdp"),
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history?riderStatus=ACTIVE",
    )

    url = crawler._baemin_history_page_url(custom, 2)

    assert url.startswith("https://deliverycenter.baemin.com/delivery/history?")
    assert "riderStatus=ACTIVE" in url
    assert "page=2" in url
    assert f"size={crawler._BAEMIN_HISTORY_PAGE_SIZE}" in url

    # 배달현황 경로가 아니면(달성현황 등) 기본 배달현황 URL을 쓴다.
    report_cfg = dataclasses.replace(
        _config(tmp_path, browser_mode="cdp"),
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/report",
    )
    fallback = crawler._baemin_history_page_url(report_cfg, 0)
    assert fallback.startswith("https://deliverycenter.baemin.com/delivery/history?")


def test_baemin_history_page_url_preserves_custom_host(tmp_path):
    # 프록시/스테이징 등 커스텀 host도 path가 /delivery/history면 그대로 보존한다
    # (공식 host 강제 폴백 금지). page/size만 덮어쓰고 host/path/쿼리는 유지한다.
    proxy = dataclasses.replace(
        _config(tmp_path, browser_mode="cdp"),
        coupang_eats_url="https://baemin-proxy.staging.example.com/delivery/history?riderStatus=ACTIVE",
    )

    url = crawler._baemin_history_page_url(proxy, 1)

    assert url.startswith("https://baemin-proxy.staging.example.com/delivery/history?")
    assert "riderStatus=ACTIVE" in url
    assert "page=1" in url
    assert f"size={crawler._BAEMIN_HISTORY_PAGE_SIZE}" in url


def test_crawl_current_screen_rejects_mismatched_selected_baemin_center_id(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected value="DP999">송파센터</option></select>'
    )

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_crawl_current_screen_rejects_mismatched_selected_baemin_center_name(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="",
    )
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected>송파센터</option></select>'
    )

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_crawl_current_screen_rejects_missing_baemin_center_evidence_when_center_expected(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html()

    with pytest.raises(RuntimeError, match="센터 정보를 확인하지 못했습니다"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_crawl_current_screen_rejects_unverifiable_center_when_id_expected(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected>강남센터</option></select>'
    )

    with pytest.raises(RuntimeError, match="센터 정보를 확인하지 못했습니다"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_crawl_current_screen_rejects_matching_name_without_expected_center_id(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected>강남센터</option></select>'
    )

    with pytest.raises(RuntimeError, match="센터 정보를 확인하지 못했습니다"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_crawl_current_screen_rejects_conflicting_selected_center_even_with_matching_hidden_id(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html(
        """
        <input name="centerId" value="DP123">
        <select name="center"><option selected value="DP999">송파센터</option></select>
        """
    )

    with pytest.raises(RuntimeError, match="설정한 센터와 Chrome 화면에서 확인된 센터가 다릅니다"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_crawl_current_screen_accepts_matching_center_id_with_different_display_name(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    html = _baemin_delivery_history_html(
        '<select name="center"><option selected value="DP123">배민 표시명</option></select>'
    )

    snapshot = crawl_current_screen(config, fetch_html=lambda _config: html)

    assert snapshot.completed_count == 1


def test_crawl_current_screen_accepts_center_shown_as_plain_text_span(tmp_path):
    # 실제 배달현황 화면은 선택된 센터를 드롭다운이 아니라 <span>센터명(DP아이디)</span>
    # 같은 일반 텍스트로만 표시한다. option/input만 보던 파서는 증거를 못 찾아
    # "센터 정보를 확인하지 못했습니다"로 실패했다. 텍스트 라벨도 증거로 잡아야 한다.
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="표준서울마포B이츠앤홀딩스3",
        baemin_center_id="DP2605181318",
    )
    html = _baemin_delivery_history_html(
        '<span data-atelier-component="Typography">'
        "표준서울마포B이츠앤홀딩스3(DP2605181318)</span>"
    )

    snapshot = crawl_current_screen(config, fetch_html=lambda _config: html)

    assert snapshot.completed_count == 1


def test_crawl_current_screen_rejects_wrong_center_shown_as_plain_text_span(tmp_path):
    # 텍스트 라벨로 다른 센터 ID가 떠 있으면 잘못된 계정 실적 전송을 막아야 한다.
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="표준서울마포B이츠앤홀딩스3",
        baemin_center_id="DP2605181318",
    )
    html = _baemin_delivery_history_html(
        '<span data-atelier-component="Typography">다른센터(DP9999999999)</span>'
    )

    with pytest.raises(RuntimeError, match="배민 센터 검증 실패"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_select_page_by_url_allows_query_and_hash():
    pages = [
        _FakePage("https://partner.coupangeats.com/page/peak-dashboard"),
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=1#today"),
    ]

    page = crawler._select_page_by_url(
        pages,
        "https://partner.coupangeats.com/page/rider-performance",
    )

    assert page is pages[1]


def test_select_page_by_url_rejects_different_scheme():
    pages = [
        _FakePage("http://deliverycenter.baemin.com/delivery/history?page=0&size=20"),
    ]

    page = crawler._select_page_by_url(
        pages,
        "https://deliverycenter.baemin.com/delivery/history?page=0&size=20",
    )

    assert page is None


def test_select_page_by_url_prefers_exact_query_match():
    pages = [
        _FakePage("https://deliverycenter.baemin.com/delivery/history?page=1&size=20"),
        _FakePage("https://deliverycenter.baemin.com/delivery/history?page=0&size=20"),
    ]

    page = crawler._select_page_by_url(
        pages,
        "https://deliverycenter.baemin.com/delivery/history?page=0&size=20",
    )

    assert page is pages[1]


def test_select_page_by_url_treats_reordered_query_as_exact_match():
    pages = [
        _FakePage("https://deliverycenter.baemin.com/delivery/history?size=20&page=0"),
    ]

    page = crawler._select_page_by_url(
        pages,
        "https://deliverycenter.baemin.com/delivery/history?page=0&size=20",
    )

    assert page is pages[0]


def test_select_page_by_url_refuses_duplicate_exact_matches():
    pages = [
        _FakePage("https://deliverycenter.baemin.com/delivery/history?page=0&size=20"),
        _FakePage("https://deliverycenter.baemin.com/delivery/history?size=20&page=0"),
    ]

    page = crawler._select_page_by_url(
        pages,
        "https://deliverycenter.baemin.com/delivery/history?page=0&size=20",
    )

    assert page is None


def test_select_page_by_url_refuses_ambiguous_path_only_match():
    pages = [
        _FakePage("https://deliverycenter.baemin.com/delivery/history?page=1&size=20"),
        _FakePage("https://deliverycenter.baemin.com/delivery/history?page=2&size=20"),
    ]

    page = crawler._select_page_by_url(
        pages,
        "https://deliverycenter.baemin.com/delivery/history?page=0&size=20",
    )

    assert page is None


def test_select_page_by_url_returns_none_when_target_tab_is_missing():
    pages = [_FakePage("https://partner.coupangeats.com/page/peak-dashboard")]

    page = crawler._select_page_by_url(
        pages,
        "https://partner.coupangeats.com/page/rider-performance",
    )

    assert page is None


def test_open_baemin_delivery_history_page_enforces_configured_center_before_history(tmp_path, monkeypatch):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    page = _FakeAsyncNavigationPage(config.coupang_eats_url)
    browser = _FakeBrowser([page])
    selected: list[tuple[str, str]] = []

    async def fake_select_baemin_center(received_page, received_config):
        selected.append((received_page.url, received_config.baemin_center_id))

    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(TimeoutError=TimeoutError),
    )
    monkeypatch.setattr(crawler, "_select_baemin_center", fake_select_baemin_center)

    opened_page = asyncio.run(crawler._open_baemin_delivery_history_page(browser, config))

    assert opened_page is page
    assert page.goto_urls[0] == crawler._BAEMIN_CENTER_CHANGE_URL
    assert page.goto_urls[-1] == crawler._baemin_report_url(config)
    assert selected == [(crawler._BAEMIN_CENTER_CHANGE_URL, "DP123")]


def test_baemin_has_logged_in_page_detects_deliverycenter_host():
    logged_in = _FakePage("https://deliverycenter.baemin.com/center/change")
    logged_out = _FakePage("https://biz-member.baemin.com/login")
    assert crawler._baemin_has_logged_in_page([logged_in]) is True
    assert crawler._baemin_has_logged_in_page([logged_out]) is False
    assert crawler._baemin_has_logged_in_page([]) is False


class _GuardContext:
    """new_page 호출 여부를 기록하는 컨텍스트 — 로그아웃 시 새 탭이 안 열려야 함을 검증."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        page = _FakeAsyncNavigationPage("about:blank")
        self.pages.append(page)
        return page


class _GuardBrowser:
    def __init__(self, pages):
        self.contexts = [_GuardContext(pages)]


def test_open_baemin_delivery_history_page_fails_closed_when_logged_out(tmp_path, monkeypatch):
    # 로그인된 배민 탭(deliverycenter 호스트)이 없으면 새 로그인 탭을 열지 않고 fail-closed.
    config = _config(tmp_path, browser_mode="cdp")
    logged_out = _FakePage("https://biz-member.baemin.com/login")
    browser = _GuardBrowser([logged_out])

    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(TimeoutError=TimeoutError),
    )

    with pytest.raises(crawler.BrowserActionRequiredError):
        asyncio.run(crawler._open_baemin_delivery_history_page(browser, config))

    assert browser.contexts[0].new_page_calls == 0


def test_open_baemin_delivery_history_page_reuses_logged_in_tab(tmp_path, monkeypatch):
    # 로그인된 배민 탭이 있으면(report URL 정확 일치) 새 탭을 열지 않고 그대로 재사용한다.
    config = _config(tmp_path, browser_mode="cdp")
    report_url = crawler._baemin_report_url(config)
    page = _FakeAsyncNavigationPage(report_url)
    browser = _GuardBrowser([page])

    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(TimeoutError=TimeoutError),
    )

    opened = asyncio.run(crawler._open_baemin_delivery_history_page(browser, config))

    assert opened is page
    assert browser.contexts[0].new_page_calls == 0


def test_fetch_target_page_content_does_not_close_cdp_browser(tmp_path):
    config = _config(tmp_path, browser_mode="cdp")
    browser = _FakeBrowser(
        [
            _FakePage(
                "https://partner.coupangeats.com/page/rider-performance",
                html="<html>ok</html>",
            )
        ]
    )

    html = crawler._fetch_target_page_content(browser, config)

    assert html == "<html>ok</html>"
    assert browser.closed is False


def test_click_baemin_refresh_button_clicks_real_refresh_button():
    page = _FakeAsyncPage()

    asyncio.run(crawler._click_baemin_refresh_button(page))

    assert page.clicked_button_name == "새로고침"


def test_collect_baemin_achievement_report_text_preserves_frame_line_breaks(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_id="DP2605181318",
    )
    frame = _FakeAsyncTextFrame(
        "\n".join(
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
                "오늘 배달현황",
                "협력사아이디",
                "아침점심",
                "오후논피",
                "저녁피크",
                "심야논피",
                "표준서울마포B - DP2605181318",
                "471/341 (100%)",
                "295/242 (100%)",
                "494/396 (100%)",
                "489/341 (100%)",
            ]
        )
    )
    page = _FakeAsyncReportPage([frame])

    text = asyncio.run(crawler._collect_baemin_achievement_report_text(page, config))

    assert "\n26-06-10\n" in text
    assert frame.scrolled is True


def test_collect_baemin_achievement_report_text_waits_for_today_delivery_table(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_id="DP2605181318",
    )
    weekly_only = "\n".join(
        [
            "주간 배달 현황",
            "표준서울마포B - DP2605181318",
            "26-06-14",
            "일",
            "0/363 (0%)",
            "0/242 (0%)",
            "0/385 (0%)",
            "0/330 (0%)",
            "0.00%",
        ]
    )
    weekly_plus_today = "\n".join(
        [
            weekly_only,
            "오늘 배달현황",
            "협력사아이디",
            "아침점심",
            "오후논피",
            "저녁피크",
            "심야논피",
            "표준서울마포B - DP2605181318",
            "471/341 (100%)",
            "295/242 (100%)",
            "494/396 (100%)",
            "489/341 (100%)",
        ]
    )
    frame = _FakeProgressiveTextFrame([weekly_only, weekly_plus_today])
    page = _FakeAsyncReportPage([frame])

    text = asyncio.run(crawler._collect_baemin_achievement_report_text(page, config))

    assert "471/341 (100%)" in text
    assert page.wait_calls == 1


def _consume_coroutine_then(value):
    def runner(coro, *_args, **_kwargs):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return value

    return runner


def test_crawl4ai_cdp_async_path_never_closes_user_browser(tmp_path, monkeypatch):
    # 실제 _fetch_page_html_via_crawl4ai_cdp async 경로를 직접 실행해, 사용자가 켜 둔
    # CDP 대상 Chrome(browser)에 browser.close()가 호출되지 않는지 회귀 검증한다.
    # 나중에 close()가 다시 들어오면 이 테스트가 실패해 잡아낸다.
    config = _config(tmp_path, browser_mode="cdp")

    table_page = _FakeAsyncContentPage(config.coupang_eats_url, html="<table>ok</table>")
    browser = _FakeAsyncCdpBrowser()
    playwright = _FakeAsyncPlaywright(browser)

    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(
            TimeoutError=TimeoutError,
            async_playwright=lambda: playwright,
        ),
    )

    async def fake_open(received_browser, received_config):
        assert received_browser is browser
        return table_page

    async def fake_collect(_page, _config):
        return "주간 배달 현황 collected"

    monkeypatch.setattr(crawler, "_open_baemin_delivery_history_page", fake_open)
    monkeypatch.setattr(crawler, "_collect_baemin_achievement_report_text", fake_collect)

    html = asyncio.run(crawler._fetch_page_html_via_crawl4ai_cdp(config))

    assert html == "주간 배달 현황 collected"
    assert browser.connected_with == config.cdp_url
    assert browser.close_calls == 0


def test_fetch_via_cdp_rejects_non_local_address_before_connecting(tmp_path, monkeypatch):
    from rider_crawl.browser_launcher import BrowserLaunchError

    config = _config(tmp_path, browser_mode="cdp", cdp_url="http://10.0.0.5:9222")

    connect_attempts = []
    monkeypatch.setattr(
        crawler.asyncio,
        "run",
        lambda *_args, **_kwargs: connect_attempts.append(True),
    )

    with pytest.raises(BrowserLaunchError, match="로컬 주소"):
        crawler.fetch_page_html_via_crawl4ai_cdp(config)
    assert connect_attempts == []


def test_fetch_via_cdp_allows_localhost_address(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="cdp", cdp_url="http://localhost:9222")

    monkeypatch.setattr(crawler.asyncio, "run", _consume_coroutine_then("<html></html>"))

    assert crawler.fetch_page_html_via_crawl4ai_cdp(config) == "<html></html>"


def test_fetch_via_cdp_connection_refused_raises_cdp_unavailable(tmp_path, monkeypatch):
    # Chrome이 포트에 안 떠 있어 connect_over_cdp가 거부되면 환경 오류로 구분한다.
    # 이래야 UI가 5초마다 재시도하지 않고 정규 주기까지 기다린다.
    from rider_crawl.browser_launcher import CdpUnavailableError

    config = _config(tmp_path, browser_mode="cdp")

    def raise_connect_refused(coro, *_args, **_kwargs):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            "BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9222"
        )

    monkeypatch.setattr(crawler.asyncio, "run", raise_connect_refused)

    with pytest.raises(CdpUnavailableError, match="CDP 연결 실패"):
        crawler.fetch_page_html_via_crawl4ai_cdp(config)


def test_fetch_via_cdp_non_connection_error_stays_runtime_error(tmp_path, monkeypatch):
    # 페이지가 떠 있는 상태의 일시적 실패는 기존처럼 RuntimeError로 두어 빠른 재시도
    # 경로를 타게 한다(CdpUnavailableError로 묶으면 일시 장애 복구가 늦어진다).
    from rider_crawl.browser_launcher import CdpUnavailableError

    config = _config(tmp_path, browser_mode="cdp")

    def raise_table_timeout(coro, *_args, **_kwargs):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError("Timeout 60000ms exceeded waiting for locator('table')")

    monkeypatch.setattr(crawler.asyncio, "run", raise_table_timeout)

    with pytest.raises(RuntimeError) as excinfo:
        crawler.fetch_page_html_via_crawl4ai_cdp(config)
    assert not isinstance(excinfo.value, CdpUnavailableError)


def _config(
    tmp_path,
    *,
    browser_mode: str,
    baemin_center_name: str = "",
    baemin_center_id: str = "",
    cdp_url: str = "http://127.0.0.1:9222",
) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        baemin_center_name=baemin_center_name,
        baemin_center_id=baemin_center_id,
        browser_mode=browser_mode,
        cdp_url=cdp_url,
        browser_user_data_dir=tmp_path / "browser",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )


def _baemin_delivery_history_html(center_control: str = "") -> str:
    return f"""
    <html><body>
      {center_control}
      <table>
        <thead><tr>
          <th>이름</th>
          <th>운행상태</th>
          <th>완료</th>
          <th>거절</th>
          <th>배차취소</th>
          <th>배달취소(라이더귀책)</th>
          <th>아침점심피크</th>
          <th>오후논피크</th>
          <th>저녁피크</th>
          <th>심야논피크</th>
        </tr></thead>
        <tbody><tr>
          <td>라이더1</td>
          <td>운행중</td>
          <td>1</td>
          <td>0</td>
          <td>0</td>
          <td>0</td>
          <td>0</td>
          <td>1</td>
          <td>0</td>
          <td>0</td>
        </tr></tbody>
      </table>
    </body></html>
    """


def _baemin_delivery_history_html_rows(rider_count: int, center_control: str = "") -> str:
    rows = "".join(
        """
        <tr>
          <td>라이더{i}</td>
          <td>운행중</td>
          <td>1</td><td>0</td><td>0</td><td>0</td><td>0</td><td>1</td><td>0</td><td>0</td>
        </tr>
        """.format(i=i)
        for i in range(rider_count)
    )
    return f"""
    <html><body>
      {center_control}
      <table>
        <thead><tr>
          <th>이름</th><th>운행상태</th><th>완료</th><th>거절</th>
          <th>배차취소</th><th>배달취소(라이더귀책)</th>
          <th>아침점심피크</th><th>오후논피크</th><th>저녁피크</th><th>심야논피크</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </body></html>
    """


class _FakePaginatingBodyLocator:
    def __init__(self, total_count: int, total_text: str | None = None) -> None:
        self._total_count = total_count
        self._total_text = total_text

    async def inner_text(self, **_kwargs) -> str:
        if self._total_text is not None:
            return self._total_text
        return f"총 {self._total_count}건"


class _FakePaginatingHistoryPage:
    """배민 배달현황 멀티페이지 수집 테스트용 async fake page.

    페이지 수는 '총 N건'(total_count)으로 결정되므로 body inner_text로 그 값을 흘려준다.
    goto할 때마다 다음 페이지 HTML을 내어주고, 첫 goto에서 ``redirect_to``로 한 번 튕기게
    해 센터 변경 리다이렉트도 재현한다.
    """

    def __init__(
        self,
        page_htmls: list[str],
        *,
        total_count: int | None = None,
        total_text: str | None = None,
        redirect_to: str | None = None,
    ) -> None:
        self._page_htmls = page_htmls
        self._total_count = total_count if total_count is not None else len(page_htmls)
        self._total_text = total_text
        self._redirect_to = redirect_to
        self._page_index = -1
        self.url = ""
        self.goto_urls: list[str] = []

    async def goto(self, url: str, **_kwargs):
        self.goto_urls.append(url)
        if self._redirect_to is not None and len(self.goto_urls) == 1:
            self.url = self._redirect_to
            return
        self.url = url
        self._page_index += 1

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def locator(self, selector: str):
        if selector == "body":
            return _FakePaginatingBodyLocator(self._total_count, self._total_text)
        return _FakeAsyncLocator()

    async def content(self) -> str:
        index = min(self._page_index, len(self._page_htmls) - 1)
        return self._page_htmls[index]


class _SyncBodyLocator:
    def __init__(self, total_count: int) -> None:
        self._total_count = total_count

    def inner_text(self, **_kwargs) -> str:
        return f"총 {self._total_count}건"


class _SyncTableLocatorTarget:
    def wait_for(self, **_kwargs):
        return None


class _SyncTableLocator:
    @property
    def first(self):
        return _SyncTableLocatorTarget()


class _SyncPaginatingHistoryPage:
    """persistent(sync) 경로용 배달현황 멀티페이지 fake page.

    _FakePaginatingHistoryPage의 sync 버전. goto/content/inner_text가 동기다.
    """

    def __init__(
        self,
        page_htmls: list[str],
        *,
        total_count: int | None = None,
        redirect_to: str | None = None,
    ) -> None:
        self._page_htmls = page_htmls
        self._total_count = total_count if total_count is not None else len(page_htmls)
        self._redirect_to = redirect_to
        self._page_index = -1
        self.url = ""
        self.goto_urls: list[str] = []

    def goto(self, url: str, **_kwargs):
        self.goto_urls.append(url)
        if self._redirect_to is not None and len(self.goto_urls) == 1:
            self.url = self._redirect_to
            return None
        self.url = url
        self._page_index += 1
        return None

    def locator(self, selector: str):
        if selector == "body":
            return _SyncBodyLocator(self._total_count)
        return _SyncTableLocator()

    def content(self) -> str:
        index = min(self._page_index, len(self._page_htmls) - 1)
        return self._page_htmls[index]


class _FakeSyncPersistentContext:
    def __init__(self, page: _SyncPaginatingHistoryPage) -> None:
        self.pages = [page]
        self.closed = False

    def new_page(self):
        return self.pages[0]

    def close(self):
        self.closed = True


class _FakeSyncChromium:
    def __init__(self, context: _FakeSyncPersistentContext) -> None:
        self._context = context

    def launch_persistent_context(self, *_args, **_kwargs):
        return self._context


class _FakeSyncPlaywright:
    def __init__(self, context: _FakeSyncPersistentContext) -> None:
        self.chromium = _FakeSyncChromium(context)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _patch_sync_playwright(monkeypatch, page: _SyncPaginatingHistoryPage) -> _FakeSyncPersistentContext:
    context = _FakeSyncPersistentContext(page)
    playwright = _FakeSyncPlaywright(context)
    fake_module = types.SimpleNamespace(
        sync_playwright=lambda: playwright,
        TimeoutError=_PlaywrightFakeTimeout,
    )
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    return context


class _PlaywrightFakeTimeout(Exception):
    pass


class _FakePage:
    def __init__(self, url: str, html: str = "") -> None:
        self.url = url
        self.html = html

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def get_by_text(self, _text: str):
        return self

    def wait_for(self, **_kwargs):
        return None

    def content(self) -> str:
        return self.html


class _FakeBrowser:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.contexts = [_FakeContext(pages)]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages


class _FakeAsyncPage:
    def __init__(self) -> None:
        self.clicked_button_name: str | None = None

    def get_by_role(self, role: str, *, name: str, exact: bool):
        assert role == "button"
        assert exact is True
        return _FakeAsyncButton(self, name)

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None


class _FakeAsyncNavigationPage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.goto_urls: list[str] = []

    async def goto(self, url: str, **_kwargs):
        self.url = url
        self.goto_urls.append(url)

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None


class _FakeAsyncButton:
    def __init__(self, page: _FakeAsyncPage, name: str) -> None:
        self.page = page
        self.name = name

    async def click(self, **_kwargs):
        self.page.clicked_button_name = self.name


class _FakeAsyncLocatorTarget:
    async def wait_for(self, **_kwargs):
        return None


class _FakeAsyncLocator:
    @property
    def first(self):
        return _FakeAsyncLocatorTarget()


class _FakeAsyncContentPage:
    def __init__(self, url: str, *, html: str) -> None:
        self.url = url
        self._html = html

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def locator(self, _selector: str):
        return _FakeAsyncLocator()

    async def content(self) -> str:
        return self._html


class _FakeAsyncTextFrame:
    def __init__(self, text: str) -> None:
        self.text = text
        self.scrolled = False

    async def evaluate(self, _script: str):
        self.scrolled = True

    def locator(self, selector: str):
        assert selector == "body"
        return _FakeAsyncTextLocator(self.text)


class _FakeAsyncTextLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    async def inner_text(self, **_kwargs):
        return self.text


class _FakeProgressiveTextFrame:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self._index = 0
        self.scrolled = False

    async def evaluate(self, _script: str):
        self.scrolled = True

    def locator(self, selector: str):
        assert selector == "body"
        text = self._texts[min(self._index, len(self._texts) - 1)]
        self._index += 1
        return _FakeAsyncTextLocator(text)


class _FakeAsyncReportPage:
    def __init__(self, frames: list[_FakeAsyncTextFrame]) -> None:
        self.frames = frames
        self.wait_calls = 0

    async def wait_for_timeout(self, _timeout: int):
        self.wait_calls += 1
        return None


class _FakeAsyncCdpBrowser:
    def __init__(self) -> None:
        self.close_calls = 0
        self.connected_with: str | None = None

    async def close(self) -> None:
        self.close_calls += 1


class _FakeAsyncChromium:
    def __init__(self, browser: _FakeAsyncCdpBrowser) -> None:
        self._browser = browser

    async def connect_over_cdp(self, cdp_url: str):
        self._browser.connected_with = cdp_url
        return self._browser


class _FakeAsyncPlaywright:
    def __init__(self, browser: _FakeAsyncCdpBrowser) -> None:
        self.chromium = _FakeAsyncChromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False
