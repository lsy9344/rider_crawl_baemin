from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .config import AppConfig
from .lock import RunLock
from .message import render_current_screen_message
from .models import CrawlSnapshotResult


@dataclass(frozen=True)
class RunResult:
    message: str
    sent: bool
    skipped: bool
    message_hash: str


def run_once(
    config: AppConfig,
    *,
    crawl_snapshot: Callable[[AppConfig], CrawlSnapshotResult] | None = None,
    send_message: Callable[[AppConfig, str], None] | None = None,
) -> RunResult:
    crawl = crawl_snapshot or _crawl_snapshot
    sender = send_message or _send_message

    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.mkdir(parents=True, exist_ok=True)

    with RunLock(_run_lock_path(config), stale_timeout_seconds=config.run_lock_timeout_seconds):
        snapshot = crawl(config)
        # 메시지 라벨은 탭의 '센터명'(baemin_center_name; 쿠팡 탭은 기대 센터/상점명으로
        # 재사용)을 쓴다. 센터명이 비어 있으면 기존처럼 크롤링 탭 이름으로 대체한다.
        source_label = config.baemin_center_name.strip() or config.crawl_name
        message = render_current_screen_message(snapshot, source_label=source_label)
        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()

        if config.send_only_on_change and _is_duplicate(config, message_hash):
            return RunResult(message=message, sent=False, skipped=True, message_hash=message_hash)

        if config.send_enabled:
            sender(config, message)
            _write_last_hash(config, message_hash)
            return RunResult(message=message, sent=True, skipped=False, message_hash=message_hash)

        return RunResult(message=message, sent=False, skipped=False, message_hash=message_hash)


def _is_duplicate(config: AppConfig, message_hash: str) -> bool:
    path = _last_message_hash_path(config)
    return path.exists() and path.read_text(encoding="utf-8").strip() == message_hash


def _write_last_hash(config: AppConfig, message_hash: str) -> None:
    _last_message_hash_path(config).write_text(message_hash, encoding="utf-8")


def _last_message_hash_path(config: AppConfig) -> Path:
    scope_hash = hashlib.sha256(_message_scope_key(config).encode("utf-8")).hexdigest()[:16]
    return config.state_dir / f"last_message.{scope_hash}.sha256"


def _run_lock_path(config: AppConfig) -> Path:
    scope_hash = hashlib.sha256(_run_scope_key(config).encode("utf-8")).hexdigest()[:16]
    return config.runtime_dir / "state" / "run_locks" / f"run.{scope_hash}.lock"


def _run_scope_key(config: AppConfig) -> str:
    browser_mode = config.browser_mode.strip()
    if browser_mode == "cdp":
        return "\n".join([browser_mode, _cdp_endpoint_key(config.cdp_url)])

    return "\n".join(
        [
            browser_mode,
            str(config.browser_user_data_dir.expanduser().resolve()).casefold(),
        ]
    )


def _cdp_endpoint_key(cdp_url: str) -> str:
    value = cdp_url.strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    if host == "localhost":
        host = "127.0.0.1"
    if parsed.port is None:
        return value.casefold()
    scheme = (parsed.scheme or "http").casefold()
    return f"{scheme}://{host}:{parsed.port}"


def _message_scope_key(config: AppConfig) -> str:
    messenger_name = config.messenger_name.strip() or "telegram"
    parts = [
        messenger_name,
        config.platform_name.strip() or "baemin",
        config.coupang_eats_url.strip(),
        config.peak_dashboard_url.strip(),
        config.baemin_center_name.strip(),
        config.baemin_center_id.strip(),
    ]
    if messenger_name == "telegram":
        parts.extend(
            [
                config.telegram_bot_token.strip(),
                config.telegram_chat_id.strip(),
                _normalize_telegram_thread_id(config.telegram_message_thread_id),
            ]
        )
    elif messenger_name == "kakao":
        parts.append(config.kakao_chat_name.strip())
    return "\n".join(parts)


def _normalize_telegram_thread_id(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        return str(int(value))
    except ValueError:
        return value


def _crawl_snapshot(config: AppConfig) -> CrawlSnapshotResult:
    from .platforms import crawl_snapshot

    return crawl_snapshot(config, platform_name=config.platform_name)


def _send_message(config: AppConfig, message: str) -> None:
    from .messengers import dispatch_text_message

    dispatch_text_message(config, message)
