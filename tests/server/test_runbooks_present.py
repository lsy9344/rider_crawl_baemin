"""Story 5.9 / AC3 — 7종 운영 runbook 파일 존재 + FailureCategory 참조(always-run).

"파일 없이 done 처리"를 차단한다(완료 위조 방지 — "lying about completion"). 각 runbook 이
해당 정본 ``FailureCategory`` 코드 문자열을 명시 참조하는지(NFR-15 분류 계약) 확인하고, 7
runbook 이 정본 7 카테고리를 모두 커버하는지 잠근다.
"""

from __future__ import annotations

from pathlib import Path

from rider_server.domain import FailureCategory

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOK_DIR = _REPO_ROOT / "docs" / "runbooks"

# runbook 파일 → 명시 참조해야 하는 정본 FailureCategory 코드(NFR-15 분류 계약).
_REQUIRED: dict[str, tuple[str, ...]] = {
    "agent_offline.md": ("CRAWL_FAILURE",),
    "queue_lag.md": ("KAKAO_FAILURE",),
    "api_error_rate.md": ("CRAWL_FAILURE", "RENDER_FAILURE", "TELEGRAM_FAILURE"),
    "auth_required.md": ("AUTH_REQUIRED",),
    "profile_mismatch.md": ("TARGET_VALIDATION_FAILURE",),
    "kakao_ambiguous_room.md": ("KAKAO_FAILURE",),
    "duplicate_blocked.md": ("DUPLICATE_BLOCKED",),
}


def test_all_seven_runbooks_exist_as_files() -> None:
    for name in _REQUIRED:
        path = _RUNBOOK_DIR / name
        assert path.is_file(), f"누락된 runbook: {name}"
        assert path.read_text(encoding="utf-8").strip(), f"빈 runbook: {name}"


def test_each_runbook_references_its_failure_category_codes() -> None:
    for name, codes in _REQUIRED.items():
        text = (_RUNBOOK_DIR / name).read_text(encoding="utf-8")
        for code in codes:
            assert code in text, f"{name} 가 {code} 를 참조하지 않음(NFR-15 분류 누락)"


def test_required_codes_are_canonical_failure_category_members() -> None:
    # 참조 코드가 정본 FailureCategory 값에서 벗어나지 않는지(오타/임의 코드 차단).
    canonical = {m.value for m in FailureCategory}
    used = {code for codes in _REQUIRED.values() for code in codes}
    assert used <= canonical, used - canonical


def test_seven_runbooks_cover_all_seven_failure_categories() -> None:
    # 7 runbook 이 정본 7 카테고리를 모두 커버(NFR-15 — 모든 원인이 조치 가능하게 분류).
    covered = {code for codes in _REQUIRED.values() for code in codes}
    assert covered == {m.value for m in FailureCategory}
    assert len(_REQUIRED) == 7
