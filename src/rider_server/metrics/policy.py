"""순수 지표·알림 정책 — Story 5.9 (AC1·AC2).

5.4 ``scheduler.policy`` / 5.6 ``admin.severity`` 정본을 계승한다: **FastAPI/SQLAlchemy/async
의존 0**, 내부에서 ``datetime.now()``/``random`` 을 **호출하지 않는다**(시각·임계는 호출부
주입 — always-run 결정성). DB I/O 는 :mod:`rider_server.metrics.service` 의 async repository
소유다. 알림 코드/심각도는 기존 enum 에 멤버를 추가하지 않고 **plain-string 상수**로 둔다(5.4
``BREAKER_OPEN``/5.6 ``SEVERITY_*`` 선례 — ``test_domain_states`` count-lock 회피).

**임계 drift 0(재사용 강제):** crawl 임계는 scheduler 정본, agent offline 은 severity 정본을
**그대로** 쓴다(같은 의미의 임계를 두 곳에서 다른 값으로 두지 않는다). private 상수
(``_TELEGRAM_ERROR_WINDOW``)는 import 불가라 동일값을 재선언하고 ``test_metrics_policy`` 가
identity/동등으로 잠근다.

**비식별(redaction) 1차 방어선:** :class:`MetricsSnapshot` 은 **집계 수치(count/rate/gauge)만**
담는다 — 고객명·센터/상점명·target_id·방명 등 식별 텍스트를 애초에 담지 않는다(``redact()`` 가
운영 ID 를 마스킹하지 않으므로 redaction 에 의존하지 않고 payload 에서 제외한다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rider_server.admin import severity
from rider_server.scheduler import policy as scheduler_policy

# ══════════════════════════════════════════════════════════════════════════
# 임계 상수 — 기존 정본 재사용(재정의 금지·drift 방지)
# ══════════════════════════════════════════════════════════════════════════

#: agent offline 임계(2분) — severity 정본 그대로(``is_agent_online`` 과 같은 값을 쓴다).
AGENT_OFFLINE_AFTER: timedelta = severity.AGENT_OFFLINE_AFTER

#: crawl 실패율 임계/표본 가드 — scheduler 정본 재사용(``evaluate_breaker`` 와 동일값).
#: min_samples 가 1/1=100% 소표본 오탐을 막는다 — 반드시 함께 적용.
DEFAULT_BREAKER_THRESHOLD: float = scheduler_policy.DEFAULT_BREAKER_THRESHOLD
DEFAULT_BREAKER_MIN_SAMPLES: int = scheduler_policy.DEFAULT_BREAKER_MIN_SAMPLES

#: crawl 집계 윈도(최근 15분). scheduler ``service.DEFAULT_BREAKER_WINDOW`` 와 동일값을
#: 재선언하고 ``test_metrics_policy`` 가 동등으로 잠근다(import 시 async service 의존 회피).
DEFAULT_BREAKER_WINDOW: timedelta = timedelta(minutes=15)

#: Telegram 전송 오류 집계 윈도(최근 10분). dashboard ``_TELEGRAM_ERROR_WINDOW`` 는 private 라
#: 동일값을 재선언하고 동등 테스트로 잠근다(NFR-14 정합).
TELEGRAM_ERROR_WINDOW: timedelta = timedelta(minutes=10)

#: kakao queue lag 알림 임계(120초 — NFR-14 "120s 반복 초과", **초과(>)** 일 때 발화).
QUEUE_LAG_ALERT_SECONDS: int = 120

#: Telegram 오류 급증으로 보는 최소 카운트(10분 윈도). 운영 정본 임계가 없어 fail-loud(≥1)로
#: 두고 실 알람(CloudWatch) 임계는 deploy/운영 설정에서 튜닝한다(api_error_rate.md 기록).
TELEGRAM_ERROR_ALERT_MIN: int = 1

#: 인증 필요 알림 임계(≥1 이면 alert). auth_required / email auth 공통.
AUTH_REQUIRED_ALERT_MIN: int = 1
EMAIL_AUTH_ALERT_MIN: int = 1

# ══════════════════════════════════════════════════════════════════════════
# 알림 코드(plain-string 상수 — 새 Enum 금지, AC2 최소 4종)
# ══════════════════════════════════════════════════════════════════════════
# AC2 정본 표기(소문자) 그대로 — 새 Enum 을 만들지 않아 ``test_domain_states`` count-lock 무관.
ALERT_AGENT_OFFLINE = "agent_offline"
ALERT_QUEUE_LAG = "queue_lag"
ALERT_API_ERROR_RATE = "api_error_rate"
ALERT_AUTH_REQUIRED = "auth_required"

#: 최소 알림 4종(AC2). tuple 로 우발 변이를 막는다.
MINIMUM_ALERT_CODES: tuple[str, ...] = (
    ALERT_AGENT_OFFLINE,
    ALERT_QUEUE_LAG,
    ALERT_API_ERROR_RATE,
    ALERT_AUTH_REQUIRED,
)


# ══════════════════════════════════════════════════════════════════════════
# 집계 facts 스냅샷(비식별 — 식별 텍스트 금지, AC1)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MetricsSnapshot:
    """7개 모니터링 지표의 **비식별 fleet 집계 facts**(중립 타입만).

    고객명·센터/상점명·target_id 등 식별 텍스트는 **담지 않는다**(unauthenticated scrape 안전).
    심각도/알림 판정은 :func:`evaluate_alerts` 가 시각·임계 주입으로 결정한다(여긴 facts 만).

    지표 매핑(AC1):
      1. ``agent_last_heartbeat`` → ``agents_total``/``agents_offline``/``oldest_heartbeat_age_seconds``
      2. ``target_last_success_at`` → ``targets_warning``/``targets_critical``(개별 timestamp/이름 금지)
      3. ``auth_required_count``
      4. ``kakao_queue_lag_seconds``(fleet max)
      5. ``crawl_error_rate_by_platform``(플랫폼별 rate) + ``crawl_samples_by_platform``(표본수)
      6. ``telegram_error_count``(10분 윈도)
      7. ``gmail_reauth_required_count``
    """

    # 1. agent heartbeat
    agents_total: int = 0
    agents_offline: int = 0
    oldest_heartbeat_age_seconds: int | None = None
    # 2. target freshness(개별 노출 금지 — warning/critical **대상 수**만)
    targets_total: int = 0
    targets_warning: int = 0
    targets_critical: int = 0
    # 3. auth required
    auth_required_count: int = 0
    # 4. kakao queue lag(fleet 최댓값, 초)
    kakao_queue_lag_seconds: int = 0
    # 5. crawl 실패율(플랫폼별 BAEMIN/COUPANG) + 표본수
    crawl_error_rate_by_platform: dict[str, float] = field(default_factory=dict)
    crawl_samples_by_platform: dict[str, int] = field(default_factory=dict)
    # 6. telegram 전송 오류(10분 윈도 카운트)
    telegram_error_count: int = 0
    # 7. gmail reauth(쿠팡 미해결 auth_session 근사)
    gmail_reauth_required_count: int = 0

    def to_payload(self) -> dict:
        """``/metrics/operational`` JSON 용 집계-only payload(snake_case·식별 텍스트 0)."""
        return {
            "agents_total": self.agents_total,
            "agents_offline": self.agents_offline,
            "oldest_heartbeat_age_seconds": self.oldest_heartbeat_age_seconds,
            "targets_total": self.targets_total,
            "targets_warning": self.targets_warning,
            "targets_critical": self.targets_critical,
            "auth_required_count": self.auth_required_count,
            "kakao_queue_lag_seconds": self.kakao_queue_lag_seconds,
            "crawl_error_rate_by_platform": dict(self.crawl_error_rate_by_platform),
            "crawl_samples_by_platform": dict(self.crawl_samples_by_platform),
            "telegram_error_count": self.telegram_error_count,
            "gmail_reauth_required_count": self.gmail_reauth_required_count,
        }


# ══════════════════════════════════════════════════════════════════════════
# 알림(plain-string 코드 + 심각도 상수 재사용)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Alert:
    """발화 알림 한 건(불변). ``code`` 는 plain-string 알림 코드, ``severity`` 는 severity.py
    plain-string 상수(``WARNING``/``CRITICAL``) — enum 멤버 추가 금지."""

    code: str
    severity: str


def crawl_alert_open(
    error_rate: float,
    samples: int,
    *,
    threshold: float = DEFAULT_BREAKER_THRESHOLD,
    min_samples: int = DEFAULT_BREAKER_MIN_SAMPLES,
) -> bool:
    """플랫폼 crawl 실패율이 알림 임계를 넘었는가(scheduler ``evaluate_breaker`` 와 동치).

    ``samples >= min_samples`` 이고 ``error_rate > threshold`` 일 때만 True. 표본 부족이면
    ``1/1=100%`` 오탐을 막기 위해 False(min_samples 가드). rate 위에서 판정하지만 결과는
    ``evaluate_breaker(total, failures)`` 와 동일하다(``test_metrics_policy`` 가 동등 잠금)."""

    if samples < min_samples or samples <= 0:
        return False
    return error_rate > threshold


def evaluate_alerts(
    snapshot: MetricsSnapshot, *, now: datetime | None = None
) -> tuple[Alert, ...]:
    """비식별 스냅샷에서 **최소 4개 알림** 발화를 결정한다(순수·결정적, AC2).

    스냅샷은 이미 시각이 해석된(offline/freshness 집계 완료) facts 라 판정은 임계 비교만
    한다 — ``now`` 는 시그니처 대칭/향후 확장용이며 현재 분기는 사용하지 않는다(snapshot 조립
    단계에서 ``now`` 주입으로 시간 의존을 이미 결정).

    - ``agent_offline``: offline agent ≥ 1 → CRITICAL.
    - ``queue_lag``: kakao lag > 120초 → WARNING.
    - ``api_error_rate``: 어느 플랫폼이든 crawl 실패율 > 30% & 표본 ≥ min_samples, **또는**
      telegram 오류 ≥ min(10분 윈도) → CRITICAL.
    - ``auth_required``: auth_required ≥ 1 **또는** gmail_reauth ≥ 1 → WARNING.

    임계는 AC1 정본(2분/120초/30%+min_samples/≥1)을 그대로 쓴다(drift 0).
    """

    del now  # facts 는 조립 단계에서 시각이 해석됨 — 알림은 임계 비교만(시그니처 대칭).
    alerts: list[Alert] = []

    if snapshot.agents_offline >= 1:
        alerts.append(Alert(ALERT_AGENT_OFFLINE, severity.SEVERITY_CRITICAL))

    if snapshot.kakao_queue_lag_seconds > QUEUE_LAG_ALERT_SECONDS:
        alerts.append(Alert(ALERT_QUEUE_LAG, severity.SEVERITY_WARNING))

    if _api_error_rate_alerting(snapshot):
        alerts.append(Alert(ALERT_API_ERROR_RATE, severity.SEVERITY_CRITICAL))

    if (
        snapshot.auth_required_count >= AUTH_REQUIRED_ALERT_MIN
        or snapshot.gmail_reauth_required_count >= EMAIL_AUTH_ALERT_MIN
    ):
        alerts.append(Alert(ALERT_AUTH_REQUIRED, severity.SEVERITY_WARNING))

    return tuple(alerts)


def _api_error_rate_alerting(snapshot: MetricsSnapshot) -> bool:
    """``api_error_rate`` 발화 판정 — crawl breaker(플랫폼별) **또는** telegram 급증."""
    for platform, rate in snapshot.crawl_error_rate_by_platform.items():
        samples = snapshot.crawl_samples_by_platform.get(platform, 0)
        if crawl_alert_open(rate, samples):
            return True
    return snapshot.telegram_error_count >= TELEGRAM_ERROR_ALERT_MIN
