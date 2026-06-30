# Kakao Inbound Rider Lookup Design

Date: 2026-07-01
Status: Draft for review

## Goal

Build a command-triggered rider lookup flow for configured KakaoTalk rooms, with the
same command contract later usable from Telegram.

The first supported command is a cancellation-rate lookup:

```text
!!홍길동1234
```

The system watches only operator-configured KakaoTalk rooms. When a recent message
contains a valid command token, it triggers a crawl, computes the rider's cancel
rate from the crawled data, and sends a scoped reply back to the requesting room.

Existing Telegram parser, cancel-rate logic, and reply rendering are not treated as
trusted production contracts for this work. They can inform implementation, but the
new command parser, calculation, and response renderer must be specified and tested
as a new shared command contract.

## Non-Goals

- Do not replace the existing production Kakao sender with `docs/kakao_db/kakao_sender.py`.
- Do not let the Kakao DB monitor directly crawl or directly send Kakao messages.
- Do not change the protected Coupang login/email 2FA flow.
- Do not build a generic KakaoTalk bot platform.
- Do not guarantee recovery of every missed KakaoTalk message while using a latest-N
  scan. The latest-N scan is a best-effort trigger source.

## Decisions

1. Keep the existing outbound Kakao send path.
   `KAKAO_SEND` jobs must continue through `KakaoSenderWorker` and
   `send_kakao_text`, because that path already performs exact-room validation,
   input verification, FIFO serialization, and fail-closed error mapping.

2. Use `docs/kakao_db` only as an inbound detection prototype.
   Its DB-reading idea is useful, but its callback and sender are not production
   boundaries.

3. Agent detects, server decides.
   The Agent PC is the only place that can inspect local KakaoTalk DB files, so it
   should detect candidate commands locally. It should then POST a sanitized
   inbound event to the server. The server validates, dedupes, maps room to target,
   enqueues crawl/lookup work, and creates the final `KAKAO_SEND` reply.

4. Treat Kakao and Telegram commands as one shared product contract.
   Kakao inbound and future Telegram inbound should call the same parser,
   calculation, and renderer. Telegram-specific code must not own the business
   rule.

5. Reply only to the requesting room/channel.
   Command-triggered responses are not normal snapshot fanout. A command result
   must be scoped to the inbound channel that requested it.

## Command Contract

### Detection Token

The first-stage DB filter may use a broad contains filter equivalent to:

```sql
LIKE '%!!%'
```

That broad filter is only a cheap candidate filter. A message is actionable only
if it contains a valid rider lookup command token.

### Valid Command Token

Recommended regex:

```python
r"(?<!\S)!!(?P<name>[가-힣]{1,20})(?P<phone_last4>\d{4})(?=$|\s|[.,!?;:)\]\}〉》」』”’…])"
```

Rules:

- Prefix is exactly `!!`.
- `name` is one or more Korean Hangul syllables, capped at 20 characters.
- `phone_last4` is exactly four ASCII digits.
- The token starts at message start or after whitespace.
- The token ends at message end, whitespace, or common punctuation.
- `!!`, `!!1234`, `!!홍길동12`, `!!hong1234`, and `!!홍길동12345` are ignored.

Examples:

| Message | Result |
| --- | --- |
| `!!홍길동1234` | match |
| `확인 !!홍길동1234` | match |
| `!!김1234 부탁` | match |
| `!!1234` | ignore |
| `!!홍길동12` | ignore |
| `!!홍길동12345` | ignore |
| `테스트!!홍길동1234` | ignore |

If product usage needs commands embedded inside words later, loosen the leading
boundary in a separate tested change.

If one message contains multiple valid command tokens, phase 1 processes only the
first token in left-to-right order and ignores the rest. This keeps dedupe and
reply behavior one-message-to-one-job.

### Parsed Command Payload

```json
{
  "type": "RIDER_CANCEL_RATE_LOOKUP",
  "name": "홍길동",
  "phone_last4": "1234"
}
```

The raw message should not be stored in normal logs. Structured payloads containing
name and phone suffix are sensitive operational data and must be excluded from
status, heartbeat, and free-text logs.

## Cancel-Rate Contract

This is a new shared contract, even if similar code already exists.

Input is a platform-normalized rider row with at least:

- rider name
- phone number or phone suffix
- completed count
- rejected count
- rider-attributable delivery cancel count
- assignment cancel count

Matching rule:

- Match exact normalized Hangul name.
- Match phone last 4 digits exactly.
- If no rows match, reply `해당 라이더를 찾지 못했습니다.`
- If multiple rows match, reply with an ambiguity message listing only redacted
  source labels, not full phone numbers.

Calculation:

```text
total_cancel = assignment_cancel + rider_delivery_cancel
denominator = completed + rejected + total_cancel
cancel_rate = 0 if denominator == 0 else round(total_cancel / denominator * 100, 1)
```

Initial risk threshold:

```text
cancel_rate >= 4.0 -> 위험합니다.
cancel_rate < 4.0  -> 정상 범위입니다.
```

Renderer:

```text
홍길동1234
취소율 3.8%, 취소 2개
정상 범위입니다.
```

The renderer must be platform-neutral. It should not mention Telegram or Kakao.

## Architecture

### Components

`KakaoInboundWatcher` in `rider_agent`

- Runs only on Windows interactive Agent nodes with inbound Kakao enabled.
- Reads configured Kakao DB files through a small `KakaoDbReader` abstraction.
- Filters to configured room names and, when available, stable Kakao `chatId`.
- Scans latest N messages per room, default 20.
- Applies the shared command parser.
- Persists local high-water marks per `chatId`.
- POSTs sanitized events to server.
- Never sends messages directly.
- Never starts browser crawling directly.

`KakaoInboundClient` in `rider_agent`

- Sends `POST /v1/kakao/inbound-events`.
- Uses the existing Agent token.
- Retries transient network failures with bounded backoff.
- Marks local events processed only after server acceptance.

`KakaoInboundEventService` in `rider_server`

- Authenticates Agent token.
- Validates event schema.
- Dedupes by event key.
- Maps `room_name` and `chat_id` to an active Kakao messenger channel.
- Resolves the target/platform allowed for that channel.
- Applies tenant send gates, subscription gates, per-target concurrency, and
  cooldown/rate-limit policy.
- Enqueues lookup work.

`RiderLookupCommandService`

- Owns parser, matching, cancel-rate calculation, and renderer.
- Is shared by Kakao inbound and future Telegram inbound.
- Has no KakaoTalk, Telegram, browser, or server transport dependency.

`RiderLookupWorker`

- Executes command-triggered lookup work.
- Reuses existing browser/profile/crawl seams where possible.
- Produces a command result, not a normal fanout snapshot message.
- Must not touch protected Coupang login/2FA files unless a later scoped design
  and protected test plan explicitly approves it.

`KAKAO_SEND` path

- Sends final reply using the existing Agent Kakao sender worker.
- Payload must include the exact configured room name for the requesting channel.

### Data Flow

```text
KakaoTalk local DB
  -> Agent KakaoInboundWatcher
  -> parse !!한글+숫자4 command
  -> POST /v1/kakao/inbound-events
  -> Server validate/dedupe/map/gate
  -> enqueue RIDER_LOOKUP work
  -> Agent executes crawl/lookup
  -> Server records command result
  -> enqueue KAKAO_SEND scoped to requesting channel
  -> Agent KakaoSenderWorker sends reply
```

## Latest-N Kakao DB Scan

The current `docs/kakao_db/kakao_monitor.py` reads `chatRoomList.lastChatMessage`,
which provides the latest visible message per room, not latest 20 messages per
room. For this design, latest 20 requires a reader that can tail per-room chat log
storage.

Implement the DB reader behind an interface:

```python
class KakaoDbReader:
    def list_rooms(self) -> list[KakaoRoomRef]: ...
    def latest_messages(self, room: KakaoRoomRef, limit: int) -> list[KakaoMessageRef]: ...
```

Phase 1 target reader:

- Resolve room refs from `chatRoomList`.
- For each allowlisted room, read latest 20 rows from the corresponding chat log
  DB if the schema is confirmed.
- Sort by Kakao log id or timestamp descending.
- Return only fields needed by the watcher: `chat_id`, `room_name`, `log_id`,
  `timestamp`, and message text.

Fallback reader:

- If per-room chat log schema cannot be confirmed quickly, use
  `chatRoomList.lastChatMessage` as a one-message fallback.
- The fallback must report degraded health: `latest_window_size=1`.
- The UI/Admin status must make clear that this mode can miss messages.

Startup behavior:

- On first start, initialize high-water marks to the newest observed log id per
  configured room.
- Do not process historical messages from before watcher activation.

Gap behavior:

- If the previous high-water log id is no longer present within the latest-N
  window, mark `gap_possible=true` in Agent status/event diagnostics.
- Continue from the newest accepted event to avoid floods.

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
  "kakao_account_hash": "redacted-or-stable-local-id",
  "chat_id": "35189107907951",
  "room_name": "운영방A",
  "last_log_id": "123456789",
  "message_timestamp": "2026-07-01T10:12:30+09:00",
  "detected_at": "2026-07-01T10:12:32+09:00",
  "command": {
    "type": "RIDER_CANCEL_RATE_LOOKUP",
    "name": "홍길동",
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

Do not include raw message text in the request unless future requirements prove it
is needed. If raw text is added later, it must be redacted from logs and excluded
from heartbeat/status payloads.

## Deduplication Design

Preferred 1차 design: avoid a new DB table.

Reason: current architecture documentation records a locked 14-table server model.
A new table is not impossible, but it increases migration and architecture-contract
cost. Start with additive use of existing queue/job storage.

Event key:

```text
sha256(source + kakao_account_hash + chat_id + last_log_id + command_type)
```

Server behavior:

- Include `origin="kakao_inbound"` and `origin_event_key` in the lookup job
  payload.
- Before enqueue, check existing non-terminal or recently terminal jobs from the
  last 24 hours with the same `origin_event_key`.
- If one exists, return duplicate.
- Longer-term, add an indexed additive column to `jobs` for `origin_event_key` if
  payload JSON filtering is too slow.

Escalation option:

- If command volume grows or auditability becomes a hard requirement, add a
  dedicated inbound-event table in a separate migration design. That should be a
  later decision, not the first implementation.

## Job Type Decision

Do not overload normal snapshot fanout for command replies.

Recommended new job type:

```text
RIDER_LOOKUP
```

Rationale:

- The desired output is a scoped command reply, not the normal monitoring message.
- Reusing `CRAWL_BAEMIN` would complete into snapshot ingest and existing delivery
  rules, which can fan out too broadly.
- A command job can carry `reply_channel_id`, `origin_event_key`, and parsed
  command payload explicitly.
- The new type should route through existing queue claim/complete mechanics
  without changing queue backend semantics.

Initial payload:

```json
{
  "tenant_id": "...",
  "target_id": "...",
  "platform": "baemin",
  "reply_channel_id": "...",
  "origin": "kakao_inbound",
  "origin_event_key": "...",
  "command": {
    "type": "RIDER_CANCEL_RATE_LOOKUP",
    "name": "홍길동",
    "phone_last4": "1234"
  },
  "timeout_seconds": 60
}
```

Baemin is the first supported platform because the cancellation-rate source is a
Baemin rider table. Coupang support requires separate verification of a stable
rider-level source and must not modify protected Coupang login/2FA behavior in the
first implementation. Phase 1 should reject command-trigger enablement for Coupang
targets in Admin/UI validation. If an event still reaches a Coupang target because
of stale configuration, the server sends a scoped unsupported reply instead of
crawling Coupang as if it were Baemin.

## Room and Target Mapping

The user/operator provides Kakao room names.

Server should store or derive:

- tenant
- Kakao channel id
- Kakao room name
- optional stable Kakao `chatId`
- target id
- platform
- enabled flag
- command trigger enabled flag

Mapping rules:

- A room can trigger only if its messenger channel is active.
- A room should map to exactly one target for command lookup in phase 1.
- Duplicate active room names under one tenant fail validation.
- If a room name matches but `chatId` conflicts with the stored value, fail closed
  and require operator confirmation.
- If only room name is available on first registration, bind `chatId` on first
  verified inbound event.

Default throttles:

- At most one in-flight `RIDER_LOOKUP` per target.
- At most one accepted command event per room every 5 seconds.
- Duplicate event keys are always treated as duplicates regardless of throttle.

## Error Handling

Agent watcher:

- DB key missing or invalid: disabled health with fixed reason, no secret logging.
- Kakao DB schema mismatch: disabled or degraded health.
- Room not found: warn in health, do not scan all rooms.
- Network failure to server: retry; do not mark event processed until accepted.
- Parser miss: ignore silently or debug-count only.

Server:

- Unknown room/channel: reject accepted=false with fixed reason.
- Duplicate event: accepted=true duplicate=true.
- Tenant/target disabled: accepted=false with fixed reason.
- Existing in-flight lookup for same target: either coalesce or rate-limit.
- Crawl failure: send scoped failure reply if safe, e.g. `조회 중 오류가 발생했습니다.`
- No match: send `해당 라이더를 찾지 못했습니다.`
- Multiple matches: send a deterministic ambiguity reply without full PII.

Outbound send:

- Use existing `KAKAO_SEND` job path.
- Do not retry ambiguous Kakao sends immediately.
- Do not fall back to another room or Telegram channel.

## Security and Privacy

- Do not commit real Kakao DB keys, user hashes, room IDs, memory dumps, chat logs,
  phone numbers, passwords, OTPs, or app passwords.
- Store Kakao DB key and local user hash in Agent-local secret/config storage,
  preferably DPAPI-protected on Windows.
- Do not log raw message text.
- Do not put name/phone suffix in heartbeat/status.
- Redact command payloads in server audit and Agent logs.
- Callback-style subprocess execution from `docs/kakao_db` is not allowed in the
  production path.

## Tests

Shared command contract:

- Parses `!!홍길동1234`.
- Parses command preceded by whitespace.
- Rejects `!!`, `!!1234`, `!!홍길동12`, `!!홍길동12345`, `!!hong1234`.
- Does not treat a bare `!!` as a trigger.
- Does not run keyword auto-reply before command lookup.

Cancel-rate contract:

- Exact name and phone suffix match.
- No match reply.
- Multiple match reply.
- Zero denominator produces 0%.
- Threshold at 4.0% is risky.
- Renderer output is stable and messenger-neutral.

Agent watcher:

- Scans only configured rooms.
- Limits to latest 20 messages per room.
- Dedupes `chatId + lastLogId`.
- Initializes startup high-water marks without historical floods.
- Marks event processed only after server acceptance.
- Reports degraded health for one-message fallback mode.
- Reports `gap_possible` when previous high-water mark falls out of latest-N.

Server inbound:

- Requires Agent token.
- Rejects unknown room/channel.
- Idempotently handles duplicate `origin_event_key`.
- Enqueues exactly one `RIDER_LOOKUP` for an active mapped room.
- Includes `reply_channel_id`.
- Does not enqueue when tenant/target/channel is disabled.
- Applies rate limit/in-flight policy.

Lookup job:

- Routes `RIDER_LOOKUP` to the lookup worker and other jobs to existing fallback.
- Produces scoped reply payload, not normal snapshot fanout.
- For Baemin, uses browser/profile/crawl seams without changing protected Coupang files.

Kakao send:

- Final reply becomes one `KAKAO_SEND` job for the requesting channel.
- No raw room/message leaks in worker status/result/logs.

## Rollout Plan

Phase 0: Documentation and cleanup

- Keep `docs/kakao_db` as research material only.
- Remove or replace any real DB key/user hash before committing those docs.
- Do not wire callback scripts into runtime.

Phase 1: Shared command core

- Implement parser, cancel-rate calculator, matcher, and renderer as a new shared
  module.
- Refactor Telegram later to use the shared module; do not depend on the current
  Telegram implementation for correctness.

Phase 2: Agent inbound watcher

- Add Kakao DB reader interface.
- Implement latest-20 reader if per-room chat log schema is confirmed.
- Otherwise ship one-message fallback only behind a degraded-health flag.
- Add Agent event POST client.

Phase 3: Server inbound event and lookup job

- Add authenticated endpoint.
- Add event dedupe using `origin_event_key`.
- Add `RIDER_LOOKUP` job type and worker routing.
- Add scoped `KAKAO_SEND` reply creation.

Phase 4: Telegram convergence

- Move Telegram command handling onto the shared command service.
- Keep Telegram transport-specific update polling separate from business rules.

Phase 5: Coupang evaluation

- Separately verify whether Coupang has the needed rider-level cancellation data.
- If yes, design platform-specific lookup extraction without changing protected
  login/2FA behavior.

## Review Defaults

These defaults are part of this draft and should be changed only if product
requirements differ:

1. Phase 1 is Baemin-only.
2. One Kakao room maps to exactly one lookup target.
3. Latest-20 scanning is accepted as best-effort, not guaranteed ingestion.
4. One inbound message creates at most one lookup job.
