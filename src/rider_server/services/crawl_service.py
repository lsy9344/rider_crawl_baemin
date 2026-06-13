"""CrawlService — run_once의 수집 단계 분리(Story 3.1 / P2-01, FR-7).

책임: ``AppConfig`` 를 받아 한 번 수집해 기존 ``CrawlSnapshotResult``
(``CurrentScreenSnapshot | PerformanceSnapshot``)를 반환한다. 기본 adapter는
``run_once`` 의 ``_crawl_snapshot`` 과 동일한 ``platforms.crawl_snapshot`` 위임이며,
테스트는 ``crawl_snapshot`` 인자로 fake를 주입해 외부 브라우저 호출을 끊는다.

위임처(여기서 하지 않는 것):
  - 정규화 Snapshot 모델(platform/target_id/collected_at/parser_version/
    quality_state)·필수데이터 fail-closed(``MissingPerformanceDataError``) → Story 3.2.
  - dedup/DeliveryLog → Story 3.5. async/DB/executor 와이어링 → Epic 5.

설계 불변식:
  - 순수·결정적·의존성 0: FastAPI/SQLAlchemy/async 의존이 없고 내부에서
    ``datetime.now()``/``uuid4()`` 를 호출하지 않는다(2.6 services 규약 계승).
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만, 역방향 0.
  - 독립 실패(FR-7·AC3): adapter 예외를 ``try/except`` 로 삼키지 않고 그대로
    전파한다 — 빈/기본 Snapshot을 만들지 않아 다음 단계(render/dispatch)로
    이어지지 않게 한다(오발송보다 미발송).
"""

from __future__ import annotations

from typing import Callable

from rider_crawl import platforms
from rider_crawl.config import AppConfig
from rider_crawl.models import CrawlSnapshotResult


class CrawlService:
    """수집 단계 — 주입 가능한 crawler를 받아 한 번 수집한다(순수 정적)."""

    @staticmethod
    def crawl(
        config: AppConfig,
        *,
        crawl_snapshot: Callable[[AppConfig], CrawlSnapshotResult] | None = None,
    ) -> CrawlSnapshotResult:
        crawler = crawl_snapshot or _default_crawl_snapshot
        # adapter 예외는 그대로 전파한다(FR-7) — 삼켜서 기본/빈 Snapshot을 만들지 않는다.
        return crawler(config)


def _default_crawl_snapshot(config: AppConfig) -> CrawlSnapshotResult:
    # run_once._crawl_snapshot(app.py 131-134)와 동일 경로(platform registry 위임).
    return platforms.crawl_snapshot(config, platform_name=config.platform_name)
