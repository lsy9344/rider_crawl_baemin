import asyncio
import sys
import types

import pytest

from rider_crawl.config import AppConfig
from rider_crawl import crawler
from rider_crawl.crawler import crawl_current_screen


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
    assert page.goto_urls[-1] == config.coupang_eats_url
    assert selected == [(crawler._BAEMIN_CENTER_CHANGE_URL, "DP123")]


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


def _config(
    tmp_path,
    *,
    browser_mode: str,
    baemin_center_name: str = "",
    baemin_center_id: str = "",
) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        baemin_center_name=baemin_center_name,
        baemin_center_id=baemin_center_id,
        browser_mode=browser_mode,
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
