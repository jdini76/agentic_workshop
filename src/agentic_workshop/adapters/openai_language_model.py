"""OpenAI Responses API adapter for the provider-neutral language-model port."""

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, cast

import openai
from openai import AsyncOpenAI

from agentic_workshop.adapters.env_credentials import (
    environment_value,
    is_invalid_credential,
    local_environment,
)
from agentic_workshop.adapters.model_attempts import ModelAttemptRecorder
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

OPENAI_PROVIDER = os.getenv("OPENAI_PROVIDER", "openai")
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")


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
        attempt_recorder: ModelAttemptRecorder | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._attempt_recorder = attempt_recorder

    @classmethod
    def from_environment(
        cls,
        *,
        model: str | None,
        timeout_seconds: float,
        reasoning_effort: str,
        max_output_tokens: int,
        load_dotenv: bool = True,
        env_file: Path | None = None,
        attempt_recorder: ModelAttemptRecorder | None = None,
    ) -> "OpenAILanguageModel":
        local_values = local_environment(env_file, load_dotenv=load_dotenv)
        api_key = environment_value("OPENAI_API_KEY", local_values)
        if is_invalid_credential(api_key):
            raise ModelAuthenticationError(
                "OPENAI_API_KEY is absent, empty, or a placeholder; no OpenAI request was made",
                provider=OPENAI_PROVIDER,
            )
        assert api_key is not None
        selected_model = (
            model
            or environment_value("OPENAI_MODEL", local_values)
            or DEFAULT_OPENAI_MODEL
        )
        client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )
        return cls(
            cast(OpenAIClient, client),
            model=selected_model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            attempt_recorder=attempt_recorder,
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
        content = str(getattr(response, "output_text", ""))
        usage = self._normalized_usage(getattr(response, "usage", None))
        metadata = {
            "provider": OPENAI_PROVIDER,
            "model": str(getattr(response, "model", self._model)),
            "response_id": str(getattr(response, "id", "unknown-response")),
            "latency_ms": latency_ms,
        }
        self._record_completed_response(content, usage, metadata)
        if getattr(response, "status", "completed") != "completed":
            self._record_rejected(("OpenAI response did not complete",))
            raise ModelMalformedOutputError(
                "OpenAI response did not complete",
                provider=OPENAI_PROVIDER,
            )
        try:
            structured_output = self._parse_structured_output(content, request)
        except ModelMalformedOutputError as error:
            self._record_rejected((str(error),))
            raise
        return ModelResponse(
            content=content,
            structured_output=structured_output,
            usage=usage,
            provider_metadata=metadata,
        )

    def _record_completed_response(
        self,
        content: str,
        usage: dict[str, int],
        metadata: dict[str, Any],
    ) -> None:
        if self._attempt_recorder is None:
            return
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            value = {"unparsed_output": content}
        raw_output = value if isinstance(value, dict) else {"unparsed_output": value}
        self._attempt_recorder.received(
            provider=OPENAI_PROVIDER,
            model=str(metadata["model"]),
            response_id=str(metadata["response_id"]),
            usage=usage,
            latency_ms=int(metadata["latency_ms"]),
            raw_structured_output=raw_output,
        )

    def _record_rejected(self, errors: tuple[str, ...]) -> None:
        if self._attempt_recorder is not None:
            self._attempt_recorder.rejected(errors)

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
