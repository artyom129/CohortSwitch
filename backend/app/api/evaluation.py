from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import Principal, get_principal, require_api_scope
from backend.app.api.schemas import (
    BatchEvaluationRequest,
    BatchEvaluationResponse,
    EvaluationRequest,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.metrics import EVALUATIONS
from backend.app.core.redis import redis_client
from backend.app.database.session import get_session
from backend.app.evaluation.engine import evaluate_flag
from backend.app.evaluation.models import EvaluationContext, EvaluationResult
from backend.app.services.cache import load_configuration, load_configurations

router = APIRouter(prefix="/api/v1/evaluate", tags=["evaluation"])


def _scope(principal: Principal) -> tuple[object, object]:
    require_api_scope(principal, "evaluate")
    if not principal.project_id or not principal.environment_id:
        from backend.app.core.errors import APIError

        raise APIError(403, "invalid_key_scope", "API key is not scoped to an environment.")
    return principal.project_id, principal.environment_id


def _observe(environment: str, result: EvaluationResult) -> None:
    EVALUATIONS.labels(environment, result.reason.value, result.variation).inc()


@router.post("", response_model=EvaluationResult)
async def evaluate(
    payload: EvaluationRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> EvaluationResult:
    project_id, environment_id = _scope(principal)
    config = await load_configuration(
        session,
        redis_client,
        settings,
        project_id,
        environment_id,
        payload.flag_key,
    )
    result = evaluate_flag(
        config,
        EvaluationContext(subject_key=payload.subject_key, attributes=payload.attributes),
    )
    _observe(config.environment_key, result)
    return result


@router.post("/batch", response_model=BatchEvaluationResponse)
async def evaluate_batch(
    payload: BatchEvaluationRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BatchEvaluationResponse:
    project_id, environment_id = _scope(principal)
    configs = await load_configurations(
        session,
        redis_client,
        settings,
        project_id,
        environment_id,
        payload.flags,
    )
    context = EvaluationContext(subject_key=payload.subject_key, attributes=payload.attributes)
    results = {key: evaluate_flag(configs[key], context) for key in payload.flags}
    for key, result in results.items():
        _observe(configs[key].environment_key, result)
    return BatchEvaluationResponse(results=results)
