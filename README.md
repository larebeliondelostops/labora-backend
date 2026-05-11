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
