"""Story 5.1 / AC3 · Task 5 — rider_server 전용 async 경계 가드(Epic 4 retro A7-carry).

rider_server 는 async 런타임이라 rider_agent 의 sync 가드(tests/agent/test_agent_package.py)
를 그대로 쓸 수 없다. 여기서는 rider_server 의 async 핸들러가 알려진 **blocking sync**
(``time.sleep``/sync subprocess/IO)를 event loop 에서 직접 호출하지 않음을 AST 로 검증하고,
이 가드가 rider_agent 의 9-dep·sync 가드와 **스코프가 분리**돼 있음을 확인한다.

부정 가드는 raw grep 이 아니라 AST import/call edge 로 본다(scope 경계 docstring 이
금지 심볼을 문자열로 명시하므로 raw 매칭은 오탐 — memory/negative-guard-tests-use-ast).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
SERVER_DIR = SRC_DIR / "rider_server"
AGENT_DIR = SRC_DIR / "rider_agent"

# event loop 를 막는 대표 sync 호출 — async 핸들러에서 직접 호출 금지(필요 시 executor).
_FORBIDDEN_DOTTED = {
    "time.sleep",
    "os.system",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
}
_FORBIDDEN_BARE = {"sleep"}  # from time import sleep


def _py_files(pkg_dir: Path) -> list[Path]:
    return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(call: ast.Call) -> str | None:
    """``time.sleep`` 같은 dotted 호출 또는 bare 호출 이름을 돌려준다."""
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f"{f.value.id}.{f.attr}"
    if isinstance(f, ast.Name):
        return f.id
    return None


def _blocking_calls_in_async(tree: ast.Module) -> list[tuple[str, str]]:
    """async 함수 본문에서 발견된 (함수명, 금지 호출명) 목록."""
    hits: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    name = _call_name(sub)
                    if name in _FORBIDDEN_DOTTED or name in _FORBIDDEN_BARE:
                        hits.append((node.name, name))
    return hits


def test_rider_server_async_handlers_have_no_blocking_calls():
    offenders: list[str] = []
    for path in _py_files(SERVER_DIR):
        for fn, name in _blocking_calls_in_async(_parse(path)):
            offenders.append(f"{path.name}:{fn} -> {name}")
    assert offenders == [], offenders


def test_async_blocking_guard_is_not_vacuous():
    # 가드가 실제로 위반을 잡는지(no-op 아님) 자기검증.
    planted = ast.parse("import time\nasync def h():\n    time.sleep(1)\n")
    assert _blocking_calls_in_async(planted) == [("h", "time.sleep")]
    planted_bare = ast.parse("from time import sleep\nasync def h():\n    sleep(1)\n")
    assert _blocking_calls_in_async(planted_bare) == [("h", "sleep")]
    # 정상(async-safe) 호출은 잡지 않는다.
    clean = ast.parse("import time\nasync def h():\n    return time.monotonic()\n")
    assert _blocking_calls_in_async(clean) == []


def test_server_guard_scope_is_separate_from_agent_guard():
    # server 가드는 rider_server 만 스캔하고 rider_agent 와 겹치지 않는다.
    assert SERVER_DIR.name == "rider_server"
    server_files = {p.resolve() for p in _py_files(SERVER_DIR)}
    assert server_files, "rider_server 에 .py 가 있어야 한다"
    assert not any("rider_agent" in p.parts for p in server_files)
    if AGENT_DIR.exists():
        agent_files = {p.resolve() for p in _py_files(AGENT_DIR)}
        assert server_files.isdisjoint(agent_files)
