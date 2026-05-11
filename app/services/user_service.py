import uuid

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.user_repository import UserRepository


class UserService:
    def __init__(self, db: Session) -> None:
        self.users = UserRepository(db)

    def get_active_user(self, user_id: str | uuid.UUID) -> User | None:
        user = self.users.get_by_id(user_id)
        if user is None or not user.is_active:
            return None
        return user
