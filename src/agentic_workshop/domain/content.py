"""Validated, source-grounded content deliverables."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from agentic_workshop.domain.assets import AssetRecommendation
from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, EmployeeId, NonBlank
from agentic_workshop.domain.marketing import BriefApprovalState


class ContentDraft(DomainModel):
    """One channel-adapted draft with its own provenance and unresolved needs."""

    assignment: NonBlank
    channel: NonBlank
    title: NonBlank
    body: NonBlank
    state: Literal["draft"] = "draft"
    brand_voice_applied: tuple[NonBlank, ...]
    approved_facts_used: tuple[NonBlank, ...]
    source_references: tuple[NonBlank, ...]
    missing_assets_or_information: tuple[NonBlank, ...]
    required_assets: tuple[NonBlank, ...]
    asset_recommendations: tuple[AssetRecommendation, ...] = ()

    @model_validator(mode="after")
    def require_provenance(self) -> "ContentDraft":
        if not self.source_references:
            raise ValueError("every content draft requires at least one source reference")
        return self


class ContentGenerationMetadata(DomainModel):
    """Safe operational metadata; never contains prompts, content, or credentials."""

    generator: NonBlank
    model: str | None = None
    response_id: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = Field(default=0, ge=0)


class DraftGenerationResult(DomainModel):
    """Drafts plus sanitized metadata returned by a drafting strategy."""

    drafts: tuple[ContentDraft, ...]
    metadata: ContentGenerationMetadata


class ContentPackage(DomainModel):
    """Casey's reviewable output; package approval never authorizes publication."""

    package_id: NonBlank
    client_id: ClientId
    employee_id: EmployeeId
    week: date
    approved_brief_source: NonBlank
    client_profile_source: NonBlank
    brand_voice: tuple[NonBlank, ...]
    drafts: tuple[ContentDraft, ...]
    assumptions: tuple[NonBlank, ...]
    missing_assets_or_information: tuple[NonBlank, ...]
    required_assets: tuple[NonBlank, ...]
    asset_recommendations: tuple[AssetRecommendation, ...] = ()
    generation_metadata: ContentGenerationMetadata = ContentGenerationMetadata(
        generator="deterministic"
    )
    approval_state: BriefApprovalState = BriefApprovalState.DRAFT
    revision_note: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_review_state(self) -> "ContentPackage":
        if self.approval_state is BriefApprovalState.REVISION_REQUESTED:
            if self.revision_note is None or not self.revision_note.strip():
                raise ValueError("revision_note is required when revision is requested")
        elif self.revision_note is not None:
            raise ValueError("revision_note is only valid when revision is requested")
        return self
