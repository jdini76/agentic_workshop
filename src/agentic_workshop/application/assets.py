"""Local-only validation and recommendation of approved client assets."""

import asyncio
import hashlib
import struct
from pathlib import Path

from agentic_workshop.domain.assets import (
    AssetApprovalState,
    AssetRecommendation,
    AssetValidationResult,
    ClientAsset,
    ClientAssetManifest,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RECOMMENDATION_USE = "content_package_asset_recommendation"


class AssetValidationError(ValueError):
    """Raised when an asset cannot satisfy its approved manifest entry."""


class ClientAssetInventory:
    """Verify local originals without changing or exposing their embedded metadata."""

    def __init__(self, repository_root: Path) -> None:
        self._root = repository_root.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("repository root must be a directory")

    async def validate(self, asset: ClientAsset) -> AssetValidationResult:
        try:
            verified = await asyncio.to_thread(self._validate_sync, asset)
        except AssetValidationError as error:
            return AssetValidationResult(
                asset_id=asset.asset_id,
                valid=False,
                diagnostic=str(error),
            )
        return AssetValidationResult(
            asset_id=asset.asset_id,
            valid=True,
            diagnostic="path, type, dimensions, file size, and SHA-256 checksum verified",
            verified_path=verified.as_posix(),
        )

    async def inventory(
        self, manifest: ClientAssetManifest
    ) -> tuple[AssetValidationResult, ...]:
        return tuple([await self.validate(asset) for asset in manifest.assets])

    async def recommendations(
        self, manifest: ClientAssetManifest
    ) -> tuple[AssetRecommendation, ...]:
        recommendations: list[AssetRecommendation] = []
        for asset in manifest.assets:
            if asset.approval_state is not AssetApprovalState.APPROVED:
                continue
            if RECOMMENDATION_USE not in asset.approved_uses:
                continue
            result = await self.validate(asset)
            recommendations.append(
                AssetRecommendation(
                    asset_id=asset.asset_id,
                    asset_type=asset.asset_type,
                    repository_path=asset.repository_path,
                    manifest_source=manifest.source_reference,
                    availability="available" if result.valid else "unavailable",
                    diagnostic=result.diagnostic,
                    approved_use=RECOMMENDATION_USE,
                    permitted_uses=asset.approved_uses,
                )
            )
        return tuple(recommendations)

    def _validate_sync(self, asset: ClientAsset) -> Path:
        relative = Path(asset.repository_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise AssetValidationError("asset path is absolute or contains traversal")
        candidate = (self._root / relative).resolve(strict=False)
        if not candidate.is_relative_to(self._root):
            raise AssetValidationError("asset path escapes repository root")
        if not candidate.is_file():
            raise AssetValidationError(f"asset file is missing: {asset.repository_path}")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self._root):
            raise AssetValidationError("asset symlink escapes repository root")
        if resolved.suffix.lower() != ".png" or asset.file_format != "png":
            raise AssetValidationError("unsupported asset file type; expected PNG")
        data = resolved.read_bytes()
        if not data.startswith(PNG_SIGNATURE):
            raise AssetValidationError("file content is not a valid PNG signature")
        if len(data) != asset.file_size_bytes:
            raise AssetValidationError("asset file size does not match manifest")
        if hashlib.sha256(data).hexdigest() != asset.checksum.value:
            raise AssetValidationError("asset SHA-256 checksum does not match manifest")
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise AssetValidationError("PNG is missing a valid IHDR header")
        width, height = struct.unpack(">II", data[16:24])
        if (width, height) != (
            asset.dimensions.width_px,
            asset.dimensions.height_px,
        ):
            raise AssetValidationError("asset dimensions do not match manifest")
        return resolved.relative_to(self._root)
