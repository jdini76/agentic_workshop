"""Explicit dependency graph for application services."""

from dataclasses import dataclass

from agentic_workshop.ports.events import EventPublisher
from agentic_workshop.ports.memory import MemoryStore
from agentic_workshop.ports.models import LanguageModel
from agentic_workshop.ports.organizations import OrganizationRepository
from agentic_workshop.ports.resources import ResourceLoader
from agentic_workshop.ports.tasks import TaskDispatcher, TaskRepository
from agentic_workshop.ports.tools import ToolExecutor, ToolRegistry


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    """Injected ports available to use cases; contains no service-locator behavior."""

    organizations: OrganizationRepository
    tasks: TaskRepository
    dispatcher: TaskDispatcher
    memory: MemoryStore
    tools: ToolRegistry
    tool_executor: ToolExecutor
    events: EventPublisher
    model: LanguageModel
    resources: ResourceLoader

