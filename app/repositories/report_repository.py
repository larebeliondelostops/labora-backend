import uuid
from typing import Any

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.report import (
    ExportFile,
    Report,
    ReportGenerationJob,
    ReportSection,
    ReportTemplate,
    ReportVersion,
)


ACTIVE_REPORT_STATUSES = {"queued", "generating"}
REUSABLE_REPORT_STATUSES = {"ready", "approved", "requires_review"}


class ReportRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, report_id: str | uuid.UUID) -> Report | None:
        parsed = _parse_uuid(report_id)
        if parsed is None:
            return None
        return self.db.get(Report, parsed)

    def get_version(self, version_id: str | uuid.UUID) -> ReportVersion | None:
        parsed = _parse_uuid(version_id)
        if parsed is None:
            return None
        return self.db.get(ReportVersion, parsed)

    def get_export(self, export_file_id: str | uuid.UUID) -> ExportFile | None:
        parsed = _parse_uuid(export_file_id)
        if parsed is None:
            return None
        return self.db.get(ExportFile, parsed)

    def latest_for_case_type(
        self,
        *,
        case_id: uuid.UUID,
        report_type: str,
        statuses: set[str] | None = None,
    ) -> Report | None:
        query = self.db.query(Report).filter(
            Report.case_id == case_id,
            Report.report_type == report_type,
            Report.deleted_at.is_(None),
        )
        if statuses:
            query = query.filter(Report.status.in_(statuses))
        return query.order_by(desc(Report.updated_at), desc(Report.created_at)).first()

    def list_for_case(
        self,
        *,
        case_id: uuid.UUID,
        report_type: str | None,
        status: str | None,
        page: int,
        limit: int,
    ) -> tuple[list[Report], int]:
        query = self.db.query(Report).filter(
            Report.case_id == case_id,
            Report.deleted_at.is_(None),
        )
        if report_type:
            query = query.filter(Report.report_type == report_type)
        if status:
            query = query.filter(Report.status == status)
        total = query.with_entities(func.count(Report.id)).scalar() or 0
        items = (
            query.order_by(desc(Report.updated_at), desc(Report.created_at))
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return items, total

    def create_report(self, **values: Any) -> Report:
        item = Report(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_job(self, **values: Any) -> ReportGenerationJob:
        item = ReportGenerationJob(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def latest_job(
        self,
        *,
        report_id: uuid.UUID,
        job_type: str | None = None,
    ) -> ReportGenerationJob | None:
        query = self.db.query(ReportGenerationJob).filter(
            ReportGenerationJob.report_id == report_id,
        )
        if job_type:
            query = query.filter(ReportGenerationJob.job_type == job_type)
        return query.order_by(desc(ReportGenerationJob.created_at)).first()

    def replace_sections(self, report: Report, sections: list[dict[str, Any]]) -> list[ReportSection]:
        self.db.query(ReportSection).filter(ReportSection.report_id == report.id).delete()
        self.db.flush()
        rows: list[ReportSection] = []
        for item in sections:
            section = ReportSection(report_id=report.id, **item)
            self.db.add(section)
            rows.append(section)
        self.db.flush()
        return rows

    def next_version_number(self, report_id: uuid.UUID) -> int:
        current = (
            self.db.query(func.max(ReportVersion.version_number))
            .filter(ReportVersion.report_id == report_id)
            .scalar()
        )
        return int(current or 0) + 1

    def mark_versions_superseded(self, report_id: uuid.UUID) -> None:
        self.db.query(ReportVersion).filter(
            ReportVersion.report_id == report_id,
            ReportVersion.status == "current",
        ).update({"status": "superseded"})
        self.db.flush()

    def create_version(self, **values: Any) -> ReportVersion:
        version = ReportVersion(**values)
        self.db.add(version)
        self.db.flush()
        return version

    def current_version(self, report: Report) -> ReportVersion | None:
        if report.current_version_id is not None:
            version = self.get_version(report.current_version_id)
            if version is not None and version.report_id == report.id:
                return version
        return (
            self.db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id)
            .order_by(desc(ReportVersion.version_number))
            .first()
        )

    def list_versions(self, report_id: uuid.UUID) -> list[ReportVersion]:
        return (
            self.db.query(ReportVersion)
            .filter(ReportVersion.report_id == report_id)
            .order_by(desc(ReportVersion.version_number))
            .all()
        )

    def list_ready_exports(self, report_id: uuid.UUID) -> list[ExportFile]:
        return (
            self.db.query(ExportFile)
            .filter(
                ExportFile.report_id == report_id,
                ExportFile.status.in_(["ready", "expired"]),
            )
            .order_by(desc(ExportFile.created_at))
            .all()
        )

    def create_export(self, **values: Any) -> ExportFile:
        item = ExportFile(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def active_template(self, *, template_key: str, report_type: str) -> ReportTemplate | None:
        return (
            self.db.query(ReportTemplate)
            .filter(
                ReportTemplate.template_key == template_key,
                ReportTemplate.report_type == report_type,
                ReportTemplate.status == "active",
            )
            .order_by(desc(ReportTemplate.created_at))
            .first()
        )

    def create_template(self, **values: Any) -> ReportTemplate:
        item = ReportTemplate(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_sections(self, report_id: uuid.UUID) -> list[ReportSection]:
        return (
            self.db.query(ReportSection)
            .filter(ReportSection.report_id == report_id)
            .order_by(asc(ReportSection.order_index))
            .all()
        )


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
