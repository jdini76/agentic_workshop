from agentic_workshop.config import Settings


def test_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.model.provider == "unconfigured"

