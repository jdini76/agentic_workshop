"""Governed client visual-asset records and inventory results."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, NonBlank


class AssetType(StrEnum):
    """Supported semantic roles for client assets."""

    FRONT_COVER = "front_cover"
    PHOTO = "photo"


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
    """Exact, non-generative transformation provenance for one derivative.

    Two operations are recognized: "resize_and_strip_metadata" (the front-cover derivative --
    resized and metadata-stripped) and "strip_metadata" (metadata-stripped only, dimensions
    unchanged). Resize-only fields (maximum_height_px, resampling) are required for the former
    and forbidden for the latter -- a transformation record must exactly match what was really
    done, never claim a resize that didn't happen.
    """

    operation: Literal["resize_and_strip_metadata", "strip_metadata"]
    source_dimensions: AssetDimensions
    output_dimensions: AssetDimensions
    maximum_height_px: int | None = Field(default=None, gt=0)
    resampling: Literal["lanczos"] | None = None
    color_space: Literal["sRGB"]
    preserve_aspect_ratio: Literal[True]
    cropped: Literal[False]
    rotated: Literal[False]
    recolored: Literal[False]
    text_changed: Literal[False]
    artwork_changed: Literal[False]
    layout_changed: Literal[False]
    embedded_metadata_removed: Literal[True]
    lossless_png_optimization: bool

    @model_validator(mode="after")
    def validate_operation_matches_fields(self) -> "AssetTransformation":
        if self.operation == "resize_and_strip_metadata":
            if self.maximum_height_px is None or self.resampling is None:
                raise ValueError(
                    "resize_and_strip_metadata requires maximum_height_px and resampling"
                )
        else:
            if self.maximum_height_px is not None or self.resampling is not None:
                raise ValueError("strip_metadata must not claim a resize")
            if self.source_dimensions != self.output_dimensions:
                raise ValueError("strip_metadata must not change dimensions")
        return self


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
        # These two literal strings block only *blanket* publication/delivery grants -- an
        # approved_uses value that would authorize an asset for any and every destination. A
        # narrow, destination-specific use (e.g. "facebook_page_auto_publish" or
        # "website_auto_publish") is a deliberate, separate opt-in per destination and does not
        # match this set. It still authorizes nothing by itself: publishing an asset also
        # requires the package it's attached to to be approved by the CEO. See
        # application/publish_content_package.py.
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
