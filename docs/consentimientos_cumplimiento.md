# Consentimientos y cumplimiento

Modulo backend para consultar documentos legales vigentes, registrar consentimientos separados y bloquear la carga documental cuando falten autorizaciones obligatorias.

## Endpoints

- `GET /api/v1/legal-documents/current?types=terms_and_conditions,personal_data_processing`
- `GET /api/v1/users/me/consents/status`
- `POST /api/v1/consents` con header `Idempotency-Key`
- `GET /api/v1/users/me/consents`
- `GET /api/v1/users/me/permissions/document-upload`
- `POST /api/v1/admin/legal-documents`
- `POST /api/v1/admin/legal-documents/{id}/activate`
- `GET /api/v1/admin/users/{userId}/consents`

## Crear consentimientos

```json
{
  "items": [
    {
      "legalDocumentId": "uuid",
      "consentType": "terms_and_conditions",
      "accepted": true
    }
  ],
  "source": "web",
  "locale": "es-CO"
}
```

El backend captura IP y user agent desde el request, guarda version y hash del documento, y calcula `evidenceHashSha256` con JSON canonico ordenado.

## Seed inicial

Ejecuta migraciones y luego:

```bash
python -m app.services.consent_seed
```

El seed crea cinco documentos activos version `2026.05.01` para terminos, datos personales, datos sensibles, medios electronicos y alcance IA. Los textos son placeholders y deben reemplazarse por la redaccion legal definitiva antes de produccion.
