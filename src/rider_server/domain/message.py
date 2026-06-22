"""``Message`` 도메인 모델(Story 3.3 / P2-03, FR-8) — Snapshot에서 렌더된 메시지 레코드.

``data-api-contract`` 의 ``messages`` 모델(필수 필드 + P2-03의 ``text``)을 순수 frozen
dataclass로 둔다(2.5/3.2 도메인 모델 패턴 계승). raw Snapshot→``text``/``text_hash``/
``text_redacted_preview`` **변환(bridge)** 은 ``services/message_render_service.py`` 의
``render_message`` 가 담당한다 — ``domain/`` 은 ``rider_crawl`` 을 import하지 않는 순수
레코드로 유지한다(레이어 분리: domain=순수 레코드, services=정책/변환).

``text`` vs ``text_redacted_preview``: ``text`` 는 전송·hash·재렌더 비교용 전체 텍스트,
``text_redacted_preview`` 는 영속·Admin 표시용 redaction 통과 미리보기다(원문 영속 최소화·NFR-5).

위임처(여기서 하지 않는 것): fan-out(1 Message → N 채널)=Story 3.4, DeliveryLog/
idempotency dedup key(``…+template_version+text_hash``)=Story 3.5, ``messages`` 테이블/
ORM/Alembic·tenant-level 템플릿 선택·영속=Epic 5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    id: str
    snapshot_id: str  # → Snapshot FK. 어느 Snapshot에서 렌더됐는지 추적(호출부 주입)
    template_version: str  # 메시지 포맷 버전(server-side 상수). parser_version과 별개 축
    text: str  # 전송용 전체 렌더 텍스트(hash·재렌더 비교용 — 영속 제외 가능: Epic 5)
    text_hash: str  # sha256(text) — 3.1 message_hash 와 동일 계산(3.5 dedup 정합)
    text_redacted_preview: str  # 영속·표시용 redaction 통과 미리보기
