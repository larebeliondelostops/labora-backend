# Entrega final y centro de descargas

Modulo backend para centralizar archivos finales de un caso, exponer descargas protegidas, compartir paquetes con abogado mediante enlaces temporales y cerrar el caso sin borrar trazabilidad.

## Variables de entorno

```env
DELIVERY_SHARE_BASE_URL=https://labora.centralspike.com/share/delivery
DELIVERY_SHARE_MAX_DAYS=30
DELIVERY_SIGNED_URL_TTL_SECONDS=300
DELIVERY_MAX_SHARE_VIEWS_DEFAULT=20
DELIVERY_AI_SUMMARY_ENABLED=true
DELIVERY_DOWNLOAD_RATE_LIMIT=60
DELIVERY_PUBLIC_SHARE_RATE_LIMIT=30
```

`DELIVERY_SIGNED_URL_TTL_SECONDS` se limita entre 60 y 300 segundos. Los share links guardan solo hash HMAC del token y el token plano se retorna una unica vez al crear el enlace.

## Endpoints principales

- `GET /api/v1/cases/{caseId}/delivery`
- `GET /api/v1/files/{fileId}/download`
- `POST /api/v1/cases/{caseId}/share-links`
- `GET /api/v1/share/delivery/{token}`
- `GET /api/v1/share/delivery/{token}/files/{fileId}/download`
- `DELETE /api/v1/cases/{caseId}/share-links/{shareLinkId}`
- `POST /api/v1/cases/{caseId}/delivery/complement`
- `POST /api/v1/cases/{caseId}/close`
- `GET /api/v1/cases/{caseId}/delivery/events`
- `POST /api/v1/cases/{caseId}/delivery/ai-summary`

## Seguridad aplicada

- Autorizacion por propietario, usuarios autorizados del expediente o roles internos.
- Descargas mediante URL temporal firmada y streaming protegido desde backend.
- Validacion de pago/desbloqueo antes de descargar.
- Share links con expiracion obligatoria, permisos granulares, limite de vistas y revocacion inmediata.
- Sin exposicion de `file_storage_key`.
- Auditoria persistida en `delivery_event` y `audit_events`.
- IP hasheada y user agent minimizado en eventos del modulo.

## Jobs disponibles

`app.workers.delivery_worker` expone helpers para:

- construir paquete desde exports de informes y escritos;
- expirar share links activos vencidos;
- regenerar resumen final IA.

El resumen IA es no vinculante, deriva de archivos ya generados y guarda confianza y fuentes internas por ID de archivo.
