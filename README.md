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

### Governed client visual assets

Original client assets are local-only and live under
`assets/clients/<client-id>/originals/`. That directory is Git-ignored because originals may contain
private embedded metadata or have distribution rights that differ from the public source-code
license. Never download a substitute from a website, retailer, or search result.

The versioned manifest at
`src/agentic_workshop/resources/client-assets/jordan-and-the-fosters.v1.json` records the official
front cover's expected path, PNG dimensions, byte size, SHA-256 checksum, source, approval state,
allowed use, transformation permissions, attribution status, and restrictions. It deliberately does
not reproduce embedded Canva identifiers.

Validate the local inventory without modifying the original:

```console
agentic-workshop asset-inventory jordan-and-the-fosters --repository-root .
```

Review a draft or revision-requested manifest entry locally:

```console
agentic-workshop asset-review path/to/manifest.json ASSET_ID --repository-root . --approve
agentic-workshop asset-review path/to/manifest.json ASSET_ID --repository-root . \
  --request-revision "Revision instructions"
```

Approval first verifies path containment, file signature, format, dimensions, byte size, and checksum.
Casey may place an approved, verified asset ID in `ContentPackage.asset_recommendations`; this does
not embed, transform, upload, distribute, externally transmit, or publish the file. Missing or altered
originals produce an explicit unavailable recommendation, and text-only generation continues. There
is no website, retailer, or generated-image fallback. Clients with no manifest or no asset approved
for recommendation continue through the existing text-only workflow with an empty recommendation
list.

Future marketing derivatives must be stored separately from originals and require their own asset
ID, checksum, manifest entry, approval state, uses, restrictions, and provenance. The original must
never be overwritten or sanitized in place.

The CEO has confirmed ownership rights to the complete Jordan and the Fosters cover and authorized
its use for public book marketing on the official website, social posts, email marketing, and
campaign-package previews. This rights confirmation does not authorize automatic publication or
external delivery. The original remains local-only and Git-ignored.

A metadata-stripped 1576 × 1600 sRGB PNG is recorded as a separate approved derivative under
`assets/clients/jordan-and-the-fosters/derivatives/`. Its manifest state is `approved` for the
official website, social posts, email marketing, campaign previews, and Casey's package-metadata
recommendations. Casey recommends this metadata-clean derivative instead of the metadata-bearing
original. Publication and external delivery remain separate human-authorized actions.

Generate a static, local-only campaign preview from an approved content package:

```console
agentic-workshop campaign-preview artifacts/visual-enabled/2026-08-03/PACKAGE.json
```

Preview output is restricted to the ignored `artifacts/campaign-previews/` tree. The command rejects
unapproved packages, unapproved or invalid assets, original images, and channel-use mismatches. It
copies the validated metadata-clean derivative beside an escaped static HTML file; it does not embed
image bytes, start a server, contact external destinations, or expose publish/upload/send actions.
Existing previews are preserved unless `--overwrite` is supplied explicitly.

## Today's Work dashboard

Generate the read-only local workspace dashboard with one command:

```console
agentic-workshop todays-work
```

The command prints the path to a static `index.html` beneath the ignored
`artifacts/todays-work/` directory. Open that file locally to review the current campaign strategy,
Sarah and Casey approval states, website and social draft summaries, the validated marketing-cover
recommendation, and a link to the local campaign preview when one exists. The dashboard clearly
lists missing work and items needing attention instead of failing when optional campaign artifacts
are absent.

The dashboard is a local review aid only. It does not start a server, run a model, edit or approve
work, publish content, upload assets, or contact external destinations. Existing dashboards are
preserved; regenerate intentionally with:

```console
agentic-workshop todays-work --overwrite
```

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
