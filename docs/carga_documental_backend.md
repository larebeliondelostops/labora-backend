# Carga y gestion documental

Modulo backend para cargar, clasificar, validar y administrar documentos de un expediente. El alcance es documental preliminar: no ejecuta analisis juridico completo, calculos, informes finales, pagos ni escritos.

## Rutas principales

- `GET /api/v1/document-types`
- `POST /api/v1/cases/{caseId}/documents`
- `POST /api/v1/documents/{documentId}/complete-upload`
- `GET /api/v1/cases/{caseId}/documents`
- `GET /api/v1/documents/{documentId}`
- `GET /api/v1/documents/{documentId}/view-url`
- `GET /api/v1/documents/{documentId}/file?expires=...&token=...`
- `PATCH /api/v1/documents/{documentId}`
- `POST /api/v1/documents/{documentId}/replace`
- `DELETE /api/v1/documents/{documentId}`
- `GET /api/v1/cases/{caseId}/document-readiness`

## Flujo MVP

1. El usuario debe estar autenticado, tener acceso al expediente y contar con consentimientos vigentes.
2. La carga multipart valida nombre, MIME, extension, tamano, limite total del expediente y firma real del archivo.
3. El archivo queda en storage local privado y se registra `file_upload`.
4. La cola in-process ejecuta validacion documental preliminar: SHA-256, duplicados, parseo basico de PDF, paginas, PDF protegido, corrupcion y calidad basica.
5. El clasificador documental queda desacoplado y usa senales deterministicas de nombre/texto como placeholder del proveedor IA.
6. Las acciones sensibles generan auditoria `carga_documental.*`.
7. `view-url` devuelve una URL temporal firmada hacia un proxy autenticado; no se expone `storage_key`.

## Errores normalizados

Los errores usan el formato global:

```json
{
  "error": {
    "code": "DOCUMENT_SIZE_EXCEEDED",
    "message": "El archivo supera el limite de 50 MB.",
    "details": {},
    "requestId": "req_xxx",
    "traceId": "req_xxx"
  }
}
```

Codigos principales: `CONSENT_REQUIRED`, `CASE_ACCESS_DENIED`, `CASE_STATUS_BLOCKS_UPLOAD`, `DOCUMENT_TYPE_INVALID`, `DOCUMENT_MIME_TYPE_NOT_ALLOWED`, `DOCUMENT_EXTENSION_NOT_ALLOWED`, `DOCUMENT_SIZE_EXCEEDED`, `DOCUMENT_NOT_FOUND`, `DOCUMENT_PASSWORD_PROTECTED`, `DOCUMENT_CORRUPTED`, `DOCUMENT_DUPLICATE`, `DOCUMENT_DELETE_NOT_ALLOWED`, `DOCUMENT_REPLACE_NOT_ALLOWED`, `STORAGE_PROVIDER_ERROR`.
