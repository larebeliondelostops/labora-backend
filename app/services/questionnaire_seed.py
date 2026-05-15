from sqlalchemy.orm import Session

from app.models.questionnaire import (
    ConditionalRule,
    Question,
    QuestionnaireTemplate,
    QuestionOption,
)


GUIDED_CASE_TEMPLATE_CODE = "guided_case_v1"


QUESTIONS = [
    {
        "code": "birth_date",
        "section": "base",
        "label": "Fecha de nacimiento del titular",
        "type": "date",
        "required": True,
        "order": 10,
    },
    {
        "code": "gender",
        "section": "base",
        "label": "Genero con el que se identifica el titular",
        "type": "select",
        "required": False,
        "order": 20,
        "options": [
            ("female", "Femenino"),
            ("male", "Masculino"),
            ("other", "Otro"),
            ("prefer_not_to_say", "Prefiero no decir"),
        ],
    },
    {
        "code": "pension_fund",
        "section": "base",
        "label": "Fondo o entidad pensional",
        "type": "text",
        "required": True,
        "order": 30,
    },
    {
        "code": "case_goal",
        "section": "base",
        "label": "Objetivo principal del caso",
        "type": "select",
        "required": True,
        "order": 40,
        "options": [
            ("labor_history_review", "Revisar historia laboral"),
            ("missing_weeks", "Encontrar semanas o tiempos faltantes"),
            ("reliquidation", "Revisar liquidacion o reliquidacion"),
            ("recognition", "Solicitar reconocimiento pensional"),
            ("not_sure", "No estoy seguro"),
        ],
    },
    {
        "code": "current_pension_status",
        "section": "pension",
        "label": "Estado pensional actual",
        "type": "select",
        "required": True,
        "order": 100,
        "options": [
            ("not_pensioned_yet", "Aun no pensionado"),
            ("pensioned", "Ya pensionado"),
            ("denied", "Solicitud negada"),
            ("in_process", "Solicitud en tramite"),
            ("not_sure", "No estoy seguro"),
        ],
    },
    {
        "code": "has_resolution",
        "section": "pension",
        "label": "Tiene resolucion o acto administrativo pensional",
        "type": "boolean",
        "required": True,
        "order": 110,
    },
    {
        "code": "resolution_date",
        "section": "pension",
        "label": "Fecha de la resolucion",
        "type": "date",
        "required": False,
        "order": 120,
    },
    {
        "code": "has_prior_claim",
        "section": "pension",
        "label": "Ha presentado reclamaciones previas ante el fondo o entidad",
        "type": "boolean",
        "required": True,
        "order": 130,
    },
    {
        "code": "believes_missing_weeks",
        "section": "missing_weeks",
        "label": "Cree que faltan semanas, periodos o tiempos laborales",
        "type": "boolean",
        "required": True,
        "order": 200,
    },
    {
        "code": "missing_weeks_description",
        "section": "missing_weeks",
        "label": "Describa los periodos, empleadores o tiempos que cree faltantes",
        "type": "textarea",
        "required": False,
        "order": 210,
    },
    {
        "code": "has_supporting_documents",
        "section": "missing_weeks",
        "label": "Tiene soportes de esos periodos faltantes",
        "type": "boolean",
        "required": False,
        "order": 220,
    },
    {
        "code": "has_public_sector_work",
        "section": "public_server",
        "label": "Trabajo en entidades publicas o como servidor publico",
        "type": "boolean",
        "required": True,
        "order": 300,
    },
    {
        "code": "public_entity_names",
        "section": "public_server",
        "label": "Indique las entidades publicas donde trabajo",
        "type": "textarea",
        "required": False,
        "order": 310,
    },
    {
        "code": "has_public_service_certificates",
        "section": "public_server",
        "label": "Tiene certificaciones de tiempo de servicio publico",
        "type": "boolean",
        "required": False,
        "order": 320,
    },
    {
        "code": "has_teacher_history",
        "section": "teacher",
        "label": "Tiene historia laboral docente o vinculacion con magisterio",
        "type": "boolean",
        "required": True,
        "order": 400,
    },
    {
        "code": "teacher_type",
        "section": "teacher",
        "label": "Tipo de vinculacion docente",
        "type": "select",
        "required": False,
        "order": 410,
        "options": [
            ("public_teacher", "Docente oficial"),
            ("private_teacher", "Docente privado"),
            ("mixed", "Mixto"),
            ("not_sure", "No estoy seguro"),
        ],
    },
    {
        "code": "has_teacher_appointment_docs",
        "section": "teacher",
        "label": "Tiene actos de nombramiento, posesion o certificados docentes",
        "type": "boolean",
        "required": False,
        "order": 420,
    },
    {
        "code": "believes_wrong_allowance",
        "section": "reliquidation",
        "label": "Cree que la pension fue liquidada con un valor incorrecto",
        "type": "boolean",
        "required": True,
        "order": 500,
    },
    {
        "code": "wrong_allowance_reason",
        "section": "reliquidation",
        "label": "Explique por que cree que el valor esta mal liquidado",
        "type": "textarea",
        "required": False,
        "order": 510,
    },
    {
        "code": "has_salary_supports",
        "section": "reliquidation",
        "label": "Tiene soportes salariales, desprendibles o factores devengados",
        "type": "boolean",
        "required": False,
        "order": 520,
    },
]


RULES = [
    ("question", "resolution_date", "show", "has_resolution", True, 10),
    ("question", "resolution_date", "require", "has_resolution", True, 11),
    ("question", "missing_weeks_description", "show", "believes_missing_weeks", True, 20),
    ("question", "missing_weeks_description", "require", "believes_missing_weeks", True, 21),
    ("question", "has_supporting_documents", "show", "believes_missing_weeks", True, 22),
    ("question", "has_supporting_documents", "require", "believes_missing_weeks", True, 23),
    ("question", "public_entity_names", "show", "has_public_sector_work", True, 30),
    ("question", "public_entity_names", "require", "has_public_sector_work", True, 31),
    ("question", "has_public_service_certificates", "show", "has_public_sector_work", True, 32),
    ("question", "has_public_service_certificates", "require", "has_public_sector_work", True, 33),
    ("question", "teacher_type", "show", "has_teacher_history", True, 40),
    ("question", "teacher_type", "require", "has_teacher_history", True, 41),
    ("question", "has_teacher_appointment_docs", "show", "has_teacher_history", True, 42),
    ("question", "has_teacher_appointment_docs", "require", "has_teacher_history", True, 43),
    ("question", "wrong_allowance_reason", "show", "believes_wrong_allowance", True, 50),
    ("question", "wrong_allowance_reason", "require", "believes_wrong_allowance", True, 51),
    ("question", "has_salary_supports", "show", "believes_wrong_allowance", True, 52),
    ("question", "has_salary_supports", "require", "believes_wrong_allowance", True, 53),
    ("session", "requires_review", "flag_review", "has_public_sector_work", True, 90),
    ("session", "requires_review", "flag_review", "has_teacher_history", True, 91),
    ("session", "requires_review", "flag_review", "believes_missing_weeks", True, 92),
    ("session", "requires_review", "flag_review", "believes_wrong_allowance", True, 93),
]


SECTION_TITLES = {
    "base": "Datos base",
    "pension": "Situacion pensional",
    "missing_weeks": "Semanas o tiempos faltantes",
    "public_server": "Tiempo publico",
    "teacher": "Regimen docente",
    "reliquidation": "Reliquidacion",
}


def ensure_guided_case_template(db: Session) -> QuestionnaireTemplate:
    template = (
        db.query(QuestionnaireTemplate)
        .filter(
            QuestionnaireTemplate.code == GUIDED_CASE_TEMPLATE_CODE,
            QuestionnaireTemplate.version == 1,
        )
        .one_or_none()
    )
    if template is None:
        template = QuestionnaireTemplate(
            code=GUIDED_CASE_TEMPLATE_CODE,
            name="Cuestionario guiado del caso",
            description="Recopila hechos pensionarios relevantes para perfilar el expediente.",
            version=1,
            status="active",
            case_types=[
                "labor_history_analysis",
                "pension_liquidation_review",
                "pension_reliquidation",
                "missing_weeks_review",
                "public_service_time_review",
                "teacher_magisterio_case",
                "special_regime_case",
                "administrative_claim",
                "lawsuit_draft_preparation",
                "not_sure",
            ],
        )
        db.add(template)
        db.flush()

    questions_by_code = {item.code: item for item in template.questions}
    for item in QUESTIONS:
        question = questions_by_code.get(item["code"])
        if question is None:
            question = Question(
                template_id=template.id,
                code=item["code"],
                section=item["section"],
                label=item["label"],
                help_text=item.get("help_text"),
                type=item["type"],
                required=item["required"],
                display_order=item["order"],
                metadata_json={"sectionTitle": SECTION_TITLES[item["section"]]},
            )
            db.add(question)
            db.flush()
            questions_by_code[question.code] = question
        else:
            question.section = item["section"]
            question.label = item["label"]
            question.help_text = item.get("help_text")
            question.type = item["type"]
            question.required = item["required"]
            question.display_order = item["order"]
            question.metadata_json = {"sectionTitle": SECTION_TITLES[item["section"]]}

        option_values = {option.value: option for option in question.options}
        for order, (value, label) in enumerate(item.get("options", []), start=1):
            option = option_values.get(value)
            if option is None:
                db.add(
                    QuestionOption(
                        question_id=question.id,
                        value=value,
                        label=label,
                        display_order=order,
                    )
                )
            else:
                option.label = label
                option.display_order = order

    existing_rules = {
        (rule.target_type, rule.target_code, rule.action, rule.priority): rule
        for rule in template.conditional_rules
    }
    for target_type, target_code, action, answer_code, value, priority in RULES:
        key = (target_type, target_code, action, priority)
        condition = {
            "field": f"answer.{answer_code}",
            "operator": "equals",
            "value": value,
        }
        rule = existing_rules.get(key)
        if rule is None:
            db.add(
                ConditionalRule(
                    template_id=template.id,
                    target_type=target_type,
                    target_code=target_code,
                    condition_json=condition,
                    action=action,
                    priority=priority,
                    enabled=True,
                )
            )
        else:
            rule.condition_json = condition
            rule.enabled = True

    db.flush()
    db.expire(template, ["questions", "conditional_rules"])
    return template
