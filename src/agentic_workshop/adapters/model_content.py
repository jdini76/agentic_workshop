"""Language-model-backed content drafting with application-owned validation."""

import json
import re
from difflib import SequenceMatcher

from pydantic import ValidationError, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import (
    ContentDraft,
    ContentGenerationMetadata,
    DraftGenerationResult,
)
from agentic_workshop.domain.identity import NonBlank
from agentic_workshop.domain.marketing import ContentAssignment, WeeklyMarketingBrief
from agentic_workshop.ports.content_generation import ContentDraftGenerator
from agentic_workshop.ports.models import (
    LanguageModel,
    ModelMalformedOutputError,
    ModelMessage,
    ModelRequest,
)

URL_PATTERN = re.compile(r"https?://[^\s<>()]+")
WEBSITE_HEADING = "A Story of Kindness, Courage, and Belonging."
FORMAT_STATEMENT = (
    "Jordan and the Fosters is available in paperback, hardcover, and digital editions."
)
INTERNAL_COPY_PATTERNS = (
    "draft note",
    "internal note",
    "workflow status",
    "approval state",
    "missing asset",
    "required asset",
    "no image",
    "text-only",
    "automated marketing",
)


class ModelDraftProposal(DomainModel):
    assignment: NonBlank
    channel: NonBlank
    title: NonBlank
    body: NonBlank
    approved_fact_ids: tuple[NonBlank, ...]

    @model_validator(mode="after")
    def require_unique_fact_ids(self) -> "ModelDraftProposal":
        if len(set(self.approved_fact_ids)) != len(self.approved_fact_ids):
            raise ValueError("approved fact IDs must be unique")
        return self


class ModelDraftBatch(DomainModel):
    drafts: tuple[ModelDraftProposal, ...]


class ModelContentDraftGenerator(ContentDraftGenerator):
    """Ask a model for copy while retaining provenance and draft state in application code."""

    def __init__(self, model: LanguageModel, *, instructions: str) -> None:
        self._model = model
        self._instructions = instructions

    async def generate(
        self,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        *,
        approved_brief_source: str,
        source_references: tuple[str, ...],
        missing_information: tuple[str, ...],
        required_assets: tuple[str, ...],
    ) -> DraftGenerationResult:
        request = ModelRequest(
            messages=(
                ModelMessage(role="system", content=self._instructions),
                ModelMessage(
                    role="user",
                    content=self._request_payload(brief, client),
                ),
            ),
            response_schema=ModelDraftBatch.model_json_schema(),
        )
        response = await self._model.complete(request)
        if response.structured_output is None:
            raise ModelMalformedOutputError(
                "model did not return structured content drafts",
                provider="configured-model",
            )
        try:
            proposals = ModelDraftBatch.model_validate(response.structured_output)
        except ValidationError:
            raise ModelMalformedOutputError(
                "model content drafts failed Pydantic validation",
                provider="configured-model",
            ) from None

        violations = self._batch_violations(
            proposals,
            brief,
            client,
            approved_brief_source,
        )
        if violations:
            raise ModelMalformedOutputError(
                "model content failed editorial validation: " + "; ".join(violations),
                provider="configured-model",
            )

        drafts = tuple(
            self._to_content_draft(
                proposal,
                client,
                approved_brief_source,
                missing_information,
                required_assets,
            )
            for proposal in proposals.drafts
        )
        metadata = response.provider_metadata
        return DraftGenerationResult(
            drafts=drafts,
            metadata=ContentGenerationMetadata(
                generator="language-model",
                model=self._optional_string(metadata.get("model")),
                response_id=self._optional_string(metadata.get("response_id")),
                usage=response.usage,
                latency_ms=self._non_negative_int(metadata.get("latency_ms")),
            ),
        )

    @classmethod
    def _request_payload(
        cls,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
    ) -> str:
        payload = {
            "task": (
                "Return exactly one draft for every assignment. Use only approved facts by "
                "fact ID. Follow all brand voice, prohibited claim, permission, quotation, link, "
                "editorial, and no-publication constraints. Public copy is only title and body; "
                "never put metadata or internal notes in it. Report only approved fact IDs; the "
                "application owns final source provenance."
            ),
            "brief": brief.model_dump(mode="json"),
            "client": {
                "identity": client.identity,
                "summary": client.summary,
                "author_story": client.author_story,
                "brand_voice": client.brand_voice,
                "audiences": [audience.model_dump(mode="json") for audience in client.audiences],
                "calls_to_action": client.calls_to_action,
                "purchase_links": [link.model_dump(mode="json") for link in client.purchase_links],
                "public_channels": [
                    channel.model_dump(mode="json") for channel in client.public_channels
                ],
                "approved_reviews": [
                    review.model_dump(mode="json") for review in client.approved_reviews
                ],
                "approved_facts": [
                    {
                        "fact_id": cls._fact_id(index),
                        "text": fact,
                    }
                    for index, fact in enumerate(client.approved_facts)
                ],
                "prohibited_claims": client.prohibited_claims,
                "marketing_permissions": client.marketing_permissions,
                "missing_information": client.missing_information,
                "deferred_information": client.deferred_information,
            },
            "editorial_contract": {
                "all_assignment_instructions_are_required_unless_explicitly_optional": True,
                "public_copy_excludes_internal_notes_and_asset_restrictions": True,
                "website_heading": WEBSITE_HEADING,
                "required_format_statement": FORMAT_STATEMENT,
                "social_word_range": {"minimum": 100, "maximum": 140},
                "one_call_to_action_and_purchase_url_per_draft": True,
                "application_derives_sources_from_validated_facts_and_copy": True,
                "channel_drafts_must_use_meaningfully_different_structures": True,
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _to_content_draft(
        cls,
        proposal: ModelDraftProposal,
        client: ClientProfile,
        approved_brief_source: str,
        missing_information: tuple[str, ...],
        required_assets: tuple[str, ...],
    ) -> ContentDraft:
        catalog = cls._fact_catalog(client)
        facts = tuple(catalog[fact_id] for fact_id in proposal.approved_fact_ids)
        sources = cls._derive_sources(
            proposal,
            client,
            approved_brief_source,
        )
        return ContentDraft(
            assignment=proposal.assignment,
            channel=proposal.channel,
            title=proposal.title,
            body=proposal.body,
            brand_voice_applied=client.brand_voice,
            approved_facts_used=facts,
            source_references=sources,
            missing_assets_or_information=missing_information,
            required_assets=required_assets,
        )

    @classmethod
    def _batch_violations(
        cls,
        batch: ModelDraftBatch,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        approved_brief_source: str,
    ) -> tuple[str, ...]:
        assignments = {
            (assignment.deliverable, assignment.channel): assignment
            for assignment in brief.content_assignments
        }
        violations: list[str] = []
        bodies: list[str] = []
        for proposal in batch.drafts:
            assignment = assignments.get((proposal.assignment, proposal.channel))
            if assignment is None:
                violations.append(f"{proposal.assignment}: unknown assignment")
                continue
            bodies.append(proposal.body)
            violations.extend(
                cls._draft_violations(
                    proposal,
                    assignment,
                    brief,
                    client,
                    approved_brief_source,
                )
            )
        if len(bodies) > 1 and cls._body_similarity(bodies[0], bodies[1]) > 0.75:
            violations.append("channel adaptation: public copy is excessively repetitive")
        return tuple(dict.fromkeys(violations))

    @classmethod
    def _draft_violations(
        cls,
        proposal: ModelDraftProposal,
        assignment: ContentAssignment,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        approved_brief_source: str,
    ) -> list[str]:
        label = assignment.deliverable
        body_lower = proposal.body.lower()
        violations = [
            f"{label}: public copy contains internal or asset-note language"
            for pattern in INTERNAL_COPY_PATTERNS
            if pattern in body_lower
        ]
        if "website" in assignment.deliverable.lower():
            required_heading = cls._required_heading(assignment)
            if proposal.title != required_heading:
                violations.append(f"{label}: required exact heading is missing")
            if FORMAT_STATEMENT not in proposal.body:
                violations.append(f"{label}: required edition format statement is missing")
        if "social" in assignment.channel.lower():
            minimum_words, maximum_words = cls._word_range(assignment)
            word_count = len(proposal.body.split())
            if not minimum_words <= word_count <= maximum_words:
                violations.append(
                    f"{label}: public copy must contain "
                    f"{minimum_words}-{maximum_words} words"
                )
        if proposal.body.count(brief.call_to_action) != 1:
            violations.append(f"{label}: call to action must appear exactly once")
        for link in client.purchase_links:
            if proposal.body.count(link.url) != 1:
                violations.append(f"{label}: approved purchase URL must appear exactly once")
        instructions_lower = assignment.instructions.lower()
        review_required = "review" in instructions_lower and not any(
            marker in instructions_lower for marker in ("may", "optional")
        )
        if review_required:
            for review in client.approved_reviews:
                if (
                    proposal.body.count(review.quote) != 1
                    or proposal.body.count(review.attribution) != 1
                ):
                    violations.append(
                        f"{label}: required review quotation or attribution is missing"
                    )
        try:
            cls._validate_urls(proposal.body, client)
            cls._validate_review_usage(proposal.body, client)
        except ModelMalformedOutputError as error:
            violations.append(f"{label}: {error}")
        unknown_fact_ids = set(proposal.approved_fact_ids) - set(cls._fact_catalog(client))
        if unknown_fact_ids:
            violations.append(f"{label}: an invented or unauthorized fact ID was reported")
        return violations

    @staticmethod
    def _required_heading(assignment: ContentAssignment) -> str:
        match = re.search(
            r'exact heading [“"](?P<heading>.+?)[”"]',
            assignment.instructions,
            flags=re.IGNORECASE,
        )
        return match.group("heading") if match is not None else WEBSITE_HEADING

    @staticmethod
    def _word_range(assignment: ContentAssignment) -> tuple[int, int]:
        match = re.search(
            r"between (?P<minimum>\d+) and (?P<maximum>\d+) words",
            assignment.instructions,
            flags=re.IGNORECASE,
        )
        if match is None:
            return 100, 140
        return int(match.group("minimum")), int(match.group("maximum"))

    @classmethod
    def _derive_sources(
        cls,
        proposal: ModelDraftProposal,
        client: ClientProfile,
        approved_brief_source: str,
    ) -> tuple[str, ...]:
        sources = [approved_brief_source]
        if proposal.approved_fact_ids:
            sources.append(client.source_reference)
        sources.extend(link.url for link in client.purchase_links if link.url in proposal.body)
        sources.extend(
            channel.url for channel in client.public_channels if channel.url in proposal.body
        )
        sources.extend(
            review.source_url
            for review in client.approved_reviews
            if review.quote in proposal.body or review.attribution in proposal.body
        )
        return tuple(dict.fromkeys(sources))

    @staticmethod
    def _fact_id(index: int) -> str:
        return f"fact-{index:03d}"

    @classmethod
    def _fact_catalog(cls, client: ClientProfile) -> dict[str, str]:
        return {
            cls._fact_id(index): fact for index, fact in enumerate(client.approved_facts)
        }

    @staticmethod
    def _body_similarity(left: str, right: str) -> float:
        return SequenceMatcher(None, left.lower(), right.lower()).ratio()

    @staticmethod
    def _validate_urls(body: str, client: ClientProfile) -> None:
        authorized = {
            *(link.url for link in client.purchase_links),
            *(channel.url for channel in client.public_channels),
            *(review.source_url for review in client.approved_reviews),
        }
        used = {match.rstrip(".,;:!?") for match in URL_PATTERN.findall(body)}
        if not used.issubset(authorized):
            raise ModelMalformedOutputError(
                "model draft contained an unauthorized URL",
                provider="configured-model",
            )

    @staticmethod
    def _validate_review_usage(body: str, client: ClientProfile) -> None:
        for review in client.approved_reviews:
            mentions_source = review.attribution in body or "Readers\u2019 Favorite" in body
            if mentions_source and (
                body.count(review.quote) != 1 or body.count(review.attribution) != 1
            ):
                raise ModelMalformedOutputError(
                    "model altered or misattributed an approved review",
                    provider="configured-model",
                )

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _non_negative_int(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0
