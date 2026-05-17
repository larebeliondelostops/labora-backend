from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


PaywallStatus = Literal[
    "not_started",
    "in_progress",
    "completed",
    "blocked",
    "requires_review",
    "error",
]
AlertLevel = Literal["low", "medium", "high", "unknown"]
UnlockType = Literal["payment", "manual_admin", "included_plan"]
ConversionEventName = Literal[
    "paywall_viewed",
    "preview_cta_clicked",
    "checkout_started",
    "checkout_returned",
    "unlock_completed",
    "unlock_abandoned",
    "comparison_viewed",
    "locked_feature_clicked",
]
ConversionSource = Literal["web", "mobile_web", "admin", "system"]


class PreviewCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    force_refresh: bool = Field(alias="forceRefresh", default=False)
    reason: str = Field(default="user_request", min_length=1, max_length=120)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        return normalized or "user_request"


class PreviewSummaryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    limited_text: str = Field(alias="limitedText")
    main_finding_teaser: str | None = Field(alias="mainFindingTeaser", default=None)
    alert_level: AlertLevel = Field(alias="alertLevel")
    completion_score: float | None = Field(alias="completionScore", default=None)
    confidence_score: float | None = Field(alias="confidenceScore", default=None)
    requires_human_review: bool = Field(alias="requiresHumanReview")
    hidden_value_hint: str | None = Field(alias="hiddenValueHint", default=None)


class LockedFeatureDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    title: str
    description: str | None = None
    is_highlighted: bool = Field(alias="isHighlighted")


class LockedContentDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    blurred_sections: list[str] = Field(alias="blurredSections")
    features: list[LockedFeatureDto]


class PreviewCtaDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str
    target: str
    checkout_url: str | None = Field(alias="checkoutUrl", default=None)
    price_label: str | None = Field(alias="priceLabel", default=None)
    disclaimer: str


class PreviewComparisonDto(BaseModel):
    free: list[str]
    paid: list[str]


class PreviewResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    preview_id: str | None = Field(alias="previewId")
    status: PaywallStatus
    is_unlocked: bool = Field(alias="isUnlocked")
    summary: PreviewSummaryDto
    locked_content: LockedContentDto = Field(alias="lockedContent")
    cta: PreviewCtaDto
    comparison: PreviewComparisonDto
    warnings: list[dict[str, str]] = Field(default_factory=list)


class PreviewStartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    preview_id: str = Field(alias="previewId")
    status: PaywallStatus
    message: str | None = None


class PaywallPriceDto(BaseModel):
    amount: float
    currency: str
    label: str


class PaywallConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    paywall_id: str | None = Field(alias="paywallId")
    unlock_required: bool = Field(alias="unlockRequired")
    unlock_type: UnlockType = Field(alias="unlockType")
    status: PaywallStatus
    payment_product_code: str | None = Field(alias="paymentProductCode", default=None)
    price: PaywallPriceDto | None = None
    unlocked_at: datetime | None = Field(alias="unlockedAt", default=None)


class ConversionEventCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str | None = Field(alias="caseId", default=None)
    event_name: ConversionEventName = Field(alias="eventName")
    source: ConversionSource = "web"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversionEventResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    stored: bool


class CheckoutSessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(default="preview_paywall", min_length=1, max_length=80)
    return_url: str | None = Field(alias="returnUrl", default=None, max_length=500)


class CheckoutSessionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    checkout_session_id: str = Field(alias="checkoutSessionId")
    checkout_url: str = Field(alias="checkoutUrl")
    expires_at: datetime = Field(alias="expiresAt")
    provider: str = "epayco"
    checkout_type: str | None = Field(alias="checkoutType", default=None)
    test_mode: bool | None = Field(alias="testMode", default=None)
    provider_session_token: str | None = Field(alias="providerSessionToken", default=None)


class AdminRejectPreviewRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class AdminPaywallPreviewItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    user_id: str = Field(alias="userId")
    status: PaywallStatus
    alert_level: str | None = Field(alias="alertLevel")
    completion_score: float | None = Field(alias="completionScore")
    confidence_score: float | None = Field(alias="confidenceScore")
    requires_human_review: bool = Field(alias="requiresHumanReview")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AdminPaywallPreviewListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[AdminPaywallPreviewItem]
    pagination: dict[str, Any]
