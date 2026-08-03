"""Structured, source-grounded client knowledge."""

from pydantic import Field

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, NonBlank


class AudienceProfile(DomainModel):
    """An approved audience and the needs marketing may address."""

    name: NonBlank
    needs: tuple[NonBlank, ...]


class PurchaseLink(DomainModel):
    """An approved destination at which a customer can take action."""

    label: NonBlank
    url: NonBlank


class PublicChannel(DomainModel):
    """An approved public presence and the uses authorized for it."""

    kind: NonBlank
    label: NonBlank
    url: NonBlank
    approved_uses: tuple[NonBlank, ...]
    is_central_hub: bool = False


class ApprovedReview(DomainModel):
    """A verbatim third-party quotation with inseparable provenance and permissions."""

    quote: NonBlank
    attribution: NonBlank
    source_url: NonBlank
    approved_uses: tuple[NonBlank, ...]


class ClientProfile(DomainModel):
    """The sole authoritative marketing input for a client."""

    id: ClientId
    identity: NonBlank
    summary: NonBlank
    author_story: NonBlank | None = None
    mission: NonBlank
    brand_voice: tuple[NonBlank, ...]
    audiences: tuple[AudienceProfile, ...]
    themes: tuple[NonBlank, ...]
    marketing_goals: tuple[NonBlank, ...]
    calls_to_action: tuple[NonBlank, ...]
    purchase_links: tuple[PurchaseLink, ...]
    public_channels: tuple[PublicChannel, ...] = ()
    approved_reviews: tuple[ApprovedReview, ...] = ()
    approved_facts: tuple[NonBlank, ...]
    prohibited_claims: tuple[NonBlank, ...]
    marketing_permissions: tuple[NonBlank, ...] = ()
    missing_information: tuple[NonBlank, ...]
    deferred_information: tuple[NonBlank, ...] = ()
    source_reference: NonBlank
    version: int = Field(ge=1)

    @property
    def is_complete(self) -> bool:
        """Return whether the profile has no explicitly tracked knowledge gaps."""
        return not self.missing_information
