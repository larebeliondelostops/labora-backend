import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.consent import LegalDocument
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.consent_repository import ConsentRepository
from app.schemas.consent import (
    ConsentStatusResponse,
    ConsentSubmitRequest,
    DocumentUploadPermission,
    LegalDocumentCreateRequest,
)
from app.utils.dates import utc_now


REQUIRED_CONSENT_TYPES = [
    "terms_and_conditions",
    "personal_data_processing",
    "sensitive_data_processing",
    "electronic_means",
    "ai_scope_acknowledgement",
]

CONSENT_AUDIT_PREFIX = "consentimientos_cumplimiento"
ADMIN_ROLES = {"admin", "legal_admin"}
SUPPORT_READ_ROLES = {"admin", "legal_admin", "support"}
HTTP_422_UNPROCESSABLE = 422


class ConsentComplianceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.consents = ConsentRepository(db)
        self.audit_events = AuditEventRepository(db)

    def list_current_documents(
        self,
        *,
        types: list[str] | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> list[dict[str, Any]]:
        self._validate_consent_types(types or [])
        documents, conflicts = self._select_current_documents(types=types)
        for consent_type in conflicts:
            self._audit(
                "legal_document.current_conflict",
                user=user,
                entity_type="legal_document",
                metadata={"consentType": consent_type},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self._audit(
            f"{CONSENT_AUDIT_PREFIX}.viewed",
            user=user,
            entity_type="legal_document",
            metadata={"types": types or REQUIRED_CONSENT_TYPES},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return [self._legal_document_current(document) for document in documents]

    def get_status(self, user_id: uuid.UUID | str) -> ConsentStatusResponse:
        return ConsentStatusResponse.model_validate(
            self._build_status(_parse_uuid(user_id)),
        )

    def can_upload_documents(self, user_id: uuid.UUID | str) -> DocumentUploadPermission:
        status_payload = self._build_status(_parse_uuid(user_id))
        if status_payload["canUploadDocuments"]:
            return DocumentUploadPermission(allowed=True)
        return DocumentUploadPermission(
            allowed=False,
            reason=(
                "missing_required_consents"
                if status_payload["status"] != "blocked"
                else "consent_status_blocked"
            ),
            missing_consent_types=status_payload["missingConsentTypes"],
        )

    def list_user_history(self, user_id: uuid.UUID | str) -> list[dict[str, Any]]:
        parsed_user_id = _parse_uuid(user_id)
        return [
            {
                "id": str(consent.id),
                "consentType": consent.consent_type,
                "title": consent.legal_document.title,
                "documentVersion": consent.document_version,
                "acceptedAt": consent.accepted_at,
                "documentHashSha256": consent.document_hash_sha256,
                "evidenceHashSha256": consent.evidence_hash_sha256,
            }
            for consent in self.consents.list_user_consents(parsed_user_id)
        ]

    def register_consents(
        self,
        payload: ConsentSubmitRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
        idempotency_key: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        request_hash = _stable_sha256(_idempotency_payload(payload))
        if idempotency_key:
            existing_key = self.consents.get_idempotency_key(
                user_id=user.id,
                idempotency_key=idempotency_key,
            )
            if existing_key is not None:
                if existing_key.request_hash_sha256 != request_hash:
                    raise ApiError(
                        status_code=HTTP_422_UNPROCESSABLE,
                        code="invalid_payload",
                        message="La llave de idempotencia ya fue usada con otro payload.",
                    )
                return existing_key.response_json

        self._assert_required_documents_configured()
        self._assert_payload_items(payload)

        now = utc_now()
        documents_by_id = self._load_and_validate_documents(payload)
        accepted_consents: list[dict[str, Any]] = []

        self._audit(
            f"{CONSENT_AUDIT_PREFIX}.submitted",
            user=user,
            entity_type="user_consent",
            metadata={
                "itemCount": len(payload.items),
                "source": payload.source,
                "idempotencyKey": idempotency_key,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        try:
            for item in payload.items:
                legal_document = documents_by_id[uuid.UUID(item.legal_document_id)]
                existing_consent = self.consents.get_existing_accepted_consent(
                    user_id=user.id,
                    legal_document_id=legal_document.id,
                )
                if existing_consent is not None:
                    raise ApiError(
                        status_code=status.HTTP_409_CONFLICT,
                        code="duplicate_consent",
                        message="El consentimiento ya fue registrado para esta version legal.",
                    )

                evidence_payload = _evidence_payload(
                    user_id=user.id,
                    legal_document=legal_document,
                    accepted=True,
                    accepted_at=now,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    source=payload.source,
                )
                evidence_hash = _stable_sha256(evidence_payload)
                consent = self.consents.create_user_consent(
                    user_id=user.id,
                    legal_document=legal_document,
                    accepted=True,
                    accepted_at=now,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    locale=payload.locale,
                    source=payload.source,
                    evidence_hash_sha256=evidence_hash,
                )
                self.consents.create_evidence(
                    user_consent_id=consent.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=idempotency_key,
                    session_id=session_id,
                    accepted_text_snapshot_hash=legal_document.hash_sha256,
                    acceptance_payload_json=_json_safe(evidence_payload),
                )
                self._audit(
                    f"{CONSENT_AUDIT_PREFIX}.created",
                    user=user,
                    entity_type="user_consent",
                    entity_id=consent.id,
                    metadata={
                        "userId": str(user.id),
                        "consentType": legal_document.type,
                        "legalDocumentId": str(legal_document.id),
                        "documentVersion": legal_document.version,
                        "documentHashSha256": legal_document.hash_sha256,
                        "evidenceHashSha256": evidence_hash,
                        "ipAddress": ip_address,
                        "userAgent": user_agent,
                        "source": payload.source,
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                accepted_consents.append(
                    {
                        "id": str(consent.id),
                        "consentType": consent.consent_type,
                        "documentVersion": consent.document_version,
                        "acceptedAt": consent.accepted_at,
                        "evidenceHashSha256": consent.evidence_hash_sha256,
                    }
                )

            status_payload = self._build_status(user.id)
            response_payload = {
                "status": status_payload["status"],
                "canUploadDocuments": status_payload["canUploadDocuments"],
                "acceptedConsents": accepted_consents,
            }
            if status_payload["status"] == "completed":
                self._audit(
                    f"{CONSENT_AUDIT_PREFIX}.completed",
                    user=user,
                    entity_type="user_consent",
                    metadata={"userId": str(user.id), "source": payload.source},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

            if idempotency_key:
                self.consents.create_idempotency_key(
                    user_id=user.id,
                    idempotency_key=idempotency_key,
                    request_hash_sha256=request_hash,
                    response_json=_json_safe(response_payload),
                )
            self.db.commit()
        except ApiError:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="duplicate_consent",
                message="El consentimiento ya fue registrado para esta version legal.",
            ) from exc

        return response_payload

    def create_legal_document(
        self,
        payload: LegalDocumentCreateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_admin(user)
        existing = self.consents.get_legal_document_by_type_version(
            consent_type=payload.type,
            version=payload.version,
        )
        if existing is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="duplicate_legal_document_version",
                message="Ya existe una version legal para ese tipo de consentimiento.",
            )
        document_hash = calculate_document_hash(
            consent_type=payload.type,
            title=payload.title,
            version=payload.version,
            content_markdown=payload.content_markdown,
        )
        document = self.consents.create_legal_document(
            consent_type=payload.type,
            title=payload.title,
            slug=payload.slug or _slugify(f"{payload.type}-{payload.version}"),
            content_markdown=payload.content_markdown,
            content_plain_text=payload.content_plain_text,
            version=payload.version,
            hash_sha256=document_hash,
            status=payload.status,
            is_required=payload.is_required,
            effective_from=payload.effective_from,
            created_by=user.id,
        )
        self._audit(
            "legal_document.created",
            user=user,
            entity_type="legal_document",
            entity_id=document.id,
            new_state=self._legal_document_admin(document),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._legal_document_admin(document)

    def activate_legal_document(
        self,
        document_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_admin(user)
        document = self.consents.get_legal_document(document_id)
        if document is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="legal_document_not_found",
                message="Documento legal no encontrado.",
            )

        now = utc_now()
        for active_document in self.consents.list_active_legal_documents_by_type(
            document.type
        ):
            if active_document.id == document.id:
                continue
            previous_state = self._legal_document_admin(active_document)
            active_document.status = "archived"
            active_document.effective_to = now
            active_document.updated_at = now
            self._audit(
                "legal_document.archived",
                user=user,
                entity_type="legal_document",
                entity_id=active_document.id,
                previous_state=previous_state,
                new_state=self._legal_document_admin(active_document),
                ip_address=ip_address,
                user_agent=user_agent,
            )

        previous_state = self._legal_document_admin(document)
        document.status = "active"
        document.effective_to = None
        if _as_utc(document.effective_from) > now:
            document.effective_from = now
        document.updated_at = now
        self._audit(
            "legal_document.activated",
            user=user,
            entity_type="legal_document",
            entity_id=document.id,
            previous_state=previous_state,
            new_state=self._legal_document_admin(document),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._legal_document_admin(document)

    def _build_status(self, user_id: uuid.UUID) -> dict[str, Any]:
        required_documents, _conflicts, missing_document_types = (
            self._required_current_documents()
        )
        required_types = [document.type for document in required_documents]
        current_document_ids = [document.id for document in required_documents]
        accepted_consents = self.consents.list_user_accepted_consents_for_documents(
            user_id=user_id,
            legal_document_ids=current_document_ids,
        )
        accepted_types = sorted({consent.consent_type for consent in accepted_consents})
        missing_types = [
            consent_type
            for consent_type in required_types
            if consent_type not in accepted_types
        ]
        if missing_document_types:
            status_value = "blocked"
            missing_types = missing_document_types + missing_types
        elif not required_types:
            status_value = "blocked"
        elif not accepted_types and self.consents.count_user_consents(user_id) == 0:
            status_value = "not_started"
        elif not missing_types:
            status_value = "completed"
        else:
            status_value = "in_progress"

        return {
            "status": status_value,
            "canUploadDocuments": status_value == "completed",
            "requiredConsentTypes": required_types or REQUIRED_CONSENT_TYPES,
            "acceptedConsentTypes": accepted_types,
            "missingConsentTypes": missing_types,
            "lastAcceptedAt": self.consents.get_last_accepted_at(user_id),
        }

    def _load_and_validate_documents(
        self,
        payload: ConsentSubmitRequest,
    ) -> dict[uuid.UUID, LegalDocument]:
        now = utc_now()
        documents_by_id: dict[uuid.UUID, LegalDocument] = {}
        for item in payload.items:
            try:
                parsed_document_id = uuid.UUID(item.legal_document_id)
            except ValueError as exc:
                raise ApiError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="legal_document_not_found",
                    message="Documento legal no encontrado.",
                ) from exc

            legal_document = self.consents.get_legal_document(parsed_document_id)
            if legal_document is None:
                raise ApiError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="legal_document_not_found",
                    message="Documento legal no encontrado.",
                )
            if legal_document.type != item.consent_type:
                raise ApiError(
                    status_code=HTTP_422_UNPROCESSABLE,
                    code="consent_type_mismatch",
                    message="El tipo de consentimiento no coincide con el documento legal.",
                )
            if (
                legal_document.status != "active"
                or _as_utc(legal_document.effective_from) > now
                or (
                    legal_document.effective_to is not None
                    and _as_utc(legal_document.effective_to) <= now
                )
            ):
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="legal_document_not_active",
                    message="El documento legal no esta vigente.",
                )
            documents_by_id[parsed_document_id] = legal_document
        return documents_by_id

    def _assert_payload_items(self, payload: ConsentSubmitRequest) -> None:
        if not payload.items:
            raise ApiError(
                status_code=HTTP_422_UNPROCESSABLE,
                code="invalid_payload",
                message="Debes enviar al menos un consentimiento.",
            )
        seen: set[tuple[str, str]] = set()
        for item in payload.items:
            if item.accepted is not True:
                raise ApiError(
                    status_code=HTTP_422_UNPROCESSABLE,
                    code="invalid_payload",
                    message="El consentimiento debe aceptarse explicitamente.",
                )
            key = (item.legal_document_id, item.consent_type)
            if key in seen:
                raise ApiError(
                    status_code=HTTP_422_UNPROCESSABLE,
                    code="invalid_payload",
                    message="No repitas el mismo consentimiento en una solicitud.",
                )
            seen.add(key)

    def _assert_required_documents_configured(self) -> None:
        _documents, _conflicts, missing_types = self._required_current_documents()
        if missing_types:
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="missing_required_consents",
                message="Faltan documentos legales obligatorios vigentes.",
                details={"missingConsentTypes": missing_types},
            )

    def _required_current_documents(
        self,
    ) -> tuple[list[LegalDocument], list[str], list[str]]:
        documents, conflicts = self._select_current_documents(types=REQUIRED_CONSENT_TYPES)
        required_documents = [document for document in documents if document.is_required]
        available_types = {document.type for document in documents}
        missing_types = [
            consent_type
            for consent_type in REQUIRED_CONSENT_TYPES
            if consent_type not in available_types
        ]
        return required_documents, conflicts, missing_types

    def _select_current_documents(
        self,
        *,
        types: list[str] | None,
    ) -> tuple[list[LegalDocument], list[str]]:
        current_documents = self.consents.list_current_legal_documents(
            at=utc_now(),
            types=types,
        )
        selected_by_type: dict[str, LegalDocument] = {}
        counts_by_type: dict[str, int] = {}
        for document in current_documents:
            counts_by_type[document.type] = counts_by_type.get(document.type, 0) + 1
            if document.type not in selected_by_type:
                selected_by_type[document.type] = document
        selected_documents = sorted(
            selected_by_type.values(),
            key=lambda document: REQUIRED_CONSENT_TYPES.index(document.type)
            if document.type in REQUIRED_CONSENT_TYPES
            else document.type,
        )
        conflicts = [
            consent_type
            for consent_type, count in counts_by_type.items()
            if count > 1
        ]
        return selected_documents, conflicts

    def _validate_consent_types(self, consent_types: list[str]) -> None:
        invalid_types = [
            consent_type
            for consent_type in consent_types
            if consent_type not in REQUIRED_CONSENT_TYPES
        ]
        if invalid_types:
            raise ApiError(
                status_code=HTTP_422_UNPROCESSABLE,
                code="invalid_payload",
                message="Tipo de consentimiento invalido.",
                details={"invalidConsentTypes": invalid_types},
            )

    def _require_admin(self, user: User) -> None:
        if user.role not in ADMIN_ROLES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="forbidden",
                message="No tienes permisos para administrar documentos legales.",
            )

    def _audit(
        self,
        event_type: str,
        *,
        user: User | None = None,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
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
            entity_id=entity_id,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata=_json_safe(metadata) if metadata else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _legal_document_current(self, document: LegalDocument) -> dict[str, Any]:
        return {
            "id": str(document.id),
            "type": document.type,
            "title": document.title,
            "version": document.version,
            "hashSha256": document.hash_sha256,
            "contentMarkdown": document.content_markdown,
            "isRequired": document.is_required,
            "effectiveFrom": document.effective_from,
        }

    def _legal_document_admin(self, document: LegalDocument) -> dict[str, Any]:
        return {
            "id": str(document.id),
            "type": document.type,
            "title": document.title,
            "slug": document.slug,
            "version": document.version,
            "hashSha256": document.hash_sha256,
            "status": document.status,
            "isRequired": document.is_required,
            "effectiveFrom": document.effective_from,
            "effectiveTo": document.effective_to,
            "createdAt": document.created_at,
            "updatedAt": document.updated_at,
        }


class ConsentService(ConsentComplianceService):
    """Backward-compatible alias for older imports."""


def can_role_read_user_consents(user: User) -> bool:
    return user.role in SUPPORT_READ_ROLES


def calculate_document_hash(
    *,
    consent_type: str,
    title: str,
    version: str,
    content_markdown: str,
) -> str:
    return _stable_sha256(
        {
            "type": consent_type,
            "title": title,
            "version": version,
            "contentMarkdown": content_markdown,
        }
    )


def _stable_sha256(payload: dict[str, Any]) -> str:
    # Evidence hashes use canonical JSON with sorted keys, compact separators and
    # UTC ISO timestamps so the same legal acceptance can be reproduced later.
    serialized = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _isoformat_utc(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _isoformat_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _evidence_payload(
    *,
    user_id: uuid.UUID,
    legal_document: LegalDocument,
    accepted: bool,
    accepted_at: datetime,
    ip_address: str | None,
    user_agent: str | None,
    source: str,
) -> dict[str, Any]:
    return {
        "userId": str(user_id),
        "legalDocumentId": str(legal_document.id),
        "consentType": legal_document.type,
        "documentVersion": legal_document.version,
        "documentHashSha256": legal_document.hash_sha256,
        "accepted": accepted,
        "acceptedAt": accepted_at,
        "ipAddress": ip_address,
        "userAgent": user_agent,
        "source": source,
    }


def _idempotency_payload(payload: ConsentSubmitRequest) -> dict[str, Any]:
    return {
        "items": sorted(
            [
                {
                    "legalDocumentId": item.legal_document_id,
                    "consentType": item.consent_type,
                    "accepted": item.accepted,
                }
                for item in payload.items
            ],
            key=lambda item: (item["legalDocumentId"], item["consentType"]),
        ),
        "source": payload.source,
        "locale": payload.locale,
    }


def _parse_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:160] or "legal-document"
