# Technical debt register

## TD-001: Client resource filename and profile revision ambiguity

- **Status:** Deferred before public release
- **Affected resource:** `clients/jordan-and-the-fosters.v1.json`
- **Observed internal version:** `10`

The filename suffix `v1` currently identifies the resource format or stable resource label, while
the JSON field `version: 10` identifies the tenth approved revision of the client profile. Both are
valid in their current contexts, and the filesystem loader validates and uses internal version 10,
but the shared word “version” makes stale-resource diagnosis unnecessarily ambiguous.

Before public release, separate these concepts explicitly:

- add a `schema_version` field for the serialized resource contract;
- rename or replace `version` with `profile_revision` for approved client-profile changes;
- define whether filenames track schema versions, profile revisions, or immutable resource IDs;
- provide compatibility loading or migration for existing versioned resources and artifacts.

Do not rename the current resource or implement this migration during the deterministic vertical
slice. The change requires an explicit compatibility policy because existing briefs and content
packages retain the current resource reference for provenance.

