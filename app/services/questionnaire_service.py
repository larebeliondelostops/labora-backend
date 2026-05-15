import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.case import LaboraCase
from app.models.questionnaire import (
    AnswerVersion,
    CaseProfile,
    CaseQuestionnaireSession,
    ConditionalRule,
    Question,
    QuestionnaireAnswer,
    QuestionnaireTemplate,
)
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.schemas.questionnaire import (
    AdminQuestionnaireReviewRequest,
    AnswerPatchRequest,
    QuestionnaireAnswersRequest,
    QuestionnaireStartRequest,
    QuestionnaireSubmitRequest,
)
from app.services.case_profile_builder import CaseProfileBuilder
from app.services.conditional_rules_engine import ConditionalRulesEngine
from app.services.consent_service import ConsentComplianceService
from app.services.questionnaire_seed import SECTION_TITLES, ensure_guided_case_template
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
INTERNAL_ROLES = {"system", *ADMIN_ROLES}
LOCKED_STATUSES = {"closed", "archived"}
QUESTIONNAIRE_AUDIT_PREFIX = "cuestionario_guiado"


class QuestionnaireService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.audit_events = AuditEventRepository(db)
        self.rules_engine = ConditionalRulesEngine()
        self.profile_builder = CaseProfileBuilder()

    def get_questionnaire(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_sensitive_consent(user)
        template = ensure_guided_case_template(self.db)
        session = self._get_or_create_session(case=case, template=template, user=user)
        self._prefill_answers(case=case, session=session, user=user)
        payload = self._questionnaire_payload(case=case, session=session, template=template)
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.viewed",
            actor=user,
            case=case,
            session=session,
            metadata={"templateCode": template.code, "sessionStatus": session.status},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return payload

    def start_questionnaire(
        self,
        case_id: str,
        payload: QuestionnaireStartRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_sensitive_consent(user)
        self._require_case_allows_writes(case, user)
        template = ensure_guided_case_template(self.db)
        if template.code != payload.template_code:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="QUESTIONNAIRE_TEMPLATE_NOT_FOUND",
                message="Plantilla de cuestionario no encontrada.",
            )
        session = self._get_or_create_session(case=case, template=template, user=user)
        if session.status == "not_started":
            session.status = "in_progress"
        if session.started_at is None:
            session.started_at = utc_now()
        session.updated_at = utc_now()
        self._prefill_answers(case=case, session=session, user=user)
        self._recompute_session(case=case, session=session, template=template)
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.created",
            actor=user,
            case=case,
            session=session,
            metadata={"templateCode": template.code},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._questionnaire_payload(case=case, session=session, template=template)

    def save_answers(
        self,
        case_id: str,
        payload: QuestionnaireAnswersRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_sensitive_consent(user)
        self._require_case_allows_writes(case, user)
        template = ensure_guided_case_template(self.db)
        session = self._get_or_create_session(case=case, template=template, user=user)
        self._assert_session_matches(payload.session_id, session)

        before_visible, _before_required, _before_flag = self._visible_state(
            case=case,
            session=session,
            template=template,
        )
        question_by_code = {question.code: question for question in template.questions}
        changed_questions: list[str] = []
        for item in payload.answers:
            question = question_by_code.get(item.question_code)
            if question is None:
                raise self._invalid_answer(
                    "questionCode",
                    "La pregunta no existe en la plantilla activa.",
                )
            normalized = self._validate_answer(question, item.value)
            answer = self._upsert_answer(
                case=case,
                session=session,
                question=question,
                value=normalized,
                user=user,
                change_reason="autosave",
            )
            if answer.question_code not in changed_questions:
                changed_questions.append(answer.question_code)
        self.db.flush()
        self.db.expire(session, ["answers"])

        if session.status in {"not_started", "completed", "requires_review"}:
            session.status = "in_progress"
            session.completed_at = None
            session.submitted_by = None
        if session.started_at is None:
            session.started_at = utc_now()

        profile = self._recompute_session(case=case, session=session, template=template)
        after_visible, _after_required, _after_flag = self._visible_state(
            case=case,
            session=session,
            template=template,
        )
        newly_visible = sorted(after_visible - before_visible)

        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.updated",
            actor=user,
            case=case,
            session=session,
            metadata={
                "changedQuestions": changed_questions,
                "newlyVisibleQuestions": newly_visible,
                "clientRequestId": payload.client_request_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.profile_recomputed",
            actor=user,
            case=case,
            session=session,
            metadata={"requiresReview": profile.requires_review},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "saved": True,
            "session": self._session_response(session),
            "changedQuestions": changed_questions,
            "newlyVisibleQuestions": newly_visible,
            "profilePreview": self._profile_response(profile),
            "warnings": self._warnings_from_profile(profile),
        }

    def patch_answer(
        self,
        answer_id: str,
        payload: AnswerPatchRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        answer = self._get_answer_or_404(answer_id)
        case = self._get_case_or_404(str(answer.case_id))
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_sensitive_consent(user)
        self._require_case_allows_writes(case, user)
        session = answer.session
        template = session.template
        normalized = self._validate_answer(answer.question, payload.value)
        updated_answer = self._upsert_answer(
            case=case,
            session=session,
            question=answer.question,
            value=normalized,
            user=user,
            change_reason=payload.change_reason or "manual_update",
        )
        profile = self._recompute_session(case=case, session=session, template=template)
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.updated",
            actor=user,
            case=case,
            session=session,
            metadata={"changedQuestions": [answer.question_code], "answerId": str(answer.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "answer": self._answer_response(updated_answer),
            "session": self._session_response(session),
            "profilePreview": self._profile_response(profile),
        }

    def submit_questionnaire(
        self,
        case_id: str,
        payload: QuestionnaireSubmitRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_sensitive_consent(user)
        self._require_case_allows_writes(case, user)
        if not payload.confirm_accuracy:
            raise self._invalid_answer("confirmAccuracy", "Debes confirmar la veracidad de la informacion.")

        template = ensure_guided_case_template(self.db)
        session = self._get_or_create_session(case=case, template=template, user=user)
        self._assert_session_matches(payload.session_id, session)
        missing = self._missing_required_answers(case=case, session=session, template=template)
        if missing:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="MISSING_REQUIRED_ANSWERS",
                message="Faltan respuestas obligatorias del cuestionario.",
                details={"missingQuestions": missing},
            )

        profile = self._recompute_session(case=case, session=session, template=template)
        session.status = "requires_review" if profile.requires_review else "completed"
        session.completed_at = utc_now()
        session.submitted_by = user.id
        session.updated_at = utc_now()
        if profile.requires_review:
            session.requires_review_reason = "El perfil contiene senales que requieren revision juridica."
            self._audit(
                f"{QUESTIONNAIRE_AUDIT_PREFIX}.requires_review",
                actor=user,
                case=case,
                session=session,
                metadata={"criticalFacts": [item["code"] for item in profile.critical_facts]},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.submitted",
            actor=user,
            case=case,
            session=session,
            metadata={"status": session.status, "requiresReview": profile.requires_review},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "session": self._session_response(session),
            "status": session.status,
            "completedAt": session.completed_at,
            "profile": self._profile_response(profile),
            "nextStep": "upload_documents",
            "warnings": self._warnings_from_profile(profile),
        }

    def get_profile(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_sensitive_consent(user)
        template = ensure_guided_case_template(self.db)
        session = self._get_latest_session(case=case, template=template)
        profile = self._get_profile(case.id)
        if profile is None and session is not None:
            profile = self._recompute_session(case=case, session=session, template=template)
            self.db.commit()
        if profile is None:
            return {
                "caseId": str(case.id),
                "status": "not_started",
                "profile": None,
            }
        return {
            "caseId": str(case.id),
            "status": session.status if session else "computed",
            "profile": self._profile_response(profile),
        }

    def list_admin_sessions(
        self,
        *,
        user: User,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        self._require_admin(user)
        query = self.db.query(CaseQuestionnaireSession)
        if status_filter:
            query = query.filter(CaseQuestionnaireSession.status == status_filter)
        total = query.count()
        sessions = (
            query.order_by(CaseQuestionnaireSession.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "data": [self._admin_session_response(session) for session in sessions],
            "pagination": {"page": page, "pageSize": page_size, "total": total},
        }

    def review_admin_session(
        self,
        session_id: str,
        payload: AdminQuestionnaireReviewRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_admin(user)
        session = self._get_session_or_404(session_id)
        case = self._get_case_or_404(str(session.case_id))
        previous_status = session.status
        session.status = payload.status
        session.requires_review_reason = payload.reason
        session.updated_at = utc_now()
        self._audit(
            (
                f"{QUESTIONNAIRE_AUDIT_PREFIX}.approved"
                if payload.status == "completed"
                else f"{QUESTIONNAIRE_AUDIT_PREFIX}.rejected"
            ),
            actor=user,
            case=case,
            session=session,
            previous_state={"status": previous_status},
            new_state={"status": session.status},
            metadata={"reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._admin_session_response(session)

    def recompute_internal_profile(self, case_id: str, *, user: User) -> dict[str, Any]:
        self._require_internal_role(user)
        case = self._get_case_or_404(case_id)
        template = ensure_guided_case_template(self.db)
        session = self._get_or_create_session(case=case, template=template, user=user)
        profile = self._recompute_session(case=case, session=session, template=template)
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.profile_recomputed",
            actor=user,
            case=case,
            session=session,
            metadata={"source": "internal"},
            ip_address=None,
            user_agent=None,
        )
        self.db.commit()
        return {"caseId": str(case.id), "profile": self._profile_response(profile)}

    def _get_or_create_session(
        self,
        *,
        case: LaboraCase,
        template: QuestionnaireTemplate,
        user: User,
    ) -> CaseQuestionnaireSession:
        session = self._get_latest_session(case=case, template=template)
        if session is not None:
            return session
        session = CaseQuestionnaireSession(
            case_id=case.id,
            template_id=template.id,
            status="in_progress",
            completion_percentage=Decimal("0.00"),
            started_at=utc_now(),
            metadata_json={"createdBy": str(user.id), "templateCode": template.code},
        )
        self.db.add(session)
        self.db.flush()
        return session

    def _get_latest_session(
        self,
        *,
        case: LaboraCase,
        template: QuestionnaireTemplate,
    ) -> CaseQuestionnaireSession | None:
        return (
            self.db.query(CaseQuestionnaireSession)
            .filter(
                CaseQuestionnaireSession.case_id == case.id,
                CaseQuestionnaireSession.template_id == template.id,
            )
            .order_by(CaseQuestionnaireSession.created_at.desc())
            .first()
        )

    def _prefill_answers(
        self,
        *,
        case: LaboraCase,
        session: CaseQuestionnaireSession,
        user: User,
    ) -> None:
        existing_codes = {answer.question_code for answer in session.answers}
        question_by_code = {question.code: question for question in session.template.questions}
        prefill_values = {
            "birth_date": case.holder_birth_date.isoformat() if case.holder_birth_date else None,
            "pension_fund": case.pension_fund_or_entity,
        }
        for code, value in prefill_values.items():
            if value is None or code in existing_codes or code not in question_by_code:
                continue
            self._upsert_answer(
                case=case,
                session=session,
                question=question_by_code[code],
                value=value,
                user=user,
                source="prefill",
                change_reason="prefill_from_case",
            )
            existing_codes.add(code)
        self.db.flush()
        self.db.expire(session, ["answers"])

    def _upsert_answer(
        self,
        *,
        case: LaboraCase,
        session: CaseQuestionnaireSession,
        question: Question,
        value: Any,
        user: User,
        change_reason: str | None,
        source: str = "user",
    ) -> QuestionnaireAnswer:
        answer = (
            self.db.query(QuestionnaireAnswer)
            .filter(
                QuestionnaireAnswer.session_id == session.id,
                QuestionnaireAnswer.question_id == question.id,
            )
            .one_or_none()
        )
        is_critical = self._is_critical_answer(question.code, value)
        requires_review = is_critical
        value_text = self._value_text(value)
        if answer is None:
            answer = QuestionnaireAnswer(
                session_id=session.id,
                case_id=case.id,
                question_id=question.id,
                question_code=question.code,
                value=value,
                value_text=value_text,
                source=source,
                confidence=Decimal("0.9000") if source == "prefill" else Decimal("1.0000"),
                is_critical=is_critical,
                requires_review=requires_review,
                version=1,
                created_by=user.id,
                updated_by=user.id,
            )
            self.db.add(answer)
            self.db.flush()
            return answer

        if answer.value != value:
            self.db.add(
                AnswerVersion(
                    answer_id=answer.id,
                    previous_value=answer.value,
                    new_value=value,
                    changed_by=user.id,
                    change_reason=change_reason,
                )
            )
            answer.value = value
            answer.value_text = value_text
            answer.version += 1
        answer.source = source if answer.source == "prefill" and source == "user" else answer.source
        answer.is_critical = is_critical
        answer.requires_review = requires_review
        answer.updated_by = user.id
        answer.updated_at = utc_now()
        self.db.flush()
        return answer

    def _recompute_session(
        self,
        *,
        case: LaboraCase,
        session: CaseQuestionnaireSession,
        template: QuestionnaireTemplate,
    ) -> CaseProfile:
        visible_codes, required_codes, flag_review = self._visible_state(
            case=case,
            session=session,
            template=template,
        )
        answers = self._answer_values(session)
        answered_visible = [
            code
            for code in visible_codes
            if code in answers and not self._is_empty(answers[code])
        ]
        completion = (
            round((len(answered_visible) / len(visible_codes)) * 100, 2)
            if visible_codes
            else 0.0
        )
        session.completion_percentage = Decimal(str(completion))
        session.current_section = self._first_missing_section(
            template=template,
            required_codes=required_codes,
            answers=answers,
        )
        session.updated_at = utc_now()

        profile_payload = self.profile_builder.build(
            case=case,
            answers=answers,
            completion_percentage=completion,
        )
        if flag_review:
            profile_payload["requires_review"] = True
        profile = self._upsert_profile(case=case, session=session, payload=profile_payload)
        self.db.flush()
        return profile

    def _visible_state(
        self,
        *,
        case: LaboraCase,
        session: CaseQuestionnaireSession,
        template: QuestionnaireTemplate,
    ) -> tuple[set[str], set[str], bool]:
        answers = self._answer_values(session)
        context = {
            "case": {
                "type": case.case_type_requested,
                "situation_type": case.situation_type,
                "status": case.status,
            },
            "answer": answers,
        }
        questions_by_code = {question.code: question for question in template.questions}
        show_rule_targets = {
            rule.target_code
            for rule in template.conditional_rules
            if rule.enabled and rule.target_type == "question" and rule.action == "show"
        }
        visible = {
            code: code not in show_rule_targets
            for code in questions_by_code
        }
        required = {
            question.code
            for question in template.questions
            if question.required and visible.get(question.code, False)
        }
        flag_review = False
        for rule in sorted(template.conditional_rules, key=lambda item: item.priority):
            if not rule.enabled:
                continue
            matches = self.rules_engine.evaluate(rule.condition_json, context)
            if not matches:
                continue
            if rule.target_type == "question":
                if rule.action == "show" and rule.target_code in visible:
                    visible[rule.target_code] = True
                elif rule.action == "hide" and rule.target_code in visible:
                    visible[rule.target_code] = False
                    required.discard(rule.target_code)
                elif rule.action == "require" and rule.target_code in visible:
                    visible[rule.target_code] = True
                    required.add(rule.target_code)
            elif rule.target_type == "session" and rule.action == "flag_review":
                flag_review = True

        visible_codes = {code for code, is_visible in visible.items() if is_visible}
        required = {code for code in required if code in visible_codes}
        return visible_codes, required, flag_review

    def _missing_required_answers(
        self,
        *,
        case: LaboraCase,
        session: CaseQuestionnaireSession,
        template: QuestionnaireTemplate,
    ) -> list[dict[str, str]]:
        _visible_codes, required_codes, _flag = self._visible_state(
            case=case,
            session=session,
            template=template,
        )
        answers = self._answer_values(session)
        questions_by_code = {question.code: question for question in template.questions}
        missing = []
        for code in sorted(required_codes, key=lambda item: questions_by_code[item].display_order):
            if self._is_empty(answers.get(code)):
                missing.append({"questionCode": code, "label": questions_by_code[code].label})
        return missing

    def _questionnaire_payload(
        self,
        *,
        case: LaboraCase,
        session: CaseQuestionnaireSession,
        template: QuestionnaireTemplate,
    ) -> dict[str, Any]:
        visible_codes, required_codes, flag_review = self._visible_state(
            case=case,
            session=session,
            template=template,
        )
        answers = {
            answer.question_code: answer
            for answer in session.answers
        }
        sections: list[dict[str, Any]] = []
        section_codes = []
        for question in template.questions:
            if question.section not in section_codes:
                section_codes.append(question.section)
        for section_code in section_codes:
            questions = [
                self._question_response(
                    question,
                    answer=answers.get(question.code),
                    required=question.code in required_codes,
                )
                for question in template.questions
                if question.section == section_code and question.code in visible_codes
            ]
            if not questions:
                continue
            sections.append(
                {
                    "code": section_code,
                    "title": SECTION_TITLES.get(section_code, section_code),
                    "visible": True,
                    "questions": questions,
                }
            )
        profile = self._get_profile(case.id)
        return {
            "caseId": str(case.id),
            "template": {
                "id": str(template.id),
                "code": template.code,
                "version": template.version,
                "name": template.name,
            },
            "session": self._session_response(session),
            "sections": sections,
            "visibleQuestions": sorted(visible_codes),
            "requiredQuestions": sorted(required_codes),
            "requiresReview": bool(flag_review or (profile.requires_review if profile else False)),
            "profilePreview": self._profile_response(profile) if profile else None,
        }

    def _question_response(
        self,
        question: Question,
        *,
        answer: QuestionnaireAnswer | None,
        required: bool,
    ) -> dict[str, Any]:
        return {
            "id": str(question.id),
            "code": question.code,
            "section": question.section,
            "label": question.label,
            "helpText": question.help_text,
            "type": question.type,
            "required": required,
            "order": question.display_order,
            "options": [
                {
                    "value": option.value,
                    "label": option.label,
                    "order": option.display_order,
                    "metadata": option.metadata_json or {},
                }
                for option in question.options
            ],
            "answer": self._answer_response(answer) if answer else None,
        }

    def _validate_answer(self, question: Question, value: Any) -> Any:
        if self._is_empty(value):
            return None
        if question.type == "boolean":
            parsed = self._parse_bool(value)
            if parsed is None:
                raise self._invalid_answer(question.code, "La respuesta debe ser verdadero o falso.")
            return parsed
        if question.type == "date":
            if isinstance(value, date):
                return value.isoformat()
            if not isinstance(value, str):
                raise self._invalid_answer(question.code, "La respuesta debe ser una fecha ISO.")
            try:
                return date.fromisoformat(value[:10]).isoformat()
            except ValueError as exc:
                raise self._invalid_answer(question.code, "La respuesta debe ser una fecha valida.") from exc
        if question.type in {"number", "money"}:
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise self._invalid_answer(question.code, "La respuesta debe ser numerica.") from exc
        if question.type in {"select", "radio"}:
            normalized = str(value).strip()
            valid_values = {option.value for option in question.options}
            if valid_values and normalized not in valid_values:
                raise self._invalid_answer(question.code, "La opcion seleccionada no es valida.")
            return normalized
        if question.type == "checkbox":
            if not isinstance(value, list):
                raise self._invalid_answer(question.code, "La respuesta debe ser una lista.")
            valid_values = {option.value for option in question.options}
            normalized = [str(item).strip() for item in value if str(item).strip()]
            if valid_values and any(item not in valid_values for item in normalized):
                raise self._invalid_answer(question.code, "Una de las opciones seleccionadas no es valida.")
            return normalized
        if question.type in {"text", "textarea"}:
            normalized = " ".join(str(value).strip().split())
            if len(normalized) > 4000:
                raise self._invalid_answer(question.code, "La respuesta supera el limite de caracteres.")
            return normalized
        return value

    def _upsert_profile(
        self,
        *,
        case: LaboraCase,
        session: CaseQuestionnaireSession,
        payload: dict[str, Any],
    ) -> CaseProfile:
        profile = self._get_profile(case.id)
        if profile is None:
            profile = CaseProfile(case_id=case.id)
            self.db.add(profile)
        profile.birth_date = payload["birth_date"]
        profile.gender = payload["gender"]
        profile.pension_fund = payload["pension_fund"]
        profile.current_status = payload["current_status"]
        profile.has_public_sector_work = payload["has_public_sector_work"]
        profile.has_teacher_history = payload["has_teacher_history"]
        profile.has_special_regime_signal = payload["has_special_regime_signal"]
        profile.has_missing_weeks_claim = payload["has_missing_weeks_claim"]
        profile.has_reliquidation_signal = payload["has_reliquidation_signal"]
        profile.has_prior_claim = payload["has_prior_claim"]
        profile.detected_route = payload["detected_route"]
        profile.critical_facts = payload["critical_facts"]
        profile.missing_documents = payload["missing_documents"]
        profile.confidence = payload["confidence"]
        profile.requires_review = payload["requires_review"]
        profile.generated_from_session_id = session.id
        profile.updated_at = utc_now()
        self.db.flush()
        return profile

    def _get_profile(self, case_id: uuid.UUID) -> CaseProfile | None:
        return (
            self.db.query(CaseProfile)
            .filter(CaseProfile.case_id == case_id)
            .one_or_none()
        )

    def _answer_values(self, session: CaseQuestionnaireSession) -> dict[str, Any]:
        return {
            answer.question_code: answer.value
            for answer in session.answers
            if not self._is_empty(answer.value)
        }

    def _first_missing_section(
        self,
        *,
        template: QuestionnaireTemplate,
        required_codes: set[str],
        answers: dict[str, Any],
    ) -> str | None:
        for question in template.questions:
            if question.code in required_codes and self._is_empty(answers.get(question.code)):
                return question.section
        return None

    def _session_response(self, session: CaseQuestionnaireSession) -> dict[str, Any]:
        return {
            "id": str(session.id),
            "caseId": str(session.case_id),
            "templateId": str(session.template_id),
            "status": session.status,
            "completionPercentage": float(session.completion_percentage or 0),
            "currentSection": session.current_section,
            "startedAt": session.started_at,
            "completedAt": session.completed_at,
            "requiresReviewReason": session.requires_review_reason,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }

    def _admin_session_response(self, session: CaseQuestionnaireSession) -> dict[str, Any]:
        return {
            **self._session_response(session),
            "answersCount": len(session.answers),
        }

    def _answer_response(self, answer: QuestionnaireAnswer | None) -> dict[str, Any] | None:
        if answer is None:
            return None
        return {
            "id": str(answer.id),
            "questionCode": answer.question_code,
            "value": answer.value,
            "source": answer.source,
            "version": answer.version,
            "isCritical": answer.is_critical,
            "requiresReview": answer.requires_review,
            "createdAt": answer.created_at,
            "updatedAt": answer.updated_at,
        }

    def _profile_response(self, profile: CaseProfile | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        return {
            "id": str(profile.id),
            "caseId": str(profile.case_id),
            "birthDate": profile.birth_date.isoformat() if profile.birth_date else None,
            "gender": profile.gender,
            "pensionFund": profile.pension_fund,
            "currentStatus": profile.current_status,
            "hasPublicSectorWork": profile.has_public_sector_work,
            "hasTeacherHistory": profile.has_teacher_history,
            "hasSpecialRegimeSignal": profile.has_special_regime_signal,
            "hasMissingWeeksClaim": profile.has_missing_weeks_claim,
            "hasReliquidationSignal": profile.has_reliquidation_signal,
            "hasPriorClaim": profile.has_prior_claim,
            "detectedRoute": profile.detected_route,
            "criticalFacts": profile.critical_facts or [],
            "missingDocuments": profile.missing_documents or [],
            "confidence": float(profile.confidence or 0),
            "requiresReview": profile.requires_review,
            "generatedFromSessionId": (
                str(profile.generated_from_session_id)
                if profile.generated_from_session_id
                else None
            ),
            "createdAt": profile.created_at,
            "updatedAt": profile.updated_at,
        }

    def _warnings_from_profile(self, profile: CaseProfile) -> list[dict[str, Any]]:
        warnings = []
        if profile.requires_review:
            warnings.append(
                {
                    "code": "requires_review",
                    "message": "El cuestionario contiene senales que ameritan revision juridica.",
                }
            )
        for item in profile.missing_documents or []:
            warnings.append(
                {
                    "code": item.get("code"),
                    "message": item.get("reason"),
                    "document": item.get("label"),
                }
            )
        return warnings

    def _get_case_or_404(self, case_id: str) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_session_or_404(self, session_id: str) -> CaseQuestionnaireSession:
        parsed = self._parse_uuid(session_id)
        if parsed is None:
            session = None
        else:
            session = self.db.get(CaseQuestionnaireSession, parsed)
        if session is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="QUESTIONNAIRE_SESSION_NOT_FOUND",
                message="Sesion de cuestionario no encontrada.",
            )
        return session

    def _get_answer_or_404(self, answer_id: str) -> QuestionnaireAnswer:
        parsed = self._parse_uuid(answer_id)
        if parsed is None:
            answer = None
        else:
            answer = self.db.get(QuestionnaireAnswer, parsed)
        if answer is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ANSWER_NOT_FOUND",
                message="Respuesta no encontrada.",
            )
        return answer

    def _assert_session_matches(
        self,
        requested_session_id: str | None,
        session: CaseQuestionnaireSession,
    ) -> None:
        if requested_session_id is None:
            return
        if str(session.id) != str(requested_session_id):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="QUESTIONNAIRE_SESSION_MISMATCH",
                message="La sesion de cuestionario no corresponde al expediente.",
            )

    def _require_sensitive_consent(self, user: User) -> None:
        if user.role in {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, "system"}:
            return
        permission = ConsentComplianceService(self.db).can_upload_documents(user.id)
        if not permission.allowed:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="MISSING_CONSENT",
                message="Debes aceptar los consentimientos requeridos antes de completar el cuestionario.",
                details={
                    "missingConsentTypes": permission.missing_consent_types,
                    "reason": permission.reason,
                },
            )

    def _require_can_view(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_view(case, user):
            return
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.access_denied",
            actor=user,
            case=case,
            metadata={"action": "view"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise self._access_denied()

    def _require_can_update(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_update(case, user):
            return
        self._audit(
            f"{QUESTIONNAIRE_AUDIT_PREFIX}.access_denied",
            actor=user,
            case=case,
            metadata={"action": "update"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise self._access_denied()

    def _require_case_allows_writes(self, case: LaboraCase, user: User) -> None:
        if user.role in ADMIN_ROLES:
            return
        if case.status in LOCKED_STATUSES:
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="CASE_STATUS_BLOCKS_QUESTIONNAIRE",
                message="El estado del expediente no permite modificar el cuestionario.",
            )

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if user.role in ADMIN_ROLES:
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return (
                case.status == "requires_review"
                or self.cases.get_owner(
                    case_id=case.id,
                    user_id=user.id,
                    roles={"legal_reviewer"},
                )
                is not None
            )
        if case.owner_user_id == user.id:
            return True
        return (
            self.cases.get_owner(
                case_id=case.id,
                user_id=user.id,
                roles={"authorized_user", "creator", "owner"},
            )
            is not None
        )

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if user.role in ADMIN_ROLES:
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return False
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _require_admin(self, user: User) -> None:
        if user.role not in ADMIN_ROLES:
            raise self._access_denied()

    def _require_internal_role(self, user: User) -> None:
        if user.role not in INTERNAL_ROLES:
            raise self._access_denied()

    def _audit(
        self,
        event_type: str,
        *,
        actor: User,
        case: LaboraCase,
        session: CaseQuestionnaireSession | None = None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        metadata_payload = {
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            **(metadata or {}),
        }
        if session is not None:
            metadata_payload["sessionId"] = str(session.id)
        self.audit_events.create(
            event_type=event_type,
            entity_type="questionnaire_session" if session else "case",
            entity_id=session.id if session else case.id,
            actor_user_id=actor.id,
            previous_state=previous_state,
            new_state=new_state,
            metadata=metadata_payload,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _invalid_answer(self, field: str, message: str) -> ApiError:
        return ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_ANSWER_VALUE",
            message="La respuesta del cuestionario no es valida.",
            details={"field": field, "message": message},
        )

    def _access_denied(self) -> ApiError:
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CASE_ACCESS_DENIED",
            message="No tienes permisos para acceder a este expediente.",
        )

    def _is_critical_answer(self, question_code: str, value: Any) -> bool:
        if not self._truthy(value):
            return False
        return question_code in {
            "has_public_sector_work",
            "has_teacher_history",
            "believes_missing_weeks",
            "believes_wrong_allowance",
        }

    def _value_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _truthy(self, value: Any) -> bool:
        parsed = self._parse_bool(value)
        if parsed is not None:
            return parsed
        return bool(value)

    def _parse_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "si", "s", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off"}:
                return False
        return None

    def _is_empty(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) == 0
        return False

    def _parse_uuid(self, value: str | uuid.UUID) -> uuid.UUID | None:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except ValueError:
            return None
