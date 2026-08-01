"""Provider-neutral work assignment records."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import EmployeeId, NonBlank, TaskId


class TaskStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class WorkTask(DomainModel):
    id: TaskId
    title: NonBlank
    description: NonBlank
    requester_id: EmployeeId | None
    assignee_id: EmployeeId | None = None
    status: TaskStatus = TaskStatus.DRAFT
    priority: TaskPriority = TaskPriority.NORMAL
    input: dict[str, Any] = Field(default_factory=dict)
    parent_id: TaskId | None = None
    dependency_ids: tuple[TaskId, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    due_at: datetime | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def prevent_direct_self_dependency(self) -> "WorkTask":
        if self.id in self.dependency_ids:
            raise ValueError("a task cannot depend on itself")
        return self


class TaskResult(DomainModel):
    task_id: TaskId
    summary: NonBlank
    output: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()

