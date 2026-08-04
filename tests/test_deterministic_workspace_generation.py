import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urlencode

import pytest
from tests.test_local_workspace import (
    CLIENT_ID,
    SOURCE_PACKAGE,
    add_campaign,
    campaign_brief,
    get,
    local_config,
    set_package_state,
)

from agentic_workshop.adapters.deterministic_content import DeterministicContentDraftGenerator
from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.adapters.local_workspace import (
    LocalWorkspaceApp,
    WorkspaceRequest,
    WorkspaceSecurity,
)
from agentic_workshop.application.brief_review import ReviewWeeklyMarketingBrief
from agentic_workshop.application.content_review import ReviewContentPackage
from agentic_workshop.application.deterministic_content import (
    DeterministicContentConflictError,
    DeterministicContentPrerequisiteError,
    GenerateDeterministicContentPackage,
)
from agentic_workshop.domain.content import ContentPackage, DraftGenerationResult
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief


def package_path(root: Path, week: str = "2026-08-03") -> Path:
    return root / "artifacts" / "content-packages" / f"jordan-and-the-fosters-{week}-content.json"


def write_package(root: Path, *, state: BriefApprovalState) -> Path:
    source = ContentPackage.model_validate_json(SOURCE_PACKAGE.read_text(encoding="utf-8"))
    target = package_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        source.model_copy(
            update={"approval_state": state, "revision_note": None}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return target


def service_for(config, *, factory=None) -> GenerateDeterministicContentPackage:
    return GenerateDeterministicContentPackage(
        config.repository_root,
        FilesystemResourceLoader(config.resource_root),
        generator_factory=factory,
    )


def approve_brief(config) -> tuple[Path, str]:
    path = campaign_brief(config)
    reviewed = ReviewWeeklyMarketingBrief().approve(path)
    return path, reviewed.checksum


def test_service_creates_atomic_draft_and_regenerates_with_revision_note(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    brief_path, brief_checksum = approve_brief(config)
    target = package_path(tmp_path)
    created = asyncio.run(
        service_for(config).execute(
            brief_path=brief_path,
            package_path=target,
            expected_client_id=CLIENT_ID,
            expected_week=WeeklyMarketingBrief.model_validate_json(
                brief_path.read_text(encoding="utf-8")
            ).week,
            expected_brief_checksum=brief_checksum,
            expect_package_absent=True,
        )
    )
    assert created.package.approval_state is BriefApprovalState.DRAFT
    assert target.is_file() and target.with_suffix(".md").is_file()
    assert not tuple(target.parent.glob("*.tmp"))

    revised = ReviewContentPackage().request_revision(target, "Warm the social opening.")
    observed: list[str | None] = []

    def factory(note: str | None) -> DeterministicContentDraftGenerator:
        observed.append(note)
        return DeterministicContentDraftGenerator(revision_instructions=note)

    regenerated = asyncio.run(
        service_for(config, factory=factory).execute(
            brief_path=brief_path,
            package_path=target,
            expected_client_id=CLIENT_ID,
            expected_week=revised.package.week,
            expected_brief_checksum=brief_checksum,
            expected_package_checksum=revised.checksum,
            expected_package_identity=revised.package.package_id,
        )
    )
    assert observed == ["Warm the social opening."]
    assert regenerated.package.approval_state is BriefApprovalState.DRAFT
    assert regenerated.package.revision_note is None


def test_service_blocks_states_conflicts_and_preserves_failed_regeneration(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    brief_path = campaign_brief(config)
    brief = WeeklyMarketingBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    target = package_path(tmp_path)
    with pytest.raises(DeterministicContentPrerequisiteError, match="must be approved"):
        asyncio.run(
            service_for(config).execute(
                brief_path=brief_path,
                package_path=target,
                expected_client_id=CLIENT_ID,
                expected_week=brief.week,
            )
        )

    brief_path, brief_checksum = approve_brief(config)
    write_package(tmp_path, state=BriefApprovalState.DRAFT)
    with pytest.raises(DeterministicContentPrerequisiteError, match="missing or revision"):
        asyncio.run(
            service_for(config).execute(
                brief_path=brief_path,
                package_path=target,
                expected_client_id=CLIENT_ID,
                expected_week=brief.week,
            )
        )
    target.unlink()
    with pytest.raises(DeterministicContentConflictError, match="brief changed"):
        asyncio.run(
            service_for(config).execute(
                brief_path=brief_path,
                package_path=target,
                expected_client_id=CLIENT_ID,
                expected_week=brief.week,
                expected_brief_checksum="0" * 64,
                expect_package_absent=True,
            )
        )

    write_package(tmp_path, state=BriefApprovalState.DRAFT)
    revised = ReviewContentPackage().request_revision(target, "Revise it.")
    before_json = target.read_bytes()
    before_markdown = target.with_suffix(".md").read_bytes()

    class FailingGenerator(DeterministicContentDraftGenerator):
        async def generate(self, *args, **kwargs) -> DraftGenerationResult:
            raise RuntimeError("generation failed")

    with pytest.raises(RuntimeError, match="generation failed"):
        asyncio.run(
            service_for(config, factory=lambda note: FailingGenerator()).execute(
                brief_path=brief_path,
                package_path=target,
                expected_client_id=CLIENT_ID,
                expected_week=brief.week,
                expected_brief_checksum=brief_checksum,
                expected_package_checksum=revised.checksum,
                expected_package_identity=revised.package.package_id,
            )
        )
    assert target.read_bytes() == before_json
    assert target.with_suffix(".md").read_bytes() == before_markdown


def generation_confirmation(app, config) -> tuple[str, dict[str, str]]:
    response = get(app, config, "/campaign/2026-08-03/package/generate/confirm")
    assert response.status == 200
    cookie = response.headers["Set-Cookie"].split(";", maxsplit=1)[0]
    fields = dict(
        re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', response.body.decode())
    )
    return cookie, fields


def generation_post(app, config, cookie: str, fields: dict[str, str]):
    return app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/2026-08-03/package/generate",
            {
                "Host": config.host_header,
                "Origin": config.origin,
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode(fields).encode(),
        )
    )


def test_workspace_missing_package_confirmation_generation_and_security(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    approve_brief(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"generation"))
    home = get(app, config, "/campaign/2026-08-03").body.decode()
    assert "Generate Casey's package" in home
    cookie, fields = generation_confirmation(app, config)
    assert fields["package_identity"] == "absent"
    assert "No paid model request will be made" in get(
        app, config, "/campaign/2026-08-03/package/generate/confirm"
    ).body.decode()
    before = hashlib.sha256(campaign_brief(config).read_bytes()).hexdigest()
    wrong_csrf = generation_post(app, config, cookie, {**fields, "csrf_token": "bad"})
    assert wrong_csrf.status == 403
    wrong_nonce = generation_post(
        app, config, cookie, {**fields, "confirmation_nonce": "bad"}
    )
    assert wrong_nonce.status == 403
    generated = generation_post(app, config, cookie, fields)
    assert generated.status == 303
    assert generated.headers["Location"].startswith("/campaign/2026-08-03/package")
    assert generation_post(app, config, cookie, fields).status == 403
    assert hashlib.sha256(campaign_brief(config).read_bytes()).hexdigest() == before
    assert ReviewContentPackage().load(
        package_path(tmp_path)
    ).package.approval_state is BriefApprovalState.DRAFT


def test_workspace_generation_conflicts_and_state_gates(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    approve_brief(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"generation-conflict"))
    cookie, fields = generation_confirmation(app, config)
    write_package(tmp_path, state=BriefApprovalState.DRAFT)
    conflict = generation_post(app, config, cookie, fields)
    assert conflict.status == 409

    assert get(app, config, "/campaign/2026-08-03/package/generate/confirm").status == 422
    target = package_path(tmp_path)
    set_package_state(target, BriefApprovalState.APPROVED)
    assert get(app, config, "/campaign/2026-08-03/package/generate/confirm").status == 422

    target.unlink()
    brief_path = campaign_brief(config)
    approved = ReviewWeeklyMarketingBrief().load(brief_path).brief
    brief_path.write_text(
        approved.model_copy(
            update={"approval_state": BriefApprovalState.REVISION_REQUESTED, "revision_note": "x"}
        ).model_dump_json(),
        encoding="utf-8",
    )
    assert get(app, config, "/campaign/2026-08-03/package/generate/confirm").status == 422
    assert get(app, config, "/campaign/2026-08-03/package/generate").status == 404


def test_workspace_regenerates_revision_requested_and_detects_changed_inputs(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    approve_brief(config)
    target = write_package(tmp_path, state=BriefApprovalState.DRAFT)
    ReviewContentPackage().request_revision(target, "Use a warmer opening.")
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"regeneration"))
    home = get(app, config, "/campaign/2026-08-03").body.decode()
    assert "Regenerate Casey's package" in home
    cookie, fields = generation_confirmation(app, config)
    assert fields["package_identity"] != "absent"
    regenerated = generation_post(app, config, cookie, fields)
    assert regenerated.status == 303
    assert ReviewContentPackage().load(
        target
    ).package.approval_state is BriefApprovalState.DRAFT

    target.unlink()
    target.with_suffix(".md").unlink()
    cookie, fields = generation_confirmation(app, config)
    brief_path = campaign_brief(config)
    brief_path.write_bytes(brief_path.read_bytes() + b"\n")
    assert generation_post(app, config, cookie, fields).status == 409

    write_package(tmp_path, state=BriefApprovalState.DRAFT)
    ReviewContentPackage().request_revision(target, "Try again.")
    cookie, fields = generation_confirmation(app, config)
    target.write_bytes(target.read_bytes() + b"\n")
    assert generation_post(app, config, cookie, fields).status == 409


def test_generation_route_reuses_host_origin_and_has_no_model_selector(tmp_path: Path) -> None:
    config = local_config(tmp_path)
    approve_brief(config)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"network-boundary"))
    assert app.handle(
        WorkspaceRequest(
            "GET",
            "/campaign/2026-08-03/package/generate/confirm",
            {"Host": "localhost:8765"},
        )
    ).status == 400
    cookie, fields = generation_confirmation(app, config)
    wrong_origin = app.handle(
        WorkspaceRequest(
            "POST",
            "/campaign/2026-08-03/package/generate",
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
    confirmation_page = get(
        app, config, "/campaign/2026-08-03/package/generate/confirm"
    ).body.decode()
    assert "deterministic" in confirmation_page
    assert 'name="generator"' not in confirmation_page
    assert "OpenAI" not in confirmation_page
    assert get(app, config, "/campaign/2026-08-03/package/model").status == 404


def test_generation_errors_preserve_august_3_and_august_10_campaign_context(
    tmp_path: Path,
) -> None:
    config = local_config(tmp_path)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"error-context"))
    august_3 = get(
        app,
        config,
        "/campaign/2026-08-03/package/generate/confirm",
    )
    august_3_page = august_3.body.decode()
    assert august_3.status == 422
    assert "package can&#x27;t be generated yet" in august_3_page
    assert 'href="/campaign/2026-08-03"' in august_3_page
    assert 'href="/"' not in august_3_page
    assert "Unprocessable Content" not in august_3_page

    add_campaign(config, "2026-08-10")
    august_10 = get(
        app,
        config,
        "/campaign/2026-08-10/package/generate/confirm",
    )
    august_10_page = august_10.body.decode()
    assert august_10.status == 422
    assert "package can&#x27;t be generated yet" in august_10_page
    assert 'href="/campaign/2026-08-10"' in august_10_page
    assert 'href="/"' not in august_10_page
    assert "Approval is not valid" not in august_10_page
