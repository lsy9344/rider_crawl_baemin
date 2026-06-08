from __future__ import annotations

from rider_crawl.config import AppConfig

from .base import Messenger
from .kakao import KakaoMessenger

DEFAULT_MESSENGER_NAME = "kakao"

_MESSENGERS: dict[str, Messenger] = {
    DEFAULT_MESSENGER_NAME: KakaoMessenger(),
}


def register_messenger(messenger: Messenger) -> None:
    _MESSENGERS[messenger.name] = messenger


def get_messenger(name: str = DEFAULT_MESSENGER_NAME) -> Messenger:
    try:
        return _MESSENGERS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported messenger: {name}") from exc


def dispatch_text_message(config: AppConfig, message: str, *, messenger_name: str = DEFAULT_MESSENGER_NAME) -> None:
    get_messenger(messenger_name).send_text(config, message)
