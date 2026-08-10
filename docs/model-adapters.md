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

## The Publisher port: the same pattern applied to outbound delivery

`Publisher` (`ports/publishing.py`) is the second application port built on this pattern, this time
for delivering approved content to an external destination rather than generating it. The same two
boundaries from the compatibility review above hold here too:

1. Content-review and approval logic (`application/content_review.py`) does not depend on
   `Publisher` at all; `ReviewContentPackage.approve()` stays a pure, network-free file transition.
   A separate orchestrator, `PublishApprovedContentPackage` (`application/publish_content_package.py`),
   receives the port through constructor injection and runs only after approval already succeeded.
2. Provider request objects, response objects, errors, credentials, and SDK/HTTP details stay inside
   the adapter module (`adapters/facebook_page_publisher.py`). The application and domain layers see
   only `PublishRequest`, `PublishResponse`, and the normalized `Publisher*Error` hierarchy.

The first implementation is `FacebookPagePublisher`, posting to a Meta Graph API Page. The second is
`WebsitePublisher` (`adapters/website_publisher.py`), covered in its own section below. Future
adapters (an Instagram adapter once a public image host exists; a TikTok adapter once TikTok audits
this integration) can be added the same way, without changing `PublishApprovedContentPackage` or
any deliverable model.

Since Facebook and the website are independent destinations, `PublishApprovedContentPackage`
publishes to each separately — one destination's failure, missing configuration, or missing
eligible draft never blocks the other's attempt, and each writes its own `PublicationRecord`
(`{client_id}-{week}-content-facebook_page.json` / `-website.json`). `execute()` returns a
`PublicationOutcome{facebook_page, website}` with both results, not a single record.

Normalized publisher errors mirror the model-port table above: `PublisherAuthenticationError`,
`PublisherRateLimitError` (with `retry_after_seconds`), `PublisherTimeoutError`,
`PublisherContentRejectedError` (policy/spam rejections), `PublisherMalformedResponseError`, and
`PublisherUnavailableError`. A failed publish never raises out of
`PublishApprovedContentPackage.execute()` — it is recorded as a `FAILED` `PublicationRecord` with a
sanitized `error_detail`, surfaced through Today's Work, and left for deliberate operator-initiated
retry rather than automatic retry (see the plan rationale on idempotency).

Credentials follow the same OS-env-then-repository-`.env` pattern as the OpenAI adapter, via the
shared `adapters/env_credentials.py` helper:

```text
AUTO_PUBLISH_ENABLED=true
FACEBOOK_PAGE_ID=<page-id>
FACEBOOK_PAGE_ACCESS_TOKEN=<injected-by-the-process-or-secret-manager>
FACEBOOK_GRAPH_API_VERSION=v21.0
```

If `AUTO_PUBLISH_ENABLED` is true but credentials are missing or an obvious placeholder, the
workspace does not fail to start — `FacebookPagePublisher.from_environment()` is attempted once at
startup, and if it raises `PublisherAuthenticationError` the orchestrator runs with no publisher
configured, recording a `SKIPPED` publication with a clear diagnostic on every approval instead.
This mirrors the model port's "fail fast at construction, never silently at request time" rule, but
applied so that an unconfigured integration degrades visibly rather than blocking the whole
workspace.

Live verification uses the same convention as `live-smoke-openai`: a `live-smoke-facebook-publish`
CLI command makes one real, explicitly confirmed call against production credentials. Because this
call is publicly visible rather than merely billed, it requires two confirmation flags
(`--confirm-live-post` and `--i-understand-this-posts-publicly`) instead of one.

## WebsitePublisher: git as the source of truth, cPanel's own API as the deploy trigger

The live site (`jordanandthefosters.fun`) is built with a proprietary drag-and-drop "Website
Builder" bundled with the host, not hand-editable files — so `WebsitePublisher` doesn't touch
`public_html` directly. Instead it treats a separate GitHub repository as the site's real source of
truth and lets cPanel's own **Git™ Version Control** feature pull and deploy from there:

1. Render the homepage (`presentation/website_site.py` — plain f-strings and `html.escape`, no
   templating dependency, same convention as `content_markdown.py` and `preview.py`), write it into
   a local git working copy, commit, and `git push` to GitHub over HTTPS using a token embedded in
   the remote URL (`https://x-access-token:{GITHUB_TOKEN}@github.com/...`) — no SSH key needed for
   this leg, so it works even on shared hosting where SSH/shell access is disabled.
2. Call cPanel's own **UAPI** (`VersionControlDeployment` module) over HTTPS on port 2083,
   authenticated with a cPanel API token via the `Authorization: cpanel {username}:{token}` header —
   a core cPanel API surface distinct from actual shell access, so it needs no SSH either. This
   queues a deploy, then polls until it reports `complete` or `failed`.

Git subprocess calls go through an injected `GitRunner` protocol (mirroring how `httpx.AsyncClient`
is injected into `FacebookPagePublisher`) wrapping the system `git` binary via `subprocess.run` in
`asyncio.to_thread` — no new dependency (`GitPython`, etc.) for what's a handful of straightforward
invocations, and easily faked in tests.

Only the weekly "current pitch" block (Casey's `official_website` draft: a title plus ~100-140
words) is regenerated on each publish — the surrounding static content (author bio, reviews,
contact links) comes from `resources/website/{client_id}.v1.json`, validated by
`domain/website_content.py`'s `WebsiteStaticContent`, and is carried over verbatim from what the CEO
already published by hand. It is deliberately outside Casey's governed-facts/approved-reviews
system — this is the CEO's own already-published content being preserved during the format
conversion, not new content Casey is generating or sourcing.

Credentials, same OS-env-then-`.env` pattern:

```text
GITHUB_TOKEN=<injected-by-the-process-or-secret-manager>
GITHUB_REPO=<owner>/<repo>
GITHUB_BRANCH=main
CPANEL_USERNAME=<cpanel-account-username>
CPANEL_API_TOKEN=<injected-by-the-process-or-secret-manager>
CPANEL_HOST=<cpanel-server-hostname>
CPANEL_GIT_REPO_NAME=<repo-name-as-registered-in-cPanel-Git-Version-Control>
WEBSITE_CANONICAL_URL=https://jordanandthefosters.fun
```

No separate enable flag: `WebsitePublisher` reuses `AUTO_PUBLISH_ENABLED`, and the same
credentials-missing-means-`SKIPPED` degradation as Facebook lets Facebook and the website be
configured independently and at different times.

Live verification: `live-smoke-website-publish` mirrors `live-smoke-facebook-publish`'s two-flag
pattern. Point the cPanel repo's Pull Directory at a throwaway staging path before ever repointing
it at `public_html` — the site is live and in active use, so nothing should land there unreviewed.

**Honest caveat, not silently assumed as fact:** the exact UAPI function names
(`VersionControlDeployment::create` / `::retrieve`) are the standard cPanel Git Version Control API
surface, but were not confirmed against this specific account's live API during implementation —
confirm against that account's self-documented `/execute/` endpoints before depending on this in
production.
