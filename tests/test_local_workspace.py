import hashlib
import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pytest

from agentic_workshop.adapters import local_workspace
from agentic_workshop.adapters.local_workspace import (
    SESSION_COOKIE,
    WORKSPACE_HOST,
    LocalWorkspaceApp,
    WorkspaceConfig,
    WorkspaceRequest,
    WorkspaceSecurity,
)
from agentic_workshop.application.brief_review import (
    BriefArtifactConflictError,
    BriefArtifactIdentityError,
    BriefArtifactInvalidError,
    BriefArtifactMissingError,
    ReviewWeeklyMarketingBrief,
)
from agentic_workshop.application.content_review import ReviewContentPackage
from agentic_workshop.cli import build_parser, run
from agentic_workshop.domain.assets import (
    AssetRecommendation,
    AssetType,
    ClientAssetManifest,
)
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief

REPOSITORY_ROOT = Path(__file__).parents[1]
RESOURCES = REPOSITORY_ROOT / "src" / "agentic_workshop" / "resources"
SOURCE_BRIEF = (
    REPOSITORY_ROOT
    / "artifacts"
    / "weekly-briefs"
    / "jordan-and-the-fosters-2026-08-03.json"
)
SOURCE_PACKAGE = (
    REPOSITORY_ROOT / "artifacts" / "content-packages"
    / "jordan-and-the-fosters-2026-08-03-content.json"
)
CLIENT_ID = ClientId("jordan-and-the-fosters")


def local_config(root: Path, *, port: int = 8765) -> WorkspaceConfig:
    resources = root / "resources"
    (resources / "clients").mkdir(parents=True)
    (resources / "client-assets").mkdir()
    (resources / "static").mkdir()
    shutil.copyfile(
        RESOURCES / "clients" / "jordan-and-the-fosters.v1.json",
        resources / "clients" / "jordan-and-the-fosters.v1.json",
    )
    manifest_path = resources / "client-assets" / "jordan-and-the-fosters.v1.json"
    shutil.copyfile(
        RESOURCES / "client-assets" / "jordan-and-the-fosters.v1.json",
        manifest_path,
    )
    shutil.copyfile(
        RESOURCES / "static" / "workspace.css",
        resources / "static" / "workspace.css",
    )
    manifest = ClientAssetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    derivative = manifest.assets[1]
    target_asset = root / derivative.repository_path
    target_asset.parent.mkdir(parents=True)
    shutil.copyfile(REPOSITORY_ROOT / derivative.repository_path, target_asset)
    brief = (
        root / "artifacts" / "weekly-briefs"
        / "jordan-and-the-fosters-2026-08-03.json"
    )
    brief.parent.mkdir(parents=True)
    source_brief = WeeklyMarketingBrief.model_validate_json(
        SOURCE_BRIEF.read_text(encoding="utf-8")
    )
    brief.write_text(
        source_brief.model_copy(
            update={
                "approval_state": BriefApprovalState.DRAFT,
                "revision_note": None,
            }
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return WorkspaceConfig(
        repository_root=root,
        resource_root=resources,
        client_id=CLIENT_ID,
        port=port,
    )


def campaign_brief(config: WorkspaceConfig) -> Path:
    return (
        config.repository_root / "artifacts" / "weekly-briefs"
        / "jordan-and-the-fosters-2026-08-03.json"
    )


def add_campaign(config: WorkspaceConfig, week: str = "2026-08-10") -> tuple[Path, Path]:
    brief = WeeklyMarketingBrief.model_validate_json(SOURCE_BRIEF.read_text(encoding="utf-8"))
    brief_path = (
        config.repository_root / "artifacts" / "weekly-briefs"
        / f"jordan-and-the-fosters-{week}.json"
    )
    brief_path.write_text(
        brief.model_copy(
            update={
                "week": date.fromisoformat(week),
                "approval_state": BriefApprovalState.DRAFT,
                "revision_note": None,
            }
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    package = ContentPackage.model_validate_json(SOURCE_PACKAGE.read_text(encoding="utf-8"))
    package_path = (
        config.repository_root / "artifacts" / "content-packages"
        / f"jordan-and-the-fosters-{week}-content.json"
    )
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text(
        package.model_copy(update={"week": date.fromisoformat(week)}).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return brief_path, package_path


def set_package_state(path: Path, state: BriefApprovalState) -> ContentPackage:
    package = ContentPackage.model_validate_json(path.read_text(encoding="utf-8"))
    updated = package.model_copy(update={"approval_state": state, "revision_note": None})
    path.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return updated


def get(app: LocalWorkspaceApp, config: WorkspaceConfig, target: str, cookie: str = ""):
    headers = {"Host": config.host_header}
    if cookie:
        headers["Cookie"] = cookie
    return app.handle(WorkspaceRequest("GET", target, headers))


def confirmation(
    app: LocalWorkspaceApp,
    config: WorkspaceConfig,
    action: str,
) -> tuple[str, dict[str, str]]:
    response = get(app, config, f"/brief/{action}/confirm")
    assert response.status == 200
    cookie = response.headers["Set-Cookie"].split(";", maxsplit=1)[0]
    page = response.body.decode()
    fields = dict(
        re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', page)
    )
    return cookie, fields


def post(
    app: LocalWorkspaceApp,
    config: WorkspaceConfig,
    action: str,
    cookie: str,
    fields: dict[str, str],
):
    return app.handle(
        WorkspaceRequest(
            "POST",
            f"/brief/{action}",
            {
                "Host": config.host_header,
                "Origin": config.origin,
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )


def test_review_service_and_cli_preserve_approval_and_revision_behavior(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    service = ReviewWeeklyMarketingBrief()
    brief_path = campaign_brief(config)
    loaded = service.load(brief_path)
    approved = service.approve(brief_path, expected_checksum=loaded.checksum)
    assert approved.brief.approval_state is BriefApprovalState.APPROVED
    assert approved.brief.revision_note is None
    assert run(
        ["review", str(brief_path), "--request-revision", "Clarify <audience>."]
    ) == 0
    revised = service.load(brief_path).brief
    assert revised.approval_state is BriefApprovalState.REVISION_REQUESTED
    assert revised.revision_note == "Clarify <audience>."
    assert brief_path.with_suffix(".md").is_file()


def test_review_service_rejects_missing_invalid_identity_and_stale_artifacts(
    tmp_path: Path,
) -> None:
    service = ReviewWeeklyMarketingBrief()
    missing = tmp_path / "missing.json"
    with pytest.raises(BriefArtifactMissingError):
        service.load(missing)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    with pytest.raises(BriefArtifactInvalidError):
        service.load(invalid)
    config = local_config(tmp_path / "workspace")
    brief_path = campaign_brief(config)
    loaded = service.load(brief_path)
    with pytest.raises(BriefArtifactIdentityError):
        service.approve(
            brief_path,
            expected_client_id=ClientId("another-client"),
        )
    brief_path.write_bytes(brief_path.read_bytes() + b"\n")
    with pytest.raises(BriefArtifactConflictError):
        service.approve(brief_path, expected_checksum=loaded.checksum)


def test_home_brief_escaping_headers_and_get_requests_are_read_only(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    brief_path = campaign_brief(config)
    brief = WeeklyMarketingBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    data = brief.model_dump(mode="json")
    data["objective"] = "Introduce <script>alert(1)</script> safely"
    brief_path.write_text(
        WeeklyMarketingBrief.model_validate(data).model_dump_json(indent=2),
        encoding="utf-8",
    )
    before = hashlib.sha256(brief_path.read_bytes()).hexdigest()
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"test-secret"))
    home = get(app, config, "/")
    brief_page = get(app, config, "/brief")
    confirm = get(app, config, "/brief/approve/confirm")
    after = hashlib.sha256(brief_path.read_bytes()).hexdigest()
    assert home.status == brief_page.status == confirm.status == 200
    assert before == after
    rendered = brief_page.body.decode()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>" not in rendered
    for response in (home, brief_page, confirm):
        policy = response.headers["Content-Security-Policy"]
        assert policy.startswith("default-src 'self'")
        assert "style-src 'self'" in policy
        assert "script-src 'none'" in policy
        assert "unsafe-inline" not in policy
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"


def test_fixed_stylesheet_route_and_pages_have_no_inline_styles(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"test-secret"))
    stylesheet = get(app, config, "/workspace.css")
    assert stylesheet.status == 200
    assert stylesheet.headers["Content-Type"] == "text/css; charset=utf-8"
    assert b".actions" in stylesheet.body
    assert get(app, config, "/workspace.css?path=anything").status == 400
    pages = (
        get(app, config, "/"),
        get(app, config, "/brief"),
        get(app, config, "/brief/approve/confirm"),
        get(app, config, "/brief/revision/confirm"),
        get(app, config, "/missing"),
    )
    for response in pages:
        rendered = response.body.decode()
        assert '<link rel="stylesheet" href="/workspace.css">' in rendered
        assert "<style" not in rendered
        assert re.search(r"\sstyle=", rendered) is None


def test_brief_actions_follow_state_and_direct_invalid_actions_are_rejected(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    security = WorkspaceSecurity(b"test-secret")
    app = LocalWorkspaceApp(config, security=security)
    draft_page = get(app, config, "/brief").body.decode()
    assert "Approve Sarah's brief" in draft_page
    assert "Request a revision" in draft_page

    service = ReviewWeeklyMarketingBrief()
    brief_path = campaign_brief(config)
    service.approve(brief_path)
    approved_page = get(app, config, "/brief").body.decode()
    assert "Approve Sarah's brief" not in approved_page
    assert "Request a revision" in approved_page
    assert get(app, config, "/brief/approve/confirm").status == 422

    loaded = service.load(brief_path)
    session = security.new_session()
    nonce = security.confirmation_nonce(
        action="approve",
        client_id=loaded.brief.client_id,
        week=loaded.brief.week,
        checksum=loaded.checksum,
    )
    fields = {
        "csrf_token": security.csrf_token(session),
        "confirmation_nonce": nonce,
        "artifact_checksum": loaded.checksum,
        "client_id": str(loaded.brief.client_id),
        "week": loaded.brief.week.isoformat(),
    }
    invalid_post = post(
        app,
        config,
        "approve",
        f"{SESSION_COOKIE}={session}",
        fields,
    )
    assert invalid_post.status == 422
    assert "Approval is not valid" in invalid_post.body.decode()

    service.request_revision(brief_path, "Regenerate the campaign strategy.")
    revision_page = get(app, config, "/brief").body.decode()
    assert "Approve Sarah's brief" not in revision_page
    assert "Request a revision" not in revision_page
    assert "until Sarah regenerates a draft" in revision_page
    assert get(app, config, "/brief/approve/confirm").status == 422
    assert get(app, config, "/brief/revision/confirm").status == 422


def test_approve_and_revision_posts_use_confirmation_and_redirect(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"test-secret"))
    cookie, fields = confirmation(app, config, "approve")
    approved = post(app, config, "approve", cookie, fields)
    assert approved.status == 303
    assert approved.headers["Location"] == "/campaign/2026-08-03?result=brief-approved"
    brief_path = campaign_brief(config)
    assert ReviewWeeklyMarketingBrief().load(
        brief_path
    ).brief.approval_state is BriefApprovalState.APPROVED

    cookie, fields = confirmation(app, config, "revision")
    fields["revision_note"] = "Use <strong>warmer</strong> language."
    revised = post(app, config, "revision", cookie, fields)
    assert revised.status == 303
    stored = ReviewWeeklyMarketingBrief().load(brief_path).brief
    assert stored.approval_state is BriefApprovalState.REVISION_REQUESTED
    page = get(app, config, "/brief", cookie).body.decode()
    assert "Use &lt;strong&gt;warmer&lt;/strong&gt; language." in page
    assert "<strong>warmer</strong>" not in page


def test_post_rejects_stale_blank_invalid_and_reused_confirmations(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"test-secret"))
    cookie, fields = confirmation(app, config, "approve")
    missing_csrf = {**fields}
    missing_csrf.pop("csrf_token")
    assert post(app, config, "approve", cookie, missing_csrf).status == 403
    invalid_csrf = {**fields, "csrf_token": "invalid"}
    assert post(app, config, "approve", cookie, invalid_csrf).status == 403
    assert post(app, config, "approve", "", fields).status == 403
    missing_nonce = {**fields}
    missing_nonce.pop("confirmation_nonce")
    assert post(app, config, "approve", cookie, missing_nonce).status == 403
    invalid_nonce = {**fields, "confirmation_nonce": "invalid"}
    assert post(app, config, "approve", cookie, invalid_nonce).status == 403

    first = post(app, config, "approve", cookie, fields)
    assert first.status == 303
    assert post(app, config, "approve", cookie, fields).status == 403

    cookie, fields = confirmation(app, config, "revision")
    fields["revision_note"] = "   "
    assert post(app, config, "revision", cookie, fields).status == 422

    cookie, fields = confirmation(app, config, "revision")
    fields["revision_note"] = "Revise the campaign objective."
    brief_path = campaign_brief(config)
    brief_path.write_bytes(brief_path.read_bytes() + b"\n")
    conflict = post(app, config, "revision", cookie, fields)
    assert conflict.status == 409
    assert "changed after confirmation" in conflict.body.decode()


def test_wrong_host_origin_identity_and_unknown_paths_are_rejected(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"test-secret"))
    assert app.handle(WorkspaceRequest("GET", "/", {"Host": "localhost:8765"})).status == 400
    cookie, fields = confirmation(app, config, "approve")
    wrong_origin = app.handle(
        WorkspaceRequest(
            "POST",
            "/brief/approve",
            {
                "Host": config.host_header,
                "Origin": "http://evil.example",
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )
    assert wrong_origin.status == 403
    cookie, fields = confirmation(app, config, "approve")
    fields["client_id"] = "another-client"
    assert post(app, config, "approve", cookie, fields).status == 403
    assert get(app, config, "/model/run").status == 404
    assert get(app, config, "https://example.com/").status == 400


def test_missing_and_wrong_campaign_brief_have_plain_language_pages(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_campaign(config, "2026-08-03")
    app = LocalWorkspaceApp(config)
    campaign_brief(config).unlink()
    missing = get(app, config, "/campaign/2026-08-03/brief")
    assert missing.status == 404
    assert "weekly brief is missing" in missing.body.decode()

    config = local_config(tmp_path / "invalid")
    campaign_brief(config).write_text("not json", encoding="utf-8")
    invalid = get(LocalWorkspaceApp(config), config, "/brief")
    assert invalid.status == 422
    assert "Invalid campaign artifact" in invalid.body.decode()

    config = local_config(tmp_path / "wrong")
    brief_path = campaign_brief(config)
    brief = WeeklyMarketingBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    brief_path.write_text(
        brief.model_copy(update={"client_id": ClientId("another-client")}).model_dump_json(),
        encoding="utf-8",
    )
    mismatch = get(LocalWorkspaceApp(config), config, "/brief")
    assert mismatch.status == 422
    assert "client does not match" in mismatch.body.decode()


def test_workspace_binding_is_fixed_and_cli_has_no_bind_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = local_config(tmp_path, port=9876)
    observed: list[tuple[str, int]] = []

    class FakeServer:
        def __init__(self, address: tuple[str, int], handler: object) -> None:
            del handler
            observed.append(address)

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    monkeypatch.setattr(local_workspace, "ThreadingHTTPServer", FakeServer)
    local_workspace.serve_workspace(config)
    assert WORKSPACE_HOST == "127.0.0.1"
    assert observed == [("127.0.0.1", 9876)]
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["workspace", "--host", "0.0.0.0"])
    assert SESSION_COOKIE in confirmation(LocalWorkspaceApp(config), config, "approve")[0]


def test_campaign_history_lists_two_weeks_and_defaults_to_latest(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_campaign(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"history-secret"))

    home = get(app, config, "/")
    rendered = home.body.decode()
    assert home.status == 200
    assert "Viewing campaign 2026-08-10" in rendered
    assert rendered.count("2026-08-03") >= 1
    assert rendered.count("2026-08-10") >= 2
    assert "Campaigns" in rendered

    august_3 = get(app, config, "/campaign/2026-08-03").body.decode()
    august_10 = get(app, config, "/campaign/2026-08-10").body.decode()
    assert "Viewing campaign 2026-08-03" in august_3
    assert "Viewing campaign 2026-08-10" in august_10
    assert '/campaign/2026-08-03/brief' in august_3
    assert '/campaign/2026-08-10/brief' in august_10


def test_campaign_history_reports_missing_package_and_preview(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    app = LocalWorkspaceApp(config)
    rendered = get(app, config, "/campaign/2026-08-03").body.decode()
    assert "content package is waiting for Sarah" in rendered
    assert "Campaign preview: not available" in rendered
    assert ">missing</td>" in rendered


def test_campaign_history_rejects_ambiguous_invalid_unknown_and_mismatch(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    duplicate = campaign_brief(config).with_name("jordan-and-the-fosters-copy.json")
    shutil.copyfile(campaign_brief(config), duplicate)
    assert get(LocalWorkspaceApp(config), config, "/").status == 409

    duplicate.unlink()
    app = LocalWorkspaceApp(config)
    assert get(app, config, "/campaign/2026-8-3").status == 400
    assert get(app, config, "/campaign/2026-08-17").status == 404

    brief_path = campaign_brief(config)
    brief = WeeklyMarketingBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    brief_path.write_text(
        brief.model_copy(update={"client_id": ClientId("wrong-client")}).model_dump_json(),
        encoding="utf-8",
    )
    assert get(LocalWorkspaceApp(config), config, "/").status == 422


def test_confirmation_is_bound_to_selected_week_and_redirects_back(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_campaign(config)
    security = WorkspaceSecurity(b"week-secret")
    app = LocalWorkspaceApp(config, security=security)
    response = get(app, config, "/campaign/2026-08-10/brief/approve/confirm")
    assert response.status == 200
    cookie = response.headers["Set-Cookie"].split(";", maxsplit=1)[0]
    fields = dict(
        re.findall(
            r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"',
            response.body.decode(),
        )
    )
    fields["week"] = "2026-08-03"
    rejected = app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/2026-08-10/brief/approve",
            {
                "Host": config.host_header,
                "Origin": config.origin,
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )
    assert rejected.status == 403

    response = get(app, config, "/campaign/2026-08-10/brief/approve/confirm")
    cookie = response.headers["Set-Cookie"].split(";", maxsplit=1)[0]
    fields = dict(
        re.findall(
            r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"',
            response.body.decode(),
        )
    )
    approved = app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/2026-08-10/brief/approve",
            {
                "Host": config.host_header,
                "Origin": config.origin,
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )
    assert approved.status == 303
    assert approved.headers["Location"].startswith("/campaign/2026-08-10?")


def test_complete_casey_package_presentation_and_state_aware_actions(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    _, package_path = add_campaign(config, "2026-08-03")
    package = set_package_state(package_path, BriefApprovalState.DRAFT)
    recommendation = AssetRecommendation(
        asset_id="marketing-cover",
        asset_type=AssetType.FRONT_COVER,
        repository_path="hidden-from-workspace.png",
        manifest_source="client-assets/example.json",
        availability="available",
        diagnostic="validated",
        approved_use="content_package_asset_recommendation",
        permitted_uses=("official_website",),
    )
    first = package.drafts[0].model_copy(
        update={
            "body": "Safe <script>alert(1)</script> public copy.",
            "asset_recommendations": (recommendation,),
        }
    )
    package_path.write_text(
        package.model_copy(update={"drafts": (first, *package.drafts[1:])}).model_dump_json(
            indent=2
        ),
        encoding="utf-8",
    )
    before_get = hashlib.sha256(package_path.read_bytes()).hexdigest()
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"package-page"))
    page = get(app, config, "/campaign/2026-08-03/package")
    assert hashlib.sha256(package_path.read_bytes()).hexdigest() == before_get
    rendered = page.body.decode()
    assert page.status == 200
    assert "Safe &lt;script&gt;alert(1)&lt;/script&gt; public copy." in rendered
    assert "<script>" not in rendered
    assert "Source provenance" in rendered
    assert "Approved fact identifiers" in rendered
    assert "marketing-cover" in rendered
    assert "official_website" in rendered
    assert "hidden-from-workspace.png" not in rendered
    assert "Approve Casey's package" in rendered
    assert "Request a revision" in rendered

    ReviewContentPackage().approve(package_path)
    approved = get(app, config, "/campaign/2026-08-03/package").body.decode()
    assert "Approve Casey's package" not in approved
    assert "Request a revision" in approved
    assert '<p class="status">approved</p>' in approved
    assert "Generation-time assumptions" in approved
    assert "These assumptions were recorded when Casey generated the package." in approved
    assert "The current workflow state is shown above." in approved
    assert "Package assumptions" not in approved
    assert "This package is a draft and will not be published automatically." in approved
    ReviewContentPackage().request_revision(package_path, "Use warmer language.")
    revision = get(app, config, "/campaign/2026-08-03/package").body.decode()
    assert "Approve Casey's package" not in revision
    assert "Request a revision" not in revision
    assert "Use warmer language." in revision


def package_confirmation(
    app: LocalWorkspaceApp,
    config: WorkspaceConfig,
    week: str,
    action: str,
) -> tuple[str, dict[str, str]]:
    response = get(app, config, f"/campaign/{week}/package/{action}/confirm")
    assert response.status == 200
    cookie = response.headers["Set-Cookie"].split(";", maxsplit=1)[0]
    fields = dict(
        re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', response.body.decode())
    )
    return cookie, fields


def package_post(
    app: LocalWorkspaceApp,
    config: WorkspaceConfig,
    week: str,
    action: str,
    cookie: str,
    fields: dict[str, str],
):
    return app.handle(
        WorkspaceRequest(
            "POST",
            f"/campaign/{week}/package/{action}",
            {
                "Host": config.host_header,
                "Origin": config.origin,
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )


def test_casey_approval_revision_security_and_selected_redirect(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    _, package_path = add_campaign(config)
    set_package_state(package_path, BriefApprovalState.DRAFT)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"package-actions"))
    cookie, fields = package_confirmation(app, config, "2026-08-10", "approve")
    before = hashlib.sha256(package_path.read_bytes()).hexdigest()
    wrong_csrf = package_post(
        app, config, "2026-08-10", "approve", cookie, {**fields, "csrf_token": "wrong"}
    )
    assert wrong_csrf.status == 403
    assert hashlib.sha256(package_path.read_bytes()).hexdigest() == before
    wrong_nonce = package_post(
        app,
        config,
        "2026-08-10",
        "approve",
        cookie,
        {**fields, "confirmation_nonce": "wrong"},
    )
    assert wrong_nonce.status == 403
    wrong_origin = app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/2026-08-10/package/approve",
            {
                "Host": config.host_header,
                "Origin": "http://evil.example",
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )
    assert wrong_origin.status == 403
    approved = package_post(app, config, "2026-08-10", "approve", cookie, fields)
    assert approved.status == 303
    assert approved.headers["Location"] == "/campaign/2026-08-10?result=package-approved"
    assert package_post(app, config, "2026-08-10", "approve", cookie, fields).status == 403
    assert ReviewContentPackage().load(
        package_path
    ).package.approval_state is BriefApprovalState.APPROVED

    cookie, fields = package_confirmation(app, config, "2026-08-10", "revision")
    assert package_post(app, config, "2026-08-10", "revision", cookie, fields).status == 422
    cookie, fields = package_confirmation(app, config, "2026-08-10", "revision")
    fields["revision_note"] = "Revise <tone>."
    revised = package_post(app, config, "2026-08-10", "revision", cookie, fields)
    assert revised.status == 303
    stored = ReviewContentPackage().load(package_path).package
    assert stored.revision_note == "Revise <tone>."
    rendered = get(app, config, "/campaign/2026-08-10/package").body.decode()
    assert "Revise &lt;tone&gt;." in rendered


def test_casey_rejects_stale_invalid_direct_and_missing_packages(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    _, package_path = add_campaign(config, "2026-08-03")
    set_package_state(package_path, BriefApprovalState.DRAFT)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"package-invalid"))
    cookie, fields = package_confirmation(app, config, "2026-08-03", "approve")
    package_path.write_bytes(package_path.read_bytes() + b"\n")
    assert package_post(app, config, "2026-08-03", "approve", cookie, fields).status == 409

    set_package_state(package_path, BriefApprovalState.APPROVED)
    assert get(app, config, "/campaign/2026-08-03/package/approve/confirm").status == 422

    package_path.unlink()
    assert get(app, config, "/campaign/2026-08-03/package").status == 404

    _, package_path = add_campaign(config, "2026-08-03")
    package_path.write_text("not json", encoding="utf-8")
    assert get(LocalWorkspaceApp(config), config, "/campaign/2026-08-03/package").status == 422


def test_casey_rejects_client_and_week_mismatched_packages(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    _, package_path = add_campaign(config, "2026-08-03")
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    package_path.write_text(
        package.model_copy(update={"client_id": ClientId("wrong-client")}).model_dump_json(),
        encoding="utf-8",
    )
    assert get(LocalWorkspaceApp(config), config, "/campaign/2026-08-03/package").status == 422

    _, package_path = add_campaign(config, "2026-08-03")
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    package_path.write_text(
        package.model_copy(update={"week": date(2026, 8, 10)}).model_dump_json(),
        encoding="utf-8",
    )
    response = get(LocalWorkspaceApp(config), config, "/campaign/2026-08-03/package")
    assert response.status == 422
    assert "week does not match its filename" in response.body.decode()
