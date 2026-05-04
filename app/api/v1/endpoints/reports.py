from fastapi import APIRouter

router = APIRouter()


@router.get("/cases/{case_id}/reports")
def list_reports(case_id: str) -> list[dict[str, str]]:
    return [{"case_id": case_id, "report_type": "executive_summary"}]


@router.post("/cases/{case_id}/reports")
def create_report(case_id: str) -> dict[str, str]:
    return {"case_id": case_id, "status": "generated"}


@router.get("/reports/{report_id}/download")
def download_report(report_id: str) -> dict[str, str]:
    return {"id": report_id, "message": "Download scaffold ready"}
