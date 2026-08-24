from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.app.evaluation.engine import evaluate_flag
from backend.app.evaluation.models import (
    Condition,
    EvaluationContext,
    FlagConfiguration,
    ProgressiveStage,
    Reason,
    RolloutAllocation,
    TargetingRule,
)


def config(**overrides: object) -> FlagConfiguration:
    values = {
        "project_id": "project",
        "environment_id": "production",
        "environment_key": "production",
        "flag_id": "flag",
        "flag_key": "new_checkout",
        "flag_type": "BOOLEAN",
        "enabled": True,
        "default_variation": "disabled",
        "variations": {"enabled": True, "disabled": False},
        "config_version": 17,
        "salt": "stable-salt",
        "rules": [],
        "rollout": [],
        "progressive_stages": [],
    }
    values.update(overrides)
    return FlagConfiguration.model_validate(values)


def context(**attributes: object) -> EvaluationContext:
    return EvaluationContext(subject_key="user_42", attributes=attributes)


def test_disabled_flag_wins_over_targeting() -> None:
    rule = TargetingRule(
        name="all-users",
        priority=0,
        conditions=[],
        variation="enabled",
    )
    result = evaluate_flag(config(enabled=False, rules=[rule]), context())
    assert result.value is False
    assert result.reason is Reason.FLAG_DISABLED
    assert result.matched_rule is None


def test_default_variation_is_used_without_rules() -> None:
    result = evaluate_flag(config(), context())
    assert result.variation == "disabled"
    assert result.reason is Reason.DEFAULT


def test_conditions_are_and_and_rules_follow_priority() -> None:
    rules = [
        TargetingRule(
            name="lower-priority",
            priority=20,
            conditions=[Condition(attribute="country", operator="equals", value="KZ")],
            variation="disabled",
        ),
        TargetingRule(
            name="beta-pro",
            priority=10,
            conditions=[
                Condition(attribute="country", operator="equals", value="KZ"),
                Condition(attribute="plan", operator="equals", value="PRO"),
            ],
            variation="enabled",
        ),
    ]
    result = evaluate_flag(config(rules=rules), context(country="KZ", plan="PRO"))
    assert result.variation == "enabled"
    assert result.matched_rule == "beta-pro"
    assert result.reason is Reason.TARGETING_MATCH


def test_failed_and_condition_moves_to_next_rule() -> None:
    rules = [
        TargetingRule(
            name="beta-pro",
            priority=0,
            conditions=[
                Condition(attribute="country", operator="equals", value="KZ"),
                Condition(attribute="plan", operator="equals", value="PRO"),
            ],
            variation="enabled",
        )
    ]
    result = evaluate_flag(config(rules=rules), context(country="KZ", plan="FREE"))
    assert result.reason is Reason.DEFAULT


def test_percentage_result_is_sticky() -> None:
    rollout = [
        RolloutAllocation(variation="enabled", end_bucket=1000),
        RolloutAllocation(variation="disabled", end_bucket=10_000),
    ]
    decisions = {evaluate_flag(config(rollout=rollout), context()).variation for _ in range(100)}
    assert len(decisions) == 1


def test_rule_can_start_percentage_rollout() -> None:
    rule = TargetingRule(
        name="kazakhstan",
        priority=0,
        conditions=[Condition(attribute="country", operator="equals", value="KZ")],
        rollout=[
            RolloutAllocation(variation="enabled", end_bucket=5000),
            RolloutAllocation(variation="disabled", end_bucket=10_000),
        ],
    )
    result = evaluate_flag(config(rules=[rule]), context(country="KZ"))
    assert result.reason is Reason.PERCENTAGE_ROLLOUT
    assert result.matched_rule == "kazakhstan"


def test_progressive_stage_uses_latest_active_percentage() -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    stages = [
        ProgressiveStage(
            percentage_basis_points=0,
            variation="enabled",
            fallback_variation="disabled",
            activate_at=now - timedelta(hours=2),
        ),
        ProgressiveStage(
            percentage_basis_points=10_000,
            variation="enabled",
            fallback_variation="disabled",
            activate_at=now - timedelta(hours=1),
        ),
        ProgressiveStage(
            percentage_basis_points=0,
            variation="enabled",
            fallback_variation="disabled",
            activate_at=now + timedelta(hours=1),
        ),
    ]
    result = evaluate_flag(config(progressive_stages=stages), context(), now=now)
    assert result.variation == "enabled"
    assert result.reason is Reason.PROGRESSIVE_ROLLOUT


@pytest.mark.parametrize(
    ("flag_type", "value"),
    [("BOOLEAN", 1), ("STRING", True), ("INTEGER", True), ("JSON", "not-json-container")],
)
def test_flag_value_types_are_strict(flag_type: str, value: object) -> None:
    with pytest.raises(ValidationError):
        config(flag_type=flag_type, variations={"enabled": value, "disabled": value})


def test_rollout_must_cover_every_bucket() -> None:
    with pytest.raises(ValidationError, match="end at 10000"):
        config(rollout=[RolloutAllocation(variation="enabled", end_bucket=1000)])
