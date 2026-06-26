"""Story 5.2 / AC1·AC2·AC3 (P4-02, ADD-5·7·8, NFR-8) — DB 스키마·마이그레이션 잠금.

세 층으로 분리한다(anti-pattern: SQLite 로 마이그레이션 fidelity 검증 금지):
  (a) metadata-level — DB 불필요. ``Base.metadata`` 가 14개 테이블·필드·PK·시각·유니크·
      secret 규약·native-enum 금지를 만족하는지 단언.
  (b) Alembic offline SQL — DB 불필요. 실제 ``postgresql`` dialect 로 ``upgrade head --sql``
      을 렌더해 14개 CREATE TABLE + ``uq_delivery_logs_dedup_key`` 를 단언(인프라 없이 잠금).
  (c) Postgres-gated 온라인 — ``TEST_DATABASE_URL`` 있을 때만. 실 빈 DB 에 upgrade →
      14개 테이블·유니크 확인 → 같은 ``dedup_key`` 2회 INSERT 가 IntegrityError → downgrade
      round-trip. 이것이 AC1·AC3 literal fidelity 테스트(현 WSL/venv 엔 Postgres 부재 → skip).

외부 DB 직접 호출은 (c) gated 만. (a)(b)는 연결 없이 동작한다. 모든 fixture 는 가짜
ID/ref(실제 토큰/전화/이메일/chat_id 형태 금지).
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from rider_server.db import models  # noqa: F401  (import 으로 Base.metadata 등록)
from rider_server.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# data-api-contract Required Tables 14개(정본). SecretRef 는 모델이지만 테이블 아님.
EXPECTED_TABLES = {
    "tenants",
    "subscriptions",
    "platform_accounts",
    "monitoring_targets",
    "browser_profiles",
    "messenger_channels",
    "delivery_rules",
    "snapshots",
    "messages",
    "delivery_logs",
    "agents",
    "jobs",
    "auth_sessions",
    "audit_logs",
}

# 각 테이블의 계약 required fields(정본 — 누락·오타 금지). 컬럼은 이 집합의 superset 이어야 한다.
REQUIRED_FIELDS = {
    "tenants": {"id", "name", "status", "created_at"},
    "subscriptions": {"tenant_id", "plan", "status", "current_period_end", "quotas"},
    "platform_accounts": {
        "id", "tenant_id", "platform", "label", "username", "password",
        "verification_email_address", "verification_email_app_password",
        "verification_email_subject_keyword", "verification_email_sender_keyword",
        "auth_state",
    },
    "monitoring_targets": {
        "id", "tenant_id", "platform_account_id", "name", "external_id", "url",
        "interval_minutes", "schedule_enabled", "start_time", "stop_time", "status",
    },
    "browser_profiles": {"id", "agent_id", "target_id", "profile_path_ref", "cdp_port", "state"},
    "messenger_channels": {
        "id", "tenant_id", "messenger", "telegram_chat_id", "thread_id", "kakao_room_name", "state",
    },
    "delivery_rules": {
        "id", "tenant_id", "target_id", "channel_id", "template_id", "enabled", "send_only_on_change",
    },
    "snapshots": {
        "id", "target_id", "collected_at", "normalized_json", "parser_version", "quality_state",
    },
    "messages": {"id", "snapshot_id", "template_version", "text", "text_hash", "text_redacted_preview"},
    "delivery_logs": {
        "id", "message_id", "channel_id", "status", "dedup_key", "error_code", "sent_at",
        "available_at", "attempt_count", "locked_at", "locked_by",
    },
    "agents": {
        "id", "name", "machine_id", "version", "os", "status", "last_heartbeat_at", "capacity_json",
    },
    "jobs": {"id", "type", "target_id", "agent_id", "status", "run_after", "attempts", "error_code"},
    "auth_sessions": {"id", "account_id", "state", "reason", "requested_at", "resolved_at"},
    # Story 5.8: source/reason/result additive(readiness gate 7필드 — actor/source/diff/
    # target/reason/timestamp/result). created_at 이 timestamp, diff_redacted 가 diff 역할.
    "audit_logs": {
        "actor_id", "action", "target_type", "target_id", "diff_redacted", "created_at",
        "source", "reason", "result",
    },
}

# 평문 secret 컬럼명 금지(NFR-8) — ``*_ref`` 컬럼명은 신규 secret에 우선한다.
# NOTE: PlatformAccount 기존 컬럼명은 계약 호환을 위해 유지하지만 password 류 값은 ref 핸들만 담는다.
FORBIDDEN_PLAINTEXT_COLUMNS = {"token", "secret", "profile_path"}


# ══════════════════════════════════════════════════════════════════════════
# (a) metadata-level — DB 불필요 (AC1·AC2·AC3)
# ══════════════════════════════════════════════════════════════════════════

def test_metadata_has_exactly_14_contract_tables():
    # "13"은 도메인 모델 수, "14"가 테이블 수다(SecretRef 테이블 없음, jobs·audit_logs 추가).
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert len(Base.metadata.tables) == 14


def test_secret_refs_table_is_not_created():
    # SecretRef 의 존재 이유가 "평문을 DB 밖에 둔다"이므로 secret_refs 테이블은 없어야 한다.
    assert "secret_refs" not in Base.metadata.tables


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_each_table_has_required_fields(table_name):
    columns = set(Base.metadata.tables[table_name].columns.keys())
    missing = REQUIRED_FIELDS[table_name] - columns
    assert not missing, f"{table_name} 누락 컬럼: {missing}"


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_primary_key_is_single_uuid_id(table_name):
    pk_cols = list(Base.metadata.tables[table_name].primary_key.columns)
    assert [c.name for c in pk_cols] == ["id"], table_name
    assert isinstance(pk_cols[0].type, sa.Uuid), f"{table_name}.id 는 UUID 여야 한다"


def test_all_at_columns_are_timezone_aware():
    offenders = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if col.name.endswith("_at"):
                ok = isinstance(col.type, sa.DateTime) and col.type.timezone is True
                if not ok:
                    offenders.append(f"{table.name}.{col.name}")
    assert offenders == [], offenders


def test_no_native_pg_enum_columns():
    # 상태/타입은 String(또는 non-native Enum) — native PG ENUM 타입 0(ALTER TYPE 고통 회피).
    enum_cols = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if isinstance(c.type, sa.Enum)
    ]
    assert enum_cols == [], enum_cols


def test_no_plaintext_secret_columns():
    offenders = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if col.name in FORBIDDEN_PLAINTEXT_COLUMNS:
                offenders.append(f"{table.name}.{col.name}")
    assert offenders == [], offenders


def test_platform_account_credential_columns_present():
    pa = set(Base.metadata.tables["platform_accounts"].columns.keys())
    assert {
        "username",
        "password",
        "verification_email_address",
        "verification_email_app_password",
    } <= pa
    bp = set(Base.metadata.tables["browser_profiles"].columns.keys())
    assert "profile_path_ref" in bp


def test_platform_accounts_have_coupang_auto_recovery_columns() -> None:
    """Recovery cooldown is persisted on platform account rows."""

    table = Base.metadata.tables["platform_accounts"]
    columns = set(table.columns.keys())
    assert {
        "auto_recovery_attempted_at",
        "auto_recovery_failed_at",
        "auto_recovery_cooldown_until",
    } <= columns
    # timezone-aware + nullable(시도 이력 없음=NULL).
    for name in (
        "auto_recovery_attempted_at",
        "auto_recovery_failed_at",
        "auto_recovery_cooldown_until",
    ):
        assert table.c[name].type.timezone is True
        assert table.c[name].nullable is True


def test_delivery_logs_dedup_unique_constraint():
    table = Base.metadata.tables["delivery_logs"]
    uniques = {
        c.name: {col.name for col in c.columns}
        for c in table.constraints
        if isinstance(c, sa.UniqueConstraint)
    }
    assert "uq_delivery_logs_dedup_key" in uniques
    assert uniques["uq_delivery_logs_dedup_key"] == {"dedup_key"}


def test_delivery_logs_have_dispatch_claim_columns_and_index():
    table = Base.metadata.tables["delivery_logs"]

    assert table.c.available_at.type.timezone is True
    assert table.c.available_at.nullable is True
    assert table.c.send_attempted_at.type.timezone is True
    assert table.c.send_attempted_at.nullable is True
    assert table.c.last_failed_at.type.timezone is True
    assert table.c.last_failed_at.nullable is True
    assert table.c.attempt_count.nullable is False
    assert table.c.locked_at.type.timezone is True
    assert table.c.locked_at.nullable is True
    assert table.c.locked_by.nullable is True

    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    assert indexes["ix_delivery_logs_dispatch_claim"] == (
        "status",
        "available_at",
        "locked_at",
    )


def test_snapshot_latest_message_query_indexes_exist():
    snapshots = Base.metadata.tables["snapshots"]
    messages = Base.metadata.tables["messages"]
    delivery_logs = Base.metadata.tables["delivery_logs"]
    indexes = {
        table.name: {index.name for index in table.indexes}
        for table in (snapshots, messages, delivery_logs)
    }

    assert "ix_snapshots_target_collected_at_id" in indexes["snapshots"]
    assert "ix_messages_snapshot_template_version" in indexes["messages"]
    assert "ix_delivery_logs_channel_message" in indexes["delivery_logs"]


def test_jobs_have_stale_lease_recovery_index():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Base.metadata.tables["jobs"].indexes
    }

    assert indexes["ix_jobs_status"] == ("status", "run_after")
    assert indexes["ix_jobs_status_lease_expires_at"] == ("status", "lease_expires_at")
    assert indexes["ix_jobs_pending_claim"] == (
        "status",
        "type",
        "assigned_agent_id",
        "run_after",
        "id",
    )


def test_active_crawl_job_guard_index_exists():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Base.metadata.tables["jobs"].indexes
    }

    assert indexes["ix_jobs_active_crawl_target_type"] == ("target_id", "type", "status")


def test_monitoring_targets_have_send_window_columns():
    table = Base.metadata.tables["monitoring_targets"]

    assert table.c.schedule_enabled.nullable is False
    assert isinstance(table.c.schedule_enabled.type, sa.Boolean)
    assert table.c.start_time.nullable is False
    assert isinstance(table.c.start_time.type, sa.String)
    assert table.c.stop_time.nullable is False
    assert isinstance(table.c.stop_time.type, sa.String)


def test_browser_profile_and_kakao_active_room_unique_indexes():
    browser_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Base.metadata.tables["browser_profiles"].indexes
    }
    channel_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Base.metadata.tables["messenger_channels"].indexes
    }

    assert browser_indexes["uq_browser_profiles_agent_target"] == ("agent_id", "target_id")
    assert browser_indexes["uq_browser_profiles_agent_cdp_port"] == ("agent_id", "cdp_port")
    assert channel_indexes["uq_messenger_channels_active_kakao_room"] == (
        "tenant_id",
        "kakao_room_name",
    )


def test_jobs_have_completion_metadata_columns():
    jobs = Base.metadata.tables["jobs"]

    assert isinstance(jobs.c.completed_at.type, sa.DateTime)
    assert jobs.c.completed_at.type.timezone is True
    assert isinstance(jobs.c.duration_ms.type, sa.Integer)
    assert isinstance(jobs.c.result_schema_version.type, sa.String)


def test_cross_tenant_integrity_constraints_are_db_level():
    """DB가 target/account, target/channel tenant 불일치를 직접 막아야 한다."""

    platform_accounts = Base.metadata.tables["platform_accounts"]
    monitoring_targets = Base.metadata.tables["monitoring_targets"]
    messenger_channels = Base.metadata.tables["messenger_channels"]
    delivery_rules = Base.metadata.tables["delivery_rules"]

    unique_sets = {
        constraint.name: tuple(col.name for col in constraint.columns)
        for table in (platform_accounts, monitoring_targets, messenger_channels)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert unique_sets["uq_platform_accounts_tenant_id_id"] == ("tenant_id", "id")
    assert unique_sets["uq_monitoring_targets_tenant_id_id"] == ("tenant_id", "id")
    assert unique_sets["uq_messenger_channels_tenant_id_id"] == ("tenant_id", "id")

    fk_sets = {
        constraint.name: (
            tuple(element.parent.name for element in constraint.elements),
            constraint.elements[0].column.table.name,
            tuple(element.column.name for element in constraint.elements),
        )
        for table in (monitoring_targets, delivery_rules)
        for constraint in table.foreign_key_constraints
    }
    assert fk_sets["fk_monitoring_targets_tenant_account"] == (
        ("tenant_id", "platform_account_id"),
        "platform_accounts",
        ("tenant_id", "id"),
    )
    assert fk_sets["fk_delivery_rules_tenant_target"] == (
        ("tenant_id", "target_id"),
        "monitoring_targets",
        ("tenant_id", "id"),
    )
    assert fk_sets["fk_delivery_rules_tenant_channel"] == (
        ("tenant_id", "channel_id"),
        "messenger_channels",
        ("tenant_id", "id"),
    )


def test_account_id_field_name_preserved_on_auth_sessions():
    # 계약 필드명 account_id 를 platform_account_id 로 바꾸지 않았는지 잠근다.
    cols = set(Base.metadata.tables["auth_sessions"].columns.keys())
    assert "account_id" in cols
    assert "platform_account_id" not in cols


# ══════════════════════════════════════════════════════════════════════════
# (b) Alembic offline SQL — DB 불필요 (AC1·AC3)
# ══════════════════════════════════════════════════════════════════════════

# 오프라인 dialect 잠금용 정본 URL(연결 안 함 — 평문 비밀 아님, 더미 자격).
_OFFLINE_PG_URL = "postgresql://alembic:offline@localhost/offline"


def _alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _offline_sql(direction: str) -> str:
    """offline ``--sql`` 모드로 마이그레이션 SQL 을 렌더해 캡처한다(연결 없음)."""
    cfg = _alembic_config(_OFFLINE_PG_URL)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if direction == "upgrade":
            command.upgrade(cfg, "head", sql=True)
        else:
            command.downgrade(cfg, "0001_initial_schema:base", sql=True)
    return buf.getvalue()


def test_offline_upgrade_emits_all_14_create_tables():
    sql = _offline_sql("upgrade")
    created = set(re.findall(r"CREATE TABLE ([a-z_]+) \(", sql))
    assert created - {"alembic_version"} == EXPECTED_TABLES
    assert "alembic_version" in created
    for name in EXPECTED_TABLES:
        assert f"CREATE TABLE {name} " in sql, f"CREATE TABLE {name} 누락"


def test_offline_upgrade_uses_postgres_dialect_types():
    sql = _offline_sql("upgrade")
    # 실제 Postgres dialect 로 렌더됐음을 잠근다(JSONB·UUID 의미가 Postgres 정본).
    assert "JSONB" in sql
    assert "uq_delivery_logs_dedup_key" in sql


def test_offline_upgrade_emits_dbx_unique_guards():
    sql = _offline_sql("upgrade")
    expected = {
        "uq_messenger_channels_active_telegram_general",
        "uq_messenger_channels_registration_code",
        "uq_agents_registration_code_hash",
        "uq_agents_token_hash",
    }
    for name in expected:
        assert f"CREATE UNIQUE INDEX {name}" in sql
    assert "thread_id IS NULL" in sql
    assert "registration_code IS NOT NULL" in sql
    assert "registration_code_hash IS NOT NULL" in sql
    assert "token_hash IS NOT NULL" in sql


def test_offline_upgrade_emits_cross_tenant_integrity_guards():
    sql = _offline_sql("upgrade")
    for name in (
        "uq_platform_accounts_tenant_id_id",
        "uq_monitoring_targets_tenant_id_id",
        "uq_messenger_channels_tenant_id_id",
        "fk_monitoring_targets_tenant_account",
        "fk_delivery_rules_tenant_target",
        "fk_delivery_rules_tenant_channel",
    ):
        assert name in sql


def test_offline_upgrade_emits_jobs_stale_lease_index():
    sql = _offline_sql("upgrade")

    assert "CREATE INDEX ix_jobs_status_lease_expires_at" in sql
    assert "lease_expires_at" in sql
    assert "WHERE status IN ('CLAIMED', 'RUNNING') AND lease_expires_at IS NOT NULL" in sql


def test_offline_upgrade_emits_jobs_completion_metadata_columns():
    sql = _offline_sql("upgrade")

    assert "ALTER TABLE jobs ADD COLUMN completed_at" in sql
    assert "ALTER TABLE jobs ADD COLUMN duration_ms INTEGER" in sql
    assert "ALTER TABLE jobs ADD COLUMN result_schema_version" in sql


def test_offline_upgrade_emits_target_send_window_columns():
    sql = _offline_sql("upgrade")

    assert "ALTER TABLE monitoring_targets ADD COLUMN schedule_enabled" in sql
    assert "ALTER TABLE monitoring_targets ADD COLUMN start_time" in sql
    assert "ALTER TABLE monitoring_targets ADD COLUMN stop_time" in sql


def test_verification_email_migration_drops_backfill_server_defaults():
    source = (
        MIGRATIONS_DIR / "versions" / "0008_platform_account_verification_email_refs.py"
    ).read_text(encoding="utf-8")
    for column in (
        "verification_email_address_ref",
        "verification_email_app_password_ref",
        "verification_email_subject_keyword",
        "verification_email_sender_keyword",
    ):
        assert f'op.alter_column("platform_accounts", "{column}", server_default=None)' in source


def test_offline_downgrade_drops_all_14_tables_round_trip():
    sql = _offline_sql("downgrade")
    for name in EXPECTED_TABLES:
        assert f"DROP TABLE {name}" in sql, f"DROP TABLE {name} 누락"


# ══════════════════════════════════════════════════════════════════════════
# (d) AC2 — 네이밍 정본·FK 관계 무결성 (metadata, DB 불필요) [QA gap-fill]
# ══════════════════════════════════════════════════════════════════════════
#
# (a)는 14표·필드·PK UUID·시각·secret·dedup 유니크만 잠근다. ADD-8 정본의 나머지를
# 추가로 잠근다: 결정적 제약 이름(pk_/fk_), FK 가 올바른 부모 id 를 가리키는지, FK 컬럼이
# ``<entity>_id`` UUID 인지, audit_logs 의 무FK(다형 참조) 설계.

# 각 테이블의 FK 정본: {(로컬 컬럼, 부모 테이블, 부모 컬럼)} — data-api-contract 관계.
EXPECTED_FOREIGN_KEYS = {
    "subscriptions": {("tenant_id", "tenants", "id")},
    "platform_accounts": {("tenant_id", "tenants", "id")},
    "messenger_channels": {("tenant_id", "tenants", "id")},
    "monitoring_targets": {
        ("tenant_id", "tenants", "id"),
        ("platform_account_id", "platform_accounts", "id"),
        ("tenant_id", "platform_accounts", "tenant_id"),
    },
    "auth_sessions": {("account_id", "platform_accounts", "id")},
    "browser_profiles": {
        ("agent_id", "agents", "id"),
        ("target_id", "monitoring_targets", "id"),
    },
    "snapshots": {("target_id", "monitoring_targets", "id")},
    "delivery_rules": {
        ("target_id", "monitoring_targets", "id"),
        ("tenant_id", "monitoring_targets", "tenant_id"),
        ("channel_id", "messenger_channels", "id"),
        ("tenant_id", "messenger_channels", "tenant_id"),
    },
    "jobs": {
        ("target_id", "monitoring_targets", "id"),
        ("assigned_agent_id", "agents", "id"),
        ("agent_id", "agents", "id"),
    },
    "messages": {("snapshot_id", "snapshots", "id")},
    "delivery_logs": {
        ("message_id", "messages", "id"),
        ("channel_id", "messenger_channels", "id"),
    },
}

# FK 를 의도적으로 두지 않는 테이블. audit_logs.actor_id·target_id 는 admin users 부재 +
# 다형 참조라 FK 없음(후속 보안 스토리가 users 도입 시 FK 추가). tenants·agents 는 루트.
TABLES_WITHOUT_FK = {"tenants", "agents", "audit_logs"}


@pytest.mark.parametrize("table_name", sorted(EXPECTED_FOREIGN_KEYS))
def test_foreign_keys_point_to_correct_parents(table_name):
    table = Base.metadata.tables[table_name]
    actual = {
        (fk.parent.name, fk.column.table.name, fk.column.name) for fk in table.foreign_keys
    }
    assert actual == EXPECTED_FOREIGN_KEYS[table_name]


@pytest.mark.parametrize("table_name", sorted(TABLES_WITHOUT_FK))
def test_tables_without_fk_have_none(table_name):
    # 루트/다형 테이블엔 FK 가 없어야 한다(audit_logs 다형 참조 설계 잠금).
    assert Base.metadata.tables[table_name].foreign_keys == set()


def test_fk_columns_are_entity_id_uuid():
    offenders = []
    for table_name in EXPECTED_FOREIGN_KEYS:
        for fk in Base.metadata.tables[table_name].foreign_keys:
            if not fk.parent.name.endswith("_id"):
                offenders.append(f"{table_name}.{fk.parent.name} (이름)")
            if not isinstance(fk.parent.type, sa.Uuid):
                offenders.append(f"{table_name}.{fk.parent.name} (타입)")
    assert offenders == [], offenders


def test_pk_constraint_names_follow_naming_convention():
    # naming_convention(ADD-8)이 모든 PK 에 결정적 이름 pk_<table> 을 만든다.
    offenders = [
        f"{name}: {table.primary_key.name}"
        for name, table in Base.metadata.tables.items()
        if table.primary_key.name != f"pk_{name}"
    ]
    assert offenders == [], offenders


def test_fk_constraint_names_follow_naming_convention():
    # fk_<table>_<col>_<reftable> — 결정적 이름이 autogenerate drift 0 의 토대다.
    composite_names = {
        "fk_monitoring_targets_tenant_account",
        "fk_delivery_rules_tenant_target",
        "fk_delivery_rules_tenant_channel",
    }
    offenders = []
    for table_name, fks in EXPECTED_FOREIGN_KEYS.items():
        table = Base.metadata.tables[table_name]
        constraints = [
            c for c in table.constraints if isinstance(c, sa.ForeignKeyConstraint)
        ]
        names = {c.name for c in constraints}
        for constraint in constraints:
            if len(constraint.elements) > 1:
                if constraint.name not in composite_names:
                    offenders.append(constraint.name or "<unnamed-composite-fk>")
                continue
            element = constraint.elements[0]
            expected = (
                f"fk_{table_name}_{element.parent.name}_{element.column.table.name}"
            )
            if expected not in names:
                offenders.append(expected)
    assert offenders == [], offenders


# ══════════════════════════════════════════════════════════════════════════
# (e) AC2 — 컬럼/테이블 네이밍 규약 (metadata, DB 불필요) [QA gap-fill]
# ══════════════════════════════════════════════════════════════════════════

def test_all_table_names_are_plural_snake_case():
    offenders = [
        name for name in Base.metadata.tables if not re.fullmatch(r"[a-z][a-z0-9_]*s", name)
    ]
    assert offenders == [], offenders


def test_all_column_names_are_snake_case():
    offenders = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.tables.values()
        for col in table.columns
        if not re.fullmatch(r"[a-z][a-z0-9_]*", col.name)
    ]
    assert offenders == [], offenders


# ══════════════════════════════════════════════════════════════════════════
# (f) AC2 — JSON 컬럼은 이식 가능 JSON(Postgres JSONB) (metadata, DB 불필요) [QA gap-fill]
# ══════════════════════════════════════════════════════════════════════════

# 계약상 JSON 컬럼 정본 — json_variant()(JSON→JSONB) 타입이어야 한다.
EXPECTED_JSON_COLUMNS = {
    ("subscriptions", "quotas"),
    ("snapshots", "normalized_json"),
    ("agents", "capacity_json"),
    ("jobs", "payload_json"),
    ("jobs", "result_json"),
    ("audit_logs", "diff_redacted"),
}


@pytest.mark.parametrize("table_name,col_name", sorted(EXPECTED_JSON_COLUMNS))
def test_json_columns_use_portable_json_type(table_name, col_name):
    col = Base.metadata.tables[table_name].columns[col_name]
    assert isinstance(col.type, sa.JSON), f"{table_name}.{col_name} 은 JSON(JSONB) 여야 한다"


def test_jobs_json_columns_are_db_checked_as_objects():
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables["jobs"].constraints
        if isinstance(constraint, sa.CheckConstraint)
    }

    assert "ck_jobs_payload_json_object" in checks
    assert "ck_jobs_result_json_object" in checks
    assert "jsonb_typeof(payload_json) = 'object'" in checks["ck_jobs_payload_json_object"]
    assert "jsonb_typeof(result_json) = 'object'" in checks["ck_jobs_result_json_object"]


def test_offline_upgrade_emits_jobs_json_object_checks():
    sql = _offline_sql("upgrade")

    assert "CONSTRAINT ck_jobs_payload_json_object" in sql
    assert "CONSTRAINT ck_jobs_result_json_object" in sql
    assert "ck_jobs_ck_jobs_payload_json_object" not in sql
    assert "ck_jobs_ck_jobs_result_json_object" not in sql
    assert "jsonb_typeof(payload_json) = 'object'" in sql
    assert "jsonb_typeof(result_json) = 'object'" in sql


# ══════════════════════════════════════════════════════════════════════════
# (g) AC3 — dedup_key NOT NULL (metadata, DB 불필요) [QA gap-fill]
# ══════════════════════════════════════════════════════════════════════════

def test_dedup_key_is_not_nullable():
    # 유니크 제약은 NOT NULL 일 때만 재시도를 확실히 차단한다(Postgres 는 NULL 중복 허용).
    col = Base.metadata.tables["delivery_logs"].columns["dedup_key"]
    assert col.nullable is False


# ══════════════════════════════════════════════════════════════════════════
# (h) AC1 — 모델↔마이그레이션 drift 가드 + 리비전 그래프 (offline, DB 불필요) [QA gap-fill]
# ══════════════════════════════════════════════════════════════════════════
#
# Task 4 의 "autogenerate drift 0" 은 실DB autogenerate 가 필요해 Postgres 환경 후속으로
# 미뤄졌다. 그 사이를 메우는 DB-less 가드: 모든 모델 컬럼이 offline CREATE TABLE SQL 에
# 실제로 나타나는지 확인한다(예: center_name 은 REQUIRED_FIELDS 에 없어 (a)가 못 잡는다).

def _create_table_block(sql: str, table_name: str) -> str:
    match = re.search(rf"CREATE TABLE {re.escape(table_name)} \(.*?\n\);", sql, re.S)
    assert match, f"CREATE TABLE {table_name} 누락"
    return match.group(0)


def _table_migration_text(sql: str, table_name: str) -> str:
    """테이블 관련 마이그레이션 SQL(CREATE TABLE 블록 + 이후 ALTER TABLE 문)을 모은다.

    Story 5.3 부터 컬럼이 additive(``ALTER TABLE … ADD COLUMN``, 0002 리비전)로 추가되므로
    CREATE 블록만 보면 신규 컬럼(jobs.lease_expires_at 등)을 drift 로 오탐한다 — 같은 테이블의
    ALTER 문도 함께 본다(테이블 스코프는 유지해 다른 테이블로 새지 않음).
    """
    block = _create_table_block(sql, table_name)
    alters = re.findall(rf"ALTER TABLE {re.escape(table_name)} .*?;", sql, re.S)
    return block + "\n" + "\n".join(alters)


def test_migration_renders_every_model_column():
    sql = _offline_sql("upgrade")
    offenders = []
    for name, table in Base.metadata.tables.items():
        text = _table_migration_text(sql, name)
        for col in table.columns:
            if not re.search(rf"\b{re.escape(col.name)}\b", text):
                offenders.append(f"{name}.{col.name}")
    assert offenders == [], f"모델에 있으나 마이그레이션에 없는 컬럼(drift): {offenders}"


def test_single_migration_head_with_initial_base():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config(_OFFLINE_PG_URL))
    heads = script.get_heads()
    assert len(heads) == 1, f"단일 head 여야 한다(분기 금지): {heads}"
    # 0023: tenant 전송 테스트 게이트(send_test_passed_at) on top of Coupang auto recovery state.
    assert heads[0] == "0023_tenant_send_test_gate"
    assert (
        script.get_revision("0023_tenant_send_test_gate").down_revision
        == "0022_coupang_auto_recovery_state"
    )
    assert (
        script.get_revision("0022_coupang_auto_recovery_state").down_revision
        == "0021_target_send_window"
    )
    assert (
        script.get_revision("0021_target_send_window").down_revision
        == "0020_fleet_claim_scale"
    )
    assert (
        script.get_revision("0020_fleet_claim_scale").down_revision
        == "0019_profile_channel_uniqueness"
    )
    assert (
        script.get_revision("0019_profile_channel_uniqueness").down_revision
        == "0018_jobs_claim_index"
    )
    assert (
        script.get_revision("0018_jobs_claim_index").down_revision
        == "0017_delivery_attempt_timestamps"
    )
    assert (
        script.get_revision("0017_delivery_attempt_timestamps").down_revision
        == "0016_jobs_last_failed_at"
    )
    assert (
        script.get_revision("0016_jobs_last_failed_at").down_revision
        == "0015_delivery_outbox"
    )
    assert (
        script.get_revision("0015_delivery_outbox").down_revision
        == "0014_jobs_completion_metadata"
    )
    assert (
        script.get_revision("0014_jobs_completion_metadata").down_revision
        == "0013_jobs_stale_lease_index"
    )
    assert (
        script.get_revision("0013_jobs_stale_lease_index").down_revision
        == "0012_tenant_telegram_config"
    )
    assert (
        script.get_revision("0012_tenant_telegram_config").down_revision
        == "0011_rename_ref_to_plaintext"
    )
    assert (
        script.get_revision("0009_dbx_unique_guards").down_revision
        == "0008_acct_email_2fa_refs"
    )
    assert (
        script.get_revision("0008_acct_email_2fa_refs").down_revision
        == "0007_jobs_payload_json"
    )
    assert (
        script.get_revision("0007_jobs_payload_json").down_revision
        == "0006_agent_registration_contract"
    )
    assert (
        script.get_revision("0006_agent_registration_contract").down_revision
        == "0005_audit_agent_tokens"
    )
    assert (
        script.get_revision("0005_audit_agent_tokens").down_revision
        == "0004_channel_reg"
    )
    assert (
        script.get_revision("0004_channel_reg").down_revision
        == "0003_targets_sched"
    )
    assert (
        script.get_revision("0003_targets_sched").down_revision
        == "0002_jobs_lease_columns"
    )
    assert (
        script.get_revision("0002_jobs_lease_columns").down_revision
        == "0001_initial_schema"
    )
    assert script.get_revision("0001_initial_schema").down_revision is None


# ══════════════════════════════════════════════════════════════════════════
# (c) Postgres-gated 온라인 — 실 빈 DB 만(AC1·AC3 literal fidelity)
# ══════════════════════════════════════════════════════════════════════════

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
_pg_gate = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재(현 WSL/venv). (a)+(b)로 AC 잠금.",
)


def _fake_full_chain_ids():
    """delivery_logs 까지의 FK 부모 체인을 가짜 ID 로 만든다(평문 secret 없음)."""
    return {k: uuid.uuid4() for k in ("tenant", "account", "target", "channel", "snapshot", "message")}


@_pg_gate
def test_postgres_upgrade_creates_14_tables_and_dedup_blocks_duplicate():
    import asyncio

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import create_async_engine

    from rider_server.db.models import (
        DeliveryLog,
        Message,
        MessengerChannel,
        MonitoringTarget,
        PlatformAccount,
        Snapshot,
        Tenant,
    )

    cfg = _alembic_config(_TEST_DB_URL)

    # env.py 는 DATABASE_URL 도 읽으므로 둘 다 맞춘다(online async 경로).
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL = _TEST_DB_URL
    try:
        command.upgrade(cfg, "head")

        async def _exercise():
            engine = create_async_engine(_TEST_DATABASE_URL)
            try:
                # 14개 테이블 + 유니크 제약 확인
                async with engine.connect() as conn:
                    names = await conn.run_sync(
                        lambda sc: set(sa.inspect(sc).get_table_names())
                    )
                    assert EXPECTED_TABLES <= names
                    uniques = await conn.run_sync(
                        lambda sc: {
                            u["name"] for u in sa.inspect(sc).get_unique_constraints("delivery_logs")
                        }
                    )
                    assert "uq_delivery_logs_dedup_key" in uniques

                # FK 부모 체인 + 같은 dedup_key 2회 → 2번째가 IntegrityError 로 차단(AC3)
                ids = _fake_full_chain_ids()
                from sqlalchemy.ext.asyncio import async_sessionmaker

                Session = async_sessionmaker(engine, expire_on_commit=False)
                async with Session() as s:
                    s.add(Tenant(id=ids["tenant"], name="t", status="ACTIVE",
                                 created_at=_fixed_dt()))
                    await s.flush()
                    s.add(PlatformAccount(id=ids["account"], tenant_id=ids["tenant"],
                                          platform="BAEMIN", label="l",
                                          username="vault://u", password="vault://p",
                                          auth_state="UNKNOWN"))
                    s.add(MessengerChannel(id=ids["channel"], tenant_id=ids["tenant"],
                                           messenger="TELEGRAM", state="ACTIVE"))
                    await s.flush()
                    s.add(MonitoringTarget(id=ids["target"], tenant_id=ids["tenant"],
                                           platform_account_id=ids["account"], name="n",
                                           center_name="c", external_id="", url="",
                                           interval_minutes=0, status="ACTIVE"))
                    await s.flush()
                    s.add(Snapshot(id=ids["snapshot"], target_id=ids["target"],
                                   collected_at=_fixed_dt(), normalized_json={},
                                   parser_version="v1", quality_state="OK"))
                    await s.flush()
                    s.add(Message(id=ids["message"], snapshot_id=ids["snapshot"],
                                  template_version="v1", text="p", text_hash="h",
                                  text_redacted_preview="p"))
                    await s.commit()

                async with Session() as s:
                    s.add(DeliveryLog(message_id=ids["message"], channel_id=ids["channel"],
                                      status="SENT", dedup_key="DUP"))
                    await s.commit()
                with pytest.raises(IntegrityError):
                    async with Session() as s:
                        s.add(DeliveryLog(message_id=ids["message"], channel_id=ids["channel"],
                                          status="SENT", dedup_key="DUP"))
                        await s.commit()
            finally:
                await engine.dispose()

        asyncio.run(_exercise())

        # round-trip — downgrade base 로 빈 DB 복귀
        command.downgrade(cfg, "base")
        engine2 = create_async_engine(_TEST_DATABASE_URL)

        async def _verify_clean():
            try:
                async with engine2.connect() as conn:
                    names = await conn.run_sync(
                        lambda sc: set(sa.inspect(sc).get_table_names())
                    )
                    assert EXPECTED_TABLES.isdisjoint(names)
            finally:
                await engine2.dispose()

        asyncio.run(_verify_clean())
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev


def _fixed_dt():
    from datetime import datetime, timezone

    return datetime(2026, 6, 14, tzinfo=timezone.utc)
