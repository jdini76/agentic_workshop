"""Tool declarations and invocation envelopes."""

from typing import Any

from pydantic import Field

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import NonBlank, ToolId


class ToolDefinition(DomainModel):
    id: ToolId
    name: NonBlank
    description: NonBlank
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False


class ToolCall(DomainModel):
    call_id: NonBlank
    tool_id: ToolId
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(DomainModel):
    call_id: NonBlank
    succeeded: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

