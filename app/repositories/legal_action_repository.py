import uuid
from typing import Any

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.case import LaboraCase
from app.models.legal_action import (
    AiGenerationRun,
    DraftComment,
    DraftExport,
    DraftQualityCheck,
    DraftSection,
    DraftVersion,
    LegalAction,
    LegalActionJob,
    LegalDraft,
    LegalTemplate,
)


ACTIVE_ACTION_STATUSES = {
    "not_started",
    "in_progress",
    "generated",
    "requires_review",
    "blocked",
    "completed",
    "error",
}


class LegalActionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_action(self, action_id: str | uuid.UUID) -> LegalAction | None:
        parsed = _parse_uuid(action_id)
        if parsed is None:
            return None
        return self.db.get(LegalAction, parsed)

    def get_draft(self, draft_id: str | uuid.UUID) -> LegalDraft | None:
        parsed = _parse_uuid(draft_id)
        if parsed is None:
            return None
        return self.db.get(LegalDraft, parsed)

    def get_section(self, section_id: str | uuid.UUID) -> DraftSection | None:
        parsed = _parse_uuid(section_id)
        if parsed is None:
            return None
        return self.db.get(DraftSection, parsed)

    def get_export(self, export_id: str | uuid.UUID) -> DraftExport | None:
        parsed = _parse_uuid(export_id)
        if parsed is None:
            return None
        return self.db.get(DraftExport, parsed)

    def get_job(self, job_id: str | uuid.UUID) -> LegalActionJob | None:
        parsed = _parse_uuid(job_id)
        if parsed is None:
            return None
        return self.db.get(LegalActionJob, parsed)

    def active_action_for_case_type(
        self,
        *,
        case_id: uuid.UUID,
        action_type: str,
    ) -> LegalAction | None:
        return (
            self.db.query(LegalAction)
            .filter(
                LegalAction.case_id == case_id,
                LegalAction.action_type == action_type,
                LegalAction.status != "cancelled",
            )
            .order_by(desc(LegalAction.updated_at), desc(LegalAction.created_at))
            .first()
        )

    def list_actions_for_case(self, case_id: uuid.UUID) -> list[LegalAction]:
        return (
            self.db.query(LegalAction)
            .filter(LegalAction.case_id == case_id)
            .order_by(desc(LegalAction.updated_at), desc(LegalAction.created_at))
            .all()
        )

    def create_action(self, **values: Any) -> LegalAction:
        item = LegalAction(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def active_template(self, *, action_type: str, legal_domain: str = "pensional") -> LegalTemplate | None:
        return (
            self.db.query(LegalTemplate)
            .filter(
                LegalTemplate.action_type == action_type,
                LegalTemplate.legal_domain == legal_domain,
                LegalTemplate.is_active.is_(True),
            )
            .order_by(desc(LegalTemplate.template_version), desc(LegalTemplate.created_at))
            .first()
        )

    def create_template(self, **values: Any) -> LegalTemplate:
        item = LegalTemplate(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_draft(self, **values: Any) -> LegalDraft:
        item = LegalDraft(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def latest_draft_for_action(self, action_id: uuid.UUID) -> LegalDraft | None:
        return (
            self.db.query(LegalDraft)
            .filter(LegalDraft.legal_action_id == action_id)
            .order_by(desc(LegalDraft.updated_at), desc(LegalDraft.created_at))
            .first()
        )

    def create_section(self, **values: Any) -> DraftSection:
        item = DraftSection(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def replace_sections(self, draft: LegalDraft, sections: list[dict[str, Any]]) -> list[DraftSection]:
        self.db.query(DraftSection).filter(DraftSection.draft_id == draft.id).delete()
        self.db.flush()
        rows: list[DraftSection] = []
        for item in sections:
            row = DraftSection(draft_id=draft.id, **item)
            self.db.add(row)
            rows.append(row)
        self.db.flush()
        return rows

    def list_sections(self, draft_id: uuid.UUID) -> list[DraftSection]:
        return (
            self.db.query(DraftSection)
            .filter(DraftSection.draft_id == draft_id)
            .order_by(asc(DraftSection.order_index))
            .all()
        )

    def next_version_number(self, draft_id: uuid.UUID) -> int:
        current = (
            self.db.query(func.max(DraftVersion.version_number))
            .filter(DraftVersion.draft_id == draft_id)
            .scalar()
        )
        return int(current or 0) + 1

    def create_version(self, **values: Any) -> DraftVersion:
        item = DraftVersion(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def latest_quality_check(self, draft_id: uuid.UUID) -> DraftQualityCheck | None:
        return (
            self.db.query(DraftQualityCheck)
            .filter(DraftQualityCheck.draft_id == draft_id)
            .order_by(desc(DraftQualityCheck.created_at))
            .first()
        )

    def create_quality_check(self, **values: Any) -> DraftQualityCheck:
        item = DraftQualityCheck(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_export(self, **values: Any) -> DraftExport:
        item = DraftExport(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_exports(self, draft_id: uuid.UUID) -> list[DraftExport]:
        return (
            self.db.query(DraftExport)
            .filter(DraftExport.draft_id == draft_id)
            .order_by(desc(DraftExport.created_at))
            .all()
        )

    def create_comment(self, **values: Any) -> DraftComment:
        item = DraftComment(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_ai_run(self, **values: Any) -> AiGenerationRun:
        item = AiGenerationRun(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_job(self, **values: Any) -> LegalActionJob:
        item = LegalActionJob(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def job_by_idempotency_key(self, key: str) -> LegalActionJob | None:
        return (
            self.db.query(LegalActionJob)
            .filter(LegalActionJob.idempotency_key == key)
            .one_or_none()
        )

    def active_generation_job_for_draft(self, draft_id: uuid.UUID) -> LegalActionJob | None:
        return (
            self.db.query(LegalActionJob)
            .filter(
                LegalActionJob.draft_id == draft_id,
                LegalActionJob.job_type.in_(["legal_draft.generate", "draft_section.regenerate"]),
                LegalActionJob.status.in_(["queued", "processing"]),
            )
            .order_by(desc(LegalActionJob.created_at))
            .first()
        )

    def list_admin_drafts(
        self,
        *,
        status: str | None,
        case_id: uuid.UUID | None,
        action_type: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[LegalDraft, LegalAction, LaboraCase]], int]:
        query = (
            self.db.query(LegalDraft, LegalAction, LaboraCase)
            .join(LegalAction, LegalAction.id == LegalDraft.legal_action_id)
            .join(LaboraCase, LaboraCase.id == LegalDraft.case_id)
        )
        if status:
            query = query.filter(LegalDraft.status == status)
        if case_id:
            query = query.filter(LegalDraft.case_id == case_id)
        if action_type:
            query = query.filter(LegalAction.action_type == action_type)
        total = query.with_entities(func.count(LegalDraft.id)).scalar() or 0
        rows = (
            query.order_by(desc(LegalDraft.updated_at), desc(LegalDraft.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return rows, total


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
