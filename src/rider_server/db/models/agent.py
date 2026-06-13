"""agents·browser_profiles·jobs ORM 모델 — Story 5.2 (AC1·AC2).

``agents``/``jobs`` 는 domain dataclass 가 없어 data-api-contract Required fields 에서 직접
정의한다(추측 컬럼 추가 금지 — 운영 컬럼은 후속 스토리가 additive 마이그레이션으로).
``jobs`` 상태머신·claim(``FOR UPDATE SKIP LOCKED``)·lease 는 Story 5.3 소유 — 여기는 구조만
제공한다(claim 로직 0). ``browser_profiles.profile_path_ref`` 는 ref 컬럼(raw 경로 평문 금지).
``capacity_json`` 은 JSON(Postgres JSONB).
"""

import uuid
from datetime import datetime

from sqlalchemy import Integer, String
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
