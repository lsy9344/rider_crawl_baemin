# Baemin/Coupang Platform Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a UI-selectable performance platform (`baemin` or `coupang`) and port the Coupang crawling, parsing, and message rendering logic from `C:\Code\rider_cr` while preserving the current Baemin behavior.

**Architecture:** Keep the current app orchestration, multi-tab UI, messenger registry, lock/hash scoping, and local-CDP safety. Add platform selection to settings/config, route `app.run_once()` through the existing platform registry, keep Baemin in the current legacy modules, and add Coupang as a separate platform package that ports the reference two-page crawl (`rider-performance` + `peak-dashboard`) and original Coupang message body.

**Tech Stack:** Python 3.10+, Tkinter, Playwright, optional Scrapling with existing HTMLParser fallback, pytest, existing Telegram/Kakao messenger adapters.

---

## 1. Scope And Non-Goals

### In Scope

- UI setting to choose `배민` or `쿠팡이츠` per crawling tab.
- Persistent settings support for the selected platform.
- Environment-variable support for CLI `--once`.
- Platform-aware validation:
  - Baemin tabs still require Baemin center identity.
  - Coupang tabs do not require Baemin center fields.
- Port Coupang reference crawling logic from `C:\Code\rider_cr`.
- Port Coupang reference parser logic:
  - `parse_current_screen_text`
  - `parse_peak_dashboard_text`
  - `parse_quantity_pair`
  - `_required_peak_period`
- Port Coupang reference model types:
  - `PeakPeriodSnapshot`
  - `PeakDashboardSnapshot`
  - `PerformanceSnapshot`
- Port Coupang reference message body:
  - `[실시간 실적봇]`
  - update time from peak dashboard
  - `아침`, `점심 피크`, `점심 논피크`, `저녁 피크`, `저녁 논피크`
  - `배정 N건 / 처리 N건`
  - `거절률`
  - `수행중인인원`
- Keep current optional tab label behavior where the project already passes `source_label=config.crawl_name`. This may add `[크롤링N]` above the Coupang body when running from the multi-tab UI.
- Update tests and docs so a future worker can verify without live Baemin/Coupang sessions.

### Out Of Scope

- Do not automate Baemin or Coupang login.
- Do not change Telegram or Kakao sending behavior except where platform selection requires small guards.
- Do not remove or broadly rename legacy `coupang_eats_url` fields in the first pass. The name is misleading, but changing it everywhere would create extra risk. Use a generic UI label and add comments/docs instead.
- Do not refactor Baemin crawler/parser into a new package in this pass.
- Do not touch generated build output under `dist/`, `build/`, or executable artifacts.

## 2. Current Project State

### Target Project

Workspace:

```text
C:\Users\KimYS\Desktop\개발외주\rider_result_mornitoring
```

Important files:

- `src/rider_crawl/app.py`
  - Orchestrates lock, crawl, message rendering, duplicate skip, and sending.
  - `_crawl_snapshot()` currently calls `rider_crawl.platforms.crawl_snapshot(config)` without passing platform name.
- `src/rider_crawl/config.py`
  - Runtime `AppConfig`.
  - Has legacy field `coupang_eats_url`, but current default value is the Baemin delivery-history URL.
  - Does not have `platform_name` or `peak_dashboard_url`.
- `src/rider_crawl/ui_settings.py`
  - Persistent `UiSettings`.
  - Already has `peak_dashboard_url`, but `to_app_config()` drops it because `AppConfig` does not accept it yet.
  - Does not have `platform_name`.
- `src/rider_crawl/ui.py`
  - Tkinter multi-tab UI.
  - Each tab stores URL, Baemin center fields, browser fields, messenger fields, and intervals.
  - Validation is currently Baemin-specific because active tabs require `baemin_center_name` or `baemin_center_id`.
- `src/rider_crawl/platforms/__init__.py`
  - Existing platform registry.
  - Default is `baemin`.
- `src/rider_crawl/platforms/base.py`
  - Protocol currently returns `CurrentScreenSnapshot`.
- `src/rider_crawl/platforms/baemin.py`
  - Thin adapter around the legacy Baemin crawler.
- `src/rider_crawl/crawler.py`
  - Current Baemin crawler.
  - Handles CDP/persistent fetch, center selection, refresh click, pagination, and center evidence validation.
- `src/rider_crawl/parser.py`
  - Current Baemin parser plus older text-parser fallback.
- `src/rider_crawl/message.py`
  - Current Baemin-style message renderer for `CurrentScreenSnapshot`.
- `src/rider_crawl/telegram_commands.py`
  - Baemin-only rider lookup command.
  - It directly fetches Baemin HTML and calls `parse_baemin_delivery_history_html`.

### Reference Coupang Project

Reference path:

```text
C:\Code\rider_cr
```

Important source files:

- `C:\Code\rider_cr\src\rider_crawl\crawler.py`
  - `crawl_performance_snapshot()` fetches two Coupang pages:
    - `config.coupang_eats_url`
    - `config.peak_dashboard_url`
  - Returns `PerformanceSnapshot`.
  - CDP path uses `playwright.chromium.connect_over_cdp(config.cdp_url)`.
  - Page readiness text:
    - performance page: `라이더 현황`
    - peak dashboard: `피크타임별 현황`
- `C:\Code\rider_cr\src\rider_crawl\parser.py`
  - Text extraction and Coupang parsing functions.
  - Optional `scrapling` usage is already safe because `_scrapling_text()` returns `""` if import fails.
- `C:\Code\rider_cr\src\rider_crawl\models.py`
  - Adds Coupang-specific `PeakPeriodSnapshot`, `PeakDashboardSnapshot`, `PerformanceSnapshot`.
- `C:\Code\rider_cr\src\rider_crawl\message.py`
  - `render_current_screen_message()` accepts `CurrentScreenSnapshot | PerformanceSnapshot`.
  - `PerformanceSnapshot` renders the original Coupang message body.
- `C:\Code\rider_cr\tests\test_parser.py`
  - Useful parser tests for current screen and peak dashboard.
- `C:\Code\rider_cr\tests\test_crawler.py`
  - Useful crawler tests for two-page snapshot and target page readiness.
- `C:\Code\rider_cr\tests\test_message.py`
  - Useful message tests for original Coupang output format.

## 3. Target Module Structure

Keep current Baemin files as-is for compatibility. Add Coupang under the platform boundary.

```text
src/rider_crawl/
  app.py                         # small routing/type updates only
  config.py                      # add platform_name and peak_dashboard_url
  models.py                      # add Coupang snapshot models and result alias
  message.py                     # add PerformanceSnapshot rendering branch
  ui.py                          # add platform selector and platform-aware validation
  ui_settings.py                 # persist platform_name and pass peak_dashboard_url
  browser_launcher.py            # platform-neutral login/ready text; still launches configured URL
  telegram_commands.py           # guard Baemin-only lookup command
  platforms/
    __init__.py                  # register baemin and coupang
    base.py                      # platform protocol returns CrawlSnapshotResult
    baemin.py                    # unchanged except type alias if needed
    coupang/
      __init__.py                # CoupangEatsPlatform adapter
      crawler.py                 # ported Coupang fetch/crawl logic
      parser.py                  # ported Coupang parser logic
```

Test structure:

```text
tests/
  fixtures/
    coupang_current_screen.html  # copy from reference tests/fixtures/current_screen.html
  test_coupang_parser.py         # new
  test_coupang_crawler.py        # new
  test_coupang_message.py        # new or extend test_message.py
  test_architecture.py           # add registry/type tests
  test_config.py                 # add platform/env tests
  test_ui_settings.py            # add persistence/migration tests
  test_ui_helpers.py             # add platform selector/validation tests
  test_browser_launcher.py       # update messages/URL expectations if needed
  test_telegram_commands.py      # add Baemin-only command guard test
```

This structure keeps platform-specific Coupang parsing and navigation out of the already-large Baemin `crawler.py` and `parser.py`.

## 4. Data Model Design

### Add Snapshot Result Types

Modify `src/rider_crawl/models.py`.

Keep the existing `CurrentScreenSnapshot` unchanged. Add the reference Coupang models below it:

```python
@dataclass(frozen=True)
class PeakPeriodSnapshot:
    done: float | int
    total: float | int


@dataclass(frozen=True)
class PeakDashboardSnapshot:
    updated_at: str
    assigned_count: float | int
    processed_count: float | int
    reject_rate: float | int
    morning: PeakPeriodSnapshot
    lunch_peak: PeakPeriodSnapshot
    lunch_non_peak: PeakPeriodSnapshot
    dinner_peak: PeakPeriodSnapshot
    dinner_non_peak: PeakPeriodSnapshot


@dataclass(frozen=True)
class PerformanceSnapshot:
    current_screen: CurrentScreenSnapshot
    peak_dashboard: PeakDashboardSnapshot


CrawlSnapshotResult = CurrentScreenSnapshot | PerformanceSnapshot
```

Use `CrawlSnapshotResult` anywhere a platform result may be either Baemin or Coupang.

### Why Keep `CurrentScreenSnapshot`

Baemin already maps its delivery-history table into `CurrentScreenSnapshot`, and many tests rely on that shape. Do not replace it. Coupang needs `PerformanceSnapshot` because the original code combines two pages.

## 5. Platform Registry Design

### `platforms/base.py`

Change the protocol return type:

```python
from rider_crawl.models import CrawlSnapshotResult


class PerformancePlatform(Protocol):
    name: str

    def crawl_snapshot(self, config: AppConfig) -> CrawlSnapshotResult:
        ...
```

### `platforms/__init__.py`

Register both platforms.

Expected behavior:

- Default remains `baemin`.
- `get_platform("baemin")` returns `BaeminDeliveryPlatform`.
- `get_platform("coupang")` returns `CoupangEatsPlatform`.
- `crawl_snapshot(config)` uses `config.platform_name` when the caller does not pass `platform_name`.
- Explicit `platform_name` still overrides config for tests.

Sketch:

```python
from .coupang import CoupangEatsPlatform

DEFAULT_PLATFORM_NAME = "baemin"

_PLATFORMS: dict[str, PerformancePlatform] = {
    "baemin": BaeminDeliveryPlatform(),
    "coupang": CoupangEatsPlatform(),
}


def crawl_snapshot(
    config: AppConfig,
    *,
    platform_name: str | None = None,
) -> CrawlSnapshotResult:
    selected_name = platform_name or getattr(config, "platform_name", DEFAULT_PLATFORM_NAME)
    return get_platform(selected_name).crawl_snapshot(config)
```

### `platforms/baemin.py`

Only update type hints if needed. Do not change Baemin crawl behavior.

### `platforms/coupang/__init__.py`

Create a thin adapter.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rider_crawl.config import AppConfig
from rider_crawl.models import PerformanceSnapshot

from .crawler import crawl_performance_snapshot


CrawlPerformance = Callable[[AppConfig], PerformanceSnapshot]


@dataclass(frozen=True)
class CoupangEatsPlatform:
    crawl: CrawlPerformance = crawl_performance_snapshot
    name: str = "coupang"

    def crawl_snapshot(self, config: AppConfig) -> PerformanceSnapshot:
        return self.crawl(config)
```

## 6. Coupang Crawler Design

Create `src/rider_crawl/platforms/coupang/crawler.py`.

Port these functions from `C:\Code\rider_cr\src\rider_crawl\crawler.py`:

- `crawl_current_screen`
- `crawl_performance_snapshot`
- `fetch_page_html`
- `fetch_page_html_via_cdp`
- `fetch_page_html_via_persistent_context`
- `_browser_pages`
- `_fetch_target_page_content`
- `_select_page_by_url`
- `_url_matches`
- `_normalize_path`
- `_wait_for_target_page_ready`

Adaptations required:

1. Import target project models and parser:

```python
from rider_crawl.config import AppConfig
from rider_crawl.models import CurrentScreenSnapshot, PerformanceSnapshot

from .parser import parse_current_screen_html, parse_peak_dashboard_html
```

2. Reuse target project's CDP safety check before connecting:

```python
from rider_crawl.browser_launcher import ensure_local_cdp_address
```

Call it in `fetch_page_html_via_cdp()` before Playwright connects.

3. Keep the reference project's CDP behavior of not closing the user's Chrome:

```python
# CDP 대상은 사용자가 켜 둔 Chrome이므로 여기서 browser.close()를 호출하지 않는다.
```

4. Use `config.peak_dashboard_url`; do not hardcode inside the crawler.

5. For persistent context, still launch the configured `target_url`.

6. Page readiness rules:

```python
if _url_matches(target_url, config.coupang_eats_url):
    label = "쿠팡이츠 실적 페이지"
    required_text = "라이더 현황"
elif _url_matches(target_url, config.peak_dashboard_url):
    label = "쿠팡이츠 피크 대시보드"
    required_text = "피크타임별 현황"
```

7. Keep the reference action message simple and operator-friendly:

```python
raise RuntimeError(
    f"{label}가 {seconds}초 안에 준비되지 않았습니다. "
    "Chrome에서 쿠팡이츠 로그인과 화면 로딩을 확인하세요."
) from exc
```

## 7. Coupang Parser Design

Create `src/rider_crawl/platforms/coupang/parser.py`.

Port from `C:\Code\rider_cr\src\rider_crawl\parser.py`:

- `MissingPerformanceDataError`
- `_VisibleTextParser`
- `html_to_text`
- `parse_current_screen_html`
- `parse_peak_dashboard_html`
- `parse_peak_dashboard_text`
- `parse_current_screen_text`
- `parse_count`
- `parse_pair`
- `parse_quantity_pair`
- `_scrapling_text`
- `_normalize_text`
- `_append_input_values`
- `_extract_date_label`
- `_required_number_after`
- `_required_peak_period`
- `_peak_time_section`

Import from the target central models:

```python
from rider_crawl.models import CurrentScreenSnapshot, PeakDashboardSnapshot, PeakPeriodSnapshot
```

### Important Parser Improvement

The reference parser has this region-specific regex:

```python
center_match = re.search(r"(?P<center>.+?)\s+의정부남부", normalized)
```

Do not rely on `의정부남부`. Use `heading_match` as the primary source. Keep `center_match` only as a fallback if needed, and make it generic:

```python
heading_match = re.search(
    r"(?P<center>.+?)\s+(?P<shift>[가-힣]+)\((?P<range>\d{2}:\d{2}~\d{2}:\d{2})\)\s+"
    r"(?P<status>.+?)\s+라이더 현황",
    normalized,
)
```

If `heading_match` is required anyway, use `heading_match.group("center")` and remove the old `center_match` dependency.

This is a small safety fix that keeps the original Coupang logic but avoids hardcoding one region.

### Optional Scrapling Dependency

Do not add `scrapling` to `pyproject.toml` just for this port. The reference parser already handles missing Scrapling:

```python
try:
    from scrapling.parser import Selector
except ImportError:
    return ""
```

The existing `HTMLParser` fallback is enough for tests and keeps dependency churn low.

## 8. Message Rendering Design

Modify `src/rider_crawl/message.py`.

Current signature:

```python
def render_current_screen_message(snapshot: CurrentScreenSnapshot, *, source_label: str = "") -> str:
```

Target signature:

```python
def render_current_screen_message(
    snapshot: CurrentScreenSnapshot | PerformanceSnapshot,
    *,
    source_label: str = "",
) -> str:
```

Branch:

```python
if isinstance(snapshot, PerformanceSnapshot):
    return _render_performance_message(snapshot, source_label=source_label)
return _render_baemin_current_screen_message(snapshot, source_label=source_label)
```

Keep the current Baemin body unchanged.

Add reference Coupang rendering:

```python
def _render_performance_message(snapshot: PerformanceSnapshot, *, source_label: str = "") -> str:
    dashboard = snapshot.peak_dashboard
    lines = [
        "[실시간 실적봇]",
    ]
    if source_label.strip():
        lines.append(f"[{source_label.strip()}]")
    lines.extend(
        [
            f"⏰ {dashboard.updated_at} 기준",
            "",
            f"아침 : {_format_period(dashboard.morning)}",
            f"점심 피크 : {_format_period(dashboard.lunch_peak)}",
            f"점심 논피크 : {_format_period(dashboard.lunch_non_peak)}",
            f"저녁 피크 : {_format_period(dashboard.dinner_peak)}",
            f"저녁 논피크 : {_format_period(dashboard.dinner_non_peak)}",
            "",
            f"배정 {_format_count(dashboard.assigned_count)}건 / 처리 {_format_count(dashboard.processed_count)}건",
            f"🚨거절률: {_format_count(dashboard.reject_rate)}%🚨",
            f"🌇수행중인인원 : {snapshot.current_screen.active_riders}명",
        ]
    )
    return "\n".join(lines)
```

Also port:

```python
def _format_period(period: PeakPeriodSnapshot) -> str:
    if period.done >= period.total:
        return "완료"
    return f"{_format_count(period.done)}건/{_format_count(period.total)}건"
```

Reason for keeping `source_label`: current project already labels multi-tab messages. The Coupang body below that label remains the original reference body.

## 9. Config Design

Modify `src/rider_crawl/config.py`.

Add default constants:

```python
DEFAULT_BAEMIN_DELIVERY_HISTORY_URL = (
    "https://deliverycenter.baemin.com/delivery/history?"
    "page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
)
DEFAULT_COUPANG_RIDER_PERFORMANCE_URL = "https://partner.coupangeats.com/page/rider-performance"
DEFAULT_COUPANG_PEAK_DASHBOARD_URL = "https://partner.coupangeats.com/page/peak-dashboard"
DEFAULT_PLATFORM_NAME = "baemin"
```

Add `platform_name` and `peak_dashboard_url` as defaulted fields after existing required fields:

```python
peak_dashboard_url: str = ""
platform_name: str = "baemin"
```

Keep `coupang_eats_url` for now. Treat it as the generic primary performance URL.

### Environment Variables

Support these names:

- `PERFORMANCE_PLATFORM`
  - `baemin`
  - `coupang`
- `PERFORMANCE_URL`
  - generic override for the primary page URL
- `BAEMIN_DELIVERY_HISTORY_URL`
  - legacy Baemin primary URL
- `COUPANG_EATS_URL`
  - legacy/reference Coupang primary URL
- `PEAK_DASHBOARD_URL`
  - Coupang peak dashboard URL

Suggested `from_env()` flow:

```python
platform_name = _platform_name(os.getenv("PERFORMANCE_PLATFORM", DEFAULT_PLATFORM_NAME))
primary_url = _primary_url_from_env(platform_name)
```

Behavior:

- If `PERFORMANCE_URL` is set, use it for all platforms.
- If `platform_name == "coupang"` and `COUPANG_EATS_URL` is set, use it.
- If `platform_name == "coupang"` and no override is set, use `DEFAULT_COUPANG_RIDER_PERFORMANCE_URL`.
- If `platform_name == "baemin"` and `BAEMIN_DELIVERY_HISTORY_URL` is set, use it.
- If `platform_name == "baemin"` and no override is set, use `DEFAULT_BAEMIN_DELIVERY_HISTORY_URL`.
- `COUPANG_EATS_URL` should remain a fallback for old tests only if Baemin URL env is absent; do not let it override an explicit Baemin URL.

Validation helper:

```python
def _platform_name(raw: str) -> str:
    value = str(raw or "").strip().casefold() or DEFAULT_PLATFORM_NAME
    if value not in {"baemin", "coupang"}:
        raise ValueError("PERFORMANCE_PLATFORM은 baemin 또는 coupang이어야 합니다")
    return value
```

## 10. UI Settings Design

Modify `src/rider_crawl/ui_settings.py`.

Add field:

```python
platform_name: str
```

Default:

```python
platform_name="baemin"
```

For default first tab:

- `platform_name = "baemin"`
- `performance_url = DEFAULT_BAEMIN_DELIVERY_HISTORY_URL`
- `peak_dashboard_url = ""`

For inactive additional tabs:

- keep current blank `performance_url`
- keep current blank center fields
- keep `platform_name = "baemin"` by default

Add migration helper:

```python
def _infer_platform_name(raw: dict[str, Any], default: str) -> str:
    explicit = str(raw.get("platform_name", "")).strip().casefold()
    if explicit:
        return explicit
    url = str(raw.get("performance_url", "")).casefold()
    peak_url = str(raw.get("peak_dashboard_url", "")).casefold()
    if "partner.coupangeats.com" in url or "partner.coupangeats.com" in peak_url:
        return "coupang"
    return default
```

After merging raw settings with defaults, validate:

```python
if data["platform_name"] not in {"baemin", "coupang"}:
    data["platform_name"] = defaults.platform_name
```

Pass to `AppConfig`:

```python
return AppConfig(
    coupang_eats_url=self.performance_url,
    peak_dashboard_url=self.peak_dashboard_url,
    platform_name=self.platform_name,
    ...
)
```

## 11. UI Design

Modify `src/rider_crawl/ui.py`.

Add:

```python
PLATFORM_OPTIONS = (("baemin", "배민"), ("coupang", "쿠팡이츠"))
```

Add `platform_name` to `_build_vars()`:

```python
"platform_name": StringVar(value=settings.platform_name),
```

Add platform coercion:

```python
platform_name = _platform_name(values.get("platform_name", "baemin"))
```

Add helper:

```python
def _platform_name(raw: Any) -> str:
    value = str(raw).strip().casefold() or "baemin"
    valid_names = {name for name, _label in PLATFORM_OPTIONS}
    if value not in valid_names:
        raise ValueError("플랫폼은 배민 또는 쿠팡이츠만 선택하세요")
    return value
```

### UI Placement

In `_build_settings_fields()`, add platform selector near the browser/messenger controls. Keep the form simple:

- Add label `플랫폼`.
- Add readonly combobox with `("baemin", "coupang")`, or radio buttons matching the messenger style.
- If using combobox values, display raw values only. If using radio buttons, show Korean labels. Radio buttons are clearer and match current messenger pattern.

Recommended radio buttons:

```python
ttk.Label(checks, text="플랫폼").grid(row=0, column=0, padx=(0, 8))
for offset, (value, label) in enumerate(PLATFORM_OPTIONS, start=1):
    ttk.Radiobutton(
        checks,
        text=label,
        value=value,
        variable=tab_vars["platform_name"],
    ).grid(row=0, column=offset, sticky="w", padx=(0, 18))
```

Then move the existing browser controls to the next row if needed. Keep layout readable rather than squeezing too many controls into one row.

### Labels

Change visible URL labels to platform-neutral text:

- `배달현황 URL` -> `실적/배달현황 URL`
- `보조 URL` -> `보조 URL(쿠팡 피크 대시보드)`

Keep Baemin center fields visible for now. Do not add dynamic hide/show unless the implementer wants to add a focused helper with tests. A simpler first pass is:

- The fields remain visible.
- Validation ignores them for Coupang.
- Start checklist explains that Baemin uses center fields and Coupang uses the 보조 URL.

### Checklist Text

Update checklist to mention both platforms:

- Baemin: login to deliverycenter Baemin delivery-history page.
- Coupang: login to Coupang Eats `rider-performance` and `peak-dashboard`.
- CDP/persistent login is manual.
- Messenger selection is independent from platform selection.

Do not rewrite the whole UI copy beyond this platform addition.

## 12. UI Validation Design

Modify `validate_active_tab_isolation()` and helpers.

Current active tab means `settings.performance_url.strip()` is non-empty. Keep that.

Validation should still run:

- Local CDP address check.
- Unique CDP port.
- Unique browser profile path.
- Messenger required fields.
- Unique Telegram target.
- Unique Kakao chat name.

Baemin-only validation:

- `_validate_active_baemin_center_identity()` should inspect only active settings where `platform_name == "baemin"`.
- Error text should say `배민 탭` when relevant.

Coupang validation:

- For active Coupang tabs, `performance_url` is already non-empty because that is how a tab becomes active.
- `peak_dashboard_url` should be required for active Coupang tabs.
- Error text: `크롤링N 쿠팡 피크 대시보드 URL을 입력하세요.`

Add helper:

```python
def _validate_active_coupang_urls(indexed_settings: list[tuple[int, UiSettings]]) -> None:
    for index, settings in indexed_settings:
        if settings.platform_name != "coupang":
            continue
        if not settings.peak_dashboard_url.strip():
            raise ValueError(f"크롤링{index + 1} 쿠팡 피크 대시보드 URL을 입력하세요.")
```

Call it from `validate_active_tab_isolation()` after Baemin center validation.

## 13. App Flow Design

Modify `src/rider_crawl/app.py`.

### Type Updates

Change imported model type:

```python
from .models import CrawlSnapshotResult
```

Change `run_once` injection type:

```python
crawl_snapshot: Callable[[AppConfig], CrawlSnapshotResult] | None = None
```

### Platform Routing

Change:

```python
return crawl_snapshot(config)
```

No direct change needed if `platforms.crawl_snapshot(config)` reads `config.platform_name`.

But make `_crawl_snapshot()` explicit:

```python
def _crawl_snapshot(config: AppConfig) -> CrawlSnapshotResult:
    from .platforms import crawl_snapshot

    return crawl_snapshot(config, platform_name=config.platform_name)
```

### Duplicate Message Scope

Update `_message_scope_key(config)` so Baemin and Coupang states never collide:

```python
parts = [
    config.platform_name.strip() or "baemin",
    config.coupang_eats_url.strip(),
    config.peak_dashboard_url.strip(),
    config.baemin_center_name.strip(),
    config.baemin_center_id.strip(),
]
```

Do not add platform to `_run_scope_key()`. The run lock should still be based on browser scope because one CDP Chrome session should not be driven by two runs at the same time.

## 14. Browser Launcher Design

Modify `src/rider_crawl/browser_launcher.py`.

The launcher already uses `config.coupang_eats_url` as the URL to open. With `UiSettings.to_app_config()` passing the selected platform's primary URL, this still works.

Update operator messages from Baemin-only to platform-neutral:

- `열린 Chrome 창에서 배민에 로그인하고 배달현황 페이지가 보이는 상태로 두세요.`
- Replace with:

```python
f"Chrome 실행 요청 완료. 열린 Chrome 창에서 {_platform_display_name(config)}에 로그인하고 "
"실적 페이지가 보이는 상태로 두세요."
```

Add:

```python
def _platform_display_name(config: AppConfig) -> str:
    if getattr(config, "platform_name", "baemin") == "coupang":
        return "쿠팡이츠"
    return "배민"
```

Update `_ensure_cdp_endpoint_unused()` text:

```text
여러 계정은 탭마다 다른 CDP 포트를 사용하고...
```

No need to change `build_mac_chrome_command()` or `build_windows_chrome_command()` beyond tests; they already open `config.coupang_eats_url`.

## 15. Telegram Command Design

`src/rider_crawl/telegram_commands.py` is Baemin-only because it parses Baemin delivery-history tables.

Add a clear guard so Coupang tabs do not try to parse Coupang pages as Baemin:

```python
if getattr(config, "platform_name", "baemin") != "baemin":
    return []
```

Better user feedback:

In `handle_text()`, after resolving `config`, if platform is not Baemin:

```python
if getattr(config, "platform_name", "baemin") != "baemin":
    self.send_text(
        config,
        "라이더 조회 명령은 배민 탭에서만 지원합니다.",
        message_thread_id=message_thread_id,
    )
    return True
```

This keeps the command predictable. It does not add new Coupang rider lookup behavior.

## 16. README And Architecture Docs

Update `README.md`:

- First paragraph: supports Baemin and Coupang Eats.
- Preparation:
  - Baemin login requirement.
  - Coupang Eats login requirement.
  - Coupang needs both 실적 URL and 피크 대시보드 URL.
- UI settings:
  - Add `플랫폼`.
  - Explain primary URL.
  - Explain `보조 URL(쿠팡 피크 대시보드)`.
  - Explain Baemin center fields apply only to Baemin.
- Multi-tab:
  - Each tab may be Baemin or Coupang.
  - Still use separate CDP ports and profiles per account/session.
- Collection method:
  - Baemin reads delivery-history table.
  - Coupang reads rider-performance and peak-dashboard pages.
- Telegram command:
  - Rider lookup command is Baemin-only.

Update `docs/module-architecture.md`:

- Platform boundary now returns `CrawlSnapshotResult`.
- `BaeminDeliveryPlatform` returns `CurrentScreenSnapshot`.
- `CoupangEatsPlatform` returns `PerformanceSnapshot`.
- Message renderer handles both result types.

## 17. Test-Driven Implementation Plan

Follow this order. Do not write production code for a task until its failing test exists and has been run.

### Task 1: Add Coupang Snapshot Models

**Files:**

- Modify: `src/rider_crawl/models.py`
- Modify: `tests/test_architecture.py`

- [ ] Add failing test:

```python
def test_coupang_snapshot_models_are_available():
    from rider_crawl.models import (
        CurrentScreenSnapshot,
        PeakDashboardSnapshot,
        PeakPeriodSnapshot,
        PerformanceSnapshot,
    )

    current = CurrentScreenSnapshot(
        center_name="센터",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        dinner_peak_count=0,
        non_peak_count=41.8,
        active_riders=7,
    )
    dashboard = PeakDashboardSnapshot(
        updated_at="20:38",
        assigned_count=103,
        processed_count=67,
        reject_rate=6.5,
        morning=PeakPeriodSnapshot(done=9, total=9),
        lunch_peak=PeakPeriodSnapshot(done=45, total=45),
        lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
        dinner_peak=PeakPeriodSnapshot(done=17, total=39),
        dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
    )

    snapshot = PerformanceSnapshot(current_screen=current, peak_dashboard=dashboard)

    assert snapshot.current_screen.active_riders == 7
    assert snapshot.peak_dashboard.dinner_non_peak.done == 2
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_architecture.py::test_coupang_snapshot_models_are_available -q
```

Expected before implementation: import failure for `PeakDashboardSnapshot`, `PeakPeriodSnapshot`, or `PerformanceSnapshot`.

- [ ] Implement models in `src/rider_crawl/models.py`.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 2: Add Coupang Parser

**Files:**

- Create: `src/rider_crawl/platforms/coupang/__init__.py`
- Create: `src/rider_crawl/platforms/coupang/parser.py`
- Create: `tests/test_coupang_parser.py`
- Copy fixture: `C:\Code\rider_cr\tests\fixtures\current_screen.html` -> `tests/fixtures/coupang_current_screen.html`

- [ ] Add parser tests by porting reference tests:

```python
from pathlib import Path

import pytest

from rider_crawl.platforms.coupang.parser import (
    MissingPerformanceDataError,
    parse_count,
    parse_current_screen_html,
    parse_peak_dashboard_text,
    parse_pair,
)


def test_parse_coupang_current_screen_html_extracts_summary_fields():
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")

    snapshot = parse_current_screen_html(html)

    assert snapshot.center_name == "제이앤에이치플러스 의정부남부"
    assert snapshot.date_label == "5월 21일(오늘)"
    assert snapshot.shift_label == "오후논피크"
    assert snapshot.shift_time_range == "13:00~16:55"
    assert snapshot.shift_status == "할당량 소진 중"
    assert snapshot.updated_at == "14:02"
    assert snapshot.available_current == 7
    assert snapshot.available_total == 25
    assert snapshot.waiting_count == 0
    assert snapshot.online_riders == 7
    assert snapshot.rejected_ignored_count == 2.4
    assert snapshot.cancelled_count == 0
    assert snapshot.completed_count == 102.4
    assert snapshot.sequence_violation_count == 0
    assert snapshot.lunch_peak_count == 60.6
    assert snapshot.dinner_peak_count == 0
    assert snapshot.non_peak_count == 41.8
    assert snapshot.active_riders == 7


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10건", 10),
        ("102.4건", 102.4),
        ("거절율: 6.2%", 6.2),
        ("1,203 건", 1203),
        ("-", 0),
    ],
)
def test_parse_coupang_count_normalizes_korean_count_text(raw, expected):
    assert parse_count(raw) == expected


def test_parse_coupang_pair_extracts_done_and_total_counts():
    assert parse_pair("10건/19건") == (10, 19)
    assert parse_pair("7 / 25 명") == (7, 25)


def test_parse_coupang_current_screen_html_raises_when_required_data_is_missing():
    with pytest.raises(MissingPerformanceDataError):
        parse_current_screen_html("<html><body>로그인이 필요합니다</body></html>")


def test_parse_coupang_peak_dashboard_text_extracts_format_metrics():
    snapshot = parse_peak_dashboard_text(
        "\n".join(
            [
                "제이앤에이치플러스 의정부남부",
                "저녁피크(16:55~20:00)",
                "19:27 업데이트",
                "실시간 오늘의 실적",
                "배정 물량",
                "309건",
                "처리 물량",
                "245.6건",
                "총 거절 수",
                "12.6건",
                "거절률",
                "4.6%",
                "피크타임별 현황",
                "아침",
                "344.4%",
                "잔여",
                "+22",
                "목표/완료",
                "9/31",
                "점심 피크",
                "134.7%",
                "잔여",
                "+15.6",
                "목표/완료",
                "45/60.6",
                "점심 논피크",
                "130.5%",
                "잔여",
                "+17.4",
                "목표/완료",
                "57/74.4",
                "저녁 피크",
                "66.3%",
                "잔여",
                "-40.4",
                "목표/완료",
                "120/79.6",
                "저녁 논피크",
                "0%",
                "잔여",
                "78",
                "목표/완료",
                "78/0",
                "시간대별 기록",
            ]
        )
    )

    assert snapshot.updated_at == "19:27"
    assert snapshot.assigned_count == 309
    assert snapshot.processed_count == 245.6
    assert snapshot.reject_rate == 4.6
    assert snapshot.morning.done == 31
    assert snapshot.morning.total == 9
    assert snapshot.lunch_peak.done == 60.6
    assert snapshot.lunch_peak.total == 45
    assert snapshot.lunch_non_peak.done == 74.4
    assert snapshot.lunch_non_peak.total == 57
    assert snapshot.dinner_peak.done == 79.6
    assert snapshot.dinner_peak.total == 120
    assert snapshot.dinner_non_peak.done == 0
    assert snapshot.dinner_non_peak.total == 78
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_coupang_parser.py -q
```

Expected before implementation: import failure for `rider_crawl.platforms.coupang.parser`.

- [ ] Port parser implementation.
- [ ] Run same command again.

Expected after implementation: all Coupang parser tests pass.

### Task 3: Add Coupang Crawler

**Files:**

- Create: `src/rider_crawl/platforms/coupang/crawler.py`
- Modify: `src/rider_crawl/platforms/coupang/__init__.py`
- Create: `tests/test_coupang_crawler.py`

- [ ] Add failing tests:

```python
from pathlib import Path

import pytest

from rider_crawl.config import AppConfig
from rider_crawl.platforms.coupang import crawler
from rider_crawl.platforms.coupang.crawler import crawl_current_screen, crawl_performance_snapshot


def test_coupang_crawl_current_screen_parses_html_from_injected_fetcher(tmp_path):
    html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    snapshot = crawl_current_screen(_config(tmp_path), fetch_html=lambda _config: html)

    assert snapshot.updated_at == "14:02"
    assert snapshot.completed_count == 102.4
    assert snapshot.active_riders == 7


def test_coupang_crawl_performance_snapshot_parses_performance_and_peak_dashboard(tmp_path):
    performance_html = Path("tests/fixtures/coupang_current_screen.html").read_text(encoding="utf-8")
    peak_dashboard_html = """
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

    snapshot = crawl_performance_snapshot(
        _config(tmp_path),
        fetch_performance_html=lambda _config: performance_html,
        fetch_peak_dashboard_html=lambda _config: peak_dashboard_html,
    )

    assert snapshot.current_screen.active_riders == 7
    assert snapshot.peak_dashboard.updated_at == "20:38"
    assert snapshot.peak_dashboard.assigned_count == 103
    assert snapshot.peak_dashboard.processed_count == 67
    assert snapshot.peak_dashboard.reject_rate == 6.5
    assert snapshot.peak_dashboard.dinner_non_peak.done == 2
    assert snapshot.peak_dashboard.dinner_non_peak.total == 27


def test_coupang_fetch_page_html_uses_cdp_mode_by_default(tmp_path, monkeypatch):
    config = _config(tmp_path, browser_mode="cdp")
    monkeypatch.setattr(crawler, "fetch_page_html_via_cdp", lambda _config, *, target_url=None: "cdp-html")
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


def test_coupang_fetch_target_page_content_does_not_close_cdp_browser(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser([_FakePage(config.coupang_eats_url, html="<html>ok</html>")])

    html = crawler._fetch_target_page_content(browser, config)

    assert html == "<html>ok</html>"
    assert browser.closed is False


def test_coupang_fetch_target_page_content_wraps_locator_timeout_with_actionable_message(tmp_path):
    config = _config(tmp_path)
    browser = _FakeBrowser([_FakePage(config.coupang_eats_url, wait_error=FakeTimeout("locator timeout"))])

    with pytest.raises(RuntimeError, match="쿠팡이츠 실적 페이지"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))


def _config(tmp_path, *, browser_mode: str = "cdp") -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        peak_dashboard_url="https://partner.coupangeats.com/page/peak-dashboard",
        platform_name="coupang",
        baemin_center_name="",
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
    )


class FakeTimeout(Exception):
    pass


class _FakePage:
    def __init__(self, url: str, html: str = "", wait_error: Exception | None = None) -> None:
        self.url = url
        self.html = html
        self.wait_error = wait_error

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def get_by_text(self, _text: str):
        return self

    def wait_for(self, **_kwargs):
        if self.wait_error:
            raise self.wait_error
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

    def new_page(self):
        page = _FakePage("about:blank")
        self.pages.append(page)
        return page
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_coupang_crawler.py -q
```

Expected before implementation: import failure for `rider_crawl.platforms.coupang.crawler`.

- [ ] Implement crawler.
- [ ] Run same command again.

Expected after implementation: all Coupang crawler tests pass.

### Task 4: Register Coupang Platform

**Files:**

- Modify: `src/rider_crawl/platforms/__init__.py`
- Modify: `src/rider_crawl/platforms/base.py`
- Modify: `src/rider_crawl/platforms/baemin.py`
- Modify: `tests/test_architecture.py`

- [ ] Add failing tests:

```python
def test_coupang_platform_registry_resolves_coupang_crawler():
    from rider_crawl.platforms import get_platform
    from rider_crawl.platforms.coupang import CoupangEatsPlatform

    platform = get_platform("coupang")

    assert isinstance(platform, CoupangEatsPlatform)


def test_crawl_snapshot_uses_configured_platform_name(tmp_path, monkeypatch):
    from rider_crawl.platforms import crawl_snapshot
    from rider_crawl.platforms.coupang import CoupangEatsPlatform

    config = _config(tmp_path, platform_name="coupang")
    snapshot = _performance_snapshot()
    monkeypatch.setattr(
        "rider_crawl.platforms.get_platform",
        lambda name: CoupangEatsPlatform(crawl=lambda received: snapshot),
    )

    assert crawl_snapshot(config) is snapshot
```

Add helpers in `tests/test_architecture.py`:

```python
def _config(tmp_path, *, platform_name: str = "baemin") -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://deliverycenter.baemin.com/delivery/history",
        peak_dashboard_url="",
        platform_name=platform_name,
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
```

Use a local `_performance_snapshot()` helper that builds `PerformanceSnapshot` with the models from Task 1.

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_architecture.py -q
```

Expected before implementation: Coupang platform lookup fails.

- [ ] Register platform and update protocol return type.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 5: Render Coupang Message

**Files:**

- Modify: `src/rider_crawl/message.py`
- Create: `tests/test_coupang_message.py` or extend `tests/test_message.py`

- [ ] Add failing tests ported from reference:

```python
from rider_crawl.message import render_current_screen_message
from rider_crawl.models import CurrentScreenSnapshot, PeakDashboardSnapshot, PeakPeriodSnapshot, PerformanceSnapshot


def test_render_coupang_performance_message_matches_original_format():
    snapshot = PerformanceSnapshot(
        current_screen=_current_screen(active_riders=3),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=18, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )

    assert render_current_screen_message(snapshot) == "\n".join(
        [
            "[실시간 실적봇]",
            "⏰ 20:38 기준",
            "",
            "아침 : 완료",
            "점심 피크 : 완료",
            "점심 논피크 : 10건/19건",
            "저녁 피크 : 17건/39건",
            "저녁 논피크 : 2건/27건",
            "",
            "배정 103건 / 처리 67건",
            "🚨거절률: 6.5%🚨",
            "🌇수행중인인원 : 3명",
        ]
    )


def test_render_coupang_performance_message_keeps_current_tab_label_when_present():
    snapshot = PerformanceSnapshot(
        current_screen=_current_screen(active_riders=4),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:54",
            assigned_count=103,
            processed_count=68,
            reject_rate=6.2,
            morning=PeakPeriodSnapshot(done=9, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=19, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=3, total=27),
        ),
    )

    message = render_current_screen_message(snapshot, source_label="크롤링2")

    assert message.splitlines()[0:2] == ["[실시간 실적봇]", "[크롤링2]"]
    assert "점심 논피크 : 완료" in message


def _current_screen(*, active_riders: int) -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name="제이앤에이치플러스 의정부남부",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        dinner_peak_count=0,
        non_peak_count=41.8,
        active_riders=active_riders,
    )
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_coupang_message.py tests/test_message.py -q
```

Expected before implementation: renderer does not accept `PerformanceSnapshot`.

- [ ] Implement message branch.
- [ ] Run same command again.

Expected after implementation: pass and existing Baemin message tests unchanged.

### Task 6: Add Config Platform Fields

**Files:**

- Modify: `src/rider_crawl/config.py`
- Modify: `tests/test_config.py`

- [ ] Add failing tests:

```python
def test_app_config_reads_coupang_environment_values(monkeypatch):
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.setenv("COUPANG_EATS_URL", "https://example.test/rider-performance")
    monkeypatch.setenv("PEAK_DASHBOARD_URL", "https://example.test/peak-dashboard")

    config = AppConfig.from_env()

    assert config.platform_name == "coupang"
    assert config.coupang_eats_url == "https://example.test/rider-performance"
    assert config.peak_dashboard_url == "https://example.test/peak-dashboard"


def test_app_config_defaults_to_baemin_platform(monkeypatch):
    for key in (
        "PERFORMANCE_PLATFORM",
        "PERFORMANCE_URL",
        "COUPANG_EATS_URL",
        "BAEMIN_DELIVERY_HISTORY_URL",
        "PEAK_DASHBOARD_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    config = AppConfig.from_env()

    assert config.platform_name == "baemin"
    assert "deliverycenter.baemin.com" in config.coupang_eats_url


def test_app_config_coupang_platform_uses_coupang_defaults(monkeypatch):
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.delenv("PERFORMANCE_URL", raising=False)
    monkeypatch.delenv("COUPANG_EATS_URL", raising=False)
    monkeypatch.delenv("PEAK_DASHBOARD_URL", raising=False)

    config = AppConfig.from_env()

    assert config.coupang_eats_url == "https://partner.coupangeats.com/page/rider-performance"
    assert config.peak_dashboard_url == "https://partner.coupangeats.com/page/peak-dashboard"
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected before implementation: `AppConfig` has no platform fields.

- [ ] Implement config changes.
- [ ] Update existing test helper constructors across tests to either pass no new fields or pass explicit `platform_name`/`peak_dashboard_url` only where needed.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 7: Add UI Settings Persistence

**Files:**

- Modify: `src/rider_crawl/ui_settings.py`
- Modify: `tests/test_ui_settings.py`

- [ ] Add failing tests:

```python
def test_ui_settings_defaults_to_baemin_platform():
    settings = UiSettings.defaults()

    assert settings.platform_name == "baemin"
    assert settings.peak_dashboard_url == ""


def test_ui_settings_save_and_load_round_trip_keeps_platform(tmp_path):
    store = UiSettingsStore(tmp_path / "settings.json")
    settings = UiSettings.defaults()
    settings.platform_name = "coupang"
    settings.performance_url = "https://partner.coupangeats.com/page/rider-performance"
    settings.peak_dashboard_url = "https://partner.coupangeats.com/page/peak-dashboard"

    store.save(settings)
    loaded = store.load()

    assert loaded.platform_name == "coupang"
    assert loaded.performance_url == "https://partner.coupangeats.com/page/rider-performance"
    assert loaded.peak_dashboard_url == "https://partner.coupangeats.com/page/peak-dashboard"


def test_ui_settings_load_infers_coupang_from_legacy_coupang_url(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "performance_url": "https://partner.coupangeats.com/page/rider-performance",
          "peak_dashboard_url": "https://partner.coupangeats.com/page/peak-dashboard"
        }
        """,
        encoding="utf-8",
    )

    loaded = UiSettingsStore(path).load()

    assert loaded.platform_name == "coupang"
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py -q
```

Expected before implementation: `UiSettings` has no platform field.

- [ ] Implement settings changes.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 8: Add UI Platform Selection And Validation

**Files:**

- Modify: `src/rider_crawl/ui.py`
- Modify: `tests/test_ui_helpers.py`

- [ ] Add failing tests:

```python
def test_platform_options_expose_baemin_and_coupang_for_ui():
    assert ui.PLATFORM_OPTIONS == (("baemin", "배민"), ("coupang", "쿠팡이츠"))


def test_coerce_settings_builds_coupang_ui_settings_from_form_values(tmp_path):
    settings = coerce_settings(
        {
            "platform_name": "coupang",
            "performance_url": " https://partner.coupangeats.com/page/rider-performance ",
            "peak_dashboard_url": " https://partner.coupangeats.com/page/peak-dashboard ",
            "baemin_center_name": "",
            "baemin_center_id": "",
            "browser_mode": "cdp",
            "cdp_url": " http://127.0.0.1:9222 ",
            "browser_user_data_dir": str(tmp_path / "browser"),
            "log_dir": str(tmp_path / "logs"),
            "kakao_chat_name": "",
            "telegram_bot_token": " token ",
            "telegram_chat_id": " -100123 ",
            "telegram_message_thread_id": "",
            "messenger_name": "telegram",
            "interval_minutes": "35",
            "page_timeout_seconds": "60000",
            "run_lock_timeout_seconds": "900",
            "headless": False,
            "send_enabled": True,
            "send_only_on_change": False,
        }
    )

    assert settings.platform_name == "coupang"
    assert settings.baemin_center_name == ""
    assert settings.baemin_center_id == ""


def test_validate_active_tab_isolation_allows_coupang_without_baemin_center(tmp_path):
    settings = _settings(
        tmp_path,
        platform_name="coupang",
        performance_url="https://partner.coupangeats.com/page/rider-performance",
        peak_dashboard_url="https://partner.coupangeats.com/page/peak-dashboard",
    )
    settings.baemin_center_name = ""
    settings.baemin_center_id = ""

    validate_active_tab_isolation([settings])


def test_validate_active_tab_isolation_rejects_coupang_without_peak_dashboard_url(tmp_path):
    settings = _settings(
        tmp_path,
        platform_name="coupang",
        performance_url="https://partner.coupangeats.com/page/rider-performance",
        peak_dashboard_url="",
    )

    with pytest.raises(ValueError, match="쿠팡 피크 대시보드 URL"):
        validate_active_tab_isolation([settings])
```

Update `_settings()` helper in the same test file to accept `platform_name` and `peak_dashboard_url`.

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_helpers.py -q
```

Expected before implementation: missing `PLATFORM_OPTIONS` or validation failure.

- [ ] Implement UI changes.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 9: Route App Through Configured Platform

**Files:**

- Modify: `src/rider_crawl/app.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_architecture.py`

- [ ] Add failing tests:

```python
def test_app_default_crawl_uses_configured_platform_name(tmp_path, monkeypatch):
    import rider_crawl.app as app

    config = _config(tmp_path, platform_name="coupang")
    snapshot = _performance_snapshot()
    calls: list[str] = []

    monkeypatch.setattr(
        "rider_crawl.platforms.crawl_snapshot",
        lambda received, *, platform_name=None: calls.append(platform_name) or snapshot,
    )

    assert app._crawl_snapshot(config) is snapshot
    assert calls == ["coupang"]


def test_message_scope_key_includes_platform_and_peak_dashboard_url(tmp_path):
    from dataclasses import replace
    import rider_crawl.app as app

    baemin = _config(tmp_path, platform_name="baemin")
    coupang = replace(
        baemin,
        platform_name="coupang",
        peak_dashboard_url="https://partner.coupangeats.com/page/peak-dashboard",
    )

    assert app._message_scope_key(baemin) != app._message_scope_key(coupang)
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_architecture.py -q
```

Expected before implementation: app does not pass configured platform.

- [ ] Implement app routing and scope changes.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 10: Make Browser Launcher Messages Platform-Aware

**Files:**

- Modify: `src/rider_crawl/browser_launcher.py`
- Modify: `tests/test_browser_launcher.py`

- [ ] Add failing test:

```python
def test_prepare_chrome_message_names_coupang_platform(tmp_path):
    calls = []
    probes = []
    config = AppConfig(
        **{
            **_config(tmp_path).__dict__,
            "platform_name": "coupang",
            "coupang_eats_url": "https://partner.coupangeats.com/page/rider-performance",
            "peak_dashboard_url": "https://partner.coupangeats.com/page/peak-dashboard",
        }
    )

    def probe(cdp_url):
        probes.append(cdp_url)
        if not calls:
            raise OSError("not ready")

    message = prepare_chrome(
        config,
        platform_name="Windows",
        run_command=lambda command, check: calls.append((command, check)),
        cdp_probe=probe,
    )

    assert "쿠팡이츠" in message
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_browser_launcher.py -q
```

Expected before implementation: message still mentions only Baemin.

- [ ] Implement platform-aware message text.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 11: Guard Telegram Rider Lookup For Baemin Only

**Files:**

- Modify: `src/rider_crawl/telegram_commands.py`
- Modify: `tests/test_telegram_commands.py`

- [ ] Add failing test:

```python
def test_telegram_command_processor_replies_that_lookup_is_baemin_only_for_coupang(tmp_path):
    sent: list[str] = []
    config = AppConfig(
        **{
            **_config(tmp_path, crawl_name="크롤링1", chat_id="-100123").__dict__,
            "platform_name": "coupang",
            "coupang_eats_url": "https://partner.coupangeats.com/page/rider-performance",
            "peak_dashboard_url": "https://partner.coupangeats.com/page/peak-dashboard",
        }
    )
    processor = TelegramCommandProcessor(
        [config],
        fetch_html=lambda _config: (_ for _ in ()).throw(AssertionError("must not fetch Coupang as Baemin")),
        send_text=lambda _config, message, **_kwargs: sent.append(message),
    )

    handled = processor.handle_text("-100123", "!홍길동1234")

    assert handled is True
    assert sent == ["라이더 조회 명령은 배민 탭에서만 지원합니다."]
```

- [ ] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telegram_commands.py -q
```

Expected before implementation: it tries to fetch/parse Coupang as Baemin.

- [ ] Implement guard.
- [ ] Run same command again.

Expected after implementation: pass.

### Task 12: Update README And Module Architecture Docs

**Files:**

- Modify: `README.md`
- Modify: `docs/module-architecture.md`

- [ ] Update docs as described in section 16.
- [ ] Run a doc sanity search:

```powershell
rg "배민 배달현황 실적봇|배민 `배달현황` 페이지만|returns `CurrentScreenSnapshot`" README.md docs/module-architecture.md
```

Expected: no stale statement says the whole project supports only Baemin.

### Task 13: Full Regression

**Files:**

- No expected production changes.

- [ ] Run focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_coupang_parser.py tests/test_coupang_crawler.py tests/test_coupang_message.py tests/test_architecture.py tests/test_config.py tests/test_ui_settings.py tests/test_ui_helpers.py tests/test_app.py tests/test_browser_launcher.py tests/test_telegram_commands.py -q
```

Expected: all pass.

- [ ] Run full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expected: all pass.

- [ ] Optional live manual check on an operator machine:
  - Start UI.
  - Set one tab to `쿠팡이츠`.
  - Fill primary URL `https://partner.coupangeats.com/page/rider-performance`.
  - Fill 보조 URL `https://partner.coupangeats.com/page/peak-dashboard`.
  - Click Chrome prepare.
  - Log in manually.
  - Run once with sending disabled.
  - Verify preview contains original Coupang message body.

## 18. Risk Notes For The Implementing Agent

- Do not close user Chrome after connecting through CDP for Coupang. The reference project intentionally leaves it open.
- Do not make Baemin center validation apply to Coupang tabs.
  - **Amendment (2026-06-10):** superseded for the *center name* only. Coupang
    tabs now require an **expected center/shop name** (reusing the `배민 센터명`
    field) so the crawler can exact-match it against the on-screen center and
    refuse to send another account's data. The Baemin center **ID** still does not
    apply to Coupang. See the Acceptance Criteria amendment for the rationale.
- Do not make `peak_dashboard_url` required for Baemin tabs.
- Do not remove legacy `COUPANG_EATS_URL`; tests and older `.env` files may still use it.
- Do not add `scrapling` unless tests prove the fallback parser cannot handle the fixture. The reference code already works without it.
- Do not change Kakao/Telegram send behavior except the Baemin-only Telegram command guard.
- Keep default platform `baemin` so existing users do not suddenly open Coupang pages.
- Keep existing `performance_url` settings active/inactive behavior: an empty primary URL means the tab is inactive.
- If old saved settings include a Coupang URL but no platform field, migrate to `coupang` to avoid breaking old Coupang-derived setups.

## 19. Acceptance Criteria

The implementation is complete when:

- UI exposes `배민` and `쿠팡이츠` platform selection per tab.
- Existing Baemin tests still pass without expected-output changes except where constructor defaults need new fields.
- A Coupang tab can run without Baemin center name or center ID.
  - **Amendment (2026-06-10, intentional requirement change):** a Coupang tab no
    longer runs without a center value. The `배민 센터명` field is reused as the
    **expected Coupang center/shop name** and is now **required** for active
    Coupang tabs (UI save and `--once` CLI both reject an empty value or the
    Baemin default). Baemin center **ID** remains unused for Coupang. This was
    added because a Coupang account is determined only by the CDP port and the
    logged-in Chrome profile; if those are mis-wired, the crawler could send
    another Coupang account's performance as if it were correct. The crawler
    skips center validation when the expected name is empty, so requiring it at
    save/run time guarantees the exact-match check always runs. See
    `config._require_coupang_center`, `ui._validate_coupang_expected_center`, and
    `crawler._validate_coupang_center`. The original acceptance line above is kept
    for history; the binding behavior is this amendment.
- A Coupang tab requires `peak_dashboard_url`.
- `platforms.get_platform("coupang")` resolves to `CoupangEatsPlatform`.
- `app._crawl_snapshot()` passes `config.platform_name` to the platform registry.
- Coupang parser tests ported from the reference project pass.
- Coupang crawler tests ported from the reference project pass.
- Coupang message rendering matches the original reference body.
- Duplicate message scope includes platform and peak dashboard URL.
- Telegram rider lookup command replies that lookup is Baemin-only when used against a Coupang tab.
- `README.md` and `docs/module-architecture.md` describe both platforms.
- Full `pytest` suite passes.
