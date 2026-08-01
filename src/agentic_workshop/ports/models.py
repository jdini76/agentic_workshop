"""LLM-agnostic inference contract."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import Field

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import NonBlank
from agentic_workshop.domain.tools import ToolCall, ToolDefinition


class ModelMessage(DomainModel):
    role: NonBlank
    content: NonBlank


class ModelRequest(DomainModel):
    """Provider-neutral inference input interpreted by one configured adapter."""

    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    response_schema: dict[str, Any] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(DomainModel):
    """Normalized inference result with provider details quarantined as metadata."""

    content: str
    structured_output: dict[str, Any] | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class LanguageModelError(Exception):
    """Base for sanitized, retry-aware failures raised by model adapters."""

    def __init__(self, message: str, *, provider: str) -> None:
        self.provider = provider
        super().__init__(message)


class ModelTimeoutError(LanguageModelError):
    """The provider did not finish within the configured deadline."""


class ModelAuthenticationError(LanguageModelError):
    """Provider credentials were absent, invalid, or unauthorized."""


class ModelRateLimitError(LanguageModelError):
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


class ModelMalformedOutputError(LanguageModelError):
    """The provider returned output that could not satisfy the requested contract."""


class LanguageModel(ABC):
    """Async inference port implemented independently by each provider adapter."""

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[str]: ...
