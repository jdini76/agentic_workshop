"""Guarded, idempotent orchestration between package approval and external publishing.

Approval (ReviewContentPackage.approve) stays a pure, checksum-guarded file transition with no
network dependency -- so CLI/script/test usage of `review --approve` never requires Facebook or
website credentials or risks a surprise post from a non-interactive context. This orchestrator
runs *after* approval succeeds, as a separate, possibly-failing side effect: approval always
succeeds regardless of whether any destination is reachable, matching the existing invariant that
approval state is a content-review decision, not a delivery guarantee.

Facebook and the website are two independent destinations: each gets its own PublicationRecord,
and one destination's failure or missing configuration never blocks the other's attempt.
"""

import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agentic_workshop.application.assets import ClientAssetInventory
from agentic_workshop.application.channels import channel_use
from agentic_workshop.domain.assets import ClientAssetManifest
from agentic_workshop.domain.content import ContentDraft, ContentPackage
from agentic_workshop.domain.publication import PublicationRecord, PublicationStatus
from agentic_workshop.ports.publishing import Publisher, PublisherError, PublishRequest

FACEBOOK_ASSET_USE = "facebook_page_auto_publish"
WEBSITE_ASSET_USE = "website_auto_publish"

Platform = Literal["facebook_page", "website"]


class PublishContentPackageError(ValueError):
    """Raised only for genuine precondition bugs, never for a failed publish attempt."""


class PublicationOutcome(BaseModel):
    """Independent per-destination results from one execute() call."""

    model_config = ConfigDict(frozen=True)

    facebook_page: PublicationRecord | None = None
    website: PublicationRecord | None = None


@dataclass(frozen=True)
class _DestinationConfig:
    platform: Platform
    channel_use_target: str
    asset_use: str
    publisher: Publisher | None
    include_title: bool
    unconfigured_detail: str
    no_channel_detail: str


class PublishApprovedContentPackage:
    """Publish one approved package's drafts to Facebook and the website, once, idempotently."""

    def __init__(
        self,
        repository_root: Path,
        *,
        facebook_publisher: Publisher | None,
        website_publisher: Publisher | None,
        enabled: bool,
        output_root: Path | None = None,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._facebook_publisher = facebook_publisher
        self._website_publisher = website_publisher
        self._enabled = enabled
        self._output_root = (output_root or self._root / "artifacts" / "publications").resolve(
            strict=False
        )

    async def execute(
        self,
        *,
        package: ContentPackage,
        package_checksum: str,
        manifest: ClientAssetManifest,
    ) -> PublicationOutcome:
        if not self._enabled:
            return PublicationOutcome()
        if package.approval_state.value != "approved":
            raise PublishContentPackageError(
                "PublishApprovedContentPackage requires an approved package"
            )

        facebook_record = await self._publish_to_destination(
            self._facebook_config(), package, package_checksum, manifest
        )
        website_record = await self._publish_to_destination(
            self._website_config(), package, package_checksum, manifest
        )
        return PublicationOutcome(facebook_page=facebook_record, website=website_record)

    def _facebook_config(self) -> _DestinationConfig:
        return _DestinationConfig(
            platform="facebook_page",
            channel_use_target="social_posts",
            asset_use=FACEBOOK_ASSET_USE,
            publisher=self._facebook_publisher,
            include_title=False,
            unconfigured_detail=(
                "Facebook credentials are not configured (FACEBOOK_PAGE_ID / "
                "FACEBOOK_PAGE_ACCESS_TOKEN); auto-publish is enabled but cannot run yet."
            ),
            no_channel_detail=(
                "No single social-channel assignment was found to publish to Facebook."
            ),
        )

    def _website_config(self) -> _DestinationConfig:
        return _DestinationConfig(
            platform="website",
            channel_use_target="official_website",
            asset_use=WEBSITE_ASSET_USE,
            publisher=self._website_publisher,
            include_title=True,
            unconfigured_detail=(
                "Website credentials are not configured (GITHUB_TOKEN / GITHUB_REPO); "
                "auto-publish is enabled but cannot run yet."
            ),
            no_channel_detail=(
                "No single official-website assignment was found to publish to the site."
            ),
        )

    async def _publish_to_destination(
        self,
        config: _DestinationConfig,
        package: ContentPackage,
        package_checksum: str,
        manifest: ClientAssetManifest,
    ) -> PublicationRecord:
        record_path = self._record_path(
            str(package.client_id), package.week.isoformat(), config.platform
        )
        existing = self._load_existing(record_path)
        if (
            existing is not None
            and existing.status is PublicationStatus.PUBLISHED
            and existing.content_package_sha256 == package_checksum
        ):
            return existing

        if config.publisher is None:
            return self._write_record(
                record_path,
                package,
                package_checksum,
                existing,
                platform=config.platform,
                status=PublicationStatus.SKIPPED,
                error_detail=config.unconfigured_detail,
            )

        matches = [
            draft
            for draft in package.drafts
            if channel_use(draft.channel) == config.channel_use_target
        ]
        if len(matches) != 1:
            return self._write_record(
                record_path,
                package,
                package_checksum,
                existing,
                platform=config.platform,
                status=PublicationStatus.SKIPPED,
                error_detail=config.no_channel_detail,
            )
        draft = matches[0]

        image_path = await self._eligible_asset_path(draft, manifest, config.asset_use)
        if image_path is None:
            return self._write_record(
                record_path,
                package,
                package_checksum,
                existing,
                platform=config.platform,
                status=PublicationStatus.SKIPPED,
                error_detail=(
                    f"No approved asset carries the {config.asset_use!r} use yet; add it to "
                    "the cover derivative's approved_uses when ready to publish with an image."
                ),
            )

        pending = self._write_record(
            record_path,
            package,
            package_checksum,
            existing,
            platform=config.platform,
            status=PublicationStatus.PENDING,
        )

        try:
            response = await config.publisher.publish(
                PublishRequest(
                    destination_platform=config.platform,
                    text=draft.body,
                    title=draft.title if config.include_title else None,
                    image_path=image_path,
                )
            )
        except PublisherError as error:
            return self._finalize(
                record_path,
                pending,
                status=PublicationStatus.FAILED,
                error_detail=str(error),
            )

        return self._finalize(
            record_path,
            pending,
            status=PublicationStatus.PUBLISHED,
            external_post_id=response.external_post_id,
            external_url=response.external_url,
        )

    async def _eligible_asset_path(
        self, draft: ContentDraft, manifest: ClientAssetManifest, asset_use: str
    ) -> Path | None:
        eligible = [
            recommendation
            for recommendation in draft.asset_recommendations
            if recommendation.availability == "available"
            and asset_use in recommendation.permitted_uses
        ]
        if not eligible:
            return None
        matches = [
            asset for asset in manifest.assets if asset.asset_id == eligible[0].asset_id
        ]
        if len(matches) != 1:
            return None
        result = await ClientAssetInventory(self._root).validate(matches[0])
        if not result.valid:
            return None
        return self._root / matches[0].repository_path

    def _record_path(self, client_id: str, week: str, platform: Platform) -> Path:
        return self._output_root / f"{client_id}-{week}-content-{platform}.json"

    @staticmethod
    def _load_existing(path: Path) -> PublicationRecord | None:
        if not path.is_file():
            return None
        return PublicationRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _write_record(
        self,
        record_path: Path,
        package: ContentPackage,
        package_checksum: str,
        existing: PublicationRecord | None,
        *,
        platform: Platform,
        status: PublicationStatus,
        error_detail: str | None = None,
    ) -> PublicationRecord:
        record = PublicationRecord(
            destination_platform=platform,
            client_id=package.client_id,
            campaign_week=package.week,
            content_package_id=package.package_id,
            content_package_sha256=package_checksum,
            status=status,
            attempted_at=datetime.now(UTC),
            attempt_count=(existing.attempt_count + 1) if existing is not None else 1,
            error_detail=error_detail,
        )
        self._atomic_write(record_path, record)
        return record

    def _finalize(
        self,
        record_path: Path,
        pending: PublicationRecord,
        *,
        status: PublicationStatus,
        external_post_id: str | None = None,
        external_url: str | None = None,
        error_detail: str | None = None,
    ) -> PublicationRecord:
        published_at = (
            datetime.now(UTC).isoformat() if status is PublicationStatus.PUBLISHED else None
        )
        payload = {
            **pending.model_dump(mode="json"),
            "status": status,
            "external_post_id": external_post_id,
            "external_url": external_url,
            "error_detail": error_detail,
            "published_at": published_at,
        }
        record = PublicationRecord.model_validate(payload)
        self._atomic_write(record_path, record)
        return record

    @staticmethod
    def _atomic_write(path: Path, record: PublicationRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (record.model_dump_json(indent=2) + "\n").encode()
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
