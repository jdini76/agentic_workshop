"""Composition-root contract; concrete adapter wiring arrives with adapters."""

from collections.abc import Callable

from agentic_workshop.application.runtime import RuntimeDependencies
from agentic_workshop.config import Settings

DependencyFactory = Callable[[Settings], RuntimeDependencies]


def bootstrap(settings: Settings, dependency_factory: DependencyFactory) -> RuntimeDependencies:
    """Build a runtime explicitly, making wiring replaceable in tests and deployments."""
    return dependency_factory(settings)

