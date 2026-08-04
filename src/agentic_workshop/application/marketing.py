"""Deterministic weekly marketing brief generation."""

import json
from datetime import date, timedelta
from typing import TypeVar

from pydantic import ValidationError

from agentic_workshop.domain.assets import AssetRecommendation
from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.employee import Employee
from agentic_workshop.domain.identity import EmployeeId
from agentic_workshop.domain.marketing import (
    ContentAssignment,
    SuccessMetric,
    WeeklyMarketingBrief,
)
from agentic_workshop.ports.resources import ResourceLoader

SARAH_RESOURCE = "employees/sarah-collins.v1.json"
CLIENT_RESOURCE_TEMPLATE = "clients/{client_id}.v1.json"
ModelT = TypeVar("ModelT", bound=DomainModel)


class MarketingBriefError(Exception):
    """Base error for weekly brief generation."""


class IncompleteClientProfileError(MarketingBriefError):
    """Raised when strict generation encounters tracked missing information."""

    def __init__(self, client_id: str, missing_information: tuple[str, ...]) -> None:
        self.client_id = client_id
        self.missing_information = missing_information
        super().__init__(
            f"client profile {client_id!r} is incomplete: " + "; ".join(missing_information)
        )


class InvalidResourceError(MarketingBriefError):
    """Raised when a structured resource does not match its domain schema."""


class CampaignDirection(DomainModel):
    objective: str
    theme: str
    website_instructions: str
    social_instructions: str


class GenerateWeeklyMarketingBrief:
    """Build a source-grounded draft without model calls or publication side effects."""

    def __init__(
        self,
        resources: ResourceLoader,
        *,
        asset_recommendations: tuple[AssetRecommendation, ...] = (),
    ) -> None:
        self._resources = resources
        self._asset_recommendations = asset_recommendations

    async def execute(
        self, client_id: str, requested_date: date, *, strict: bool = False
    ) -> WeeklyMarketingBrief:
        employee = await self._load_employee()
        client_ref = CLIENT_RESOURCE_TEMPLATE.format(client_id=client_id)
        client = await self._load_client(client_ref)

        if strict and not client.is_complete:
            raise IncompleteClientProfileError(str(client.id), client.missing_information)

        # Ensure all governing resources exist. Their prose is never executed as business logic.
        for procedure in employee.procedures:
            await self._resources.load_text(procedure.resource_ref)
        await self._resources.load_text(employee.prompt_resource)

        week = requested_date - timedelta(days=requested_date.weekday())
        sources = (
            SARAH_RESOURCE,
            client_ref,
            employee.prompt_resource,
            *(procedure.resource_ref for procedure in employee.procedures),
            client.source_reference,
        )
        unique_sources = tuple(
            dict.fromkeys(
                (
                    *sources,
                    *(
                        recommendation.manifest_source
                        for recommendation in self._asset_recommendations
                    ),
                )
            )
        )

        audience = (
            "; ".join(profile.name for profile in client.audiences)
            if client.audiences
            else "Prospective readers; audience definition pending author approval"
        )
        call_to_action = (
            client.calls_to_action[0]
            if client.calls_to_action
            else "No public call to action until the author approves one"
        )

        if client.is_complete and client.public_channels:
            return self._public_brief(
                employee,
                client,
                week,
                call_to_action,
                unique_sources,
                self._asset_recommendations,
            )

        return self._readiness_brief(
            employee,
            client,
            week,
            audience,
            call_to_action,
            unique_sources,
        )

    @staticmethod
    def _public_brief(
        employee: Employee,
        client: ClientProfile,
        week: date,
        call_to_action: str,
        sources: tuple[str, ...],
        asset_recommendations: tuple[AssetRecommendation, ...],
    ) -> WeeklyMarketingBrief:
        channels = tuple(
            f"Official {channel.kind}" if channel.is_central_hub else channel.label
            for channel in client.public_channels
        )
        has_linked_social_channels = any(
            "social" in use.lower()
            for channel in client.public_channels
            for use in channel.approved_uses
        )
        if has_linked_social_channels:
            channels = (*channels, "Social channels linked from the official website")
        purchase_url = (
            client.purchase_links[0].url
            if client.purchase_links
            else "No approved purchase link"
        )
        available_assets = tuple(
            recommendation
            for recommendation in asset_recommendations
            if recommendation.availability == "available"
        )
        website_assets = GenerateWeeklyMarketingBrief._assets_for_use(
            available_assets, "official_website"
        )
        social_assets = GenerateWeeklyMarketingBrief._assets_for_use(
            available_assets, "social_posts"
        )
        shared_asset_available = bool(website_assets and social_assets)
        asset_id = (
            website_assets[0].asset_id
            if shared_asset_available
            else "No approved shared campaign asset"
        )
        deferred = tuple(
            item
            for item in client.deferred_information
            if not (
                shared_asset_available
                and "approved cover and illustrations" in item.lower()
            )
        )
        campaign = GenerateWeeklyMarketingBrief._campaign_direction(
            week,
            purchase_url,
        )
        if client.approved_reviews:
            review = client.approved_reviews[0]
            review_permission = (
                " The optional approved quotation is "
                f"\N{LEFT DOUBLE QUOTATION MARK}{review.quote}"
                f"\N{RIGHT DOUBLE QUOTATION MARK} — {review.attribution}. If used, reproduce "
                "the quotation and attribution exactly once."
            )
            brief_sources = tuple(dict.fromkeys((*sources, review.source_url)))
        else:
            review_permission = ""
            brief_sources = sources
        asset_assumptions: tuple[str, ...]
        if shared_asset_available:
            image_rules = (
                f" Recommend approved asset {asset_id}. Do not modify, transform, embed, upload, "
                "publish, or externally transmit the image."
            )
            rationale = (
                "The client profile has approved story facts, audiences, brand voice, calls to "
                "action, purchase and website links, an author story, and a sourced review. The "
                f"approved metadata-clean front-cover derivative {asset_id} is available for "
                "official website and social recommendations under its recorded permissions."
            )
            asset_assumptions = (
                (
                    "The approved metadata-clean front-cover derivative may be recommended in "
                    "assignment metadata for its permitted channel uses."
                ),
                (
                    "Recommendation does not authorize modifying, transforming, embedding, "
                    "uploading, publishing, or externally transmitting the image."
                ),
            )
        else:
            image_rules = " Do not add or select an image."
            rationale = (
                "The client profile has approved story facts, audiences, brand voice, calls to "
                "action, purchase and website links, an author story, and a sourced review. "
                "Visual assets remain deferred, so this campaign is text-only."
            )
            asset_assumptions = (
                "The campaign remains text-only until local visual assets receive CEO approval.",
            )

        return WeeklyMarketingBrief(
            client_id=client.id,
            employee_id=employee.id,
            week=week,
            objective=campaign.objective,
            audience=(
                "Parents and caregivers of children ages 3\N{EN DASH}8 who value warm animal "
                "stories and "
                "want age-appropriate ways to discuss kindness, patience, trust, and belonging."
            ),
            campaign_theme=campaign.theme,
            rationale=rationale,
            source_references=brief_sources,
            recommended_channels=tuple(dict.fromkeys(channels)),
            content_assignments=(
                ContentAssignment(
                    owner_id=EmployeeId("casey"),
                    deliverable="Official website campaign feature",
                    channel="Official website",
                    instructions=(
                        campaign.website_instructions + review_permission + image_rules
                    ),
                    asset_recommendations=website_assets,
                    asset_required_use=(
                        "official_website" if website_assets else None
                    ),
                ),
                ContentAssignment(
                    owner_id=EmployeeId("casey"),
                    deliverable="Social awareness post",
                    channel="Social channels linked from the official website",
                    instructions=campaign.social_instructions + image_rules,
                    asset_recommendations=social_assets,
                    asset_required_use="social_posts" if social_assets else None,
                ),
            ),
            call_to_action=call_to_action,
            success_metrics=(
                SuccessMetric(
                    name="CEO draft approval",
                    target="Both drafts approved as accurate and on-brand",
                ),
                SuccessMetric(
                    name="Unsupported claims or unapproved assets",
                    target="0",
                ),
                SuccessMetric(
                    name="Amazon destination engagement",
                    target="Trackable visits or clicks once channel analytics are available",
                ),
                SuccessMetric(
                    name="Meaningful audience responses",
                    target=(
                        "Comments or responses related to kindness, trust, fostering, or reading "
                        "with children, once published"
                    ),
                ),
            ),
            assumptions=(
                "No public content will be published from this brief.",
                "Only approved client facts, quotations, links, and calls to action may be used.",
                *asset_assumptions,
                (
                    "Teachers, librarians, rescue organizations, and foster families remain "
                    "approved audiences reserved for future campaigns."
                ),
                "Analytics-dependent measures remain pending until analytics are available.",
            ),
            missing_inputs=deferred,
        )

    @staticmethod
    def _assets_for_use(
        recommendations: tuple[AssetRecommendation, ...],
        required_use: str,
    ) -> tuple[AssetRecommendation, ...]:
        return tuple(
            recommendation
            for recommendation in recommendations
            if required_use in recommendation.permitted_uses
        )

    @staticmethod
    def _campaign_direction(week: date, purchase_url: str) -> CampaignDirection:
        if week.isocalendar().week % 2 == 1:
            return CampaignDirection(
                objective=(
                    "Help parents and caregivers of children ages 3\N{EN DASH}8 explore how "
                    "stories can introduce the idea that trust may take time, while encouraging "
                    "qualified visitors to view the available editions on Amazon."
                ),
                theme="Trust can grow through patience, kindness, and time.",
                website_instructions=(
                    "Create a warm official website feature titled "
                    "\N{LEFT DOUBLE QUOTATION MARK}When Trust Takes Time"
                    "\N{RIGHT DOUBLE QUOTATION MARK} that differs meaningfully from the August 3 "
                    "feature. Focus on how Jordan's cautious journey can help parents and "
                    "caregivers discuss patience and slowly developing trust with children ages "
                    "3\N{EN DASH}8. State exactly once that Jordan and the Fosters is available in "
                    "paperback, hardcover, and digital editions. End with exactly one approved "
                    "CTA and exactly one canonical Amazon URL "
                    f"({purchase_url})."
                ),
                social_instructions=(
                    "Create one social-awareness post of 100\N{EN DASH}140 words for parents and "
                    "caregivers of children ages 3\N{EN DASH}8. Focus on how stories can help "
                    "children understand that a cautious animal may need patience before trust "
                    "develops. Make the structure and wording meaningfully different from the "
                    "August 3 social post. Do not mention edition formats or use the review "
                    "quotation. End with exactly one approved CTA and exactly one canonical Amazon "
                    f"URL ({purchase_url}). Do not invent a platform, hashtag, or character limit."
                ),
            )
        return CampaignDirection(
            objective=(
                "Introduce Jordan and the Fosters to parents and caregivers of children ages "
                "3\N{EN DASH}8, build interest in its themes, and encourage qualified visitors "
                "to view the available editions on Amazon."
            ),
            theme="How patience and kindness help a cautious dog discover trust and belonging.",
            website_instructions=(
                "Use the exact heading \N{LEFT DOUBLE QUOTATION MARK}A Story of Kindness, Courage, "
                "and Belonging.\N{RIGHT DOUBLE QUOTATION MARK} Create concise website copy "
                "introducing Jordan's journey, the intended family audience, and the book's "
                "central themes. State exactly once that Jordan and the Fosters is available in "
                "paperback, hardcover, and digital editions. The approved review excerpt may be "
                "included exactly once with its required attribution. End with exactly one "
                f"approved CTA and exactly one canonical Amazon URL ({purchase_url})."
            ),
            social_instructions=(
                "Create one post of 100\N{EN DASH}140 words aimed specifically at parents and "
                "caregivers. Lead with a relatable question or observation about helping children "
                "understand patience and kindness toward cautious animals. Connect it to Jordan's "
                "journey. End with exactly one approved CTA and exactly one canonical Amazon URL "
                f"({purchase_url}). Do not invent a platform, hashtag, or character limit."
            ),
        )

    @staticmethod
    def _readiness_brief(
        employee: Employee,
        client: ClientProfile,
        week: date,
        audience: str,
        call_to_action: str,
        sources: tuple[str, ...],
    ) -> WeeklyMarketingBrief:
        campaign_theme = (
            client.themes[0]
            if client.themes
            else "Campaign readiness and approved-information collection"
        )

        return WeeklyMarketingBrief(
            client_id=client.id,
            employee_id=employee.id,
            week=week,
            objective=(
                client.marketing_goals[0]
                if client.marketing_goals
                else "Prepare the minimum approved inputs required for a public campaign"
            ),
            audience=audience,
            campaign_theme=campaign_theme,
            rationale=(
                "The client profile contains unresolved source gaps. This brief therefore limits "
                "work to internal campaign preparation and requests approval before public use."
            ),
            source_references=sources,
            recommended_channels=("Internal author review",),
            content_assignments=(
                ContentAssignment(
                    owner_id=employee.id,
                    deliverable="Author information request",
                    channel="Internal author review",
                    instructions=(
                        "Request every item listed under missing inputs; record responses in a new "
                        "version of the client profile before drafting public copy."
                    ),
                ),
                ContentAssignment(
                    owner_id=employee.id,
                    deliverable="Approved-claims ledger",
                    channel="Internal planning",
                    instructions=(
                        "Map each future campaign statement to an approved fact and its source. "
                        "Do not draft claims that lack approval."
                    ),
                ),
            ),
            call_to_action=call_to_action,
            success_metrics=(
                SuccessMetric(
                    name="Missing inputs resolved",
                    target=f"Resolve {len(client.missing_information)} of "
                    f"{len(client.missing_information)} tracked items",
                ),
                SuccessMetric(
                    name="Unsupported public claims",
                    target="0",
                ),
            ),
            assumptions=(
                "No public content will be published from this brief.",
                "Only facts in approved_facts may support future marketing claims.",
            ),
            missing_inputs=client.missing_information,
        )

    async def _load_employee(self) -> Employee:
        return await self._load_model(SARAH_RESOURCE, Employee)

    async def _load_client(self, resource_ref: str) -> ClientProfile:
        return await self._load_model(resource_ref, ClientProfile)

    async def _load_model(
        self, resource_ref: str, model_type: type[ModelT]
    ) -> ModelT:
        raw = await self._resources.load_text(resource_ref)
        try:
            return model_type.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as error:
            raise InvalidResourceError(f"invalid resource {resource_ref!r}: {error}") from error
