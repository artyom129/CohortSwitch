"""Create the CohortSwitch data model.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaced_by_id", UUID, sa.ForeignKey("refresh_tokens.id")),
        *timestamps(),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])

    op.create_table(
        "organizations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        *timestamps(),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])

    op.create_table(
        "memberships",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        sa.CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'DEVELOPER', 'VIEWER')", name="ck_memberships_valid_role"
        ),
    )
    op.create_index("ix_memberships_organization_id", "memberships", ["organization_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("organization_id", "slug", name="uq_project_org_slug"),
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])

    op.create_table(
        "environments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        *timestamps(),
        sa.UniqueConstraint("project_id", "key", name="uq_environment_project_key"),
    )
    op.create_index("ix_environments_project_id", "environments", ["project_id"])

    op.create_table(
        "feature_flags",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("flag_type", sa.String(20), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("project_id", "key", name="uq_flag_project_key"),
        sa.CheckConstraint(
            "flag_type IN ('BOOLEAN', 'STRING', 'INTEGER', 'JSON')",
            name="ck_feature_flags_valid_flag_type",
        ),
    )
    op.create_index("ix_feature_flags_project_id", "feature_flags", ["project_id"])
    op.create_index("ix_feature_flags_archived_at", "feature_flags", ["archived_at"])

    op.create_table(
        "flag_configs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "flag_id", UUID, sa.ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "environment_id",
            UUID,
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_variation_key", sa.String(100), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("salt", sa.String(64), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("flag_id", "environment_id", name="uq_flag_config_flag_environment"),
    )
    op.create_index("ix_flag_configs_flag_id", "flag_configs", ["flag_id"])
    op.create_index("ix_flag_configs_environment_id", "flag_configs", ["environment_id"])

    op.create_table(
        "flag_variations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "config_id", UUID, sa.ForeignKey("flag_configs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", JSONB, nullable=True),
        *timestamps(),
        sa.UniqueConstraint("config_id", "key", name="uq_variation_config_key"),
    )
    op.create_index("ix_flag_variations_config_id", "flag_variations", ["config_id"])

    op.create_table(
        "targeting_rules",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "config_id", UUID, sa.ForeignKey("flag_configs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("serve_variation_key", sa.String(100)),
        *timestamps(),
        sa.UniqueConstraint("config_id", "priority", name="uq_rule_config_priority"),
    )
    op.create_index("ix_targeting_rules_config_id", "targeting_rules", ["config_id"])

    op.create_table(
        "rule_conditions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "rule_id", UUID, sa.ForeignKey("targeting_rules.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("attribute", sa.String(200), nullable=False),
        sa.Column("operator", sa.String(40), nullable=False),
        sa.Column("value", JSONB),
        *timestamps(),
        sa.CheckConstraint(
            "operator IN ('equals','not_equals','in','not_in','contains','starts_with',"
            "'ends_with','exists','not_exists','greater_than','greater_or_equal','less_than',"
            "'less_or_equal','semver_equal','semver_greater','semver_less')",
            name="ck_rule_conditions_valid_operator",
        ),
    )
    op.create_index("ix_rule_conditions_rule_id", "rule_conditions", ["rule_id"])

    op.create_table(
        "rollout_allocations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("config_id", UUID, sa.ForeignKey("flag_configs.id", ondelete="CASCADE")),
        sa.Column("rule_id", UUID, sa.ForeignKey("targeting_rules.id", ondelete="CASCADE")),
        sa.Column("variation_key", sa.String(100), nullable=False),
        sa.Column("end_bucket", sa.Integer(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "(config_id IS NOT NULL AND rule_id IS NULL) OR "
            "(config_id IS NULL AND rule_id IS NOT NULL)",
            name="ck_rollout_allocations_one_rollout_parent",
        ),
        sa.CheckConstraint(
            "end_bucket > 0 AND end_bucket <= 10000",
            name="ck_rollout_allocations_valid_end_bucket",
        ),
    )
    op.create_index("ix_rollout_allocations_config_id", "rollout_allocations", ["config_id"])
    op.create_index("ix_rollout_allocations_rule_id", "rollout_allocations", ["rule_id"])

    op.create_table(
        "progressive_stages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("config_id", UUID, sa.ForeignKey("flag_configs.id", ondelete="CASCADE")),
        sa.Column("rule_id", UUID, sa.ForeignKey("targeting_rules.id", ondelete="CASCADE")),
        sa.Column("variation_key", sa.String(100), nullable=False),
        sa.Column("fallback_variation_key", sa.String(100), nullable=False),
        sa.Column("percentage_basis_points", sa.Integer(), nullable=False),
        sa.Column("activate_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "(config_id IS NOT NULL AND rule_id IS NULL) OR "
            "(config_id IS NULL AND rule_id IS NOT NULL)",
            name="ck_progressive_stages_one_stage_parent",
        ),
        sa.CheckConstraint(
            "percentage_basis_points >= 0 AND percentage_basis_points <= 10000",
            name="ck_progressive_stages_valid_stage_percentage",
        ),
    )
    op.create_index("ix_progressive_stages_config_id", "progressive_stages", ["config_id"])
    op.create_index("ix_progressive_stages_rule_id", "progressive_stages", ["rule_id"])
    op.create_index("ix_progressive_stages_activate_at", "progressive_stages", ["activate_at"])

    op.create_table(
        "flag_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "config_id", UUID, sa.ForeignKey("flag_configs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_version", sa.Integer()),
        sa.UniqueConstraint("config_id", "version", name="uq_flag_version_config_version"),
    )
    op.create_index("ix_flag_versions_config_id", "flag_versions", ["config_id"])
    op.create_index("ix_flag_versions_created_at", "flag_versions", ["created_at"])

    op.create_table(
        "scheduled_changes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "config_id", UUID, sa.ForeignKey("flag_configs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("change_type", sa.String(40), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("activate_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *timestamps(),
        sa.CheckConstraint(
            "change_type IN ('enable','disable','rollout_percentage','variation')",
            name="ck_scheduled_changes_valid_change_type",
        ),
    )
    op.create_index("ix_scheduled_changes_config_id", "scheduled_changes", ["config_id"])
    op.create_index("ix_scheduled_changes_activate_at", "scheduled_changes", ["activate_at"])
    op.create_index("ix_scheduled_changes_applied_at", "scheduled_changes", ["applied_at"])

    op.create_table(
        "api_keys",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("environment_id", UUID, sa.ForeignKey("environments.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", JSONB, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *timestamps(),
        sa.CheckConstraint("kind IN ('SDK', 'MANAGEMENT')", name="ck_api_keys_valid_key_kind"),
    )
    op.create_index("ix_api_keys_organization_id", "api_keys", ["organization_id"])
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"])
    op.create_index("ix_api_keys_environment_id", "api_keys", ["environment_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])
    op.create_index("ix_api_keys_revoked_at", "api_keys", ["revoked_at"])
    op.create_index("ix_api_key_active_hash", "api_keys", ["key_hash", "revoked_at"])

    op.create_table(
        "audit_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_api_key_id", UUID, sa.ForeignKey("api_keys.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("request_id", sa.String(36)),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_resource_id", "audit_events", ["resource_id"])
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_org_created", "audit_events", ["organization_id", "created_at"])

    op.create_table(
        "evaluation_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "environment_id",
            UUID,
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "flag_id", UUID, sa.ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("variation_key", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evaluation_events_environment_id", "evaluation_events", ["environment_id"])
    op.create_index("ix_evaluation_events_flag_id", "evaluation_events", ["flag_id"])
    op.create_index("ix_evaluation_events_created_at", "evaluation_events", ["created_at"])
    op.create_index(
        "ix_evaluation_sample_time", "evaluation_events", ["environment_id", "created_at"]
    )


def downgrade() -> None:
    for table in (
        "evaluation_events",
        "audit_events",
        "api_keys",
        "scheduled_changes",
        "flag_versions",
        "progressive_stages",
        "rollout_allocations",
        "rule_conditions",
        "targeting_rules",
        "flag_variations",
        "flag_configs",
        "feature_flags",
        "environments",
        "projects",
        "memberships",
        "organizations",
        "refresh_tokens",
        "users",
    ):
        op.drop_table(table)

