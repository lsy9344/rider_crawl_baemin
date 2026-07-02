from dataclasses import replace
from pathlib import Path

import pytest

from rider_crawl.browser_launcher import BrowserActionRequiredError
from rider_crawl.config import AppConfig
from rider_crawl.platforms.coupang import crawler
from rider_crawl.platforms.coupang.crawler import crawl_current_screen, crawl_performance_snapshot

_PEAK_DASHBOARD_HTML = """
<main>
  <p>20:38 업데이트</p>
  <p>배정 물량</p><p>103건</p>
  <p>처리 물량</p><p>67건</p>
  <p>거절률</p><p>6.5%</p>
  <section>
    <h2>피크타임별 현황</h2>
    <p>아침</p><p>100%</p><p>잔여</p><p>0</p><p>목표/완료</p><p>9/9</p>
    <p>점심 피크</p><p>100%</p><p>잔여</p><p>0</p><p>목표/완료</p><p>45/45</p>
    <p>점심 논피크</p><p>52.6%</p><p>잔여</p><p>9</p><p>목표/완료</p><p>19/10</p>
    <p>저녁 피크</p><p>43.5%</p><p>잔여</p><p>22</p><p>목표/완료</p><p>39/17</p>
    <p>저녁 논피크</p><p>7.4%</p><p>잔여</p><p>25</p><p>목표/완료</p><p>27/2</p>
  </section>
  <h2>시간대별 기록</h2>
</main>
"""


_PEAK_DASHBOARD_HTML_WITH_CENTER = _PEAK_DASHBOARD_HTML.replace(
    "<main>",
    "<main>\n  <h1>제이앤에이치플러스 의정부남부</h1>",
)

_PEAK_DASHBOARD_HTML_WITH_TITLE_CENTER = _PEAK_DASHBOARD_HTML.replace(
    "<main>",
    '<main>\n  <div class="align-center dashboard-page-title-content flex">'
    "<span>제이앤에이치플러스 의정부남부</span>"
    "<span>저녁피크(16:55~20:00)</span>"
    "<span>할당량 소진 중</span>"
    "</div>",
)

_PEAK_DASHBOARD_HTML_WITH_SECTION_HEADINGS_ONLY = _PEAK_DASHBOARD_HTML.replace(
    "<p>배정 물량</p>",
    "<h2>실시간 오늘의 실적</h2>\n  <p>배정 물량</p>",
)

# 실제 선택 센터(헤딩)는 다른데, 드롭다운/option 등 부수 텍스트에 기대 센터명이 있는
# 경우. 헤딩 exact 비교가 아니라 전체 텍스트 contains로 검증하면 잘못 통과한다.
_PEAK_DASHBOARD_HTML_OTHER_CENTER_HEADING = _PEAK_DASHBOARD_HTML.replace(
    "<main>",
    "<main>\n  <h1>서초센터</h1>\n"
    "  <select><option>강남센터</option>"
    "<option>제이앤에이치플러스 의정부남부</option></select>",
)


def test_coupang_crawl_current_screen_parses_html_from_injected_fetcher(tmp_path):
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    snapshot = crawl_current_screen(_config(tmp_path), fetch_html=lambda _config: html)

    assert snapshot.updated_at == "14:02"
    assert snapshot.completed_count == 102.4
    assert snapshot.active_riders == 7


def test_coupang_crawl_current_screen_rejects_unexpected_center(tmp_path):
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    config = _config(tmp_path, baemin_center_name="다른센터 강남")

    with pytest.raises(RuntimeError, match="쿠팡 센터 검증 실패"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_coupang_crawl_current_screen_accepts_expected_center(tmp_path):
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    snapshot = crawl_current_screen(config, fetch_html=lambda _config: html)

    assert snapshot.center_name == "제이앤에이치플러스 의정부남부"


def test_coupang_config_center_name_aliases_baemin_center_name(tmp_path):
    # Story 2.3 AC1: 쿠팡 검증이 쓰는 baemin_center_name(기대 센터/상점명)을 플랫폼 중립
    # center_name 접근자가 항상 동일 값으로 노출한다 — _validate_coupang_center 경로가
    # center_name으로도 동일하게 유지된다는 근거를 잠근다.
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    assert config.center_name == config.baemin_center_name == "제이앤에이치플러스 의정부남부"


def test_coupang_crawl_current_screen_rejects_substring_center_match(tmp_path):
    # 화면이 "제이앤에이치플러스 의정부남부"인데 설정이 그 부분 문자열이면, exact가
    # 아니므로 통과하지 않아야 한다(다른 계정/센터 전송 방지).
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    config = _config(tmp_path, baemin_center_name="의정부남부")

    with pytest.raises(RuntimeError, match="쿠팡 센터 검증 실패"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_coupang_crawl_current_screen_rejects_superstring_center_match(tmp_path):
    # 설정이 화면 센터명을 포함하는 더 긴 이름이어도 exact가 아니므로 막아야 한다.
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부2호점")

    with pytest.raises(RuntimeError, match="쿠팡 센터 검증 실패"):
        crawl_current_screen(config, fetch_html=lambda _config: html)


def test_coupang_crawl_current_screen_accepts_explicit_alias(tmp_path):
    # alias 목록(; 또는 줄바꿈 구분)에 화면 센터명이 있으면 통과한다.
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    config = _config(
        tmp_path,
        baemin_center_name="제이앤에이치 의정부; 제이앤에이치플러스 의정부남부",
    )

    snapshot = crawl_current_screen(config, fetch_html=lambda _config: html)

    assert snapshot.center_name == "제이앤에이치플러스 의정부남부"


def test_coupang_crawl_performance_snapshot_rejects_unexpected_center(tmp_path):
    # peak-dashboard는 권위 페이지이므로 화면 헤딩 센터가 기대값과 다르면 거부한다.
    config = _config(tmp_path, baemin_center_name="엉뚱한센터")

    with pytest.raises(RuntimeError, match="쿠팡 센터 검증 실패"):
        crawl_performance_snapshot(
            config,
            fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML_WITH_CENTER,
        )


def test_coupang_crawl_performance_snapshot_uses_injected_peak_without_current_screen(tmp_path):
    # peak HTML을 직접 주입하는 단위 테스트에서는 보조 rider-performance를 읽지 않는다.
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    snapshot = crawl_performance_snapshot(
        config,
        fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML_WITH_CENTER,
    )

    assert snapshot.current_screen is None
    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_fetches_current_screen_and_peak_dashboard(tmp_path, monkeypatch):
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")
    current_html = """
    <main>
      <h1>제이앤에이치플러스 의정부남부</h1>
      <h2>제이앤에이치플러스 의정부남부 밤논피크(20:00~06:00) 할당량 소진 중 라이더 현황</h2>
      <p>01:05 업데이트</p>
      <p>밤논피크 참여 가능</p><p>0 / 15 명</p>
      <p>대기</p><p>0명</p>
      <section><h3>활성 라이더</h3><p>이름 / 연락처</p><p>총 4명</p></section>
      <p>온라인 0명</p>
      <p>거절/무시: 5.8건</p><p>취소: 1건</p><p>완료: 78.8건</p>
      <p>순서 미준수: 0건</p><p>점심피크: 21.6건</p><p>저녁피크: 15.8건</p><p>논피크: 41.4건</p>
      <section><h3>비활성 라이더</h3><p>이름 / 연락처</p><p>총 0명</p></section>
    </main>
    """
    html_by_url = {
        config.coupang_eats_url: current_html,
        config.peak_dashboard_url: _PEAK_DASHBOARD_HTML_WITH_CENTER,
    }
    requested_urls: list[str | None] = []

    def fake_fetch_page_html(_config, *, target_url=None, force_new_tab=False):
        requested_urls.append(target_url)
        return html_by_url[target_url]

    monkeypatch.setattr(crawler, "fetch_page_html", fake_fetch_page_html)

    snapshot = crawl_performance_snapshot(config)

    assert requested_urls == [config.coupang_eats_url, config.peak_dashboard_url]
    assert snapshot.current_screen is not None
    assert snapshot.current_screen.active_riders == 0
    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_retries_current_screen_in_fresh_tab_when_existing_tab_is_stale(
    tmp_path, monkeypatch
):
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")
    stale_current_html = """
    <main>
      <h1>제이앤에이치플러스 의정부남부</h1>
      <p>6월 13일(토)</p>
      <h2>라이더 현황</h2>
      <p>10:51 업데이트</p>
      <p>이름 / 연락처</p><p>총 60명</p>
      <p>거절/무시</p><p>161.4건</p>
    </main>
    """
    fresh_current_html = """
    <main>
      <h1>제이앤에이치플러스 의정부남부</h1>
      <h2>제이앤에이치플러스 의정부남부 아침논피크(06:00~10:55) 할당량 소진 중 라이더 현황</h2>
      <p>10:52 업데이트</p>
      <p>아침논피크 참여 가능</p><p>18 / 45 명</p>
      <p>대기</p><p>0명</p>
      <section><h3>활성 라이더</h3><p>이름 / 연락처</p><p>총 60명</p></section>
      <p>온라인 18명</p>
      <p>거절/무시: 7건</p><p>취소: 0건</p><p>완료: 68.8건</p>
      <p>순서 미준수: 0건</p><p>점심피크: 0건</p><p>저녁피크: 0건</p><p>논피크: 68.8건</p>
    </main>
    """
    rider_url = crawler._rider_performance_url(config)
    requested: list[tuple[str | None, bool]] = []

    def fake_fetch_page_html(_config, *, target_url=None, force_new_tab=False):
        requested.append((target_url, force_new_tab))
        if target_url == rider_url and not force_new_tab:
            return stale_current_html
        if target_url == rider_url and force_new_tab:
            return fresh_current_html
        return _PEAK_DASHBOARD_HTML_WITH_CENTER

    monkeypatch.setattr(crawler, "fetch_page_html", fake_fetch_page_html)

    snapshot = crawl_performance_snapshot(config)

    assert requested == [
        (rider_url, False),
        (rider_url, True),
        (crawler._peak_dashboard_url(config), False),
    ]
    assert snapshot.current_screen is not None
    assert snapshot.current_screen.active_riders == 18


def test_coupang_crawl_performance_snapshot_skips_current_screen_when_rider_tab_missing(tmp_path, monkeypatch):
    # rider-performance 보조 조회가 실패해도 수행중 인원만 생략하고 peak-dashboard는 보낸다.
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")
    rider_performance_url = crawler._rider_performance_url(config)
    requested_urls: list[str | None] = []

    def fake_fetch_page_html(_config, *, target_url=None, force_new_tab=False):
        requested_urls.append(target_url)
        if target_url == rider_performance_url:
            raise BrowserActionRequiredError("열려 있는 Chrome 탭에서 쿠팡이츠 대상 페이지를 찾지 못했습니다.")
        return _PEAK_DASHBOARD_HTML_WITH_CENTER

    monkeypatch.setattr(crawler, "fetch_page_html", fake_fetch_page_html)

    snapshot = crawl_performance_snapshot(config)

    assert requested_urls == [rider_performance_url, crawler._peak_dashboard_url(config)]
    assert snapshot.current_screen is None
    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_peak_dashboard_requires_center_confirmation_when_expected_center_set(tmp_path):
    # 기대 센터가 있으면 피크 HTML에서 센터를 명시적으로 확인하지 못할 때 fail-closed한다.
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    with pytest.raises(RuntimeError, match="화면에서 센터명을 확인하지 못했습니다"):
        crawl_performance_snapshot(
            config,
            fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML,
        )


def test_coupang_crawl_performance_snapshot_accepts_peak_html_without_center_heading_when_no_expected_center(tmp_path):
    # 기대 센터가 비어 있는 legacy/manual 경로에서는 센터 검증을 건너뛴다.
    config = _config(tmp_path, baemin_center_name="")

    snapshot = crawl_performance_snapshot(
        config,
        fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML,
    )

    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_accepts_peak_section_headings_without_center(tmp_path):
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    with pytest.raises(RuntimeError, match="화면에서 센터명을 확인하지 못했습니다"):
        crawl_performance_snapshot(
            config,
            fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML_WITH_SECTION_HEADINGS_ONLY,
        )


def test_coupang_center_mismatch_is_not_swallowed_by_screen_detection(tmp_path, monkeypatch):
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")
    rider_url = crawler._rider_performance_url(config)
    other_center_html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8").replace(
        "제이앤에이치플러스 의정부남부",
        "서초센터",
    )

    def fake_fetch_page_html(_config, *, target_url=None, force_new_tab=False):
        if target_url == rider_url:
            return other_center_html
        return _PEAK_DASHBOARD_HTML_WITH_CENTER

    monkeypatch.setattr(crawler, "fetch_page_html", fake_fetch_page_html)

    with pytest.raises(RuntimeError, match="쿠팡 센터 검증 실패"):
        crawl_performance_snapshot(config)


def test_coupang_aux_runtime_error_does_not_block_peak_dashboard(tmp_path, monkeypatch):
    # 보조 rider-performance 페이지가 준비 지연 등 RuntimeError로 실패해도, 권위 페이지인
    # peak-dashboard 수집은 계속돼야 한다('수행중인원'만 빠진다). 센터 불일치가 아닌
    # 일반 RuntimeError는 best-effort로 흡수한다.
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")
    rider_url = crawler._rider_performance_url(config)

    def fake_fetch_page_html(_config, *, target_url=None, force_new_tab=False):
        if target_url == rider_url:
            raise RuntimeError("쿠팡이츠 실적 페이지가 60초 안에 준비되지 않았습니다.")
        return _PEAK_DASHBOARD_HTML_WITH_CENTER

    monkeypatch.setattr(crawler, "fetch_page_html", fake_fetch_page_html)

    snapshot = crawl_performance_snapshot(config)

    assert snapshot.current_screen is None
    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_rejects_peak_when_only_side_text_matches(tmp_path):
    # 실제 선택 센터 헤딩은 "서초센터"인데, 드롭다운 option에만 기대 센터명이 있는
    # 경우. 헤딩 exact 비교이므로 부수 텍스트 일치로는 통과하면 안 된다(회귀 방지).
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    with pytest.raises(RuntimeError, match="센터명과 일치하지 않습니다"):
        crawl_performance_snapshot(
            config,
            fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML_OTHER_CENTER_HEADING,
        )


def test_coupang_crawl_performance_snapshot_accepts_matching_peak_center(tmp_path):
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    snapshot = crawl_performance_snapshot(
        config,
        fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML_WITH_CENTER,
    )

    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_accepts_peak_title_center_with_shift_suffix(tmp_path):
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    snapshot = crawl_performance_snapshot(
        config,
        fetch_peak_dashboard_html=lambda _config: _PEAK_DASHBOARD_HTML_WITH_TITLE_CENTER,
    )

    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_accepts_peak_title_center_with_night_nonpeak_suffix(tmp_path):
    peak_html = _PEAK_DASHBOARD_HTML.replace(
        "<main>",
        '<main>\n  <div class="align-center dashboard-page-title-content flex">'
        "<span>제이앤에이치플러스 의정부남부 밤논피크(20:00~06:00) 할당량 소진 중</span>"
        "</div>",
    )
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    snapshot = crawl_performance_snapshot(
        config,
        fetch_peak_dashboard_html=lambda _config: peak_html,
    )

    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_accepts_peak_heading_with_shift_suffix(tmp_path):
    # 헤딩이 "센터명 시프트(시간)" 형태여도 앞쪽 센터명만 떼어 비교한다.
    peak_html = _PEAK_DASHBOARD_HTML.replace(
        "<main>",
        "<main>\n  <h1>제이앤에이치플러스 의정부남부 저녁피크(16:55~20:00)</h1>",
    )
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    snapshot = crawl_performance_snapshot(
        config,
        fetch_peak_dashboard_html=lambda _config: peak_html,
    )

    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_crawl_performance_snapshot_accepts_peak_heading_with_spaced_shift_suffix(tmp_path):
    # 실제 표기처럼 시프트명에 공백이 있어도("저녁 피크") 센터명을 잘못 자르지 않는다.
    peak_html = _PEAK_DASHBOARD_HTML.replace(
        "<main>",
        "<main>\n  <h1>제이앤에이치플러스 의정부남부 저녁 피크(16:55~20:00)</h1>",
    )
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 의정부남부")

    snapshot = crawl_performance_snapshot(
        config,
        fetch_peak_dashboard_html=lambda _config: peak_html,
    )

    assert snapshot.peak_dashboard.updated_at == "20:38"


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("제이앤에이치플러스 의정부남부", "제이앤에이치플러스 의정부남부"),
        ("제이앤에이치플러스 의정부남부 저녁피크(16:55~20:00)", "제이앤에이치플러스 의정부남부"),
        ("제이앤에이치플러스 의정부남부 저녁 피크(16:55~20:00)", "제이앤에이치플러스 의정부남부"),
        ("제이앤에이치플러스 의정부남부 저녁 피크(16:55~20:00) 할당량 소진 중", "제이앤에이치플러스 의정부남부"),
        ("제이앤에이치플러스 의정부남부 밤논피크(20:00~06:00) 할당량 소진 중", "제이앤에이치플러스 의정부남부"),
        ("에이비씨로지스 강남센터 점심 논피크(12:00~14:00)", "에이비씨로지스 강남센터"),
        ("에이비씨로지스 강남센터 오전피크(09:00~12:30)", "에이비씨로지스 강남센터"),
        # allowlist에 없는(쿠팡이 새로 추가/표기 변경한) 시프트명이어도 (HH:MM~HH:MM)
        # 시간 범위를 앵커로 시프트를 떼어내 센터명을 복원한다 — 정상 화면을 가짜
        # '센터 불일치(오발송 위험)'로 막던 회귀 방지.
        ("제이앤에이치플러스 의정부남부 새벽논피크(02:00~06:00)", "제이앤에이치플러스 의정부남부"),
        ("제이앤에이치플러스 의정부남부 새벽논피크(02:00~06:00) 할당량 소진 중", "제이앤에이치플러스 의정부남부"),
        ("제이앤에이치플러스 의정부남부 아침(06:00~09:00)", "제이앤에이치플러스 의정부남부"),
        # 센터 전환 직후(시프트 정보 로딩 전)에는 시프트가 플레이스홀더("--(--:--~--:--)")로
        # 뜬다(실측: 안양서부 3센터 전환 직후). 이때도 시간 범위 자리를 앵커로 시프트를
        # 떼어내 센터명만 남긴다 — 전환 직후 정상 화면을 '센터 불일치'로 오발하던 회귀 방지.
        ("안양서부 --(--:--~--:--) 할당량 소진 중", "안양서부"),
        ("안양서부 --(--:--~--:--)", "안양서부"),
        # 안전성: 상위문자열(2호점) 센터는 시프트만 떼고 그대로 남아 이후 exact 비교가 막는다.
        ("제이앤에이치플러스 의정부남부2호점 저녁피크(16:55~20:00)", "제이앤에이치플러스 의정부남부2호점"),
    ],
)
def test_coupang_center_from_heading_strips_shift_keeping_full_center(heading, expected):
    assert crawler._coupang_center_from_heading(heading) == expected


@pytest.mark.parametrize(
    ("label", "configured", "expected"),
    [
        # 탭 라벨(짧은 센터명)이 설정값과 정확히 같음.
        ("양주중앙", "양주중앙", True),
        # 설정값이 회사명을 포함해도(헤딩 형태) 짧은 탭 라벨과 매칭돼야 한다.
        ("양주중앙", "제이앤에이치플러스 양주중앙", True),
        # 공백/대소문자는 정규화 후 비교한다.
        ("양주 중앙", "양주중앙", True),
        # 다른 센터 탭은 매칭되면 안 된다.
        ("의정부남부", "양주중앙", False),
        ("의정부중앙", "양주중앙", False),
    ],
)
def test_coupang_center_tab_label_matches(label, configured, expected):
    aliases = crawler._coupang_center_aliases(configured)
    assert crawler._coupang_center_tab_label_matches(label, aliases) is expected


def test_select_coupang_center_clicks_matching_inactive_tab(tmp_path):
    config = _config(tmp_path, baemin_center_name="양주중앙")
    page = _FakePage(
        config.coupang_eats_url,
        center_tabs=[
            {"text": "양주중앙", "selected": False},
            {"text": "의정부남부", "selected": True},
            {"text": "의정부중앙", "selected": False},
        ],
    )

    clicked = crawler._select_coupang_center(page, config, timeout_errors=(FakeTimeout,))

    assert clicked is True
    assert page.clicked_tab_labels == ["양주중앙"]


def test_select_coupang_center_clicks_when_no_tab_active(tmp_path):
    # 여러 센터 계정이 아직 어떤 센터도 고르지 않은 통합 상태("협력사 N개")에서는
    # 어떤 탭에도 active 클래스가 없다. 이때도 일치 탭을 눌러 전환해야 한다.
    config = _config(tmp_path, baemin_center_name="양주중앙")
    page = _FakePage(
        config.coupang_eats_url,
        center_tabs=[
            {"text": "양주중앙", "selected": False},
            {"text": "의정부남부", "selected": False},
            {"text": "의정부중앙", "selected": False},
        ],
    )

    clicked = crawler._select_coupang_center(page, config, timeout_errors=(FakeTimeout,))

    assert clicked is True
    assert page.clicked_tab_labels == ["양주중앙"]


def test_select_coupang_center_matches_label_when_config_has_company_prefix(tmp_path):
    # 설정값이 회사명을 포함해도(헤딩 형태) 짧은 탭 라벨과 매칭돼 클릭돼야 한다.
    config = _config(tmp_path, baemin_center_name="제이앤에이치플러스 양주중앙")
    page = _FakePage(
        config.coupang_eats_url,
        center_tabs=[
            {"text": "양주중앙", "selected": False},
            {"text": "의정부남부", "selected": False},
        ],
    )

    clicked = crawler._select_coupang_center(page, config, timeout_errors=(FakeTimeout,))

    assert clicked is True
    assert page.clicked_tab_labels == ["양주중앙"]


def test_select_coupang_center_skips_already_active_tab(tmp_path):
    config = _config(tmp_path, baemin_center_name="양주중앙")
    page = _FakePage(
        config.coupang_eats_url,
        center_tabs=[
            {"text": "양주중앙", "selected": True},
            {"text": "의정부남부", "selected": False},
        ],
    )

    clicked = crawler._select_coupang_center(page, config, timeout_errors=(FakeTimeout,))

    assert clicked is False
    assert page.clicked_tab_labels == []


def test_select_coupang_center_noops_when_no_matching_tab(tmp_path):
    # 단일 센터 계정처럼 일치 탭이 없으면 클릭하지 않고 넘어간다(검증이 이후 단계에서 막음).
    config = _config(tmp_path, baemin_center_name="양주중앙")
    page = _FakePage(
        config.coupang_eats_url,
        center_tabs=[{"text": "의정부남부", "selected": True}],
    )

    clicked = crawler._select_coupang_center(page, config, timeout_errors=(FakeTimeout,))

    assert clicked is False
    assert page.clicked_tab_labels == []


def test_select_coupang_center_noops_when_center_name_empty(tmp_path):
    config = _config(tmp_path, baemin_center_name="")
    page = _FakePage(
        config.coupang_eats_url,
        center_tabs=[{"text": "양주중앙", "selected": False}],
    )

    clicked = crawler._select_coupang_center(page, config, timeout_errors=(FakeTimeout,))

    assert clicked is False
    assert page.clicked_tab_labels == []


def test_select_coupang_center_tolerates_pages_without_tabs(tmp_path):
    # evaluate가 실패해도(탭 UI 없음 등) 크롤링을 막지 않는다.
    config = _config(tmp_path, baemin_center_name="양주중앙")
    page = _FakePage(config.coupang_eats_url)  # center_tabs=None → evaluate raises

    clicked = crawler._select_coupang_center(page, config, timeout_errors=(FakeTimeout,))

    assert clicked is False
    assert page.clicked_tab_labels == []


def test_coupang_crawl_performance_snapshot_parses_peak_dashboard(tmp_path):
    peak_dashboard_html = _PEAK_DASHBOARD_HTML

    snapshot = crawl_performance_snapshot(
        _config(tmp_path),
        fetch_peak_dashboard_html=lambda _config: peak_dashboard_html,
    )

    assert snapshot.current_screen is None
    assert snapshot.peak_dashboard.updated_at == "20:38"
    assert snapshot.peak_dashboard.assigned_count == 103
    assert snapshot.peak_dashboard.processed_count == 67
    assert snapshot.peak_dashboard.reject_rate == 6.5
    assert snapshot.peak_dashboard.dinner_non_peak.done == 2
    assert snapshot.peak_dashboard.dinner_non_peak.total == 27


def test_coupang_crawl_performance_snapshot_derives_current_url_when_primary_url_is_peak(tmp_path, monkeypatch):
    # 주 URL에 peak-dashboard가 들어와도 같은 host의 rider-performance를 함께 읽어
    # 온라인 수행중 인원을 채운다.
    base_config = _config(tmp_path)
    config = replace(base_config, coupang_eats_url=base_config.peak_dashboard_url, peak_dashboard_url="")
    rider_performance_url = "https://partner.coupangeats.com/page/rider-performance"
    current_html = """
    <main>
      <h1>제이앤에이치플러스 의정부남부</h1>
      <h2>제이앤에이치플러스 의정부남부 밤논피크(20:00~06:00) 할당량 소진 중 라이더 현황</h2>
      <p>01:05 업데이트</p>
      <p>밤논피크 참여 가능</p><p>0 / 15 명</p>
      <p>대기</p><p>0명</p>
      <section><h3>활성 라이더</h3><p>이름 / 연락처</p><p>총 4명</p></section>
      <p>온라인 0명</p>
      <p>거절/무시: 5.8건</p><p>취소: 1건</p><p>완료: 78.8건</p>
      <p>순서 미준수: 0건</p><p>점심피크: 21.6건</p><p>저녁피크: 15.8건</p><p>논피크: 41.4건</p>
    </main>
    """
    html_by_url = {
        rider_performance_url: current_html,
        config.coupang_eats_url: _PEAK_DASHBOARD_HTML,
    }
    requested_urls: list[str | None] = []

    def fake_fetch_page_html(_config, *, target_url=None, force_new_tab=False):
        requested_urls.append(target_url)
        return html_by_url[target_url]

    monkeypatch.setattr(crawler, "fetch_page_html", fake_fetch_page_html)

    snapshot = crawl_performance_snapshot(config)

    assert requested_urls == [rider_performance_url, config.coupang_eats_url]
    assert snapshot.current_screen is not None
    assert snapshot.current_screen.active_riders == 0
    assert snapshot.peak_dashboard.updated_at == "20:38"


def test_coupang_fetch_page_html_uses_cdp_mode_by_default(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="cdp")
    monkeypatch.setattr(
        crawler,
        "fetch_page_html_via_cdp",
        lambda _config, *, target_url=None, force_new_tab=False: "cdp-html",
    )
    monkeypatch.setattr(
        crawler,
        "fetch_page_html_via_persistent_context",
        lambda _config, *, target_url=None: "persistent-html",
    )

    assert crawler.fetch_page_html(config) == "cdp-html"


def test_coupang_fetch_page_html_keeps_persistent_context_as_fallback(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="persistent")
    monkeypatch.setattr(crawler, "fetch_page_html_via_cdp", lambda _config, *, target_url=None: "cdp-html")
    monkeypatch.setattr(
        crawler,
        "fetch_page_html_via_persistent_context",
        lambda _config, *, target_url=None: "persistent-html",
    )

    assert crawler.fetch_page_html(config) == "persistent-html"


def test_coupang_select_page_by_url_allows_query_and_hash():
    pages = [
        _FakePage("https://partner.coupangeats.com/page/peak-dashboard"),
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=1#today"),
    ]

    page = crawler._select_page_by_url(pages, "https://partner.coupangeats.com/page/rider-performance")

    assert page is pages[1]


def test_coupang_select_page_by_url_prefers_exact_match_over_path_match():
    target = "https://partner.coupangeats.com/page/rider-performance?center=1"
    pages = [
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=2"),
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=1"),
    ]

    page = crawler._select_page_by_url(pages, target)

    assert page is pages[1]


def test_coupang_select_page_by_url_rejects_duplicate_exact_matches():
    target = "https://partner.coupangeats.com/page/rider-performance?center=1"
    pages = [
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=1"),
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=1"),
    ]

    assert crawler._select_page_by_url(pages, target) is None


def test_coupang_select_page_by_url_rejects_scheme_mismatch_in_path_fallback():
    # https 대상에 http 탭만 열려 있으면 매칭되지 않아야 한다(다운그레이드 탭 방지).
    target = "https://partner.coupangeats.com/page/rider-performance"
    pages = [_FakePage("http://partner.coupangeats.com/page/rider-performance")]

    assert crawler._select_page_by_url(pages, target) is None


def test_coupang_select_page_by_url_rejects_duplicate_path_matches():
    target = "https://partner.coupangeats.com/page/rider-performance"
    pages = [
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=1"),
        _FakePage("https://partner.coupangeats.com/page/rider-performance?center=2"),
    ]

    assert crawler._select_page_by_url(pages, target) is None


def test_coupang_fetch_target_page_content_does_not_close_cdp_browser(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser([_FakePage(config.coupang_eats_url, html="<html>ok</html>")])

    html = crawler._fetch_target_page_content(browser, config)

    assert html == "<html>ok</html>"
    assert browser.closed is False


def test_coupang_fetch_target_page_content_does_not_open_new_tab_when_target_missing(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser([_FakePage("about:blank", html="<html></html>")])

    with pytest.raises(BrowserActionRequiredError, match="열려 있는 Chrome 탭"):
        crawler._fetch_target_page_content(browser, config)

    assert browser.contexts[0].new_page_calls == 0


def test_coupang_fetch_target_page_content_does_not_open_new_tab_when_target_duplicated(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser(
        [
            _FakePage(config.coupang_eats_url, html="<html>old</html>"),
            _FakePage(config.coupang_eats_url, html="<html>new</html>"),
        ]
    )

    with pytest.raises(BrowserActionRequiredError, match="대상 탭이 여러 개"):
        crawler._fetch_target_page_content(browser, config)

    assert browser.contexts[0].new_page_calls == 0


def test_coupang_fetch_target_page_content_opens_temp_tab_for_missing_rider_performance(tmp_path):
    # peak-dashboard만 로그인돼 떠 있고 rider-performance 탭이 없으면, 같은 세션에 임시
    # 탭을 새로 열어 rider-performance를 직접 읽고 닫는다.
    config = _config(tmp_path)
    rider_url = "https://partner.coupangeats.com/page/rider-performance"
    peak_page = _FakePage(
        "https://partner.coupangeats.com/page/peak-dashboard", html="<html>logged in</html>"
    )
    browser = _FakeBrowser([peak_page], new_page_html="<html>라이더 현황 총 4명</html>")

    html = crawler._fetch_target_page_content(
        browser, config, target_url=rider_url, load_timeout_errors=(FakeTimeout,)
    )

    assert html == "<html>라이더 현황 총 4명</html>"
    assert browser.contexts[0].new_page_calls == 1
    temp_tab = browser.contexts[0].opened_pages[0]
    assert temp_tab.goto_calls == [rider_url]
    assert temp_tab.closed is True


def test_coupang_fetch_target_page_content_opens_temp_tab_for_missing_peak_when_logged_in(tmp_path):
    # rider-performance만 로그인돼 떠 있고 peak-dashboard 탭이 없으면, 같은 세션에 임시
    # 탭을 열어 피크 실적을 읽는다.
    config = _config(tmp_path)
    peak_url = "https://partner.coupangeats.com/page/peak-dashboard"
    rider_page = _FakePage(
        "https://partner.coupangeats.com/page/rider-performance", html="<html>logged in</html>"
    )
    browser = _FakeBrowser([rider_page], new_page_html="<html>피크타임별 현황</html>")

    html = crawler._fetch_target_page_content(
        browser, config, target_url=peak_url, load_timeout_errors=(FakeTimeout,)
    )

    assert html == "<html>피크타임별 현황</html>"
    assert browser.contexts[0].new_page_calls == 1
    temp_tab = browser.contexts[0].opened_pages[0]
    assert temp_tab.goto_calls == [peak_url]
    assert temp_tab.closed is True
    assert rider_page.closed is False


def test_coupang_fetch_target_page_content_skips_temp_tab_when_no_logged_in_context(tmp_path):
    config = _config(tmp_path)
    rider_url = "https://partner.coupangeats.com/page/rider-performance"
    browser = _FakeBrowser([_FakePage("about:blank", html="<html></html>")])

    with pytest.raises(BrowserActionRequiredError, match="열려 있는 Chrome 탭"):
        crawler._fetch_target_page_content(
            browser, config, target_url=rider_url, load_timeout_errors=(FakeTimeout,)
        )

    assert browser.contexts[0].new_page_calls == 0


def test_coupang_fetch_target_page_content_reports_login_required_without_fast_retry(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))


def test_coupang_fetch_target_page_content_reports_vendor_portal_login_structure(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                html="""
                <html>
                  <head><title>vendor-portal</title></head>
                  <body>
                    <div>Vendor Portal</div>
                    <form action="https://xauth.coupang.com/auth/realms/eats-partner/login-actions/authenticate" method="post">
                      <input class="ant-input ant-input-borderless" type="text" placeholder="아이디 입력">
                      <input class="ant-input ant-input-borderless" type="password" placeholder="비밀번호 입력">
                      <input name="username">
                      <input name="password">
                      <input name="credentialId">
                      <button class="ant-btn ant-btn-primary login-input-button" type="button">로그인</button>
                    </form>
                  </body>
                </html>
                """,
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))


def test_coupang_fetch_target_page_content_reports_xauth_login_form_without_vendor_text(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                html="""
                <html>
                  <body>
                    <form action="/auth/realms/eats-partner/login-actions/authenticate">
                      <input name="username" autocomplete="username">
                      <input name="password" type="password">
                      <input name="credentialId">
                      <button type="submit">로그인</button>
                    </form>
                  </body>
                </html>
                """,
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))


def test_coupang_login_detection_does_not_match_plain_login_word_only():
    page = _FakePage(
        "https://partner.coupangeats.com/page/rider-performance",
        html="<html><body>로그인 안내 문구만 있는 일반 오류</body></html>",
    )

    assert crawler._page_looks_like_coupang_login_required(page) is False


def test_coupang_fetch_target_page_content_reports_login_url_without_fast_retry(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                final_url="https://partner.coupangeats.com/login",
                html="<html><body>Login</body></html>",
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))


def test_coupang_fetch_target_page_content_reports_xauth_login_url_without_fast_retry(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                final_url=(
                    "https://xauth.coupang.com/auth/realms/eats-partner/protocol/"
                    "openid-connect/auth?client_id=edp-vendor-portal"
                ),
                html="<html><body>Vendor Portal</body></html>",
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))


def test_coupang_fetch_target_page_content_wraps_locator_timeout_with_actionable_message(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser([_FakePage(config.coupang_eats_url, wait_error=FakeTimeout("locator timeout"))])

    with pytest.raises(RuntimeError, match="쿠팡이츠 실적 페이지"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))


def test_coupang_fetch_target_page_content_reports_peak_dashboard_readiness_timeout(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser(
        [_FakePage(config.peak_dashboard_url, wait_error=FakeTimeout("locator timeout"))]
    )

    with pytest.raises(RuntimeError, match="쿠팡이츠 피크 대시보드"):
        crawler._fetch_target_page_content(
            browser,
            config,
            target_url=config.peak_dashboard_url,
            load_timeout_errors=(FakeTimeout,),
        )


def test_coupang_fetch_target_page_content_waits_for_peak_dashboard_required_text(tmp_path):
    config = _config(tmp_path)
    page = _FakePage(config.peak_dashboard_url, html="<html>피크타임별 현황</html>")
    browser = _FakeBrowser([page])

    html = crawler._fetch_target_page_content(
        browser, config, target_url=config.peak_dashboard_url
    )

    assert html == "<html>피크타임별 현황</html>"
    assert page.required_texts == ["피크타임별 현황"]


def test_coupang_fetch_target_page_content_refreshes_peak_dashboard_once_before_failing(tmp_path):
    # 재시도해도 계속 타임아웃이면 결국 준비 실패(RuntimeError)로 끝난다. 단, 그 전에
    # peak-dashboard를 한 번 reload하고 required_text를 다시 기다린다(두 번 시도).
    config = _config(tmp_path)
    page = _FakePage(config.peak_dashboard_url, wait_error=FakeTimeout("locator timeout"))
    browser = _FakeBrowser([page])

    with pytest.raises(RuntimeError, match="쿠팡이츠 피크 대시보드"):
        crawler._fetch_target_page_content(
            browser,
            config,
            target_url=config.peak_dashboard_url,
            load_timeout_errors=(FakeTimeout,),
        )

    # required_text 대기를 두 번 했고(첫 시도 + 재시도), 그 사이 reload(target_url로 goto)를
    # 정확히 한 번 했다. goto를 직접 단언해 'wait_for만 두 번' 구현으로는 통과하지 못하게 한다.
    assert page.required_texts == ["피크타임별 현황", "피크타임별 현황"]
    assert page.goto_calls == [config.peak_dashboard_url]


def test_coupang_fetch_target_page_content_refreshes_peak_dashboard_once_and_succeeds(tmp_path):
    # 첫 대기는 타임아웃이지만 reload 후 둘째 대기는 통과하면 정상 HTML을 돌려준다.
    config = _config(tmp_path)
    page = _RefreshablePeakPage(
        config.peak_dashboard_url,
        ready_html="<html>피크타임별 현황</html>",
    )
    browser = _FakeBrowser([page])

    html = crawler._fetch_target_page_content(
        browser,
        config,
        target_url=config.peak_dashboard_url,
        load_timeout_errors=(FakeTimeout,),
    )

    assert html == "<html>피크타임별 현황</html>"
    assert page.required_texts == ["피크타임별 현황", "피크타임별 현황"]
    # 성공 케이스도 reload(target_url로 goto)를 한 번 거친 뒤 둘째 대기가 통과한 것이다.
    assert page.goto_calls == [config.peak_dashboard_url]


def test_coupang_fetch_target_page_content_reopens_chrome_error_page_once(tmp_path):
    config = _config(tmp_path)
    page = _ChromeErrorThenReadyPage(
        config.coupang_eats_url,
        ready_html="<html><body>라이더 현황</body></html>",
    )
    browser = _FakeBrowser([page])

    html = crawler._fetch_target_page_content(
        browser,
        config,
        load_timeout_errors=(FakeTimeout,),
    )

    assert html == "<html><body>라이더 현황</body></html>"
    assert page.goto_calls == [config.coupang_eats_url]


def test_coupang_fetch_target_page_content_reports_persistent_chrome_error_page(tmp_path):
    config = _config(tmp_path)
    page = _PersistentChromeErrorPage(config.coupang_eats_url)
    browser = _FakeBrowser([page])

    with pytest.raises(RuntimeError, match="Chrome 오류 페이지.*오류코드: 15"):
        crawler._fetch_target_page_content(
            browser,
            config,
            load_timeout_errors=(FakeTimeout,),
        )

    assert page.goto_calls == [config.coupang_eats_url]


def test_coupang_login_required_stops_tab_when_auto_2fa_disabled(tmp_path):
    # 자동 2FA를 명시적으로 끄면 로그인 만료 시 기존처럼 탭을 중지한다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=False)
    assert config.coupang_auto_email_2fa_enabled is False
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )
    recover_calls: list[object] = []

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(
            browser,
            config,
            load_timeout_errors=(FakeTimeout,),
            recover_session=lambda page, _config: recover_calls.append(page) or True,
        )

    # 꺼져 있으면 복구 함수를 아예 호출하지 않는다.
    assert recover_calls == []


def test_log_recovery_failure_masks_email_and_omits_secrets(tmp_path):
    config = replace(
        _config(tmp_path, coupang_auto_email_2fa_enabled=True),
        verification_email_address="rider1234@naver.com",
        verification_email_app_password="super-secret-app-pass",
    )

    exc = RuntimeError(
        "인증 메일 미도착 "
        "rider1234@naver.com super-secret-app-pass OTP=123456 token=abc query=secret"
    )
    exc.recovery_step = "fetch_otp"
    exc.recovery_reason = "otp_not_found"

    crawler._log_recovery_failure(config, exc)

    log_text = (config.log_dir / "run_errors.log").read_text(encoding="utf-8")
    assert "provider=naver" in log_text
    assert "r***@naver.com" in log_text
    assert "exception_class=RuntimeError" in log_text
    assert "step=fetch_otp" in log_text
    assert "reason=otp_not_found" in log_text
    assert "rider1234@naver.com" not in log_text
    assert "super-secret-app-pass" not in log_text
    assert "123456" not in log_text
    assert "token=" not in log_text
    assert "query=" not in log_text


def test_coupang_login_required_recovers_when_auto_2fa_enabled_and_recovery_succeeds(tmp_path):
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    # 첫 준비 대기에서는 로그인 만료로 실패하고, 복구 후 다시 열면 준비가 된다.
    page = _RecoverablePage(
        config.coupang_eats_url,
        login_html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
        ready_html="<html>라이더 현황 ok</html>",
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    def _recover(received_page, _config):
        recover_calls.append(received_page)
        received_page.mark_recovered()
        return True

    html = crawler._fetch_target_page_content(
        browser,
        config,
        load_timeout_errors=(FakeTimeout,),
        recover_session=_recover,
    )

    assert recover_calls == [page]
    assert page.reopened is True
    assert html == "<html>라이더 현황 ok</html>"


def test_coupang_recovery_uses_mailbox_run_lock_before_recover(tmp_path, monkeypatch):
    config = replace(
        _config(tmp_path, coupang_auto_email_2fa_enabled=True),
        verification_email_mailbox_lock_id="vault://mail/address",
    )
    page = _RecoverablePage(
        config.coupang_eats_url,
        login_html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
        ready_html="<html>라이더 현황 ok</html>",
    )
    browser = _FakeBrowser([page])
    events: list[str] = []
    lock_paths: list[Path] = []

    class FakeRunLock:
        def __init__(self, path, *, stale_timeout_seconds):
            lock_paths.append(path)
            assert stale_timeout_seconds == config.run_lock_timeout_seconds

        def __enter__(self):
            events.append("lock-enter")
            return self

        def __exit__(self, *_args):
            events.append("lock-exit")

    def _recover(received_page, _config):
        events.append("recover")
        received_page.mark_recovered()
        return True

    monkeypatch.setattr(crawler, "RunLock", FakeRunLock)

    html = crawler._fetch_target_page_content(
        browser,
        config,
        load_timeout_errors=(FakeTimeout,),
        recover_session=_recover,
    )

    assert events == ["lock-enter", "recover", "lock-exit"]
    assert len(lock_paths) == 1
    assert lock_paths[0].parent == config.runtime_dir / "state" / "mailbox_locks"
    assert "vault://mail/address" not in str(lock_paths[0])
    assert html == "<html>라이더 현황 ok</html>"


def test_coupang_login_required_stops_when_recovery_fails(tmp_path):
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(
            browser,
            config,
            load_timeout_errors=(FakeTimeout,),
            recover_session=lambda page, _config: False,
        )


def test_coupang_recovery_swallows_recover_exception_and_stops_tab(tmp_path):
    # 복구 함수가 예외를 던져도(예: Gmail 미도착) 인증번호 누출 없이 기존 로그인 필요
    # 오류로 탭을 중지한다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    browser = _FakeBrowser(
        [
            _FakePage(
                config.coupang_eats_url,
                html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
                wait_error=FakeTimeout("locator timeout"),
            )
        ]
    )

    def _boom(page, _config):
        raise RuntimeError("인증 메일 미도착")

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(
            browser,
            config,
            load_timeout_errors=(FakeTimeout,),
            recover_session=_boom,
        )


def test_coupang_recovers_when_target_tab_url_drifted_to_login(tmp_path):
    # 로그인 만료로 대상 탭 URL이 xauth 로그인으로 바뀌어 대상 탭 매칭이 실패한 경우.
    # 자동 2FA가 켜져 있으면 로그인 페이지에서 복구 후 대상 URL로 되돌려 읽는다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    login_url = (
        "https://xauth.coupang.com/auth/realms/eats-partner/protocol/openid-connect/auth"
    )
    page = _LoginDriftPage(
        login_url=login_url,
        target_url=config.coupang_eats_url,
        ready_html="<html>라이더 현황 ok</html>",
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    def _recover(received_page, _config):
        recover_calls.append(received_page)
        received_page.mark_recovered()
        return True

    html = crawler._fetch_target_page_content(
        browser,
        config,
        load_timeout_errors=(FakeTimeout,),
        recover_session=_recover,
    )

    assert recover_calls == [page]
    assert html == "<html>라이더 현황 ok</html>"


def test_coupang_login_drift_reopens_target_when_session_already_restored(tmp_path):
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    login_url = (
        "https://xauth.coupang.com/auth/realms/eats-partner/protocol/openid-connect/auth"
    )
    page = _SessionRestoredLoginDriftPage(
        login_url=login_url,
        target_url=config.peak_dashboard_url,
        ready_html="<html>피크타임별 현황 ok</html>",
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    html = crawler._fetch_target_page_content(
        browser,
        config,
        target_url=config.peak_dashboard_url,
        load_timeout_errors=(FakeTimeout,),
        recover_session=lambda received_page, _config: recover_calls.append(received_page) or True,
    )

    assert recover_calls == []
    assert page.url == config.peak_dashboard_url
    assert html == "<html>피크타임별 현황 ok</html>"


def test_coupang_login_drift_stops_when_auto_2fa_disabled(tmp_path):
    # 2FA가 꺼져 있으면 URL이 로그인으로 바뀐 경우에도 기존처럼 운영자 조치 오류로 중지.
    config = _config(tmp_path)
    login_url = (
        "https://xauth.coupang.com/auth/realms/eats-partner/protocol/openid-connect/auth"
    )
    page = _LoginDriftPage(
        login_url=login_url,
        target_url=config.coupang_eats_url,
        ready_html="<html>라이더 현황 ok</html>",
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(
            browser,
            config,
            load_timeout_errors=(FakeTimeout,),
            recover_session=lambda p, _c: recover_calls.append(p) or True,
        )

    assert recover_calls == []


# 세션이 만료됐는데 화면이 로그인으로 분류되지 않아 readiness(앵커 텍스트)는 통과하고 peak
# 파싱만 실패하는 창을 재현하는 HTML. 앵커("피크타임별 현황")는 있지만 parse_peak_dashboard_text
# 가 요구하는 "업데이트"/배정·처리 물량/거절률/목표·완료 쌍이 없고, 로그인 만료 신호가 있다.
_PEAK_MISSING_DATA_LOGIN_HTML = (
    "<html><body><h2>피크타임별 현황</h2>"
    "세션이 만료되었습니다. 다시 로그인하세요.</body></html>"
)
# 데이터는 비었지만 로그인 신호는 없는 화면(영업외/물량 0 같은 정상 인증 상태). 이 경우엔
# 자동복구를 하면 안 된다(OTP 낭비). 앵커만 있고 나머지 필수 필드가 없다.
_PEAK_MISSING_DATA_AUTHED_HTML = (
    "<html><body><h2>피크타임별 현황</h2>아직 집계된 실적이 없습니다.</body></html>"
)


class _MissingDataPeakPage:
    """readiness 는 통과하지만 첫 content 는 데이터 누락인 peak 페이지.

    ``login_looking`` 이면 누락 화면이 로그인 만료로 보인다(자동복구 게이트 통과). 첫
    ``content()`` 는 누락 HTML, ``mark_recovered()`` + 재오픈(goto/reload) 뒤에는 정상 peak
    HTML 을 준다. ``wait_for`` 는 항상 통과한다(앵커 텍스트가 늘 있으므로) — 로그인-만료
    readiness 경로가 아니라 "ready 통과 + 파싱 실패" 창만 재현한다.
    """

    def __init__(self, url: str, *, login_looking: bool, ready_html: str) -> None:
        self.url = url
        self._login_looking = login_looking
        self._ready_html = ready_html
        self._recovered = False
        self.reopened = False
        self.required_texts: list[str] = []

    def mark_recovered(self) -> None:
        self._recovered = True

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def goto(self, _url, **_kwargs):
        self.reopened = True
        return None

    def reload(self, **_kwargs):
        self.reopened = True
        return None

    def get_by_text(self, text: str):
        self.required_texts.append(text)
        return self

    def wait_for(self, **_kwargs):
        # 앵커 텍스트는 누락 화면에도 있으므로 readiness 는 항상 통과한다.
        return None

    def evaluate(self, _script):
        return []

    def content(self) -> str:
        if self.reopened and (self._recovered or not self._login_looking):
            return self._ready_html
        return (
            _PEAK_MISSING_DATA_LOGIN_HTML
            if self._login_looking
            else _PEAK_MISSING_DATA_AUTHED_HTML
        )


def test_coupang_missing_data_on_login_page_recovers_when_auto_2fa_enabled(tmp_path):
    # 세션 만료로 데이터 누락 + 화면이 로그인으로 보임 + 자동 2FA 켜짐 → 같은 턴 1회 복구 후
    # 재파싱 성공. 복구는 정확히 1회.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    page = _MissingDataPeakPage(
        config.peak_dashboard_url,
        login_looking=True,
        ready_html=_PEAK_DASHBOARD_HTML_WITH_CENTER,
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    def _recover(received_page, _config):
        recover_calls.append(received_page)
        received_page.mark_recovered()
        return True

    html = crawler._fetch_target_page_content(
        browser,
        config,
        target_url=config.peak_dashboard_url,
        load_timeout_errors=(FakeTimeout,),
        recover_session=_recover,
        post_load_validate=crawler.parse_peak_dashboard_html,
    )

    assert recover_calls == [page]
    assert page.reopened is True
    assert html == _PEAK_DASHBOARD_HTML_WITH_CENTER


def test_coupang_missing_data_on_login_page_escalates_to_auth_required_when_recovery_fails(tmp_path):
    # 데이터 누락 + 로그인으로 보임 + 복구 실패 → MissingPerformanceDataError 가 아니라
    # BrowserActionRequiredError 로 올려 워커가 AUTH_REQUIRED 로 표면화하게 한다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    page = _MissingDataPeakPage(
        config.peak_dashboard_url,
        login_looking=True,
        ready_html=_PEAK_DASHBOARD_HTML_WITH_CENTER,
    )
    browser = _FakeBrowser([page])

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(
            browser,
            config,
            target_url=config.peak_dashboard_url,
            load_timeout_errors=(FakeTimeout,),
            recover_session=lambda _page, _config: False,
            post_load_validate=crawler.parse_peak_dashboard_html,
        )


def test_coupang_missing_data_on_authed_page_refreshes_once_without_recovery(tmp_path):
    # 데이터는 비었지만 화면이 로그인으로 보이지 않음(정상 인증 빈 shell) → OTP 복구는 하지 않고
    # target URL 을 한 번 reload 한 뒤 재파싱한다. reload 후 실제 데이터가 채워지면 성공한다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    page = _MissingDataPeakPage(
        config.peak_dashboard_url,
        login_looking=False,
        ready_html=_PEAK_DASHBOARD_HTML_WITH_CENTER,
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    html = crawler._fetch_target_page_content(
        browser,
        config,
        target_url=config.peak_dashboard_url,
        load_timeout_errors=(FakeTimeout,),
        recover_session=lambda p, _c: recover_calls.append(p) or True,
        post_load_validate=crawler.parse_peak_dashboard_html,
    )

    assert recover_calls == []
    assert page.reopened is True
    assert html == _PEAK_DASHBOARD_HTML_WITH_CENTER


def test_coupang_missing_data_on_authed_page_still_fails_after_one_refresh(tmp_path):
    # reload 후에도 인증된 빈 shell 이 계속되면 기존처럼 MissingPerformanceDataError 로 끝난다.
    # 단, 실패 전 target URL reload 는 정확히 한 번 수행되어야 한다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    page = _MissingDataPeakPage(
        config.peak_dashboard_url,
        login_looking=False,
        ready_html=_PEAK_MISSING_DATA_AUTHED_HTML,
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    with pytest.raises(crawler.MissingPerformanceDataError):
        crawler._fetch_target_page_content(
            browser,
            config,
            target_url=config.peak_dashboard_url,
            load_timeout_errors=(FakeTimeout,),
            recover_session=lambda p, _c: recover_calls.append(p) or True,
            post_load_validate=crawler.parse_peak_dashboard_html,
        )

    assert recover_calls == []
    assert page.reopened is True


def test_coupang_current_screen_rejects_placeholder_record_table_shell():
    html = """
    <main>
      <h1>해운대플러스 수영중앙</h1>
      <section>
        <h2>아침논피크(06:00~10:55)</h2>
        <p>할당량 소진 중</p>
        <h2>라이더 현황</h2>
        <p>21:32 업데이트</p>
      </section>
      <section>
        <h3>라이더 정보</h3>
        <h3>종합기록</h3>
        <h3>피크별 상세 완료 건수</h3>
      </section>
      <table>
        <thead>
          <tr>
            <th>이름 / 연락처</th>
            <th>상태 온라인 -</th>
            <th>권한 -</th>
            <th>거절/무시 -</th>
            <th>취소 -</th>
            <th>완료 -</th>
            <th>순서 미준수 -</th>
            <th>점심피크 -</th>
            <th>저녁피크 -</th>
            <th>논피크 -</th>
          </tr>
        </thead>
      </table>
    </main>
    """

    with pytest.raises(crawler.MissingPerformanceDataError):
        crawler.parse_current_screen_html(html)


def test_coupang_missing_data_recovery_disabled_propagates_missing_data(tmp_path):
    # post_load_validate 가 None(자동 2FA off)이면 검증 자체를 안 한다 → content 를 그대로
    # 반환하고 회복도 호출하지 않는다(기능 off = 무변화). 누락 판정은 상위 파서가 한다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=False)
    page = _MissingDataPeakPage(
        config.peak_dashboard_url,
        login_looking=True,
        ready_html=_PEAK_DASHBOARD_HTML_WITH_CENTER,
    )
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    html = crawler._fetch_target_page_content(
        browser,
        config,
        target_url=config.peak_dashboard_url,
        load_timeout_errors=(FakeTimeout,),
        recover_session=lambda p, _c: recover_calls.append(p) or True,
        post_load_validate=None,
    )

    assert recover_calls == []
    assert html == _PEAK_MISSING_DATA_LOGIN_HTML


def test_coupang_recovery_runs_at_most_once_across_login_and_missing_data(tmp_path):
    # readiness 단계에서 먼저 로그인 만료로 복구가 1회 일어난 뒤에도 재파싱이 여전히 누락+
    # 로그인으로 보이면, 두 번째 복구를 하지 않고(더블 OTP 금지) BrowserActionRequiredError
    # 로 포기한다.
    config = _config(tmp_path, coupang_auto_email_2fa_enabled=True)
    page = _ReadyThenMissingDataPage(config.peak_dashboard_url)
    browser = _FakeBrowser([page])
    recover_calls: list[object] = []

    def _recover(received_page, _config):
        recover_calls.append(received_page)
        received_page.mark_recovered()
        return True

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(
            browser,
            config,
            target_url=config.peak_dashboard_url,
            load_timeout_errors=(FakeTimeout,),
            recover_session=_recover,
            post_load_validate=crawler.parse_peak_dashboard_html,
        )

    assert len(recover_calls) == 1


def test_coupang_missing_data_recovery_uses_mailbox_run_lock(tmp_path, monkeypatch):
    # missing-data 복구도 readiness 복구와 동일하게 메일박스 RunLock 으로 직렬화한다.
    config = replace(
        _config(tmp_path, coupang_auto_email_2fa_enabled=True),
        verification_email_mailbox_lock_id="vault://mail/address",
    )
    page = _MissingDataPeakPage(
        config.peak_dashboard_url,
        login_looking=True,
        ready_html=_PEAK_DASHBOARD_HTML_WITH_CENTER,
    )
    browser = _FakeBrowser([page])
    events: list[str] = []

    class FakeRunLock:
        def __init__(self, path, *, stale_timeout_seconds):
            assert stale_timeout_seconds == config.run_lock_timeout_seconds

        def __enter__(self):
            events.append("lock-enter")
            return self

        def __exit__(self, *_args):
            events.append("lock-exit")

    def _recover(received_page, _config):
        events.append("recover")
        received_page.mark_recovered()
        return True

    monkeypatch.setattr(crawler, "RunLock", FakeRunLock)

    html = crawler._fetch_target_page_content(
        browser,
        config,
        target_url=config.peak_dashboard_url,
        load_timeout_errors=(FakeTimeout,),
        recover_session=_recover,
        post_load_validate=crawler.parse_peak_dashboard_html,
    )

    assert events == ["lock-enter", "recover", "lock-exit"]
    assert html == _PEAK_DASHBOARD_HTML_WITH_CENTER


def _config(
    tmp_path,
    *,
    browser_mode: str = "cdp",
    baemin_center_name: str = "",
    coupang_auto_email_2fa_enabled: bool = False,
) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        peak_dashboard_url="https://partner.coupangeats.com/page/peak-dashboard",
        platform_name="coupang",
        baemin_center_name=baemin_center_name,
        baemin_center_id="",
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
        coupang_auto_email_2fa_enabled=coupang_auto_email_2fa_enabled,
    )


class FakeTimeout(Exception):
    pass


class _FakePage:
    def __init__(
        self,
        url: str,
        html: str = "",
        final_url: str | None = None,
        wait_error: Exception | None = None,
        center_tabs: list[dict] | None = None,
    ) -> None:
        self.url = final_url or url
        self.html = html
        self.wait_error = wait_error
        self.required_texts: list[str] = []
        # ``center_tabs``는 _COUPANG_CENTER_TAB_JS가 돌려주는 값을 흉내 낸다.
        self.center_tabs = center_tabs
        self.clicked_tab_labels: list[str] = []
        self.goto_calls: list[str] = []
        self.closed = False

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self.url = url
        return None

    def close(self):
        self.closed = True

    def get_by_text(self, text: str):
        self.required_texts.append(text)
        return self

    def wait_for(self, **_kwargs):
        if self.wait_error:
            raise self.wait_error
        return None

    def evaluate(self, _script):
        if self.center_tabs is None:
            raise RuntimeError("evaluate not supported")
        return self.center_tabs

    def locator(self, _selector):
        return _FakeTabLocator(self)

    def content(self) -> str:
        return self.html


class _RefreshablePeakPage(_FakePage):
    """첫 wait_for는 타임아웃, reload(goto) 뒤 둘째 wait_for는 통과하는 피크 페이지.

    peak-dashboard 준비 지연 시 1회 자동 재시도(_reload_target_page → 재대기) 흐름을
    검증한다.
    """

    def __init__(self, url: str, *, ready_html: str) -> None:
        super().__init__(url, html="<html>loading</html>")
        self._ready_html = ready_html
        self._wait_attempts = 0

    def goto(self, url, **kwargs):
        super().goto(url, **kwargs)
        self.html = self._ready_html
        return None

    def wait_for(self, **_kwargs):
        self._wait_attempts += 1
        if self._wait_attempts == 1:
            raise FakeTimeout("locator timeout")
        return None


_CHROME_ERROR_15_HTML = """
<html>
  <body>
    <h1>이 웹페이지를 표시하는 도중 문제가 발생했습니다.</h1>
    <p>오류코드: 15</p>
  </body>
</html>
"""


class _ChromeErrorThenReadyPage(_FakePage):
    def __init__(self, url: str, *, ready_html: str) -> None:
        super().__init__(
            url,
            html=_CHROME_ERROR_15_HTML,
            wait_error=FakeTimeout("locator timeout"),
        )
        self._ready_html = ready_html

    def goto(self, url, **kwargs):
        super().goto(url, **kwargs)
        self.html = self._ready_html
        self.wait_error = None
        return None


class _PersistentChromeErrorPage(_FakePage):
    def __init__(self, url: str) -> None:
        super().__init__(
            url,
            html=_CHROME_ERROR_15_HTML,
            wait_error=FakeTimeout("locator timeout"),
        )

    def goto(self, url, **kwargs):
        super().goto(url, **kwargs)
        return None


class _RecoverablePage:
    """A page that is login-expired until recovery, then serves the ready target.

    첫 ``wait_for``는 로그인 만료(타임아웃)로 실패하고 content는 로그인 HTML이다.
    ``mark_recovered()`` 뒤 ``goto``/``reload``로 다시 열면 준비된 대상 HTML을 주고
    ``wait_for``가 통과한다. 자동 2FA 복구 후 대상 페이지 재준비 흐름을 검증한다.
    """

    def __init__(self, url: str, *, login_html: str, ready_html: str) -> None:
        self.url = url
        self._login_html = login_html
        self._ready_html = ready_html
        self._recovered = False
        self.reopened = False
        self.required_texts: list[str] = []

    def mark_recovered(self) -> None:
        self._recovered = True

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def goto(self, _url, **_kwargs):
        self.reopened = True
        return None

    def reload(self, **_kwargs):
        self.reopened = True
        return None

    def get_by_text(self, text: str):
        self.required_texts.append(text)
        return self

    def wait_for(self, **_kwargs):
        # 복구 전에는 대상 텍스트가 없어 타임아웃, 복구 후 재오픈되면 준비 완료.
        if self._recovered and self.reopened:
            return None
        raise FakeTimeout("locator timeout")

    def evaluate(self, _script):
        # 센터 탭 조회는 이 테스트에서 불필요하므로 빈 목록.
        return []

    def content(self) -> str:
        return self._ready_html if (self._recovered and self.reopened) else self._login_html


class _LoginDriftPage:
    """A tab whose URL drifted to the login/xauth screen on session expiry.

    대상 URL 매칭이 실패하지만 ``_page_looks_like_coupang_login_required``에는 걸리는
    상태다. 복구 후 ``goto(target_url)``로 되돌리면 URL이 대상과 맞고 준비 HTML을 준다.
    """

    def __init__(self, *, login_url: str, target_url: str, ready_html: str) -> None:
        self.url = login_url
        self._target_url = target_url
        self._ready_html = ready_html
        self._recovered = False
        self.required_texts: list[str] = []

    def mark_recovered(self) -> None:
        self._recovered = True

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        # 복구 후 대상 URL로 되돌리는 호출만 반영한다.
        if self._recovered:
            self.url = url
        return None

    def reload(self, **_kwargs):
        return None

    def get_by_text(self, text: str):
        self.required_texts.append(text)
        return self

    def wait_for(self, **_kwargs):
        if self._recovered and self.url == self._target_url:
            return None
        raise FakeTimeout("locator timeout")

    def evaluate(self, _script):
        return []

    def content(self) -> str:
        # 복구 전에는 로그인 필요 신호를 노출해 _login_required_page에 걸리게 한다.
        if self._recovered and self.url == self._target_url:
            return self._ready_html
        return "<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>"


class _SessionRestoredLoginDriftPage:
    def __init__(self, *, login_url: str, target_url: str, ready_html: str) -> None:
        self.url = login_url
        self._target_url = target_url
        self._ready_html = ready_html
        self.required_texts: list[str] = []

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        self.url = url
        return None

    def reload(self, **_kwargs):
        return None

    def get_by_text(self, text: str):
        self.required_texts.append(text)
        return self

    def wait_for(self, **_kwargs):
        if self.url == self._target_url:
            return None
        raise FakeTimeout("locator timeout")

    def evaluate(self, _script):
        return []

    def content(self) -> str:
        if self.url == self._target_url:
            return self._ready_html
        return "<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>"


class _ReadyThenMissingDataPage:
    """readiness 가 먼저 로그인 만료로 1회 복구되지만, 복구 후에도 content 는 여전히 데이터
    누락+로그인으로 보이는 페이지(거짓 복구 시나리오).

    readiness 복구 경로(BrowserActionRequiredError)와 missing-data 검증 경로가 한 fetch 안에서
    둘 다 fire 하는 상황을 만든다 — 복구는 최대 1회여야 하고, 두 번째 누락은 재복구 없이
    BrowserActionRequiredError 로 포기해야 한다(더블 OTP 방지).
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self._recovered = False
        self.reopened = False
        self.required_texts: list[str] = []

    def mark_recovered(self) -> None:
        self._recovered = True

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def goto(self, _url, **_kwargs):
        self.reopened = True
        return None

    def reload(self, **_kwargs):
        self.reopened = True
        return None

    def get_by_text(self, text: str):
        self.required_texts.append(text)
        return self

    def wait_for(self, **_kwargs):
        # 복구+재오픈 전에는 readiness 가 로그인 만료로 실패(타임아웃)한다.
        if self._recovered and self.reopened:
            return None
        raise FakeTimeout("locator timeout")

    def evaluate(self, _script):
        return []

    def content(self) -> str:
        # 복구해도 데이터가 채워지지 않는다(거짓 복구) — 늘 누락+로그인으로 보인다.
        return _PEAK_MISSING_DATA_LOGIN_HTML


class _FakeTabLocator:
    def __init__(self, page: "_FakePage", label: str | None = None) -> None:
        self._page = page
        self._label = label

    def filter(self, *, has_text: str):
        return _FakeTabLocator(self._page, has_text)

    @property
    def first(self):
        return self

    def click(self, **_kwargs):
        if self._label is not None:
            self._page.clicked_tab_labels.append(self._label)


class _FakeBrowser:
    def __init__(self, pages: list[_FakePage], *, new_page_html: str = "") -> None:
        self.contexts = [_FakeContext(pages, new_page_html=new_page_html)]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, pages: list[_FakePage], *, new_page_html: str = "") -> None:
        self.pages = pages
        self._new_page_html = new_page_html
        self.new_page_calls = 0
        self.opened_pages: list[_FakePage] = []

    def new_page(self):
        self.new_page_calls += 1
        page = _FakePage("about:blank", html=self._new_page_html)
        self.pages.append(page)
        self.opened_pages.append(page)
        return page


# ── 여러 센터 통합('협력사 N개') 화면 처리 (실측: 안양서부 3센터 계정) ──────────────

# 여러 센터 계정이 로그인 직후 아직 어떤 센터도 고르지 않은 통합 화면. 상단에 센터
# 슬라이드 탭(.slide-tab)이 뜨고 제목은 "<회사명> 협력사 N개"이며, 대상 페이지 준비
# 텍스트("피크타임별 현황")가 없다.
_MULTI_CENTER_AGGREGATE_PEAK_HTML = """
<main>
  <div class="slide-tab">군포중앙</div>
  <div class="slide-tab">안양서부</div>
  <div class="slide-tab">안양중앙2</div>
  <div class="align-center dashboard-page-title-content flex">고고안양지사 협력사 3개</div>
  <p>대시보드</p>
  <p>협력사</p>
</main>
"""

_ANYANG_CENTER_TABS = [
    {"text": "군포중앙", "selected": False},
    {"text": "안양서부", "selected": False},
    {"text": "안양중앙2", "selected": False},
]


class _AggregatePeakThenReadyPage(_FakePage):
    """통합 화면: 준비 텍스트가 없다가, 설정 센터 탭 클릭 후에만 준비된다."""

    def __init__(self, url: str) -> None:
        super().__init__(
            url,
            html=_MULTI_CENTER_AGGREGATE_PEAK_HTML,
            center_tabs=list(_ANYANG_CENTER_TABS),
        )

    def wait_for(self, **_kwargs):
        if self.clicked_tab_labels:
            return None
        raise FakeTimeout("locator timeout")

    def content(self) -> str:
        if self.clicked_tab_labels:
            return _PEAK_DASHBOARD_HTML
        return self.html


class _LateAggregatePeakPage(_FakePage):
    """준비 대기 시작 시점엔 로딩 중이라 통합 화면 감지가 안 되고, 첫 대기 타임아웃
    후에야 통합 화면이 되는 페이지(로드 지연 케이스)."""

    def __init__(self, url: str) -> None:
        super().__init__(
            url,
            html="<html>loading</html>",
            center_tabs=list(_ANYANG_CENTER_TABS),
        )

    def wait_for(self, **_kwargs):
        if self.clicked_tab_labels:
            return None
        self.html = _MULTI_CENTER_AGGREGATE_PEAK_HTML
        raise FakeTimeout("locator timeout")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        # 실측 통합 화면: 센터 슬라이드 탭 + "협력사 N개" 제목.
        (_MULTI_CENTER_AGGREGATE_PEAK_HTML, True),
        # 센터가 선택된 정상 peak 화면은 통합 화면이 아니다.
        (_PEAK_DASHBOARD_HTML_WITH_TITLE_CENTER, False),
        # "협력사 N개" 텍스트만으로는 통합 화면이 아니다(슬라이드 탭 필요).
        ("<main><p>협력사 3개</p></main>", False),
        # 슬라이드 탭만으로는 통합 화면이 아니다("협력사 N개" 제목 필요).
        ('<main><div class="slide-tab">양주중앙</div></main>', False),
        ("", False),
    ],
)
def test_html_looks_like_coupang_multi_center_aggregate(html, expected):
    assert crawler._html_looks_like_coupang_multi_center_aggregate(html) is expected


def test_wait_for_target_page_ready_selects_center_on_multi_center_aggregate(tmp_path):
    # 통합 화면에는 준비 텍스트("피크타임별 현황")가 없다. 준비 대기 '전에' 통합 화면을
    # 감지해 설정 센터 탭을 눌러 전환해야 한다 — 탭 클릭이 준비 대기 뒤에만 있으면
    # 상한까지 헛대기 후 CRAWL_TIMEOUT 으로 고착된다(실측: 안양서부 3센터, snapshots 0).
    config = _config(tmp_path, baemin_center_name="안양서부")
    page = _AggregatePeakThenReadyPage(config.peak_dashboard_url)

    crawler._wait_for_target_page_ready(
        page, config, target_url=config.peak_dashboard_url, timeout_errors=(FakeTimeout,)
    )

    assert page.clicked_tab_labels == ["안양서부"]


def test_wait_for_target_page_ready_selects_center_when_aggregate_settles_during_wait(tmp_path):
    # 준비 대기 시작 시점엔 로딩 전이라 감지가 안 됐다가 대기 중 통합 화면으로 로드가
    # 끝난 경우 — 타임아웃 분기에서 한 번 더 감지해 센터 탭을 누르고 재대기해야 한다.
    config = _config(tmp_path, baemin_center_name="안양서부")
    page = _LateAggregatePeakPage(config.peak_dashboard_url)

    crawler._wait_for_target_page_ready(
        page, config, target_url=config.peak_dashboard_url, timeout_errors=(FakeTimeout,)
    )

    assert page.clicked_tab_labels == ["안양서부"]


def test_wait_for_target_page_ready_still_times_out_when_aggregate_has_no_matching_tab(tmp_path):
    # 통합 화면이라도 설정 센터와 일치하는 탭이 없으면(센터명 오설정 등) 탭을 누르지
    # 않고 기존 준비-타임아웃 오류를 그대로 낸다(fail-closed 무회귀).
    config = _config(tmp_path, baemin_center_name="여기없는센터")
    page = _AggregatePeakThenReadyPage(config.peak_dashboard_url)

    with pytest.raises(RuntimeError, match="준비되지 않았습니다"):
        crawler._wait_for_target_page_ready(
            page, config, target_url=config.peak_dashboard_url, timeout_errors=(FakeTimeout,)
        )

    assert page.clicked_tab_labels == []
