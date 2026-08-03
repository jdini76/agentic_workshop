"""OpenAI Responses API adapter for the provider-neutral language-model port."""

import json
import os
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any, Protocol, cast

import openai
from openai import AsyncOpenAI

from agentic_workshop.ports.models import (
    LanguageModel,
    LanguageModelError,
    ModelAuthenticationError,
    ModelMalformedOutputError,
    ModelRateLimitError,
    ModelRequest,
    ModelResponse,
    ModelTimeoutError,
    ModelUnavailableError,
)

OPENAI_PROVIDER = "openai"


class ResponsesResource(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class OpenAIClient(Protocol):
    responses: ResponsesResource


class OpenAILanguageModel(LanguageModel):
    """Translate normalized requests to one non-persistent OpenAI Responses call."""

    def __init__(
        self,
        client: OpenAIClient,
        *,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
    ) -> None:
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens

    @classmethod
    def from_environment(
        cls,
        *,
        model: str,
        timeout_seconds: float,
        reasoning_effort: str,
        max_output_tokens: int,
    ) -> "OpenAILanguageModel":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ModelAuthenticationError(
                "OPENAI_API_KEY is not set; no OpenAI request was made",
                provider=OPENAI_PROVIDER,
            )
        client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )
        return cls(
            cast(OpenAIClient, client),
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        started = perf_counter()
        parameters: dict[str, Any] = {
            "model": self._model,
            "input": [message.model_dump(mode="json") for message in request.messages],
            "max_output_tokens": self._max_output_tokens,
            "reasoning": {"effort": self._reasoning_effort},
            "store": False,
        }
        if request.response_schema is not None:
            parameters["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "agentic_workshop_response",
                    "schema": request.response_schema,
                    "strict": True,
                }
            }

        try:
            response = await self._client.responses.create(**parameters)
        except openai.AuthenticationError:
            raise ModelAuthenticationError(
                "OpenAI authentication failed",
                provider=OPENAI_PROVIDER,
            ) from None
        except openai.RateLimitError as error:
            retry_after = self._retry_after_seconds(error)
            raise ModelRateLimitError(
                "OpenAI rate limit reached",
                provider=OPENAI_PROVIDER,
                retry_after_seconds=retry_after,
            ) from None
        except openai.APITimeoutError:
            raise ModelTimeoutError(
                "OpenAI request timed out",
                provider=OPENAI_PROVIDER,
            ) from None
        except openai.NotFoundError:
            raise ModelUnavailableError(
                "The selected OpenAI model is unavailable",
                provider=OPENAI_PROVIDER,
            ) from None
        except openai.BadRequestError as error:
            if getattr(error, "code", None) == "model_not_found":
                raise ModelUnavailableError(
                    "The selected OpenAI model is unavailable",
                    provider=OPENAI_PROVIDER,
                ) from None
            raise LanguageModelError(
                "OpenAI rejected the normalized request",
                provider=OPENAI_PROVIDER,
            ) from None
        except openai.APIResponseValidationError:
            raise ModelMalformedOutputError(
                "OpenAI returned a malformed SDK response",
                provider=OPENAI_PROVIDER,
            ) from None
        except openai.APIConnectionError:
            raise LanguageModelError(
                "OpenAI could not be reached",
                provider=OPENAI_PROVIDER,
            ) from None

        latency_ms = round((perf_counter() - started) * 1000)
        if getattr(response, "status", "completed") != "completed":
            raise ModelMalformedOutputError(
                "OpenAI response did not complete",
                provider=OPENAI_PROVIDER,
            )
        content = str(getattr(response, "output_text", ""))
        structured_output = self._parse_structured_output(content, request)
        usage = self._normalized_usage(getattr(response, "usage", None))
        return ModelResponse(
            content=content,
            structured_output=structured_output,
            usage=usage,
            provider_metadata={
                "model": str(getattr(response, "model", self._model)),
                "response_id": str(getattr(response, "id", "")),
                "latency_ms": latency_ms,
            },
        )

    def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        async def one_response() -> AsyncIterator[str]:
            response = await self.complete(request)
            yield response.content

        return one_response()

    @staticmethod
    def _parse_structured_output(
        content: str, request: ModelRequest
    ) -> dict[str, Any] | None:
        if request.response_schema is None:
            return None
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            raise ModelMalformedOutputError(
                "OpenAI structured output was not valid JSON",
                provider=OPENAI_PROVIDER,
            ) from None
        if not isinstance(parsed, dict):
            raise ModelMalformedOutputError(
                "OpenAI structured output was not a JSON object",
                provider=OPENAI_PROVIDER,
            )
        return parsed

    @staticmethod
    def _normalized_usage(usage: Any) -> dict[str, int]:
        if usage is None:
            return {}
        normalized: dict[str, int] = {}
        for source, target in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = getattr(usage, source, None)
            if isinstance(value, int):
                normalized[target] = value
        return normalized

    @staticmethod
    def _retry_after_seconds(error: openai.RateLimitError) -> float | None:
        value = error.response.headers.get("retry-after")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

