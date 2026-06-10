from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .browser_launcher import ensure_local_cdp_address
from .config import AppConfig
from .models import CurrentScreenSnapshot
from .parser import parse_current_screen_html


def crawl_current_screen(
    config: AppConfig,
    *,
    fetch_html: Callable[[AppConfig], str] | None = None,
) -> CurrentScreenSnapshot:
    html = (fetch_html or fetch_page_html)(config)
    if fetch_html is not None:
        _validate_baemin_center_in_html(config, html, require_evidence=True)
    return parse_current_screen_html(html)


def fetch_page_html(config: AppConfig) -> str:
    if config.browser_mode == "cdp":
        html = fetch_page_html_via_cdp(config)
    elif config.browser_mode == "persistent":
        html = fetch_page_html_via_persistent_context(config)
    else:
        raise ValueError("브라우저 연결 방식은 cdp 또는 persistent 중 하나여야 합니다")
    _validate_baemin_center_in_html(config, html, require_evidence=True)
    return html


def fetch_page_html_via_cdp(config: AppConfig) -> str:
    return fetch_page_html_via_crawl4ai_cdp(config)


def fetch_page_html_via_crawl4ai_cdp(config: AppConfig) -> str:
    # Verify the CDP address before connecting. A non-local address could point
    # at another machine's Chrome and read a different login session, so reject
    # it up front with a clear message instead of letting connect_over_cdp run.
    ensure_local_cdp_address(config.cdp_url)
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

    ensure_local_cdp_address(config.cdp_url)

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
    pages = _browser_pages(browser)
    page = _select_page_by_url(pages, config.coupang_eats_url)
    if page is None:
        page = _select_page_by_url(pages, _BAEMIN_CENTER_CHANGE_URL)
    if page is None:
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

    if _has_configured_baemin_center(config):
        await _goto_page(page, _BAEMIN_CENTER_CHANGE_URL, config)
        await _select_baemin_center(page, config)

    await _goto_page(page, config.coupang_eats_url, config)
    if _url_matches(str(page.url), _BAEMIN_CENTER_CHANGE_URL):
        await _select_baemin_center(page, config)
        await _goto_page(page, config.coupang_eats_url, config)

    if not _url_matches(str(page.url), config.coupang_eats_url):
        await _goto_page(page, config.coupang_eats_url, config)

    return page


async def _goto_page(page: Any, url: str, config: AppConfig) -> None:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=config.page_timeout_seconds,
    )
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
        pass


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
    target_labels = _baemin_center_labels(config)

    select = page.locator("select").first
    if await select.count():
        if config.baemin_center_id.strip():
            try:
                await select.select_option(value=config.baemin_center_id.strip(), timeout=config.page_timeout_seconds)
                await page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return
            except Exception:
                pass
        for label in target_labels:
            try:
                await select.select_option(label=label, timeout=config.page_timeout_seconds)
                break
            except Exception:
                continue
        else:
            raise RuntimeError("배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다")
    else:
        await _click_first_visible_text(page, *target_labels)

    await page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass


def _has_configured_baemin_center(config: AppConfig) -> bool:
    return bool(config.baemin_center_id.strip() or config.baemin_center_name.strip())


def _baemin_center_labels(config: AppConfig) -> list[str]:
    center_name = config.baemin_center_name.strip()
    center_id = config.baemin_center_id.strip()
    labels: list[str] = []
    if center_name and center_id:
        labels.extend([f"{center_name} ({center_id})", f"{center_name}({center_id})"])
    if center_name:
        labels.append(center_name)
    if center_id:
        labels.append(center_id)
    return labels


async def _click_first_visible_text(page: Any, *texts: str) -> None:
    for text in texts:
        locator = page.get_by_text(text, exact=True).first
        if await locator.count():
            await locator.click(timeout=5_000)
            return
    raise RuntimeError("배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다")


async def _click_baemin_refresh_button(page: Any) -> None:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        PlaywrightTimeoutError = TimeoutError

    await page.get_by_role("button", name="새로고침", exact=True).click(timeout=5_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
        pass


def _validate_baemin_center_in_html(config: AppConfig, html: str, *, require_evidence: bool = False) -> None:
    expected_name = config.baemin_center_name.strip()
    expected_id = config.baemin_center_id.strip()
    if not expected_name and not expected_id:
        return

    parser = _BaeminCenterEvidenceParser()
    parser.feed(html)
    if require_evidence and not parser.evidence:
        raise RuntimeError(
            "배민 센터 검증 실패: Chrome 화면에서 센터 정보를 확인하지 못했습니다.\n"
            f"설정 센터명: {expected_name or '(비어 있음)'}\n"
            f"설정 센터 ID: {expected_id or '(비어 있음)'}"
        )

    matches: list[_BaeminCenterEvidence] = []
    mismatches: list[_BaeminCenterEvidence] = []
    unverifiable: list[_BaeminCenterEvidence] = []
    for evidence in parser.evidence:
        if expected_id:
            if evidence.center_id:
                if _normalize_center_id(evidence.center_id) == _normalize_center_id(expected_id):
                    matches.append(evidence)
                else:
                    mismatches.append(evidence)
                continue
            if expected_name and evidence.name and not _center_name_matches(evidence.name, expected_name, evidence.center_id):
                mismatches.append(evidence)
            else:
                unverifiable.append(evidence)
            continue

        if expected_name and evidence.name:
            if _center_name_matches(evidence.name, expected_name, evidence.center_id):
                matches.append(evidence)
            else:
                mismatches.append(evidence)

    if mismatches:
        evidence = mismatches[0]
        raise RuntimeError(
            "배민 센터 검증 실패: 설정한 센터와 Chrome 화면에서 확인된 센터가 다릅니다.\n"
            f"설정 센터명: {expected_name or '(비어 있음)'}\n"
            f"설정 센터 ID: {expected_id or '(비어 있음)'}\n"
            f"화면 센터명: {evidence.name or '(확인 불가)'}\n"
            f"화면 센터 ID: {evidence.center_id or '(확인 불가)'}"
        )

    if matches:
        return

    if require_evidence:
        raise RuntimeError(
            "배민 센터 검증 실패: Chrome 화면에서 비교 가능한 센터 정보를 확인하지 못했습니다.\n"
            f"설정 센터명: {expected_name or '(비어 있음)'}\n"
            f"설정 센터 ID: {expected_id or '(비어 있음)'}"
        )


@dataclass(frozen=True)
class _BaeminCenterEvidence:
    name: str
    center_id: str


class _BaeminCenterEvidenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.evidence: list[_BaeminCenterEvidence] = []
        self._select_stack: list[bool] = []
        self._current_option: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = _attrs_to_dict(attrs)
        if tag == "select":
            self._select_stack.append(_attrs_look_like_baemin_center(attrs_dict))
        elif tag == "option" and _attrs_mark_selected(attrs_dict):
            self._current_option = {
                "in_center_select": any(self._select_stack),
                "attrs": attrs_dict,
                "text": [],
            }
        elif tag == "input" and _attrs_look_like_baemin_center(attrs_dict):
            value = attrs_dict.get("value", "").strip()
            if value:
                self.evidence.append(_BaeminCenterEvidence(name="", center_id=value))

    def handle_data(self, data: str) -> None:
        if self._current_option is not None:
            self._current_option["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "option" and self._current_option is not None:
            attrs = self._current_option["attrs"]
            name = _normalize_visible_text("".join(self._current_option["text"]))
            center_id = attrs.get("value", "").strip() or _extract_baemin_center_id(name)
            if (
                self._current_option["in_center_select"]
                or _looks_like_baemin_center_value(center_id)
                or _looks_like_baemin_center_name(name)
            ):
                self.evidence.append(_BaeminCenterEvidence(name=name, center_id=center_id))
            self._current_option = None
        elif tag == "select" and self._select_stack:
            self._select_stack.pop()


def _attrs_to_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.lower(): value or "" for name, value in attrs}


def _attrs_mark_selected(attrs: dict[str, str]) -> bool:
    if attrs.get("aria-selected", "").strip().lower() == "true":
        return True
    if "selected" not in attrs:
        return False
    return attrs["selected"].strip().lower() not in {"false", "0", "no"}


def _attrs_look_like_baemin_center(attrs: dict[str, str]) -> bool:
    text = " ".join([*attrs.keys(), *attrs.values()]).casefold()
    return any(token in text for token in ("center", "센터", "협력사", "partner"))


def _looks_like_baemin_center_value(value: str) -> bool:
    return bool(re.fullmatch(r"DP[A-Z0-9_-]+", value.strip(), flags=re.IGNORECASE))


def _looks_like_baemin_center_name(name: str) -> bool:
    return "센터" in name or "협력사" in name


def _extract_baemin_center_id(text: str) -> str:
    match = re.search(r"\bDP[A-Z0-9_-]+\b", text, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def _center_name_matches(actual_name: str, expected_name: str, actual_id: str) -> bool:
    expected = _normalize_center_name(expected_name)
    if not expected:
        return True
    return expected in _center_name_candidates(actual_name, actual_id)


def _center_name_candidates(name: str, center_id: str) -> set[str]:
    candidates = {_normalize_center_name(name)}
    if center_id:
        candidates.add(_normalize_center_name(name.replace(center_id, "")))
    candidates.add(_normalize_center_name(re.sub(r"\([^)]*\)", "", name)))
    candidates.add(_normalize_center_name(re.sub(r"\bDP[A-Z0-9_-]+\b", "", name, flags=re.IGNORECASE)))
    return {candidate for candidate in candidates if candidate}


def _normalize_center_id(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _normalize_center_name(value: str) -> str:
    return re.sub(r"[\s()\[\]{}（）]+", "", value).casefold()


def _normalize_visible_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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
    pages_list = list(pages)
    exact_matches = [page for page in pages_list if _url_matches_exact(str(page.url), target_url)]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None
    path_matches = [page for page in pages_list if _url_matches(str(page.url), target_url)]
    return path_matches[0] if len(path_matches) == 1 else None


def _url_matches_exact(page_url: str, target_url: str) -> bool:
    page = urlsplit(page_url)
    target = urlsplit(target_url)
    return (
        page.scheme == target.scheme
        and page.netloc == target.netloc
        and _normalize_path(page.path) == _normalize_path(target.path)
        and _query_items(page.query) == _query_items(target.query)
    )


def _url_matches(page_url: str, target_url: str) -> bool:
    page = urlsplit(page_url)
    target = urlsplit(target_url)
    return (
        page.scheme == target.scheme
        and page.netloc == target.netloc
        and _normalize_path(page.path) == _normalize_path(target.path)
    )


def _normalize_path(path: str) -> str:
    return path.rstrip("/") or "/"


def _query_items(query: str) -> list[tuple[str, str]]:
    return sorted(parse_qsl(query, keep_blank_values=True))


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
