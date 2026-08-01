"""Declarative employee intent and operating constraints."""

from enum import StrEnum

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import GoalId, NonBlank


class GoalStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


class Goal(DomainModel):
    id: GoalId
    objective: NonBlank
    success_criteria: tuple[NonBlank, ...]
    status: GoalStatus = GoalStatus.PROPOSED


class Routine(DomainModel):
    name: NonBlank
    schedule: NonBlank
    procedure_ref: NonBlank


class StandardOperatingProcedure(DomainModel):
    name: NonBlank
    resource_ref: NonBlank
    version: NonBlank


class DeliverableDefinition(DomainModel):
    name: NonBlank
    description: NonBlank
    media_type: NonBlank
    schema_ref: str | None = None

