from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from .config import AppConfig
from .lock import RunLock
from .message import render_current_screen_message
from .models import CurrentScreenSnapshot


@dataclass(frozen=True)
class RunResult:
    message: str
    sent: bool
    skipped: bool
    message_hash: str


def run_once(
    config: AppConfig,
    *,
    crawl_snapshot: Callable[[AppConfig], CurrentScreenSnapshot] | None = None,
    send_message: Callable[[AppConfig, str], None] | None = None,
) -> RunResult:
    crawl = crawl_snapshot or _crawl_snapshot
    sender = send_message or _send_message

    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.mkdir(parents=True, exist_ok=True)

    with RunLock(config.state_dir / "run.lock", stale_timeout_seconds=config.run_lock_timeout_seconds):
        snapshot = crawl(config)
        message = render_current_screen_message(snapshot, source_label=config.crawl_name)
        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()

        if config.send_only_on_change and _is_duplicate(config, message_hash):
            return RunResult(message=message, sent=False, skipped=True, message_hash=message_hash)

        if config.send_enabled:
            sender(config, message)
            _write_last_hash(config, message_hash)
            return RunResult(message=message, sent=True, skipped=False, message_hash=message_hash)

        return RunResult(message=message, sent=False, skipped=False, message_hash=message_hash)


def _is_duplicate(config: AppConfig, message_hash: str) -> bool:
    path = config.state_dir / "last_message.sha256"
    return path.exists() and path.read_text(encoding="utf-8").strip() == message_hash


def _write_last_hash(config: AppConfig, message_hash: str) -> None:
    (config.state_dir / "last_message.sha256").write_text(message_hash, encoding="utf-8")


def _crawl_snapshot(config: AppConfig) -> CurrentScreenSnapshot:
    from .platforms import crawl_snapshot

    return crawl_snapshot(config)


def _send_message(config: AppConfig, message: str) -> None:
    from .messengers import dispatch_text_message

    dispatch_text_message(config, message)
