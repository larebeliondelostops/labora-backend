from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.external_auth_repository import ExternalAuthRepository
from app.repositories.user_repository import UserRepository
from app.services.google_oauth_service import EmailNotVerifiedError, GoogleUserProfile


class UserDisabledError(Exception):
    pass


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.external_accounts = ExternalAuthRepository(db)

    def complete_google_login(self, profile: GoogleUserProfile) -> User:
        if profile.email_verified is not True:
            raise EmailNotVerifiedError("Google email is not verified")

        profile = GoogleUserProfile(
            provider=profile.provider,
            provider_user_id=profile.provider_user_id,
            email=profile.email.strip().lower(),
            email_verified=True,
            full_name=profile.full_name,
            first_name=profile.first_name,
            last_name=profile.last_name,
            avatar_url=profile.avatar_url,
        )
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
