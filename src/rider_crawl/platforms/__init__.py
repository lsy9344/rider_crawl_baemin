from __future__ import annotations

from rider_crawl.config import AppConfig
from rider_crawl.models import CrawlSnapshotResult

from .baemin import BaeminDeliveryPlatform
from .base import PerformancePlatform
from .coupang import CoupangEatsPlatform

DEFAULT_PLATFORM_NAME = "baemin"

_PLATFORMS: dict[str, PerformancePlatform] = {
    DEFAULT_PLATFORM_NAME: BaeminDeliveryPlatform(),
    "coupang": CoupangEatsPlatform(),
}


def register_platform(platform: PerformancePlatform) -> None:
    _PLATFORMS[platform.name] = platform


def get_platform(name: str = DEFAULT_PLATFORM_NAME) -> PerformancePlatform:
    try:
        return _PLATFORMS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported performance platform: {name}") from exc


def crawl_snapshot(config: AppConfig, *, platform_name: str | None = None) -> CrawlSnapshotResult:
    selected_name = platform_name or getattr(config, "platform_name", DEFAULT_PLATFORM_NAME)
    return get_platform(selected_name).crawl_snapshot(config)
