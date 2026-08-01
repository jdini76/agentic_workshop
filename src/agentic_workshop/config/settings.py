"""Environment-backed configuration with secret-safe representations."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = "unconfigured"
    model: str = "unconfigured"
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)


class ObservabilitySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_logs: bool = True
    service_name: str = "agentic-workshop"


class Settings(BaseSettings):
    """Composition-root settings; nested values use AW_ and __ delimiters."""

    model_config = SettingsConfigDict(
        env_prefix="AW_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="forbid",
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    resource_root: Path = Path("resources")
    model: ModelSettings = ModelSettings()
    observability: ObservabilitySettings = ObservabilitySettings()

