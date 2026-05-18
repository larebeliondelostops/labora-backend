import uuid
from typing import Any

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.professional_review import (
    LawyerComment,
    ProfessionalReview,
    ReviewedFile,
    ReviewerAssignment,
    ReviewOrder,
)


FINAL_REVIEW_STATUSES = {"completed", "rejected", "cancelled"}
ACTIVE_ASSIGNMENT_STATUSES = {"assigned", "accepted"}


class ProfessionalReviewRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, review_id: str | uuid.UUID) -> ProfessionalReview | None:
        parsed = _parse_uuid(review_id)
        if parsed is None:
            return None
        return self.db.get(ProfessionalReview, parsed)

    def get_assignment(self, assignment_id: str | uuid.UUID | None) -> ReviewerAssignment | None:
        parsed = _parse_uuid(assignment_id)
        if parsed is None:
            return None
        return self.db.get(ReviewerAssignment, parsed)

    def get_comment(self, comment_id: str | uuid.UUID) -> LawyerComment | None:
        parsed = _parse_uuid(comment_id)
        if parsed is None:
            return None
        return self.db.get(LawyerComment, parsed)

    def get_file(self, file_id: str | uuid.UUID | None) -> ReviewedFile | None:
        parsed = _parse_uuid(file_id)
        if parsed is None:
            return None
        return self.db.get(ReviewedFile, parsed)

    def active_review_for_target(
        self,
        *,
        case_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
    ) -> ProfessionalReview | None:
        return (
            self.db.query(ProfessionalReview)
            .filter(
                ProfessionalReview.case_id == case_id,
                ProfessionalReview.target_type == target_type,
                ProfessionalReview.target_id == target_id,
                ProfessionalReview.deleted_at.is_(None),
                ProfessionalReview.status.notin_(FINAL_REVIEW_STATUSES),
            )
            .order_by(desc(ProfessionalReview.updated_at))
            .first()
        )

    def create_review(self, **values: Any) -> ProfessionalReview:
        item = ProfessionalReview(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_review_order(self, **values: Any) -> ReviewOrder:
        item = ReviewOrder(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_assignment(self, **values: Any) -> ReviewerAssignment:
        item = ReviewerAssignment(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_comment(self, **values: Any) -> LawyerComment:
        item = LawyerComment(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_file(self, **values: Any) -> ReviewedFile:
        item = ReviewedFile(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_reviews(
        self,
        *,
        status: str | None,
        case_id: uuid.UUID | None,
        client_id: uuid.UUID | None,
        lawyer_id: uuid.UUID | None,
        include_all: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[ProfessionalReview], int]:
        query = self.db.query(ProfessionalReview).filter(
            ProfessionalReview.deleted_at.is_(None),
        )
        if not include_all and client_id is not None:
            query = query.filter(ProfessionalReview.client_id == client_id)
        if not include_all and lawyer_id is not None:
            query = query.join(
                ReviewerAssignment,
                ProfessionalReview.reviewer_assignment_id == ReviewerAssignment.id,
            ).filter(
                ReviewerAssignment.lawyer_id == lawyer_id,
                ReviewerAssignment.assignment_status.in_(ACTIVE_ASSIGNMENT_STATUSES | {"completed"}),
            )
        if status:
            query = query.filter(ProfessionalReview.status == status)
        if case_id:
            query = query.filter(ProfessionalReview.case_id == case_id)
        total = query.with_entities(func.count(ProfessionalReview.id)).scalar() or 0
        items = (
            query.order_by(desc(ProfessionalReview.updated_at), desc(ProfessionalReview.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def list_comments(self, review_id: uuid.UUID) -> list[LawyerComment]:
        return (
            self.db.query(LawyerComment)
            .filter(LawyerComment.professional_review_id == review_id)
            .order_by(LawyerComment.created_at)
            .all()
        )

    def list_files(self, review_id: uuid.UUID) -> list[ReviewedFile]:
        return (
            self.db.query(ReviewedFile)
            .filter(ReviewedFile.professional_review_id == review_id)
            .order_by(ReviewedFile.version_number, ReviewedFile.created_at)
            .all()
        )

    def list_assignments(self, review_id: uuid.UUID) -> list[ReviewerAssignment]:
        return (
            self.db.query(ReviewerAssignment)
            .filter(ReviewerAssignment.professional_review_id == review_id)
            .order_by(desc(ReviewerAssignment.assigned_at))
            .all()
        )

    def next_file_version(self, review_id: uuid.UUID) -> int:
        current = (
            self.db.query(func.max(ReviewedFile.version_number))
            .filter(ReviewedFile.professional_review_id == review_id)
            .scalar()
        )
        return int(current or 0) + 1

    def mark_other_files_archived(self, *, review_id: uuid.UUID, keep_file_id: uuid.UUID) -> None:
        (
            self.db.query(ReviewedFile)
            .filter(
                ReviewedFile.professional_review_id == review_id,
                ReviewedFile.id != keep_file_id,
                ReviewedFile.status.in_(["approved", "published"]),
            )
            .update({"status": "archived"}, synchronize_session=False)
        )
        self.db.flush()


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
