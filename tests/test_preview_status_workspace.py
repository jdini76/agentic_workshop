import asyncio
import hashlib
import html
import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pytest

from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.adapters.local_workspace import (
    LocalWorkspaceApp,
    WorkspaceRequest,
    WorkspaceSecurity,
)
from agentic_workshop.application.preview import CampaignPreviewError
from agentic_workshop.application.preview_status import (
    PREVIEW_ATTENTION,
    PREVIEW_ROUTE_GUIDANCE,
    SIDECAR_NAME,
    GenerateVerifiedCampaignPreview,
    PreviewStatusService,
    PreviewWorkflowConflictError,
    PreviewWorkflowError,
    asset_binding,
)
from agentic_workshop.domain.assets import ClientAssetManifest
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.marketing import BriefApprovalState
from tests.test_campaign_preview import preview_inputs
from tests.test_local_workspace import CLIENT_ID, RESOURCES, get, local_config

EXPECTED_PREVIEW_GUIDANCE = {
    "missing": (
        "Generate the local campaign preview.",
        "Campaign preview hasn't been generated",
        "Casey's package must be approved before a local campaign preview can be generated.",
    ),
    "current": (
        "Review the current local campaign preview; nothing has been published.",
        None,
        None,
    ),
    "stale": (
        "Regenerate the campaign preview because Casey's package or approved assets changed.",
        "Campaign preview is out of date",
        "It no longer matches Casey's approved package or the approved campaign assets.",
    ),
    "unverified": (
        "Regenerate the legacy campaign preview so its package and assets can be verified.",
        "Campaign preview can't be verified",
        "This legacy preview has no provenance record and must be regenerated before review.",
    ),
    "invalid": (
        "Regenerate the campaign preview because its files or provenance failed validation.",
        "Campaign preview failed validation",
        "Its files or provenance failed validation, so it must be regenerated before review.",
    ),
}


def test_every_preview_state_has_matching_ceo_guidance(tmp_path: Path) -> None:
    app = LocalWorkspaceApp(local_config(tmp_path), security=WorkspaceSecurity(b"guidance"))
    for state, (attention, heading, explanation) in EXPECTED_PREVIEW_GUIDANCE.items():
        assert PREVIEW_ATTENTION[state] == attention
        if state == "current":
            assert state not in PREVIEW_ROUTE_GUIDANCE
            continue
        assert PREVIEW_ROUTE_GUIDANCE[state] == (heading, explanation)
        response = app._preview_error(
            date(2026, 8, 3),
            409,
            "technical details must not appear",
            preview_state=state,
        )
        page = html.unescape(response.body.decode())
        assert heading in page
        assert explanation in page
        assert "technical details must not appear" not in page
        assert '/campaign/2026-08-03' in page


def setup_preview(root: Path) -> tuple[ContentPackage, Path, FilesystemResourceLoader, Path]:
    derivative_parent = (
        root / "assets" / "clients" / "jordan-and-the-fosters" / "derivatives"
    )
    if derivative_parent.exists():
        shutil.rmtree(derivative_parent)
    package, manifest, preview_root = preview_inputs(root)
    resources = root / "resources"
    (resources / "clients").mkdir(parents=True, exist_ok=True)
    (resources / "client-assets").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        RESOURCES / "clients" / "jordan-and-the-fosters.v1.json",
        resources / "clients" / "jordan-and-the-fosters.v1.json",
    )
    (resources / "client-assets" / "jordan-and-the-fosters.v1.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    package_path = (
        root / "artifacts" / "content-packages"
        / "jordan-and-the-fosters-2026-08-03-content.json"
    )
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return package, package_path, FilesystemResourceLoader(resources), preview_root


def generate_verified(root: Path):
    package, package_path, loader, preview_root = setup_preview(root)
    manifest = ClientAssetManifest.model_validate_json(
        asyncio.run(loader.load_text("client-assets/jordan-and-the-fosters.v1.json"))
    )
    checksum = hashlib.sha256(package_path.read_bytes()).hexdigest()
    status = asyncio.run(
        GenerateVerifiedCampaignPreview(root, loader).execute(
            client_id=CLIENT_ID,
            week=package.week,
            package_path=package_path,
            expected_package_checksum=checksum,
            expected_state="missing",
            expected_asset_binding=asset_binding(manifest, package),
        )
    )
    return package, package_path, loader, preview_root, status


def test_missing_legacy_current_and_package_stale_states(tmp_path: Path) -> None:
    package, package_path, loader, preview_root = setup_preview(tmp_path)
    directory = preview_root / package.package_id
    service = PreviewStatusService(tmp_path, loader)
    missing = asyncio.run(
        service.inspect(
            client_id=CLIENT_ID,
            week=package.week,
            package_path=package_path,
            preview_directory=directory,
        )
    )
    assert missing.state == "missing"
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("legacy", encoding="utf-8")
    legacy = asyncio.run(
        service.inspect(
            client_id=CLIENT_ID,
            week=package.week,
            package_path=package_path,
            preview_directory=directory,
        )
    )
    assert legacy.state == "unverified"
    shutil.rmtree(directory)
    _, _, _, _, current = generate_verified(tmp_path)
    assert current.state == "current"
    package_path.write_bytes(package_path.read_bytes() + b"\n")
    stale = asyncio.run(
        service.inspect(
            client_id=CLIENT_ID,
            week=package.week,
            package_path=package_path,
            preview_directory=directory,
        )
    )
    assert stale.state == "stale"


def test_asset_stale_and_html_asset_sidecar_invalid_states(tmp_path: Path) -> None:
    package, package_path, loader, _, status = generate_verified(tmp_path)
    directory = status.preview_directory
    service = PreviewStatusService(tmp_path, loader)
    manifest = ClientAssetManifest.model_validate_json(
        asyncio.run(loader.load_text("client-assets/jordan-and-the-fosters.v1.json"))
    )
    source = tmp_path / manifest.assets[0].repository_path
    original_source = source.read_bytes()
    source.write_bytes(original_source + b"changed")
    stale = asyncio.run(
        service.inspect(
            client_id=CLIENT_ID, week=package.week, package_path=package_path,
            preview_directory=directory,
        )
    )
    assert stale.state == "stale"
    source.write_bytes(original_source)

    (directory / "index.html").write_text("changed", encoding="utf-8")
    invalid = asyncio.run(
        service.inspect(
            client_id=CLIENT_ID, week=package.week, package_path=package_path,
            preview_directory=directory,
        )
    )
    assert invalid.state == "invalid"

    shutil.rmtree(directory)
    _, _, _, _, status = generate_verified(tmp_path)
    copied = next((status.preview_directory / "assets").iterdir())
    copied.write_bytes(copied.read_bytes() + b"changed")
    assert asyncio.run(
        service.inspect(
            client_id=CLIENT_ID, week=package.week, package_path=package_path,
            preview_directory=status.preview_directory,
        )
    ).state == "invalid"

    (status.preview_directory / SIDECAR_NAME).write_text("{}", encoding="utf-8")
    assert asyncio.run(
        service.inspect(
            client_id=CLIENT_ID, week=package.week, package_path=package_path,
            preview_directory=status.preview_directory,
        )
    ).state == "invalid"


def test_generation_guards_current_draft_and_stale_confirmation(tmp_path: Path) -> None:
    package, package_path, loader, _, status = generate_verified(tmp_path)
    manifest = ClientAssetManifest.model_validate_json(
        asyncio.run(loader.load_text("client-assets/jordan-and-the-fosters.v1.json"))
    )
    service = GenerateVerifiedCampaignPreview(tmp_path, loader)
    checksum = hashlib.sha256(package_path.read_bytes()).hexdigest()
    binding = asset_binding(manifest, package)
    with pytest.raises(PreviewWorkflowError, match="does not need"):
        asyncio.run(
            service.execute(
                client_id=CLIENT_ID, week=package.week, package_path=package_path,
                expected_package_checksum=checksum, expected_state="current",
                expected_asset_binding=binding,
            )
        )
    draft = package.model_copy(update={"approval_state": BriefApprovalState.DRAFT})
    package_path.write_text(draft.model_dump_json(), encoding="utf-8")
    with pytest.raises(PreviewWorkflowError, match="approved"):
        asyncio.run(
            service.execute(
                client_id=CLIENT_ID, week=package.week, package_path=package_path,
                expected_package_checksum=hashlib.sha256(package_path.read_bytes()).hexdigest(),
                expected_state="current", expected_asset_binding=binding,
            )
        )
    package_path.write_text(package.model_dump_json(), encoding="utf-8")
    with pytest.raises(PreviewWorkflowConflictError, match="changed"):
        asyncio.run(
            service.execute(
                client_id=CLIENT_ID, week=package.week, package_path=package_path,
                expected_package_checksum="0" * 64, expected_state=status.state,
                expected_asset_binding=binding,
            )
        )


def test_workspace_serves_only_current_preview_and_recorded_asset(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    _, package_path, loader, _, status = generate_verified(tmp_path)
    del loader
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"preview-serving"))
    home = get(app, config, "/campaign/2026-08-03").body.decode()
    assert "View campaign preview" in home
    # The trailing slash is load-bearing, not cosmetic: the preview page's own relative
    # asset paths ("assets/...") only resolve correctly in a browser when the address bar
    # shows a directory-style URL. Without it, every image in the preview 404s.
    assert 'href="/campaign/2026-08-03/preview/"' in home
    preview = get(app, config, "/campaign/2026-08-03/preview")
    assert preview.status == 200
    assert preview.headers["Content-Type"] == "text/html; charset=utf-8"
    assert b'<link rel="stylesheet" href="/preview.css">' in preview.body
    assert b"<style" not in preview.body
    css = get(app, config, "/preview.css")
    assert css.status == 200 and css.headers["Content-Type"] == "text/css; charset=utf-8"
    name = status.provenance.assets[0].copied_name if status.provenance else ""
    asset = get(app, config, f"/campaign/2026-08-03/preview/assets/{name}")
    assert asset.status == 200 and asset.headers["Content-Type"] == "image/png"
    assert get(app, config, "/campaign/2026-08-03/preview/assets/unknown.png").status == 404
    assert get(app, config, "/campaign/2026-08-03/preview/assets/%2e%2e").status == 404
    before = hashlib.sha256(package_path.read_bytes()).hexdigest()
    get(app, config, "/campaign/2026-08-03/preview/generate/confirm")
    assert hashlib.sha256(package_path.read_bytes()).hexdigest() == before
    assert "script-src 'none'" in preview.headers["Content-Security-Policy"]


def test_workspace_missing_preview_confirmation_and_selected_redirect(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    package, package_path, _, _ = setup_preview(tmp_path)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"preview-generation"))
    response = get(app, config, "/campaign/2026-08-03/preview/generate/confirm")
    assert response.status == 200
    cookie = response.headers["Set-Cookie"].split(";", 1)[0]
    fields = dict(re.findall(
        r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', response.body.decode()
    ))
    post = app.handle(WorkspaceRequest(
        "POST", "/campaign/2026-08-03/preview/generate",
        {"Host": config.host_header, "Origin": config.origin, "Cookie": cookie,
         "Content-Type": "application/x-www-form-urlencoded"},
        urlencode(fields).encode(),
    ))
    assert post.status == 303
    assert post.headers["Location"] == "/campaign/2026-08-03"
    assert ContentPackage.model_validate_json(
        package_path.read_text(encoding="utf-8")
    ).model_dump() == package.model_dump()


@pytest.mark.parametrize("initial_state", ["unverified", "stale"])
def test_explicit_unverified_and_stale_regeneration(
    tmp_path: Path, initial_state: str
) -> None:
    package, package_path, loader, preview_root = setup_preview(tmp_path)
    directory = preview_root / package.package_id
    if initial_state == "unverified":
        directory.mkdir(parents=True)
        (directory / "index.html").write_text("legacy", encoding="utf-8")
    else:
        manifest = ClientAssetManifest.model_validate_json(
            asyncio.run(loader.load_text("client-assets/jordan-and-the-fosters.v1.json"))
        )
        asyncio.run(
            GenerateVerifiedCampaignPreview(tmp_path, loader).execute(
                client_id=CLIENT_ID,
                week=package.week,
                package_path=package_path,
                expected_package_checksum=hashlib.sha256(package_path.read_bytes()).hexdigest(),
                expected_state="missing",
                expected_asset_binding=asset_binding(manifest, package),
            )
        )
        package_path.write_bytes(package_path.read_bytes() + b"\n")
    status_service = PreviewStatusService(tmp_path, loader)
    status = asyncio.run(
        status_service.inspect(
            client_id=CLIENT_ID,
            week=package.week,
            package_path=package_path,
            preview_directory=directory,
        )
    )
    assert status.state == initial_state
    current_package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    manifest = ClientAssetManifest.model_validate_json(
        asyncio.run(loader.load_text("client-assets/jordan-and-the-fosters.v1.json"))
    )
    regenerated = asyncio.run(
        GenerateVerifiedCampaignPreview(tmp_path, loader).execute(
            client_id=CLIENT_ID,
            week=package.week,
            package_path=package_path,
            expected_package_checksum=hashlib.sha256(package_path.read_bytes()).hexdigest(),
            expected_state=initial_state,
            expected_asset_binding=asset_binding(manifest, current_package),
        )
    )
    assert regenerated.state == "current"


def test_invalid_asset_generation_leaves_legacy_preview_unchanged(tmp_path: Path) -> None:
    package, package_path, loader, preview_root = setup_preview(tmp_path)
    directory = preview_root / package.package_id
    directory.mkdir(parents=True)
    legacy = directory / "index.html"
    legacy.write_text("legacy preview", encoding="utf-8")
    manifest_path = tmp_path / "resources" / "client-assets" / "jordan-and-the-fosters.v1.json"
    manifest = ClientAssetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    asset = manifest.assets[0]
    changed = asset.model_copy(
        update={"approved_uses": ("content_package_asset_recommendation",)}
    )
    changed_manifest = manifest.model_copy(update={"assets": (changed,)})
    manifest_path.write_text(changed_manifest.model_dump_json(), encoding="utf-8")
    before = legacy.read_bytes()
    with pytest.raises(CampaignPreviewError, match="does not permit"):
        asyncio.run(
            GenerateVerifiedCampaignPreview(tmp_path, loader).execute(
                client_id=CLIENT_ID,
                week=package.week,
                package_path=package_path,
                expected_package_checksum=hashlib.sha256(package_path.read_bytes()).hexdigest(),
                expected_state="unverified",
                expected_asset_binding=asset_binding(changed_manifest, package),
            )
        )
    assert legacy.read_bytes() == before
    assert not (directory / SIDECAR_NAME).exists()
