"""Per-destination publication outcomes, kept separate from content approval state.

Approval state (BriefApprovalState, reused by ContentPackage) is about content review --
whether the CEO accepts what Casey drafted. Publication is about external delivery, which can
fail for reasons entirely unrelated to content quality (network, credentials, platform policy),
and needs shape approval state does not: an external post identifier and URL, timestamps, a
retry count, and sanitized failure detail. Records are keyed by
(content_package_id, content_package_sha256, destination_platform) -- this is the idempotency
guarantee that prevents a re-approval, retry, or process restart from ever double-posting the
same approved content to the same destination.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, NonBlank


class PublicationStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"


class PublicationRecord(DomainModel):
    """Diagnostic, non-approvable record of one publish attempt to one destination."""

    schema_version: Literal[1] = 1
    destination_platform: Literal["facebook_page", "website"]
    client_id: ClientId
    campaign_week: date
    content_package_id: NonBlank
    content_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: PublicationStatus
    external_post_id: NonBlank | None = None
    external_url: str | None = None
    attempted_at: datetime
    published_at: datetime | None = None
    error_detail: NonBlank | None = None
    attempt_count: int = Field(ge=1, default=1)

    @model_validator(mode="after")
    def validate_status_fields(self) -> "PublicationRecord":
        if self.status is PublicationStatus.PUBLISHED:
            if self.external_post_id is None or self.published_at is None:
                raise ValueError("published records require an external post ID and timestamp")
            if self.error_detail is not None:
                raise ValueError("published records cannot carry error detail")
        elif self.status in (PublicationStatus.FAILED, PublicationStatus.SKIPPED):
            if self.error_detail is None:
                raise ValueError("failed or skipped records require error detail")
            if self.external_post_id is not None or self.published_at is not None:
                raise ValueError("failed or skipped records cannot carry a published result")
        else:
            if (
                self.external_post_id is not None
                or self.published_at is not None
                or self.error_detail is not None
            ):
                raise ValueError("pending records cannot carry a result or error yet")
        return self
