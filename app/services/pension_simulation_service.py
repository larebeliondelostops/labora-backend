import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import LaboraCase
from app.models.document import Document, DocumentType
from app.models.paywall import LockedFeature, Paywall, PreviewResult
from app.models.payment import Order
from app.models.pension import (
    CaseEntitlement,
    LegalRoute,
    PensionAffiliateProfile,
    PensionAlert,
    PensionLegalParameter,
    PensionMonthlyContribution,
    PensionSimulation,
    PensionSimulationAssumptions,
    PensionSimulationScenario,
)
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.payment_repository import PaymentRepository
from app.schemas.pension import DISCLAIMER_TEXT, PensionAffiliateProfilePatch, PensionAssumptionsRequest
from app.services.case_state_machine import step_for_status, validate_case_transition
from app.services.consent_service import ConsentComplianceService
from app.utils.dates import utc_now


CALCULATION_VERSION = "rais-prepayment-v1"
DISCLAIMER_VERSION = "preliminary-pension-simulation-v1"
LEGAL_DRAFT_PRODUCT_CODE = "LEGAL_DRAFT_GENERATION"
LEGAL_DRAFT_PRODUCT_NAME = "Generacion de borrador juridico"
LEGAL_DRAFT_PRODUCT_DESCRIPTION = "Desbloquea la seleccion de plantilla y generacion de borrador juridico."


class PensionSimulationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.payments = PaymentRepository(db)
        self.audit_events = AuditEventRepository(db)

    def get_profile(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        profile = self._profile_for_case(case)
        return self._profile_payload(case, profile)

    def update_profile(
        self,
        case_id: str,
        payload: PensionAffiliateProfilePatch,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._case_for_update(case_id, user, ip_address, user_agent)
        profile = self._profile_for_case(case, create=True)
        previous = self._profile_state(profile)
        data = payload.model_dump(exclude_unset=True)
        mapping = {
            "document_type": "document_type",
            "document_number": "document_number",
            "full_name": "full_name",
            "birth_date": "birth_date",
            "age": "age",
            "sex": "sex",
            "fund_name": "fund_name",
            "inferred_regime": "inferred_regime",
            "current_weeks": "current_weeks",
            "current_individual_account_balance": "current_individual_account_balance",
            "current_monthly_income_reference": "current_monthly_income_reference",
            "has_pension_bond": "has_pension_bond",
            "pension_bond_estimated_value": "pension_bond_estimated_value",
            "data_confidence_score": "data_confidence_score",
        }
        for key, attr in mapping.items():
            if key not in data:
                continue
            value = data[key]
            if key == "birth_date" and value:
                value = date.fromisoformat(str(value))
            if key in {
                "current_weeks",
                "current_individual_account_balance",
                "current_monthly_income_reference",
                "pension_bond_estimated_value",
                "data_confidence_score",
            } and value is not None:
                value = Decimal(str(value))
            setattr(profile, attr, value)
        profile.missing_fields = self._missing_profile_fields(profile)
        profile.updated_at = utc_now()
        if profile.inferred_regime in {"RAIS", "RPM", "UNKNOWN"}:
            case.pension_regime = profile.inferred_regime
        if profile.fund_name:
            case.pension_fund_or_entity = profile.fund_name
        case.updated_at = utc_now()
        self._audit(
            "pension.profile.extracted",
            actor=user,
            case=case,
            previous_state=previous,
            new_state=self._profile_state(profile),
            metadata={"source": "manual_or_review"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._profile_payload(case, profile)

    def list_contributions(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        rows = (
            self.db.query(PensionMonthlyContribution)
            .filter(PensionMonthlyContribution.case_id == case.id)
            .order_by(PensionMonthlyContribution.period)
            .all()
        )
        return {"items": [self._contribution_payload(item) for item in rows]}

    def default_assumptions(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        profile = self._profile_for_case(case)
        target_age = 62
        if profile and profile.sex == "F":
            target_age = 57
        pension_bond_value = profile.pension_bond_estimated_value if profile else None
        return {
            "targetAge": target_age,
            "incomeReferenceMethod": "last_36_month_avg",
            "userIncomeInput": float(profile.current_monthly_income_reference) if profile and profile.current_monthly_income_reference else None,
            "realReturnAnnualConservative": 0.01,
            "realReturnAnnualBase": 0.03,
            "realReturnAnnualOptimistic": 0.05,
            "realIncomeGrowthAnnual": 0.01,
            "individualAccountRate": 0.115,
            "annuityFactorMethod": "simple_years",
            "expectedPaymentYears": 20,
            "actuarialTableVersion": None,
            "pensionBondValue": float(pension_bond_value) if pension_bond_value else None,
            "spouseOrBeneficiariesInfo": None,
            "monthlyExpenseExpected": None,
            "targetMonthlyPension": None,
        }

    def create_assumptions(
        self,
        case_id: str,
        payload: PensionAssumptionsRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._case_for_update(case_id, user, ip_address, user_agent)
        data = payload.model_dump()
        item = PensionSimulationAssumptions(
            case_id=case.id,
            target_age=data["target_age"],
            income_reference_method=data["income_reference_method"],
            user_income_input=_decimal_or_none(data.get("user_income_input")),
            real_return_annual_conservative=Decimal(str(data["real_return_annual_conservative"])),
            real_return_annual_base=Decimal(str(data["real_return_annual_base"])),
            real_return_annual_optimistic=Decimal(str(data["real_return_annual_optimistic"])),
            real_income_growth_annual=Decimal(str(data["real_income_growth_annual"])),
            individual_account_rate=Decimal(str(data["individual_account_rate"])),
            annuity_factor_method=data["annuity_factor_method"],
            expected_payment_years=data.get("expected_payment_years"),
            actuarial_table_version=data.get("actuarial_table_version"),
            pension_bond_value=_decimal_or_none(data.get("pension_bond_value")),
            spouse_or_beneficiaries_info=data.get("spouse_or_beneficiaries_info"),
            monthly_expense_expected=_decimal_or_none(data.get("monthly_expense_expected")),
            target_monthly_pension=_decimal_or_none(data.get("target_monthly_pension")),
            created_by=user.id,
            created_at=utc_now(),
        )
        self.db.add(item)
        self._audit(
            "pension.assumptions.created",
            actor=user,
            case=case,
            new_state={"assumptionsId": str(item.id), "targetAge": item.target_age},
            metadata=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(item)
        return self._assumptions_payload(item)

    def run_simulation(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._case_for_update(case_id, user, ip_address, user_agent)
        self._require_consents(case.owner_user_id)
        self._require_pension_history(case)
        profile = self._require_profile(case)
        missing = self._missing_profile_fields(profile)
        if missing:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PENSION_CRITICAL_DATA_MISSING",
                message="No fue posible calcular la pension porque faltan datos criticos.",
                details={"missingFields": missing},
            )
        assumptions = self._latest_assumptions(case)
        if assumptions is None:
            defaults = PensionAssumptionsRequest(**self.default_assumptions(str(case.id), user=user, ip_address=ip_address, user_agent=user_agent))
            assumptions_payload = self.create_assumptions(
                str(case.id),
                defaults,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            assumptions = self.db.get(PensionSimulationAssumptions, uuid.UUID(assumptions_payload["id"]))
        previous_status = case.status
        self._set_case_status_if_allowed(
            case,
            "pension_simulation_running",
            reason="Simulacion pensional preliminar iniciada.",
            source_module="pension_simulation",
            metadata=None,
        )
        self._audit(
            "pension.simulation.started",
            actor=user,
            case=case,
            previous_state={"status": previous_status},
            new_state={"status": case.status},
            metadata={"assumptionsId": str(assumptions.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        legal_params = self._legal_params_by_year()
        scenarios = self._run_rais_projection(profile, self._contributions_for_case(case), assumptions, legal_params)
        diagnosis = self._diagnosis(scenarios, missing)
        confidence = self._simulation_confidence(profile, scenarios)
        simulation = PensionSimulation(
            case_id=case.id,
            assumptions_id=assumptions.id,
            status="completed",
            calculation_version=CALCULATION_VERSION,
            disclaimer_version=DISCLAIMER_VERSION,
            overall_diagnosis=diagnosis,
            confidence_score=confidence,
            visible_before_payment=True,
            created_at=utc_now(),
            completed_at=utc_now(),
        )
        self.db.add(simulation)
        self.db.flush()
        for scenario in scenarios:
            self.db.add(PensionSimulationScenario(simulation_id=simulation.id, **scenario))
        self._replace_alerts(case=case, simulation=simulation, scenarios=scenarios, diagnosis=diagnosis)
        route = self._create_or_replace_route(case=case, simulation=simulation, diagnosis=diagnosis, scenarios=scenarios)
        self._ensure_entitlement(case.id, "free_pension_simulation", None)
        case.has_free_simulation = True
        target_status = "legal_route_suggested" if route.route_type != "no_action" else "pension_simulation_ready"
        self._set_case_status_if_allowed(
            case,
            target_status,
            reason="Simulacion pensional preliminar completada.",
            source_module="pension_simulation",
            metadata={"simulationId": str(simulation.id), "visibleBeforePayment": True},
        )
        self._audit(
            "pension.simulation.completed",
            actor=user,
            case=case,
            new_state={"simulationId": str(simulation.id), "diagnosis": diagnosis, "status": case.status},
            metadata={"visibleBeforePayment": True},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(simulation)
        return self._simulation_payload(case, simulation)

    def latest_simulation(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        simulation = self._latest_simulation(case)
        if simulation is None:
            raise ApiError(status_code=404, code="PENSION_SIMULATION_NOT_FOUND", message="No hay simulacion pensional para este expediente.")
        self._audit(
            "pension.result.viewed",
            actor=user,
            case=case,
            metadata={"simulationId": str(simulation.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._simulation_payload(case, simulation)

    def get_simulation(self, case_id: str, simulation_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        simulation = self.db.get(PensionSimulation, _parse_uuid_or_error(simulation_id))
        if simulation is None or simulation.case_id != case.id:
            raise ApiError(status_code=404, code="PENSION_SIMULATION_NOT_FOUND", message="Simulacion no encontrada.")
        return self._simulation_payload(case, simulation)

    def list_alerts(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        alerts = (
            self.db.query(PensionAlert)
            .filter(PensionAlert.case_id == case.id)
            .order_by(desc(PensionAlert.created_at))
            .all()
        )
        return {"items": [self._alert_payload(item) for item in alerts]}

    def legal_route(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        route = self._latest_route(case)
        if route is None:
            simulation = self._latest_simulation(case)
            if simulation is None:
                raise ApiError(status_code=404, code="LEGAL_ROUTE_NOT_FOUND", message="Aun no hay ruta juridica sugerida.")
            scenarios = [self._scenario_payload(row) for row in simulation.scenarios]
            route = self._create_or_replace_route(
                case=case,
                simulation=simulation,
                diagnosis=simulation.overall_diagnosis,
                scenarios=[self._scenario_values(row) for row in simulation.scenarios],
            )
            self.db.commit()
        return self._route_payload(route)

    def entitlements(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        self._ensure_entitlement(case.id, "free_pension_simulation", None)
        rows = (
            self.db.query(CaseEntitlement)
            .filter(CaseEntitlement.case_id == case.id)
            .order_by(CaseEntitlement.created_at)
            .all()
        )
        self.db.commit()
        return {"items": [self._entitlement_payload(item) for item in rows]}

    def legal_draft_paywall(self, case_id: str, *, user: User, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
        case = self._case_for_view(case_id, user, ip_address, user_agent)
        simulation = self._latest_simulation(case)
        entitlement = self._active_entitlement(case.id, "legal_draft_generation")
        return {
            "caseId": str(case.id),
            "paywallType": "legal_draft_generation",
            "freeSimulationCompleted": simulation is not None and simulation.status == "completed",
            "priceCop": self._legal_draft_price(),
            "unlocks": [
                "Seleccion de plantilla juridica",
                "Borrador editable de demanda o reclamacion",
                "Control de calidad automatico",
                "Exportacion Word/PDF",
                "Opcion de revision profesional",
            ],
            "paymentRequired": entitlement is None,
            "entitlementActive": entitlement is not None,
        }

    def create_legal_draft_order(
        self,
        case_id: str,
        *,
        return_url: str | None,
        cancel_url: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._case_for_update(case_id, user, ip_address, user_agent)
        if self._latest_simulation(case) is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PENSION_SIMULATION_REQUIRED",
                message="Debes completar la simulacion pensional gratuita antes de pagar por el borrador juridico.",
            )
        entitlement = self._active_entitlement(case.id, "legal_draft_generation")
        if entitlement is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="LEGAL_DRAFT_ALREADY_UNLOCKED",
                message="El borrador juridico ya esta desbloqueado para este expediente.",
            )
        existing = self.payments.active_order_for_case(case_id=case.id, product_code=LEGAL_DRAFT_PRODUCT_CODE)
        if existing is not None and existing.total_amount == self._legal_draft_price():
            return {"order": self._order_payload(existing)}
        if existing is not None and existing.status != "paid":
            existing.status = "expired"
            existing.updated_at = utc_now()
        preview, paywall = self._ensure_legal_draft_paywall(case)
        now = utc_now()
        order = self.payments.create_order(
            case_id=case.id,
            user_id=case.owner_user_id,
            paywall_id=paywall.id,
            status="created",
            currency=settings.payment_currency,
            subtotal_amount=self._legal_draft_price(),
            tax_amount=0,
            discount_amount=0,
            total_amount=self._legal_draft_price(),
            product_code=LEGAL_DRAFT_PRODUCT_CODE,
            product_name=LEGAL_DRAFT_PRODUCT_NAME,
            description=LEGAL_DRAFT_PRODUCT_DESCRIPTION,
            expires_at=now + _payment_expiration_delta(),
            metadata_json={"returnUrl": return_url, "cancelUrl": cancel_url, "previewId": str(preview.id), "paywallType": "legal_draft_generation"},
            created_at=now,
            updated_at=now,
        )
        self._set_case_status_if_allowed(
            case,
            "legal_document_paywall",
            reason="Paywall de borrador juridico generado.",
            source_module="payments",
            metadata={"orderId": str(order.id), "productCode": LEGAL_DRAFT_PRODUCT_CODE},
        )
        self._audit(
            "payment.created",
            actor=user,
            case=case,
            new_state={"orderId": str(order.id), "productCode": LEGAL_DRAFT_PRODUCT_CODE},
            metadata=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"order": self._order_payload(order)}

    def _run_rais_projection(
        self,
        profile: PensionAffiliateProfile,
        contributions: list[PensionMonthlyContribution],
        assumptions: PensionSimulationAssumptions,
        legal_params: dict[int, PensionLegalParameter],
    ) -> list[dict[str, Any]]:
        ibc_base = self._reference_ibc(contributions, assumptions)
        if ibc_base <= 0 and profile.current_monthly_income_reference:
            ibc_base = Decimal(profile.current_monthly_income_reference)
        current_balance = Decimal(profile.current_individual_account_balance or 0)
        current_weeks = Decimal(profile.current_weeks or 0)
        current_age = int(profile.age or 0)
        months = max(0, (assumptions.target_age - current_age) * 12)
        results: list[dict[str, Any]] = []
        for scenario_name, annual_return in [
            ("conservative", assumptions.real_return_annual_conservative),
            ("base", assumptions.real_return_annual_base),
            ("optimistic", assumptions.real_return_annual_optimistic),
        ]:
            monthly_return = _monthly_rate(annual_return)
            monthly_income_growth = _monthly_rate(assumptions.real_income_growth_annual)
            balance = current_balance
            weeks = current_weeks
            ibc = ibc_base
            contribution_sum = Decimal("0")
            for month_index in range(1, months + 1):
                projection_year = utc_now().year + ((month_index - 1) // 12)
                params = legal_params.get(projection_year) or legal_params[max(legal_params)]
                ibc = ibc * (Decimal("1") + monthly_income_growth)
                ibc_validated = min(max(ibc, params.ibc_min_value), params.ibc_max_value)
                contribution = ibc_validated * Decimal(assumptions.individual_account_rate)
                contribution_sum += contribution
                balance = balance * (Decimal("1") + monthly_return) + contribution
                weeks += Decimal(30) / Decimal(7)
            capital = balance + Decimal(assumptions.pension_bond_value or 0)
            monthly_pension = self._capital_to_monthly_pension(capital, assumptions)
            replacement_rate = monthly_pension / ibc_base if ibc_base > 0 else Decimal("0")
            params = legal_params.get(utc_now().year) or legal_params[max(legal_params)]
            smmlv_multiple = monthly_pension / params.smmlv if params.smmlv > 0 else Decimal("0")
            capital_needed = (assumptions.target_monthly_pension or params.smmlv) * Decimal(assumptions.expected_payment_years or 20) * Decimal(12)
            gap = max(Decimal("0"), Decimal(capital_needed) - capital)
            alerts = []
            if replacement_rate < Decimal("0.30"):
                alerts.append("Tasa de reemplazo proyectada inferior al 30%.")
            if smmlv_multiple < Decimal("1.00"):
                alerts.append("La mesada proyectada es inferior a 1 SMMLV actual.")
            qualifies_gpm = (
                weeks >= Decimal(params.rais_minimum_guarantee_weeks)
                and assumptions.target_age >= (params.rais_minimum_guarantee_age_female if profile.sex == "F" else params.rais_minimum_guarantee_age_male)
            )
            results.append(
                {
                    "scenario": scenario_name,
                    "projected_balance": _money(capital),
                    "projected_weeks": _number(weeks),
                    "projected_monthly_pension_today_value": _money(monthly_pension),
                    "projected_monthly_pension_nominal": None,
                    "replacement_rate": _rate(replacement_rate),
                    "smmlv_multiple": _rate(smmlv_multiple),
                    "capital_gap": _money(gap),
                    "additional_savings_required": _money(gap / Decimal(months)) if months else _money(gap),
                    "can_retire_by_capital": capital >= capital_needed,
                    "qualifies_minimum_guarantee": qualifies_gpm,
                    "alerts": alerts,
                    "formula_trace": {
                        "source": "deterministic_rais_projection",
                        "calculationVersion": CALCULATION_VERSION,
                        "ibcBase": float(ibc_base),
                        "monthsProjected": months,
                        "annualReturn": float(annual_return),
                        "monthlyReturnFormula": "(1 + annual_return) ** (1 / 12) - 1",
                        "monthlyReturn": float(monthly_return),
                        "individualAccountRate": float(assumptions.individual_account_rate),
                        "contributionSum": float(_money(contribution_sum)),
                        "annuityFactorMethod": assumptions.annuity_factor_method,
                        "expectedPaymentYears": assumptions.expected_payment_years,
                    },
                }
            )
        return results

    def _diagnosis(self, scenarios: list[dict[str, Any]], missing: list[str]) -> str:
        if missing:
            return "insufficient_information"
        base = next((item for item in scenarios if item["scenario"] == "base"), scenarios[0])
        replacement_rate = Decimal(str(base["replacement_rate"]))
        if base["qualifies_minimum_guarantee"] and replacement_rate < Decimal("0.30"):
            return "possible_minimum_guarantee"
        if replacement_rate < Decimal("0.30"):
            return "high_risk_low_replacement"
        if replacement_rate < Decimal("0.50"):
            return "medium_risk_low_replacement"
        return "appears_sufficient"

    def _create_or_replace_route(
        self,
        *,
        case: LaboraCase,
        simulation: PensionSimulation,
        diagnosis: str,
        scenarios: list[dict[str, Any]],
    ) -> LegalRoute:
        existing = self._latest_route(case)
        route_type = "no_action"
        reason = "La simulacion no muestra por ahora una brecha suficiente para sugerir escrito juridico."
        viability = "low"
        requires_payment = False
        template_key = None
        warnings: list[str] = []
        required_documents = ["pension_history_pdf"]
        if diagnosis in {"high_risk_low_replacement", "medium_risk_low_replacement"}:
            route_type = "request_official_projection"
            reason = "La tasa de reemplazo proyectada puede ser baja; se recomienda pedir proyeccion oficial y revisar soportes antes de demandar."
            viability = "medium"
            requires_payment = True
            template_key = "RAIS_RETIRO_PROGRAMADO"
            required_documents.extend(["afp_projection", "individual_account_statement"])
        if diagnosis == "possible_minimum_guarantee":
            route_type = "administrative_claim"
            reason = "La persona podria acercarse a garantia de pension minima; conviene verificar bono, semanas y respuesta del fondo."
            viability = "medium"
            requires_payment = True
            template_key = "RAIS_GPM"
            required_documents.extend(["pension_bond_status", "official_response"])
        if diagnosis == "insufficient_information":
            route_type = "insufficient_documents"
            reason = "Faltan datos criticos para sugerir una demanda o reclamacion completa."
            viability = "unknown"
            required_documents.extend(["individual_account_statement", "id_document"])
            warnings.append("No se debe generar borrador sin completar los datos faltantes.")
        if existing is None:
            route = LegalRoute(
                case_id=case.id,
                simulation_id=simulation.id,
                route_type=route_type,
                reason=reason,
                viability=viability,
                requires_payment=requires_payment,
                suggested_template_key=template_key,
                required_documents=required_documents,
                warnings=warnings,
            )
            self.db.add(route)
            self.db.flush()
        else:
            route = existing
            route.simulation_id = simulation.id
            route.route_type = route_type
            route.reason = reason
            route.viability = viability
            route.requires_payment = requires_payment
            route.suggested_template_key = template_key
            route.required_documents = required_documents
            route.warnings = warnings
        return route

    def _replace_alerts(
        self,
        *,
        case: LaboraCase,
        simulation: PensionSimulation,
        scenarios: list[dict[str, Any]],
        diagnosis: str,
    ) -> None:
        self.db.query(PensionAlert).filter(PensionAlert.case_id == case.id).delete()
        base = next((item for item in scenarios if item["scenario"] == "base"), scenarios[0])
        severity = "info"
        if diagnosis.startswith("high"):
            severity = "critical"
        elif diagnosis.startswith("medium") or diagnosis == "possible_minimum_guarantee":
            severity = "warning"
        title = {
            "appears_sufficient": "Simulacion sin alerta critica",
            "medium_risk_low_replacement": "Riesgo medio de mesada baja",
            "high_risk_low_replacement": "Riesgo alto de mesada baja",
            "possible_minimum_guarantee": "Posible garantia de pension minima",
            "insufficient_information": "Informacion insuficiente",
        }.get(diagnosis, "Diagnostico preliminar")
        self.db.add(
            PensionAlert(
                case_id=case.id,
                simulation_id=simulation.id,
                severity=severity,
                category="low_replacement_rate" if "replacement" in diagnosis else "data_quality",
                title=title,
                message=f"Escenario base: tasa de reemplazo {float(base['replacement_rate']) * 100:.1f}% y mesada estimada ${int(base['projected_monthly_pension_today_value']):,} COP.",
                action_suggested="review_legal_route" if severity != "info" else None,
            )
        )

    def _capital_to_monthly_pension(self, capital: Decimal, assumptions: PensionSimulationAssumptions) -> Decimal:
        years = Decimal(assumptions.expected_payment_years or 20)
        return capital / (years * Decimal(12)) if years > 0 else Decimal("0")

    def _reference_ibc(self, contributions: list[PensionMonthlyContribution], assumptions: PensionSimulationAssumptions) -> Decimal:
        if assumptions.income_reference_method == "user_input" and assumptions.user_income_input:
            return Decimal(assumptions.user_income_input)
        sorted_rows = sorted(contributions, key=lambda item: item.period, reverse=True)
        limit = 12 if assumptions.income_reference_method == "last_12_month_avg" else 36
        values = [Decimal(item.ibc) for item in sorted_rows[:limit] if item.ibc is not None and Decimal(item.ibc) > 0]
        return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")

    def _legal_params_by_year(self) -> dict[int, PensionLegalParameter]:
        rows = self.db.query(PensionLegalParameter).all()
        if rows:
            return {row.year: row for row in rows}
        year = utc_now().year
        smmlv = Decimal("1423500")
        default = PensionLegalParameter(
            year=year,
            country="CO",
            smmlv=smmlv,
            ibc_min_value=smmlv,
            ibc_max_smmlv=25,
            ibc_max_value=smmlv * Decimal(25),
            rais_minimum_guarantee_weeks=1150,
            rais_minimum_guarantee_age_male=62,
            rais_minimum_guarantee_age_female=57,
            source_label="Parametro provisional configurable",
            valid_from=utc_now(),
        )
        self.db.add(default)
        self.db.flush()
        return {year: default}

    def _simulation_payload(self, case: LaboraCase, simulation: PensionSimulation) -> dict[str, Any]:
        profile = self._profile_for_case(case)
        scenarios = [self._scenario_payload(item) for item in simulation.scenarios]
        route = self._latest_route(case)
        return {
            "caseId": str(case.id),
            "simulationId": str(simulation.id),
            "status": simulation.status,
            "visibleBeforePayment": simulation.visible_before_payment,
            "disclaimer": DISCLAIMER_TEXT,
            "disclaimerVersion": simulation.disclaimer_version,
            "calculationVersion": simulation.calculation_version,
            "overallDiagnosis": simulation.overall_diagnosis,
            "confidenceScore": float(simulation.confidence_score),
            "profileSummary": {
                "regime": profile.inferred_regime if profile else case.pension_regime,
                "fundName": profile.fund_name if profile else case.pension_fund_or_entity,
                "age": profile.age if profile else None,
                "sex": profile.sex if profile else "UNKNOWN",
                "currentWeeks": float(profile.current_weeks) if profile and profile.current_weeks is not None else None,
                "currentBalance": float(profile.current_individual_account_balance) if profile and profile.current_individual_account_balance is not None else None,
            },
            "scenarios": scenarios,
            "recommendedNextStep": {
                "label": "Generar borrador de reclamacion o demanda" if route and route.requires_payment else "Revisar simulacion preliminar",
                "requiresPayment": bool(route.requires_payment) if route else False,
                "reason": route.reason if route else "La simulacion queda disponible antes del pago.",
            },
            "completedAt": simulation.completed_at,
        }

    def _scenario_payload(self, item: PensionSimulationScenario) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "scenario": item.scenario,
            "projectedBalance": float(item.projected_balance),
            "projectedWeeks": float(item.projected_weeks),
            "projectedMonthlyPensionTodayValue": float(item.projected_monthly_pension_today_value),
            "projectedMonthlyPensionNominal": float(item.projected_monthly_pension_nominal) if item.projected_monthly_pension_nominal is not None else None,
            "replacementRate": float(item.replacement_rate),
            "smmlvMultiple": float(item.smmlv_multiple),
            "capitalGap": float(item.capital_gap) if item.capital_gap is not None else None,
            "additionalSavingsRequired": float(item.additional_savings_required) if item.additional_savings_required is not None else None,
            "canRetireByCapital": item.can_retire_by_capital,
            "qualifiesMinimumGuarantee": item.qualifies_minimum_guarantee,
            "alerts": item.alerts,
            "formulaTrace": item.formula_trace,
        }

    def _scenario_values(self, item: PensionSimulationScenario) -> dict[str, Any]:
        return {
            "scenario": item.scenario,
            "replacement_rate": item.replacement_rate,
            "qualifies_minimum_guarantee": item.qualifies_minimum_guarantee,
        }

    def _profile_payload(self, case: LaboraCase, profile: PensionAffiliateProfile | None) -> dict[str, Any]:
        if profile is None:
            return {
                "id": None,
                "caseId": str(case.id),
                "documentType": None,
                "documentNumber": None,
                "fullName": None,
                "birthDate": None,
                "age": None,
                "sex": "UNKNOWN",
                "fundName": case.pension_fund_or_entity,
                "inferredRegime": case.pension_regime,
                "currentWeeks": None,
                "currentIndividualAccountBalance": None,
                "currentMonthlyIncomeReference": None,
                "hasPensionBond": False,
                "pensionBondEstimatedValue": None,
                "dataConfidenceScore": 0,
                "missingFields": ["age", "current_weeks", "current_individual_account_balance", "current_monthly_income_reference"],
            }
        return {
            "id": str(profile.id),
            "caseId": str(case.id),
            "documentType": profile.document_type,
            "documentNumber": profile.document_number,
            "fullName": profile.full_name,
            "birthDate": profile.birth_date.isoformat() if profile.birth_date else None,
            "age": profile.age,
            "sex": profile.sex,
            "fundName": profile.fund_name,
            "inferredRegime": profile.inferred_regime,
            "currentWeeks": float(profile.current_weeks) if profile.current_weeks is not None else None,
            "currentIndividualAccountBalance": float(profile.current_individual_account_balance) if profile.current_individual_account_balance is not None else None,
            "currentMonthlyIncomeReference": float(profile.current_monthly_income_reference) if profile.current_monthly_income_reference is not None else None,
            "hasPensionBond": profile.has_pension_bond,
            "pensionBondEstimatedValue": float(profile.pension_bond_estimated_value) if profile.pension_bond_estimated_value is not None else None,
            "dataConfidenceScore": float(profile.data_confidence_score),
            "missingFields": profile.missing_fields or self._missing_profile_fields(profile),
        }

    def _contribution_payload(self, item: PensionMonthlyContribution) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "period": item.period,
            "contributorName": item.contributor_name,
            "contributorId": item.contributor_id,
            "employmentType": item.employment_type,
            "ibc": float(item.ibc),
            "mandatoryContribution": float(item.mandatory_contribution) if item.mandatory_contribution is not None else None,
            "daysContributed": item.days_contributed,
            "weeksCalculated": float(item.weeks_calculated) if item.weeks_calculated is not None else None,
            "status": item.status,
            "warnings": item.warnings or [],
        }

    def _assumptions_payload(self, item: PensionSimulationAssumptions) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "targetAge": item.target_age,
            "incomeReferenceMethod": item.income_reference_method,
            "userIncomeInput": float(item.user_income_input) if item.user_income_input else None,
            "realReturnAnnualConservative": float(item.real_return_annual_conservative),
            "realReturnAnnualBase": float(item.real_return_annual_base),
            "realReturnAnnualOptimistic": float(item.real_return_annual_optimistic),
            "realIncomeGrowthAnnual": float(item.real_income_growth_annual),
            "individualAccountRate": float(item.individual_account_rate),
            "annuityFactorMethod": item.annuity_factor_method,
            "expectedPaymentYears": item.expected_payment_years,
            "actuarialTableVersion": item.actuarial_table_version,
            "pensionBondValue": float(item.pension_bond_value) if item.pension_bond_value else None,
            "spouseOrBeneficiariesInfo": item.spouse_or_beneficiaries_info,
            "monthlyExpenseExpected": float(item.monthly_expense_expected) if item.monthly_expense_expected else None,
            "targetMonthlyPension": float(item.target_monthly_pension) if item.target_monthly_pension else None,
            "createdAt": item.created_at,
        }

    def _alert_payload(self, item: PensionAlert) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "severity": item.severity,
            "category": item.category,
            "title": item.title,
            "message": item.message,
            "actionSuggested": item.action_suggested,
            "createdAt": item.created_at,
        }

    def _route_payload(self, item: LegalRoute) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "simulationId": str(item.simulation_id) if item.simulation_id else None,
            "routeType": item.route_type,
            "reason": item.reason,
            "viability": item.viability,
            "requiresPayment": item.requires_payment,
            "suggestedTemplateKey": item.suggested_template_key,
            "requiredDocuments": item.required_documents or [],
            "warnings": item.warnings or [],
        }

    def _entitlement_payload(self, item: CaseEntitlement) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "entitlement": item.entitlement,
            "active": item.active,
            "unlockedByPaymentId": str(item.unlocked_by_payment_id) if item.unlocked_by_payment_id else None,
            "createdAt": item.created_at,
        }

    def _order_payload(self, order: Order) -> dict[str, Any]:
        return {
            "id": str(order.id),
            "caseId": str(order.case_id),
            "status": order.status,
            "currency": order.currency,
            "subtotalAmount": order.subtotal_amount,
            "taxAmount": order.tax_amount,
            "discountAmount": order.discount_amount,
            "totalAmount": order.total_amount,
            "productCode": order.product_code,
            "productName": order.product_name,
            "description": order.description,
            "expiresAt": order.expires_at,
            "paidAt": order.paid_at,
        }

    def _ensure_legal_draft_paywall(self, case: LaboraCase) -> tuple[PreviewResult, Paywall]:
        paywall = (
            self.db.query(Paywall)
            .filter(Paywall.case_id == case.id, Paywall.payment_product_code == LEGAL_DRAFT_PRODUCT_CODE)
            .order_by(desc(Paywall.created_at))
            .first()
        )
        if paywall is not None:
            return paywall.preview_result, paywall
        now = utc_now()
        preview = PreviewResult(
            case_id=case.id,
            user_id=case.owner_user_id,
            status="completed",
            summary_title="Simulacion pensional preliminar lista",
            summary_limited="La simulacion gratuita ya esta disponible. El pago solo desbloquea el borrador juridico.",
            alert_level="medium",
            completion_score=Decimal("100.00"),
            hidden_value_hint="Seleccion de plantilla juridica y generacion de borrador.",
            main_finding_teaser="Paywall de documento juridico.",
            confidence_score=Decimal("1.0000"),
            requires_human_review=False,
            ai_model="deterministic",
            ai_prompt_version="none",
            created_at=now,
            updated_at=now,
        )
        self.db.add(preview)
        self.db.flush()
        paywall = Paywall(
            case_id=case.id,
            user_id=case.owner_user_id,
            preview_result_id=preview.id,
            status="blocked",
            unlock_required=True,
            unlock_type="payment",
            payment_product_code=LEGAL_DRAFT_PRODUCT_CODE,
            price_amount=Decimal(self._legal_draft_price()),
            price_currency=settings.payment_currency,
            price_label=_format_price(self._legal_draft_price(), settings.payment_currency),
            created_at=now,
            updated_at=now,
        )
        self.db.add(paywall)
        self.db.flush()
        features = [
            ("legal_template_selection", "Seleccion de plantilla juridica", "Elige demanda, reclamacion o derecho de peticion.", True, 1),
            ("legal_draft_generation", "Borrador editable", "Genera un borrador con datos reales y campos pendientes marcados.", True, 2),
            ("word_pdf_export", "Exportacion Word/PDF", "Descarga del escrito cuando el producto lo permita.", False, 3),
        ]
        for key, title, description, highlighted, sort_order in features:
            self.db.add(
                LockedFeature(
                    paywall_id=paywall.id,
                    feature_key=key,
                    title=title,
                    description=description,
                    is_highlighted=highlighted,
                    sort_order=sort_order,
                )
            )
        return preview, paywall

    def _profile_for_case(self, case: LaboraCase, *, create: bool = False) -> PensionAffiliateProfile | None:
        profile = (
            self.db.query(PensionAffiliateProfile)
            .filter(PensionAffiliateProfile.case_id == case.id)
            .one_or_none()
        )
        if profile is None and create:
            profile = PensionAffiliateProfile(
                case_id=case.id,
                document_type=case.holder_document_type,
                document_number=case.holder_document_number,
                full_name=f"{case.holder_first_name} {case.holder_last_name}".strip(),
                birth_date=case.holder_birth_date,
                age=_age_from_birth_date(case.holder_birth_date),
                sex="UNKNOWN",
                fund_name=case.pension_fund_or_entity,
                inferred_regime=case.pension_regime or "UNKNOWN",
                current_weeks=None,
                current_individual_account_balance=None,
                current_monthly_income_reference=None,
                missing_fields=[],
            )
            profile.missing_fields = self._missing_profile_fields(profile)
            self.db.add(profile)
            self.db.flush()
        return profile

    def _require_profile(self, case: LaboraCase) -> PensionAffiliateProfile:
        profile = self._profile_for_case(case)
        if profile is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PENSION_CRITICAL_DATA_MISSING",
                message="No fue posible calcular la pension porque falta el perfil pensional.",
                details={"missingFields": ["pension_affiliate_profile"]},
            )
        return profile

    def _missing_profile_fields(self, profile: PensionAffiliateProfile) -> list[str]:
        missing = []
        if profile.age is None:
            missing.append("age")
        if profile.current_weeks is None:
            missing.append("current_weeks")
        if profile.current_individual_account_balance is None:
            missing.append("current_individual_account_balance")
        if profile.current_monthly_income_reference is None:
            missing.append("current_monthly_income_reference")
        return missing

    def _profile_state(self, profile: PensionAffiliateProfile) -> dict[str, Any]:
        return {
            "id": str(profile.id),
            "caseId": str(profile.case_id),
            "age": profile.age,
            "weeks": float(profile.current_weeks) if profile.current_weeks is not None else None,
            "balance": float(profile.current_individual_account_balance) if profile.current_individual_account_balance is not None else None,
            "income": float(profile.current_monthly_income_reference) if profile.current_monthly_income_reference is not None else None,
            "missingFields": profile.missing_fields,
        }

    def _latest_assumptions(self, case: LaboraCase) -> PensionSimulationAssumptions | None:
        return (
            self.db.query(PensionSimulationAssumptions)
            .filter(PensionSimulationAssumptions.case_id == case.id)
            .order_by(desc(PensionSimulationAssumptions.created_at))
            .first()
        )

    def _latest_simulation(self, case: LaboraCase) -> PensionSimulation | None:
        return (
            self.db.query(PensionSimulation)
            .filter(PensionSimulation.case_id == case.id)
            .order_by(desc(PensionSimulation.created_at))
            .first()
        )

    def _latest_route(self, case: LaboraCase) -> LegalRoute | None:
        return (
            self.db.query(LegalRoute)
            .filter(LegalRoute.case_id == case.id)
            .order_by(desc(LegalRoute.created_at), desc(LegalRoute.id))
            .first()
        )

    def _contributions_for_case(self, case: LaboraCase) -> list[PensionMonthlyContribution]:
        return (
            self.db.query(PensionMonthlyContribution)
            .filter(PensionMonthlyContribution.case_id == case.id)
            .order_by(PensionMonthlyContribution.period)
            .all()
        )

    def _require_pension_history(self, case: LaboraCase) -> None:
        exists = (
            self.db.query(Document.id)
            .outerjoin(DocumentType, Document.document_type_id == DocumentType.id)
            .filter(
                Document.case_id == case.id,
                Document.deleted_at.is_(None),
                Document.status.in_(["uploaded", "validated", "accepted", "ready", "requires_review"]),
                DocumentType.code == "pension_history_pdf",
            )
            .first()
        )
        if exists is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PENSION_HISTORY_DOCUMENT_REQUIRED",
                message="La historia laboral en PDF es obligatoria para correr el calculo MVP.",
                details={"requiredDocumentType": "pension_history_pdf"},
            )

    def _ensure_entitlement(self, case_id: uuid.UUID, entitlement: str, payment_id: uuid.UUID | None) -> CaseEntitlement:
        existing = (
            self.db.query(CaseEntitlement)
            .filter(CaseEntitlement.case_id == case_id, CaseEntitlement.entitlement == entitlement)
            .one_or_none()
        )
        if existing is not None:
            existing.active = True
            if payment_id is not None:
                existing.unlocked_by_payment_id = payment_id
            return existing
        item = CaseEntitlement(
            case_id=case_id,
            entitlement=entitlement,
            unlocked_by_payment_id=payment_id,
            active=True,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def _active_entitlement(self, case_id: uuid.UUID, entitlement: str) -> CaseEntitlement | None:
        return (
            self.db.query(CaseEntitlement)
            .filter(CaseEntitlement.case_id == case_id, CaseEntitlement.entitlement == entitlement, CaseEntitlement.active.is_(True))
            .one_or_none()
        )

    def _simulation_confidence(self, profile: PensionAffiliateProfile, scenarios: list[dict[str, Any]]) -> Decimal:
        base = Decimal(profile.data_confidence_score or 0)
        if base <= 0:
            base = Decimal("0.7000")
        if any(item["alerts"] for item in scenarios):
            base -= Decimal("0.0500")
        return max(Decimal("0.1000"), min(Decimal("1.0000"), base)).quantize(Decimal("0.0001"))

    def _case_for_view(self, case_id: str, user: User, ip_address: str | None, user_agent: str | None) -> LaboraCase:
        case = self._get_case_or_404(case_id)
        if not self._can_view(case, user):
            self._audit_access_denied(case=case, actor=user, ip_address=ip_address, user_agent=user_agent)
        return case

    def _case_for_update(self, case_id: str, user: User, ip_address: str | None, user_agent: str | None) -> LaboraCase:
        case = self._get_case_or_404(case_id)
        if not self._can_update(case, user):
            self._audit_access_denied(case=case, actor=user, ip_address=ip_address, user_agent=user_agent)
        if case.status in {"closed", "archived", "blocked"}:
            raise ApiError(status_code=status.HTTP_423_LOCKED, code="CASE_LOCKED", message="El expediente esta bloqueado.")
        return case

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(status_code=status.HTTP_404_NOT_FOUND, code="CASE_NOT_FOUND", message="Expediente no encontrado.")
        return case

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if user.role in {"admin", "legal_admin"}:
            return True
        if case.owner_user_id == user.id:
            return True
        return self.cases.get_owner(case_id=case.id, user_id=user.id, roles={"authorized_user", "creator", "owner"}) is not None

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if user.role in {"admin", "legal_admin"}:
            return True
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(case_id=case.id, user_id=user.id, roles={"authorized_user", "creator", "owner"})
        return owner is not None and owner.permissions.get("edit_case") is True

    def _audit_access_denied(self, *, case: LaboraCase, actor: User, ip_address: str | None, user_agent: str | None) -> None:
        self._audit(
            "pension.access_denied",
            actor=actor,
            case=case,
            metadata={"blockedReason": "permission_denied"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(status_code=status.HTTP_403_FORBIDDEN, code="UNAUTHORIZED_CASE_ACCESS", message="No tienes permisos para acceder a este expediente.")

    def _set_case_status_if_allowed(
        self,
        case: LaboraCase,
        new_status: str,
        *,
        reason: str,
        source_module: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        if case.status == new_status:
            return
        try:
            validate_case_transition(self.db, case, new_status=new_status, validate_transition=True)
        except ApiError:
            return
        previous_status = case.status
        case.status = new_status
        case.status_reason = reason
        case.current_step, case.next_best_action = step_for_status(new_status)
        case.updated_at = utc_now()
        self.cases.create_status_history(
            case_id=case.id,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            changed_by_user_id=None,
            changed_by_role="system",
            source_module=source_module,
            metadata=metadata,
        )

    def _require_consents(self, user_id: uuid.UUID) -> None:
        permission = ConsentComplianceService(self.db).can_upload_documents(user_id)
        if not permission.allowed:
            raise ApiError(
                status_code=409,
                code="CONSENT_REQUIRED",
                message="Debes aceptar los consentimientos requeridos antes de simular la pension.",
                details={"missingConsentTypes": permission.missing_consent_types, "reason": permission.reason},
            )

    def _legal_draft_price(self) -> int:
        raw = getattr(settings, "LEGAL_DRAFT_GENERATION_PRICE_COP", None)
        if raw is None:
            raw = getattr(settings, "FULL_ANALYSIS_UNLOCK_PRICE_COP", 150000)
        return max(int(raw), 0)

    def _audit(
        self,
        event_type: str,
        *,
        actor: User,
        case: LaboraCase,
        ip_address: str | None,
        user_agent: str | None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        audit_metadata = {"caseId": str(case.id), "sourceModule": "pension_simulation", "actorRole": actor.role}
        if metadata:
            audit_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_type,
            entity_type="case",
            entity_id=case.id,
            actor_user_id=actor.id,
            previous_state=previous_state,
            new_state=new_state,
            metadata=audit_metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )


def grant_entitlement_for_approved_legal_draft_payment(
    db: Session,
    *,
    case: LaboraCase,
    payment_id: uuid.UUID,
) -> CaseEntitlement:
    service = PensionSimulationService(db)
    entitlement = service._ensure_entitlement(case.id, "legal_draft_generation", payment_id)
    case.paid_legal_document_unlocked = True
    if case.status in {"legal_document_paywall", "payment_approved", "payment_pending", "legal_route_suggested", "pension_simulation_ready"}:
        service._set_case_status_if_allowed(
            case,
            "template_selection_required",
            reason="Borrador juridico desbloqueado por pago aprobado.",
            source_module="payments",
            metadata={"paymentId": str(payment_id), "entitlement": "legal_draft_generation"},
        )
    return entitlement


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _monthly_rate(annual_rate: Decimal) -> Decimal:
    return Decimal(str((1 + float(annual_rate)) ** (1 / 12) - 1))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _number(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _age_from_birth_date(value: date | None) -> int | None:
    if value is None:
        return None
    today = utc_now().date()
    return today.year - value.year - ((today.month, today.day) < (value.month, value.day))


def _parse_uuid_or_error(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ApiError(status_code=400, code="VALIDATION_ERROR", message="Identificador UUID invalido.") from exc


def _format_price(amount: int, currency: str) -> str:
    return f"${amount:,} {currency}".replace(",", ".")


def _payment_expiration_delta():
    from datetime import timedelta

    return timedelta(minutes=settings.payment_order_expiration_minutes)

