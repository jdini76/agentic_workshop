# Agentic Workshop

Agentic Workshop is an operating system for companies made of collaborating AI employees. The first
working vertical slices contain one company, its Marketing department, Marketing Strategist Sarah
Collins, Content Creator Casey, the client Jordan and the Fosters, and governed brief-to-content
workflows.

The workflow is deterministic and local. It does not call a language model, access a database, use a
queue, or publish content. Client gaps remain explicit instead of being filled with invented facts.

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

## Generate a weekly marketing brief

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

## Review a brief

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

## Generate a content package

Casey accepts only an approved weekly brief. First approve the brief as shown above, then run:

```shell
agentic-workshop content-package \
  artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json
```

The command writes draft JSON and Markdown under `artifacts/content-packages/`. Each requested
assignment is adapted to its channel, carries source references and missing-input flags, and remains
grounded in the matching client profile. A draft or revision-requested brief is rejected.

Content packages use the same explicit CEO review command:

```shell
agentic-workshop review \
  artifacts/content-packages/jordan-and-the-fosters-2026-08-03-content.json --approve

agentic-workshop review \
  artifacts/content-packages/jordan-and-the-fosters-2026-08-03-content.json \
  --request-revision "Tailor the email opening more closely to the approved brand voice."
```

Package approval is not publication authorization. No command in this slice publishes content.

## Version-controlled resources

Employee definitions, client profiles, prompts, SOPs, and policies live under
`src/agentic_workshop/resources/`. Structured definitions use JSON and validate against domain
models. Prompt text remains a resource and does not contain application control flow.
