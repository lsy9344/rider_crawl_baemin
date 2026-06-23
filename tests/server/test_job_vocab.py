"""Story 5.3 / AC3·Task 5(e) — job type 어휘 + 단방향 import 가드.

정본 6종 job type 이 Agent ``DEFAULT_CAPABILITIES`` 와 **문자열로 일치**(미러)하고, epic 초안의
구표기(CRAWL/RENDER/DISPATCH_TELEGRAM/BAEMIN_AUTH_OPEN)가 부재함을 잠근다 — 구표기로 구현하면
Agent capability 매칭이 깨져 claim 이 0건이 된다. 또한 ``rider_server.queue``/``api`` 가
``rider_agent`` 를 import 하지 않음을 AST 로 확인한다(단방향: 값만 미러, import 강결합 금지).

job type/status 는 plain-string 상수(enum/"정확히 N개" lock 금지)임을 함께 확인한다 — 후속이
type 을 늘려도 count-lock 이 깨지지 않게 한 선례(memory: enum-member-count-locks, agent-job-type-vocab).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# 테스트는 단방향 import 가드의 대상이 아니다 — 미러 충실도 검증을 위해 양쪽을 import 한다.
from rider_agent.heartbeat import DEFAULT_CAPABILITIES
from rider_server.queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUSES,
    JOB_TYPES,
    RESULT_REASON_CRAWL_RECOVERY_COOLDOWN,
    RESULT_REASON_CRAWL_RECOVERY_NOT_ALLOWED,
    RESULT_REASON_PAYLOAD_EXPIRED,
    RESULT_REASON_STALE_AUTH_JOB_EXPIRED,
    RESULT_REASON_STALE_CRAWL_SKIPPED,
    InvalidJobTransition,
    UnknownAgentStatus,
    assert_transition,
    is_allowed_transition,
    map_agent_status,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_DIR = REPO_ROOT / "src" / "rider_server" / "queue"
API_DIR = REPO_ROOT / "src" / "rider_server" / "api"

# epic 초안 구표기 — architecture.md:308-313 정본으로 대체됨(사용 금지).
_LEGACY_NAMES = {"CRAWL", "RENDER", "DISPATCH_TELEGRAM", "BAEMIN_AUTH_OPEN"}


def test_job_types_mirror_agent_default_capabilities():
    # 문자열 값이 1:1 일치(import 강결합이 아니라 값 미러 — 같은 문자열이어야 claim 매칭됨).
    # count-lock 은 두지 않는다(superset 허용) — 후속 type 확장 시 이 단언만 같이 자란다.
    assert set(JOB_TYPES) == set(DEFAULT_CAPABILITIES)


def test_canonical_job_types_exact_set():
    assert set(JOB_TYPES) == {
        "CRAWL_BAEMIN",
        "CRAWL_COUPANG",
        "AUTH_CHECK",
        "OPEN_AUTH_BROWSER",
        "AUTH_COUPANG_2FA",
        "KAKAO_SEND",
        "CAPTURE_DIAGNOSTIC",
    }


def test_auth_coupang_2fa_vocabulary_is_mirrored_between_server_and_agent():
    # crawl-coupang-auth-separation Task 1: 서버 job type 과 Agent capability 가 같은 문자열로
    # AUTH_COUPANG_2FA 를 이해해야 claim 매칭이 된다(import 강결합 금지 — 값만 미러).
    from rider_agent.heartbeat import CAPABILITY_AUTH_COUPANG_2FA
    from rider_server.queue.states import JOB_TYPE_AUTH_COUPANG_2FA

    assert JOB_TYPE_AUTH_COUPANG_2FA == "AUTH_COUPANG_2FA"
    assert CAPABILITY_AUTH_COUPANG_2FA == "AUTH_COUPANG_2FA"
    assert JOB_TYPE_AUTH_COUPANG_2FA == CAPABILITY_AUTH_COUPANG_2FA
    assert "AUTH_COUPANG_2FA" in JOB_TYPES
    assert "AUTH_COUPANG_2FA" in DEFAULT_CAPABILITIES


def test_no_legacy_job_type_names():
    assert _LEGACY_NAMES.isdisjoint(set(JOB_TYPES))


def test_job_types_and_statuses_are_plain_strings():
    # enum 이 아니라 plain-string tuple(count-lock 회피).
    assert isinstance(JOB_TYPES, tuple)
    assert all(isinstance(t, str) for t in JOB_TYPES)
    assert isinstance(JOB_STATUSES, tuple)
    assert all(isinstance(s, str) for s in JOB_STATUSES)


# ── (QA gap E) AC3: Agent 소문자 status → job 상태머신값(대문자) 매핑 ─────────────


def test_map_agent_status_maps_lowercase_to_state_machine():
    # Agent 는 소문자("success"/"failed")를 보내고 서버가 UPPER_SNAKE 로 매핑한다.
    assert map_agent_status("success") == JOB_STATUS_SUCCEEDED
    assert map_agent_status("failed") == JOB_STATUS_FAILED


def test_map_agent_status_rejects_unknown():
    # lease_lost 는 client-side 마커라 서버로 오지 않는다(매핑 대상 아님) → 거부.
    with pytest.raises(UnknownAgentStatus):
        map_agent_status("lease_lost")
    with pytest.raises(UnknownAgentStatus):
        map_agent_status("weird-status")
    # 대문자 상태머신값을 그대로 넣어도(이미 매핑된 값) Agent 어휘가 아니므로 거부.
    with pytest.raises(UnknownAgentStatus):
        map_agent_status("SUCCEEDED")


# ── (QA gap F) AC3: 허용 전이표 + SUCCEEDED 터미널 ───────────────────────────────


def test_allowed_transitions_cover_claim_run_complete_and_recovery():
    assert is_allowed_transition(JOB_STATUS_PENDING, JOB_STATUS_CLAIMED)
    assert is_allowed_transition(JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)
    assert is_allowed_transition(JOB_STATUS_RUNNING, JOB_STATUS_SUCCEEDED)
    assert is_allowed_transition(JOB_STATUS_RUNNING, JOB_STATUS_FAILED)
    # lease 만료 stale 회수: CLAIMED/RUNNING → PENDING.
    assert is_allowed_transition(JOB_STATUS_CLAIMED, JOB_STATUS_PENDING)
    assert is_allowed_transition(JOB_STATUS_RUNNING, JOB_STATUS_PENDING)


def test_undefined_transitions_rejected():
    # PENDING 에서 바로 SUCCEEDED 로 가는 등 미정의 전이는 거부.
    assert not is_allowed_transition(JOB_STATUS_PENDING, JOB_STATUS_SUCCEEDED)
    assert not is_allowed_transition(JOB_STATUS_PENDING, JOB_STATUS_RUNNING)
    with pytest.raises(InvalidJobTransition):
        assert_transition(JOB_STATUS_PENDING, JOB_STATUS_SUCCEEDED)


def test_succeeded_is_terminal():
    # SUCCEEDED 는 종단 — 어떤 status 로도 나가는 전이가 없다(complete 후 불변).
    for target in JOB_STATUSES:
        assert not is_allowed_transition(JOB_STATUS_SUCCEEDED, target)
    with pytest.raises(InvalidJobTransition):
        assert_transition(JOB_STATUS_SUCCEEDED, JOB_STATUS_PENDING)


def test_pending_to_failed_allowed_for_stale_backlog_cleanup():
    # Task 6: payload TTL 만료 PENDING scheduled crawl 을 recovery 가 terminal 종료한다.
    assert is_allowed_transition(JOB_STATUS_PENDING, JOB_STATUS_FAILED)


# ── Task 1: stale/expired job safe reason vocabulary(secret 0 분류 코드) ───────────


def test_safe_result_reason_constants_are_machine_readable_and_distinct():
    reasons = [
        RESULT_REASON_STALE_AUTH_JOB_EXPIRED,
        RESULT_REASON_STALE_CRAWL_SKIPPED,
        RESULT_REASON_CRAWL_RECOVERY_COOLDOWN,
        RESULT_REASON_CRAWL_RECOVERY_NOT_ALLOWED,
        RESULT_REASON_PAYLOAD_EXPIRED,
    ]
    # 모두 distinct, 비어있지 않은 소문자 snake-case 분류 코드.
    assert len(set(reasons)) == len(reasons)
    for reason in reasons:
        assert reason and reason == reason.strip()
        assert " " not in reason
    # 정본 값(server/Agent/문서가 같은 문자열을 쓴다 — 중복 정의 금지).
    assert RESULT_REASON_STALE_AUTH_JOB_EXPIRED == "stale_auth_job_expired"
    assert RESULT_REASON_STALE_CRAWL_SKIPPED == "stale_crawl_skipped"
    assert RESULT_REASON_PAYLOAD_EXPIRED == "payload_expired"


def test_safe_result_reasons_contain_no_obvious_secret_tokens():
    # tenant/account/email/password/code 같은 secret 값을 담지 않는다(분류 코드만).
    forbidden = ("tenant", "account", "email", "password", "@", "token")
    for reason in (
        RESULT_REASON_STALE_AUTH_JOB_EXPIRED,
        RESULT_REASON_STALE_CRAWL_SKIPPED,
        RESULT_REASON_CRAWL_RECOVERY_COOLDOWN,
        RESULT_REASON_CRAWL_RECOVERY_NOT_ALLOWED,
        RESULT_REASON_PAYLOAD_EXPIRED,
    ):
        lowered = reason.lower()
        assert not any(part in lowered for part in forbidden)


# ── 단방향 import: rider_server.queue/api 는 rider_agent 를 import 하지 않는다 ──────


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _py_files(pkg_dir: Path) -> list[Path]:
    return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)


def test_queue_and_api_never_import_rider_agent():
    offenders: list[str] = []
    for pkg in (QUEUE_DIR, API_DIR):
        for path in _py_files(pkg):
            if "rider_agent" in _import_roots(path):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], offenders


def test_import_guard_is_not_vacuous():
    # 가드가 실제로 잡는지 자기검증(no-op 아님).
    planted = ast.parse("from rider_agent.heartbeat import DEFAULT_CAPABILITIES\n")
    roots = set()
    for node in ast.walk(planted):
        if isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert "rider_agent" in roots
