"""Approval-gated orchestration for content package generation."""

from collections import Counter

from agentic_workshop.application.channels import channel_use
from agentic_workshop.domain.assets import AssetRecommendation
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentDraft, ContentPackage
from agentic_workshop.domain.identity import EmployeeId
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief
from agentic_workshop.ports.content_generation import ContentDraftGenerator

CASEY_ID = EmployeeId("casey")


class ContentPackageError(Exception):
    """Base error for content package generation."""


class UnapprovedMarketingBriefError(ContentPackageError):
    """Raised when Casey is asked to work without CEO brief approval."""


class ClientProfileMismatchError(ContentPackageError):
    """Raised when a brief and profile refer to different clients."""


class InvalidGeneratedDraftsError(ContentPackageError):
    """Raised when a drafting strategy violates provenance or coverage contracts."""


class GenerateContentPackage:
    """Validate inputs and generator output, then assemble a reviewable draft package."""

    def __init__(
        self,
        generator: ContentDraftGenerator,
        *,
        asset_recommendations: tuple[AssetRecommendation, ...] = (),
    ) -> None:
        self._generator = generator
        self._asset_recommendations = asset_recommendations

    async def execute(
        self,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        *,
        approved_brief_source: str,
    ) -> ContentPackage:
        if brief.approval_state is not BriefApprovalState.APPROVED:
            raise UnapprovedMarketingBriefError(
                "Casey requires an approved WeeklyMarketingBrief; received "
                f"{brief.approval_state.value!r}"
            )
        if brief.client_id != client.id:
            raise ClientProfileMismatchError(
                f"brief client {brief.client_id!r} does not match profile {client.id!r}"
            )

        sources = tuple(
            dict.fromkeys(
                (*brief.source_references, client.source_reference, approved_brief_source)
            )
        )
        missing = self._missing_inputs(brief, client)
        assets = self._required_assets(missing)
        generation = await self._generator.generate(
            brief,
            client,
            approved_brief_source=approved_brief_source,
            source_references=sources,
            missing_information=missing,
            required_assets=assets,
        )
        self._validate_drafts(
            generation.drafts,
            brief,
            client,
            (client.source_reference, approved_brief_source),
        )
        drafts = tuple(self._attach_assignment_assets(draft) for draft in generation.drafts)

        return ContentPackage(
            package_id=f"{client.id}-{brief.week.isoformat()}-content",
            client_id=client.id,
            employee_id=CASEY_ID,
            week=brief.week,
            approved_brief_source=approved_brief_source,
            client_profile_source=client.source_reference,
            brand_voice=client.brand_voice,
            drafts=drafts,
            assumptions=(
                "This package is a draft and has not been published.",
                "Only client_profile.approved_facts are available as factual claims.",
            ),
            missing_assets_or_information=missing,
            required_assets=assets,
            asset_recommendations=self._asset_recommendations,
            generation_metadata=generation.metadata,
        )

    def _attach_assignment_assets(self, draft: ContentDraft) -> ContentDraft:
        required_use = channel_use(draft.channel)
        recommendations = tuple(
            recommendation
            for recommendation in self._asset_recommendations
            if recommendation.availability == "available"
            and required_use in recommendation.permitted_uses
        )
        return ContentDraft.model_validate(
            {
                **draft.model_dump(mode="json"),
                "asset_recommendations": recommendations,
            }
        )

    @staticmethod
    def _validate_drafts(
        drafts: tuple[ContentDraft, ...],
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        required_sources: tuple[str, ...],
    ) -> None:
        expected = Counter(
            (assignment.deliverable, assignment.channel)
            for assignment in brief.content_assignments
        )
        actual = Counter((draft.assignment, draft.channel) for draft in drafts)
        if actual != expected:
            raise InvalidGeneratedDraftsError(
                "drafts must cover every brief assignment exactly once"
            )
        approved_facts = set(client.approved_facts)
        required_source_set = set(required_sources)
        for draft in drafts:
            if draft.state != "draft":
                raise InvalidGeneratedDraftsError("generated content must remain in draft state")
            if draft.brand_voice_applied != client.brand_voice:
                raise InvalidGeneratedDraftsError("draft brand voice differs from client profile")
            if not set(draft.approved_facts_used).issubset(approved_facts):
                raise InvalidGeneratedDraftsError("draft cites an unapproved client fact")
            if not required_source_set.issubset(draft.source_references):
                raise InvalidGeneratedDraftsError(
                    "every draft must cite the approved brief and client sources"
                )

    def _missing_inputs(
        self, brief: WeeklyMarketingBrief, client: ClientProfile
    ) -> tuple[str, ...]:
        missing = [*brief.missing_inputs, *client.missing_information]
        if any(
            recommendation.availability == "available"
            for recommendation in self._asset_recommendations
        ):
            missing = [
                item
                for item in missing
                if "approved cover and illustrations" not in item.lower()
            ]
        if any(voice.lower() == "not yet supplied" for voice in client.brand_voice):
            missing.append("Approved brand voice")
        return tuple(dict.fromkeys(missing))

    @staticmethod
    def _required_assets(missing: tuple[str, ...]) -> tuple[str, ...]:
        asset_terms = ("asset", "cover", "illustration", "image", "logo", "video")
        return tuple(
            item for item in missing if any(term in item.lower() for term in asset_terms)
        )
