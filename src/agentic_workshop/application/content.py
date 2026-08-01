"""Deterministic, approval-gated content package generation."""

from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentDraft, ContentPackage
from agentic_workshop.domain.identity import EmployeeId
from agentic_workshop.domain.marketing import (
    BriefApprovalState,
    ContentAssignment,
    WeeklyMarketingBrief,
)

CASEY_ID = EmployeeId("casey")


class ContentPackageError(Exception):
    """Base error for content package generation."""


class UnapprovedMarketingBriefError(ContentPackageError):
    """Raised when Casey is asked to work without CEO brief approval."""


class ClientProfileMismatchError(ContentPackageError):
    """Raised when a brief and profile refer to different clients."""


class GenerateContentPackage:
    """Transform approved assignments into channel-specific, non-publishing drafts."""

    def execute(
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
        drafts = tuple(
            self._draft_assignment(assignment, brief, client, sources)
            for assignment in brief.content_assignments
        )
        missing = self._missing_inputs(brief, client)
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
                "This package is a draft and will not be published automatically.",
                "Only client_profile.approved_facts are available as factual claims.",
            ),
            missing_assets_or_information=missing,
        )

    def _draft_assignment(
        self,
        assignment: ContentAssignment,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        sources: tuple[str, ...],
    ) -> ContentDraft:
        channel = assignment.channel.lower()
        if "author review" in channel:
            title = f"Information request for {client.identity}"
            body = self._author_request(client)
        elif "planning" in channel:
            title = f"Approved-claims ledger for {client.identity}"
            body = self._claims_ledger(client)
        elif "social" in channel:
            title = f"Social draft: {brief.campaign_theme}"
            body = self._public_draft(client, brief, compact=True)
        elif "mail" in channel:
            title = f"Email draft: {brief.campaign_theme}"
            body = self._public_draft(client, brief, compact=False)
        elif "website" in channel:
            title = f"Website draft: {brief.campaign_theme}"
            body = self._website_draft(client, brief)
        else:
            title = f"{assignment.deliverable} draft"
            body = self._generic_draft(client, brief, assignment)

        return ContentDraft(
            assignment=assignment.deliverable,
            channel=assignment.channel,
            title=title,
            body=body,
            brand_voice_applied=client.brand_voice,
            source_references=sources,
            missing_assets_or_information=self._missing_inputs(brief, client),
        )

    @staticmethod
    def _missing_inputs(
        brief: WeeklyMarketingBrief, client: ClientProfile
    ) -> tuple[str, ...]:
        missing = [*brief.missing_inputs, *client.missing_information]
        if any(voice.lower() == "not yet supplied" for voice in client.brand_voice):
            missing.append("Approved brand voice")
        return tuple(dict.fromkeys(missing))

    @staticmethod
    def _author_request(client: ClientProfile) -> str:
        requested = "\n".join(f"- {item}" for item in client.missing_information)
        return (
            f"Please review the following information needed for {client.identity}:\n\n"
            f"{requested}\n\n"
            "Please explicitly approve each response before it is added to the client profile."
        )

    @staticmethod
    def _claims_ledger(client: ClientProfile) -> str:
        if client.approved_facts:
            facts = "\n".join(f"- APPROVED: {fact}" for fact in client.approved_facts)
        else:
            facts = "- No factual marketing claims are currently approved."
        restrictions = "\n".join(f"- PROHIBITED: {claim}" for claim in client.prohibited_claims)
        return f"Approved facts\n{facts}\n\nRestrictions\n{restrictions}"

    @staticmethod
    def _public_draft(
        client: ClientProfile, brief: WeeklyMarketingBrief, *, compact: bool
    ) -> str:
        facts = " ".join(client.approved_facts)
        if not facts:
            return "Draft withheld: no factual marketing claims are currently approved."
        if compact:
            return f"{facts}\n\n{brief.call_to_action}"
        return f"Hello,\n\n{facts}\n\n{brief.call_to_action}"

    @staticmethod
    def _website_draft(client: ClientProfile, brief: WeeklyMarketingBrief) -> str:
        facts = "\n".join(client.approved_facts)
        if not facts:
            return "Website copy withheld: no factual marketing claims are currently approved."
        return f"{client.identity}\n\n{facts}\n\n{brief.call_to_action}"

    @staticmethod
    def _generic_draft(
        client: ClientProfile,
        brief: WeeklyMarketingBrief,
        assignment: ContentAssignment,
    ) -> str:
        facts = "\n".join(client.approved_facts) or "No approved factual claims available."
        return (
            f"Assignment: {assignment.instructions}\n\n"
            f"Approved source material:\n{facts}\n\n"
            f"Call to action: {brief.call_to_action}"
        )
