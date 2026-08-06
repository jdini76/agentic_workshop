"""Start a new campaign week with guarded, atomic creation and duplicate detection."""

import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from agentic_workshop.adapters.filesystem_resources import (
    FilesystemResourceLoader,
    ResourceNotFoundError,
)
from agentic_workshop.application.assets import ClientAssetInventory
from agentic_workshop.application.marketing import GenerateWeeklyMarketingBrief
from agentic_workshop.domain.assets import AssetRecommendation, ClientAssetManifest
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import WeeklyMarketingBrief
from agentic_workshop.presentation.markdown import render_weekly_marketing_brief

ASSET_MANIFEST_TEMPLATE = "client-assets/{client_id}.v1.json"


class NextCampaignWorkflowError(ValueError):
    """Base error for guarded next-campaign creation."""


class DuplicateCampaignWeekError(NextCampaignWorkflowError):
    """Raised when a brief already exists for the normalized campaign week."""

    def __init__(self, week: date) -> None:
        self.week = week
        super().__init__(
            f"A campaign already exists for {week.isoformat()}. "
            "Open that campaign instead of starting a duplicate."
        )


@dataclass(frozen=True)
class StartedCampaignArtifacts:
    brief: WeeklyMarketingBrief
    json_path: Path
    markdown_path: Path


class StartNextCampaign:
    """Create Sarah's first draft for a new campaign week, guarding against duplicates."""

    def __init__(
        self,
        repository_root: Path,
        resource_loader: FilesystemResourceLoader,
        *,
        output_root: Path | None = None,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._output_root = (
            output_root or self._root / "artifacts" / "weekly-briefs"
        ).resolve(strict=False)
        self._loader = resource_loader

    async def execute(
        self,
        *,
        client_id: ClientId,
        requested_week: date,
        strict: bool = False,
    ) -> StartedCampaignArtifacts:
        week = requested_week - timedelta(days=requested_week.weekday())
        stem = f"{client_id}-{week.isoformat()}"
        json_path = self._safe_output_path(self._output_root / f"{stem}.json")
        markdown_path = json_path.with_suffix(".md")
        if json_path.exists():
            raise DuplicateCampaignWeekError(week)

        assets = await self._asset_recommendations(client_id)
        service = GenerateWeeklyMarketingBrief(self._loader, asset_recommendations=assets)
        brief = await service.execute(str(client_id), requested_week, strict=strict)

        json_bytes = (brief.model_dump_json(indent=2) + "\n").encode()
        markdown_bytes = render_weekly_marketing_brief(brief).encode()
        # Re-check immediately before the atomic create to narrow -- not eliminate -- the race
        # window. The workspace server is documented as the sole workflow writer while running;
        # a concurrent CLI write is a known, accepted gap, not one this class can close alone.
        if json_path.exists():
            raise DuplicateCampaignWeekError(week)
        self._atomic_create(markdown_path, markdown_bytes)
        self._atomic_create(json_path, json_bytes)
        return StartedCampaignArtifacts(brief, json_path, markdown_path)

    def _safe_output_path(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self._output_root):
            raise NextCampaignWorkflowError(
                "Weekly brief output must remain beneath its configured artifact root."
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
    def _atomic_create(path: Path, content: bytes) -> None:
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
