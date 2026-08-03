import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agentic_workshop.adapters import openai_language_model
from agentic_workshop.adapters.openai_language_model import OpenAILanguageModel
from agentic_workshop.cli import build_parser
from agentic_workshop.ports.models import (
    ModelAuthenticationError,
    ModelMalformedOutputError,
    ModelMessage,
    ModelRateLimitError,
    ModelRequest,
    ModelTimeoutError,
    ModelUnavailableError,
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
        )


def test_cli_defaults_to_deterministic_and_requires_explicit_paid_confirmation() -> None:
    parser = build_parser()
    default = parser.parse_args(["content-package", "brief.json"])
    assert default.generator == "deterministic"
    assert default.confirm_paid_call is False

    with pytest.raises(SystemExit):
        parser.parse_args(["live-smoke-openai", "brief.json"])
