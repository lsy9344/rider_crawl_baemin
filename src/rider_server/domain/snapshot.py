"""``Snapshot`` 도메인 모델(Story 3.2 / P2-02, FR-7) — 정규화된 수집 결과 레코드.

``data-api-contract`` 의 ``snapshots`` 모델(필수 필드 + P2-02의 ``platform``)을
순수 frozen dataclass로 둔다(2.5 도메인 모델 패턴 계승). ``CrawlSnapshotResult``
(parser 출력)→``Snapshot`` **변환(bridge)** 은 ``services/snapshot_normalizer.py``
가 담당한다 — ``domain/`` 은 ``rider_crawl`` 을 import하지 않는 순수 레코드로 유지한다
(레이어 분리: domain=순수 레코드, services=정책/변환).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .states import Platform, SnapshotQualityState


@dataclass(frozen=True)
class Snapshot:
    id: str
    target_id: str  # → MonitoringTarget. 추적: 대상(AC2)
    platform: Platform
    collected_at: datetime  # 추적: 실행 시각(AC2). 호출부 주입 — 자동 now() 금지
    # 안정적 키 + parser 출력 전량 보존(``dataclasses.asdict`` 재귀 변환, architecture 301).
    # 필드 추가·삭제·기본값 주입 없이 parser 출력을 그대로 감싼다(AC3 동등성).
    normalized_json: dict[str, Any]
    parser_version: str
    quality_state: SnapshotQualityState
    # 추적 필드(AC2): 사전-Cloud/Agent 컨텍스트에서 미상일 수 있어 default ""
    # (런타임 미배선 — Epic 5 wiring 전까지). target_id/collected_at 은 정규화 필수라 default 없음.
    tenant_id: str = ""
    platform_account_id: str = ""
    agent_id: str = ""  # Agent 모델은 Epic 4/5 — forward-ref FK는 str placeholder
