from fastapi import APIRouter

router = APIRouter()


@router.post("/documents/{document_id}/ocr")
def run_ocr(document_id: str) -> dict[str, str]:
    return {"id": document_id, "status": "processing"}


@router.get("/documents/{document_id}/ocr")
def get_ocr(document_id: str) -> dict[str, str]:
    return {"id": document_id, "status": "processed"}
