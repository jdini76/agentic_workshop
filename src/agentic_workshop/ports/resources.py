"""Externalized prompt and policy resource loading."""

from abc import ABC, abstractmethod


class ResourceLoader(ABC):
    @abstractmethod
    async def load_text(self, resource_ref: str) -> str:
        """Load a versionable text resource by logical reference."""

