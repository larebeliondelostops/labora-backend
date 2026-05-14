import secrets

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.api_errors import ApiError
from app.core.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.APP_DEBUG,
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    api_router,
    prefix=settings.API_V1_PREFIX,
)


@app.exception_handler(ApiError)
def api_error_handler(request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "requestId": exc.trace_id,
                "traceId": exc.trace_id,
            }
        },
    )


@app.exception_handler(RequestValidationError)
def request_validation_error_handler(request, exc: RequestValidationError) -> JSONResponse:
    request_id = f"req_{secrets.token_urlsafe(8)}"
    is_case_path = _is_case_path(request.url.path)
    is_document_path = _is_document_path(request.url.path)
    return JSONResponse(
        status_code=400 if is_case_path and not is_document_path else 422,
        content={
            "error": {
                "code": _validation_error_code(
                    is_case_path=is_case_path,
                    is_document_path=is_document_path,
                ),
                "message": "La solicitud contiene datos invalidos.",
                "details": [
                    {
                        "field": _validation_field(error.get("loc", [])),
                        "message": error.get("msg", "Dato invalido."),
                    }
                    for error in exc.errors()
                ],
                "requestId": request_id,
                "traceId": request_id,
            }
        },
    )


def _is_case_path(path: str) -> bool:
    return path.startswith(
        (
            f"{settings.API_V1_PREFIX}/cases",
            f"{settings.API_V1_PREFIX}/admin/cases",
            f"{settings.API_V1_PREFIX}/internal/cases",
        )
    )


def _is_document_path(path: str) -> bool:
    return path.startswith(
        (
            f"{settings.API_V1_PREFIX}/documents",
            f"{settings.API_V1_PREFIX}/document-types",
        )
    ) or (
        path.startswith(f"{settings.API_V1_PREFIX}/cases")
        and "document" in path
    )


def _validation_error_code(*, is_case_path: bool, is_document_path: bool) -> str:
    if is_document_path:
        return "DOCUMENT_VALIDATION_FAILED"
    if is_case_path:
        return "CASE_VALIDATION_ERROR"
    return "VALIDATION_ERROR"


def _validation_field(loc: list | tuple) -> str:
    parts = [str(item) for item in loc if item not in {"body", "query", "path"}]
    return ".".join(parts) or "request"


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.APP_NAME,
        "docs": "/docs",
        "health": f"{settings.API_V1_PREFIX}/health",
    }
