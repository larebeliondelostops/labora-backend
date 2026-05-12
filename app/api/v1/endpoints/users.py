from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.auth import DataResponse
from app.schemas.user import UserProfileUpdate
from app.services.account_auth_service import AccountAuthService

router = APIRouter()


@router.get("/me", response_model=DataResponse)
def get_me(
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    account_user = AccountAuthService(db)._account_user(user)
    return {"data": account_user.model_dump(by_alias=True)}


@router.patch("/me", response_model=DataResponse)
def update_me(
    payload: UserProfileUpdate,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    account_user = AccountAuthService(db).update_profile(
        user=user,
        payload=payload,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": account_user.model_dump(by_alias=True)}


@router.get("/me/sessions", response_model=DataResponse)
def list_sessions(
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, token_payload = context
    sessions = AccountAuthService(db).list_sessions(
        user=user,
        current_session_id=token_payload.get("sid"),
    )
    return {"data": [session.model_dump(by_alias=True) for session in sessions]}


@router.delete("/me/sessions/{session_id}", response_model=DataResponse)
def revoke_session(
    session_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    data = AccountAuthService(db).revoke_session(
        user=user,
        session_id=session_id,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}
