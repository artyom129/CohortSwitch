import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from backend.app.api.schemas import (
    FlagCreate,
    FlagResponse,
    FlagUpdate,
    ProgressiveStageInput,
    RolloutInput,
    RuleInput,
    VariationInput,
)
from backend.app.core.errors import APIError
from backend.app.database.models import (
    Environment,
    FeatureFlag,
    FlagConfig,
    FlagVariation,
    FlagVersion,
    ProgressiveStage,
    Project,
    RolloutAllocation,
    RuleCondition,
    TargetingRule,
)
from backend.app.evaluation.models import (
    Condition,
    FlagConfiguration,
)
from backend.app.evaluation.models import (
    ProgressiveStage as EngineProgressiveStage,
)
from backend.app.evaluation.models import (
    RolloutAllocation as EngineRolloutAllocation,
)
from backend.app.evaluation.models import (
    TargetingRule as EngineTargetingRule,
)
from backend.app.services.audit import record_audit


def _loader_options() -> tuple[object, ...]:
    return (
        joinedload(FlagConfig.flag),
        joinedload(FlagConfig.environment),
        selectinload(FlagConfig.variations),
        selectinload(FlagConfig.rollouts),
        selectinload(FlagConfig.progressive_stages),
        selectinload(FlagConfig.rules).selectinload(TargetingRule.conditions),
        selectinload(FlagConfig.rules).selectinload(TargetingRule.rollouts),
        selectinload(FlagConfig.rules).selectinload(TargetingRule.progressive_stages),
    )


async def get_config_by_flag_environment(
    session: AsyncSession, flag_id: UUID, environment_id: UUID
) -> FlagConfig:
    config = await session.scalar(
        select(FlagConfig)
        .where(FlagConfig.flag_id == flag_id, FlagConfig.environment_id == environment_id)
        .options(*_loader_options())
    )
    if not config or config.flag.archived_at is not None:
        raise APIError(404, "flag_not_found", "Feature flag was not found.")
    return config


async def get_config_by_key(
    session: AsyncSession, project_id: UUID, environment_id: UUID, flag_key: str
) -> FlagConfiguration:
    config = await session.scalar(
        select(FlagConfig)
        .join(FeatureFlag)
        .where(
            FeatureFlag.project_id == project_id,
            FeatureFlag.key == flag_key,
            FeatureFlag.archived_at.is_(None),
            FlagConfig.environment_id == environment_id,
        )
        .options(*_loader_options())
    )
    if not config:
        raise APIError(404, "flag_not_found", "Feature flag was not found.")
    return compile_configuration(config)


async def get_configs_by_keys(
    session: AsyncSession,
    project_id: UUID,
    environment_id: UUID,
    flag_keys: list[str],
) -> dict[str, FlagConfiguration]:
    result = await session.scalars(
        select(FlagConfig)
        .join(FeatureFlag)
        .where(
            FeatureFlag.project_id == project_id,
            FeatureFlag.key.in_(flag_keys),
            FeatureFlag.archived_at.is_(None),
            FlagConfig.environment_id == environment_id,
        )
        .options(*_loader_options())
    )
    return {config.flag.key: compile_configuration(config) for config in result.unique().all()}


async def get_all_environment_configs(
    session: AsyncSession, project_id: UUID, environment_id: UUID
) -> list[FlagConfiguration]:
    result = await session.scalars(
        select(FlagConfig)
        .join(FeatureFlag)
        .where(
            FeatureFlag.project_id == project_id,
            FeatureFlag.archived_at.is_(None),
            FlagConfig.environment_id == environment_id,
        )
        .options(*_loader_options())
        .order_by(FeatureFlag.key)
    )
    return [compile_configuration(config) for config in result.unique().all()]


def compile_configuration(config: FlagConfig) -> FlagConfiguration:
    rules = [
        EngineTargetingRule(
            name=rule.name,
            priority=rule.priority,
            conditions=[
                Condition(
                    attribute=condition.attribute,
                    operator=condition.operator,
                    value=condition.value,
                )
                for condition in rule.conditions
            ],
            variation=rule.serve_variation_key,
            rollout=[
                EngineRolloutAllocation(variation=item.variation_key, end_bucket=item.end_bucket)
                for item in sorted(rule.rollouts, key=lambda value: value.end_bucket)
            ],
            progressive_stages=[_engine_stage(item) for item in rule.progressive_stages],
        )
        for rule in sorted(config.rules, key=lambda item: item.priority)
        if rule.enabled
    ]
    return FlagConfiguration(
        project_id=str(config.flag.project_id),
        environment_id=str(config.environment_id),
        environment_key=config.environment.key,
        flag_id=str(config.flag_id),
        flag_key=config.flag.key,
        flag_type=config.flag.flag_type,
        enabled=config.enabled,
        default_variation=config.default_variation_key,
        variations={item.key: item.value for item in config.variations},
        config_version=config.current_version,
        salt=config.salt,
        rules=rules,
        rollout=[
            EngineRolloutAllocation(variation=item.variation_key, end_bucket=item.end_bucket)
            for item in sorted(config.rollouts, key=lambda value: value.end_bucket)
        ],
        progressive_stages=[_engine_stage(item) for item in config.progressive_stages],
    )


def _engine_stage(stage: ProgressiveStage) -> EngineProgressiveStage:
    return EngineProgressiveStage(
        percentage_basis_points=stage.percentage_basis_points,
        variation=stage.variation_key,
        fallback_variation=stage.fallback_variation_key,
        activate_at=stage.activate_at,
    )


def _rollout_allocations(inputs: list[RolloutInput]) -> list[EngineRolloutAllocation]:
    boundary = 0
    allocations: list[EngineRolloutAllocation] = []
    for item in inputs:
        boundary += item.percentage_basis_points
        allocations.append(EngineRolloutAllocation(variation=item.variation, end_bucket=boundary))
    if allocations and boundary != 10_000:
        raise APIError(
            422,
            "invalid_rollout",
            "Rollout percentages must add up to exactly 10000 basis points.",
        )
    return allocations


def _build_configuration(
    *,
    project_id: UUID,
    environment: Environment,
    flag: FeatureFlag,
    enabled: bool,
    default_variation: str,
    variations: list[VariationInput],
    rules: list[RuleInput],
    rollout: list[RolloutInput],
    progressive_stages: list[ProgressiveStageInput],
    version: int,
    salt: str,
) -> FlagConfiguration:
    if len({item.key for item in variations}) != len(variations):
        raise APIError(422, "duplicate_variation", "Variation keys must be unique.")
    if len({item.priority for item in rules}) != len(rules):
        raise APIError(422, "duplicate_rule_priority", "Rule priorities must be unique.")
    if bool(rollout) and bool(progressive_stages):
        raise APIError(422, "invalid_flag_action", "Use rollout or progressive stages, not both.")
    engine_rules = [
        EngineTargetingRule(
            name=rule.name,
            priority=rule.priority,
            conditions=[Condition.model_validate(item.model_dump()) for item in rule.conditions],
            variation=rule.variation,
            rollout=_rollout_allocations(rule.rollout),
            progressive_stages=[
                EngineProgressiveStage.model_validate(item.model_dump())
                for item in rule.progressive_stages
            ],
        )
        for rule in rules
    ]
    try:
        return FlagConfiguration(
            project_id=str(project_id),
            environment_id=str(environment.id),
            environment_key=environment.key,
            flag_id=str(flag.id),
            flag_key=flag.key,
            flag_type=flag.flag_type,
            enabled=enabled,
            default_variation=default_variation,
            variations={item.key: item.value for item in variations},
            config_version=version,
            salt=salt,
            rules=engine_rules,
            rollout=_rollout_allocations(rollout),
            progressive_stages=[
                EngineProgressiveStage.model_validate(item.model_dump())
                for item in progressive_stages
            ],
        )
    except ValueError as exc:
        raise APIError(422, "invalid_flag_configuration", str(exc)) from exc


async def create_flag(
    session: AsyncSession,
    *,
    project: Project,
    environment: Environment,
    payload: FlagCreate,
    actor_user_id: UUID | None,
    actor_api_key_id: UUID | None,
    request_id: str | None,
) -> FlagResponse:
    if environment.project_id != project.id:
        raise APIError(404, "environment_not_found", "Environment was not found.")
    existing = await session.scalar(
        select(FeatureFlag.id).where(
            FeatureFlag.project_id == project.id, FeatureFlag.key == payload.key
        )
    )
    if existing:
        raise APIError(409, "flag_key_exists", "A flag with this immutable key already exists.")
    flag = FeatureFlag(
        project_id=project.id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
        flag_type=payload.flag_type.value,
    )
    session.add(flag)
    await session.flush()
    salt = secrets.token_hex(16)
    compiled = _build_configuration(
        project_id=project.id,
        environment=environment,
        flag=flag,
        enabled=payload.enabled,
        default_variation=payload.default_variation,
        variations=payload.variations,
        rules=payload.rules,
        rollout=payload.rollout,
        progressive_stages=payload.progressive_stages,
        version=1,
        salt=salt,
    )
    config = FlagConfig(
        flag_id=flag.id,
        environment_id=environment.id,
        enabled=payload.enabled,
        default_variation_key=payload.default_variation,
        current_version=1,
        salt=salt,
    )
    session.add(config)
    await session.flush()
    _persist_children(session, config, compiled)
    session.add(
        FlagVersion(
            config_id=config.id,
            version=1,
            snapshot=compiled.model_dump(mode="json"),
            created_at=datetime.now(UTC),
            created_by_id=actor_user_id,
        )
    )
    environment.revision += 1
    record_audit(
        session,
        organization_id=project.organization_id,
        action="flag.created",
        resource_type="feature_flag",
        resource_id=flag.id,
        request_id=request_id,
        actor_user_id=actor_user_id,
        actor_api_key_id=actor_api_key_id,
        metadata={"flag_key": flag.key, "environment_id": str(environment.id), "version": 1},
    )
    return _response(flag, compiled)


async def update_flag(
    session: AsyncSession,
    *,
    flag: FeatureFlag,
    environment: Environment,
    project: Project,
    payload: FlagUpdate,
    actor_user_id: UUID | None,
    actor_api_key_id: UUID | None,
    request_id: str | None,
    action: str = "flag.updated",
    source_version: int | None = None,
) -> FlagResponse:
    config = await session.scalar(
        select(FlagConfig).where(
            FlagConfig.flag_id == flag.id, FlagConfig.environment_id == environment.id
        )
    )
    if not config or flag.archived_at is not None:
        raise APIError(404, "flag_not_found", "Feature flag was not found.")
    new_version = payload.expected_version + 1
    compiled = _build_configuration(
        project_id=project.id,
        environment=environment,
        flag=flag,
        enabled=payload.enabled,
        default_variation=payload.default_variation,
        variations=payload.variations,
        rules=payload.rules,
        rollout=payload.rollout,
        progressive_stages=payload.progressive_stages,
        version=new_version,
        salt=config.salt,
    )
    updated_id = await session.scalar(
        update(FlagConfig)
        .where(FlagConfig.id == config.id, FlagConfig.current_version == payload.expected_version)
        .values(
            enabled=payload.enabled,
            default_variation_key=payload.default_variation,
            current_version=new_version,
        )
        .returning(FlagConfig.id)
    )
    if not updated_id:
        current_version = await session.scalar(
            select(FlagConfig.current_version).where(FlagConfig.id == config.id)
        )
        raise APIError(
            409,
            "configuration_conflict",
            "The flag configuration was changed by another writer.",
            details={"current_version": current_version},
        )
    if payload.name is not None:
        flag.name = payload.name
    if payload.description is not None:
        flag.description = payload.description
    await _delete_children(session, config.id)
    _persist_children(session, config, compiled)
    session.add(
        FlagVersion(
            config_id=config.id,
            version=new_version,
            snapshot=compiled.model_dump(mode="json"),
            created_at=datetime.now(UTC),
            created_by_id=actor_user_id,
            source_version=source_version,
        )
    )
    await session.execute(
        update(Environment)
        .where(Environment.id == environment.id)
        .values(revision=Environment.revision + 1)
    )
    environment.revision += 1
    record_audit(
        session,
        organization_id=project.organization_id,
        action=action,
        resource_type="feature_flag",
        resource_id=flag.id,
        request_id=request_id,
        actor_user_id=actor_user_id,
        actor_api_key_id=actor_api_key_id,
        metadata={
            "flag_key": flag.key,
            "environment_id": str(environment.id),
            "version": new_version,
            **({"source_version": source_version} if source_version else {}),
        },
    )
    return _response(flag, compiled)


async def rollback_flag(
    session: AsyncSession,
    *,
    flag: FeatureFlag,
    environment: Environment,
    project: Project,
    target_version: int,
    expected_version: int,
    actor_user_id: UUID | None,
    actor_api_key_id: UUID | None,
    request_id: str | None,
) -> FlagResponse:
    config_id = await session.scalar(
        select(FlagConfig.id).where(
            FlagConfig.flag_id == flag.id, FlagConfig.environment_id == environment.id
        )
    )
    version = await session.scalar(
        select(FlagVersion).where(
            FlagVersion.config_id == config_id, FlagVersion.version == target_version
        )
    )
    if not version:
        raise APIError(404, "version_not_found", "Flag version was not found.")
    old = FlagConfiguration.model_validate(version.snapshot)
    payload = _update_from_configuration(old, environment.id, expected_version)
    return await update_flag(
        session,
        flag=flag,
        environment=environment,
        project=project,
        payload=payload,
        actor_user_id=actor_user_id,
        actor_api_key_id=actor_api_key_id,
        request_id=request_id,
        action="flag.rollback",
        source_version=target_version,
    )


def _update_from_configuration(
    config: FlagConfiguration, environment_id: UUID, expected_version: int
) -> FlagUpdate:
    return FlagUpdate(
        environment_id=environment_id,
        expected_version=expected_version,
        enabled=config.enabled,
        default_variation=config.default_variation,
        variations=[
            VariationInput(key=key, value=value) for key, value in config.variations.items()
        ],
        rules=[_rule_input(rule) for rule in config.rules],
        rollout=_rollout_inputs(config.rollout),
        progressive_stages=[
            ProgressiveStageInput(
                percentage_basis_points=stage.percentage_basis_points,
                variation=stage.variation,
                fallback_variation=stage.fallback_variation,
                activate_at=stage.activate_at,
            )
            for stage in config.progressive_stages
        ],
    )


def _rule_input(rule: EngineTargetingRule) -> RuleInput:
    return RuleInput(
        name=rule.name,
        priority=rule.priority,
        conditions=[item.model_dump() for item in rule.conditions],
        variation=rule.variation,
        rollout=_rollout_inputs(rule.rollout),
        progressive_stages=[
            ProgressiveStageInput(
                percentage_basis_points=stage.percentage_basis_points,
                variation=stage.variation,
                fallback_variation=stage.fallback_variation,
                activate_at=stage.activate_at,
            )
            for stage in rule.progressive_stages
        ],
    )


def _rollout_inputs(rollout: list[EngineRolloutAllocation]) -> list[RolloutInput]:
    previous = 0
    values = []
    for item in rollout:
        values.append(
            RolloutInput(
                variation=item.variation,
                percentage_basis_points=item.end_bucket - previous,
            )
        )
        previous = item.end_bucket
    return values


async def _delete_children(session: AsyncSession, config_id: UUID) -> None:
    await session.execute(delete(RolloutAllocation).where(RolloutAllocation.config_id == config_id))
    await session.execute(delete(ProgressiveStage).where(ProgressiveStage.config_id == config_id))
    await session.execute(delete(TargetingRule).where(TargetingRule.config_id == config_id))
    await session.execute(delete(FlagVariation).where(FlagVariation.config_id == config_id))


def _persist_children(
    session: AsyncSession, config: FlagConfig, compiled: FlagConfiguration
) -> None:
    session.add_all(
        [
            FlagVariation(config_id=config.id, key=key, value=value)
            for key, value in compiled.variations.items()
        ]
    )
    for allocation in compiled.rollout:
        session.add(
            RolloutAllocation(
                config_id=config.id,
                variation_key=allocation.variation,
                end_bucket=allocation.end_bucket,
            )
        )
    for stage in compiled.progressive_stages:
        session.add(_db_stage(stage, config_id=config.id))
    for rule in compiled.rules:
        db_rule = TargetingRule(
            config_id=config.id,
            name=rule.name,
            priority=rule.priority,
            enabled=True,
            serve_variation_key=rule.variation,
        )
        session.add(db_rule)
        for condition in rule.conditions:
            db_rule.conditions.append(
                RuleCondition(
                    attribute=condition.attribute,
                    operator=condition.operator,
                    value=condition.value,
                )
            )
        for allocation in rule.rollout:
            db_rule.rollouts.append(
                RolloutAllocation(
                    variation_key=allocation.variation,
                    end_bucket=allocation.end_bucket,
                )
            )
        for stage in rule.progressive_stages:
            db_rule.progressive_stages.append(_db_stage(stage))


def _db_stage(stage: EngineProgressiveStage, *, config_id: UUID | None = None) -> ProgressiveStage:
    return ProgressiveStage(
        config_id=config_id,
        variation_key=stage.variation,
        fallback_variation_key=stage.fallback_variation,
        percentage_basis_points=stage.percentage_basis_points,
        activate_at=stage.activate_at,
    )


def _response(flag: FeatureFlag, config: FlagConfiguration) -> FlagResponse:
    return FlagResponse(
        id=flag.id,
        project_id=flag.project_id,
        environment_id=UUID(config.environment_id),
        key=flag.key,
        name=flag.name,
        description=flag.description,
        flag_type=config.flag_type,
        enabled=config.enabled,
        default_variation=config.default_variation,
        current_version=config.config_version,
        archived_at=flag.archived_at,
        variations=[
            VariationInput(key=key, value=value) for key, value in config.variations.items()
        ],
        rules=[_rule_input(rule) for rule in config.rules],
        rollout=_rollout_inputs(config.rollout),
        progressive_stages=[
            ProgressiveStageInput(
                percentage_basis_points=stage.percentage_basis_points,
                variation=stage.variation,
                fallback_variation=stage.fallback_variation,
                activate_at=stage.activate_at,
            )
            for stage in config.progressive_stages
        ],
    )


async def append_rule_update(
    session: AsyncSession,
    config: FlagConfig,
    rule: RuleInput,
    expected_version: int,
) -> FlagUpdate:
    compiled = compile_configuration(config)
    payload = _update_from_configuration(compiled, config.environment_id, expected_version)
    payload.rules.append(rule)
    return payload
