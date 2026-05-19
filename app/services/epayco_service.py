import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from app.core.config import settings
from app.models.case import LaboraCase
from app.models.paywall import Paywall, PreviewResult
from app.utils.dates import utc_now


@dataclass(frozen=True)
class EpaycoCheckoutSession:
    session_id: str
    provider_session_token: str | None
    checkout_url: str
    expires_at: datetime
    invoice: str
    provider_payload: dict[str, Any]
    raw_response: dict[str, Any] | None
    provider: str = "epayco"


class EpaycoProviderError(Exception):
    def __init__(self, message: str, *, code: str = "EPAYCO_PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.code = code


class EpaycoCheckoutClient:
    def is_configured(self) -> bool:
        return bool(settings.epayco_public_key and settings.epayco_private_key)

    def create_session(
        self,
        *,
        case: LaboraCase,
        preview: PreviewResult,
        paywall: Paywall,
        return_url: str | None,
        confirmation_url: str | None = None,
    ) -> EpaycoCheckoutSession:
        invoice = epayco_invoice_for_paywall(paywall.id)
        response_url = return_url or f"{settings.frontend_url}/app/cases/{case.id}/preview?payment=return"
        checkout_url = f"{settings.frontend_url}/app/cases/{case.id}/checkout?provider=epayco"
        expires_at = utc_now() + timedelta(hours=1)
        payload = self._payload(
            case=case,
            preview=preview,
            paywall=paywall,
            invoice=invoice,
            response_url=response_url,
            confirmation_url=confirmation_url or settings.epayco_confirmation_url,
        )
        if not self.is_configured():
            return EpaycoCheckoutSession(
                session_id=str(uuid.uuid4()),
                provider_session_token=None,
                checkout_url=checkout_url,
                expires_at=expires_at,
                invoice=invoice,
                provider_payload=payload,
                raw_response=None,
                provider="epayco_unconfigured",
            )

        token = self._login()
        data = self._create_remote_session(token=token, payload=payload)
        session_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        session_id = session_data.get("sessionId")
        if not session_id:
            raise EpaycoProviderError(
                "ePayco no retorno sessionId al crear la sesion.",
                code="EPAYCO_SESSION_INVALID",
            )
        return EpaycoCheckoutSession(
            session_id=str(session_id),
            provider_session_token=session_data.get("token"),
            checkout_url=checkout_url,
            expires_at=expires_at,
            invoice=invoice,
            provider_payload=payload,
            raw_response=data,
        )

    def _login(self) -> str:
        raw_credentials = f"{settings.epayco_public_key}:{settings.epayco_private_key}"
        basic_token = base64.b64encode(raw_credentials.encode("utf-8")).decode("ascii")
        try:
            response = requests.post(
                f"{settings.epayco_api_base_url}/login",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Basic {basic_token}",
                },
                timeout=settings.epayco_checkout_timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise EpaycoProviderError(
                "Timeout autenticando con ePayco.",
                code="EPAYCO_LOGIN_TIMEOUT",
            ) from exc
        except requests.RequestException as exc:
            raise EpaycoProviderError(
                "No fue posible autenticar con ePayco.",
                code="EPAYCO_LOGIN_FAILED",
            ) from exc
        data = response.json()
        token = data.get("token")
        if not token:
            raise EpaycoProviderError(
                "ePayco no retorno token de autenticacion.",
                code="EPAYCO_LOGIN_INVALID",
            )
        return str(token)

    def _create_remote_session(
        self,
        *,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{settings.epayco_api_base_url}/payment/session/create",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=settings.epayco_checkout_timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise EpaycoProviderError(
                "Timeout creando sesion de ePayco.",
                code="EPAYCO_SESSION_TIMEOUT",
            ) from exc
        except requests.RequestException as exc:
            raise EpaycoProviderError(
                "No fue posible crear la sesion de ePayco.",
                code="EPAYCO_SESSION_FAILED",
            ) from exc
        data = response.json()
        if data.get("success") is False:
            raise EpaycoProviderError(
                str(data.get("textResponse") or "ePayco rechazo la creacion de sesion."),
                code="EPAYCO_SESSION_REJECTED",
            )
        return data

    def _payload(
        self,
        *,
        case: LaboraCase,
        preview: PreviewResult,
        paywall: Paywall,
        invoice: str,
        response_url: str,
        confirmation_url: str,
    ) -> dict[str, Any]:
        amount = _checkout_amount(paywall.price_amount)
        payload: dict[str, Any] = {
            "checkout_version": settings.epayco_checkout_version,
            "name": settings.epayco_commerce_name,
            "currency": paywall.price_currency or "COP",
            "amount": amount,
            "description": "Analisis completo de historia laboral Labora",
            "lang": "ES",
            "country": "CO",
            "invoice": invoice,
            "response": response_url,
            "confirmation": confirmation_url,
            "method": "POST",
            "uniqueTransactionPerBill": True,
            "extras": {
                "extra1": str(case.id),
                "extra2": str(paywall.id),
                "extra3": str(preview.id),
                "extra4": paywall.payment_product_code,
            },
        }
        billing = _billing_payload(case)
        if billing:
            payload["billing"] = billing
        return payload


def _checkout_amount(value: Decimal | None) -> float:
    if value is None:
        raise EpaycoProviderError(
            "El monto del checkout no fue configurado.",
            code="EPAYCO_INVALID_AMOUNT",
        )
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EpaycoProviderError(
            "El monto del checkout no es valido.",
            code="EPAYCO_INVALID_AMOUNT",
        ) from exc
    if amount <= 0:
        raise EpaycoProviderError(
            "El monto del checkout debe ser mayor a cero.",
            code="EPAYCO_INVALID_AMOUNT",
        )
    return float(amount)


def epayco_invoice_for_paywall(paywall_id: uuid.UUID) -> str:
    return f"LABORA-{paywall_id.hex}"


def paywall_id_from_epayco_invoice(invoice: str | None) -> uuid.UUID | None:
    if not invoice:
        return None
    normalized = str(invoice).strip()
    if normalized.upper().startswith("LABORA-"):
        normalized = normalized.split("-", 1)[1]
    try:
        return uuid.UUID(hex=normalized)
    except (TypeError, ValueError):
        return None


def epayco_confirmation_signature(payload: dict[str, Any]) -> str | None:
    p_key = settings.epayco_p_key
    if not p_key:
        return None
    values = [
        str(payload.get("x_cust_id_cliente") or settings.epayco_p_cust_id_cliente),
        p_key,
        str(payload.get("x_ref_payco") or ""),
        str(payload.get("x_transaction_id") or ""),
        str(payload.get("x_amount") or payload.get("x_amount_ok") or ""),
        str(payload.get("x_currency_code") or ""),
    ]
    return hashlib.sha256("^".join(values).encode("utf-8")).hexdigest()


def _billing_payload(case: LaboraCase) -> dict[str, str]:
    billing: dict[str, str] = {}
    full_name = f"{case.holder_first_name} {case.holder_last_name}".strip()
    if full_name:
        billing["name"] = full_name[:120]
    if case.holder_email:
        billing["email"] = case.holder_email[:255]
    if case.holder_phone:
        billing["callingCode"] = "+57"
        billing["mobilePhone"] = "".join(ch for ch in case.holder_phone if ch.isdigit())[-10:]
    return billing
