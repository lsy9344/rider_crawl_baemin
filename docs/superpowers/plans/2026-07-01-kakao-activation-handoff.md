# Kakao Inbound Rider Lookup — Handoff (2026-07-01)

Self-contained handoff so a fresh session can continue without re-deriving state.
Communicate in Korean. Work in the existing worktree/branch below.

- **Worktree:** `C:\code\rider_crawl_baemin\.claude\worktrees\kakao-inbound-rider-lookup`
- **Branch:** `feature/kakao-inbound-rider-lookup` (base `main`)
- **Draft PR:** https://github.com/lsy9344/rider_crawl_baemin/pull/3 (needs new commits pushed)
- **Design spec:** `docs/superpowers/specs/2026-07-01-kakao-inbound-rider-lookup-design.md`
- **Activation design:** `docs/superpowers/specs/2026-07-01-kakao-inbound-activation-wiring.md`
- **chatLogs schema (confirmed):** `docs/superpowers/specs/2026-07-01-kakao-chatlogs-schema-investigation.md`

## Status: feature code-complete; only operator E2E remains

Phases 1–5 + Hybrid activation wiring + all review findings (HIGH/MEDIUM/LOW) are
done and committed. Tests green: server **1432**, agent+protected **804**, plus
per-slice runs. **No protected file changed** (`worker_composition.py` untouched;
only non-protected `job_loop.py` / `__main__.py` were edited).

### Commit chain this session (on top of operator's `c17c0a0`)
- `d65ef4c` review fixes: exactly-one KAKAO_SEND (HIGH retry-gating, MEDIUM reply replay dedupe) + expires_at
- `616f40c` Phase 5 chatLogs schema investigation runbook
- `c17c0a0` (operator) ChatLogsReader latest-N + confirmed schema
- `78d4124` job_loop inbound-watcher daemon-thread injection point
- `ded079a` Hybrid activation design + `build_kakao_inbound_watcher`
- `d26ed86` **A** server `GET /v1/agents/kakao-inbound-config` watchlist
- `5908514` **B** Agent `KakaoWatchlistClient` (fetch/parse) + `Transport.get_json`
- `619740b` **C** `resolve_kakao_inbound_enabled` gate + `resolve_kakao_inbound_rooms`
- `d34364c` **D1** `load_local_kakao_inbound_settings` + `make_kakao_reader_factory`
- `cfb59b6` **D2** `build_kakao_inbound_watcher_from_sources` + `__main__` wiring
- `60863d4` review LOW: unsupported-platform reply dedupe on origin_event_key

## Hybrid contract (do not violate)

- **Server/WebApp = SoT for the non-secret watchlist** (room_name/optional chat_id,
  channel ACTIVE/INACTIVE, command_trigger_enabled, target mapping, tenant/send
  gate/dedupe/rate-limit/in-flight). Server re-validates every inbound event via
  `decide_inbound_event`; the watchlist is only a scan-scope limiter.
- **Agent = SoT for local prerequisites + secrets** (DB path, SQLCipher db_key,
  user_hash, per-room chatlogs key, local kill switch, scan defaults).
- **effective_enabled = local kill switch && local prereq OK && session interactive
  && server watchlist non-empty** (local fallback rooms count as canary).
- **Never** send/receive DB key/user_hash/path to/from the server. Only the digest
  + sanitized events leave the Agent. No raw Kakao text / name / phone suffix /
  secret in logs/heartbeat/status/exceptions. Rejection reasons are fixed codes.

## Key files & seams

- `src/rider_agent/kakao_inbound.py` — watcher, client, `KakaoWatchlistClient`,
  gate (`resolve_kakao_inbound_enabled`), `resolve_kakao_inbound_rooms`,
  `LocalKakaoInboundSettings`+`load_local_kakao_inbound_settings`,
  `make_kakao_reader_factory`, `build_kakao_inbound_watcher_from_sources`.
  Secret refs: `KAKAO_DB_KEY_REF="kakao_inbound:db_key"`,
  `KAKAO_USER_HASH_REF="kakao_inbound:user_hash"`.
- `src/rider_agent/job_loop.py` — `run_agent(kakao_inbound_watcher=..., kakao_inbound_interval_seconds=...)`;
  `_run_kakao_inbound_loop` / `start_kakao_inbound_thread`; `AgentRunSummary.kakao_inbound_thread`.
- `src/rider_agent/__main__.py` — `_build_kakao_inbound_watcher(...)` (fail-safe → None), wired into `runner(...)`.
- `src/rider_agent/registration.py` — `Transport.get_json` + `HttpTransport.get_json`.
- `src/rider_crawl/kakao_db.py` — `ChatLogsReader` (latest-20, degrades to `ChatRoomListReader` latest-one) + `KakaoDbReader` Protocol.
- `src/rider_server/api/agents.py` — `GET /v1/agents/kakao-inbound-config` (Depends(resolve_agent)).
- `src/rider_server/services/channel_registration.py` / `channel_repository_postgres.py` — `active_kakao_command_channels()`.
- `src/rider_server/services/kakao_inbound_event_service.py` — decision core + `handle()`; `already_replied` seam on ACTION_REPLY.
- `src/rider_server/services/kakao_inbound_wiring.py` — `build_kakao_inbound_event_service`, `build_kakao_lookup_reply_service`.

## Remaining = operator-only (cannot be done in this sandbox)

1. **push** the branch (no GitHub auth here: no stored creds/SSH/token, `gh` absent)
   → updates draft PR #3. PR body prepared earlier; regenerate from commits if needed.
2. **Server/WebApp:** set target Kakao channel `ACTIVE` + `command_trigger_enabled`.
3. **Agent PC (local only):** write `app_state_root()/runtime/config/kakao-inbound.json`:
   ```json
   {"enabled": true,
    "chat_list_db_path": "%LOCALAPPDATA%/Kakao/KakaoTalk/users/<hash>/chat_data/chatListInfo.edb",
    "chat_logs_dir": "%LOCALAPPDATA%/Kakao/KakaoTalk/users/<hash>/chat_data",
    "use_chat_logs": true, "latest_messages_limit": 20, "rooms": []}
   ```
   and register secure-store secrets `kakao_inbound:db_key`, `kakao_inbound:user_hash`.
4. **Headed E2E** (required by CLAUDE.md before "complete"): clear duplicate Agents →
   restart a single Agent on this branch/venv → in a controlled room send
   `!!강민기1234` → Baemin lookup → scoped Kakao reply. Keep PR draft until verified.

## Next phases (new expansions — not part of current feature)

### Phase 6 — Telegram convergence (needs a product decision)
Move `telegram_commands.py`'s own parser/matcher onto the shared `rider_crawl`
rider-lookup core. **Decision required:** preserve legacy single-`!` `!홍길동1234`
behavior, or intentionally migrate to `!!`. Design says either is OK but it must be
a separately tested change. Keep Telegram transport polling/webhook outside business
rules. Recommend scouting `telegram_commands.py` first, then decide.

### Phase 7 — Coupang evaluation
Separately verify whether Coupang exposes stable rider-level cancellation data;
if yes design platform-specific extraction **without** changing protected Coupang
login/2FA files.

## Environment gotchas (sandbox)

- Python 3.10: `tomllib` missing → `test_deployment_config.py`, `test_admin_dashboard.py`,
  `test_agent_package.py` fail to COLLECT (pre-existing env issue, not code). Operator
  ran full suite (3013 passed) by injecting `tomli` as `tomllib` + blanking `USERDOMAIN`
  to bypass an `icacls` principal issue in `secret_store.py` (21 `test_ui_settings.py`).
- No GitHub auth here → cannot push/PR. `gh` not installed.
- PowerShell here-strings break on Korean → commit via `git commit -F <file>`
  (scratchpad under `$CLAUDE_JOB_DIR/tmp` or session scratchpad).
- `docs/kakao_db/` (main repo, untracked, NOT in this worktree) holds REAL secrets —
  read-only for schema only; never copy values into code/commits/PR.

## Protected files (CLAUDE.md — trace + test-first + protected set + headed verify before editing)

Runtime: `rider_crawl/auth/coupang_email_2fa.py`, `rider_agent/auth/coupang_gmail_2fa.py`,
`rider_agent/worker_composition.py`, `rider_crawl/platforms/coupang/crawler.py`,
`rider_server/services/admin_action_service.py`, `rider_server/scheduler/service.py`,
`rider_server/queue/postgres_queue.py`.

Protected test set:
```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
```

## Handy test commands

```powershell
# Kakao feature (agent side)
.\.venv\Scripts\python.exe -m pytest tests\agent\test_kakao_inbound.py tests\agent\test_job_loop.py -q
# Kakao feature (server side)
.\.venv\Scripts\python.exe -m pytest tests\server\test_kakao_inbound_event.py tests\server\test_kakao_inbound_api.py tests\server\test_kakao_lookup_reply.py tests\server\test_kakao_agent_config_api.py tests\server\test_channel_lifecycle.py -q
# Full server suite (skip tomllib-collect failures)
.\.venv\Scripts\python.exe -m pytest tests\server --ignore=tests\server\test_deployment_config.py --ignore=tests\server\test_admin_dashboard.py -q
```
