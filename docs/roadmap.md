# Twenty implementation milestones

Each milestone ends with tests, documentation, structured logs where applicable, and an architectural
review. The order deliberately proves invariants with deterministic local components before adding
distributed systems or paid model calls.

1. **Architecture enforcement.** Add import-boundary tests, CI for Python 3.13, Ruff, mypy, pytest,
   packaging checks, contribution guidance, and architecture decision record templates.
2. **Foundational runtime values.** Introduce injected clock and ID-generator ports, typed exception
   taxonomy, correlation context, cancellation conventions, and sensitive-data redaction.
3. **Organizational validation.** Add cross-reference validation for company, department, employee,
   lead, tool, SOP, and resource definitions with actionable diagnostics.
4. **File configuration adapter.** Load versioned YAML/TOML organization definitions and package
   resources, with schema migration and environment overlay tests.
5. **Task lifecycle service.** Define legal transitions, assignment rules, dependency readiness,
   cancellation, failure, and optimistic concurrency behavior as pure application logic.
6. **In-memory reference adapters.** Implement deterministic repositories, dispatcher, event bus,
   memory, resources, model fake, and tools for use-case and example testing.
7. **Delegation use cases.** Implement create, assign, accept, block, complete, fail, and decompose
   operations with authorization seams and domain events.
8. **Event handling foundation.** Add typed event payloads, handler registry, idempotency keys,
   retry/dead-letter policy, and an in-process implementation.
9. **Persistent relational storage.** Implement organization, task, result, and event-outbox adapters
   with migrations and shared adapter contract tests.
10. **Worker and scheduling runtime.** Build async task claiming, leases, heartbeats, bounded
    concurrency, graceful shutdown, routine scheduling, and recovery after process failure.
11. **Prompt resource system.** Add versioned templates, strict variable schemas, deterministic
    rendering, composition rules, snapshots, and checks that prohibit embedded workflow logic.
12. **First model adapter contract.** Specify capability negotiation, structured output, streaming,
    usage, timeouts, rate limits, retries, and normalized errors; implement one provider plus a fake.
13. **Employee execution loop.** Coordinate context assembly, model calls, tools, task state, and
    results under explicit step, time, token, and cost budgets.
14. **Tool security boundary.** Add scoped grants, argument validation, confirmation workflow,
    sandbox policy, audit events, timeouts, idempotency, and secret injection outside model context.
15. **Durable memory.** Add working and episodic memory persistence, retention, provenance, access
    controls, deletion, and deterministic lexical retrieval before embeddings.
16. **Institutional knowledge.** Add department/company ownership, semantic consolidation,
    contradiction handling, citations, temporal validity, and knowledge retained across employee
    replacement.
17. **Hybrid retrieval adapters.** Add embedding-provider ports, vector and hybrid search adapters,
    reranking, evaluation datasets, and retrieval-quality metrics without changing employee code.
18. **CEO control plane.** Expose authenticated API/CLI operations for organizational inspection,
    task oversight, approvals, pause/resume, audit history, budgets, and policy management.
19. **Department workflows and deliverables.** Add policy-driven routing, quality gates, artifact
    storage, schemas, review loops, and one end-to-end reference department as a separately packaged
    example—not hard-coded core behavior.
20. **Production hardening and release.** Add OpenTelemetry, dashboards, load/chaos/security tests,
    backup and restore, tenancy isolation, upgrade compatibility, provider conformance, threat model,
    operator handbook, and the first stable public API release.

