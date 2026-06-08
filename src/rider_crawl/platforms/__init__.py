from __future__ import annotations

from rider_crawl.config import AppConfig
from rider_crawl.models import CurrentScreenSnapshot

from .baemin import BaeminDeliveryPlatform
from .base import PerformancePlatform

DEFAULT_PLATFORM_NAME = "baemin"

_PLATFORMS: dict[str, PerformancePlatform] = {
    DEFAULT_PLATFORM_NAME: BaeminDeliveryPlatform(),
}


def register_platform(platform: PerformancePlatform) -> None:
    _PLATFORMS[platform.name] = platform


def get_platform(name: str = DEFAULT_PLATFORM_NAME) -> PerformancePlatform:
    try:
        return _PLATFORMS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported performance platform: {name}") from exc


def crawl_snapshot(config: AppConfig, *, platform_name: str = DEFAULT_PLATFORM_NAME) -> CurrentScreenSnapshot:
    return get_platform(platform_name).crawl_snapshot(config)
