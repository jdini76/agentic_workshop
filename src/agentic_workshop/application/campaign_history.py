"""Typed, deterministic discovery of local campaign artifacts."""

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.application.preview_status import PREVIEW_ATTENTION, PreviewStatusService
from agentic_workshop.application.todays_work import LoadTodaysWork, TodaysWorkSnapshot
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import WeeklyMarketingBrief


class CampaignHistoryError(ValueError):
    """Base error for unsafe or inconsistent campaign discovery."""


class CampaignNotFoundError(CampaignHistoryError):
    """Raised when a requested campaign week is not known."""


class CampaignAmbiguityError(CampaignHistoryError):
    """Raised when more than one artifact claims the same workflow role."""


class CampaignArtifactError(CampaignHistoryError):
    """Raised when a candidate artifact is invalid or has the wrong identity."""


@dataclass(frozen=True)
class CampaignRecord:
    week: date
    brief_path: Path
    package_path: Path
    preview_path: Path
    facebook_publication_path: Path
    website_publication_path: Path
    brief: WeeklyMarketingBrief | None
    package: ContentPackage | None


@dataclass(frozen=True)
class CampaignView:
    record: CampaignRecord
    snapshot: TodaysWorkSnapshot


def parse_campaign_week(value: str) -> date:
    """Parse only the canonical YYYY-MM-DD ISO representation."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise CampaignNotFoundError("Campaign week must use YYYY-MM-DD format.") from error
    if parsed.isoformat() != value:
        raise CampaignNotFoundError("Campaign week must use YYYY-MM-DD format.")
    return parsed


class LoadCampaignHistory:
    """Discover campaign roles from fixed roots and build typed workspace views."""

    def __init__(
        self,
        repository_root: Path,
        resource_loader: FilesystemResourceLoader,
        client_id: ClientId,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._loader = resource_loader
        self._client_id = client_id
        self._brief_root = self._root / "artifacts" / "weekly-briefs"
        self._package_root = self._root / "artifacts" / "content-packages"
        self._preview_root = self._root / "artifacts" / "campaign-previews"
        self._publication_root = self._root / "artifacts" / "publications"

    async def execute(self) -> tuple[CampaignView, ...]:
        records = await asyncio.to_thread(self._discover)
        service = LoadTodaysWork(self._root, self._loader)
        previews = PreviewStatusService(self._root, self._loader)
        views: list[CampaignView] = []
        for record in records:
            snapshot = await service.execute(
                str(self._client_id),
                brief_path=record.brief_path,
                package_path=record.package_path,
                preview_path=record.preview_path,
                facebook_publication_path=record.facebook_publication_path,
                website_publication_path=record.website_publication_path,
            )
            preview = await previews.inspect(
                client_id=self._client_id,
                week=record.week,
                package_path=record.package_path,
                preview_directory=record.preview_path.parent,
            )
            refreshed_preview_attention = PREVIEW_ATTENTION[preview.state]
            attention_without_stale_preview = tuple(
                item for item in snapshot.attention if item != snapshot.preview_attention
            )
            views.append(CampaignView(
                record=record,
                snapshot=snapshot.model_copy(
                    update={
                        "preview_state": preview.state,
                        "preview_diagnostic": preview.diagnostic,
                        "preview_attention": refreshed_preview_attention,
                        "attention": (
                            *attention_without_stale_preview,
                            refreshed_preview_attention,
                        ),
                    }
                ),
            ))
        return tuple(views)

    def _discover(self) -> tuple[CampaignRecord, ...]:
        briefs = self._read_role(self._brief_root, WeeklyMarketingBrief)
        packages = self._read_role(self._package_root, ContentPackage)
        weeks = sorted(set(briefs) | set(packages))
        if not weeks:
            raise CampaignNotFoundError("No campaigns are available for this client.")
        return tuple(
            CampaignRecord(
                week=week,
                brief_path=(
                    briefs[week][0]
                    if week in briefs
                    else self._brief_root / f"{self._client_id}-{week.isoformat()}.json"
                ),
                package_path=(
                    packages[week][0]
                    if week in packages
                    else self._package_root
                    / f"{self._client_id}-{week.isoformat()}-content.json"
                ),
                preview_path=(
                    self._preview_root
                    / f"{self._client_id}-{week.isoformat()}-content"
                    / "index.html"
                ),
                facebook_publication_path=(
                    self._publication_root
                    / f"{self._client_id}-{week.isoformat()}-content-facebook_page.json"
                ),
                website_publication_path=(
                    self._publication_root
                    / f"{self._client_id}-{week.isoformat()}-content-website.json"
                ),
                brief=cast(
                    WeeklyMarketingBrief | None,
                    briefs.get(week, (None, None))[1],
                ),
                package=cast(
                    ContentPackage | None,
                    packages.get(week, (None, None))[1],
                ),
            )
            for week in weeks
        )

    def _read_role(
        self,
        root: Path,
        model: type[WeeklyMarketingBrief] | type[ContentPackage],
    ) -> dict[date, tuple[Path, WeeklyMarketingBrief | ContentPackage]]:
        found: dict[date, tuple[Path, WeeklyMarketingBrief | ContentPackage]] = {}
        if not root.exists():
            return found
        resolved_root = root.resolve(strict=True)
        for path in sorted(root.glob(f"{self._client_id}-*.json")):
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
                raise CampaignArtifactError(
                    "Campaign artifacts must be regular files in their fixed root."
                )
            try:
                artifact = model.model_validate_json(resolved.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValidationError) as error:
                raise CampaignArtifactError(f"Invalid campaign artifact: {path.name}") from error
            if artifact.client_id != self._client_id:
                raise CampaignArtifactError(
                    f"Campaign artifact client does not match {self._client_id}: {path.name}"
                )
            week_match = re.search(r"\d{4}-\d{2}-\d{2}", path.name)
            if (
                week_match is not None
                and parse_campaign_week(week_match.group()) != artifact.week
            ):
                raise CampaignArtifactError(
                    f"Campaign artifact week does not match its filename: {path.name}"
                )
            if artifact.week in found:
                raise CampaignAmbiguityError(
                    f"More than one {model.__name__} exists for {artifact.week.isoformat()}."
                )
            found[artifact.week] = (resolved, artifact)
        return found
