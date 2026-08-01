"""Departmental structure and routing policy references."""

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import DepartmentId, EmployeeId, NonBlank


class Department(DomainModel):
    id: DepartmentId
    name: NonBlank
    mission: NonBlank
    employee_ids: tuple[EmployeeId, ...]
    lead_id: EmployeeId | None = None
    routing_policy: NonBlank

