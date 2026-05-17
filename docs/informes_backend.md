# Modulo de informes y exportaciones

El modulo genera informes versionados desde un expediente desbloqueado con
analisis completo disponible. La generacion usa datos estructurados de
`full_analysis` y redaccion controlada mediante `ReportAiClient`; no inventa
hechos, reglas, valores ni documentos.

## Crear informe

`POST /api/v1/cases/{caseId}/reports`

```json
{
  "reportType": "full",
  "templateKey": "labora_full_report_v1",
  "forceRegenerate": false,
  "includeSections": [
    "executive_summary",
    "calculation_summary",
    "inconsistency_matrix",
    "conclusions"
  ],
  "outputMode": "async"
}
```

Responde `202` cuando queda en cola. Con `outputMode=sync` responde `201` y
entrega el informe listo o `requires_review`.

## Listar y consultar

- `GET /api/v1/cases/{caseId}/reports`
- `GET /api/v1/reports/{reportId}`
- `GET /api/v1/reports/{reportId}/versions`

Los usuarios solo acceden a informes de sus expedientes. Roles internos pueden
consultar segun las mismas reglas del resto del backend.

## Exportar

`POST /api/v1/reports/{reportId}/export`

```json
{
  "format": "pdf",
  "versionId": "uuid",
  "includeTraceabilityStamp": true,
  "includeEvidenceIndex": true
}
```

Formatos soportados: `pdf`, `docx`. Solo se exportan informes `ready` o
`approved`; informes `requires_review` deben aprobarse antes.

## Descargar

`GET /api/v1/exports/{exportFileId}/download`

Devuelve una URL firmada temporal:

```json
{
  "downloadUrl": "http://localhost:8000/api/v1/exports/{id}/file?expires=...&token=...",
  "expiresAt": "2026-05-17T10:40:00Z"
}
```

El frontend no recibe `storage_key`. La descarga registra auditoria
`informes.export_downloaded`.

## Revision backoffice

- `POST /api/v1/reports/{reportId}/approve`
- `POST /api/v1/reports/{reportId}/reject`

`approve` acepta:

```json
{
  "reviewNotes": "Informe validado para entrega al usuario."
}
```

`reject` acepta:

```json
{
  "reason": "La conclusion no tiene soporte suficiente."
}
```
