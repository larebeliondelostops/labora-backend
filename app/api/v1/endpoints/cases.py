from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_cases() -> list[dict[str, str]]:
    return []


@router.post("")
def create_case() -> dict[str, str]:
    return {"message": "Case scaffold ready"}


@router.get("/{case_id}")
def get_case(case_id: str) -> dict[str, str]:
    return {"id": case_id, "status": "draft"}


@router.patch("/{case_id}")
def update_case(case_id: str) -> dict[str, str]:
    return {"message": f"Case {case_id} update scaffold ready"}


@router.get("/{case_id}/timeline")
def get_case_timeline(case_id: str) -> list[dict[str, str]]:
    return [{"case_id": case_id, "event": "created"}]
