"""agents·browser_profiles·jobs ORM 모델 — Story 5.2 (AC1·AC2) + additive runtime columns.

``agents``/``jobs`` 는 domain dataclass 가 없어 data-api-contract Required fields 에서 직접
정의한다(추측 컬럼 추가 금지 — 운영 컬럼은 후속 스토리가 additive 마이그레이션으로).
``jobs`` 상태머신·claim(``FOR UPDATE SKIP LOCKED``)·lease 는 Story 5.3 소유다 — 5.3 이
``lease_expires_at``/``claimed_at``/``result_json`` 을 **additive**(0002 마이그레이션)로 추가하고
claim 로직(``queue/postgres_queue.py``)을 채운다. 계약 Required 8필드(id/type/target_id/
agent_id/status/run_after/attempts/error_code)는 그대로 유지 — 새 컬럼은 superset 이라 5.2
schema 가드 무회귀. ``browser_profiles.profile_path_ref`` 는 ref 컬럼(raw 경로 평문 금지).
``capacity_json`` 은 JSON(Postgres JSONB). Agent registration columns store only hashes and
timestamps; plaintext registration codes and bearer tokens never enter the DB.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, text
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
    # ── Agent register/heartbeat contract(additive, 0006 migration) ─────────────
    registration_code_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    registration_code_used_at: Mapped[datetime | None] = ts(nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    token_issued_at: Mapped[datetime | None] = ts(nullable=True)

    __table_args__ = (
        Index(
            "uq_agents_registration_code_hash",
            "registration_code_hash",
            unique=True,
            postgresql_where=text("registration_code_hash IS NOT NULL"),
        ),
        Index(
            "uq_agents_token_hash",
            "token_hash",
            unique=True,
            postgresql_where=text("token_hash IS NOT NULL"),
        ),
    )


class BrowserProfile(Base):
    __tablename__ = "browser_profiles"
    __table_args__ = (
        Index("uq_browser_profiles_agent_target", "agent_id", "target_id", unique=True),
        Index(
            "uq_browser_profiles_agent_cdp_port",
            "agent_id",
            "cdp_port",
            unique=True,
            postgresql_where=text("cdp_port IS NOT NULL"),
        ),
    )

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
    assigned_agent_id: Mapped[uuid.UUID | None] = fk("agents.id", nullable=True)
    agent_id: Mapped[uuid.UUID | None] = fk("agents.id", nullable=True)  # claim 전엔 미할당
    status: Mapped[str] = mapped_column(String, nullable=False)
    run_after: Mapped[datetime | None] = ts(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)  # FailureCategory 값
    payload_json: Mapped[dict | None] = mapped_column(json_variant(), nullable=True)
    # ── 5.3 lease 컬럼(additive, 0002 마이그레이션) ─────────────────────────────
    lease_expires_at: Mapped[datetime | None] = ts(nullable=True)  # claim 시 부여, 만료 시 stale 회수
    claimed_at: Mapped[datetime | None] = ts(nullable=True)
    result_json: Mapped[dict | None] = mapped_column(json_variant(), nullable=True)  # complete 결과(JSONB)
    # ── retry/status 관측 컬럼(additive, 0014 마이그레이션) ─────────────────────
    completed_at: Mapped[datetime | None] = ts(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_schema_version: Mapped[str | None] = mapped_column(String, nullable=True)
    completion_id: Mapped[str | None] = mapped_column(String, nullable=True)
    completion_payload_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    last_failed_at: Mapped[datetime | None] = ts(nullable=True)

    # claim 대상 행(status='PENDING', run_after 정렬) 스캔 최소화 — SKIP LOCKED 성능.
    # naming_convention 의존 없이 명시 이름(결정적). claim 은 (status, run_after),
    # stale lease 회수는 partial (status, lease_expires_at) 로 각각 잠근다.
    __table_args__ = (
        Index("ix_jobs_status", "status", "run_after"),
        Index(
            "ix_jobs_pending_claim",
            "status",
            "type",
            "assigned_agent_id",
            "run_after",
            "id",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_jobs_active_crawl_target_type",
            "target_id",
            "type",
            "status",
            postgresql_where=text(
                "target_id IS NOT NULL AND type IN ('CRAWL_BAEMIN', 'CRAWL_COUPANG') "
                "AND status IN ('PENDING', 'CLAIMED', 'RUNNING', 'RETRY')"
            ),
        ),
        Index(
            "ix_jobs_status_lease_expires_at",
            "status",
            "lease_expires_at",
            postgresql_where=text(
                "status IN ('CLAIMED', 'RUNNING') AND lease_expires_at IS NOT NULL"
            ),
        ),
        CheckConstraint(
            "payload_json IS NULL OR jsonb_typeof(payload_json) = 'object'",
            name="payload_json_object",
        ),
        CheckConstraint(
            "result_json IS NULL OR jsonb_typeof(result_json) = 'object'",
            name="result_json_object",
        ),
    )
