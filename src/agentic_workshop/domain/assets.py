"""Governed client visual-asset records and inventory results."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, NonBlank


class AssetType(StrEnum):
    """Supported semantic roles for client assets."""

    FRONT_COVER = "front_cover"


class AssetApprovalState(StrEnum):
    """CEO review state for a manifest asset."""

    DRAFT = "draft"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"


class AssetDimensions(DomainModel):
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)


class AssetChecksum(DomainModel):
    algorithm: Literal["sha256"] = "sha256"
    value: str = Field(pattern=r"^[a-f0-9]{64}$")


class AssetSource(DomainModel):
    source_type: Literal["ceo_supplied_local_file", "local_derivative"]
    description: NonBlank


class AssetAttribution(DomainModel):
    text: str | None = None
    required: bool
    status: Literal["confirmed", "not_confirmed", "rights_confirmed_by_ceo"]

    @model_validator(mode="after")
    def require_confirmed_text(self) -> "AssetAttribution":
        if self.required and (self.text is None or not self.text.strip()):
            raise ValueError("required attribution must include text")
        return self


class AssetTransformation(DomainModel):
    """Exact, non-generative transformation provenance for one derivative."""

    operation: Literal["resize_and_strip_metadata"]
    source_dimensions: AssetDimensions
    output_dimensions: AssetDimensions
    maximum_height_px: int = Field(gt=0)
    resampling: Literal["lanczos"]
    color_space: Literal["sRGB"]
    preserve_aspect_ratio: Literal[True]
    cropped: Literal[False]
    rotated: Literal[False]
    recolored: Literal[False]
    text_changed: Literal[False]
    artwork_changed: Literal[False]
    layout_changed: Literal[False]
    embedded_metadata_removed: Literal[True]
    lossless_png_optimization: Literal[True]


class ClientAsset(DomainModel):
    """One immutable-original asset governed by explicit CEO permissions."""

    asset_id: NonBlank
    asset_version: int = Field(gt=0)
    name: NonBlank
    description: NonBlank
    asset_type: AssetType
    parent_asset_id: str | None = None
    parent_asset_version: int | None = Field(default=None, gt=0)
    transformation: AssetTransformation | None = None
    repository_path: NonBlank
    file_format: Literal["png"]
    dimensions: AssetDimensions
    file_size_bytes: int = Field(gt=0)
    checksum: AssetChecksum
    source: AssetSource
    approval_state: AssetApprovalState = AssetApprovalState.DRAFT
    revision_note: str | None = Field(default=None, min_length=1)
    approved_uses: tuple[NonBlank, ...]
    permitted_transformations: tuple[NonBlank, ...]
    attribution: AssetAttribution
    restrictions: tuple[NonBlank, ...]

    @model_validator(mode="after")
    def validate_governance(self) -> "ClientAsset":
        if self.approval_state is AssetApprovalState.REVISION_REQUESTED:
            if self.revision_note is None or not self.revision_note.strip():
                raise ValueError("revision_note is required when asset revision is requested")
        elif self.revision_note is not None:
            raise ValueError("revision_note is only valid when asset revision is requested")
        if not self.restrictions:
            raise ValueError("asset restrictions cannot be empty")
        parent_fields = (
            self.parent_asset_id,
            self.parent_asset_version,
            self.transformation,
        )
        if any(value is not None for value in parent_fields) and not all(
            value is not None for value in parent_fields
        ):
            raise ValueError("derivative parent and transformation fields must be complete")
        forbidden_uses = {"automatic_publication", "external_delivery"}
        if forbidden_uses.intersection(self.approved_uses):
            raise ValueError("asset use cannot authorize publication or external delivery")
        return self


class ClientAssetManifest(DomainModel):
    """Versioned client asset catalog; derivatives must be separate assets."""

    schema_version: Literal[1] = 1
    manifest_revision: int = Field(gt=0)
    client_id: ClientId
    source_reference: NonBlank
    assets: tuple[ClientAsset, ...]

    @model_validator(mode="after")
    def require_unique_asset_ids(self) -> "ClientAssetManifest":
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset IDs must be unique")
        return self


class AssetValidationResult(DomainModel):
    """Read-only verification outcome for one manifest entry."""

    asset_id: NonBlank
    valid: bool
    diagnostic: NonBlank
    verified_path: str | None = None


class AssetRecommendation(DomainModel):
    """Safe package metadata; it never embeds or publishes the asset."""

    asset_id: NonBlank
    asset_type: AssetType
    repository_path: NonBlank
    manifest_source: NonBlank
    availability: Literal["available", "unavailable"]
    diagnostic: NonBlank
    approved_use: Literal["content_package_asset_recommendation"]
    permitted_uses: tuple[NonBlank, ...] = ()
