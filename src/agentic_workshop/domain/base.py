"""Common rules for domain records."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Immutable, strict and serialization-safe base for domain values."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)

