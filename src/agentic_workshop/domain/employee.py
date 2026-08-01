"""Configuration of an AI employee; execution belongs to application services."""

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.governance import (
    DeliverableDefinition,
    Goal,
    Routine,
    StandardOperatingProcedure,
)
from agentic_workshop.domain.identity import EmployeeId, Identity, NonBlank, ToolId


class Personality(DomainModel):
    traits: tuple[NonBlank, ...]
    communication_style: NonBlank
    decision_principles: tuple[NonBlank, ...] = ()


class Employee(DomainModel):
    id: EmployeeId
    identity: Identity
    personality: Personality
    responsibilities: tuple[NonBlank, ...]
    routines: tuple[Routine, ...] = ()
    tool_ids: tuple[ToolId, ...] = ()
    goals: tuple[Goal, ...] = ()
    procedures: tuple[StandardOperatingProcedure, ...] = ()
    deliverables: tuple[DeliverableDefinition, ...] = ()
    prompt_resource: NonBlank
    memory_namespace: NonBlank
    enabled: bool = True

