"""SnapshotNormalizer — 수집 결과 정규화 + 필수데이터 fail-closed 게이트(Story 3.2 / P2-02, FR-7).

책임: ``CrawlSnapshotResult``(배민 ``CurrentScreenSnapshot`` 또는 쿠팡
``PerformanceSnapshot``)를 받아 도메인 ``Snapshot`` 레코드로 **wrapping** 한다 —
parser 출력은 ``dataclasses.asdict`` 로 한 글자도 바꾸지 않고 보존하고(AC3),
필수 실적 데이터가 없으면 0/기본값으로 채우지 않고 ``MissingSnapshotDataError``
(=``MissingPerformanceDataError`` 계승)를 raise해 **그 실행이 Message 생성으로
이어지지 않게(fail-closed)** 한다(AC2, NFR-2).

이것은 ``rider_crawl``(parser 출력)↔``rider_server.domain``(순수 레코드) **브리지**라
서비스 레이어에 둔다. ``CrawlService.crawl`` 은 무변경(3.1 parity) — 정규화는 별도
seam이고, 런타임에서 crawl→normalize→render 를 잇는 wiring은 Epic 5다.

설계 불변식(2.5/2.6/3.1 계승):
  - 순수·결정적·의존성 0: FastAPI/SQLAlchemy/async 없음. 내부에서 ``datetime.now()``/
    ``uuid4()`` 를 호출하지 않는다 — ``snapshot_id``/``collected_at`` 은 호출부 주입.
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만, 역방향 0.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from rider_crawl.models import (
    CrawlSnapshotResult,
    CurrentScreenSnapshot,
    PerformanceSnapshot,
)

# base는 ``rider_crawl.parser`` 정본(11행, ValueError 계승)만 import한다.
# 주의(2-class 함정): ``rider_crawl.platforms.coupang.parser`` 에도 동명의 별개
# 클래스가 있으나(서로 subclass 관계 아님) base로 쓰지 않는다(혼동 금지).
from rider_crawl.parser import MissingPerformanceDataError
from rider_server.domain import Platform, Snapshot, SnapshotQualityState


class MissingSnapshotDataError(MissingPerformanceDataError):
    """필수 실적 데이터 누락으로 정규화를 거부할 때 raise.

    ``MissingPerformanceDataError`` 를 계승하므로(AC2) 기존 ``except
    MissingPerformanceDataError`` / ``except ValueError`` 코드·테스트가 그대로 잡는다.
    """


# server-side parser_version 상수: rider_crawl 에 버전 필드가 없어 여기에 둔다(무변경).
# parser 출력 shape 가 바뀌면 bump 한다.
_BAEMIN_PARSER_VERSION = "baemin.current_screen.v1"
_COUPANG_PARSER_VERSION = "coupang.peak_dashboard.v1"


class SnapshotNormalizer:
    """정규화 단계 — parser 출력을 ``Snapshot`` 으로 wrapping + fail-closed(순수 정적)."""

    @staticmethod
    def normalize(
        raw: CrawlSnapshotResult | None,
        *,
        snapshot_id: str,
        target_id: str,
        collected_at: datetime,
        tenant_id: str = "",
        platform_account_id: str = "",
        agent_id: str = "",
    ) -> Snapshot:
        # (1) 수집 결과 없음 → fail-closed(기본/빈 Snapshot 을 만들지 않는다).
        if raw is None:
            raise MissingSnapshotDataError("수집 결과가 없습니다(raw is None) — 정규화를 거부한다.")

        # (2) raw 타입으로 platform·parser_version 을 결정한다.
        if isinstance(raw, CurrentScreenSnapshot):
            platform = Platform.BAEMIN
            parser_version = _BAEMIN_PARSER_VERSION
        elif isinstance(raw, PerformanceSnapshot):
            platform = Platform.COUPANG
            parser_version = _COUPANG_PARSER_VERSION
        else:
            raise MissingSnapshotDataError(
                f"예상 외 수집 결과 타입({type(raw).__name__}) — 정규화를 거부한다."
            )

        # (3) parser 출력 전량 보존: 중첩 dataclass 재귀 변환, 안정적 키, JSON-safe.
        #     키 재명명·정렬·필터·기본값 주입 없이 그대로 감싼다(AC3 동등성).
        normalized_json = dataclasses.asdict(raw)

        # (4) 필수 의미값 2차 게이트(defense-in-depth) — 누락 시 raise(0/기본값 주입 금지).
        SnapshotNormalizer._require_present(raw)

        # (5) 정규화 성공 → quality_state=OK.
        return Snapshot(
            id=snapshot_id,
            target_id=target_id,
            platform=platform,
            collected_at=collected_at,
            normalized_json=normalized_json,
            parser_version=parser_version,
            quality_state=SnapshotQualityState.OK,
            tenant_id=tenant_id,
            platform_account_id=platform_account_id,
            agent_id=agent_id,
        )

    @staticmethod
    def _require_present(raw: CrawlSnapshotResult) -> None:
        """parser 출력이 구조적으로 present 여도 의미적으로 비면 fail-closed."""
        if isinstance(raw, CurrentScreenSnapshot):
            # 배민: center_name(기대 센터/상점명)이 비면 다른 계정 실적 오발송 위험
            # (project-context §88) → fail-closed 1순위 의미값. 수치 필드는 parser 가
            # 이미 보장하므로 추가 0-체크는 하지 않는다(과검증 금지).
            if raw.center_name is None or not raw.center_name.strip():
                raise MissingSnapshotDataError("배민 center_name 누락 — 정규화를 거부한다(오발송 방지).")
        elif isinstance(raw, PerformanceSnapshot):
            # 쿠팡: peak_dashboard 필수(방어적 None 체크). current_screen 은 선택값
            # (쿠팡은 보통 None 이 정상 — models.py 59-66)이라 검증하지 않는다.
            if raw.peak_dashboard is None:
                raise MissingSnapshotDataError("쿠팡 peak_dashboard 누락 — 정규화를 거부한다.")
