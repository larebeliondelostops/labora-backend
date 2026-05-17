# Analisis preliminar gratuito backend

Modulo backend para generar y consultar el preanalisis gratuito de un expediente sin exponer calculos completos, fundamentos juridicos extensos ni estrategia final antes del pago.

## Endpoints

- `POST /api/v1/cases/{caseId}/pre-analysis`
  - Body: `{ "forceRegenerate": false, "source": "user_request" }`
  - Responde `202` con estado `queued` cuando crea una ejecucion.
  - Responde `200` si ya existe un resultado `completed` vigente con el mismo hash de insumos.
  - `forceRegenerate=true` queda reservado a roles admin.

- `GET /api/v1/cases/{caseId}/pre-analysis`
  - Devuelve el resultado visible para el usuario: semaforo, viabilidad, resumen limitado, issues publicos, documentos faltantes, CTA y warnings.
  - Si el resultado queda en `requires_review`, incluye `reviewGuidance` con una razon y acciones concretas para mejorar la confianza del preanalisis.
  - Nunca devuelve campos de calculo completo, valores finales, fundamentos extensos ni estrategia legal.

- `GET /api/v1/cases/{caseId}/pre-analysis/status`
  - Devuelve estado, progreso aproximado, paso actual y si se puede reintentar.

- `POST /api/v1/cases/{caseId}/pre-analysis/retry`
  - Reintenta cuando el estado previo es `error` o `blocked`; admin puede forzar regeneracion.

- `GET /api/v1/admin/pre-analysis`
  - Lista ejecuciones para revision por filtros `status`, `caseId`, `userId`, `page`, `pageSize`.

- `PATCH /api/v1/admin/pre-analysis/{preAnalysisId}/review`
  - Permite aprobar o ajustar resultados en `requires_review`.

## Job

El worker disponible es:

```python
from app.workers.analysis_worker import run_pre_analysis_job
```

Procesa una ejecucion `queued`, marca `in_progress`, llama al proveedor IA desacoplado, valida schema, aplica sanitizacion, persiste tablas separadas y finaliza en `completed`, `requires_review` o `error`.

## Tablas

- `pre_analysis`
- `pre_issue`
- `pre_viability`
- `missing_document`
- `case_signal`
- `pre_analysis_job`

## Garantias de seguridad

- Los insumos enviados a IA son resumenes minimos, no texto documental completo.
- La salida IA se valida contra schema y enums.
- Se bloquean campos prohibidos como calculo completo, retroactivo, valor final, fundamentos detallados o estrategia legal.
- Si `confidence < 0.70`, el resultado termina en `requires_review`.
- Se registran eventos de auditoria `analisis_preliminar_gratuito.*` en `audit_events`.
