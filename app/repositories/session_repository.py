import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.models.session import Session as UserSession


class SessionRepository:
    def __init__(self, db: DbSession) -> None:
        self.db = db

    def create(
        self,
        *,
        user_id: uuid.UUID,
        refresh_token_hash: str,
        ip_address: str | None,
        user_agent: str | None,
        device_name: str | None,
    ) -> UserSession:
        now = datetime.utcnow()
        session = UserSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            device_name=device_name,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        )
        self.db.add(session)
        self.db.flush()
        return session

    def get_by_refresh_token_hash(self, refresh_token_hash: str) -> UserSession | None:
        return (
            self.db.query(UserSession)
            .filter(UserSession.refresh_token_hash == refresh_token_hash)
            .one_or_none()
        )

    def get_by_id(self, session_id: str | uuid.UUID) -> UserSession | None:
        try:
            parsed_session_id = uuid.UUID(str(session_id))
        except ValueError:
            return None
        return self.db.get(UserSession, parsed_session_id)

    def list_active_for_user(self, user_id: uuid.UUID) -> list[UserSession]:
        now = datetime.utcnow()
        return (
            self.db.query(UserSession)
            .filter(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.created_at.desc())
            .all()
        )

    def rotate(self, session: UserSession, refresh_token_hash: str) -> UserSession:
        session.refresh_token_hash = refresh_token_hash
        session.last_used_at = datetime.utcnow()
        session.expires_at = datetime.utcnow() + timedelta(days=settings.refresh_token_ttl_days)
        self.db.flush()
        return session

    def revoke(self, session: UserSession, reason: str) -> None:
        session.revoked_at = datetime.utcnow()
        session.revoked_reason = reason
        self.db.flush()

    def revoke_all_for_user(self, user_id: uuid.UUID, reason: str) -> None:
        for session in self.list_active_for_user(user_id):
            self.revoke(session, reason)
