import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_workshop.adapters.filesystem_resources import (
    FilesystemResourceLoader,
    UnsafeResourcePathError,
)
from agentic_workshop.application.marketing import (
    GenerateWeeklyMarketingBrief,
    IncompleteClientProfileError,
)
from agentic_workshop.cli import PACKAGE_RESOURCE_ROOT, run
from agentic_workshop.domain.identity import ClientId, EmployeeId
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief


def generate_brief(requested_date: date = date(2026, 8, 3)) -> WeeklyMarketingBrief:
    loader = FilesystemResourceLoader(PACKAGE_RESOURCE_ROOT)
    return asyncio.run(
        GenerateWeeklyMarketingBrief(loader).execute(
            "jordan-and-the-fosters", requested_date
        )
    )


def test_successful_draft_generation_is_schema_valid() -> None:
    brief = generate_brief()

    assert brief.client_id == ClientId("jordan-and-the-fosters")
    assert brief.employee_id == EmployeeId("sarah-collins")
    assert brief.approval_state is BriefApprovalState.DRAFT
    assert brief.missing_inputs
    assert "No public content will be published from this brief." in brief.assumptions
    assert brief.recommended_channels == ("Internal author review",)


def test_requested_date_is_normalized_to_monday() -> None:
    brief = generate_brief(date(2026, 8, 9))

    assert brief.week == date(2026, 8, 3)


def test_brief_preserves_governing_source_references() -> None:
    brief = generate_brief()

    assert "clients/jordan-and-the-fosters.v1.json" in brief.source_references
    assert "employees/sarah-collins.v1.json" in brief.source_references
    assert "prompts/sarah-weekly-marketing.v1.md" in brief.source_references
    assert "sops/weekly-marketing-brief.v1.md" in brief.source_references


def test_strict_mode_rejects_incomplete_profile() -> None:
    loader = FilesystemResourceLoader(PACKAGE_RESOURCE_ROOT)

    with pytest.raises(IncompleteClientProfileError) as raised:
        asyncio.run(
            GenerateWeeklyMarketingBrief(loader).execute(
                "jordan-and-the-fosters", date(2026, 8, 3), strict=True
            )
        )

    assert "Author-approved synopsis" in raised.value.missing_information


def test_filesystem_loader_rejects_path_traversal(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    resource_root.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    loader = FilesystemResourceLoader(resource_root)

    with pytest.raises(UnsafeResourcePathError):
        asyncio.run(loader.load_text("../secret.txt"))


def test_revision_state_requires_a_note() -> None:
    data = generate_brief().model_dump()
    data["approval_state"] = BriefApprovalState.REVISION_REQUESTED

    with pytest.raises(ValidationError, match="revision_note is required"):
        WeeklyMarketingBrief.model_validate(data)


def test_approval_and_revision_cli_updates_are_valid(tmp_path: Path) -> None:
    artifact_root = tmp_path / "briefs"
    assert (
        run(
            [
                "brief",
                "jordan-and-the-fosters",
                "--week-of",
                "2026-08-03",
                "--artifact-root",
                str(artifact_root),
            ]
        )
        == 0
    )
    brief_path = artifact_root / "jordan-and-the-fosters-2026-08-03.json"

    assert run(["review", str(brief_path), "--approve"]) == 0
    approved = WeeklyMarketingBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    assert approved.approval_state is BriefApprovalState.APPROVED
    assert approved.revision_note is None

    note = "Use a shorter internal information request."
    assert run(["review", str(brief_path), "--request-revision", note]) == 0
    revised = WeeklyMarketingBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    assert revised.approval_state is BriefApprovalState.REVISION_REQUESTED
    assert revised.revision_note == note
    assert note in brief_path.with_suffix(".md").read_text(encoding="utf-8")


def test_generated_json_contains_no_unapproved_book_facts(tmp_path: Path) -> None:
    artifact_root = tmp_path / "briefs"
    run(
        [
            "brief",
            "jordan-and-the-fosters",
            "--week-of",
            "2026-08-03",
            "--artifact-root",
            str(artifact_root),
        ]
    )
    payload = json.loads(
        (artifact_root / "jordan-and-the-fosters-2026-08-03.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["missing_inputs"] == [
        "Author-approved synopsis",
        "Reader age range",
        "Author story and reason for writing the book",
        "Official purchase link",
        "Preferred public call to action",
        "Approved cover and illustrations",
        "Reviews or testimonials",
        "Current website, mailing list, and social channels",
    ]

