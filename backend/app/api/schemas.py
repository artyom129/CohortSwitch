from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from backend.app.evaluation.models import EvaluationResult, FlagType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32)


class LogoutRequest(RefreshRequest):
    pass


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserResponse(ORMModel):
    id: UUID
    email: EmailStr
    is_active: bool
    created_at: datetime


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")


class OrganizationResponse(ORMModel):
    id: UUID
    name: str
    slug: str
    created_at: datetime


class MembershipResponse(BaseModel):
    user_id: UUID
    role: str


class MembershipRoleUpdate(BaseModel):
    role: Literal["ADMIN", "DEVELOPER", "VIEWER"]


class ProjectCreate(BaseModel):
    organization_id: UUID
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")


class ProjectResponse(ORMModel):
    id: UUID
    organization_id: UUID
    name: str
    slug: str
    created_at: datetime


class EnvironmentCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=200)


class EnvironmentResponse(ORMModel):
    id: UUID
    project_id: UUID
    key: str
    name: str
    revision: int
    created_at: datetime


class VariationInput(BaseModel):
    key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
    value: Any


class ConditionInput(BaseModel):
    attribute: str = Field(min_length=1, max_length=200)
    operator: Literal[
        "equals",
        "not_equals",
        "in",
        "not_in",
        "contains",
        "starts_with",
        "ends_with",
        "exists",
        "not_exists",
        "greater_than",
        "greater_or_equal",
        "less_than",
        "less_or_equal",
        "semver_equal",
        "semver_greater",
        "semver_less",
    ]
    value: Any | None = None


class RolloutInput(BaseModel):
    variation: str
    percentage_basis_points: int = Field(gt=0, le=10_000)


class ProgressiveStageInput(BaseModel):
    percentage_basis_points: int = Field(ge=0, le=10_000)
    variation: str
    fallback_variation: str
    activate_at: datetime

    @field_validator("activate_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("activate_at must be timezone-aware")
        return value


class RuleInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=0)
    conditions: list[ConditionInput] = Field(default_factory=list)
    variation: str | None = None
    rollout: list[RolloutInput] = Field(default_factory=list)
    progressive_stages: list[ProgressiveStageInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_single_action(self) -> "RuleInput":
        if bool(self.variation) + bool(self.rollout) + bool(self.progressive_stages) != 1:
            raise ValueError("A rule must define one of variation, rollout, or progressive_stages")
        return self


class FlagCreate(BaseModel):
    environment_id: UUID
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,118}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    flag_type: FlagType
    enabled: bool = False
    default_variation: str
    variations: list[VariationInput] = Field(min_length=1)
    rules: list[RuleInput] = Field(default_factory=list)
    rollout: list[RolloutInput] = Field(default_factory=list)
    progressive_stages: list[ProgressiveStageInput] = Field(default_factory=list)


class FlagUpdate(BaseModel):
    environment_id: UUID
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool
    default_variation: str
    variations: list[VariationInput] = Field(min_length=1)
    rules: list[RuleInput] = Field(default_factory=list)
    rollout: list[RolloutInput] = Field(default_factory=list)
    progressive_stages: list[ProgressiveStageInput] = Field(default_factory=list)


class RuleCreate(BaseModel):
    environment_id: UUID
    expected_version: int = Field(ge=1)
    rule: RuleInput


class FlagResponse(BaseModel):
    id: UUID
    project_id: UUID
    environment_id: UUID
    key: str
    name: str
    description: str
    flag_type: FlagType
    enabled: bool
    default_variation: str
    current_version: int
    archived_at: datetime | None
    variations: list[VariationInput]
    rules: list[RuleInput]
    rollout: list[RolloutInput]
    progressive_stages: list[ProgressiveStageInput]


class VersionResponse(ORMModel):
    version: int
    snapshot: dict[str, Any]
    created_at: datetime
    created_by_id: UUID | None
    source_version: int | None


class ScheduledChangeCreate(BaseModel):
    environment_id: UUID
    expected_version: int = Field(ge=1)
    change_type: Literal["enable", "disable", "rollout_percentage", "variation"]
    payload: dict[str, Any] = Field(default_factory=dict)
    activate_at: datetime


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    organization_id: UUID
    project_id: UUID | None = None
    environment_id: UUID | None = None
    scopes: list[Literal["flags:read", "flags:write", "evaluate", "audit:read"]] = Field(
        default_factory=list
    )


class APIKeyCreated(BaseModel):
    id: UUID
    name: str
    kind: Literal["SDK", "MANAGEMENT"]
    key_prefix: str
    key: str
    scopes: list[str]
    created_at: datetime


class APIKeyResponse(ORMModel):
    id: UUID
    name: str
    kind: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class EvaluationRequest(BaseModel):
    flag_key: str
    subject_key: str = Field(min_length=1, max_length=500)
    attributes: dict[str, Any] = Field(default_factory=dict)


class BatchEvaluationRequest(BaseModel):
    subject_key: str = Field(min_length=1, max_length=500)
    attributes: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(min_length=1, max_length=100)


class BatchEvaluationResponse(BaseModel):
    results: dict[str, EvaluationResult]


class ConfigBundle(BaseModel):
    project_id: UUID
    environment_id: UUID
    revision: int
    flags: list[dict[str, Any]]


class AuditResponse(ORMModel):
    id: UUID
    actor_user_id: UUID | None
    actor_api_key_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    request_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
