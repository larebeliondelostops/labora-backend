import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import LaboraCase
from app.models.document import Document, DocumentType, FileUpload
from app.models.user import User
from app.repositories.case_repository import CaseRepository
from app.repositories.document_repository import DocumentRepository
from app.schemas.document import DocumentCreateRequest, DocumentReplaceRequest, DocumentUpdateRequest
from app.services.case_state_machine import step_for_status, validate_case_transition
from app.services.consent_service import ConsentComplianceService
from app.services.document_audit_service import DocumentAuditService
from app.services.document_job_queue import DocumentJobQueue
from app.services.document_storage_service import DocumentStorageService, StorageProviderError
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
LOCKED_CASE_STATUSES = {"closed", "archived"}
ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
EXTENSION_MIME_TYPES = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}
DOCUMENT_STATUSES = {
    "draft",
    "uploading",
    "uploaded",
    "processing",
    "validated",
    "requires_review",
    "rejected",
    "replaced",
    "deleted",
    "failed",
}
MAX_CASE_STORAGE_BYTES = 250 * 1024 * 1024


DOCUMENT_TYPE_SEED = [
    {
        "code": "historia_laboral",
        "name": "Historia laboral",
        "description": "Documento principal de semanas, periodos o historia laboral.",
        "category": "principal",
        "is_required_for_basic_flow": True,
        "is_primary_candidate": True,
        "allowed_mime_types": ["application/pdf"],
        "max_size_mb": 50,
        "sort_order": 10,
    },
    {
        "code": "cedula",
        "name": "Cedula",
        "description": "Documento de identificacion.",
        "category": "identidad",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"],
        "max_size_mb": 10,
        "sort_order": 20,
    },
    {
        "code": "resolucion_pensional",
        "name": "Resolucion pensional",
        "description": "Acto o resolucion de reconocimiento pensional.",
        "category": "soporte",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf"],
        "max_size_mb": 25,
        "sort_order": 30,
    },
    {
        "code": "certificacion_laboral",
        "name": "Certificacion laboral",
        "description": "Certificaciones laborales o contractuales.",
        "category": "soporte",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"],
        "max_size_mb": 25,
        "sort_order": 40,
    },
    {
        "code": "desprendible_nomina",
        "name": "Desprendible de nomina",
        "description": "Comprobantes de pago o nomina.",
        "category": "soporte",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"],
        "max_size_mb": 10,
        "sort_order": 50,
    },
    {
        "code": "acto_administrativo",
        "name": "Acto administrativo",
        "description": "Actos administrativos relacionados con el caso.",
        "category": "soporte",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf"],
        "max_size_mb": 25,
        "sort_order": 60,
    },
    {
        "code": "certificado_docente",
        "name": "Certificado docente",
        "description": "Certificados del regimen docente.",
        "category": "soporte",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"],
        "max_size_mb": 25,
        "sort_order": 70,
    },
    {
        "code": "acta_posesion",
        "name": "Acta de posesion",
        "description": "Actas de posesion o vinculacion.",
        "category": "soporte",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"],
        "max_size_mb": 25,
        "sort_order": 80,
    },
    {
        "code": "respuesta_fondo",
        "name": "Respuesta de fondo",
        "description": "Respuestas de Colpensiones, AFP o entidad administradora.",
        "category": "soporte",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf"],
        "max_size_mb": 25,
        "sort_order": 90,
    },
    {
        "code": "sentencia_previa",
        "name": "Sentencia previa",
        "description": "Sentencias o decisiones judiciales previas.",
        "category": "juridico",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf"],
        "max_size_mb": 25,
        "sort_order": 100,
    },
    {
        "code": "tutela_previa",
        "name": "Tutela previa",
        "description": "Acciones de tutela o fallos asociados.",
        "category": "juridico",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf"],
        "max_size_mb": 25,
        "sort_order": 110,
    },
    {
        "code": "otro_soporte",
        "name": "Otro soporte",
        "description": "Documento de soporte no clasificado.",
        "category": "otro",
        "is_required_for_basic_flow": False,
        "is_primary_candidate": False,
        "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"],
        "max_size_mb": 25,
        "sort_order": 999,
    },
]


class DocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.cases = CaseRepository(db)
        self.storage = DocumentStorageService()
        self.audit = DocumentAuditService(db)

    def list_document_types(self) -> dict[str, Any]:
        seeded = self._ensure_document_types_seeded()
        if seeded:
            self.db.commit()
        return {
            "items": [
                {
                    "code": item.code,
                    "name": item.name,
                    "category": item.category,
                    "isRequiredForBasicFlow": item.is_required_for_basic_flow,
                    "isPrimaryCandidate": item.is_primary_candidate,
                    "allowedMimeTypes": item.allowed_mime_types,
                    "maxSizeMb": item.max_size_mb,
                }
                for item in self.documents.list_document_types()
            ]
        }

    def create_document_upload(
        self,
        case_id: str,
        payload: DocumentCreateRequest,
        *,
        file_content: bytes | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case, document_type, filename, extension = self._validate_create_request(
            case_id=case_id,
            payload=payload,
            file_content=file_content,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        upload_method = "multipart" if file_content is not None else "signed_url"
        now = utc_now()
        document_id = uuid.uuid4()
        storage_key = self.storage.build_storage_key(
            case_id=str(case.id),
            document_id=str(document_id),
            filename=filename,
        )
        document = self.documents.create_document(
            id=document_id,
            case_id=case.id,
            uploaded_by_user_id=user.id,
            document_type_id=document_type.id if document_type else None,
            original_filename=filename,
            display_name=None,
            mime_type=payload.mime_type,
            extension=extension,
            size_bytes=payload.size_bytes,
            storage_bucket=self.storage.bucket_name,
            storage_key=storage_key,
            status="uploaded" if file_content is not None else "uploading",
            validation_status="not_started",
            classification_source="manual" if payload.document_type_code else "unknown",
            is_primary=payload.is_primary,
            is_duplicate=False,
            created_at=now,
            updated_at=now,
        )
        upload = self.documents.create_upload(
            case_id=case.id,
            document_id=document.id,
            user_id=user.id,
            status="completed" if file_content is not None else "initiated",
            upload_method=upload_method,
            original_filename=filename,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
            storage_key=storage_key,
            expires_at=now + timedelta(
                seconds=settings.minio_presigned_upload_ttl_seconds,
            )
            if file_content is None
            else None,
            completed_at=now if file_content is not None else None,
        )
        if payload.is_primary:
            self.documents.unset_other_primary_documents(
                case_id=case.id,
                keep_document_id=document.id,
            )

        self.audit.record(
            "carga_documental.created",
            actor=user,
            document=document,
            new_state=self._document_state(document),
            metadata={"uploadMethod": upload_method},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        jobs: list[dict] = []
        if file_content is not None:
            try:
                self.storage.save(
                    storage_key=storage_key,
                    content=file_content,
                    content_type=document.mime_type,
                )
            except StorageProviderError as exc:
                raise ApiError(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    code="STORAGE_PROVIDER_ERROR",
                    message="No fue posible guardar el archivo en el almacenamiento.",
                    details={"documentId": str(document.id)},
                ) from exc
            jobs = DocumentJobQueue(self.db).enqueue_post_upload_jobs(
                document,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self._mark_case_documents_uploaded(case)
        self.db.commit()
        self.db.refresh(document)
        return {
            "document": self._document_summary(document),
            "upload": self._upload_payload(upload, document=document),
            "jobs": jobs,
        }

    def complete_upload(
        self,
        document_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_update_case(case, user, ip_address, user_agent)
        upload = self.documents.latest_upload_for_document(document.id)
        if upload is not None and upload.expires_at is not None and _as_utc(upload.expires_at) < utc_now():
            self.documents.mark_upload_failed(
                upload,
                error_code="DOCUMENT_UPLOAD_EXPIRED",
                error_message="La carga expiro. Inicia una nueva carga.",
            )
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_UPLOAD_EXPIRED",
                message="La carga expiro. Inicia una nueva carga.",
                details={"documentId": str(document.id)},
            )
        if upload is not None and upload.status != "completed":
            if upload.upload_method == "signed_url":
                self._ensure_uploaded_object_exists(document, upload)
            self.documents.complete_upload(upload, utc_now())
        elif upload is not None and upload.upload_method == "signed_url":
            self._ensure_uploaded_object_exists(document, upload)
        document.status = "uploaded"
        document.validation_status = "in_progress"
        document.updated_at = utc_now()
        self.audit.record(
            "carga_documental.submitted",
            actor=user,
            document=document,
            new_state=self._document_state(document),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        jobs = DocumentJobQueue(self.db).enqueue_post_upload_jobs(
            document,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._mark_case_documents_uploaded(case)
        self.db.commit()
        self.db.refresh(document)
        return {
            "documentId": str(document.id),
            "status": document.status,
            "validationStatus": document.validation_status,
            "jobs": jobs,
        }

    def list_case_documents(
        self,
        case_id: str,
        *,
        user: User,
        document_type_code: str | None,
        status_filter: str | None,
        include_deleted: bool,
        page: int,
        limit: int,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view_case(case, user, ip_address, user_agent)
        self._ensure_document_types_seeded()
        if document_type_code and self.documents.get_type_by_code(document_type_code) is None:
            raise self._document_type_invalid()
        if status_filter and status_filter not in DOCUMENT_STATUSES:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="DOCUMENT_VALIDATION_FAILED",
                message="Estado documental invalido.",
            )
        items, total = self.documents.list_case_documents(
            case_id=case.id,
            document_type_code=document_type_code,
            status=status_filter,
            include_deleted=include_deleted,
            page=page,
            limit=limit,
        )
        return {
            "items": [self._document_list_item(item) for item in items],
            "pagination": {"page": page, "limit": limit, "total": total},
        }

    def get_document(
        self,
        document_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_view_case(case, user, ip_address, user_agent)
        return self._document_detail(document)

    def get_view_url(
        self,
        document_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_view_case(case, user, ip_address, user_agent)
        if document.status in {"deleted", "failed"} or document.deleted_at is not None:
            raise self._document_not_found()
        self.audit.record(
            "carga_documental.viewed",
            actor=user,
            document=document,
            metadata={"access": "view_url"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        try:
            view_url = self.storage.signed_view_url(
                document_id=str(document.id),
                storage_key=document.storage_key,
            )
        except StorageProviderError as exc:
            raise ApiError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="PUBLIC_URL_CONFIGURATION_ERROR",
                message="No fue posible generar una URL publica para visualizar el documento.",
            ) from exc
        return {
            "url": view_url,
            "expiresInSeconds": 300,
        }

    def save_signed_upload(
        self,
        document_id: str,
        *,
        content: bytes,
        content_type: str | None,
        expires: int,
        token: str,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        if not self.storage.validate_token(
            document_id=str(document.id),
            expires=expires,
            token=token,
            action="upload",
        ):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="DOCUMENT_UPLOAD_EXPIRED",
                message="La URL temporal expiro o no es valida.",
            )
        upload = self.documents.latest_upload_for_document(document.id)
        if upload is None or upload.upload_method != "signed_url":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_UPLOAD_NOT_ALLOWED",
                message="Este documento no tiene una carga firmada activa.",
            )
        if upload.expires_at is not None and _as_utc(upload.expires_at) < utc_now():
            self.documents.mark_upload_failed(
                upload,
                error_code="DOCUMENT_UPLOAD_EXPIRED",
                error_message="La carga expiro. Inicia una nueva carga.",
            )
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_UPLOAD_EXPIRED",
                message="La carga expiro. Inicia una nueva carga.",
                details={"documentId": str(document.id)},
            )
        if upload.status == "completed":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_UPLOAD_ALREADY_COMPLETED",
                message="La carga de este documento ya fue completada.",
            )
        if len(content) != document.size_bytes:
            raise ApiError(
                status_code=422,
                code="DOCUMENT_VALIDATION_FAILED",
                message="El tamano del archivo no coincide con la carga solicitada.",
                details={
                    "expectedSizeBytes": document.size_bytes,
                    "receivedSizeBytes": len(content),
                },
            )
        if content_type:
            received_type = content_type.split(";", 1)[0].strip().lower()
            if received_type and received_type != document.mime_type.lower():
                raise ApiError(
                    status_code=422,
                    code="DOCUMENT_MIME_TYPE_NOT_ALLOWED",
                    message="El tipo MIME enviado no coincide con el documento.",
                )
        _assert_content_matches_mime(content, document.mime_type)
        try:
            self.storage.save(
                storage_key=document.storage_key,
                content=content,
                content_type=document.mime_type,
            )
        except StorageProviderError as exc:
            raise ApiError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="STORAGE_PROVIDER_ERROR",
                message="No fue posible guardar el archivo en el almacenamiento.",
                details={"documentId": str(document.id)},
            ) from exc
        return {"documentId": str(document.id), "status": "uploaded"}

    def update_document(
        self,
        document_id: str,
        payload: DocumentUpdateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_update_case(case, user, ip_address, user_agent)
        if document.status in {"deleted", "replaced"} or document.deleted_at is not None:
            raise self._document_not_found()
        previous_state = self._document_state(document)
        changed = False
        if payload.display_name is not None and payload.display_name != document.display_name:
            document.display_name = payload.display_name
            changed = True
        if payload.document_type_code is not None:
            document_type = self._get_document_type_or_error(payload.document_type_code)
            if payload.is_primary is True and document_type.code != "historia_laboral":
                raise self._document_type_invalid("El documento principal debe ser historia_laboral.")
            if document.document_type_id != document_type.id:
                document.document_type_id = document_type.id
                document.classification_source = "manual"
                changed = True
        if payload.is_primary is not None and payload.is_primary != document.is_primary:
            if payload.is_primary:
                document_type_code = document.document_type.code if document.document_type else None
                if document_type_code != "historia_laboral":
                    raise self._document_type_invalid("El documento principal debe ser historia_laboral.")
            document.is_primary = payload.is_primary
            changed = True
        if document.is_primary:
            self.documents.unset_other_primary_documents(
                case_id=document.case_id,
                keep_document_id=document.id,
            )
        if changed:
            document.updated_at = utc_now()
            self.audit.record(
                "carga_documental.updated",
                actor=user,
                document=document,
                previous_state=previous_state,
                new_state=self._document_state(document),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            self.db.refresh(document)
        return {
            "id": str(document.id),
            "displayName": document.display_name,
            "documentTypeCode": document.document_type.code if document.document_type else None,
            "classificationSource": document.classification_source,
            "isPrimary": document.is_primary,
            "updatedAt": document.updated_at,
        }

    def replace_document(
        self,
        document_id: str,
        payload: DocumentReplaceRequest,
        *,
        file_content: bytes | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        old_document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(old_document.case_id)
        self._require_can_update_case(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        if old_document.status in {"deleted", "replaced"} or old_document.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_REPLACE_NOT_ALLOWED",
                message="Este documento no puede reemplazarse.",
            )
        previous_state = self._document_state(old_document)
        document_type_code = payload.document_type_code
        if document_type_code is None and old_document.document_type is not None:
            document_type_code = old_document.document_type.code
        replacement_payload = DocumentCreateRequest(
            originalFilename=payload.original_filename,
            mimeType=payload.mime_type,
            sizeBytes=payload.size_bytes,
            documentTypeCode=document_type_code,
            isPrimary=old_document.is_primary or payload.is_primary,
        )
        create_result = self.create_document_upload(
            str(case.id),
            replacement_payload,
            file_content=file_content,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        new_document = self.documents.get(create_result["document"]["id"])
        old_document.status = "replaced"
        old_document.replaced_by_document_id = new_document.id if new_document else None
        old_document.updated_at = utc_now()
        self.audit.record(
            "carga_documental.replaced",
            actor=user,
            document=old_document,
            previous_state=previous_state,
            new_state=self._document_state(old_document),
            metadata={"newDocumentId": str(new_document.id) if new_document else None},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "oldDocumentId": str(old_document.id),
            "newDocumentId": create_result["document"]["id"],
            "oldStatus": "replaced",
            "newStatus": create_result["document"]["status"],
            "upload": create_result["upload"],
        }

    def delete_document(
        self,
        document_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_update_case(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        if document.status in {"deleted", "replaced"} or document.deleted_at is not None:
            raise self._document_not_found()
        if (
            document.is_primary
            and not self._is_admin(user)
            and self.documents.count_active_primary_documents(
                case_id=document.case_id,
                exclude_document_id=document.id,
            )
            == 0
        ):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_DELETE_NOT_ALLOWED",
                message="No puedes eliminar el unico documento principal del expediente.",
            )
        previous_state = self._document_state(document)
        document.status = "deleted"
        document.deleted_at = utc_now()
        document.updated_at = document.deleted_at
        self.audit.record(
            "carga_documental.deleted",
            actor=user,
            document=document,
            previous_state=previous_state,
            new_state=self._document_state(document),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "id": str(document.id),
            "status": document.status,
            "deletedAt": document.deleted_at,
        }

    def get_readiness(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view_case(case, user, ip_address, user_agent)
        documents = self.documents.list_active_case_documents(case.id)
        latest_validations = {
            document.id: self.documents.latest_validation(document.id)
            for document in documents
        }
        has_primary = any(
            document.is_primary
            and document.document_type is not None
            and document.document_type.code == "historia_laboral"
            and document.status == "validated"
            for document in documents
        )
        rejected = [document for document in documents if document.status == "rejected"]
        requires_review = [
            document
            for document in documents
            if document.status == "requires_review"
            or document.validation_status == "requires_review"
        ]
        warnings = [
            {
                "code": "SUPPORT_OPTIONAL_MISSING",
                "message": "Puedes agregar resolucion pensional si ya la tienes.",
            }
        ]
        blocking_issues: list[dict[str, str]] = []
        if not has_primary:
            blocking_issues.append(
                {
                    "code": "PRIMARY_LABOR_HISTORY_MISSING",
                    "message": "Carga una historia laboral principal validada para continuar.",
                }
            )
        if rejected:
            blocking_issues.append(
                {
                    "code": "DOCUMENTS_REJECTED",
                    "message": "Hay documentos rechazados que requieren reemplazo.",
                }
            )
        if blocking_issues:
            readiness_status = "needs_documents"
            next_action = "upload_documents"
        elif requires_review:
            readiness_status = "requires_review"
            next_action = "review_documents"
        else:
            readiness_status = "ready_for_preanalysis"
            next_action = "continue_to_preanalysis"

        return {
            "caseId": str(case.id),
            "readinessStatus": readiness_status,
            "hasPrimaryLaborHistory": has_primary,
            "documentsTotal": len(documents),
            "documentsValidated": sum(1 for item in documents if item.status == "validated"),
            "documentsWithWarnings": sum(
                1
                for validation in latest_validations.values()
                if validation is not None
                and validation.result in {"accepted_with_warnings", "requires_review"}
            ),
            "documentsRejected": len(rejected),
            "blockingIssues": blocking_issues,
            "warnings": [] if blocking_issues else warnings,
            "nextAction": next_action,
        }

    def validate_file_access(
        self,
        document_id: str,
        *,
        expires: int,
        token: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> Document:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_view_case(case, user, ip_address, user_agent)
        if document.status in {"deleted", "failed"} or document.deleted_at is not None:
            raise self._document_not_found()
        if not self.storage.validate_token(
            document_id=str(document.id),
            expires=expires,
            token=token,
            action="view",
        ):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="DOCUMENT_UPLOAD_EXPIRED",
                message="La URL temporal expiro o no es valida.",
            )
        return document

    def _validate_create_request(
        self,
        *,
        case_id: str,
        payload: DocumentCreateRequest,
        file_content: bytes | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[LaboraCase, DocumentType | None, str, str]:
        self._ensure_document_types_seeded()
        case = self._get_case_or_404(case_id)
        self._require_can_update_case(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        self._require_sensitive_consent(user)
        filename = _sanitize_filename(payload.original_filename)
        extension = _extension(filename)
        if payload.mime_type not in ALLOWED_MIME_TYPES:
            raise ApiError(
                status_code=422,
                code="DOCUMENT_MIME_TYPE_NOT_ALLOWED",
                message="Tipo MIME de documento no permitido.",
                details={"mimeType": payload.mime_type},
            )
        if extension not in EXTENSION_MIME_TYPES:
            raise ApiError(
                status_code=422,
                code="DOCUMENT_EXTENSION_NOT_ALLOWED",
                message="Extension de documento no permitida.",
                details={"extension": extension},
            )
        if EXTENSION_MIME_TYPES[extension] != payload.mime_type:
            raise ApiError(
                status_code=422,
                code="DOCUMENT_EXTENSION_NOT_ALLOWED",
                message="La extension no coincide con el tipo MIME informado.",
            )
        if file_content is not None:
            if len(file_content) != payload.size_bytes:
                payload.size_bytes = len(file_content)
            _assert_content_matches_mime(file_content, payload.mime_type)

        document_type = self._resolve_document_type(payload.document_type_code)
        if payload.is_primary:
            if document_type is None or document_type.code != "historia_laboral":
                raise self._document_type_invalid("El documento principal debe ser historia_laboral.")
        max_size_mb = document_type.max_size_mb if document_type else _default_max_size_mb(payload.mime_type)
        if payload.mime_type not in (document_type.allowed_mime_types if document_type else ALLOWED_MIME_TYPES):
            raise ApiError(
                status_code=422,
                code="DOCUMENT_MIME_TYPE_NOT_ALLOWED",
                message="El tipo documental no permite este MIME.",
            )
        if payload.size_bytes > max_size_mb * 1024 * 1024:
            raise ApiError(
                status_code=413,
                code="DOCUMENT_SIZE_EXCEEDED",
                message=f"El archivo supera el limite de {max_size_mb} MB.",
                details={"maxSizeMb": max_size_mb},
            )
        if self.documents.sum_active_case_size_bytes(case.id) + payload.size_bytes > MAX_CASE_STORAGE_BYTES:
            raise ApiError(
                status_code=413,
                code="DOCUMENT_SIZE_EXCEEDED",
                message="El expediente supera el limite total de carga documental.",
                details={"maxCaseSizeMb": 250},
            )
        return case, document_type, filename, extension

    def _resolve_document_type(self, code: str | None) -> DocumentType | None:
        if code is None:
            return self.documents.get_type_by_code("otro_soporte")
        return self._get_document_type_or_error(code)

    def _get_document_type_or_error(self, code: str) -> DocumentType:
        document_type = self.documents.get_type_by_code(code)
        if document_type is None:
            raise self._document_type_invalid()
        return document_type

    def _ensure_document_types_seeded(self) -> bool:
        if self.documents.list_document_types(active_only=False):
            return False
        try:
            for item in DOCUMENT_TYPE_SEED:
                self.documents.create_document_type(**item)
            self.db.flush()
            return True
        except IntegrityError:
            self.db.rollback()
            return False

    def _mark_case_documents_uploaded(self, case: LaboraCase) -> None:
        if case.status not in {"created", "ready_for_documents", "documents_pending"}:
            return
        previous_status = case.status
        validate_case_transition(
            self.db,
            case,
            new_status="documents_uploaded",
            validate_transition=True,
        )
        current_step, next_best_action = step_for_status("documents_uploaded")
        case.status = "documents_uploaded"
        case.current_step = current_step
        case.next_best_action = next_best_action
        case.updated_at = utc_now()
        self.cases.create_status_history(
            case_id=case.id,
            previous_status=previous_status,
            new_status=case.status,
            reason="Documentos cargados.",
            changed_by_user_id=None,
            changed_by_role="system",
            source_module="documents",
            metadata=None,
        )

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_document_or_404(self, document_id: str | uuid.UUID) -> Document:
        document = self.documents.get(document_id)
        if document is None:
            raise self._document_not_found()
        return document

    def _require_sensitive_consent(self, user: User) -> None:
        permission = ConsentComplianceService(self.db).can_upload_documents(user.id)
        if not permission.allowed:
            raise ApiError(
                status_code=422,
                code="CONSENT_REQUIRED",
                message="Debes aceptar los consentimientos requeridos antes de cargar documentos.",
                details={
                    "missingConsentTypes": permission.missing_consent_types,
                    "reason": permission.reason,
                },
            )

    def _require_can_view_case(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_view_case(case, user):
            return
        self.audit.record(
            "carga_documental.access_denied",
            actor=user,
            case_id=case.id,
            metadata={"action": "view"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CASE_ACCESS_DENIED",
            message="No tienes permisos para acceder a este expediente.",
        )

    def _require_can_update_case(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_update_case(case, user):
            return
        self.audit.record(
            "carga_documental.access_denied",
            actor=user,
            case_id=case.id,
            metadata={"action": "update"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CASE_ACCESS_DENIED",
            message="No tienes permisos para modificar este expediente.",
        )

    def _require_case_allows_writes(self, case: LaboraCase, user: User) -> None:
        if self._is_admin(user):
            return
        if case.status in LOCKED_CASE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="CASE_STATUS_BLOCKS_UPLOAD",
                message="El estado del expediente no permite cargar o modificar documentos.",
            )

    def _can_view_case(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return (
                case.status == "requires_review"
                or self.cases.get_owner(
                    case_id=case.id,
                    user_id=user.id,
                    roles={"legal_reviewer"},
                )
                is not None
            )
        if case.owner_user_id == user.id:
            return True
        return (
            self.cases.get_owner(
                case_id=case.id,
                user_id=user.id,
                roles={"authorized_user", "creator", "owner"},
            )
            is not None
        )

    def _can_update_case(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return False
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _is_admin(self, user: User) -> bool:
        return user.role in ADMIN_ROLES

    def _upload_payload(self, upload, *, document: Document) -> dict[str, Any]:
        payload = {
            "id": str(upload.id),
            "method": upload.upload_method,
            "status": upload.status,
            "expiresAt": upload.expires_at,
        }
        if upload.upload_method == "signed_url":
            try:
                payload["uploadUrl"] = self.storage.signed_upload_url(
                    document_id=str(document.id),
                    storage_key=document.storage_key,
                    expires_in_seconds=settings.minio_presigned_upload_ttl_seconds,
                )
            except StorageProviderError as exc:
                raise ApiError(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    code="STORAGE_PROVIDER_ERROR",
                    message="No fue posible generar la URL temporal de subida.",
                    details={"documentId": str(document.id)},
                ) from exc
            payload["headers"] = {"Content-Type": document.mime_type}
        return payload

    def _ensure_uploaded_object_exists(self, document: Document, upload: FileUpload) -> None:
        try:
            exists = self.storage.exists(document.storage_key)
        except StorageProviderError as exc:
            raise ApiError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="STORAGE_PROVIDER_ERROR",
                message="No fue posible verificar el archivo en el almacenamiento.",
                details={"documentId": str(document.id)},
            ) from exc
        if exists:
            return

        self.documents.mark_upload_failed(
            upload,
            error_code="STORAGE_PROVIDER_ERROR",
            error_message="El archivo no existe en el almacenamiento.",
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="STORAGE_PROVIDER_ERROR",
            message="El archivo no fue encontrado en el almacenamiento. Sube el archivo antes de completar la carga.",
            details={"documentId": str(document.id)},
        )

    def _document_summary(self, document: Document) -> dict[str, Any]:
        return {
            "id": str(document.id),
            "caseId": str(document.case_id),
            "originalFilename": document.original_filename,
            "displayName": document.display_name,
            "documentTypeCode": document.document_type.code if document.document_type else None,
            "status": document.status,
            "validationStatus": document.validation_status,
            "isPrimary": document.is_primary,
            "createdAt": document.created_at,
        }

    def _document_list_item(self, document: Document) -> dict[str, Any]:
        validation = self.documents.latest_validation(document.id)
        return {
            "id": str(document.id),
            "caseId": str(document.case_id),
            "displayName": document.display_name or _display_name_from_filename(document.original_filename),
            "originalFilename": document.original_filename,
            "documentType": {
                "code": document.document_type.code if document.document_type else None,
                "name": document.document_type.name if document.document_type else None,
            },
            "status": document.status,
            "validationStatus": document.validation_status,
            "validationResult": validation.result if validation else None,
            "pageCount": document.page_count,
            "sizeBytes": document.size_bytes,
            "isPrimary": document.is_primary,
            "isDuplicate": document.is_duplicate,
            "aiConfidence": float(document.ai_confidence) if document.ai_confidence is not None else None,
            "createdAt": document.created_at,
        }

    def _document_detail(self, document: Document) -> dict[str, Any]:
        validation = self.documents.latest_validation(document.id)
        return {
            "id": str(document.id),
            "caseId": str(document.case_id),
            "originalFilename": document.original_filename,
            "displayName": document.display_name,
            "mimeType": document.mime_type,
            "sizeBytes": document.size_bytes,
            "status": document.status,
            "validationStatus": document.validation_status,
            "classificationSource": document.classification_source,
            "documentTypeCode": document.document_type.code if document.document_type else None,
            "aiConfidence": float(document.ai_confidence) if document.ai_confidence is not None else None,
            "pageCount": document.page_count,
            "isPrimary": document.is_primary,
            "isDuplicate": document.is_duplicate,
            "validation": {
                "result": validation.result,
                "score": float(validation.score),
                "checks": validation.checks,
                "warnings": validation.warnings,
                "errors": validation.errors,
            }
            if validation
            else None,
            "createdAt": document.created_at,
            "updatedAt": document.updated_at,
        }

    def _document_state(self, document: Document) -> dict[str, Any]:
        return {
            "id": str(document.id),
            "caseId": str(document.case_id),
            "status": document.status,
            "validationStatus": document.validation_status,
            "documentTypeCode": document.document_type.code if document.document_type else None,
            "classificationSource": document.classification_source,
            "isPrimary": document.is_primary,
            "isDuplicate": document.is_duplicate,
            "sha256Hash": document.sha256_hash,
        }

    def _document_not_found(self) -> ApiError:
        return ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="DOCUMENT_NOT_FOUND",
            message="Documento no encontrado.",
        )

    def _document_type_invalid(self, message: str = "Tipo documental invalido.") -> ApiError:
        return ApiError(
            status_code=422,
            code="DOCUMENT_TYPE_INVALID",
            message=message,
        )


def _sanitize_filename(filename: str | None) -> str:
    if not filename:
        raise ApiError(
            status_code=422,
            code="DOCUMENT_VALIDATION_FAILED",
            message="El nombre del archivo es obligatorio.",
        )
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    basename = re.sub(r"[^A-Za-z0-9._ -]+", "_", basename)
    basename = re.sub(r"\s+", " ", basename).strip(" .")
    if not basename or "." not in basename:
        raise ApiError(
            status_code=422,
            code="DOCUMENT_EXTENSION_NOT_ALLOWED",
            message="El archivo debe tener una extension valida.",
        )
    return basename[:255]


def _extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix


def _default_max_size_mb(mime_type: str) -> int:
    if mime_type == "application/pdf":
        return 25
    return 10


def _assert_content_matches_mime(content: bytes, mime_type: str) -> None:
    if mime_type == "application/pdf" and not content.startswith(b"%PDF"):
        raise ApiError(
            status_code=422,
            code="DOCUMENT_CORRUPTED",
            message="El contenido del archivo no corresponde a un PDF valido.",
        )
    if mime_type == "image/jpeg" and not content.startswith(b"\xff\xd8"):
        raise ApiError(
            status_code=422,
            code="DOCUMENT_CORRUPTED",
            message="El contenido del archivo no corresponde a una imagen JPEG valida.",
        )
    if mime_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ApiError(
            status_code=422,
            code="DOCUMENT_CORRUPTED",
            message="El contenido del archivo no corresponde a una imagen PNG valida.",
        )


def _display_name_from_filename(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    return " ".join(stem.split()).title() or filename


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
