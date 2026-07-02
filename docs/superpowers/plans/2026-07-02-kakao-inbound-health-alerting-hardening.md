# Kakao Inbound Health Alerting Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface Kakao inbound disabled/warning health in Agent heartbeat and the Admin WebApp without exposing Kakao room/message/name/phone/DB secrets.

**Architecture:** Keep the existing heartbeat-based path. Add a static safe health source for pre-watcher setup failures, allow `run_agent()` to merge heartbeat health separately from the polling watcher, and update Jinja templates to compute inbound banner counts from sanitized `AgentRow` fields.

**Tech Stack:** Python 3.10+, pytest, FastAPI/TestClient, Jinja2 templates, existing `rider_agent` and `rider_server` modules.

---

## File Structure

- Modify `src/rider_agent/kakao_inbound.py`: add a small static heartbeat health source factory.
- Modify `src/rider_agent/job_loop.py`: accept and merge `kakao_inbound_health_source` independently from `kakao_inbound_watcher`.
- Modify `src/rider_agent/__main__.py`: return a static disabled health source when local config is missing/disabled or setup fails, and pass it into `run_agent()`.
- Modify `src/rider_server/admin/templates/dashboard.html`: include online Agent Kakao inbound warning/critical counts in the top status banner.
- Modify `src/rider_server/admin/templates/_agents.html`: render critical inbound reasons with `sev-critical`, warning reasons with `sev-warning`, and active/feature-disabled as neutral.
- Modify `src/rider_server/admin/templates/_kakao_inbound.html`: clarify that an empty event list can still mean Agent inbound health is disabled/warning.
- Modify `docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md`: document `.[kakao]`/`uv sync --extra kakao` setup and a SQLCipher import/connect smoke test.
- Test `tests/agent/test_kakao_inbound.py`: static health source sanitization.
- Test `tests/agent/test_job_loop.py`: watcher absent but health source present still reaches heartbeat, with no polling thread; CLI passes the source.
- Test `tests/server/test_admin_dashboard.py`: banner critical/warning counts, `feature_disabled` neutral, `_agents.html` reason class split, empty inbound guidance.

Protected files intentionally not modified:

- `src/rider_agent/worker_composition.py`
- all Coupang login/email 2FA protected runtime and test files listed in `AGENTS.md`

---

### Task 1: Agent Static Inbound Health Source

**Files:**
- Modify: `src/rider_agent/kakao_inbound.py`
- Test: `tests/agent/test_kakao_inbound.py`

- [ ] **Step 1: Write the failing test**

Add a test near the gate/build tests:

```python
def test_static_kakao_inbound_health_uses_fixed_safe_keys():
    source = static_kakao_inbound_health(
        HEALTH_DISABLED,
        REASON_DB_UNAVAILABLE,
        latest_window_size=20,
        configured_missing_count=1,
        room_name="raw-room",
        message="!!raw1234",
        db_path="C:/Users/raw/chatListInfo.edb",
        db_key="secret",
        user_hash="rawhash",
        phone_last4="1234",
    )

    health = source.health()

    assert health == {
        "state": HEALTH_DISABLED,
        "reason": REASON_DB_UNAVAILABLE,
        "latest_window_size": 20,
        "configured_missing_count": 1,
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\agent\test_kakao_inbound.py::test_static_kakao_inbound_health_uses_fixed_safe_keys -q`

Expected: FAIL because `static_kakao_inbound_health` is not defined/importable.

- [ ] **Step 3: Implement the minimal source**

In `src/rider_agent/kakao_inbound.py`, add:

```python
_SAFE_HEALTH_KEYS = frozenset({
    "state",
    "reason",
    "latest_window_size",
    "configured_missing_count",
    "scanned_count",
    "submitted_count",
    "duplicate_count",
    "rejected_count",
    "parser_miss_count",
    "submit_error_count",
    "gap_possible_count",
})


@dataclass(frozen=True)
class StaticKakaoInboundHealth:
    state: str
    reason: str
    metrics: dict[str, Any]

    def health(self) -> dict[str, Any]:
        payload = {"state": self.state, "reason": self.reason}
        for key, value in self.metrics.items():
            if key in _SAFE_HEALTH_KEYS and isinstance(value, (int, bool)) and not isinstance(value, bool):
                payload[key] = value
        return payload


def static_kakao_inbound_health(state: str, reason: str, **metrics: Any) -> StaticKakaoInboundHealth:
    return StaticKakaoInboundHealth(state=str(state), reason=str(reason), metrics=dict(metrics))
```

Adjust the numeric check if the linter flags the expression; bool must not be accepted as a count.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\agent\test_kakao_inbound.py::test_static_kakao_inbound_health_uses_fixed_safe_keys -q`

Expected: PASS.

---

### Task 2: Merge Heartbeat Health Source Without Starting Watcher Thread

**Files:**
- Modify: `src/rider_agent/job_loop.py`
- Test: `tests/agent/test_job_loop.py`

- [ ] **Step 1: Write the failing tests**

Add two tests near existing Kakao inbound job loop tests:

```python
def test_run_agent_heartbeat_includes_kakao_inbound_health_source_without_thread(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)

    class _HealthSource:
        def health(self):
            return {"state": "disabled", "reason": "db_unavailable"}

    summary = run_agent(
        transport=FakeTransport(claim_script=[{"jobs": []}]),
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
        kakao_inbound_health_source=_HealthSource(),
    )

    assert summary.kakao_inbound_thread is None
    assert summary.reporter._kakao_status_provider()["inbound"] == {
        "state": "disabled",
        "reason": "db_unavailable",
    }


def test_run_agent_without_watcher_or_health_source_keeps_default_kakao_status(tmp_path):
    store = FakeStore()
    identity_path = tmp_path / "agent_config.json"
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)

    summary = run_agent(
        transport=FakeTransport(claim_script=[{"jobs": []}]),
        store=store,
        identity_path=identity_path,
        sleep=sleep,
        now=lambda: 0.0,
        stop_event=stop,
        start_heartbeat=False,
    )

    assert summary.reporter._kakao_status_provider() == DEFAULT_KAKAO_STATUS
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests\agent\test_job_loop.py::test_run_agent_heartbeat_includes_kakao_inbound_health_source_without_thread tests\agent\test_job_loop.py::test_run_agent_without_watcher_or_health_source_keeps_default_kakao_status -q`

Expected: first test FAILS because `run_agent()` does not accept `kakao_inbound_health_source`; second should pass or expose a regression.

- [ ] **Step 3: Implement health-source merge**

Change `_merge_kakao_status_provider(kakao_status_provider, inbound_watcher)` to accept a source:

```python
def _merge_kakao_status_provider(kakao_status_provider: Any, inbound_health_source: Any) -> Any:
    if inbound_health_source is None:
        return kakao_status_provider
    ...
    inbound = _safe_kakao_inbound_health(inbound_health_source)
```

Add `kakao_inbound_health_source: Any = None` to `run_agent()` and set:

```python
effective_inbound_health_source = (
    kakao_inbound_health_source
    if kakao_inbound_health_source is not None
    else kakao_inbound_watcher
)
effective_kakao_status_provider = _merge_kakao_status_provider(
    composition.kakao_status_provider,
    effective_inbound_health_source,
)
```

Keep `start_kakao_inbound_thread(...)` gated only on `kakao_inbound_watcher is not None`.

- [ ] **Step 4: Run the focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\agent\test_job_loop.py::test_run_agent_heartbeat_includes_kakao_inbound_health tests\agent\test_job_loop.py::test_run_agent_heartbeat_includes_kakao_inbound_health_source_without_thread tests\agent\test_job_loop.py::test_run_agent_without_watcher_or_health_source_keeps_default_kakao_status -q`

Expected: PASS.

---

### Task 3: CLI Builder Surfaces Pre-Watcher Disabled Reasons

**Files:**
- Modify: `src/rider_agent/__main__.py`
- Test: `tests/agent/test_job_loop.py`

- [ ] **Step 1: Write the failing tests**

Add tests near existing `_run_agent_loop` CLI tests:

```python
def test_run_agent_loop_cli_wires_static_inbound_health_when_local_config_missing(tmp_path, monkeypatch):
    from rider_agent import __main__ as agent_main
    import rider_crawl.config as crawl_config

    captured: dict = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return AgentRunSummary(started=True, token_status=TOKEN_STATUS_VALID)

    state_root = tmp_path / "state-root"
    monkeypatch.setattr(crawl_config, "app_state_root", lambda: state_root)
    identity_path = tmp_path / "agent_config.json"
    store = FakeStore()
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    rc = agent_main._run_agent_loop([], transport=object(), store=store, identity_path=identity_path, runner=fake_run_agent)

    assert rc == 0
    assert captured["kakao_inbound_watcher"] is None
    assert captured["kakao_inbound_health_source"].health() == {
        "state": "disabled",
        "reason": "feature_disabled",
    }


def test_run_agent_loop_cli_wires_static_inbound_health_when_local_config_disabled(tmp_path, monkeypatch):
    from rider_agent import __main__ as agent_main
    import rider_crawl.config as crawl_config

    captured: dict = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return AgentRunSummary(started=True, token_status=TOKEN_STATUS_VALID)

    state_root = tmp_path / "state-root"
    config_dir = state_root / "runtime" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "kakao-inbound.json").write_text('{"enabled": false}', encoding="utf-8")
    monkeypatch.setattr(crawl_config, "app_state_root", lambda: state_root)
    identity_path = tmp_path / "agent_config.json"
    store = FakeStore()
    save_agent_identity(_IDENTITY, store=store, identity_path=identity_path)

    rc = agent_main._run_agent_loop([], transport=object(), store=store, identity_path=identity_path, runner=fake_run_agent)

    assert rc == 0
    assert captured["kakao_inbound_health_source"].health()["reason"] == "feature_disabled"
```

Update the existing `test_run_agent_loop_cli_wires_refreshing_kakao_inbound_watcher` to assert:

```python
assert captured["kakao_inbound_health_source"] is captured["kakao_inbound_watcher"]
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests\agent\test_job_loop.py::test_run_agent_loop_cli_wires_static_inbound_health_when_local_config_missing tests\agent\test_job_loop.py::test_run_agent_loop_cli_wires_static_inbound_health_when_local_config_disabled tests\agent\test_job_loop.py::test_run_agent_loop_cli_wires_refreshing_kakao_inbound_watcher -q`

Expected: new tests FAIL because the CLI does not pass `kakao_inbound_health_source`.

- [ ] **Step 3: Implement CLI result pairing**

Make `_build_kakao_inbound_watcher()` return `(watcher, health_source)`:

```python
return None, static_kakao_inbound_health(HEALTH_DISABLED, REASON_FEATURE_DISABLED)
```

When settings are enabled and setup succeeds:

```python
watcher = RefreshingKakaoInboundWatcher(...)
return watcher, watcher
```

On setup exception, return `db_unavailable`:

```python
return None, static_kakao_inbound_health(HEALTH_DISABLED, REASON_DB_UNAVAILABLE)
```

Update `_run_agent_loop()`:

```python
kakao_inbound_watcher, kakao_inbound_health_source = _build_kakao_inbound_watcher(...)
...
kakao_inbound_watcher=kakao_inbound_watcher,
kakao_inbound_health_source=kakao_inbound_health_source,
```

- [ ] **Step 4: Run focused CLI tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\agent\test_job_loop.py::test_run_agent_loop_cli_started_prints_redacted tests\agent\test_job_loop.py::test_run_agent_loop_cli_wires_static_inbound_health_when_local_config_missing tests\agent\test_job_loop.py::test_run_agent_loop_cli_wires_static_inbound_health_when_local_config_disabled tests\agent\test_job_loop.py::test_run_agent_loop_cli_wires_refreshing_kakao_inbound_watcher -q`

Expected: PASS.

---

### Task 4: Admin Dashboard and Fragment Alerting

**Files:**
- Modify: `src/rider_server/admin/templates/dashboard.html`
- Modify: `src/rider_server/admin/templates/_agents.html`
- Modify: `src/rider_server/admin/templates/_kakao_inbound.html`
- Test: `tests/server/test_admin_dashboard.py`

- [ ] **Step 1: Write failing dashboard/template tests**

Add tests:

```python
def test_status_banner_red_when_online_kakao_inbound_db_unavailable() -> None:
    repo = _clean_repo()
    repo.seed_agent(
        AgentHealthFacts(
            agent_id="a-inbound",
            name="agent-inbound",
            version="1.0.0",
            last_heartbeat_at=datetime.now(timezone.utc),
            current_job_type=None,
            capabilities=("KAKAO_SEND",),
            kakao_status={"inbound": {"state": "disabled", "reason": "db_unavailable"}},
        )
    )

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    assert 'class="statusbanner sb-crit"' in body
    assert "Kakao inbound 1대 장애" in body


def test_status_banner_warn_when_online_kakao_inbound_room_missing() -> None:
    repo = _clean_repo()
    repo.seed_agent(
        AgentHealthFacts(
            agent_id="a-inbound",
            name="agent-inbound",
            version="1.0.0",
            last_heartbeat_at=datetime.now(timezone.utc),
            current_job_type=None,
            capabilities=("KAKAO_SEND",),
            kakao_status={"inbound": {"state": "warning", "reason": "configured_room_not_found"}},
        )
    )

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    assert 'class="statusbanner sb-warn"' in body
    assert "Kakao inbound 1대 확인 필요" in body


def test_status_banner_keeps_feature_disabled_neutral() -> None:
    repo = _clean_repo()
    repo.seed_agent(
        AgentHealthFacts(
            agent_id="a-inbound",
            name="agent-inbound",
            version="1.0.0",
            last_heartbeat_at=datetime.now(timezone.utc),
            current_job_type=None,
            capabilities=("KAKAO_SEND",),
            kakao_status={"inbound": {"state": "disabled", "reason": "feature_disabled"}},
        )
    )

    body = _client(repo).get(f"/admin?tenant={_TENANT}").text

    assert 'class="statusbanner sb-ok"' in body
    assert "Kakao inbound 1대" not in body


def test_agents_fragment_marks_inbound_reason_by_severity_class() -> None:
    html = admin_routes.templates.env.get_template("_agents.html").render(
        agents=[
            AgentRow(
                agent_id="a-critical",
                name="agent-critical",
                version="1.0.0",
                last_heartbeat_at=_NOW,
                online=True,
                current_job_type=None,
                capabilities=("KAKAO_SEND",),
                kakao_inbound_state="disabled",
                kakao_inbound_reason="db_unavailable",
            ),
            AgentRow(
                agent_id="a-warning",
                name="agent-warning",
                version="1.0.0",
                last_heartbeat_at=_NOW,
                online=True,
                current_job_type=None,
                capabilities=("KAKAO_SEND",),
                kakao_inbound_state="warning",
                kakao_inbound_reason="configured_room_not_found",
            ),
        ]
    )

    assert '<span class="sev-critical">db_unavailable</span>' in html
    assert '<span class="sev-warning">configured_room_not_found</span>' in html


def test_kakao_inbound_fragment_empty_state_points_to_agent_fleet() -> None:
    body = _client(InMemoryDashboardRepository()).get(f"/admin/kakao-inbound?tenant={_TENANT}").text

    assert "Agent fleet" in body
    assert "DB" in body
    assert "SQLCipher" in body
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests\server\test_admin_dashboard.py::test_status_banner_red_when_online_kakao_inbound_db_unavailable tests\server\test_admin_dashboard.py::test_status_banner_warn_when_online_kakao_inbound_room_missing tests\server\test_admin_dashboard.py::test_status_banner_keeps_feature_disabled_neutral tests\server\test_admin_dashboard.py::test_agents_fragment_marks_inbound_reason_by_severity_class tests\server\test_admin_dashboard.py::test_kakao_inbound_fragment_empty_state_points_to_agent_fleet -q`

Expected: FAIL until templates are updated.

- [ ] **Step 3: Implement template-only alerting**

In `dashboard.html`, compute lists/counts:

```jinja2
{% set inbound_critical_reasons = ['db_unavailable', 'sqlcipher_missing', 'db_key_missing', 'prerequisites_missing', 'non_interactive_session'] %}
{% set inbound_warning_reasons = ['configured_room_not_found', 'empty_watchlist', 'latest_window_size_1'] %}
{% set inbound = namespace(critical=0, warning=0) %}
{% for a in agents %}
  {% if a.online and a.kakao_inbound_reason in inbound_critical_reasons %}
    {% set inbound.critical = inbound.critical + 1 %}
  {% elif a.online and a.kakao_inbound_reason in inbound_warning_reasons %}
    {% set inbound.warning = inbound.warning + 1 %}
  {% elif a.online and a.kakao_inbound_state and a.kakao_inbound_state not in ['active', 'disabled'] %}
    {% set inbound.warning = inbound.warning + 1 %}
  {% endif %}
{% endfor %}
```

Add `inbound.critical` to `sb_crit`, `inbound.warning` to `sb_warn`, and add detail bits:

```jinja2
{% if inbound.critical %}{% set _ = sb_bits.append('Kakao inbound ' ~ inbound.critical ~ '대 장애') %}{% endif %}
{% if inbound.warning %}{% set _ = sb_bits.append('Kakao inbound ' ~ inbound.warning ~ '대 확인 필요') %}{% endif %}
```

In `_agents.html`, define reason lists and choose the class by reason. In `_kakao_inbound.html`, replace the empty row text with the operator guidance from the spec.

- [ ] **Step 4: Run focused server tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\server\test_admin_dashboard.py::test_status_banner_red_when_online_kakao_inbound_db_unavailable tests\server\test_admin_dashboard.py::test_status_banner_warn_when_online_kakao_inbound_room_missing tests\server\test_admin_dashboard.py::test_status_banner_keeps_feature_disabled_neutral tests\server\test_admin_dashboard.py::test_agents_fragment_marks_inbound_reason_by_severity_class tests\server\test_admin_dashboard.py::test_kakao_inbound_fragment_empty_state_points_to_agent_fleet -q`

Expected: PASS.

---

### Task 5: Operation Runbook Update

**Files:**
- Modify: `docs/operations/agent-pc-setup-jena-5800h-2026-06-18.md`

- [ ] **Step 1: Update setup instructions**

Change editable install command from:

```powershell
.venv\Scripts\pip.exe install -e ".[dev,server]"
```

to:

```powershell
.venv\Scripts\pip.exe install -e ".[dev,server,kakao]"
```

Add an alternate `uv sync --extra kakao` path and the SQLCipher smoke test:

```powershell
uv sync --extra kakao
```

```powershell
@'
import importlib

mod = importlib.import_module("sqlcipher3")
connect = getattr(mod, "connect", None)
if connect is None:
    sub = importlib.import_module("sqlcipher3._sqlite3")
    connect = getattr(sub, "connect", None)
assert callable(connect), "sqlcipher3 DB-API connect is unavailable"
conn = connect(":memory:")
try:
    assert conn.execute("select 1").fetchone()[0] == 1
finally:
    conn.close()
print("sqlcipher3 ok")
'@ | .\.venv\Scripts\python.exe -
```

- [ ] **Step 2: Verify docs contain the operational commands**

Run: `rg -n "dev,server,kakao|uv sync --extra kakao|sqlcipher3 ok|sqlcipher3._sqlite3" docs\operations\agent-pc-setup-jena-5800h-2026-06-18.md`

Expected: all strings are found.

---

### Task 6: Final Verification

**Files:**
- Verify all modified code/tests/docs.

- [ ] **Step 1: Run related tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\agent\test_kakao_inbound.py tests\agent\test_job_loop.py tests\agent\test_heartbeat.py tests\server\test_agents_api.py tests\server\test_admin_dashboard.py tests\test_kakao_db.py -q
```

Expected: PASS.

- [ ] **Step 2: Run protected test set if any protected files were touched**

If `git diff --name-only` includes any path listed as protected in `AGENTS.md`, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
```

Expected: PASS. If no protected file is touched, record that this protected suite was not required.

- [ ] **Step 3: Inspect diff for secret exposure and protected files**

Run: `git diff --name-only`

Expected: no Coupang protected runtime file changes; no OTP/password/app password/plaintext secret additions.
