import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import CaseHistoryEvent, LaboraCase
from app.models.delivery import DeliveryPackage, DownloadFile, ShareLink
from app.models.legal_action import DraftExport, LegalAction, LegalActionJob, LegalDraft
from app.models.paywall import Paywall
from app.models.payment import Order, Payment
from app.models.professional_review import ProfessionalReview
from app.models.report import ExportFile, Report
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.delivery_repository import DeliveryRepository
from app.repositories.payment_repository import PaymentRepository
from app.schemas.delivery import (
    AiSummaryRequest,
    CloseCaseRequest,
    ComplementDeliveryRequest,
    CreateShareLinkRequest,
)
from app.services.document_storage_service import DocumentStorageService, StorageProviderError
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
OPERATOR_ROLES = {"operator", "legal_ops", "reviewer", "support_agent", "support"}
INTERNAL_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, *OPERATOR_ROLES, "system"}
SHARE_PERMISSIONS = {"view", "view_only", "download", "comment", "upload_supporting_files"}
PACKAGE_READY_STATUSES = {"ready", "partially_ready", "completed", "closed"}
PACKAGE_BLOCKED_STATUSES = {"blocked", "generating", "error"}
CASE_UNLOCKED_STATUSES = {
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "completed",
    "requires_review",
    "result_completed",
    "report_ready",
    "payment_approved",
    "closed",
}
ACTIVE_REVIEW_STATUSES = {
    "not_started",
    "requested",
    "approved",
    "assigned",
    "accepted",
    "in_review",
    "changes_requested",
    "blocked",
}
GENERATING_LEGAL_ACTION_STATUSES = {"in_progress"}
CRITICAL_LEGAL_ACTION_STATUSES = {"error"}
LOW_AI_CONFIDENCE = Decimal("0.7000")

DELIVERY_EVENTS = {
    "created": "entrega_final.created",
    "updated": "entrega_final.updated",
    "viewed": "entrega_final.viewed",
    "file_download_requested": "entrega_final.file_download_requested",
    "file_downloaded": "entrega_final.file_downloaded",
    "share_link_created": "entrega_final.share_link.created",
    "share_link_viewed": "entrega_final.share_link.viewed",
    "share_link_revoked": "entrega_final.share_link.revoked",
    "complement_requested": "entrega_final.complement_requested",
    "close_requested": "entrega_final.close_requested",
    "closed": "entrega_final.closed",
    "reopened": "entrega_final.reopened",
    "ai_summary_generated": "entrega_final.ai_summary.generated",
    "failed": "entrega_final.failed",
    "access_denied": "entrega_final.access_denied",
}


class DeliveryAuditService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.delivery = DeliveryRepository(db)
        self.audit_events = AuditEventRepository(db)

    def record(
        self,
        event_type: str,
        *,
        case: LaboraCase,
        package: DeliveryPackage | None,
        actor: User | None,
        actor_role: str | None = None,
        ip_address: str | None,
        user_agent: str | None,
        previous_status: str | None = None,
        new_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        role = actor_role or _actor_role(actor)
        safe_metadata = _json_safe(metadata or {})
        self.delivery.create_event(
            case_id=case.id,
            delivery_package_id=package.id if package else None,
            actor_user_id=actor.id if actor else None,
            actor_role=role,
            event_type=event_type,
            previous_status=previous_status,
            new_status=new_status,
            ip_hash=_hash_optional(ip_address),
            user_agent=_safe_user_agent(user_agent),
            metadata_json=safe_metadata,
            created_at=utc_now(),
        )
        audit_metadata = {
            "event": event_type,
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "deliveryPackageId": str(package.id) if package else None,
            "actorRole": role,
            "previousStatus": previous_status,
            "newStatus": new_status,
            "sourceModule": "delivery",
            **safe_metadata,
        }
        self.audit_events.create(
            event_type=event_type,
            entity_type="delivery_package",
            entity_id=package.id if package else case.id,
            actor_user_id=actor.id if actor else None,
            metadata=_json_safe(audit_metadata),
            ip_address=_hash_optional(ip_address),
            user_agent=_safe_user_agent(user_agent),
        )


class DeliveryPackageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.delivery = DeliveryRepository(db)
        self.payments = PaymentRepository(db)
        self.audit = DeliveryAuditService(db)

    def get_delivery_center(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        package = self._get_latest_package_or_404(case)
        files = self.delivery.list_files(package.id)
        now = utc_now()
        share_links = self.delivery.list_active_share_links(package.id, now=now)
        events = self.delivery.list_events(case_id=case.id, limit=10)
        self.audit.record(
            DELIVERY_EVENTS["viewed"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"surface": "delivery_center"},
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "package": _package_payload(package),
            "files": [_file_payload(item) for item in files],
            "shareLinks": [_share_link_payload(item) for item in share_links],
            "timeline": [_event_payload(item) for item in events],
            "availableActions": self._available_actions(case, package, user, files),
        }

    def complement(
        self,
        case_id: str,
        payload: ComplementDeliveryRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        package = self._get_latest_package_or_404(case)
        if package.status == "closed" or case.status == "closed":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_ALREADY_CLOSED",
                message="El caso ya esta cerrado y no permite complementar el expediente.",
                details={"caseId": str(case.id), "deliveryPackageId": str(package.id)},
            )
        previous_status = package.status
        package.status = "requires_review"
        package.updated_at = utc_now()
        self.audit.record(
            DELIVERY_EVENTS["complement_requested"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            previous_status=previous_status,
            new_status=package.status,
            metadata={
                "reason": payload.reason,
                "requestedDocumentTypes": payload.requested_document_types,
                "hasMessage": bool(payload.message),
            },
        )
        self._record_history_event(
            case=case,
            event_type=DELIVERY_EVENTS["complement_requested"],
            title="Complemento solicitado",
            description="Se registro una solicitud para complementar el expediente entregado.",
            severity="warning",
            actor=user,
            metadata={"deliveryPackageId": str(package.id), "reason": payload.reason},
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "status": package.status,
            "message": "Solicitud de complemento registrada.",
        }

    def list_events(
        self,
        case_id: str,
        *,
        limit: int,
        cursor: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        cursor_dt = _parse_cursor(cursor)
        items = self.delivery.list_events(case_id=case.id, limit=limit, cursor=cursor_dt)
        next_cursor = items[-1].created_at.isoformat() if len(items) == limit else None
        return {
            "items": [_event_payload(item) for item in items],
            "nextCursor": next_cursor,
        }

    def build_from_generated_files(
        self,
        case_id: str,
        *,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        if actor is not None:
            self._require_can_update(case, actor, ip_address, user_agent)
        self.delivery.mark_packages_not_latest(case.id)
        now = utc_now()
        package = self.delivery.create_package(
            case_id=case.id,
            owner_user_id=case.owner_user_id,
            status="ready",
            title="Entrega final del expediente",
            description="Documentos finales generados para el caso.",
            version=self.delivery.next_package_version(case.id),
            is_latest=True,
            unlocked_at=now if self._case_is_unlocked(case) else None,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        file_count = 0
        for values in self._generated_report_files(case):
            self.delivery.create_file(delivery_package_id=package.id, case_id=case.id, **values)
            file_count += 1
        for values in self._generated_legal_action_files(case):
            self.delivery.create_file(delivery_package_id=package.id, case_id=case.id, **values)
            file_count += 1
        if file_count == 0:
            package.status = "blocked"
            package.completed_at = None
        self.audit.record(
            DELIVERY_EVENTS["created"],
            case=case,
            package=package,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
            new_status=package.status,
            metadata={"fileCount": file_count, "version": package.version},
        )
        self.db.commit()
        return {"packageId": str(package.id), "status": package.status, "fileCount": file_count}

    def _generated_report_files(self, case: LaboraCase) -> list[dict[str, Any]]:
        rows = (
            self.db.query(ExportFile, Report)
            .join(Report, Report.id == ExportFile.report_id)
            .filter(
                ExportFile.case_id == case.id,
                ExportFile.status == "ready",
                ExportFile.storage_key.isnot(None),
                Report.status.in_(["ready", "approved"]),
            )
            .all()
        )
        values: list[dict[str, Any]] = []
        for export, report in rows:
            values.append(
                {
                    "source_document_id": None,
                    "generated_document_id": report.id,
                    "file_storage_key": export.storage_key,
                    "file_name": export.file_name,
                    "file_type": export.file_format,
                    "mime_type": export.mime_type,
                    "size_bytes": export.file_size_bytes or 0,
                    "checksum_sha256": export.checksum_sha256 or _stable_hash(str(export.id)),
                    "category": _report_category(report.report_type),
                    "status": "available",
                    "is_unlocked": True,
                    "requires_review": False,
                    "version": 1,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
        return values

    def _generated_legal_action_files(self, case: LaboraCase) -> list[dict[str, Any]]:
        rows = (
            self.db.query(DraftExport, LegalDraft, LegalAction)
            .join(LegalDraft, LegalDraft.id == DraftExport.draft_id)
            .join(LegalAction, LegalAction.id == LegalDraft.legal_action_id)
            .filter(
                DraftExport.case_id == case.id,
                DraftExport.status == "ready",
                DraftExport.storage_key.isnot(None),
            )
            .all()
        )
        values: list[dict[str, Any]] = []
        for export, draft, action in rows:
            values.append(
                {
                    "source_document_id": None,
                    "generated_document_id": draft.id,
                    "file_storage_key": export.storage_key,
                    "file_name": export.file_name,
                    "file_type": export.format,
                    "mime_type": export.mime_type,
                    "size_bytes": export.file_size_bytes or 0,
                    "checksum_sha256": export.checksum_sha256 or _stable_hash(str(export.id)),
                    "category": _legal_action_category(action.action_type),
                    "status": "available",
                    "is_unlocked": True,
                    "requires_review": False,
                    "version": export.version_number,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
        return values

    def _available_actions(
        self,
        case: LaboraCase,
        package: DeliveryPackage,
        user: User,
        files: list[DownloadFile],
    ) -> dict[str, bool]:
        can_update = self._can_update(case, user)
        is_closed = package.status == "closed" or case.status == "closed"
        can_download = any(_is_file_downloadable(item) for item in files) and self._case_is_unlocked(case)
        return {
            "canDownload": can_download,
            "canCreateShareLink": can_update and not is_closed,
            "canComplementCase": can_update and not is_closed,
            "canCloseCase": can_update and not is_closed,
        }

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DELIVERY_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_latest_package_or_404(self, case: LaboraCase) -> DeliveryPackage:
        package = self.delivery.latest_package_for_case(case.id)
        if package is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DELIVERY_NOT_FOUND",
                message="El paquete de entrega final no existe para este caso.",
                details={"caseId": str(case.id)},
            )
        return package

    def _require_can_view(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_view(case, user):
            return
        self._audit_access_denied(case, user, ip_address, user_agent)

    def _require_can_update(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_update(case, user):
            return
        self._audit_access_denied(case, user, ip_address, user_agent)

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if user.role in INTERNAL_ROLES:
            return True
        if case.owner_user_id == user.id:
            return True
        return self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        ) is not None

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if user.role in {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, "operator", "legal_ops", "reviewer"}:
            return True
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _case_is_unlocked(self, case: LaboraCase) -> bool:
        if case.status in CASE_UNLOCKED_STATUSES:
            return True
        order = self.payments.latest_order_for_case(case_id=case.id, product_code="FULL_ANALYSIS_UNLOCK")
        if order is not None and order.status == "paid":
            return True
        paywall = (
            self.db.query(Paywall)
            .filter(Paywall.case_id == case.id)
            .order_by(Paywall.created_at.desc())
            .first()
        )
        return paywall is not None and (
            paywall.status == "completed"
            or paywall.unlock_required is False
            or paywall.unlocked_at is not None
        )

    def _audit_access_denied(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        package = self.delivery.latest_package_for_case(case.id)
        self.audit.record(
            DELIVERY_EVENTS["access_denied"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"blockedReason": "permission_denied"},
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="DELIVERY_FORBIDDEN",
            message="No tienes permisos para acceder a este centro de entrega.",
        )

    def _record_history_event(
        self,
        *,
        case: LaboraCase,
        event_type: str,
        title: str,
        description: str | None,
        severity: str,
        actor: User | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.db.add(
            CaseHistoryEvent(
                case_id=case.id,
                event_type=event_type,
                title=title,
                description=description,
                visibility="both",
                severity=severity,
                created_by_user_id=actor.id if actor else None,
                metadata_json=_json_safe(metadata or {}),
            )
        )
        self.db.flush()


class DownloadFileService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.delivery = DeliveryRepository(db)
        self.package_service = DeliveryPackageService(db)
        self.storage = DocumentStorageService()
        self.audit = DeliveryAuditService(db)

    def download_url(
        self,
        file_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        file = self._get_file_or_404(file_id)
        package = self._get_package_or_404(file.delivery_package_id)
        case = self.package_service._get_case_or_404(file.case_id)
        self.package_service._require_can_view(case, user, ip_address, user_agent)
        self.audit.record(
            DELIVERY_EVENTS["file_download_requested"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"fileId": str(file.id), "fileName": file.file_name, "source": "authenticated"},
        )
        self._validate_downloadable(case=case, package=package, file=file)
        body = self._signed_url_for_file(file)
        self._mark_downloaded(
            case=case,
            package=package,
            file=file,
            actor=user,
            actor_role=None,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"source": "authenticated"},
        )
        self.db.commit()
        return body

    def download_url_from_share(
        self,
        *,
        share_link: ShareLink,
        file_id: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        file = self._get_file_or_404(file_id)
        package = self._get_package_or_404(file.delivery_package_id)
        case = self.package_service._get_case_or_404(file.case_id)
        if share_link.delivery_package_id != package.id or share_link.case_id != case.id:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="DELIVERY_FORBIDDEN",
                message="El archivo no pertenece al paquete compartido.",
            )
        if "download" not in (share_link.permissions or []):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="DELIVERY_FORBIDDEN",
                message="El enlace compartido no permite descargas.",
            )
        if not _file_allowed_by_share(file, share_link):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="DELIVERY_FORBIDDEN",
                message="El enlace compartido no permite acceder a este archivo.",
            )
        self.audit.record(
            DELIVERY_EVENTS["file_download_requested"],
            case=case,
            package=package,
            actor=None,
            actor_role="lawyer",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"fileId": str(file.id), "shareLinkId": str(share_link.id), "source": "share_link"},
        )
        self._validate_downloadable(case=case, package=package, file=file)
        body = self._signed_url_for_file(file)
        self._mark_downloaded(
            case=case,
            package=package,
            file=file,
            actor=None,
            actor_role="lawyer",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"source": "share_link", "shareLinkId": str(share_link.id)},
        )
        self.db.commit()
        return body

    def stream_file(self, file_id: str, *, expires: int, token: str) -> StreamingResponse:
        file = self._get_file_or_404(file_id)
        if expires < int(time.time()) or not hmac.compare_digest(
            _download_signature(file.id, expires),
            token,
        ):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="DELIVERY_UNAUTHORIZED",
                message="El enlace temporal de descarga no es valido o expiro.",
            )
        if file.status != "available" or not file.is_unlocked or file.requires_review:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DELIVERY_FILE_LOCKED",
                message="El archivo aun no esta disponible para descarga.",
                details={"fileId": str(file.id), "status": file.status},
            )
        try:
            stream = self.storage.stream(file.file_storage_key)
        except (FileNotFoundError, StorageProviderError) as exc:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DELIVERY_FILE_NOT_FOUND",
                message="No se encontro el archivo solicitado.",
            ) from exc
        quoted_name = quote(file.file_name)
        return StreamingResponse(
            stream,
            media_type=file.mime_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted_name}"},
        )

    def _signed_url_for_file(self, file: DownloadFile) -> dict[str, Any]:
        ttl = settings.delivery_signed_url_ttl_seconds
        expires = int((utc_now() + timedelta(seconds=ttl)).timestamp())
        token = _download_signature(file.id, expires)
        return {
            "downloadUrl": (
                f"{settings.backend_public_url}{settings.api_v1_prefix}/files/{file.id}/file"
                f"?expires={expires}&token={token}"
            ),
            "expiresInSeconds": ttl,
        }

    def _mark_downloaded(
        self,
        *,
        case: LaboraCase,
        package: DeliveryPackage,
        file: DownloadFile,
        actor: User | None,
        actor_role: str | None,
        ip_address: str | None,
        user_agent: str | None,
        metadata: dict[str, Any],
    ) -> None:
        file.download_count += 1
        file.last_downloaded_at = utc_now()
        file.updated_at = utc_now()
        self.audit.record(
            DELIVERY_EVENTS["file_downloaded"],
            case=case,
            package=package,
            actor=actor,
            actor_role=actor_role,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={
                "fileId": str(file.id),
                "fileName": file.file_name,
                "category": file.category,
                **metadata,
            },
        )

    def _validate_downloadable(
        self,
        *,
        case: LaboraCase,
        package: DeliveryPackage,
        file: DownloadFile,
    ) -> None:
        if not self.package_service._case_is_unlocked(case):
            raise ApiError(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                code="DELIVERY_PAYMENT_REQUIRED",
                message="La descarga requiere pago aprobado o desbloqueo equivalente.",
                details={"caseId": str(case.id)},
            )
        if package.status in PACKAGE_BLOCKED_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DELIVERY_BLOCKED",
                message="El paquete de entrega esta bloqueado temporalmente.",
                details={"deliveryPackageId": str(package.id), "status": package.status},
            )
        if file.requires_review or file.status == "requires_review":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DELIVERY_FILE_REQUIRES_REVIEW",
                message="El archivo requiere revision antes de descargarse.",
                details={"fileId": str(file.id), "status": file.status},
            )
        if not file.is_unlocked or file.status != "available":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DELIVERY_FILE_LOCKED",
                message="El archivo aun no esta disponible para descarga.",
                details={"fileId": str(file.id), "status": file.status},
            )

    def _get_file_or_404(self, file_id: str | uuid.UUID) -> DownloadFile:
        file = self.delivery.get_file(file_id)
        if file is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DELIVERY_FILE_NOT_FOUND",
                message="Archivo de entrega no encontrado.",
            )
        return file

    def _get_package_or_404(self, package_id: str | uuid.UUID) -> DeliveryPackage:
        package = self.delivery.get_package(package_id)
        if package is None or package.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DELIVERY_NOT_FOUND",
                message="Paquete de entrega no encontrado.",
            )
        return package


class ShareLinkService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.delivery = DeliveryRepository(db)
        self.package_service = DeliveryPackageService(db)
        self.downloads = DownloadFileService(db)
        self.audit = DeliveryAuditService(db)

    def create(
        self,
        case_id: str,
        payload: CreateShareLinkRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self.package_service._get_case_or_404(case_id)
        self.package_service._require_can_update(case, user, ip_address, user_agent)
        package = self.package_service._get_latest_package_or_404(case)
        self._validate_package_shareable(package)
        expires_at = _ensure_aware(payload.expires_at)
        now = utc_now()
        if expires_at <= now:
            raise ApiError(
                status_code=422,
                code="SHARE_LINK_INVALID",
                message="La expiracion del enlace debe ser futura.",
            )
        max_expiration = now + timedelta(days=settings.delivery_share_max_days)
        if expires_at > max_expiration:
            raise ApiError(
                status_code=422,
                code="SHARE_LINK_INVALID",
                message="La expiracion del enlace supera el maximo permitido.",
                details={"maxDays": settings.delivery_share_max_days},
            )
        permissions = _normalize_permissions(payload.permissions)
        allowed_file_ids = self._validate_allowed_files(package, payload.allowed_file_ids)
        token = secrets.token_urlsafe(32)
        token_hash = _share_token_hash(token)
        share_link = self.delivery.create_share_link(
            delivery_package_id=package.id,
            case_id=case.id,
            created_by=user.id,
            recipient_name=payload.recipient_name,
            recipient_email=payload.recipient_email,
            token_hash=token_hash,
            status="active",
            permissions=permissions,
            allowed_file_ids=allowed_file_ids,
            expires_at=expires_at,
            max_views=payload.max_views or settings.delivery_max_share_views_default,
            view_count=0,
            created_at=now,
            updated_at=now,
        )
        self.audit.record(
            DELIVERY_EVENTS["share_link_created"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={
                "shareLinkId": str(share_link.id),
                "permissions": permissions,
                "allowedFileIds": allowed_file_ids,
                "expiresAt": expires_at.isoformat(),
                "recipientEmail": payload.recipient_email,
            },
        )
        self.db.commit()
        return {
            "id": str(share_link.id),
            "shareUrl": f"{settings.delivery_share_base_url}/{token}",
            "status": share_link.status,
            "expiresAt": share_link.expires_at,
            "permissions": share_link.permissions,
        }

    def get_shared_delivery(
        self,
        token: str,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        share_link, package, case = self._validated_link(
            token,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        files = [
            item
            for item in self.delivery.list_available_files(package.id)
            if _file_allowed_by_share(item, share_link)
        ]
        can_download = "download" in (share_link.permissions or [])
        self.db.commit()
        return {
            "package": {
                "casePublicCode": case.case_number,
                "status": package.status,
                "title": package.title,
                "ownerDisplayName": "Cliente Labora",
            },
            "files": [
                {
                    "id": str(item.id),
                    "fileName": item.file_name,
                    "category": item.category,
                    "mimeType": item.mime_type,
                    "sizeBytes": item.size_bytes,
                    "canDownload": can_download,
                }
                for item in files
            ],
            "permissions": share_link.permissions or [],
            "expiresAt": share_link.expires_at,
        }

    def download_from_share(
        self,
        token: str,
        file_id: str,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        share_link, _package, _case = self._validated_link(
            token,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return self.downloads.download_url_from_share(
            share_link=share_link,
            file_id=file_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def revoke(
        self,
        case_id: str,
        share_link_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self.package_service._get_case_or_404(case_id)
        self.package_service._require_can_update(case, user, ip_address, user_agent)
        package = self.package_service._get_latest_package_or_404(case)
        share_link = self.delivery.get_share_link(share_link_id)
        if share_link is None or share_link.case_id != case.id or share_link.delivery_package_id != package.id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="SHARE_LINK_INVALID",
                message="Enlace compartido no encontrado.",
            )
        if user.role not in INTERNAL_ROLES and share_link.created_by != user.id:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="DELIVERY_FORBIDDEN",
                message="No puedes revocar este enlace compartido.",
            )
        now = utc_now()
        previous_status = share_link.status
        share_link.status = "revoked"
        share_link.revoked_at = now
        share_link.revoked_by = user.id
        share_link.updated_at = now
        self.audit.record(
            DELIVERY_EVENTS["share_link_revoked"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            previous_status=previous_status,
            new_status=share_link.status,
            metadata={"shareLinkId": str(share_link.id)},
        )
        self.db.commit()
        return {
            "id": str(share_link.id),
            "status": share_link.status,
            "revokedAt": share_link.revoked_at,
        }

    def expire_links(self) -> dict[str, int]:
        count = self.delivery.expire_active_share_links(now=utc_now())
        self.db.commit()
        return {"expired": count}

    def _validated_link(
        self,
        token: str,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[ShareLink, DeliveryPackage, LaboraCase]:
        share_link = self.delivery.get_share_link_by_hash(_share_token_hash(token))
        if share_link is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="SHARE_LINK_INVALID",
                message="El enlace compartido no existe o no es valido.",
            )
        package = self.delivery.get_package(share_link.delivery_package_id)
        if package is None or package.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DELIVERY_NOT_FOUND",
                message="El paquete compartido ya no esta disponible.",
            )
        case = self.package_service._get_case_or_404(share_link.case_id)
        now = utc_now()
        if share_link.status == "revoked":
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="SHARE_LINK_REVOKED",
                message="El enlace compartido fue revocado.",
            )
        if share_link.status in {"disabled", "expired"} or _ensure_aware(share_link.expires_at) <= now:
            if share_link.status == "active":
                share_link.status = "expired"
                share_link.updated_at = now
                self.db.commit()
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="SHARE_LINK_EXPIRED",
                message="El enlace compartido expiro.",
            )
        if share_link.status == "max_views_reached" or (
            share_link.max_views is not None and share_link.view_count >= share_link.max_views
        ):
            if share_link.status == "active":
                share_link.status = "max_views_reached"
                share_link.updated_at = now
                self.db.commit()
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="SHARE_LINK_MAX_VIEWS_REACHED",
                message="El enlace compartido alcanzo el limite de vistas.",
            )
        if package.status not in PACKAGE_READY_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DELIVERY_NOT_READY",
                message="El paquete compartido no esta listo.",
                details={"deliveryPackageId": str(package.id), "status": package.status},
            )
        share_link.view_count += 1
        share_link.last_viewed_at = now
        share_link.last_ip_hash = _hash_optional(ip_address)
        share_link.updated_at = now
        self.audit.record(
            DELIVERY_EVENTS["share_link_viewed"],
            case=case,
            package=package,
            actor=None,
            actor_role="lawyer",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"shareLinkId": str(share_link.id), "viewCount": share_link.view_count},
        )
        return share_link, package, case

    def _validate_package_shareable(self, package: DeliveryPackage) -> None:
        if package.status == "closed":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_ALREADY_CLOSED",
                message="No se pueden crear enlaces para un caso cerrado.",
            )
        if package.status not in {"ready", "partially_ready", "completed"}:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DELIVERY_NOT_READY",
                message="El paquete de entrega aun no esta listo para compartir.",
                details={"deliveryPackageId": str(package.id), "status": package.status},
            )

    def _validate_allowed_files(
        self,
        package: DeliveryPackage,
        allowed_file_ids: list[str] | None,
    ) -> list[str] | None:
        if allowed_file_ids is None:
            return None
        available = {str(item.id): item for item in self.delivery.list_available_files(package.id)}
        invalid = [file_id for file_id in allowed_file_ids if file_id not in available]
        if invalid:
            raise ApiError(
                status_code=422,
                code="SHARE_LINK_INVALID",
                message="Uno o mas archivos no pertenecen al paquete o no estan desbloqueados.",
                details={"invalidFileIds": invalid},
            )
        return allowed_file_ids


class CaseClosureService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.delivery = DeliveryRepository(db)
        self.package_service = DeliveryPackageService(db)
        self.audit = DeliveryAuditService(db)

    def close_case(
        self,
        case_id: str,
        payload: CloseCaseRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self.package_service._get_case_or_404(case_id)
        self.package_service._require_can_update(case, user, ip_address, user_agent)
        package = self.package_service._get_latest_package_or_404(case)
        if case.status == "closed" or package.status == "closed":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_ALREADY_CLOSED",
                message="El caso ya se encuentra cerrado.",
            )
        blockers = self._closure_blockers(case, package)
        if blockers:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_CANNOT_BE_CLOSED",
                message="El caso tiene procesos criticos pendientes y no puede cerrarse.",
                details={"blockers": blockers},
            )
        now = utc_now()
        previous_case_status = case.status
        previous_package_status = package.status
        self.delivery.create_closure(
            case_id=case.id,
            delivery_package_id=package.id,
            requested_by=user.id,
            closed_by=user.id,
            status="closed",
            reason=payload.reason,
            notes=payload.notes,
            metadata_json={"previousCaseStatus": previous_case_status},
            requested_at=now,
            closed_at=now,
            created_at=now,
            updated_at=now,
        )
        package.status = "closed"
        package.closed_at = now
        package.closed_by = user.id
        package.closure_reason = payload.reason
        package.updated_at = now
        case.status = "closed"
        case.closed_at = now
        case.status_reason = payload.reason
        case.current_step = "closed"
        case.next_best_action = "none"
        case.updated_at = now
        CaseRepository(self.db).create_status_history(
            case_id=case.id,
            previous_status=previous_case_status,
            new_status=case.status,
            reason=payload.reason,
            changed_by_user_id=user.id,
            changed_by_role=_actor_role(user),
            source_module="delivery",
            metadata={"deliveryPackageId": str(package.id)},
        )
        self.audit.record(
            DELIVERY_EVENTS["close_requested"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            previous_status=previous_package_status,
            new_status=package.status,
            metadata={"reason": payload.reason},
        )
        self.audit.record(
            DELIVERY_EVENTS["closed"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            previous_status=previous_case_status,
            new_status=case.status,
            metadata={"reason": payload.reason},
        )
        self.db.add(
            CaseHistoryEvent(
                case_id=case.id,
                event_type=DELIVERY_EVENTS["closed"],
                title="Caso cerrado",
                description="El caso fue cerrado sin eliminar trazabilidad ni documentos.",
                visibility="both",
                severity="success",
                created_by_user_id=user.id,
                metadata_json={"deliveryPackageId": str(package.id), "reason": payload.reason},
            )
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "closureStatus": "closed",
            "closedAt": now,
        }

    def _closure_blockers(self, case: LaboraCase, package: DeliveryPackage) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        if package.status in {"generating", "requires_review", "blocked", "error"}:
            blockers.append({"type": "delivery_package", "status": package.status})
        pending_files = [
            item
            for item in self.delivery.list_files(package.id)
            if item.status in {"pending", "requires_review", "error"} or item.requires_review
        ]
        if pending_files:
            blockers.append(
                {
                    "type": "download_file",
                    "fileIds": [str(item.id) for item in pending_files],
                }
            )
        payment_issue = (
            self.db.query(Order)
            .filter(Order.case_id == case.id, Order.status.in_(["requires_review", "disputed"]))
            .first()
        )
        if payment_issue is not None:
            blockers.append({"type": "payment", "orderId": str(payment_issue.id), "status": payment_issue.status})
        payment_review = (
            self.db.query(Payment)
            .filter(Payment.case_id == case.id, Payment.status.in_(["requires_review", "disputed"]))
            .first()
        )
        if payment_review is not None:
            blockers.append({"type": "payment", "paymentId": str(payment_review.id), "status": payment_review.status})
        review = (
            self.db.query(ProfessionalReview)
            .filter(
                ProfessionalReview.case_id == case.id,
                ProfessionalReview.deleted_at.is_(None),
                ProfessionalReview.status.in_(ACTIVE_REVIEW_STATUSES),
            )
            .first()
        )
        if review is not None:
            blockers.append({"type": "professional_review", "reviewId": str(review.id), "status": review.status})
        action = (
            self.db.query(LegalAction)
            .filter(
                LegalAction.case_id == case.id,
                LegalAction.status.in_(GENERATING_LEGAL_ACTION_STATUSES | CRITICAL_LEGAL_ACTION_STATUSES),
            )
            .first()
        )
        if action is not None:
            blockers.append({"type": "legal_action", "legalActionId": str(action.id), "status": action.status})
        action_job = (
            self.db.query(LegalActionJob)
            .filter(
                LegalActionJob.case_id == case.id,
                LegalActionJob.status.in_(["queued", "processing"]),
            )
            .first()
        )
        if action_job is not None:
            blockers.append({"type": "legal_action_job", "jobId": str(action_job.id), "status": action_job.status})
        return blockers


class DeliveryAiSummaryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.delivery = DeliveryRepository(db)
        self.package_service = DeliveryPackageService(db)
        self.audit = DeliveryAuditService(db)

    def regenerate(
        self,
        case_id: str,
        payload: AiSummaryRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_admin_or_system(user)
        if not settings.delivery_ai_summary_enabled:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="TEMPORARY_PROCESSING_FAILURE",
                message="El resumen IA de entrega esta deshabilitado.",
            )
        case = self.package_service._get_case_or_404(case_id)
        package = self.package_service._get_latest_package_or_404(case)
        if package.ai_summary and not payload.force:
            return _ai_summary_payload(package)
        files = self.delivery.list_available_files(package.id)
        source_ids = [str(item.id) for item in files]
        if not files:
            package.ai_summary = (
                "Resumen no vinculante: aun no hay archivos disponibles para construir el resumen final."
            )
            package.ai_next_steps = [
                {
                    "label": "Esperar disponibilidad de documentos",
                    "type": "review",
                    "disclaimer": "Sugerencia no vinculante.",
                    "sourceFileIds": [],
                }
            ]
            package.ai_confidence = Decimal("0.4500")
            package.status = "requires_review"
            package.updated_at = utc_now()
            self.audit.record(
                DELIVERY_EVENTS["ai_summary_generated"],
                case=case,
                package=package,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
                new_status=package.status,
                metadata={"confidence": float(package.ai_confidence), "sourceFileIds": source_ids},
            )
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="AI_SUMMARY_LOW_CONFIDENCE",
                message="El resumen IA quedo con baja confianza y requiere revision.",
                details={"deliveryPackageId": str(package.id), "confidence": float(package.ai_confidence)},
            )
        names = ", ".join(item.file_name for item in files[:5])
        extra = "" if len(files) <= 5 else f" y {len(files) - 5} archivo(s) adicional(es)"
        package.ai_summary = (
            "Resumen no vinculante: el paquete contiene documentos finales generados y desbloqueados, "
            f"incluyendo {names}{extra}. La salida deriva de archivos ya existentes y no reemplaza "
            "la revision juridica profesional."
        )
        package.ai_next_steps = [
            {
                "label": "Descargar copia del paquete",
                "type": "recommended",
                "disclaimer": "Sugerencia no vinculante.",
                "sourceFileIds": source_ids,
            },
            {
                "label": "Compartir con abogado",
                "type": "recommended",
                "disclaimer": "Sugerencia no vinculante.",
                "sourceFileIds": source_ids,
            },
        ]
        package.ai_confidence = Decimal("0.8800")
        if package.status == "requires_review" and package.ai_confidence >= LOW_AI_CONFIDENCE:
            package.status = "ready"
        package.updated_at = utc_now()
        self.audit.record(
            DELIVERY_EVENTS["ai_summary_generated"],
            case=case,
            package=package,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            new_status=package.status,
            metadata={"confidence": float(package.ai_confidence), "sourceFileIds": source_ids},
        )
        self.db.commit()
        return _ai_summary_payload(package)

    def _require_admin_or_system(self, user: User) -> None:
        if user.role in {*ADMIN_ROLES, "system"}:
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="DELIVERY_FORBIDDEN",
            message="Solo admin o system puede regenerar el resumen final IA.",
        )


def _package_payload(package: DeliveryPackage) -> dict[str, Any]:
    return {
        "id": str(package.id),
        "status": package.status,
        "version": package.version,
        "title": package.title,
        "description": package.description,
        "completedAt": package.completed_at,
        "closedAt": package.closed_at,
        "aiSummary": package.ai_summary,
        "aiConfidence": float(package.ai_confidence) if package.ai_confidence is not None else None,
        "aiNextSteps": package.ai_next_steps or [],
    }


def _file_payload(file: DownloadFile) -> dict[str, Any]:
    return {
        "id": str(file.id),
        "fileName": file.file_name,
        "category": file.category,
        "mimeType": file.mime_type,
        "sizeBytes": file.size_bytes,
        "status": file.status,
        "isUnlocked": file.is_unlocked,
        "requiresReview": file.requires_review,
        "downloadCount": file.download_count,
        "lastDownloadedAt": file.last_downloaded_at,
    }


def _share_link_payload(share_link: ShareLink) -> dict[str, Any]:
    return {
        "id": str(share_link.id),
        "recipientEmail": share_link.recipient_email,
        "status": share_link.status,
        "permissions": share_link.permissions or [],
        "expiresAt": share_link.expires_at,
        "viewCount": share_link.view_count,
        "maxViews": share_link.max_views,
    }


def _event_payload(event) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "eventType": event.event_type,
        "actorRole": event.actor_role,
        "previousStatus": event.previous_status,
        "newStatus": event.new_status,
        "metadata": event.metadata_json or {},
        "createdAt": event.created_at,
    }


def _ai_summary_payload(package: DeliveryPackage) -> dict[str, Any]:
    return {
        "packageId": str(package.id),
        "status": package.status,
        "aiSummary": package.ai_summary or "",
        "aiNextSteps": package.ai_next_steps or [],
        "aiConfidence": float(package.ai_confidence or Decimal("0")),
    }


def _normalize_permissions(permissions: list[str]) -> list[str]:
    normalized = []
    for item in permissions:
        permission = str(item).strip()
        if permission not in SHARE_PERMISSIONS:
            raise ApiError(
                status_code=422,
                code="SHARE_LINK_INVALID",
                message="El enlace incluye permisos no permitidos.",
                details={"permission": permission},
            )
        if permission not in normalized:
            normalized.append(permission)
    if "view_only" in normalized and "view" not in normalized:
        normalized.insert(0, "view")
    if "view" not in normalized and "view_only" not in normalized:
        normalized.insert(0, "view")
    return normalized


def _is_file_downloadable(file: DownloadFile) -> bool:
    return file.status == "available" and file.is_unlocked and not file.requires_review


def _file_allowed_by_share(file: DownloadFile, share_link: ShareLink) -> bool:
    allowed = share_link.allowed_file_ids
    if allowed is None:
        return True
    return str(file.id) in {str(item) for item in allowed}


def _download_signature(file_id: uuid.UUID, expires: int) -> str:
    payload = f"delivery-download:{file_id}:{expires}".encode("utf-8")
    return hmac.new(settings.jwt_access_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _share_token_hash(token: str) -> str:
    payload = f"delivery-share:{token}".encode("utf-8")
    return hmac.new(settings.jwt_access_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _hash_optional(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _safe_user_agent(value: str | None) -> str | None:
    if not value:
        return None
    return str(value)[:256]


def _actor_role(user: User | None) -> str:
    if user is None:
        return "system"
    if user.role in ADMIN_ROLES:
        return "admin"
    if user.role == "lawyer":
        return "lawyer"
    if user.role in LEGAL_REVIEWER_ROLES or user.role == "reviewer":
        return "reviewer"
    if user.role == "system":
        return "system"
    return "user"


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_cursor(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        raise ApiError(
            status_code=422,
            code="DELIVERY_INVALID_CURSOR",
            message="Cursor de timeline invalido.",
        )


def _report_category(report_type: str) -> str:
    return {
        "executive": "executive_report",
        "technical": "technical_report",
        "calculation": "calculation_sheet",
        "inconsistency_matrix": "inconsistency_matrix",
        "full": "technical_report",
    }.get(report_type, "other")


def _legal_action_category(action_type: str) -> str:
    return {
        "petition": "petition",
        "administrative_claim": "legal_claim",
        "reliquidation_request": "legal_claim",
        "administrative_appeal": "legal_claim",
        "lawsuit_draft": "lawsuit_draft",
    }.get(action_type, "other")


def _stable_hash(value: Any) -> str:
    raw = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value
