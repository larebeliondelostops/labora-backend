from typing import Any


ISSUE_CATALOG: dict[str, dict[str, str]] = {
    "file_unsupported": {
        "severity": "critical",
        "title": "Archivo no soportado",
        "message": "El formato del archivo no es compatible con el prechequeo documental.",
        "suggested_action": "upload_correct_document",
    },
    "file_too_large": {
        "severity": "critical",
        "title": "Archivo demasiado grande",
        "message": "El archivo supera el tamano permitido para el prechequeo.",
        "suggested_action": "upload_better_scan",
    },
    "pdf_password_protected": {
        "severity": "critical",
        "title": "PDF protegido",
        "message": "El PDF parece estar protegido y no puede leerse automaticamente.",
        "suggested_action": "upload_correct_document",
    },
    "pdf_corrupted": {
        "severity": "critical",
        "title": "PDF corrupto",
        "message": "El PDF no pudo leerse correctamente.",
        "suggested_action": "upload_correct_document",
    },
    "no_text_detected": {
        "severity": "warning",
        "title": "Texto no detectado",
        "message": "No se detecto texto suficiente en el documento.",
        "suggested_action": "upload_better_scan",
    },
    "ocr_low_confidence": {
        "severity": "warning",
        "title": "Baja confianza OCR",
        "message": "La calidad de lectura preliminar es baja.",
        "suggested_action": "upload_better_scan",
    },
    "page_blurry": {
        "severity": "warning",
        "title": "Pagina borrosa",
        "message": "La pagina tiene baja nitidez o poco texto detectable.",
        "suggested_action": "upload_better_scan",
    },
    "page_rotated": {
        "severity": "warning",
        "title": "Pagina rotada",
        "message": "La pagina podria requerir rotacion o nuevo escaneo.",
        "suggested_action": "rotate_or_rescan",
    },
    "wrong_document_type": {
        "severity": "critical",
        "title": "Tipo documental incorrecto",
        "message": "El documento no parece corresponder al tipo requerido.",
        "suggested_action": "upload_correct_document",
    },
    "not_labor_or_pension_document": {
        "severity": "critical",
        "title": "Documento no relacionado",
        "message": "No se detectaron senales laborales o pensionales suficientes.",
        "suggested_action": "upload_correct_document",
    },
    "history_table_not_detected": {
        "severity": "warning",
        "title": "Tabla de historia no detectada",
        "message": "No se detectaron tablas o senales de semanas cotizadas.",
        "suggested_action": "add_supporting_document",
    },
    "low_confidence": {
        "severity": "warning",
        "title": "Baja confianza",
        "message": "El resultado preliminar tiene baja confianza.",
        "suggested_action": "human_review",
    },
    "provider_timeout": {
        "severity": "warning",
        "title": "Proveedor IA sin respuesta",
        "message": "El proveedor de IA no respondio a tiempo.",
        "suggested_action": "wait_and_retry",
    },
    "provider_invalid_json": {
        "severity": "warning",
        "title": "Respuesta IA invalida",
        "message": "La respuesta del proveedor IA no pudo validarse.",
        "suggested_action": "human_review",
    },
    "provider_rate_limited": {
        "severity": "warning",
        "title": "Proveedor IA limitado",
        "message": "El proveedor IA limito temporalmente la solicitud.",
        "suggested_action": "wait_and_retry",
    },
    "human_review_required": {
        "severity": "warning",
        "title": "Revision humana requerida",
        "message": "El documento requiere revision de un operador.",
        "suggested_action": "human_review",
    },
}


def build_issue(
    code: str,
    *,
    page_number: int | None = None,
    severity: str | None = None,
    title: str | None = None,
    message: str | None = None,
    suggested_action: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    template = ISSUE_CATALOG.get(code, {})
    return {
        "code": code,
        "severity": severity or template.get("severity", "warning"),
        "page_number": page_number,
        "title": title or template.get("title", code),
        "message": message or template.get("message", "Observacion documental."),
        "suggested_action": suggested_action or template.get("suggested_action", "human_review"),
        "metadata": metadata,
    }
