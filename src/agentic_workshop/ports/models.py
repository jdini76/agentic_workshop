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
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    response_schema: dict[str, Any] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(DomainModel):
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class LanguageModel(ABC):
    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[str]: ...

