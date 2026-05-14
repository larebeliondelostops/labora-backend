import hashlib
import io
import logging
import re
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_precheck import OcrJob
from app.models.user import User
from app.repositories.document_precheck_repository import DocumentPrecheckRepository
from app.services.document_audit_service import DocumentAuditService
from app.services.document_issue_service import build_issue
from app.services.document_storage_service import DocumentStorageService, StorageProviderError
from app.utils.dates import utc_now


logger = logging.getLogger(__name__)


class OcrPreviewError(Exception):
    def __init__(self, code: str, message: str, *, issue_code: str = "ocr_failed") -> None:
        self.code = code
        self.issue_code = issue_code
        super().__init__(message)


class StorageReadError(OcrPreviewError):
    def __init__(self, message: str = "No fue posible leer el archivo para OCR preliminar.") -> None:
        super().__init__("STORAGE_READ_ERROR", message, issue_code="storage_read_error")


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
            metadata={
                "ocrJobId": str(job.id),
                "precheckId": str(precheck_id) if precheck_id else None,
                "storageKey": document.storage_key,
            },
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
                "engine": job.engine,
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
        logger.info(
            "Starting OCR preview read",
            extra={
                "case_id": str(document.case_id),
                "document_id": str(document.id),
                "ocr_job_id": str(job.id),
                "storage_key": document.storage_key,
                "document_size_bytes": document.size_bytes,
            },
        )
        try:
            content = self.storage.read(document.storage_key)
        except (FileNotFoundError, OSError, StorageProviderError) as exc:
            job.status = "error"
            job.failed_at = utc_now()
            job.error_code = "STORAGE_READ_ERROR"
            job.error_message = "No fue posible leer el archivo para OCR preliminar."
            job.updated_at = job.failed_at
            logger.exception(
                "Storage read failed during OCR preview",
                extra={
                    "case_id": str(document.case_id),
                    "document_id": str(document.id),
                    "ocr_job_id": str(job.id),
                    "storage_key": document.storage_key,
                },
            )
            raise StorageReadError() from exc

        try:
            result = inspect_document_content(
                document=document,
                content=content,
                max_pages=max_pages,
                include_text_preview=include_text_preview,
            )
        except Exception as exc:
            job.status = "error"
            job.failed_at = utc_now()
            job.error_code = "OCR_FAILED"
            job.error_message = "No fue posible ejecutar la lectura OCR preliminar."
            job.updated_at = job.failed_at
            logger.exception(
                "OCR preview inspection failed",
                extra={
                    "case_id": str(document.case_id),
                    "document_id": str(document.id),
                    "ocr_job_id": str(job.id),
                    "storage_key": document.storage_key,
                    "file_size_bytes": len(content),
                },
            )
            raise OcrPreviewError("OCR_FAILED", "No fue posible ejecutar la lectura OCR preliminar.") from exc
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
        job.engine = result["engine"]
        job.pages_total = result["pages_total"]
        job.pages_processed = len(pages)
        job.text_detected = result["text_detected"]
        job.avg_text_density = Decimal(str(result["avg_text_density"]))
        job.status = "completed"
        job.completed_at = utc_now()
        job.updated_at = job.completed_at
        self.db.flush()
        logger.info(
            "OCR preview completed",
            extra={
                "case_id": str(document.case_id),
                "document_id": str(document.id),
                "ocr_job_id": str(job.id),
                "storage_key": document.storage_key,
                "file_size_bytes": len(content),
                "ocr_method": result["engine"],
                "pages_processed": job.pages_processed,
                "characters_extracted": result["characters_extracted"],
                "text_detected": job.text_detected,
            },
        )


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
        "engine": "unsupported",
        "pages_total": 1,
        "pages": [],
        "text_detected": False,
        "avg_text_density": 0.0,
        "characters_extracted": 0,
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
    text, engine = _extract_pdf_text(content)
    page_chunks = _split_text_into_pages(text, max_pages=min(max_pages, pages_total))
    pages: list[dict[str, Any]] = []
    for index in range(min(max_pages, pages_total)):
        page_text = page_chunks[index] if index < len(page_chunks) else ""
        density = _text_density(page_text)
        page_issues: list[dict[str, Any]] = []
        is_blurry = False
        if not page_text.strip():
            is_blurry = True
            page_issues.append(build_issue("no_text_detected", page_number=index + 1))
            if engine == "ocr_engine_not_configured":
                page_issues.append(build_issue("ocr_provider_not_configured", page_number=index + 1))
        elif density < 0.08:
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
        "engine": engine,
        "pages_total": pages_total,
        "pages": pages,
        "text_detected": bool(text.strip()),
        "avg_text_density": round(avg_density, 4),
        "characters_extracted": len(text),
        "issues": [issue for page in pages for issue in page["issues"]],
    }


def _inspect_image(*, max_pages: int) -> dict[str, Any]:
    issue = build_issue("no_text_detected", page_number=1)
    return {
        "engine": "ocr_engine_not_configured",
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
        "characters_extracted": 0,
        "issues": [issue],
    }


def _single_issue_pdf(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": "embedded_text",
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
        "characters_extracted": 0,
        "issues": [issue],
    }


def _extract_pdf_text(content: bytes) -> tuple[str, str]:
    text = _extract_text_with_pypdf(content)
    if text:
        return text, "embedded_text"
    text = _extract_text_sample(content)
    if text:
        return text, "embedded_text"
    return "", "ocr_engine_not_configured"


def _extract_text_with_pypdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        page_text = "\n".join((page.extract_text() or "") for page in reader.pages[:20])
    except Exception:
        return ""
    return re.sub(r"\s+", " ", page_text).strip()[:12000]


def _extract_text_sample(content: bytes) -> str:
    decoded = content[:200000].decode("latin-1", errors="ignore")
    printable = re.sub(r"[^\x20-\x7E\n\r\t]+", " ", decoded)
    without_streams = re.sub(r"stream.*?endstream", " ", printable, flags=re.IGNORECASE | re.DOTALL)
    stripped = re.sub(r"\b(?:obj|endobj|xref|trailer|startxref|Catalog|Pages?|Type|Root|Kids|Count|MediaBox|Parent)\b", " ", without_streams)
    stripped = re.sub(r"%PDF-\d+\.\d+|%%EOF|/[A-Za-z0-9_]+|<<|>>|\[[^\]]*\]|\d+\s+\d+\s+R", " ", stripped)
    compact = re.sub(r"\s+", " ", stripped).strip()
    if len(re.findall(r"[A-Za-z]{3,}", compact)) < 3:
        return ""
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
