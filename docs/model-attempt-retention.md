# Local model-attempt retention

Every completed live-model response is written atomically to an `attempts/` directory before its
structured output enters application validation. These records are typed
`untrusted_model_attempt`; they are diagnostic evidence, not `ContentPackage` deliverables. They
cannot be approved, rendered as publishable Markdown, imported by the normal review workflow, or
published.

Attempt records contain model-generated client copy. Keep them local, restrict filesystem access,
and delete them according to the operator's local data-retention policy when they are no longer
needed for diagnosis. The entire `artifacts/` tree is Git-ignored. Records contain normalized model
metadata and structured output only; they must never contain prompts, credentials, environment
variables, authorization headers, or raw SDK/HTTP diagnostic objects.

`received` means a complete provider response was persisted but has not passed the current local
validators. Validation atomically changes the record to `accepted` with a separate draft-package
path, or `rejected` with exact validation errors. Revalidation uses the retained structured output
and current validators without provider access; any resulting package remains a draft.
