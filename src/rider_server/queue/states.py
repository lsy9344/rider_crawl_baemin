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
# 쿠팡 email 2FA 자동복구 전용 인증 job. ``OPEN_AUTH_BROWSER`` 는 "사람이 브라우저에서 직접
# 조치하는 job" 으로 남기고, 쿠팡 자동 OTP 입력은 이 type 이 담당한다(Agent capability
# ``CAPABILITY_AUTH_COUPANG_2FA`` 와 **문자열로 일치**하되 import 하지 않는다 — 값만 미러).
JOB_TYPE_AUTH_COUPANG_2FA = "AUTH_COUPANG_2FA"
JOB_TYPE_KAKAO_SEND = "KAKAO_SEND"
JOB_TYPE_CAPTURE_DIAGNOSTIC = "CAPTURE_DIAGNOSTIC"

#: 정본 job type. tuple 로 두어 우발적 변이를 막는다(후속이 늘려도 count-lock 없음 — superset
#: 허용. Agent ``DEFAULT_CAPABILITIES`` 와 같은 문자열 집합이어야 claim 매칭이 된다).
JOB_TYPES: tuple[str, ...] = (
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
    JOB_TYPE_AUTH_CHECK,
    JOB_TYPE_OPEN_AUTH_BROWSER,
    JOB_TYPE_AUTH_COUPANG_2FA,
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
    # PENDING→CLAIMED(claim). PENDING→FAILED 는 stale backlog cleanup 전용(payload TTL 만료
    # PENDING scheduled crawl 을 recovery 가 terminal 종료 — 서버 downtime 뒤 누적 backlog 가
    # 한 번에 claim 되는 것을 막는다). 정상 claim 경로는 여전히 PENDING→CLAIMED 만 쓴다.
    JOB_STATUS_PENDING: frozenset({JOB_STATUS_CLAIMED, JOB_STATUS_FAILED}),
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

# ── stale/expired job 종료 사유(평문 상수) — server/scheduler/Agent 공용 어휘 ─────
# server·scheduler·Agent 가 같은 reason vocabulary 로 stale 여부를 판단하고 result_json
# ["reason"] 에 같은 값을 남기게 한다. 이 값들은 tenant/계정/이메일/비밀번호/인증번호 같은
# secret 을 담지 않는 **기계가독 분류 코드**다(audit/log 안전). 1차 구현은 새 status(SKIPPED)를
# 만들지 않고 ``FAILED + 이 reason`` 으로 stale/expired 작업을 닫아 상태 전이 영향 범위를 줄인다.
RESULT_REASON_STALE_AUTH_JOB_EXPIRED = "stale_auth_job_expired"
RESULT_REASON_STALE_CRAWL_SKIPPED = "stale_crawl_skipped"
RESULT_REASON_CRAWL_RECOVERY_COOLDOWN = "coupang_auto_recovery_cooldown"
RESULT_REASON_CRAWL_RECOVERY_NOT_ALLOWED = "coupang_auto_recovery_not_allowed"
RESULT_REASON_PAYLOAD_EXPIRED = "payload_expired"


# ── stale lease recovery 분류(server/Agent 공용 — backend 간 동일 규칙) ───────────
# PostgreSQL/in-memory backend 의 ``recover_stale`` 가 **같은 규칙**으로 stale 작업을 닫게
# 하는 순수 헬퍼다. 브라우저를 여는 interactive job(``OPEN_AUTH_BROWSER``)이나 scheduled
# crawl 은 lease 만료 시 **재시도(PENDING 재진입)하지 않고** payload TTL(``expires_at``)이 지났으면
# terminal FAILED + safe reason 으로 닫는다(서버/Agent 재시작 뒤 오래된 브라우저 작업·stale
# backlog 가 무제한 재실행되는 것을 막는다). 그 외 job 은 기존 retry 정책을 그대로 따른다.

from datetime import datetime as _dt  # noqa: E402 - 모듈 하단 헬퍼용 지역 별칭(상단 import 오염 회피).

JOB_ORIGIN_SCHEDULER = "scheduler"


def _parse_iso_utc(value: object) -> "_dt | None":
    """payload 의 ISO 8601(``…Z``) 시각 문자열을 timezone-aware datetime 으로 파싱(실패 시 None)."""

    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import timezone as _tz

        parsed = parsed.replace(tzinfo=_tz.utc)
    return parsed


def stale_recovery_reason(
    *,
    job_type: str,
    payload_json: object,
    now: "_dt",
) -> str | None:
    """stale lease recovery 시 이 job 을 **terminal 종료**시킬 reason(또는 재시도 대상이면 None).

    * ``OPEN_AUTH_BROWSER`` + payload ``expires_at < now`` → :data:`RESULT_REASON_STALE_AUTH_JOB_EXPIRED`.
    * scheduled crawl(``job_origin == "scheduler"`` 또는 ``CRAWL_*`` job) + ``expires_at < now``
      → :data:`RESULT_REASON_STALE_CRAWL_SKIPPED`.
    * 그 외(또는 ``expires_at`` 없음/미만료) → ``None``(기존 retry 정책 유지).

    payload 가 dict 가 아니거나 ``expires_at`` 가 없으면 stale 로 보지 않는다(보수적 — 기존
    동작 보존). reason 값은 secret 0(기계가독 분류 코드)이다.
    """

    payload = payload_json if isinstance(payload_json, dict) else {}
    expires_at = _parse_iso_utc(payload.get("expires_at"))
    if job_type == JOB_TYPE_OPEN_AUTH_BROWSER:
        if expires_at is not None and now >= expires_at:
            return RESULT_REASON_STALE_AUTH_JOB_EXPIRED
        return None
    is_scheduled_crawl = (
        payload.get("job_origin") == JOB_ORIGIN_SCHEDULER
        or job_type in (JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG)
    )
    if is_scheduled_crawl and expires_at is not None and now >= expires_at:
        return RESULT_REASON_STALE_CRAWL_SKIPPED
    return None


def preflight_decision(
    *,
    job_type: str,
    payload_json: object,
    now: "_dt",
) -> tuple[bool, str | None]:
    """Agent 가 브라우저/profile 을 열기 직전 server preflight 결정 ``(allowed, reason)``.

    Agent 는 claim 한 job 을 실행하기 전에 server 에 preflight 를 물어, payload TTL 이 지났거나
    서버 상태가 더는 그 작업을 원하지 않으면 **브라우저를 열지 않고** 안전히 닫는다. 1차 구현은
    payload ``expires_at`` 기반 stale 판정을 server_time(``now``)으로 재확인한다:

    * ``OPEN_AUTH_BROWSER`` / scheduled crawl 의 ``expires_at < now`` → denied
      (``RESULT_REASON_PAYLOAD_EXPIRED``).
    * 그 외(미만료/만료시각 없음) → allowed.

    payload 가 dict 가 아니거나 ``expires_at`` 가 없으면 allowed(보수적 — 기존 동작 보존).
    """

    stale = stale_recovery_reason(job_type=job_type, payload_json=payload_json, now=now)
    if stale is not None:
        return False, RESULT_REASON_PAYLOAD_EXPIRED
    return True, None


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
