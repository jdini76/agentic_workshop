"""Validated, source-grounded content deliverables."""

from datetime import date

from pydantic import Field, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, EmployeeId, NonBlank
from agentic_workshop.domain.marketing import BriefApprovalState


class ContentDraft(DomainModel):
    """One channel-adapted draft with its own provenance and unresolved needs."""

    assignment: NonBlank
    channel: NonBlank
    title: NonBlank
    body: NonBlank
    brand_voice_applied: tuple[NonBlank, ...]
    source_references: tuple[NonBlank, ...]
    missing_assets_or_information: tuple[NonBlank, ...]


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
