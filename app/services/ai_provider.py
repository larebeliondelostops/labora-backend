import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.config import settings
from app.services.document_issue_service import build_issue


logger = logging.getLogger(__name__)

ALLOWED_DOCUMENT_TYPES = [
    "historia_laboral",
    "resolucion_pensional",
    "certificacion_laboral",
    "desprendible_nomina",
    "acto_administrativo",
    "otro_soporte_laboral_pensional",
]


class AiProviderError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class AiInvalidResponseError(AiProviderError):
    pass


class AiTimeoutError(AiProviderError):
    pass


class AiRateLimitedError(AiProviderError):
    pass


class AiIssue(BaseModel):
    code: str
    severity: str
    page_number: int | None = Field(default=None, alias="pageNumber")
    title: str
    message: str
    suggested_action: str | None = Field(default=None, alias="suggestedAction")

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, value: str) -> str:
        if value not in {"info", "warning", "critical"}:
            raise ValueError("severity invalida")
        return value


class DocumentClassificationOutput(BaseModel):
    document_type: str = Field(alias="documentType")
    is_labor_or_pension_related: bool = Field(alias="isLaborOrPensionRelated")
    is_suitable_for_preanalysis: bool = Field(alias="isSuitableForPreanalysis")
    traffic_light: str = Field(alias="trafficLight")
    confidence_score: float = Field(alias="confidenceScore", ge=0, le=1)
    summary: str
    detected_signals: list[str] = Field(alias="detectedSignals", default_factory=list)
    issues: list[AiIssue] = Field(default_factory=list)
    recommended_next_action: str = Field(alias="recommendedNextAction", default="continue")

    @field_validator("traffic_light")
    @classmethod
    def valid_traffic_light(cls, value: str) -> str:
        if value not in {"green", "yellow", "red", "gray"}:
            raise ValueError("trafficLight invalido")
        return value


@dataclass(frozen=True)
class AiProviderResult:
    provider: str
    model: str
    task: str
    output: DocumentClassificationOutput
    latency_ms: int
    input_hash: str
    raw_response_hash: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None


class LlmProvider(Protocol):
    provider_name: str
    model: str

    def classify_document(self, input_payload: dict[str, Any]) -> AiProviderResult:
        ...

    def summarize_ocr_quality(self, input_payload: dict[str, Any]) -> AiProviderResult:
        ...


class MockAiProvider:
    provider_name = "mock"
    model = "mock-document-precheck-v1"

    def classify_document(self, input_payload: dict[str, Any]) -> AiProviderResult:
        started = time.perf_counter()
        pages = input_payload.get("ocrSignals", {}).get("pages", [])
        text = " ".join((page.get("textPreview") or "") for page in pages).lower()
        text_detected = bool(input_payload.get("ocrSignals", {}).get("textDetected"))
        avg_density = float(input_payload.get("ocrSignals", {}).get("avgTextDensity") or 0)
        page_issues = [
            build_issue("page_blurry", page_number=page.get("pageNumber"))
            for page in pages
            if page.get("isBlurry")
        ]
        strong_labor_signals = [
            "historia laboral",
            "semanas",
            "cotizadas",
            "colpensiones",
            "periodos laborales",
            "certificacion laboral",
            "resolucion",
            "nomina",
        ]
        related_signals = [
            "laboral",
            "pensional",
            "pension",
            "trabajador",
            "empleador",
            "afiliacion",
        ]
        is_labor = any(signal in text for signal in strong_labor_signals)
        is_ambiguous_related = not is_labor and any(signal in text for signal in related_signals)
        if not text_detected:
            issues = [build_issue("no_text_detected", severity="critical")]
            output = DocumentClassificationOutput.model_validate(
                {
                    "documentType": "otro_soporte_laboral_pensional",
                    "isLaborOrPensionRelated": False,
                    "isSuitableForPreanalysis": False,
                    "trafficLight": "red",
                    "confidenceScore": 0.25,
                    "summary": "No se detecto texto suficiente para el preanalisis documental.",
                    "detectedSignals": [],
                    "issues": [_external_issue(issue) for issue in issues],
                    "recommendedNextAction": "upload_better_scan",
                }
            )
        elif is_ambiguous_related:
            issue = build_issue("human_review_required")
            output = DocumentClassificationOutput.model_validate(
                {
                    "documentType": "otro_soporte_laboral_pensional",
                    "isLaborOrPensionRelated": True,
                    "isSuitableForPreanalysis": False,
                    "trafficLight": "yellow",
                    "confidenceScore": 0.68,
                    "summary": "El documento tiene senales laborales o pensionales, pero requiere validacion humana por ambiguedad documental.",
                    "detectedSignals": _detected_signals(text),
                    "issues": [_external_issue(issue)],
                    "recommendedNextAction": "human_review",
                }
            )
        else:
            confidence = 0.9 if is_labor and avg_density >= 0.35 else 0.72 if is_labor else 0.52
            traffic_light = "green" if confidence >= 0.85 and not page_issues else "yellow" if confidence >= 0.65 else "red"
            output = DocumentClassificationOutput.model_validate(
                {
                    "documentType": "historia_laboral" if is_labor else "otro_soporte_laboral_pensional",
                    "isLaborOrPensionRelated": is_labor,
                    "isSuitableForPreanalysis": confidence >= 0.65,
                    "trafficLight": traffic_light,
                    "confidenceScore": confidence,
                    "summary": (
                        "Documento apto para preanalisis documental."
                        if traffic_light == "green"
                        else "Documento con observaciones para revision preliminar."
                    ),
                    "detectedSignals": _detected_signals(text),
                    "issues": [_external_issue(issue) for issue in page_issues],
                    "recommendedNextAction": "continue" if confidence >= 0.65 else "human_review",
                }
            )
        raw = output.model_dump_json(by_alias=True)
        return AiProviderResult(
            provider=self.provider_name,
            model=self.model,
            task="classify_document",
            output=output,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_hash=_hash_json(input_payload),
            raw_response_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )

    def summarize_ocr_quality(self, input_payload: dict[str, Any]) -> AiProviderResult:
        return self.classify_document(input_payload)


class OpenAiCompatibleProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        model: str,
        api_key: str,
    ) -> None:
        if not api_key:
            raise AiProviderError("AI_PROVIDER_NOT_CONFIGURED", "Falta AI_API_KEY.")
        if not base_url:
            raise AiProviderError("AI_PROVIDER_NOT_CONFIGURED", "Falta AI_BASE_URL.")
        if not model:
            raise AiProviderError("AI_PROVIDER_NOT_CONFIGURED", "Falta AI_MODEL.")
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def classify_document(self, input_payload: dict[str, Any]) -> AiProviderResult:
        started = time.perf_counter()
        messages = build_classification_messages(input_payload)
        context = {
            **_request_log_context(input_payload),
            "ai_provider": self.provider_name,
            "ai_model": self.model,
        }
        raw_payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.ai_temperature,
        }
        response_format_enabled = settings.ai_json_mode and _supports_json_object_response_format(
            provider_name=self.provider_name,
            model=self.model,
        )
        if response_format_enabled:
            raw_payload["response_format"] = {"type": "json_object"}

        logger.info(
            "Sending AI classification request",
            extra={
                **context,
                "response_format_enabled": response_format_enabled,
                "payload_hash": _hash_json(input_payload),
                "payload_summary": _classification_payload_summary(input_payload),
            },
        )
        try:
            response_json = self._post_chat_completion(raw_payload, context=context)
        except AiProviderError as exc:
            if exc.code == "AI_PROVIDER_JSON_SCHEMA_ERROR" and "response_format" in raw_payload:
                fallback_payload = {key: value for key, value in raw_payload.items() if key != "response_format"}
                logger.warning(
                    "AI provider rejected response_format; retrying without it",
                    extra={
                        **context,
                        "provider_status_code": exc.details.get("statusCode"),
                        "provider_message": exc.details.get("providerMessage"),
                    },
                )
                response_json = self._post_chat_completion(
                    fallback_payload,
                    context={**context, "response_format_fallback": True},
                )
            else:
                raise
        content = _extract_message_content(response_json)
        output = _parse_classification_json(content)
        usage = response_json.get("usage") if isinstance(response_json, dict) else {}
        return AiProviderResult(
            provider=self.provider_name,
            model=self.model,
            task="classify_document",
            output=output,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_hash=_hash_json(input_payload),
            raw_response_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            tokens_input=usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            tokens_output=usage.get("completion_tokens") if isinstance(usage, dict) else None,
        )

    def summarize_ocr_quality(self, input_payload: dict[str, Any]) -> AiProviderResult:
        return self.classify_document(input_payload)

    def _post_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        attempts = max(settings.ai_max_retries, 0) + 1
        last_invalid: AiInvalidResponseError | None = None
        for attempt in range(attempts):
            started = time.perf_counter()
            retry_payload = payload
            if attempt > 0 and last_invalid is not None:
                retry_payload = {
                    **payload,
                    "messages": [
                        *payload["messages"],
                        {
                            "role": "user",
                            "content": "La respuesta anterior no cumplio el schema JSON. Devuelve solo JSON valido con todos los campos requeridos.",
                        },
                    ],
                }
            try:
                response = requests.post(
                    url,
                    json=retry_payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=settings.ai_timeout_seconds,
                )
            except requests.Timeout as exc:
                raise AiTimeoutError(
                    "AI_PROVIDER_TIMEOUT",
                    "El proveedor IA no respondio a tiempo.",
                    details={**context, "attempt": attempt + 1},
                ) from exc
            except requests.RequestException as exc:
                raise AiProviderError(
                    "AI_PROVIDER_ERROR",
                    "No fue posible comunicarse con el proveedor IA.",
                    details={**context, "attempt": attempt + 1},
                ) from exc
            latency_ms = int((time.perf_counter() - started) * 1000)
            if response.status_code == 429:
                details = {**_provider_response_details(response), **context, "attempt": attempt + 1}
                _log_provider_error(details=details, latency_ms=latency_ms)
                raise AiRateLimitedError(
                    "AI_PROVIDER_RATE_LIMITED",
                    "El proveedor IA limito la solicitud.",
                    details=details,
                )
            if response.status_code >= 500:
                details = {**_provider_response_details(response), **context, "attempt": attempt + 1}
                _log_provider_error(details=details, latency_ms=latency_ms)
                raise AiProviderError(
                    "AI_PROVIDER_UNAVAILABLE",
                    "El proveedor IA no esta disponible.",
                    details=details,
                )
            if response.status_code >= 400:
                details = {**_provider_response_details(response), **context, "attempt": attempt + 1}
                _log_provider_error(details=details, latency_ms=latency_ms)
                code = _provider_error_code(response.status_code, details)
                raise AiProviderError(
                    code,
                    _provider_error_message(code, details),
                    details=details,
                )
            try:
                response_json = response.json()
                content = _extract_message_content(response_json)
                _parse_classification_json(content)
                usage = response_json.get("usage") if isinstance(response_json, dict) else {}
                logger.info(
                    "AI provider HTTP response accepted",
                    extra={
                        **context,
                        "provider_status_code": response.status_code,
                        "latency_ms": latency_ms,
                        "tokens_input": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                        "tokens_output": usage.get("completion_tokens") if isinstance(usage, dict) else None,
                        "attempt": attempt + 1,
                    },
                )
                return response_json
            except (ValueError, ValidationError, AiInvalidResponseError) as exc:
                last_invalid = AiInvalidResponseError(
                    "AI_PROVIDER_INVALID_RESPONSE",
                    "El proveedor IA devolvio JSON invalido.",
                    details={**context, "attempt": attempt + 1},
                )
                if attempt >= attempts - 1:
                    raise last_invalid from exc
        raise AiInvalidResponseError("AI_PROVIDER_INVALID_RESPONSE", "El proveedor IA devolvio JSON invalido.")


class DeepSeekProvider(OpenAiCompatibleProvider):
    def __init__(self, *, base_url: str, model: str, api_key: str) -> None:
        super().__init__(
            provider_name="deepseek",
            base_url=base_url,
            model=model,
            api_key=api_key,
        )


class KimiProvider(OpenAiCompatibleProvider):
    def __init__(self, *, base_url: str, model: str, api_key: str) -> None:
        super().__init__(
            provider_name="kimi",
            base_url=base_url,
            model=model,
            api_key=api_key,
        )


def build_classification_messages(input_payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Eres un clasificador documental para una plataforma legal-tech colombiana llamada Labora.\n"
                "Tu tarea NO es dar asesoria juridica ni concluir si existe derecho pensional.\n"
                "Solo debes analizar senales documentales preliminares: tipo de documento, legibilidad, calidad OCR, paginas problematicas y confianza.\n"
                "Devuelve exclusivamente JSON valido que cumpla el schema solicitado.\n"
                "No incluyas markdown.\n"
                "No incluyas explicacion fuera del JSON.\n"
                "Si no hay suficiente informacion, usa baja confianza y solicita revision o nueva carga.\n"
                "El JSON debe tener exactamente estos campos principales: documentType, isLaborOrPensionRelated, "
                "isSuitableForPreanalysis, trafficLight, confidenceScore, summary, detectedSignals, issues, "
                "recommendedNextAction.\n"
                "trafficLight solo puede ser green, yellow, red o gray. confidenceScore debe ser numero entre 0 y 1.\n"
                "Cada issue debe tener code, severity, title, message, suggestedAction y opcionalmente pageNumber."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(input_payload, ensure_ascii=False, sort_keys=True),
        },
    ]


def ai_provider_factory() -> LlmProvider:
    provider = settings.ai_provider
    if provider == "mock":
        return MockAiProvider()
    if provider == "deepseek":
        return DeepSeekProvider(
            base_url=settings.ai_base_url or "https://api.deepseek.com",
            model=settings.ai_model or "deepseek-chat",
            api_key=settings.ai_api_key,
        )
    if provider == "kimi":
        return KimiProvider(
            base_url=settings.ai_base_url or "https://api.moonshot.ai/v1",
            model=settings.ai_model or "kimi-k2.6",
            api_key=settings.ai_api_key,
        )
    raise AiProviderError("AI_PROVIDER_NOT_CONFIGURED", "Proveedor IA no soportado.")


def _parse_classification_json(raw_content: str) -> DocumentClassificationOutput:
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise AiInvalidResponseError("AI_PROVIDER_INVALID_RESPONSE", "JSON IA invalido.") from exc
    try:
        return DocumentClassificationOutput.model_validate(payload)
    except ValidationError as exc:
        raise AiInvalidResponseError("AI_PROVIDER_INVALID_RESPONSE", "Schema IA invalido.") from exc


def _extract_message_content(response_json: dict[str, Any]) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiInvalidResponseError("AI_PROVIDER_INVALID_RESPONSE", "Respuesta IA sin contenido.") from exc
    if not isinstance(content, str) or not content.strip():
        raise AiInvalidResponseError("AI_PROVIDER_INVALID_RESPONSE", "Respuesta IA vacia.")
    return content.strip()


def _hash_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _detected_signals(text: str) -> list[str]:
    signals: list[str] = []
    for needle, label in [
        ("historia laboral", "historia laboral"),
        ("semanas", "semanas cotizadas"),
        ("colpensiones", "fondo pensional"),
        ("periodos", "periodos laborales"),
        ("certificacion", "certificacion laboral"),
        ("nomina", "nomina"),
    ]:
        if needle in text:
            signals.append(label)
    return sorted(set(signals))


def _external_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": issue["code"],
        "severity": issue["severity"],
        "pageNumber": issue.get("page_number"),
        "title": issue["title"],
        "message": issue["message"],
        "suggestedAction": issue.get("suggested_action"),
    }


def _provider_error_code(status_code: int, details: dict[str, Any]) -> str:
    haystack = " ".join(
        str(details.get(key, ""))
        for key in ("responseBody", "providerMessage")
    ).lower()
    if status_code == 413 or any(
        needle in haystack
        for needle in ("maximum context", "context length", "token limit", "too many tokens", "tokens")
    ):
        return "AI_PROVIDER_TOKEN_LIMIT"
    if status_code == 400 and any(
        needle in haystack
        for needle in ("response_format", "json_schema", "json schema", "schema", "json mode")
    ):
        return "AI_PROVIDER_JSON_SCHEMA_ERROR"
    if status_code in {401, 403}:
        return "AI_PROVIDER_AUTH_ERROR"
    if status_code == 402:
        return "AI_PROVIDER_BILLING_ERROR"
    if status_code == 404:
        return "AI_PROVIDER_MODEL_NOT_FOUND"
    if status_code == 400:
        return "AI_PROVIDER_BAD_REQUEST"
    return "AI_PROVIDER_ERROR"


def _provider_error_message(code: str, details: dict[str, Any]) -> str:
    provider_message = details.get("providerMessage")
    if code == "AI_PROVIDER_AUTH_ERROR":
        return provider_message or "El proveedor IA rechazo las credenciales configuradas."
    if code == "AI_PROVIDER_BILLING_ERROR":
        return provider_message or "El proveedor IA rechazo la solicitud por facturacion o saldo."
    if code == "AI_PROVIDER_MODEL_NOT_FOUND":
        return provider_message or "El proveedor IA no encontro el modelo o endpoint configurado."
    if code == "AI_PROVIDER_TOKEN_LIMIT":
        return provider_message or "El proveedor IA rechazo la solicitud por limite de tokens."
    if code == "AI_PROVIDER_JSON_SCHEMA_ERROR":
        return provider_message or "El proveedor IA rechazo el formato JSON solicitado."
    if code == "AI_PROVIDER_BAD_REQUEST":
        return provider_message or "El proveedor IA rechazo el payload de clasificacion."
    return provider_message or "El proveedor IA rechazo la solicitud."


def _provider_response_details(response) -> dict[str, Any]:
    body = _safe_response_body(response)
    details: dict[str, Any] = {
        "statusCode": response.status_code,
        "responseBody": body,
    }
    provider_message = _provider_message_from_body(response, body)
    if provider_message:
        details["providerMessage"] = provider_message
    return details


def _provider_message_from_body(response, fallback_body: str) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return fallback_body[:300] if fallback_body else None
    if not isinstance(payload, dict):
        return fallback_body[:300] if fallback_body else None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or error.get("code")
        return str(message)[:300] if message else None
    if isinstance(error, str):
        return error[:300]
    message = payload.get("message")
    return str(message)[:300] if message else None


def _safe_response_body(response) -> str:
    try:
        text = response.text
    except Exception:
        return ""
    return text[:1200]


def _request_log_context(input_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": input_payload.get("requestId"),
        "case_id": input_payload.get("caseId"),
        "document_id": input_payload.get("documentId"),
        "precheck_id": input_payload.get("precheckId"),
    }


def _classification_payload_summary(input_payload: dict[str, Any]) -> dict[str, Any]:
    pages = input_payload.get("ocrSignals", {}).get("pages", [])
    return {
        "textDetected": input_payload.get("ocrSignals", {}).get("textDetected"),
        "avgTextDensity": input_payload.get("ocrSignals", {}).get("avgTextDensity"),
        "pagesTotal": input_payload.get("fileMetadata", {}).get("pagesTotal"),
        "pagesSent": len(pages) if isinstance(pages, list) else 0,
        "totalPreviewChars": sum(len((page.get("textPreview") or "")) for page in pages if isinstance(page, dict)),
        "pagePreviews": [
            {
                "pageNumber": page.get("pageNumber"),
                "previewChars": len(page.get("textPreview") or ""),
                "previewHash": hashlib.sha256((page.get("textPreview") or "").encode("utf-8")).hexdigest()
                if page.get("textPreview")
                else None,
                "truncated": page.get("textPreviewTruncated"),
            }
            for page in pages
            if isinstance(page, dict)
        ],
    }


def _supports_json_object_response_format(*, provider_name: str, model: str) -> bool:
    normalized_model = model.strip().lower()
    if provider_name == "deepseek" and "reasoner" in normalized_model:
        return False
    return True


def _log_provider_error(*, details: dict[str, Any], latency_ms: int) -> None:
    logger.warning(
        "AI provider HTTP response rejected",
        extra={
            "request_id": details.get("request_id"),
            "case_id": details.get("case_id"),
            "document_id": details.get("document_id"),
            "precheck_id": details.get("precheck_id"),
            "ai_provider": details.get("ai_provider"),
            "ai_model": details.get("ai_model"),
            "provider_status_code": details.get("statusCode"),
            "provider_message": details.get("providerMessage"),
            "provider_response_body": details.get("responseBody"),
            "latency_ms": latency_ms,
            "attempt": details.get("attempt"),
        },
    )
