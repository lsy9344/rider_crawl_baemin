"""Story 5.4 / AC1·AC2·AC3 Task 4(e) — 재사용·import 경계 가드(항상 실행, DB-less).

5.4 는 정책을 **조립(compose)** 하는 스토리지 평행 재구현이 아니다. 이 가드는:
  - ``scheduler`` 가 ``rider_agent`` 를 import 하지 않음(단방향 import, AST edge).
  - ``scheduler`` 의 third-party root 가 허용집합(``sqlalchemy``/``rider_crawl``)뿐.
  - 구독 게이트(:class:`SubscriptionGate`)·error_code backoff(:class:`DeliveryFailurePolicy`)를
    **import 재사용**(동일 객체 identity) — 평행 게이트/backoff 함수 신설 0.
  - job type 이 정본 6종(``CRAWL_BAEMIN``/``CRAWL_COUPANG``)이고 구표기(``CRAWL``/``RENDER``/
    ``DISPATCH_TELEGRAM``) 미사용.

부정 가드는 raw grep 이 아니라 **AST import-edge** 로 본다(scope 경계 docstring 이 금지 심볼을
문자열로 명시 — raw 매칭 오탐 방지, memory/negative-guard-tests-use-ast).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_DIR = REPO_ROOT / "src" / "rider_server" / "scheduler"

# scheduler 자기 모듈에서 허용되는 root: 표준 라이브러리 ∪ 자기 서버 패키지 ∪ 허용 third-party.
_SELF_ROOTS = {"rider_server", "__future__"}
_ALLOWED_THIRD_PARTY = {"sqlalchemy", "rider_crawl"}


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
                continue  # 상대 import → 자기 패키지(scheduler) 내부
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _abs_import_modules(tree: ast.Module) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not (node.level and node.level > 0):
            if node.module:
                mods.add(node.module)
    return mods


def test_scheduler_has_python_files() -> None:
    assert _py_files(SCHEDULER_DIR), "scheduler 패키지에 .py 가 있어야 한다"


def test_scheduler_never_imports_rider_agent() -> None:
    offenders = [
        path.name
        for path in _py_files(SCHEDULER_DIR)
        if "rider_agent" in _abs_import_roots(_parse(path))
    ]
    assert offenders == [], offenders


def test_scheduler_third_party_roots_within_allowed() -> None:
    import sys

    stdlib = set(sys.stdlib_module_names)
    for path in _py_files(SCHEDULER_DIR):
        roots = _abs_import_roots(_parse(path))
        third_party = roots - stdlib - _SELF_ROOTS
        assert third_party <= _ALLOWED_THIRD_PARTY, f"{path.name}: {third_party}"


def test_scheduler_import_guard_is_not_vacuous() -> None:
    # 가드가 실제로 위반을 잡는지 자기검증.
    planted = ast.parse("import rider_agent.reuse\n")
    assert "rider_agent" in _abs_import_roots(planted)


def test_policy_imports_subscription_gate_and_failure_policy() -> None:
    # 재사용(재구현 금지)의 정적 근거: policy.py 가 두 정본 서비스를 import 한다.
    mods = _abs_import_modules(_parse(SCHEDULER_DIR / "policy.py"))
    assert "rider_server.services.subscription_gate" in mods
    assert "rider_server.services.delivery_failure_policy" in mods


def test_scheduler_reuses_same_service_objects_not_reimplemented() -> None:
    # import identity — scheduler.policy 가 쓰는 게이트/backoff 가 정본과 동일 객체.
    from rider_server.scheduler import policy
    from rider_server.services.delivery_failure_policy import DeliveryFailurePolicy
    from rider_server.services.subscription_gate import SubscriptionGate

    assert policy.SubscriptionGate is SubscriptionGate
    assert policy.DeliveryFailurePolicy is DeliveryFailurePolicy


def test_scheduler_job_types_are_canonical_not_legacy() -> None:
    from rider_server.domain import Platform
    from rider_server.queue.states import JOB_TYPES
    from rider_server.scheduler import policy

    produced = {policy.crawl_job_type_for(p) for p in (Platform.BAEMIN, Platform.COUPANG)}
    assert produced <= set(JOB_TYPES)
    # 구표기 금지 — Agent capability 매칭이 깨지는 옛 이름은 나오지 않는다.
    assert produced.isdisjoint({"CRAWL", "RENDER", "DISPATCH_TELEGRAM", "BAEMIN_AUTH_OPEN"})
