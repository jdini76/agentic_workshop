"""Replaceable content-drafting strategy contract."""

from abc import ABC, abstractmethod

from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import DraftGenerationResult
from agentic_workshop.domain.marketing import WeeklyMarketingBrief


class ContentDraftGenerator(ABC):
    """Generate drafts from the only authorized business inputs."""

    @abstractmethod
    async def generate(
        self,
        brief: WeeklyMarketingBrief,
        client: ClientProfile,
        *,
        source_references: tuple[str, ...],
        missing_information: tuple[str, ...],
        required_assets: tuple[str, ...],
    ) -> DraftGenerationResult:
        """Return one source-grounded draft for every brief assignment."""
