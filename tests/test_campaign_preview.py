import asyncio
import hashlib
import shutil
from pathlib import Path

import pytest

from agentic_workshop.application.preview import (
    CampaignPreviewError,
    GenerateCampaignPreview,
)
from agentic_workshop.domain.assets import (
    AssetRecommendation,
    ClientAssetManifest,
)
from agentic_workshop.domain.content import ContentDraft, ContentPackage

REPOSITORY_ROOT = Path(__file__).parents[1]
PACKAGE_PATH = (
    REPOSITORY_ROOT
    / "artifacts"
    / "content-packages"
    / "jordan-and-the-fosters-2026-08-03-content.json"
)
MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "src"
    / "agentic_workshop"
    / "resources"
    / "client-assets"
    / "jordan-and-the-fosters.v1.json"
)
AMAZON_URL = "https://www.amazon.com/gp/aw/d/B0D5BT1XDZ"


def preview_inputs(root: Path) -> tuple[ContentPackage, ClientAssetManifest, Path]:
    package = ContentPackage.model_validate_json(PACKAGE_PATH.read_text(encoding="utf-8"))
    source_manifest = ClientAssetManifest.model_validate_json(
        MANIFEST_PATH.read_text(encoding="utf-8")
    )
    derivative = source_manifest.assets[1]
    local_asset = root / derivative.repository_path
    local_asset.parent.mkdir(parents=True)
    shutil.copyfile(REPOSITORY_ROOT / derivative.repository_path, local_asset)
    assert hashlib.sha256(local_asset.read_bytes()).hexdigest() == derivative.checksum.value
    manifest = source_manifest.model_copy(update={"assets": (derivative,)})
    recommendation = AssetRecommendation(
        asset_id=derivative.asset_id,
        asset_type=derivative.asset_type,
        repository_path=derivative.repository_path,
        manifest_source=manifest.source_reference,
        availability="available",
        diagnostic="verified",
        approved_use="content_package_asset_recommendation",
        permitted_uses=derivative.approved_uses,
    )
    drafts = tuple(
        ContentDraft.model_validate(
            {
                **draft.model_dump(mode="json"),
                "asset_recommendations": [recommendation.model_dump(mode="json")],
            }
        )
        for draft in package.drafts
    )
    approved = ContentPackage.model_validate(
        {**package.model_dump(mode="json"), "drafts": drafts}
    )
    preview_root = root / "artifacts" / "campaign-previews"
    return approved, manifest, preview_root


def test_preview_is_static_escaped_and_copies_only_the_derivative(tmp_path: Path) -> None:
    package, manifest, preview_root = preview_inputs(tmp_path)
    website = package.drafts[0]
    escaped_website = ContentDraft.model_validate(
        {
            **website.model_dump(mode="json"),
            "title": "Safe <script>alert(1)</script>",
            "body": website.body + "\nLiteral <b>review text</b>",
        }
    )
    package = ContentPackage.model_validate(
        {
            **package.model_dump(mode="json"),
            "drafts": [escaped_website, package.drafts[1]],
        }
    )

    result = asyncio.run(
        GenerateCampaignPreview(tmp_path, preview_root).execute(
            package,
            manifest,
            approved_destinations=(AMAZON_URL,),
        )
    )

    rendered = result.html_path.read_text(encoding="utf-8")
    assert "Local campaign preview — not published." in rendered
    assert "Safe &lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "Literal &lt;b&gt;review text&lt;/b&gt;" in rendered
    assert "<script" not in rendered
    assert "data:image" not in rendered
    assert f'href="{AMAZON_URL}"' in rendered
    assert "cdn" not in rendered.lower()
    assert result.asset_path.read_bytes() == (
        tmp_path / manifest.assets[0].repository_path
    ).read_bytes()
    assert "originals" not in result.asset_path.as_posix()


def test_preview_rejects_unapproved_package_and_existing_output(tmp_path: Path) -> None:
    package, manifest, preview_root = preview_inputs(tmp_path)
    service = GenerateCampaignPreview(tmp_path, preview_root)
    draft = package.model_copy(update={"approval_state": "draft"})

    with pytest.raises(CampaignPreviewError, match="approved ContentPackage"):
        asyncio.run(
            service.execute(draft, manifest, approved_destinations=(AMAZON_URL,))
        )

    asyncio.run(service.execute(package, manifest, approved_destinations=(AMAZON_URL,)))
    with pytest.raises(CampaignPreviewError, match="already exists"):
        asyncio.run(service.execute(package, manifest, approved_destinations=(AMAZON_URL,)))
    result = asyncio.run(
        service.execute(
            package,
            manifest,
            approved_destinations=(AMAZON_URL,),
            overwrite=True,
        )
    )
    assert result.html_path.is_file()


@pytest.mark.parametrize("failure", ["unavailable", "wrong_use", "original"])
def test_preview_rejects_ineligible_assignment_assets(
    tmp_path: Path, failure: str
) -> None:
    package, manifest, preview_root = preview_inputs(tmp_path)
    recommendation = package.drafts[0].asset_recommendations[0]
    if failure == "unavailable":
        changed = recommendation.model_copy(update={"availability": "unavailable"})
        manifest_for_test = manifest
    elif failure == "wrong_use":
        changed = recommendation.model_copy(
            update={"permitted_uses": ("content_package_asset_recommendation",)}
        )
        manifest_for_test = manifest
    else:
        original = ClientAssetManifest.model_validate_json(
            MANIFEST_PATH.read_text(encoding="utf-8")
        ).assets[0]
        changed = recommendation.model_copy(
            update={
                "asset_id": original.asset_id,
                "repository_path": original.repository_path,
            }
        )
        manifest_for_test = manifest.model_copy(update={"assets": (original,)})
    first = ContentDraft.model_validate(
        {
            **package.drafts[0].model_dump(mode="json"),
            "asset_recommendations": [changed.model_dump(mode="json")],
        }
    )
    changed_package = ContentPackage.model_validate(
        {
            **package.model_dump(mode="json"),
            "drafts": [first, package.drafts[1]],
        }
    )

    with pytest.raises(CampaignPreviewError):
        asyncio.run(
            GenerateCampaignPreview(tmp_path, preview_root).execute(
                changed_package,
                manifest_for_test,
                approved_destinations=(AMAZON_URL,),
            )
        )


def test_preview_output_cannot_escape_ignored_artifact_root(tmp_path: Path) -> None:
    with pytest.raises(CampaignPreviewError, match="artifacts/campaign-previews"):
        GenerateCampaignPreview(tmp_path, tmp_path / "public")

    package, manifest, preview_root = preview_inputs(tmp_path)
    unsafe = package.model_copy(update={"package_id": "../escaped"})
    with pytest.raises(CampaignPreviewError, match="package ID would escape"):
        asyncio.run(
            GenerateCampaignPreview(tmp_path, preview_root).execute(
                unsafe,
                manifest,
                approved_destinations=(AMAZON_URL,),
            )
        )
