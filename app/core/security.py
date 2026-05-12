import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, extra_claims: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.jwt_access_ttl_seconds)
    payload = {
        "sub": subject,
        "type": "access",
        "role": "user",
        "iat": now,
        "exp": expires_at,
    }
    if extra_claims:
        payload.update(extra_claims)
        payload["sub"] = subject
        payload["type"] = "access"
        payload["exp"] = expires_at
    return jwt.encode(payload, settings.jwt_access_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_access_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None

    if payload.get("type") != "access" or not payload.get("sub"):
        return None
    return payload


def hash_oauth_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp_code(code: str) -> str:
    return hash_token(code)


def verify_otp_code(code: str, code_hash: str) -> bool:
    return hash_otp_code(code) == code_hash


def validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise ValueError("La contrasena debe tener al menos 10 caracteres.")
    if not any(character.islower() for character in password):
        raise ValueError("La contrasena debe incluir una minuscula.")
    if not any(character.isupper() for character in password):
        raise ValueError("La contrasena debe incluir una mayuscula.")
    if not any(character.isdigit() for character in password):
        raise ValueError("La contrasena debe incluir un numero.")
    if not any(not character.isalnum() for character in password):
        raise ValueError("La contrasena debe incluir un simbolo.")
