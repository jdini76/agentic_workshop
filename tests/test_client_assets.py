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
    ClientAssetManifest,
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
