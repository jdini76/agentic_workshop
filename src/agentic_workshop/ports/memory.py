"""Storage-neutral employee memory contract."""

from abc import ABC, abstractmethod

from agentic_workshop.domain.identity import EmployeeId, MemoryId
from agentic_workshop.domain.memory import MemoryMatch, MemoryQuery, MemoryRecord


class MemoryStore(ABC):
    @abstractmethod
    async def remember(self, record: MemoryRecord) -> None: ...

    @abstractmethod
    async def recall(self, owner_id: EmployeeId, query: MemoryQuery) -> tuple[MemoryMatch, ...]: ...

    @abstractmethod
    async def forget(self, memory_id: MemoryId) -> bool: ...

