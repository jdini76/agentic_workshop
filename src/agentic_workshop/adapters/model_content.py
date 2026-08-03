"""Language-model-backed content drafting with application-owned validation."""

import json
import re

from pydantic import ValidationError, model_validator

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import (
    ContentDraft,
    ContentGenerationMetadata,
    DraftGenerationResult,
)
from agentic_workshop.domain.identity import NonBlank
from agentic_workshop.domain.marketing import WeeklyMarketingBrief
from agentic_workshop.ports.content_generation import ContentDraftGenerator
from agentic_workshop.ports.models import (
    LanguageModel,
    ModelMalformedOutputError,
    ModelMessage,
    ModelRequest,
)

URL_PATTERN = re.compile(r"https?://[^\s<>()]+")


class ModelDraftProposal(DomainModel):
    assignment: NonBlank
    channel: NonBlank
    title: NonBlank
    body: NonBlank
    approved_fact_indexes: tuple[int, ...]

    @model_validator(mode="after")
    def require_unique_non_negative_indexes(self) -> "ModelDraftProposal":
        if any(index < 0 for index in self.approved_fact_indexes):
            raise ValueError("approved fact indexes cannot be negative")
        if len(set(self.approved_fact_indexes)) != len(self.approved_fact_indexes):
            raise ValueError("approved fact indexes must be unique")
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

        authorized_sources = self._authorized_sources(client, source_references)
        drafts = tuple(
            self._to_content_draft(
                proposal,
                client,
                authorized_sources,
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

    @staticmethod
    def _request_payload(brief: WeeklyMarketingBrief, client: ClientProfile) -> str:
        payload = {
            "task": (
                "Return exactly one draft for every assignment. Use only approved_facts by "
                "index. Follow all brand voice, prohibited claim, permission, quotation, link, "
                "missing asset, and no-publication constraints. Content remains draft."
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
                "approved_facts": list(enumerate(client.approved_facts)),
                "prohibited_claims": client.prohibited_claims,
                "marketing_permissions": client.marketing_permissions,
                "missing_information": client.missing_information,
                "deferred_information": client.deferred_information,
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _to_content_draft(
        cls,
        proposal: ModelDraftProposal,
        client: ClientProfile,
        authorized_sources: tuple[str, ...],
        missing_information: tuple[str, ...],
        required_assets: tuple[str, ...],
    ) -> ContentDraft:
        try:
            facts = tuple(
                client.approved_facts[index] for index in proposal.approved_fact_indexes
            )
        except IndexError:
            raise ModelMalformedOutputError(
                "model cited an unknown approved fact index",
                provider="configured-model",
            ) from None
        cls._validate_urls(proposal.body, client)
        cls._validate_review_usage(proposal.body, client)
        return ContentDraft(
            assignment=proposal.assignment,
            channel=proposal.channel,
            title=proposal.title,
            body=proposal.body,
            brand_voice_applied=client.brand_voice,
            approved_facts_used=facts,
            source_references=authorized_sources,
            missing_assets_or_information=missing_information,
            required_assets=required_assets,
        )

    @staticmethod
    def _authorized_sources(
        client: ClientProfile, source_references: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *source_references,
                    *(link.url for link in client.purchase_links),
                    *(channel.url for channel in client.public_channels),
                    *(review.source_url for review in client.approved_reviews),
                )
            )
        )

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
