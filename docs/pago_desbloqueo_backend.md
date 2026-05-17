# Pago al final y desbloqueo

Modulo backend para crear una orden de desbloqueo, iniciar checkout, confirmar pagos por webhook e idempotencia, desbloquear el analisis completo y consultar comprobantes.

## Endpoints

- `POST /api/v1/cases/{caseId}/orders`: crea o reutiliza una orden activa para `FULL_ANALYSIS_UNLOCK`.
- `POST /api/v1/payments/checkout`: crea un intento de pago pendiente y devuelve `checkoutUrl`.
- `GET /api/v1/payments/{paymentId}`: consulta estado de pago, estado del caso y recibo disponible.
- `POST /api/v1/payments/webhook/{provider}`: recibe eventos del proveedor. Para ePayco usa `provider=epayco`.
- `GET /api/v1/orders/{orderId}/receipt`: consulta el comprobante de una orden pagada.
- `POST /api/v1/orders/{orderId}/retry-payment`: genera un nuevo intento de pago sobre una orden no pagada.
- `GET /api/v1/cases/{caseId}/payment-flow`: estado consolidado para UI.

## Reglas clave

- El frontend nunca aprueba pagos; solo el webhook valido cambia el pago a `approved`.
- La idempotencia se controla con `provider_event_id`, `provider_payment_id`, `idempotency_key` y estados actuales.
- El pago no ejecuta IA ni calculos; solo actualiza estados, registra auditoria y emite eventos internos.
- El desbloqueo crea un `unlock_event`, actualiza el expediente a `full_analysis_unlocked`, completa el paywall y emite `case.full_analysis_unlocked`.
- Los webhooks duplicados responden `200` con `duplicate: true` y no duplican recibos ni desbloqueos.

## Variables

```env
PAYMENT_PROVIDER=epayco
PAYMENT_PROVIDER_WEBHOOK_SECRET=
PAYMENT_ORDER_EXPIRATION_MINUTES=60
PAYMENT_CURRENCY=COP
FULL_ANALYSIS_UNLOCK_PRICE_COP=150000
PAYMENT_WEBHOOK_RATE_LIMIT_PER_MINUTE=60
```

Tambien se usan las variables existentes de ePayco:

```env
EPAYCO_PUBLIC_KEY=
EPAYCO_PRIVATE_KEY=
EPAYCO_P_CUST_ID_CLIENTE=
EPAYCO_P_KEY=
EPAYCO_API_BASE_URL=https://apify.epayco.co
EPAYCO_CONFIRMATION_URL=
```

## Errores principales

- `CASE_NOT_ELIGIBLE_FOR_PAYMENT`
- `CASE_ALREADY_UNLOCKED`
- `ORDER_NOT_FOUND`
- `ORDER_ALREADY_PAID`
- `ORDER_EXPIRED`
- `PAYMENT_NOT_FOUND`
- `PROVIDER_CHECKOUT_FAILED`
- `WEBHOOK_SIGNATURE_INVALID`
- `RECEIPT_NOT_FOUND`
- `FORBIDDEN`
