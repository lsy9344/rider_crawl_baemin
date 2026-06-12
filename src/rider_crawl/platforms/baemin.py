from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rider_crawl.config import AppConfig
from rider_crawl.models import CurrentScreenSnapshot


CrawlSnapshot = Callable[[AppConfig], CurrentScreenSnapshot]


def _crawl_current_screen(config: AppConfig) -> CurrentScreenSnapshot:
    from rider_crawl.crawler import crawl_current_screen

    return crawl_current_screen(config)


@dataclass(frozen=True)
class BaeminDeliveryPlatform:
    crawl: CrawlSnapshot = _crawl_current_screen
    name: str = "baemin"

    def crawl_snapshot(self, config: AppConfig) -> CurrentScreenSnapshot:
        return self.crawl(config)
