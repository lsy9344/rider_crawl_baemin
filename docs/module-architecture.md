# Module Architecture

This project keeps the current Baemin crawling behavior and sends messages
through a pluggable messenger transport. The runtime boundary is organized
around two extension points.

## Runtime Flow

`app.run_once` remains the orchestration entry point:

1. Acquire `RunLock`.
2. Crawl a `CurrentScreenSnapshot` through `rider_crawl.platforms`.
3. Render the existing message through `message.render_current_screen_message`.
4. Skip duplicate messages when `send_only_on_change` is enabled.
5. Dispatch text through `rider_crawl.messengers`.

## Platform Boundary

`rider_crawl.platforms` owns crawler/platform selection.

- `platforms.base.PerformancePlatform` defines the crawler contract.
- `platforms.baemin.BaeminDeliveryPlatform` is the default implementation.
- The legacy `crawler.py` and `parser.py` modules stay in place for existing
  imports and tests.

When another delivery platform is added, create a new platform adapter that
returns `CurrentScreenSnapshot`, register it with `register_platform`, then add
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
- The default behavior is Baemin crawling plus Telegram Bot API sending.
- Build output directories (`build/`, `dist/`) should not be modified as part
  of architecture work.
