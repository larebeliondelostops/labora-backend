from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProductCode = Literal["FULL_ANALYSIS_UNLOCK", "LEGAL_DRAFT_GENERATION"]
PaymentMethod = Literal["CARD", "PSE", "TRANSFER", "CASH", "OTHER"]


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_code: ProductCode = Field(alias="productCode")
    return_url: str | None = Field(alias="returnUrl", default=None, max_length=500)
    cancel_url: str | None = Field(alias="cancelUrl", default=None, max_length=500)

    @field_validator("return_url", "cancel_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not normalized.lower().startswith(("http://", "https://")):
            raise ValueError("Debe ser una URL http o https.")
        return normalized


class OrderDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    status: str
    currency: str
    subtotal_amount: int = Field(alias="subtotalAmount")
    tax_amount: int = Field(alias="taxAmount")
    discount_amount: int = Field(alias="discountAmount")
    total_amount: int = Field(alias="totalAmount")
    product_code: str = Field(alias="productCode")
    product_name: str = Field(alias="productName")
    description: str | None = None
    expires_at: datetime | None = Field(alias="expiresAt", default=None)
    paid_at: datetime | None = Field(alias="paidAt", default=None)


class OrderCreateResponse(BaseModel):
    order: OrderDto


class CustomerDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    full_name: str = Field(alias="fullName", min_length=3, max_length=160)
    email: str = Field(min_length=5, max_length=255)
    document_type: str = Field(alias="documentType", min_length=1, max_length=30)
    document_number: str = Field(alias="documentNumber", min_length=4, max_length=80)
    phone: str | None = Field(default=None, max_length=30)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    order_id: str = Field(alias="orderId")
    payment_method: PaymentMethod = Field(alias="paymentMethod", default="CARD")
    customer: CustomerDto


class PaymentDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    order_id: str = Field(alias="orderId")
    case_id: str = Field(alias="caseId")
    status: str
    provider: str
    provider_payment_id: str | None = Field(alias="providerPaymentId", default=None)
    provider_reference: str | None = Field(alias="providerReference", default=None)
    ref_payco: str | None = Field(alias="refPayco", default=None)
    invoice: str | None = None
    provider_checkout_id: str | None = Field(alias="providerCheckoutId", default=None)
    provider_status: str | None = Field(alias="providerStatus", default=None)
    amount: int
    currency: str
    payment_method: str | None = Field(alias="paymentMethod", default=None)
    checkout_url: str | None = Field(alias="checkoutUrl", default=None)
    expires_at: datetime | None = Field(alias="expiresAt", default=None)
    approved_at: datetime | None = Field(alias="approvedAt", default=None)


class CheckoutResponse(BaseModel):
    payment: PaymentDto


class CasePaymentStateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    payment_status: str = Field(alias="paymentStatus")
    unlock_status: str = Field(alias="unlockStatus")


class ReceiptSummaryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    receipt_number: str = Field(alias="receiptNumber")
    available: bool


class PaymentStatusResponse(BaseModel):
    payment: PaymentDto
    case: CasePaymentStateDto
    receipt: ReceiptSummaryDto | None = None


class ReceiptItemDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    quantity: int
    unit_amount: int = Field(alias="unitAmount")
    total_amount: int = Field(alias="totalAmount")


class ReceiptDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    receipt_number: str = Field(alias="receiptNumber")
    order_id: str = Field(alias="orderId")
    payment_id: str = Field(alias="paymentId")
    case_id: str = Field(alias="caseId")
    status: str
    currency: str
    total_amount: int = Field(alias="totalAmount")
    issued_at: datetime = Field(alias="issuedAt")
    items: list[ReceiptItemDto]
    pdf_url: str | None = Field(alias="pdfUrl", default=None)


class ReceiptResponse(BaseModel):
    receipt: ReceiptDto


class RetryPaymentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    payment_method: PaymentMethod = Field(alias="paymentMethod", default="CARD")
    return_url: str | None = Field(alias="returnUrl", default=None, max_length=500)


class PaymentFlowDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    case_status: str = Field(alias="caseStatus")
    can_pay: bool = Field(alias="canPay")
    can_retry: bool = Field(alias="canRetry")
    can_continue: bool = Field(alias="canContinue")
    is_unlocked: bool = Field(alias="isUnlocked")
    order: dict[str, Any] | None = None
    payment: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    ui_message: str = Field(alias="uiMessage")


class PaymentFlowResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    payment_flow: PaymentFlowDto = Field(alias="paymentFlow")


class WebhookResponse(BaseModel):
    received: bool
    duplicate: bool = False
