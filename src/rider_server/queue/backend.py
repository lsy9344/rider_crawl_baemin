"""``QueueBackend`` 인터페이스 + 중립 반환형 — Story 5.3 (AC1·AC2).

job queue 를 backend 중립 인터페이스로 추상화한다. PostgreSQL ``jobs`` 테이블이 정본
구현이지만, 인터페이스는 **PG/Redis/SQS 어디서든 구현 가능**한 중립형(원시 타입/dataclass)만
노출한다 — ``AsyncSession``·SQL·``FOR UPDATE`` 같은 PG 세부를 인터페이스에 새지 않는다(AC1,
P4-05). 그래서 backend-중립 계약 테스트 suite 가 in-memory 구현과 PostgreSQL 구현 양쪽에
동일하게 통과해 "구현을 Redis/SQS 로 옮길 수 있음"을 보장한다.

모든 메서드는 ``async`` 다 — ``rider_server`` 가 async 런타임이고 PG 구현이 ``AsyncSession``
기반이라 인터페이스를 async 로 통일한다(in-memory 구현은 async 함수 본문에서 동기 작업만 한다).
반환형(:class:`ClaimedJobRecord`/:class:`CompleteOutcome`)은 ORM Row 가 아닌 **중립 dataclass**
라 PG 세부가 api/agent 로 누출되지 않는다(레이어 분리).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from .states import JOB_STATUS_CLAIMED

# ── complete 결과 코드(중립) — 라우트가 HTTP 상태로 매핑 ─────────────────────────
#: complete 가 정상 수락됨 → 2xx.
COMPLETE_ACCEPTED = "ACCEPTED"
#: lease 만료/재할당으로 이 Agent 가 더는 소유하지 않음 → 409(Agent 가 lease_lost 로 흡수).
COMPLETE_LEASE_LOST = "LEASE_LOST"
#: 해당 job_id 가 존재하지 않음 → 404.
COMPLETE_NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class ClaimedJobRecord:
    """claim 으로 잡은 job 한 건의 중립 표현(ORM Row 누출 금지).

    ``lease_expires_at`` 는 timezone-aware ``datetime`` 으로 들고(api 경계에서 ISO 8601 UTC
    로 직렬화), ``job_id``/``target_id`` 는 문자열(UUID) 로 노출한다 — backend 중립.
    """

    job_id: str
    type: str
    target_id: str | None
    lease_expires_at: datetime
    payload_json: dict[str, Any] | None = None
    attempts: int = 0
    status: str = JOB_STATUS_CLAIMED


@dataclass(frozen=True)
class CompleteOutcome:
    """complete 결과(중립). ``result`` 는 :data:`COMPLETE_ACCEPTED` 등 중립 코드."""

    result: str
    job_id: str
    final_status: str | None = None

    @property
    def accepted(self) -> bool:
        return self.result == COMPLETE_ACCEPTED


class QueueBackend(abc.ABC):
    """job queue backend 추상 인터페이스(AC1).

    구현: :class:`~rider_server.queue.memory_queue.InMemoryQueueBackend`(DB-less, 계약
    테스트 always-run 대상) / :class:`~rider_server.queue.postgres_queue.PostgresQueueBackend`
    (``FOR UPDATE SKIP LOCKED`` 정본). 시각은 호출부가 주입(``now``)해 lease 만료를 결정적으로
    검증한다.
    """

    @abc.abstractmethod
    async def enqueue(
        self,
        *,
        job_type: str,
        target_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
        run_after: datetime | None = None,
        now: datetime,
    ) -> str:
        """``PENDING`` job 을 생성하고 job_id 를 돌려준다(5.4 scheduler 가 호출)."""

    @abc.abstractmethod
    async def claim(
        self,
        *,
        agent_id: str,
        capabilities: Sequence[str],
        max_jobs: int,
        lease_seconds: float,
        now: datetime,
    ) -> list[ClaimedJobRecord]:
        """capability 매칭되는 ``PENDING`` job 을 최대 ``max_jobs`` 건 claim 한다.

        claim 시 한 트랜잭션에서 ``CLAIMED`` + ``agent_id`` + ``lease_expires_at``(=now+lease)
        + ``claimed_at`` 를 부여한다. 동시 claim 에서 같은 job 은 **정확히 하나만** 성공한다
        (PG=``FOR UPDATE SKIP LOCKED``, in-memory=lock). 빈 큐면 ``[]``.
        """

    @abc.abstractmethod
    async def in_flight_job(
        self,
        *,
        job_id: str,
        agent_id: str,
        now: datetime,
    ) -> ClaimedJobRecord | None:
        """이 Agent 가 아직 소유 중인 진행 job 을 조회한다.

        ``agent_id`` 소유 + 미만료 + 진행 중(CLAIMED/RUNNING)이면 중립 record 를 돌려주고,
        미존재/재할당/만료/종료 상태면 ``None`` 을 돌려준다. 완료 전 snapshot 검증처럼 job 의
        target 계약을 확인해야 하지만 상태를 아직 바꾸면 안 되는 경로에서 쓴다.
        """

    @abc.abstractmethod
    async def complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None = None,
        error_code: str | None = None,
        now: datetime,
    ) -> CompleteOutcome:
        """job 을 완료 처리한다(lease 소유 검증 포함).

        이 job 이 여전히 ``agent_id`` 소유 + 미만료 + 진행 중(CLAIMED/RUNNING)이면 ``status``
        (SUCCEEDED/FAILED)로 전이하고 :data:`COMPLETE_ACCEPTED`. 만료/재할당이면
        :data:`COMPLETE_LEASE_LOST`(라우트가 409), 미존재면 :data:`COMPLETE_NOT_FOUND`(404).
        이로써 재할당된 job 의 이중 success 기록을 차단한다(AC2).
        """

    @abc.abstractmethod
    async def extend_lease(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_seconds: float,
        now: datetime,
    ) -> bool:
        """heartbeat 연장 입력 — 소유 + 미만료면 lease 를 ``now+lease`` 로 연장(True)."""

    @abc.abstractmethod
    async def recover_stale(self, *, now: datetime) -> int:
        """만료 lease(CLAIMED/RUNNING & lease_expires_at<=now)를 ``PENDING`` 재진입시킨다.

        회수한 job 수를 돌려준다. 회수된 job 은 다른 Agent 에 재할당 가능(AC2).
        """

    @abc.abstractmethod
    async def emit_event(
        self,
        *,
        job_id: str,
        event_type: str,
        severity: str,
        message_redacted: str,
        artifact_refs: Sequence[Any] = (),
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """job 진행 이벤트를 best-effort 로 받는다(본문은 이미 redact 통과값).

        PG 구현은 작은 구조 이벤트를 ``audit_logs`` 에 남기고, in-memory 구현은 테스트 가시성을
        위해 기록한다. 저장/로깅 시 secret 평문이 남지 않는다.
        """
