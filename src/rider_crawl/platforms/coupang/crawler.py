from __future__ import annotations

import hashlib
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from rider_crawl.browser_launcher import BrowserActionRequiredError, CdpUnavailableError, ensure_local_cdp_address
from rider_crawl.config import DEFAULT_COUPANG_RIDER_PERFORMANCE_URL, AppConfig
from rider_crawl.lock import RunLock
from rider_crawl.models import CurrentScreenSnapshot, PerformanceSnapshot

from .parser import MissingPerformanceDataError, parse_current_screen_html, parse_peak_dashboard_html


class CoupangCenterValidationError(RuntimeError):
    """Raised when a Coupang page cannot be proven to match the expected center."""


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
    fetch_current_screen_html: Callable[[AppConfig], str] | None = None,
    fetch_peak_dashboard_html: Callable[[AppConfig], str] | None = None,
) -> PerformanceSnapshot:
    current_screen = None
    if fetch_current_screen_html is not None:
        current_screen_html = fetch_current_screen_html(config)
        current_screen = parse_current_screen_html(current_screen_html)
        _validate_coupang_center(config, current_screen)
    elif fetch_peak_dashboard_html is None:
        # rider-performance는 수행중 인원을 채우는 보조 페이지다. 실패해도 peak-dashboard
        # 수집은 계속하고, stale 화면이면 같은 세션의 임시 탭에서 한 번 더 읽는다.
        # 보조 페이지의 '탭 부재/로그인 만료'(BrowserActionRequiredError)·'파싱 실패'
        # (MissingPerformanceDataError)·'준비 지연/CDP 불가'(RuntimeError)는 best-effort로
        # 흡수해 '수행중인원'만 생략한다 — 보조 timeout 하나로 권위 페이지(peak-dashboard)
        # 수집까지 막지 않기 위해서다. 다만 '센터 불일치'(CoupangCenterValidationError)는
        # 다른 계정 데이터를 정상처럼 흘려보내면 안 되므로 흡수하지 않고 그대로 올린다.
        try:
            rider_url = _rider_performance_url(config)
            current_screen_html = fetch_page_html(config, target_url=rider_url)
            try:
                current_screen = parse_current_screen_html(current_screen_html)
            except MissingPerformanceDataError:
                current_screen_html = fetch_page_html(config, target_url=rider_url, force_new_tab=True)
                current_screen = parse_current_screen_html(current_screen_html)
            _validate_coupang_center(config, current_screen)
        except CoupangCenterValidationError:
            raise
        except (BrowserActionRequiredError, MissingPerformanceDataError, RuntimeError):
            current_screen = None

    # peak-dashboard 는 권위 페이지다. 세션이 만료됐는데 화면이 로그인으로 분류되지 않아
    # ready 검사는 통과하고 파싱만 실패하는 창(수집데이터누락 고착의 실제 원인)을 같은 턴에
    # 닫는다: 자동 email 2FA 가 켜져 있으면 page 가 살아 있는 동안 peak 를 파싱해 보고,
    # MissingPerformanceDataError 면 fetch 내부에서 로그인 게이트로 1회 자동복구 후 재시도한다.
    # 복구 불가/재시도도 실패면 fetch 가 BrowserActionRequiredError 로 올려 워커가 AUTH_REQUIRED
    # 로 표면화한다(다음 tick 의 AUTH_COUPANG_2FA 폴백을 깨운다). 꺼져 있으면 None → 기존 동작.
    # post_load_validate 는 자동 2FA 가 켜졌을 때만 넘긴다. None 이면 키워드를 아예 빼서 기존
    # 호출 시그니처를 보존한다(monkeypatch 한 fetch 가 새 키워드를 몰라도 무회귀).
    if fetch_peak_dashboard_html:
        peak_dashboard_html = fetch_peak_dashboard_html(config)
    elif config.coupang_auto_email_2fa_enabled:
        peak_dashboard_html = fetch_page_html(
            config,
            target_url=_peak_dashboard_url(config),
            post_load_validate=parse_peak_dashboard_html,
        )
    else:
        peak_dashboard_html = fetch_page_html(
            config, target_url=_peak_dashboard_url(config)
        )
    # 피크 대시보드 헤딩에 기대 센터가 노출되면 그것으로 다른 계정/오래된 탭을 막는다.
    # 권위 페이지라 fail-closed로 둔다: 헤딩에서 센터를 확인하지 못하면(미노출 포함)
    # 검증을 건너뛰지 않고 CoupangCenterValidationError를 던져 잘못된 계정 전송을 막는다.
    # (기대 센터명이 비어 있을 때만 검증을 건너뛴다.)
    _validate_coupang_center_in_peak_html(config, peak_dashboard_html)
    return PerformanceSnapshot(
        current_screen=current_screen,
        peak_dashboard=parse_peak_dashboard_html(peak_dashboard_html),
    )


def _rider_performance_url(config: AppConfig) -> str:
    primary = config.coupang_eats_url.strip()
    if _path_is(primary, "/page/rider-performance"):
        return primary
    if _path_is(primary, "/page/peak-dashboard"):
        return _replace_url_path(primary, "/page/rider-performance")
    return DEFAULT_COUPANG_RIDER_PERFORMANCE_URL


def _peak_dashboard_url(config: AppConfig) -> str:
    if config.peak_dashboard_url.strip():
        return config.peak_dashboard_url.strip()
    primary = config.coupang_eats_url.strip()
    if _path_is(primary, "/page/peak-dashboard"):
        return primary
    if _path_is(primary, "/page/rider-performance"):
        return _replace_url_path(primary, "/page/peak-dashboard")
    return primary


def _path_is(url: str, path: str) -> bool:
    return _normalize_path(urlsplit(url).path).casefold() == path.casefold()


def _replace_url_path(url: str, path: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


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
        raise CoupangCenterValidationError(
            "쿠팡 센터 검증 실패: 화면에서 센터명을 확인하지 못했습니다.\n"
            f"설정 센터명: {config.baemin_center_name.strip()}"
        )

    if actual not in expected_aliases:
        raise CoupangCenterValidationError(
            "쿠팡 센터 검증 실패: 설정한 센터와 화면에서 확인된 센터가 다릅니다.\n"
            f"설정 센터명: {config.baemin_center_name.strip()}\n"
            f"화면 센터명: {actual_raw}"
        )


def _validate_coupang_center_in_peak_html(config: AppConfig, peak_html: str) -> None:
    """Reject a peak-dashboard HTML that does not belong to the expected center.

    피크 페이지의 **실제 선택 센터(상단 제목/헤딩)**만 추출해 alias와 exact 비교한다. 페이지
    전체 텍스트에 "포함"되는지만 보면, 실제 선택 센터가 다른데도 드롭다운/목록/이전
    값 같은 부수 텍스트(예: ``<option>강남센터</option>``)에 기대 센터명이 있으면
    잘못 통과한다. 그래서 상단 제목 영역(``dashboard-page-title-content``)을 우선 보고,
    없으면 기존 헤딩(``h1``~``h3``)을 본다. 제목/헤딩에 ``센터명 시프트(시간)…`` 형태로
    시프트가 붙어 있으면 앞쪽 센터명만 떼어내 비교한다.
    피크 페이지는 권위 페이지라 fail-closed로 둔다: 제목/헤딩에서 센터를 확인하지 못하면
    (미노출 포함) CoupangCenterValidationError를 던진다.
    기대 센터가 비어 있으면 검증을 건너뛴다(기존 동작 유지).
    """

    expected_aliases = _coupang_center_aliases(config.baemin_center_name)
    if not expected_aliases:
        return

    peak_centers = _coupang_peak_centers(peak_html)
    if not peak_centers:
        raise CoupangCenterValidationError(
            "쿠팡 센터 검증 실패: 피크 대시보드 화면에서 센터명을 확인하지 못했습니다.\n"
            f"설정 센터명: {config.baemin_center_name.strip()}"
        )

    if not (peak_centers & expected_aliases):
        raise CoupangCenterValidationError(
            "쿠팡 센터 검증 실패: 설정한 센터가 피크 대시보드 화면 센터명과 일치하지 않습니다. "
            "다른 계정이거나 오래된 피크 탭이 열려 있을 수 있습니다.\n"
            f"설정 센터명: {config.baemin_center_name.strip()}\n"
            f"화면 센터명: {', '.join(sorted(_coupang_peak_center_raw(peak_html))) or '(확인 불가)'}"
        )


def _coupang_peak_centers(peak_html: str) -> set[str]:
    return {_normalize_coupang_center(name) for name in _coupang_peak_center_raw(peak_html)}


def _coupang_peak_center_raw(peak_html: str) -> set[str]:
    title_centers = _coupang_peak_title_center_raw(peak_html)
    if title_centers:
        return title_centers
    return _coupang_peak_heading_center_raw(peak_html)


def _coupang_peak_title_center_raw(peak_html: str) -> set[str]:
    parser = _CoupangPeakTitleParser()
    parser.feed(peak_html)
    centers: set[str] = set()
    for title in parser.titles:
        center = _coupang_center_from_heading(title)
        if center:
            centers.add(center)
    return centers


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
    r"|오전\s*논피크|오후\s*논피크|심야\s*논피크|밤\s*논피크|논피크|피크)"
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
    # 알려진 시프트 패턴이 안 맞아도, ``(HH:MM~HH:MM)`` 시간 범위가 붙어 있으면 그건
    # 시프트 라벨이다. 쿠팡이 새 시프트명("밤논피크"→"심야논피크"처럼 표기를 또 바꾸거나
    # 신규 시프트를 추가)을 쓰면 allowlist가 못 따라가 정상 화면을 '센터 불일치'로
    # 오발(오발송 위험으로 표시)했다. 시간 범위를 앵커로 앞쪽 시프트 토큰만 떼어내
    # allowlist 없이도 센터명을 복원한다. 시프트 라벨은 "…피크/…논피크/아침"으로 끝나고
    # 센터명은 그렇게 끝나지 않으므로, 이 절단이 다른 센터를 잘못 통과시키지 않는다
    # (센터명 자체는 그대로 남아 이후 exact 비교가 막는다).
    time_range = re.search(r"\(\d{2}:\d{2}~\d{2}:\d{2}\)", text)
    if time_range:
        before = text[: time_range.start()].rstrip()
        center = re.sub(r"(?:\s+\S*(?:피크|아침))+$", "", before).strip()
        return center or text
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


class _CoupangPeakTitleParser(HTMLParser):
    """Collect text from the peak dashboard's selected-center title area."""

    _TITLE_CLASS = "dashboard-page-title-content"

    def __init__(self) -> None:
        super().__init__()
        self.titles: list[str] = []
        self._depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._depth > 0:
            self._depth += 1
            return
        if _attrs_include_class(attrs, self._TITLE_CLASS):
            self._depth = 1
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._depth <= 0:
            return
        self._depth -= 1
        if self._depth == 0:
            text = _normalize_visible_text(" ".join(self._buffer))
            if text:
                self.titles.append(text)
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._depth > 0:
            self._buffer.append(data)


def _attrs_include_class(attrs: list[tuple[str, str | None]], class_name: str) -> bool:
    for name, value in attrs:
        if name.lower() != "class" or value is None:
            continue
        if class_name in str(value).split():
            return True
    return False


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


def fetch_page_html(
    config: AppConfig,
    *,
    target_url: str | None = None,
    force_new_tab: bool = False,
    post_load_validate: Callable[[str], None] | None = None,
) -> str:
    # ``post_load_validate`` 는 page 가 살아 있는 동안 content 를 검증하는 콜백이다(권위 peak
    # 파싱). MissingPerformanceDataError 를 던지면 fetch 가 로그인 게이트로 1회 자동복구를
    # 시도한다. None 이면 기존 호출 시그니처 그대로 호출한다(검증 없음 + 하위호환 — 보조
    # 페이지·테스트가 monkeypatch 한 fetch 가 새 키워드를 몰라도 깨지지 않게).
    extra = {} if post_load_validate is None else {"post_load_validate": post_load_validate}
    if config.browser_mode == "cdp":
        if target_url is None:
            return fetch_page_html_via_cdp(config, force_new_tab=force_new_tab, **extra)
        return fetch_page_html_via_cdp(
            config, target_url=target_url, force_new_tab=force_new_tab, **extra
        )
    if config.browser_mode == "persistent":
        if target_url is None:
            return fetch_page_html_via_persistent_context(config, **extra)
        return fetch_page_html_via_persistent_context(
            config, target_url=target_url, **extra
        )
    raise ValueError("브라우저 연결 방식은 cdp 또는 persistent 중 하나여야 합니다")


def fetch_page_html_via_cdp(
    config: AppConfig,
    *,
    target_url: str | None = None,
    force_new_tab: bool = False,
    post_load_validate: Callable[[str], None] | None = None,
) -> str:
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
            force_new_tab=force_new_tab,
            post_load_validate=post_load_validate,
        )


def fetch_page_html_via_persistent_context(
    config: AppConfig,
    *,
    target_url: str | None = None,
    post_load_validate: Callable[[str], None] | None = None,
) -> str:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    config.browser_user_data_dir.mkdir(parents=True, exist_ok=True)

    # CDP 경로와 동일: 이 fetch 안에서 자동복구는 최대 1회(중복 OTP 방지). ponytail: 단순 불리언.
    recovery_attempted = False

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
                recovery_attempted = True
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
            return _content_with_post_load_recovery(
                page,
                config,
                target_url=resolved_target,
                load_timeout_errors=(PlaywrightTimeoutError,),
                recover_session=None,
                post_load_validate=post_load_validate,
                recovery_attempted=recovery_attempted,
            )
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
    force_new_tab: bool = False,
    post_load_validate: Callable[[str], None] | None = None,
) -> str:
    target_url = target_url or config.coupang_eats_url
    # 이 fetch 호출 안에서 자동복구는 최대 1회만 한다(로그인-만료 readiness 경로와 신규
    # missing-data 검증 경로가 둘 다 fire 해 중복 OTP 를 요청하는 것을 막는다). 메일박스
    # RunLock 은 프로세스 간 직렬화, 이 불리언은 호출 내 중복 제거다(서로 다른 층).
    # ponytail: 단순 불리언 — 상태 객체 불필요.
    recovery_attempted = False
    pages = _browser_pages(browser)
    if force_new_tab:
        opened = _open_target_in_new_tab(
            browser,
            pages,
            config,
            target_url=target_url,
            load_timeout_errors=load_timeout_errors,
            allow_existing=True,
        )
        if opened is not None:
            return opened
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
            opened = _open_target_in_new_tab(
                browser,
                pages,
                config,
                target_url=target_url,
                load_timeout_errors=load_timeout_errors,
            )
            if opened is not None:
                return opened
            _log_page_selection_failure(browser, pages, config, target_url=target_url)
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
        recovery_attempted = True
        _reload_target_page(page, config, target_url=target_url, load_timeout_errors=load_timeout_errors)
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
    if _select_coupang_center(page, config, timeout_errors=load_timeout_errors):
        # 탭을 눌러 다른 센터로 전환했으면, 새 센터 화면이 준비될 때까지 한 번 더 기다린다.
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
    return _content_with_post_load_recovery(
        page,
        config,
        target_url=target_url,
        load_timeout_errors=load_timeout_errors,
        recover_session=recover_session,
        post_load_validate=post_load_validate,
        recovery_attempted=recovery_attempted,
    )


def _content_with_post_load_recovery(
    page: Any,
    config: AppConfig,
    *,
    target_url: str,
    load_timeout_errors: tuple[type[BaseException], ...],
    recover_session: Callable[[Any, AppConfig], bool] | None,
    post_load_validate: Callable[[str], None] | None,
    recovery_attempted: bool,
) -> str:
    """page content 를 반환하되, ``post_load_validate`` 가 데이터 누락을 알리고 화면이 로그인
    만료로 보이면 같은 턴에서 1회 자동복구 후 재시도한다.

    세션이 만료됐는데도 화면이 로그인으로 분류되지 않아 readiness 는 통과하고 파싱만 실패하는
    경우(수집데이터누락 고착)를 닫는다. **로그인 게이트가 필수**다: 진짜로 인증됐지만 데이터가
    빈(영업외/물량 0) 화면에서 OTP 를 낭비하지 않도록, ``_page_looks_like_coupang_login_required``
    가 참일 때만 복구한다. 복구 불가/재시도도 빈값이면, 화면이 로그인으로 보이는 한
    ``BrowserActionRequiredError`` 로 올려 워커가 AUTH_REQUIRED 로 표면화하게 한다(missing-data
    로 끝나 계정이 ACTIVE 로 굳고 다음 tick 의 인증 복구가 안 깨우던 데드락 방지). 로그인으로
    보이지 않으면 원래 ``MissingPerformanceDataError`` 를 그대로 전파한다(비인증 케이스 무변화).
    """

    content = page.content()
    if _html_looks_like_chrome_error_page(content):
        _recover_chrome_error_page_once(
            page,
            config,
            target_url=target_url,
            load_timeout_errors=load_timeout_errors,
        )
        _wait_for_target_page_ready(
            page,
            config,
            target_url=target_url,
            timeout_errors=load_timeout_errors,
        )
        if _select_coupang_center(page, config, timeout_errors=load_timeout_errors):
            _wait_for_target_page_ready(
                page,
                config,
                target_url=target_url,
                timeout_errors=load_timeout_errors,
            )
        content = page.content()
    if post_load_validate is None:
        return content
    try:
        post_load_validate(content)
        return content
    except MissingPerformanceDataError as exc:
        looks_login = _page_looks_like_coupang_login_required(page)
        if not (
            not recovery_attempted
            and looks_login
            and _try_recover_coupang_session(page, config, recover_session)
        ):
            if looks_login:
                raise BrowserActionRequiredError(
                    _coupang_login_required_message(target_url)
                ) from exc
            raise

    _reload_target_page(page, config, target_url=target_url, load_timeout_errors=load_timeout_errors)
    _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
    if _select_coupang_center(page, config, timeout_errors=load_timeout_errors):
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
    content = page.content()
    try:
        post_load_validate(content)
    except MissingPerformanceDataError as exc:
        # 복구를 해봤는데도 여전히 데이터가 없다. 화면이 로그인으로 보이면 AUTH_REQUIRED 로
        # 올리고(다음 tick 인증 복구 폴백), 아니면 원래 누락 오류를 전파한다.
        if _page_looks_like_coupang_login_required(page):
            raise BrowserActionRequiredError(
                _coupang_login_required_message(target_url)
            ) from exc
        raise
    return content


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


def _open_target_in_new_tab(
    browser: Any,
    pages: list[Any],
    config: AppConfig,
    *,
    target_url: str,
    load_timeout_errors: tuple[type[BaseException], ...],
    allow_existing: bool = False,
) -> str | None:
    """Open a temporary tab in the logged-in Coupang context and read target content."""

    if not (_path_is(target_url, "/page/rider-performance") or _path_is(target_url, "/page/peak-dashboard")):
        return None
    if _login_required_page(pages) is not None:
        return None
    if not allow_existing and any(_url_matches(str(page.url), target_url) for page in pages):
        return None

    context = _coupang_logged_in_context(browser)
    if context is None:
        return None

    page = context.new_page()
    try:
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=config.page_timeout_seconds)
        except load_timeout_errors:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except load_timeout_errors:
            pass
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
        if _select_coupang_center(page, config, timeout_errors=load_timeout_errors):
            _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
        return page.content()
    finally:
        try:
            page.close()
        except Exception:
            pass


def _coupang_logged_in_context(browser: Any) -> Any | None:
    for context in getattr(browser, "contexts", []) or []:
        for page in getattr(context, "pages", []) or []:
            host = (urlsplit(str(getattr(page, "url", ""))).hostname or "").casefold()
            if host == "partner.coupangeats.com":
                return context
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
        lock_id = _mailbox_lock_id(config)
        if lock_id:
            with RunLock(
                _mailbox_lock_path(config),
                stale_timeout_seconds=config.run_lock_timeout_seconds,
            ):
                succeeded = bool(recover(page, config))
        else:
            succeeded = bool(recover(page, config))
    except Exception as exc:
        # 복구 실패는 자동 복구 불가로 본다. 단, 왜 실패했는지 안전한 범위에서 로그에 남긴다.
        _log_recovery_failure(config, exc)
        return False
    if not succeeded:
        exc = RuntimeError("자동복구 불가 화면(로그인 제출 실패 또는 비대상 화면)")
        exc.recovery_step = "page_selection"
        exc.recovery_reason = "non_target_or_submit_failed"
        exc.recovery_diagnostics = _recovery_page_diagnostics(page)
        _log_recovery_failure(config, exc)
    return succeeded


def _mailbox_lock_id(config: AppConfig) -> str:
    value = (
        getattr(config, "verification_email_mailbox_lock_id", "")
        or config.verification_email_address
    )
    value = str(value or "").strip()
    return value.casefold() if "@" in value else value


def _mailbox_lock_path(config: AppConfig) -> Path:
    lock_id = _mailbox_lock_id(config)
    handle = hashlib.sha256(lock_id.encode("utf-8")).hexdigest()[:16]
    return config.runtime_dir / "state" / "mailbox_locks" / f"mailbox.{handle}.lock"


_EMAIL_PROVIDER_BY_DOMAIN = {
    "naver.com": "naver",
    "mail.naver.com": "naver",
    "gmail.com": "gmail",
    "googlemail.com": "gmail",
}
_SAFE_RECOVERY_STEPS = frozenset(
    {
        "primary_login",
        "select_email_auth",
        "click_send_code",
        "fetch_otp",
        "fill_code",
        "submit",
        "reopen_target",
        "page_selection",
    }
)
_SAFE_RECOVERY_REASONS = frozenset(
    {
        "otp_not_found",
        "non_target_or_submit_failed",
        "verification_mail_delayed",
        "repeated_recovery_failure",
        "browser_unavailable",
        "captcha_or_abnormal_login",
        "email_auth_required",
        "mail_app_password_invalid",
        "imap_access_disabled",
        "unsupported_email_domain",
        "mailbox_auth_blocked",
        "mailbox_login_failed",
    }
)
_SAFE_DIAGNOSTIC_KEYS = (
    "code_found",
    "msgs_found",
    "latest_code_age_s",
    "within_poll_window",
    "email_2fa_poll_seconds",
    "email_2fa_poll_interval_seconds",
    "page_host",
    "page_path",
    "login_page",
)


def _email_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().casefold() if "@" in (address or "") else ""


def _mask_email_address(address: str) -> str:
    address = str(address or "")
    if "@" not in address:
        return "?"
    local, _, domain = address.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def _log_recovery_failure(config: AppConfig, exc: Exception) -> None:
    """Write a safe one-line email 2FA recovery failure diagnostic."""

    try:
        log_dir = getattr(config, "log_dir", None) or Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        address = str(getattr(config, "verification_email_address", "") or "")
        provider = _EMAIL_PROVIDER_BY_DOMAIN.get(_email_domain(address), "unknown")
        masked = _mask_email_address(address)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        details = _safe_recovery_failure_details(exc)
        detail_text = _format_recovery_details(details)
        line = (
            f"[{ts}] 쿠팡 이메일 2FA 자동복구 실패 "
            f"(provider={provider}, email={masked}{detail_text}): "
            "인증 이메일 설정 또는 메일 수신 상태 확인 필요\n"
            "----------------------------------------\n"
        )
        with (log_dir / "run_errors.log").open("a", encoding="utf-8") as file:
            file.write(line)
    except Exception:
        pass


def _safe_recovery_failure_details(exc: Exception) -> dict[str, object]:
    details: dict[str, object] = {"exception_class": _safe_exception_class(exc)}
    step = str(getattr(exc, "recovery_step", "") or "").strip()
    if step in _SAFE_RECOVERY_STEPS:
        details["step"] = step
    reason = str(getattr(exc, "recovery_reason", "") or "").strip()
    if reason in _SAFE_RECOVERY_REASONS:
        details["reason"] = reason
    details.update(_safe_recovery_diagnostics(getattr(exc, "recovery_diagnostics", None)))
    return details


def _safe_exception_class(exc: Exception) -> str:
    name = type(exc).__name__
    return name if name.isidentifier() and len(name) <= 80 else "Exception"


def _safe_recovery_diagnostics(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, object] = {}
    for key in _SAFE_DIAGNOSTIC_KEYS:
        if key not in value:
            continue
        item = value.get(key)
        if key in {"code_found", "within_poll_window", "login_page"}:
            if isinstance(item, bool):
                safe[key] = item
        elif key in {
            "msgs_found",
            "latest_code_age_s",
            "email_2fa_poll_seconds",
            "email_2fa_poll_interval_seconds",
        }:
            if item is None and key == "latest_code_age_s":
                safe[key] = None
            elif isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                safe[key] = item
        elif key == "page_host" and isinstance(item, str):
            host = (urlsplit(item).hostname or item).strip().split("/", 1)[0]
            if host and len(host) <= 120:
                safe[key] = host
        elif key == "page_path" and isinstance(item, str):
            path = urlsplit(item).path or item.split("?", 1)[0]
            path = path.strip()
            if path and len(path) <= 200:
                safe[key] = path
    return safe


def _format_recovery_details(details: dict[str, object]) -> str:
    keys = ("exception_class", "step", "reason", *_SAFE_DIAGNOSTIC_KEYS)
    parts = [f"{key}={details[key]}" for key in keys if key in details]
    return ", " + ", ".join(parts) if parts else ""


def _recovery_page_diagnostics(page: Any) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    parsed = urlsplit(str(getattr(page, "url", "") or ""))
    if parsed.hostname:
        diagnostics["page_host"] = parsed.hostname
    if parsed.path:
        diagnostics["page_path"] = parsed.path
    diagnostics["login_page"] = _page_looks_like_coupang_login_required(page)
    return diagnostics


def _url_host_path(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    host = parsed.hostname or ""
    path = _normalize_path(parsed.path) if parsed.path else ""
    return f"{host}{path}" if (host or path) else (str(url or "") or "?")


def _log_page_selection_failure(
    browser: Any, pages: Iterable[Any], config: AppConfig, *, target_url: str
) -> None:
    """Log target page lookup diagnostics without URL query strings."""

    try:
        pages_list = list(pages)
        open_paths = [_url_host_path(str(getattr(page, "url", ""))) for page in pages_list]
        exact = sum(1 for page in pages_list if _url_matches_exact(str(getattr(page, "url", "")), target_url))
        path = sum(1 for page in pages_list if _url_matches(str(getattr(page, "url", "")), target_url))
        login_page = _login_required_page(pages_list) is not None
        logged_in_context = _coupang_logged_in_context(browser) is not None
        log_dir = getattr(config, "log_dir", None) or Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"[{ts}] 쿠팡 대상 탭 탐색 실패 "
            f"(cdp={getattr(config, 'cdp_url', '?')}, target={_url_host_path(target_url)}): "
            f"open_tabs={open_paths}, exact_match={exact}, path_match={path}, "
            f"login_page={login_page}, logged_in_context={logged_in_context}\n"
            "----------------------------------------\n"
        )
        with (log_dir / "run_errors.log").open("a", encoding="utf-8") as file:
            file.write(line)
    except Exception:
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

    _recover_chrome_error_page_once(
        page,
        config,
        target_url=target_url,
        load_timeout_errors=timeout_errors,
    )

    try:
        page.get_by_text(required_text).wait_for(timeout=config.page_timeout_seconds)
    except timeout_errors as exc:
        if _page_looks_like_coupang_login_required(page):
            raise BrowserActionRequiredError(_coupang_login_required_message(target_url)) from exc
        retried_chrome_error = False
        try:
            retried_chrome_error = _recover_chrome_error_page_once(
                page,
                config,
                target_url=target_url,
                load_timeout_errors=timeout_errors,
            )
        except RuntimeError as chrome_exc:
            raise chrome_exc from exc
        if retried_chrome_error:
            try:
                page.get_by_text(required_text).wait_for(timeout=config.page_timeout_seconds)
                return
            except timeout_errors as retry_exc:
                if _page_looks_like_coupang_login_required(page):
                    raise BrowserActionRequiredError(_coupang_login_required_message(target_url)) from retry_exc
                exc = retry_exc
        if path == "/page/peak-dashboard":
            try:
                _reload_target_page(
                    page,
                    config,
                    target_url=target_url,
                    load_timeout_errors=timeout_errors,
                )
                page.get_by_text(required_text).wait_for(timeout=config.page_timeout_seconds)
                return
            except timeout_errors as retry_exc:
                if _page_looks_like_coupang_login_required(page):
                    raise BrowserActionRequiredError(_coupang_login_required_message(target_url)) from retry_exc
                exc = retry_exc
        seconds = max(1, config.page_timeout_seconds // 1000)
        raise RuntimeError(
            f"{label}가 {seconds}초 안에 준비되지 않았습니다. "
            "Chrome에서 쿠팡이츠 로그인과 화면 로딩을 확인하세요."
        ) from exc


def _recover_chrome_error_page_once(
    page: Any,
    config: AppConfig,
    *,
    target_url: str,
    load_timeout_errors: tuple[type[BaseException], ...],
) -> bool:
    if not _page_looks_like_chrome_error_page(page):
        return False

    _reload_target_page(page, config, target_url=target_url, load_timeout_errors=load_timeout_errors)
    if _page_looks_like_chrome_error_page(page):
        raise RuntimeError(_chrome_error_page_message(page, target_url))
    return True


def _page_looks_like_chrome_error_page(page: Any) -> bool:
    url = str(getattr(page, "url", "")).casefold()
    if url.startswith("chrome-error://"):
        return True

    try:
        html = str(page.content())
    except Exception:
        return False
    return _html_looks_like_chrome_error_page(html)


def _html_looks_like_chrome_error_page(html: str) -> bool:
    text = re.sub(r"\s+", " ", html or "")
    lower = text.casefold()
    if "chrome-error://chromewebdata" in lower:
        return True
    if "이 웹페이지를 표시하는 도중 문제가 발생" in text:
        return True
    if "aw, snap" in lower and "error code" in lower:
        return True
    return "this page isn't working" in lower and "error code" in lower


def _chrome_error_page_message(page: Any, target_url: str) -> str:
    html = ""
    try:
        html = str(page.content())
    except Exception:
        pass
    code = _chrome_error_code_from_html(html)
    code_text = f" 오류코드: {code}." if code else ""
    return (
        f"Chrome 오류 페이지가 표시되어 쿠팡이츠 대상 페이지를 읽을 수 없습니다.{code_text}\n"
        "Chrome 탭 또는 프로필이 비정상 상태일 수 있습니다. 대상 탭을 닫고 다시 연 뒤 재시도하세요.\n"
        f"대상 URL: {target_url}"
    )


def _chrome_error_code_from_html(html: str) -> str:
    text = re.sub(r"\s+", " ", html or "")
    match = re.search(
        r"(?:오류\s*코드|오류코드|error\s*code)\s*[:：]?\s*([A-Za-z0-9_-]+)",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


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
    return (has_xauth_form and has_login_fields) or (has_vendor_identity and has_visible_login_controls)


def _coupang_login_required_message(target_url: str) -> str:
    return (
        "쿠팡이츠 로그인이 만료되었거나 로그인 화면으로 이동했습니다.\n"
        "Chrome에서 쿠팡이츠에 다시 로그인한 뒤 peak-dashboard 페이지를 "
        "로그인된 상태로 열어두세요.\n"
        f"대상 URL: {target_url}"
    )
