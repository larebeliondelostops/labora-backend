import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import settings


REPORT_PROMPT_VERSION = "report-writing-v1"


@dataclass(frozen=True)
class AiSectionOutput:
    content_markdown: str
    confidence: float
    metadata: dict[str, Any]


class ReportAiClient(Protocol):
    def generate_executive_summary(self, input_payload: dict[str, Any]) -> AiSectionOutput:
        ...

    def generate_technical_narrative(self, input_payload: dict[str, Any]) -> AiSectionOutput:
        ...

    def simplify_legal_language(self, input_payload: dict[str, Any]) -> AiSectionOutput:
        ...


class DeterministicReportAiClient:
    """Controlled writer used locally and in CI without making external calls."""

    provider = "deterministic"
    model = "labora-report-writer-v1"

    def generate_executive_summary(self, input_payload: dict[str, Any]) -> AiSectionOutput:
        analysis = input_payload.get("analysis") or {}
        calculations = input_payload.get("calculations") or []
        inconsistencies = input_payload.get("inconsistencies") or []
        viability = analysis.get("viabilityLevel") or "por validar"
        route = analysis.get("recommendedRoute") or "revision_profesional"
        amount = _first_amount(calculations)
        missing = _missing_documents(inconsistencies)
        lines = [
            f"El analisis completo ubica la viabilidad en nivel **{viability}**.",
            f"La ruta recomendada es **{route}**, sujeta a revision de los soportes.",
        ]
        if amount is not None:
            lines.append(f"El valor estimado de referencia es **{amount} COP**.")
        if inconsistencies:
            lines.append(f"Se identificaron {len(inconsistencies)} inconsistencias relevantes.")
        if missing:
            lines.append("Documentos faltantes o recomendados: " + ", ".join(missing) + ".")
        lines.append(
            "Este informe es un analisis asistido y puede requerir revision profesional antes de usarse como soporte definitivo."
        )
        return self._output("\n\n".join(lines), input_payload, confidence=analysis.get("confidenceGlobal"))

    def generate_technical_narrative(self, input_payload: dict[str, Any]) -> AiSectionOutput:
        rules = input_payload.get("rules") or []
        calculations = input_payload.get("calculations") or []
        analysis = input_payload.get("analysis") or {}
        rule_lines = [
            f"- **{rule.get('ruleName', rule.get('ruleCode'))}**: {rule.get('explanation', 'Sin explicacion registrada.')}"
            for rule in rules[:6]
        ] or ["- No hay reglas juridicas verificables registradas."]
        calculation_lines = [
            f"- **{item.get('calculationName', item.get('calculationCode'))}**: {item.get('resultValue')} {item.get('resultUnit') or ''}".strip()
            for item in calculations[:6]
        ] or ["- No hay calculos estructurados asociados."]
        markdown = "\n\n".join(
            [
                "### Reglas aplicadas",
                "\n".join(rule_lines),
                "### Calculos",
                "\n".join(calculation_lines),
                "### Conclusion tecnica",
                analysis.get("legalConclusion") or "La conclusion tecnica depende del analisis completo registrado.",
            ]
        )
        return self._output(markdown, input_payload, confidence=analysis.get("confidenceGlobal"))

    def simplify_legal_language(self, input_payload: dict[str, Any]) -> AiSectionOutput:
        conclusion = (input_payload.get("analysis") or {}).get("legalConclusion")
        text = conclusion or "El expediente debe revisarse con los soportes disponibles."
        markdown = (
            f"En lenguaje claro: {text} "
            "La recomendacion debe contrastarse con documentos completos y revision profesional cuando aplique."
        )
        return self._output(markdown, input_payload, confidence=(input_payload.get("analysis") or {}).get("confidenceGlobal"))

    def _output(
        self,
        content: str,
        input_payload: dict[str, Any],
        *,
        confidence: Any,
    ) -> AiSectionOutput:
        prompt_hash = _stable_hash(
            {
                "promptVersion": REPORT_PROMPT_VERSION,
                "task": "controlled_report_writing",
            }
        )
        input_hash = _stable_hash(input_payload)
        output_hash = _stable_hash({"content": content})
        return AiSectionOutput(
            content_markdown=content,
            confidence=float(confidence or 80.0),
            metadata={
                "provider": self.provider,
                "model": self.model,
                "promptVersion": REPORT_PROMPT_VERSION,
                "promptHash": prompt_hash,
                "inputHash": input_hash,
                "outputHash": output_hash,
            },
        )


def build_report_ai_client() -> ReportAiClient:
    provider = (settings.AI_PROVIDER or "mock").strip().lower()
    client = DeterministicReportAiClient()
    if provider not in {"", "mock", "deterministic"}:
        object.__setattr__(client, "provider", provider)
        object.__setattr__(client, "model", settings.AI_MODEL or "configured-report-model")
    return client


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _first_amount(calculations: list[dict[str, Any]]) -> str | None:
    for code in ("RETROACTIVE_ESTIMATE_001", "ECONOMIC_DIFFERENCE_001", "CORRECT_ESTIMATED_AMOUNT_001"):
        for item in calculations:
            if item.get("calculationCode") == code and item.get("resultValue") is not None:
                return str(item["resultValue"])
    return None


def _missing_documents(inconsistencies: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in inconsistencies:
        for document in item.get("missingDocuments") or []:
            if document and str(document) not in values:
                values.append(str(document))
    return values
