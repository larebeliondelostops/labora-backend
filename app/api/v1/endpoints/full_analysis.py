from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.full_analysis import (
    CalculationsResponse,
    ConfidenceResponse,
    FullAnalysisCreateRequest,
    FullAnalysisResultResponse,
    FullAnalysisRetryRequest,
    FullAnalysisReviewDecisionRequest,
    InconsistenciesResponse,
    RuleResultsResponse,
    ScenariosResponse,
)
from app.services.full_analysis_service import FullAnalysisService
from app.workers.analysis_worker import run_full_analysis_background


router = APIRouter()
admin_router = APIRouter()

ENQUEUED_MESSAGE = "El analisis completo fue encolado correctamente."


@router.post("/cases/{case_id}/full-analysis")
def create_full_analysis(
    case_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: FullAnalysisCreateRequest | None = Body(default=None),
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    payload = payload or FullAnalysisCreateRequest()
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body, status_code = FullAnalysisService(db).create_or_reuse(
        case_id,
        force_reprocess=payload.force_reprocess,
        reason=payload.reason,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if _should_start_background_job(body, status_code):
        _enqueue_background_job(
            background_tasks,
            db=db,
            full_analysis_id=body["id"],
            user_id=str(user.id),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return JSONResponse(status_code=status_code, content=body)


@router.get(
    "/cases/{case_id}/full-analysis",
    response_model=FullAnalysisResultResponse,
)
def get_full_analysis(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return FullAnalysisService(db).get_result(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/rules-results",
    response_model=RuleResultsResponse,
)
def get_rule_results(
    case_id: str,
    request: Request,
    category: Annotated[str | None, Query()] = None,
    result: Annotated[str | None, Query()] = None,
    requires_review: Annotated[bool | None, Query(alias="requiresReview")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return FullAnalysisService(db).list_rule_results(
        case_id,
        category=category,
        result=result,
        requires_review=requires_review,
        page=page,
        limit=limit,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/calculations",
    response_model=CalculationsResponse,
)
def get_calculations(
    case_id: str,
    request: Request,
    calculation_type: Annotated[str | None, Query(alias="type")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return FullAnalysisService(db).list_calculations(
        case_id,
        calculation_type=calculation_type,
        page=page,
        limit=limit,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/full-analysis/scenarios",
    response_model=ScenariosResponse,
)
def get_scenarios(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return FullAnalysisService(db).list_scenarios(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/full-analysis/inconsistencies",
    response_model=InconsistenciesResponse,
)
def get_inconsistencies(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return FullAnalysisService(db).list_inconsistencies(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/full-analysis/confidence",
    response_model=ConfidenceResponse,
)
def get_confidence(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return FullAnalysisService(db).get_confidence(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/full-analysis/retry")
def retry_full_analysis(
    case_id: str,
    payload: FullAnalysisRetryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body, status_code = FullAnalysisService(db).retry(
        case_id,
        reason=payload.reason,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if _should_start_background_job(body, status_code):
        _enqueue_background_job(
            background_tasks,
            db=db,
            full_analysis_id=body["id"],
            user_id=str(user.id),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return JSONResponse(status_code=status_code, content=body)


@admin_router.post(
    "/cases/{case_id}/full-analysis/review-decision",
    response_model=FullAnalysisResultResponse,
)
def review_full_analysis(
    case_id: str,
    payload: FullAnalysisReviewDecisionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    result = FullAnalysisService(db).review_decision(
        case_id,
        decision=payload.decision,
        notes=payload.notes,
        adjustments=payload.adjustments,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    if payload.decision == "needs_recalculation" and result.get("id"):
        _enqueue_background_job(
            background_tasks,
            db=db,
            full_analysis_id=result["id"],
            user_id=str(user.id),
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    return result


def _should_start_background_job(body: dict, status_code: int) -> bool:
    return status_code == 202 and body.get("id") is not None and body.get("message") == ENQUEUED_MESSAGE


def _enqueue_background_job(
    background_tasks: BackgroundTasks,
    *,
    db: Session,
    full_analysis_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db.get_bind(),
    )
    background_tasks.add_task(
        run_full_analysis_background,
        full_analysis_id,
        actor_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        session_factory=session_factory,
    )
