"""Destination-agnostic external publishing contract."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import Field

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import NonBlank


class PublishRequest(DomainModel):
    """Provider-neutral publish input interpreted by one configured adapter."""

    destination_platform: NonBlank
    text: NonBlank
    title: NonBlank | None = None
    image_path: Path | None = None


class PublishResponse(DomainModel):
    """Normalized publish result with provider details quarantined as metadata."""

    external_post_id: NonBlank
    external_url: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class PublisherError(Exception):
    """Base for sanitized, retry-aware failures raised by publisher adapters."""

    def __init__(self, message: str, *, provider: str) -> None:
        self.provider = provider
        super().__init__(message)


class PublisherAuthenticationError(PublisherError):
    """Provider credentials were absent, invalid, or unauthorized."""


class PublisherRateLimitError(PublisherError):
    """The provider refused work because a request or quota limit was reached."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message, provider=provider)


class PublisherTimeoutError(PublisherError):
    """The provider did not finish within the configured deadline."""


class PublisherContentRejectedError(PublisherError):
    """The provider rejected the content itself (policy, spam, or abuse detection)."""


class PublisherMalformedResponseError(PublisherError):
    """The provider returned a response that could not be interpreted safely."""


class PublisherUnavailableError(PublisherError):
    """The destination (page, account, etc.) was not found or not authorized."""


class Publisher(ABC):
    """Async publish port implemented independently by each destination adapter."""

    @abstractmethod
    async def publish(self, request: PublishRequest) -> PublishResponse: ...
