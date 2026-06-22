"""Story 5.9 / Task 6.5 — 재사용·import 경계 가드(항상 실행, DB-less).

5.4 ``test_scheduler_boundary`` / 5.6 boundary 선례. 이 가드는:
  - ``metrics`` 가 ``rider_agent`` 를 import 하지 않음(단방향 import, AST edge).
  - ``metrics`` 의 third-party root 가 허용집합(``sqlalchemy``)뿐.
  - ``metrics`` 가 **읽기 전용**(write/상태전이 SQL 0) — ``update``/``insert``/``delete``/
    ``commit``/``enqueue`` 미사용(dashboard 읽기 전용 선례 계승).
  - 순수 policy 가 임계 정본을 import 재사용(scheduler.policy / admin.severity).

부정 가드는 raw grep 이 아니라 **AST import-edge** 로 본다(scope 경계 docstring 이 금지 심볼을
문자열로 명시 — raw 매칭 오탐 방지, memory/negative-guard-tests-use-ast).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
METRICS_DIR = REPO_ROOT / "src" / "rider_server" / "metrics"

_SELF_ROOTS = {"rider_server", "__future__"}
_ALLOWED_THIRD_PARTY = {"sqlalchemy"}

# 읽기 전용 — 상태를 바꾸는 SQLAlchemy 동사를 import 하지 않는다(write/상태전이 0).
_FORBIDDEN_SQL_WRITE_NAMES = {"update", "insert", "delete"}


def _py_files(pkg_dir: Path) -> list[Path]:
    return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _abs_import_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 상대 import → metrics 패키지 내부
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_metrics_has_python_files() -> None:
    assert _py_files(METRICS_DIR), "metrics 패키지에 .py 가 있어야 한다"


def test_metrics_never_imports_rider_agent() -> None:
    offenders = [
        path.name
        for path in _py_files(METRICS_DIR)
        if "rider_agent" in _abs_import_roots(_parse(path))
    ]
    assert offenders == [], offenders


def test_metrics_third_party_roots_within_allowed() -> None:
    import sys

    stdlib = set(sys.stdlib_module_names)
    for path in _py_files(METRICS_DIR):
        roots = _abs_import_roots(_parse(path))
        third_party = roots - stdlib - _SELF_ROOTS
        assert third_party <= _ALLOWED_THIRD_PARTY, f"{path.name}: {third_party}"


def test_metrics_is_read_only_no_write_sql_verbs() -> None:
    # 읽기 전용: update/insert/delete(SQLAlchemy write 동사) import 0, commit/enqueue 호출 0.
    for path in _py_files(METRICS_DIR):
        names = _imported_names(_parse(path))
        assert names.isdisjoint(_FORBIDDEN_SQL_WRITE_NAMES), f"{path.name}: {names}"
        # 호출 형태(.commit(/.enqueue()만 잡는다 — scope 경계 docstring 이 금지 심볼을 문자열로
        # 명시하므로 raw 단어 매칭은 오탐(memory/negative-guard-tests-use-ast).
        source = path.read_text(encoding="utf-8")
        assert ".commit(" not in source, f"{path.name} 가 commit 호출(상태 변경)"
        assert ".enqueue(" not in source, f"{path.name} 가 enqueue 호출(상태 변경)"


def test_metrics_import_guard_is_not_vacuous() -> None:
    planted = ast.parse("import rider_agent.reuse\n")
    assert "rider_agent" in _abs_import_roots(planted)


def test_policy_reuses_canonical_threshold_modules() -> None:
    # 재사용(재구현 금지)의 정적 근거: policy.py 가 severity·scheduler.policy 를 import 한다.
    from rider_server.metrics import policy
    from rider_server.scheduler import policy as scheduler_policy

    assert policy.DEFAULT_BREAKER_THRESHOLD is scheduler_policy.DEFAULT_BREAKER_THRESHOLD


def test_pg_repository_reuses_scheduler_window_query() -> None:
    # crawl 윈도는 scheduler 정본 재사용(평행 쿼리 신설 0)의 정적 근거.
    repo_src = (METRICS_DIR / "repository_postgres.py").read_text(encoding="utf-8")
    assert "platform_failure_window" in repo_src
    assert "PostgresSchedulerRepository" in repo_src
