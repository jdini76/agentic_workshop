"""Local-only records of untrusted completed model attempts."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import NonBlank


class AttemptValidationStatus(StrEnum):
    RECEIVED = "received"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class UntrustedModelAttempt(DomainModel):
    """Diagnostic evidence only; never an approvable or publishable deliverable."""

    record_type: Literal["untrusted_model_attempt"] = "untrusted_model_attempt"
    attempt_id: NonBlank
    timestamp: datetime
    provider: NonBlank
    model: NonBlank
    response_id: NonBlank
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = Field(ge=0)
    raw_structured_output: dict[str, Any]
    validation_status: AttemptValidationStatus
    validation_errors: tuple[str, ...] = ()
    final_package_artifact_path: str | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> "UntrustedModelAttempt":
        if self.validation_status is AttemptValidationStatus.RECEIVED:
            if self.validation_errors or self.final_package_artifact_path is not None:
                raise ValueError("received attempts cannot have a result")
        elif self.validation_status is AttemptValidationStatus.REJECTED:
            if not self.validation_errors or self.final_package_artifact_path is not None:
                raise ValueError("rejected attempts require errors and cannot reference a package")
        elif self.final_package_artifact_path is None or self.validation_errors:
            raise ValueError("accepted attempts require a package path and no errors")
        return self
