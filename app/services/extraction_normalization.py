import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


CONFIDENCE_HIGH = Decimal("0.85")
CONFIDENCE_LOW = Decimal("0.65")
CONFIDENCE_BLOCKING = Decimal("0.50")


class ExtractionNormalizationError(ValueError):
    pass


def normalize_date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        raise ExtractionNormalizationError("La fecha es obligatoria.")
    raw = str(value).strip()
    if not raw:
        raise ExtractionNormalizationError("La fecha es obligatoria.")
    raw = raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", raw) else raw
    formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ExtractionNormalizationError("La fecha no tiene un formato valido.")


def normalize_money_value(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        raw = str(value or "").strip()
        if not raw:
            raise ExtractionNormalizationError("El valor monetario es obligatorio.")
        cleaned = (
            raw.upper()
            .replace("COP", "")
            .replace("$", "")
            .replace(" ", "")
            .replace("\u00a0", "")
        )
        cleaned = re.sub(r"[^0-9,.\-]", "", cleaned)
        if cleaned.count("-") > 1 or (cleaned.startswith("-") is False and "-" in cleaned):
            raise ExtractionNormalizationError("El valor monetario no es valido.")
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            whole, _, fraction = cleaned.rpartition(",")
            cleaned = f"{whole}.{fraction}" if len(fraction) in {1, 2} else cleaned.replace(",", "")
        elif cleaned.count(".") > 1:
            cleaned = cleaned.replace(".", "")
        elif "." in cleaned:
            whole, _, fraction = cleaned.rpartition(".")
            cleaned = f"{whole}.{fraction}" if len(fraction) in {1, 2} else cleaned.replace(".", "")
        try:
            amount = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ExtractionNormalizationError("El valor monetario no es valido.") from exc
    if amount < 0:
        raise ExtractionNormalizationError("El valor monetario no puede ser negativo.")
    return amount.quantize(Decimal("0.01"))


def normalize_decimal_value(value: Any, *, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ExtractionNormalizationError(f"{field_name} debe ser numerico.") from exc
    if parsed < 0:
        raise ExtractionNormalizationError(f"{field_name} no puede ser negativo.")
    return parsed


def status_for_confidence(confidence: Decimal | float | None) -> str:
    if confidence is None:
        return "extracted"
    parsed = Decimal(str(confidence))
    if parsed < CONFIDENCE_LOW:
        return "low_confidence"
    return "normalized"


def needs_review_for_confidence(confidence: Decimal | float | None) -> bool:
    if confidence is None:
        return False
    return Decimal(str(confidence)) < CONFIDENCE_LOW


def is_blocking_confidence(confidence: Decimal | float | None) -> bool:
    if confidence is None:
        return False
    return Decimal(str(confidence)) < CONFIDENCE_BLOCKING


def validate_labor_period_range(start_date: date, end_date: date | None) -> None:
    if end_date is not None and end_date < start_date:
        raise ExtractionNormalizationError("La fecha fin debe ser mayor o igual a la fecha inicio.")
    if start_date.year < 1900:
        raise ExtractionNormalizationError("La fecha inicio esta fuera del rango permitido.")


def normalize_employer_name(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ExtractionNormalizationError("El nombre del empleador es obligatorio.")
    return normalized


def short_source_text(value: str | None, *, max_length: int = 500) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    return normalized[:max_length]
