from __future__ import annotations

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
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(config.cdp_url)
        except PlaywrightError as exc:
            raise RuntimeError(
                f"Chrome CDP 연결 실패: {config.cdp_url}\n"
                "Chrome을 --remote-debugging-port=9222 옵션으로 실행한 뒤 다시 시도하세요."
            ) from exc

        # CDP 대상은 사용자가 켜 둔 Chrome이므로 여기서 browser.close()를 호출하지 않는다.
        return _fetch_target_page_content(browser, config, load_timeout_errors=(PlaywrightTimeoutError,))


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

            page.get_by_text("라이더 현황").wait_for(timeout=config.page_timeout_seconds)
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
            "열려 있는 Chrome 탭에서 쿠팡이츠 실적 페이지를 찾지 못했습니다.\n"
            f"{config.coupang_eats_url} 페이지를 로그인된 상태로 열어두세요."
        )
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except load_timeout_errors:
        pass
    page.get_by_text("라이더 현황").wait_for(timeout=config.page_timeout_seconds)
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
