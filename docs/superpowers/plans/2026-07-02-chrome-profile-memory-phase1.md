# Chrome Profile Memory Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only Chrome root inventory and a sanitized top-level `browser_slots` heartbeat contract without closing Chrome processes.

**Architecture:** Phase 1 observes Agent-owned Chrome profiles under `runtime/agent-browser-profiles`, counts browser roots using the verified root rule, and reports aggregate slot fields through heartbeat. Server storage uses a dedicated allowlist so raw paths, URLs, titles, and command lines never enter `capacity_json`.

**Tech Stack:** Python, pytest, FastAPI/Pydantic, psutil when available.

---

### Task 1: Root Detection And Read-Only Inventory

**Files:**
- Modify: `src/rider_crawl/browser_launcher.py`
- Create: `src/rider_agent/browser_inventory.py`
- Modify: `src/rider_agent/browser_profile.py`
- Test: `tests/test_browser_launcher.py`
- Test: `tests/agent/test_browser_profile.py`

- [ ] Write failing tests proving `--type=` Chrome children with `--remote-debugging-port` are not adopted as browser roots.
- [ ] Write failing tests proving inventory counts only roots under the Agent profile root and returns aggregate counts without raw path/cmdline fields.
- [ ] Implement the minimal root predicate and inventory helper.
- [ ] Add a `BrowserProfileManager.browser_slots()` provider that combines registry count with read-only OS inventory.
- [ ] Run focused browser tests.

### Task 2: Agent Heartbeat Payload

**Files:**
- Modify: `src/rider_agent/heartbeat.py`
- Modify: `src/rider_agent/job_loop.py`
- Test: `tests/agent/test_heartbeat.py`
- Test: `tests/agent/test_job_loop.py`

- [ ] Write failing tests for top-level `browser_slots` default `{}` and provider injection.
- [ ] Add `browser_slots_provider` plumbing through payload builder, sender, reporter, and agent component wiring.
- [ ] Wire a default provider from the crawl profile manager when available without touching protected `worker_composition.py`.
- [ ] Run focused agent heartbeat tests.

### Task 3: Server Heartbeat Contract

**Files:**
- Modify: `src/rider_server/api/agents.py`
- Modify: `src/rider_server/services/agent_registry.py`
- Test: `tests/server/test_agents_api.py`

- [ ] Write failing tests for top-level `browser_slots` acceptance, old-payload compatibility, and sanitizer behavior.
- [ ] Add `browser_slots` to `HeartbeatRequest` and `HeartbeatInput`.
- [ ] Store `capacity_json["browser_slots"]` through a dedicated numeric allowlist.
- [ ] Run focused server heartbeat tests.

### Task 4: Verification

**Files:**
- No production edits.

- [ ] Run `C:\code\rider_crawl_baemin\.venv\Scripts\python.exe -m pytest tests\test_browser_launcher.py tests\agent\test_browser_profile.py tests\agent\test_heartbeat.py tests\agent\test_job_loop.py tests\server\test_agents_api.py -q`.
- [ ] Run protected test set only if a protected runtime file is touched.
- [ ] Request subagent review for spec compliance and code quality before final status.
