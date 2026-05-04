from fastapi import APIRouter

router = APIRouter()


@router.post("/cases/{case_id}/questionnaire")
def save_questionnaire(case_id: str) -> dict[str, str]:
    return {"case_id": case_id, "status": "saved"}


@router.get("/cases/{case_id}/questionnaire")
def get_questionnaire(case_id: str) -> dict[str, str]:
    return {"case_id": case_id, "status": "pending"}
