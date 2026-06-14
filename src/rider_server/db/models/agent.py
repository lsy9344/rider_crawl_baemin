"""agents·browser_profiles·jobs ORM 모델 — Story 5.2 (AC1·AC2) + 5.3 jobs lease 컬럼(AC2).

``agents``/``jobs`` 는 domain dataclass 가 없어 data-api-contract Required fields 에서 직접
정의한다(추측 컬럼 추가 금지 — 운영 컬럼은 후속 스토리가 additive 마이그레이션으로).
``jobs`` 상태머신·claim(``FOR UPDATE SKIP LOCKED``)·lease 는 Story 5.3 소유다 — 5.3 이
``lease_expires_at``/``claimed_at``/``result_json`` 을 **additive**(0002 마이그레이션)로 추가하고
claim 로직(``queue/postgres_queue.py``)을 채운다. 계약 Required 8필드(id/type/target_id/
agent_id/status/run_after/attempts/error_code)는 그대로 유지 — 새 컬럼은 superset 이라 5.2
schema 가드 무회귀. ``browser_profiles.profile_path_ref`` 는 ref 컬럼(raw 경로 평문 금지).
``capacity_json`` 은 JSON(Postgres JSONB).
"""

import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, json_variant
from ._columns import fk, ts, uuid_pk


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    machine_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    os: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = ts(nullable=True)
    capacity_json: Mapped[dict] = mapped_column(json_variant(), nullable=False, default=dict)
    # ── 5.8 server-side token revoke/rotate(additive, 0005 마이그레이션, AC3) ──────────
    # token 자체는 Agent-local DPAPI(서버 비저장)다. server 는 revoke/rotate **시각** 만 두어
    # ``resolve_agent_id`` 가 revoked agent 의 bearer 를 거부(→None→401)하게 한다. ``token``
    # 단독 컬럼명 금지(forbidden-column 정확매치 — ``token_revoked_at``/``token_rotated_at`` 안전).
    token_revoked_at: Mapped[datetime | None] = ts(nullable=True)
    token_rotated_at: Mapped[datetime | None] = ts(nullable=True)


class BrowserProfile(Base):
    __tablename__ = "browser_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_id: Mapped[uuid.UUID] = fk("agents.id")
    target_id: Mapped[uuid.UUID] = fk("monitoring_targets.id")
    profile_path_ref: Mapped[str] = mapped_column(String, nullable=False)  # SecretRef 핸들(평문 경로 아님)
    cdp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False)  # BrowserProfileState 값


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    type: Mapped[str] = mapped_column(String, nullable=False)  # UPPER_SNAKE job type
    target_id: Mapped[uuid.UUID | None] = fk("monitoring_targets.id", nullable=True)
    agent_id: Mapped[uuid.UUID | None] = fk("agents.id", nullable=True)  # claim 전엔 미할당
    status: Mapped[str] = mapped_column(String, nullable=False)
    run_after: Mapped[datetime | None] = ts(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)  # FailureCategory 값
    # ── 5.3 lease 컬럼(additive, 0002 마이그레이션) ─────────────────────────────
    lease_expires_at: Mapped[datetime | None] = ts(nullable=True)  # claim 시 부여, 만료 시 stale 회수
    claimed_at: Mapped[datetime | None] = ts(nullable=True)
    result_json: Mapped[dict | None] = mapped_column(json_variant(), nullable=True)  # complete 결과(JSONB)

    # claim 대상 행(status='PENDING', run_after 정렬) 스캔 최소화 — SKIP LOCKED 성능.
    # naming_convention 의존 없이 명시 이름(결정적). 복합 (status, run_after).
    __table_args__ = (Index("ix_jobs_status", "status", "run_after"),)
