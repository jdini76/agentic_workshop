"""Deterministic weekly marketing brief generation."""

import json
from datetime import date, timedelta
from typing import TypeVar

from pydantic import ValidationError

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.employee import Employee
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


class GenerateWeeklyMarketingBrief:
    """Build a source-grounded draft without model calls or publication side effects."""

    def __init__(self, resources: ResourceLoader) -> None:
        self._resources = resources

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
        unique_sources = tuple(dict.fromkeys(sources))

        audience = (
            "; ".join(profile.name for profile in client.audiences)
            if client.audiences
            else "Prospective readers; audience definition pending author approval"
        )
        campaign_theme = (
            client.themes[0]
            if client.themes
            else "Campaign readiness and approved-information collection"
        )
        channel_information_missing = any(
            "website" in item.lower()
            or "mailing list" in item.lower()
            or "social channels" in item.lower()
            for item in client.missing_information
        )
        public_channels = (
            () if channel_information_missing else ("Website", "Mailing list", "Social media")
        )
        recommended_channels = public_channels or ("Internal author review",)
        call_to_action = (
            client.calls_to_action[0]
            if client.calls_to_action
            else "No public call to action until the author approves one"
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
            source_references=unique_sources,
            recommended_channels=recommended_channels,
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
