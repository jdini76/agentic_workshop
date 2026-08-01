"""Task persistence contract with optimistic concurrency."""

from abc import ABC, abstractmethod

from agentic_workshop.domain.identity import EmployeeId, TaskId
from agentic_workshop.domain.tasks import TaskStatus, WorkTask


class TaskRepository(ABC):
    @abstractmethod
    async def get(self, task_id: TaskId) -> WorkTask | None: ...

    @abstractmethod
    async def save(self, task: WorkTask, *, expected_version: int | None) -> None: ...

    @abstractmethod
    async def list_for_assignee(
        self, assignee_id: EmployeeId, *, statuses: frozenset[TaskStatus] = frozenset()
    ) -> tuple[WorkTask, ...]: ...


class TaskDispatcher(ABC):
    @abstractmethod
    async def dispatch(self, task_id: TaskId) -> None:
        """Make a persisted task eligible for execution."""

