from pathlib import Path


def test_project_current_state_doc_matches_refactored_runtime() -> None:
    doc = Path("docs/project-current-state-and-structure.md").read_text(encoding="utf-8")

    assert "중앙 서버 + Windows Local Agent + 관리자 웹 대시보드" in doc
    assert "`src/rider_agent/`" in doc
    assert "python -m rider_agent register" in doc
    assert "python -m rider_agent run" in doc
    assert "python -m rider_agent autostart" in doc
    assert "Epic 5" in doc
    assert "현재 코드는 중앙 서버가 아니라 로컬 PC 앱이다." not in doc
    assert "아직 런타임 미배선" not in doc
    assert "아직 DB/ORM 영속이나 실행 UI 연동은 없고" not in doc
    assert "### 완료된 리팩토링" in doc


def test_project_context_matches_current_dependency_and_2fa_policy() -> None:
    context = Path("_bmad-output/project-context.md").read_text(encoding="utf-8")

    assert "메인 7-dep" in context
    assert "9-dep" not in context
    assert "IMAPClient" in context
    assert "google-api-python-client" not in context
    assert "google-auth-oauthlib" not in context
    assert "google-auth-httplib2" not in context
