from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from html.parser import HTMLParser
import re
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .browser_launcher import (
    BrowserActionRequiredError,
    CdpUnavailableError,
    ensure_local_cdp_address,
)
from .config import (
    DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL,
    DEFAULT_BAEMIN_DELIVERY_HISTORY_URL,
    AppConfig,
)
from .models import CurrentScreenSnapshot
from .parser import (
    BaeminDeliveryHistoryTable,
    MissingPerformanceDataError,
    baemin_delivery_history_to_snapshot,
    has_today_delivery_status,
    parse_achievement_report_text,
    parse_baemin_delivery_history_html,
    parse_current_screen_html,
)


def crawl_current_screen(
    config: AppConfig,
    *,
    fetch_html: Callable[[AppConfig], str] | None = None,
    fetch_cancel_summary: Callable[[AppConfig], CurrentScreenSnapshot | None] | None = None,
) -> CurrentScreenSnapshot:
    content = (fetch_html or fetch_page_html)(config)
    if _looks_like_baemin_achievement_report(content):
        snapshot = parse_achievement_report_text(
            content,
            center_id=config.baemin_center_id,
            center_name=config.baemin_center_name,
        )
        if fetch_cancel_summary is not None:
            cancel = fetch_cancel_summary(config)
        elif fetch_html is None:
            cancel = crawl_baemin_cancel_summary(config)
        else:
            cancel = None
        if cancel is not None:
            snapshot = _merge_cancel_rate(snapshot, cancel)
        return snapshot
    if fetch_html is not None:
        _validate_baemin_center_in_html(config, content, require_evidence=True)
    return parse_current_screen_html(content)


def _merge_cancel_rate(
    snapshot: CurrentScreenSnapshot, cancel: CurrentScreenSnapshot
) -> CurrentScreenSnapshot:
    return replace(
        snapshot,
        cancel_rate=cancel.reject_rate,
        active_riders=cancel.active_riders,
    )


_BAEMIN_HISTORY_PAGE_SIZE = 100
_BAEMIN_HISTORY_MAX_PAGES = 20


def _baemin_history_base_url(config: AppConfig) -> str:
    # 운영 환경이 BAEMIN_DELIVERY_HISTORY_URL(=coupang_eats_url)로 프록시/스테이징 등
    # 커스텀 host/쿼리를 쓸 수 있으므로, 설정값의 path가 배달현황(/delivery/history)을
    # 가리키면 host/path/쿼리를 그대로 보존해 base로 쓴다(쿠팡 _peak_dashboard_url과
    # 동일하게 path 기준 판단). 주 URL은 보통 달성현황 /delivery/report라, 배달현황
    # 경로가 아니면 기본 URL로 둔다.
    configured = config.coupang_eats_url.strip()
    if configured and _normalize_path(urlsplit(configured).path) == "/delivery/history":
        return configured
    return DEFAULT_BAEMIN_DELIVERY_HISTORY_URL


def _baemin_history_page_url(config: AppConfig, page_index: int) -> str:
    return _delivery_history_page_url(
        _baemin_history_base_url(config), page_index, _BAEMIN_HISTORY_PAGE_SIZE
    )


def crawl_baemin_cancel_summary(config: AppConfig) -> CurrentScreenSnapshot | None:
    try:
        tables = _fetch_baemin_delivery_history_tables(config)
        if not tables:
            return None
        return baemin_delivery_history_to_snapshot(_combine_history_page_tables(tables))
    except Exception:
        return None


def _combine_history_page_tables(
    tables: list[BaeminDeliveryHistoryTable],
) -> BaeminDeliveryHistoryTable:
    return BaeminDeliveryHistoryTable(
        headers=tables[0].headers,
        summary=tables[0].summary,
        riders=[rider for table in tables for rider in table.riders],
    )


def _fetch_baemin_delivery_history_tables(
    config: AppConfig,
) -> list[BaeminDeliveryHistoryTable]:
    if config.browser_mode == "cdp":
        return _fetch_baemin_history_tables_via_cdp(config)
    if config.browser_mode == "persistent":
        return _fetch_baemin_history_tables_via_persistent_context(config)
    raise ValueError("브라우저 연결 방식은 cdp 또는 persistent 중 하나여야 합니다")


def _fetch_baemin_history_tables_via_cdp(
    config: AppConfig,
) -> list[BaeminDeliveryHistoryTable]:
    ensure_local_cdp_address(config.cdp_url)
    return asyncio.run(_fetch_baemin_history_tables_via_cdp_async(config))


async def _fetch_baemin_history_tables_via_cdp_async(
    config: AppConfig,
) -> list[BaeminDeliveryHistoryTable]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(config.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        try:
            return await _collect_baemin_history_tables(page, config)
        finally:
            await page.close()


def _baemin_history_page_count(total_count: int, served_page_size: int) -> int:
    # 총건수(서버가 노출하는 '총 N건')를 '첫 페이지가 실제로 내려준 라이더 수'로 나눠
    # 페이지 수를 정한다. 요청 size=100을 서버가 무시하고 20/50으로 떨어뜨려도, 실제
    # 응답 행수를 분모로 쓰므로 끝까지 읽는다(예: 총109/응답20 → 6페이지). 첫 페이지가
    # 빈 응답(0행)이면 더 읽어도 의미 없으니 1페이지로 둔다.
    # 여기선 '필요한' 페이지 수를 그대로 돌려주고, 상한(_BAEMIN_HISTORY_MAX_PAGES) 초과
    # 여부는 호출 측이 판단한다 — 상한을 넘으면 조용히 자르지 않고 보조 수집 전체를 폐기한다.
    if total_count <= 0 or served_page_size <= 0:
        return 1
    return max(1, (total_count + served_page_size - 1) // served_page_size)


def _ensure_baemin_history_page_count_within_cap(page_count: int) -> None:
    # 상한을 넘는 초대형 센터는 일부만 읽고 정상 스냅샷처럼 보내면 active_riders가 누락된다.
    # 조용히 자르는 대신 예외로 보조 수집 전체를 폐기한다(crawl_baemin_cancel_summary가 None).
    if page_count > _BAEMIN_HISTORY_MAX_PAGES:
        raise MissingPerformanceDataError(
            f"배민 배달현황 페이지 수가 상한({_BAEMIN_HISTORY_MAX_PAGES})을 초과했습니다: {page_count}"
        )


def _ensure_baemin_history_total_count_available(
    total_count: int, served_page_size: int
) -> None:
    if total_count <= 0 and served_page_size > 0:
        raise MissingPerformanceDataError(
            "배민 배달현황 총건수를 확인하지 못해 전체 페이지 수를 계산할 수 없습니다"
        )


def _parse_validated_history_table(html: str, config: AppConfig) -> BaeminDeliveryHistoryTable:
    # 배달현황 HTML이 설정 센터(A)가 아니라 다른 센터(B) 데이터면, 달성현황(A)에 B의
    # 취소율/수행중인원을 섞어 보내게 된다. 파싱 전에 센터 일치를 강제해 그걸 막는다.
    # require_evidence=True: 센터가 설정돼 있는데 화면에서 센터 단서를 못 찾으면 '검증 불가'도
    # RuntimeError로 폐기한다(fetch_page_html의 주 페이지 검증과 동일 기준). 센터가 미설정이면
    # _validate_baemin_center_in_html이 곧장 통과시킨다.
    _validate_baemin_center_in_html(config, html, require_evidence=True)
    return parse_baemin_delivery_history_html(html)


async def _collect_baemin_history_tables(
    page: Any, config: AppConfig
) -> list[BaeminDeliveryHistoryTable]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    tables: list[BaeminDeliveryHistoryTable] = []
    page_count = 1
    page_index = 0
    while page_index < page_count:
        url = _baemin_history_page_url(config, page_index)
        await _goto_page(page, url, config)
        if _url_matches(str(page.url), _BAEMIN_CENTER_CHANGE_URL):
            await _select_baemin_center(page, config)
            await _goto_page(page, url, config)
        try:
            await page.locator("table").first.wait_for(timeout=config.page_timeout_seconds)
        except PlaywrightTimeoutError:
            pass
        # 첫 페이지의 파싱 실패는 보조 수집을 그냥 건너뛰면 되지만(None), 둘째 페이지부터의
        # 실패는 일부 데이터만 정상값처럼 흘려보내는 셈이라 더 위험하다. 그래서 첫 페이지
        # 이후의 어떤 실패도 예외를 그대로 올려(crawl_baemin_cancel_summary가 None 반환)
        # 보조 수집 전체를 폐기한다.
        if page_index == 0:
            try:
                table = _parse_validated_history_table(await page.content(), config)
            except MissingPerformanceDataError:
                break
            total_count = await _delivery_history_total_count(page)
            _ensure_baemin_history_total_count_available(total_count, len(table.riders))
            # 페이지 수는 '첫 페이지가 실제로 내려준 행수'로 나눈다(요청 size가 아님).
            page_count = _baemin_history_page_count(total_count, len(table.riders))
            _ensure_baemin_history_page_count_within_cap(page_count)
        else:
            table = _parse_validated_history_table(await page.content(), config)
        tables.append(table)
        page_index += 1
    return tables


def _fetch_baemin_history_tables_via_persistent_context(
    config: AppConfig,
) -> list[BaeminDeliveryHistoryTable]:
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
            tables: list[BaeminDeliveryHistoryTable] = []
            page_count = 1
            page_index = 0
            while page_index < page_count:
                url = _baemin_history_page_url(config, page_index)
                page.goto(url, wait_until="domcontentloaded", timeout=config.page_timeout_seconds)
                if _url_matches(str(page.url), _BAEMIN_CENTER_CHANGE_URL):
                    _select_baemin_center_sync(page, config)
                    page.goto(url, wait_until="domcontentloaded", timeout=config.page_timeout_seconds)
                try:
                    page.locator("table").first.wait_for(timeout=config.page_timeout_seconds)
                except PlaywrightTimeoutError:
                    pass
                # 첫 페이지 이후의 파싱/검증 실패는 예외를 그대로 올려 보조 수집 전체를
                # 폐기한다(부분 데이터를 정상값처럼 보내지 않기 위해. async 경로와 동일 정책).
                if page_index == 0:
                    try:
                        table = _parse_validated_history_table(page.content(), config)
                    except MissingPerformanceDataError:
                        break
                    total_count = _delivery_history_total_count_sync(page)
                    _ensure_baemin_history_total_count_available(total_count, len(table.riders))
                    page_count = _baemin_history_page_count(total_count, len(table.riders))
                    _ensure_baemin_history_page_count_within_cap(page_count)
                else:
                    table = _parse_validated_history_table(page.content(), config)
                tables.append(table)
                page_index += 1
            return tables
        finally:
            context.close()


def fetch_page_html(config: AppConfig) -> str:
    if config.browser_mode == "cdp":
        html = fetch_page_html_via_cdp(config)
    elif config.browser_mode == "persistent":
        html = fetch_page_html_via_persistent_context(config)
    else:
        raise ValueError("브라우저 연결 방식은 cdp 또는 persistent 중 하나여야 합니다")
    if not _looks_like_baemin_achievement_report(html):
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
    except (CdpUnavailableError, BrowserActionRequiredError):
        # 로그인 필요(BrowserActionRequiredError)는 일시 로딩 실패가 아니므로 RuntimeError 로
        # 뭉개지 말고 그대로 올려 크롤 워커가 AUTH_REQUIRED 로 분류하게 한다.
        raise
    except Exception as exc:
        # Chrome이 CDP 포트에 안 떠 있어 connect_over_cdp가 거부되면(ECONNREFUSED 등),
        # 이건 "Chrome 준비하기"가 안 된 환경 오류이지 일시적 페이지 로딩 실패가 아니다.
        # 스케줄러 5초 재시도로는 절대 복구되지 않으므로 별도 예외로 구분해, UI가 정규
        # 주기까지 기다리며 로그를 폭주시키지 않게 한다(ui._run_once_background 참고).
        if _is_cdp_connection_failure(exc):
            raise CdpUnavailableError(
                f"Chrome CDP 연결 실패: {config.cdp_url}\n"
                "'준비하기'로 이 탭의 Chrome을 --remote-debugging-port 옵션과 전용 "
                "프로필로 먼저 실행하고, 배민 달성현황 페이지에 로그인해 두세요.\n"
                f"상세 오류: {type(exc).__name__}: {exc}"
            ) from exc
        raise RuntimeError(
            f"Chrome CDP 연결 또는 배민 달성현황 수집 실패: {config.cdp_url}\n"
            "Chrome을 --remote-debugging-port=9222 옵션과 전용 프로필로 실행하고, "
            "배민 달성현황 페이지에 로그인된 상태인지 확인하세요.\n"
            f"상세 오류: {type(exc).__name__}: {exc}"
        ) from exc


def _is_cdp_connection_failure(exc: Exception) -> bool:
    # connect_over_cdp가 포트에 못 붙을 때 나는 신호들. 포트에 Chrome이 없거나(거부),
    # 주소를 못 찾거나, 핸드셰이크 자체가 실패한 경우를 "환경 오류"로 본다. 페이지가
    # 떠 있는 상태의 일시적 로딩 타임아웃과는 구분해야 하므로 연결 단계 신호만 본다.
    message = str(exc).casefold()
    signals = (
        "econnrefused",
        "connect_over_cdp",
        "connection refused",
        "retrieving websocket url",
        "enotfound",
        "ehostunreach",
    )
    return any(signal in message for signal in signals)


async def _fetch_page_html_via_crawl4ai_cdp(config: AppConfig) -> str:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(config.cdp_url)
        # CDP 대상은 사용자가 켜 둔 Chrome이므로 browser.close()를 호출하지 않는다.
        # 여러 아이디/프로필을 운영 중일 때 사용자의 Chrome 창이나 로그인 세션을
        # 닫지 않도록, 쿠팡 크롤러와 동일하게 닫지 않는 정책으로 맞춘다.
        page = await _open_baemin_delivery_history_page(browser, config)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeoutError:
            pass
        html = await _collect_baemin_achievement_report_text(page, config)

    if not html:
        raise RuntimeError("배민 달성현황 텍스트를 가져오지 못했습니다")
    return str(html)


async def _ensure_baemin_center_selected_via_cdp(config: AppConfig) -> None:
    if not config.baemin_center_id:
        return

    ensure_local_cdp_address(config.cdp_url)

    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(config.cdp_url)
        # CDP 대상은 사용자가 켜 둔 Chrome이므로 여기서도 browser.close()를 호출하지 않는다.
        pages = _browser_pages(browser)
        page = _select_page_by_url(pages, _baemin_report_url(config))
        if page is None:
            page = _select_page_by_url(pages, config.coupang_eats_url)
        if page is None:
            page = _select_page_by_url(pages, _BAEMIN_CENTER_CHANGE_URL)
        if page is None:
            # 로그인된 배민 탭이 없으면 새 로그인 탭을 열지 않고 멈춘다(난입 차단, 인증 필요).
            if not _baemin_has_logged_in_page(pages):
                raise BrowserActionRequiredError(_baemin_login_required_message())
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

        await page.goto(
            _baemin_report_url(config),
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


async def _open_baemin_delivery_history_page(browser: Any, config: AppConfig) -> Any:
    pages = _browser_pages(browser)
    report_url = _baemin_report_url(config)
    page = _select_page_by_url(pages, report_url)
    if page is None:
        page = _select_page_by_url(pages, config.coupang_eats_url)
    if page is None:
        page = _select_page_by_url(pages, _BAEMIN_CENTER_CHANGE_URL)
    if page is None:
        # 재사용할 배민 운영 화면이 없다 — 로그인 안 된 상태에서 새 탭을 열고 report_url 로
        # 이동하면 매 주기(~5초)마다 '배민 로그인 사이트' 탭만 쌓인다(쿠팡 전용 고객엔 난입).
        # 로그인된 배민 탭이 하나도 없으면 새 탭을 열지 않고 fail-closed 로 멈춘다(인증 필요).
        if not _baemin_has_logged_in_page(pages):
            raise BrowserActionRequiredError(_baemin_login_required_message())
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

    if _has_configured_baemin_center(config):
        await _goto_page(page, _BAEMIN_CENTER_CHANGE_URL, config)
        await _select_baemin_center(page, config)

    await _goto_page(page, report_url, config)
    if _url_matches(str(page.url), _BAEMIN_CENTER_CHANGE_URL):
        await _select_baemin_center(page, config)
        await _goto_page(page, report_url, config)

    if not _url_matches(str(page.url), report_url):
        await _goto_page(page, report_url, config)

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


async def _collect_baemin_achievement_report_text(page: Any, config: AppConfig) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (config.page_timeout_seconds / 1000)
    expected_id = config.baemin_center_id.strip().upper()
    last_report_text = ""

    saw_weekly_without_id = False
    while loop.time() < deadline:
        await _scroll_baemin_report(page)
        for text in await _baemin_report_text_candidates(page):
            if "주간 배달 현황" not in text:
                continue
            if expected_id and expected_id not in text.upper():
                saw_weekly_without_id = True
                continue
            last_report_text = text
            if not expected_id or has_today_delivery_status(text, center_id=expected_id):
                return text
        await page.wait_for_timeout(1_000)

    if last_report_text:
        return last_report_text
    if saw_weekly_without_id:
        raise RuntimeError(
            "배민 달성현황은 열렸지만 설정한 센터 ID 행을 찾지 못했습니다.\n"
            f"설정 센터 ID: {config.baemin_center_id or '(비어 있음)'}"
        )
    raise RuntimeError("배민 달성현황의 '주간 배달 현황' 영역을 찾지 못했습니다")


async def _baemin_report_text_candidates(page: Any) -> list[str]:
    candidates = []
    frames = list(getattr(page, "frames", []) or [])
    if page not in frames:
        frames.append(page)

    for frame in frames:
        try:
            text = await frame.locator("body").inner_text(timeout=2_000)
        except Exception:
            continue
        normalized = _normalize_report_text(text)
        if normalized:
            candidates.append(normalized)
    return candidates


async def _scroll_baemin_report(page: Any) -> None:
    for frame in list(getattr(page, "frames", []) or [page]):
        try:
            await frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            continue


def _parse_delivery_history_total_count(text: str) -> int:
    match = re.search(r"총\s*(?P<count>\d[\d,]*)\s*건", text)
    if not match:
        return 0
    return int(match.group("count").replace(",", ""))


async def _delivery_history_total_count(page: Any) -> int:
    text = await page.locator("body").inner_text(timeout=10_000)
    return _parse_delivery_history_total_count(text)


def _delivery_history_total_count_sync(page: Any) -> int:
    text = page.locator("body").inner_text(timeout=10_000)
    return _parse_delivery_history_total_count(text)


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


def _select_baemin_center_sync(page: Any, config: AppConfig) -> None:
    target_labels = _baemin_center_labels(config)

    select = page.locator("select").first
    if select.count():
        if config.baemin_center_id.strip():
            try:
                select.select_option(value=config.baemin_center_id.strip(), timeout=config.page_timeout_seconds)
                page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return
            except Exception:
                pass
        for label in target_labels:
            try:
                select.select_option(label=label, timeout=config.page_timeout_seconds)
                break
            except Exception:
                continue
        else:
            raise RuntimeError("배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다")
    else:
        _click_first_visible_text_sync(page, *target_labels)

    page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass


def _click_first_visible_text_sync(page: Any, *texts: str) -> None:
    for text in texts:
        locator = page.get_by_text(text, exact=True).first
        if locator.count():
            locator.click(timeout=5_000)
            return
    raise RuntimeError("배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다")


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


# ``센터명(DP아이디)`` 형태의 화면 텍스트에서 센터명과 ID를 함께 잡는다. 이름은 바로
# 앞의 괄호 없는 텍스트 묶음으로 본다(공백/구분자가 섞일 수 있어 [^()]로 받되 normalize).
_CENTER_LABEL_WITH_ID_PATTERN = re.compile(
    r"(?P<name>[^()\n\r]*?)\s*\(\s*(?P<id>DP[A-Z0-9_-]+)\s*\)",
    flags=re.IGNORECASE,
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
        # 선택된 센터가 드롭다운이 아니라 일반 텍스트(예: <span>센터명(DP...)</span>)로만
        # 표시되는 화면도 있다. 이 경우 option/input 기반 수집은 증거를 0개로 보고
        # "센터 정보를 확인하지 못했습니다"로 실패한다. ``센터명(DP아이디)`` 형태의
        # 텍스트를 직접 잡아 증거로 추가한다. DP 아이디가 괄호 안에 있는 라벨은 화면의
        # 센터 표기 형식이라 오탐 위험이 낮고, ID가 다르면 기존 mismatch 검증에 그대로
        # 걸린다.
        self._collect_center_label_from_text(data)

    def _collect_center_label_from_text(self, data: str) -> None:
        for match in _CENTER_LABEL_WITH_ID_PATTERN.finditer(data):
            name = _normalize_visible_text(match.group("name"))
            center_id = match.group("id").strip()
            if center_id:
                self.evidence.append(_BaeminCenterEvidence(name=name, center_id=center_id))

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
                _baemin_report_url(config),
                wait_until="domcontentloaded",
                timeout=config.page_timeout_seconds,
            )
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass

            return _collect_baemin_achievement_report_text_sync(page, config)
        finally:
            context.close()


def _collect_baemin_achievement_report_text_sync(page: Any, config: AppConfig) -> str:
    import time

    deadline = time.monotonic() + (config.page_timeout_seconds / 1000)
    expected_id = config.baemin_center_id.strip().upper()
    last_report_text = ""

    saw_weekly_without_id = False
    while time.monotonic() < deadline:
        _scroll_baemin_report_sync(page)
        for text in _baemin_report_text_candidates_sync(page):
            if "주간 배달 현황" not in text:
                continue
            if expected_id and expected_id not in text.upper():
                saw_weekly_without_id = True
                continue
            last_report_text = text
            if not expected_id or has_today_delivery_status(text, center_id=expected_id):
                return text
        page.wait_for_timeout(1_000)

    if last_report_text:
        return last_report_text
    if saw_weekly_without_id:
        raise RuntimeError(
            "배민 달성현황은 열렸지만 설정한 센터 ID 행을 찾지 못했습니다.\n"
            f"설정 센터 ID: {config.baemin_center_id or '(비어 있음)'}"
        )
    raise RuntimeError("배민 달성현황의 '주간 배달 현황' 영역을 찾지 못했습니다")


def _baemin_report_text_candidates_sync(page: Any) -> list[str]:
    candidates = []
    frames = list(getattr(page, "frames", []) or [])
    if page not in frames:
        frames.append(page)

    for frame in frames:
        try:
            text = frame.locator("body").inner_text(timeout=2_000)
        except Exception:
            continue
        normalized = _normalize_report_text(text)
        if normalized:
            candidates.append(normalized)
    return candidates


def _scroll_baemin_report_sync(page: Any) -> None:
    for frame in list(getattr(page, "frames", []) or [page]):
        try:
            frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            continue


def _browser_pages(browser: Any) -> list[Any]:
    pages: list[Any] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    return pages


#: 로그인된 배민 운영 화면은 모두 이 호스트에 있다(달성/배달현황·센터변경). 로그아웃 시
#: 배민은 별도 로그인/회원 호스트로 redirect 하므로, 이 호스트의 페이지가 하나도 없으면
#: "로그인 필요"로 본다.
_BAEMIN_LOGGED_IN_HOST = "deliverycenter.baemin.com"


def _baemin_has_logged_in_page(pages: Iterable[Any]) -> bool:
    """열린 탭 중 로그인된 배민 운영 화면(deliverycenter 호스트)이 하나라도 있으면 True."""

    for page in pages:
        host = urlsplit(str(getattr(page, "url", ""))).netloc.casefold()
        if host == _BAEMIN_LOGGED_IN_HOST:
            return True
    return False


def _baemin_login_required_message() -> str:
    return (
        "배민 로그인이 만료되었거나 로그인 화면으로 이동했습니다.\n"
        "'인증 시작'으로 배민 달성현황 페이지에 로그인해 두세요. "
        "스케줄러는 로그인 탭을 자동으로 새로 열지 않고 멈춥니다."
    )


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


def _baemin_report_url(config: AppConfig) -> str:
    configured = config.coupang_eats_url.strip()
    if not configured:
        return DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL

    parsed = urlsplit(configured)
    if parsed.netloc == "deliverycenter.baemin.com" and _normalize_path(parsed.path) == "/delivery/report":
        return configured
    return DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL


def _looks_like_baemin_achievement_report(content: str) -> bool:
    text = content if "<" not in content else _normalize_visible_text(content)
    if "주간 배달 현황" not in text:
        return False
    if any(label in text for label in ("아침점심", "오후논피", "저녁피크", "심야논피")):
        return True
    period_pattern = r"\d+(?:,\d{3})*\s*/\s*\d+(?:,\d{3})*\s*\(\s*\d+(?:\.\d+)?\s*%\s*\)"
    return len(re.findall(period_pattern, text)) >= 4


def _normalize_report_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


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
