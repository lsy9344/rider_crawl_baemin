from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from rider_crawl.config import AppConfig
from rider_crawl.models import PerformanceSnapshot

from .crawler import crawl_performance_snapshot


CrawlPerformance = Callable[[AppConfig], PerformanceSnapshot]


@dataclass(frozen=True)
class CoupangEatsPlatform:
    crawl: CrawlPerformance = field(default=crawl_performance_snapshot)
    name: str = "coupang"

    def crawl_snapshot(self, config: AppConfig) -> PerformanceSnapshot:
        return self.crawl(config)
