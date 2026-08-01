"""Memory values without storage or retrieval policy."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import EmployeeId, MemoryId, NonBlank


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"


class MemoryRecord(DomainModel):
    id: MemoryId
    owner_id: EmployeeId
    kind: MemoryKind
    content: NonBlank
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None


class MemoryQuery(DomainModel):
    text: NonBlank
    kinds: frozenset[MemoryKind] = frozenset()
    limit: int = Field(default=10, ge=1, le=100)
    metadata_filter: dict[str, Any] = Field(default_factory=dict)


class MemoryMatch(DomainModel):
    record: MemoryRecord
    score: float = Field(ge=0.0, le=1.0)

