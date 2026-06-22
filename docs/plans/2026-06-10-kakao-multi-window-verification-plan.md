# KakaoTalk Multi-Window Verification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify and harden KakaoTalk sending so each `크롤링1` to `크롤링9` tab sends to the correct already-open KakaoTalk chatroom window.

**Architecture:** Keep the current tab-based crawler and messenger registry. Make KakaoTalk window selection strict: each active Kakao tab must have one configured chatroom name, the sender must find exactly one matching open chatroom window, and it must never fall back to an arbitrary KakaoTalk message-input window when multiple rooms may be open.

**Tech Stack:** Python 3.10+, Tkinter, pywinauto on Windows, pyautogui, pyperclip, pytest.

---

## Review Summary

### Assumptions

- Telegram behavior for `크롤링1` to `크롤링9` is treated as already working.
- This plan only covers KakaoTalk behavior.
- The likely last known good KakaoTalk commit is `63a260b0f04769d06f2483a210d077bcb8d7e335` from 2026-06-03.
- A useful secondary checkpoint is `40224156fa94a37529cfc1867530207e9e1adc65` from 2026-06-09, where the new messenger wrapper existed but KakaoTalk was still the default.
- Current `HEAD` is `f6e3d8767c35b8ab1b2a2730b92e7f667d2431ea`.

### Original Behavior (Before Implementation)

> **Status: superseded.** This section describes the code as it was when this plan
> was written, before the tasks below were implemented. For the behavior after
> implementation, see [Implemented Behavior](#implemented-behavior).

- `src/rider_crawl/ui.py` stores one setting set per tab and passes `crawl_name="크롤링N"` plus `state_subdir="crawlingN"` into `run_once`.
- `src/rider_crawl/messengers/__init__.py` dispatches by `config.messenger_name`.
- `src/rider_crawl/messengers/kakao.py` delegates to `src/rider_crawl/sender.py::send_kakao_text`.
- `send_kakao_text` requires `kakao_chat_name`, Windows, `pyautogui`, and `pyperclip`.
- Kakao sending is serialized by one UI-level `kakao_send_lock`, so several tabs can crawl in parallel but Kakao sends happen one by one.
- The Kakao window lookup order was:
  1. find a window whose title equals or contains `kakao_chat_name`;
  2. reuse the last successful Kakao window handle (a single global handle) if its title still matches;
  3. search from the Kakao main window;
  4. fall back to any KakaoTalk window with a message input.

### Implemented Behavior

> **Status: current.** This reflects the code after the tasks below were
> implemented.

- The Kakao window lookup now uses strict open-window selection first:
  1. scan open KakaoTalk chat windows across both `uia` and `win32` backends, deduplicated by handle;
  2. require exactly one window whose **normalized title is an exact match** for `kakao_chat_name` (no `chat_name in title` substring match);
  3. if exactly one matches, focus it and send;
  4. if the chat-window list cannot be scanned at all (e.g. `pywinauto` missing, both backends fail) or two or more windows share the exact title, raise `KakaoUnsafeSelectionError` immediately — no main-window search, no arbitrary message-input fallback;
  5. only when zero windows match does it fall back to the Kakao main-window search, after which it re-runs strict selection on the opened window.
- After a window is strictly selected, a missing message-input control fails the send. It never switches to another KakaoTalk window (`_focus_kakao_message_window()` is not called on the strict path).
- The remembered window handle is keyed per chat name (`_LAST_KAKAO_CHAT_HANDLE_BY_CHAT`), so a handle is never reused across different chat names.
- UI validation rejects send-enabled active Kakao tabs with an empty `kakao_chat_name` and rejects duplicate normalized chat names across send-enabled active Kakao tabs.
- Legacy settings with `kakao_chat_name` but no `messenger_name` and no Telegram credentials load as `kakao` regardless of `send_enabled`; ambiguous settings still default to `telegram`, and new tabs still default to `telegram`.

### Comparison With 2026-06-03

- The core KakaoTalk sender body is mostly preserved.
- The current code is safer than `63a260b` in one place: remembered window reuse now checks the chat name before focusing it.
- The main behavior change is routing. On 2026-06-03, `run_once()` always called KakaoTalk sending. Current code defaults to Telegram unless `messenger_name` is set to `kakao`.
- Legacy settings that do not contain `messenger_name` may now load as Telegram. That can make an old Kakao setup appear broken until the UI tab is changed to `카카오톡`.

## Feasibility

This is feasible if each active Kakao tab has a distinct chatroom name and that chatroom is already open as a separate KakaoTalk window. The current code already has most of the needed pieces: per-tab settings, per-tab `crawl_name`, Kakao serialization, diagnostics, and UI automation.

The unsafe part is not the ability to send. The unsafe part is selection. If a title match fails, the current fallback can choose the foreground or first KakaoTalk message-input window. With multiple open rooms, that can send to the wrong room. The plan should first make wrong-room sending harder than failing.

If the intended rule is "do not type a Kakao room name; infer the room only from `크롤링N`", then a new explicit mapping rule is required. Without either a configured room name or a strict naming convention such as a chatroom title that contains `크롤링N`, the program has no reliable way to know which open room belongs to which tab.

## Risks To Fix Before Real Kakao Verification

> These were the risks identified before implementation. Each line notes how it
> was resolved.

- **Wrong room on fallback:** ~~`_focus_kakao_message_window()` can select an unrelated KakaoTalk chat window.~~ Resolved — the strict path never calls `_focus_kakao_message_window()`.
- **Input fallback after strict selection:** ~~even if strict selection finds the right room, `send_kakao_text()` can still fall back to an arbitrary message-input window when the selected room's input control is not found.~~ Resolved — a missing input control on a strictly selected window fails the send.
- **Partial title collision:** ~~`chat_name in title` can match similar rooms, for example `실적봇_A` and `실적봇_A_테스트`.~~ Resolved — selection requires an exact normalized-title match.
- **Kakao send enabled without room name:** ~~UI validation checks Telegram credentials but does not reject Kakao tabs with empty `kakao_chat_name` until send time.~~ Resolved — `validate_active_tab_isolation` rejects send-enabled active Kakao tabs without a chat name.
- **Legacy default changed:** ~~older Kakao settings may silently become Telegram because `messenger_name` now defaults to `telegram`.~~ Resolved — legacy Kakao-only settings migrate to `kakao` on load.
- **Global remembered handle:** ~~the remembered Kakao handle is global.~~ Resolved — the remembered handle is keyed per chat name.
- **Main-window search ambiguity:** KakaoTalk search can open the wrong room if search results are duplicated or ordered unexpectedly. Mitigated — the main-window search is now a fallback reached only when zero windows match, and its result is rechecked by strict selection (which rejects ambiguous or duplicate matches).
- **Verification gap:** ~~tests cover basic Kakao errors and lock behavior, but not "select the correct one among several open Kakao chat windows," duplicate backend handles, or duplicate exact titles.~~ Resolved — `tests/test_sender.py` now covers strict selection, dedup by handle, duplicate exact titles, missing input control, unscannable desktop, and the `send_kakao_text` paths.

## Recommended Direction

Use a strict open-window selection mode for KakaoTalk verification.

1. Keep the existing per-tab `kakao_chat_name` field as the source of truth.
2. Scan currently open KakaoTalk chatroom windows and require exactly one safe match.
3. Prefer exact normalized title matches.
4. Treat multiple matches as an error, not a best guess.
5. Do not use "any Kakao message-input window" fallback in the multi-window path.
6. Keep the old main-window search only as an explicit compatibility fallback after strict selection fails, and only if the opened/focused window title can be rechecked.
7. After strict selection succeeds, do not recover from a missing input control by switching to another KakaoTalk window. Fail with diagnostics instead.

This keeps the working KakaoTalk concept from the old program, while adding enough guardrails for multiple tabs.

## Implementation Plan

### Task 1: Add Strict Kakao Window Selection Tests

**Files:**
- Modify: `tests/test_sender.py`
- Modify: `src/rider_crawl/sender.py`

- [x] Add fake Kakao window objects with title, class name, handle, visibility, and message-input descendants.
- [x] Add a test that exact title match selects the correct room from several open rooms.
- [x] Add a test that similar titles do not collide: an exact-title request selects only the exact room, and a partial-only request raises `KakaoSendError`.
- [x] Add a test that no title match does not fall back to an arbitrary message-input window in strict mode.
- [x] Add a test that a strict title match with no message-input descendant raises `KakaoSendError` and does not call `_focus_kakao_message_window()`.
- [x] Add a test that the same window returned by both `uia` and `win32` backends is deduplicated by handle and does not count as multiple matches.
- [x] Add a test that two different open KakaoTalk windows with the same exact normalized title raise `KakaoUnsafeSelectionError`.
- [x] Add a test that remembered handles are keyed by chat name, or at least never reused across different chat names.
- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sender.py
```

Expected: new tests fail before implementation. (Confirmed: 6 failed before implementing Task 2.)

### Task 2: Implement Strict Open-Window Finder

**Files:**
- Modify: `src/rider_crawl/sender.py`

- [x] Add a small helper to normalize KakaoTalk window titles for matching (`_normalize_kakao_title` trims whitespace; no extra suffix stripping was needed).
- [x] Add a helper that lists KakaoTalk windows from both `uia` and `win32` backends and deduplicates by handle (`_list_kakao_windows`). It raises `KakaoUnsafeSelectionError` when neither backend can be scanned, instead of returning an empty list.
- [x] Add a helper that returns one matching chat window or raises a clear `KakaoSendError` with candidate titles in diagnostics (`_select_kakao_chat_window`); duplicate exact titles raise `KakaoUnsafeSelectionError`.
- [x] Change `send_kakao_text` so the primary path uses strict selection among open windows.
- [x] Change `send_kakao_text` so `_focus_chat_message_input()` failure on a strictly selected window fails the send. Do not call `_focus_kakao_message_window()` after strict selection succeeds.
- [x] Ensure ambiguous or unscannable cases (`KakaoUnsafeSelectionError`) skip the main-window search entirely rather than automating the KakaoTalk UI.
- [x] Keep diagnostics for selected title, handle, backend, and rejected candidates.
- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sender.py
```

Expected: all sender tests pass. (Confirmed: 12 passed.)

### Task 3: Add UI Validation For Kakao Tabs

**Files:**
- Modify: `src/rider_crawl/ui.py`
- Modify: `tests/test_ui_helpers.py`

- [x] Add validation that an active tab with `messenger_name == "kakao"` and `send_enabled == True` must have `kakao_chat_name`.
- [x] Add validation that send-enabled active Kakao tabs cannot share the same normalized `kakao_chat_name`.
- [x] Keep Telegram validation unchanged.
- [x] Add tests for missing Kakao chat name and duplicate Kakao chat names.
- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_helpers.py
```

Expected: all UI helper tests pass. (Confirmed: 40 passed.)

### Task 4: Preserve Legacy Kakao Settings Safely

**Files:**
- Modify: `src/rider_crawl/ui_settings.py`
- Modify: `tests/test_ui_settings.py`
- Modify: `README.md`

- [x] Add a migration test for old saved settings that have `kakao_chat_name` but no `messenger_name`, no `telegram_bot_token`, and no `telegram_chat_id`.
- [x] Include both `send_enabled=true` and `send_enabled=false` legacy cases in the migration test.
- [x] Implement this migration rule: if old settings have `kakao_chat_name`, no `messenger_name`, no `telegram_bot_token`, and no `telegram_chat_id`, load them as `kakao` regardless of `send_enabled` (`_is_legacy_kakao_mapping`).
- [x] Keep the current default `telegram` for ambiguous settings (and keep an explicit `messenger_name` over the heuristic).
- [x] Document that new tabs still default to Telegram.
- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_settings.py
```

Expected: all UI settings tests pass. (Confirmed: 12 passed.)

### Task 5: End-To-End Kakao Dry Verification

**Files:**
- No production file changes expected.
- Create: `docs/kakao-verification-checklist.md`

- [x] Create `docs/kakao-verification-checklist.md` capturing the operator steps below.

> The steps below require a Windows host with KakaoTalk PC running and logged in.
> They are operator-run and are not exercised by the automated test suite.

- [ ] Open KakaoTalk PC and log in. *(operator-run)*
- [ ] Open separate chatroom windows for two or more test rooms. *(operator-run)*
- [ ] Configure `크롤링1` and `크롤링2` with different `kakao_chat_name` values and `전송 방식=카카오톡`. *(operator-run)*
- [ ] Keep `메시지 전송` off and run each tab once to confirm generated messages contain `[크롤링1]` and `[크롤링2]`. *(operator-run)*
- [ ] Turn `메시지 전송` on only for test rooms. *(operator-run)*
- [ ] Run both tabs close together. *(operator-run)*
- [ ] Confirm each KakaoTalk room receives only its own tab's message. *(operator-run)*
- [ ] Repeat with two similar room names and confirm the app fails safely instead of sending to one. *(operator-run)*
- [ ] Check `logs/kakao_diagnostics.log` after each failure path. *(operator-run)*

### Task 6: Full Regression Check

**Files:**
- No file changes expected.

- [x] Run focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sender.py tests/test_ui_helpers.py tests/test_ui_settings.py tests/test_architecture.py
```

  (Confirmed: 70 passed.)

- [x] Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

  (Confirmed: 173 passed.)

- [ ] On Windows with KakaoTalk open, repeat one real test-room send after the full suite passes. *(operator-run)*

## Verification

Before this plan was written, the following focused command passed on the original code:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sender.py tests/test_ui_helpers.py tests/test_architecture.py
```

Result: `45 passed`.

After implementing Tasks 1–4, the focused and full suites passed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sender.py tests/test_ui_helpers.py tests/test_ui_settings.py tests/test_architecture.py
.\.venv\Scripts\python.exe -m pytest
```

Result: `70 passed` (focused) and `173 passed` (full suite). The live KakaoTalk send (Task 5 and the last step of Task 6) is operator-run on a Windows host.

## Matching Rule For Implementation

Use this rule unless the operator explicitly changes the requirement before implementation:

Each tab keeps a configured `카카오톡 채팅방명`, and the sender finds that exact open window. The `크롤링N` label stays in the generated message and logs, but it is not used as the room selector.

This is safer because the UI already has the field and it matches the old KakaoTalk program concept. Mapping by `crawl_name` alone would require every KakaoTalk room title to contain `크롤링N`, which is easier to misconfigure.
