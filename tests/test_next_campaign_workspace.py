import asyncio
import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.adapters.local_workspace import (
    LocalWorkspaceApp,
    WorkspaceConfig,
    WorkspaceRequest,
    WorkspaceSecurity,
)
from agentic_workshop.application.next_campaign import (
    DuplicateCampaignWeekError,
    StartNextCampaign,
)
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief
from tests.test_local_workspace import CLIENT_ID, RESOURCES, get, local_config


def add_sarah_resources(config: WorkspaceConfig) -> None:
    """Sarah's brief generation needs her employee/prompt/SOP resources; the shared
    ``local_config`` fixture in test_local_workspace.py only wires up client/asset resources
    for Casey-generation tests, so this fills the additional gap for brief generation."""
    (config.resource_root / "employees").mkdir(exist_ok=True)
    (config.resource_root / "sops").mkdir(exist_ok=True)
    (config.resource_root / "prompts").mkdir(exist_ok=True)
    shutil.copyfile(
        RESOURCES / "employees" / "sarah-collins.v1.json",
        config.resource_root / "employees" / "sarah-collins.v1.json",
    )
    shutil.copyfile(
        RESOURCES / "sops" / "weekly-marketing-brief.v1.md",
        config.resource_root / "sops" / "weekly-marketing-brief.v1.md",
    )
    shutil.copyfile(
        RESOURCES / "prompts" / "sarah-weekly-marketing.v1.md",
        config.resource_root / "prompts" / "sarah-weekly-marketing.v1.md",
    )


def new_campaign_form(app: LocalWorkspaceApp, config: WorkspaceConfig):
    response = get(app, config, "/campaign/new")
    assert response.status == 200
    cookie = response.headers["Set-Cookie"].split(";", maxsplit=1)[0]
    fields = dict(
        re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', response.body.decode())
    )
    return response, cookie, fields


def post_new_campaign(
    app: LocalWorkspaceApp,
    config: WorkspaceConfig,
    cookie: str,
    fields: dict[str, str],
):
    return app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/new",
            {
                "Host": config.host_header,
                "Origin": config.origin,
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )


def brief_path(config: WorkspaceConfig, week: str) -> Path:
    return (
        config.repository_root / "artifacts" / "weekly-briefs"
        / f"jordan-and-the-fosters-{week}.json"
    )


def test_service_creates_new_brief_and_normalizes_to_monday(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_sarah_resources(config)
    service = StartNextCampaign(
        config.repository_root, FilesystemResourceLoader(config.resource_root)
    )
    started = asyncio.run(
        service.execute(client_id=CLIENT_ID, requested_week=date(2026, 8, 19))
    )
    assert started.brief.week == date(2026, 8, 17)
    assert started.brief.approval_state is BriefApprovalState.DRAFT
    assert started.brief.client_id == CLIENT_ID
    assert started.json_path.is_file()
    assert started.markdown_path.is_file()
    assert not tuple(started.json_path.parent.glob("*.tmp"))


def test_service_rejects_duplicate_week(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_sarah_resources(config)
    service = StartNextCampaign(
        config.repository_root, FilesystemResourceLoader(config.resource_root)
    )
    try:
        asyncio.run(
            service.execute(client_id=CLIENT_ID, requested_week=date(2026, 8, 3))
        )
    except DuplicateCampaignWeekError as error:
        assert error.week == date(2026, 8, 3)
    else:
        raise AssertionError("Expected DuplicateCampaignWeekError")


def test_workspace_start_next_campaign_creates_draft_and_redirects(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_sarah_resources(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"new-campaign-secret"))

    _, cookie, fields = new_campaign_form(app, config)
    fields["week"] = "2026-08-19"
    response = post_new_campaign(app, config, cookie, fields)

    assert response.status == 303
    assert response.headers["Location"] == "/campaign/2026-08-17?result=campaign-started"

    created = brief_path(config, "2026-08-17")
    assert created.is_file()
    saved = WeeklyMarketingBrief.model_validate_json(created.read_text(encoding="utf-8"))
    assert saved.approval_state is BriefApprovalState.DRAFT
    assert saved.week == date(2026, 8, 17)

    landing = get(app, config, "/campaign/2026-08-17?result=campaign-started").body.decode()
    assert "Sarah drafted a new weekly brief" in landing
    assert "Viewing campaign 2026-08-17" in landing


def test_workspace_start_next_campaign_rejects_duplicate(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_sarah_resources(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"duplicate-secret"))

    _, cookie, fields = new_campaign_form(app, config)
    fields["week"] = "2026-08-03"
    response = post_new_campaign(app, config, cookie, fields)

    assert response.status == 409
    body = response.body.decode()
    assert "already exists" in body
    assert 'href="/campaign/2026-08-03"' in body


def test_workspace_start_next_campaign_rejects_invalid_date(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_sarah_resources(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"invalid-date-secret"))

    _, cookie, fields = new_campaign_form(app, config)
    fields["week"] = "not-a-date"
    response = post_new_campaign(app, config, cookie, fields)

    assert response.status == 400


def test_workspace_start_next_campaign_security(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    add_sarah_resources(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"security-secret"))

    _, cookie, fields = new_campaign_form(app, config)
    fields["week"] = "2026-08-24"

    wrong_origin = app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/new",
            {
                "Host": config.host_header,
                "Origin": "http://127.0.0.1:9999",
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )
    assert wrong_origin.status == 403

    # A sandboxed cross-origin page forging this request would also send Origin: null --
    # the same value real Chrome sends on a same-origin POST under a stale "no-referrer"
    # policy. Fixing the header (see local_workspace.py's _headers) must not come at the
    # cost of still rejecting a genuine forgery attempt presenting this exact value.
    null_origin = app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/new",
            {
                "Host": config.host_header,
                "Origin": "null",
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )
    assert null_origin.status == 403

    tampered = dict(fields)
    tampered["csrf_token"] = "not-the-real-token"
    bad_csrf = post_new_campaign(app, config, cookie, tampered)
    assert bad_csrf.status == 403

    first = post_new_campaign(app, config, cookie, fields)
    assert first.status == 303
    reused_nonce = post_new_campaign(app, config, cookie, fields)
    assert reused_nonce.status == 403


def test_workspace_start_next_campaign_get_is_read_only_and_tolerates_empty_history(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    add_sarah_resources(config)
    brief_path(config, "2026-08-03").unlink()
    app = LocalWorkspaceApp(config)

    before = set(config.repository_root.glob("artifacts/weekly-briefs/*"))
    response, _, fields = new_campaign_form(app, config)
    after = set(config.repository_root.glob("artifacts/weekly-briefs/*"))

    assert response.status == 200
    assert before == after
    assert "None yet." in response.body.decode()
    assert fields["client_id"] == str(CLIENT_ID)


def test_workspace_home_links_to_start_next_campaign(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    app = LocalWorkspaceApp(config)
    home = get(app, config, "/").body.decode()
    assert 'href="/campaign/new"' in home
    assert "Start next campaign" in home
