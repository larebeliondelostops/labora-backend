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
