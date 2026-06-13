from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlsplit

from rider_crawl.browser_launcher import BrowserActionRequiredError, CdpUnavailableError, ensure_local_cdp_address
from rider_crawl.config import AppConfig
from rider_crawl.models import CurrentScreenSnapshot, PerformanceSnapshot

from .parser import parse_current_screen_html, parse_peak_dashboard_html


def crawl_current_screen(
    config: AppConfig,
    *,
    fetch_html: Callable[[AppConfig], str] | None = None,
) -> CurrentScreenSnapshot:
    html = (fetch_html or fetch_page_html)(config)
    snapshot = parse_current_screen_html(html)
    _validate_coupang_center(config, snapshot)
    return snapshot


def crawl_performance_snapshot(
    config: AppConfig,
    *,
    fetch_peak_dashboard_html: Callable[[AppConfig], str] | None = None,
) -> PerformanceSnapshot:
    # 쿠팡 탭은 로그인 직후 열리는 peak-dashboard 한 페이지만 읽는다. 주 URL
    # (``coupang_eats_url``, UI의 '실적/달성현황 URL')에 peak-dashboard가 들어온다.
    # rider-performance 페이지는 더 이상 요구하지 않으므로(그 탭이 없어도 오류가 나지
    # 않는다) ``current_screen``은 ``None``으로 둔다. '수행중인인원' 줄만 그 페이지에서
    # 왔는데, peak-only로 바꾸면서 메시지에서도 그 줄을 생략한다.
    peak_dashboard_html = (
        fetch_peak_dashboard_html(config)
        if fetch_peak_dashboard_html
        else fetch_page_html(config, target_url=config.coupang_eats_url)
    )
    # 피크 대시보드 헤딩에 기대 센터가 노출되면 그것으로 다른 계정/오래된 탭을 막는다.
    # 헤딩이 없으면(피크 페이지가 센터를 노출하지 않으면) 기존처럼 검증을 건너뛴다.
    _validate_coupang_center_in_peak_html(config, peak_dashboard_html)
    return PerformanceSnapshot(
        current_screen=None,
        peak_dashboard=parse_peak_dashboard_html(peak_dashboard_html),
    )


def _validate_coupang_center(config: AppConfig, snapshot: CurrentScreenSnapshot) -> None:
    """Reject a snapshot that does not belong to the expected Coupang center.

    Coupang의 계정/센터/상점은 CDP 포트와 Chrome 프로필 로그인으로만 결정되므로,
    포트나 프로필이 꼬이면 다른 쿠팡 계정의 실적을 정상처럼 전송할 수 있다.
    설정에 기대 센터명(``baemin_center_name``, 쿠팡 탭에서는 기대 센터/상점명을
    재사용)이 있으면 화면 헤딩에서 읽은 센터명과 비교해 다른 계정을 차단한다.

    검증은 기본적으로 **exact match**다. 부분 문자열 매칭을 쓰면 "강남센터" 설정이
    "강남센터2"나 "다른강남센터" 화면에도 통과해 다른 계정 실적을 막지 못한다.
    여러 표기를 허용해야 하면 설정값에 ``;`` 또는 줄바꿈으로 alias를 나열한다.
    값이 비어 있으면 검증을 건너뛴다(기존 동작 유지).
    """

    expected_aliases = _coupang_center_aliases(config.baemin_center_name)
    if not expected_aliases:
        return

    actual_raw = snapshot.center_name.strip()
    actual = _normalize_coupang_center(actual_raw)
    if not actual:
        raise RuntimeError(
            "쿠팡 센터 검증 실패: 화면에서 센터명을 확인하지 못했습니다.\n"
            f"설정 센터명: {config.baemin_center_name.strip()}"
        )

    if actual not in expected_aliases:
        raise RuntimeError(
            "쿠팡 센터 검증 실패: 설정한 센터와 화면에서 확인된 센터가 다릅니다.\n"
            f"설정 센터명: {config.baemin_center_name.strip()}\n"
            f"화면 센터명: {actual_raw}"
        )


def _validate_coupang_center_in_peak_html(config: AppConfig, peak_html: str) -> None:
    """Reject a peak-dashboard HTML that does not belong to the expected center.

    피크 페이지의 **실제 선택 센터(헤딩)**만 추출해 alias와 exact 비교한다. 페이지
    전체 텍스트에 "포함"되는지만 보면, 실제 선택 센터가 다른데도 드롭다운/목록/이전
    값 같은 부수 텍스트(예: ``<option>강남센터</option>``)에 기대 센터명이 있으면
    잘못 통과한다. 그래서 헤딩(``h1``~``h3``)만 보고, 헤딩에 ``센터명 시프트(시간)…``
    형태로 시프트가 붙어 있으면 앞쪽 센터명만 떼어내 비교한다.
    피크 페이지에 센터 헤딩이 노출되지 않으면 실적 페이지 검증만 사용한다.
    기대 센터가 비어 있으면 검증을 건너뛴다(기존 동작 유지).
    """

    expected_aliases = _coupang_center_aliases(config.baemin_center_name)
    if not expected_aliases:
        return

    heading_centers = _coupang_peak_heading_centers(peak_html)
    if not heading_centers:
        return

    if not (heading_centers & expected_aliases):
        raise RuntimeError(
            "쿠팡 센터 검증 실패: 설정한 센터가 피크 대시보드 화면 헤딩과 일치하지 않습니다. "
            "다른 계정이거나 오래된 피크 탭이 열려 있을 수 있습니다.\n"
            f"설정 센터명: {config.baemin_center_name.strip()}\n"
            f"화면 헤딩 센터명: {', '.join(sorted(_coupang_peak_heading_center_raw(peak_html))) or '(확인 불가)'}"
        )


def _coupang_peak_heading_centers(peak_html: str) -> set[str]:
    return {_normalize_coupang_center(name) for name in _coupang_peak_heading_center_raw(peak_html)}


def _coupang_peak_heading_center_raw(peak_html: str) -> set[str]:
    parser = _CoupangHeadingParser()
    parser.feed(peak_html)
    # 센터명은 페이지 최상위 헤딩(가장 높은 레벨, 보통 ``h1``)에 노출된다. "피크타임별
    # 현황"/"시간대별 기록" 같은 섹션 제목은 더 낮은 레벨(``h2``/``h3``)이므로, 가장
    # 높은 레벨의 헤딩만 센터 후보로 본다.
    top_headings = parser.top_level_headings()
    centers: set[str] = set()
    for heading in top_headings:
        if _is_coupang_peak_section_heading(heading):
            continue
        center = _coupang_center_from_heading(heading)
        if center:
            centers.add(center)
    return centers


# 쿠팡 시프트명. 피크 페이지는 "저녁 피크"처럼 공백을 두고, 실적 페이지는 "오후논피크"
# 처럼 붙여 쓰기도 하므로 각 구성요소 사이 공백을 선택적으로 허용한다.
_COUPANG_SHIFT_PATTERN = (
    r"(?:아침|오전\s*피크|오후\s*피크"
    r"|점심\s*피크|점심\s*논피크|저녁\s*피크|저녁\s*논피크"
    r"|오전\s*논피크|오후\s*논피크|심야\s*논피크|논피크|피크)"
)

_COUPANG_PEAK_SECTION_HEADINGS = {
    "실시간오늘의실적",
    "피크타임별현황",
    "시간대별기록",
}


def _is_coupang_peak_section_heading(heading: str) -> bool:
    return _normalize_coupang_center(heading) in _COUPANG_PEAK_SECTION_HEADINGS


def _coupang_center_from_heading(heading: str) -> str:
    # 헤딩이 "센터명 시프트(09:00~12:30) …" 형태면 시프트 이후를 떼어 센터명만 남긴다.
    # 시프트명은 "저녁 피크"처럼 내부 공백이 있을 수 있으므로, 공백을 허용하는 알려진
    # 시프트 패턴으로 자른다. 일반 ``[가-힣]+`` 로 자르면 "저녁 피크"의 "저녁"까지만
    # 시프트로 보고 "…남부 저녁"을 센터명으로 잘못 남긴다.
    # 시프트 패턴이 없으면 헤딩 전체를 센터명으로 본다(예: 단독 ``<h1>센터명</h1>``).
    text = _normalize_visible_text(heading)
    match = re.match(rf"(?P<center>.+?)\s+{_COUPANG_SHIFT_PATTERN}\s*\(\d{{2}}:\d{{2}}~\d{{2}}:\d{{2}}\)", text)
    if match:
        return match.group("center").strip()
    return text


def _normalize_visible_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


class _CoupangHeadingParser(HTMLParser):
    """Collect heading text by level (h1~h3), ignoring other elements.

    드롭다운/option/목록 같은 부수 텍스트를 배제하고, 실제 선택 센터가 노출되는
    헤딩 텍스트만 레벨별로 모은다. ``top_level_headings()``는 가장 높은 레벨(작은
    숫자)의 헤딩만 돌려준다.
    """

    _HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3}

    def __init__(self) -> None:
        super().__init__()
        self.headings_by_level: dict[int, list[str]] = {}
        self._open_level: int | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        level = self._HEADING_LEVELS.get(tag.lower())
        if level is not None and self._open_level is None:
            self._open_level = level
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        level = self._HEADING_LEVELS.get(tag.lower())
        if level is not None and self._open_level == level:
            text = "".join(self._buffer).strip()
            if text:
                self.headings_by_level.setdefault(level, []).append(text)
            self._open_level = None
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._open_level is not None:
            self._buffer.append(data)

    def top_level_headings(self) -> list[str]:
        if not self.headings_by_level:
            return []
        top_level = min(self.headings_by_level)
        return list(self.headings_by_level[top_level])


def _coupang_center_aliases(value: str) -> set[str]:
    aliases = (re.split(r"[;\n]", value or ""))
    return {normalized for alias in aliases if (normalized := _normalize_coupang_center(alias))}


def _normalize_coupang_center(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


# 쿠팡 실적/대시보드 페이지 상단의 센터 탭(``<div class="slide-tab">센터명</div>``)
# 텍스트를 읽기 위한 JS. 활성 탭에는 ``slide-tab-active`` 클래스가 붙는다(실측 확인).
# 센터가 1개면 탭이 1개만 노출되고, 여러 센터 계정이 아직 어떤 센터도 고르지 않은
# 통합 상태("…협력사 N개" 헤딩)에서는 어떤 탭에도 active 클래스가 없을 수 있다.
# 그 경우 일치 탭을 그대로 눌러 해당 센터로 전환한다. 화면에 보이는(offsetParent !=
# null) 탭만 후보로 본다. 구버전 호환을 위해 ``.slide-tab``이 없으면 Ant Design 탭/
# ``role=tab``으로 떨어진다.
_COUPANG_CENTER_TAB_JS = """
() => {
  const seen = [];
  const collect = (nodes) => {
    for (const node of nodes) {
      if (!node || node.offsetParent === null) continue;
      const text = (node.innerText || node.textContent || '').trim();
      if (!text) continue;
      if (seen.some((entry) => entry.node === node)) continue;
      const cls = String(node.className || '');
      const selected =
        node.getAttribute('aria-selected') === 'true' ||
        /slide-tab-active|ant-tabs-tab-active|(?:^|\\s)active(?:\\s|$)|selected/.test(cls);
      seen.push({ node, text, selected });
    }
  };
  collect(document.querySelectorAll('.slide-tab'));
  if (seen.length === 0) collect(document.querySelectorAll('.ant-tabs-tab'));
  if (seen.length === 0) collect(document.querySelectorAll('[role="tab"]'));
  return seen.map(({ text, selected }) => ({ text, selected }));
}
"""


def _coupang_center_tab_label_matches(label: str, expected_aliases: set[str]) -> bool:
    """Return True when a center-tab label matches one of the expected aliases.

    탭 라벨은 짧은 센터명(예: ``양주중앙``)으로 뜨고, 실적 페이지 헤딩은 회사명을 붙여
    ``제이앤에이치플러스 양주중앙``으로 뜬다. 같은 설정값(``baemin_center_name``)이
    회사명 포함/미포함 어느 쪽이어도 탭을 찾도록, 정규화 후 양방향 부분일치를 본다.
    이 단계는 "탭을 눌러 이동"만 하고, 잘못된 센터는 이후 ``_validate_coupang_center``
    의 exact 검증이 그대로 막으므로 부분일치로 인한 오선택 위험이 낮다.
    """

    normalized_label = _normalize_coupang_center(label)
    if not normalized_label:
        return False
    for alias in expected_aliases:
        if normalized_label == alias or alias in normalized_label or normalized_label in alias:
            return True
    return False


def _select_coupang_center(page: Any, config: AppConfig, *, timeout_errors: tuple[type[BaseException], ...]) -> bool:
    """Click the center tab matching the configured center name, if present.

    쿠팡 실적/대시보드 화면은 한 계정에 여러 센터가 있으면 상단에 센터 탭
    (예: ``양주중앙 / 의정부남부 / 의정부중앙``)을 노출하고, 그중 하나만 활성화된다.
    배민의 센터 선택과 동일하게, 설정한 센터(``baemin_center_name``)에 맞는 탭을 찾아
    아직 활성이 아니면 클릭해 그 센터 화면으로 전환한다.

    - 기대 센터명이 비어 있으면 아무 것도 하지 않는다(기존 동작 유지).
    - 센터가 1개인 계정은 탭이 1개만(또는 0개) 노출되므로, 일치 탭이 없거나 이미
      활성이면 조용히 넘어간다. 다른 센터가 선택돼 있으면 이후
      ``_validate_coupang_center`` 검증이 막으므로 여기서 실패시키지 않는다.

    실제로 탭을 클릭해 화면을 전환했을 때만 ``True``를 돌려준다. 호출부는 이 값으로
    전환 후 페이지가 다시 준비됐는지 한 번만 더 기다린다(불필요한 재대기 방지).
    """

    expected_aliases = _coupang_center_aliases(config.baemin_center_name)
    if not expected_aliases:
        return False

    try:
        tabs = page.evaluate(_COUPANG_CENTER_TAB_JS)
    except Exception:
        return False

    match = next(
        (
            tab
            for tab in tabs
            if _coupang_center_tab_label_matches(str(tab.get("text", "")), expected_aliases)
        ),
        None,
    )
    if match is None or match.get("selected"):
        # 일치 탭이 없거나(단일 센터 등) 이미 선택돼 있으면 전환 불필요.
        return False

    label = str(match["text"]).strip()
    try:
        # 같은 라벨의 다른 요소(헤딩 등)를 누르지 않도록 탭 컨테이너 안에서만 찾는다.
        tab_locator = page.locator(".slide-tab, .ant-tabs-tab, [role=tab]").filter(has_text=label).first
        tab_locator.click(timeout=config.page_timeout_seconds)
    except Exception:
        # 탭 클릭이 실패해도 크롤링 자체는 계속한다. 잘못된 센터면 이후 검증이 막는다.
        return False

    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except timeout_errors:
        pass
    return True


def fetch_page_html(config: AppConfig, *, target_url: str | None = None) -> str:
    if config.browser_mode == "cdp":
        if target_url is None:
            return fetch_page_html_via_cdp(config)
        return fetch_page_html_via_cdp(config, target_url=target_url)
    if config.browser_mode == "persistent":
        if target_url is None:
            return fetch_page_html_via_persistent_context(config)
        return fetch_page_html_via_persistent_context(config, target_url=target_url)
    raise ValueError("브라우저 연결 방식은 cdp 또는 persistent 중 하나여야 합니다")


def fetch_page_html_via_cdp(config: AppConfig, *, target_url: str | None = None) -> str:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    ensure_local_cdp_address(config.cdp_url)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(config.cdp_url)
        except PlaywrightError as exc:
            # Chrome이 CDP 포트에 안 떠 있는 환경 오류. 스케줄러 5초 재시도로는 복구되지
            # 않으므로 CdpUnavailableError로 구분해 UI가 정규 주기까지 기다리게 한다.
            raise CdpUnavailableError(
                f"Chrome CDP 연결 실패: {config.cdp_url}\n"
                "'준비하기'로 이 탭의 Chrome을 --remote-debugging-port 옵션으로 먼저 "
                "실행한 뒤 다시 시도하세요."
            ) from exc

        # CDP 대상은 사용자가 켜 둔 Chrome이므로 여기서 browser.close()를 호출하지 않는다.
        return _fetch_target_page_content(
            browser,
            config,
            target_url=target_url or config.coupang_eats_url,
            load_timeout_errors=(PlaywrightTimeoutError,),
        )


def fetch_page_html_via_persistent_context(config: AppConfig, *, target_url: str | None = None) -> str:
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
                target_url or config.coupang_eats_url,
                wait_until="domcontentloaded",
                timeout=config.page_timeout_seconds,
            )
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass

            resolved_target = target_url or config.coupang_eats_url
            try:
                _wait_for_target_page_ready(
                    page,
                    config,
                    target_url=resolved_target,
                    timeout_errors=(PlaywrightTimeoutError,),
                )
            except BrowserActionRequiredError:
                # CDP 경로와 동일하게, 로그인 만료 시 자동 이메일 2FA가 켜져 있으면 한 번만
                # 복구를 시도하고 대상 페이지를 다시 준비시킨다. 꺼져 있거나 실패하면 raise.
                if not _try_recover_coupang_session(page, config, None):
                    raise
                _reload_target_page(
                    page,
                    config,
                    target_url=resolved_target,
                    load_timeout_errors=(PlaywrightTimeoutError,),
                )
                _wait_for_target_page_ready(
                    page,
                    config,
                    target_url=resolved_target,
                    timeout_errors=(PlaywrightTimeoutError,),
                )
            if _select_coupang_center(page, config, timeout_errors=(PlaywrightTimeoutError,)):
                _wait_for_target_page_ready(
                    page,
                    config,
                    target_url=resolved_target,
                    timeout_errors=(PlaywrightTimeoutError,),
                )
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
    target_url: str | None = None,
    load_timeout_errors: tuple[type[BaseException], ...] = (),
    recover_session: Callable[[Any, AppConfig], bool] | None = None,
) -> str:
    target_url = target_url or config.coupang_eats_url
    pages = _browser_pages(browser)
    page = _select_page_by_url(pages, target_url)
    if page is None:
        # 대상 탭을 못 찾았다. 로그인 만료로 대상 탭의 URL이 login/xauth로 바뀐 경우가
        # 있으므로, 자동 이메일 2FA가 켜져 있으면 열린 로그인 페이지에서 복구를 한 번
        # 시도하고 그 페이지를 대상으로 이어 간다. 복구 대상이 없거나 실패하면 기존처럼
        # 운영자 조치 필요 오류를 던진다.
        page = _recover_login_page_to_target(
            pages,
            config,
            target_url=target_url,
            recover_session=recover_session,
            load_timeout_errors=load_timeout_errors,
        )
        if page is None:
            _raise_coupang_page_action_required(pages, target_url)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except load_timeout_errors:
        pass
    try:
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
    except BrowserActionRequiredError:
        # 로그인 만료 감지. 자동 이메일 2FA가 켜져 있으면 딱 한 번 복구를 시도한 뒤,
        # 대상 화면을 다시 띄워 준비 상태를 재확인한다. 복구가 꺼져 있거나 실패하면
        # 기존처럼 BrowserActionRequiredError로 탭을 중지한다(빠른 재시도 금지).
        if not _try_recover_coupang_session(page, config, recover_session):
            raise
        _reload_target_page(page, config, target_url=target_url, load_timeout_errors=load_timeout_errors)
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
    if _select_coupang_center(page, config, timeout_errors=load_timeout_errors):
        # 탭을 눌러 다른 센터로 전환했으면, 새 센터 화면이 준비될 때까지 한 번 더 기다린다.
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
    return page.content()


def _recover_login_page_to_target(
    pages: list[Any],
    config: AppConfig,
    *,
    target_url: str,
    recover_session: Callable[[Any, AppConfig], bool] | None,
    load_timeout_errors: tuple[type[BaseException], ...],
) -> Any | None:
    """Recover an expired login tab whose URL drifted to login/xauth, if possible.

    대상 탭이 없을 때, 열려 있는 로그인 필요 페이지를 찾아 자동 이메일 2FA로 복구한다.
    복구가 꺼져 있거나 로그인 페이지가 없으면 ``None``을 돌려 호출부가 기존 오류 흐름을
    타게 한다. 복구 성공 후 대상 URL로 다시 연 뒤, 그 페이지 URL이 대상과 맞으면 그
    페이지를, 아니면 ``None``을 돌려준다(중복 탭 방지를 위해 새 탭은 만들지 않는다).
    """

    if not config.coupang_auto_email_2fa_enabled:
        return None

    login_page = _login_required_page(pages)
    if login_page is None:
        return None

    _reload_target_page(
        login_page,
        config,
        target_url=target_url,
        load_timeout_errors=load_timeout_errors,
    )
    if _url_matches(str(getattr(login_page, "url", "")), target_url):
        return login_page

    if not _try_recover_coupang_session(login_page, config, recover_session):
        return None

    _reload_target_page(login_page, config, target_url=target_url, load_timeout_errors=load_timeout_errors)
    if _url_matches(str(getattr(login_page, "url", "")), target_url):
        return login_page
    return None


def _try_recover_coupang_session(
    page: Any,
    config: AppConfig,
    recover_session: Callable[[Any, AppConfig], bool] | None,
) -> bool:
    """Attempt email-2FA recovery once when enabled; return whether it succeeded.

    ``COUPANG_AUTO_EMAIL_2FA_ENABLED``가 꺼져 있으면 아무 것도 하지 않고 ``False``.
    복구 함수는 주입 가능(테스트용)하며, 기본값은 이메일 2FA 복구 구현이다. 복구 중
    발생한 운영자 조치 필요 오류는 호출부가 기존 로그인 필요 오류로 중단하도록 그대로
    삼킨다(여기서 새 예외를 만들지 않는다 — 인증번호/토큰 누출 위험을 줄인다).
    """

    if not config.coupang_auto_email_2fa_enabled:
        return False

    recover = recover_session or _default_recover_coupang_session
    try:
        succeeded = bool(recover(page, config))
    except Exception as exc:
        # 복구 실패(Gmail 미도착, 입력칸 못 찾음 등)는 자동 복구 불가로 본다. 상위에서
        # 기존 BrowserActionRequiredError를 다시 던져 탭을 중지한다. 다만 과거에는 이
        # 실패를 흔적 없이 삼켜, run_errors.log에 "로그인 만료"만 남고 *왜* 2FA 복구가
        # 안 됐는지(예: 이 탭의 토큰이 보는 inbox로 인증메일이 오지 않음)를 알 수 없었다.
        # 그래서 어떤 토큰/검색어로 폴링하다 무슨 사유로 실패했는지 한 줄로 남긴다.
        # Coupang2faError/Imap2faError 메시지는 코드·앱 비밀번호 값을 담지 않게 설계돼 있다.
        _log_recovery_failure(config, exc)
        return False
    if not succeeded:
        # 예외 없이 False면 자동복구 범위 밖 화면이었다는 뜻이다(CAPTCHA, 아이디/비번
        # 제출이 안 먹어 로그인 화면에 머묾, 이메일 인증 화면이 아님 등). 예전에는 이
        # 경우 run_errors.log에 "로그인 만료"만 남아 *왜* 복구가 안 됐는지 알 수 없었다.
        _log_recovery_failure(
            config,
            RuntimeError("자동복구 불가 화면(로그인 제출 실패·CAPTCHA·비대상 화면 등)"),
        )
    return succeeded


_EMAIL_PROVIDER_BY_DOMAIN = {
    "naver.com": "naver",
    "mail.naver.com": "naver",
    "gmail.com": "gmail",
    "googlemail.com": "gmail",
}


def _email_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().casefold() if "@" in (address or "") else ""


def _mask_email_address(address: str) -> str:
    # 전체 이메일 주소는 기록하지 않는다. 로컬파트는 첫 글자만 남기고 가린다(도메인은 단서로 유지).
    address = str(address or "")
    if "@" not in address:
        return "?"
    local, _, domain = address.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def _log_recovery_failure(config: AppConfig, exc: Exception) -> None:
    """쿠팡 이메일 2FA 자동복구 실패를 ``<log_dir>/run_errors.log``에 한 줄 남긴다.

    어느 메일 계정으로 폴링하다 실패했는지가 핵심 단서다. 공급자(naver/gmail)와 마스킹된
    인증 이메일 주소(로컬파트 첫 글자 + 도메인)만 적고, 앱 비밀번호·인증번호·전체 이메일
    주소는 절대 적지 않는다. 로깅 자체가 실패해도 복구 흐름을 막지 않도록 모든 예외를 삼킨다.
    """

    try:
        log_dir = getattr(config, "log_dir", None) or Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        address = str(getattr(config, "verification_email_address", "") or "")
        provider = _EMAIL_PROVIDER_BY_DOMAIN.get(_email_domain(address), "?")
        masked = _mask_email_address(address)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"[{ts}] 쿠팡 이메일 2FA 자동복구 실패 "
            f"(provider={provider}, email={masked}): "
            f"{type(exc).__name__}: {exc}\n"
            "----------------------------------------\n"
        )
        with (log_dir / "run_errors.log").open("a", encoding="utf-8") as file:
            file.write(line)
    except Exception:
        # 로깅 실패는 복구 결과에 영향 주지 않는다.
        pass


def _default_recover_coupang_session(page: Any, config: AppConfig) -> bool:
    from rider_crawl.auth.coupang_email_2fa import recover_coupang_session_with_email_2fa

    return recover_coupang_session_with_email_2fa(page, config)


def _reload_target_page(
    page: Any,
    config: AppConfig,
    *,
    target_url: str,
    load_timeout_errors: tuple[type[BaseException], ...],
) -> None:
    """Re-open the target URL after a successful 2FA recovery, then settle.

    인증 성공 뒤 화면이 인증 화면에 머물러 있을 수 있으므로 대상 URL을 다시 연다.
    ``goto``를 지원하지 않는 page(테스트 fake 등)는 ``reload``로 떨어진다.
    """

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=config.page_timeout_seconds)
    except load_timeout_errors:
        pass
    except Exception:
        try:
            page.reload()
        except Exception:
            pass
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except load_timeout_errors:
        pass


def _select_page_by_url(pages: Iterable[Any], target_url: str) -> Any | None:
    # 같은 프로필에 같은 경로 탭이 여러 개 열려 있으면 오래된 탭을 읽을 수 있으므로,
    # 배민과 동일하게 exact match(쿼리 포함)를 우선하고 중복이면 거부한다. exact가
    # 없을 때만 host/path 매칭으로 떨어지되, 이 또한 한 개일 때만 선택한다.
    pages_list = list(pages)
    exact_matches = [page for page in pages_list if _url_matches_exact(str(page.url), target_url)]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None
    path_matches = [page for page in pages_list if _url_matches(str(page.url), target_url)]
    return path_matches[0] if len(path_matches) == 1 else None


def _raise_coupang_page_action_required(pages: list[Any], target_url: str) -> None:
    login_page = _login_required_page(pages)
    if login_page is not None:
        raise BrowserActionRequiredError(_coupang_login_required_message(target_url))

    exact_matches = [page for page in pages if _url_matches_exact(str(page.url), target_url)]
    path_matches = [page for page in pages if _url_matches(str(page.url), target_url)]
    if len(exact_matches) > 1 or (not exact_matches and len(path_matches) > 1):
        raise BrowserActionRequiredError(
            "Chrome CDP에서 쿠팡이츠 대상 탭이 여러 개 열려 있습니다.\n"
            "중복된 rider-performance 또는 peak-dashboard 탭을 하나만 남긴 뒤 다시 실행하세요."
        )

    raise BrowserActionRequiredError(
        "열려 있는 Chrome 탭에서 쿠팡이츠 대상 페이지를 찾지 못했습니다.\n"
        f"{target_url} 페이지를 로그인된 상태로 열어두세요."
    )


def _login_required_page(pages: Iterable[Any]) -> Any | None:
    for page in pages:
        if _page_looks_like_coupang_login_required(page):
            return page
    return None


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
    # scheme까지 비교한다. 비교하지 않으면 https://... 대상에 http://... 탭이 매칭돼
    # 잘못된(혹은 다운그레이드된) 탭을 읽을 수 있다(배민과 동일 정책).
    return (
        page.scheme == target.scheme
        and page.netloc == target.netloc
        and _normalize_path(page.path) == _normalize_path(target.path)
    )


def _normalize_path(path: str) -> str:
    return path.rstrip("/") or "/"


def _query_items(query: str) -> list[tuple[str, str]]:
    return sorted(parse_qsl(query, keep_blank_values=True))


def _wait_for_target_page_ready(
    page: Any,
    config: AppConfig,
    *,
    target_url: str,
    timeout_errors: tuple[type[BaseException], ...],
) -> None:
    # 대상 페이지는 어떤 config 필드에 들어 있는지가 아니라 URL 경로로 판별한다. 쿠팡
    # 탭이 peak-dashboard를 주 URL(``coupang_eats_url``)로 쓰게 되면서, config 필드 비교는
    # 주/보조 URL이 같은 값일 때 잘못된 분기를 탈 수 있다. 경로로 보면 어느 필드에 있든
    # 항상 알맞은 준비 텍스트를 기다린다.
    path = _normalize_path(urlsplit(target_url).path).casefold()
    if path == "/page/peak-dashboard":
        label = "쿠팡이츠 피크 대시보드"
        required_text = "피크타임별 현황"
    elif path == "/page/rider-performance":
        label = "쿠팡이츠 실적 페이지"
        required_text = "라이더 현황"
    else:
        return

    try:
        page.get_by_text(required_text).wait_for(timeout=config.page_timeout_seconds)
    except timeout_errors as exc:
        if _page_looks_like_coupang_login_required(page):
            raise BrowserActionRequiredError(_coupang_login_required_message(target_url)) from exc
        seconds = max(1, config.page_timeout_seconds // 1000)
        raise RuntimeError(
            f"{label}가 {seconds}초 안에 준비되지 않았습니다. "
            "Chrome에서 쿠팡이츠 로그인과 화면 로딩을 확인하세요."
        ) from exc


def _page_looks_like_coupang_login_required(page: Any) -> bool:
    url = str(getattr(page, "url", "")).casefold()
    if _url_looks_like_coupang_login_required(url):
        return True

    try:
        html = str(page.content())
    except Exception:
        return False
    return _html_looks_like_coupang_login_required(html)


def _url_looks_like_coupang_login_required(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if host == "xauth.coupang.com" and "/auth/realms/eats-partner" in path:
        return True
    return host == "partner.coupangeats.com" and any(token in path for token in ("login", "signin", "sign-in", "auth"))


def _html_looks_like_coupang_login_required(html: str) -> bool:
    text = re.sub(r"\s+", " ", html or "").casefold()
    strong_text_signals = ("세션이 만료", "다시 로그인", "로그인이 필요", "sign in to eats-partner")
    if any(signal in text for signal in strong_text_signals):
        return True

    has_vendor_identity = "vendor portal" in text or "vendor-portal" in text
    has_xauth_form = "login-actions/authenticate" in text and "realms/eats-partner" in text
    has_login_fields = "username" in text and "password" in text
    has_visible_login_controls = "아이디 입력" in text and "비밀번호 입력" in text and "로그인" in text
    return has_vendor_identity and ((has_xauth_form and has_login_fields) or has_visible_login_controls)


def _coupang_login_required_message(target_url: str) -> str:
    return (
        "쿠팡이츠 로그인이 만료되었거나 로그인 화면으로 이동했습니다.\n"
        "Chrome에서 쿠팡이츠에 다시 로그인한 뒤 peak-dashboard 페이지를 "
        "로그인된 상태로 열어두세요.\n"
        f"대상 URL: {target_url}"
    )
