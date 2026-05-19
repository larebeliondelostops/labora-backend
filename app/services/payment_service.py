import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import LaboraCase
from app.models.paywall import Paywall, PreviewResult
from app.models.payment import Order, Payment, PaymentTransaction, Receipt
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.paywall_repository import PaywallRepository
from app.services.case_service import step_for_status
from app.services.consent_service import ConsentComplianceService
from app.services.epayco_service import (
    EpaycoCheckoutClient,
    EpaycoProviderError,
    epayco_confirmation_signature,
    paywall_id_from_epayco_invoice,
)
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
PRODUCT_CODE = "FULL_ANALYSIS_UNLOCK"
PRODUCT_NAME = "Desbloqueo de analisis completo"
PRODUCT_DESCRIPTION = "Acceso al analisis completo del expediente Labora."
LEGACY_PAYWALL_PRODUCT_CODE = "ANALISIS_COMPLETO_HISTORIA_LABORAL"
ELIGIBLE_CASE_STATUSES = {
    "preanalysis_ready",
    "preview_locked",
    "payment_not_started",
    "payment_order_created",
    "payment_rejected",
    "payment_failed",
    "payment_expired",
}
LOCKED_CASE_STATUSES = {"closed", "archived", "blocked"}
UNLOCKED_CASE_STATUSES = {
    "paid_unlocked",
    "analysis_in_progress",
    "completed",
    "full_analysis_unlocked",
}
ORDER_PAID_STATUSES = {"paid", "refunded"}
ORDER_CANCELLED_STATUSES = {"cancelled", "failed", "requires_review"}
PAYMENT_TERMINAL_STATUSES = {
    "approved",
    "rejected",
    "failed",
    "cancelled",
    "expired",
    "refunded",
    "chargeback",
    "requires_review",
}
UNLOCKED_FEATURES = {
    "technical_report": True,
    "calculation_breakdown": True,
    "inconsistency_matrix": True,
    "recommended_route": True,
    "legal_document_draft": True,
}


@dataclass(frozen=True)
class ProviderPaymentEvent:
    provider_event_id: str
    provider_payment_id: str | None
    provider_checkout_id: str | None
    event_type: str
    provider_status: str | None
    normalized_status: str
    amount: int | None
    currency: str | None
    invoice: str | None
    paywall_id: uuid.UUID | None
    raw_payload: dict[str, Any]


class PaymentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.paywalls = PaywallRepository(db)
        self.payments = PaymentRepository(db)
        self.audit_events = AuditEventRepository(db)

    def create_order(
        self,
        case_id: str,
        *,
        product_code: str,
        return_url: str | None,
        cancel_url: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if product_code != PRODUCT_CODE:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="PRODUCT_NOT_AVAILABLE",
                message="El producto solicitado no esta disponible para pago.",
            )
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_case_eligible_for_order(case)
        self._require_consents(case.owner_user_id)
        preview, paywall = self._ensure_paywall_ready(
            case=case,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        price_amount = self._unlock_price()
        price_currency = self._currency()

        existing_order = self.payments.active_order_for_case(
            case_id=case.id,
            product_code=PRODUCT_CODE,
        )
        if (
            existing_order is not None
            and not self._is_expired(existing_order)
            and existing_order.total_amount == price_amount
            and existing_order.currency == price_currency
        ):
            self._audit(
                "pago_desbloqueo.viewed",
                actor=user,
                case=case,
                order=existing_order,
                payment=None,
                metadata={"reusedActiveOrder": True},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"order": self._order_payload(existing_order)}
        if existing_order is not None and existing_order.status != "paid":
            existing_order.status = "expired"
            existing_order.updated_at = utc_now()

        now = utc_now()
        order = self.payments.create_order(
            case_id=case.id,
            user_id=case.owner_user_id,
            paywall_id=paywall.id,
            status="created",
            currency=price_currency,
            subtotal_amount=price_amount,
            tax_amount=0,
            discount_amount=0,
            total_amount=price_amount,
            product_code=PRODUCT_CODE,
            product_name=PRODUCT_NAME,
            description=PRODUCT_DESCRIPTION,
            expires_at=now + timedelta(minutes=settings.payment_order_expiration_minutes),
            metadata_json={
                "returnUrl": return_url,
                "cancelUrl": cancel_url,
                "previewId": str(preview.id),
                "paywallId": str(paywall.id),
            },
            created_at=now,
            updated_at=now,
        )
        self._set_case_status(
            case,
            new_status="payment_order_created",
            reason="Orden de desbloqueo creada.",
            source_module="payments",
            metadata={"orderId": str(order.id), "productCode": PRODUCT_CODE},
        )
        self._record_internal_event(
            event_name="payment.order_created",
            case_id=case.id,
            user_id=case.owner_user_id,
            metadata={"orderId": str(order.id), "productCode": PRODUCT_CODE},
        )
        self._audit(
            "pago_desbloqueo.created",
            actor=user,
            case=case,
            order=order,
            payment=None,
            new_state=self._order_state(order),
            metadata={"previewId": str(preview.id), "paywallId": str(paywall.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(order)
        return {"order": self._order_payload(order)}

    def create_checkout(
        self,
        *,
        order_id: str,
        payment_method: str,
        customer: dict[str, Any],
        return_url: str | None = None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
        allow_retry: bool = False,
        reuse_existing: bool = True,
    ) -> dict[str, Any]:
        order = self._get_order_or_404(order_id)
        case = self._get_case_or_404(order.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_order_access(order, user)
        self._require_order_can_checkout(order, allow_retry=allow_retry)
        if reuse_existing:
            existing_payment = self.payments.pending_payment_for_order(order.id)
            if existing_payment is not None and existing_payment.checkout_url:
                self.db.commit()
                return {"payment": self._payment_payload(existing_payment)}

        paywall = self._paywall_for_order(order)
        preview = self._preview_for_paywall(paywall)
        checkout_return_url = return_url or (order.metadata_json or {}).get("returnUrl")
        try:
            checkout_session = EpaycoCheckoutClient().create_session(
                case=case,
                preview=preview,
                paywall=paywall,
                return_url=checkout_return_url,
                confirmation_url=self._provider_webhook_url("epayco"),
            )
        except EpaycoProviderError as exc:
            order.status = "failed"
            order.updated_at = utc_now()
            self._audit(
                "pago_desbloqueo.failed",
                actor=user,
                case=case,
                order=order,
                payment=None,
                metadata={"reason": exc.code},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="PROVIDER_CHECKOUT_FAILED",
                message="No fue posible iniciar el checkout con el proveedor de pago.",
                details={"reason": exc.code},
            ) from exc

        now = utc_now()
        payment = self.payments.create_payment(
            order_id=order.id,
            case_id=order.case_id,
            user_id=order.user_id,
            provider="epayco",
            provider_payment_id=None,
            provider_checkout_id=checkout_session.session_id,
            idempotency_key=f"checkout:{order.id}:{uuid.uuid4()}",
            status="pending",
            provider_status="checkout_started",
            currency=order.currency,
            amount=order.total_amount,
            payment_method=payment_method,
            checkout_url=checkout_session.checkout_url,
            return_url=checkout_return_url,
            expires_at=checkout_session.expires_at,
            raw_provider_payload={
                "checkoutPayload": _json_safe(checkout_session.provider_payload),
                "providerResponse": _json_safe(checkout_session.raw_response),
                "customer": _safe_customer_metadata(customer),
            },
            created_at=now,
            updated_at=now,
        )
        previous_order_state = self._order_state(order)
        order.status = "checkout_started"
        order.updated_at = now
        paywall.checkout_url = checkout_session.checkout_url
        paywall.updated_at = now
        self._set_case_status(
            case,
            new_status="payment_pending",
            reason="Checkout de pago iniciado.",
            source_module="payments",
            metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
        )
        self._record_internal_event(
            event_name="payment.checkout_started",
            case_id=case.id,
            user_id=order.user_id,
            metadata={
                "orderId": str(order.id),
                "paymentId": str(payment.id),
                "provider": payment.provider,
            },
        )
        self._audit(
            "pago_desbloqueo.submitted",
            actor=user,
            case=case,
            order=order,
            payment=payment,
            previous_state=previous_order_state,
            new_state={"order": self._order_state(order), "payment": self._payment_state(payment)},
            metadata={
                "provider": payment.provider,
                "providerCheckoutId": payment.provider_checkout_id,
                "checkoutInvoice": checkout_session.invoice,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(payment)
        return {"payment": self._payment_payload(payment)}

    def get_payment_status(
        self,
        payment_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        payment = self._get_payment_or_404(payment_id)
        case = self._get_case_or_404(payment.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        receipt = self.payments.receipt_for_payment(payment.id)
        self._audit(
            "pago_desbloqueo.viewed",
            actor=user,
            case=case,
            order=payment.order,
            payment=payment,
            metadata={"surface": "payment_status"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "payment": self._payment_payload(payment),
            "case": {
                "id": str(case.id),
                "paymentStatus": self._case_payment_status(case, payment),
                "unlockStatus": self._case_unlock_status(case),
            },
            "receipt": self._receipt_summary(receipt),
        }

    def handle_webhook(
        self,
        *,
        provider: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        raw_body: bytes,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        normalized_provider = provider.strip().lower()
        signature_valid = self._verify_webhook_signature(
            provider=normalized_provider,
            payload=payload,
            headers=headers,
            raw_body=raw_body,
        )
        provider_event = self._provider_event(
            provider=normalized_provider,
            payload=payload,
            raw_body=raw_body,
        )
        if not signature_valid:
            self._audit(
                "pago_desbloqueo.failed",
                actor=None,
                case=None,
                order=None,
                payment=None,
                metadata={
                    "action": "payment.webhook_invalid",
                    "provider": normalized_provider,
                    "providerEventId": provider_event.provider_event_id,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="WEBHOOK_SIGNATURE_INVALID",
                message="La firma del webhook no es valida.",
            )

        existing = self.payments.transaction_by_event(
            provider=normalized_provider,
            provider_event_id=provider_event.provider_event_id,
        )
        if existing is not None:
            self._audit(
                "pago_desbloqueo.updated",
                actor=None,
                case=None,
                order=None,
                payment=None,
                metadata={
                    "action": "payment.webhook_duplicate",
                    "provider": normalized_provider,
                    "providerEventId": provider_event.provider_event_id,
                    "transactionId": str(existing.id),
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"received": True, "duplicate": True}

        payment, order = self._resolve_payment_and_order(
            provider=normalized_provider,
            provider_event=provider_event,
        )
        transaction = self.payments.create_transaction(
            payment_id=payment.id if payment else None,
            order_id=order.id if order else None,
            provider=normalized_provider,
            provider_event_id=provider_event.provider_event_id,
            provider_payment_id=provider_event.provider_payment_id,
            event_type=provider_event.event_type,
            provider_status=provider_event.provider_status,
            normalized_status=provider_event.normalized_status,
            amount=provider_event.amount,
            currency=provider_event.currency,
            signature_valid=signature_valid,
            processed=False,
            raw_payload=_json_safe(provider_event.raw_payload),
            created_at=utc_now(),
        )
        self._record_internal_event(
            event_name="payment.webhook_received",
            case_id=order.case_id if order else None,
            user_id=order.user_id if order else None,
            metadata={
                "provider": normalized_provider,
                "providerEventId": provider_event.provider_event_id,
                "paymentId": str(payment.id) if payment else None,
                "orderId": str(order.id) if order else None,
            },
        )

        if payment is None or order is None:
            transaction.processed = True
            transaction.processed_at = utc_now()
            self._audit(
                "pago_desbloqueo.failed",
                actor=None,
                case=None,
                order=order,
                payment=payment,
                metadata={
                    "action": "payment.webhook_unmatched",
                    "provider": normalized_provider,
                    "providerEventId": provider_event.provider_event_id,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"received": True, "duplicate": False}

        self._process_transaction(
            transaction=transaction,
            provider_event=provider_event,
            payment=payment,
            order=order,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"received": True, "duplicate": False}

    def get_receipt(
        self,
        order_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        order = self._get_order_or_404(order_id)
        case = self._get_case_or_404(order.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_order_access(order, user)
        if order.status != "paid":
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="RECEIPT_NOT_FOUND",
                message="El comprobante solo esta disponible despues de un pago aprobado.",
            )
        receipt = self.payments.receipt_for_order(order.id)
        if receipt is None:
            payment = self._approved_payment_for_order(order)
            receipt = self._ensure_receipt(order=order, payment=payment)
        self._audit(
            "pago_desbloqueo.receipt_viewed",
            actor=user,
            case=case,
            order=order,
            payment=receipt.payment,
            metadata={"receiptId": str(receipt.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"receipt": self._receipt_payload(receipt)}

    def retry_payment(
        self,
        order_id: str,
        *,
        payment_method: str,
        return_url: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        order = self._get_order_or_404(order_id)
        if order.status == "paid":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ORDER_ALREADY_PAID",
                message="La orden ya fue pagada.",
            )
        if self._is_expired(order):
            now = utc_now()
            order.status = "created"
            order.expires_at = now + timedelta(minutes=settings.payment_order_expiration_minutes)
            order.updated_at = now
        self._audit(
            "pago_desbloqueo.retry_requested",
            actor=user,
            case=self._get_case_or_404(order.case_id),
            order=order,
            payment=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return self.create_checkout(
            order_id=str(order.id),
            payment_method=payment_method,
            customer=self._customer_from_order(order),
            return_url=return_url,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            allow_retry=True,
            reuse_existing=False,
        )

    def get_payment_flow(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        order = self.payments.latest_order_for_case(case_id=case.id, product_code=PRODUCT_CODE)
        payment = self.payments.latest_payment_for_order(order.id) if order else None
        receipt = self.payments.receipt_for_order(order.id) if order else None
        is_unlocked = self._case_is_unlocked(case)
        can_pay = (not is_unlocked) and order is not None and order.status not in ORDER_PAID_STATUSES
        can_retry = (
            not is_unlocked
            and order is not None
            and (payment is None or payment.status in {"rejected", "failed", "expired", "cancelled"})
        )
        return {
            "paymentFlow": {
                "caseId": str(case.id),
                "caseStatus": self._case_payment_status(case, payment),
                "canPay": can_pay,
                "canRetry": can_retry,
                "canContinue": is_unlocked,
                "isUnlocked": is_unlocked,
                "order": self._payment_flow_order(order),
                "payment": self._payment_flow_payment(payment),
                "receipt": self._payment_flow_receipt(receipt),
                "uiMessage": self._payment_flow_message(case, order, payment, receipt),
            }
        }

    def _process_transaction(
        self,
        *,
        transaction: PaymentTransaction,
        provider_event: ProviderPaymentEvent,
        payment: Payment,
        order: Order,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        case = self._get_case_or_404(order.case_id)
        now = utc_now()
        payment.provider_payment_id = provider_event.provider_payment_id or payment.provider_payment_id
        payment.provider_checkout_id = provider_event.provider_checkout_id or payment.provider_checkout_id
        payment.provider_status = provider_event.provider_status
        payment.raw_provider_payload = _json_safe(provider_event.raw_payload)
        payment.updated_at = now
        if provider_event.normalized_status == "approved":
            if not self._payment_amount_matches(order, provider_event):
                self._mark_requires_review(
                    case=case,
                    order=order,
                    payment=payment,
                    transaction=transaction,
                    provider_event=provider_event,
                    reason="El monto o la moneda reportada no coincide con la orden.",
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                return
            self._approve_payment(
                case=case,
                order=order,
                payment=payment,
                transaction=transaction,
                provider_event=provider_event,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return

        payment.status = provider_event.normalized_status
        if provider_event.normalized_status == "rejected":
            payment.rejected_at = now
            order.status = "failed"
            self._set_case_status(
                case,
                new_status="payment_rejected",
                reason="Pago rechazado por el proveedor.",
                source_module="payments",
                metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
            )
        elif provider_event.normalized_status == "failed":
            payment.failed_at = now
            order.status = "failed"
            self._set_case_status(
                case,
                new_status="payment_failed",
                reason="Pago fallido segun el proveedor.",
                source_module="payments",
                metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
            )
        elif provider_event.normalized_status == "expired":
            order.status = "expired"
            self._set_case_status(
                case,
                new_status="payment_expired",
                reason="Pago expirado.",
                source_module="payments",
                metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
            )
        elif provider_event.normalized_status == "pending":
            order.status = "pending"
            self._set_case_status(
                case,
                new_status="payment_pending",
                reason="Pago pendiente de confirmacion.",
                source_module="payments",
                metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
            )
        else:
            order.status = "requires_review"
            self._set_case_status(
                case,
                new_status="payment_requires_review",
                reason="El pago requiere revision manual.",
                source_module="payments",
                metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
            )
        order.updated_at = now
        transaction.payment_id = payment.id
        transaction.order_id = order.id
        transaction.processed = True
        transaction.processed_at = now
        self._record_internal_event(
            event_name=f"payment.{provider_event.normalized_status}",
            case_id=case.id,
            user_id=order.user_id,
            metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
        )
        self._audit(
            f"pago_desbloqueo.{_audit_suffix(provider_event.normalized_status)}",
            actor=None,
            case=case,
            order=order,
            payment=payment,
            new_state={"order": self._order_state(order), "payment": self._payment_state(payment)},
            metadata={"providerEventId": provider_event.provider_event_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _approve_payment(
        self,
        *,
        case: LaboraCase,
        order: Order,
        payment: Payment,
        transaction: PaymentTransaction,
        provider_event: ProviderPaymentEvent,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        now = utc_now()
        previous_order = self._order_state(order)
        previous_payment = self._payment_state(payment)
        if payment.status == "approved" and order.status == "paid":
            transaction.processed = True
            transaction.processed_at = now
            return

        payment.status = "approved"
        payment.amount = provider_event.amount or payment.amount
        payment.currency = provider_event.currency or payment.currency
        payment.approved_at = payment.approved_at or now
        payment.updated_at = now
        order.status = "paid"
        order.paid_at = order.paid_at or now
        order.updated_at = now

        if order.product_code == "PROFESSIONAL_REVIEW":
            self._approve_professional_review_payment(
                case=case,
                order=order,
                payment=payment,
                transaction=transaction,
                provider_event=provider_event,
                previous_order=previous_order,
                previous_payment=previous_payment,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return

        previous_case_status = case.status
        self._set_case_status(
            case,
            new_status="payment_approved",
            reason="Pago aprobado por webhook del proveedor.",
            source_module="payments",
            metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
        )
        unlock_event = self._unlock_full_analysis(
            case=case,
            order=order,
            payment=payment,
            previous_case_status=previous_case_status,
        )
        receipt = self._ensure_receipt(order=order, payment=payment)
        transaction.payment_id = payment.id
        transaction.order_id = order.id
        transaction.processed = True
        transaction.processed_at = now

        self._record_internal_event(
            event_name="payment.approved",
            case_id=case.id,
            user_id=order.user_id,
            metadata={
                "orderId": str(order.id),
                "paymentId": str(payment.id),
                "provider": payment.provider,
                "amount": payment.amount,
                "currency": payment.currency,
            },
        )
        self._record_internal_event(
            event_name="case.full_analysis_unlocked",
            case_id=case.id,
            user_id=order.user_id,
            metadata={
                "orderId": str(order.id),
                "paymentId": str(payment.id),
                "unlockEventId": str(unlock_event.id),
            },
        )
        self._record_internal_event(
            event_name="payment.receipt_issued",
            case_id=case.id,
            user_id=order.user_id,
            metadata={"orderId": str(order.id), "paymentId": str(payment.id), "receiptId": str(receipt.id)},
        )
        self._audit(
            "pago_desbloqueo.approved",
            actor=None,
            case=case,
            order=order,
            payment=payment,
            previous_state={"order": previous_order, "payment": previous_payment},
            new_state={"order": self._order_state(order), "payment": self._payment_state(payment)},
            metadata={"providerEventId": provider_event.provider_event_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            "pago_desbloqueo.unlocked",
            actor=None,
            case=case,
            order=order,
            payment=payment,
            new_state={"caseStatus": case.status, "unlockEventId": str(unlock_event.id)},
            metadata={"eventName": "case.full_analysis_unlocked"},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _approve_professional_review_payment(
        self,
        *,
        case: LaboraCase,
        order: Order,
        payment: Payment,
        transaction: PaymentTransaction,
        provider_event: ProviderPaymentEvent,
        previous_order: dict[str, Any],
        previous_payment: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        from app.services.professional_review_service import ProfessionalReviewService

        now = utc_now()
        ProfessionalReviewService(self.db).confirm_payment_order(order.id, commit=False)
        receipt = self._ensure_receipt(order=order, payment=payment)
        transaction.payment_id = payment.id
        transaction.order_id = order.id
        transaction.processed = True
        transaction.processed_at = now
        self._record_internal_event(
            event_name="payment.approved",
            case_id=case.id,
            user_id=order.user_id,
            metadata={
                "orderId": str(order.id),
                "paymentId": str(payment.id),
                "provider": payment.provider,
                "amount": payment.amount,
                "currency": payment.currency,
                "productCode": order.product_code,
            },
        )
        self._record_internal_event(
            event_name="payment.receipt_issued",
            case_id=case.id,
            user_id=order.user_id,
            metadata={"orderId": str(order.id), "paymentId": str(payment.id), "receiptId": str(receipt.id)},
        )
        self._audit(
            "pago_desbloqueo.approved",
            actor=None,
            case=case,
            order=order,
            payment=payment,
            previous_state={"order": previous_order, "payment": previous_payment},
            new_state={"order": self._order_state(order), "payment": self._payment_state(payment)},
            metadata={"providerEventId": provider_event.provider_event_id, "productCode": order.product_code},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _unlock_full_analysis(
        self,
        *,
        case: LaboraCase,
        order: Order,
        payment: Payment,
        previous_case_status: str,
    ):
        existing = self.payments.unlock_event_for_payment(payment.id)
        if existing is not None:
            if existing.status == "completed":
                return existing
            existing.status = "skipped_already_unlocked" if self._case_is_unlocked(case) else "completed"
            existing.completed_at = existing.completed_at or utc_now()
            return existing

        now = utc_now()
        if not self._case_is_unlocked(case):
            self._set_case_status(
                case,
                new_status="full_analysis_unlocked",
                reason="Analisis completo desbloqueado por pago aprobado.",
                source_module="payments",
                metadata={"orderId": str(order.id), "paymentId": str(payment.id)},
            )
        paywall = self._paywall_for_order(order)
        paywall.status = "completed"
        paywall.unlock_required = False
        paywall.unlocked_at = paywall.unlocked_at or now
        paywall.unlocked_by_payment_id = payment.id
        paywall.updated_at = now
        return self.payments.create_unlock_event(
            case_id=case.id,
            user_id=order.user_id,
            order_id=order.id,
            payment_id=payment.id,
            status="completed",
            unlocked_features=UNLOCKED_FEATURES,
            previous_case_status=previous_case_status,
            new_case_status=case.status,
            reason="Pago aprobado por proveedor.",
            created_at=now,
            completed_at=now,
        )

    def _mark_requires_review(
        self,
        *,
        case: LaboraCase,
        order: Order,
        payment: Payment,
        transaction: PaymentTransaction,
        provider_event: ProviderPaymentEvent,
        reason: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        now = utc_now()
        payment.status = "requires_review"
        payment.updated_at = now
        order.status = "requires_review"
        order.updated_at = now
        transaction.payment_id = payment.id
        transaction.order_id = order.id
        transaction.processed = True
        transaction.processed_at = now
        self._set_case_status(
            case,
            new_status="payment_requires_review",
            reason=reason,
            source_module="payments",
            metadata={
                "orderId": str(order.id),
                "paymentId": str(payment.id),
                "reportedAmount": provider_event.amount,
                "reportedCurrency": provider_event.currency,
            },
        )
        self._audit(
            "pago_desbloqueo.failed",
            actor=None,
            case=case,
            order=order,
            payment=payment,
            new_state={"order": self._order_state(order), "payment": self._payment_state(payment)},
            metadata={"reason": reason, "providerEventId": provider_event.provider_event_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _ensure_receipt(self, *, order: Order, payment: Payment) -> Receipt:
        existing = self.payments.receipt_for_payment(payment.id)
        if existing is not None:
            return existing
        now = utc_now()
        receipt = self.payments.create_receipt(
            order_id=order.id,
            payment_id=payment.id,
            case_id=order.case_id,
            user_id=order.user_id,
            receipt_number=self.payments.next_receipt_number(now.year),
            status="issued",
            currency=order.currency,
            total_amount=order.total_amount,
            issued_at=now,
            pdf_url=None,
            metadata_json={"productCode": order.product_code},
            created_at=now,
            updated_at=now,
        )
        self._audit(
            "pago_desbloqueo.receipt_issued",
            actor=None,
            case=self._get_case_or_404(order.case_id),
            order=order,
            payment=payment,
            new_state={"receiptId": str(receipt.id), "receiptNumber": receipt.receipt_number},
            ip_address=None,
            user_agent=None,
        )
        return receipt

    def _ensure_paywall_ready(
        self,
        *,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[PreviewResult, Paywall]:
        paywall = self.paywalls.latest_paywall_for_case(case.id)
        if paywall is None:
            from app.services.paywall_service import PaywallPreviewService

            PaywallPreviewService(self.db).create_or_refresh(
                str(case.id),
                force_refresh=False,
                reason="payment_order",
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            paywall = self.paywalls.latest_paywall_for_case(case.id)
        if paywall is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_ELIGIBLE_FOR_PAYMENT",
                message="El expediente aun no tiene una vista previa disponible para pago.",
            )
        preview = self._preview_for_paywall(paywall)
        if preview.status == "requires_review" or preview.requires_human_review or paywall.status == "requires_review":
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="CASE_NOT_ELIGIBLE_FOR_PAYMENT",
                message="El expediente requiere revision interna antes de pagar.",
                details={"caseId": str(case.id), "previewId": str(preview.id)},
            )
        self._ensure_paywall_pricing(paywall)
        return preview, paywall

    def _provider_event(
        self,
        *,
        provider: str,
        payload: dict[str, Any],
        raw_body: bytes,
    ) -> ProviderPaymentEvent:
        if provider == "epayco":
            return self._epayco_event(payload=payload, raw_body=raw_body)
        provider_payment_id = _first_string(payload, ["provider_payment_id", "payment_id", "id"])
        provider_event_id = _first_string(payload, ["event_id", "id", "reference"]) or _stable_hash(raw_body, payload)
        provider_status = _first_string(payload, ["status", "payment_status"])
        return ProviderPaymentEvent(
            provider_event_id=provider_event_id,
            provider_payment_id=provider_payment_id,
            provider_checkout_id=_first_string(payload, ["checkout_id", "session_id"]),
            event_type=_first_string(payload, ["event_type", "type"]) or "payment.webhook",
            provider_status=provider_status,
            normalized_status=_normalize_status(provider_status, None),
            amount=_amount_from_payload(payload, ["amount", "total_amount"]),
            currency=_first_string(payload, ["currency"]),
            invoice=_first_string(payload, ["invoice"]),
            paywall_id=None,
            raw_payload=payload,
        )

    def _epayco_event(self, *, payload: dict[str, Any], raw_body: bytes) -> ProviderPaymentEvent:
        provider_payment_id = _first_string(payload, ["x_ref_payco", "x_transaction_id"])
        invoice = _first_string(payload, ["x_id_invoice", "invoice"])
        paywall_id = paywall_id_from_epayco_invoice(invoice)
        if paywall_id is None:
            paywall_id = _uuid_or_none(payload.get("x_extra2") or payload.get("extra2"))
        response_code = _first_string(payload, ["x_cod_response", "x_transaction_state"])
        provider_status = _first_string(payload, ["x_response", "x_response_reason_text", "x_cod_response"])
        provider_event_id = (
            _first_string(payload, ["x_transaction_id", "x_ref_payco", "x_id_invoice"])
            or _stable_hash(raw_body, payload)
        )
        return ProviderPaymentEvent(
            provider_event_id=provider_event_id,
            provider_payment_id=provider_payment_id,
            provider_checkout_id=_first_string(payload, ["x_session_id", "sessionId"]),
            event_type="payment.confirmation",
            provider_status=provider_status,
            normalized_status=_normalize_status(provider_status, response_code),
            amount=_amount_from_payload(payload, ["x_amount_ok", "x_amount"]),
            currency=_first_string(payload, ["x_currency_code"]),
            invoice=invoice,
            paywall_id=paywall_id,
            raw_payload=payload,
        )

    def _resolve_payment_and_order(
        self,
        *,
        provider: str,
        provider_event: ProviderPaymentEvent,
    ) -> tuple[Payment | None, Order | None]:
        payment = self.payments.payment_by_provider_id(
            provider=provider,
            provider_payment_id=provider_event.provider_payment_id,
        )
        order = payment.order if payment is not None else None
        if order is None and provider_event.paywall_id is not None:
            order = self.payments.order_by_paywall_id(provider_event.paywall_id)
            if order is not None:
                payment = self.payments.latest_payment_for_order(order.id)
        if order is None:
            order_id = _uuid_or_none(provider_event.raw_payload.get("orderId") or provider_event.raw_payload.get("order_id"))
            order = self.payments.get_order(order_id) if order_id else None
            if order is not None and payment is None:
                payment = self.payments.latest_payment_for_order(order.id)
        return payment, order

    def _verify_webhook_signature(
        self,
        *,
        provider: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        raw_body: bytes,
    ) -> bool:
        if provider == "epayco":
            expected_signature = epayco_confirmation_signature(payload)
            if not expected_signature:
                return True
            return hmac.compare_digest(str(payload.get("x_signature") or ""), expected_signature)
        secret = settings.payment_provider_webhook_secret
        if not secret:
            return True
        received = headers.get("x-webhook-signature") or headers.get("x-signature") or ""
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(received, digest)

    def _require_case_eligible_for_order(self, case: LaboraCase) -> None:
        if case.status in LOCKED_CASE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="CASE_NOT_ELIGIBLE_FOR_PAYMENT",
                message="El expediente esta bloqueado para pago.",
            )
        if self._case_is_unlocked(case):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_ALREADY_UNLOCKED",
                message="El analisis completo ya esta desbloqueado.",
            )
        if case.status not in ELIGIBLE_CASE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_ELIGIBLE_FOR_PAYMENT",
                message="El expediente aun no esta listo para pago.",
                details={"caseId": str(case.id), "status": case.status},
            )

    def _require_order_can_checkout(self, order: Order, *, allow_retry: bool) -> None:
        if order.status in ORDER_PAID_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ORDER_ALREADY_PAID",
                message="La orden ya fue pagada.",
            )
        blocked_statuses = ORDER_CANCELLED_STATUSES - {"failed"} if allow_retry else ORDER_CANCELLED_STATUSES
        if order.status in blocked_statuses:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ORDER_NOT_PAYABLE",
                message="La orden no puede iniciar checkout desde su estado actual.",
            )
        if self._is_expired(order):
            order.status = "expired"
            order.updated_at = utc_now()
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ORDER_EXPIRED",
                message="La orden esta vencida.",
            )

    def _payment_amount_matches(self, order: Order, provider_event: ProviderPaymentEvent) -> bool:
        if provider_event.amount is not None and provider_event.amount != order.total_amount:
            return False
        if provider_event.currency and provider_event.currency.upper() != order.currency.upper():
            return False
        return True

    def _approved_payment_for_order(self, order: Order) -> Payment:
        for payment in self.payments.payments_for_order(order.id):
            if payment.status == "approved":
                return payment
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PAYMENT_NOT_FOUND",
            message="No se encontro un pago aprobado para la orden.",
        )

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_order_or_404(self, order_id: str | uuid.UUID) -> Order:
        order = self.payments.get_order(order_id)
        if order is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ORDER_NOT_FOUND",
                message="Orden no encontrada.",
            )
        return order

    def _get_payment_or_404(self, payment_id: str | uuid.UUID) -> Payment:
        payment = self.payments.get_payment(payment_id)
        if payment is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="PAYMENT_NOT_FOUND",
                message="Pago no encontrado.",
            )
        return payment

    def _paywall_for_order(self, order: Order) -> Paywall:
        paywall = self.db.get(Paywall, order.paywall_id) if order.paywall_id else None
        if paywall is None:
            paywall = self.paywalls.latest_paywall_for_case(order.case_id)
        if paywall is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_ELIGIBLE_FOR_PAYMENT",
                message="No se encontro el paywall asociado a la orden.",
            )
        return paywall

    def _preview_for_paywall(self, paywall: Paywall) -> PreviewResult:
        preview = self.paywalls.get_preview(paywall.preview_result_id)
        if preview is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_ELIGIBLE_FOR_PAYMENT",
                message="No se encontro la vista previa asociada al paywall.",
            )
        return preview

    def _require_order_access(self, order: Order, user: User) -> None:
        if user.role in ADMIN_ROLES or order.user_id == user.id:
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            message="No tienes permisos para operar esta orden.",
        )

    def _require_can_view(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_view(case, user):
            return
        self._audit(
            "pago_desbloqueo.access_denied",
            actor=user,
            case=case,
            order=None,
            payment=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            message="No tienes permisos para acceder a este expediente.",
        )

    def _require_can_update(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_update(case, user):
            return
        self._audit(
            "pago_desbloqueo.access_denied",
            actor=user,
            case=case,
            order=None,
            payment=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            message="No tienes permisos para crear ordenes en este expediente.",
        )

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if user.role in ADMIN_ROLES:
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return case.status == "requires_review" or self.cases.get_owner(
                case_id=case.id,
                user_id=user.id,
                roles={"legal_reviewer"},
            ) is not None
        if case.owner_user_id == user.id:
            return True
        return (
            self.cases.get_owner(
                case_id=case.id,
                user_id=user.id,
                roles={"authorized_user", "creator", "owner"},
            )
            is not None
        )

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if user.role in ADMIN_ROLES:
            return True
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _require_consents(self, user_id: uuid.UUID) -> None:
        permission = ConsentComplianceService(self.db).can_upload_documents(user_id)
        if not permission.allowed:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CONSENT_REQUIRED",
                message="Debes aceptar los consentimientos requeridos antes de pagar.",
                details={
                    "missingConsentTypes": permission.missing_consent_types,
                    "reason": permission.reason,
                },
            )

    def _set_case_status(
        self,
        case: LaboraCase,
        *,
        new_status: str,
        reason: str,
        source_module: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        if case.status == new_status:
            return
        previous_status = case.status
        current_step, next_best_action = step_for_status(new_status)
        case.status = new_status
        case.status_reason = reason
        case.current_step = current_step
        case.next_best_action = next_best_action
        case.updated_at = utc_now()
        self.cases.create_status_history(
            case_id=case.id,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            changed_by_user_id=None,
            changed_by_role="system",
            source_module=source_module,
            metadata=_json_safe(metadata) if metadata else None,
        )

    def _case_is_unlocked(self, case: LaboraCase) -> bool:
        return case.status in UNLOCKED_CASE_STATUSES

    def _is_expired(self, order: Order) -> bool:
        return order.expires_at is not None and _as_utc(order.expires_at) <= utc_now()

    def _currency(self) -> str:
        return (settings.payment_currency or "COP").strip().upper()

    def _unlock_price(self) -> int:
        return max(settings.full_analysis_unlock_price_cop, 0)

    def _ensure_paywall_pricing(self, paywall: Paywall) -> None:
        expected_price = self._unlock_price()
        expected_currency = self._currency()
        updated = False

        current_price = 0
        if paywall.price_amount is not None:
            try:
                current_price = int(Decimal(paywall.price_amount))
            except (InvalidOperation, TypeError, ValueError):
                current_price = 0
        if current_price != expected_price:
            paywall.price_amount = Decimal(str(expected_price))
            updated = True
        current_currency = (paywall.price_currency or "").strip().upper()
        if current_currency != expected_currency:
            paywall.price_currency = expected_currency
            updated = True
        expected_label = _format_price_label(expected_price, expected_currency)
        if paywall.price_label != expected_label:
            paywall.price_label = _format_price_label(expected_price, expected_currency)
            updated = True
        if updated:
            paywall.updated_at = utc_now()

    def _provider_webhook_url(self, provider: str) -> str:
        return f"{settings.backend_public_url}{settings.API_V1_PREFIX}/payments/webhook/{provider}"

    def _order_payload(self, order: Order) -> dict[str, Any]:
        return {
            "id": str(order.id),
            "caseId": str(order.case_id),
            "status": order.status,
            "currency": order.currency,
            "subtotalAmount": order.subtotal_amount,
            "taxAmount": order.tax_amount,
            "discountAmount": order.discount_amount,
            "totalAmount": order.total_amount,
            "productCode": order.product_code,
            "productName": order.product_name,
            "description": order.description,
            "expiresAt": order.expires_at,
            "paidAt": order.paid_at,
        }

    def _payment_payload(self, payment: Payment) -> dict[str, Any]:
        return {
            "id": str(payment.id),
            "orderId": str(payment.order_id),
            "caseId": str(payment.case_id),
            "status": payment.status,
            "provider": payment.provider,
            "providerPaymentId": payment.provider_payment_id,
            "providerCheckoutId": payment.provider_checkout_id,
            "providerStatus": payment.provider_status,
            "amount": payment.amount,
            "currency": payment.currency,
            "paymentMethod": payment.payment_method,
            "checkoutUrl": payment.checkout_url,
            "expiresAt": payment.expires_at,
            "approvedAt": payment.approved_at,
        }

    def _receipt_summary(self, receipt: Receipt | None) -> dict[str, Any] | None:
        if receipt is None:
            return None
        return {
            "id": str(receipt.id),
            "receiptNumber": receipt.receipt_number,
            "available": receipt.status == "issued",
        }

    def _receipt_payload(self, receipt: Receipt) -> dict[str, Any]:
        return {
            "id": str(receipt.id),
            "receiptNumber": receipt.receipt_number,
            "orderId": str(receipt.order_id),
            "paymentId": str(receipt.payment_id),
            "caseId": str(receipt.case_id),
            "status": receipt.status,
            "currency": receipt.currency,
            "totalAmount": receipt.total_amount,
            "issuedAt": receipt.issued_at,
            "items": [
                {
                    "name": receipt.order.product_name,
                    "quantity": 1,
                    "unitAmount": receipt.total_amount,
                    "totalAmount": receipt.total_amount,
                }
            ],
            "pdfUrl": receipt.pdf_url,
        }

    def _payment_flow_order(self, order: Order | None) -> dict[str, Any] | None:
        if order is None:
            return None
        return {
            "id": str(order.id),
            "status": order.status,
            "totalAmount": order.total_amount,
            "currency": order.currency,
        }

    def _payment_flow_payment(self, payment: Payment | None) -> dict[str, Any] | None:
        if payment is None:
            return None
        return {
            "id": str(payment.id),
            "status": payment.status,
            "checkoutUrl": payment.checkout_url,
        }

    def _payment_flow_receipt(self, receipt: Receipt | None) -> dict[str, Any] | None:
        if receipt is None:
            return None
        return {
            "id": str(receipt.id),
            "receiptNumber": receipt.receipt_number,
            "available": receipt.status == "issued",
        }

    def _payment_flow_message(
        self,
        case: LaboraCase,
        order: Order | None,
        payment: Payment | None,
        receipt: Receipt | None,
    ) -> str:
        if self._case_is_unlocked(case):
            return "Tu pago fue confirmado y el analisis completo esta desbloqueado."
        if payment and payment.status == "pending":
            return "Tu pago esta en proceso. Te avisaremos cuando sea confirmado."
        if payment and payment.status in {"rejected", "failed"}:
            return "Tu pago no fue aprobado. Puedes intentar nuevamente."
        if order is None:
            return "Crea una orden para desbloquear el analisis completo."
        if receipt is not None:
            return "Tu comprobante esta disponible."
        return "Puedes continuar con el pago para desbloquear el analisis completo."

    def _case_payment_status(self, case: LaboraCase, payment: Payment | None) -> str:
        if case.status in {
            "payment_order_created",
            "payment_pending",
            "payment_approved",
            "payment_rejected",
            "payment_failed",
            "payment_expired",
            "payment_requires_review",
            "full_analysis_unlocked",
        }:
            return case.status
        if payment is not None:
            return f"payment_{payment.status}"
        if case.status == "preview_locked":
            return "payment_not_started"
        return case.status

    def _case_unlock_status(self, case: LaboraCase) -> str:
        return "full_analysis_unlocked" if self._case_is_unlocked(case) else "locked"

    def _order_state(self, order: Order) -> dict[str, Any]:
        return {
            "id": str(order.id),
            "caseId": str(order.case_id),
            "userId": str(order.user_id),
            "status": order.status,
            "currency": order.currency,
            "totalAmount": order.total_amount,
            "productCode": order.product_code,
        }

    def _payment_state(self, payment: Payment) -> dict[str, Any]:
        return {
            "id": str(payment.id),
            "orderId": str(payment.order_id),
            "caseId": str(payment.case_id),
            "status": payment.status,
            "provider": payment.provider,
            "providerPaymentId": payment.provider_payment_id,
            "providerCheckoutId": payment.provider_checkout_id,
            "amount": payment.amount,
            "currency": payment.currency,
        }

    def _audit(
        self,
        event_type: str,
        *,
        actor: User | None,
        case: LaboraCase | None,
        order: Order | None,
        payment: Payment | None,
        ip_address: str | None,
        user_agent: str | None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        audit_metadata = {
            "caseId": str(case.id) if case else (str(order.case_id) if order else None),
            "orderId": str(order.id) if order else None,
            "paymentId": str(payment.id) if payment else None,
            "actorRole": self._actor_role(actor),
            "sourceModule": "payments",
        }
        if metadata:
            audit_metadata.update(metadata)
        entity_type = "payment" if payment else "order" if order else "payment_webhook"
        entity_id = payment.id if payment else order.id if order else None
        self.audit_events.create(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor.id if actor else None,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata=_json_safe(audit_metadata),
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _record_internal_event(
        self,
        *,
        event_name: str,
        case_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        metadata: dict[str, Any],
    ) -> None:
        self.paywalls.create_conversion_event(
            event_name=event_name,
            source="system",
            case_id=case_id,
            user_id=user_id,
            metadata=_json_safe(metadata),
        )

    def _actor_role(self, user: User | None) -> str:
        if user is None:
            return "system"
        if user.role in ADMIN_ROLES:
            return "admin"
        if user.role in LEGAL_REVIEWER_ROLES:
            return "legal_reviewer"
        if user.role == "system":
            return "system"
        return "user"

    def _customer_from_order(self, order: Order) -> dict[str, Any]:
        case = self._get_case_or_404(order.case_id)
        return {
            "fullName": f"{case.holder_first_name} {case.holder_last_name}".strip(),
            "email": case.holder_email or "usuario@labora.local",
            "documentType": case.holder_document_type,
            "documentNumber": case.holder_document_number,
            "phone": case.holder_phone,
        }


def _normalize_status(provider_status: str | None, response_code: str | None) -> str:
    code = str(response_code or "").strip()
    if code == "1":
        return "approved"
    if code == "2":
        return "rejected"
    if code == "3":
        return "pending"
    if code == "4":
        return "failed"
    normalized = _normalize_text(provider_status)
    if normalized in {"approved", "paid", "success", "aceptada", "accepted"}:
        return "approved"
    if normalized in {"pending", "processing", "pendiente"}:
        return "pending"
    if normalized in {"rejected", "declined", "rechazada", "denegada"}:
        return "rejected"
    if normalized in {"failed", "error", "fallida"}:
        return "failed"
    if normalized in {"cancelled", "canceled", "cancelada"}:
        return "cancelled"
    if normalized in {"expired", "expirada"}:
        return "expired"
    if normalized in {"refunded", "reembolsada"}:
        return "refunded"
    if normalized in {"chargeback", "contracargo"}:
        return "chargeback"
    return "unknown"


def _format_price_label(amount: int, currency: str) -> str:
    normalized_amount = max(int(amount), 0)
    normalized_currency = (currency or "COP").strip().upper()
    return f"${normalized_amount:,} {normalized_currency}".replace(",", ".")


def _normalize_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _audit_suffix(normalized_status: str) -> str:
    return {
        "approved": "approved",
        "rejected": "rejected",
        "failed": "failed",
        "expired": "failed",
        "pending": "updated",
    }.get(normalized_status, "failed")


def _first_string(payload: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _amount_from_payload(payload: dict[str, Any], keys: list[str]) -> int | None:
    raw = _first_string(payload, keys)
    if raw is None:
        return None
    try:
        return int(Decimal(raw).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


def _stable_hash(raw_body: bytes, payload: dict[str, Any]) -> str:
    body = raw_body or json.dumps(_json_safe(payload), sort_keys=True).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _safe_customer_metadata(customer: dict[str, Any]) -> dict[str, Any]:
    return {
        "fullName": str(customer.get("fullName") or customer.get("full_name") or "")[:160],
        "email": str(customer.get("email") or "")[:255],
        "documentType": str(customer.get("documentType") or customer.get("document_type") or "")[:30],
        "documentNumberLast4": str(
            customer.get("documentNumber") or customer.get("document_number") or ""
        )[-4:],
        "phoneLast4": str(customer.get("phone") or "")[-4:],
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
