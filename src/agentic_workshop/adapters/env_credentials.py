"""Shared OS-env-then-repository-.env credential loading for external adapters.

Every credentialed adapter (OpenAI, and now Facebook) follows the identical rule: operating-system
environment variables take precedence over an optional repository-root .env file, used only for
local development. Extracted here so a third or fourth adapter does not duplicate it again.
"""

import os
from pathlib import Path

from dotenv import dotenv_values

REJECTED_CREDENTIAL_VALUES = frozenset(
    {
        "changeme",
        "placeholder",
        "replace-me",
        "your-api-key",
        "your_api_key",
    }
)


def local_environment(
    env_file: Path | None, *, load_dotenv: bool = True
) -> dict[str, str | None]:
    if not load_dotenv:
        return {}
    path = env_file or repository_env_file(Path.cwd())
    if not path.is_file():
        return {}
    return dict(dotenv_values(path))


def repository_env_file(start: Path) -> Path:
    resolved = start.resolve()
    for directory in (resolved, *resolved.parents):
        if (directory / "pyproject.toml").is_file():
            return directory / ".env"
    return resolved / ".env"


def environment_value(name: str, local_values: dict[str, str | None]) -> str | None:
    if name in os.environ:
        return os.environ[name]
    return local_values.get(name)


def is_invalid_credential(value: str | None) -> bool:
    if value is None or not value.strip():
        return True
    normalized = value.strip().lower()
    return (
        normalized in REJECTED_CREDENTIAL_VALUES
        or "placeholder" in normalized
        or "your_api_key" in normalized
        or "your-api-key" in normalized
        or (normalized.startswith("<") and normalized.endswith(">"))
    )
