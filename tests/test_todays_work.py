import asyncio
import shutil
from pathlib import Path

import pytest

from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.application.todays_work import (
    LoadTodaysWork,
    TodaysWorkError,
    TodaysWorkSnapshot,
)
from agentic_workshop.cli import run
from agentic_workshop.domain.assets import AssetRecommendation, ClientAssetManifest
from agentic_workshop.domain.content import ContentDraft, ContentPackage
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief

REPOSITORY_ROOT = Path(__file__).parents[1]
SOURCE_RESOURCES = REPOSITORY_ROOT / "src" / "agentic_workshop" / "resources"
SOURCE_BRIEF = (
    REPOSITORY_ROOT
    / "artifacts"
    / "weekly-briefs"
    / "jordan-and-the-fosters-2026-08-03.json"
)
SOURCE_PACKAGE = (
    REPOSITORY_ROOT
    / "artifacts"
    / "content-packages"
    / "jordan-and-the-fosters-2026-08-03-content.json"
)
CLIENT_ID = "jordan-and-the-fosters"


def workspace(root: Path) -> tuple[Path, Path, Path, Path]:
    resources = root / "resources"
    (resources / "clients").mkdir(parents=True)
    (resources / "client-assets").mkdir()
    shutil.copyfile(
        SOURCE_RESOURCES / "clients" / f"{CLIENT_ID}.v1.json",
        resources / "clients" / f"{CLIENT_ID}.v1.json",
    )
    manifest_path = resources / "client-assets" / f"{CLIENT_ID}.v1.json"
    shutil.copyfile(
        SOURCE_RESOURCES / "client-assets" / f"{CLIENT_ID}.v1.json",
        manifest_path,
    )
    manifest = ClientAssetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    derivative = manifest.assets[1]
    local_asset = root / derivative.repository_path
    local_asset.parent.mkdir(parents=True)
    shutil.copyfile(REPOSITORY_ROOT / derivative.repository_path, local_asset)

    artifacts = root / "artifacts"
    brief_path = artifacts / "weekly-briefs" / "brief.json"
    package_path = artifacts / "visual-enabled" / "package.json"
    preview_path = artifacts / "campaign-previews" / "campaign" / "index.html"
    brief_path.parent.mkdir(parents=True)
    package_path.parent.mkdir(parents=True)
    preview_path.parent.mkdir(parents=True)
    shutil.copyfile(SOURCE_BRIEF, brief_path)
    package = ContentPackage.model_validate_json(SOURCE_PACKAGE.read_text(encoding="utf-8"))
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
    package = ContentPackage.model_validate(
        {**package.model_dump(mode="json"), "drafts": drafts}
    )
    package_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    preview_path.write_text("<!doctype html><title>Preview</title>", encoding="utf-8")
    return resources, brief_path, package_path, preview_path


def load_snapshot(
    root: Path,
    resources: Path,
    brief_path: Path,
    package_path: Path,
    preview_path: Path,
) -> TodaysWorkSnapshot:
    return asyncio.run(
        LoadTodaysWork(root, FilesystemResourceLoader(resources)).execute(
            CLIENT_ID,
            brief_path=brief_path,
            package_path=package_path,
            preview_path=preview_path,
        )
    )


def test_complete_workspace_snapshot(tmp_path: Path) -> None:
    resources, brief_path, package_path, preview_path = workspace(tmp_path)

    snapshot = load_snapshot(
        tmp_path, resources, brief_path, package_path, preview_path
    )

    assert snapshot.client_name == "Jordan and the Fosters"
    assert snapshot.campaign_week.isoformat() == "2026-08-03"
    assert snapshot.brief.state == "approved"
    assert snapshot.content_package.state == "approved"
    assert len(snapshot.draft_summaries) == 2
    assert snapshot.asset is not None
    assert snapshot.asset.asset_id == "jordan-and-the-fosters-front-cover-marketing-1600h"
    assert snapshot.preview_exists
    assert snapshot.attention == (
        "Review the local campaign preview; nothing has been published.",
    )


def test_pending_workspace_identifies_both_approval_gates(tmp_path: Path) -> None:
    resources, brief_path, package_path, preview_path = workspace(tmp_path)
    brief = WeeklyMarketingBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    brief_path.write_text(
        brief.model_copy(
            update={"approval_state": BriefApprovalState.DRAFT}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    package_path.write_text(
        package.model_copy(
            update={"approval_state": BriefApprovalState.DRAFT}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    snapshot = load_snapshot(
        tmp_path, resources, brief_path, package_path, preview_path
    )

    assert snapshot.brief.state == "draft"
    assert snapshot.content_package.state == "draft"
    assert "Review Sarah's weekly brief." in snapshot.attention
    assert "Review Casey's content package." in snapshot.attention


def test_missing_workspace_has_clear_states_instead_of_errors(tmp_path: Path) -> None:
    resources, brief_path, package_path, preview_path = workspace(tmp_path)
    brief_path.unlink()
    package_path.unlink()
    preview_path.unlink()

    snapshot = load_snapshot(
        tmp_path, resources, brief_path, package_path, preview_path
    )

    assert snapshot.campaign_week is None
    assert snapshot.brief.state == "missing"
    assert snapshot.content_package.state == "missing"
    assert snapshot.draft_summaries == ()
    assert "Sarah's weekly brief is missing." in snapshot.attention
    assert "Casey's content package is missing." in snapshot.attention
    assert "The local campaign preview has not been generated." in snapshot.attention


def test_workspace_rejects_mismatched_client_artifact(tmp_path: Path) -> None:
    resources, brief_path, package_path, preview_path = workspace(tmp_path)
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    package_path.write_text(
        package.model_copy(update={"client_id": "another-client"}).model_dump_json(
            indent=2
        ),
        encoding="utf-8",
    )

    with pytest.raises(TodaysWorkError, match="content package client ID"):
        load_snapshot(tmp_path, resources, brief_path, package_path, preview_path)


def test_todays_work_cli_generates_escaped_dashboard_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    resources, brief_path, package_path, preview_path = workspace(tmp_path)
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    first = ContentDraft.model_validate(
        {**package.drafts[0].model_dump(mode="json"), "title": "<script>unsafe</script>"}
    )
    package_path.write_text(
        ContentPackage.model_validate(
            {**package.model_dump(mode="json"), "drafts": [first, package.drafts[1]]}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    dashboard_root = tmp_path / "artifacts" / "todays-work"
    arguments = [
        "todays-work",
        "--repository-root",
        str(tmp_path),
        "--resource-root",
        str(resources),
        "--brief-file",
        str(brief_path),
        "--package-file",
        str(package_path),
        "--preview-file",
        str(preview_path),
        "--dashboard-root",
        str(dashboard_root),
    ]

    assert run(arguments) == 0
    dashboard = dashboard_root / f"{CLIENT_ID}-2026-08-03" / "index.html"
    rendered = dashboard.read_text(encoding="utf-8")
    assert "Local workspace — nothing is published." in rendered
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in rendered
    assert "<script>" not in rendered
    assert 'href="http://' not in rendered and 'href="https://' not in rendered
    assert "originals" not in rendered
    assert "../../campaign-previews/campaign/index.html" in rendered
    with pytest.raises(SystemExit) as error:
        run(arguments)
    assert error.value.code == 2
    assert run([*arguments, "--overwrite"]) == 0
