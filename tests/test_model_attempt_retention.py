import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from agentic_workshop.adapters import model_attempts
from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.adapters.model_attempts import ModelAttemptRecorder
from agentic_workshop.adapters.model_content import ModelContentDraftGenerator
from agentic_workshop.application.content import GenerateContentPackage
from agentic_workshop.cli import PACKAGE_RESOURCE_ROOT, run
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief
from agentic_workshop.domain.model_attempts import (
    AttemptValidationStatus,
    UntrustedModelAttempt,
)
from agentic_workshop.ports.models import (
    LanguageModel,
    ModelMalformedOutputError,
    ModelRequest,
    ModelResponse,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
BRIEF_PATH = Path("artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json")


class RetentionFakeModel(LanguageModel):
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="structured response",
            structured_output=self._payload,
            usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            provider_metadata={
                "provider": "openai",
                "model": "gpt-5.6-terra",
                "response_id": "resp_retention_test",
                "latency_ms": 123,
            },
        )

    def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        async def no_stream() -> AsyncIterator[str]:
            if False:
                yield ""

        return no_stream()


def inputs() -> tuple[WeeklyMarketingBrief, ClientProfile, str]:
    brief = WeeklyMarketingBrief.model_validate_json(BRIEF_PATH.read_text(encoding="utf-8"))
    loader = FilesystemResourceLoader(PACKAGE_RESOURCE_ROOT)
    client = ClientProfile.model_validate_json(
        asyncio.run(loader.load_text("clients/jordan-and-the-fosters.v1.json"))
    )
    prompt = asyncio.run(loader.load_text("prompts/casey-content-creator.v1.md"))
    return brief, client, prompt


def payload(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def retained(path: Path) -> UntrustedModelAttempt:
    return UntrustedModelAttempt.model_validate_json(path.read_text(encoding="utf-8"))


def test_successful_response_is_received_before_acceptance_and_contains_no_secrets(
    tmp_path: Path,
) -> None:
    brief, client, prompt = inputs()
    recorder = ModelAttemptRecorder(tmp_path / "attempts")
    raw = payload("openai_corrected_drafts.json")
    generator = ModelContentDraftGenerator(
        RetentionFakeModel(raw), instructions=prompt, attempt_recorder=recorder
    )

    package = asyncio.run(
        GenerateContentPackage(generator).execute(
            brief, client, approved_brief_source=str(BRIEF_PATH)
        )
    )

    assert recorder.path is not None
    attempt = retained(recorder.path)
    assert attempt.validation_status is AttemptValidationStatus.RECEIVED
    assert attempt.raw_structured_output == raw
    assert attempt.response_id == "resp_retention_test"
    serialized = recorder.path.read_text(encoding="utf-8")
    assert prompt not in serialized
    assert "OPENAI_API_KEY" not in serialized
    assert "Authorization" not in serialized

    package_path = tmp_path / "packages" / "package.json"
    package_path.parent.mkdir()
    package_path.write_text(package.model_dump_json(), encoding="utf-8")
    accepted = recorder.accepted(package_path)
    assert accepted.validation_status is AttemptValidationStatus.ACCEPTED
    assert accepted.final_package_artifact_path == str(package_path)
    assert recorder.path.parent.name == "attempts"
    assert package_path.parent != recorder.path.parent


def test_rejected_response_retains_raw_output_and_exact_validation_errors(
    tmp_path: Path,
) -> None:
    brief, client, prompt = inputs()
    recorder = ModelAttemptRecorder(tmp_path / "attempts")
    raw = payload("openai_first_smoke_sanitized.json")
    generator = ModelContentDraftGenerator(
        RetentionFakeModel(raw), instructions=prompt, attempt_recorder=recorder
    )

    with pytest.raises(ModelMalformedOutputError):
        asyncio.run(
            GenerateContentPackage(generator).execute(
                brief, client, approved_brief_source=str(BRIEF_PATH)
            )
        )

    assert recorder.path is not None
    attempt = retained(recorder.path)
    assert attempt.validation_status is AttemptValidationStatus.REJECTED
    assert attempt.raw_structured_output == raw
    assert attempt.final_package_artifact_path is None
    assert "Official website campaign feature: required exact heading is missing" in (
        attempt.validation_errors
    )
    assert "Social awareness post: public copy must contain 100-140 words" in (
        attempt.validation_errors
    )
    assert not recorder.path.with_suffix(".md").exists()
    with pytest.raises(SystemExit):
        run(["review", str(recorder.path), "--approve"])
    assert retained(recorder.path).validation_status is AttemptValidationStatus.REJECTED


def test_attempt_writes_use_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def observe_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.exists()
        assert source_path.parent == destination_path.parent
        assert source_path.suffix == ".tmp"
        observed.append((source_path, destination_path))
        real_replace(source_path, destination_path)

    monkeypatch.setattr(model_attempts.os, "replace", observe_replace)
    recorder = ModelAttemptRecorder(tmp_path / "attempts")
    recorder.received(
        provider="openai",
        model="gpt-5.6-terra",
        response_id="resp_atomic",
        usage={},
        latency_ms=1,
        raw_structured_output={"drafts": []},
    )

    assert len(observed) == 1
    assert recorder.path is not None and recorder.path.exists()
    assert not list(recorder.path.parent.glob("*.tmp"))


def test_local_revalidation_creates_separate_draft_and_attempt_cannot_be_reviewed(
    tmp_path: Path,
) -> None:
    recorder = ModelAttemptRecorder(tmp_path / "attempts")
    recorder.received(
        provider="openai",
        model="gpt-5.6-terra",
        response_id="resp_revalidate",
        usage={"total_tokens": 30},
        latency_ms=123,
        raw_structured_output=payload("openai_corrected_drafts.json"),
    )
    recorder.rejected(("legacy validator rejection",))
    assert recorder.path is not None
    output_root = tmp_path / "revalidated"

    assert run(
        [
            "revalidate-attempt",
            str(recorder.path),
            str(BRIEF_PATH),
            "--artifact-root",
            str(output_root),
        ]
    ) == 0

    updated = retained(recorder.path)
    assert updated.validation_status is AttemptValidationStatus.ACCEPTED
    assert updated.final_package_artifact_path is not None
    package_path = Path(updated.final_package_artifact_path)
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    assert package.approval_state is BriefApprovalState.DRAFT
    assert package_path.parent == output_root
    assert recorder.path.parent != package_path.parent

    with pytest.raises(SystemExit) as rejected:
        run(["review", str(recorder.path), "--approve"])
    assert rejected.value.code == 2
    assert retained(recorder.path).validation_status is AttemptValidationStatus.ACCEPTED
