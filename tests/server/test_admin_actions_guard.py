"""Story 5.7 / AC1·가드레일 — 액션 라우트는 직접 write 0·service 위임만(AST call-edge).

5.6 read-only 가드를 읽기 전용 파일로 좁힌 대신(``test_admin_readonly_guard``), 5.7 액션 모듈
(``admin/actions_routes.py``)에는 **"라우트 직접 ORM write/상태 전이 0, write 는 service 에서만"**
가드를 신설한다(열린 질문 #2). raw grep 이 아닌 AST call-edge 로 강제한다(memory/
negative-guard-tests-use-ast). 단방향 import(``rider_agent`` 0, sqlalchemy 직접 import 0)도 함께
확인한다 — 라우트가 ORM 을 직접 만지지 않음을 import 레벨에서도 보장.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIONS_ROUTE = REPO_ROOT / "src" / "rider_server" / "admin" / "actions_routes.py"
ACTION_SERVICE = REPO_ROOT / "src" / "rider_server" / "services" / "admin_action_service.py"
ACTION_PG_REPO = REPO_ROOT / "src" / "rider_server" / "services" / "admin_action_repository_postgres.py"

# 라우트가 직접 호출하면 안 되는 저수준 write/전이 primitive(반드시 service 경유).
_FORBIDDEN_ROUTE_CALLS = {
    # SQLAlchemy / session 쓰기
    "commit", "add", "add_all", "flush", "merge", "delete", "insert", "update",
    # queue / idempotency primitive
    "save", "enqueue", "deliver_once", "build_dedup_key",
    # job 상태 전이
    "assert_transition", "claim", "complete", "recover_stale",
    # 구독/Dispatch 게이트 primitive(라우트가 직접 게이트를 만지면 안 됨 — service 경유)
    "suspend", "resume", "dispose_held", "hold_undelivered",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _forbidden_calls_in(tree: ast.Module, forbidden: set[str]) -> list[str]:
    return [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (name := _called_name(node)) in forbidden
    ]


def _abs_import_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


# ── 라우트는 직접 write/전이를 하지 않는다 ─────────────────────────────────────

def test_action_route_makes_no_direct_write_or_transition_calls() -> None:
    hits = _forbidden_calls_in(_parse(ACTIONS_ROUTE), _FORBIDDEN_ROUTE_CALLS)
    assert hits == [], f"액션 라우트가 직접 write/전이 호출: {hits}"


def test_action_route_delegates_to_service() -> None:
    """가드가 vacuous 하지 않도록 — 라우트가 실제로 action service 에 위임함을 확인."""
    source = ACTIONS_ROUTE.read_text(encoding="utf-8")
    assert "admin_action_service" in source, "라우트는 admin_action_service 에 위임해야 한다"


def test_action_route_does_not_import_sqlalchemy_or_rider_agent() -> None:
    roots = _abs_import_roots(_parse(ACTIONS_ROUTE))
    assert "sqlalchemy" not in roots, "라우트는 ORM 을 직접 import 하지 않는다(service 경유)"
    assert "rider_agent" not in roots, "단방향 import 위반(rider_agent)"


def test_route_guard_is_not_vacuous() -> None:
    planted = ast.parse("async def h(s):\n    s.commit()\n    s.enqueue()\n")
    assert set(_forbidden_calls_in(planted, _FORBIDDEN_ROUTE_CALLS)) == {"commit", "enqueue"}


# ── 단방향 import: service / PG repo 도 rider_agent 0 ──────────────────────────

def test_action_modules_never_import_rider_agent() -> None:
    for path in (ACTION_SERVICE, ACTION_PG_REPO):
        assert "rider_agent" not in _abs_import_roots(_parse(path)), path.name


def test_action_service_is_the_write_owner() -> None:
    """positive: 액션 service 는 게이트/queue/idempotency 를 compose 한다(정책 재구현 아님)."""
    source = ACTION_SERVICE.read_text(encoding="utf-8")
    assert "SubscriptionGate" in source
    assert "assert_transition" in source
    assert "IdempotentDeliveryService" in source
