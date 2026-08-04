"""Checksum-guarded review transitions for Casey content packages."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import BriefApprovalState
from agentic_workshop.presentation.content_markdown import render_content_package


class ContentReviewError(ValueError):
    """Base error for safe content-package review operations."""


class ContentArtifactMissingError(ContentReviewError):
    """Raised when the selected package does not exist."""


class ContentArtifactInvalidError(ContentReviewError):
    """Raised when an artifact is not a valid content package."""


class ContentArtifactConflictError(ContentReviewError):
    """Raised when a package changed after confirmation."""


class ContentArtifactIdentityError(ContentReviewError):
    """Raised when the selected package belongs to another campaign."""


class ContentReviewTransitionError(ContentReviewError):
    """Raised when an action is invalid for the package state."""


class ContentReviewAction(StrEnum):
    APPROVE = "approve"
    REQUEST_REVISION = "revision"


@dataclass(frozen=True)
class LoadedContentArtifact:
    package: ContentPackage
    checksum: str


class ReviewContentPackage:
    """Load, transition, and atomically persist one Casey package."""

    def load(self, artifact_path: Path) -> LoadedContentArtifact:
        try:
            raw = artifact_path.read_bytes()
        except FileNotFoundError as error:
            raise ContentArtifactMissingError(
                f"Casey's content package is missing: {artifact_path}"
            ) from error
        if not artifact_path.is_file():
            raise ContentArtifactInvalidError(
                f"Casey's content package is not a file: {artifact_path}"
            )
        try:
            package = ContentPackage.model_validate_json(raw)
        except (ValidationError, UnicodeDecodeError, ValueError) as error:
            raise ContentArtifactInvalidError(
                "Casey's content package is invalid and cannot be reviewed."
            ) from error
        return LoadedContentArtifact(package, hashlib.sha256(raw).hexdigest())

    def approve(
        self,
        artifact_path: Path,
        *,
        expected_checksum: str | None = None,
        expected_client_id: ClientId | None = None,
        expected_week: date | None = None,
    ) -> LoadedContentArtifact:
        return self._transition(
            artifact_path,
            action=ContentReviewAction.APPROVE,
            approval_state=BriefApprovalState.APPROVED,
            revision_note=None,
            expected_checksum=expected_checksum,
            expected_client_id=expected_client_id,
            expected_week=expected_week,
        )

    def request_revision(
        self,
        artifact_path: Path,
        revision_note: str,
        *,
        expected_checksum: str | None = None,
        expected_client_id: ClientId | None = None,
        expected_week: date | None = None,
    ) -> LoadedContentArtifact:
        if not revision_note.strip():
            raise ContentReviewError("Revision instructions are required.")
        return self._transition(
            artifact_path,
            action=ContentReviewAction.REQUEST_REVISION,
            approval_state=BriefApprovalState.REVISION_REQUESTED,
            revision_note=revision_note.strip(),
            expected_checksum=expected_checksum,
            expected_client_id=expected_client_id,
            expected_week=expected_week,
        )

    def _transition(
        self,
        artifact_path: Path,
        *,
        action: ContentReviewAction,
        approval_state: BriefApprovalState,
        revision_note: str | None,
        expected_checksum: str | None,
        expected_client_id: ClientId | None,
        expected_week: date | None,
    ) -> LoadedContentArtifact:
        current = self.load(artifact_path)
        if expected_checksum is not None and current.checksum != expected_checksum:
            raise ContentArtifactConflictError(
                "Casey's package changed after confirmation. Reload it and review again."
            )
        if expected_client_id is not None and current.package.client_id != expected_client_id:
            raise ContentArtifactIdentityError("Casey's package belongs to a different client.")
        if expected_week is not None and current.package.week != expected_week:
            raise ContentArtifactIdentityError(
                "Casey's package belongs to a different campaign week."
            )
        self.ensure_action_allowed(current.package, action)
        reviewed = ContentPackage.model_validate(
            {
                **current.package.model_dump(mode="json"),
                "approval_state": approval_state,
                "revision_note": revision_note,
            }
        )
        json_bytes = (reviewed.model_dump_json(indent=2) + "\n").encode()
        markdown_bytes = render_content_package(reviewed).encode()
        self._atomic_write(artifact_path.with_suffix(".md"), markdown_bytes)
        self._atomic_write(artifact_path, json_bytes)
        return LoadedContentArtifact(reviewed, hashlib.sha256(json_bytes).hexdigest())

    @staticmethod
    def available_actions(package: ContentPackage) -> tuple[ContentReviewAction, ...]:
        if package.approval_state is BriefApprovalState.DRAFT:
            return (ContentReviewAction.APPROVE, ContentReviewAction.REQUEST_REVISION)
        if package.approval_state is BriefApprovalState.APPROVED:
            return (ContentReviewAction.REQUEST_REVISION,)
        return ()

    @classmethod
    def ensure_action_allowed(
        cls,
        package: ContentPackage,
        action: ContentReviewAction,
    ) -> None:
        if action not in cls.available_actions(package):
            label = "Approval" if action is ContentReviewAction.APPROVE else "Revision request"
            raise ContentReviewTransitionError(
                f"{label} is not valid while Casey's package is "
                f"{package.approval_state.value.replace('_', ' ')}."
            )

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
