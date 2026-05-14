import hashlib
import re
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.document import Document
from app.models.document_precheck import OcrJob
from app.models.user import User
from app.repositories.document_precheck_repository import DocumentPrecheckRepository
from app.services.document_audit_service import DocumentAuditService
from app.services.document_issue_service import build_issue
from app.services.document_storage_service import DocumentStorageService, StorageProviderError
from app.utils.dates import utc_now


class OcrPreviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.prechecks = DocumentPrecheckRepository(db)
        self.storage = DocumentStorageService()
        self.audit = DocumentAuditService(db)

    def create_preview(
        self,
        document: Document,
        *,
        actor: User | None,
        max_pages: int = 5,
        include_text_preview: bool = True,
        force: bool = False,
        precheck_id=None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> OcrJob:
        existing = self.prechecks.latest_ocr_job(document.id)
        if existing is not None and existing.status in {"queued", "in_progress", "completed"} and not force:
            return existing

        now = utc_now()
        job = self.prechecks.create_ocr_job(
            case_id=document.case_id,
            document_id=document.id,
            precheck_id=precheck_id,
            status="queued",
            engine="embedded_text_preview",
            pages_processed=0,
            text_detected=False,
            created_at=now,
            updated_at=now,
        )
        self.audit.record(
            "ia_documental_preliminar.ocr_preview_started",
            actor=actor,
            document=document,
            metadata={"ocrJobId": str(job.id), "precheckId": str(precheck_id) if precheck_id else None},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._process_job(
            job,
            document=document,
            max_pages=max_pages,
            include_text_preview=include_text_preview,
        )
        self.audit.record(
            "ia_documental_preliminar.ocr_preview_completed",
            actor=actor,
            document=document,
            metadata={
                "ocrJobId": str(job.id),
                "precheckId": str(precheck_id) if precheck_id else None,
                "pagesProcessed": job.pages_processed,
                "textDetected": job.text_detected,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return job

    def _process_job(
        self,
        job: OcrJob,
        *,
        document: Document,
        max_pages: int,
        include_text_preview: bool,
    ) -> None:
        job.status = "in_progress"
        job.started_at = utc_now()
        job.updated_at = job.started_at
        try:
            content = self.storage.read(document.storage_key)
        except (OSError, StorageProviderError) as exc:
            job.status = "error"
            job.failed_at = utc_now()
            job.error_code = "OCR_FAILED"
            job.error_message = "No fue posible leer el archivo para OCR preliminar."
            job.updated_at = job.failed_at
            raise ApiError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="OCR_FAILED",
                message="No fue posible leer el archivo para OCR preliminar.",
            ) from exc

        result = inspect_document_content(
            document=document,
            content=content,
            max_pages=max_pages,
            include_text_preview=include_text_preview,
        )
        pages = [
            {
                "document_id": document.id,
                "page_number": page["page_number"],
                "text_preview": page["text_preview"],
                "text_hash": page["text_hash"],
                "text_density": Decimal(str(page["text_density"])),
                "confidence_score": Decimal(str(page["confidence_score"])),
                "is_blurry": page["is_blurry"],
                "is_rotated": page["is_rotated"],
                "rotation_degrees": page["rotation_degrees"],
                "has_table_like_content": page["has_table_like_content"],
                "detected_labels": page["detected_labels"],
                "issues_json": page["issues"],
            }
            for page in result["pages"]
        ]
        self.prechecks.replace_ocr_pages(job.id, pages)
        job.pages_total = result["pages_total"]
        job.pages_processed = len(pages)
        job.text_detected = result["text_detected"]
        job.avg_text_density = Decimal(str(result["avg_text_density"]))
        job.status = "completed"
        job.completed_at = utc_now()
        job.updated_at = job.completed_at
        self.db.flush()


def inspect_document_content(
    *,
    document: Document,
    content: bytes,
    max_pages: int,
    include_text_preview: bool,
) -> dict[str, Any]:
    if document.mime_type == "application/pdf":
        return _inspect_pdf(
            content,
            max_pages=max_pages,
            include_text_preview=include_text_preview,
        )
    if document.mime_type in {"image/jpeg", "image/png"}:
        return _inspect_image(max_pages=max_pages)
    return {
        "pages_total": 1,
        "pages": [],
        "text_detected": False,
        "avg_text_density": 0.0,
        "issues": [build_issue("file_unsupported")],
    }


def _inspect_pdf(
    content: bytes,
    *,
    max_pages: int,
    include_text_preview: bool,
) -> dict[str, Any]:
    if not content.startswith(b"%PDF"):
        issue = build_issue("pdf_corrupted", severity="critical")
        return _single_issue_pdf(issue)
    if b"/Encrypt" in content:
        issue = build_issue("pdf_password_protected", severity="critical")
        return _single_issue_pdf(issue)

    pages_total = max(len(re.findall(rb"/Type\s*/Page\b", content)), 1)
    text = _extract_text_sample(content)
    page_chunks = _split_text_into_pages(text, max_pages=min(max_pages, pages_total))
    pages: list[dict[str, Any]] = []
    for index in range(min(max_pages, pages_total)):
        page_text = page_chunks[index] if index < len(page_chunks) else ""
        density = _text_density(page_text)
        page_issues: list[dict[str, Any]] = []
        is_blurry = False
        if density < 0.08:
            is_blurry = True
            page_issues.append(build_issue("page_blurry", page_number=index + 1))
        preview = page_text[:1200] if include_text_preview else None
        pages.append(
            {
                "page_number": index + 1,
                "text_preview": preview,
                "text_hash": hashlib.sha256(page_text.encode("utf-8")).hexdigest() if page_text else None,
                "text_density": round(density, 4),
                "confidence_score": round(min(0.95, max(0.2, 0.45 + density)), 4),
                "is_blurry": is_blurry,
                "is_rotated": False,
                "rotation_degrees": None,
                "has_table_like_content": _has_table_like_content(page_text),
                "detected_labels": _detected_labels(page_text),
                "issues": page_issues,
            }
        )
    avg_density = sum(page["text_density"] for page in pages) / len(pages) if pages else 0
    return {
        "pages_total": pages_total,
        "pages": pages,
        "text_detected": bool(text.strip()),
        "avg_text_density": round(avg_density, 4),
        "issues": [issue for page in pages for issue in page["issues"]],
    }


def _inspect_image(*, max_pages: int) -> dict[str, Any]:
    issue = build_issue("no_text_detected", page_number=1)
    return {
        "pages_total": 1,
        "pages": [
            {
                "page_number": 1,
                "text_preview": None,
                "text_hash": None,
                "text_density": 0.0,
                "confidence_score": 0.25,
                "is_blurry": True,
                "is_rotated": False,
                "rotation_degrees": None,
                "has_table_like_content": False,
                "detected_labels": [],
                "issues": [issue],
            }
        ][:max_pages],
        "text_detected": False,
        "avg_text_density": 0.0,
        "issues": [issue],
    }


def _single_issue_pdf(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "pages_total": 1,
        "pages": [
            {
                "page_number": 1,
                "text_preview": None,
                "text_hash": None,
                "text_density": 0.0,
                "confidence_score": 0.0,
                "is_blurry": True,
                "is_rotated": False,
                "rotation_degrees": None,
                "has_table_like_content": False,
                "detected_labels": [],
                "issues": [issue],
            }
        ],
        "text_detected": False,
        "avg_text_density": 0.0,
        "issues": [issue],
    }


def _extract_text_sample(content: bytes) -> str:
    decoded = content[:200000].decode("latin-1", errors="ignore")
    printable = re.sub(r"[^\x20-\x7E\n\r\t]+", " ", decoded)
    compact = re.sub(r"\s+", " ", printable).strip()
    return compact[:12000]


def _split_text_into_pages(text: str, *, max_pages: int) -> list[str]:
    if max_pages <= 1:
        return [text]
    chunk_size = max(len(text) // max_pages, 1)
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)][:max_pages]


def _text_density(text: str) -> float:
    if not text:
        return 0.0
    alnum = sum(1 for char in text if char.isalnum())
    return min(1.0, alnum / max(len(text), 1))


def _has_table_like_content(text: str) -> bool:
    lowered = text.lower()
    return any(signal in lowered for signal in ["semanas", "periodos", "cotizadas", "salario", "empleador"])


def _detected_labels(text: str) -> list[str]:
    lowered = text.lower()
    labels: list[str] = []
    for needle, label in [
        ("historia laboral", "historia_laboral"),
        ("semanas", "semanas"),
        ("cotizadas", "semanas_cotizadas"),
        ("colpensiones", "fondo_pensional"),
        ("salario", "salario"),
        ("empleador", "empleador"),
    ]:
        if needle in lowered:
            labels.append(label)
    return sorted(set(labels))
