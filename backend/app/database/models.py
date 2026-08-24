from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(back_populates="user")


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("refresh_tokens.id"))

    user: Mapped[User] = relationship(back_populates="refresh_tokens", foreign_keys=[user_id])


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        CheckConstraint("role IN ('OWNER', 'ADMIN', 'DEVELOPER', 'VIEWER')", name="valid_role"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_project_org_slug"),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100))

    organization: Mapped[Organization] = relationship(back_populates="projects")
    environments: Mapped[list[Environment]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    flags: Mapped[list[FeatureFlag]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Environment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "environments"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_environment_project_key"),)

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="environments")
    flag_configs: Mapped[list[FlagConfig]] = relationship(back_populates="environment")


class FeatureFlag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_flag_project_key"),
        CheckConstraint(
            "flag_type IN ('BOOLEAN', 'STRING', 'INTEGER', 'JSON')", name="valid_flag_type"
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    flag_type: Mapped[str] = mapped_column(String(20))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    project: Mapped[Project] = relationship(back_populates="flags")
    configs: Mapped[list[FlagConfig]] = relationship(
        back_populates="flag", cascade="all, delete-orphan"
    )


class FlagConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flag_configs"
    __table_args__ = (
        UniqueConstraint("flag_id", "environment_id", name="uq_flag_config_flag_environment"),
    )

    flag_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_flags.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    default_variation_key: Mapped[str] = mapped_column(String(100))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    salt: Mapped[str] = mapped_column(String(64))

    flag: Mapped[FeatureFlag] = relationship(back_populates="configs")
    environment: Mapped[Environment] = relationship(back_populates="flag_configs")
    variations: Mapped[list[FlagVariation]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )
    rules: Mapped[list[TargetingRule]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )
    rollouts: Mapped[list[RolloutAllocation]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )
    progressive_stages: Mapped[list[ProgressiveStage]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )
    versions: Mapped[list[FlagVersion]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )


class FlagVariation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flag_variations"
    __table_args__ = (UniqueConstraint("config_id", "key", name="uq_variation_config_key"),)

    config_id: Mapped[UUID] = mapped_column(
        ForeignKey("flag_configs.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[Any] = mapped_column(JSON_VALUE)

    config: Mapped[FlagConfig] = relationship(back_populates="variations")


class TargetingRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "targeting_rules"
    __table_args__ = (UniqueConstraint("config_id", "priority", name="uq_rule_config_priority"),)

    config_id: Mapped[UUID] = mapped_column(
        ForeignKey("flag_configs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    serve_variation_key: Mapped[str | None] = mapped_column(String(100))

    config: Mapped[FlagConfig] = relationship(back_populates="rules")
    conditions: Mapped[list[RuleCondition]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )
    rollouts: Mapped[list[RolloutAllocation]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )
    progressive_stages: Mapped[list[ProgressiveStage]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )


class RuleCondition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rule_conditions"
    __table_args__ = (
        CheckConstraint(
            "operator IN ('equals','not_equals','in','not_in','contains','starts_with',"
            "'ends_with','exists','not_exists','greater_than','greater_or_equal','less_than',"
            "'less_or_equal','semver_equal','semver_greater','semver_less')",
            name="valid_operator",
        ),
    )

    rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("targeting_rules.id", ondelete="CASCADE"), index=True
    )
    attribute: Mapped[str] = mapped_column(String(200))
    operator: Mapped[str] = mapped_column(String(40))
    value: Mapped[Any | None] = mapped_column(JSON_VALUE)

    rule: Mapped[TargetingRule] = relationship(back_populates="conditions")


class RolloutAllocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rollout_allocations"
    __table_args__ = (
        CheckConstraint(
            "(config_id IS NOT NULL AND rule_id IS NULL) OR "
            "(config_id IS NULL AND rule_id IS NOT NULL)",
            name="one_rollout_parent",
        ),
        CheckConstraint("end_bucket > 0 AND end_bucket <= 10000", name="valid_end_bucket"),
    )

    config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("flag_configs.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("targeting_rules.id", ondelete="CASCADE"), index=True
    )
    variation_key: Mapped[str] = mapped_column(String(100))
    end_bucket: Mapped[int] = mapped_column(Integer)

    config: Mapped[FlagConfig | None] = relationship(back_populates="rollouts")
    rule: Mapped[TargetingRule | None] = relationship(back_populates="rollouts")


class ProgressiveStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "progressive_stages"
    __table_args__ = (
        CheckConstraint(
            "(config_id IS NOT NULL AND rule_id IS NULL) OR "
            "(config_id IS NULL AND rule_id IS NOT NULL)",
            name="one_stage_parent",
        ),
        CheckConstraint(
            "percentage_basis_points >= 0 AND percentage_basis_points <= 10000",
            name="valid_stage_percentage",
        ),
    )

    config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("flag_configs.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("targeting_rules.id", ondelete="CASCADE"), index=True
    )
    variation_key: Mapped[str] = mapped_column(String(100))
    fallback_variation_key: Mapped[str] = mapped_column(String(100))
    percentage_basis_points: Mapped[int] = mapped_column(Integer)
    activate_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    config: Mapped[FlagConfig | None] = relationship(back_populates="progressive_stages")
    rule: Mapped[TargetingRule | None] = relationship(back_populates="progressive_stages")


class FlagVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "flag_versions"
    __table_args__ = (
        UniqueConstraint("config_id", "version", name="uq_flag_version_config_version"),
    )

    config_id: Mapped[UUID] = mapped_column(
        ForeignKey("flag_configs.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source_version: Mapped[int | None] = mapped_column(Integer)

    config: Mapped[FlagConfig] = relationship(back_populates="versions")


class ScheduledChange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scheduled_changes"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('enable','disable','rollout_percentage','variation')",
            name="valid_change_type",
        ),
    )

    config_id: Mapped[UUID] = mapped_column(
        ForeignKey("flag_configs.id", ondelete="CASCADE"), index=True
    )
    change_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    activate_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class APIKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("kind IN ('SDK', 'MANAGEMENT')", name="valid_key_kind"),
        Index("ix_api_key_active_hash", "key_hash", "revoked_at"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    key_prefix: Mapped[str] = mapped_column(String(32), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON_VALUE)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_api_key_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[UUID] = mapped_column(index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EvaluationEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_events"
    __table_args__ = (Index("ix_evaluation_sample_time", "environment_id", "created_at"),)

    environment_id: Mapped[UUID] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    flag_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_flags.id", ondelete="CASCADE"), index=True
    )
    variation_key: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
