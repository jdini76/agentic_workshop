"""Filesystem resource adapter constrained to a configured root."""

import asyncio
from pathlib import Path

from agentic_workshop.ports.resources import ResourceLoader


class ResourceLoadingError(Exception):
    """Base error for filesystem resource access."""


class UnsafeResourcePathError(ResourceLoadingError):
    """Raised when a resource reference could escape the configured root."""


class ResourceNotFoundError(ResourceLoadingError):
    """Raised when a safe resource reference does not identify a file."""


class FilesystemResourceLoader(ResourceLoader):
    """Read UTF-8 files while rejecting absolute paths, traversal, and symlink escape."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError(f"resource root is not a directory: {root}")

    async def load_text(self, resource_ref: str) -> str:
        if not resource_ref or Path(resource_ref).is_absolute():
            raise UnsafeResourcePathError(f"unsafe resource reference: {resource_ref!r}")

        candidate = (self._root / resource_ref).resolve(strict=False)
        if not candidate.is_relative_to(self._root):
            raise UnsafeResourcePathError(f"resource escapes configured root: {resource_ref!r}")
        if not candidate.is_file():
            raise ResourceNotFoundError(f"resource does not exist: {resource_ref!r}")

        # Resolve again after the file check to protect against links escaping the root.
        resolved_file = candidate.resolve(strict=True)
        if not resolved_file.is_relative_to(self._root):
            raise UnsafeResourcePathError(f"resource symlink escapes root: {resource_ref!r}")
        return await asyncio.to_thread(resolved_file.read_text, encoding="utf-8")

