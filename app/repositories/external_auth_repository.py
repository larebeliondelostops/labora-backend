from typing import Any

from sqlalchemy.orm import Session

from app.models.external_auth_account import ExternalAuthAccount
from app.models.user import User


class ExternalAuthRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_provider_user_id(
        self,
        provider: str,
        provider_user_id: str,
    ) -> ExternalAuthAccount | None:
        return (
            self.db.query(ExternalAuthAccount)
            .filter(
                ExternalAuthAccount.provider == provider,
                ExternalAuthAccount.provider_user_id == provider_user_id,
            )
            .one_or_none()
        )

    def get_by_user_and_provider(
        self,
        user: User,
        provider: str,
    ) -> ExternalAuthAccount | None:
        return (
            self.db.query(ExternalAuthAccount)
            .filter(
                ExternalAuthAccount.user_id == user.id,
                ExternalAuthAccount.provider == provider,
            )
            .one_or_none()
        )

    def create_or_update_for_user(
        self,
        user: User,
        profile: Any,
    ) -> ExternalAuthAccount:
        account = self.get_by_provider_user_id(
            profile.provider,
            profile.provider_user_id,
        )
        if account is None:
            account = self.get_by_user_and_provider(user, profile.provider)

        if account is None:
            account = ExternalAuthAccount(
                user_id=user.id,
                provider=profile.provider,
                provider_user_id=profile.provider_user_id,
                provider_email=profile.email.lower(),
                provider_email_verified=profile.email_verified,
            )
            self.db.add(account)
        else:
            account.user_id = user.id
            account.provider_email = profile.email.lower()
            account.provider_email_verified = profile.email_verified

        self.db.flush()
        return account
