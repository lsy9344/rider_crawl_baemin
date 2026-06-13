"""rider_server 마이그레이션 레이어(로컬 설정 → ID 기반 도메인 모델) — 명시 재노출.

본 패키지의 첫 코드는 Story 2.7의 순수 마이그레이션 오케스트레이션이다(ADD-16 1~5단계, FR-31).
architecture(442·507)가 ``rider_server/migration/`` 을 cutover 상태머신·kill switch·canary의
정본 위치로 두고, 본 스토리(첫 migration 코드)가 디렉터리를 신설한다.

Story 3.8(FR-3, NFR-22·24·25)이 실제 dry-run 실행(``run_dry_run``/``DryRunResult``)·기준선
비교(``compare_to_baseline``/``DryRunComparison``)·차이 시 자동 활성화 차단 승인 게이트
(``approve_after_review``/``CutoverApprovalError``)·cutover 동시전송 가드
(``assert_no_dual_active_send``/``activate_cutover``/``DualSendError``)·rollback dedup 보존
(``roll_back_cutover``/``RollbackResult``)을 ``cutover.py`` 에 추가해 2.7 상태머신에 compose한다
(2.7 ``runner.py`` 본문 무변경 — wrapping만). 실제 영속·async·kill switch·canary·Admin UI·
legacy 폴러 물리 종료는 Epic 5/P6가 같은 디렉터리에 운영 cutover wiring으로 additive로 덧붙인다.
``pythonpath = ["src"]`` 덕분에 별도 설치 없이 ``from rider_server.migration import
run_migration`` 이 동작한다.
"""

from __future__ import annotations

from .cutover import (
    CutoverApprovalError,
    CutoverResult,
    DryRunComparison,
    DryRunResult,
    DualSendError,
    RollbackResult,
    activate_cutover,
    approve_after_review,
    assert_no_dual_active_send,
    compare_to_baseline,
    roll_back_cutover,
    run_dry_run,
)
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
    # Story 3.8 — dry-run 실행 + 값 객체
    "run_dry_run",
    "DryRunResult",
    # Story 3.8 — 기준선 비교
    "compare_to_baseline",
    "DryRunComparison",
    # Story 3.8 — 차이 시 자동 활성화 차단 승인 게이트
    "approve_after_review",
    "CutoverApprovalError",
    # Story 3.8 — cutover 동시전송 가드(NFR-24)
    "assert_no_dual_active_send",
    "activate_cutover",
    "CutoverResult",
    "DualSendError",
    # Story 3.8 — rollback dedup 보존(NFR-25)
    "roll_back_cutover",
    "RollbackResult",
]
