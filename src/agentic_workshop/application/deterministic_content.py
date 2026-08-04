"""Deterministic Casey generation with guarded, atomic artifact persistence."""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agentic_workshop.adapters.deterministic_content import DeterministicContentDraftGenerator
from agentic_workshop.adapters.filesystem_resources import (
    FilesystemResourceLoader,
    ResourceNotFoundError,
)
from agentic_workshop.application.assets import ClientAssetInventory
from agentic_workshop.application.brief_review import ReviewWeeklyMarketingBrief
from agentic_workshop.application.content import GenerateContentPackage
from agentic_workshop.application.content_review import ReviewContentPackage
from agentic_workshop.domain.assets import AssetRecommendation, ClientAssetManifest
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import BriefApprovalState
from agentic_workshop.presentation.content_markdown import render_content_package

CLIENT_RESOURCE_TEMPLATE = "clients/{client_id}.v1.json"
ASSET_MANIFEST_TEMPLATE = "client-assets/{client_id}.v1.json"


class DeterministicContentWorkflowError(ValueError):
    """Base error for guarded deterministic Casey generation."""


class DeterministicContentPrerequisiteError(DeterministicContentWorkflowError):
    """Raised when workflow states do not permit generation."""


class DeterministicContentConflictError(DeterministicContentWorkflowError):
    """Raised when confirmed inputs changed before generation."""


@dataclass(frozen=True)
class GeneratedContentArtifacts:
    package: ContentPackage
    json_path: Path
    markdown_path: Path


GeneratorFactory = Callable[[str | None], DeterministicContentDraftGenerator]


class GenerateDeterministicContentPackage:
    """Generate Casey's draft from fixed local inputs with optimistic checks."""

    def __init__(
        self,
        repository_root: Path,
        resource_loader: FilesystemResourceLoader,
        *,
        generator_factory: GeneratorFactory | None = None,
        output_root: Path | None = None,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._output_root = (output_root or self._root / "artifacts").resolve(strict=False)
        self._loader = resource_loader
        self._generator_factory = generator_factory or (
            lambda note: DeterministicContentDraftGenerator(revision_instructions=note)
        )
        self._brief_review = ReviewWeeklyMarketingBrief()
        self._content_review = ReviewContentPackage()

    async def execute(
        self,
        *,
        brief_path: Path,
        package_path: Path,
        expected_client_id: ClientId,
        expected_week: date,
        expected_brief_checksum: str | None = None,
        expected_package_checksum: str | None = None,
        expected_package_identity: str | None = None,
        expect_package_absent: bool = False,
    ) -> GeneratedContentArtifacts:
        brief_path = await asyncio.to_thread(brief_path.resolve, strict=False)
        package_path = await asyncio.to_thread(self._safe_output_path, package_path)
        brief = await asyncio.to_thread(self._brief_review.load, brief_path)
        if expected_brief_checksum and brief.checksum != expected_brief_checksum:
            raise DeterministicContentConflictError(
                "Sarah's brief changed after confirmation. Reload and confirm again."
            )
        if (
            brief.brief.client_id != expected_client_id
            or brief.brief.week != expected_week
        ):
            raise DeterministicContentPrerequisiteError(
                "Sarah's brief does not match the selected campaign."
            )
        if brief.brief.approval_state is not BriefApprovalState.APPROVED:
            raise DeterministicContentPrerequisiteError(
                "Sarah's brief must be approved before Casey can generate content."
            )

        existing = None
        if package_path.exists():
            existing = await asyncio.to_thread(self._content_review.load, package_path)
        if expect_package_absent and existing is not None:
            raise DeterministicContentConflictError(
                "Casey's package appeared after confirmation. Reload the campaign."
            )
        if existing is None and expected_package_checksum is not None:
            raise DeterministicContentConflictError(
                "Casey's confirmed package is now missing. Reload the campaign."
            )
        revision_note: str | None = None
        if existing is not None:
            if (
                expected_package_identity is not None
                and existing.package.package_id != expected_package_identity
            ):
                raise DeterministicContentConflictError(
                    "Casey's package identity changed after confirmation."
                )
            if (
                existing.package.client_id != expected_client_id
                or existing.package.week != expected_week
            ):
                raise DeterministicContentPrerequisiteError(
                    "Casey's package does not match the selected campaign."
                )
            if expected_package_checksum and existing.checksum != expected_package_checksum:
                raise DeterministicContentConflictError(
                    "Casey's package changed after confirmation. Reload and confirm again."
                )
            if existing.package.approval_state is not BriefApprovalState.REVISION_REQUESTED:
                raise DeterministicContentPrerequisiteError(
                    "Casey generation is allowed only when the package is missing "
                    "or revision requested."
                )
            revision_note = existing.package.revision_note

        client = ClientProfile.model_validate_json(
            await self._loader.load_text(
                CLIENT_RESOURCE_TEMPLATE.format(client_id=expected_client_id)
            )
        )
        if client.id != expected_client_id:
            raise DeterministicContentPrerequisiteError(
                "The client profile does not match the selected campaign."
            )
        assets = await self._asset_recommendations(expected_client_id)
        generator = self._generator_factory(revision_note)
        package = await GenerateContentPackage(
            generator,
            asset_recommendations=assets,
        ).execute(
            brief.brief,
            client,
            approved_brief_source=str(brief_path),
        )
        json_bytes = (package.model_dump_json(indent=2) + "\n").encode()
        markdown_bytes = render_content_package(package).encode()
        await asyncio.to_thread(
            self._atomic_write,
            package_path.with_suffix(".md"),
            markdown_bytes,
        )
        await asyncio.to_thread(self._atomic_write, package_path, json_bytes)
        return GeneratedContentArtifacts(package, package_path, package_path.with_suffix(".md"))

    def _safe_output_path(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self._output_root):
            raise DeterministicContentPrerequisiteError(
                "Content package output must remain beneath its configured artifact root."
            )
        return resolved

    async def _asset_recommendations(
        self, client_id: ClientId
    ) -> tuple[AssetRecommendation, ...]:
        reference = ASSET_MANIFEST_TEMPLATE.format(client_id=client_id)
        try:
            raw = await self._loader.load_text(reference)
        except ResourceNotFoundError:
            return ()
        manifest = ClientAssetManifest.model_validate_json(raw)
        return await ClientAssetInventory(self._root).recommendations(manifest)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def combined_generation_checksum(brief_checksum: str, package_checksum: str | None) -> str:
    """Bind a confirmation to both exact inputs, including explicit absence."""
    package_value = package_checksum or "absent"
    return hashlib.sha256(f"{brief_checksum}:{package_value}".encode()).hexdigest()
