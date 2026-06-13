# Module Architecture

This project supports Baemin delivery-history crawling and Coupang Eats
performance crawling, and sends messages through a pluggable messenger
transport. The platform is selected per crawling tab. The runtime boundary is
organized around two extension points.

## Runtime Flow

`app.run_once` remains the orchestration entry point:

1. Acquire `RunLock`.
2. Crawl a `CrawlSnapshotResult` through `rider_crawl.platforms`, routed by
   `config.platform_name`.
3. Render the message through `message.render_current_screen_message`, which
   handles both result types.
4. Skip duplicate messages when `send_only_on_change` is enabled. The duplicate
   scope key includes `platform_name` and `peak_dashboard_url` so Baemin and
   Coupang states never collide.
5. Dispatch text through `rider_crawl.messengers`.

## Platform Boundary

`rider_crawl.platforms` owns crawler/platform selection.

- `platforms.base.PerformancePlatform` defines the crawler contract and returns
  `CrawlSnapshotResult` (`CurrentScreenSnapshot | PerformanceSnapshot`).
- `platforms.baemin.BaeminDeliveryPlatform` is the default implementation and
  returns `CurrentScreenSnapshot`.
- `platforms.coupang.CoupangEatsPlatform` returns `PerformanceSnapshot`, built
  from the two Coupang pages (`rider-performance` + `peak-dashboard`). Its
  crawler/parser live under `platforms/coupang/` to keep Coupang-specific
  navigation and parsing out of the larger Baemin `crawler.py`/`parser.py`.
- The legacy `crawler.py` and `parser.py` modules stay in place for existing
  Baemin imports and tests.

`platforms.crawl_snapshot(config, platform_name=...)` reads `config.platform_name`
when the caller does not pass an explicit `platform_name`. When another delivery
platform is added, create a new platform adapter that returns a
`CrawlSnapshotResult`, register it with `register_platform`, then add
configuration selection only where needed.

## Messenger Boundary

`rider_crawl.messengers` owns outgoing message transport selection.

- `messengers.base.Messenger` defines the text sending contract.
- `messengers.telegram.TelegramMessenger` is the default implementation.
- `messengers.kakao.KakaoMessenger` remains available for the legacy
  KakaoTalk PC app automation path.
- The legacy `sender.py` module stays in place for KakaoTalk UI automation and
  existing imports.

When Discord or another messenger is added, create a messenger
adapter that implements `send_text`, register it with `register_messenger`, and
then add settings/env selection without changing `app.run_once`.

## Server Domain Boundary (Epic 2–3)

`rider_server` is a new top-level package holding the platform-neutral domain
and service layer introduced in Epic 2 and grown in Epic 3 (the `run_once` split
and the collect/render/dispatch pipeline). It is pure and dependency-free (no
FastAPI, SQLAlchemy, or async); it may import `rider_crawl`, but `rider_crawl`
never depends on it. Throughout Epic 3 `src/rider_crawl/` was changed by zero
lines — every new behaviour is additive inside `rider_server`.

- `rider_server.domain` defines 11 frozen-dataclass models — Epic 2 added
  `Tenant`, `Subscription`, `PlatformAccount`, `MonitoringTarget`,
  `BrowserProfile`, `MessengerChannel`, `DeliveryRule`, `SecretRef`; Epic 3 added
  `Snapshot` (9th, normalized crawl result), `Message` (10th, rendered message
  with stable `text_hash`), and `DeliveryLog` (11th, dispatch result / dedup
  record). State-machine and support enums include `CustomerLifecycleState`,
  `SubscriptionStatus`, `BaeminAuthState`, `Platform`, `Messenger`,
  `SecretStorageClass`, `SnapshotQualityState`, `DeliveryStatus`, and
  `FailureCategory` (Epic 3 added the last three). Credentials are referenced via
  `SecretRef`, never stored as plaintext.
- `rider_server.services` holds pure, deterministic, synchronous policy/transform
  logic (no FastAPI/SQLAlchemy/async; identifiers and timestamps are
  caller-injected, so there are no internal `datetime.now()`/`uuid4()` calls):
  - `SubscriptionGate` (Epic 2) decides whether new crawl/dispatch jobs are
    allowed from `SubscriptionStatus`, holds undelivered dispatches on suspend,
    and is fail-closed (unknown states blocked, succeeded dispatches never
    re-sent).
  - `CrawlService` / `MessageRenderService` / `DispatchService` (Story 3.1) are
    the `run_once` collect/render/dispatch split, each independently callable
    with injectable crawler/sender adapters. The default adapters delegate to the
    same `rider_crawl` building blocks, so the composed result reproduces
    `run_once` (`message`/`sent`/`message_hash`); `app.run_once` itself is
    untouched and stays the legacy compatibility path.
  - `SnapshotNormalizer` (Story 3.2) wraps parser output into a normalized
    `Snapshot` and is fail-closed: missing required data raises
    `MissingSnapshotDataError` (a `MissingPerformanceDataError` subclass) instead
    of filling defaults, so a bad/partial snapshot never produces a message.
  - `MessageRenderService.render_message` (Story 3.3) returns a `Message` with a
    stable `text_hash` equal to the dispatch `message_hash`. `template_version`
    is a server-side constant (`baemin.realtime.v1` / `coupang.realtime.v1`); it
    was deliberately *not* added to `rider_crawl/message.py`, keeping the renderer
    reused byte-for-byte.
  - `DispatchFanoutService` (Story 3.4) fans one `Message` out to a per-channel
    `DispatchJob` for each active `DeliveryRule`, with channel isolation (one
    channel's failure does not invalidate others) and a fail-closed
    `UnknownChannelError` on dangling channel references.
  - `IdempotentDeliveryService` (`idempotency.py`, Story 3.5) builds the 5-field
    dedup key (`target_id + channel_id + collected_at + template_version +
    message_hash`) and uses insert-then-send so a crash after sending cannot
    cause a re-send; it records a `DeliveryLog` (`SENT` / `DUPLICATE_BLOCKED`).
  - `DeliveryFailurePolicy` (Story 3.6) classifies failures into `FailureCategory`
    and decides retry vs. human intervention with deterministic backoff (no fixed
    5s / infinite retry); `AUTH_REQUIRED` / target-validation failures go to
    `HELD` rather than retrying forever.
  - `CentralTelegramSender` (Story 3.7) is a central, send-only Telegram adapter
    that reuses the legacy `send_telegram_text` and never imports `getUpdates` /
    the poller, removing per-Agent polling of a shared bot token.
- `rider_server.migration.runner` (Epic 2) orchestrates the deterministic
  migration of existing active tabs (`runtime/state/ui_settings.json`) into the
  ID-based domain models: it backs up the original first and stops at `MAPPED`,
  never activating a target before operator approval.
  `rider_server.migration.cutover` (Story 3.8) adds the dry-run/cutover layer:
  it runs the new path with no sender, compares the rendered hash against the
  `MigrationSeed` baseline, blocks activation until an operator approves a diff,
  guards against old/new dual active send (`DualSendError`), and on rollback
  disables the new rule while preserving the dedup logs.

DB/ORM/Alembic, Pydantic schemas, and runtime wiring for this layer remain out of
Epic 2–3 scope (Epic 5); through Epic 3 `rider_server` is defined and tested but
not yet wired into a running process (the UI still calls `run_once`).

## Local Agent Boundary (Epic 4)

`rider_agent` is the third top-level package — the Windows Local Agent runtime,
launched with `python -m rider_agent`. Epic 4 added it as a brand-new runtime
**without changing a single line of `rider_crawl` or `rider_server`** (verified by
empty `git diff -w` over both). Its job is to run the real Baemin/Coupang
collection and KakaoTalk sending on an operator PC while talking to the central
server (Epic 5) outbound-only.

- **Dependency direction is strictly one-way.** `rider_agent` imports `rider_crawl`
  only — through a single re-export chokepoint, `reuse.py` (crawler / parser /
  renderer / Gmail 2FA / KakaoTalk sender). It must **never import `rider_server`**;
  where it needs a server-side enum value (e.g. `BaeminAuthState`,
  `FailureCategory`) it mirrors the value as a plain-string constant rather than
  importing it. The dependency edges `rider_crawl → rider_agent` and
  `rider_agent → rider_server` are both zero.
- **Sync runtime, stdlib-only.** Agent code stays synchronous (no `asyncio`,
  unlike the Cloud async boundary) and adds **no new third-party dependency**:
  HTTPS via stdlib `urllib`, Windows DPAPI via stdlib `ctypes`/crypt32, periodic
  loops via `threading`/`time`, port allocation via `socket`. `pyproject.toml`
  stays at its frozen 9 dependencies (`playwright==1.60.0`, `crawl4ai==0.8.7`).
- **One AST guard locks the whole package.** `tests/agent/test_agent_package.py`
  (Story 4.1) `rglob`s `src/rider_agent/**/*.py` and asserts: sync-only, third-party
  import root is `rider_crawl`, the one-way import edges above, and the 9-dependency
  pin. Every later module inherits this guard automatically — new modules need no
  new guard.

The runtime is composed of additive primitives, each delivered by one story:

- `registration.py` + `secure_store.py` (Stories 4.2): one-time registration code →
  `agent_id`/`agent_token`, stored via `DpapiSecretStore` (Windows DPAPI); the token
  is never written in plaintext to logs/config/disk (`AgentIdentity.__repr__` masks it).
- `heartbeat.py` (Story 4.3): periodic 30–60s report (`metrics`, `capabilities`,
  `active_jobs`, `kakao_status`, `browser_profiles`); the token rides only in the
  `Authorization: Bearer` header, never the body. Capabilities are plain-string
  constants (e.g. `CRAWL_BAEMIN`, `CRAWL_COUPANG`, `AUTH_CHECK`, `OPEN_AUTH_BROWSER`,
  `KAKAO_SEND`, `CAPTURE_DIAGNOSTIC`) so adding job types never breaks a count lock.
- `job_loop.py` (Story 4.4): outbound HTTPS `claim` / `complete` / `events` plus the
  `run_agent` bootstrap. The Agent only *cooperatively* records the lease and
  self-checks before completing; lease issuance, single-claim, extension, stale
  sweep and reassignment are enforced server-side (Epic 5). Job events/results carry
  only `message_redacted` / `error_message_redacted`.
- `browser_profile.py` (Story 4.5): `BrowserProfileManager` isolates Chrome
  profile + CDP port per target and is fail-closed — duplicate port/profile, a busy
  CDP endpoint, or an expected-center mismatch (`TARGET_VALIDATION_FAILURE`) stops the
  work before any message is built. It reuses `rider_crawl`'s existing
  `prepare_chrome` / center-validation, reimplementing nothing.
- `workers/kakao_sender.py` (Story 4.6): `KakaoSenderWorker` serializes KakaoTalk
  sends through a single-consumer `queue.Queue` (FIFO) so one session never types
  into two rooms in parallel. It reuses `send_kakao_text`'s exact-room verification,
  never falls back to another room, and — because free-text `redact()` does not mask
  operational identifiers — never puts a raw room name into a failure message
  (fixed reason string only).
- `autostart.py` (Story 4.7): registers Agent auto-start after reboot via a Startup
  folder `.cmd` (default, no admin rights) or Task Scheduler (`/sc ONLOGON /it`,
  alternative). KakaoTalk work is gated to an interactive Windows session
  (Session 0 service mode is refused, fail-closed).
- `auth/baemin_auth.py` (Story 4.8) vs `auth/coupang_gmail_2fa.py` (Story 4.9) sit
  in the same subpackage with **opposite policies**. Baemin is human-in-the-loop:
  it detects `AUTH_REQUIRED` (without ever mapping a parser error such as
  `MissingPerformanceDataError` to auth) and never acquires, inputs, or bypasses an
  OTP — an AST import-edge guard forbids Gmail/`pyautogui` imports in that module.
  Coupang Gmail 2FA *does* auto-recover: it stores an OAuth token per
  `mailbox_id` (opaque ref, server keeps the ref only), serializes same-mailbox
  reads through `MailboxLockRegistry`, and uses `dataclasses.replace` to give each
  mailbox a distinct token-file path so customers never share a token.

Server-side job creation/queue/lease enforcement, the Admin UI, the
`workers/crawl_worker.py` collection executor, and real OS/browser bindings for the
auth probes are all Epic 5 — through Epic 4 the Agent is verified against server
stubs/mocks and injected seams, not a live server.

## Compatibility Notes

- Existing public modules (`app.py`, `crawler.py`, `parser.py`, `sender.py`,
  `message.py`, `ui.py`, `ui_settings.py`) are intentionally preserved.
- Epic 2 added two new modules inside `rider_crawl` alongside the preserved set:
  `secret_store.py` (a secret-store seam so `ui_settings.json` keeps only opaque
  `*_ref` handles instead of plaintext) and `log_rotation.py` (size-based
  rotation for `run_errors.log` / `kakao_diagnostics.log`).
- The default platform is Baemin, so existing setups keep crawling Baemin unless
  a tab is explicitly switched to Coupang.
- The default behavior is Baemin crawling plus Telegram Bot API sending.
- The Telegram rider lookup command is Baemin-only; on a Coupang tab it replies
  that the lookup is only supported for Baemin.
- Build output directories (`build/`, `dist/`) should not be modified as part
  of architecture work.
