import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentic_workshop.adapters import openai_language_model
from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.adapters.model_content import ModelContentDraftGenerator
from agentic_workshop.adapters.openai_language_model import OpenAILanguageModel
from agentic_workshop.cli import PACKAGE_RESOURCE_ROOT, build_parser
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.marketing import WeeklyMarketingBrief
from agentic_workshop.ports.models import (
    LanguageModel,
    ModelAuthenticationError,
    ModelMalformedOutputError,
    ModelMessage,
    ModelRateLimitError,
    ModelRequest,
    ModelResponse,
    ModelTimeoutError,
    ModelUnavailableError,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
APPROVED_BRIEF_SOURCE = (
    "artifacts\\weekly-briefs\\jordan-and-the-fosters-2026-08-03.json"
)


class MockResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.parameters: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> object:
        self.parameters = kwargs
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class MockClient:
    def __init__(self, response: object) -> None:
        self.responses = MockResponses(response)


class StructuredFakeModel(LanguageModel):
    def __init__(self, structured_output: dict[str, Any]) -> None:
        self._structured_output = structured_output

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(content="{}", structured_output=self._structured_output)

    def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        async def empty() -> AsyncIterator[str]:
            if False:
                yield ""

        return empty()


def model_generator_inputs() -> tuple[WeeklyMarketingBrief, ClientProfile, str]:
    brief = WeeklyMarketingBrief.model_validate_json(
        Path(APPROVED_BRIEF_SOURCE).read_text(encoding="utf-8")
    )
    loader = FilesystemResourceLoader(PACKAGE_RESOURCE_ROOT)
    client = ClientProfile.model_validate_json(
        asyncio.run(loader.load_text("clients/jordan-and-the-fosters.v1.json"))
    )
    prompt = asyncio.run(loader.load_text("prompts/casey-content-creator.v1.md"))
    return brief, client, prompt


def fixture_payload(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_openai_adapter_uses_responses_structured_output_without_storage() -> None:
    payload = {"drafts": []}
    response = SimpleNamespace(
        status="completed",
        output_text=json.dumps(payload),
        model="gpt-5.6-sol",
        id="resp_test",
        usage=SimpleNamespace(input_tokens=12, output_tokens=8, total_tokens=20),
    )
    client = MockClient(response)
    model = OpenAILanguageModel(
        client,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=4000,
    )
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="test content"),),
        response_schema={"type": "object"},
    )

    result = asyncio.run(model.complete(request))

    assert result.structured_output == payload
    assert result.provider_metadata["response_id"] == "resp_test"
    assert result.usage == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    assert client.responses.parameters["store"] is False
    assert client.responses.parameters["model"] == "gpt-5.6-sol"
    assert client.responses.parameters["reasoning"] == {"effort": "medium"}
    assert client.responses.parameters["max_output_tokens"] == 4000
    assert client.responses.parameters["text"]["format"]["strict"] is True


def test_openai_adapter_rejects_malformed_structured_output() -> None:
    response = SimpleNamespace(
        status="completed",
        output_text="not-json",
        model="gpt-5.6-sol",
        id="resp_test",
        usage=None,
    )
    model = OpenAILanguageModel(
        MockClient(response),
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=4000,
    )
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="test content"),),
        response_schema={"type": "object"},
    )

    with pytest.raises(ModelMalformedOutputError):
        asyncio.run(model.complete(request))


@pytest.mark.parametrize(
    ("sdk_error_name", "normalized_error"),
    [
        ("AuthenticationError", ModelAuthenticationError),
        ("RateLimitError", ModelRateLimitError),
        ("APITimeoutError", ModelTimeoutError),
        ("NotFoundError", ModelUnavailableError),
    ],
)
def test_openai_adapter_normalizes_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
    sdk_error_name: str,
    normalized_error: type[Exception],
) -> None:
    class FakeSDKError(Exception):
        response = SimpleNamespace(headers={})

    monkeypatch.setattr(openai_language_model.openai, sdk_error_name, FakeSDKError)
    model = OpenAILanguageModel(
        MockClient(FakeSDKError("sensitive provider detail")),
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=4000,
    )
    request = ModelRequest(messages=(ModelMessage(role="user", content="test content"),))

    with pytest.raises(normalized_error) as captured:
        asyncio.run(model.complete(request))
    assert "sensitive provider detail" not in str(captured.value)


def test_missing_openai_key_fails_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelAuthenticationError, match="no OpenAI request was made"):
        OpenAILanguageModel.from_environment(
            model="gpt-5.6-sol",
            timeout_seconds=60,
            reasoning_effort="medium",
            max_output_tokens=4000,
            load_dotenv=False,
        )


def test_dotenv_loading_and_operating_system_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=dotenv-key\nOPENAI_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "operating-system-key")
    monkeypatch.setenv("OPENAI_MODEL", "operating-system-model")
    captured: dict[str, Any] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.responses = MockResponses(SimpleNamespace())

    monkeypatch.setattr(openai_language_model, "AsyncOpenAI", FakeAsyncOpenAI)
    model = OpenAILanguageModel.from_environment(
        model=None,
        timeout_seconds=60,
        reasoning_effort="low",
        max_output_tokens=4000,
        env_file=env_file,
    )

    assert captured["api_key"] == "operating-system-key"
    assert model._model == "operating-system-model"


@pytest.mark.parametrize("value", ["", "placeholder", "<OPENAI_API_KEY>"])
def test_dotenv_rejects_empty_and_placeholder_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"OPENAI_API_KEY={value}\n", encoding="utf-8")

    with pytest.raises(ModelAuthenticationError):
        OpenAILanguageModel.from_environment(
            model=None,
            timeout_seconds=60,
            reasoning_effort="low",
            max_output_tokens=4000,
            env_file=env_file,
        )


def test_cli_defaults_to_deterministic_and_requires_explicit_paid_confirmation() -> None:
    parser = build_parser()
    default = parser.parse_args(["content-package", "brief.json"])
    assert default.generator == "deterministic"
    assert default.confirm_paid_call is False

    with pytest.raises(SystemExit):
        parser.parse_args(["live-smoke-openai", "brief.json"])


def test_first_smoke_fixture_is_rejected_for_all_observed_editorial_failures() -> None:
    brief, client, prompt = model_generator_inputs()
    generator = ModelContentDraftGenerator(
        StructuredFakeModel(fixture_payload("openai_first_smoke_sanitized.json")),
        instructions=prompt,
    )
    sources = (*brief.source_references, client.source_reference, APPROVED_BRIEF_SOURCE)

    with pytest.raises(ModelMalformedOutputError) as captured:
        asyncio.run(
            generator.generate(
                brief,
                client,
                source_references=sources,
                missing_information=brief.missing_inputs,
                required_assets=brief.missing_inputs,
            )
        )

    message = str(captured.value)
    assert "internal or asset-note language" in message
    assert "required exact heading" in message
    assert "edition format statement" in message
    assert "100-140 words" in message
    assert "reported sources do not match" in message
    assert "same fact selection" in message


def test_corrected_website_and_social_fixtures_pass_editorial_validation() -> None:
    brief, client, prompt = model_generator_inputs()
    generator = ModelContentDraftGenerator(
        StructuredFakeModel(fixture_payload("openai_corrected_drafts.json")),
        instructions=prompt,
    )
    sources = (*brief.source_references, client.source_reference, APPROVED_BRIEF_SOURCE)

    result = asyncio.run(
        generator.generate(
            brief,
            client,
            source_references=sources,
            missing_information=brief.missing_inputs,
            required_assets=brief.missing_inputs,
        )
    )

    assert len(result.drafts) == 2
    assert all(draft.state == "draft" for draft in result.drafts)
    assert all("Draft note" not in draft.body for draft in result.drafts)
    assert 100 <= len(result.drafts[1].body.split()) <= 140


def test_required_review_and_duplicate_cta_rules_are_enforced() -> None:
    brief, client, prompt = model_generator_inputs()
    brief_data = brief.model_dump(mode="json")
    brief_data["content_assignments"][0]["instructions"] = brief_data[
        "content_assignments"
    ][0]["instructions"].replace("may be included", "must be included")
    required_review_brief = WeeklyMarketingBrief.model_validate(brief_data)
    payload = fixture_payload("openai_corrected_drafts.json")
    website = payload["drafts"][0]
    review = client.approved_reviews[0]
    website["body"] = website["body"].replace(f'"{review.quote}"\n— {review.attribution}\n\n', "")
    generator = ModelContentDraftGenerator(StructuredFakeModel(payload), instructions=prompt)
    sources = (
        *required_review_brief.source_references,
        client.source_reference,
        APPROVED_BRIEF_SOURCE,
    )

    with pytest.raises(ModelMalformedOutputError, match="required review quotation"):
        asyncio.run(
            generator.generate(
                required_review_brief,
                client,
                source_references=sources,
                missing_information=brief.missing_inputs,
                required_assets=brief.missing_inputs,
            )
        )

    duplicate_payload = fixture_payload("openai_corrected_drafts.json")
    social = duplicate_payload["drafts"][1]
    social["body"] += f"\n\n{brief.call_to_action}\n{client.purchase_links[0].url}"
    duplicate_generator = ModelContentDraftGenerator(
        StructuredFakeModel(duplicate_payload), instructions=prompt
    )
    with pytest.raises(ModelMalformedOutputError) as captured:
        asyncio.run(
            duplicate_generator.generate(
                brief,
                client,
                source_references=(
                    *brief.source_references,
                    client.source_reference,
                    APPROVED_BRIEF_SOURCE,
                ),
                missing_information=brief.missing_inputs,
                required_assets=brief.missing_inputs,
            )
        )
    assert "call to action must appear exactly once" in str(captured.value)
    assert "purchase URL must appear exactly once" in str(captured.value)


def test_unicode_fixture_round_trips_as_utf8_without_mojibake(tmp_path: Path) -> None:
    source = FIXTURE_ROOT / "openai_corrected_drafts.json"
    text = source.read_bytes().decode("utf-8", errors="strict")
    assert "children ages 3\N{EN DASH}8" in text
    assert "Jordan\N{RIGHT SINGLE QUOTATION MARK}s" in text
    assert "â€“" not in text
    assert "â€™" not in text

    target = tmp_path / "round-trip.json"
    target.write_text(text, encoding="utf-8", newline="\n")
    assert target.read_bytes().decode("utf-8", errors="strict") == text
