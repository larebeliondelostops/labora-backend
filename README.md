# labora-backend

Base inicial del backend de Labora construida con FastAPI, SQLAlchemy, Alembic y PostgreSQL.

## Requisitos

- Python 3.12
- Docker y Docker Compose

## Inicio rapido

1. Crear `.env` a partir de `.env.example`.
2. Ejecutar `docker compose up --build`.
3. Abrir `http://localhost:8000/docs`.

## Endpoints base incluidos

- `GET /api/v1/health`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/users/me`
- Rutas placeholder para consents, cases, payments, documents, OCR, questionnaires, analysis, reports, legal actions y admin.

## Migraciones

```bash
docker compose exec labora-backend alembic revision --autogenerate -m "initial schema"
docker compose exec labora-backend alembic upgrade head
```
