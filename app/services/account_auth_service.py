from datetime import datetime
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session as DbSession

from app.core.api_errors import ApiError
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_opaque_token,
    generate_otp_code,
    get_password_hash,
    hash_otp_code,
    hash_token,
    verify_otp_code,
    verify_password,
)
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.otp_repository import OTPRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import UserRegisterRequest
from app.schemas.user import AccountUser, UserProfileUpdate, UserSessionResponse
from app.services.email_service import EmailService


class AccountAuthService:
    def __init__(self, db: DbSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.sessions = SessionRepository(db)
        self.otps = OTPRepository(db)
        self.password_resets = PasswordResetRepository(db)
        self.audit_events = AuditEventRepository(db)
        self.email = EmailService()

    def register(
        self,
        payload: UserRegisterRequest,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if self.users.get_by_email(str(payload.email)):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="EMAIL_ALREADY_EXISTS",
                message="El correo ya esta registrado.",
            )
        if self.users.get_by_document(payload.document_type, payload.document_number):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_ALREADY_EXISTS",
                message="El documento ya esta registrado.",
            )

        user = self.users.create_from_registration(payload)
        otp_code = self._issue_otp(
            user=user,
            recipient=user.email,
            purpose="register",
        )
        self.email.send_otp(recipient=user.email, code=otp_code, purpose="register")
        self._audit(
            "cuenta_autenticacion.created",
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            "auth.otp.sent",
            user=user,
            metadata={"purpose": "register", "channel": "email"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()

        return {
            "user": self._account_user(user).model_dump(by_alias=True),
            "nextStep": "verify_otp",
        }

    def login(
        self,
        *,
        email: str,
        password: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        user = self.users.get_by_email(email)
        if user is None or not user.password_hash or not verify_password(password, user.password_hash):
            self._audit(
                "auth.login.failed",
                entity_type="user",
                metadata={"email": email.lower()},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="INVALID_CREDENTIALS",
                message="Credenciales invalidas.",
            )

        if user.status == "pending_verification" or not user.email_verified_at:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ACCOUNT_NOT_VERIFIED",
                message="La cuenta requiere verificacion.",
                details=[{"nextStep": "verify_otp"}],
            )

        if user.status in {"blocked", "suspended", "deleted"} or not user.is_active:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ACCOUNT_BLOCKED",
                message="La cuenta no esta habilitada.",
            )

        user.last_login_at = datetime.utcnow()
        tokens = self._create_session_tokens(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            "auth.login.succeeded",
            user=user,
            metadata={"sessionId": tokens["sessionId"]},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()

        return {
            "accessToken": tokens["accessToken"],
            "refreshToken": tokens["refreshToken"],
            "expiresIn": settings.jwt_access_ttl_seconds,
            "user": self._account_user(user).model_dump(by_alias=True),
            "nextStep": "dashboard",
        }

    def create_session_for_user(
        self,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if user.status in {"blocked", "suspended", "deleted"} or not user.is_active:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ACCOUNT_BLOCKED",
                message="La cuenta no esta habilitada.",
            )

        user.last_login_at = datetime.utcnow()
        tokens = self._create_session_tokens(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            "auth.login.succeeded",
            user=user,
            metadata={"sessionId": tokens["sessionId"], "provider": "google"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()

        account_user = self._account_user(user)
        return {
            "accessToken": tokens["accessToken"],
            "refreshToken": tokens["refreshToken"],
            "expiresIn": settings.jwt_access_ttl_seconds,
            "user": account_user.model_dump(by_alias=True),
            "nextStep": account_user.next_step,
            "sessionId": tokens["sessionId"],
        }

    def send_register_otp_for_user(
        self,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if user.status != "pending_verification" and user.email_verified_at:
            return {"sent": False, "reason": "already_verified"}

        otp_code = self._issue_otp(
            user=user,
            recipient=user.email,
            purpose="register",
        )
        self.email.send_otp(recipient=user.email, code=otp_code, purpose="register")
        self._audit(
            "auth.otp.sent",
            user=user,
            metadata={"purpose": "register", "channel": "email", "trigger": "google_callback"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"sent": True, "cooldownSeconds": 60}

    def verify_otp(
        self,
        *,
        recipient: str,
        purpose: str,
        code: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        otp_code = self.otps.get_latest_active(recipient=recipient, purpose=purpose)
        if otp_code is None:
            raise self._otp_error("OTP_INVALID", "Codigo OTP invalido.")

        now = datetime.utcnow()
        if otp_code.expires_at <= now:
            raise self._otp_error("OTP_EXPIRED", "El codigo OTP expiro.")

        if otp_code.attempts >= otp_code.max_attempts:
            raise self._otp_error("OTP_ATTEMPTS_EXCEEDED", "Se superaron los intentos.")

        if not verify_otp_code(code, otp_code.code_hash):
            otp_code.attempts += 1
            self._audit(
                "auth.otp.failed",
                entity_type="otp_code",
                entity_id=otp_code.id,
                metadata={"purpose": purpose},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            raise self._otp_error("OTP_INVALID", "Codigo OTP invalido.")

        otp_code.consumed_at = now
        user = self.users.get_by_id(otp_code.user_id) if otp_code.user_id else None
        if user is not None and purpose == "register":
            user.status = "active"
            user.is_verified = True
            user.email_verified_at = now

        self._audit(
            "auth.otp.verified",
            user=user,
            entity_type="otp_code",
            entity_id=otp_code.id,
            metadata={"purpose": purpose},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()

        next_step = (
            "complete_profile"
            if user is not None and not _registration_completed(user)
            else "consents"
        )
        return {
            "verified": True,
            "userStatus": user.status if user else "active",
            "nextStep": next_step,
        }

    def resend_otp(
        self,
        *,
        recipient: str,
        purpose: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        user = self.users.get_by_email(recipient)
        if user is not None:
            otp_code = self._issue_otp(
                user=user,
                recipient=recipient,
                purpose=purpose,
            )
            self.email.send_otp(recipient=recipient, code=otp_code, purpose=purpose)
            self._audit(
                "auth.otp.sent",
                user=user,
                metadata={"purpose": purpose, "channel": "email"},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
        return {"sent": True, "cooldownSeconds": 60}

    def forgot_password(
        self,
        *,
        email: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, str]:
        user = self.users.get_by_email(email)
        if user is not None:
            self.password_resets.invalidate_active_for_user(user.id)
            token = create_opaque_token()
            self.password_resets.create(user_id=user.id, token_hash=hash_token(token))
            self.email.send_password_reset(recipient=user.email, token=token)
            self._audit(
                "auth.password_reset.requested",
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
        return {
            "message": "Si el correo existe, enviaremos instrucciones para restablecer la contrasena."
        }

    def reset_password(
        self,
        *,
        token: str,
        new_password: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, bool]:
        reset_token = self.password_resets.get_by_token_hash(hash_token(token))
        now = datetime.utcnow()
        if reset_token is None or reset_token.used_at is not None:
            raise ApiError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="TOKEN_INVALID",
                message="Token invalido.",
            )
        if reset_token.expires_at <= now:
            raise ApiError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="TOKEN_EXPIRED",
                message="Token expirado.",
            )

        user = self.users.get_by_id(reset_token.user_id)
        if user is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="RESOURCE_NOT_FOUND",
                message="Usuario no encontrado.",
            )

        user.password_hash = get_password_hash(new_password)
        reset_token.used_at = now
        self.sessions.revoke_all_for_user(user.id, "password_reset")
        self._audit(
            "auth.password_reset.completed",
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"passwordUpdated": True, "sessionsRevoked": True}

    def refresh(
        self,
        *,
        refresh_token: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        session = self.sessions.get_by_refresh_token_hash(hash_token(refresh_token))
        now = datetime.utcnow()
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= now
        ):
            raise ApiError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="TOKEN_INVALID",
                message="Refresh token invalido.",
            )

        user = self.users.get_by_id(session.user_id)
        if user is None or not user.is_active or user.status != "active":
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ACCOUNT_BLOCKED",
                message="La cuenta no esta habilitada.",
            )

        new_refresh_token = create_opaque_token()
        self.sessions.rotate(session, hash_token(new_refresh_token))
        access_token = create_access_token(
            str(user.id),
            {"role": user.role, "sid": str(session.id)},
        )
        self.db.commit()
        return {
            "accessToken": access_token,
            "refreshToken": new_refresh_token,
            "expiresIn": settings.jwt_access_ttl_seconds,
        }

    def logout_session(
        self,
        *,
        session_id: str | None,
        user: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, bool]:
        session = self.sessions.get_by_id(session_id) if session_id else None
        if session is not None:
            self.sessions.revoke(session, "logout")
        self._audit(
            "auth.logout.succeeded",
            user=user,
            metadata={"sessionId": session_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"loggedOut": True}

    def logout_all(self, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, bool]:
        self.sessions.revoke_all_for_user(user.id, "logout_all")
        self._audit(
            "auth.session.revoked",
            user=user,
            metadata={"scope": "all"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"sessionsRevoked": True}

    def update_profile(
        self,
        *,
        user: User,
        payload: UserProfileUpdate,
        ip_address: str | None,
        user_agent: str | None,
    ) -> AccountUser:
        document_type = payload.document_type or user.document_type
        document_number = payload.document_number or user.document_number
        if document_type and document_number:
            existing_user = self.users.get_by_document(document_type, document_number)
            if existing_user is not None and existing_user.id != user.id:
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="DOCUMENT_ALREADY_EXISTS",
                    message="El documento ya esta registrado.",
                )

        previous_state = {
            "firstName": user.first_name,
            "lastName": user.last_name,
            "documentType": user.document_type,
            "documentNumber": _mask_document(user.document_number),
            "phone": _mask_phone(user.phone),
        }
        self.users.update_profile(user, payload)
        self._audit(
            "cuenta_autenticacion.updated",
            user=user,
            previous_state=previous_state,
            new_state={
                "firstName": user.first_name,
                "lastName": user.last_name,
                "documentType": user.document_type,
                "documentNumber": _mask_document(user.document_number),
                "phone": _mask_phone(user.phone),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(user)
        return self._account_user(user)

    def list_sessions(self, *, user: User, current_session_id: str | None) -> list[UserSessionResponse]:
        return [
            UserSessionResponse(
                id=str(session.id),
                device_name=session.device_name,
                ip_address_masked=_mask_ip(session.ip_address),
                created_at=session.created_at,
                last_used_at=session.last_used_at,
                current=str(session.id) == str(current_session_id),
            )
            for session in self.sessions.list_active_for_user(user.id)
        ]

    def revoke_session(
        self,
        *,
        user: User,
        session_id: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, bool]:
        session = self.sessions.get_by_id(session_id)
        if session is None or session.user_id != user.id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="RESOURCE_NOT_FOUND",
                message="Sesion no encontrada.",
            )
        self.sessions.revoke(session, "user_revoked")
        self._audit(
            "auth.session.revoked",
            user=user,
            metadata={"sessionId": session_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"revoked": True}

    def _issue_otp(self, *, user: User, recipient: str, purpose: str) -> str:
        self.otps.invalidate_active(recipient=recipient, purpose=purpose)
        code = generate_otp_code()
        self.otps.create(
            user_id=user.id,
            recipient=recipient,
            purpose=purpose,
            code_hash=hash_otp_code(code),
        )
        return code

    def _create_session_tokens(
        self,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, str]:
        refresh_token = create_opaque_token()
        session = self.sessions.create(
            user_id=user.id,
            refresh_token_hash=hash_token(refresh_token),
            ip_address=ip_address,
            user_agent=user_agent,
            device_name=_device_name(user_agent),
        )
        access_token = create_access_token(
            str(user.id),
            {"role": user.role, "sid": str(session.id)},
        )
        return {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "sessionId": str(session.id),
        }

    def _account_user(self, user: User) -> AccountUser:
        email_verified = user.email_verified_at is not None or user.is_verified
        registration_completed = _registration_completed(user)
        requires_otp = user.status == "pending_verification" or not email_verified
        return AccountUser(
            id=str(user.id),
            first_name=user.first_name,
            last_name=user.last_name,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            document_type=user.document_type,
            document_number_masked=_mask_document(user.document_number),
            email=user.email,
            phone_masked=_mask_phone(user.phone),
            status=user.status,
            email_verified=email_verified,
            phone_verified=user.phone_verified_at is not None,
            requires_otp=requires_otp,
            registration_completed=registration_completed,
            next_step=_next_step(user, requires_otp, registration_completed),
            roles=[user.role],
            created_at=user.created_at,
        )

    def _audit(
        self,
        event_type: str,
        *,
        user: User | None = None,
        entity_type: str = "user",
        entity_id=None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.audit_events.create(
            event_type=event_type,
            entity_type=entity_type,
            actor_user_id=user.id if user else None,
            entity_id=entity_id or (user.id if user else None),
            previous_state=previous_state,
            new_state=new_state,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _otp_error(self, code: str, message: str) -> ApiError:
        return ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=code,
            message=message,
        )


def _mask_document(document_number: str | None) -> str | None:
    if not document_number:
        return None
    suffix = document_number[-4:]
    return f"{'*' * max(len(document_number) - 4, 0)}{suffix}"


def _registration_completed(user: User) -> bool:
    return all(
        [
            user.first_name,
            user.last_name,
            user.document_type,
            user.document_number,
        ]
    )


def _next_step(
    user: User,
    requires_otp: bool,
    registration_completed: bool,
) -> str:
    if requires_otp:
        return "verify_otp"
    if not registration_completed:
        return "complete_profile"
    if user.status in {"blocked", "suspended", "deleted"}:
        return "contact_support"
    return "dashboard"


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    suffix = phone[-4:]
    prefix = phone[:3] if phone.startswith("+") else ""
    middle = "*" * max(len(phone) - len(prefix) - 4, 0)
    return f"{prefix}{middle}{suffix}"


def _mask_ip(ip_address: str | None) -> str | None:
    if not ip_address:
        return None
    parts = ip_address.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.***.***.{parts[-1]}"
    return "***"


def _device_name(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    if "Chrome" in user_agent:
        return "Chrome"
    if "Firefox" in user_agent:
        return "Firefox"
    if "Safari" in user_agent:
        return "Safari"
    return user_agent[:120]
