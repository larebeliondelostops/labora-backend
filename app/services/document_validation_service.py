import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.user import User
from app.repositories.document_repository import DocumentRepository
from app.services.document_audit_service import DocumentAuditService
from app.services.document_storage_service import DocumentStorageService
from app.utils.dates import utc_now
from app.utils.hashing import sha256_bytes


class DocumentValidationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.storage = DocumentStorageService()
        self.audit = DocumentAuditService(db)

    def validate_document(
        self,
        document: Document,
        *,
        actor: User | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        previous_state = _document_state(document)
        document.status = "processing"
        document.validation_status = "in_progress"
        document.updated_at = utc_now()
        self.audit.record(
            "carga_documental.validation_started",
            actor=actor,
            document=document,
            previous_state=previous_state,
            new_state=_document_state(document),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        try:
            content = self.storage.read(document.storage_key)
        except OSError:
            return self._fail_validation(
                document,
                actor=actor,
                code="STORAGE_PROVIDER_ERROR",
                message="No fue posible leer el archivo almacenado.",
                ip_address=ip_address,
                user_agent=user_agent,
            )

        sha256_hash = sha256_bytes(content)
        document.sha256_hash = sha256_hash
        self.documents.upsert_hash(
            document_id=document.id,
            hash_type="sha256",
            hash_value=sha256_hash,
        )

        checks, warnings, errors, pages, page_count = self._inspect_content(
            document=document,
            content=content,
        )
        document.page_count = page_count
        document.is_password_protected = bool(checks["isPasswordProtected"])
        document.is_corrupted = bool(checks["isCorrupted"])
        self.documents.replace_pages(document_id=document.id, pages=pages)

        duplicate = self.documents.find_duplicate_by_hash(
            sha256_hash=sha256_hash,
            exclude_document_id=document.id,
        )
        if duplicate is not None:
            document.is_duplicate = True
            if duplicate.case_id == document.case_id:
                document.duplicate_of_document_id = duplicate.id
            warnings.append(
                {
                    "code": "DOCUMENT_DUPLICATE",
                    "message": "Este archivo parece duplicado de otro documento ya cargado.",
                }
            )
            self.audit.record(
                "carga_documental.duplicate_detected",
                actor=actor,
                document=document,
                metadata={"sameCase": duplicate.case_id == document.case_id},
                ip_address=ip_address,
                user_agent=user_agent,
            )

        if errors:
            result = "rejected"
            status = "blocked"
            score = Decimal("0.0000")
            document.status = "rejected"
            document.validation_status = "blocked"
        elif warnings:
            result = "accepted_with_warnings"
            status = "completed"
            score = Decimal("0.7000")
            document.status = "requires_review" if document.is_duplicate else "validated"
            document.validation_status = "completed"
        else:
            result = "accepted"
            status = "completed"
            score = Decimal("0.9500")
            document.status = "validated"
            document.validation_status = "completed"

        document.updated_at = utc_now()
        validation = self.documents.create_validation(
            document_id=document.id,
            status=status,
            result=result,
            score=score,
            checks=checks,
            warnings=warnings,
            errors=errors,
            created_by="system",
        )
        self.audit.record(
            "carga_documental.validation_completed",
            actor=actor,
            document=document,
            previous_state=previous_state,
            new_state=_document_state(document),
            metadata={"result": result, "score": float(score)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "validation": validation,
            "checks": checks,
            "warnings": warnings,
            "errors": errors,
            "textSample": _extract_text_sample(content) if not errors else "",
        }

    def _inspect_content(
        self,
        *,
        document: Document,
        content: bytes,
    ) -> tuple[dict, list[dict], list[dict], list[dict], int | None]:
        warnings: list[dict] = []
        errors: list[dict] = []
        checks = {
            "sha256Calculated": True,
            "isPdfReadable": False,
            "isPasswordProtected": False,
            "isCorrupted": False,
            "hasExtractableText": False,
            "looksLikeLaborHistory": False,
            "hasLowQualityPages": False,
        }

        if not content:
            checks["isCorrupted"] = True
            errors.append(_error("DOCUMENT_CORRUPTED", "El archivo esta vacio o corrupto."))
            return checks, warnings, errors, [], None

        if document.mime_type == "application/pdf":
            if not content.startswith(b"%PDF"):
                checks["isCorrupted"] = True
                errors.append(
                    _error("DOCUMENT_CORRUPTED", "El archivo PDF no es parseable."),
                )
                return checks, warnings, errors, [], None
            checks["isPdfReadable"] = True
            if b"/Encrypt" in content:
                checks["isPasswordProtected"] = True
                errors.append(
                    _error(
                        "DOCUMENT_PASSWORD_PROTECTED",
                        "El PDF esta protegido con contrasena. Sube una version sin contrasena.",
                    ),
                )
                return checks, warnings, errors, [], None
            text_sample = _extract_text_sample(content)
            checks["hasExtractableText"] = bool(text_sample.strip())
            checks["looksLikeLaborHistory"] = _looks_like_labor_history(
                document.original_filename,
                text_sample,
            )
            page_count = _pdf_page_count(content)
            if not checks["hasExtractableText"]:
                checks["hasLowQualityPages"] = True
                warnings.append(
                    {
                        "code": "PDF_WITHOUT_TEXT",
                        "message": "El PDF no tiene texto extraible; puede requerir OCR o revision manual.",
                    }
                )
            pages = [
                {
                    "page_number": number,
                    "ocr_status": "completed" if text_sample and number == 1 else "pending",
                    "text_extracted": text_sample[:2000] if number == 1 else None,
                    "text_confidence": Decimal("0.7500") if text_sample and number == 1 else None,
                    "quality_score": Decimal("0.8000") if text_sample else Decimal("0.4500"),
                    "warnings": warnings if number == 1 and warnings else None,
                }
                for number in range(1, page_count + 1)
            ]
            return checks, warnings, errors, pages, page_count

        if document.mime_type == "image/jpeg" and not content.startswith(b"\xff\xd8"):
            checks["isCorrupted"] = True
            errors.append(_error("DOCUMENT_CORRUPTED", "La imagen JPEG no es valida."))
            return checks, warnings, errors, [], None

        if document.mime_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            checks["isCorrupted"] = True
            errors.append(_error("DOCUMENT_CORRUPTED", "La imagen PNG no es valida."))
            return checks, warnings, errors, [], None

        pages = [
            {
                "page_number": 1,
                "ocr_status": "pending",
                "quality_score": Decimal("0.7000"),
                "warnings": None,
            }
        ]
        return checks, warnings, errors, pages, 1

    def _fail_validation(
        self,
        document: Document,
        *,
        actor: User | None,
        code: str,
        message: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict:
        previous_state = _document_state(document)
        document.status = "failed"
        document.validation_status = "error"
        document.updated_at = utc_now()
        error = _error(code, message)
        validation = self.documents.create_validation(
            document_id=document.id,
            status="error",
            result="rejected",
            score=Decimal("0.0000"),
            checks={"storageReadable": False},
            warnings=[],
            errors=[error],
            created_by="system",
        )
        self.audit.record(
            "carga_documental.failed",
            actor=actor,
            document=document,
            previous_state=previous_state,
            new_state=_document_state(document),
            metadata={"code": code},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "validation": validation,
            "checks": {"storageReadable": False},
            "warnings": [],
            "errors": [error],
            "textSample": "",
        }


def _pdf_page_count(content: bytes) -> int:
    matches = re.findall(rb"/Type\s*/Page\b", content)
    return max(len(matches), 1)


def _extract_text_sample(content: bytes) -> str:
    decoded = content[:120000].decode("latin-1", errors="ignore")
    printable = re.sub(r"[^\x20-\x7E\n\r\t]+", " ", decoded)
    compact = re.sub(r"\s+", " ", printable).strip()
    return compact[:4000]


def _looks_like_labor_history(filename: str, text_sample: str) -> bool:
    haystack = f"{filename}\n{text_sample}".lower()
    return any(
        signal in haystack
        for signal in [
            "historia laboral",
            "semanas",
            "cotizadas",
            "periodos laborales",
            "colpensiones",
        ]
    )


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _document_state(document: Document) -> dict:
    return {
        "id": str(document.id),
        "caseId": str(document.case_id),
        "status": document.status,
        "validationStatus": document.validation_status,
        "sha256Hash": document.sha256_hash,
        "isDuplicate": document.is_duplicate,
        "pageCount": document.page_count,
    }
