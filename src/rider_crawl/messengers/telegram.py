from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rider_crawl.config import AppConfig


SendText = Callable[[AppConfig, str], None]


def _send_telegram_text(config: AppConfig, message: str) -> None:
    from rider_crawl.sender import send_telegram_text

    send_telegram_text(config, message)


@dataclass(frozen=True)
class TelegramMessenger:
    send: SendText = _send_telegram_text
    name: str = "telegram"

    def send_text(self, config: AppConfig, message: str) -> None:
        self.send(config, message)
