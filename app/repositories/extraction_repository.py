import uuid
from typing import Any

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.extraction import (
    ContributionGap,
    ContributionWeek,
    Employer,
    ExtractionAuditEvent,
    ExtractionConfirmation,
    ExtractionField,
    ExtractionIssue,
    ExtractionJob,
    ExtractionRun,
    LaborNovelty,
    LaborPeriod,
    SalaryBase,
    UserCorrection,
)


class ExtractionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_run(self, run_id: str | uuid.UUID) -> ExtractionRun | None:
        parsed = _parse_uuid(run_id)
        if parsed is None:
            return None
        return self.db.get(ExtractionRun, parsed)

    def latest_run(self, case_id: uuid.UUID) -> ExtractionRun | None:
        return (
            self.db.query(ExtractionRun)
            .filter(ExtractionRun.case_id == case_id)
            .order_by(desc(ExtractionRun.created_at))
            .first()
        )

    def running_run(self, case_id: uuid.UUID) -> ExtractionRun | None:
        return (
            self.db.query(ExtractionRun)
            .filter(
                ExtractionRun.case_id == case_id,
                ExtractionRun.status == "in_progress",
            )
            .order_by(desc(ExtractionRun.created_at))
            .first()
        )

    def create_run(self, **values: Any) -> ExtractionRun:
        run = ExtractionRun(**values)
        self.db.add(run)
        self.db.flush()
        return run

    def create_job(self, **values: Any) -> ExtractionJob:
        existing = (
            self.db.query(ExtractionJob)
            .filter(ExtractionJob.idempotency_key == values["idempotency_key"])
            .one_or_none()
        )
        if existing is not None:
            return existing
        job = ExtractionJob(**values)
        self.db.add(job)
        self.db.flush()
        return job

    def create_field(self, **values: Any) -> ExtractionField:
        field = ExtractionField(**values)
        self.db.add(field)
        self.db.flush()
        return field

    def get_field(self, field_id: str | uuid.UUID) -> ExtractionField | None:
        parsed = _parse_uuid(field_id)
        if parsed is None:
            return None
        return self.db.get(ExtractionField, parsed)

    def list_fields(self, case_id: uuid.UUID) -> list[ExtractionField]:
        return (
            self.db.query(ExtractionField)
            .filter(ExtractionField.case_id == case_id)
            .order_by(asc(ExtractionField.created_at))
            .all()
        )

    def list_active_fields(self, case_id: uuid.UUID) -> list[ExtractionField]:
        return (
            self.db.query(ExtractionField)
            .filter(
                ExtractionField.case_id == case_id,
                ExtractionField.status != "ignored",
            )
            .order_by(asc(ExtractionField.created_at))
            .all()
        )

    def find_employer(
        self,
        *,
        case_id: uuid.UUID,
        name: str,
        nit: str | None,
    ) -> Employer | None:
        query = self.db.query(Employer).filter(
            Employer.case_id == case_id,
            Employer.status != "ignored",
            func.lower(Employer.name) == name.lower(),
        )
        if nit:
            query = query.filter(Employer.nit == nit)
        return query.order_by(asc(Employer.created_at)).first()

    def create_employer(self, **values: Any) -> Employer:
        item = Employer(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_employers(self, case_id: uuid.UUID) -> list[Employer]:
        return (
            self.db.query(Employer)
            .filter(Employer.case_id == case_id)
            .order_by(asc(Employer.name))
            .all()
        )

    def create_labor_period(self, **values: Any) -> LaborPeriod:
        item = LaborPeriod(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def find_labor_period(
        self,
        *,
        case_id: uuid.UUID,
        employer_id: uuid.UUID | None,
        start_date,
        end_date,
        period_type: str,
    ) -> LaborPeriod | None:
        query = self.db.query(LaborPeriod).filter(
            LaborPeriod.case_id == case_id,
            LaborPeriod.start_date == start_date,
            LaborPeriod.end_date == end_date,
            LaborPeriod.period_type == period_type,
            LaborPeriod.status != "ignored",
        )
        if employer_id is None:
            query = query.filter(LaborPeriod.employer_id.is_(None))
        else:
            query = query.filter(LaborPeriod.employer_id == employer_id)
        return query.first()

    def overlapping_periods(
        self,
        *,
        case_id: uuid.UUID,
        start_date,
        end_date,
        exclude_period_id: uuid.UUID | None = None,
    ) -> list[LaborPeriod]:
        query = self.db.query(LaborPeriod).filter(
            LaborPeriod.case_id == case_id,
            LaborPeriod.status != "ignored",
            LaborPeriod.start_date <= (end_date or start_date),
            (LaborPeriod.end_date.is_(None)) | (LaborPeriod.end_date >= start_date),
        )
        if exclude_period_id is not None:
            query = query.filter(LaborPeriod.id != exclude_period_id)
        return query.order_by(asc(LaborPeriod.start_date)).all()

    def list_labor_periods(self, case_id: uuid.UUID) -> list[LaborPeriod]:
        return (
            self.db.query(LaborPeriod)
            .filter(LaborPeriod.case_id == case_id)
            .order_by(asc(LaborPeriod.start_date))
            .all()
        )

    def create_contribution_week(self, **values: Any) -> ContributionWeek:
        item = ContributionWeek(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_contribution_weeks(self, case_id: uuid.UUID) -> list[ContributionWeek]:
        return (
            self.db.query(ContributionWeek)
            .filter(ContributionWeek.case_id == case_id)
            .order_by(asc(ContributionWeek.year), asc(ContributionWeek.month))
            .all()
        )

    def create_salary_base(self, **values: Any) -> SalaryBase:
        item = SalaryBase(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_salary_bases(self, case_id: uuid.UUID) -> list[SalaryBase]:
        return (
            self.db.query(SalaryBase)
            .filter(SalaryBase.case_id == case_id)
            .order_by(asc(SalaryBase.period_year), asc(SalaryBase.period_month))
            .all()
        )

    def create_gap(self, **values: Any) -> ContributionGap:
        item = ContributionGap(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_gaps(self, case_id: uuid.UUID) -> list[ContributionGap]:
        return (
            self.db.query(ContributionGap)
            .filter(ContributionGap.case_id == case_id)
            .order_by(asc(ContributionGap.start_date))
            .all()
        )

    def create_novelty(self, **values: Any) -> LaborNovelty:
        item = LaborNovelty(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_novelties(self, case_id: uuid.UUID) -> list[LaborNovelty]:
        return (
            self.db.query(LaborNovelty)
            .filter(LaborNovelty.case_id == case_id)
            .order_by(asc(LaborNovelty.created_at))
            .all()
        )

    def create_correction(self, **values: Any) -> UserCorrection:
        item = UserCorrection(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_corrections(
        self,
        *,
        case_id: uuid.UUID,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
        field_key: str | None,
        page: int,
        limit: int,
    ) -> tuple[list[UserCorrection], int]:
        query = self.db.query(UserCorrection).filter(UserCorrection.case_id == case_id)
        if entity_type:
            query = query.filter(UserCorrection.entity_type == entity_type)
        if entity_id:
            query = query.filter(UserCorrection.entity_id == entity_id)
        if field_key:
            query = query.filter(UserCorrection.field_key == field_key)
        total = query.with_entities(func.count(UserCorrection.id)).scalar() or 0
        items = (
            query.order_by(desc(UserCorrection.created_at))
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return items, total

    def create_confirmation(self, **values: Any) -> ExtractionConfirmation:
        item = ExtractionConfirmation(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_issue(self, **values: Any) -> ExtractionIssue:
        item = ExtractionIssue(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def get_issue(self, issue_id: str | uuid.UUID) -> ExtractionIssue | None:
        parsed = _parse_uuid(issue_id)
        if parsed is None:
            return None
        return self.db.get(ExtractionIssue, parsed)

    def list_issues(self, case_id: uuid.UUID) -> list[ExtractionIssue]:
        return (
            self.db.query(ExtractionIssue)
            .filter(ExtractionIssue.case_id == case_id)
            .order_by(asc(ExtractionIssue.created_at))
            .all()
        )

    def count_open_issues(self, case_id: uuid.UUID) -> int:
        return (
            self.db.query(func.count(ExtractionIssue.id))
            .filter(ExtractionIssue.case_id == case_id, ExtractionIssue.status == "open")
            .scalar()
            or 0
        )

    def create_audit_event(self, **values: Any) -> ExtractionAuditEvent:
        item = ExtractionAuditEvent(**values)
        self.db.add(item)
        self.db.flush()
        return item


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
