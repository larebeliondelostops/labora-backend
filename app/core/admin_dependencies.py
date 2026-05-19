from dataclasses import dataclass

from fastapi import Depends, Request, status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.auth_dependencies import get_optional_auth_payload
from app.core.database import get_db
from app.models.admin import AdminRolePermission, AdminUser
from app.models.user import User
from app.repositories.user_repository import UserRepository


ADMIN_PERMISSIONS = {
    "admin.cases.read",
    "admin.cases.assign",
    "admin.cases.update_status",
    "admin.cases.block",
    "admin.documents.read",
    "admin.documents.review",
    "admin.extraction.read",
    "admin.extraction.correct",
    "admin.analysis.read",
    "admin.analysis.review",
    "admin.calculations.read",
    "admin.calculations.review",
    "admin.reports.read",
    "admin.reports.approve",
    "admin.legal_drafts.read",
    "admin.legal_drafts.approve",
    "admin.notes.create",
    "admin.notes.read_internal",
    "admin.payments.read",
    "admin.payments.override_unlock",
    "admin.audit.read",
    "admin.config.read",
    "admin.config.write",
}

ADMIN_ROLES = {
    "super_admin",
    "admin_manager",
    "document_reviewer",
    "legal_reviewer",
    "calculation_reviewer",
    "support_agent",
    "read_only_admin",
}

LEGACY_ADMIN_ROLE_MAP = {
    "admin": "super_admin",
    "legal_admin": "super_admin",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": set(ADMIN_PERMISSIONS),
    "admin_manager": {
        "admin.cases.read",
        "admin.cases.assign",
        "admin.cases.update_status",
        "admin.cases.block",
        "admin.documents.read",
        "admin.extraction.read",
        "admin.analysis.read",
        "admin.calculations.read",
        "admin.reports.read",
        "admin.legal_drafts.read",
        "admin.notes.create",
        "admin.notes.read_internal",
        "admin.payments.read",
        "admin.audit.read",
        "admin.config.read",
    },
    "document_reviewer": {
        "admin.cases.read",
        "admin.documents.read",
        "admin.documents.review",
        "admin.extraction.read",
        "admin.notes.create",
        "admin.notes.read_internal",
    },
    "legal_reviewer": {
        "admin.cases.read",
        "admin.documents.read",
        "admin.extraction.read",
        "admin.analysis.read",
        "admin.analysis.review",
        "admin.calculations.read",
        "admin.reports.read",
        "admin.reports.approve",
        "admin.legal_drafts.read",
        "admin.legal_drafts.approve",
        "admin.notes.create",
        "admin.notes.read_internal",
    },
    "calculation_reviewer": {
        "admin.cases.read",
        "admin.extraction.read",
        "admin.calculations.read",
        "admin.calculations.review",
        "admin.notes.create",
        "admin.notes.read_internal",
    },
    "support_agent": {
        "admin.cases.read",
        "admin.documents.read",
        "admin.notes.create",
        "admin.notes.read_internal",
        "admin.payments.read",
    },
    "read_only_admin": {
        "admin.cases.read",
        "admin.documents.read",
        "admin.extraction.read",
        "admin.analysis.read",
        "admin.calculations.read",
        "admin.reports.read",
        "admin.legal_drafts.read",
        "admin.notes.read_internal",
        "admin.payments.read",
    },
}


@dataclass(frozen=True)
class AdminContext:
    user: User
    admin_user: AdminUser
    permissions: set[str]
    token_payload: dict


def require_admin_permission(permission: str):
    def dependency(
        request: Request,
        db: Session = Depends(get_db),
    ) -> AdminContext:
        context = get_current_admin_context(request=request, db=db)
        if not has_admin_permission(context, permission):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ADMIN_PERMISSION_DENIED",
                message="No tienes permiso para ejecutar esta accion.",
            )
        return context

    return dependency


def get_current_admin_context(
    *,
    request: Request,
    db: Session,
) -> AdminContext:
    payload = get_optional_auth_payload(request)
    if payload is None:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="ADMIN_AUTH_REQUIRED",
            message="Autenticacion administrativa requerida.",
        )

    user = UserRepository(db).get_by_id(payload["sub"])
    if (
        user is None
        or not user.is_active
        or user.status in {"blocked", "suspended", "deleted"}
    ):
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="ADMIN_AUTH_REQUIRED",
            message="Sesion administrativa invalida.",
        )

    admin_user = _ensure_admin_user(db, user)
    if admin_user.status != "active":
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="ADMIN_PERMISSION_DENIED",
            message="El usuario administrativo no esta activo.",
        )
    permissions = _permissions_for_role(db, admin_user.role)
    return AdminContext(
        user=user,
        admin_user=admin_user,
        permissions=permissions,
        token_payload=payload,
    )


def has_admin_permission(context: AdminContext, permission: str) -> bool:
    return context.admin_user.role == "super_admin" or permission in context.permissions


def _ensure_admin_user(db: Session, user: User) -> AdminUser:
    role = _admin_role_for_user(user)
    if role is None:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="ADMIN_PERMISSION_DENIED",
            message="No tienes un rol administrativo.",
        )

    admin_user = (
        db.query(AdminUser)
        .filter(AdminUser.user_id == user.id)
        .one_or_none()
    )
    if admin_user is None:
        admin_user = (
            db.query(AdminUser)
            .filter(AdminUser.email == user.email.lower())
            .one_or_none()
        )
    if admin_user is not None:
        if admin_user.user_id is None:
            admin_user.user_id = user.id
        return admin_user

    full_name = (
        user.full_name
        or " ".join(part for part in [user.first_name, user.last_name] if part)
        or user.email
    )
    admin_user = AdminUser(
        user_id=user.id,
        full_name=full_name[:180],
        email=user.email.lower(),
        role=role,
        status="active",
    )
    db.add(admin_user)
    db.flush()
    return admin_user


def _admin_role_for_user(user: User) -> str | None:
    if user.role in ADMIN_ROLES:
        return user.role
    return LEGACY_ADMIN_ROLE_MAP.get(user.role)


def _permissions_for_role(db: Session, role: str) -> set[str]:
    permissions = set(ROLE_PERMISSIONS.get(role, set()))
    stored = (
        db.query(AdminRolePermission.permission)
        .filter(AdminRolePermission.role == role)
        .all()
    )
    permissions.update(item[0] for item in stored)
    return permissions
