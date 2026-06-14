"""Story 5.6 / AC (읽기 전용 불변식) — admin 모듈 read-only·단방향 import AST 가드.

대시보드는 상태를 바꾸지 않는다(상태 전이는 5.7). 이를 raw grep 이 아닌 **AST call-edge**로
강제한다(scope-boundary docstring 이 금지 심볼을 문자열로 명시 → raw 매칭 오탐, memory/
negative-guard-tests-use-ast):
  (1) admin 모듈은 DB write(``commit``/``add``/``flush``/``insert``/``update``/``delete`` …)·
      queue/상태 전이 service(``enqueue``/``save``/``register``/``verify``/``activate``/
      ``deactivate``/``claim``/``complete`` …)를 **호출하지 않는다**(읽기 전용).
  (2) 단방향 import: ``rider_agent`` import 0, third-party root 는 허용 집합(fastapi/sqlalchemy/
      jinja2/starlette/pydantic/rider_crawl) ⊆.
가드가 vacuous 하지 않음(실제 위반을 잡음)을 자기검증한다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_DIR = REPO_ROOT / "src" / "rider_server" / "admin"

# DB write / 상태 전이 / queue mutation 함수·메서드 이름(읽기 전용 admin 에서 금지).
_FORBIDDEN_CALLS = {
    # SQLAlchemy / session 쓰기
    "commit",
    "add",
    "add_all",
    "flush",
    "merge",
    "delete",
    "insert",
    "update",
    # queue/repository 쓰기
    "save",
    "enqueue",
    "emit_event",
    "recover_stale",
    # 채널 lifecycle 상태 전이(5.5 service)
    "register",
    "verify",
    "activate",
    "deactivate",
    # job 상태 전이/스케줄 mutation
    "claim",
    "complete",
    "run_tick",
    "assert_transition",
    "assert_channel_transition",
}

# 허용 third-party import root(단방향 — rider_agent 절대 금지).
_ALLOWED_THIRD_PARTY = {
    "fastapi",
    "starlette",
    "pydantic",
    "sqlalchemy",
    "jinja2",
    "rider_crawl",
}
_SELF_ROOTS = {"rider_server"}


def _py_files(pkg_dir: Path) -> list[Path]:
    return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_name(call: ast.Call) -> str | None:
    """호출 대상의 함수/메서드 이름(수신자 깊이 무관). bare ``f()`` 또는 ``x.y.f()`` → ``f``."""
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _forbidden_calls_in(tree: ast.Module) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if name in _FORBIDDEN_CALLS:
                hits.append(name)
    return hits


def _abs_import_roots(tree: ast.Module) -> set[str]:
    """절대 import 의 top-level root 집합(상대 import 는 내부라 제외)."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


# ── (1) 읽기 전용: write/transition 호출 0 ─────────────────────────────────────

def test_admin_modules_make_no_write_or_transition_calls() -> None:
    offenders: list[str] = []
    for path in _py_files(ADMIN_DIR):
        for name in _forbidden_calls_in(_parse(path)):
            offenders.append(f"{path.name} -> {name}()")
    assert offenders == [], offenders


def test_readonly_guard_is_not_vacuous() -> None:
    planted = ast.parse("async def h(session):\n    session.commit()\n")
    assert _forbidden_calls_in(planted) == ["commit"]
    planted2 = ast.parse("def h(svc):\n    svc.activate('x')\n")
    assert _forbidden_calls_in(planted2) == ["activate"]
    clean = ast.parse("async def h(session, stmt):\n    return (await session.execute(stmt)).all()\n")
    assert _forbidden_calls_in(clean) == []


# ── (2) 단방향 import ──────────────────────────────────────────────────────────

def test_admin_never_imports_rider_agent() -> None:
    offenders = [p.name for p in _py_files(ADMIN_DIR) if "rider_agent" in _abs_import_roots(_parse(p))]
    assert offenders == [], offenders


def test_admin_third_party_imports_within_allowlist() -> None:
    stdlib = set(sys.stdlib_module_names)
    for path in _py_files(ADMIN_DIR):
        roots = _abs_import_roots(_parse(path))
        third_party = roots - stdlib - _SELF_ROOTS
        assert third_party <= _ALLOWED_THIRD_PARTY, f"{path.name}: {third_party - _ALLOWED_THIRD_PARTY}"


def test_admin_package_has_python_files() -> None:
    # 가드가 빈 디렉터리에 대해 vacuously pass 하지 않도록(파일 존재 보장).
    assert _py_files(ADMIN_DIR), "admin 패키지에 .py 가 있어야 한다"
