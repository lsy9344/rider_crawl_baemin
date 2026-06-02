from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from .config import AppConfig
from .models import CurrentScreenSnapshot
from .parser import parse_current_screen_html


def crawl_current_screen(
    config: AppConfig,
    *,
    fetch_html: Callable[[AppConfig], str] | None = None,
) -> CurrentScreenSnapshot:
    html = (fetch_html or fetch_page_html)(config)
    return parse_current_screen_html(html)


def fetch_page_html(config: AppConfig) -> str:
    if config.browser_mode == "cdp":
        return fetch_page_html_via_cdp(config)
    if config.browser_mode == "persistent":
        return fetch_page_html_via_persistent_context(config)
    raise ValueError("브라우저 연결 방식은 cdp 또는 persistent 중 하나여야 합니다")


def fetch_page_html_via_cdp(config: AppConfig) -> str:
    return fetch_page_html_via_crawl4ai_cdp(config)


def fetch_page_html_via_crawl4ai_cdp(config: AppConfig) -> str:
    try:
        return asyncio.run(_fetch_page_html_via_crawl4ai_cdp(config))
    except ImportError as exc:
        raise RuntimeError(
            "crawl4ai가 설치되어 있지 않습니다.\n"
            "pip install -U crawl4ai 실행 후 crawl4ai-setup, crawl4ai-doctor를 확인하세요."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Chrome CDP 연결 또는 배민 배달현황 수집 실패: {config.cdp_url}\n"
            "Chrome을 --remote-debugging-port=9222 옵션과 전용 프로필로 실행하고, "
            "배민 배달현황 페이지에 로그인된 상태인지 확인하세요."
        ) from exc


async def _fetch_page_html_via_crawl4ai_cdp(config: AppConfig) -> str:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    browser_config = _make_crawl4ai_browser_config(
        BrowserConfig,
        cdp_url=config.cdp_url,
        headless=config.headless,
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for="css:table",
        js_code=_BAEMIN_REFRESH_BUTTON_JS,
        delay_before_return_html=1,
        page_timeout=config.page_timeout_seconds,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=config.coupang_eats_url, config=run_config)

    if not getattr(result, "success", False):
        message = getattr(result, "error_message", "unknown crawl4ai error")
        raise RuntimeError(str(message))

    html = getattr(result, "html", "") or getattr(result, "cleaned_html", "")
    if not html:
        raise RuntimeError("crawl4ai가 HTML을 반환하지 않았습니다")
    return str(html)


def _make_crawl4ai_browser_config(factory: Any, *, cdp_url: str, headless: bool) -> Any:
    try:
        return factory(
            browser_type="chromium",
            browser_mode="custom",
            cdp_url=cdp_url,
            cache_cdp_connection=False,
            headless=headless,
        )
    except TypeError:
        return factory(
            browser_type="chromium",
            cdp_url=cdp_url,
            headless=headless,
        )


def fetch_page_html_via_persistent_context(config: AppConfig) -> str:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    config.browser_user_data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(config.browser_user_data_dir),
            headless=config.headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                config.coupang_eats_url,
                wait_until="domcontentloaded",
                timeout=config.page_timeout_seconds,
            )
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass

            page.get_by_text("배달현황").wait_for(timeout=config.page_timeout_seconds)
            return page.content()
        finally:
            context.close()


def _browser_pages(browser: Any) -> list[Any]:
    pages: list[Any] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    return pages


def _fetch_target_page_content(
    browser: Any,
    config: AppConfig,
    *,
    load_timeout_errors: tuple[type[BaseException], ...] = (),
) -> str:
    page = _select_page_by_url(_browser_pages(browser), config.coupang_eats_url)
    if page is None:
        raise RuntimeError(
            "열려 있는 Chrome 탭에서 배민 배달현황 페이지를 찾지 못했습니다.\n"
            f"{config.coupang_eats_url} 페이지를 로그인된 상태로 열어두세요."
        )
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except load_timeout_errors:
        pass
    page.get_by_text("배달현황").wait_for(timeout=config.page_timeout_seconds)
    return page.content()


def _select_page_by_url(pages: Iterable[Any], target_url: str) -> Any | None:
    for page in pages:
        if _url_matches(str(page.url), target_url):
            return page
    return None


def _url_matches(page_url: str, target_url: str) -> bool:
    page = urlsplit(page_url)
    target = urlsplit(target_url)
    return page.netloc == target.netloc and _normalize_path(page.path) == _normalize_path(target.path)


def _normalize_path(path: str) -> str:
    return path.rstrip("/") or "/"


_BAEMIN_REFRESH_BUTTON_JS = """
(() => {
  const candidates = Array.from(document.querySelectorAll('button, [role="button"]'));
  const refresh = candidates.find((element) => {
    const text = (element.innerText || element.textContent || '').trim();
    return text === '새로고침';
  });
  if (refresh) {
    if (typeof refresh.click === 'function') {
      refresh.click();
    } else if (window.HTMLElement && refresh instanceof HTMLElement) {
      HTMLElement.prototype.click.call(refresh);
    } else if (typeof MouseEvent === 'function') {
      refresh.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    } else if (typeof document.createEvent === 'function') {
      const event = document.createEvent('MouseEvents');
      event.initMouseEvent('click', true, true, window, 1, 0, 0, 0, 0, false, false, false, false, 0, null);
      refresh.dispatchEvent(event);
    }
  }
})();
"""
