"""Stable identities and human-readable metadata."""

from typing import Annotated, NewType

from pydantic import StringConstraints

from agentic_workshop.domain.base import DomainModel

CompanyId = NewType("CompanyId", str)
DepartmentId = NewType("DepartmentId", str)
EmployeeId = NewType("EmployeeId", str)
TaskId = NewType("TaskId", str)
EventId = NewType("EventId", str)
MemoryId = NewType("MemoryId", str)
ToolId = NewType("ToolId", str)
GoalId = NewType("GoalId", str)

NonBlank = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class Identity(DomainModel):
    """A display identity independent of a provider or prompt representation."""

    name: NonBlank
    role: NonBlank
    description: NonBlank
