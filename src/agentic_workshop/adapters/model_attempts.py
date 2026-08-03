"""Atomic local persistence for untrusted model-attempt diagnostics."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentic_workshop.domain.model_attempts import (
    AttemptValidationStatus,
    UntrustedModelAttempt,
)
from agentic_workshop.ports.models import LanguageModel, ModelRequest, ModelResponse


class ModelAttemptRecorder:
    """Persist response evidence without prompts, credentials, or SDK objects."""

    def __init__(self, attempts_root: Path) -> None:
        self._attempts_root = attempts_root
        self._path: Path | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    def received(
        self,
        *,
        provider: str,
        model: str,
        response_id: str,
        usage: dict[str, int],
        latency_ms: int,
        raw_structured_output: dict[str, Any],
    ) -> UntrustedModelAttempt:
        attempt = UntrustedModelAttempt(
            attempt_id=str(uuid4()),
            timestamp=datetime.now(UTC),
            provider=provider,
            model=model,
            response_id=response_id,
            usage=usage,
            latency_ms=latency_ms,
            raw_structured_output=raw_structured_output,
            validation_status=AttemptValidationStatus.RECEIVED,
        )
        self._path = self._attempts_root / f"{attempt.attempt_id}.json"
        self._atomic_write(self._path, attempt)
        return attempt

    def rejected(self, errors: tuple[str, ...]) -> UntrustedModelAttempt:
        attempt = self._require_attempt()
        combined = tuple(dict.fromkeys((*attempt.validation_errors, *errors)))
        updated = attempt.model_copy(
            update={
                "validation_status": AttemptValidationStatus.REJECTED,
                "validation_errors": combined,
                "final_package_artifact_path": None,
            }
        )
        validated = UntrustedModelAttempt.model_validate(updated.model_dump(mode="json"))
        self._atomic_write(self._require_path(), validated)
        return validated

    def accepted(self, final_package_path: Path) -> UntrustedModelAttempt:
        attempt = self._require_attempt()
        updated = attempt.model_copy(
            update={
                "validation_status": AttemptValidationStatus.ACCEPTED,
                "validation_errors": (),
                "final_package_artifact_path": str(final_package_path),
            }
        )
        validated = UntrustedModelAttempt.model_validate(updated.model_dump(mode="json"))
        self._atomic_write(self._require_path(), validated)
        return validated

    def attach(self, path: Path) -> UntrustedModelAttempt:
        self._path = path
        return self._require_attempt()

    def _require_attempt(self) -> UntrustedModelAttempt:
        return UntrustedModelAttempt.model_validate_json(
            self._require_path().read_text(encoding="utf-8")
        )

    def _require_path(self) -> Path:
        if self._path is None:
            raise RuntimeError("no model attempt has been received")
        return self._path

    @staticmethod
    def _atomic_write(path: Path, attempt: UntrustedModelAttempt) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(attempt.model_dump_json(indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


class RetainedAttemptLanguageModel(LanguageModel):
    """Replay one retained structured response without any provider access."""

    def __init__(self, attempt: UntrustedModelAttempt) -> None:
        self._attempt = attempt

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="retained structured response",
            structured_output=self._attempt.raw_structured_output,
            usage=self._attempt.usage,
            provider_metadata={
                "provider": self._attempt.provider,
                "model": self._attempt.model,
                "response_id": self._attempt.response_id,
                "latency_ms": self._attempt.latency_ms,
            },
        )

    def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        async def no_stream() -> AsyncIterator[str]:
            if False:
                yield ""

        return no_stream()
