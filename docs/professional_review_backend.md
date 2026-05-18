# Modulo de revision profesional backend

Implementa la revision profesional opcional sobre resultados ya generados por
Labora: informes, borradores juridicos, archivos generados, resultados de caso
y calculos.

## Tablas

- `professional_reviews`
- `reviewer_assignments`
- `lawyer_comments`
- `reviewed_files`
- `review_orders`

La auditoria se registra en la tabla global `audit_events` con
`entity_type = professional_review`.

## Endpoints

Base path: `/api/v1`

- `POST /cases/{case_id}/professional-review`
- `GET /professional-reviews`
- `GET /professional-reviews/{review_id}`
- `PATCH /professional-reviews/{review_id}`
- `POST /professional-reviews/{review_id}/assign`
- `POST /professional-reviews/{review_id}/assignment-response`
- `POST /professional-reviews/{review_id}/comments`
- `PATCH /professional-reviews/{review_id}/comments/{comment_id}`
- `POST /professional-reviews/{review_id}/request-client-action`
- `POST /professional-reviews/{review_id}/reviewed-files`
- `POST /professional-reviews/{review_id}/approve`
- `POST /professional-reviews/{review_id}/reject`
- `POST /professional-reviews/{review_id}/cancel`
- `POST /professional-reviews/{review_id}/ai-summary`

## Roles soportados

- Cliente: `user`, `client`
- Abogado revisor: `legal_reviewer`, `lawyer`
- Administrador: `admin`, `legal_admin`
- Soporte: `support`, `support_agent`, `operator`, `legal_ops`, `reviewer`

## Pagos

Las revisiones con `requiresPayment = true` crean una orden en `orders` con
`product_code = PROFESSIONAL_REVIEW` y una fila puente en `review_orders`.

Cuando el webhook de pagos aprueba una orden de este producto, el pago no
desbloquea analisis completo. En su lugar confirma la revision:

- `review_orders.status = paid`
- `professional_reviews.status` pasa de `payment_pending` a `requested`
- se audita `revision_profesional.payment_confirmed`

## Seguridad

- El cliente solo ve sus propias revisiones.
- El abogado solo ve las revisiones donde es asignado vigente.
- Admin ve todo.
- Soporte tiene lectura limitada.
- Comentarios `internal`, `lawyer_only` y `admin_only` nunca se exponen al
  cliente.
- El cliente no puede crear comentarios internos.
- Los accesos de detalle se auditan con `revision_profesional.viewed`.

## Pruebas

Suite cubierta en `tests/test_professional_reviews.py`:

- solicitud y bloqueo de duplicados activos
- solicitud con pago y orden puente
- asignacion y aceptacion de abogado
- comentarios internos/visibles y filtrado para cliente
- asociacion de archivo revisado
- aprobacion y publicacion final
- restriccion de comentarios internos para cliente
