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

## Compatibility Notes

- Existing public modules (`app.py`, `crawler.py`, `parser.py`, `sender.py`,
  `message.py`, `ui.py`, `ui_settings.py`) are intentionally preserved.
- The default platform is Baemin, so existing setups keep crawling Baemin unless
  a tab is explicitly switched to Coupang.
- The default behavior is Baemin crawling plus Telegram Bot API sending.
- The Telegram rider lookup command is Baemin-only; on a Coupang tab it replies
  that the lookup is only supported for Baemin.
- Build output directories (`build/`, `dist/`) should not be modified as part
  of architecture work.
