from app.models.analysis import AnalysisResult
from app.models.audit import AuditLog
from app.models.case import LaboraCase
from app.models.consent import Consent
from app.models.document import Document
from app.models.inconsistency import Inconsistency
from app.models.legal_action import LegalAction
from app.models.ocr import OcrResult
from app.models.payment import Payment
from app.models.questionnaire import Questionnaire
from app.models.report import Report
from app.models.user import User

all_models = [
    User,
    Consent,
    LaboraCase,
    Payment,
    Document,
    OcrResult,
    Questionnaire,
    AnalysisResult,
    Inconsistency,
    Report,
    LegalAction,
    AuditLog,
]
