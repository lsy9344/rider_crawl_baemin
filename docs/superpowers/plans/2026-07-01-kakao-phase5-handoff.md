# Kakao Phase 5 Handoff

Date: 2026-07-01
Worktree: `C:\code\rider_crawl_baemin\.claude\worktrees\kakao-inbound-rider-lookup`
Branch: `feature/kakao-inbound-rider-lookup`
Draft PR: https://github.com/lsy9344/rider_crawl_baemin/pull/3
Latest commit at handoff: `c17c0a0 Add Kakao chatLogs latest-N reader`

This note records operational handoff details only. It intentionally excludes
Kakao DB keys, room IDs, room names, message bodies, account identifiers, device
identifiers, OTPs, passwords, and plaintext secret values.

## Completed

- Found the active worktree for `feature/kakao-inbound-rider-lookup`.
- Installed optional `sqlcipher3` into the local project venv.
- Confirmed the local KakaoTalk `chatLogs_<id>.edb` schema from copied DB files.
- Implemented `ChatLogsReader` for latest-N reads.
- Pushed the branch to `origin/feature/kakao-inbound-rider-lookup`.
- Opened draft PR #3.

## Confirmed ChatLogs Schema

The per-room log DB uses SQLCipher with:

```text
PRAGMA cipher_compatibility = 4
PRAGMA key = "x'<raw-hex-key>'"
```

Confirmed table and column mapping:

```text
message_table = "chatLogs"
log_id    -> "logId"   (UNSIGNED BIG INT, primary key, monotonic)
chat_id   -> derived from file name chatLogs_<chat_id>.edb
text      -> "message" (TEXT after SQLCipher open)
timestamp -> "sendAt"  (INTEGER epoch seconds)
type      -> "type"    (INTEGER)
deleted   -> "deleted" (INTEGER; filter with COALESCE(deleted, 0) = 0)
```

Important finding: `chatListInfo.edb` and `chatLogs_<id>.edb` can use different
keys. Do not assume the chat-list key opens every chat-log DB.

## Code Changes

- `src/rider_crawl/kakao_db.py`
  - Added `LATEST_TWENTY_WINDOW_SIZE`.
  - Added `ChatLogsReader`.
  - Room discovery still delegates to `ChatRoomListReader`.
  - Message reads use `chatLogs_<chat_id>.edb`.
  - Latest candidate rows are selected newest-first, then returned
    oldest-to-newest for high-water processing.
  - If chatLogs open/schema/key lookup fails, fallback to `ChatRoomListReader`
    and report `latest_window_size = 1`.

- `src/rider_agent/kakao_inbound.py`
  - Default `latest_messages_limit` is now `20`.
  - Added `gap_possible` to `ScanReport`.
  - First latest-N scan primes to the newest visible log ID without processing
    historical commands.
  - New visible messages are processed oldest-to-newest.
  - If the previous high-water mark is outside a full latest-N window, the
    watcher reports `gap_possible` and primes to the newest visible message
    instead of flooding old messages.

- `src/rider_agent/reuse.py`
  - Re-exported `ChatLogsReader`.

- Tests added/updated:
  - `tests/test_kakao_db.py`
  - `tests/agent/test_kakao_inbound.py`

## Validation

Targeted regression:

```powershell
C:\code\rider_crawl_baemin\.venv\Scripts\python.exe -m pytest tests\test_kakao_db.py tests\agent\test_kakao_inbound.py tests\agent\test_job_loop.py tests\server\test_kakao_inbound_api.py tests\server\test_kakao_inbound_event.py tests\server\test_kakao_lookup_reply.py -q
```

Result:

```text
155 passed, 1 skipped
```

Full suite validation used Python 3.10 with `tomli` injected as `tomllib`, and
`USERDOMAIN` cleared so local `icacls` tests use the current username principal:

```text
3013 passed, 79 skipped
```

Real local Kakao DB smoke check:

```text
REAL_CHATLOG_READER=ok messages=0 window=20
```

This means the reader opened a copied real chatLogs DB and executed the query
successfully. It does not mean a real command message was observed in that room.

## Current Runtime Notes

At handoff, two `rider_agent run --server-url http://54.116.103.149:8000`
processes were observed. They were not stopped or restarted.

Before headed E2E verification, ensure only one intended Agent process is
running from the expected branch/venv.

## Remaining Verification

The PR must stay draft until a controlled headed end-to-end check is completed:

```text
Kakao command in configured room:
  !!강민기1234

Expected flow:
  Kakao DB latest-N watcher
  -> POST /v1/kakao/inbound-events
  -> server maps room to one active Baemin target
  -> RIDER_LOOKUP job
  -> Baemin delivery-history row fetch
  -> scoped KAKAO_SEND reply to the requesting room
```

Do not claim Phase 4/5 production completion until this real headed flow is
verified on the Agent PC or equivalent local Chrome/Kakao session.

## Safety Notes

- No Coupang protected login/email 2FA runtime files were changed by the Phase 5
  commit.
- Do not record Kakao DB keys, chat IDs, room names, message bodies, account
  identifiers, device identifiers, OTPs, passwords, or plaintext secret values
  in code, docs, commits, PR comments, or logs.
- The main worktree still has unrelated local changes:

```text
M docs/superpowers/specs/2026-07-01-kakao-inbound-rider-lookup-design.md
?? docs/kakao_db/
```

Those files were left untouched by this handoff.
