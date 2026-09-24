# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scorer API routes."""

from fastapi import APIRouter, Path, Request, status
from fastapi.responses import JSONResponse

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scorers import (
    CreateScorerRequest,
    ScoreAttackRequest,
    ScoreAttackResponse,
    ScorerCatalogResponse,
    ScorerInstance,
    ScorerListResponse,
    ScorerValidationResponse,
)
from pyrit.backend.services.scorer_service import ScorerConflictError, get_scorer_service

router = APIRouter(prefix="/scorers", tags=["scorers"])


@router.get(
    "",
    response_model=ScorerListResponse,
)
async def list_scorers() -> ScorerListResponse:  # pyrit-async-suffix-exempt
    """
    List registered scorer instances.

    Returns:
        ScorerListResponse: Registered scorer instances.
    """
    service = get_scorer_service()
    return await service.list_scorers_async()


@router.get(
    "/catalog",
    response_model=ScorerCatalogResponse,
)
async def list_scorer_catalog() -> ScorerCatalogResponse:  # pyrit-async-suffix-exempt
    """
    List scorer types available from the backend scorer registry.

    Returns:
        ScorerCatalogResponse: Available scorer classes and their parameter contracts.
    """
    service = get_scorer_service()
    return await service.list_scorer_catalog_async()


@router.post(
    "",
    response_model=ScorerInstance,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid scorer type or parameters"},
        422: {"model": ProblemDetail, "description": "Request validation failed"},
    },
)
async def create_scorer(request: CreateScorerRequest) -> ScorerInstance:  # pyrit-async-suffix-exempt
    """
    Create a new scorer instance.

    Returns:
        ScorerInstance: The created scorer instance.
    """
    service = get_scorer_service()
    return await service.create_scorer_async(request=request)


@router.post(
    "/validate",
    response_model=ScorerValidationResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid scorer type or parameters"},
        422: {"model": ProblemDetail, "description": "Request validation failed"},
    },
)
async def validate_scorer(request: CreateScorerRequest) -> ScorerValidationResponse:  # pyrit-async-suffix-exempt
    """
    Validate a scorer configuration without registering it.

    Returns:
        ScorerValidationResponse: ``{"valid": true}`` when the configuration is constructible.
    """
    service = get_scorer_service()
    return await service.validate_scorer_request_async(request=request)


@router.get(
    "/{scorer_id}",
    response_model=ScorerInstance,
    responses={
        404: {"model": ProblemDetail, "description": "Scorer not found"},
    },
)
async def get_scorer(
    scorer_id: str = Path(..., min_length=1, max_length=200, description="Scorer instance ID or alias"),
) -> ScorerInstance:  # pyrit-async-suffix-exempt
    """
    Get a scorer instance by ID or alias.

    Returns:
        ScorerInstance: The requested scorer instance.
    """
    service = get_scorer_service()
    scorer = await service.get_scorer_async(scorer_id=scorer_id)
    if scorer is None:
        raise FileNotFoundError(f"Scorer '{scorer_id}' not found")
    return scorer


@router.post(
    "/{scorer_id}/score",
    response_model=ScoreAttackResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid score request"},
        404: {"model": ProblemDetail, "description": "Scorer or attack not found"},
        409: {"model": ProblemDetail, "description": "Scorer hash or attack conversation mismatch"},
        422: {"model": ProblemDetail, "description": "Request validation failed"},
    },
)
async def score_attack_result(
    http_request: Request,
    request: ScoreAttackRequest,
    scorer_id: str = Path(..., min_length=1, max_length=200, description="Scorer instance ID or alias"),
) -> ScoreAttackResponse | JSONResponse:  # pyrit-async-suffix-exempt
    """
    Score stored attack evidence with a registered scorer.

    Returns:
        ScoreAttackResponse | JSONResponse: Score results, or a conflict problem response.
    """
    service = get_scorer_service()
    try:
        return await service.score_attack_result_async(scorer_id=scorer_id, request=request)
    except ScorerConflictError as exc:
        problem = ProblemDetail(
            type="/errors/conflict",
            title="Conflict",
            status=status.HTTP_409_CONFLICT,
            detail=str(exc),
            instance=str(http_request.url.path),
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=problem.model_dump(exclude_none=True),
        )
