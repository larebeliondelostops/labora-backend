from fastapi import Depends, Request, status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User
from app.repositories.user_repository import UserRepository


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def get_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def get_bearer_or_cookie_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(settings.auth_cookie_name)


def get_optional_auth_payload(request: Request) -> dict | None:
    token = get_bearer_or_cookie_token(request)
    if not token:
        return None
    return decode_access_token(token)


def get_current_user_context(
    request: Request,
    db: Session = Depends(get_db),
) -> tuple[User, dict]:
    payload = get_optional_auth_payload(request)
    if payload is None:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message="No autenticado.",
        )

    user = UserRepository(db).get_by_id(payload["sub"])
    if user is None or not user.is_active or user.status != "active":
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message="Sesion invalida.",
        )
    return user, payload
