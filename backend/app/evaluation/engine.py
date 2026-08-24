from datetime import UTC, datetime

from backend.app.evaluation.hashing import stable_bucket
from backend.app.evaluation.models import (
    EvaluationContext,
    EvaluationResult,
    FlagConfiguration,
    ProgressiveStage,
    Reason,
    RolloutAllocation,
)
from backend.app.evaluation.operators import MISSING, condition_matches


def evaluate_flag(
    configuration: FlagConfiguration,
    context: EvaluationContext,
    *,
    now: datetime | None = None,
) -> EvaluationResult:
    evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
    if not configuration.enabled:
        return _result(
            configuration,
            configuration.default_variation,
            Reason.FLAG_DISABLED,
            None,
            evaluated_at,
        )

    bucket: int | None = None

    def get_bucket() -> int:
        nonlocal bucket
        if bucket is None:
            bucket = stable_bucket(
                configuration.project_id,
                configuration.environment_id,
                configuration.flag_key,
                context.subject_key,
                configuration.salt,
            )
        return bucket

    for rule in sorted(configuration.rules, key=lambda item: item.priority):
        if not all(
            condition_matches(
                condition.operator,
                context.attributes.get(condition.attribute, MISSING),
                condition.value,
            )
            for condition in rule.conditions
        ):
            continue
        if rule.variation:
            return _result(
                configuration,
                rule.variation,
                Reason.TARGETING_MATCH,
                rule.name,
                evaluated_at,
            )
        if rule.progressive_stages:
            variation = _progressive_variation(rule.progressive_stages, get_bucket(), evaluated_at)
            return _result(
                configuration,
                variation,
                Reason.PROGRESSIVE_ROLLOUT,
                rule.name,
                evaluated_at,
            )
        return _result(
            configuration,
            _rollout_variation(rule.rollout, get_bucket()),
            Reason.PERCENTAGE_ROLLOUT,
            rule.name,
            evaluated_at,
        )

    if configuration.progressive_stages:
        return _result(
            configuration,
            _progressive_variation(configuration.progressive_stages, get_bucket(), evaluated_at),
            Reason.PROGRESSIVE_ROLLOUT,
            None,
            evaluated_at,
        )
    if configuration.rollout:
        return _result(
            configuration,
            _rollout_variation(configuration.rollout, get_bucket()),
            Reason.PERCENTAGE_ROLLOUT,
            None,
            evaluated_at,
        )
    return _result(
        configuration,
        configuration.default_variation,
        Reason.DEFAULT,
        None,
        evaluated_at,
    )


def _rollout_variation(rollout: list[RolloutAllocation], bucket: int) -> str:
    for allocation in rollout:
        if bucket < allocation.end_bucket:
            return allocation.variation
    raise ValueError("Invalid rollout: no allocation contains the bucket")


def _progressive_variation(stages: list[ProgressiveStage], bucket: int, now: datetime) -> str:
    active = [stage for stage in stages if stage.activate_at <= now]
    if not active:
        first = min(stages, key=lambda item: item.activate_at)
        return first.fallback_variation
    current = max(active, key=lambda item: item.activate_at)
    if bucket < current.percentage_basis_points:
        return current.variation
    return current.fallback_variation


def _result(
    config: FlagConfiguration,
    variation: str,
    reason: Reason,
    matched_rule: str | None,
    evaluated_at: datetime,
) -> EvaluationResult:
    return EvaluationResult(
        flag_key=config.flag_key,
        value=config.variations[variation],
        variation=variation,
        reason=reason,
        matched_rule=matched_rule,
        config_version=config.config_version,
        evaluated_at=evaluated_at,
    )
