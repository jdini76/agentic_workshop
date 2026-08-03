import asyncio
from datetime import date
from pathlib import Path

import pytest

from agentic_workshop.adapters.deterministic_content import (
    DeterministicContentDraftGenerator,
)
from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.application.content import (
    GenerateContentPackage,
    InvalidGeneratedDraftsError,
    UnapprovedMarketingBriefError,
)
from agentic_workshop.application.marketing import GenerateWeeklyMarketingBrief
from agentic_workshop.cli import PACKAGE_RESOURCE_ROOT, run
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import (
    ContentDraft,
    ContentGenerationMetadata,
    ContentPackage,
    DraftGenerationResult,
)
from agentic_workshop.domain.employee import Employee
from agentic_workshop.domain.marketing import (
    BriefApprovalState,
    ContentAssignment,
    WeeklyMarketingBrief,
)
from agentic_workshop.ports.content_generation import ContentDraftGenerator


def load_inputs() -> tuple[WeeklyMarketingBrief, ClientProfile]:
    loader = FilesystemResourceLoader(PACKAGE_RESOURCE_ROOT)
    brief = asyncio.run(
        GenerateWeeklyMarketingBrief(loader).execute(
            "jordan-and-the-fosters", date(2026, 8, 3)
        )
    )
    client = ClientProfile.model_validate_json(
        asyncio.run(loader.load_text("clients/jordan-and-the-fosters.v1.json"))
    )
    return brief, client


def approve(brief: WeeklyMarketingBrief) -> WeeklyMarketingBrief:
    data = brief.model_dump(mode="json")
    data["approval_state"] = BriefApprovalState.APPROVED
    return WeeklyMarketingBrief.model_validate(data)


def generate_package(
    brief: WeeklyMarketingBrief,
    client: ClientProfile,
    *,
    source: str = "approved-brief.json",
) -> ContentPackage:
    return asyncio.run(
        GenerateContentPackage(DeterministicContentDraftGenerator()).execute(
            brief, client, approved_brief_source=source
        )
    )


def test_casey_employee_resource_is_valid() -> None:
    loader = FilesystemResourceLoader(PACKAGE_RESOURCE_ROOT)
    casey = Employee.model_validate_json(
        asyncio.run(loader.load_text("employees/casey.v1.json"))
    )

    assert str(casey.id) == "casey"
    assert casey.identity.role == "Content Creator"


def test_unapproved_brief_is_rejected() -> None:
    brief, client = load_inputs()

    with pytest.raises(UnapprovedMarketingBriefError):
        generate_package(brief, client, source="brief.json")


def test_approved_brief_creates_source_grounded_draft_package() -> None:
    brief, client = load_inputs()
    package = generate_package(approve(brief), client)

    assert package.approval_state is BriefApprovalState.DRAFT
    assert package.employee_id == "casey"
    assert len(package.drafts) == len(brief.content_assignments)
    assert len({draft.body for draft in package.drafts}) == len(package.drafts)
    assert all("approved-brief.json" in draft.source_references for draft in package.drafts)
    assert all(draft.brand_voice_applied == client.brand_voice for draft in package.drafts)
    assert all(draft.state == "draft" for draft in package.drafts)
    assert all(draft.approved_facts_used for draft in package.drafts)
    assert all(
        set(draft.approved_facts_used).issubset(client.approved_facts)
        for draft in package.drafts
    )
    assert all(draft.missing_assets_or_information for draft in package.drafts)
    assert "Approved brand voice" not in package.missing_assets_or_information
    assert any(
        asset.startswith("Approved cover and illustrations")
        for asset in package.required_assets
    )
    assert "publish" in package.assumptions[0].lower()
    website, social = package.drafts
    assert website.title == "A Story of Kindness, Courage, and Belonging."
    assert "https://www.amazon.com/gp/aw/d/B0D5BT1XDZ" in website.body
    assert website.body.count("https://www.amazon.com/gp/aw/d/B0D5BT1XDZ") == 1
    assert website.body.count("Choose your edition") == 1
    assert "approved reader age range" not in website.body.lower()
    assert "canonical" not in website.body.lower()
    assert '"Joe Dinicola tells a tale' in website.body
    assert "Constance Stadler, Readers\u2019 Favorite" in website.body
    assert any(
        "readersfavorite.com/book-review" in source
        for source in website.source_references
    )
    assert social.body.startswith("How can we help children understand")
    assert 100 <= len(social.body.split()) <= 140
    assert social.body.count("https://www.amazon.com/gp/aw/d/B0D5BT1XDZ") == 1
    assert social.body.count("Choose your edition") == 1
    assert "#" not in social.body


def test_channel_drafts_adapt_copy_without_inventing_facts() -> None:
    brief, client = load_inputs()
    client_data = client.model_dump(mode="json")
    client_data.update(
        brand_voice=["Warm", "Direct"],
        approved_facts=["Jordan and the Fosters is the approved title."],
        missing_information=[],
        calls_to_action=["Learn more"],
    )
    approved_client = ClientProfile.model_validate(client_data)
    brief_data = approve(brief).model_dump(mode="json")
    brief_data.update(
        call_to_action="Learn more",
        missing_inputs=[],
        content_assignments=[
            ContentAssignment(
                owner_id="sarah-collins",
                deliverable="Social post",
                channel="Social media",
                instructions="Create a concise post.",
            ).model_dump(mode="json"),
            ContentAssignment(
                owner_id="sarah-collins",
                deliverable="Reader email",
                channel="Email",
                instructions="Create an email.",
            ).model_dump(mode="json"),
        ],
    )
    channel_brief = WeeklyMarketingBrief.model_validate(brief_data)

    package = generate_package(channel_brief, approved_client, source="approved.json")

    social, email = package.drafts
    assert social.body != email.body
    assert social.body.startswith("How can we help children understand")
    assert email.body.startswith("Hello,")
    assert "Learn more" in social.body and "Learn more" in email.body
    assert social.brand_voice_applied == ("Warm", "Direct")
    assert social.approved_facts_used == approved_client.approved_facts


class InvalidSourceGenerator(ContentDraftGenerator):
    async def generate(
        self,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        *,
        source_references: tuple[str, ...],
        missing_information: tuple[str, ...],
        required_assets: tuple[str, ...],
    ) -> DraftGenerationResult:
        drafts = tuple(
            ContentDraft(
                assignment=assignment.deliverable,
                channel=assignment.channel,
                title="Invalid source draft",
                body="No claims.",
                brand_voice_applied=client.brand_voice,
                approved_facts_used=(),
                source_references=("untrusted.json",),
                missing_assets_or_information=missing_information,
                required_assets=required_assets,
            )
            for assignment in brief.content_assignments
        )
        return DraftGenerationResult(
            drafts=drafts,
            metadata=ContentGenerationMetadata(generator="invalid-test"),
        )


def test_generator_output_requires_complete_approved_sources() -> None:
    brief, client = load_inputs()

    with pytest.raises(InvalidGeneratedDraftsError, match="cite the approved brief"):
        asyncio.run(
            GenerateContentPackage(InvalidSourceGenerator()).execute(
                approve(brief), client, approved_brief_source="approved.json"
            )
        )


def test_cli_rejects_draft_and_writes_then_reviews_approved_package(
    tmp_path: Path,
) -> None:
    brief, _ = load_inputs()
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(brief.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(SystemExit) as rejected:
        run(["content-package", str(brief_path)])
    assert rejected.value.code == 2

    brief_path.write_text(approve(brief).model_dump_json(indent=2), encoding="utf-8")
    artifact_root = tmp_path / "packages"
    assert (
        run(
            [
                "content-package",
                str(brief_path),
                "--artifact-root",
                str(artifact_root),
            ]
        )
        == 0
    )
    package_path = artifact_root / "jordan-and-the-fosters-2026-08-03-content.json"
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    assert package.approval_state is BriefApprovalState.DRAFT
    assert package_path.with_suffix(".md").is_file()

    assert run(["review", str(package_path), "--approve"]) == 0
    approved_package = ContentPackage.model_validate_json(
        package_path.read_text(encoding="utf-8")
    )
    assert approved_package.approval_state is BriefApprovalState.APPROVED

    note = "Revise channel phrasing."
    assert run(["review", str(package_path), "--request-revision", note]) == 0
    revised_package = ContentPackage.model_validate_json(
        package_path.read_text(encoding="utf-8")
    )
    assert revised_package.approval_state is BriefApprovalState.REVISION_REQUESTED
    assert revised_package.revision_note == note
