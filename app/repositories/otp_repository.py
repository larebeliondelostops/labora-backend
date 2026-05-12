import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.otp_code import OTPCode


class OTPRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def invalidate_active(self, *, recipient: str, purpose: str) -> None:
        active_codes = (
            self.db.query(OTPCode)
            .filter(
                OTPCode.recipient == recipient,
                OTPCode.purpose == purpose,
                OTPCode.consumed_at.is_(None),
            )
            .all()
        )
        now = datetime.utcnow()
        for otp_code in active_codes:
            otp_code.consumed_at = now
        self.db.flush()

    def create(
        self,
        *,
        user_id: uuid.UUID | None,
        recipient: str,
        purpose: str,
        code_hash: str,
    ) -> OTPCode:
        otp_code = OTPCode(
            user_id=user_id,
            channel="email",
            recipient=recipient,
            purpose=purpose,
            code_hash=code_hash,
            attempts=0,
            max_attempts=settings.otp_max_attempts,
            expires_at=datetime.utcnow() + timedelta(minutes=settings.otp_ttl_minutes),
        )
        self.db.add(otp_code)
        self.db.flush()
        return otp_code

    def get_latest_active(self, *, recipient: str, purpose: str) -> OTPCode | None:
        return (
            self.db.query(OTPCode)
            .filter(
                OTPCode.recipient == recipient,
                OTPCode.purpose == purpose,
                OTPCode.consumed_at.is_(None),
            )
            .order_by(OTPCode.created_at.desc())
            .first()
        )
