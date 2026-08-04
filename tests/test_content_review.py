import hashlib
from pathlib import Path

import pytest

from agentic_workshop.application.content_review import (
    ContentArtifactConflictError,
    ContentArtifactIdentityError,
    ContentReviewError,
    ContentReviewTransitionError,
    ReviewContentPackage,
)
from agentic_workshop.cli import run
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import BriefApprovalState

ROOT = Path(__file__).parents[1]
SOURCE = (
    ROOT / "artifacts" / "content-packages"
    / "jordan-and-the-fosters-2026-08-03-content.json"
)


def draft_package(path: Path) -> ContentPackage:
    source = ContentPackage.model_validate_json(SOURCE.read_text(encoding="utf-8"))
    package = source.model_copy(
        update={"approval_state": BriefApprovalState.DRAFT, "revision_note": None}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return package


def test_cli_uses_content_review_service_for_approval_and_revision(tmp_path: Path) -> None:
    path = tmp_path / "package.json"
    draft_package(path)
    assert run(["review", str(path), "--approve"]) == 0
    service = ReviewContentPackage()
    assert service.load(path).package.approval_state is BriefApprovalState.APPROVED
    assert run(["review", str(path), "--request-revision", "Warm the copy."]) == 0
    revised = service.load(path).package
    assert revised.approval_state is BriefApprovalState.REVISION_REQUESTED
    assert revised.revision_note == "Warm the copy."
    assert path.with_suffix(".md").is_file()


def test_content_review_validates_identity_state_note_and_checksum(tmp_path: Path) -> None:
    path = tmp_path / "package.json"
    package = draft_package(path)
    service = ReviewContentPackage()
    loaded = service.load(path)
    with pytest.raises(ContentReviewError, match="required"):
        service.request_revision(path, "   ")
    with pytest.raises(ContentArtifactIdentityError, match="different client"):
        service.approve(path, expected_client_id=ClientId("another-client"))
    with pytest.raises(ContentArtifactIdentityError, match="different campaign week"):
        service.approve(path, expected_week=package.week.replace(day=10))
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ContentArtifactConflictError, match="changed after confirmation"):
        service.approve(path, expected_checksum=loaded.checksum)

    path = tmp_path / "approved.json"
    draft_package(path)
    service.approve(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ContentReviewTransitionError, match="Approval is not valid"):
        service.approve(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
