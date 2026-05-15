# Modulo de extraccion y validacion de datos

Implementacion backend inicial del modulo 08.

## Rutas

Todas las rutas estan bajo `/api/v1` y requieren autenticacion:

- `GET /cases/{caseId}/extraction`
- `POST /cases/{caseId}/extraction-runs`
- `PATCH /cases/{caseId}/extraction-fields`
- `POST /cases/{caseId}/extraction/employers`
- `POST /cases/{caseId}/extraction/labor-periods`
- `PATCH /cases/{caseId}/extraction/entities/{entityType}/{entityId}/ignore`
- `POST /cases/{caseId}/confirm-extraction`
- `GET /cases/{caseId}/extraction/corrections`
- `GET /cases/{caseId}/extraction/issues`
- `PATCH /cases/{caseId}/extraction/issues/{issueId}`

## Garantias implementadas

- Control de acceso por expediente.
- Validacion de consentimiento antes de iniciar extraccion.
- Entidades para ejecuciones, campos, empleadores, periodos, semanas, salarios, vacios, novedades, correcciones, confirmaciones, issues, auditoria y jobs.
- Auditoria en `audit_events` y `extraction_audit_events`.
- Proveedor IA abstracto con modos `mock`, `none`, `deepseek`, `kimi` y `openai`.
- Jobs idempotentes mediante `idempotency_key`.
- Correcciones sin sobrescritura silenciosa: cada cambio crea `user_corrections`.
- Confirmacion bloqueada por baja confianza salvo aceptacion explicita o marcado de pendientes.
- Guard de analisis: `/cases/{caseId}/analysis/start` exige extraccion confirmada o con pendientes.

## Variables

Ver `.env.example`:

- `AI_EXTRACTION_PROVIDER`
- `AI_EXTRACTION_MODEL`
- `AI_EXTRACTION_API_KEY`
- `AI_EXTRACTION_BASE_URL`
- `AI_EXTRACTION_TIMEOUT_MS`
- `AI_EXTRACTION_CONFIDENCE_LOW`
- `AI_EXTRACTION_CONFIDENCE_BLOCKING`
- `EXTRACTION_JOB_MAX_RETRIES`
- `EXTRACTION_JOB_TIMEOUT_MS`
- `EXTRACTION_ENABLE_REPROCESS`
- `EXTRACTION_PRESERVE_USER_CORRECTIONS`
