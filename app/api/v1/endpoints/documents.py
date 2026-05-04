from fastapi import APIRouter, File, Form, UploadFile

router = APIRouter()


@router.post("/documents/upload")
async def upload_document(
    case_id: str = Form(...),
    document_type: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, str | int]:
    content = await file.read()
    return {
        "case_id": case_id,
        "document_type": document_type,
        "filename": file.filename or "unknown.pdf",
        "size_bytes": len(content),
        "status": "uploaded",
    }


@router.get("/cases/{case_id}/documents")
def list_case_documents(case_id: str) -> list[dict[str, str]]:
    return [{"case_id": case_id, "status": "uploaded"}]


@router.get("/documents/{document_id}")
def get_document(document_id: str) -> dict[str, str]:
    return {"id": document_id, "status": "uploaded"}


@router.delete("/documents/{document_id}")
def delete_document(document_id: str) -> dict[str, str]:
    return {"message": f"Document {document_id} delete scaffold ready"}


@router.post("/documents/{document_id}/validate")
def validate_document(document_id: str) -> dict[str, str]:
    return {"id": document_id, "status": "valid"}
