from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
            "playwright가 설치되어 있지 않습니다.\n"
            "pip install -e . 실행 후 다시 확인하세요."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Chrome CDP 연결 또는 배민 배달현황 수집 실패: {config.cdp_url}\n"
            "Chrome을 --remote-debugging-port=9222 옵션과 전용 프로필로 실행하고, "
            "배민 배달현황 페이지에 로그인된 상태인지 확인하세요.\n"
            f"상세 오류: {type(exc).__name__}: {exc}"
        ) from exc


async def _fetch_page_html_via_crawl4ai_cdp(config: AppConfig) -> str:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(config.cdp_url)
        try:
            page = await _open_baemin_delivery_history_page(browser, config)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            await _click_baemin_refresh_button(page)
            await page.locator("table").first.wait_for(timeout=config.page_timeout_seconds)
            html = await _collect_baemin_delivery_history_pages(page, config)
        finally:
            await browser.close()

    if not html:
        raise RuntimeError("배민 배달현황 HTML을 가져오지 못했습니다")
    return str(html)


async def _ensure_baemin_center_selected_via_cdp(config: AppConfig) -> None:
    if not config.baemin_center_id:
        return

    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(config.cdp_url)
        try:
            pages = _browser_pages(browser)
            page = _select_page_by_url(pages, config.coupang_eats_url)
            if page is None:
                page = _select_page_by_url(pages, _BAEMIN_CENTER_CHANGE_URL)
            if page is None:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()

            await page.goto(
                config.coupang_eats_url,
                wait_until="domcontentloaded",
                timeout=config.page_timeout_seconds,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass

            if not _url_matches(str(page.url), _BAEMIN_CENTER_CHANGE_URL):
                return

            await _select_baemin_center(page, config)
        finally:
            await browser.close()


async def _open_baemin_delivery_history_page(browser: Any, config: AppConfig) -> Any:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    pages = _browser_pages(browser)
    page = _select_page_by_url(pages, config.coupang_eats_url)
    if page is None:
        page = _select_page_by_url(pages, _BAEMIN_CENTER_CHANGE_URL)
    if page is None:
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

    await page.goto(
        config.coupang_eats_url,
        wait_until="domcontentloaded",
        timeout=config.page_timeout_seconds,
    )
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
        pass

    if _url_matches(str(page.url), _BAEMIN_CENTER_CHANGE_URL):
        await _select_baemin_center(page, config)

    if not _url_matches(str(page.url), config.coupang_eats_url):
        await page.goto(
            config.coupang_eats_url,
            wait_until="domcontentloaded",
            timeout=config.page_timeout_seconds,
        )

    return page


async def _collect_baemin_delivery_history_pages(page: Any, config: AppConfig) -> str:
    first_html = await page.content()
    total_count = await _delivery_history_total_count(page)
    page_size = _delivery_history_page_size(config.coupang_eats_url)
    page_count = max(1, (total_count + page_size - 1) // page_size)
    if page_count == 1:
        return first_html

    html_parts = [first_html]
    for page_index in range(1, page_count):
        await page.goto(
            _delivery_history_page_url(config.coupang_eats_url, page_index, page_size),
            wait_until="domcontentloaded",
            timeout=config.page_timeout_seconds,
        )
        await page.locator("table").first.wait_for(timeout=config.page_timeout_seconds)
        html_parts.append(await page.content())

    await page.goto(
        _delivery_history_page_url(config.coupang_eats_url, 0, page_size),
        wait_until="domcontentloaded",
        timeout=config.page_timeout_seconds,
    )
    return "\n".join(html_parts)


async def _delivery_history_total_count(page: Any) -> int:
    text = await page.locator("body").inner_text(timeout=10_000)
    match = re.search(r"총\s*(?P<count>\d+)\s*건", text)
    if not match:
        return 0
    return int(match.group("count"))


def _delivery_history_page_size(url: str) -> int:
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    raw = query.get("size", "20")
    try:
        value = int(raw)
    except ValueError:
        return 20
    return value if value > 0 else 20


def _delivery_history_page_url(url: str, page_index: int, page_size: int) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page_index)
    query["size"] = str(page_size)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


async def _select_baemin_center(page: Any, config: AppConfig) -> None:
    target_label = f"{config.baemin_center_name} ({config.baemin_center_id})"
    target_compact_label = f"{config.baemin_center_name}({config.baemin_center_id})"

    select = page.locator("select").first
    if await select.count():
        try:
            await select.select_option(value=config.baemin_center_id, timeout=config.page_timeout_seconds)
        except Exception:
            await select.select_option(label=target_label, timeout=config.page_timeout_seconds)
    else:
        await _click_first_visible_text(page, target_label, target_compact_label)

    await page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass


async def _click_first_visible_text(page: Any, *texts: str) -> None:
    for text in texts:
        locator = page.get_by_text(text, exact=True).first
        if await locator.count():
            await locator.click(timeout=5_000)
            return
    raise RuntimeError("배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다")


async def _click_baemin_refresh_button(page: Any) -> None:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    await page.get_by_role("button", name="새로고침", exact=True).click(timeout=5_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
        pass


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

            _click_baemin_refresh_button_sync(page)
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


def _click_baemin_refresh_button_sync(page: Any) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page.get_by_role("button", name="새로고침", exact=True).click(timeout=5_000)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
        pass


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


_BAEMIN_CENTER_CHANGE_URL = "https://deliverycenter.baemin.com/center/change"


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
