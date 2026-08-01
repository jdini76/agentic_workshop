"""Event publication and subscription contracts."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from agentic_workshop.domain.events import DomainEvent


class EventPublisher(ABC):
    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Publish a fact at least once; implementations document delivery semantics."""


class EventSubscription(ABC):
    @abstractmethod
    def subscribe(self, *event_types: str) -> AsyncIterator[DomainEvent]:
        """Stream matching events until the consumer closes the iterator."""

