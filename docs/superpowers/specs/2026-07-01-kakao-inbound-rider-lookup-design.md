# Kakao Inbound Rider Lookup Design

Date: 2026-07-01
Status: Revised design for implementation planning

## Goal

Build a command-triggered rider lookup flow for operator-configured KakaoTalk
rooms. The first command is a Baemin cancellation-rate lookup:

```text
!!강민기1234
```

The Agent PC watches only configured KakaoTalk rooms. When a recent inbound
message contains a valid command token, the Agent reports a sanitized event to
the server. The server validates, deduplicates, maps the room to exactly one
active Baemin target, enqueues lookup work, and sends one scoped reply back to the
requesting Kakao room through the existing production Kakao send path.

The same parser, matching, calculation, and renderer must later be usable from
Telegram. The current local `rider_crawl.telegram_commands` implementation is
useful evidence, but it remains a legacy direct-local path until it is migrated
through this shared contract.

## Reviewed Context

### `docs/kakao_db` Findings

The research package proves these facts for the tested KakaoTalk Windows build:

- `chatListInfo.edb` is a SQLCipher DB under
  `%LOCALAPPDATA%\Kakao\KakaoTalk\users\<hash>\chat_data\`.
- `kakao_monitor.py` copies the locked DB, opens it with
  `PRAGMA cipher_compatibility = 4`, and reads `chatRoomList`.
- The validated monitor searches `lastChatMessage LIKE '%!%'`, stores processed
  `chatId_lastLogId` keys in `monitor_state.json`, and passes `KAKAO_CHAT_INFO`
  to a callback process.
- The validated sender uses KakaoTalk PC UI focus, coordinates, clipboard paste,
  `DOWN`, and `ENTER`.
- The docs and sample code currently contain real DB key, account, device, and
  user-hash material. Treat `docs/kakao_db` as untracked research input until it
  is sanitized.

Production implications:

- Do not import or execute `docs/kakao_db/callback_example.py` in runtime.
- Do not use `docs/kakao_db/kakao_sender.py` for production send.
- Do not hard-code Kakao DB keys, user hashes, account identifiers, device IDs,
  room IDs, chat logs, or message bodies.
- The validated monitor proves only a latest-one-room-state signal via
  `chatRoomList.lastChatMessage`; it does not prove latest-20 per-room log
  ingestion.

### Current Runtime Findings

- Production Kakao outbound send is `KAKAO_SEND` -> Agent
  `KakaoSenderWorker` -> `rider_crawl.sender.send_kakao_text`.
- `KakaoSenderWorker` already provides FIFO single-session send, exact-room
  validation reuse, fail-closed missing payload handling, no fallback to another
  room/channel, and no raw room/message values in heartbeat/result logs.
- Server job types are plain strings in `src/rider_server/queue/states.py` and
  must mirror Agent capabilities in `src/rider_agent/heartbeat.py`. Existing
  tests lock this relationship.
- Agent code may import only stdlib, `rider_agent`, and `rider_crawl` roots.
  Direct `sqlcipher3`, `pywinauto`, or server imports from `rider_agent` would
  violate package guards unless the architecture is deliberately changed.
- Normal `CRAWL_BAEMIN` completion stores aggregate snapshot JSON. Rider-level
  name/phone/cancel rows exist only while parsing Baemin delivery-history tables,
  not in the stored snapshot.
- The protected Coupang login/email 2FA contract must remain untouched in phase 1.

## Non-Goals

- Do not replace the existing production Kakao sender with the research sender.
- Do not let the Kakao DB monitor directly crawl or directly send Kakao messages.
- Do not change protected Coupang login/email 2FA behavior.
- Do not make command-triggered lookup run against Coupang in phase 1.
- Do not build a generic KakaoTalk bot framework.
- Do not guarantee recovery of every missed KakaoTalk message while using
  polling and latest-N scans. This is a best-effort trigger source.
- Do not add a new server table for inbound events in the first implementation.

## Operational Safety Constraint

The current project code is already running in production-like operation. This
work must be additive and must not disrupt existing crawl, auth recovery, queue,
Telegram dispatch, or Kakao outbound send behavior.

Implementation must preserve these active paths unless a later approved plan
explicitly changes them:

- scheduled Baemin and Coupang crawl jobs;
- Coupang email 2FA recovery and auth-state updates;
- existing `KAKAO_SEND` outbound delivery;
- Telegram webhook, central dispatch, and legacy local Telegram command handling;
- queue claim, lease, completion, recovery, and scheduler behavior;
- Admin manual actions and current dashboard/status surfaces.

Every implementation slice must include focused regression tests for the touched
path and a rollback-safe default. New inbound Kakao detection must be disabled by
default until configured, and any degraded or invalid Kakao DB state must disable
only inbound detection, not the existing production crawl/send paths.

## Design Decisions

1. Keep the existing outbound Kakao path.
   Final replies must be `KAKAO_SEND` jobs handled by `KakaoSenderWorker`.

2. Use `docs/kakao_db` as research only.
   Copy the DB-reading idea into tested production modules. Do not import the
   research scripts or run callback subprocesses.

3. Agent detects, server decides.
   The Agent PC can inspect local Kakao DB files. The server owns validation,
   room/channel/target mapping, tenant gates, dedupe, throttling, job creation,
   and reply creation.

4. Make command logic transport-neutral.
   Parser, matcher, cancel-rate calculation, and renderer live in one shared
   command module. Kakao and Telegram only provide transport adapters.

5. Reply only to the requesting room/channel.
   Command replies are not normal snapshot fanout and must not use delivery rules
   to broadcast to other channels.

6. Phase 1 is Baemin-only.
   If an inbound event maps to a Coupang target, the server sends a scoped
   unsupported reply and does not crawl Coupang.

7. Avoid a new inbound-event table initially.
   Use the existing `jobs` table payload for `origin_event_key` dedupe. Add a
   dedicated table only after volume or audit requirements justify a migration.

8. Respect Agent dependency guards.
   Kakao DB reading needs SQLCipher. Put optional DB-reader code behind a
   `rider_crawl` reuse seam and an optional install/package extra, or otherwise
   keep it outside `rider_agent` imports. Do not import `sqlcipher3` directly from
   `rider_agent`.

## Command Contract

### Detection Filter

The DB reader may use a broad candidate filter equivalent to:

```sql
LIKE '%!!%'
```

That filter is only a cheap prefilter. A message is actionable only if the shared
command parser finds a valid command token.

### Valid Token

Recommended regex:

```python
COMMAND_TOKEN_RE = re.compile(
    r"(?<!\S)!!(?P<name>[가-힣]{1,20})(?P<phone_last4>[0-9]{4})(?=$|\s|[.,!?;:)\]\}])"
)
```

Rules:

- Prefix is exactly `!!`.
- `name` is 1-20 Hangul syllables.
- `phone_last4` is exactly four ASCII digits.
- The token starts at message start or after whitespace.
- The token ends at message end, whitespace, or listed punctuation.
- If one message contains multiple valid tokens, phase 1 processes only the first
  token in left-to-right order.

Examples:

| Message | Result |
| --- | --- |
| `!!강민기1234` | match |
| `확인 !!강민기1234` | match |
| `!!김1234 확인` | match |
| `!!강민기1234.` | match |
| `!!` | ignore |
| `!!1234` | ignore |
| `!!강민기12` | ignore |
| `!!강민기12345` | ignore |
| `!!hong1234` | ignore |
| `메모!!강민기1234` | ignore |

If product usage later requires embedded commands or non-Hangul names, loosen the
regex in a separate tested change.

### Parsed Payload

```json
{
  "type": "RIDER_CANCEL_RATE_LOOKUP",
  "name": "강민기",
  "phone_last4": "1234"
}
```

Raw message text must not be stored in normal logs, heartbeat payloads, job
events, or server audit text. Parsed name and phone suffix are sensitive
operational data; log fixed event types and hashes instead.

## Cancel-Rate Contract

Input is rider-level Baemin delivery-history rows, not aggregate snapshot JSON.
Each row must contain:

- `이름`
- `휴대폰번호` or an equivalent phone column
- `완료`
- `거절`
- `배차취소`
- `배달취소(라이더귀책)`

Matching:

- Normalize names with Unicode NFC and surrounding whitespace trim.
- Match the normalized Hangul name exactly.
- Extract digits from the phone field and match the last four digits exactly.
- Do not fuzzy-match names or phone suffixes.

Calculation:

```text
total_cancel = 배차취소 + 배달취소(라이더귀책)
denominator = 완료 + 거절 + total_cancel
cancel_rate = 0 if denominator == 0 else round(total_cancel / denominator * 100, 1)
```

Risk threshold:

```text
cancel_rate >= 4.0 -> 위험합니다.
cancel_rate < 4.0  -> 정상 범위입니다.
```

Renderer output:

```text
강민기1234
취소율 3.8%, 취소 2개
정상 범위입니다.
```

No match:

```text
강민기1234
해당 라이더를 찾지 못했습니다.
```

Multiple matches:

```text
강민기1234
동명이인 또는 중복 후보가 있어 조회할 수 없습니다: 크롤링1, 크롤링2
```

Ambiguity labels must be redacted source labels only. Do not include full phone
numbers or raw row values.

Unsupported platform:

```text
라이더 조회 명령은 배민 탭에서만 지원합니다.
```

Kakao phase 1 sends only the final response. Telegram may keep its existing
`조회 중입니다.` progress reply outside the shared command service for backward
compatibility.

## Architecture

### Components

`RiderLookupCommandService` in `rider_crawl` or another shared neutral module

- Owns parser, command DTOs, row matching, cancel-rate calculation, and rendering.
- Has no KakaoTalk, Telegram, browser, Agent, FastAPI, SQLAlchemy, or queue
  dependency.
- Replaces duplicated business logic before Telegram convergence.

`KakaoDbReader` behind a production interface

- Reads KakaoTalk DB files through tested code copied from the research pattern.
- Uses optional SQLCipher support lazily.
- Exposes only room refs and sanitized message refs.
- Never logs DB key, user hash, message text, or raw chat dump.

`KakaoInboundWatcher` in `rider_agent`

- Runs only on Windows interactive Agent nodes with inbound Kakao enabled.
- Imports DB-reader functionality only through `rider_crawl`/reuse seams.
- Scans only server-configured or locally allowlisted rooms.
- Applies the shared command parser.
- Maintains local high-water marks per `chat_id` when available, otherwise per
  normalized room name.
- POSTs sanitized events to the server.
- Never sends Kakao messages directly.
- Never starts browser crawling directly.

`KakaoInboundClient` in `rider_agent`

- Sends `POST /v1/kakao/inbound-events`.
- Uses the existing Agent token in the `Authorization: Bearer` header.
- Retries transient network failures with bounded backoff.
- Marks an event processed only after server acceptance.

`KakaoInboundEventService` in `rider_server`

- Authenticates the Agent token.
- Validates event schema and fixed vocabularies.
- Computes or verifies `origin_event_key`.
- Maps Kakao room to one active Kakao channel and one active Baemin target.
- Applies global send gate, tenant/subscription gates, channel state, target state,
  per-room throttle, and per-target in-flight policy.
- Enqueues `RIDER_LOOKUP` jobs or enqueues a scoped unsupported/busy/failure reply.

`RiderLookupWorker` in `rider_agent`

- Handles `RIDER_LOOKUP` jobs.
- Reuses browser profile/config preparation patterns from existing crawl workers.
- Fetches Baemin delivery-history HTML/tables and parses rider rows directly.
- Produces a command result, not a normal snapshot.
- Does not touch protected Coupang login/email 2FA code in phase 1.

`KAKAO_SEND` path

- Sends final reply through the existing Agent `KakaoSenderWorker`.
- Payload includes the exact configured Kakao room name for the requesting channel.

### Data Flow

```text
KakaoTalk local DB
  -> Agent KakaoInboundWatcher
  -> shared parser finds !!강민기1234
  -> POST /v1/kakao/inbound-events
  -> Server validate/dedupe/map/gate
  -> enqueue RIDER_LOOKUP
  -> Agent RiderLookupWorker fetches Baemin rider table
  -> Server receives command result
  -> enqueue KAKAO_SEND scoped to requesting Kakao channel
  -> Agent KakaoSenderWorker sends final reply
```

## Kakao DB Scan Design

### Reader Interface

```python
class KakaoDbReader:
    def list_rooms(self) -> list[KakaoRoomRef]: ...
    def latest_messages(self, room: KakaoRoomRef, limit: int) -> list[KakaoMessageRef]: ...
```

`KakaoRoomRef` fields:

- `chat_id`
- `room_name`
- `chat_type`

`KakaoMessageRef` fields:

- `chat_id`
- `room_name`
- `log_id`
- `timestamp`
- `text`

The watcher may hold `text` in memory long enough to parse the command. It must
not put `text` into logs, heartbeat, job events, or server requests.

### Validated Fallback Reader

The validated research code reads only:

```sql
SELECT chatId, chatRoomTitle, lastChatMessage, lastLogId, lastUpdatedAt, type
FROM chatRoomList
WHERE lastChatMessage LIKE '%!!%'
  AND lastChatMessage IS NOT NULL
ORDER BY lastUpdatedAt DESC
```

This provides one latest visible message per room. If this fallback ships:

- Report health as degraded with `latest_window_size=1`.
- State clearly in Admin/health that messages can be missed.
- Do not claim latest-20 coverage.

### Latest-N Reader

Latest-20 requires confirmed `chatLogs_<id>.edb` schema. Implement only after
schema inspection and fake-DB tests prove:

- how to map `chatRoomList.chatId` to the right chat log file;
- which column is stable `log_id`;
- which timestamp column is monotonic enough for diagnostics;
- how text is encoded;
- how locked DB files are copied safely.

If latest-20 is confirmed, default `limit=20`. If the previous high-water mark
falls outside the latest-N window, report `gap_possible=true` and continue from
the newest accepted event to avoid floods.

### Startup and State

- On first start, initialize high-water marks to the newest observed log id per
  configured room.
- Do not process messages sent before watcher activation.
- Duplicate suppression is `(chat_id or room_name, log_id)`.
- If the server is unreachable, retry while the message remains visible in the
  scan window. This is best-effort; phase 1 does not promise durable recovery
  across Agent restarts.
- If durable local pending storage is added later, store it DPAPI-protected and
  never plaintext.

### Room Scope

- Scan only configured rooms.
- Do not scan every room looking for commands.
- Default accepted chat types are `DirectChat` and `MultiChat`.
- `PlusChat`, `OM`, and unknown chat types are ignored unless explicitly enabled
  for a room in a later design.

## Server Event API

Endpoint:

```http
POST /v1/kakao/inbound-events
Authorization: Bearer <agent-token>
```

Request body:

```json
{
  "source": "pc_kakao_db",
  "kakao_user_hash_digest": "sha256:...",
  "chat_id": "35189107907951",
  "room_name": "운영방",
  "last_log_id": "123456789",
  "message_timestamp": "2026-07-01T10:12:30+09:00",
  "detected_at": "2026-07-01T10:12:32+09:00",
  "command": {
    "type": "RIDER_CANCEL_RATE_LOOKUP",
    "name": "강민기",
    "phone_last4": "1234"
  }
}
```

Response:

```json
{
  "accepted": true,
  "duplicate": false,
  "job_id": "..."
}
```

Rejected response shape:

```json
{
  "accepted": false,
  "duplicate": false,
  "reason": "unknown_room"
}
```

Allowed rejection reasons are fixed lowercase strings such as:

- `unknown_room`
- `channel_inactive`
- `command_disabled`
- `target_unmapped`
- `unsupported_platform`
- `tenant_disabled`
- `sending_disabled`
- `rate_limited`
- `lookup_in_flight`
- `invalid_event`

Do not include raw message text or secret values in request, response, logs, or
audit entries.

## Deduplication

Event key:

```text
sha256(
  source + "\n" +
  kakao_user_hash_digest + "\n" +
  (chat_id or normalized_room_name) + "\n" +
  last_log_id + "\n" +
  command.type + "\n" +
  command.name + "\n" +
  command.phone_last4
)
```

Server behavior:

- Store `origin="kakao_inbound"` and `origin_event_key` in the `RIDER_LOOKUP`
  job payload.
- Before enqueue, query existing non-terminal or recently terminal jobs from the
  last 24 hours with the same `origin_event_key`.
- If found, return `accepted=true, duplicate=true`.
- If JSONB payload filtering is too slow, add an indexed `jobs.origin_event_key`
  column in a separate migration. Do not add a new table for phase 1.

Duplicate events do not create another lookup or another reply. A user can send
the command again as a new Kakao message to request a fresh lookup.

## Job Type and Capability

Add a new plain-string job type:

```text
RIDER_LOOKUP
```

This is preferred over overloading `CRAWL_BAEMIN` because lookup output is a
scoped command reply, not a normal snapshot that should enter fanout delivery.

Required implementation impact:

- Add `JOB_TYPE_RIDER_LOOKUP` to `src/rider_server/queue/states.py`.
- Add matching Agent capability to `src/rider_agent/heartbeat.py`.
- Keep `JOB_TYPES` and `DEFAULT_CAPABILITIES` synchronized.
- Update job vocabulary/autostart tests that assert capability equivalence.
- Add worker routing without changing the behavior of existing crawl/auth/Kakao
  jobs.

Initial payload:

```json
{
  "tenant_id": "...",
  "target_id": "...",
  "platform": "baemin",
  "platform_account_id": "...",
  "primary_url": "https://deliverycenter.baemin.com/delivery/history",
  "expected_display_name": "남구센터",
  "reply_channel_id": "...",
  "reply_messenger": "KAKAO",
  "reply_kakao_room_name": "운영방",
  "origin": "kakao_inbound",
  "origin_event_key": "sha256:...",
  "command": {
    "type": "RIDER_CANCEL_RATE_LOOKUP",
    "name": "강민기",
    "phone_last4": "1234"
  },
  "timeout_seconds": 60,
  "expires_at": "2026-07-01T01:13:32Z"
}
```

Lookup completion result:

```json
{
  "schema_version": 1,
  "result_type": "rider_lookup",
  "target_id": "...",
  "tenant_id": "...",
  "reply_channel_id": "...",
  "origin_event_key": "sha256:...",
  "reply_text": "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다.",
  "auth_state": "ACTIVE"
}
```

`reply_text` is intentionally stored only in job completion/result scope so the
server can enqueue `KAKAO_SEND`. Do not put it in heartbeat or free-text logs.

## Room and Target Mapping

Use existing `messenger_channels` and `delivery_rules` first.

Required data:

- tenant id
- messenger channel id
- `messenger = KAKAO`
- `kakao_room_name`
- optional `kakao_chat_id` additive column
- channel `state = ACTIVE`
- command trigger enabled flag
- exactly one enabled `delivery_rule` from the channel to a Baemin target

Mapping rules:

- A room can trigger only if the Kakao messenger channel is active.
- Phase 1 requires one active room/channel to map to exactly one active Baemin
  target through enabled delivery rules.
- Zero matching targets returns `target_unmapped`.
- More than one enabled target returns `target_unmapped` and does not crawl.
- Duplicate active room names under one tenant fail validation.
- If a stored `kakao_chat_id` exists and inbound `chat_id` conflicts, fail closed
  and require operator confirmation.
- If only room name is configured, bind `kakao_chat_id` on the first accepted
  inbound event after exact room-name match.

Schema changes should be additive columns, not new tables:

- `messenger_channels.kakao_chat_id nullable`
- `messenger_channels.command_trigger_enabled boolean default false`

If operators need many rooms mapped to one target or one room mapped to multiple
lookup targets, design that separately.

## Lookup Worker Design

The worker must not use aggregate snapshot JSON for lookup because it lacks
rider-level phone/name rows. It should:

1. Parse `RIDER_LOOKUP` payload.
2. Fail closed if `platform != "baemin"`.
3. Prepare browser profile/config using existing crawl-worker patterns.
4. Fetch Baemin delivery-history HTML/table rows.
5. Parse rows with `parse_baemin_delivery_history_html`.
6. Run shared matcher/calculator/renderer.
7. Complete the job with `result_type="rider_lookup"` and `reply_text`.

Prefer adding a small public row-level helper in `rider_crawl` over depending on
private functions such as `_fetch_baemin_delivery_history_tables`. The helper can
wrap the existing implementation and tests can lock the public behavior.

Do not write the lookup worker as a second copy of the current Telegram lookup
code. The current code proves behavior, but the new module should become the
shared contract.

## Server Completion and Reply

`RIDER_LOOKUP` completion must not enter normal snapshot ingest/fanout.

Server completion behavior:

- If job succeeds with `result_type="rider_lookup"`, enqueue exactly one
  `KAKAO_SEND` job to `reply_channel_id`.
- Apply global send gate and channel state again before enqueue.
- Payload to `KAKAO_SEND` uses existing accepted keys:

```json
{
  "kakao_room_name": "운영방",
  "message": "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다.",
  "origin": "kakao_inbound",
  "origin_event_key": "sha256:..."
}
```

- If lookup fails due to auth required, timeout, parser missing data, or profile
  unavailable, enqueue one scoped fixed failure reply only when the send gate is
  enabled:

```text
조회 중 오류가 발생했습니다.
```

- Do not include raw exception text in the reply.
- Do not retry ambiguous Kakao sends immediately. Existing `KakaoSenderWorker`
  behavior remains the source of truth.

## Security and Privacy

- Sanitize `docs/kakao_db` before committing it: remove DB key, user hash,
  MachineGuid, sys_uuid, device ID, MAC address, account email, and any room IDs
  or chat logs.
- Store Kakao DB key and local user hash only in Agent-local secure config,
  preferably DPAPI-protected on Windows.
- Send only `kakao_user_hash_digest` to the server.
- Do not log raw Kakao message text.
- Do not put command name/phone suffix in heartbeat/status.
- Redact command payloads in server audit and Agent logs.
- Never log, persist, or return OTPs, Coupang passwords, email app passwords, or
  plaintext secret values.
- Do not introduce callback-style subprocess execution from the research package.
- If optional SQLCipher support is packaged, keep it out of `rider_agent` direct
  imports and make missing dependency a disabled/degraded health state, not a
  crash.

## Protected Contract Notes

Phase 1 should not change protected Coupang runtime files. However, adding a new
Agent job route may require editing `src/rider_agent/worker_composition.py`, which
is protected because it also composes Coupang email 2FA.

Before changing any protected runtime file:

- trace the caller and payload path through crawl, auth, Kakao send, queue, and
  completion;
- add focused regression tests first;
- confirm the new `RIDER_LOOKUP` route does not change `AUTH_COUPANG_2FA`,
  `CRAWL_COUPANG`, or `KAKAO_SEND` routing order;
- run the protected test set from `AGENTS.md`;
- for any selector, wait, login, 2FA, CDP, or agent-routing changes, verify a
  real headed browser flow before claiming completion.

If implementation can avoid protected files, prefer that narrower path.

## Error Handling

Agent watcher:

- DB key missing/invalid: disabled health with fixed reason.
- Optional SQLCipher dependency missing: disabled health with fixed reason.
- DB schema mismatch: degraded or disabled health.
- Configured room not found: health warning; do not scan all rooms.
- Parser miss: ignore silently and increment a non-PII counter.
- Network failure to server: bounded retry; do not mark accepted locally.
- Gap detected: report `gap_possible=true`; continue without flooding old
  messages.

Server inbound:

- Unknown room/channel: reject with fixed reason.
- Duplicate event: accept as duplicate.
- Tenant/target/channel disabled: reject with fixed reason.
- Sending disabled: reject before crawling.
- Unsupported platform: enqueue scoped unsupported reply if sending is enabled.
- Existing in-flight lookup for same target: reject or enqueue one scoped busy
  reply, but do not enqueue another crawl.
- Rate limit: fixed reason, no crawl.

Lookup job:

- No match: final no-match reply.
- Multiple matches: deterministic ambiguity reply with redacted source labels.
- Auth required/user action pending: scoped fixed failure reply.
- Parser missing data: scoped fixed failure reply.
- Timeout/profile unavailable: scoped fixed failure reply.
- Coupang target: unsupported reply, no Coupang crawl.

Outbound send:

- Use existing `KAKAO_SEND`.
- Do not retry ambiguous/unconfirmed sends immediately.
- Do not fall back to another room or messenger.

## Tests

Shared command contract:

- Parses `!!강민기1234`.
- Parses command preceded by whitespace.
- Parses command followed by punctuation.
- Rejects `!!`, `!!1234`, `!!강민기12`, `!!강민기12345`,
  `!!hong1234`, and embedded `메모!!강민기1234`.
- Processes only the first valid token in a multi-token message.
- Does not run keyword auto-reply before lookup for valid commands.

Cancel-rate contract:

- Exact normalized name and phone suffix match.
- No match reply.
- Multiple match reply without full phones.
- Zero denominator produces `0`.
- Threshold at `4.0%` is risky.
- Renderer is messenger-neutral.

Kakao DB reader/watcher:

- Reads only configured rooms.
- Uses latest-one fallback with `latest_window_size=1` degraded health.
- Latest-20 reader test uses fake SQLCipher DB fixtures before enablement.
- Dedupes by `chat_id + log_id`.
- Initializes startup high-water marks without historical floods.
- Marks accepted only after server success.
- Reports `gap_possible` when previous high-water mark falls out of latest-N.
- Does not log raw message text or DB keys.

Server inbound:

- Requires Agent token.
- Rejects unknown/inactive room.
- Binds first matching `chat_id` when stored value is empty.
- Fails closed on stored `chat_id` conflict.
- Rejects zero or multiple mapped targets.
- Rejects non-Baemin targets with unsupported behavior.
- Idempotently handles duplicate `origin_event_key`.
- Enqueues exactly one `RIDER_LOOKUP` for an active mapped room.
- Applies sending gate, subscription gate, rate limit, and in-flight policy.

Lookup job:

- Routes `RIDER_LOOKUP` to lookup worker and preserves existing fallback for other
  jobs.
- Fetches/parses row-level Baemin rider table, not aggregate snapshot JSON.
- Completes with `result_type="rider_lookup"`.
- Does not modify protected Coupang login/2FA behavior.

Server completion/reply:

- `RIDER_LOOKUP` success creates exactly one `KAKAO_SEND` for the requesting
  channel.
- `RIDER_LOOKUP` does not enter snapshot ingest or delivery fanout.
- Failure replies are scoped and fixed-text.
- No raw room/message/command payload leaks in worker status/result/logs.

Regression suites likely affected:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_telegram_commands.py tests\test_baemin_parser.py tests\agent\test_kakao_sender.py tests\agent\test_job_loop.py tests\agent\test_agent_package.py tests\agent\test_autostart.py tests\server\test_job_vocab.py tests\server\test_jobs_api.py tests\server\test_queue_backend.py tests\server\test_channel_lifecycle.py -q
```

If protected files are touched, also run the protected test command from
`AGENTS.md`.

## Rollout Plan

### Phase 0: Research Quarantine and Spec Lock

- Sanitize or quarantine `docs/kakao_db` before commit.
- Keep research scripts out of runtime imports.
- Add the shared command contract tests first.

### Phase 1: Shared Command Core

- Add parser, DTOs, matcher, cancel-rate calculator, and renderer.
- Cover current Telegram behavior with compatibility tests before migration.
- Do not change Kakao DB, Agent jobs, or server endpoints in this phase.

### Phase 2: Kakao DB Reader and Agent Watcher

- Add a production DB-reader interface.
- Implement the latest-one fallback from `chatRoomList.lastChatMessage`.
- Gate the feature behind inbound Kakao enabled config/capability.
- Report degraded health for fallback mode.
- Add event POST client with Agent token auth.

### Phase 3: Server Inbound Event

- Add authenticated `/v1/kakao/inbound-events`.
- Add room/channel/target mapping using existing channel/rule data plus additive
  columns.
- Add dedupe using `origin_event_key` in job payload.
- Add rate limit and in-flight policy.

### Phase 4: Lookup Job and Reply

- Add `RIDER_LOOKUP` job type and Agent capability.
- Add row-level Baemin lookup worker.
- Add server completion handling that enqueues one scoped `KAKAO_SEND`.
- Verify no normal snapshot fanout is triggered.

### Phase 5: Latest-N Upgrade

- Inspect and test `chatLogs_<id>.edb` schema.
- Replace latest-one fallback with latest-20 reader when proven.
- Keep fallback available with degraded health.

### Phase 6: Telegram Convergence

- Move Telegram rider lookup onto the shared command service.
- Preserve or intentionally migrate legacy `!홍길동1234` behavior in a separately
  tested change.
- Keep Telegram transport polling/webhook concerns outside business rules.

### Phase 7: Coupang Evaluation

- Separately verify whether Coupang exposes stable rider-level cancellation data.
- If yes, design platform-specific extraction without changing protected Coupang
  login/email 2FA behavior.

## Implementation Defaults

These defaults are part of the design:

1. Phase 1-4 are Baemin-only.
2. One Kakao room maps to exactly one lookup target.
3. Latest-one fallback may ship only with degraded health.
4. One inbound message creates at most one lookup job.
5. Kakao command replies are final-only, no progress reply.
6. No new inbound-event table in the first implementation.
7. `RIDER_LOOKUP` is a new job/capability, not `CRAWL_BAEMIN`.
8. Missing SQLCipher support disables inbound Kakao detection instead of crashing.
