import uuid
from typing import Any

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.payment import (
    Order,
    Payment,
    PaymentTransaction,
    Receipt,
    UnlockEvent,
)


ACTIVE_ORDER_STATUSES = {"created", "checkout_started", "pending", "paid"}


class PaymentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_order(self, order_id: str | uuid.UUID) -> Order | None:
        parsed = _parse_uuid(order_id)
        if parsed is None:
            return None
        return self.db.get(Order, parsed)

    def get_payment(self, payment_id: str | uuid.UUID) -> Payment | None:
        parsed = _parse_uuid(payment_id)
        if parsed is None:
            return None
        return self.db.get(Payment, parsed)

    def active_order_for_case(
        self,
        *,
        case_id: uuid.UUID,
        product_code: str,
    ) -> Order | None:
        return (
            self.db.query(Order)
            .filter(
                Order.case_id == case_id,
                Order.product_code == product_code,
                Order.status.in_(ACTIVE_ORDER_STATUSES),
            )
            .order_by(desc(Order.created_at))
            .first()
        )

    def latest_order_for_case(
        self,
        *,
        case_id: uuid.UUID,
        product_code: str | None = None,
    ) -> Order | None:
        query = self.db.query(Order).filter(Order.case_id == case_id)
        if product_code:
            query = query.filter(Order.product_code == product_code)
        return query.order_by(desc(Order.created_at)).first()

    def latest_payment_for_order(self, order_id: uuid.UUID) -> Payment | None:
        return (
            self.db.query(Payment)
            .filter(Payment.order_id == order_id)
            .order_by(desc(Payment.created_at))
            .first()
        )

    def pending_payment_for_order(self, order_id: uuid.UUID) -> Payment | None:
        return (
            self.db.query(Payment)
            .filter(
                Payment.order_id == order_id,
                Payment.status.in_(["created", "pending"]),
            )
            .order_by(desc(Payment.created_at))
            .first()
        )

    def payment_by_provider_id(
        self,
        *,
        provider: str,
        provider_payment_id: str | None,
    ) -> Payment | None:
        if not provider_payment_id:
            return None
        return (
            self.db.query(Payment)
            .filter(
                Payment.provider == provider,
                Payment.provider_payment_id == provider_payment_id,
            )
            .one_or_none()
        )

    def payments_for_order(self, order_id: uuid.UUID) -> list[Payment]:
        return (
            self.db.query(Payment)
            .filter(Payment.order_id == order_id)
            .order_by(desc(Payment.created_at))
            .all()
        )

    def order_by_paywall_id(self, paywall_id: uuid.UUID) -> Order | None:
        return (
            self.db.query(Order)
            .filter(Order.paywall_id == paywall_id)
            .order_by(desc(Order.created_at))
            .first()
        )

    def create_order(self, **values: Any) -> Order:
        order = Order(**values)
        self.db.add(order)
        self.db.flush()
        return order

    def create_payment(self, **values: Any) -> Payment:
        payment = Payment(**values)
        self.db.add(payment)
        self.db.flush()
        return payment

    def transaction_by_event(
        self,
        *,
        provider: str,
        provider_event_id: str,
    ) -> PaymentTransaction | None:
        return (
            self.db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.provider == provider,
                PaymentTransaction.provider_event_id == provider_event_id,
            )
            .one_or_none()
        )

    def create_transaction(self, **values: Any) -> PaymentTransaction:
        transaction = PaymentTransaction(**values)
        self.db.add(transaction)
        self.db.flush()
        return transaction

    def receipt_for_order(self, order_id: uuid.UUID) -> Receipt | None:
        return (
            self.db.query(Receipt)
            .filter(Receipt.order_id == order_id)
            .order_by(desc(Receipt.issued_at))
            .first()
        )

    def receipt_for_payment(self, payment_id: uuid.UUID) -> Receipt | None:
        return (
            self.db.query(Receipt)
            .filter(Receipt.payment_id == payment_id)
            .order_by(desc(Receipt.issued_at))
            .first()
        )

    def create_receipt(self, **values: Any) -> Receipt:
        receipt = Receipt(**values)
        self.db.add(receipt)
        self.db.flush()
        return receipt

    def next_receipt_number(self, year: int) -> str:
        prefix = f"LAB-REC-{year}-"
        last = (
            self.db.query(Receipt.receipt_number)
            .filter(Receipt.receipt_number.like(f"{prefix}%"))
            .order_by(desc(Receipt.receipt_number))
            .first()
        )
        next_value = 1
        if last:
            try:
                next_value = int(last[0].rsplit("-", 1)[1]) + 1
            except (IndexError, ValueError):
                next_value = self._count_receipts_for_year(prefix) + 1
        return f"{prefix}{next_value:06d}"

    def unlock_event_for_payment(self, payment_id: uuid.UUID) -> UnlockEvent | None:
        return (
            self.db.query(UnlockEvent)
            .filter(UnlockEvent.payment_id == payment_id)
            .one_or_none()
        )

    def create_unlock_event(self, **values: Any) -> UnlockEvent:
        event = UnlockEvent(**values)
        self.db.add(event)
        self.db.flush()
        return event

    def _count_receipts_for_year(self, prefix: str) -> int:
        return (
            self.db.query(func.count(Receipt.id))
            .filter(Receipt.receipt_number.like(f"{prefix}%"))
            .scalar()
            or 0
        )


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
