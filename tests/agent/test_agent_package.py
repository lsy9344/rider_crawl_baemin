"""Story 4.1 — rider_agent 패키지 토대 + rider_crawl 재사용 seam 검증.

외부 호출 없음(실제 브라우저/네트워크/Kakao/email 미호출). 부정 가드(단방향 import·
sync 런타임·새 프레임워크 0)는 raw grep 이 아니라 **AST import-edge** 로 검사한다 —
scope 경계 docstring 이 금지 심볼(rider_agent/rider_server/async)을 문자열로 명시하므로
raw 소스 매칭은 docstring 언급을 import 로 오탐한다(memory/negative-guard-tests-use-ast).
fixture 는 가짜 값만 — 실제 토큰/chat_id/전화/이메일/OTP 원문 없음.
"""

from __future__ import annotations

import ast
import os
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
AGENT_DIR = SRC_DIR / "rider_agent"
CRAWL_DIR = SRC_DIR / "rider_crawl"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# rider_agent 자기 모듈에서 허용되는 import root: 표준 라이브러리 ∪ 자기 패키지 ∪ rider_crawl.
_SELF_ROOTS = {"rider_agent", "__future__"}
_ALLOWED_THIRD_PARTY = {"rider_crawl"}


def _py_files(pkg_dir: Path) -> list[Path]:
    return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _abs_import_roots(tree: ast.Module) -> set[str]:
    """절대 import 의 top-level root 집합(상대 import 는 자기 패키지라 제외)."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 상대 import → 자기 패키지(rider_agent / rider_crawl) 내부
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _abs_import_modules(tree: ast.Module) -> set[str]:
    """절대 import 의 **전체 점(dotted) 모듈 경로** 집합(submodule 단위 가드용).

    ``_abs_import_roots`` 는 top-level root(``rider_crawl``)만 보지만, 레거시 UI
    진입 가드는 ``rider_crawl.ui`` / ``rider_crawl.app`` 같은 **서브모듈**을 구분해야
    한다. ``import a.b.c`` → ``a.b.c``, ``from a.b import x`` → ``a.b`` 를 수집한다.
    """
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                mods.add(node.module)
    return mods


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    """``src`` 를 ``PYTHONPATH`` 에 둔 깨끗한 서브프로세스에서 ``code`` 를 실행한다.

    import-safety/lightweight-``__init__`` 가드는 같은 pytest 프로세스 안에서는
    다른 테스트가 이미 무거운 모듈을 ``sys.modules`` 에 올렸을 수 있어 신뢰할 수
    없다 → 매번 새 인터프리터로 확인한다.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ──────────────────────────────────────────────────────────────────────────
# AC1 — 패키지 생성 + import + `python -m rider_agent` 실행
# ──────────────────────────────────────────────────────────────────────────

def test_rider_agent_package_and_seam_import():
    import rider_agent
    import rider_agent.reuse  # noqa: F401

    assert rider_agent.__version__ == "0.1.0"


def test_python_dash_m_rider_agent_exits_zero_runpy():
    # `python -m rider_agent` 와 동일 경로(패키지 __main__ 실행)를 in-process 로 검증.
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("rider_agent", run_name="__main__")
    assert exc.value.code == 0


def test_python_dash_m_rider_agent_exits_zero_subprocess():
    # 실제 서브프로세스로 무-GUI 정상 종료(exit 0) 확인 — tkinter/브라우저/네트워크/Kakao
    # 부작용이 있으면 hang 하거나 비정상 종료한다.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    result = subprocess.run(
        [sys.executable, "-m", "rider_agent"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "rider_agent" in result.stdout
    assert "sync runtime" in result.stdout


# ──────────────────────────────────────────────────────────────────────────
# AC1·재사용 identity — seam 심볼이 rider_crawl 의 동일 객체(재구현 아님)
# ──────────────────────────────────────────────────────────────────────────

def test_reuse_seam_reexports_same_objects():
    import rider_crawl.auth.coupang_email_2fa as cp2fa
    import rider_crawl.auth.imap_2fa as imap_2fa
    import rider_crawl.message as message
    import rider_crawl.messengers as messengers
    import rider_crawl.platforms as platforms
    import rider_crawl.sender as sender
    from rider_agent import reuse

    # 수집
    assert reuse.crawl_snapshot is platforms.crawl_snapshot
    import rider_crawl.crawler as crawler
    import rider_crawl.parser as parser
    import rider_crawl.platforms.coupang as coupang
    assert reuse.crawler is crawler
    assert reuse.parser is parser
    assert reuse.coupang is coupang
    # Chrome 실행 + CDP/프로필 격리 가드(4.5) — 재구현 금지(동일 객체 identity 잠금).
    import rider_crawl.browser_launcher as browser_launcher
    import rider_crawl.config as config
    import rider_crawl.lock as lock
    assert reuse.prepare_chrome is browser_launcher.prepare_chrome
    assert reuse.ensure_local_cdp_address is browser_launcher.ensure_local_cdp_address
    assert reuse.BrowserLaunchError is browser_launcher.BrowserLaunchError
    assert reuse.CdpUnavailableError is browser_launcher.CdpUnavailableError
    assert reuse.BrowserActionRequiredError is browser_launcher.BrowserActionRequiredError
    # 실행 락(4.5 — 선택) + 쿠팡 위험 분류(4.5)
    assert reuse.RunLock is lock.RunLock
    assert reuse.coupang_center_name_risk is config.coupang_center_name_risk
    # 렌더
    assert reuse.render_current_screen_message is message.render_current_screen_message
    # Email/IMAP 2FA
    assert reuse.fetch_latest_verification_code is imap_2fa.fetch_latest_verification_code
    assert (
        reuse.recover_coupang_session_with_email_2fa
        is cp2fa.recover_coupang_session_with_email_2fa
    )
    # Kakao sender
    assert reuse.send_kakao_text is sender.send_kakao_text
    assert reuse.KakaoSendError is sender.KakaoSendError
    assert reuse.KakaoUnsafeSelectionError is sender.KakaoUnsafeSelectionError
    assert reuse.KakaoMessenger is messengers.KakaoMessenger
    assert reuse.dispatch_text_message is messengers.dispatch_text_message


# ──────────────────────────────────────────────────────────────────────────
# AC3 — sync 런타임 가드 (AST, rider_agent 자기 모듈만)
# ──────────────────────────────────────────────────────────────────────────

def test_rider_agent_modules_are_pure_sync():
    offenders: list[str] = []
    for path in _py_files(AGENT_DIR):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
                offenders.append(f"{path.name}: {type(node).__name__}")
        # 자기 모듈이 직접 이벤트 루프를 띄우지 않음(transitive 금지 아님 — rider_crawl.crawler 의
        # asyncio 는 검사 대상이 아니다).
        if "asyncio" in _abs_import_roots(tree):
            offenders.append(f"{path.name}: import asyncio")
    assert offenders == [], offenders


# ──────────────────────────────────────────────────────────────────────────
# AC2 — 새 프레임워크 미도입 (AST + pyproject 핀)
# ──────────────────────────────────────────────────────────────────────────

def test_rider_agent_only_third_party_root_is_rider_crawl():
    stdlib = set(sys.stdlib_module_names)
    third_party_union: set[str] = set()
    for path in _py_files(AGENT_DIR):
        roots = _abs_import_roots(_parse(path))
        third_party = roots - stdlib - _SELF_ROOTS
        assert third_party <= _ALLOWED_THIRD_PARTY, f"{path.name}: {third_party}"
        third_party_union |= third_party
    # seam 이 실제로 rider_crawl 을 재사용함(유일한 third-party root)을 잠근다.
    assert third_party_union == {"rider_crawl"}


def test_pyproject_dependencies_unchanged_pins():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    normalized = {d.replace(" ", "") for d in deps}
    assert "playwright==1.60.0" in normalized
    assert "crawl4ai==0.8.7" in normalized
    # Google OAuth 의존 제거 후 main deps는 IMAPClient 포함 7개다.
    assert len(deps) == 7, deps


# ──────────────────────────────────────────────────────────────────────────
# AC5 — 단방향 import (AST import-edge)
# ──────────────────────────────────────────────────────────────────────────

def test_rider_crawl_never_imports_rider_agent():
    offenders: list[str] = []
    for path in _py_files(CRAWL_DIR):
        if "rider_agent" in _abs_import_roots(_parse(path)):
            offenders.append(path.name)
    assert offenders == [], offenders


def test_rider_agent_never_imports_rider_server():
    offenders: list[str] = []
    for path in _py_files(AGENT_DIR):
        if "rider_server" in _abs_import_roots(_parse(path)):
            offenders.append(path.name)
    assert offenders == [], offenders


# ══════════════════════════════════════════════════════════════════════════
# QA gap coverage (qa-generate-e2e-tests) — 기존 9건이 비운 명시 요구사항 보강.
#   Gap1: __main__ 이 tkinter/레거시 UI 진입을 import 하지 않음 (Task 3, AC1 부작용 0)
#   Gap2: reuse seam eager import 가 무거운/플랫폼 의존을 끌지 않음 (AC1 import-safety)
#   Gap3: import rider_agent 가 seam 을 eager-load 하지 않음 (Task 1 가벼운 __init__)
#   Gap4: main() 직접 호출이 0 반환 + sync 배너 출력 (AC1 단위 계약)
#   Gap5: reuse.__all__ 의 모든 이름이 실제 attribute 로 해석됨 (re-export drift 가드)
# ══════════════════════════════════════════════════════════════════════════

# AC1·Task 3 — __main__ 은 thin sync bootstrap 이라 tkinter GUI 와 레거시 UI 진입
# (`rider_crawl.ui` / `rider_crawl.app`)을 import 하지 않는다(부작용 0의 정적 근거).
def test_main_does_not_import_tkinter_or_legacy_ui():
    main_path = AGENT_DIR / "__main__.py"
    modules = _abs_import_modules(_parse(main_path))
    roots = {m.split(".")[0] for m in modules}
    assert "tkinter" not in roots, modules
    legacy = {
        m
        for m in modules
        if m in {"rider_crawl.ui", "rider_crawl.app"}
        or m.startswith(("rider_crawl.ui.", "rider_crawl.app."))
    }
    assert legacy == set(), legacy


# AC1·import-safety(핵심 설계 결정) — reuse seam 을 eager import 해도 crawl4ai/
# playwright/Windows GUI(pyautogui·pywinauto·pyperclip)/mail 클라이언트를 끌지
# 않아야 `python -m rider_agent` 가 무-GUI·무-브라우저로 뜬다. rider_crawl 의 lazy
# 경계(함수 내부 import)에 의존하므로, 깨끗한 서브프로세스의 sys.modules 로 확인한다.
def test_reuse_seam_is_import_safe_no_heavy_deps():
    code = (
        "import sys\n"
        "import rider_agent.reuse\n"
        "heavy = ('crawl4ai','playwright','pyautogui','pyperclip',"
        "'pywinauto','googleapiclient')\n"
        "print(sorted(m for m in heavy if m in sys.modules))\n"
    )
    result = _run_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


# Task 1 — __init__ 은 seam(reuse)을 eager import 하지 않아 `import rider_agent`
# 자체가 가볍다(무거운 import 를 끌지 않음). 깨끗한 서브프로세스로 단언.
def test_import_rider_agent_does_not_eager_load_reuse_seam():
    code = (
        "import sys\n"
        "import rider_agent\n"
        "print('rider_agent.reuse' in sys.modules)\n"
    )
    result = _run_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", result.stdout


# AC1 — main() 단위 계약: 동기 함수가 0 을 반환하고 sync 시작 배너(버전·"sync
# runtime")를 출력한다. runpy/subprocess 가 아닌 직접 호출로 실패 위치를 좁힌다.
def test_main_returns_zero_and_prints_sync_banner(capsys):
    from rider_agent import __main__ as agent_main
    from rider_agent import __version__

    rc = agent_main.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert "sync runtime" in out


# AC1·재사용 완전성 — reuse.__all__ 에 적힌 이름이 모두 실제 attribute 로 해석된다
# (한 줄 re-export 가 누락되면 __all__ 엔 남고 attribute 는 없는 drift 를 잡는다).
def test_reuse_all_names_are_resolvable():
    from rider_agent import reuse

    missing = [name for name in reuse.__all__ if not hasattr(reuse, name)]
    assert missing == [], missing
