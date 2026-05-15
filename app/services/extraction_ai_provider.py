import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from app.core.config import settings


class AiExtractionProviderError(Exception):
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


@dataclass(frozen=True)
class ExtractionProviderResult:
    provider: str
    model: str
    output: dict[str, list[dict[str, Any]]]
    latency_ms: int


class AiExtractionProvider(Protocol):
    provider_name: str
    model: str

    def extract_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        ...

    def normalize_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        ...

    def detect_gaps_and_duplicates(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        ...


EMPTY_EXTRACTION_OUTPUT: dict[str, list[dict[str, Any]]] = {
    "employers": [],
    "laborPeriods": [],
    "contributionWeeks": [],
    "salaryBases": [],
    "gaps": [],
    "novelties": [],
    "fields": [],
    "issues": [],
}


class NullExtractionProvider:
    provider_name = "none"
    model = "none"

    def extract_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return self._empty()

    def normalize_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return self._empty()

    def detect_gaps_and_duplicates(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return self._empty()

    def _empty(self) -> ExtractionProviderResult:
        return ExtractionProviderResult(
            provider=self.provider_name,
            model=self.model,
            output={key: list(value) for key, value in EMPTY_EXTRACTION_OUTPUT.items()},
            latency_ms=0,
        )


class MockExtractionProvider:
    provider_name = "mock"
    model = "mock-labor-extraction-v1"

    def extract_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        started = time.perf_counter()
        text = _input_text(input_payload)
        document_id = _first_document_id(input_payload)
        employer_name = _extract_employer_name(text) or "Empleador Por Validar"
        confidence = 0.72 if employer_name == "Empleador Por Validar" else 0.88
        output = {
            **EMPTY_EXTRACTION_OUTPUT,
            "employers": [
                {
                    "name": employer_name,
                    "rawName": employer_name.upper(),
                    "nit": None,
                    "employerType": "unknown",
                    "confidence": confidence,
                    "source": "ai",
                }
            ],
            "laborPeriods": [
                {
                    "employerName": employer_name,
                    "startDate": "2000-01-01",
                    "endDate": "2000-12-31",
                    "periodType": "reported",
                    "regimeHint": "unknown",
                    "weeksDetected": 52,
                    "confidence": confidence,
                    "sourceDocumentId": document_id,
                    "sourcePage": 1,
                }
            ],
            "contributionWeeks": [
                {
                    "year": 2000,
                    "weeks": 52,
                    "source": "document",
                    "confidence": confidence,
                }
            ],
            "fields": [
                {
                    "entityType": "employer",
                    "fieldKey": "name",
                    "value": employer_name,
                    "rawValue": employer_name.upper(),
                    "confidence": confidence,
                    "sourceDocumentId": document_id,
                    "sourcePage": 1,
                    "sourceText": _source_excerpt(text),
                    "extractionMethod": "ai",
                },
                {
                    "entityType": "labor_period",
                    "fieldKey": "start_date",
                    "value": "2000-01-01",
                    "rawValue": "2000-01-01",
                    "confidence": confidence,
                    "sourceDocumentId": document_id,
                    "sourcePage": 1,
                    "sourceText": _source_excerpt(text),
                    "extractionMethod": "ai",
                },
            ],
            "issues": [],
        }
        if confidence < settings.ai_extraction_confidence_low:
            output["issues"].append(
                {
                    "type": "low_confidence",
                    "severity": "medium",
                    "message": "La extraccion mock requiere revision por baja confianza.",
                }
            )
        return ExtractionProviderResult(
            provider=self.provider_name,
            model=self.model,
            output=output,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def normalize_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return self.extract_labor_data(input_payload)

    def detect_gaps_and_duplicates(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return ExtractionProviderResult(
            provider=self.provider_name,
            model=self.model,
            output={key: list(value) for key, value in EMPTY_EXTRACTION_OUTPUT.items()},
            latency_ms=0,
        )


class OpenAiCompatibleExtractionProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        model: str,
        api_key: str,
    ) -> None:
        if not api_key:
            raise AiExtractionProviderError("AI_PROVIDER_NOT_CONFIGURED", "Falta API key de extraccion IA.")
        if not model:
            raise AiExtractionProviderError("AI_PROVIDER_NOT_CONFIGURED", "Falta modelo de extraccion IA.")
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/") or _default_base_url(provider_name)
        self.model = model
        self.api_key = api_key

    def extract_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return self._chat_json(
            task="extract_labor_data",
            input_payload=input_payload,
        )

    def normalize_labor_data(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return self._chat_json(
            task="normalize_labor_data",
            input_payload=input_payload,
        )

    def detect_gaps_and_duplicates(self, input_payload: dict[str, Any]) -> ExtractionProviderResult:
        return self._chat_json(
            task="detect_gaps_and_duplicates",
            input_payload=input_payload,
        )

    def _chat_json(
        self,
        *,
        task: str,
        input_payload: dict[str, Any],
    ) -> ExtractionProviderResult:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": _messages(task, input_payload),
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
                timeout=settings.ai_extraction_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise AiExtractionProviderError(
                "AI_PROVIDER_TIMEOUT",
                "El proveedor IA no respondio a tiempo.",
            ) from exc
        except requests.RequestException as exc:
            raise AiExtractionProviderError(
                "AI_PROVIDER_ERROR",
                "No fue posible comunicarse con el proveedor IA.",
            ) from exc
        if response.status_code >= 400:
            raise AiExtractionProviderError(
                "AI_PROVIDER_ERROR",
                "El proveedor IA rechazo la solicitud de extraccion.",
                details={"statusCode": response.status_code, "body": response.text[:1000]},
            )
        try:
            raw_content = response.json()["choices"][0]["message"]["content"]
            output = _coerce_output(json.loads(raw_content))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AiExtractionProviderError(
                "AI_PROVIDER_INVALID_RESPONSE",
                "El proveedor IA devolvio una extraccion invalida.",
            ) from exc
        return ExtractionProviderResult(
            provider=self.provider_name,
            model=self.model,
            output=output,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def extraction_provider_factory(provider_name: str | None = None) -> AiExtractionProvider:
    provider = (provider_name or settings.ai_extraction_provider).strip().lower()
    if provider in {"none", "local"}:
        return NullExtractionProvider()
    if provider == "mock":
        return MockExtractionProvider()
    if provider == "deepseek":
        return OpenAiCompatibleExtractionProvider(
            provider_name="deepseek",
            base_url=settings.ai_extraction_base_url or "https://api.deepseek.com",
            model=settings.ai_extraction_model or "deepseek-chat",
            api_key=settings.ai_extraction_api_key,
        )
    if provider == "kimi":
        return OpenAiCompatibleExtractionProvider(
            provider_name="kimi",
            base_url=settings.ai_extraction_base_url or "https://api.moonshot.ai/v1",
            model=settings.ai_extraction_model or "kimi-k2.6",
            api_key=settings.ai_extraction_api_key,
        )
    if provider == "openai":
        return OpenAiCompatibleExtractionProvider(
            provider_name="openai",
            base_url=settings.ai_extraction_base_url or "https://api.openai.com/v1",
            model=settings.ai_extraction_model or "gpt-5.2",
            api_key=settings.ai_extraction_api_key,
        )
    raise AiExtractionProviderError("AI_PROVIDER_NOT_CONFIGURED", "Proveedor IA de extraccion no soportado.")


def _messages(task: str, input_payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Eres un extractor documental para Labora, legal-tech colombiana. "
                "No concluyas viabilidad juridica ni calcules derechos finales. "
                "Devuelve solo JSON estricto con las llaves employers, laborPeriods, "
                "contributionWeeks, salaryBases, gaps, novelties, fields e issues. "
                "Cada dato debe incluir confianza, origen documental si existe y metodo de extraccion."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"task": task, "input": input_payload},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        },
    ]


def _coerce_output(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for key in EMPTY_EXTRACTION_OUTPUT:
        value = payload.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"{key} debe ser una lista.")
        output[key] = [item for item in value if isinstance(item, dict)]
    return output


def _input_text(input_payload: dict[str, Any]) -> str:
    fragments: list[str] = []
    for document in input_payload.get("documents", []):
        for page in document.get("pages", []):
            fragments.append(str(page.get("text") or page.get("textPreview") or ""))
    for answer in input_payload.get("questionnaireAnswers", []):
        fragments.append(str(answer.get("value") or answer.get("valueText") or ""))
    return " ".join(fragments)


def _first_document_id(input_payload: dict[str, Any]) -> str | None:
    documents = input_payload.get("documents", [])
    if not documents:
        return None
    return documents[0].get("id")


def _extract_employer_name(text: str) -> str | None:
    match = re.search(
        r"(?:empleador|empresa)\s*[:\-]\s*([A-Za-z0-9 .&\-]{3,80})",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return " ".join(match.group(1).strip().split())


def _source_excerpt(text: str) -> str | None:
    normalized = " ".join(text.strip().split())
    return normalized[:300] if normalized else None


def _default_base_url(provider_name: str) -> str:
    if provider_name == "deepseek":
        return "https://api.deepseek.com"
    if provider_name == "kimi":
        return "https://api.moonshot.ai/v1"
    return "https://api.openai.com/v1"
