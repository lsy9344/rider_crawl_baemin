"""Add tenant-scoped relational guards.

Revision ID: 0010_tenant_scope_guards
Revises: 0009_dbx_unique_guards
Create Date: 2026-06-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_tenant_scope_guards"
down_revision: str | None = "0009_dbx_unique_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UQ_PLATFORM_ACCOUNT = "uq_platform_accounts_tenant_id_id"
_UQ_MONITORING_TARGET = "uq_monitoring_targets_tenant_id_id"
_UQ_MESSENGER_CHANNEL = "uq_messenger_channels_tenant_id_id"
_FK_TARGET_ACCOUNT = "fk_monitoring_targets_tenant_account"
_FK_RULE_TARGET = "fk_delivery_rules_tenant_target"
_FK_RULE_CHANNEL = "fk_delivery_rules_tenant_channel"
_CK_JOBS_PAYLOAD_JSON_OBJECT = "ck_jobs_payload_json_object"
_CK_JOBS_RESULT_JSON_OBJECT = "ck_jobs_result_json_object"


def upgrade() -> None:
    op.create_check_constraint(
        op.f(_CK_JOBS_PAYLOAD_JSON_OBJECT),
        "jobs",
        "payload_json IS NULL OR jsonb_typeof(payload_json) = 'object'",
    )
    op.create_check_constraint(
        op.f(_CK_JOBS_RESULT_JSON_OBJECT),
        "jobs",
        "result_json IS NULL OR jsonb_typeof(result_json) = 'object'",
    )

    op.add_column("delivery_rules", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE delivery_rules AS dr
        SET tenant_id = mt.tenant_id
        FROM monitoring_targets AS mt
        WHERE dr.target_id = mt.id
        """
    )
    op.alter_column("delivery_rules", "tenant_id", nullable=False)

    op.create_unique_constraint(
        _UQ_PLATFORM_ACCOUNT, "platform_accounts", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        _UQ_MONITORING_TARGET, "monitoring_targets", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        _UQ_MESSENGER_CHANNEL, "messenger_channels", ["tenant_id", "id"]
    )

    op.create_foreign_key(
        _FK_TARGET_ACCOUNT,
        "monitoring_targets",
        "platform_accounts",
        ["tenant_id", "platform_account_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        _FK_RULE_TARGET,
        "delivery_rules",
        "monitoring_targets",
        ["tenant_id", "target_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        _FK_RULE_CHANNEL,
        "delivery_rules",
        "messenger_channels",
        ["tenant_id", "channel_id"],
        ["tenant_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(_FK_RULE_CHANNEL, "delivery_rules", type_="foreignkey")
    op.drop_constraint(_FK_RULE_TARGET, "delivery_rules", type_="foreignkey")
    op.drop_constraint(_FK_TARGET_ACCOUNT, "monitoring_targets", type_="foreignkey")
    op.drop_constraint(_UQ_MESSENGER_CHANNEL, "messenger_channels", type_="unique")
    op.drop_constraint(_UQ_MONITORING_TARGET, "monitoring_targets", type_="unique")
    op.drop_constraint(_UQ_PLATFORM_ACCOUNT, "platform_accounts", type_="unique")
    op.drop_column("delivery_rules", "tenant_id")
    op.drop_constraint(op.f(_CK_JOBS_RESULT_JSON_OBJECT), "jobs", type_="check")
    op.drop_constraint(op.f(_CK_JOBS_PAYLOAD_JSON_OBJECT), "jobs", type_="check")
