import asyncio
import binascii
import hashlib
import struct
import zlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_workshop.application.assets import PNG_SIGNATURE, ClientAssetInventory
from agentic_workshop.cli import run
from agentic_workshop.domain.assets import (
    AssetApprovalState,
    ClientAsset,
    ClientAssetManifest,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
JORDAN_MANIFEST = (
    REPOSITORY_ROOT
    / "src"
    / "agentic_workshop"
    / "resources"
    / "client-assets"
    / "jordan-and-the-fosters.v1.json"
)
PRIVATE_METADATA_MARKERS = (
    b"Canva",
    b"<x:xmpmeta",
    b"ns.attribution.com",
    b"CreatorTool",
    b"Exif\x00\x00",
)


def png_chunk(name: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(name + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", checksum)


def make_test_png() -> bytes:
    """Build a synthetic PNG at runtime; no encoded image fixture is stored in Git."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00\x00\x00")
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", pixels)
        + png_chunk(b"IEND", b"")
    )


def write_manifest(root: Path, *, state: str = "approved") -> tuple[Path, ClientAssetManifest]:
    png = make_test_png()
    original = root / "assets" / "clients" / "client" / "originals" / "cover.png"
    original.parent.mkdir(parents=True)
    original.write_bytes(png)
    payload = {
        "schema_version": 1,
        "manifest_revision": 1,
        "client_id": "client",
        "source_reference": "client-assets/client.v1.json",
        "assets": [
            {
                "asset_id": "official-cover",
                "asset_version": 1,
                "name": "Official front cover",
                "description": "CEO supplied cover",
                "asset_type": "front_cover",
                "repository_path": "assets/clients/client/originals/cover.png",
                "file_format": "png",
                "dimensions": {"width_px": 1, "height_px": 1},
                "file_size_bytes": len(png),
                "checksum": {
                    "algorithm": "sha256",
                    "value": hashlib.sha256(png).hexdigest(),
                },
                "source": {
                    "source_type": "ceo_supplied_local_file",
                    "description": "Supplied locally by CEO",
                },
                "approval_state": state,
                "revision_note": None,
                "approved_uses": ["content_package_asset_recommendation"],
                "permitted_transformations": [],
                "attribution": {
                    "text": None,
                    "required": False,
                    "status": "not_confirmed",
                },
                "restrictions": ["Do not transform or publish"],
            }
        ],
    }
    manifest = ClientAssetManifest.model_validate(payload)
    path = root / "manifest.json"
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path, manifest


def test_approved_asset_is_verified_before_recommendation(tmp_path: Path) -> None:
    _, manifest = write_manifest(tmp_path)

    recommendations = asyncio.run(ClientAssetInventory(tmp_path).recommendations(manifest))

    assert len(recommendations) == 1
    assert recommendations[0].asset_id == "official-cover"
    assert recommendations[0].availability == "available"
    assert "verified" in recommendations[0].diagnostic


@pytest.mark.parametrize("failure", ["missing", "modified"])
def test_missing_or_modified_asset_makes_recommendation_unavailable(
    tmp_path: Path, failure: str
) -> None:
    _, manifest = write_manifest(tmp_path)
    original = tmp_path / manifest.assets[0].repository_path
    if failure == "missing":
        original.unlink()
    else:
        original.write_bytes(make_test_png() + b"changed")

    recommendation = asyncio.run(
        ClientAssetInventory(tmp_path).recommendations(manifest)
    )[0]

    assert recommendation.availability == "unavailable"
    assert "missing" in recommendation.diagnostic or "does not match" in recommendation.diagnostic


def test_path_traversal_and_unsupported_type_are_rejected(tmp_path: Path) -> None:
    _, manifest = write_manifest(tmp_path)
    asset = manifest.assets[0]
    traversal = asset.model_copy(update={"repository_path": "../cover.png"})
    unsupported = asset.model_copy(
        update={"repository_path": "assets/clients/client/originals/cover.jpg"}
    )
    (tmp_path / unsupported.repository_path).write_bytes(make_test_png())
    inventory = ClientAssetInventory(tmp_path)

    traversal_result = asyncio.run(inventory.validate(traversal))
    unsupported_result = asyncio.run(inventory.validate(unsupported))

    assert not traversal_result.valid
    assert "traversal" in traversal_result.diagnostic
    assert not unsupported_result.valid
    assert "unsupported" in unsupported_result.diagnostic


def test_unapproved_asset_is_not_recommended_and_revision_requires_note(
    tmp_path: Path,
) -> None:
    _, manifest = write_manifest(tmp_path, state="draft")
    assert asyncio.run(ClientAssetInventory(tmp_path).recommendations(manifest)) == ()
    asset_data = manifest.assets[0].model_dump(mode="json")
    asset_data["approval_state"] = "revision_requested"
    with pytest.raises(ValidationError, match="revision_note is required"):
        ClientAssetManifest.model_validate(
            {**manifest.model_dump(mode="json"), "assets": [asset_data]}
        )


def test_asset_review_cli_approves_and_requests_revision(tmp_path: Path) -> None:
    manifest_path, _ = write_manifest(tmp_path, state="draft")

    assert run(
        [
            "asset-review",
            str(manifest_path),
            "official-cover",
            "--repository-root",
            str(tmp_path),
            "--approve",
        ]
    ) == 0
    approved = ClientAssetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    assert approved.assets[0].approval_state is AssetApprovalState.APPROVED
    assert approved.manifest_revision == 2

    assert run(
        [
            "asset-review",
            str(manifest_path),
            "official-cover",
            "--repository-root",
            str(tmp_path),
            "--request-revision",
            "Confirm public-use rights.",
        ]
    ) == 0
    revised = ClientAssetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    assert revised.assets[0].approval_state is AssetApprovalState.REVISION_REQUESTED
    assert revised.assets[0].revision_note == "Confirm public-use rights."
    assert revised.manifest_revision == 3


def test_asset_review_refuses_checksum_mismatch(tmp_path: Path) -> None:
    manifest_path, manifest = write_manifest(tmp_path, state="draft")
    (tmp_path / manifest.assets[0].repository_path).write_bytes(
        make_test_png() + b"changed"
    )

    with pytest.raises(SystemExit) as error:
        run(
            [
                "asset-review",
                str(manifest_path),
                "official-cover",
                "--repository-root",
                str(tmp_path),
                "--approve",
            ]
        )

    assert error.value.code == 2


def load_jordan_manifest() -> ClientAssetManifest:
    return ClientAssetManifest.model_validate_json(JORDAN_MANIFEST.read_text(encoding="utf-8"))


def test_jordan_original_checksum_is_unchanged_when_local_original_exists() -> None:
    manifest = load_jordan_manifest()
    original = manifest.assets[0]
    original_path = REPOSITORY_ROOT / original.repository_path

    assert original.checksum.value == (
        "3e63da30359b5c0ff1a4df8b49ac4fa0cdc685f2ac277f63179a1de2df827b5b"
    )
    if original_path.exists():
        assert hashlib.sha256(original_path.read_bytes()).hexdigest() == original.checksum.value


def _asset(manifest: ClientAssetManifest, asset_id: str) -> ClientAsset:
    matches = [asset for asset in manifest.assets if asset.asset_id == asset_id]
    assert len(matches) == 1
    return matches[0]


def test_jordan_derivative_preserves_aspect_ratio_and_contains_no_private_metadata() -> None:
    manifest = load_jordan_manifest()
    original = _asset(manifest, "jordan-and-the-fosters-front-cover")
    derivative = _asset(manifest, "jordan-and-the-fosters-front-cover-marketing-1600h")
    derivative_path = REPOSITORY_ROOT / derivative.repository_path
    raw = derivative_path.read_bytes()

    assert derivative.dimensions.width_px * original.dimensions.height_px == (
        derivative.dimensions.height_px * original.dimensions.width_px
    )
    assert derivative.dimensions.width_px == 1576
    assert derivative.dimensions.height_px == 1600
    assert hashlib.sha256(raw).hexdigest() == derivative.checksum.value
    assert all(marker not in raw for marker in PRIVATE_METADATA_MARKERS)
    assert derivative.transformation is not None
    assert derivative.transformation.embedded_metadata_removed


def test_approved_derivative_is_an_available_marketing_recommendation() -> None:
    manifest = load_jordan_manifest()
    original = _asset(manifest, "jordan-and-the-fosters-front-cover")
    derivative = _asset(manifest, "jordan-and-the-fosters-front-cover-marketing-1600h")

    recommendations = asyncio.run(
        ClientAssetInventory(REPOSITORY_ROOT).recommendations(manifest)
    )

    assert derivative.approval_state is AssetApprovalState.APPROVED
    assert derivative.approved_uses == (
        "content_package_asset_recommendation",
        "official_website",
        "social_posts",
        "email_marketing",
        "campaign_package_previews",
        "facebook_page_auto_publish",
        "website_auto_publish",
    )
    recommended_ids = {item.asset_id for item in recommendations}
    assert derivative.asset_id in recommended_ids
    assert original.asset_id not in recommended_ids
    matching = [item for item in recommendations if item.asset_id == derivative.asset_id]
    assert len(matching) == 1
    assert matching[0].availability == "available"


def test_approved_derivative_authorizes_publication_only_for_named_destinations() -> None:
    derivative = _asset(
        load_jordan_manifest(), "jordan-and-the-fosters-front-cover-marketing-1600h"
    )

    assert "automatic_publication" not in derivative.approved_uses
    assert "external_delivery" not in derivative.approved_uses
    assert set(derivative.approved_uses) & {
        "facebook_page_auto_publish",
        "website_auto_publish",
    } == {"facebook_page_auto_publish", "website_auto_publish"}
    assert any(
        "Automatic publication is approved only for the Facebook Page and official website"
        in restriction
        for restriction in derivative.restrictions
    )
