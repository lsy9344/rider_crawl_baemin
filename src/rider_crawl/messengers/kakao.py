from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rider_crawl.config import AppConfig


SendText = Callable[[AppConfig, str], None]


def _send_kakao_text(config: AppConfig, message: str) -> None:
    from rider_crawl.sender import send_kakao_text

    send_kakao_text(config, message)


@dataclass(frozen=True)
class KakaoMessenger:
    send: SendText = _send_kakao_text
    name: str = "kakao"

    def send_text(self, config: AppConfig, message: str) -> None:
        self.send(config, message)
