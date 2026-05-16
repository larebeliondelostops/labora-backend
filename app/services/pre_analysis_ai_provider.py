import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings


PROMPT_VERSION = "pre-analysis-v1"
OUTPUT_SCHEMA_VERSION = "pre-analysis-output-v1"


class PreAnalysisProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class PreAnalysisInvalidResponseError(PreAnalysisProviderError):
    pass


class PreIssueOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type: str
    severity: str
    title: str = Field(max_length=180)
    public_summary: str = Field(alias="publicSummary")
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_refs: list[dict[str, Any]] = Field(alias="evidenceRefs", default_factory=list)

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError("severity invalida")
        return value


class MissingDocumentOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    document_type: str = Field(alias="documentType", max_length=80)
    title: str = Field(max_length=180)
    priority: str
    reason: str | None = None
    upload_hint: str | None = Field(alias="uploadHint", default=None)

    @field_validator("priority")
    @classmethod
    def valid_priority(cls, value: str) -> str:
        if value not in {"required", "recommended", "optional"}:
            raise ValueError("priority invalida")
        return value


class CaseSignalOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    signal_type: str = Field(alias="signalType", max_length=80)
    title: str = Field(max_length=180)
    public_summary: str = Field(alias="publicSummary")
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str | None = None
    source_refs: list[dict[str, Any]] = Field(alias="sourceRefs", default_factory=list)


class PreAnalysisAiOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    preliminary_case_type: str = Field(alias="preliminaryCaseType")
    traffic_light: str = Field(alias="trafficLight")
    viability_level: str = Field(alias="viabilityLevel")
    completion_score: float = Field(alias="completionScore", ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    limited_summary: str = Field(alias="limitedSummary")
    value_detected_title: str = Field(alias="valueDetectedTitle", max_length=180)
    value_detected_summary: str = Field(alias="valueDetectedSummary")
    issues: list[PreIssueOutput] = Field(default_factory=list)
    missing_documents: list[MissingDocumentOutput] = Field(
        alias="missingDocuments",
        default_factory=list,
    )
    case_signals: list[CaseSignalOutput] = Field(alias="caseSignals", default_factory=list)

    @field_validator("traffic_light")
    @classmethod
    def valid_traffic_light(cls, value: str) -> str:
        if value not in {"green", "yellow", "red", "gray"}:
            raise ValueError("trafficLight invalido")
        return value

    @field_validator("viability_level")
    @classmethod
    def valid_viability_level(cls, value: str) -> str:
        if value not in {"high", "medium", "low", "insufficient"}:
            raise ValueError("viabilityLevel invalido")
        return value

    @field_validator("preliminary_case_type")
    @classmethod
    def valid_case_type(cls, value: str) -> str:
        allowed = {
            "historia_laboral",
            "reliquidacion_pensional",
            "semanas_no_reconocidas",
            "mora_patronal",
            "omision_afiliacion",
            "regimen_transicion_posible",
            "docente_magisterio_posible",
            "servidor_publico_posible",
            "regimen_especial_posible",
            "informacion_insuficiente",
        }
        if value not in allowed:
            raise ValueError("preliminaryCaseType invalido")
        return value


@dataclass(frozen=True)
class PreAnalysisProviderResult:
    provider: str
    model: str
    output: PreAnalysisAiOutput
    latency_ms: int
    input_hash: str
    raw_response_hash: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None


class PreAnalysisAiProvider(Protocol):
    provider_name: str
    model: str

    def generate_structured_pre_analysis(
        self,
        input_payload: dict[str, Any],
    ) -> PreAnalysisProviderResult:
        ...


class MockPreAnalysisProvider:
    provider_name = "mock"
    model = "mock-pre-analysis-v1"

    def generate_structured_pre_analysis(
        self,
        input_payload: dict[str, Any],
    ) -> PreAnalysisProviderResult:
        started = time.perf_counter()
        extraction = input_payload.get("extractionSummary", {})
        questionnaire = input_payload.get("questionnaireSummary", {})
        document_summary = input_payload.get("documentSummary", {})

        periods_count = int(extraction.get("periodsCount") or 0)
        has_gaps = bool(extraction.get("hasGaps"))
        has_salary_data = bool(extraction.get("hasSalaryData"))
        possible_regime_signals = extraction.get("possibleRegimeSignals") or []
        worked_public = bool(questionnaire.get("workedInPublicSector"))
        was_teacher = bool(questionnaire.get("wasTeacher"))
        has_prior_claim = bool(questionnaire.get("hasPriorClaim"))
        quality_status = document_summary.get("qualityStatus") or "accepted"

        issues: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        missing_documents: list[dict[str, Any]] = []

        if has_gaps or periods_count > 1:
            issues.append(
                {
                    "type": "possible_unrecognized_weeks",
                    "severity": "medium",
                    "title": "Posibles semanas o tiempos por revisar",
                    "publicSummary": "Hay periodos que podrian requerir validacion adicional.",
                    "confidence": 0.82 if has_gaps else 0.74,
                    "evidenceRefs": [],
                }
            )
            signals.append(
                {
                    "signalType": "possible_unrecognized_weeks",
                    "title": "Senales de periodos por validar",
                    "publicSummary": "La informacion disponible muestra periodos que merecen una revision completa.",
                    "confidence": 0.82 if has_gaps else 0.74,
                    "source": "rules",
                }
            )
        if has_salary_data:
            signals.append(
                {
                    "signalType": "possible_salary_inconsistency",
                    "title": "Datos salariales detectados",
                    "publicSummary": "Existen datos salariales que podrian ser utiles en el analisis completo.",
                    "confidence": 0.76,
                    "source": "extraction",
                }
            )
        if worked_public or "servidor_publico_posible" in possible_regime_signals:
            signals.append(
                {
                    "signalType": "possible_public_service_time",
                    "title": "Posible tiempo publico por revisar",
                    "publicSummary": "El cuestionario o los documentos sugieren tiempo publico que conviene validar.",
                    "confidence": 0.78,
                    "source": "questionnaire",
                }
            )
        if was_teacher:
            signals.append(
                {
                    "signalType": "possible_teacher_regime",
                    "title": "Posible historia docente",
                    "publicSummary": "Hay senales generales de historia docente o regimen especial por revisar.",
                    "confidence": 0.77,
                    "source": "questionnaire",
                }
            )
        if has_prior_claim:
            missing_documents.append(
                {
                    "documentType": "reclamacion_previa",
                    "title": "Soporte de reclamacion previa",
                    "priority": "recommended",
                    "reason": "Ayudaria a entender el contexto administrativo del caso.",
                }
            )

        if quality_status == "accepted_with_warnings":
            issues.append(
                {
                    "type": "document_quality_warning",
                    "severity": "low",
                    "title": "Documento con observaciones",
                    "publicSummary": "Algunos soportes fueron aceptados con advertencias de calidad.",
                    "confidence": 0.70,
                    "evidenceRefs": [],
                }
            )
            missing_documents.append(
                {
                    "documentType": "certificacion_laboral",
                    "title": "Certificacion laboral",
                    "priority": "recommended",
                    "reason": "Puede ayudar a validar periodos con baja claridad.",
                }
            )

        if not issues:
            issues.append(
                {
                    "type": "insufficient_information",
                    "severity": "low",
                    "title": "Informacion inicial limitada",
                    "publicSummary": "Aun se requiere contrastar mas soportes para una conclusion completa.",
                    "confidence": 0.66,
                    "evidenceRefs": [],
                }
            )

        signal_count = len(signals)
        confidence = 0.84 if signal_count >= 2 or has_gaps else 0.72 if signal_count else 0.66
        completion_score = min(92, 45 + periods_count * 8 + signal_count * 10)
        if quality_status == "accepted_with_warnings":
            completion_score = min(completion_score, 78)
        if completion_score >= 75 and confidence >= 0.78:
            traffic_light = "yellow"
            viability_level = "medium"
        elif confidence < 0.70:
            traffic_light = "gray"
            viability_level = "insufficient"
        else:
            traffic_light = "yellow"
            viability_level = "medium"

        preliminary_case_type = "semanas_no_reconocidas" if has_gaps else "historia_laboral"
        if was_teacher:
            preliminary_case_type = "docente_magisterio_posible"
        elif worked_public:
            preliminary_case_type = "servidor_publico_posible"

        output = PreAnalysisAiOutput.model_validate(
            {
                "preliminaryCaseType": preliminary_case_type,
                "trafficLight": traffic_light,
                "viabilityLevel": viability_level,
                "completionScore": completion_score,
                "confidence": confidence,
                "limitedSummary": (
                    "Encontramos senales preliminares que podrian justificar una revision mas completa del expediente."
                ),
                "valueDetectedTitle": "Hay senales que merecen revision",
                "valueDetectedSummary": (
                    "Se detectaron posibles inconsistencias generales en periodos o soportes, sin que esto constituya una conclusion final."
                ),
                "issues": issues,
                "missingDocuments": missing_documents,
                "caseSignals": signals,
            }
        )
        raw = output.model_dump_json(by_alias=True)
        return PreAnalysisProviderResult(
            provider=self.provider_name,
            model=self.model,
            output=output,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_hash=_hash_json(input_payload),
            raw_response_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )


class OpenAiCompatiblePreAnalysisProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        model: str,
        api_key: str,
    ) -> None:
        if not api_key:
            raise PreAnalysisProviderError("PRE_ANALYSIS_PROVIDER_NOT_CONFIGURED", "Falta API key IA.")
        if not model:
            raise PreAnalysisProviderError("PRE_ANALYSIS_PROVIDER_NOT_CONFIGURED", "Falta modelo IA.")
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/") or _default_base_url(provider_name)
        self.model = model
        self.api_key = api_key

    def generate_structured_pre_analysis(
        self,
        input_payload: dict[str, Any],
    ) -> PreAnalysisProviderResult:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": build_pre_analysis_messages(input_payload),
            "temperature": settings.ai_temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=settings.ai_pre_analysis_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise PreAnalysisProviderError(
                "PRE_ANALYSIS_PROVIDER_TIMEOUT",
                "El proveedor IA no respondio a tiempo.",
            ) from exc
        except requests.RequestException as exc:
            raise PreAnalysisProviderError(
                "PRE_ANALYSIS_PROVIDER_FAILED",
                "No fue posible comunicarse con el proveedor IA.",
            ) from exc
        if response.status_code >= 400:
            raise PreAnalysisProviderError(
                "PRE_ANALYSIS_PROVIDER_FAILED",
                "El proveedor IA rechazo la solicitud de preanalisis.",
                details={"statusCode": response.status_code, "body": response.text[:1000]},
            )
        try:
            response_json = response.json()
            content = response_json["choices"][0]["message"]["content"]
            output = parse_pre_analysis_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise PreAnalysisInvalidResponseError(
                "PRE_ANALYSIS_SCHEMA_INVALID",
                "El proveedor IA devolvio un preanalisis invalido.",
            ) from exc
        usage = response_json.get("usage") if isinstance(response_json, dict) else {}
        return PreAnalysisProviderResult(
            provider=self.provider_name,
            model=self.model,
            output=output,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_hash=_hash_json(input_payload),
            raw_response_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            tokens_input=usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            tokens_output=usage.get("completion_tokens") if isinstance(usage, dict) else None,
        )


def build_pre_analysis_messages(input_payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Eres un asistente de preanalisis limitado para Labora, legal-tech colombiana. "
                "Debes producir un preanalisis comercial y controlado, no asesoria legal final. "
                "Devuelve solo JSON valido con los campos: preliminaryCaseType, trafficLight, "
                "viabilityLevel, completionScore, confidence, limitedSummary, valueDetectedTitle, "
                "valueDetectedSummary, issues, missingDocuments y caseSignals. "
                "Prohibido calcular retroactivos, liquidaciones, formulas, montos finales, "
                "comparadores de escenarios, fundamentos juridicos extensos, estrategia procesal, "
                "pretensiones finales o recomendacion definitiva de demanda. "
                "No inventes documentos, periodos ni empleadores. No uses datos que no esten en el input. "
                "Si hay baja confianza, expresa incertidumbre y solicita revision humana."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(input_payload, ensure_ascii=False, sort_keys=True, default=str),
        },
    ]


def pre_analysis_provider_factory(provider_name: str | None = None) -> PreAnalysisAiProvider:
    provider = (provider_name or settings.ai_pre_analysis_provider).strip().lower()
    if provider == "mock":
        return MockPreAnalysisProvider()
    if provider == "deepseek":
        return OpenAiCompatiblePreAnalysisProvider(
            provider_name="deepseek",
            base_url=settings.ai_pre_analysis_base_url or "https://api.deepseek.com",
            model=settings.ai_pre_analysis_model or "deepseek-chat",
            api_key=settings.ai_pre_analysis_api_key,
        )
    if provider == "kimi":
        return OpenAiCompatiblePreAnalysisProvider(
            provider_name="kimi",
            base_url=settings.ai_pre_analysis_base_url or "https://api.moonshot.ai/v1",
            model=settings.ai_pre_analysis_model or "kimi-k2.6",
            api_key=settings.ai_pre_analysis_api_key,
        )
    if provider == "openai":
        return OpenAiCompatiblePreAnalysisProvider(
            provider_name="openai",
            base_url=settings.ai_pre_analysis_base_url or "https://api.openai.com/v1",
            model=settings.ai_pre_analysis_model or "gpt-5.2",
            api_key=settings.ai_pre_analysis_api_key,
        )
    raise PreAnalysisProviderError(
        "PRE_ANALYSIS_PROVIDER_NOT_CONFIGURED",
        "Proveedor IA de preanalisis no soportado.",
    )


def parse_pre_analysis_json(raw_content: str) -> PreAnalysisAiOutput:
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise PreAnalysisInvalidResponseError(
            "PRE_ANALYSIS_SCHEMA_INVALID",
            "JSON IA invalido.",
        ) from exc
    try:
        return PreAnalysisAiOutput.model_validate(payload)
    except ValidationError as exc:
        raise PreAnalysisInvalidResponseError(
            "PRE_ANALYSIS_SCHEMA_INVALID",
            "Schema IA invalido.",
        ) from exc


def _hash_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _default_base_url(provider_name: str) -> str:
    if provider_name == "deepseek":
        return "https://api.deepseek.com"
    if provider_name == "kimi":
        return "https://api.moonshot.ai/v1"
    return "https://api.openai.com/v1"
