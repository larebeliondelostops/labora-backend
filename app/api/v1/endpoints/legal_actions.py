from fastapi import APIRouter

router = APIRouter()


@router.post("/cases/{case_id}/legal-actions")
def create_legal_action(case_id: str) -> dict[str, str]:
    return {"case_id": case_id, "status": "drafted"}


@router.get("/cases/{case_id}/legal-actions")
def list_legal_actions(case_id: str) -> list[dict[str, str]]:
    return [{"case_id": case_id, "action_type": "executive_summary"}]


@router.get("/legal-actions/{legal_action_id}")
def get_legal_action(legal_action_id: str) -> dict[str, str]:
    return {"id": legal_action_id, "status": "drafted"}
