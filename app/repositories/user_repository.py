import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.user import User
from app.schemas.auth import UserRegisterRequest
from app.schemas.user import UserProfileUpdate


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: str | uuid.UUID) -> User | None:
        try:
            parsed_user_id = uuid.UUID(str(user_id))
        except ValueError:
            return None
        return self.db.get(User, parsed_user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email.lower()).one_or_none()

    def get_by_document(self, document_type: str, document_number: str) -> User | None:
        return (
            self.db.query(User)
            .filter(
                User.document_type == document_type,
                User.document_number == document_number,
            )
            .one_or_none()
        )

    def create_from_registration(self, payload: UserRegisterRequest) -> User:
        user = User(
            email=str(payload.email).lower(),
            password_hash=get_password_hash(payload.password),
            first_name=payload.first_name,
            last_name=payload.last_name,
            full_name=f"{payload.first_name} {payload.last_name}".strip(),
            document_type=payload.document_type,
            document_number=payload.document_number,
            phone=payload.phone,
            role="user",
            is_active=True,
            is_verified=False,
            status="pending_verification",
        )
        self.db.add(user)
        self.db.flush()
        return user

    def create_from_google_profile(self, profile: Any) -> User:
        user = User(
            email=profile.email.lower(),
            password_hash=None,
            first_name=profile.first_name,
            last_name=profile.last_name,
            full_name=profile.full_name,
            avatar_url=profile.avatar_url,
            role="user",
            is_active=True,
            is_verified=profile.email_verified,
            status="active" if profile.email_verified else "pending_verification",
        )
        self.db.add(user)
        self.db.flush()
        return user

    def update_from_google_profile(self, user: User, profile: Any) -> User:
        user.first_name = profile.first_name
        user.last_name = profile.last_name
        user.full_name = profile.full_name
        user.avatar_url = profile.avatar_url
        user.is_verified = user.is_verified or profile.email_verified
        if profile.email_verified:
            user.status = "active"
        self.db.flush()
        return user

    def update_profile(self, user: User, payload: UserProfileUpdate) -> User:
        if payload.first_name is not None:
            user.first_name = payload.first_name
        if payload.last_name is not None:
            user.last_name = payload.last_name
        if payload.first_name is not None or payload.last_name is not None:
            first_name = user.first_name or ""
            last_name = user.last_name or ""
            user.full_name = f"{first_name} {last_name}".strip() or None
        if payload.phone is not None:
            user.phone = payload.phone
            user.phone_verified_at = None
        self.db.flush()
        return user
