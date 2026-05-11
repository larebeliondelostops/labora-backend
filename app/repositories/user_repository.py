import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.user import User


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
        self.db.flush()
        return user
