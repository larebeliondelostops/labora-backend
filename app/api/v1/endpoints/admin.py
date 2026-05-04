from fastapi import APIRouter

router = APIRouter()


@router.get("/cases")
def list_admin_cases() -> list[dict[str, str]]:
    return []


@router.get("/cases/{case_id}")
def get_admin_case(case_id: str) -> dict[str, str]:
    return {"id": case_id, "status": "professional_review"}


@router.patch("/cases/{case_id}/review")
def review_admin_case(case_id: str) -> dict[str, str]:
    return {"message": f"Review scaffold ready for {case_id}"}


@router.post("/cases/{case_id}/notes")
def add_admin_note(case_id: str) -> dict[str, str]:
    return {"message": f"Note scaffold ready for {case_id}"}
