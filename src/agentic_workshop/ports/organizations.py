"""Repositories for organizational definitions."""

from abc import ABC, abstractmethod

from agentic_workshop.domain.company import Company
from agentic_workshop.domain.department import Department
from agentic_workshop.domain.employee import Employee
from agentic_workshop.domain.identity import CompanyId, DepartmentId, EmployeeId


class OrganizationRepository(ABC):
    @abstractmethod
    async def get_company(self, company_id: CompanyId) -> Company | None: ...

    @abstractmethod
    async def get_department(self, department_id: DepartmentId) -> Department | None: ...

    @abstractmethod
    async def get_employee(self, employee_id: EmployeeId) -> Employee | None: ...

