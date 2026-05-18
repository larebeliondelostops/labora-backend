import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import settings


LEGAL_DRAFT_PROMPT_VERSION = "legal-draft-generation-v1"
QUALITY_CHECK_PROMPT_VERSION = "legal-draft-quality-v1"


@dataclass(frozen=True)
class LegalDraftAiOutput:
    title: str
    sections: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    missing_data: list[dict[str, Any]]
    professional_review_suggestion: str
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    output_hash: str
    confidence_score: float


class LegalDraftAiProvider(Protocol):
    provider: str
    model: str

    def generate_draft(self, input_payload: dict[str, Any]) -> LegalDraftAiOutput:
        ...

    def regenerate_section(
        self,
        input_payload: dict[str, Any],
        *,
        section_key: str,
        instruction: str,
        preserve_user_edits: bool,
    ) -> LegalDraftAiOutput:
        ...


class DeterministicLegalDraftAiProvider:
    provider = "deterministic"
    model = "labora-legal-draft-writer-v1"

    def generate_draft(self, input_payload: dict[str, Any]) -> LegalDraftAiOutput:
        sections = input_payload.get("templateSchema", {}).get("sections") or []
        analysis = input_payload.get("analysis") or {}
        confidence = _confidence(input_payload)
        generated_sections = [
            self._section_payload(section, input_payload, confidence=confidence)
            for section in sections
        ]
        missing_data = _missing_data(input_payload)
        warnings = _warnings(input_payload)
        suggestion = _review_suggestion(confidence, input_payload)
        output = {
            "title": _title(input_payload),
            "sections": generated_sections,
            "warnings": warnings,
            "missing_data": missing_data,
            "professional_review_suggestion": suggestion,
            "analysisId": analysis.get("id"),
        }
        return LegalDraftAiOutput(
            title=output["title"],
            sections=generated_sections,
            warnings=warnings,
            missing_data=missing_data,
            professional_review_suggestion=suggestion,
            provider=self.provider,
            model=self.model,
            prompt_version=LEGAL_DRAFT_PROMPT_VERSION,
            input_hash=_stable_hash(input_payload),
            output_hash=_stable_hash(output),
            confidence_score=confidence,
        )

    def regenerate_section(
        self,
        input_payload: dict[str, Any],
        *,
        section_key: str,
        instruction: str,
        preserve_user_edits: bool,
    ) -> LegalDraftAiOutput:
        confidence = _confidence(input_payload)
        title = _section_title(section_key)
        current = input_payload.get("currentSection") or {}
        content = current.get("contentPlain") or ""
        if preserve_user_edits and content:
            markdown = (
                f"{content}\n\n"
                f"Ajuste propuesto: {instruction.strip()}. "
                "Se conservan los hechos y datos pendientes registrados."
            )
        else:
            markdown = _section_markdown(section_key, input_payload)
            markdown = f"{markdown}\n\nAjuste solicitado: {instruction.strip()}."
        section = {
            "section_key": section_key,
            "title": title,
            "content_markdown": markdown,
            "source_references": _source_refs(input_payload),
            "pending_markers": _missing_data(input_payload),
            "confidence_score": confidence / 100,
        }
        output = {
            "title": _title(input_payload),
            "sections": [section],
            "warnings": _warnings(input_payload),
            "missing_data": _missing_data(input_payload),
            "professional_review_suggestion": _review_suggestion(confidence, input_payload),
        }
        return LegalDraftAiOutput(
            title=output["title"],
            sections=[section],
            warnings=output["warnings"],
            missing_data=output["missing_data"],
            professional_review_suggestion=output["professional_review_suggestion"],
            provider=self.provider,
            model=self.model,
            prompt_version=LEGAL_DRAFT_PROMPT_VERSION,
            input_hash=_stable_hash(
                {
                    "payload": input_payload,
                    "sectionKey": section_key,
                    "instruction": instruction,
                    "preserveUserEdits": preserve_user_edits,
                }
            ),
            output_hash=_stable_hash(output),
            confidence_score=confidence,
        )

    def _section_payload(
        self,
        section: dict[str, Any],
        input_payload: dict[str, Any],
        *,
        confidence: float,
    ) -> dict[str, Any]:
        section_key = section["section_key"]
        return {
            "section_key": section_key,
            "title": section.get("title") or _section_title(section_key),
            "content_markdown": _section_markdown(section_key, input_payload),
            "source_references": _source_refs(input_payload),
            "pending_markers": _missing_data(input_payload),
            "confidence_score": confidence / 100,
        }


def build_legal_draft_ai_provider() -> LegalDraftAiProvider:
    provider = (settings.AI_PROVIDER or "mock").strip().lower()
    client = DeterministicLegalDraftAiProvider()
    if provider not in {"", "mock", "deterministic"}:
        object.__setattr__(client, "provider", provider)
        model = (
            getattr(settings, "AI_MODEL_DRAFT_GENERATION", "")
            or settings.AI_MODEL
            or "configured-legal-draft-model"
        )
        object.__setattr__(client, "model", model)
    return client


def _title(input_payload: dict[str, Any]) -> str:
    action_type = (input_payload.get("action") or {}).get("actionType")
    return {
        "petition": "Derecho de peticion",
        "administrative_claim": "Reclamacion administrativa",
        "reliquidation_request": "Solicitud de reliquidacion",
        "administrative_appeal": "Recurso administrativo",
        "lawsuit_draft": "Borrador de demanda",
        "professional_review_request": "Solicitud de revision profesional",
        "executive_summary": "Resumen ejecutivo",
        "technical_report_download": "Descarga de informe tecnico",
    }.get(action_type, "Escrito juridico")


def _section_title(section_key: str) -> str:
    return {
        "heading": "Encabezado",
        "parties": "Partes",
        "jurisdiction_and_competence": "Jurisdiccion y competencia",
        "recipient": "Destinatario",
        "claimant_identification": "Identificacion del solicitante",
        "facts": "Hechos",
        "requests": "Peticiones",
        "claims": "Pretensiones",
        "legal_basis": "Fundamentos juridicos",
        "evidence": "Pruebas",
        "attachments": "Anexos",
        "estimated_amount_or_oath": "Cuantia o juramento estimatorio",
        "notifications": "Notificaciones",
        "signature": "Firma",
        "professional_review_warning": "Advertencia de revision profesional",
    }.get(section_key, section_key.replace("_", " ").title())


def _section_markdown(section_key: str, input_payload: dict[str, Any]) -> str:
    case = input_payload.get("case") or {}
    analysis = input_payload.get("analysis") or {}
    report = input_payload.get("report") or {}
    user_inputs = input_payload.get("userInputs") or {}
    inconsistencies = input_payload.get("inconsistencies") or []
    calculations = input_payload.get("calculations") or []
    documents = input_payload.get("documents") or []
    pending = _missing_data(input_payload)

    if section_key in {"heading", "recipient"}:
        city = user_inputs.get("city") or user_inputs.get("ciudad") or "[DATO PENDIENTE: ciudad]"
        recipient = user_inputs.get("recipient") or user_inputs.get("entity") or case.get("pensionFundOrEntity") or "[DATO PENDIENTE: destinatario]"
        return f"{city}\n\nSenores\n{recipient}\n\nReferencia: expediente {case.get('caseNumber', 'Labora')}."
    if section_key in {"claimant_identification", "parties"}:
        return (
            f"Solicitante/Demandante: {case.get('holderName') or '[DATO PENDIENTE: nombre]'}.\n"
            f"Documento: {case.get('holderDocument') or '[DATO PENDIENTE: identificacion]'}.\n"
            f"Entidad relacionada: {case.get('pensionFundOrEntity') or '[DATO PENDIENTE: entidad]'}."
        )
    if section_key == "jurisdiction_and_competence":
        return "La jurisdiccion y competencia deben ser confirmadas por profesional juridico antes de radicar."
    if section_key == "facts":
        lines = []
        summary = report.get("technicalSummary") or report.get("executiveSummary") or analysis.get("legalConclusion")
        if summary:
            lines.append(str(summary))
        lines.extend(str(item.get("description") or item.get("title")) for item in inconsistencies[:6])
        return "\n".join(f"- {line}" for line in lines if line) or "No hay hechos suficientes; marcar como pendiente."
    if section_key in {"requests", "claims"}:
        route = analysis.get("recommendedRoute") or "revision_profesional"
        lines = [f"Que se estudie la ruta recomendada: {route}."]
        lines.extend(f"Que se revise: {item.get('title')}." for item in inconsistencies[:4] if item.get("title"))
        return "\n".join(f"- {line}" for line in lines)
    if section_key == "legal_basis":
        return analysis.get("legalConclusion") or "Los fundamentos juridicos deben completarse con base en reglas verificables del analisis."
    if section_key == "evidence":
        evidence = []
        for item in inconsistencies:
            evidence.extend(ref.get("label") or ref.get("id") for ref in item.get("evidenceRefs") or [])
        evidence.extend(document.get("fileName") for document in documents if document.get("fileName"))
        return "\n".join(f"- {item}" for item in evidence if item) or "- [DATO PENDIENTE: soportes probatorios]"
    if section_key == "attachments":
        if not documents:
            return "- [DATO PENDIENTE: anexos]"
        return "\n".join(f"- {document.get('fileName')}" for document in documents if document.get("fileName"))
    if section_key == "estimated_amount_or_oath":
        amount = _first_amount(calculations)
        return f"Valor estimado de referencia: {amount}." if amount else "[DATO PENDIENTE: cuantia o calculo]"
    if section_key == "notifications":
        return user_inputs.get("notificationAddress") or case.get("holderEmail") or "[DATO PENDIENTE: datos de notificacion]"
    if section_key == "signature":
        return f"{case.get('holderName') or '[DATO PENDIENTE: firmante]'}\n{case.get('holderDocument') or ''}".strip()
    if section_key == "professional_review_warning":
        return "Este borrador no equivale a radicacion ni reemplaza revision juridica profesional cuando aplique."
    if pending:
        return "Seccion pendiente de completar con datos faltantes verificados."
    return "Contenido base generado desde plantilla y datos verificables del expediente."


def _missing_data(input_payload: dict[str, Any]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    user_inputs = input_payload.get("userInputs") or {}
    case = input_payload.get("case") or {}
    for key, label in [
        ("city", "Ciudad"),
        ("notificationAddress", "Direccion o correo de notificacion"),
    ]:
        if not user_inputs.get(key):
            markers.append({"key": key, "label": label, "reason": "Dato no informado en wizard."})
    if not case.get("holderDocument"):
        markers.append({"key": "holder_document", "label": "Identificacion del solicitante", "reason": "No esta disponible en expediente."})
    return markers


def _warnings(input_payload: dict[str, Any]) -> list[dict[str, Any]]:
    warnings = []
    confidence = _confidence(input_payload)
    if confidence < 70:
        warnings.append(
            {
                "code": "LOW_CONFIDENCE",
                "message": "El analisis fuente tiene confianza baja y requiere revision profesional.",
            }
        )
    missing_attachments = input_payload.get("missingAttachments") or []
    if missing_attachments:
        warnings.append(
            {
                "code": "MISSING_ATTACHMENTS",
                "message": "Existen anexos recomendados o criticos pendientes.",
                "items": missing_attachments,
            }
        )
    return warnings


def _review_suggestion(confidence: float, input_payload: dict[str, Any]) -> str:
    action_type = (input_payload.get("action") or {}).get("actionType")
    if action_type == "lawsuit_draft":
        return "mandatory" if confidence < 70 else "recommended"
    if confidence < 55:
        return "mandatory"
    if confidence < 70:
        return "recommended"
    return "optional"


def _source_refs(input_payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    analysis = input_payload.get("analysis") or {}
    if analysis.get("id"):
        refs.append({"type": "analysis", "id": analysis["id"], "label": "Analisis completo"})
    report = input_payload.get("report") or {}
    if report.get("id"):
        refs.append({"type": "report", "id": report["id"], "label": "Informe base"})
    for item in (input_payload.get("inconsistencies") or [])[:5]:
        if item.get("id"):
            refs.append({"type": "inconsistency", "id": item["id"], "label": item.get("title")})
    return refs


def _confidence(input_payload: dict[str, Any]) -> float:
    raw = (input_payload.get("analysis") or {}).get("confidenceGlobal")
    try:
        return float(raw if raw is not None else 80.0)
    except (TypeError, ValueError):
        return 80.0


def _first_amount(calculations: list[dict[str, Any]]) -> str | None:
    for code in ("RETROACTIVE_ESTIMATE_001", "ECONOMIC_DIFFERENCE_001", "CORRECT_ESTIMATED_AMOUNT_001"):
        for item in calculations:
            if item.get("calculationCode") == code and item.get("resultValue") is not None:
                unit = item.get("resultUnit") or ""
                return f"{item['resultValue']} {unit}".strip()
    return None


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
