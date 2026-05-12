import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.password_reset_token import PasswordResetToken


class PasswordResetRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def invalidate_active_for_user(self, user_id: uuid.UUID) -> None:
        now = datetime.utcnow()
        active_tokens = (
            self.db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
            .all()
        )
        for token in active_tokens:
            token.used_at = now
        self.db.flush()

    def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        ttl_minutes: int = 60,
    ) -> PasswordResetToken:
        reset_token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
        )
        self.db.add(reset_token)
        self.db.flush()
        return reset_token

    def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        return (
            self.db.query(PasswordResetToken)
            .filter(PasswordResetToken.token_hash == token_hash)
            .one_or_none()
        )
