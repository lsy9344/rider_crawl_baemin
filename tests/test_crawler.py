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


def test_fetch_page_html_keeps_persistent_context_as_fallback(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="persistent")

    monkeypatch.setattr(crawler, "fetch_page_html_via_cdp", lambda _config: "cdp-html")
    monkeypatch.setattr(crawler, "fetch_page_html_via_persistent_context", lambda _config: "persistent-html")

    assert crawler.fetch_page_html(config) == "persistent-html"


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


def test_select_page_by_url_returns_none_when_target_tab_is_missing():
    pages = [_FakePage("https://partner.coupangeats.com/page/peak-dashboard")]

    page = crawler._select_page_by_url(
        pages,
        "https://partner.coupangeats.com/page/rider-performance",
    )

    assert page is None


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


def _config(tmp_path, *, browser_mode: str) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
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
