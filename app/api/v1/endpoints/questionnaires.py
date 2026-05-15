from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.questionnaire import (
    AdminQuestionnaireReviewRequest,
    AnswerPatchRequest,
    QuestionnaireAnswersRequest,
    QuestionnaireStartRequest,
    QuestionnaireSubmitRequest,
)
from app.services.questionnaire_service import QuestionnaireService


router = APIRouter()
admin_router = APIRouter()
internal_router = APIRouter()


@router.get("/cases/{case_id}/questionnaire")
def get_questionnaire(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return QuestionnaireService(db).get_questionnaire(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/questionnaire/start")
def start_questionnaire(
    case_id: str,
    payload: QuestionnaireStartRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).start_questionnaire(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/questionnaire/answers")
def save_questionnaire_answers(
    case_id: str,
    payload: QuestionnaireAnswersRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).save_answers(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch("/answers/{answer_id}")
def patch_answer(
    answer_id: str,
    payload: AnswerPatchRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).patch_answer(
        answer_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/questionnaire/submit")
def submit_questionnaire(
    case_id: str,
    payload: QuestionnaireSubmitRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).submit_questionnaire(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/profile")
def get_case_profile(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).get_profile(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.get("/questionnaire-sessions")
def list_questionnaire_sessions(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).list_admin_sessions(
        user=user,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@admin_router.patch("/questionnaire-sessions/{session_id}/review")
def review_questionnaire_session(
    session_id: str,
    payload: AdminQuestionnaireReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).review_admin_session(
        session_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.get("/cases/{case_id}/questionnaire")
def get_admin_case_questionnaire(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).get_questionnaire(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@internal_router.post("/cases/{case_id}/questionnaire/recompute-profile")
def recompute_questionnaire_profile(
    case_id: str,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return QuestionnaireService(db).recompute_internal_profile(case_id, user=user)
