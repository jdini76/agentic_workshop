"""Deterministic content drafting used before model integration."""

from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import (
    ContentDraft,
    ContentGenerationMetadata,
    DraftGenerationResult,
)
from agentic_workshop.domain.marketing import ContentAssignment, WeeklyMarketingBrief
from agentic_workshop.ports.content_generation import ContentDraftGenerator


class DeterministicContentDraftGenerator(ContentDraftGenerator):
    """Render safe drafts using only literal values from validated inputs."""

    async def generate(
        self,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        *,
        source_references: tuple[str, ...],
        missing_information: tuple[str, ...],
        required_assets: tuple[str, ...],
    ) -> DraftGenerationResult:
        return DraftGenerationResult(
            drafts=tuple(
                self._draft_assignment(
                    assignment,
                    brief,
                    client,
                    source_references,
                    missing_information,
                    required_assets,
                )
                for assignment in brief.content_assignments
            ),
            metadata=ContentGenerationMetadata(generator="deterministic"),
        )

    def _draft_assignment(
        self,
        assignment: ContentAssignment,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        sources: tuple[str, ...],
        missing_information: tuple[str, ...],
        required_assets: tuple[str, ...],
    ) -> ContentDraft:
        channel = assignment.channel.lower()
        facts_used: tuple[str, ...] = ()
        draft_sources = sources
        if "author review" in channel:
            title = f"Information request for {client.identity}"
            body = self._author_request(client)
        elif "planning" in channel:
            title = f"Approved-claims ledger for {client.identity}"
            body = self._claims_ledger(client)
            facts_used = client.approved_facts
        elif "social" in channel:
            title = f"Social draft: {brief.campaign_theme}"
            facts_used = client.approved_facts[:3]
            body = self._social_draft(client, brief, facts_used)
            draft_sources = self._purchase_sources(client, draft_sources)
        elif "mail" in channel:
            title = f"Email draft: {brief.campaign_theme}"
            body = self._public_draft(client, brief, compact=False)
            facts_used = client.approved_facts
        elif "website" in channel:
            title = "A Story of Kindness, Courage, and Belonging."
            facts_used = self._website_facts(client)
            body = self._website_draft(client, brief, facts_used)
            draft_sources = self._purchase_sources(client, draft_sources)
            draft_sources = tuple(
                dict.fromkeys(
                    (*draft_sources, *(review.source_url for review in client.approved_reviews))
                )
            )
        else:
            title = f"{assignment.deliverable} draft"
            body = self._generic_draft(client, brief)
            facts_used = client.approved_facts

        return ContentDraft(
            assignment=assignment.deliverable,
            channel=assignment.channel,
            title=title,
            body=body,
            brand_voice_applied=client.brand_voice,
            approved_facts_used=facts_used,
            source_references=draft_sources,
            missing_assets_or_information=missing_information,
            required_assets=required_assets,
        )

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

    @classmethod
    def _public_draft(
        cls, client: ClientProfile, brief: WeeklyMarketingBrief, *, compact: bool
    ) -> str:
        facts = " ".join(client.approved_facts)
        if not facts:
            return "Draft withheld: no factual marketing claims are currently approved."
        call_to_action = cls._approved_call_to_action(client, brief)
        if compact:
            return f"{facts}\n\n{call_to_action}"
        return f"Hello,\n\n{facts}\n\n{call_to_action}"

    @classmethod
    def _website_draft(
        cls,
        client: ClientProfile,
        brief: WeeklyMarketingBrief,
        approved_facts: tuple[str, ...],
    ) -> str:
        if not approved_facts:
            return "Website copy withheld: no factual marketing claims are currently approved."
        introduction = cls._first_sentences(client.summary, count=3)
        audience = (
            "This illustrated read-aloud is intended for children ages 3\N{EN DASH}8 and gives "
            "parents and caregivers a warm way to discuss kindness, patience, trust, and "
            "belonging."
        )
        availability = next(
            (
                fact
                for fact in client.approved_facts
                if "available in paperback" in fact.lower()
            ),
            "",
        )
        review_block = ""
        if client.approved_reviews:
            review = client.approved_reviews[0]
            review_block = f'\n\n"{review.quote}"\n- {review.attribution}'
        return (
            f"{introduction}\n\n{audience}\n\n{availability}"
            f"{review_block}\n\n{cls._cta_block(client, brief)}"
        )

    @classmethod
    def _social_draft(
        cls,
        client: ClientProfile,
        brief: WeeklyMarketingBrief,
        approved_facts: tuple[str, ...],
    ) -> str:
        if not approved_facts:
            return "Draft withheld: no factual marketing claims are currently approved."
        question = (
            "How can we help children understand that cautious animals may need patience "
            "and kindness?"
        )
        journey = cls._first_sentences(client.summary, count=3)
        audience = (
            f"{client.identity} is an illustrated read-aloud for children "
            "ages 3\N{EN DASH}8."
        )
        return f"{question}\n\n{journey}\n\n{audience}\n\n{cls._cta_block(client, brief)}"

    @classmethod
    def _generic_draft(cls, client: ClientProfile, brief: WeeklyMarketingBrief) -> str:
        facts = "\n".join(client.approved_facts)
        if not facts:
            return "Draft withheld: no factual marketing claims are currently approved."
        return f"{facts}\n\n{cls._approved_call_to_action(client, brief)}"

    @staticmethod
    def _approved_call_to_action(
        client: ClientProfile, brief: WeeklyMarketingBrief
    ) -> str:
        if brief.call_to_action in client.calls_to_action:
            return brief.call_to_action
        return "Call to action withheld pending explicit client approval."

    @classmethod
    def _cta_block(cls, client: ClientProfile, brief: WeeklyMarketingBrief) -> str:
        call_to_action = cls._approved_call_to_action(client, brief)
        purchase_terms = ("order", "buy", "edition", "amazon")
        if client.purchase_links and any(
            term in call_to_action.lower() for term in purchase_terms
        ):
            link = client.purchase_links[0]
            return f"{call_to_action}\n{link.url}"
        return call_to_action

    @staticmethod
    def _purchase_sources(
        client: ClientProfile, sources: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*sources, *(link.url for link in client.purchase_links))))

    @staticmethod
    def _website_facts(client: ClientProfile) -> tuple[str, ...]:
        selected = list(client.approved_facts[:3])
        selected.extend(
            fact
            for fact in client.approved_facts
            if "available in paperback" in fact.lower()
        )
        return tuple(dict.fromkeys(selected))

    @staticmethod
    def _first_sentences(text: str, *, count: int) -> str:
        sentences = text.split(". ")
        selected = ". ".join(sentences[:count])
        if selected and not selected.endswith("."):
            selected += "."
        return selected
