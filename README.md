# labora-backend

Base tecnica inicial del backend de Labora construida con FastAPI, SQLAlchemy,
Alembic y PostgreSQL.

## Requisitos

- Python 3.12
- Docker y Docker Compose

## Desarrollo local

Copiar variables de entorno:

```bash
cp .env.example .env
```

Levantar servicios:

```bash
docker compose up --build
```

API:

```txt
http://localhost:8000
```

Swagger:

```txt
http://localhost:8000/docs
```

Health:

```txt
http://localhost:8000/api/v1/health
```

Health DB:

```txt
http://localhost:8000/api/v1/health/db
```

Crear migracion:

```bash
docker compose exec labora-backend alembic revision --autogenerate -m "initial schema"
```

Aplicar migraciones:

```bash
docker compose exec labora-backend alembic upgrade head
```

Ver tablas:

```bash
docker compose exec labora-postgres psql -U labora -d labora_db -c "\dt"
```

Ejecutar tests:

```bash
docker compose exec labora-backend pytest
```

## Endpoints base incluidos

- `GET /`
- `GET /api/v1/health`
- `GET /api/v1/health/db`
- `GET /api/v1/document-types`
- `POST /api/v1/cases/{caseId}/documents`
- `GET /api/v1/cases/{caseId}/documents`
- `GET /api/v1/cases/{caseId}/document-readiness`
- `GET /api/v1/documents/{documentId}`
- `GET /api/v1/documents/{documentId}/view-url`
- `PATCH /api/v1/documents/{documentId}`
- `POST /api/v1/documents/{documentId}/replace`
- `DELETE /api/v1/documents/{documentId}`

Documentacion del modulo documental: `docs/carga_documental_backend.md`.

## IA documental preliminar

El modulo de IA documental preliminar cubre el tramo `Documentos -> IA
preliminar -> Validacion -> Resultado preliminar`. No genera conclusiones
juridicas, liquidaciones, informes finales ni escritos.

Variables de entorno:

```env
AI_PROVIDER=mock
AI_API_KEY=
AI_BASE_URL=
AI_MODEL=
AI_TIMEOUT_MS=30000
AI_MAX_RETRIES=2
AI_TEMPERATURE=0
AI_JSON_MODE=true
```

Proveedores soportados:

```env
# DeepSeek
AI_PROVIDER=deepseek
AI_BASE_URL=https://api.deepseek.com
AI_MODEL=deepseek-v4-pro
AI_API_KEY=replace_with_key

# Kimi / Moonshot
AI_PROVIDER=kimi
AI_BASE_URL=https://api.moonshot.ai/v1
AI_MODEL=kimi-k2.6
AI_API_KEY=replace_with_key

# Local / CI
AI_PROVIDER=mock
```

Endpoints:

- `POST /api/v1/cases/{caseId}/document-precheck`
- `GET /api/v1/cases/{caseId}/document-precheck`
- `GET /api/v1/cases/{caseId}/document-precheck/{precheckId}`
- `POST /api/v1/documents/{documentId}/ocr-preview`
- `GET /api/v1/documents/{documentId}/ocr-preview`
- `POST /api/v1/admin/document-precheck/{precheckId}/review`

Ejemplo:

```sh
curl -X POST "$API_URL/api/v1/cases/$CASE_ID/document-precheck" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"documentId":"'$DOCUMENT_ID'","force":false}'
```

El job corre inline en este MVP, usando una interfaz separada para moverlo luego a
worker/cola. Para aplicar las tablas nuevas:

```sh
alembic upgrade head
```

Eventos auditados principales:

- `ia_documental_preliminar.created`
- `ia_documental_preliminar.queued`
- `ia_documental_preliminar.started`
- `ia_documental_preliminar.ocr_preview_started`
- `ia_documental_preliminar.ocr_preview_completed`
- `ia_documental_preliminar.ai_classification_started`
- `ia_documental_preliminar.ai_classification_completed`
- `ia_documental_preliminar.completed`
- `ia_documental_preliminar.requires_review`
- `ia_documental_preliminar.blocked`
- `ia_documental_preliminar.manual_reviewed`

## Modulo cuenta y autenticacion

Variables principales:

```env
JWT_ACCESS_TTL_SECONDS=900
REFRESH_TOKEN_TTL_DAYS=30
OTP_TTL_MINUTES=10
OTP_MAX_ATTEMPTS=5
AUTH_RATE_LIMIT_WINDOW_SECONDS=900
AUTH_RATE_LIMIT_MAX_ATTEMPTS=10
CORS_ORIGINS=http://localhost:3000,https://labora.centralspike.com
CORS_ALLOW_CREDENTIALS=true
AUTH_COOKIE_NAME=labora_access_token
AUTH_COOKIE_HTTPONLY=true
```

Para frontend local con cookies HttpOnly:

```env
APP_FRONTEND_URL=http://localhost:3000
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=lax
AUTH_COOKIE_DOMAIN=
```

Para produccion con `https://labora.centralspike.com` consumiendo
`https://labora.backend.centralspike.com/api/v1`:

```env
APP_FRONTEND_URL=https://labora.centralspike.com
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
AUTH_COOKIE_DOMAIN=
```

## MinIO en produccion

El backend firma las subidas con el endpoint publico de MinIO, pero lee y verifica
objetos usando la red interna de Docker. En produccion usa valores como estos:

```env
STORAGE_BACKEND=minio
MINIO_ENDPOINT=http://labora-minio:9000
MINIO_PUBLIC_ENDPOINT=https://minio.centralspike.com
MINIO_ACCESS_KEY=replace_with_minio_access_key
MINIO_SECRET_KEY=replace_with_minio_secret_key
MINIO_BUCKET=documents
MINIO_REGION=us-east-1
MINIO_SECURE=false
MINIO_PRESIGNED_UPLOAD_TTL_SECONDS=900
API_PUBLIC_BASE_URL=https://labora.backend.centralspike.com
BACKEND_PUBLIC_URL=https://labora.backend.centralspike.com
CORS_ORIGINS=https://labora.centralspike.com,http://localhost:3000
```

Expone `https://minio.centralspike.com` hacia `labora-minio:9000` solamente
para la API S3. No expongas la consola `9001` en ese mismo subdominio.
Con `STORAGE_BACKEND=minio`, las URLs temporales de visualizacion/descarga se
firman directo contra `MINIO_PUBLIC_ENDPOINT` usando `presigned_get_object`.
El bucket sigue privado: el navegador solo recibe una URL temporal. En
produccion `MINIO_PUBLIC_ENDPOINT`, `API_PUBLIC_BASE_URL` y
`BACKEND_PUBLIC_URL` deben apuntar a dominios publicos reales, nunca a
`localhost`, `127.0.0.1` ni nombres internos de Docker.
La comprobacion publica esperada es:

```sh
curl -I https://minio.centralspike.com/minio/health/live
```

Debe responder `200`.

Rutas principales:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/verify-otp`
- `POST /api/v1/auth/resend-otp`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/logout-all`
- `GET /api/v1/users/me`
- `PATCH /api/v1/users/me`
- `GET /api/v1/users/me/sessions`
- `DELETE /api/v1/users/me/sessions/{session_id}`

El OTP del modulo de cuenta se emite y verifica solo por correo electronico.

Despues de desplegar cambios de autenticacion:

```bash
docker compose exec labora-backend alembic upgrade head
```
