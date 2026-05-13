import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session, joinedload

from app.models.consent import (
    ConsentEvidence,
    ConsentIdempotencyKey,
    LegalDocument,
    UserConsent,
)


class ConsentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_legal_document(self, document_id: str | uuid.UUID) -> LegalDocument | None:
        try:
            parsed_document_id = uuid.UUID(str(document_id))
        except ValueError:
            return None
        return self.db.get(LegalDocument, parsed_document_id)

    def get_legal_document_by_type_version(
        self,
        *,
        consent_type: str,
        version: str,
    ) -> LegalDocument | None:
        return (
            self.db.query(LegalDocument)
            .filter(
                LegalDocument.type == consent_type,
                LegalDocument.version == version,
            )
            .one_or_none()
        )

    def list_current_legal_documents(
        self,
        *,
        at: datetime,
        types: list[str] | None = None,
    ) -> list[LegalDocument]:
        query = self.db.query(LegalDocument).filter(
            LegalDocument.status == "active",
            LegalDocument.effective_from <= at,
            or_(
                LegalDocument.effective_to.is_(None),
                LegalDocument.effective_to > at,
            ),
        )
        if types:
            query = query.filter(LegalDocument.type.in_(types))
        return (
            query.order_by(
                LegalDocument.type.asc(),
                LegalDocument.effective_from.desc(),
                LegalDocument.created_at.desc(),
            )
            .all()
        )

    def list_active_legal_documents_by_type(
        self,
        consent_type: str,
    ) -> list[LegalDocument]:
        return (
            self.db.query(LegalDocument)
            .filter(
                LegalDocument.type == consent_type,
                LegalDocument.status == "active",
            )
            .order_by(LegalDocument.effective_from.desc())
            .all()
        )

    def create_legal_document(
        self,
        *,
        consent_type: str,
        title: str,
        slug: str,
        content_markdown: str,
        content_plain_text: str | None,
        version: str,
        hash_sha256: str,
        status: str,
        is_required: bool,
        effective_from: datetime,
        created_by: uuid.UUID | None,
    ) -> LegalDocument:
        document = LegalDocument(
            type=consent_type,
            title=title,
            slug=slug,
            content_markdown=content_markdown,
            content_plain_text=content_plain_text,
            version=version,
            hash_sha256=hash_sha256,
            status=status,
            is_required=is_required,
            effective_from=effective_from,
            created_by=created_by,
        )
        self.db.add(document)
        self.db.flush()
        return document

    def get_existing_accepted_consent(
        self,
        *,
        user_id: uuid.UUID,
        legal_document_id: uuid.UUID,
    ) -> UserConsent | None:
        return (
            self.db.query(UserConsent)
            .filter(
                UserConsent.user_id == user_id,
                UserConsent.legal_document_id == legal_document_id,
                UserConsent.accepted.is_(True),
                UserConsent.revoked_at.is_(None),
            )
            .one_or_none()
        )

    def create_user_consent(
        self,
        *,
        user_id: uuid.UUID,
        legal_document: LegalDocument,
        accepted: bool,
        accepted_at: datetime,
        ip_address: str | None,
        user_agent: str | None,
        locale: str | None,
        source: str,
        evidence_hash_sha256: str,
    ) -> UserConsent:
        consent = UserConsent(
            user_id=user_id,
            legal_document_id=legal_document.id,
            consent_type=legal_document.type,
            document_version=legal_document.version,
            document_hash_sha256=legal_document.hash_sha256,
            accepted=accepted,
            accepted_at=accepted_at,
            ip_address=ip_address,
            user_agent=user_agent,
            locale=locale,
            source=source,
            evidence_hash_sha256=evidence_hash_sha256,
        )
        self.db.add(consent)
        self.db.flush()
        return consent

    def create_evidence(
        self,
        *,
        user_consent_id: uuid.UUID,
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
        session_id: str | None,
        accepted_text_snapshot_hash: str,
        acceptance_payload_json: dict[str, Any],
    ) -> ConsentEvidence:
        evidence = ConsentEvidence(
            user_consent_id=user_consent_id,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            session_id=session_id,
            accepted_text_snapshot_hash=accepted_text_snapshot_hash,
            acceptance_payload_json=acceptance_payload_json,
        )
        self.db.add(evidence)
        self.db.flush()
        return evidence

    def list_user_consents(self, user_id: uuid.UUID) -> list[UserConsent]:
        return (
            self.db.query(UserConsent)
            .options(joinedload(UserConsent.legal_document))
            .filter(UserConsent.user_id == user_id)
            .order_by(desc(UserConsent.accepted_at))
            .all()
        )

    def list_user_accepted_consents_for_documents(
        self,
        *,
        user_id: uuid.UUID,
        legal_document_ids: list[uuid.UUID],
    ) -> list[UserConsent]:
        if not legal_document_ids:
            return []
        return (
            self.db.query(UserConsent)
            .filter(
                UserConsent.user_id == user_id,
                UserConsent.legal_document_id.in_(legal_document_ids),
                UserConsent.accepted.is_(True),
                UserConsent.revoked_at.is_(None),
            )
            .order_by(desc(UserConsent.accepted_at))
            .all()
        )

    def count_user_consents(self, user_id: uuid.UUID) -> int:
        return self.db.query(UserConsent).filter(UserConsent.user_id == user_id).count()

    def get_last_accepted_at(self, user_id: uuid.UUID) -> datetime | None:
        row = (
            self.db.query(UserConsent.accepted_at)
            .filter(
                UserConsent.user_id == user_id,
                UserConsent.accepted.is_(True),
                UserConsent.revoked_at.is_(None),
            )
            .order_by(desc(UserConsent.accepted_at))
            .first()
        )
        return row[0] if row else None

    def get_idempotency_key(
        self,
        *,
        user_id: uuid.UUID,
        idempotency_key: str,
    ) -> ConsentIdempotencyKey | None:
        return (
            self.db.query(ConsentIdempotencyKey)
            .filter(
                ConsentIdempotencyKey.user_id == user_id,
                ConsentIdempotencyKey.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )

    def create_idempotency_key(
        self,
        *,
        user_id: uuid.UUID,
        idempotency_key: str,
        request_hash_sha256: str,
        response_json: dict[str, Any],
    ) -> ConsentIdempotencyKey:
        key = ConsentIdempotencyKey(
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash_sha256=request_hash_sha256,
            response_json=response_json,
        )
        self.db.add(key)
        self.db.flush()
        return key
