# Rider Crawl Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the first runnable version of the Coupang Eats rider performance text bot.

**Architecture:** A small Python package loads `.env` settings, opens the logged-in Coupang Eats page with Playwright, extracts visible page text with Scrapling-assisted parsing, renders the KakaoTalk text message, and optionally sends it through Windows KakaoTalk UI automation. Sending is disabled by config during local dry runs.

**Tech Stack:** Python 3.10+, Playwright 1.60.0, Scrapling 0.4.8, python-dotenv, pyperclip, pyautogui, pywinauto on Windows, pytest.

---

### Task 1: Package And Parser Tests

**Files:**
- Create: `pyproject.toml`
- Create: `tests/test_parser.py`
- Create: `tests/fixtures/current_screen.html`
- Create: `src/rider_crawl/models.py`
- Create: `src/rider_crawl/parser.py`

**Steps:**
1. Write tests for current screen parsing and numeric helpers.
2. Run tests and verify they fail because the package does not exist.
3. Implement the minimal data model and parser.
4. Run parser tests and verify they pass.

### Task 2: Message Rendering

**Files:**
- Create: `tests/test_message.py`
- Create: `src/rider_crawl/message.py`

**Steps:**
1. Write a failing test for the exact current-screen KakaoTalk text format.
2. Implement message rendering.
3. Run message tests.

### Task 3: Config, Lock, And Dry-Run App

**Files:**
- Create: `tests/test_lock.py`
- Create: `src/rider_crawl/config.py`
- Create: `src/rider_crawl/lock.py`
- Create: `src/rider_crawl/app.py`
- Create: `src/rider_crawl/__main__.py`

**Steps:**
1. Test stale lock and nested lock behavior.
2. Implement config loading and run lock.
3. Add dry-run application flow that prints the generated message when sending is disabled.

### Task 4: Playwright Crawler And Kakao Sender

**Files:**
- Create: `src/rider_crawl/crawler.py`
- Create: `src/rider_crawl/sender.py`
- Create: `src/rider_crawl/__init__.py`
- Create: `.env.example`
- Create: `README.md`

**Steps:**
1. Implement Playwright persistent-context crawling with debug HTML/screenshot output.
2. Implement KakaoTalk sender behind `SEND_ENABLED=true`.
3. Document setup requirements and dry-run commands.
4. Run the full test suite.
