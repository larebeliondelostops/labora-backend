import json
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.payment import (
    CheckoutRequest,
    CheckoutResponse,
    OrderCreateRequest,
    OrderCreateResponse,
    PaymentFlowResponse,
    PaymentStatusResponse,
    ReceiptResponse,
    RetryPaymentRequest,
    WebhookResponse,
)
from app.services.payment_service import PaymentService


router = APIRouter()


@router.post(
    "/cases/{case_id}/orders",
    response_model=OrderCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_unlock_order(
    case_id: str,
    payload: OrderCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaymentService(db).create_order(
        case_id,
        product_code=payload.product_code,
        return_url=payload.return_url,
        cancel_url=payload.cancel_url,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/payments/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment_checkout(
    payload: CheckoutRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PaymentService(db).create_checkout(
        order_id=payload.order_id,
        payment_method=payload.payment_method,
        customer=payload.customer.model_dump(by_alias=True),
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentStatusResponse,
)
def get_payment_status(
    payment_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaymentService(db).get_payment_status(
        payment_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/payments/webhook/{provider}",
    response_model=WebhookResponse,
)
async def payment_provider_webhook(
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    raw_body = await request.body()
    payload = _webhook_payload(raw_body, request.headers.get("content-type", ""))
    headers = {key.lower(): value for key, value in request.headers.items()}
    return PaymentService(db).handle_webhook(
        provider=provider,
        payload=payload,
        headers=headers,
        raw_body=raw_body,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/orders/{order_id}/receipt",
    response_model=ReceiptResponse,
)
def get_order_receipt(
    order_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaymentService(db).get_receipt(
        order_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/orders/{order_id}/retry-payment",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def retry_order_payment(
    order_id: str,
    payload: RetryPaymentRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PaymentService(db).retry_payment(
        order_id,
        payment_method=payload.payment_method,
        return_url=payload.return_url,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/payment-flow",
    response_model=PaymentFlowResponse,
)
def get_case_payment_flow(
    case_id: str,
    request: Request,
    provider: str | None = None,
    ref_payco: str | None = None,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaymentService(db).get_payment_flow(
        case_id,
        provider=provider,
        ref_payco=ref_payco,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


def _webhook_payload(raw_body: bytes, content_type: str) -> dict:
    if "application/json" in content_type.lower():
        try:
            body = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}
        return body if isinstance(body, dict) else {}
    parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}
