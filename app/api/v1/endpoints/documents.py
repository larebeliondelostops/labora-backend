import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.document import (
    DocumentCompleteUploadResponse,
    DocumentCreateRequest,
    DocumentCreateResponse,
    DocumentReadinessResponse,
    DocumentReplaceRequest,
    DocumentUpdateRequest,
    DocumentViewUrlResponse,
)
from app.services.document_service import DocumentService

router = APIRouter()


@router.get("/document-types")
def list_document_types(db: Session = Depends(get_db)) -> dict:
    return DocumentService(db).list_document_types()


@router.post(
    "/cases/{case_id}/documents",
    response_model=DocumentCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_upload(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    payload, file_content = await _document_create_payload(request)
    return DocumentService(db).create_document_upload(
        case_id,
        payload,
        file_content=file_content,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/documents/{document_id}/complete-upload",
    response_model=DocumentCompleteUploadResponse,
)
def complete_upload(
    document_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return DocumentService(db).complete_upload(
        document_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/documents")
def list_case_documents(
    case_id: str,
    request: Request,
    document_type_code: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return DocumentService(db).list_case_documents(
        case_id,
        user=user,
        document_type_code=document_type_code,
        status_filter=status_filter,
        include_deleted=include_deleted,
        page=page,
        limit=limit,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/document-readiness", response_model=DocumentReadinessResponse)
def get_document_readiness(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return DocumentService(db).get_readiness(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return DocumentService(db).get_document(
        document_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/documents/{document_id}/view-url", response_model=DocumentViewUrlResponse)
def get_document_view_url(
    document_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return DocumentService(db).get_view_url(
        document_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: str,
    request: Request,
    expires: int,
    token: str,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> FileResponse:
    user, _payload = context
    path = DocumentService(db).validate_file_access(
        document_id,
        expires=expires,
        token=token,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    if not path.exists():
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="DOCUMENT_NOT_FOUND",
            message="Archivo no encontrado.",
        )
    return FileResponse(path)


@router.patch("/documents/{document_id}")
def update_document(
    document_id: str,
    payload: DocumentUpdateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DocumentService(db).update_document(
        document_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/documents/{document_id}/replace")
async def replace_document(
    document_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    payload, file_content = await _document_replace_payload(request)
    return DocumentService(db).replace_document(
        document_id,
        payload,
        file_content=file_content,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return DocumentService(db).delete_document(
        document_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


async def _document_create_payload(request: Request) -> tuple[DocumentCreateRequest, bytes | None]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        fields, files = await _parse_multipart_request(request)
        file_item = files.get("file")
        if file_item is None:
            raise _payload_error("El archivo es obligatorio.")
        filename, mime_type, content = file_item
        payload = _validate_payload(
            DocumentCreateRequest,
            {
                "originalFilename": filename,
                "mimeType": fields.get("mimeType") or mime_type,
                "sizeBytes": len(content),
                "documentTypeCode": fields.get("documentTypeCode"),
                "isPrimary": _parse_bool(fields.get("isPrimary")),
            },
        )
        return payload, content
    return _validate_payload(DocumentCreateRequest, await request.json()), None


async def _document_replace_payload(request: Request) -> tuple[DocumentReplaceRequest, bytes | None]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        fields, files = await _parse_multipart_request(request)
        file_item = files.get("file")
        if file_item is None:
            raise _payload_error("El archivo es obligatorio.")
        filename, mime_type, content = file_item
        payload = _validate_payload(
            DocumentReplaceRequest,
            {
                "originalFilename": filename,
                "mimeType": fields.get("mimeType") or mime_type,
                "sizeBytes": len(content),
                "documentTypeCode": fields.get("documentTypeCode"),
                "isPrimary": _parse_bool(fields.get("isPrimary")),
            },
        )
        return payload, content
    return _validate_payload(DocumentReplaceRequest, await request.json()), None


async def _parse_multipart_request(
    request: Request,
) -> tuple[dict[str, str], dict[str, tuple[str, str, bytes]]]:
    content_type = request.headers.get("content-type", "")
    boundary_match = re.search(r'boundary="?([^";]+)"?', content_type)
    if boundary_match is None:
        raise _payload_error("Solicitud multipart sin boundary.")
    boundary = boundary_match.group(1).encode("utf-8")
    body = await request.body()
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, str, bytes]] = {}
    delimiter = b"--" + boundary
    for raw_part in body.split(delimiter):
        part = raw_part.strip()
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].strip()
        headers_raw, separator, value = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = headers_raw.decode("latin-1", errors="ignore")
        content = value[:-2] if value.endswith(b"\r\n") else value
        disposition_match = re.search(r'(?:^|\r\n)content-disposition:([^\r\n]+)', headers, re.I)
        if disposition_match is None:
            continue
        disposition = disposition_match.group(1)
        name_match = re.search(r'(?:^|;)\s*name="([^"]+)"', disposition, re.I)
        if name_match is None:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'(?:^|;)\s*filename="([^"]*)"', disposition, re.I)
        if filename_match is not None:
            content_type_match = re.search(r"content-type:\s*([^\r\n]+)", headers, re.I)
            files[name] = (
                filename_match.group(1) or "documento.pdf",
                content_type_match.group(1).strip() if content_type_match else "application/octet-stream",
                content,
            )
        else:
            fields[name] = content.decode("utf-8", errors="ignore")
    return fields, files


def _validate_payload(model, payload: dict) -> DocumentCreateRequest:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(
            status_code=422,
            code="DOCUMENT_VALIDATION_FAILED",
            message="La solicitud contiene datos documentales invalidos.",
            details=[
                {
                    "field": ".".join(str(item) for item in error.get("loc", [])),
                    "message": error.get("msg", "Dato invalido."),
                }
                for error in exc.errors()
            ],
        ) from exc


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _payload_error(message: str) -> ApiError:
    return ApiError(
        status_code=422,
        code="DOCUMENT_VALIDATION_FAILED",
        message=message,
    )
