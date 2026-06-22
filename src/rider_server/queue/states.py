"""job type 어휘 + job status 상태머신 — Story 5.3 (AC3).

job type/status 는 **평문 UPPER_SNAKE 상수**다(enum/"정확히 N개" lock 금지). 이유:
``rider_agent.heartbeat.DEFAULT_CAPABILITIES``·``rider_agent.job_loop`` 가 capability·status
를 plain-string 상수로 둔 선례를 따라, 후속 스토리가 type/status 를 늘려도 ``test_domain_states``
의 count-lock(11/4/7)을 깨지 않게 한다(memory: enum-member-count-locks).

job type 6종은 ``rider_agent.heartbeat`` 의 ``CAPABILITY_*`` 와 **문자열로 일치**하되 import 하지
않는다(단방향 의존: ``rider_server`` 는 ``rider_agent`` 를 import 금지 — 값만 미러).
[Source: architecture.md:308-313, architecture-contract.md:120-129, src/rider_agent/heartbeat.py:62-76]

job status 전이는 **정의된 set 만** 허용한다(미정의 전이는 :class:`InvalidJobTransition`).
상태 전이는 backend/service 경계에서만 일어나고 DB 컬럼을 임의로 직접 변경하지 않는다.
``services.subscription_gate.DispatchJobStatus`` (게이트-facing 최소 부분집합 PENDING/HELD/
SUCCEEDED)와 **같은 의미값은 같은 문자열**로 reconcile 하되, 강결합(import)하지 않는다.
[Source: src/rider_server/services/subscription_gate.py:46-56]
"""

from __future__ import annotations

# ── job type 정본 6종(UPPER_SNAKE plain-string, rider_agent capability 와 1:1) ─────
# 구표기(CRAWL/RENDER/DISPATCH_TELEGRAM/BAEMIN_AUTH_OPEN)는 **사용 금지** — 구표기로
# 구현하면 Agent capability 매칭이 깨져 claim 이 0건이 된다.
JOB_TYPE_CRAWL_BAEMIN = "CRAWL_BAEMIN"
JOB_TYPE_CRAWL_COUPANG = "CRAWL_COUPANG"
JOB_TYPE_AUTH_CHECK = "AUTH_CHECK"
JOB_TYPE_OPEN_AUTH_BROWSER = "OPEN_AUTH_BROWSER"
JOB_TYPE_KAKAO_SEND = "KAKAO_SEND"
JOB_TYPE_CAPTURE_DIAGNOSTIC = "CAPTURE_DIAGNOSTIC"

#: 정본 job type 6종. tuple 로 두어 우발적 변이를 막는다(후속이 늘려도 count-lock 없음).
JOB_TYPES: tuple[str, ...] = (
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
    JOB_TYPE_AUTH_CHECK,
    JOB_TYPE_OPEN_AUTH_BROWSER,
    JOB_TYPE_KAKAO_SEND,
    JOB_TYPE_CAPTURE_DIAGNOSTIC,
)

# ── job status 정본(UPPER_SNAKE plain-string) ────────────────────────────────────
JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_CLAIMED = "CLAIMED"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_SUCCEEDED = "SUCCEEDED"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUS_RETRY = "RETRY"

#: 정의된 job status 전체.
JOB_STATUSES: tuple[str, ...] = (
    JOB_STATUS_PENDING,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RETRY,
)

# ── 허용 전이표(정본) — 미정의 전이는 거부 ───────────────────────────────────────
# PENDING→CLAIMED(claim)→RUNNING→(SUCCEEDED|FAILED). lease 만료 시 CLAIMED/RUNNING→PENDING
# (stale 회수). 재시도 FAILED/RETRY→PENDING(attempts++/backoff 는 service 소유). SUCCEEDED 는
# 종단(터미널). RETRY 는 재진입 마커.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    JOB_STATUS_PENDING: frozenset({JOB_STATUS_CLAIMED}),
    JOB_STATUS_CLAIMED: frozenset(
        {
            JOB_STATUS_RUNNING,
            JOB_STATUS_SUCCEEDED,
            JOB_STATUS_FAILED,
            JOB_STATUS_RETRY,
            JOB_STATUS_PENDING,  # lease 만료 stale 회수
        }
    ),
    JOB_STATUS_RUNNING: frozenset(
        {
            JOB_STATUS_SUCCEEDED,
            JOB_STATUS_FAILED,
            JOB_STATUS_RETRY,
            JOB_STATUS_PENDING,  # lease 만료 stale 회수
        }
    ),
    JOB_STATUS_RETRY: frozenset({JOB_STATUS_PENDING}),
    JOB_STATUS_FAILED: frozenset({JOB_STATUS_PENDING}),  # 재시도 재진입
    JOB_STATUS_SUCCEEDED: frozenset(),  # 터미널
}

#: 종단(터미널) status — 더 이상 전이 없음(complete 로 기록 후 불변).
TERMINAL_STATUSES: frozenset[str] = frozenset({JOB_STATUS_SUCCEEDED})


class InvalidJobTransition(ValueError):
    """정의되지 않은 job status 전이 시도(AC3). ``current``/``target`` 은 secret 아님."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"invalid job status transition: {current} -> {target}")
        self.current = current
        self.target = target


def is_allowed_transition(current: str, target: str) -> bool:
    """``current`` → ``target`` 가 허용 전이인가(미정의 status 는 False)."""

    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    """허용되지 않은 전이면 :class:`InvalidJobTransition` 를 올린다(직접 컬럼 변경 차단)."""

    if not is_allowed_transition(current, target):
        raise InvalidJobTransition(current, target)


# ── Agent 소문자 status → job 상태머신값(대문자) 매핑 ─────────────────────────────
# Agent 는 소문자 status 를 보낸다(``job_loop.JOB_STATUS_SUCCESS="success"`` /
# ``JOB_STATUS_FAILED="failed"``). 서버가 job 상태머신값(UPPER_SNAKE)으로 매핑한다 —
# 소문자를 DB 에 그대로 저장하면 상태머신/조회가 깨진다.
# [Source: src/rider_agent/job_loop.py:80-82,302-329]
_AGENT_STATUS_MAP: dict[str, str] = {
    "success": JOB_STATUS_SUCCEEDED,
    "failed": JOB_STATUS_FAILED,
}


class UnknownAgentStatus(ValueError):
    """Agent 가 보낸 알 수 없는 status 문자열(secret 아님)."""


def map_agent_status(agent_status: str) -> str:
    """Agent 소문자 status 를 job 상태머신값으로 매핑한다.

    ``lease_lost`` 는 Agent 가 서버에 보내지 않고 client 측에서 abandon 하므로 매핑 대상이
    아니다(서버는 받을 일 없음). 알 수 없는 값은 :class:`UnknownAgentStatus`.
    """

    mapped = _AGENT_STATUS_MAP.get(agent_status)
    if mapped is None:
        raise UnknownAgentStatus(f"unknown agent status: {agent_status}")
    return mapped
