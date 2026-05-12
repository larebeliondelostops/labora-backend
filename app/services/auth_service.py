from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.external_auth_repository import ExternalAuthRepository
from app.repositories.user_repository import UserRepository
from app.services.google_oauth_service import GoogleUserProfile


class UserDisabledError(Exception):
    pass


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.external_accounts = ExternalAuthRepository(db)

    def complete_google_login(self, profile: GoogleUserProfile) -> User:
        account = self.external_accounts.get_by_provider_user_id(
            profile.provider,
            profile.provider_user_id,
        )

        if account is not None:
            user = self.users.get_by_id(account.user_id)
            if user is None:
                user = self.users.get_by_email(profile.email)
        else:
            user = self.users.get_by_email(profile.email)

        if user is None:
            user = self.users.create_from_google_profile(profile)
        else:
            if not user.is_active or user.status in {"blocked", "suspended", "deleted"}:
                raise UserDisabledError("User is disabled")
            user = self.users.update_from_google_profile(user, profile)

        self.external_accounts.create_or_update_for_user(user, profile)
        self.db.commit()
        self.db.refresh(user)
        return user
