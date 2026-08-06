"""Preview provenance, freshness classification, and guarded local generation."""

import asyncio
import hashlib
import os
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.application.preview import GenerateCampaignPreview
from agentic_workshop.domain.assets import AssetApprovalState, ClientAssetManifest
from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId, NonBlank
from agentic_workshop.domain.marketing import BriefApprovalState

CLIENT_RESOURCE_TEMPLATE = "clients/{client_id}.v1.json"
MANIFEST_RESOURCE_TEMPLATE = "client-assets/{client_id}.v1.json"
SIDECAR_NAME = "preview-provenance.v1.json"


class PreviewAssetProvenance(DomainModel):
    asset_id: NonBlank
    asset_version: int = Field(gt=0)
    repository_path: NonBlank
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    copied_name: NonBlank
    copied_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreviewProvenance(DomainModel):
    schema_version: Literal[1] = 1
    client_id: ClientId
    campaign_week: date
    content_package_id: NonBlank
    content_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_approval_state_at_generation: Literal["approved"]
    preview_html_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    assets: tuple[PreviewAssetProvenance, ...]
    asset_manifest_source: NonBlank
    asset_manifest_revision: int = Field(gt=0)
    generated_at: datetime


class PreviewStatus(DomainModel):
    state: Literal["missing", "current", "stale", "unverified", "invalid"]
    diagnostic: NonBlank
    preview_directory: Path = Field(exclude=True)
    provenance: PreviewProvenance | None = Field(default=None, exclude=True)


PREVIEW_ATTENTION: dict[str, str] = {
    "missing": "Generate the local campaign preview.",
    "current": "Review the current local campaign preview; nothing has been published.",
    "stale": (
        "Regenerate the campaign preview because Casey's package or approved assets changed."
    ),
    "unverified": (
        "Regenerate the legacy campaign preview so its package and assets can be verified."
    ),
    "invalid": (
        "Regenerate the campaign preview because its files or provenance failed validation."
    ),
}

PREVIEW_ROUTE_GUIDANCE: dict[str, tuple[str, str]] = {
    "missing": (
        "Campaign preview hasn't been generated",
        "Casey's package must be approved before a local campaign preview can be generated.",
    ),
    "stale": (
        "Campaign preview is out of date",
        "It no longer matches Casey's approved package or the approved campaign assets.",
    ),
    "unverified": (
        "Campaign preview can't be verified",
        "This legacy preview has no provenance record and must be regenerated before review.",
    ),
    "invalid": (
        "Campaign preview failed validation",
        "Its files or provenance failed validation, so it must be regenerated before review.",
    ),
}


class PreviewWorkflowError(ValueError):
    """Base error for preview generation and validation."""


class PreviewWorkflowConflictError(PreviewWorkflowError):
    """Raised when confirmed preview inputs changed."""


class PreviewStatusService:
    """Classify a campaign preview without modifying it."""

    def __init__(self, repository_root: Path, loader: FilesystemResourceLoader) -> None:
        self._root = repository_root.resolve(strict=True)
        self._loader = loader
        self._preview_root = (self._root / "artifacts" / "campaign-previews").resolve(
            strict=False
        )

    async def inspect(
        self,
        *,
        client_id: ClientId,
        week: date,
        package_path: Path,
        preview_directory: Path,
    ) -> PreviewStatus:
        directory = await asyncio.to_thread(preview_directory.resolve, strict=False)
        if not directory.is_relative_to(self._preview_root):
            return PreviewStatus(
                state="invalid",
                diagnostic="Preview directory escapes the configured preview root.",
                preview_directory=directory,
            )
        html_path = directory / "index.html"
        sidecar = directory / SIDECAR_NAME
        if not directory.exists() or not html_path.exists():
            return PreviewStatus(
                state="missing",
                diagnostic="No local campaign preview exists.",
                preview_directory=directory,
            )
        if not sidecar.exists():
            return PreviewStatus(
                state="unverified",
                diagnostic="This legacy preview has no provenance record.",
                preview_directory=directory,
            )
        if sidecar.is_symlink() or html_path.is_symlink():
            return self._invalid(directory, "Preview files must not be symbolic links.")
        try:
            if not sidecar.resolve(strict=True).is_relative_to(directory):
                return self._invalid(directory, "Preview provenance path is unsafe.")
            if not html_path.resolve(strict=True).is_relative_to(directory):
                return self._invalid(directory, "Preview HTML path is unsafe.")
            provenance = PreviewProvenance.model_validate_json(
                await asyncio.to_thread(sidecar.read_text, encoding="utf-8")
            )
            package_raw = await asyncio.to_thread(package_path.read_bytes)
            package = ContentPackage.model_validate_json(package_raw)
            manifest = ClientAssetManifest.model_validate_json(
                await self._loader.load_text(
                    MANIFEST_RESOURCE_TEMPLATE.format(client_id=client_id)
                )
            )
        except (OSError, ValidationError, ValueError):
            return PreviewStatus(
                state="invalid",
                diagnostic="Preview provenance or campaign inputs are invalid.",
                preview_directory=directory,
            )
        if provenance.client_id != client_id or provenance.campaign_week != week:
            return self._invalid(directory, "Preview provenance belongs to another campaign.")
        try:
            html_checksum = await asyncio.to_thread(_sha256_file, html_path)
            for asset in provenance.assets:
                candidate = directory / "assets" / asset.copied_name
                if candidate.is_symlink():
                    return self._invalid(directory, "Preview asset path is unsafe.")
                copied = candidate.resolve(strict=True)
                if not copied.is_relative_to((directory / "assets").resolve()):
                    return self._invalid(directory, "Preview asset path is unsafe.")
                if await asyncio.to_thread(_sha256_file, copied) != asset.copied_sha256:
                    return self._invalid(directory, "A copied preview asset checksum failed.")
        except OSError:
            return self._invalid(directory, "A required preview file is missing or unsafe.")
        if html_checksum != provenance.preview_html_sha256:
            return self._invalid(directory, "The preview HTML checksum failed.")
        package_checksum = hashlib.sha256(package_raw).hexdigest()
        if (
            provenance.content_package_id != package.package_id
            or provenance.content_package_sha256 != package_checksum
            or package.approval_state is not BriefApprovalState.APPROVED
        ):
            return self._stale(directory, provenance, "The approved content package changed.")
        if (
            provenance.asset_manifest_source != manifest.source_reference
            or provenance.asset_manifest_revision != manifest.manifest_revision
        ):
            return self._stale(directory, provenance, "The asset manifest changed.")
        manifest_assets = {asset.asset_id: asset for asset in manifest.assets}
        for recorded in provenance.assets:
            current = manifest_assets.get(recorded.asset_id)
            if (
                current is None
                or current.approval_state is not AssetApprovalState.APPROVED
                or current.asset_version != recorded.asset_version
                or current.repository_path != recorded.repository_path
                or current.checksum.value != recorded.sha256
                or current.source.source_type != "local_derivative"
                or current.transformation is None
            ):
                return self._stale(directory, provenance, "An approved asset changed.")
            try:
                source_checksum = await asyncio.to_thread(
                    _sha256_file, self._root / current.repository_path
                )
            except OSError:
                return self._stale(directory, provenance, "An approved asset is unavailable.")
            if source_checksum != recorded.sha256:
                return self._stale(directory, provenance, "An approved asset checksum changed.")
        for draft in package.drafts:
            required_use = _channel_use(draft.channel)
            for recommendation in draft.asset_recommendations:
                current = manifest_assets.get(recommendation.asset_id)
                if current is None or required_use not in current.approved_uses:
                    return self._stale(
                        directory,
                        provenance,
                        "An asset is no longer permitted for its assignment channel.",
                    )
        return PreviewStatus(
            state="current",
            diagnostic="Preview provenance matches the approved package and assets.",
            preview_directory=directory,
            provenance=provenance,
        )

    @staticmethod
    def _invalid(directory: Path, diagnostic: str) -> PreviewStatus:
        return PreviewStatus(state="invalid", diagnostic=diagnostic, preview_directory=directory)

    @staticmethod
    def _stale(
        directory: Path, provenance: PreviewProvenance, diagnostic: str
    ) -> PreviewStatus:
        return PreviewStatus(
            state="stale",
            diagnostic=diagnostic,
            preview_directory=directory,
            provenance=provenance,
        )


class GenerateVerifiedCampaignPreview:
    """Generate a complete preview plus sidecar, then promote it as one directory."""

    def __init__(self, repository_root: Path, loader: FilesystemResourceLoader) -> None:
        self._root = repository_root.resolve(strict=True)
        self._loader = loader
        self._preview_root = self._root / "artifacts" / "campaign-previews"
        self._status = PreviewStatusService(self._root, loader)

    async def execute(
        self,
        *,
        client_id: ClientId,
        week: date,
        package_path: Path,
        expected_package_checksum: str,
        expected_state: str,
        expected_asset_binding: str,
    ) -> PreviewStatus:
        package_raw = await asyncio.to_thread(package_path.read_bytes)
        if hashlib.sha256(package_raw).hexdigest() != expected_package_checksum:
            raise PreviewWorkflowConflictError("The content package changed after confirmation.")
        package = ContentPackage.model_validate_json(package_raw)
        if package.client_id != client_id or package.week != week:
            raise PreviewWorkflowError("The package does not match the selected campaign.")
        if package.approval_state is not BriefApprovalState.APPROVED:
            raise PreviewWorkflowError("Casey's package must be approved first.")
        manifest = ClientAssetManifest.model_validate_json(
            await self._loader.load_text(MANIFEST_RESOURCE_TEMPLATE.format(client_id=client_id))
        )
        binding = asset_binding(manifest, package)
        if binding != expected_asset_binding:
            raise PreviewWorkflowConflictError("Approved assets changed after confirmation.")
        destination = self._preview_root / package.package_id
        current = await self._status.inspect(
            client_id=client_id,
            week=week,
            package_path=package_path,
            preview_directory=destination,
        )
        if current.state != expected_state:
            raise PreviewWorkflowConflictError("Preview state changed after confirmation.")
        if current.state == "current":
            raise PreviewWorkflowError("The current preview does not need regeneration.")
        client = ClientProfile.model_validate_json(
            await self._loader.load_text(CLIENT_RESOURCE_TEMPLATE.format(client_id=client_id))
        )
        self._preview_root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix=".preview-", dir=self._preview_root))
        backup = self._preview_root / f".{package.package_id}.backup"
        try:
            result = await GenerateCampaignPreview(self._root, staging_root).execute(
                package,
                manifest,
                approved_destinations=tuple(link.url for link in client.purchase_links),
            )
            assets = _provenance_assets(self._root, manifest, package, result.asset_path)
            provenance = PreviewProvenance(
                client_id=client_id,
                campaign_week=week,
                content_package_id=package.package_id,
                content_package_sha256=expected_package_checksum,
                package_approval_state_at_generation="approved",
                preview_html_sha256=_sha256_file(result.html_path),
                assets=assets,
                asset_manifest_source=manifest.source_reference,
                asset_manifest_revision=manifest.manifest_revision,
                generated_at=datetime.now(UTC),
            )
            _atomic_write(
                result.html_path.parent / SIDECAR_NAME,
                (provenance.model_dump_json(indent=2) + "\n").encode(),
            )
            staged = result.html_path.parent
            if backup.exists():
                shutil.rmtree(backup)
            if destination.exists():
                shutil.move(str(destination), str(backup))
            try:
                shutil.move(str(staged), str(destination))
            except Exception:
                if backup.exists():
                    shutil.move(str(backup), str(destination))
                raise
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            raise
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
        return await self._status.inspect(
            client_id=client_id,
            week=week,
            package_path=package_path,
            preview_directory=destination,
        )


def asset_binding(manifest: ClientAssetManifest, package: ContentPackage) -> str:
    ids = sorted(
        {item.asset_id for draft in package.drafts for item in draft.asset_recommendations}
    )
    records = []
    for asset_id in ids:
        matches = [asset for asset in manifest.assets if asset.asset_id == asset_id]
        if len(matches) != 1:
            raise PreviewWorkflowError("A recommended asset is missing or ambiguous.")
        asset = matches[0]
        if asset.approval_state is not AssetApprovalState.APPROVED:
            raise PreviewWorkflowError("A recommended asset is not approved.")
        records.append(
            ":".join(
                (
                    asset.asset_id,
                    str(asset.asset_version),
                    asset.repository_path,
                    asset.checksum.value,
                    asset.approval_state.value,
                    ",".join(sorted(asset.approved_uses)),
                )
            )
        )
    return hashlib.sha256(
        f"{manifest.manifest_revision}|{'|'.join(records)}".encode()
    ).hexdigest()


def _provenance_assets(
    root: Path,
    manifest: ClientAssetManifest,
    package: ContentPackage,
    copied_path: Path,
) -> tuple[PreviewAssetProvenance, ...]:
    ids = {item.asset_id for draft in package.drafts for item in draft.asset_recommendations}
    return tuple(
        PreviewAssetProvenance(
            asset_id=asset.asset_id,
            asset_version=asset.asset_version,
            repository_path=asset.repository_path,
            sha256=asset.checksum.value,
            copied_name=copied_path.name,
            copied_sha256=_sha256_file(copied_path),
        )
        for asset in manifest.assets
        if asset.asset_id in ids
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _channel_use(channel: str) -> str:
    normalized = channel.lower()
    if "social" in normalized:
        return "social_posts"
    if "email" in normalized:
        return "email_marketing"
    if "website" in normalized:
        return "official_website"
    return "campaign_package_previews"


def _atomic_write(path: Path, content: bytes) -> None:
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
