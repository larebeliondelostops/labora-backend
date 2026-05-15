from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.auth_dependencies import get_current_user_context
from app.core.database import get_db
from app.services.extraction_service import ExtractionService

router = APIRouter()


@router.post("/cases/{case_id}/analysis/start")
def start_analysis(
    case_id: str,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _user, _payload = context
    if not ExtractionService(db).can_case_proceed_to_analysis(case_id):
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="CONFIRMATION_BLOCKED",
            message="No se puede iniciar analisis final sin extraccion confirmada o marcada con pendientes.",
        )
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
