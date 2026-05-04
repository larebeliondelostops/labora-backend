from fastapi import APIRouter

router = APIRouter()


@router.get("/documents/{document_id}/extraction")
def get_extraction(document_id: str) -> dict[str, str]:
    return {"id": document_id, "status": "placeholder"}
