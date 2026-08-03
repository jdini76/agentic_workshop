# Agentic Workshop

Agentic Workshop is an operating system for companies made of collaborating AI employees. The first
working vertical slices contain one company, its Marketing department, Marketing Strategist Sarah
Collins, Content Creator Casey, the client Jordan and the Fosters, and governed brief-to-content
workflows.

The workflow is deterministic and local by default. An explicitly gated OpenAI drafting adapter is
available, but no command publishes content. Client gaps remain explicit instead of being filled
with invented facts.

See [the architecture guide](docs/architecture.md) and [implementation roadmap](docs/roadmap.md).

## Requirements and installation

- Python 3.13 or newer

Install the package in an isolated environment:

```shell
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# POSIX shells: source .venv/bin/activate
python -m pip install -e .
```

For development checks:

```shell
python -m pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

## Complete Sarah to Casey workflow

The complete governed workflow has two independent approval gates. Neither approval publishes
anything.

### 1. Sarah creates the weekly brief

```shell
agentic-workshop brief jordan-and-the-fosters --week-of 2026-08-03
```

The requested date may be any day; it is normalized to that week's Monday. The command writes both
JSON and Markdown under `artifacts/weekly-briefs/`. The result is always a draft and never triggers
publication.

Strict mode refuses to generate while the client profile tracks missing information:

```shell
agentic-workshop brief jordan-and-the-fosters --week-of 2026-08-03 --strict
```

### 2. The CEO reviews Sarah's brief

Approve a planning artifact:

```shell
agentic-workshop review artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json --approve
```

Request a revision, for which instructions are mandatory:

```shell
agentic-workshop review artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json \
  --request-revision "Clarify the information request before approval."
```

Review updates the JSON and its paired Markdown representation. Approval remains review state only;
it does not authorize or perform external publication.

### 3. Casey creates the content package

Casey accepts only an approved weekly brief. First approve the brief as shown above, then run:

```shell
agentic-workshop content-package \
  artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json
```

The command writes draft JSON and Markdown under `artifacts/content-packages/`. Each requested
assignment is adapted to its channel, carries source references and missing-input flags, and remains
grounded in the matching client profile. A draft or revision-requested brief is rejected.

### 4. The CEO reviews Casey's package

Content packages use the same explicit review command:

```shell
agentic-workshop review \
  artifacts/content-packages/jordan-and-the-fosters-2026-08-03-content.json --approve

agentic-workshop review \
  artifacts/content-packages/jordan-and-the-fosters-2026-08-03-content.json \
  --request-revision "Tailor the email opening more closely to the approved brand voice."
```

Package approval is not publication authorization. No command in this slice publishes content.

Content drafting defaults to an async deterministic adapter. The application service depends on
the provider-neutral `ContentDraftGenerator` port and independently enforces approval, assignment
coverage, client matching, approved-fact provenance, brand voice, and source references. A future
`LanguageModel`-backed generator without changing domain models or the CEO review workflow.

## Optional OpenAI draft generation

The official OpenAI Python SDK and Responses API are available behind the provider-neutral
`LanguageModel` port. Deterministic generation remains the default. OpenAI must be selected
explicitly and every paid request requires `--confirm-paid-call`.

For local development, copy the tracked template and add the credential only to the ignored file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` locally:

```dotenv
OPENAI_API_KEY=<your local key>
OPENAI_MODEL=gpt-5.6-terra
```

Do not commit or share `.env`. The repository-root file is loaded only when OpenAI is explicitly
selected. An operating-system `OPENAI_API_KEY` or `OPENAI_MODEL` overrides the corresponding `.env`
value. Production deployments should use operating-system environment injection or a secret manager.
The key is never accepted in a CLI argument or written to an artifact, prompt, fixture, snapshot, or
log. Empty and obvious placeholder values fail before making a request. `.env` and `.env.*` are
Git-ignored except for `.env.example`.

The explicitly named smoke test writes a new draft package under
`artifacts/live-smoke/openai/`; it cannot overwrite the deterministic baseline:

```shell
agentic-workshop live-smoke-openai \
  artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json \
  --model gpt-5.6-sol --reasoning-effort medium \
  --max-output-tokens 4000 --timeout-seconds 60 --confirm-paid-call
```

It makes one Responses API request and records only the model name, response ID, token usage, and
latency alongside the draft. Prompts and credentials are not recorded. The same adapter can be
selected with `content-package --generator openai --confirm-paid-call`; model output goes to
`artifacts/model-content-packages/` by default. All generated packages remain drafts and retain the
same approval, factual-provenance, assignment-coverage, URL, and review-quotation validation gates.

Every completed live response is first retained as a local, untrusted diagnostic JSON record under
the selected artifact root's `attempts/` directory. See
[the retention policy](docs/model-attempt-retention.md). Revalidate one with current local validators
without an API call:

```shell
agentic-workshop revalidate-attempt <attempt.json> <approved-brief.json>
```

Revalidation writes a separate draft package and never approves or publishes it.

## Version-controlled resources

Employee definitions, client profiles, prompts, SOPs, and policies live under
`src/agentic_workshop/resources/`. Structured definitions use JSON and validate against domain
models. Prompt text remains a resource and does not contain application control flow.
