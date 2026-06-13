"""MessageRenderService — run_once의 렌더 단계 분리(Story 3.1 / P2-01, FR-8).

책임: 수집 Snapshot을 받아 기존 ``render_current_screen_message`` 로 메시지
텍스트(str)만 만든다 — 렌더 로직을 재구현하지 않는다(의도치 않은 렌더링
변경은 FR-2 위반·regression). ``source_label`` 은 호출부가 derive해 인자로
넘긴다(서비스를 config-bound로 만들지 않아 독립 호출·재렌더링 가능).

위임처(여기서 하지 않는 것):
  - ``Message`` dataclass(snapshot_id/template_version/text/text_hash)·안정적
    hash·재렌더링 비교 → Story 3.3. 본 단계는 텍스트(str)만 반환한다.

설계 불변식:
  - 순수·결정적: 내부에서 ``datetime.now()`` 를 호출하지 않는다 — 시각 주입이
    필요하면 렌더러의 ``now`` 인자를 호출부가 채우는 Story 3.3 영역이다.
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만, 역방향 0.
"""

from __future__ import annotations

from rider_crawl.message import render_current_screen_message
from rider_crawl.models import CrawlSnapshotResult


class MessageRenderService:
    """렌더 단계 — Snapshot → 메시지 텍스트(순수 정적, 재구현 없음)."""

    @staticmethod
    def render(snapshot: CrawlSnapshotResult, *, source_label: str = "") -> str:
        # run_once(app.py 39-40)와 동일: 기존 렌더러 재사용(재구현 금지).
        return render_current_screen_message(snapshot, source_label=source_label)
