"""Top-level organizational aggregate."""

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import CompanyId, DepartmentId, EmployeeId, NonBlank


class Company(DomainModel):
    id: CompanyId
    name: NonBlank
    mission: NonBlank
    department_ids: tuple[DepartmentId, ...]
    executive_employee_ids: tuple[EmployeeId, ...] = ()
    policy_resource: NonBlank

