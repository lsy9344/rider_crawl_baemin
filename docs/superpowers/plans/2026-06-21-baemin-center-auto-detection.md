# Baemin Center Auto Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Baemin crawling automatically handle one-center and multi-center accounts without failing when the configured center is missing or stale.

**Architecture:** Keep the existing Baemin crawler flow in `src/rider_crawl/crawler.py`, but separate center discovery from center selection. The crawler should detect available center options from `/center/change`, auto-select when there is exactly one available center, select the configured center when multiple options exist, and fail clearly only when multiple centers exist and no configured center matches.

**Tech Stack:** Python, Playwright CDP, pytest, existing `AppConfig` and `UiSettings`.

---

## Current Evidence

Observed against crawling7 on `http://127.0.0.1:9228`:

- Open tab: `https://deliverycenter.baemin.com/delivery/report`
- Main page redirects to history: `https://deliverycenter.baemin.com/delivery/history?...`
- Active center shown on page: `표준경기남양주C팀100퍼센트(DP2606167520)`
- `/center/change` has one selectable center option: `DP2606167520`
- Crawling7 settings currently contain stale default center: `표준서울마포B이츠앤홀딩스3 / DP2605181318`
- Existing `_open_baemin_delivery_history_page()` fails with `배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다`
- Separate issue: report iframe currently says `보고서에 액세스할 수 없음` for Google account `kimjimin62@gmail.com`, so `주간 배달 현황` is not readable even after using the real center. That is not a center-selection bug, but the final manual verification must account for it.

## File Structure

- Modify: `src/rider_crawl/crawler.py`
  - Add a small center option model.
  - Add helpers to read center options from the center-change page.
  - Update `_select_baemin_center()` to auto-select exactly one center.
  - Update `_open_baemin_delivery_history_page()` so it does not force a configured center when discovery proves there is only one center.
  - Keep strict mismatch protection when multiple centers exist.
- Modify: `tests/test_crawler.py`
  - Add fake async page/locator coverage for one-center auto-selection.
  - Add multi-center configured-match behavior.
  - Add multi-center missing/mismatched behavior.
  - Add no-select/plain-current-page behavior if `/center/change` redirects directly to report/history.

---

### Task 1: Add Center Option Discovery Tests

**Files:**
- Modify: `tests/test_crawler.py`

- [ ] **Step 1: Add a focused fake select page**

Add a fake class near existing fake page helpers in `tests/test_crawler.py`:

```python
class _FakeBaeminCenterSelectPage:
    def __init__(self, options, *, url=None):
        self.url = url or crawler._BAEMIN_CENTER_CHANGE_URL
        self.options = options
        self.selected_value = None
        self.clicked_buttons = []
        self.goto_urls = []

    async def goto(self, url, **_kwargs):
        self.url = url
        self.goto_urls.append(url)

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None

    def locator(self, selector):
        if selector == "select":
            return _FakeBaeminSelectLocator(self)
        raise AssertionError(f"unexpected selector: {selector}")

    def get_by_role(self, role, name=None, exact=None):
        assert role == "button"
        return _FakeBaeminButtonLocator(self, name)

    def get_by_text(self, text, exact=False):
        return _FakeMissingTextLocator()


class _FakeBaeminSelectLocator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def select_option(self, *, value=None, label=None, timeout=None):
        for option in self.page.options:
            if value is not None and option["value"] == value:
                self.page.selected_value = option["value"]
                return [option["value"]]
            if label is not None and option["label"] == label:
                self.page.selected_value = option["value"]
                return [option["value"]]
        raise ValueError("option not found")

    def locator(self, selector):
        assert selector == "option"
        return _FakeBaeminOptionLocator(self.page)


class _FakeBaeminOptionLocator:
    def __init__(self, page):
        self.page = page

    async def evaluate_all(self, _script):
        return [
            {"text": option["label"], "value": option["value"], "selected": option.get("selected", False)}
            for option in self.page.options
        ]


class _FakeBaeminButtonLocator:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    async def click(self, **_kwargs):
        self.page.clicked_buttons.append(self.name)


class _FakeMissingTextLocator:
    @property
    def first(self):
        return self

    async def count(self):
        return 0

    async def click(self, **_kwargs):
        raise AssertionError("text should not be clicked")
```

- [ ] **Step 2: Add the failing single-center test**

Add this test:

```python
def test_select_baemin_center_auto_selects_single_available_center(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="표준서울마포B이츠앤홀딩스3",
        baemin_center_id="DP2605181318",
    )
    page = _FakeBaeminCenterSelectPage(
        [
            {
                "label": "표준경기남양주C팀100퍼센트 (DP2606167520)",
                "value": "DP2606167520",
                "selected": True,
            }
        ]
    )

    asyncio.run(crawler._select_baemin_center(page, config))

    assert page.selected_value == "DP2606167520"
    assert page.clicked_buttons == ["선택 완료"]
```

- [ ] **Step 3: Add the failing multi-center configured-match test**

Add this test:

```python
def test_select_baemin_center_uses_configured_center_when_multiple_options(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="강남센터",
        baemin_center_id="DP123",
    )
    page = _FakeBaeminCenterSelectPage(
        [
            {"label": "송파센터 (DP999)", "value": "DP999", "selected": True},
            {"label": "강남센터 (DP123)", "value": "DP123", "selected": False},
        ]
    )

    asyncio.run(crawler._select_baemin_center(page, config))

    assert page.selected_value == "DP123"
    assert page.clicked_buttons == ["선택 완료"]
```

- [ ] **Step 4: Add the failing multi-center mismatch test**

Add this test:

```python
def test_select_baemin_center_rejects_missing_configured_center_when_multiple_options(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="마포센터",
        baemin_center_id="DP2605181318",
    )
    page = _FakeBaeminCenterSelectPage(
        [
            {"label": "남양주센터 (DP2606167520)", "value": "DP2606167520", "selected": True},
            {"label": "강남센터 (DP123)", "value": "DP123", "selected": False},
        ]
    )

    with pytest.raises(RuntimeError, match="목표 센터를 찾지 못했습니다"):
        asyncio.run(crawler._select_baemin_center(page, config))
```

- [ ] **Step 5: Run tests to verify failure**

Run:

```bash
pytest tests/test_crawler.py::test_select_baemin_center_auto_selects_single_available_center tests/test_crawler.py::test_select_baemin_center_uses_configured_center_when_multiple_options tests/test_crawler.py::test_select_baemin_center_rejects_missing_configured_center_when_multiple_options -q
```

Expected: at least the single-center test fails before implementation.

---

### Task 2: Implement Center Option Discovery

**Files:**
- Modify: `src/rider_crawl/crawler.py`

- [ ] **Step 1: Add a center option dataclass**

Add near `_BaeminCenterEvidence`:

```python
@dataclass(frozen=True)
class _BaeminCenterOption:
    label: str
    value: str
    selected: bool = False
```

- [ ] **Step 2: Add async option extraction**

Add before `_select_baemin_center()`:

```python
async def _baemin_center_options(page: Any) -> list[_BaeminCenterOption]:
    select = page.locator("select").first
    if not await select.count():
        return []
    raw_options = await select.locator("option").evaluate_all(
        """(options) => options.map((option) => ({
            label: (option.innerText || option.textContent || '').trim(),
            value: (option.value || '').trim(),
            selected: option.selected === true
        }))"""
    )
    options: list[_BaeminCenterOption] = []
    for raw in raw_options:
        label = _normalize_visible_text(str(raw.get("label", "")))
        value = str(raw.get("value", "")).strip()
        selected = bool(raw.get("selected", False))
        if not label and not value:
            continue
        if not value and not _extract_baemin_center_id(label):
            continue
        options.append(_BaeminCenterOption(label=label, value=value, selected=selected))
    return options
```

- [ ] **Step 3: Add matching helpers**

Add near `_baemin_center_labels()`:

```python
def _baemin_center_option_matches(option: _BaeminCenterOption, config: AppConfig) -> bool:
    expected_id = config.baemin_center_id.strip()
    expected_name = config.baemin_center_name.strip()
    option_id = option.value.strip() or _extract_baemin_center_id(option.label)
    if expected_id and option_id:
        return _normalize_center_id(option_id) == _normalize_center_id(expected_id)
    if expected_name and option.label:
        return _center_name_matches(option.label, expected_name, option_id)
    return False


def _baemin_center_option_label(option: _BaeminCenterOption) -> str:
    if option.label and option.value and option.value not in option.label:
        return f"{option.label} ({option.value})"
    return option.label or option.value
```

- [ ] **Step 4: Update `_select_baemin_center()`**

Replace the select branch with:

```python
async def _select_baemin_center(page: Any, config: AppConfig) -> None:
    target_labels = _baemin_center_labels(config)

    select = page.locator("select").first
    if await select.count():
        options = await _baemin_center_options(page)
        if len(options) == 1:
            option = options[0]
            if option.value:
                await select.select_option(value=option.value, timeout=config.page_timeout_seconds)
            else:
                await select.select_option(label=option.label, timeout=config.page_timeout_seconds)
            await page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            return

        for option in options:
            if _baemin_center_option_matches(option, config):
                if option.value:
                    await select.select_option(value=option.value, timeout=config.page_timeout_seconds)
                else:
                    await select.select_option(label=option.label, timeout=config.page_timeout_seconds)
                await page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return

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
            available = ", ".join(_baemin_center_option_label(option) for option in options) or "(없음)"
            raise RuntimeError(
                "배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다\n"
                f"설정 센터명: {config.baemin_center_name or '(비어 있음)'}\n"
                f"설정 센터 ID: {config.baemin_center_id or '(비어 있음)'}\n"
                f"사용 가능 센터: {available}"
            )
    else:
        await _click_first_visible_text(page, *target_labels)

    await page.get_by_role("button", name="선택 완료").click(timeout=config.page_timeout_seconds)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
pytest tests/test_crawler.py::test_select_baemin_center_auto_selects_single_available_center tests/test_crawler.py::test_select_baemin_center_uses_configured_center_when_multiple_options tests/test_crawler.py::test_select_baemin_center_rejects_missing_configured_center_when_multiple_options -q
```

Expected: all pass.

---

### Task 3: Avoid Selecting When Center Change Redirects Away

**Files:**
- Modify: `tests/test_crawler.py`
- Modify: `src/rider_crawl/crawler.py`

- [ ] **Step 1: Add redirect behavior test**

Add a fake page that changes URL after `goto(_BAEMIN_CENTER_CHANGE_URL)`:

```python
def test_open_baemin_delivery_history_page_skips_selection_when_center_change_redirects_to_report(tmp_path, monkeypatch):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="마포센터",
        baemin_center_id="DP2605181318",
    )
    page = _FakeAsyncNavigationPage(crawler._baemin_report_url(config))

    async def fake_goto_page(received_page, url, _config):
        received_page.goto_urls.append(url)
        if url == crawler._BAEMIN_CENTER_CHANGE_URL:
            received_page.url = crawler._baemin_report_url(config)
        else:
            received_page.url = url

    async def fail_select(*_args, **_kwargs):
        raise AssertionError("center selection should be skipped after redirect")

    monkeypatch.setattr(crawler, "_goto_page", fake_goto_page)
    monkeypatch.setattr(crawler, "_select_baemin_center", fail_select)

    opened_page = asyncio.run(crawler._open_baemin_delivery_history_page(_FakeBrowser([page]), config))

    assert opened_page is page
    assert crawler._BAEMIN_CENTER_CHANGE_URL in page.goto_urls
    assert page.goto_urls[-1] == crawler._baemin_report_url(config)
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
pytest tests/test_crawler.py::test_open_baemin_delivery_history_page_skips_selection_when_center_change_redirects_to_report -q
```

Expected before implementation: FAIL because `_select_baemin_center()` is called immediately after navigating to `/center/change`.

- [ ] **Step 3: Update `_open_baemin_delivery_history_page()`**

Change:

```python
if _has_configured_baemin_center(config):
    await _goto_page(page, _BAEMIN_CENTER_CHANGE_URL, config)
    await _select_baemin_center(page, config)
```

to:

```python
if _has_configured_baemin_center(config):
    await _goto_page(page, _BAEMIN_CENTER_CHANGE_URL, config)
    if _url_matches(str(page.url), _BAEMIN_CENTER_CHANGE_URL):
        await _select_baemin_center(page, config)
```

- [ ] **Step 4: Run redirect test**

Run:

```bash
pytest tests/test_crawler.py::test_open_baemin_delivery_history_page_skips_selection_when_center_change_redirects_to_report -q
```

Expected: PASS.

---

### Task 4: Preserve Center Verification Without Blocking Single-Center Auto Mode

**Files:**
- Modify: `src/rider_crawl/crawler.py`
- Modify: `tests/test_crawler.py`

- [ ] **Step 1: Add a regression test for active center evidence**

Add:

```python
def test_crawl_current_screen_accepts_auto_detected_single_center_even_when_config_stale(tmp_path):
    config = _config(
        tmp_path,
        browser_mode="cdp",
        baemin_center_name="표준서울마포B이츠앤홀딩스3",
        baemin_center_id="DP2605181318",
    )
    text = "\n".join(
        [
            "주간 배달 현황",
            "표준경기남양주C팀100퍼센트 - DP2606167520",
            "26-06-21",
            "일",
            "10/5 (100%)",
            "20/10 (100%)",
            "30/15 (100%)",
            "40/20 (100%)",
            "90.00%",
        ]
    )

    snapshot = crawl_current_screen(
        config,
        fetch_html=lambda _config: text,
        fetch_cancel_summary=lambda _config: None,
    )

    assert snapshot.center_name == "표준경기남양주C팀100퍼센트"
    assert snapshot.lunch_peak_count == 10
```

- [ ] **Step 2: Run and inspect behavior**

Run:

```bash
pytest tests/test_crawler.py::test_crawl_current_screen_accepts_auto_detected_single_center_even_when_config_stale -q
```

Expected: likely PASS already for report text because report parsing trusts the fetched report text. If it fails due strict `center_id` validation inside `parse_achievement_report_text`, update the parser call policy in Task 4 Step 3.

- [ ] **Step 3: If needed, add an explicit auto-detected center field**

If report parsing rejects stale configured center IDs, do not disable mismatch protection globally. Instead, add a local mechanism where `_select_baemin_center()` returns the selected `_BaeminCenterOption | None`, and `_open_baemin_delivery_history_page()` stores that in a local return wrapper.

Preferred simpler alternative if the parser already accepts text: no code change in this task.

- [ ] **Step 4: Keep existing mismatch tests**

Run:

```bash
pytest tests/test_crawler.py::test_crawl_current_screen_rejects_mismatched_selected_baemin_center_id tests/test_crawler.py::test_crawl_current_screen_rejects_wrong_center_shown_as_plain_text_span -q
```

Expected: both still PASS. Existing HTML/history validation must continue catching wrong selected centers when the user explicitly relies on configured center identity.

---

### Task 5: Manual CDP Verification Against Crawling7

**Files:**
- No code changes.

- [ ] **Step 1: Re-run center selection helper against 9228**

Use a one-off script that calls `_open_baemin_delivery_history_page()` with current crawling7 settings:

```bash
python -u - <<'PY'
import asyncio, json, sys
from pathlib import Path
from dataclasses import replace
sys.path.insert(0, "src")
from rider_crawl.ui_settings import UiSettings
from rider_crawl import crawler

async def main():
    from playwright.async_api import async_playwright
    data = json.loads(Path("runtime/state/ui_settings.json").read_text(encoding="utf-8"))
    settings = UiSettings(**data["crawlings"][6])
    config = replace(
        settings.to_app_config(crawl_name="크롤링7", state_subdir="crawling7"),
        page_timeout_seconds=12000,
    )
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(config.cdp_url)
        page = await crawler._open_baemin_delivery_history_page(browser, config)
        print(page.url)

asyncio.run(main())
PY
```

Expected after implementation: prints `https://deliverycenter.baemin.com/delivery/report`, not `배민 협력사 드롭다운에서 목표 센터를 찾지 못했습니다`.

- [ ] **Step 2: Verify current report accessibility separately**

Run a frame inspection against `https://deliverycenter.baemin.com/delivery/report`.

Expected in the current observed environment: the Data Studio frame still says `보고서에 액세스할 수 없음`. Treat this as a separate account/report permission issue, not a center auto-detection failure.

- [ ] **Step 3: Verify history page still parses**

Run the existing history parser path with `crawl_baemin_cancel_summary(config)`.

Expected: returns a snapshot or `None` without raising to the caller. If it returns `None`, inspect whether history table parsing changed; do not mix that fix into the center auto-detection patch unless it is directly caused by center selection.

---

### Task 6: Full Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run Baemin crawler tests**

Run:

```bash
pytest tests/test_crawler.py tests/test_baemin_parser.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run architecture tests touched by platform flow**

Run:

```bash
pytest tests/test_architecture.py tests/test_app.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Inspect git diff**

Run:

```bash
git diff -- src/rider_crawl/crawler.py tests/test_crawler.py docs/superpowers/plans/2026-06-21-baemin-center-auto-detection.md
```

Expected: diff only includes center detection/selection changes, tests, and this plan.

---

## Self-Review

- Spec coverage: The plan covers one center, two centers, multiple centers, stale configured center, redirect-away behavior, and strict mismatch behavior.
- Placeholder scan: No placeholder implementation steps remain.
- Type consistency: `_BaeminCenterOption`, `_baemin_center_options()`, and `_baemin_center_option_matches()` are defined before use.

