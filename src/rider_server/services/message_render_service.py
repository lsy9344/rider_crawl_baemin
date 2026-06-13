"""MessageRenderService — run_once의 렌더 단계 분리(Story 3.1 / P2-01, FR-8).

책임: 수집 Snapshot을 받아 기존 ``render_current_screen_message`` 로 메시지
텍스트(str)만 만든다 — 렌더 로직을 재구현하지 않는다(의도치 않은 렌더링
변경은 FR-2 위반·regression). ``source_label`` 은 호출부가 derive해 인자로
넘긴다(서비스를 config-bound로 만들지 않아 독립 호출·재렌더링 가능).

Story 3.3가 ``render``(str 반환, 3.1 parity 보존 — 무변경) 옆에 ``render_message``/
``Message``(렌더 레코드 + 안정적 ``text_hash=sha256(text)`` + 재현 가능 재렌더링)를
**additive** 로 추가했다(P2-03, FR-8). 두 메서드는 같은 ``render_current_screen_message``
를 호출하므로 ``render_message(...).text == render(snapshot, source_label=...)``(같은
``now``·``source_label`` 일 때) — 텍스트 정본은 하나다.

위임처(여기서 하지 않는 것): fan-out(1 Message → N 채널)=Story 3.4, DeliveryLog/
idempotency dedup key(``…+template_version+text_hash``)=Story 3.5, tenant-level 템플릿
선택·``messages`` 영속/ORM/async wiring=Epic 5.

설계 불변식:
  - 순수·결정적: 내부에서 ``datetime.now()``/``uuid4()`` 를 호출하지 않는다 — ``id``/시각
    (``now``)은 호출부가 인자로 주입한다(쿠팡은 ``now`` 가 피크 시간표를 좌우해 hash
    안정성의 전제 — 같은 ``now`` 면 같은 hash). ``now=None`` 시 렌더러의 기존 ``now()``
    동작은 보존된다(본 서비스가 새 비결정성을 도입하지 않음).
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만, 역방향 0.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from rider_crawl.message import render_current_screen_message
from rider_crawl.models import (
    CrawlSnapshotResult,
    CurrentScreenSnapshot,
    PerformanceSnapshot,
)
from rider_crawl.redaction import redact
from rider_server.domain import Message

# server-side template_version 상수: rider_crawl 렌더러에 버전 필드가 없어 여기에 둔다
# (rider_crawl 무변경 — 3.2 parser_version 선례와 동형). 렌더 포맷이 바뀌면 bump.
# parser_version(수집 출력 shape)과 별개 축: template_version=메시지 포맷.
_BAEMIN_TEMPLATE_VERSION = "baemin.realtime.v1"
_COUPANG_TEMPLATE_VERSION = "coupang.realtime.v1"

# 영속·표시용 미리보기 길이 cap.
_PREVIEW_MAX_CHARS = 500


class MessageRenderService:
    """렌더 단계 — Snapshot → 메시지 텍스트/``Message``(순수 정적, 재구현 없음)."""

    @staticmethod
    def render(snapshot: CrawlSnapshotResult, *, source_label: str = "") -> str:
        # run_once(app.py 39-40)와 동일: 기존 렌더러 재사용(재구현 금지).
        return render_current_screen_message(snapshot, source_label=source_label)

    @staticmethod
    def render_message(
        snapshot: CrawlSnapshotResult,
        *,
        message_id: str,
        snapshot_id: str,
        source_label: str = "",
        now: datetime | None = None,
    ) -> Message:
        # (1) raw 타입으로 template_version 결정(정규화 통과 후라 정상은 미발생 — 방어적).
        if isinstance(snapshot, CurrentScreenSnapshot):
            template_version = _BAEMIN_TEMPLATE_VERSION
        elif isinstance(snapshot, PerformanceSnapshot):
            template_version = _COUPANG_TEMPLATE_VERSION
        else:
            raise TypeError(
                f"예상 외 Snapshot 타입({type(snapshot).__name__}) — render_message 를 거부한다."
            )

        # (2) 기존 renderer 재사용(재구현 금지 — AC3). now=None 시 렌더러의 기존 now() 보존.
        text = render_current_screen_message(snapshot, source_label=source_label, now=now)
        # (3) 3.1 message_hash 와 동일 계산(AC1.2 — 3.5 dedup 정합).
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # (4) 영속·표시용 미리보기: redaction 통과 후 길이 cap(P0-04 재사용·defense-in-depth).
        text_redacted_preview = redact(text)[:_PREVIEW_MAX_CHARS]

        return Message(
            id=message_id,
            snapshot_id=snapshot_id,
            template_version=template_version,
            text=text,
            text_hash=text_hash,
            text_redacted_preview=text_redacted_preview,
        )
