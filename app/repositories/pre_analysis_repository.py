import uuid
from typing import Any

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.pre_analysis import (
    CaseSignal,
    MissingDocument,
    PreAnalysis,
    PreAnalysisJob,
    PreIssue,
    PreViability,
)


class PreAnalysisRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, pre_analysis_id: str | uuid.UUID) -> PreAnalysis | None:
        parsed = _parse_uuid(pre_analysis_id)
        if parsed is None:
            return None
        return self.db.get(PreAnalysis, parsed)

    def latest_for_case(self, case_id: uuid.UUID) -> PreAnalysis | None:
        return (
            self.db.query(PreAnalysis)
            .filter(PreAnalysis.case_id == case_id, PreAnalysis.deleted_at.is_(None))
            .order_by(desc(PreAnalysis.created_at))
            .first()
        )

    def latest_completed_by_hash(
        self,
        *,
        case_id: uuid.UUID,
        input_hash: str,
    ) -> PreAnalysis | None:
        return (
            self.db.query(PreAnalysis)
            .filter(
                PreAnalysis.case_id == case_id,
                PreAnalysis.input_hash == input_hash,
                PreAnalysis.status == "completed",
                PreAnalysis.deleted_at.is_(None),
            )
            .order_by(desc(PreAnalysis.created_at))
            .first()
        )

    def active_for_case(self, case_id: uuid.UUID) -> PreAnalysis | None:
        return (
            self.db.query(PreAnalysis)
            .filter(
                PreAnalysis.case_id == case_id,
                PreAnalysis.status.in_(["queued", "in_progress"]),
                PreAnalysis.deleted_at.is_(None),
            )
            .order_by(desc(PreAnalysis.created_at))
            .first()
        )

    def create(self, **values: Any) -> PreAnalysis:
        item = PreAnalysis(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_job(self, **values: Any) -> PreAnalysisJob:
        item = PreAnalysisJob(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def latest_job(self, pre_analysis_id: uuid.UUID) -> PreAnalysisJob | None:
        return (
            self.db.query(PreAnalysisJob)
            .filter(PreAnalysisJob.pre_analysis_id == pre_analysis_id)
            .order_by(desc(PreAnalysisJob.queued_at))
            .first()
        )

    def list_jobs(self, pre_analysis_id: uuid.UUID) -> list[PreAnalysisJob]:
        return (
            self.db.query(PreAnalysisJob)
            .filter(PreAnalysisJob.pre_analysis_id == pre_analysis_id)
            .order_by(desc(PreAnalysisJob.queued_at))
            .all()
        )

    def replace_result_rows(
        self,
        *,
        pre_analysis_id: uuid.UUID,
        case_id: uuid.UUID,
        issues: list[dict[str, Any]],
        viability: dict[str, Any],
        missing_documents: list[dict[str, Any]],
        case_signals: list[dict[str, Any]],
    ) -> None:
        self.db.query(PreIssue).filter(PreIssue.pre_analysis_id == pre_analysis_id).delete()
        self.db.query(PreViability).filter(PreViability.pre_analysis_id == pre_analysis_id).delete()
        self.db.query(MissingDocument).filter(
            MissingDocument.pre_analysis_id == pre_analysis_id,
        ).delete()
        self.db.query(CaseSignal).filter(CaseSignal.pre_analysis_id == pre_analysis_id).delete()
        self.db.flush()

        for index, item in enumerate(issues):
            self.db.add(
                PreIssue(
                    pre_analysis_id=pre_analysis_id,
                    case_id=case_id,
                    issue_type=item["type"],
                    severity=item["severity"],
                    title=item["title"],
                    public_summary=item["publicSummary"],
                    locked_detail_available=item.get("lockedDetailAvailable", True),
                    evidence_label=item.get("evidenceLabel"),
                    evidence_refs=item.get("evidenceRefs") or [],
                    confidence=item.get("confidence"),
                    sort_order=index,
                )
            )

        self.db.add(
            PreViability(
                pre_analysis_id=pre_analysis_id,
                case_id=case_id,
                level=viability["level"],
                traffic_light=viability["trafficLight"],
                short_reason=viability["shortReason"],
                public_recommendation=viability["publicRecommendation"],
                confidence=viability.get("confidence"),
            )
        )

        for item in missing_documents:
            self.db.add(
                MissingDocument(
                    case_id=case_id,
                    pre_analysis_id=pre_analysis_id,
                    document_type=item["documentType"],
                    title=item["title"],
                    reason=item.get("reason"),
                    priority=item["priority"],
                    status=item.get("status", "pending"),
                    upload_hint=item.get("uploadHint"),
                )
            )

        for item in case_signals:
            self.db.add(
                CaseSignal(
                    case_id=case_id,
                    pre_analysis_id=pre_analysis_id,
                    signal_type=item["signalType"],
                    title=item["title"],
                    public_summary=item["publicSummary"],
                    confidence=item.get("confidence"),
                    source=item.get("source") or "rules",
                    source_refs=item.get("sourceRefs") or [],
                    is_visible_to_user=item.get("isVisibleToUser", True),
                )
            )
        self.db.flush()

    def list_admin(
        self,
        *,
        status_filter: str | None,
        case_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PreAnalysis], int]:
        query = self.db.query(PreAnalysis).filter(PreAnalysis.deleted_at.is_(None))
        if status_filter:
            query = query.filter(PreAnalysis.status == status_filter)
        if case_id is not None:
            query = query.filter(PreAnalysis.case_id == case_id)
        if user_id is not None:
            query = query.filter(PreAnalysis.user_id == user_id)
        total = query.with_entities(func.count(PreAnalysis.id)).scalar() or 0
        items = (
            query.order_by(desc(PreAnalysis.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def list_visible_issues(self, pre_analysis_id: uuid.UUID) -> list[PreIssue]:
        return (
            self.db.query(PreIssue)
            .filter(PreIssue.pre_analysis_id == pre_analysis_id)
            .order_by(asc(PreIssue.sort_order), asc(PreIssue.created_at))
            .all()
        )

    def list_missing_documents(self, pre_analysis_id: uuid.UUID) -> list[MissingDocument]:
        return (
            self.db.query(MissingDocument)
            .filter(MissingDocument.pre_analysis_id == pre_analysis_id)
            .order_by(asc(MissingDocument.created_at))
            .all()
        )


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
