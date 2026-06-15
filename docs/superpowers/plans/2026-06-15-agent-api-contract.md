# Agent API Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing server-side Agent register and heartbeat API required by `docs/refactoring/refactoring_improvement_direction.md` Phase 1.

**Architecture:** Keep the HTTP routes thin. Put registration and heartbeat state changes behind a small service/repository port with in-memory and PostgreSQL implementations, matching existing server patterns.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async repositories, pytest, FastAPI `TestClient`.

---

### Task 1: HTTP Contract Tests

**Files:**
- Create: `tests/server/test_agents_api.py`

- [ ] **Step 1: Write failing route tests**

Write tests that prove `/v1/agents/register` exists, returns `agent_id` and `agent_token`, rejects reused codes, requires bearer auth for heartbeat, and updates heartbeat state.

- [ ] **Step 2: Run red test**

Run: `.venv\Scripts\python.exe -m pytest tests/server/test_agents_api.py -q`

Expected: fail with `404` for `/v1/agents/register`.

### Task 2: Service And Repository

**Files:**
- Create: `src/rider_server/services/agent_registry.py`
- Create: `src/rider_server/services/agent_registry_postgres.py`

- [ ] **Step 3: Implement minimal in-memory service**

Implement a repository port plus in-memory repository that stores one-time registration codes, created agents, hashed bearer tokens, and heartbeat payload fields.

- [ ] **Step 4: Implement PostgreSQL repository**

Use the existing `agents` and `browser_profiles` tables. Store only a token hash/ref, never the plaintext token.

### Task 3: FastAPI Wiring

**Files:**
- Create: `src/rider_server/api/agents.py`
- Modify: `src/rider_server/main.py`

- [ ] **Step 5: Add routes and app state wiring**

Include the new router in `create_app()`, add `app.state.agent_registry`, and have heartbeat validate `Authorization: Bearer`.

- [ ] **Step 6: Run green tests**

Run: `.venv\Scripts\python.exe -m pytest tests/server/test_agents_api.py tests/server/test_jobs_api.py tests/agent/test_registration.py tests/agent/test_heartbeat.py -q`

Expected: all pass.

### Task 4: Final Verification

- [ ] **Step 7: Run full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: all existing tests pass.
