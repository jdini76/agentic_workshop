# Model adapter architecture

## Decision and compatibility review

`LanguageModel` is an outbound application port. OpenAI, Anthropic Claude, Google Gemini, and local
Ollama can each be implemented as an independent adapter without changing Sarah, Casey, client
profiles, weekly briefs, content packages, or any other deliverable model.

That claim follows from two boundaries:

1. Sarah's and Casey's current deterministic application services do not depend on `LanguageModel`
   at all. A future model-assisted service would receive the port through constructor injection.
2. Provider request objects, response objects, errors, credentials, model names, and SDK imports stay
   inside adapter modules. Adapters translate to and from the normalized contracts in
   `ports/models.py`.

Adapters remain independently packaged; the first implementation is now:

```text
adapters/openai_language_model.py -> official OpenAI SDK and Responses API
adapters/models/anthropic.py  -> Anthropic SDK or HTTP API
adapters/models/gemini.py     -> Google Gen AI SDK or HTTP API
adapters/models/ollama.py     -> local Ollama HTTP API
```

The OpenAI adapter exists. The other adapters remain future work and require no employee or
deliverable changes.

## Provider-neutral request contract

`ModelRequest` contains:

- ordered `ModelMessage` values with a logical role and text content;
- JSON-Schema-compatible `ToolDefinition` values;
- an optional JSON Schema describing the requested structured response;
- a parameter map for portable settings or explicitly adapter-supported extensions.

An adapter maps logical messages into the provider's format. For example, Anthropic treats system
instructions separately, Gemini uses its own content/part representation, and Ollama capabilities
depend on the installed model and server version. Those differences are translation concerns. An
adapter must reject unsupported roles, tool features, schemas, or parameters before making a call;
it must never silently discard a requested constraint.

The parameter map is an escape hatch rather than a universal provider configuration model. Shared
parameters should acquire typed fields only after their semantics are genuinely portable. Each
adapter owns an allowlist and validation rules so misspelled or incompatible values fail early.

## Provider-neutral response contract

`ModelResponse` contains:

- `content`: normalized text, which may be empty for tool-only or structured responses;
- `structured_output`: parsed JSON-compatible data when a response schema was requested;
- normalized `ToolCall` values;
- portable integer usage counters where the provider reports them;
- quarantined `provider_metadata` for diagnostics that business code must not depend upon.

Provider stop reasons, safety data, cache counters, and request IDs may be retained in metadata.
Deliverable models must never read that metadata. Streaming returns normalized text chunks only.
Structured output and tool-call assembly use `complete`; a future richer stream event contract should
be introduced only when a real use case requires partial structured or tool events.

## Structured-output validation

Structured output is a boundary with two validation stages:

1. The adapter requests the strongest native structured-output mode the provider and selected model
   support. It parses the returned payload and validates it against `ModelRequest.response_schema`.
   A provider's claim that output is valid is not sufficient.
2. The application validates `ModelResponse.structured_output` against the exact Pydantic v2 domain
   or deliverable model before using or persisting it. JSON Schema conformance does not replace
   domain validation or cross-field invariants.

When a schema is supplied, returning prose disguised as JSON, invalid JSON, a schema mismatch, or a
missing structured payload raises `ModelMalformedOutputError`. Adapters must not repair factual
content, invent missing values, or coerce an invalid deliverable into validity. If repair is ever
added, it must be an explicit, bounded application use case whose attempts remain auditable.

Provider schema subsets vary. An adapter must perform capability validation before a request. It may
translate an equivalent schema, but it must fail if translation would weaken a required invariant.

## Normalized errors

Adapters translate SDK and HTTP failures into the following port exceptions:

| Normalized error | Meaning | Retry guidance |
|---|---|---|
| `ModelTimeoutError` | The configured total deadline elapsed. | Retry only under an explicit bounded policy and idempotency/cost budget. |
| `ModelAuthenticationError` | Credentials are missing, invalid, expired, or unauthorized. | Do not retry until configuration changes. |
| `ModelRateLimitError` | A request, token, concurrency, or account quota prevented service. | Honor `retry_after_seconds` when present and apply bounded backoff. |
| `ModelMalformedOutputError` | Output could not be parsed or did not satisfy the requested schema. | Do not retry blindly; an explicit bounded regeneration policy may retry. |
| `ModelUnavailableError` | The selected model is absent or unavailable to the account. | Select an available configured model; do not silently fall back. |
| `LanguageModelError` | Sanitized base for other provider failures. | The concrete adapter documents whether a subtype is retryable. |

Errors expose a provider identifier for observability but never include API keys, authorization
headers, full prompts, full responses, tool arguments, or client memory. Adapters should chain the
original exception internally while ensuring logs use the sanitized normalized error.

The configured timeout is a total operation deadline, not only a socket-read timeout. An adapter may
derive connect and read limits within it. Cancellation must propagate rather than being translated
into a retryable provider failure.

## Configuration and secrets

`ModelSettings` contains non-secret connection settings only. At explicit construction time, the
OpenAI adapter first checks the operating-system environment and then an optional repository-root
`.env` file used for local development:

```text
OPENAI_API_KEY=<injected-by-the-process-or-secret-manager>
OPENAI_MODEL=gpt-5.6-terra
```

Operating-system values take precedence over `.env`. The OpenAI credential is never accepted by CLI
flags or configuration models and is not retained by application objects. If it is absent, empty,
or an obvious placeholder, adapter construction fails before a client request. A local
Ollama adapter normally uses no key and receives an explicitly configured trusted endpoint. Secrets
must never enter domain models, prompts, events, artifacts, metadata, exceptions, fixtures, snapshots,
or logs. `.env` and `.env.*` are Git-ignored except for the empty `.env.example` template. Tests
disable repository discovery or provide an isolated temporary file, so they cannot consume a
developer credential.

Adapter-specific configuration should use typed, immutable settings owned by that adapter. Unknown
settings are rejected. Provider environment-variable conventions may be supported by the composition
root, but adapters must not read global environment state during import.

## Deterministic fake model

Application tests use a fake implementing `LanguageModel`, never a live or paid model. Its behavior
must be deterministic and explicit:

- tests enqueue exact `ModelResponse` values or normalized exceptions;
- each request is recorded in order for assertions;
- `complete` consumes exactly one queued outcome and fails clearly when none remains;
- `stream` emits an explicitly queued tuple of chunks without delays;
- no random text, current time, network, environment credentials, implicit retries, or heuristic
  prompt matching is allowed;
- structured responses still pass through the same application-level Pydantic validation as real
  adapter responses.

A separate contract-test suite will run against the fake and every provider adapter. Paid-provider
integration tests must be opt-in, credential-gated, budget-limited, and excluded from ordinary CI.

## Runtime adapter selection

Selection occurs only in the CLI composition root. Deterministic drafting is the default; OpenAI is
selected only with `--generator openai` or the explicitly named live-smoke command. Both routes also
require `--confirm-paid-call`. The CLI constructs `OpenAILanguageModel` and injects it into
`ModelContentDraftGenerator`; Sarah, Casey, and deliverable models remain unchanged. A broader future
registry can add:

```text
"openai"    -> OpenAIModelAdapter
"anthropic" -> AnthropicModelAdapter
"gemini"    -> GeminiModelAdapter
"ollama"    -> OllamaModelAdapter
"fake"      -> DeterministicFakeModel (tests only)
```

Unknown providers fail startup; there is no automatic fallback to another provider. Startup also
validates required credentials and endpoint policy. The constructed port is injected into
`RuntimeDependencies` and then into only the use cases that need inference. Switching providers is a
configuration and wiring change, not an employee or deliverable change.

This explicit factory is preferred over provider discovery by imports or a dependency-injection
framework: it makes installed optional dependencies, secret requirements, and the selected billing
boundary obvious. Provider packages may later be optional extras so an Ollama-only installation does
not install paid-provider SDKs.
