from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DISCLAIMER_TEXT = (
    "Este resultado es una simulacion preliminar asistida por IA. No reemplaza "
    "la liquidacion oficial de la AFP, una cotizacion de aseguradora, un estudio "
    "actuarial ni revision juridica profesional."
)

Regime = Literal["RAIS", "RPM", "UNKNOWN"]
Sex = Literal["M", "F", "OTHER", "UNKNOWN"]
IncomeReferenceMethod = Literal["last_12_month_avg", "last_36_month_avg", "user_input"]


class PensionAffiliateProfilePatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_type: str | None = Field(alias="documentType", default=None, max_length=30)
    document_number: str | None = Field(alias="documentNumber", default=None, max_length=80)
    full_name: str | None = Field(alias="fullName", default=None, max_length=240)
    birth_date: str | None = Field(alias="birthDate", default=None)
    age: int | None = Field(default=None, ge=0, le=120)
    sex: Sex | None = None
    fund_name: str | None = Field(alias="fundName", default=None, max_length=160)
    inferred_regime: Regime | None = Field(alias="inferredRegime", default=None)
    current_weeks: float | None = Field(alias="currentWeeks", default=None, ge=0)
    current_individual_account_balance: float | None = Field(
        alias="currentIndividualAccountBalance",
        default=None,
        ge=0,
    )
    current_monthly_income_reference: float | None = Field(
        alias="currentMonthlyIncomeReference",
        default=None,
        ge=0,
    )
    has_pension_bond: bool | None = Field(alias="hasPensionBond", default=None)
    pension_bond_estimated_value: float | None = Field(
        alias="pensionBondEstimatedValue",
        default=None,
        ge=0,
    )
    data_confidence_score: float | None = Field(alias="dataConfidenceScore", default=None, ge=0, le=1)


class PensionAffiliateProfileResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    case_id: str = Field(alias="caseId")
    document_type: str | None = Field(alias="documentType", default=None)
    document_number: str | None = Field(alias="documentNumber", default=None)
    full_name: str | None = Field(alias="fullName", default=None)
    birth_date: str | None = Field(alias="birthDate", default=None)
    age: int | None = None
    sex: str
    fund_name: str | None = Field(alias="fundName", default=None)
    inferred_regime: str = Field(alias="inferredRegime")
    current_weeks: float | None = Field(alias="currentWeeks", default=None)
    current_individual_account_balance: float | None = Field(alias="currentIndividualAccountBalance", default=None)
    current_monthly_income_reference: float | None = Field(alias="currentMonthlyIncomeReference", default=None)
    has_pension_bond: bool = Field(alias="hasPensionBond")
    pension_bond_estimated_value: float | None = Field(alias="pensionBondEstimatedValue", default=None)
    data_confidence_score: float = Field(alias="dataConfidenceScore")
    missing_fields: list[str] = Field(alias="missingFields")


class PensionContributionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    period: str
    contributor_name: str | None = Field(alias="contributorName", default=None)
    contributor_id: str | None = Field(alias="contributorId", default=None)
    employment_type: str = Field(alias="employmentType")
    ibc: float
    mandatory_contribution: float | None = Field(alias="mandatoryContribution", default=None)
    days_contributed: int | None = Field(alias="daysContributed", default=None)
    weeks_calculated: float | None = Field(alias="weeksCalculated", default=None)
    status: str
    warnings: list[str]


class PensionAssumptionsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_age: int = Field(alias="targetAge", ge=40, le=80)
    income_reference_method: IncomeReferenceMethod = Field(alias="incomeReferenceMethod")
    user_income_input: float | None = Field(alias="userIncomeInput", default=None, ge=0)
    real_return_annual_conservative: float = Field(alias="realReturnAnnualConservative", default=0.01)
    real_return_annual_base: float = Field(alias="realReturnAnnualBase", default=0.03)
    real_return_annual_optimistic: float = Field(alias="realReturnAnnualOptimistic", default=0.05)
    real_income_growth_annual: float = Field(alias="realIncomeGrowthAnnual", default=0.01)
    individual_account_rate: float = Field(alias="individualAccountRate", default=0.115, ge=0, le=1)
    annuity_factor_method: Literal["simple_years", "actuarial_table"] = Field(
        alias="annuityFactorMethod",
        default="simple_years",
    )
    expected_payment_years: int | None = Field(alias="expectedPaymentYears", default=20, ge=1, le=50)
    actuarial_table_version: str | None = Field(alias="actuarialTableVersion", default=None)
    pension_bond_value: float | None = Field(alias="pensionBondValue", default=None, ge=0)
    spouse_or_beneficiaries_info: dict[str, Any] | None = Field(alias="spouseOrBeneficiariesInfo", default=None)
    monthly_expense_expected: float | None = Field(alias="monthlyExpenseExpected", default=None, ge=0)
    target_monthly_pension: float | None = Field(alias="targetMonthlyPension", default=None, ge=0)


class PensionAssumptionsResponse(PensionAssumptionsRequest):
    id: str
    case_id: str = Field(alias="caseId")
    created_at: datetime = Field(alias="createdAt")


class SimulationScenarioResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    scenario: str
    projected_balance: float = Field(alias="projectedBalance")
    projected_weeks: float = Field(alias="projectedWeeks")
    projected_monthly_pension_today_value: float = Field(alias="projectedMonthlyPensionTodayValue")
    projected_monthly_pension_nominal: float | None = Field(alias="projectedMonthlyPensionNominal", default=None)
    replacement_rate: float = Field(alias="replacementRate")
    smmlv_multiple: float = Field(alias="smmlvMultiple")
    capital_gap: float | None = Field(alias="capitalGap", default=None)
    additional_savings_required: float | None = Field(alias="additionalSavingsRequired", default=None)
    can_retire_by_capital: bool = Field(alias="canRetireByCapital")
    qualifies_minimum_guarantee: bool = Field(alias="qualifiesMinimumGuarantee")
    alerts: list[str]
    formula_trace: dict[str, Any] = Field(alias="formulaTrace")


class PensionSimulationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    simulation_id: str = Field(alias="simulationId")
    status: str
    visible_before_payment: bool = Field(alias="visibleBeforePayment")
    disclaimer: str
    disclaimer_version: str = Field(alias="disclaimerVersion")
    calculation_version: str = Field(alias="calculationVersion")
    overall_diagnosis: str = Field(alias="overallDiagnosis")
    confidence_score: float = Field(alias="confidenceScore")
    profile_summary: dict[str, Any] = Field(alias="profileSummary")
    scenarios: list[SimulationScenarioResponse]
    recommended_next_step: dict[str, Any] = Field(alias="recommendedNextStep")
    completed_at: datetime | None = Field(alias="completedAt", default=None)


class PensionAlertResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    severity: str
    category: str
    title: str
    message: str
    action_suggested: str | None = Field(alias="actionSuggested", default=None)
    created_at: datetime = Field(alias="createdAt")


class LegalRouteResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    simulation_id: str | None = Field(alias="simulationId", default=None)
    route_type: str = Field(alias="routeType")
    reason: str
    viability: str
    requires_payment: bool = Field(alias="requiresPayment")
    suggested_template_key: str | None = Field(alias="suggestedTemplateKey", default=None)
    required_documents: list[str] = Field(alias="requiredDocuments")
    warnings: list[str]


class CaseEntitlementResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    entitlement: str
    active: bool
    unlocked_by_payment_id: str | None = Field(alias="unlockedByPaymentId", default=None)
    created_at: datetime = Field(alias="createdAt")


class LegalDraftPaywallResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    paywall_type: str = Field(alias="paywallType")
    free_simulation_completed: bool = Field(alias="freeSimulationCompleted")
    price_cop: int = Field(alias="priceCop")
    unlocks: list[str]
    payment_required: bool = Field(alias="paymentRequired")
    entitlement_active: bool = Field(alias="entitlementActive")


class LegalDraftPaymentOrderResponse(BaseModel):
    order: dict[str, Any]

