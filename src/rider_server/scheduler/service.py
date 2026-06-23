"""async scheduler tick 오케스트레이션 — Story 5.4 (AC1·AC2·AC3·AC4).

순수 정책(:mod:`rider_server.scheduler.policy`)을 DB/queue I/O 와 와이어링한다. 정책↔DB 경계는
:class:`SchedulerRepository` 포트로 분리해(5.3 ``QueueBackend`` 추상화 선례와 동형) **always-run
in-memory fake** 와 **PostgreSQL 구현** 양쪽이 같은 tick 로직을 통과하게 한다 — 순수 정책은 sync
호출, DB/queue I/O 만 async(async 본문에서 blocking sync 직접 호출 금지 — 가드 준수).

tick 1회 흐름(architecture-contract.md:58-66 Scheduler Rules 정본):
  ① due 대상 질의(``next_run_at <= now``, 활성 status)
  ② tenant 구독·lifecycle 합성 게이트로 필터(비활성/중지 차단)
  ③ 플랫폼별 circuit breaker 평가(최근 15분 실패율) → open 플랫폼 skip
  ④ capacity/affinity throttle(capable+affine Agent & aggregate capacity 여유)
  ⑤ **멱등 enqueue**(활성 CrawlJob 없고 conditional advance 가 race 를 이겼을 때만)
  ⑥ ``next_run_at = now + interval + jitter`` 전진 + ``last_enqueued_at`` 기록

**enqueue 는 5.3 ``QueueBackend.enqueue`` 그대로 호출**(시그니처 변경 금지). scheduler 는 별도
process 라 ``create_app`` 라우트로 노출하지 않는다(architecture-contract.md:54) — 본 모듈은 호출
가능한 tick 함수/클래스만 제공하고, 주기 loop·``__main__``·compose 배선은 후속 배포 스토리 소유.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from rider_server.queue.backend import QueueBackend

from . import policy

#: scheduled crawl payload 의 ``job_origin`` 값 — 이 job 이 scheduler tick 에서 생성됐음을 표시.
#: recovery 가 "scheduled crawl" 을 식별해 stale backlog 를 안전하게 닫는 데 쓴다(Agent/manual
#: crawl 과 구분). secret 0(분류 코드).
JOB_ORIGIN_SCHEDULER = "scheduler"

#: Coupang 자동 이메일 2FA 복구 crawl 의 ``recovery_mode`` 값. result ingest 가 이 값으로
#: "자동 복구 결과"를 식별해 계정 cooldown 을 셋/클리어한다(Task 4). secret 0.
RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA = "coupang_auto_email_2fa"

# 신규 enqueue 결과/차단 사유 코드(UPPER_SNAKE — 평문 secret 없음, 결정 결과 가시성).
REASON_ENQUEUED = "ENQUEUED"
REASON_BREAKER_OPEN = "BREAKER_OPEN"
REASON_ACTIVE_JOB_EXISTS = "ACTIVE_JOB_EXISTS"
REASON_THROTTLED_CAPACITY = "THROTTLED_CAPACITY"
REASON_UNKNOWN_PLATFORM = "UNKNOWN_PLATFORM"
REASON_RACE_LOST = "RACE_LOST"
# ── 인증 상태 게이트 차단/허용 사유(Task 3) — 인증이 필요한 계정에 scheduled crawl 이 반복
# 생성되는 것을 scheduler 단계에서 막는다. 자동 복구는 한 번만 안전하게 허용한다. secret 0.
REASON_AUTH_REQUIRED_NO_AUTO_RECOVERY = "AUTH_REQUIRED_NO_AUTO_RECOVERY"
REASON_AUTH_STATE_USER_ACTION_PENDING = "AUTH_STATE_USER_ACTION_PENDING"
REASON_AUTH_STATE_BLOCKED_OR_CAPTCHA = "AUTH_STATE_BLOCKED_OR_CAPTCHA"
REASON_AUTH_STATE_UNKNOWN = "AUTH_STATE_UNKNOWN"
REASON_COUPANG_AUTO_RECOVERY_COOLDOWN = "COUPANG_AUTO_RECOVERY_COOLDOWN"

#: circuit breaker 집계 윈도(최근 15분, AC3).
DEFAULT_BREAKER_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True)
class DueTarget:
    """due 대상 한 건(중립 표현 — ORM Row 누출 금지). ``platform`` 은 ``Platform`` 값(문자열)."""

    target_id: str
    tenant_id: str
    platform: str
    interval_minutes: int
    next_run_at: datetime | None
    platform_account_id: str = ""
    primary_url: str = ""
    expected_display_name: str = ""
    username: str = ""
    password: str = ""
    verification_email_address: str = ""
    verification_email_app_password: str = ""
    verification_email_subject_keyword: str = "인증번호"
    verification_email_sender_keyword: str = "coupang"
    assigned_agent_id: str = ""
    # ── 인증 상태 + Coupang 자동 복구 cooldown facts(Task 3/4) ──────────────────
    # auth_state 는 ``PlatformAccount.auth_state``(BaeminAuthState 값). 미매핑/미상은 UNKNOWN
    # 으로 취급(fail-closed). auto_recovery_* 는 "한 번만 자동 복구 + 실패 뒤 cooldown" 을 계정
    # 단위로 강제하는 시간 facts(Task 4 가 DB 에 영속).
    auth_state: str = ""
    auto_recovery_attempted_at: datetime | None = None
    auto_recovery_failed_at: datetime | None = None
    auto_recovery_cooldown_until: datetime | None = None


@dataclass(frozen=True)
class TenantGate:
    """tenant 의 구독·lifecycle 상태(게이트 합성 입력). 미매핑은 ``None`` → fail-closed 차단."""

    subscription_status: object | None  # SubscriptionStatus | None
    lifecycle_status: object | None  # CustomerLifecycleState | None


@dataclass(frozen=True)
class ScheduleOutcome:
    """대상 한 건의 tick 결정 결과(불변). ``reason`` 은 enqueue 성공/차단 사유 코드."""

    target_id: str
    enqueued: bool
    reason: str
    job_id: str | None = None
    job_type: str | None = None
    warn_admin: bool = False


@dataclass(frozen=True)
class TickResult:
    """tick 1회 집계 결과(불변)."""

    outcomes: tuple[ScheduleOutcome, ...]
    enqueued_count: int

    @property
    def enqueued_target_ids(self) -> tuple[str, ...]:
        return tuple(o.target_id for o in self.outcomes if o.enqueued)


class SchedulerRepository(abc.ABC):
    """scheduler tick 의 DB 접근 포트(backend 중립). in-memory fake / PostgreSQL 구현 공용.

    구현은 정책 결정에 필요한 **중립 입력**만 노출하고(``AsyncSession``/SQL 누출 금지),
    멱등 전진은 :meth:`claim_due_target` 의 conditional UPDATE 로 동시 tick 경합을 차단한다.
    """

    @abc.abstractmethod
    async def due_targets(self, *, now: datetime, limit: int) -> list[DueTarget]:
        """``next_run_at <= now``(또는 NULL) & 활성 status 대상을 최대 ``limit``건 돌려준다."""

    @abc.abstractmethod
    async def tenant_gate(self, tenant_id: str) -> TenantGate:
        """tenant 의 구독 상태 + lifecycle 상태(게이트 합성 입력). 미매핑은 ``None``."""

    @abc.abstractmethod
    async def tenant_gates(self, tenant_ids: list[str]) -> dict[str, TenantGate]:
        """여러 tenant 의 게이트를 bulk 조회한다."""

    @abc.abstractmethod
    async def platform_failure_window(
        self, platform: str, *, since: datetime, now: datetime
    ) -> tuple[int, int]:
        """플랫폼의 최근 윈도 crawl job ``(total, failures)`` 집계(breaker 입력, AC3)."""

    @abc.abstractmethod
    async def has_active_crawl_job(self, target_id: str) -> bool:
        """대상에 활성 CrawlJob(PENDING/CLAIMED/RUNNING)이 이미 있는가(멱등성, AC4)."""

    @abc.abstractmethod
    async def active_crawl_job_target_ids(self, target_ids: list[str]) -> set[str]:
        """여러 target 중 활성 CrawlJob 이 있는 target id 집합을 bulk 조회한다."""

    @abc.abstractmethod
    async def capacity_snapshot(self, *, now: datetime) -> policy.CapacityPolicy:
        """Agent capacity/affinity 스냅샷(throttle 입력, AC1).

        ``now`` 는 scheduler tick 시각이다. online heartbeat 판단도 같은 시각으로 맞춘다.
        """

    @abc.abstractmethod
    async def claim_due_target(
        self, target_id: str, *, now: datetime, next_run_at: datetime
    ) -> bool:
        """대상을 이 tick 이 **원자적으로 선점**한다(conditional advance).

        ``next_run_at <= now``(또는 NULL)일 때만 ``next_run_at`` 을 ``next_run_at`` 인자로
        전진시키고 ``last_enqueued_at = now`` 를 기록한 뒤 True. 동시 tick(또는 두 worker)에서
        같은 대상은 **정확히 하나만** True 를 받는다(나머지 False=race lost) — 중복 due 작업 차단
        (AC4). [architecture-contract.md:66 "idempotent job creation"]
        """

    @abc.abstractmethod
    async def release_due_target(
        self,
        target_id: str,
        *,
        claimed_next_run_at: datetime,
        restore_next_run_at: datetime | None,
    ) -> bool:
        """enqueue 실패 시 선점 전 ``next_run_at`` 으로 되돌린다."""

    async def claim_due_target_and_enqueue(
        self,
        queue_backend: QueueBackend,
        target: DueTarget,
        *,
        job_type: str,
        payload_json: dict[str, object],
        now: datetime,
        next_run_at: datetime,
    ) -> str | None:
        """대상을 선점하고 job 을 만든다.

        기본 구현은 backend 중립 fallback 이다. PostgreSQL 구현은 이 메서드를 override 해
        target advance 와 job insert 를 같은 DB transaction 안에서 처리한다.
        """

        won = await self.claim_due_target(
            target.target_id, now=now, next_run_at=next_run_at
        )
        if not won:
            return None
        try:
            return await queue_backend.enqueue(
                job_type=job_type,
                target_id=target.target_id,
                payload_json=payload_json,
                assigned_agent_id=target.assigned_agent_id or None,
                run_after=now,
                now=now,
            )
        except Exception:
            await self.release_due_target(
                target.target_id,
                claimed_next_run_at=next_run_at,
                restore_next_run_at=target.next_run_at,
            )
            raise


class SchedulerService:
    """scheduler tick 오케스트레이터(정책↔포트↔queue 조립)."""

    def __init__(
        self,
        *,
        breaker_threshold: float = policy.DEFAULT_BREAKER_THRESHOLD,
        breaker_min_samples: int = policy.DEFAULT_BREAKER_MIN_SAMPLES,
        breaker_window: timedelta = DEFAULT_BREAKER_WINDOW,
        due_batch_size: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._breaker_threshold = breaker_threshold
        self._breaker_min_samples = breaker_min_samples
        self._breaker_window = breaker_window
        if due_batch_size is not None and batch_size is not None and due_batch_size != batch_size:
            raise ValueError("due_batch_size and batch_size must match when both are set")
        resolved_batch_size = batch_size if batch_size is not None else due_batch_size
        if resolved_batch_size is None:
            resolved_batch_size = 100
        if resolved_batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._due_batch_size = resolved_batch_size

    async def run_tick(
        self,
        repo: SchedulerRepository,
        queue_backend: QueueBackend,
        *,
        now: datetime,
    ) -> TickResult:
        """due 대상에 대해 게이트→breaker→capacity throttle→멱등 enqueue→next_run_at 전진을
        1회 수행하고 :class:`TickResult` 를 돌려준다.

        시각(``now``)은 호출부 주입(결정성). 게이트/breaker/capacity/활성-job/race 차단 같은
        **정책상 보류**는 해당 대상을 ``reason`` 으로 기록하고 다음 대상으로 진행한다(한 대상의
        정책 보류가 tick 전체를 막지 않음). 다만 ``claim_due_target``/``enqueue`` 의 **예기치 못한
        I/O 예외**는 잡지 않고 전파돼 tick 을 중단시킨다 — 다음 tick 이 같은 due 윈도를 재처리한다
        (멱등 전진이 conditional 이라 중복 없음, AC4).
        """

        due = await repo.due_targets(now=now, limit=self._due_batch_size)
        tenant_gates = await repo.tenant_gates(
            list(dict.fromkeys(target.tenant_id for target in due))
        )
        active_target_ids = await repo.active_crawl_job_target_ids(
            [target.target_id for target in due]
        )

        # ── 플랫폼별 breaker 를 tick 당 1회만 평가(대상별 중복 집계 회피, AC3) ──
        since = now - self._breaker_window
        breaker_open: dict[str, bool] = {}
        for target_platform in {t.platform for t in due}:
            total, failures = await repo.platform_failure_window(
                target_platform, since=since, now=now
            )
            breaker_open[target_platform] = policy.evaluate_breaker(
                total,
                failures,
                threshold=self._breaker_threshold,
                min_samples=self._breaker_min_samples,
            )

        capacity = await repo.capacity_snapshot(now=now)
        in_flight = capacity.aggregate_in_flight

        outcomes: list[ScheduleOutcome] = []
        enqueued_count = 0

        for target in due:
            # ② 구독 게이트 + lifecycle 합성 필터.
            gate = tenant_gates.get(target.tenant_id, TenantGate(None, None))
            decision = policy.decide_schedule(
                gate.subscription_status, gate.lifecycle_status
            )
            if not decision.allow_new_crawl_job:
                outcomes.append(
                    ScheduleOutcome(
                        target.target_id, False, decision.reason,
                        warn_admin=decision.warn_admin,
                    )
                )
                continue

            # ③ 플랫폼 circuit breaker.
            if breaker_open.get(target.platform, False):
                outcomes.append(
                    ScheduleOutcome(
                        target.target_id, False, REASON_BREAKER_OPEN,
                        warn_admin=decision.warn_admin,
                    )
                )
                continue

            # job type 매핑(미지 플랫폼 fail-closed — 임의 type 으로 claim 깨짐 방지).
            try:
                job_type = policy.crawl_job_type_for(target.platform)
            except ValueError:
                outcomes.append(
                    ScheduleOutcome(target.target_id, False, REASON_UNKNOWN_PLATFORM)
                )
                continue

            # ③-b 인증 상태 게이트(Task 3) — 인증 필요/막힘/미상 계정에는 scheduled crawl 을
            # 만들지 않는다. AUTH_REQUIRED Coupang 은 자동 2FA 설정 완전 + cooldown 없을 때만
            # 복구 crawl 1건을 받는다(한 번만 안전하게).
            auth_decision = policy.decide_auth_gate(
                auth_state=target.auth_state,
                platform=target.platform,
                auto_2fa_complete=_coupang_auto_2fa_complete(target),
                cooldown_until=target.auto_recovery_cooldown_until,
                now=now,
            )
            if not auth_decision.allow:
                outcomes.append(
                    ScheduleOutcome(
                        target.target_id, False, auth_decision.reason,
                        warn_admin=decision.warn_admin,
                    )
                )
                continue

            # ⑤-a 멱등성: 활성 CrawlJob 이 있으면 두 번째를 만들지 않는다(전진도 안 함 — 재진입 차단).
            if target.target_id in active_target_ids:
                outcomes.append(
                    ScheduleOutcome(
                        target.target_id, False, REASON_ACTIVE_JOB_EXISTS,
                        warn_admin=decision.warn_admin,
                    )
                )
                continue

            # ④ capacity/affinity throttle(이 tick 안에서 누적 in-flight 반영 — storm 방지).
            capacity_now = replace(capacity, aggregate_in_flight=in_flight)
            if not policy.can_admit(capacity_now, job_type):
                outcomes.append(
                    ScheduleOutcome(
                        target.target_id, False, REASON_THROTTLED_CAPACITY,
                        warn_admin=decision.warn_admin,
                    )
                )
                continue

            # ⑥ 멱등 선점 + enqueue. PostgreSQL 구현은 둘을 한 transaction 으로 묶는다.
            interval_seconds = target.interval_minutes * 60
            jitter = policy.compute_jitter(target.target_id, interval_seconds)
            advanced_next = policy.next_run_at(now, interval_seconds, jitter)
            crawl_payload = _crawl_job_payload(
                target, job_type, now=now, interval_seconds=interval_seconds
            )
            if auth_decision.recovery:
                # 허용된 Coupang 자동 복구 crawl — bounded recovery metadata 를 더한다.
                # 인증번호 값은 싣지 않는다(자동 2FA 플래그만). target/platform payload 는 normal
                # crawl 과 동일(같은 affinity·payload 계약).
                crawl_payload = {
                    **crawl_payload,
                    "recovery_mode": RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA,
                    "recovery_attempt": 1,
                }
            job_id = await repo.claim_due_target_and_enqueue(
                queue_backend,
                target,
                job_type=job_type,
                payload_json=crawl_payload,
                now=now,
                next_run_at=advanced_next,
            )
            if job_id is None:
                # 동시 tick 이 이미 선점/전진 → 중복 due 작업 차단(AC4).
                outcomes.append(
                    ScheduleOutcome(
                        target.target_id, False, REASON_RACE_LOST,
                        warn_admin=decision.warn_admin,
                    )
                )
                continue

            in_flight += 1
            enqueued_count += 1
            outcomes.append(
                ScheduleOutcome(
                    target.target_id, True, REASON_ENQUEUED,
                    job_id=job_id, job_type=job_type, warn_admin=decision.warn_admin,
                )
            )

        return TickResult(tuple(outcomes), enqueued_count)


def _iso_utc(dt: datetime) -> str:
    """timezone-aware datetime 을 ISO 8601 UTC(``…Z``)로 — epoch 혼용 금지(ADD-13)."""

    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _coupang_auto_2fa_complete(target: DueTarget) -> bool:
    """대상의 Coupang 자동 이메일 2FA 설정이 완전한가(로그인 + 이메일 ref 4종 모두 존재).

    auth gate 가 AUTH_REQUIRED Coupang 계정에 복구 crawl 을 허용할지 판단하는 입력이다.
    값(ref 문자열) 존재 여부만 보고 평문 secret 을 노출하지 않는다.
    """

    return bool(
        target.username
        and target.password
        and target.verification_email_address
        and target.verification_email_app_password
    )


def _crawl_job_payload(
    target: DueTarget,
    job_type: str,
    *,
    now: datetime,
    interval_seconds: int,
) -> dict[str, object]:
    platform = str(target.platform or "").strip().casefold()
    payload: dict[str, object] = {
        "target_id": target.target_id,
        "tenant_id": target.tenant_id,
        "platform": platform,
        "platform_account_id": target.platform_account_id,
        "primary_url": target.primary_url,
        "expected_display_name": target.expected_display_name,
        "browser_profile_ref": f"profile:{target.target_id}",
        "timeout_seconds": 60,
        "parser_version": f"{platform}-v1",
        "job_type": job_type,
        # scheduled crawl 의 출처/시간 경계 — recovery 가 stale backlog 를 안전하게 닫는 기준.
        # expires_at 은 scheduled_at 으로부터 최대 1 interval 뒤(다음 due 윈도가 열리기 전까지만
        # 유효 — 서버 downtime 뒤 누적된 missed-interval job 이 한 번에 재생되는 것을 막는다).
        "job_origin": JOB_ORIGIN_SCHEDULER,
        "scheduled_at": _iso_utc(now),
        "expires_at": _iso_utc(now + timedelta(seconds=max(0, interval_seconds))),
    }
    if platform == "coupang":
        payload.update(
            {
                "coupang_login_id_ref": target.username,
                "coupang_login_password_ref": target.password,
                "verification_email_address_ref": target.verification_email_address,
                "verification_email_app_password_ref": target.verification_email_app_password,
                "verification_email_subject_keyword": target.verification_email_subject_keyword,
                "verification_email_sender_keyword": target.verification_email_sender_keyword,
                "coupang_auto_email_2fa_enabled": bool(
                    target.username
                    and target.password
                    and target.verification_email_address
                    and target.verification_email_app_password
                ),
            }
        )
    return payload
