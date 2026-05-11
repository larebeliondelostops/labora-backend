from datetime import datetime

from sqlalchemy.orm import Session

from app.models.oauth_state import OAuthState


class OAuthStateRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        state_hash: str,
        nonce_hash: str,
        provider: str,
        redirect_to: str,
        ip_address: str | None,
        user_agent: str | None,
        expires_at: datetime,
    ) -> OAuthState:
        oauth_state = OAuthState(
            state_hash=state_hash,
            nonce_hash=nonce_hash,
            provider=provider,
            redirect_to=redirect_to,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        self.db.add(oauth_state)
        self.db.commit()
        self.db.refresh(oauth_state)
        return oauth_state

    def get_by_state_hash(self, state_hash: str) -> OAuthState | None:
        return (
            self.db.query(OAuthState)
            .filter(OAuthState.state_hash == state_hash)
            .one_or_none()
        )

    def mark_consumed(self, oauth_state: OAuthState, consumed_at: datetime) -> OAuthState:
        oauth_state.consumed = True
        oauth_state.consumed_at = consumed_at
        self.db.commit()
        self.db.refresh(oauth_state)
        return oauth_state
