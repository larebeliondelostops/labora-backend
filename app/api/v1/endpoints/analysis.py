from fastapi import APIRouter

router = APIRouter()


@router.post("/cases/{case_id}/analysis/start")
def start_analysis(case_id: str) -> dict[str, str]:
    return {"case_id": case_id, "status": "analysis_processing"}


@router.get("/cases/{case_id}/analysis/status")
def get_analysis_status(case_id: str) -> dict[str, str]:
    return {"case_id": case_id, "status": "analysis_processing"}


@router.get("/cases/{case_id}/results")
def get_results(case_id: str) -> dict[str, str]:
    return {
        "case_id": case_id,
        "status": "results_available",
        "viability_level": "medium",
    }
