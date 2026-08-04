"""Validated weekly marketing planning records."""

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from agentic_workshop.domain.assets import AssetRecommendation
from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, EmployeeId, NonBlank


class BriefApprovalState(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"


class ContentAssignment(DomainModel):
    """A bounded content-planning deliverable; it does not authorize publication."""

    owner_id: EmployeeId
    deliverable: NonBlank
    channel: NonBlank
    instructions: NonBlank
    asset_recommendations: tuple[AssetRecommendation, ...] = ()
    asset_required_use: NonBlank | None = None

    @model_validator(mode="after")
    def validate_asset_permission(self) -> "ContentAssignment":
        if self.asset_recommendations and self.asset_required_use is None:
            raise ValueError("asset_required_use is required for asset recommendations")
        if not self.asset_recommendations and self.asset_required_use is not None:
            raise ValueError("asset_required_use requires an asset recommendation")
        if self.asset_required_use is not None and any(
            self.asset_required_use not in recommendation.permitted_uses
            for recommendation in self.asset_recommendations
        ):
            raise ValueError("asset recommendation does not permit the assignment use")
        return self


class SuccessMetric(DomainModel):
    name: NonBlank
    target: NonBlank


class WeeklyMarketingBrief(DomainModel):
    client_id: ClientId
    employee_id: EmployeeId
    week: date
    objective: NonBlank
    audience: NonBlank
    campaign_theme: NonBlank
    rationale: NonBlank
    source_references: tuple[NonBlank, ...]
    recommended_channels: tuple[NonBlank, ...]
    content_assignments: tuple[ContentAssignment, ...]
    call_to_action: NonBlank
    success_metrics: tuple[SuccessMetric, ...]
    assumptions: tuple[NonBlank, ...]
    missing_inputs: tuple[NonBlank, ...]
    approval_state: BriefApprovalState = BriefApprovalState.DRAFT
    revision_note: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_review_state(self) -> "WeeklyMarketingBrief":
        if self.week.weekday() != 0:
            raise ValueError("week must be a Monday")
        if self.approval_state is BriefApprovalState.REVISION_REQUESTED:
            if self.revision_note is None or not self.revision_note.strip():
                raise ValueError("revision_note is required when revision is requested")
        elif self.revision_note is not None:
            raise ValueError("revision_note is only valid when revision is requested")
        return self
