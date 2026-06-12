from __future__ import annotations

from typing import Protocol

from rider_crawl.config import AppConfig


class Messenger(Protocol):
    name: str

    def send_text(self, config: AppConfig, message: str) -> None:
        ...
