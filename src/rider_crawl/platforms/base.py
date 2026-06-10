from __future__ import annotations

from typing import Protocol

from rider_crawl.config import AppConfig
from rider_crawl.models import CrawlSnapshotResult


class PerformancePlatform(Protocol):
    name: str

    def crawl_snapshot(self, config: AppConfig) -> CrawlSnapshotResult:
        ...
