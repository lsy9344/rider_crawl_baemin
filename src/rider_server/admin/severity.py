"""순수 심각도 분류 정책 — Story 5.6 (AC2·AC3).

5.4 :mod:`rider_server.scheduler.policy` 정본을 계승한다: **FastAPI/SQLAlchemy/async 의존 0**,
내부에서 ``datetime.now()``/``random`` 을 **호출하지 않는다**(시각·임계는 호출부 주입 — 테스트
결정성·always-run). DB I/O 는 :mod:`rider_server.admin.dashboard_service` 의 async repository
소유다. 심각도 어휘는 기존 enum 에 멤버를 추가하지 않고 **plain-string 상수**로 둔다(5.4
``BREAKER_OPEN``/``BREAKER_CLOSED`` 선례 — ``test_domain_states`` count-lock 회피). UI 한글
라벨(정상/주의/위험/중지)은 :mod:`rider_server.admin.routes` 템플릿 매핑이 표현한다.

심각도 계산 두 축(AC2·AC3):
  (1) **시간 경과**(:func:`classify_freshness`): 마지막 수집 성공 시각이 ``interval×2`` 초과면
      주의, ``interval×4`` 초과면 위험(ops-contract:26 "Over interval x 2 / over interval x 4").
  (2) **fail-closed 우선 신호**(:func:`classify_failclosed`): 인증 필요·기대 대상 검증 실패·
      Kakao 오발송 위험은 자동 전송보다 **중지(STOPPED)** 를 우선한다.
:func:`overall_severity` 가 둘을 병합하되 **fail-closed 가 시간 경과보다 우선**한다(AC3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from rider_server.domain import (
    BaeminAuthState,
    CustomerLifecycleState,
    FailureCategory,
)

# ── 심각도 4단계(plain-string 상수 — enum 아님 → count-lock 무관) ────────────────────
SEVERITY_NORMAL = "NORMAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_STOPPED = "STOPPED"
SEVERITY_AUTH_REQUIRED = "AUTH_REQUIRED"
SEVERITY_TARGET_VALIDATION_FAILURE = "TARGET_VALIDATION_FAILURE"
SEVERITY_KAKAO_MISDELIVERY_RISK = "KAKAO_MISDELIVERY_RISK"
SEVERITY_OPERATOR_STOPPED = "OPERATOR_STOPPED"

#: 인증이 "정상"으로 확인된 auth_state 집합. stale AUTH_REQUIRED 실패 코드보다 우선해
#: fail-closed 인증 신호를 끈다. UNKNOWN 은 중립(미판정)이라 제외한다.
HEALTHY_AUTH_STATES: frozenset[str] = frozenset(
    {BaeminAuthState.ACTIVE.value, BaeminAuthState.AUTH_VERIFIED.value}
)

#: 사람 조치가 필요한 계정 인증 상태. 스케줄러가 crawl 을 막는 상태는 대시보드에서도
#: 조치 필요로 올려야 운영자가 멈춘 이유를 볼 수 있다.
ACTION_REQUIRED_AUTH_STATES: frozenset[str] = frozenset(
    {
        BaeminAuthState.AUTH_REQUIRED.value,
        BaeminAuthState.USER_ACTION_PENDING.value,
        BaeminAuthState.BLOCKED_OR_CAPTCHA.value,
    }
)

#: 정본 심각도 4종(낮음→높음 순). tuple 로 두어 우발적 변이를 막는다.
SEVERITIES: tuple[str, ...] = (
    SEVERITY_NORMAL,
    SEVERITY_WARNING,
    SEVERITY_CRITICAL,
    SEVERITY_STOPPED,
)

# 심각도 순위(높을수록 위험) — 정렬/표시 우선순위용.
_STOPPED_RANK = SEVERITIES.index(SEVERITY_STOPPED)
_SEVERITY_RANK: dict[str, int] = {
    SEVERITY_NORMAL: SEVERITIES.index(SEVERITY_NORMAL),
    SEVERITY_WARNING: SEVERITIES.index(SEVERITY_WARNING),
    SEVERITY_CRITICAL: SEVERITIES.index(SEVERITY_CRITICAL),
    SEVERITY_STOPPED: _STOPPED_RANK,
    SEVERITY_AUTH_REQUIRED: _STOPPED_RANK,
    SEVERITY_TARGET_VALIDATION_FAILURE: _STOPPED_RANK,
    SEVERITY_KAKAO_MISDELIVERY_RISK: _STOPPED_RANK,
    SEVERITY_OPERATOR_STOPPED: _STOPPED_RANK,
}

#: agent offline 임계(ops-contract:25 "Missing for more than 2 minutes").
AGENT_OFFLINE_AFTER = timedelta(minutes=2)


# ══════════════════════════════════════════════════════════════════════════
# AC2 — 마지막 성공 시각 기반 시간 경과 심각도
# ══════════════════════════════════════════════════════════════════════════

def classify_freshness(
    last_success_at: datetime | None,
    interval_minutes: int,
    now: datetime,
) -> str:
    """마지막 수집 성공 시각으로 시간 경과 심각도를 분류한다(AC2, 순수·결정적).

    우선순위(명시적 결정):
      1. ``last_success_at is None``(한 번도 성공 못함) → 최소 :data:`SEVERITY_WARNING`
         (interval 설정과 무관 — 성공 이력이 없으면 정상으로 볼 수 없다).
      2. ``interval_minutes <= 0``(미설정) → :data:`SEVERITY_NORMAL`(시간 경과 평가 skip;
         임계가 없어 staleness 를 판단할 수 없음. fail-closed 신호는 :func:`overall_severity`
         가 그대로 우선 적용한다).
      3. ``now - last_success_at`` 가 ``interval×4`` **초과** → :data:`SEVERITY_CRITICAL`,
         ``interval×2`` **초과** → :data:`SEVERITY_WARNING`, 그 외 :data:`SEVERITY_NORMAL`.

    경계(정확히 ×2/×4)는 "초과(>)"라 하위 등급으로 떨어진다(ops-contract:26 "Over" 정본).
    """

    if last_success_at is None:
        return SEVERITY_WARNING
    if interval_minutes <= 0:
        return SEVERITY_NORMAL
    elapsed = now - last_success_at
    interval = timedelta(minutes=interval_minutes)
    if elapsed > interval * 4:
        return SEVERITY_CRITICAL
    if elapsed > interval * 2:
        return SEVERITY_WARNING
    return SEVERITY_NORMAL


# ══════════════════════════════════════════════════════════════════════════
# AC3 — fail-closed 우선 신호(자동 전송보다 중지 우선)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FailClosedSignals:
    """fail-closed 우선 신호 묶음(불변). 하나라도 True 면 자동 전송보다 중지를 우선한다(AC3)."""

    auth_required: bool = False
    target_validation_failed: bool = False
    kakao_misdelivery_risk: bool = False

    @property
    def any_signal(self) -> bool:
        return (
            self.auth_required
            or self.target_validation_failed
            or self.kakao_misdelivery_risk
        )


def failclosed_signals_from(
    *,
    account_auth_state: str | None,
    lifecycle_state: str | None,
    latest_failure_code: str | None,
    auth_session_pending: bool = False,
    last_success_at: datetime | None = None,
    latest_failure_at: datetime | None = None,
) -> FailClosedSignals:
    """정본 어휘(:class:`BaeminAuthState`/:class:`CustomerLifecycleState`/:class:`FailureCategory`)
    를 fail-closed 신호로 매핑한다(순수·결정적, AC3).

    - **인증 필요**: ``platform_accounts.auth_state`` 가 사람 조치 상태 /
      ``tenant lifecycle == AUTH_REQUIRED`` / 최신 실패 ``error_code == AUTH_REQUIRED`` /
      ``auth_sessions`` 인증대기(``auth_session_pending``).
    - **기대 대상 검증 실패**: ``auth_state == CENTER_MISMATCH`` /
      최신 ``error_code == TARGET_VALIDATION_FAILURE``.
    - **Kakao 오발송 위험**: 최신 ``error_code == KAKAO_FAILURE``.

    "어떤 enum 값이 중지를 뜻하는가"라는 정책 지식을 순수 함수 한곳에 모아 always-run 으로
    잠근다(타입별 동명 멤버 ``AUTH_REQUIRED`` 혼동 방지 — 여기선 **문자열 값**으로만 비교).

    인증 후에도 묵은 실패 코드가 배지를 띄우는 회귀를 막는다(2026-06 수정):
    - ``account_auth_state`` 가 :data:`HEALTHY_AUTH_STATES`(``ACTIVE``/``AUTH_VERIFIED``)면
      **인증 필요 신호를 끈다** — 권위 있는 인증 상태가 묵은 ``error_code`` 보다 우선한다(실제
      재실패 시 에이전트가 다시 ``AUTH_REQUIRED`` 로 뒤집으므로 현재 실패를 숨기지 않는다).
    - 최신 실패 시각이 마지막 성공 시각보다 과거(``<=``)면 **stale** 로 보고 실패-코드 신호를
      끈다. 시각이 하나라도 없으면 stale 로 보지 않는다(보수적 — 실제 실패를 숨기지 않음).
    """

    auth_verified = account_auth_state in HEALTHY_AUTH_STATES
    # 최신 실패가 마지막 성공보다 과거(<=)면 stale — 더 최근 성공이 덮었다고 본다.
    failure_is_stale = (
        last_success_at is not None
        and latest_failure_at is not None
        and latest_failure_at <= last_success_at
    )
    failure_code_active = None if failure_is_stale else latest_failure_code

    # 계정 인증이 정상(ACTIVE/AUTH_VERIFIED)이면 **계정 유래** 인증필요 신호(계정 상태·실패
    # 코드)만 끈다. tenant lifecycle 의 AUTH_REQUIRED 와 진행 중 auth_session 은 계정 인증과
    # 독립한 신호라 그대로 둔다(거짓 음성 방지).
    account_auth_required = not auth_verified and (
        account_auth_state in ACTION_REQUIRED_AUTH_STATES
        or failure_code_active == FailureCategory.AUTH_REQUIRED.value
    )
    auth_required = (
        account_auth_required
        or lifecycle_state == CustomerLifecycleState.AUTH_REQUIRED.value
        or auth_session_pending
    )
    target_validation_failed = (
        account_auth_state == BaeminAuthState.CENTER_MISMATCH.value
        or failure_code_active == FailureCategory.TARGET_VALIDATION_FAILURE.value
    )
    kakao_misdelivery_risk = (
        failure_code_active == FailureCategory.KAKAO_FAILURE.value
    )
    return FailClosedSignals(
        auth_required=auth_required,
        target_validation_failed=target_validation_failed,
        kakao_misdelivery_risk=kakao_misdelivery_risk,
    )


def classify_failclosed(signals: FailClosedSignals) -> str | None:
    """fail-closed 신호가 하나라도 있으면 :data:`SEVERITY_STOPPED`(중지 우선), 없으면 ``None``.

    AC3 권장 결정: 자동 전송보다 **중지를 우선**하므로 최고 등급 ``STOPPED`` 를 반환한다.
    ``None`` 은 "fail-closed 신호 없음 → 시간 경과 심각도를 그대로 쓰라"는 뜻이다.
    """

    if signals.any_signal:
        return SEVERITY_STOPPED
    return None


def overall_severity(freshness: str, failclosed: str | None) -> str:
    """시간 경과 심각도와 fail-closed 신호를 병합한다(AC3, 순수).

    **fail-closed > 시간 경과**: fail-closed 값이 있으면 그 값을 쓰고(예: 인증 필요면 마지막
    수집 성공이 최근이어도 ``STOPPED``), 없으면 시간 경과 값을 쓴다. 순서가 뒤집히지 않음을
    단위 테스트로 잠근다(Task 1.4).
    """

    if failclosed is not None:
        return failclosed
    return freshness


def severity_rank(severity: str) -> int:
    """심각도 순위(미지값은 0=정상 취급). 표시 정렬/우선순위 비교용."""

    return _SEVERITY_RANK.get(severity, 0)


# ══════════════════════════════════════════════════════════════════════════
# AC1 — agent online/offline 판정(시각 주입, 순수)
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# crawl-coupang-auth-separation Task 9 — 쿠팡 자동 인증 복구 세부 사유 표시
# ══════════════════════════════════════════════════════════════════════════
# 계정 gate 상태(AUTH_REQUIRED 등)는 그대로 red/yellow 심각도를 만든다. 그 위에, AUTH_COUPANG_2FA
# job 의 세부 복구 상태(``auth_recovery_state``)/reason 을 운영자에게 보이는 한글 detail 로
# 매핑한다 — "크롤 실패"가 아니라 "메일 인증 필요/인증 메일 지연/캡차·이상 로그인/자동 인증 실패"
# 로 구분해 조치를 안내한다. secret 0(분류 코드만 입력으로 받는다).

#: 세부 복구 상태(result_json.auth_recovery_state) → 한글 detail 라벨.
COUPANG_RECOVERY_DETAIL_BY_STATE: dict[str, str] = {
    "EMAIL_AUTH_REQUIRED": "메일 인증 필요",
    "USER_ACTION_REQUIRED": "캡차/이상 로그인",
    "RECOVERY_FAILED": "자동 인증 실패",
}

#: 고정 reason(result_json.reason / metrics.reason) → 한글 detail 라벨(상태보다 구체적인 경우).
COUPANG_RECOVERY_DETAIL_BY_REASON: dict[str, str] = {
    "email_auth_required": "메일 인증 필요",
    "captcha_or_abnormal_login": "캡차/이상 로그인",
    "verification_mail_delayed": "인증 메일 지연",
    "repeated_recovery_failure": "자동 인증 실패",
    "browser_unavailable": "브라우저 연결 실패",
}


def coupang_recovery_detail_label(
    *,
    auth_recovery_state: str | None,
    reason: str | None = None,
) -> str | None:
    """쿠팡 자동 인증 복구 세부 사유의 한글 detail 라벨(없거나 정상 복구면 ``None``).

    reason 이 더 구체적이면(예: ``verification_mail_delayed`` → "인증 메일 지연") reason 매핑을
    우선한다. ``ACTIVE``(복구 성공)와 미매핑은 detail 을 만들지 않는다(gate 심각도만 표시).
    입력은 기계가독 분류 코드뿐 — secret 을 받지 않는다.
    """

    reason_label = COUPANG_RECOVERY_DETAIL_BY_REASON.get(str(reason or "").strip())
    if reason_label is not None:
        return reason_label
    return COUPANG_RECOVERY_DETAIL_BY_STATE.get(str(auth_recovery_state or "").strip())


def is_agent_online(
    last_heartbeat_at: datetime | None,
    now: datetime,
    *,
    offline_after: timedelta = AGENT_OFFLINE_AFTER,
) -> bool:
    """Agent heartbeat 가 ``now - offline_after`` 보다 오래되면 offline(False).

    ``last_heartbeat_at is None``(한 번도 heartbeat 없음) → offline. 경계(정확히 2분 경과)는
    online — ops-contract:25 "Missing for **more than** 2 minutes"라 **초과(>)** 일 때만 offline.
    """

    if last_heartbeat_at is None:
        return False
    return (now - last_heartbeat_at) <= offline_after
