# UI Runner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Windows-friendly UI app that stores settings in the app, starts/stops a 35-minute polling loop, and runs the existing crawler/sender flow without requiring Windows environment variables or Task Scheduler.

**Architecture:** Keep the existing crawler, parser, message renderer, and sender as the core. Add a small settings layer that persists UI values to JSON, a scheduler service that calls `run_once()` immediately and then every N minutes, and a Tkinter desktop UI that edits settings and controls the scheduler from a background thread.

**Tech Stack:** Python 3.10+, Tkinter, Playwright 1.60.0, Scrapling 0.4.8, pytest.

---

### Task 1: UI Settings Persistence

**Files:**
- Create: `tests/test_ui_settings.py`
- Create: `src/rider_crawl/ui_settings.py`
- Modify: `src/rider_crawl/config.py`

**Step 1:** Write tests for default UI settings, JSON save/load, and conversion to `AppConfig`.

**Step 2:** Run `uv run --python 3.10 --extra dev pytest tests/test_ui_settings.py -q` and confirm failure because the module does not exist.

**Step 3:** Implement `UiSettings` and `UiSettingsStore`.

**Step 4:** Run the test again and confirm pass.

### Task 2: Start/Stop Interval Runner

**Files:**
- Create: `tests/test_scheduler.py`
- Create: `src/rider_crawl/scheduler.py`

**Step 1:** Write tests for immediate first run, interval-based next run, and stop behavior using a fake clock/event.

**Step 2:** Run scheduler tests and confirm failure because the module does not exist.

**Step 3:** Implement `BotScheduler` with injectable clock and wait event for tests.

**Step 4:** Run scheduler tests and confirm pass.

### Task 3: Tkinter UI Shell

**Files:**
- Create: `src/rider_crawl/ui.py`
- Modify: `src/rider_crawl/__main__.py`
- Modify: `README.md`

**Step 1:** Add tests only for pure helpers where possible, not Tkinter widget rendering.

**Step 2:** Implement a Tkinter UI with settings fields, request checklist, message preview/status log, `1회 실행`, `시작`, `중지`, and `설정 저장`.

**Step 3:** Update `python -m rider_crawl` to launch UI by default, with `--once` for CLI dry runs.

**Step 4:** Run the full test suite.

### Task 4: Documentation And Spec Update

**Files:**
- Modify: `docs/rider-performance-bot-spec.md`
- Modify: `README.md`

**Step 1:** Update docs to describe UI-based setup, no Task Scheduler requirement, and the need to keep the logged-in page/browser session available.

**Step 2:** Run the full test suite again.
