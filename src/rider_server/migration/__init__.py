"""rider_server 마이그레이션 레이어(로컬 설정 → ID 기반 도메인 모델) — 명시 재노출.

본 패키지의 첫 코드는 Story 2.7의 순수 마이그레이션 오케스트레이션이다(ADD-16 1~5단계, FR-31).
architecture(442·507)가 ``rider_server/migration/`` 을 cutover 상태머신·kill switch·canary의
정본 위치로 두고, 본 스토리(첫 migration 코드)가 디렉터리를 신설한다. Epic 5/P6가 같은
디렉터리에 운영 cutover(kill switch·canary·DB 영속) wiring을 additive로 덧붙인다.
``pythonpath = ["src"]`` 덕분에 별도 설치 없이 ``from rider_server.migration import
run_migration`` 이 동작한다.
"""

from __future__ import annotations

from .runner import (
    MigrationResult,
    MigrationSeed,
    MigrationState,
    TargetMapping,
    TargetMigration,
    activate,
    approve,
    back_up_settings,
    classify_and_issue,
    copy_state_dir,
    map_active_tab,
    mark_dry_run_passed,
    pause,
    roll_back,
    run_migration,
    seed_from_state,
)

__all__ = [
    # 오케스트레이션 진입점
    "run_migration",
    # 상태머신 + 값 객체
    "MigrationState",
    "TargetMapping",
    "MigrationSeed",
    "TargetMigration",
    "MigrationResult",
    # 단계별 순수 함수
    "back_up_settings",
    "classify_and_issue",
    "map_active_tab",
    "copy_state_dir",
    "seed_from_state",
    # 상태 전이 순수 함수
    "mark_dry_run_passed",
    "approve",
    "activate",
    "pause",
    "roll_back",
]
