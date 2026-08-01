"""Durable facts emitted by the system."""

from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import EventId, NonBlank


class DomainEvent(DomainModel):
    id: EventId
    type: NonBlank
    aggregate_type: NonBlank
    aggregate_id: NonBlank
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: EventId | None = None
    schema_version: int = Field(default=1, ge=1)

