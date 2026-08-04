"""Checksum-guarded review transitions for weekly marketing briefs."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief
from agentic_workshop.presentation.markdown import render_weekly_marketing_brief


class BriefReviewError(ValueError):
    """Base error for safe weekly-brief review operations."""


class BriefArtifactMissingError(BriefReviewError):
    """Raised when the selected brief artifact does not exist."""


class BriefArtifactInvalidError(BriefReviewError):
    """Raised when an artifact is not a valid weekly marketing brief."""


class BriefArtifactConflictError(BriefReviewError):
    """Raised when an artifact changed after the user confirmed an action."""


class BriefArtifactIdentityError(BriefReviewError):
    """Raised when the selected artifact is for another campaign."""


class BriefReviewTransitionError(BriefReviewError):
    """Raised when a review action is invalid for the brief's current state."""


class BriefReviewAction(StrEnum):
    APPROVE = "approve"
    REQUEST_REVISION = "revision"


@dataclass(frozen=True)
class LoadedBriefArtifact:
    brief: WeeklyMarketingBrief
    checksum: str


class ReviewWeeklyMarketingBrief:
    """Load, transition, and atomically persist one weekly brief."""

    def load(self, artifact_path: Path) -> LoadedBriefArtifact:
        try:
            raw = artifact_path.read_bytes()
        except FileNotFoundError as error:
            raise BriefArtifactMissingError(
                f"Sarah's weekly brief is missing: {artifact_path}"
            ) from error
        if not artifact_path.is_file():
            raise BriefArtifactInvalidError(
                f"Sarah's weekly brief is not a file: {artifact_path}"
            )
        try:
            brief = WeeklyMarketingBrief.model_validate_json(raw)
        except (ValidationError, UnicodeDecodeError, ValueError) as error:
            raise BriefArtifactInvalidError(
                "Sarah's weekly brief is invalid and cannot be reviewed."
            ) from error
        return LoadedBriefArtifact(
            brief=brief,
            checksum=hashlib.sha256(raw).hexdigest(),
        )

    def approve(
        self,
        artifact_path: Path,
        *,
        expected_checksum: str | None = None,
        expected_client_id: ClientId | None = None,
        expected_week: date | None = None,
    ) -> LoadedBriefArtifact:
        return self._transition(
            artifact_path,
            action=BriefReviewAction.APPROVE,
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
    ) -> LoadedBriefArtifact:
        if not revision_note.strip():
            raise BriefReviewError("Revision instructions are required.")
        return self._transition(
            artifact_path,
            action=BriefReviewAction.REQUEST_REVISION,
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
        action: BriefReviewAction,
        approval_state: BriefApprovalState,
        revision_note: str | None,
        expected_checksum: str | None,
        expected_client_id: ClientId | None,
        expected_week: date | None,
    ) -> LoadedBriefArtifact:
        current = self.load(artifact_path)
        if expected_checksum is not None and current.checksum != expected_checksum:
            raise BriefArtifactConflictError(
                "Sarah's brief changed after confirmation. Reload it and review again."
            )
        if expected_client_id is not None and current.brief.client_id != expected_client_id:
            raise BriefArtifactIdentityError(
                "Sarah's brief belongs to a different client."
            )
        if expected_week is not None and current.brief.week != expected_week:
            raise BriefArtifactIdentityError(
                "Sarah's brief belongs to a different campaign week."
            )
        self.ensure_action_allowed(current.brief, action)
        data = current.brief.model_dump(mode="json")
        data.update(approval_state=approval_state, revision_note=revision_note)
        reviewed = WeeklyMarketingBrief.model_validate(data)
        json_bytes = (reviewed.model_dump_json(indent=2) + "\n").encode()
        markdown_bytes = render_weekly_marketing_brief(reviewed).encode()
        self._atomic_write(artifact_path.with_suffix(".md"), markdown_bytes)
        self._atomic_write(artifact_path, json_bytes)
        return LoadedBriefArtifact(
            brief=reviewed,
            checksum=hashlib.sha256(json_bytes).hexdigest(),
        )

    @staticmethod
    def available_actions(brief: WeeklyMarketingBrief) -> tuple[BriefReviewAction, ...]:
        if brief.approval_state is BriefApprovalState.DRAFT:
            return (
                BriefReviewAction.APPROVE,
                BriefReviewAction.REQUEST_REVISION,
            )
        if brief.approval_state is BriefApprovalState.APPROVED:
            return (BriefReviewAction.REQUEST_REVISION,)
        return ()

    @classmethod
    def ensure_action_allowed(
        cls,
        brief: WeeklyMarketingBrief,
        action: BriefReviewAction,
    ) -> None:
        if action not in cls.available_actions(brief):
            label = "Approval" if action is BriefReviewAction.APPROVE else "Revision request"
            raise BriefReviewTransitionError(
                f"{label} is not valid while Sarah's brief is "
                f"{brief.approval_state.value.replace('_', ' ')}."
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
