from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FlagValue = bool | str | int | dict[str, Any] | list[Any] | None


class FlagType(StrEnum):
    BOOLEAN = "BOOLEAN"
    STRING = "STRING"
    INTEGER = "INTEGER"
    JSON = "JSON"


class Reason(StrEnum):
    FLAG_DISABLED = "flag_disabled"
    TARGETING_MATCH = "targeting_match"
    PERCENTAGE_ROLLOUT = "percentage_rollout"
    PROGRESSIVE_ROLLOUT = "progressive_rollout"
    DEFAULT = "default"


class EvaluationContext(BaseModel):
    subject_key: str = Field(min_length=1, max_length=500)
    attributes: dict[str, Any] = Field(default_factory=dict)


class Condition(BaseModel):
    attribute: str = Field(min_length=1, max_length=200)
    operator: str
    value: Any | None = None


class RolloutAllocation(BaseModel):
    variation: str
    end_bucket: int = Field(gt=0, le=10_000)


class ProgressiveStage(BaseModel):
    percentage_basis_points: int = Field(ge=0, le=10_000)
    variation: str
    fallback_variation: str
    activate_at: datetime

    @field_validator("activate_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("activate_at must be timezone-aware")
        return value.astimezone(UTC)


class TargetingRule(BaseModel):
    name: str
    priority: int
    conditions: list[Condition] = Field(default_factory=list)
    variation: str | None = None
    rollout: list[RolloutAllocation] = Field(default_factory=list)
    progressive_stages: list[ProgressiveStage] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_action(self) -> "TargetingRule":
        actions = bool(self.variation) + bool(self.rollout) + bool(self.progressive_stages)
        if actions != 1:
            raise ValueError("A rule must define exactly one action")
        return self


class FlagConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    environment_id: str
    environment_key: str
    flag_id: str
    flag_key: str
    flag_type: FlagType
    enabled: bool
    default_variation: str
    variations: dict[str, FlagValue]
    config_version: int = Field(ge=1)
    salt: str
    rules: list[TargetingRule] = Field(default_factory=list)
    rollout: list[RolloutAllocation] = Field(default_factory=list)
    progressive_stages: list[ProgressiveStage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_configuration(self) -> "FlagConfiguration":
        if self.default_variation not in self.variations:
            raise ValueError("default variation is missing")
        for key, value in self.variations.items():
            _validate_value(self.flag_type, value, key)
        variation_references = [allocation.variation for allocation in self.rollout]
        for rule in self.rules:
            if rule.variation:
                variation_references.append(rule.variation)
            variation_references.extend(item.variation for item in rule.rollout)
            variation_references.extend(
                item
                for stage in rule.progressive_stages
                for item in (stage.variation, stage.fallback_variation)
            )
        variation_references.extend(
            item
            for stage in self.progressive_stages
            for item in (stage.variation, stage.fallback_variation)
        )
        missing = sorted(set(variation_references) - self.variations.keys())
        if missing:
            raise ValueError(f"unknown variations referenced: {missing}")
        _validate_rollout(self.rollout)
        for rule in self.rules:
            _validate_rollout(rule.rollout)
        return self


class EvaluationResult(BaseModel):
    flag_key: str
    value: FlagValue
    variation: str
    reason: Reason
    matched_rule: str | None
    config_version: int
    evaluated_at: datetime


def _validate_rollout(rollout: list[RolloutAllocation]) -> None:
    if not rollout:
        return
    boundaries = [item.end_bucket for item in rollout]
    if boundaries != sorted(set(boundaries)) or boundaries[-1] != 10_000:
        raise ValueError("rollout boundaries must increase and end at 10000")


def _validate_value(flag_type: FlagType, value: FlagValue, variation: str) -> None:
    valid = {
        FlagType.BOOLEAN: type(value) is bool,
        FlagType.STRING: type(value) is str,
        FlagType.INTEGER: type(value) is int,
        FlagType.JSON: isinstance(value, (dict, list)) or value is None,
    }[flag_type]
    if not valid:
        raise ValueError(f"variation {variation!r} has the wrong value type for {flag_type}")
