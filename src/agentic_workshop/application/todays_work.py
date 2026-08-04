"""Read-only aggregation for the local Today's Work interface."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field

from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.application.assets import ClientAssetInventory
from agentic_workshop.domain.assets import AssetRecommendation, ClientAssetManifest
from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId, NonBlank
from agentic_workshop.domain.marketing import WeeklyMarketingBrief

CLIENT_RESOURCE_TEMPLATE = "clients/{client_id}.v1.json"
ASSET_MANIFEST_TEMPLATE = "client-assets/{client_id}.v1.json"


class TodaysWorkError(ValueError):
    """Raised when local workspace artifacts are unsafe or inconsistent."""


class WorkStatus(DomainModel):
    state: Literal["missing", "draft", "approved", "revision_requested"]
    label: NonBlank
    revision_note: str | None = None


class StrategySummary(DomainModel):
    objective: str | None = None
    audience: str | None = None
    campaign_theme: str | None = None


class DraftSummary(DomainModel):
    assignment: NonBlank
    channel: NonBlank
    title: NonBlank
    summary: NonBlank


class AssetSummary(DomainModel):
    asset_id: NonBlank
    availability: Literal["available", "unavailable"]
    diagnostic: NonBlank


class TodaysWorkSnapshot(DomainModel):
    """Presentation-neutral read model for one local campaign workspace."""

    client_id: ClientId
    client_name: NonBlank
    campaign_week: date | None
    brief: WorkStatus
    strategy: StrategySummary
    content_package: WorkStatus
    draft_summaries: tuple[DraftSummary, ...]
    asset: AssetSummary | None
    preview_exists: bool
    attention: tuple[NonBlank, ...]
    provenance: tuple[NonBlank, ...]
    generation_method: NonBlank
    asset_source_path: Path | None = Field(default=None, exclude=True)
    preview_source_path: Path | None = Field(default=None, exclude=True)


class LoadTodaysWork:
    """Validate local artifacts and assemble a safe, typed campaign snapshot."""

    def __init__(
        self,
        repository_root: Path,
        resource_loader: FilesystemResourceLoader,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._artifacts_root = (self._root / "artifacts").resolve(strict=True)
        self._loader = resource_loader
        self._inventory = ClientAssetInventory(self._root)

    async def execute(
        self,
        client_id: str,
        *,
        brief_path: Path,
        package_path: Path,
        preview_path: Path,
    ) -> TodaysWorkSnapshot:
        client = ClientProfile.model_validate_json(
            await self._loader.load_text(
                CLIENT_RESOURCE_TEMPLATE.format(client_id=client_id)
            )
        )
        manifest = ClientAssetManifest.model_validate_json(
            await self._loader.load_text(
                ASSET_MANIFEST_TEMPLATE.format(client_id=client_id)
            )
        )
        if str(client.id) != client_id or manifest.client_id != client.id:
            raise TodaysWorkError("requested client does not match client resources")

        brief = await asyncio.to_thread(self._load_optional_brief, brief_path)
        package = await asyncio.to_thread(self._load_optional_package, package_path)
        self._validate_artifact_identity(client.id, brief, package)
        preview = self._safe_artifact_path(preview_path)
        preview_exists = preview.is_file()
        asset, asset_source = await self._asset_summary(manifest, package)
        attention = self._attention(brief, package, asset, preview_exists)
        week = brief.week if brief is not None else package.week if package is not None else None
        strategy = StrategySummary(
            objective=brief.objective if brief is not None else None,
            audience=brief.audience if brief is not None else None,
            campaign_theme=brief.campaign_theme if brief is not None else None,
        )
        package_sources = (
            tuple(
                source
                for draft in package.drafts
                for source in draft.source_references
            )
            if package is not None
            else ()
        )
        provenance = tuple(
            dict.fromkeys(
                (
                    client.source_reference,
                    manifest.source_reference,
                    *(brief.source_references if brief is not None else ()),
                    *package_sources,
                )
            )
        )
        return TodaysWorkSnapshot(
            client_id=client.id,
            client_name=client.identity,
            campaign_week=week,
            brief=self._brief_status(brief),
            strategy=strategy,
            content_package=self._package_status(package),
            draft_summaries=self._draft_summaries(package),
            asset=asset,
            preview_exists=preview_exists,
            attention=attention,
            provenance=provenance,
            generation_method=(
                package.generation_metadata.generator if package is not None else "not available"
            ),
            asset_source_path=asset_source,
            preview_source_path=preview if preview_exists else None,
        )

    def _safe_artifact_path(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self._artifacts_root):
            raise TodaysWorkError("workspace artifacts must remain beneath artifacts/")
        return resolved

    def _load_optional_brief(self, path: Path) -> WeeklyMarketingBrief | None:
        safe = self._safe_artifact_path(path)
        if not safe.exists():
            return None
        if not safe.is_file():
            raise TodaysWorkError(f"brief artifact is not a file: {path}")
        return WeeklyMarketingBrief.model_validate_json(safe.read_text(encoding="utf-8"))

    def _load_optional_package(self, path: Path) -> ContentPackage | None:
        safe = self._safe_artifact_path(path)
        if not safe.exists():
            return None
        if not safe.is_file():
            raise TodaysWorkError(f"content package artifact is not a file: {path}")
        return ContentPackage.model_validate_json(safe.read_text(encoding="utf-8"))

    @staticmethod
    def _validate_artifact_identity(
        client_id: ClientId,
        brief: WeeklyMarketingBrief | None,
        package: ContentPackage | None,
    ) -> None:
        if brief is not None and brief.client_id != client_id:
            raise TodaysWorkError("brief client ID does not match the selected client")
        if package is not None and package.client_id != client_id:
            raise TodaysWorkError("content package client ID does not match the selected client")
        if brief is not None and package is not None and brief.week != package.week:
            raise TodaysWorkError("brief and content package campaign weeks do not match")

    async def _asset_summary(
        self,
        manifest: ClientAssetManifest,
        package: ContentPackage | None,
    ) -> tuple[AssetSummary | None, Path | None]:
        recommendations = await self._inventory.recommendations(manifest)
        approved = {
            recommendation.asset_id: recommendation for recommendation in recommendations
        }
        requested = self._package_asset_ids(package)
        candidates = [approved[asset_id] for asset_id in requested if asset_id in approved]
        if not candidates:
            candidates = list(approved.values())
        derivatives = [
            recommendation
            for recommendation in candidates
            if self._is_metadata_clean_derivative(manifest, recommendation)
        ]
        if not derivatives:
            return None, None
        recommendation = derivatives[0]
        source = None
        if recommendation.availability == "available":
            source = (self._root / recommendation.repository_path).resolve(strict=True)
        return (
            AssetSummary(
                asset_id=recommendation.asset_id,
                availability=recommendation.availability,
                diagnostic=recommendation.diagnostic,
            ),
            source,
        )

    @staticmethod
    def _package_asset_ids(package: ContentPackage | None) -> tuple[str, ...]:
        if package is None:
            return ()
        return tuple(
            dict.fromkeys(
                recommendation.asset_id
                for draft in package.drafts
                for recommendation in draft.asset_recommendations
            )
        )

    @staticmethod
    def _is_metadata_clean_derivative(
        manifest: ClientAssetManifest,
        recommendation: AssetRecommendation,
    ) -> bool:
        matches = [asset for asset in manifest.assets if asset.asset_id == recommendation.asset_id]
        return (
            len(matches) == 1
            and matches[0].source.source_type == "local_derivative"
            and matches[0].transformation is not None
            and matches[0].transformation.embedded_metadata_removed
        )

    @staticmethod
    def _brief_status(brief: WeeklyMarketingBrief | None) -> WorkStatus:
        if brief is None:
            return WorkStatus(state="missing", label="Sarah's brief is missing")
        return WorkStatus(
            state=brief.approval_state.value,
            label=f"Sarah's brief is {brief.approval_state.value.replace('_', ' ')}",
            revision_note=brief.revision_note,
        )

    @staticmethod
    def _package_status(package: ContentPackage | None) -> WorkStatus:
        if package is None:
            return WorkStatus(state="missing", label="Casey's package is missing")
        return WorkStatus(
            state=package.approval_state.value,
            label=f"Casey's package is {package.approval_state.value.replace('_', ' ')}",
            revision_note=package.revision_note,
        )

    @staticmethod
    def _draft_summaries(package: ContentPackage | None) -> tuple[DraftSummary, ...]:
        if package is None:
            return ()
        return tuple(
            DraftSummary(
                assignment=draft.assignment,
                channel=draft.channel,
                title=draft.title,
                summary=draft.body.split("\n\n", maxsplit=1)[0],
            )
            for draft in package.drafts
        )

    @staticmethod
    def _attention(
        brief: WeeklyMarketingBrief | None,
        package: ContentPackage | None,
        asset: AssetSummary | None,
        preview_exists: bool,
    ) -> tuple[str, ...]:
        items: list[str] = []
        if brief is None:
            items.append("Sarah's weekly brief is missing.")
        elif brief.approval_state.value != "approved":
            items.append("Review Sarah's weekly brief.")
        if package is None:
            if brief is None:
                items.append("Casey's content package is missing.")
            elif brief.approval_state.value == "approved":
                items.append("Generate Casey's content package.")
            else:
                items.append("Casey's content package is waiting for Sarah's approval.")
        elif package.approval_state.value == "revision_requested":
            items.append("Regenerate Casey's content package using the revision instructions.")
        elif package.approval_state.value != "approved":
            items.append("Review Casey's content package.")
        if asset is None or asset.availability != "available":
            items.append("The approved campaign cover is unavailable.")
        if not preview_exists:
            items.append("The local campaign preview has not been generated.")
        if not items:
            items.append("Review the local campaign preview; nothing has been published.")
        return tuple(items)
