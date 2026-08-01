"""Tool discovery and execution contracts."""

from abc import ABC, abstractmethod

from agentic_workshop.domain.identity import ToolId
from agentic_workshop.domain.tools import ToolCall, ToolDefinition, ToolResult


class ToolRegistry(ABC):
    @abstractmethod
    async def get(self, tool_id: ToolId) -> ToolDefinition | None: ...

    @abstractmethod
    async def list(self) -> tuple[ToolDefinition, ...]: ...


class ToolExecutor(ABC):
    @abstractmethod
    async def execute(self, call: ToolCall) -> ToolResult: ...

